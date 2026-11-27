"""Negativos por tienda y traslados que llegan a cubrir un negativo (para el dashboard de Diferencias).

Genera site/diferencias/negativos.enc (cifrado con CLAVE_EXISTENCIAS, igual que existencias.enc):
  - negativos: productos con existencia menor que cero en cada tienda, con su última venta en esa tienda
    y la fecha desde la que se ve en negativo (se arrastra de la sincronización anterior).
  - alertas: traslados internos hacia una tienda que llevan un producto que esa tienda tiene en negativo.
    Pendientes (todavía no se validan) y los recibidos en los últimos 30 días.

Solo lee Odoo; no cambia nada.
Uso: python sync/negativos.py --out site
"""
import argparse
import datetime as dt
import gzip
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text  # noqa: E402
from existencias import cifrar  # noqa: E402

PUBLICADO = "https://charly369-lv.github.io/Tools/diferencias/negativos.enc"
DIAS_VENTA = 120
DIAS_RECIBIDOS = 30


def descifrar(sobre, clave):
    import base64
    import hashlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    b = base64.b64decode
    k = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), b(sobre["s"]), int(sobre["it"]), 32)
    return json.loads(gzip.decompress(AESGCM(k).decrypt(b(sobre["iv"]), b(sobre["d"]), None)))


def anterior(clave):
    """Fechas 'desde' de la sincronización anterior, para saber cuántos días lleva cada negativo."""
    try:
        with urllib.request.urlopen(PUBLICADO + "?t=" + str(int(dt.datetime.now().timestamp())), timeout=20) as r:
            d = descifrar(json.loads(r.read()), clave)
        return {(n["t"], n["id"]): n["desde"] for n in d.get("negativos", [])}
    except Exception as e:  # primera vez o sin internet: todos empiezan hoy
        print("Sin datos anteriores de negativos:", e)
        return {}


def ubic(v):
    return as_text(v).split("/")[0].strip().upper()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    clave = os.environ.get("CLAVE_EXISTENCIAS", "").strip()
    if len(clave) < 10:
        print("Falta el secreto CLAVE_EXISTENCIAS: no se generan negativos.")
        return
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    tiendas = [t.upper() for t in cfg["resurtido"]["tiendas"]]
    nombres = cfg.get("nombres_tiendas", {})

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()
    hoy = dt.date.today().isoformat()

    print("Leyendo existencias negativas…")
    quants = o.search_read_all("stock.quant", [["location_id.usage", "=", "internal"], ["quantity", "<", 0]],
                               ["product_id", "location_id", "quantity"])
    neg = {}   # (tienda, product_id) -> cantidad
    for q in quants:
        t = ubic(q["location_id"])
        if t in tiendas and q.get("product_id"):
            k = (t, q["product_id"][0])
            neg[k] = neg.get(k, 0) + float(q["quantity"] or 0)
    neg = {k: v for k, v in neg.items() if v < 0}
    pids = sorted({p for _, p in neg})

    prods = {}
    for i in range(0, len(pids), 500):
        for p in o.call("product.product", "read", [pids[i:i + 500], ["display_name", "barcode", "default_code", "standard_price", "categ_id"]]):
            prods[p["id"]] = p

    print("Buscando la última venta de cada negativo…")
    desde_v = (dt.date.today() - dt.timedelta(days=DIAS_VENTA)).isoformat()
    ult = {}
    for i in range(0, len(pids), 300):
        movs = o.search_read_all("stock.move", [["state", "=", "done"], ["product_id", "in", pids[i:i + 300]],
                                                ["location_dest_id.usage", "=", "customer"], ["date", ">=", desde_v]],
                                 ["product_id", "location_id", "date", "quantity"])
        for m in movs:
            k = (ubic(m["location_id"]), m["product_id"][0])
            if k in neg and (k not in ult or m["date"] > ult[k]):
                ult[k] = m["date"]

    prev = anterior(clave)
    negativos = []
    for (t, pid), qty in neg.items():
        p = prods.get(pid, {})
        negativos.append({
            "t": t, "tn": nombres.get(t, t), "id": pid,
            "p": p.get("display_name") or "", "c": p.get("barcode") or p.get("default_code") or "",
            "cat": as_text(p.get("categ_id")), "q": qty, "costo": round(float(p.get("standard_price") or 0), 2),
            "venta": (ult.get((t, pid)) or "")[:10], "desde": prev.get((t, pid), hoy),
        })

    print("Revisando traslados hacia tiendas con negativo…")
    lim = (dt.datetime.now() - dt.timedelta(days=DIAS_RECIBIDOS)).strftime("%Y-%m-%d %H:%M:%S")
    movs = o.search_read_all("stock.move", [
        ["picking_code", "=", "internal"], ["product_id", "in", pids or [0]], ["location_dest_id.usage", "=", "internal"],
        "|", ["state", "not in", ["done", "cancel"]], "&", ["state", "=", "done"], ["date", ">=", lim]],
        ["product_id", "location_id", "location_dest_id", "picking_id", "state", "date", "product_uom_qty", "quantity", "create_uid"])
    alertas = []
    for m in movs:
        t = ubic(m["location_dest_id"])
        k = (t, m["product_id"][0])
        if k not in neg or ubic(m["location_id"]) == t:
            continue
        alertas.append({
            "t": t, "tn": nombres.get(t, t), "id": k[1], "p": prods.get(k[1], {}).get("display_name", ""),
            "c": prods.get(k[1], {}).get("barcode") or "", "neg": neg[k],
            "origen": ubic(m["location_id"]), "on": nombres.get(ubic(m["location_id"]), ubic(m["location_id"])),
            "traslado": as_text(m.get("picking_id")), "pend": m["state"] != "done",
            "cant": float((m.get("quantity") if m["state"] == "done" else m.get("product_uom_qty")) or 0),
            "fecha": (m.get("date") or "")[:10], "pidio": as_text(m.get("create_uid")),
        })

    datos = {"generado": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "negativos": negativos, "alertas": alertas}
    os.makedirs(os.path.join(a.out, "diferencias"), exist_ok=True)
    with open(os.path.join(a.out, "diferencias", "negativos.enc"), "w", encoding="utf-8") as f:
        json.dump(cifrar(gzip.compress(json.dumps(datos, ensure_ascii=False).encode("utf-8")), clave), f)
    print(f"Negativos: {len(negativos)} · alertas de traslado: {len(alertas)}")


if __name__ == "__main__":
    main()
