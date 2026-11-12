#!/usr/bin/env python3
"""
Ranking mensual de ventas del Bazar de Descuentos por tienda.

Fuente: líneas de venta del punto de venta de Odoo (lo que realmente se cobró).
  - Tienda: la ubicación de la caja (configuración del punto de venta → tipo de operación →
    ubicación de origen, ej. MIRAF/Stock → MIRAF).
  - Venta de bazar: se cobró con 35% o más de descuento sobre el precio normal del producto.
    (Odoo no guarda la categoría que tenía el libro el día de la venta; el precio cobrado sí.)
  - % de descuento de la venta: se asigna al porcentaje de bazar más cercano (40, 60, 75…).

Métricas por tienda (mes cerrado anterior):
  - Unidades e ingreso del bazar (con IVA, como en el ticket).
  - % de liquidación = unidades vendidas / (unidades vendidas + existencia actual de bazar).
  - Comparación con el mes anterior y acumulado del año.
Las tiendas sin ventas de bazar en el mes salen aparte ("aún sin ventas de bazar").

Uso:
  python sync/ranking_bazar.py --vista-previa carpeta/
"""
import argparse
import collections
import datetime as dt
import os
import re
import subprocess
import sys
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync_odoo import Odoo, as_text, clean, find_field  # noqa: E402

LOGO_URL = os.environ.get("LOGO_URL", "https://charly369-lv.github.io/Tools/checador/logo.png")
BAZAR = re.compile(r"BAZAR\D{0,3}(\d{1,2})\s*%", re.I)
UTC = dt.timezone.utc
GT = dt.timezone(dt.timedelta(hours=-6))
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
UMBRAL = 0.35
S = "Arial, Helvetica, sans-serif"


def q(x):
    return f"Q{x:,.0f}"


def sucursal(loc):
    return as_text(loc).split("/")[0].strip()


