"""Candidatos para el surtido de Fitness One (app Mayoreo – Fitness One), cifrados con clave.

También publica site/clasificacion/datos.enc (todos los libros con existencia, con categoría,
tema y género de Metadatos) para el app Revisión de clasificación.

Publica en site/fitness/datos.enc los libros a precio normal (sin bazar) que tienen existencia,
con lo necesario para elegir qué mandar:
  - título, autor, editorial, precio de venta (PVP), categoría, tema, género literario,
    portada, sinopsis y número de páginas (para la ficha que se manda a Fitness One)
  - existencia disponible por bodega (cantidad menos lo reservado)
  - ventas de la cadena: últimos 30 y 90 días (salidas a clientes menos devoluciones),
    y las ventas de 90 días por ubicación (para medir después lo que vende Fitness One)

Se cifra igual que las existencias (AES-256-GCM, clave del secreto CLAVE_EXISTENCIAS).

Uso: python sync/fitness_one.py --out site
"""
import argparse
import collections
import datetime as dt
import gzip
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text, clean, find_field, fields_get_es  # noqa: E402
from existencias import META, cifrar, limpiar_sinopsis  # noqa: E402

BAZAR = re.compile(r"BAZAR\D{0,3}\d{1,2}\s*%", re.I)
UTC = dt.timezone.utc
GT = dt.timezone(dt.timedelta(hours=-6))
MIN_TOTAL = 2   # solo libros con al menos estas unidades disponibles en total


def ubic(loc):
    """'MIRAF/Stock' → 'MIRAF'"""
    return as_text(loc).split("/")[0].strip()


