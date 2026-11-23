#!/usr/bin/env python3
"""
Envía por Gmail los correos que generan top25.py, remarcaje.py y ranking_bazar.py (modo --vista-previa).

  python sync/enviar_correos.py top25     carpeta/  [--solo-a correo@x.com] [--simular]
  python sync/enviar_correos.py remarcaje carpeta/  [--solo-a ...]
  python sync/enviar_correos.py ranking   carpeta/  [--solo-a ...]

Destinatarios: secreto de GitHub CORREOS (JSON). NO van en el repositorio, que es público:
  {
    "gerencia": ["gerencia@demuseo.com"],
    "tiendas": { "AMERI": ["americas@demuseo.com"], "MAJAD": ["majadas@demuseo.com", "otra@demuseo.com"] },
    "copia_gerencia": false
  }
  "copia_gerencia": true manda a gerencia una copia (CCO) de cada correo de tienda.

Cuenta de envío: secretos GMAIL_USER y GMAIL_APP_PASSWORD (contraseña de aplicación de 16 letras).
--solo-a: prueba. Todo se manda solo a ese correo, con el destino original en el asunto.
--simular: no manda nada; solo muestra qué se mandaría (sirve sin contraseña).
"""
import argparse
import glob
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr

REMITENTE = "De Museo"


def codigo(s):
    return re.sub(r"[^A-Za-z0-9]", "_", str(s)).upper()


def titulo(path, defecto):
    try:
        t = re.search(r"<title>(.*?)</title>", open(path, encoding="utf-8").read(), re.S | re.I)
        if t:
            txt = html.unescape(re.sub(r"\s+", " ", t.group(1))).strip()
            if txt and not txt.lower().startswith("vista previa"):
                return txt
    except OSError:
        pass
    return defecto


def cuerpo_resumen(path):
    """La página índice de la vista previa como resumen para gerencia (sin enlaces a archivos locales)."""
    s = open(path, encoding="utf-8").read()
    return re.sub(r'<a\s+href="(?!https?:)[^"]*"[^>]*>(.*?)</a>', r"\1", s, flags=re.S | re.I)