def utc_str(d):
    return d.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def mes_ini(y, m):
    return dt.datetime(y, m, 1, tzinfo=GT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vista-previa", default="vista-previa-ranking")
    ap.add_argument("--mes", help="AAAA-MM a reportar (por omisión, el mes anterior)")
    a = ap.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    if not all(env.values()):
        raise SystemExit("Faltan los secretos de conexión.")
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()

    ahora = dt.datetime.now(GT)
    if a.mes:
        y, m = map(int, a.mes.split("-"))
    else:
        y, m = (ahora.year, ahora.month - 1) if ahora.month > 1 else (ahora.year - 1, 12)
    ini, fin = mes_ini(y, m), (mes_ini(y + (m == 12), 1 if m == 12 else m + 1))
    ini_ant = mes_ini(y - (m == 1), 12 if m == 1 else m - 1)
    ini_ano = mes_ini(y, 1)
    desde = min(ini_ant, ini_ano)
    nombre_mes = f"{MESES[m - 1]} {y}"
    print(f"Mes reportado: {nombre_mes} · ventas desde {desde.date()}")

    # Caja → tienda
    configs = o.search_read_all("pos.config", [], ["name", "picking_type_id"])
    tipos = {t["id"]: t for t in o.search_read_all("stock.picking.type", [["id", "in", [c["picking_type_id"][0] for c in configs if c.get("picking_type_id")]]],
                                                     ["default_location_src_id"])} if configs else {}
    tienda_de = {}
    for c in configs:
        t = tipos.get(c["picking_type_id"][0]) if c.get("picking_type_id") else None
        tienda_de[c["id"]] = sucursal(t["default_location_src_id"]) if t and t.get("default_location_src_id") else clean(c["name"])
    print(f"  {len(configs)} cajas: " + ", ".join(sorted(set(tienda_de.values()))))

    # Pedidos del punto de venta del periodo (fecha y caja)
    pedidos = {p["id"]: p for p in o.search_read_all("pos.order", [["date_order", ">=", utc_str(desde)], ["date_order", "<", utc_str(fin)],
                                                                     ["state", "in", ["paid", "done", "invoiced"]]], ["date_order", "config_id"])}
    print(f"  {len(pedidos):,} tickets")
    lineas = []
    ids_ped = sorted(pedidos)
    for i in range(0, len(ids_ped), 3000):
        lineas += o.search_read_all("pos.order.line", [["order_id", "in", ids_ped[i:i + 3000]]], ["order_id", "product_id", "qty", "price_subtotal_incl"])
    print(f"  {len(lineas):,} líneas de venta")

    # Productos vendidos: precio normal, categoría, datos para el top
    pids = sorted({l["product_id"][0] for l in lineas if l.get("product_id")})
    pf = o.call("product.product", "fields_get", [], {"attributes": ["string", "type"]})
    tt = ("char", "text", "many2one", "many2many", "selection")
    f_aut, f_edi = find_field(pf, ["Autor", "AUTOR"], tt), find_field(pf, ["Editorial"], tt)
    price_field = "lst_price" if "lst_price" in pf else "list_price"
    prods = {}
    for i in range(0, len(pids), 2000):
        for p in o.search_read_all("product.product", [["id", "in", pids[i:i + 2000]], ["active", "in", [True, False]]],
                                   ["id", "default_code", "barcode", "name", "categ_id", price_field] + f_aut[:1] + f_edi[:1]):
            prods[p["id"]] = p
    pcts = sorted({int(mt.group(1)) for p in prods.values() for mt in [BAZAR.search(as_text(p.get("categ_id")))] if mt}) or [40, 60, 75]

    # Clasificar ventas de bazar
    V = collections.defaultdict(lambda: collections.defaultdict(lambda: {"u": 0.0, "q": 0.0}))  # V[mes][tienda]
    POR_PCT = collections.defaultdict(lambda: collections.defaultdict(float))   # [tienda][pct] unidades del mes
    TOP = collections.defaultdict(collections.Counter)                          # [tienda][pid] unidades del mes
    for l in lineas:
        ped = pedidos.get(l["order_id"][0]) if l.get("order_id") else None
        p = prods.get(l["product_id"][0]) if l.get("product_id") else None
        qty = float(l.get("qty") or 0)
        if not ped or not p or qty == 0:
            continue
        normal = float(p.get(price_field) or 0)
        if normal <= 0:
            continue
        cobrado = float(l.get("price_subtotal_incl") or 0) / qty
        desc = 1 - cobrado / normal
        if desc < UMBRAL or desc > 0.99:
            continue
        f = dt.datetime.strptime(ped["date_order"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).astimezone(GT)
        clave = f"{f.year}-{f.month:02d}"
        t = tienda_de.get(ped["config_id"][0]) if ped.get("config_id") else "?"
        V[clave][t]["u"] += qty
        V[clave][t]["q"] += float(l.get("price_subtotal_incl") or 0)
        if ini <= f < fin:
            pct = min(pcts, key=lambda x: abs(x - desc * 100))
            POR_PCT[t][pct] += qty
            TOP[t][p["id"]] += qty

    # Existencia actual de bazar por tienda (para el % de liquidación)
    bz_ids = [p["id"] for p in o.search_read_all("product.product", [["active", "=", True], ["categ_id.complete_name", "ilike", "BAZAR"]], ["id"])]
    EXB = collections.Counter()
    for i in range(0, len(bz_ids), 3000):
        for qd in o.search_read_all("stock.quant", [["product_id", "in", bz_ids[i:i + 3000]], ["location_id.usage", "=", "internal"], ["quantity", ">", 0]],
                                    ["location_id", "quantity"]):
            EXB[sucursal(qd["location_id"])] += float(qd.get("quantity") or 0)

    km, ka = f"{y}-{m:02d}", f"{ini_ant.year}-{ini_ant.month:02d}"
    meses_ano = [f"{y}-{i:02d}" for i in range(1, m + 1)]
    tiendas = sorted(set(tienda_de.values()))

    def fila(t):
        u, ing = V[km][t]["u"], V[km][t]["q"]
        ex = EXB.get(t, 0)
        liq = u / (u + ex) if (u + ex) > 0 else 0
        return dict(tienda=t, u=int(round(u)), q=ing, ex=int(round(ex)), liq=liq,
                    u_ant=int(round(V[ka][t]["u"])), q_ant=V[ka][t]["q"],
                    u_ano=int(round(sum(V[k][t]["u"] for k in meses_ano))), q_ano=sum(V[k][t]["q"] for k in meses_ano))

    filas = [fila(t) for t in tiendas]
    activas = sorted([f for f in filas if f["u"] > 0], key=lambda f: (-f["liq"], -f["u"]))
    sin = sorted([f for f in filas if f["u"] <= 0], key=lambda f: f["tienda"])
    # posición del mes anterior (para ▲▼)
    prev = sorted([t for t in tiendas if V[ka][t]["u"] > 0], key=lambda t: -(V[ka][t]["u"] / (V[ka][t]["u"] + EXB.get(t, 0) or 1)))
    pos_ant = {t: i + 1 for i, t in enumerate(prev)}
    for i, f in enumerate(activas, 1):
        f["pos"] = i
        f["tend"] = (pos_ant[f["tienda"]] - i) if f["tienda"] in pos_ant else None

    def ficha(pid):
        p = prods[pid]
        ref = clean(p.get("default_code") or "")
        name = clean(p.get("name") or "")
        if ref and name.startswith(f"[{ref}]"):
            name = name[len(ref) + 2:].strip()
        edi = next((as_text(p.get(x)) for x in f_edi if as_text(p.get(x))), "")
        if edi and name.upper().startswith(edi.upper() + " "):
            name = name[len(edi) + 1:]
        return name, next((as_text(p.get(x)) for x in f_aut if as_text(p.get(x))), ""), edi, ref

    out = a.vista_previa
    os.makedirs(out, exist_ok=True)
    tot = dict(u=sum(f["u"] for f in filas), q=sum(f["q"] for f in filas), u_ant=sum(f["u_ant"] for f in filas), q_ano=sum(f["q_ano"] for f in filas))
    with open(os.path.join(out, "gerencia.html"), "w", encoding="utf-8") as fh:
        fh.write(correo_gerencia(nombre_mes, activas, sin, tot, V, meses_ano, tiendas, POR_PCT, pcts))
    for f in activas + sin:
        top = [(ficha(pid), int(round(n))) for pid, n in TOP[f["tienda"]].most_common(10)]
        fn = re.sub(r"[^A-Za-z0-9]", "_", f["tienda"])
        with open(os.path.join(out, f"tienda_{fn}.html"), "w", encoding="utf-8") as fh:
            fh.write(correo_tienda(nombre_mes, f, len(activas), POR_PCT[f["tienda"]], pcts, top))
    excel(os.path.join(out, f"Ranking_bazar_{km}.xlsx"), nombre_mes, activas, sin, V, meses_ano, tiendas, POR_PCT, pcts, TOP, ficha)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(indice(nombre_mes, activas, sin, km))
    print(f"\nListo: {len(activas)} tiendas con ventas de bazar, {len(sin)} sin ventas. Total {tot['u']:,} unidades, {q(tot['q'])}.")
    for f in activas:
        print(f"  #{f['pos']} {f['tienda']}: {f['liq']:.0%} liquidación · {f['u']:,} u. · {q(f['q'])}")


def tend(t):
    if t is None:
        return '<span style="color:#112369">★ nueva</span>'
    if t > 0:
        return f'<span style="color:#157a3d">▲{t}</span>'
    if t < 0:
        return f'<span style="color:#c42318">▼{-t}</span>'
    return '<span style="color:#5b6386">=</span>'


def var(a, b):
    if not b:
        return ""
    d = (a - b) / b
    color = "#157a3d" if d >= 0 else "#c42318"
    return f'<span style="color:{color};font-size:11px">{"▲" if d >= 0 else "▼"}{abs(d):.0%}</span>'


def marco(titulo, sub, cuerpo):
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(titulo)}</title></head>
<body style="margin:0;background:#f3f4f9"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f9"><tr><td align="center" style="padding:16px 8px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#fff;border-radius:10px;overflow:hidden">
<tr><td style="background:#0b1748;padding:22px 24px"><img src="{LOGO_URL}" width="200" alt="De Museo" style="display:block;width:200px;height:auto;border:0">
<div style="font:bold 25px Georgia,serif;color:#fff;margin-top:14px">{escape(titulo)}</div><div style="font:14px {S};color:#c9cfe8;margin-top:4px">{escape(sub)}</div></td></tr>
{cuerpo}
<tr><td style="padding:16px 24px;font:12px {S};color:#5b6386;line-height:1.5"><b>Cómo se calcula:</b> ventas del punto de venta cobradas con 35% o más de descuento (precio de bazar), con IVA.
% de liquidación = unidades vendidas en el mes ÷ (vendidas + existencia de bazar actual). Compara de forma justa tiendas grandes y pequeñas.</td></tr>
<tr><td style="background:#f3f4f9;padding:14px 24px;font:11px {S};color:#5b6386;text-align:center">Correo automático · Uso interno de De Museo</td></tr>
</table></td></tr></table></body></html>'''


def kpis(items):
    tds = "".join(f'<td align="center" style="background:{bg};border-radius:8px;padding:10px 4px;font:11px {S};color:{fg}"><div style="font:bold 22px {S}">{v}</div>{lab}</td><td width="4"></td>'
                  for v, lab, bg, fg in items)
    return f'<tr><td style="padding:16px 24px 8px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{tds}</tr></table></td></tr>'


def correo_gerencia(mes, activas, sin, tot, V, meses_ano, tiendas, POR_PCT, pcts):
    filas = "".join(f'''<tr style="background:{"#fff" if f["pos"] % 2 else "#f3f4f9"}">
<td style="padding:9px 4px 9px 10px;font:bold 18px {S};color:#112369;width:40px">{f["pos"]}<br><span style="font:11px {S}">{tend(f["tend"])}</span></td>
<td style="padding:9px 4px;font:bold 14px {S};color:#111a3d">{escape(f["tienda"])}<br><span style="font:11px {S};color:#5b6386">exist. bazar {f["ex"]:,} u.</span></td>
<td align="right" style="padding:9px 4px;font:bold 16px {S};color:#111a3d">{f["liq"]:.0%}</td>
<td align="right" style="padding:9px 4px;font:14px {S};color:#111a3d">{f["u"]:,}<br>{var(f["u"], f["u_ant"])}</td>
<td align="right" style="padding:9px 10px 9px 4px;font:14px {S};color:#111a3d">{q(f["q"])}<br><span style="font-size:11px;color:#5b6386">año {q(f["q_ano"])}</span></td></tr>''' for f in activas)
    sin_txt = (f'<tr><td style="padding:12px 24px 0;font:13px {S};color:#5b6386"><b>Aún sin ventas de bazar en {escape(mes)}:</b> {escape(", ".join(f["tienda"] for f in sin))}</td></tr>' if sin else "")
    cab = "".join(f'<td align="right" style="padding:6px 4px;font:bold 10px {S};color:#5b6386">{MESES[int(k[5:]) - 1][:3].upper()}</td>' for k in meses_ano[-6:])
    evol = "".join(f'<tr><td style="padding:5px 4px 5px 10px;font:12px {S};color:#111a3d">{escape(t)}</td>' +
                   "".join(f'<td align="right" style="padding:5px 4px;font:12px {S};color:#111a3d">{int(round(V[k][t]["u"])):,}</td>' for k in meses_ano[-6:]) + "</tr>"
                   for t in [f["tienda"] for f in activas])
    pct_tot = collections.Counter()
    for t in POR_PCT:
        for p, n in POR_PCT[t].items():
            pct_tot[p] += n
    pct_txt = " · ".join(f"<b>{p}%</b>: {int(round(pct_tot[p])):,} u." for p in pcts if pct_tot[p])
    cuerpo = kpis([(f'{tot["u"]:,}', "UNIDADES", "#fde8e6", "#c42318"), (q(tot["q"]), "INGRESO", "#fff3d1", "#9a6700"),
                   (f'{len(activas)}', "TIENDAS CON VENTAS", "#e3f4e8", "#157a3d")]) + f'''
<tr><td style="padding:4px 24px;font:13px {S};color:#111a3d">Por descuento: {pct_txt or "—"} · Mes anterior: {tot["u_ant"]:,} u. · Año: {q(tot["q_ano"])}</td></tr>
<tr><td style="padding:14px 12px 4px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
<tr style="background:#112369"><td style="padding:7px 4px 7px 10px;font:bold 10px {S};color:#fff">#</td><td style="padding:7px 4px;font:bold 10px {S};color:#fff">TIENDA</td>
<td align="right" style="padding:7px 4px;font:bold 10px {S};color:#fff">% LIQUID.</td><td align="right" style="padding:7px 4px;font:bold 10px {S};color:#fff">UNIDADES</td>
<td align="right" style="padding:7px 10px 7px 4px;font:bold 10px {S};color:#fff">INGRESO</td></tr>{filas}</table></td></tr>
{sin_txt}
<tr><td style="padding:18px 24px 4px;font:bold 12px {S};color:#5b6386;letter-spacing:1px">UNIDADES DE BAZAR POR MES</td></tr>
<tr><td style="padding:0 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
<tr style="background:#e9ecf6"><td style="padding:6px 4px 6px 10px;font:bold 10px {S};color:#5b6386">TIENDA</td>{cab}</tr>{evol}</table></td></tr>
<tr><td style="padding:12px 24px 0;font:12px {S};color:#5b6386">📎 Excel adjunto: ranking, evolución mensual (unidades e ingreso), ventas por descuento y top 10 por tienda.</td></tr>'''
    return marco("Ranking del Bazar · Gerencia", f"{mes[0].upper() + mes[1:]} · todas las tiendas", cuerpo)


def correo_tienda(mes, f, n_activas, por_pct, pcts, top):
    if f["u"] > 0:
        lugar = f'<tr><td style="padding:18px 24px 0;font:bold 22px Georgia,serif;color:#112369">Tu tienda quedó #{f["pos"]} de {n_activas} {tend(f["tend"])}</td></tr>'
    else:
        lugar = f'<tr><td style="padding:18px 24px 0;font:15px {S};color:#111a3d">Este mes tu tienda todavía no registró ventas de bazar. Cuando montes el bazar, aquí verás tu avance.</td></tr>'
    pct_txt = " · ".join(f"<b>{p}%</b>: {int(round(por_pct[p])):,} u." for p in pcts if por_pct.get(p))
    filas = "".join(f'''<tr style="background:{"#fff" if i % 2 else "#f3f4f9"}"><td style="padding:8px 4px 8px 10px;font:bold 15px {S};color:#112369;width:28px">{i}</td>
<td style="padding:8px 4px;font:13px {S};color:#111a3d"><b>{escape(t)}</b><br><span style="font-size:11px;color:#5b6386">{escape(au)}{" · " + escape(ed) if ed else ""} · Ref. {escape(ref)}</span></td>
<td align="right" style="padding:8px 10px 8px 4px;font:bold 15px {S};color:#111a3d">{n:,}</td></tr>''' for i, ((t, au, ed, ref), n) in enumerate(top, 1))
    recuadros = kpis([(f'{f["liq"]:.0%}', "LIQUIDACIÓN", "#e3f4e8", "#157a3d"), (f'{f["u"]:,}', "UNIDADES", "#fde8e6", "#c42318"),
                      (q(f["q"]), "INGRESO", "#fff3d1", "#9a6700")]) if f["u"] > 0 else ""
    cuerpo = lugar + recuadros + f'''
<tr><td style="padding:4px 24px;font:13px {S};color:#111a3d">Mes anterior: {f["u_ant"]:,} u. {var(f["u"], f["u_ant"])} · Año: {f["u_ano"]:,} u., {q(f["q_ano"])}<br>
Existencia de bazar hoy: {f["ex"]:,} u.{(" · Por descuento: " + pct_txt) if pct_txt else ""}</td></tr>''' + (f'''
<tr><td style="padding:16px 24px 4px;font:bold 12px {S};color:#5b6386;letter-spacing:1px">TUS 10 LIBROS DE BAZAR MÁS VENDIDOS</td></tr>
<tr><td style="padding:0 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{filas}</table></td></tr>''' if top else "")
    return marco(f"Bazar de Descuentos · {f['tienda']}", f"Resultados de {mes}", cuerpo)


def excel(path, mes, activas, sin, V, meses_ano, tiendas, POR_PCT, pcts, TOP, ficha):
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl"])
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()

    def hoja(ws, titulo, head, rows, fmts=None):
        ws["A1"] = titulo
        ws["A1"].font = Font(bold=True, size=13, color="112369")
        ws.append([])
        ws.append(head)
        for c in ws[3]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="112369")
        for r in rows:
            ws.append(r)
        for col, fmt in (fmts or {}).items():
            for c in ws[col][3:]:
                c.number_format = fmt
        for i in range(1, len(head) + 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 14 if i > 1 else 18
        ws.freeze_panes = "B4"

    ws = wb.active
    ws.title = "Ranking"
    hoja(ws, f"Ranking del Bazar · {mes}", ["Posición", "Tienda", "% liquidación", "Unidades", "Ingreso (Q)", "Exist. bazar", "Unid. mes ant.", "Unid. año", "Ingreso año (Q)"],
         [[f["pos"], f["tienda"], f["liq"], f["u"], round(f["q"], 2), f["ex"], f["u_ant"], f["u_ano"], round(f["q_ano"], 2)] for f in activas]
         + [["Sin ventas", f["tienda"], 0, 0, 0, f["ex"], f["u_ant"], f["u_ano"], round(f["q_ano"], 2)] for f in sin],
         {"C": "0%", "E": '"Q"#,##0', "I": '"Q"#,##0'})
    nm = [MESES[int(k[5:]) - 1].capitalize() for k in meses_ano]
    hoja(wb.create_sheet("Unidades por mes"), "Unidades de bazar por mes", ["Tienda"] + nm,
         [[t] + [int(round(V[k][t]["u"])) for k in meses_ano] for t in tiendas])
    hoja(wb.create_sheet("Ingreso por mes"), "Ingreso de bazar por mes (Q, con IVA)", ["Tienda"] + nm,
         [[t] + [round(V[k][t]["q"], 2) for k in meses_ano] for t in tiendas])
    hoja(wb.create_sheet("Por descuento"), f"Unidades por descuento · {mes}", ["Tienda"] + [f"{p}%" for p in pcts],
         [[t] + [int(round(POR_PCT[t].get(p, 0))) for p in pcts] for t in tiendas])
    rows = []
    for f in activas:
        for i, (pid, n) in enumerate(TOP[f["tienda"]].most_common(10), 1):
            t, au, ed, ref = ficha(pid)
            rows.append([f["tienda"], i, t, au, ed, ref, int(round(n))])
    hoja(wb.create_sheet("Top 10 por tienda"), f"Top 10 de bazar por tienda · {mes}", ["Tienda", "#", "Título", "Autor", "Editorial", "Ref.", "Unidades"], rows)
    wb.save(path)


def indice(mes, activas, sin, km):
    filas = "".join(f'<tr><td>#{f["pos"]}</td><td><a href="tienda_{re.sub(r"[^A-Za-z0-9]", "_", f["tienda"])}.html">{escape(f["tienda"])}</a></td><td>{f["liq"]:.0%}</td><td>{f["u"]:,}</td><td>{q(f["q"])}</td></tr>' for f in activas)
    filas += "".join(f'<tr><td>—</td><td><a href="tienda_{re.sub(r"[^A-Za-z0-9]", "_", f["tienda"])}.html">{escape(f["tienda"])}</a></td><td colspan="3">Aún sin ventas de bazar</td></tr>' for f in sin)
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vista previa · Ranking del Bazar</title>
<style>body{{font:15px Arial,sans-serif;color:#111a3d;max-width:900px;margin:24px auto;padding:0 16px}}h1{{font-family:Georgia,serif}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border-bottom:1px solid #d9dcea;text-align:left}}th{{background:#112369;color:#fff}}a{{color:#112369}}</style></head>
<body><h1>Vista previa · Ranking del Bazar · {escape(mes)}</h1><p><b>Datos reales de Odoo.</b> No se envió ningún correo.</p>
<p>👔 <a href="gerencia.html">Correo de gerencia</a> · 📎 <a href="Ranking_bazar_{km}.xlsx">Excel</a></p>
<table><tr><th>#</th><th>Tienda (clic para ver su correo)</th><th>% liquidación</th><th>Unidades</th><th>Ingreso</th></tr>{filas}</table></body></html>'''


if __name__ == "__main__":
    main()
