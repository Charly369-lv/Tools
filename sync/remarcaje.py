#!/usr/bin/env python3
"""
Lista de remarcaje semanal por tienda.

Compara el bazar y los precios de hoy (Odoo) con la foto de la semana anterior
(sync/remarcaje_estado.json) y, para cada tienda, lista los libros que tiene en
existencia y hay que volver a etiquetar:
  - Entró al bazar       → poner etiqueta de oferta con el precio de bazar
  - Cambió de porcentaje → cambiar la etiqueta (ej. 40% → 60%)
  - Salió del bazar      → quitar la oferta y volver al precio normal
  - Cambió el precio     → nuevo precio normal (o nuevo precio de bazar)
Si no hay foto anterior (primera vez), lista todo el bazar con existencia.

Orden: por editorial y, dentro de cada una, por la localidad del estante.

Uso:
  python sync/remarcaje.py --vista-previa carpeta/            # genera correos y Excel, no envía nada
  python sync/remarcaje.py --vista-previa carpeta/ --guardar-estado   # además guarda la foto de hoy
"""
import argparse
import collections
import json
import math
import os
import re
import subprocess
import sys
import time
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_odoo import Odoo, as_text, clean, find_field  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(HERE, "remarcaje_estado.json")
LOGO_URL = os.environ.get("LOGO_URL", "https://charly369-lv.github.io/Tools/checador/logo.png")
BAZAR = re.compile(r"BAZAR\D{0,3}(\d{1,2})\s*%", re.I)
MAX_FILAS_CORREO = 120  # el resto va en el Excel (Gmail recorta correos muy largos)

ACC = {
    "entra": ("PONER OFERTA", "#fde8e6", "#c42318"),
    "cambia": ("CAMBIAR %", "#fff3d1", "#9a6700"),
    "sale": ("QUITAR OFERTA", "#e7ebf7", "#112369"),
    "precio": ("NUEVO PRECIO", "#e3f4e8", "#157a3d"),
}
ORDEN_ACC = {"sale": 0, "entra": 1, "cambia": 2, "precio": 3}


def sucursal(loc):
    return as_text(loc).split("/")[0].strip()