def planear(tipo, carpeta, cfg):
    """Lista de envíos: (destino, para[], asunto, html_path, adjuntos[], es_tienda)"""
    tiendas = {codigo(k): (k, v if isinstance(v, list) else [v]) for k, v in (cfg.get("tiendas") or {}).items()}
    gerencia = cfg.get("gerencia") or []
    gerencia = gerencia if isinstance(gerencia, list) else [gerencia]
    out, sin = [], []

    def tienda(fn_codigo, html_path, defecto, adjuntos=()):
        k = tiendas.get(fn_codigo)
        if not k or not k[1]:
            sin.append(fn_codigo)
            return
        out.append((k[0], k[1], titulo(html_path, defecto), html_path, list(adjuntos), True))

    if tipo == "top25":
        for p in sorted(glob.glob(os.path.join(carpeta, "correo_*.html"))):
            c = os.path.basename(p)[len("correo_"):-5].upper()
            tienda(c, p, f"Top 25 más vendidos · {c}")
        idx, xl = os.path.join(carpeta, "index.html"), os.path.join(carpeta, "Top25.xlsx")
        if gerencia and os.path.exists(idx):
            out.append(("GERENCIA", gerencia, "Top 25 más vendidos · resumen de la cadena · " + time.strftime("%d/%m/%Y"), idx, [xl] if os.path.exists(xl) else [], False))
    elif tipo == "remarcaje":
        for p in sorted(glob.glob(os.path.join(carpeta, "remarcaje_*.html"))):
            c = os.path.basename(p)[len("remarcaje_"):-5].upper()
            xl = os.path.join(carpeta, f"Remarcaje_{os.path.basename(p)[len('remarcaje_'):-5]}.xlsx")
            tienda(c, p, f"Remarcaje · {c}", [xl] if os.path.exists(xl) else [])
        idx = os.path.join(carpeta, "index.html")
        if gerencia and os.path.exists(idx):
            out.append(("GERENCIA", gerencia, "Remarcaje de la semana · resumen por tienda · " + time.strftime("%d/%m/%Y"), idx, [], False))
    elif tipo == "ranking":
        for p in sorted(glob.glob(os.path.join(carpeta, "tienda_*.html"))):
            c = os.path.basename(p)[len("tienda_"):-5].upper()
            tienda(c, p, f"Ranking del Bazar · {c}")
        g = os.path.join(carpeta, "gerencia.html")
        if gerencia and os.path.exists(g):
            xls = sorted(glob.glob(os.path.join(carpeta, "Ranking_bazar_*.xlsx")))
            out.append(("GERENCIA", gerencia, titulo(g, "Ranking del Bazar · gerencia"), g, xls, False))
    else:
        sys.exit(f"Tipo desconocido: {tipo}")
    return out, sin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tipo", choices=["top25", "remarcaje", "ranking"])
    ap.add_argument("carpeta")
    ap.add_argument("--solo-a", default="")
    ap.add_argument("--simular", action="store_true")
    a = ap.parse_args()

    crudo = os.environ.get("CORREOS", "").strip()
    if not crudo and os.path.exists(os.path.join(os.path.dirname(__file__), "correos.json")):
        crudo = open(os.path.join(os.path.dirname(__file__), "correos.json"), encoding="utf-8").read()
    if not crudo:
        sys.exit("Falta el secreto CORREOS con los destinatarios (gerencia y correo de cada tienda).")
    try:
        cfg = json.loads(crudo)
    except json.JSONDecodeError as e:
        sys.exit(f"El secreto CORREOS no es un JSON válido: {e}")

    envios, sin = planear(a.tipo, a.carpeta, cfg)
    if sin:
        print(f"Sin correo configurado (no se les envía): {', '.join(sorted(set(sin)))}")
    if not envios:
        sys.exit("No hay correos para enviar: revisa que la carpeta tenga los archivos y que CORREOS tenga destinatarios.")

    solo = a.solo_a.strip()
    cco_ger = (cfg.get("gerencia") if cfg.get("copia_gerencia") else []) or []
    cco_ger = cco_ger if isinstance(cco_ger, list) else [cco_ger]

    usuario, clave = os.environ.get("GMAIL_USER", "").strip(), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    smtp = None
    if not a.simular:
        if not usuario or not clave:
            sys.exit("Faltan los secretos GMAIL_USER y GMAIL_APP_PASSWORD.")
        smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=60)
        try:
            smtp.login(usuario, clave)
        except smtplib.SMTPAuthenticationError:
            sys.exit("Gmail rechazó la cuenta: revisa GMAIL_USER y que GMAIL_APP_PASSWORD sea una contraseña de aplicación vigente.")

    enviados, errores = 0, []
    for destino, para, asunto, html_path, adjuntos, es_tienda in envios:
        cuerpo = cuerpo_resumen(html_path) if not es_tienda else open(html_path, encoding="utf-8").read()
        m = EmailMessage()
        m["From"] = formataddr((REMITENTE, usuario or "simulado@demuseo.com"))
        if solo:
            m["To"] = solo
            m["Subject"] = f"[PRUEBA → {destino}: {', '.join(para)}] {asunto}"
        else:
            m["To"] = ", ".join(para)
            m["Subject"] = asunto
            if es_tienda and cco_ger:
                m["Bcc"] = ", ".join(cco_ger)
        m.set_content("Este correo se ve mejor en formato HTML. " + re.sub(r"<[^>]+>", " ", asunto))
        m.add_alternative(cuerpo, subtype="html")
        for x in adjuntos:
            with open(x, "rb") as fh:
                m.add_attachment(fh.read(), maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=os.path.basename(x))
        if a.simular:
            print(f"  [simulado] {m['To']} · {m['Subject']} · adjuntos: {[os.path.basename(x) for x in adjuntos]}")
            enviados += 1
            continue
        for intento in range(3):
            try:
                smtp.send_message(m)
                enviados += 1
                print(f"  ✓ {destino}: {m['To']} · {asunto}")
                break
            except Exception as e:   # reintenta errores temporales de Gmail
                if intento == 2:
                    errores.append(f"{destino}: {e}")
                    print(f"  ✗ {destino}: {e}")
                time.sleep(5)
        time.sleep(1.5)   # Gmail limita el ritmo de envío
    if smtp:
        smtp.quit()
    print(f"\n{enviados} correo(s) {'simulados' if a.simular else 'enviados'}" + (f" · {len(errores)} con error" if errores else "") + (f" · todo a {solo} (prueba)" if solo else ""))
    if errores:
        sys.exit(1)


if __name__ == "__main__":
    main()
