"""Existencias por ubicación, cifradas con clave, para la app de surtido.

Lee las existencias de todas las ubicaciones internas y las guarda cifradas (AES-256-GCM con una
clave derivada por PBKDF2-SHA256) en site/surtido/existencias.enc. Sin la clave el archivo no se
puede leer, aunque el sitio sea público. La clave vive solo en el secreto CLAVE_EXISTENCIAS.

Uso: python sync/existencias.py --out site
"""
import argparse
import base64
import collections
import gzip
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text, clean  # noqa: E402

ITER = 310000


def cifrar(datos: bytes, clave: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    sal, iv = os.urandom(16), os.urandom(12)
    k = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, ITER, 32)
    b = lambda x: base64.b64encode(x).decode()
    return {"kdf": "PBKDF2-SHA256", "it": ITER, "s": b(sal), "iv": b(iv), "d": b(AESGCM(k).encrypt(iv, datos, None))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    clave = os.environ.get("CLAVE_EXISTENCIAS", "").strip()
    if not clave:
        print("No está el secreto CLAVE_EXISTENCIAS: no se generan existencias protegidas.")
        return
    if len(clave) < 10:
        raise SystemExit("La clave CLAVE_EXISTENCIAS debe tener al menos 10 caracteres.")
    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    cfg = {}
    if os.path.exists(os.path.join(HERE, "config.json")):
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    excluir = {str(x).strip().upper() for x in cfg.get("existencias_excluir", [])}

    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()
    print("Leyendo existencias por ubicación…")
    quants = o.search_read_all("stock.quant", [["location_id.usage", "=", "internal"], ["quantity", ">", 0]],
                               ["product_id", "location_id", "quantity", "reserved_quantity"])
    ex = collections.defaultdict(collections.Counter)
    for q in quants:
        if not q.get("product_id"):
            continue
        ubic = as_text(q["location_id"]).split("/")[0].strip()
        if not ubic or ubic.upper() in excluir:
            continue
        disp = float(q.get("quantity") or 0) - float(q.get("reserved_quantity") or 0)
        if disp > 0:
            ex[q["product_id"][0]][ubic] += disp

    ids = sorted(ex)
    prods = {}
    for i in range(0, len(ids), 5000):
        for p in o.call("product.product", "read", [ids[i:i + 5000]], {"fields": ["barcode", "default_code"]}):
            prods[p["id"]] = p

    L = sorted({u for c in ex.values() for u in c})
    li = {u: i for i, u in enumerate(L)}
    P, R = {}, {}
    for pid, c in ex.items():
        p = prods.get(pid) or {}
        code = re.sub(r"\D", "", str(p.get("barcode") or ""))
        ref = clean(p.get("default_code") or "")
        if not code and not ref:
            continue
        key = code or "r:" + ref
        par = []
        for u, n in sorted(c.items(), key=lambda x: -x[1]):
            n = int(n)
            if n > 0:
                par += [li[u], n]
        if not par:
            continue
        if key in P:  # mismo código en dos productos: se suman
            prev = dict(zip(P[key][1][::2], P[key][1][1::2]))
            for j in range(0, len(par), 2):
                prev[par[j]] = prev.get(par[j], 0) + par[j + 1]
            par = [x for kv in sorted(prev.items(), key=lambda x: -x[1]) for x in kv]
        P[key] = [ref, par]
        if ref:
            R[ref] = key

    version = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    datos = json.dumps({"v": version, "L": L, "p": P, "r": R}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sobre = cifrar(gzip.compress(datos, 9), clave)
    sobre["v"] = version
    out = os.path.join(a.out, "surtido")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "existencias.enc"), "w", encoding="utf-8") as f:
        json.dump(sobre, f, separators=(",", ":"))
    print(f"Existencias protegidas: {len(P):,} productos con existencia en {len(L)} ubicaciones "
          f"({len(sobre['d']) // 1024:,} KB cifrados).")


if __name__ == "__main__":
    main()