def odoo_fecha(d):
    return d.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY", "CLAVE_EXISTENCIAS")}
    if not all(env.values()):
        raise SystemExit("Faltan los secretos de conexión o CLAVE_EXISTENCIAS.")
    cfg = {}
    if os.path.exists(os.path.join(HERE, "config.json")):
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    excluir = {str(x).strip().upper() for x in cfg.get("existencias_excluir", [])}

    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()

    # 1. Existencias disponibles por bodega
    print("Leyendo existencias…")
    quants = o.search_read_all("stock.quant", [["location_id.usage", "=", "internal"], ["quantity", ">", 0]],
                               ["product_id", "location_id", "quantity", "reserved_quantity"])
    ex = collections.defaultdict(collections.Counter)
    for q in quants:
        if not q.get("product_id"):
            continue
        u = ubic(q["location_id"])
        if not u or u.upper() in excluir:
            continue
        disp = float(q.get("quantity") or 0) - float(q.get("reserved_quantity") or 0)
        if disp > 0:
            ex[q["product_id"][0]][u] += disp

    # 2. Ventas de los últimos 90 días (salidas a clientes menos devoluciones)
    hoy = dt.datetime.now(GT).replace(hour=0, minute=0, second=0, microsecond=0)
    d30, d90 = hoy - dt.timedelta(days=30), hoy - dt.timedelta(days=90)
    mf = o.call("stock.move", "fields_get", [], {"attributes": ["string", "type"]})
    qf = next((f for f in ("quantity", "quantity_done", "product_qty", "product_uom_qty") if f in mf), "product_uom_qty")
    base = [["state", "=", "done"], ["date", ">=", odoo_fecha(d90)], ["date", "<", odoo_fecha(hoy)]]
    print(f"Leyendo ventas desde {d90.date()}…")
    salidas = o.search_read_all("stock.move", base + [["location_dest_id.usage", "=", "customer"], ["location_id.usage", "=", "internal"]],
                                ["product_id", "location_id", "date", qf])
    devol = o.search_read_all("stock.move", base + [["location_id.usage", "=", "customer"], ["location_dest_id.usage", "=", "internal"]],
                              ["product_id", "location_dest_id", "date", qf])
    v30, v90 = collections.Counter(), collections.Counter()
    vloc = collections.defaultdict(collections.Counter)
    lim30 = odoo_fecha(d30)
    for mvs, campo, signo in ((salidas, "location_id", 1), (devol, "location_dest_id", -1)):
        for mv in mvs:
            if not mv.get("product_id"):
                continue
            pid, n = mv["product_id"][0], signo * float(mv.get(qf) or 0)
            v90[pid] += n
            vloc[pid][ubic(mv[campo])] += n
            if str(mv["date"]) >= lim30:
                v30[pid] += n
    print(f"  {len(salidas):,} salidas y {len(devol):,} devoluciones")

    # 3. Datos de los productos con existencia suficiente
    ids = sorted(pid for pid, c in ex.items() if sum(c.values()) >= 1)
    pf = fields_get_es(o, "product.product", ["string", "type"])
    tt = ("char", "text", "many2one", "many2many", "selection")
    f_aut = (cfg.get("campo_autor") and [cfg["campo_autor"]]) or find_field(pf, ["Autor", "AUTOR", "Autores"], tt)
    f_edi = (cfg.get("campo_editorial") and [cfg["campo_editorial"]]) or find_field(pf, ["Editorial"], tt)
    f_tem = (cfg.get("campo_tema") and [cfg["campo_tema"]]) or find_field(pf, ["Tema", "Temas", "Tema del libro"], tt)
    price = cfg.get("campo_precio") or ("lst_price" if "lst_price" in pf else "list_price")
    meta = {k: (cfg.get(k) or (META[k][0] if META[k][0] in pf else None)) for k in ("meta_genero", "meta_titulo", "meta_autor", "meta_portada", "meta_sinopsis", "meta_paginas")}
    extra = [x for x in (f_aut[:1] + f_edi[:1] + f_tem[:1] + [m for m in meta.values() if m]) if x in pf]
    campos = ["id", "product_tmpl_id", "default_code", "barcode", "name", "categ_id", price] + sorted(set(extra))
    print("Campos:", ", ".join(campos))

    L = sorted({u for c in ex.values() for u in c} | {u for c in vloc.values() for u in c})
    li = {u: i for i, u in enumerate(L)}
    P, R = {}, {}   # P: candidatos de Fitness One · R: todos los libros con existencia, para revisar la clasificación
    for i in range(0, len(ids), 2000):
        for p in o.call("product.product", "read", [ids[i:i + 2000]], {"fields": campos}):
            cat = as_text(p.get("categ_id"))
            if "LIBRO" not in cat.upper():
                continue
            code = re.sub(r"\D", "", str(p.get("barcode") or ""))
            ref = clean(p.get("default_code") or "")
            if not code and not ref:
                continue
            g = lambda f: as_text(p.get(f)).strip() if f else ""
            titulo = g(meta["meta_titulo"]) or as_text(p.get("name")).strip()
            autor = g(meta["meta_autor"]) or (g(f_aut[0]) if f_aut else "")
            pid = p["id"]
            total = int(sum(ex[pid].values()))
            R[code or "r:" + ref] = [ref, titulo, autor, g(f_edi[0]) if f_edi else "", cat.split("/")[-1].strip(),
                                     g(f_tem[0]) if f_tem else "", g(meta["meta_genero"]), total, max(0, round(v90[pid])),
                                     g(f_aut[0]) if f_aut else "", g(meta["meta_autor"]), as_text(p.get("name")).strip(),
                                     (p.get("product_tmpl_id") or [0])[0], as_text(p.get("categ_id"))]
            if total < MIN_TOTAL or BAZAR.search(cat):
                continue
            st = [x for u, n in sorted(ex[pid].items(), key=lambda x: -x[1]) if int(n) > 0 for x in (li[u], int(n))]
            vl = [x for u, n in vloc[pid].items() if round(n) > 0 for x in (li[u], round(n))]
            sin = ""
            if sum(ex[pid].values()) >= 4:   # sinopsis solo para los que se pueden mandar (para no inflar el archivo)
                sin = limpiar_sinopsis(g(meta["meta_sinopsis"]))
                if len(sin) > 600:
                    sin = sin[:600].rsplit(" ", 1)[0] + "…"
            P[code or "r:" + ref] = [
                ref, titulo, autor, g(f_edi[0]) if f_edi else "",
                float(p.get(price) or 0), cat.split("/")[-1].strip(), g(f_tem[0]) if f_tem else "", g(meta["meta_genero"]),
                st, max(0, round(v30[pid])), max(0, round(v90[pid])), vl, g(meta["meta_portada"]), sin, g(meta["meta_paginas"]),
            ]

    version = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    cols = ["ref", "titulo", "autor", "editorial", "pvp", "categoria", "tema", "genero", "existencia", "v30", "v90", "v90_por_ubic", "portada", "sinopsis", "paginas"]
    datos = json.dumps({"v": version, "L": L, "c": cols, "p": P}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sobre = cifrar(gzip.compress(datos, 9), env["CLAVE_EXISTENCIAS"])
    sobre["v"] = version
    out = os.path.join(a.out, "fitness")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "datos.enc"), "w", encoding="utf-8") as f:
        json.dump(sobre, f, separators=(",", ":"))
    # Revisión de clasificación (app de Gerencia)
    datos = json.dumps({"v": version, "c": ["ref", "titulo", "autor", "editorial", "categoria", "tema", "genero", "existencia", "v90", "autor_odoo", "autor_metadatos", "nombre_producto", "id_plantilla", "categoria_completa"], "p": R},
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sobre2 = cifrar(gzip.compress(datos, 9), env["CLAVE_EXISTENCIAS"])
    sobre2["v"] = version
    os.makedirs(os.path.join(a.out, "clasificacion"), exist_ok=True)
    with open(os.path.join(a.out, "clasificacion", "datos.enc"), "w", encoding="utf-8") as f:
        json.dump(sobre2, f, separators=(",", ":"))
    print(f"Clasificación: {len(R):,} libros con existencia.")
    print(f"Fitness One: {len(P):,} libros candidatos en {len(L)} ubicaciones ({len(sobre['d']) // 1024:,} KB cifrados).")


if __name__ == "__main__":
    main()
