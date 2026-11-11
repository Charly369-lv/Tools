#!/usr/bin/env python3
"""
Top 25 libros más vendidos de la cadena, con la existencia de cada sucursal.

Lee de Odoo (con los mismos secretos que la sincronización):
  - Ventas: movimientos de inventario terminados hacia clientes (punto de venta y ventas),
    menos las devoluciones de clientes. Sirve para todas las sucursales por igual.
  - Existencias: cantidades actuales por ubicación interna.

Reglas:
  - Solo libros (categoría que contenga "LIBRO") a precio normal: sin categorías de bazar.
  - Ranking por unidades: semana = últimos 7 días; mes = últimos 30 días.
  - Tendencia: posición contra el periodo anterior de igual duración.
  - Por agotarse: la existencia no alcanza para 7 días al ritmo de venta de la sucursal
    (promedio de los últimos 30 días), con un mínimo de 2 unidades.

Uso:
  python sync/top25.py --vista-previa salida/   # genera los correos como archivos, no envía nada
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_odoo import Odoo, as_text, clean, find_field  # noqa: E402
import correo_top25 as C  # noqa: E402

LOGO_URL = os.environ.get("LOGO_URL", "https://charly369-lv.github.io/Tools/checador/logo.png")
BAZAR = re.compile(r"BAZAR\D{0,3}\d{1,2}\s*%", re.I)
UTC = dt.timezone.utc
GT = dt.timezone(dt.timedelta(hours=-6))
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def sucursal(loc):
    """'MIRAF/Stock' → 'MIRAF'"""
    return as_text(loc).split("/")[0].strip()


def odoo_fecha(d):
    return d.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def rango_texto(ini, fin):
    a, b = ini.astimezone(GT), (fin - dt.timedelta(seconds=1)).astimezone(GT)
    if a.month == b.month:
        return f"{a.day} al {b.day} de {MESES[b.month - 1]} de {b.year}"
    return f"{a.day} de {MESES[a.month - 1]} al {b.day} de {MESES[b.month - 1]} de {b.year}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vista-previa", default="vista-previa-top25", help="carpeta donde se guardan los correos y el Excel")
    a = ap.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    if not all(env.values()):
        raise SystemExit("Faltan los secretos de conexión.")
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()

    # Corte: medianoche de hoy en Guatemala (el ranking cubre días completos)
    hoy = dt.datetime.now(GT).replace(hour=0, minute=0, second=0, microsecond=0)
    w0, w1 = hoy - dt.timedelta(days=7), hoy - dt.timedelta(days=14)
    m0, m1 = hoy - dt.timedelta(days=30), hoy - dt.timedelta(days=60)

    mf = o.call("stock.move", "fields_get", [], {"attributes": ["string", "type"]})
    qf = next((f for f in ("quantity", "quantity_done", "product_qty", "product_uom_qty") if f in mf), "product_uom_qty")
    print(f"Ventas: movimientos terminados desde {m1.date()} (cantidad: {qf})")
    base = [["state", "=", "done"], ["date", ">=", odoo_fecha(m1)], ["date", "<", odoo_fecha(hoy)]]
    salidas = o.search_read_all("stock.move", base + [["location_dest_id.usage", "=", "customer"], ["location_id.usage", "=", "internal"]],
                                ["product_id", "location_id", "date", qf])
    devol = o.search_read_all("stock.move", base + [["location_id.usage", "=", "customer"], ["location_dest_id.usage", "=", "internal"]],
                              ["product_id", "location_dest_id", "date", qf])

    # unidades[periodo][producto][sucursal]
    U = {k: collections.defaultdict(collections.Counter) for k in ("w0", "w1", "m0", "m1")}

    def sumar(pid, suc, fecha, q):
        f = dt.datetime.strptime(fecha[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        if f >= w0: U["w0"][pid][suc] += q
        elif f >= w1: U["w1"][pid][suc] += q
        if f >= m0: U["m0"][pid][suc] += q
        else: U["m1"][pid][suc] += q

    for mv in salidas:
        sumar(mv["product_id"][0], sucursal(mv["location_id"]), mv["date"], float(mv.get(qf) or 0))
    for mv in devol:
        sumar(mv["product_id"][0], sucursal(mv["location_dest_id"]), mv["date"], -float(mv.get(qf) or 0))
    print(f"  {len(salidas):,} salidas y {len(devol):,} devoluciones")

    # Datos de los productos vendidos (categoría, nombre, autor, editorial)
    pids = sorted(set().union(*[set(U[k]) for k in U]))
    pf = o.call("product.product", "fields_get", [], {"attributes": ["string", "type"]})
    tt = ("char", "text", "many2one", "many2many", "selection")
    f_aut = find_field(pf, ["Autor", "AUTOR", "Autores"], tt)
    f_edi = find_field(pf, ["Editorial"], tt)
    campos = ["id", "default_code", "barcode", "name", "categ_id"] + f_aut[:2] + f_edi[:1]
    prods = {}
    for i in range(0, len(pids), 2000):
        for p in o.search_read_all("product.product", [["id", "in", pids[i:i + 2000]], ["active", "in", [True, False]]], campos):
            prods[p["id"]] = p

    def es_libro(pid):
        p = prods.get(pid)
        if not p:
            return False
        cat = as_text(p.get("categ_id")).upper()
        return "LIBRO" in cat and not BAZAR.search(cat)

    def total(per, pid):
        return sum(U[per][pid].values())

    def ranking(per):
        r = sorted((pid for pid in U[per] if es_libro(pid) and total(per, pid) > 0), key=lambda x: (-total(per, x), as_text(prods[x].get("name"))))
        return r

    rk = {k: ranking(k) for k in U}
    pos_prev = {"w0": {pid: i + 1 for i, pid in enumerate(rk["w1"])}, "m0": {pid: i + 1 for i, pid in enumerate(rk["m1"])}}
    top = {"w0": rk["w0"][:25], "m0": rk["m0"][:25]}
    top_ids = sorted(set(top["w0"]) | set(top["m0"]))
    print(f"  Libros con ventas: semana {len(rk['w0']):,}, mes {len(rk['m0']):,}")

    # Existencias actuales por sucursal
    quants = o.search_read_all("stock.quant", [["product_id", "in", top_ids], ["location_id.usage", "=", "internal"]],
                               ["product_id", "location_id", "quantity"]) if top_ids else []
    EX = collections.defaultdict(collections.Counter)
    for q in quants:
        EX[q["product_id"][0]][sucursal(q["location_id"])] += float(q.get("quantity") or 0)

    # Sucursales: las que vendieron libros en el mes
    sucs = sorted({s for pid in rk["m0"] for s, n in U["m0"][pid].items() if n > 0})
    todas_ubic = sorted({s for pid in top_ids for s in EX[pid]} | set(sucs))

    def info(pid):
        p = prods[pid]
        ref = clean(p.get("default_code") or "")
        name = clean(p.get("name") or "")
        if ref and name.startswith(f"[{ref}]"):
            name = name[len(ref) + 2:].strip()
        aut = next((as_text(p.get(f)) for f in f_aut if as_text(p.get(f))), "")
        edi = next((as_text(p.get(f)) for f in f_edi if as_text(p.get(f))), "")
        if edi and name.upper().startswith(edi.upper() + " "):
            name = name[len(edi) + 1:]
        return name, aut, edi, ref, re.sub(r"\D", "", str(p.get("barcode") or ""))

    def filas(per, suc):
        out = []
        for i, pid in enumerate(top[per], 1):
            prev = pos_prev[per].get(pid)
            exist = int(round(EX[pid].get(suc, 0)))
            ritmo = U["m0"][pid].get(suc, 0) / 30 * 7  # unidades por semana en la sucursal
            minimo = max(2, math.ceil(ritmo))
            estado = "agotado" if exist <= 0 else ("bajo" if exist < minimo else "ok")
            tras = sorted(((s, int(round(n))) for s, n in EX[pid].items() if s != suc and n >= 1), key=lambda x: -x[1])
            t, au, ed, ref, isbn = info(pid)
            out.append(dict(pos=i, tend=None if prev is None else prev - i, titulo=t, autor=au, editorial=ed, ref=ref, isbn=isbn,
                            cadena=int(round(total(per, pid))), aqui=int(round(U[per][pid].get(suc, 0))), exist=exist,
                            estado=estado, trasladar=tras))
        return out

    out = a.vista_previa
    os.makedirs(out, exist_ok=True)
    periodo = "Semana del " + rango_texto(w0, hoy)
    indice = []
    for suc in sucs:
        fs = filas("w0", suc)
        ag = sum(f["estado"] == "agotado" for f in fs)
        bj = sum(f["estado"] == "bajo" for f in fs)
        html = C.render(suc, periodo, fs, LOGO_URL)
        fn = f"correo_{re.sub(r'[^A-Za-z0-9]', '_', suc)}.html"
        with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
            f.write(html)
        indice.append((suc, fn, C.asunto(suc, periodo, ag, bj), ag, bj))

    excel(out, top, pos_prev, total, info, U, EX, todas_ubic, w0, m0, hoy)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(pagina_indice(periodo, indice, top, total, info))
    print(f"\nVista previa lista: {len(sucs)} sucursales, top semana {len(top['w0'])}, top mes {len(top['m0'])}.")
    for suc, fn, asunto, ag, bj in indice:
        print(f"  {asunto}")


def excel(out, top, pos_prev, total, info, U, EX, ubic, w0, m0, hoy):
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl"])
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    for k, (per, nombre, ini) in enumerate([("w0", "Semana", w0), ("m0", "Mes (30 días)", m0)]):
        ws = wb.active if k == 0 else wb.create_sheet()
        ws.title = nombre
        ws["A1"] = f"Top 25 más vendidos · {nombre} · {rango_texto(ini, hoy)}"
        ws["A1"].font = Font(bold=True, size=14, color="112369")
        head = ["#", "Tendencia", "Título", "Autor", "Editorial", "Ref.", "ISBN", "Vendidos cadena"] + [f"Exist. {u}" for u in ubic] + [f"Vend. {u}" for u in ubic]
        ws.append([])
        ws.append(head)
        for c in ws[3]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="112369")
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for i, pid in enumerate(top[per], 1):
            prev = pos_prev[per].get(pid)
            tend = "Nuevo" if prev is None else (f"▲{prev - i}" if prev > i else (f"▼{i - prev}" if prev < i else "="))
            t, au, ed, ref, isbn = info(pid)
            ws.append([i, tend, t, au, ed, ref, isbn, int(round(total(per, pid)))]
                      + [int(round(EX[pid].get(u, 0))) for u in ubic] + [int(round(U[per][pid].get(u, 0))) for u in ubic])
        for j, w in enumerate([4, 10, 40, 24, 18, 10, 15, 10] + [8] * (2 * len(ubic)), 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = "D4"
    wb.save(os.path.join(out, "Top25.xlsx"))


def pagina_indice(periodo, indice, top, total, info):
    filas = "".join(
        f'<tr><td><b>{escape(s)}</b></td><td><a href="{fn}">{escape(asunto)}</a></td>'
        f'<td style="color:#c42318;text-align:center">{ag}</td><td style="color:#9a6700;text-align:center">{bj}</td></tr>'
        for s, fn, asunto, ag, bj in indice)
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vista previa · Top 25</title>
<style>body{{font:15px Arial,sans-serif;color:#111a3d;max-width:900px;margin:24px auto;padding:0 16px}}h1{{font-family:Georgia,serif}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #d9dcea;text-align:left}}th{{background:#112369;color:#fff}}a{{color:#112369}}</style></head>
<body><h1>Vista previa del correo · Top 25</h1><p>{escape(periodo)}. <b>Datos reales de Odoo.</b> No se envió ningún correo: aquí ves lo que recibiría cada sucursal.</p>
<p>📎 <a href="Top25.xlsx">Top25.xlsx</a>: ranking de la semana y del mes con existencias y ventas de todas las ubicaciones.</p>
<table><tr><th>Sucursal</th><th>Asunto del correo (clic para verlo)</th><th>Agotados</th><th>Por agotarse</th></tr>{filas}</table>
</body></html>'''


if __name__ == "__main__":
    main()
