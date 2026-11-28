"""Compras y rentabilidad por proveedor (app privada de finanzas, sin enlace en ningún índice).

Genera dos archivos cifrados en site/finanzas-d33eda5e/:
  - fin.enc       con el secreto CLAVE_COMPRAS_FINANZAS (gerente financiero: ve todo)
  - compras.enc   con el secreto CLAVE_COMPRAS (compradores: solo proveedores)
En esta fase los dos llevan los mismos datos de proveedores; las fases de tiendas y flujo
de caja se agregarán solo a fin.enc.

Qué lee de Odoo (solo lectura, no cambia nada). Solo fin.enc lleva tiendas, compras pendientes y cuentas por pagar:
  - Productos: categoría, PVP, costo de Odoo, proveedor principal (product.supplierinfo) y su precio.
  - Ventas de los últimos 24 meses: líneas de punto de venta + pedidos de venta (con IVA).
  - Compras de los últimos 24 meses por proveedor (pedidos de compra confirmados), pasadas a quetzales.
  - Existencias en ubicaciones internas.
Uso: python sync/compras.py --out site
"""
import argparse
import collections
import datetime as dt
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text, clean  # noqa: E402
from existencias import cifrar  # noqa: E402

CARPETA = "finanzas-d33eda5e"
MESES = 24


def meses_atras(n):
    hoy = dt.date.today().replace(day=1)
    out = []
    y, m = hoy.year, hoy.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


MES_ES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
          "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
          "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
          "september": 9, "october": 10, "november": 11, "december": 12}


def mes_de(g, campo):
    """Convierte la etiqueta de mes de read_group ('noviembre 2026', '2026-11-01'…) en '2026-11'."""
    v = g.get(campo + ":month") or g.get(campo) or ""
    if isinstance(v, (list, tuple)):
        v = v[-1] if v else ""
    s = str(v).strip().lower()
    if len(s) >= 7 and s[4] == "-":
        return s[:7]
    partes = s.replace(".", "").split()
    if len(partes) >= 2 and partes[-1].isdigit():
        m = MES_ES.get(partes[0])
        if m:
            return f"{int(partes[-1]):04d}-{m:02d}"
    rango = (g.get("__range") or {}).get(campo + ":month") or (g.get("__range") or {}).get(campo)
    if rango and rango.get("from"):
        return str(rango["from"])[:7]
    return ""


