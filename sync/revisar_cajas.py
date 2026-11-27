"""Revisión de cajas: qué usuarios y empleados se usan en cada tienda y cómo se dan los descuentos manuales.

Solo LEE Odoo (usuario de solo lectura de la sincronización). Sirve para preparar el límite de descuento por cajero
(más de 25% solo con la clave de un supervisor): muestra si cada cajero entra con su propio usuario o empleado,
quién puede autorizar, y cuántas líneas llevan descuento manual y de qué tamaño.

Publica cifrado (clave CLAVE_EXISTENCIAS) en site/cajas/datos.enc para el app Revisión de cajas (Gerencia).
Los nombres de personas NO se imprimen en el registro de GitHub (el repo es público): solo conteos.

Uso: python sync/revisar_cajas.py --out site [--dias 30]
"""
import argparse
import collections
import datetime as dt
import gzip
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text  # noqa: E402
from existencias import cifrar  # noqa: E402
from tiendas import nombre  # noqa: E402

UTC = dt.timezone.utc
LIMITE = 25   # % de descuento que podría dar un cajero sin autorización


def campos(o, model):
    try:
        return o.call(model, "fields_get", [], {"attributes": ["type", "relation", "string"]})
    except SystemExit:
        return {}


def m2o(v):
    return (v[0], as_text(v)) if isinstance(v, (list, tuple)) and v else (None, "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--dias", type=int, default=30)
    a = ap.parse_args()
    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY", "CLAVE_EXISTENCIAS")}
    if not all(env.values()):
        raise SystemExit("Faltan los secretos de conexión o CLAVE_EXISTENCIAS.")
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()
    desde = (dt.datetime.now(UTC) - dt.timedelta(days=a.dias)).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Cajas y quién puede usarlas
    cf = campos(o, "pos.config")
    opcional = [f for f in ("module_pos_hr", "basic_employee_ids", "advanced_employee_ids", "minimal_employee_ids",
                            "manual_discount", "restrict_price_control", "pricelist_id", "active") if f in cf]
    cajas = o.call("pos.config", "search_read", [[]], {"fields": ["name"] + opcional, "context": {"active_test": False}})
    emp_ids = {i for c in cajas for f in ("basic_employee_ids", "advanced_employee_ids", "minimal_employee_ids") for i in (c.get(f) or [])}
    empleados = {}
    if emp_ids:
        try:
            for e in o.call("hr.employee", "read", [sorted(emp_ids)], {"fields": ["name"]}):
                empleados[e["id"]] = e["name"]
        except SystemExit:
            print("  Aviso: el usuario de lectura no puede ver los empleados; se muestran por número.")

    # 2. Ventas de los últimos días: quién aparece como responsable y como empleado
    of = campos(o, "pos.order")
    f_emp = "employee_id" if "employee_id" in of else None
    pedidos = o.search_read_all("pos.order", [["date_order", ">=", desde], ["state", "not in", ["cancel", "draft"]]],
                                ["config_id", "user_id", "amount_total"] + ([f_emp] if f_emp else []))
    por_caja = collections.defaultdict(lambda: {"tickets": 0, "total": 0.0, "usuarios": collections.Counter(), "empleados": collections.Counter()})
    caja_de_pedido = {}
    quien_de_pedido = {}
    for p in pedidos:
        cid, _ = m2o(p["config_id"])
        _, usr = m2o(p["user_id"])
        _, emp = m2o(p.get(f_emp)) if f_emp else (None, "")
        d = por_caja[cid]
        d["tickets"] += 1
        d["total"] += float(p.get("amount_total") or 0)
        d["usuarios"][usr or "—"] += 1
        if emp:
            d["empleados"][emp] += 1
        caja_de_pedido[p["id"]] = cid
        quien_de_pedido[p["id"]] = emp or usr or "—"

    # 3. Descuentos manuales en las líneas (campo "discount" del punto de venta)
    lineas = o.search_read_all("pos.order.line", [["order_id.date_order", ">=", desde], ["discount", ">", 0]],
                               ["order_id", "discount", "qty", "price_unit"])
    desc = collections.defaultdict(lambda: collections.defaultdict(lambda: {"lineas": 0, "suma_pct": 0.0, "max": 0.0, "sobre": 0, "quetzales": 0.0}))
    for l in lineas:
        pid, _ = m2o(l["order_id"])
        cid = caja_de_pedido.get(pid)
        if cid is None:
            continue
        pct = float(l["discount"] or 0)
        x = desc[cid][quien_de_pedido.get(pid, "—")]
        x["lineas"] += 1
        x["suma_pct"] += pct
        x["max"] = max(x["max"], pct)
        x["sobre"] += pct > LIMITE
        x["quetzales"] += float(l["qty"] or 0) * float(l["price_unit"] or 0) * pct / 100

    # 4. Armar el resultado por caja
    out = []
    for c in cajas:
        d = por_caja.get(c["id"], {"tickets": 0, "total": 0.0, "usuarios": collections.Counter(), "empleados": collections.Counter()})
        out.append({
            "caja": c["name"], "tienda": nombre((c["name"] or "").strip()), "activa": c.get("active", True),
            "con_empleados": bool(c.get("module_pos_hr")) if "module_pos_hr" in c else None,
            "descuento_manual": c.get("manual_discount") if "manual_discount" in c else None,
            "precio_solo_gerentes": c.get("restrict_price_control") if "restrict_price_control" in c else None,
            "lista_precios": as_text(c.get("pricelist_id")),
            "supervisores": sorted(empleados.get(i, f"#{i}") for i in (c.get("advanced_employee_ids") or [])),
            "cajeros": sorted(empleados.get(i, f"#{i}") for i in (c.get("basic_employee_ids") or [])),
            "tickets": d["tickets"], "total": round(d["total"], 2),
            "usuarios": d["usuarios"].most_common(), "empleados": d["empleados"].most_common(),
            "descuentos": [{"quien": q, "lineas": x["lineas"], "promedio": round(x["suma_pct"] / x["lineas"], 1), "max": x["max"],
                            "sobre_limite": x["sobre"], "quetzales": round(x["quetzales"], 2)}
                           for q, x in sorted(desc.get(c["id"], {}).items(), key=lambda kv: -kv[1]["lineas"])],
        })
    out.sort(key=lambda c: (not c["tickets"], c["tienda"], c["caja"]))

    version = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    datos = json.dumps({"v": version, "dias": a.dias, "limite": LIMITE, "campos": {"empleado_en_venta": bool(f_emp), "opciones_caja": opcional}, "cajas": out},
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sobre = cifrar(gzip.compress(datos, 9), env["CLAVE_EXISTENCIAS"])
    sobre["v"] = version
    os.makedirs(os.path.join(a.out, "cajas"), exist_ok=True)
    with open(os.path.join(a.out, "cajas", "datos.enc"), "w", encoding="utf-8") as fh:
        json.dump(sobre, fh, separators=(",", ":"))
    # Solo conteos en el registro (el repo es público)
    print(f"Revisión de cajas: {len(cajas)} cajas, {len(pedidos):,} tickets y {len(lineas):,} líneas con descuento en {a.dias} días.")


if __name__ == "__main__":
    main()
