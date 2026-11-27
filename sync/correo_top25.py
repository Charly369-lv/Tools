"""
Plantilla del correo semanal "Top 25 más vendidos" para cada sucursal.

Buenas prácticas de correo aplicadas:
- Tablas y estilos en línea (Gmail y Outlook ignoran <style> y CSS moderno).
- Una sola columna de 600 px que se adapta al celular.
- Lo urgente primero: resumen de acciones de la sucursal antes de la tabla.
- Colores con texto (no solo color) para que se entienda en blanco y negro o en modo oscuro.
- Texto previo (preheader) con el resumen, que se ve en la bandeja de entrada.
"""
from html import escape

from tiendas import nombre

NAVY, NAVY_DEEP, GOLD, RED, AMBER, GREEN, INK, MUTED, LINE, SOFT = (
    "#112369", "#0b1748", "#f2bd3c", "#c42318", "#9a6700", "#157a3d", "#111a3d", "#5b6386", "#d9dcea", "#f3f4f9")
SERIF = "'Bodoni Moda', Georgia, 'Times New Roman', serif"   # Bodoni de la marca; Georgia si el correo no la carga
SANS = "Arial, Helvetica, sans-serif"

ESTADO = {
    "agotado": ("Agotado", "#fde8e6", RED),
    "bajo": ("Por agotarse", "#fff3d1", AMBER),
    "ok": ("Con existencia", "#e3f4e8", GREEN),
}


def tendencia(t):
    if t is None:
        return f'<span style="color:{NAVY};font-weight:bold">nuevo</span>'
    if t > 0:
        return f'<span style="color:{GREEN};font-weight:bold">▲ {t}</span>'
    if t < 0:
        return f'<span style="color:{RED};font-weight:bold">▼ {abs(t)}</span>'
    return f'<span style="color:{MUTED}">igual</span>'


def asunto(suc, periodo, n_agot, n_bajo):
    alerta = []
    if n_agot:
        alerta.append(f"{n_agot} agotado{'s' if n_agot > 1 else ''}")
    if n_bajo:
        alerta.append(f"{n_bajo} por agotarse")
    # Lo importante primero: en la bandeja del celular solo se ven unos 40 caracteres
    return f"{nombre(suc)}: " + (" y ".join(alerta) if alerta else "todo con existencia") + f" en el Top 25 ({periodo})"


def render(suc, periodo, filas, logo_url, ejemplo=False):
    """filas: lista de dicts con pos, tend, titulo, autor, editorial, ref, isbn, cadena, aqui, exist, estado, dias, trasladar"""
    agot = [f for f in filas if f["estado"] == "agotado"]
    bajo = [f for f in filas if f["estado"] == "bajo"]
    ok = len(filas) - len(agot) - len(bajo)
    pre = (f"{len(agot)} agotados y {len(bajo)} por agotarse entre los 25 más vendidos de la cadena."
           if agot or bajo else "Tienes existencia de los 25 más vendidos de la cadena.")

    def badge(estado):
        t, bg, fg = ESTADO[estado]
        return (f'<span style="display:inline-block;background:{bg};color:{fg};font:bold 12px {SANS};'
                f'padding:3px 5px;border-radius:4px">{t}</span>')

    def accion(f):
        src = ", ".join(f"{escape(nombre(s))} ({n})" for s, n in f["trasladar"][:2]) or "pedir al proveedor"
        return (f'<tr><td style="padding:6px 0;border-bottom:1px solid {LINE};font:14px {SANS};color:{INK}">'
                f'<b>#{f["pos"]} {escape(f["titulo"])}</b><br>'
                f'<span style="color:{MUTED};font-size:12px">Ref. {escape(f["ref"])}. Tienes {f["exist"]} y '
                f'vendiste {f["aqui"]} esta semana.</span><br>'
                f'<span style="font-size:13px">Trasladar de <b>{src}</b></span></td></tr>')

    acciones = agot + bajo
    bloque_acciones = ""
    if acciones:
        bloque_acciones = f'''
<tr><td style="padding:0 24px 8px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:2px solid {RED};border-radius:8px">
    <tr><td style="padding:14px 16px 4px;font:bold 16px {SANS};color:{RED}">Qué hacer esta semana en {escape(nombre(suc))}</td></tr>
    <tr><td style="padding:0 16px 12px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(accion(f) for f in acciones)}</table>
    </td></tr>
  </table>
</td></tr>'''

    def fila(f):
        bg = "#fff" if f["pos"] % 2 else SOFT
        tras = ", ".join(f"{escape(nombre(s))} {n}" for s, n in f["trasladar"][:2]) if f["estado"] != "ok" else ""
        return f'''<tr style="background:{bg}">
  <td valign="top" style="padding:10px 2px 10px 8px;font:bold 18px {SANS};color:{NAVY}">{f["pos"]}<br><span style="font:11px {SANS}">{tendencia(f["tend"])}</span></td>
  <td valign="top" style="padding:10px 4px;font:13px {SANS};color:{INK};word-wrap:break-word;overflow-wrap:anywhere"><b>{escape(f["titulo"])}</b><br>
    <span style="font-size:12px;color:{MUTED}">{escape(f["autor"])}<br>Ref. {escape(f["ref"])}</span></td>
  <td valign="top" align="center" style="padding:10px 2px;font:bold 15px {SANS};color:{INK}">{f["cadena"]}</td>
  <td valign="top" align="center" style="padding:10px 2px;font:15px {SANS};color:{INK}">{f["aqui"]}</td>
  <td valign="top" align="center" style="padding:10px 8px 10px 2px;font:bold 15px {SANS};color:{INK};overflow-wrap:anywhere">{f["exist"]}{("<br>" + badge(f["estado"])) if f["estado"] != "ok" else ""}{f'<br><span style="font:12px {SANS};color:{MUTED}">Hay en {tras}</span>' if tras else ""}</td>
</tr>'''

    banner = (f'<tr><td style="background:{GOLD};padding:8px 24px;font:bold 13px {SANS};color:{NAVY_DEEP};text-align:center">'
              f'Ejemplo: ventas y existencias inventadas para ver el diseño</td></tr>') if ejemplo else ""

    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(asunto(suc, periodo, len(agot), len(bajo)))}</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,700&display=swap" rel="stylesheet"></head>