def agrupar(o, modelo, dominio, sumas, por, fecha):
    """read_group por producto (o proveedor) y mes. Devuelve lista de dicts."""
    campos = sumas + [por]
    return o.call(modelo, "read_group", [dominio, campos, [por, fecha + ":month"]], {"lazy": False})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    k_fin = os.environ.get("CLAVE_COMPRAS_FINANZAS", "").strip()
    k_com = os.environ.get("CLAVE_COMPRAS", "").strip()
    if len(k_fin) < 10:
        print("Falta el secreto CLAVE_COMPRAS_FINANZAS (mínimo 10 caracteres): no se generan datos de compras.")
        return

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()

    meses = meses_atras(MESES)
    mi = {m: i for i, m in enumerate(meses)}
    desde = meses[0] + "-01 00:00:00"
    hoy = dt.date.today()

    # ------------------------------------------------------------ ventas por producto y mes
    VU = collections.defaultdict(lambda: [0.0] * MESES)   # unidades
    VQ = collections.defaultdict(lambda: [0.0] * MESES)   # quetzales con IVA
    print("Ventas de punto de venta…")
    try:
        for g in agrupar(o, "pos.order.line", [["order_id.state", "in", ["paid", "done", "invoiced"]], ["create_date", ">=", desde]],
                         ["qty:sum", "price_subtotal_incl:sum"], "product_id", "create_date"):
            m = mi.get(mes_de(g, "create_date"))
            if m is None or not g.get("product_id"):
                continue
            pid = g["product_id"][0]
            VU[pid][m] += float(g.get("qty") or 0)
            VQ[pid][m] += float(g.get("price_subtotal_incl") or 0)
    except SystemExit as e:
        print("  Aviso: no se pudo leer el punto de venta:", str(e)[:200])
    print("Ventas de pedidos (mayoreo, web)…")
    try:
        for g in agrupar(o, "sale.order.line", [["state", "in", ["sale", "done"]], ["create_date", ">=", desde], ["display_type", "=", False]],
                         ["qty_delivered:sum", "price_total:sum", "product_uom_qty:sum"], "product_id", "create_date"):
            m = mi.get(mes_de(g, "create_date"))
            if m is None or not g.get("product_id"):
                continue
            pid = g["product_id"][0]
            ped = float(g.get("product_uom_qty") or 0)
            ent = float(g.get("qty_delivered") or 0)
            VU[pid][m] += ent
            VQ[pid][m] += float(g.get("price_total") or 0) * (ent / ped if ped else 0)
    except SystemExit as e:
        print("  Aviso: no se pudieron leer los pedidos de venta:", str(e)[:200])

    # ------------------------------------------------------------ existencias
    print("Existencias…")
    EX = collections.defaultdict(float)
    for q in o.search_read_all("stock.quant", [["location_id.usage", "=", "internal"]], ["product_id", "quantity"]):
        if q.get("product_id"):
            EX[q["product_id"][0]] += float(q.get("quantity") or 0)

    # ------------------------------------------------------------ monedas (tasa = unidades por 1 quetzal)
    tasas = {}
    for c in o.search_read_all("res.currency", [["active", "=", True]], ["name", "rate"]):
        tasas[c["id"]] = (c["name"], float(c.get("rate") or 1) or 1)
    usd = next((r for n, r in tasas.values() if n == "USD"), None)
    a_q = lambda cid, monto: monto / (tasas.get(cid, ("", 1))[1] or 1)

    # ------------------------------------------------------------ productos
    pids = sorted(pid for pid in set(VU) | {p for p, q in EX.items() if q > 0})
    print(f"Datos de {len(pids):,} productos…")
    P = {}
    for i in range(0, len(pids), 5000):
        for p in o.call("product.product", "read", [pids[i:i + 5000]], {"fields": ["barcode", "default_code", "name", "list_price", "standard_price", "categ_id", "product_tmpl_id", "create_date"], "context": {"active_test": False}}):
            P[p["id"]] = p

    print("Proveedores de cada producto…")
    prov_t = {}
    for s in o.search_read_all("product.supplierinfo", [], ["partner_id", "product_tmpl_id", "price", "currency_id", "sequence"], order="sequence, id"):
        if s.get("product_tmpl_id") and s.get("partner_id") and s["product_tmpl_id"][0] not in prov_t:
            precio = a_q((s.get("currency_id") or [0])[0], float(s.get("price") or 0))
            prov_t[s["product_tmpl_id"][0]] = (s["partner_id"][0], as_text(s["partner_id"]), precio)

    # ------------------------------------------------------------ compras por proveedor y mes
    print("Compras por proveedor…")
    CO = collections.defaultdict(lambda: [0.0] * MESES)    # partner_id -> quetzales
    try:
        for g in o.call("purchase.order.line", "read_group",
                        [[["state", "in", ["purchase", "done"]], ["create_date", ">=", desde], ["display_type", "=", False]],
                         ["price_total:sum", "partner_id", "currency_id"], ["partner_id", "currency_id", "create_date:month"]],
                        {"lazy": False}):
            m = mi.get(mes_de(g, "create_date"))
            if m is None or not g.get("partner_id"):
                continue
            CO[g["partner_id"][0]][m] += a_q((g.get("currency_id") or [0])[0], float(g.get("price_total") or 0))
    except SystemExit as e:
        print("  Aviso: no se pudieron leer las compras:", str(e)[:200])

    # ------------------------------------------------------------ pedidos de compra pendientes de recibir
    print("Pedidos de compra en camino…")
    CAMINO = collections.defaultdict(float)   # product_id -> unidades por recibir
    OC = []                                   # (partner_id, fecha prevista, quetzales pendientes)
    try:
        for l in o.search_read_all("purchase.order.line", [["state", "=", "purchase"], ["display_type", "=", False]],
                                   ["product_id", "partner_id", "product_qty", "qty_received", "price_unit", "currency_id", "date_planned"]):
            falta = float(l.get("product_qty") or 0) - float(l.get("qty_received") or 0)
            if falta <= 0:
                continue
            if l.get("product_id"):
                CAMINO[l["product_id"][0]] += falta
            if l.get("partner_id"):
                OC.append((l["partner_id"][0], (l.get("date_planned") or "")[:10], a_q((l.get("currency_id") or [0])[0], falta * float(l.get("price_unit") or 0))))
    except SystemExit as e:
        print("  Aviso: no se pudieron leer los pedidos en camino:", str(e)[:200])

    # ------------------------------------------------------------ facturas de proveedor por pagar
    print("Facturas de proveedor por pagar…")
    CXP = []
    try:
        for f in o.search_read_all("account.move", [["move_type", "=", "in_invoice"], ["state", "=", "posted"], ["amount_residual", ">", 0]],
                                   ["partner_id", "amount_residual_signed", "invoice_date_due", "invoice_date"]):
            if f.get("partner_id"):
                CXP.append((f["partner_id"][0], (f.get("invoice_date_due") or f.get("invoice_date") or "")[:10], abs(float(f.get("amount_residual_signed") or 0))))
    except SystemExit as e:
        print("  Aviso: no se pudieron leer las cuentas por pagar:", str(e)[:200])

    # ------------------------------------------------------------ unidades vendidas por tienda (12 meses)
    print("Venta por tienda…")
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as fc:
        cfg = json.load(fc)
    tiendas = [t.upper() for t in cfg["resurtido"]["tiendas"]]
    nombres = cfg.get("nombres_tiendas", {})
    VT = collections.defaultdict(float)       # (product_id, tienda) -> unidades
    try:
        d12 = meses[-12] + "-01 00:00:00"
        base = [["state", "=", "done"], ["date", ">=", d12]]
        for g in o.call("stock.move", "read_group", [base + [["location_dest_id.usage", "=", "customer"], ["location_id.usage", "=", "internal"]],
                                                     ["product_uom_qty:sum", "product_id", "location_id"], ["product_id", "location_id"]], {"lazy": False}):
            if g.get("product_id") and g.get("location_id"):
                VT[(g["product_id"][0], as_text(g["location_id"]).split("/")[0].strip().upper())] += float(g.get("product_uom_qty") or 0)
        for g in o.call("stock.move", "read_group", [base + [["location_id.usage", "=", "customer"], ["location_dest_id.usage", "=", "internal"]],
                                                     ["product_uom_qty:sum", "product_id", "location_dest_id"], ["product_id", "location_dest_id"]], {"lazy": False}):
            if g.get("product_id") and g.get("location_dest_id"):
                VT[(g["product_id"][0], as_text(g["location_dest_id"]).split("/")[0].strip().upper())] -= float(g.get("product_uom_qty") or 0)
    except SystemExit as e:
        print("  Aviso: no se pudo leer la venta por tienda:", str(e)[:200])

    pids = sorted(set(pids) | {p for p, q in CAMINO.items() if q > 0})
    faltan = [x for x in pids if x not in P]
    for i in range(0, len(faltan), 5000):
        for p in o.call("product.product", "read", [faltan[i:i + 5000]], {"fields": ["barcode", "default_code", "name", "list_price", "standard_price", "categ_id", "product_tmpl_id", "create_date"], "context": {"active_test": False}}):
            P[p["id"]] = p

    partner_ids = sorted({v[0] for v in prov_t.values()} | set(CO) | {x[0] for x in OC} | {x[0] for x in CXP})
    pais = {}
    for i in range(0, len(partner_ids), 2000):
        for r in o.call("res.partner", "read", [partner_ids[i:i + 2000]], {"fields": ["name", "country_id"], "context": {"active_test": False}}):
            pais[r["id"]] = ((r.get("country_id") or [0, ""])[1] if r.get("country_id") else "", clean(r.get("name") or "").upper())

    # ------------------------------------------------------------ armar tablas compactas
    PROV, PI = [], {}

    def prov_idx(partner_id, nombre):
        key = partner_id or nombre
        if key not in PI:
            PI[key] = len(PROV)
            PROV.append({"id": partner_id or 0, "n": (pais.get(partner_id, ("", ""))[1] or clean(nombre).upper() or "SIN PROVEEDOR"),
                         "pais": pais.get(partner_id, ("", ""))[0], "co": [0.0] * MESES})
        return PI[key]

    CATS, CI = [], {}

    def cat_idx(nombre):
        madre = (nombre.split("/")[0].strip() or "SIN CATEGORIA").upper()
        if madre not in CI:
            CI[madre] = len(CATS)
            CATS.append(madre)
        return CI[madre]

    filas, VTF = [], []
    for pid in pids:
        p = P.get(pid)
        if not p:
            continue
        sup = prov_t.get((p.get("product_tmpl_id") or [0])[0])
        pv = prov_idx(sup[0], sup[1]) if sup else prov_idx(0, "SIN PROVEEDOR")
        vu, vq = VU.get(pid, [0.0] * MESES), VQ.get(pid, [0.0] * MESES)
        ultimo = max((i for i in range(MESES) if vu[i] > 0), default=-1)
        filas.append([
            pid, p.get("barcode") or p.get("default_code") or "", clean(p.get("name") or ""), pv, cat_idx(as_text(p.get("categ_id"))),
            round(float(p.get("list_price") or 0), 2), round(float(p.get("standard_price") or 0), 2), round(sup[2], 2) if sup else 0,
            round(EX.get(pid, 0), 2), [round(x, 1) for x in vu], [round(x) for x in vq], (p.get("create_date") or "")[:7], ultimo,
            round(CAMINO.get(pid, 0), 2),
        ])
        fila = len(filas) - 1
        for ti, t in enumerate(tiendas):
            q = VT.get((pid, t), 0)
            if q > 0:
                VTF.append([fila, ti, round(q, 1)])
    for partner_id, serie in CO.items():
        PROV[prov_idx(partner_id, pais.get(partner_id, ("", ""))[1])]["co"] = [round(x) for x in serie]

    oc = [[prov_idx(pp, pais.get(pp, ("", ""))[1]), f, round(m)] for pp, f, m in OC if m > 0]
    cxp = [[prov_idx(pp, pais.get(pp, ("", ""))[1]), f, round(m)] for pp, f, m in CXP if m > 0]
    datos = {
        "generado": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "meses": meses, "tc": round(1 / usd, 4) if usd else 7.7,
        "cols": ["id", "codigo", "nombre", "prov", "cat", "pvp", "costo_odoo", "precio_prov", "existencia", "venta_uni", "venta_q", "alta", "ult_mes", "en_camino"],
        "prov": PROV, "cats": CATS, "prod": filas,
    }
    zip_ = lambda d: gzip.compress(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    plano = zip_(datos)   # compradores: proveedores, productos y pedido sugerido
    fin = dict(datos, tiendas=[{"c": t, "n": nombres.get(t, t)} for t in tiendas], vt=VTF, oc=oc, cxp=cxp)
    carpeta = os.path.join(a.out, CARPETA)
    os.makedirs(carpeta, exist_ok=True)
    with open(os.path.join(carpeta, "fin.enc"), "w", encoding="utf-8") as f:
        json.dump(cifrar(zip_(fin), k_fin), f)
    if len(k_com) >= 10:
        with open(os.path.join(carpeta, "compras.enc"), "w", encoding="utf-8") as f:
            json.dump(cifrar(plano, k_com), f)
    else:
        print("Sin CLAVE_COMPRAS: solo se genera el archivo del gerente financiero.")
    print(f"Compras: {len(filas):,} productos · {len(PROV):,} proveedores · {len(plano) / 1e6:.1f} MB comprimido")


if __name__ == "__main__":
    main()