def redondear(x):
    return int(math.floor(x + 0.5 + 1e-9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vista-previa", default="vista-previa-remarcaje")
    ap.add_argument("--guardar-estado", action="store_true", help="guardar la foto de hoy para la próxima comparación")
    a = ap.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    if not all(env.values()):
        raise SystemExit("Faltan los secretos de conexión.")
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()

    pf = o.call("product.product", "fields_get", [], {"attributes": ["string", "type"]})
    tt = ("char", "text", "many2one", "many2many", "selection")
    f_aut = find_field(pf, ["Autor", "AUTOR", "Autores"], tt)
    f_edi = find_field(pf, ["Editorial"], tt)
    f_loc = find_field(pf, ["Localidad", "Ubicación en estante", "Estante"], tt)
    price_field = "lst_price" if "lst_price" in pf else "list_price"
    print("Campos: autor", f_aut[:1], "editorial", f_edi[:1], "localidad de estante", f_loc[:1])

    campos = ["id", "default_code", "barcode", "name", "categ_id", price_field] + f_aut[:2] + f_edi[:1] + f_loc[:1]
    prods = o.search_read_all("product.product", [["active", "=", True]], campos)

    # Foto de hoy: {id: [precio, %bazar]}
    hoy = {}
    info = {}
    for p in prods:
        precio = float(p.get(price_field) or 0)
        m = BAZAR.search(as_text(p.get("categ_id")))
        pct = int(m.group(1)) if m and precio else 0
        hoy[str(p["id"])] = [round(precio, 2), pct]
        info[p["id"]] = p

    previo = None
    if os.path.exists(ESTADO):
        with open(ESTADO, encoding="utf-8") as f:
            previo = json.load(f)
    primera = previo is None
    ant = (previo or {}).get("p", {})
    print("Comparando con la foto del", previo.get("fecha") if previo else "— (primera vez: todo el bazar con existencia)")

    cambios = {}  # id → (acción, precio_ant, precio_nuevo, pct_ant, pct_nuevo)
    for sid, (precio, pct) in hoy.items():
        pid = int(sid)
        if primera:
            if pct:
                cambios[pid] = ("entra", precio, redondear(precio * (1 - pct / 100)), 0, pct)
            continue
        pa, pcta = ant.get(sid, [precio, 0])
        etiqueta_ant = redondear(pa * (1 - pcta / 100)) if pcta else pa
        etiqueta_hoy = redondear(precio * (1 - pct / 100)) if pct else precio
        if pct and not pcta:
            cambios[pid] = ("entra", etiqueta_ant, etiqueta_hoy, 0, pct)
        elif pcta and not pct:
            cambios[pid] = ("sale", etiqueta_ant, etiqueta_hoy, pcta, 0)
        elif pct and pct != pcta:
            cambios[pid] = ("cambia", etiqueta_ant, etiqueta_hoy, pcta, pct)
        elif abs(precio - pa) > 0.009 and etiqueta_ant != etiqueta_hoy:
            cambios[pid] = ("precio", etiqueta_ant, etiqueta_hoy, pcta, pct)
    print(f"  Cambios a remarcar en la cadena: {len(cambios):,} " + str(collections.Counter(c[0] for c in cambios.values())))

    # Existencias por tienda de los productos con cambios
    ids = sorted(cambios)
    EX = collections.defaultdict(collections.Counter)
    for i in range(0, len(ids), 2000):
        for q in o.search_read_all("stock.quant", [["product_id", "in", ids[i:i + 2000]], ["location_id.usage", "=", "internal"], ["quantity", ">", 0]],
                                   ["product_id", "location_id", "quantity"]):
            EX[q["product_id"][0]][sucursal(q["location_id"])] += float(q.get("quantity") or 0)

    def ficha(pid):
        p = info[pid]
        ref = clean(p.get("default_code") or "")
        name = clean(p.get("name") or "")
        if ref and name.startswith(f"[{ref}]"):
            name = name[len(ref) + 2:].strip()
        aut = next((as_text(p.get(f)) for f in f_aut if as_text(p.get(f))), "")
        edi = next((as_text(p.get(f)) for f in f_edi if as_text(p.get(f))), "") or "SIN EDITORIAL"
        loc = next((as_text(p.get(f)) for f in f_loc if as_text(p.get(f))), "")
        if edi and name.upper().startswith(edi.upper() + " "):
            name = name[len(edi) + 1:]
        return dict(titulo=name, autor=aut, editorial=edi, localidad=loc, ref=ref, isbn=re.sub(r"\D", "", str(p.get("barcode") or "")))

    tiendas = sorted({s for pid in ids for s, n in EX[pid].items() if n > 0})
    out = a.vista_previa
    os.makedirs(out, exist_ok=True)
    fecha = time.strftime("%d/%m/%Y")
    indice = []
    for t in tiendas:
        filas = []
        for pid in ids:
            n = EX[pid].get(t, 0)
            if n <= 0:
                continue
            acc, pa, pn, pcta, pctn = cambios[pid]
            filas.append(dict(ficha(pid), accion=acc, antes=pa, ahora=pn, pct_antes=pcta, pct=pctn, exist=int(round(n))))
        filas.sort(key=lambda f: (f["editorial"], f["localidad"] or "~", ORDEN_ACC[f["accion"]], f["titulo"]))
        cont = collections.Counter(f["accion"] for f in filas)
        fn = re.sub(r"[^A-Za-z0-9]", "_", t)
        with open(os.path.join(out, f"remarcaje_{fn}.html"), "w", encoding="utf-8") as f:
            f.write(correo(t, fecha, filas, cont, primera))
        excel(os.path.join(out, f"Remarcaje_{fn}.xlsx"), t, fecha, filas, primera)
        indice.append((t, fn, len(filas), cont))
        print(f"  {t}: {len(filas):,} libros por remarcar " + dict(cont).__repr__())
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(pagina_indice(fecha, indice, primera))

    if a.guardar_estado:
        with open(ESTADO, "w", encoding="utf-8") as f:
            json.dump({"fecha": fecha, "p": hoy}, f, separators=(",", ":"))
        print("Foto de hoy guardada para la próxima comparación.")


def q(x):
    """Precio en quetzales sin decimales innecesarios: 99.0 → Q99, 79.5 → Q79.50"""
    x = float(x or 0)
    return f"Q{int(round(x)):,}" if abs(x - round(x)) < 0.005 else f"Q{x:,.2f}"


def asunto(t, n, primera):
    return f"Remarcaje {'inicial' if primera else 'de la semana'} · {t}: {n:,} libro{'s' if n != 1 else ''}"


def correo(t, fecha, filas, cont, primera):
    S = "Arial, Helvetica, sans-serif"
    tiles = "".join(
        f'<td align="center" style="background:{bg};border-radius:8px;padding:10px 4px;font:11px {S};color:{fg}"><div style="font:bold 24px {S}">{cont.get(k, 0)}</div>{lab}</td><td width="4"></td>'
        for k, (lab, bg, fg) in ACC.items())

    def precio(f):
        antes = f'<s style="color:#5b6386">{q(f["antes"])}</s> → ' if f["antes"] != f["ahora"] else ""
        if f["accion"] == "sale":
            return f'{antes}<b style="font-size:16px">{q(f["ahora"])}</b><br><span style="font-size:11px;color:#5b6386">precio normal</span>'
        color = "#c42318" if f["pct"] else "#111a3d"
        nota = (f'−{f["pct"]}% (antes −{f["pct_antes"]}%)' if f["accion"] == "cambia" else (f'−{f["pct"]}%' if f["pct"] else "precio normal"))
        return f'{antes}<b style="font-size:16px;color:{color}">{q(f["ahora"])}</b><br><span style="font-size:11px;color:#5b6386">{nota}</span>'

    cuerpo, ed_actual, n = [], None, 0
    for f in filas[:MAX_FILAS_CORREO]:
        if f["editorial"] != ed_actual:
            ed_actual = f["editorial"]
            n_ed = sum(1 for x in filas if x["editorial"] == ed_actual)
            cuerpo.append(f'<tr><td colspan="2" style="background:#112369;color:#fff;font:bold 13px {S};padding:8px 10px">{escape(ed_actual)} · {n_ed}</td></tr>')
        lab, bg, fg = ACC[f["accion"]]
        cuerpo.append(f'''<tr style="background:{"#fff" if n % 2 else "#f3f4f9"}">
<td valign="top" style="padding:9px 6px 9px 10px;font:13px {S};color:#111a3d;overflow-wrap:anywhere">
<span style="font:bold 11px {S};color:#112369;background:#e9ecf6;padding:1px 5px;border-radius:3px">📍 {escape(f["localidad"] or "sin estante")}</span><br>
<b>{escape(f["titulo"])}</b><br><span style="font-size:11px;color:#5b6386">{escape(f["autor"])}<br>Ref. {escape(f["ref"])} · {escape(f["isbn"])} · <b>{f["exist"]} u.</b></span></td>
<td valign="top" align="right" style="padding:9px 10px 9px 4px;font:13px {S};color:#111a3d;width:118px">
<span style="display:inline-block;background:{bg};color:{fg};font:bold 10px {S};padding:3px 6px;border-radius:4px;margin-bottom:4px">{lab}</span><br>{precio(f)}</td></tr>''')
        n += 1
    resto = len(filas) - min(len(filas), MAX_FILAS_CORREO)
    nota_resto = f'<p style="font:bold 14px {S};color:#c42318;margin:12px 0 0">+ {resto:,} libros más en el Excel adjunto.</p>' if resto > 0 else ""
    intro = ("Primera lista: todos los libros en bazar que tienes en existencia, para revisar que tengan su etiqueta correcta. Desde la próxima semana llegarán solo los cambios."
             if primera else "Libros que cambiaron esta semana y que tienes en existencia.")
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(asunto(t, len(filas), primera))}</title></head>
<body style="margin:0;background:#f3f4f9"><div style="display:none">{len(filas)} libros por remarcar en {escape(t)}.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f9"><tr><td align="center" style="padding:16px 8px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#fff;border-radius:10px;overflow:hidden">
<tr><td style="background:#0b1748;padding:22px 24px"><img src="{LOGO_URL}" width="200" alt="De Museo" style="display:block;width:200px;height:auto;border:0">
<div style="font:bold 26px Georgia,serif;color:#fff;margin-top:14px">Remarcaje {"inicial" if primera else "de la semana"}</div>
<div style="font:14px {S};color:#c9cfe8;margin-top:4px">{fecha} · Tienda {escape(t)}</div></td></tr>
<tr><td style="padding:16px 24px 4px;font:14px {S};color:#111a3d">{intro}</td></tr>
<tr><td style="padding:10px 24px 14px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{tiles}</tr></table></td></tr>
<tr><td style="padding:0 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;table-layout:fixed">
<tr style="background:#e9ecf6"><td style="padding:6px 6px 6px 10px;font:bold 10px {S};color:#5b6386">ESTANTE Y LIBRO</td><td style="padding:6px 10px 6px 4px;font:bold 10px {S};color:#5b6386;width:118px;text-align:right">QUÉ HACER · ETIQUETA</td></tr>
{"".join(cuerpo)}</table>{nota_resto}</td></tr>
<tr><td style="padding:18px 24px;font:12px {S};color:#5b6386;line-height:1.5">📎 El Excel adjunto tiene la lista completa con una casilla para marcar cada libro remarcado; está listo para imprimir.<br>
Ordenado por editorial y por estante. "Etiqueta" es el precio que debe quedar en el libro.</td></tr>
<tr><td style="background:#f3f4f9;padding:14px 24px;font:11px {S};color:#5b6386;text-align:center">Correo automático · Uso interno de De Museo</td></tr>
</table></td></tr></table></body></html>'''


def excel(path, t, fecha, filas, primera):
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl"])
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Remarcar"
    ws["A1"] = f"Remarcaje {'inicial' if primera else 'de la semana'} · Tienda {t} · {fecha} · {len(filas):,} libros"
    ws["A1"].font = Font(bold=True, size=13, color="112369")
    head = ["✓", "Editorial", "Estante", "Título", "Autor", "Ref.", "ISBN", "Exist.", "Qué hacer", "Precio anterior", "Etiqueta nueva", "% bazar"]
    ws.append([])
    ws.append(head)
    fill = PatternFill("solid", fgColor="112369")
    for c in ws[3]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fill
        c.alignment = Alignment(vertical="center", wrap_text=True)
    caja = Border(*(Side(style="thin", color="5B6386"),) * 4)
    textos = {"entra": "Poner oferta", "cambia": "Cambiar %", "sale": "Quitar oferta", "precio": "Nuevo precio"}
    ed = None
    for f in filas:
        if f["editorial"] != ed:
            ed = f["editorial"]
            ws.append(["", ed])
            r = ws.max_row
            for c in ws[r]:
                c.fill = PatternFill("solid", fgColor="E9ECF6")
                c.font = Font(bold=True, color="112369")
        ws.append(["", f["editorial"], f["localidad"], f["titulo"], f["autor"], f["ref"], f["isbn"], f["exist"], textos[f["accion"]],
                   f["antes"], f["ahora"], (f["pct"] / 100) if f["pct"] else ""])
        r = ws.max_row
        ws.cell(r, 1).border = caja
        ws.cell(r, 10).number_format = '"Q"#,##0'
        ws.cell(r, 11).number_format = '"Q"#,##0'
        ws.cell(r, 11).font = Font(bold=True)
        ws.cell(r, 12).number_format = "0%"
    for i, w in enumerate([4, 18, 14, 40, 22, 9, 15, 6, 13, 11, 11, 8], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws.freeze_panes = "A4"
    ws.print_title_rows = "3:3"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.save(path)


def pagina_indice(fecha, indice, primera):
    filas = "".join(
        f'<tr><td><b>{escape(t)}</b></td><td><a href="remarcaje_{fn}.html">{escape(asunto(t, n, primera))}</a></td>'
        f'<td style="text-align:center">{c.get("entra", 0)}</td><td style="text-align:center">{c.get("cambia", 0)}</td>'
        f'<td style="text-align:center">{c.get("sale", 0)}</td><td style="text-align:center">{c.get("precio", 0)}</td>'
        f'<td><a href="Remarcaje_{fn}.xlsx">Excel</a></td></tr>' for t, fn, n, c in indice)
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vista previa · Remarcaje</title>
<style>body{{font:15px Arial,sans-serif;color:#111a3d;max-width:960px;margin:24px auto;padding:0 16px}}h1{{font-family:Georgia,serif}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #d9dcea;text-align:left}}th{{background:#112369;color:#fff}}a{{color:#112369}}</style></head>
<body><h1>Vista previa · Lista de remarcaje</h1><p>{fecha}. <b>Datos reales de Odoo.</b> No se envió ningún correo.
{"Primera lista: todo el bazar con existencia en cada tienda." if primera else "Cambios desde la semana anterior."}</p>
<table><tr><th>Tienda</th><th>Asunto del correo (clic para verlo)</th><th>Poner oferta</th><th>Cambiar %</th><th>Quitar oferta</th><th>Nuevo precio</th><th>Excel</th></tr>{filas}</table></body></html>'''


if __name__ == "__main__":
    main()