<body style="margin:0;padding:0;background:{SOFT}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(pre)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SOFT}"><tr><td align="center" style="padding:16px 8px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#fff;border-radius:10px;overflow:hidden">
  {banner}
  <tr><td style="background:{NAVY_DEEP};padding:22px 24px">
    <img src="{logo_url}" width="200" alt="De Museo" style="display:block;width:200px;height:auto;border:0">
    <div style="font:bold 26px {SERIF};color:#fff;margin-top:14px">Top 25 más vendidos</div>
    <div style="font:14px {SANS};color:#c9cfe8;margin-top:4px">{escape(periodo)}, toda la cadena</div>
  </td></tr>
  <tr><td style="background:{NAVY};padding:10px 24px;font:bold 15px {SANS};color:#fff">{escape(nombre(suc))}</td></tr>
  <tr><td style="padding:18px 24px 14px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="33%" align="center" style="background:#fde8e6;border-radius:8px;padding:10px 4px;font:12px {SANS};color:{RED}"><div style="font:bold 28px {SANS}">{len(agot)}</div>agotados</td>
      <td width="4"></td>
      <td width="33%" align="center" style="background:#fff3d1;border-radius:8px;padding:10px 4px;font:12px {SANS};color:{AMBER}"><div style="font:bold 28px {SANS}">{len(bajo)}</div>por agotarse</td>
      <td width="4"></td>
      <td width="33%" align="center" style="background:#e3f4e8;border-radius:8px;padding:10px 4px;font:12px {SANS};color:{GREEN}"><div style="font:bold 28px {SANS}">{ok}</div>con existencia</td>
    </tr></table>
  </td></tr>
  {bloque_acciones}
  <tr><td style="padding:16px 24px 6px;font:bold 15px {SANS};color:{INK}">Ranking de la semana</td></tr>
  <tr><td style="padding:0 12px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;table-layout:fixed">
      <tr style="background:{NAVY}">
        <td width="42" style="width:42px;padding:8px 2px 8px 8px;font:bold 12px {SANS};color:#fff">#</td>
        <td style="padding:8px 4px;font:bold 12px {SANS};color:#fff">Libro</td>
        <td width="48" align="center" style="width:48px;padding:8px 2px;font:bold 12px {SANS};color:#fff">Cadena</td>
        <td width="40" align="center" style="width:40px;padding:8px 2px;font:bold 12px {SANS};color:#fff">Aquí</td>
        <td width="80" align="center" style="width:80px;padding:8px 8px 8px 2px;font:bold 12px {SANS};color:#fff">Tienes</td>
      </tr>
      {"".join(fila(f) for f in filas)}
    </table>
  </td></tr>
  <tr><td style="padding:18px 24px;font:13px {SANS};color:{MUTED};line-height:1.5">
    El Excel adjunto tiene el ranking de la semana y el del mes (últimos 30 días), con la existencia de todas las ubicaciones, autor, editorial e ISBN.<br><br>
    <b>Cómo se calcula.</b> "Cadena" y "Aquí" son los libros vendidos esta semana en toda la cadena y en tu sucursal; "Tienes" es tu existencia. Unidades vendidas en toda la cadena (ventas menos devoluciones), solo libros a precio normal, sin bazar.
    "Por agotarse" quiere decir que tu existencia no alcanza para una semana al ritmo de venta de tu sucursal.
    Las flechas ▲▼ son las posiciones que el libro subió o bajó contra la semana anterior.
  </td></tr>
  <tr><td style="background:{SOFT};padding:14px 24px;font:11px {SANS};color:{MUTED};text-align:center">Correo automático de De Museo, de uso interno. No lo reenvíes fuera de la empresa.</td></tr>
</table>
</td></tr></table>
</body></html>'''
