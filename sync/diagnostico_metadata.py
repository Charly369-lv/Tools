"""Diagnóstico de la pestaña "Metadata" de los productos (y del campo Tema).

Solo lee. Muestra qué campos hay en la pestaña Metadata del formulario de producto, su nombre técnico,
su tipo, los valores más comunes de cada uno y algunos libros de ejemplo. Sirve para elegir de dónde
sacar las categorías del boletín (infantil, juvenil, literatura, autoayuda, ensayo).

Uso: python sync/diagnostico_metadata.py
"""
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text, norm  # noqa: E402

PESTANA = "METADATA"


def main():
    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()
    model = "product.template"
    pf = o.call(model, "fields_get", [], {"attributes": ["string", "type", "relation"]})

    # Campos que se llaman parecido a Tema (lo que usa hoy la sincronización)
    print("\n=== CAMPOS PARECIDOS A 'TEMA' ===")
    for n, f in sorted(pf.items()):
        if "TEMA" in norm(f.get("string")) or "TEMA" in n.upper():
            print(f"  {n:40s} {f.get('type'):10s} {f.get('string')}")

    # Campos dentro de la pestaña Metadata del formulario
    campos = []
    try:
        v = o.call(model, "get_views", [[[False, "form"]]], {})
        arch = v["views"]["form"]["arch"]
        root = ET.fromstring(arch)
        for page in root.iter("page"):
            titulo = norm(page.get("string") or page.get("name") or "")
            if PESTANA in titulo:
                for f in page.iter("field"):
                    n = f.get("name")
                    if n and n in pf and n not in campos:
                        campos.append(n)
        print(f"\n=== CAMPOS EN LA PESTAÑA '{PESTANA.title()}' ({len(campos)}) ===")
        if not campos:
            print("  No encontré la pestaña. Pestañas del formulario: " +
                  ", ".join(sorted({(p.get('string') or p.get('name') or '?') for p in root.iter('page')})))
    except SystemExit as e:
        print("  No se pudo leer el formulario:", str(e)[:300])

    for n in campos:
        f = pf[n]
        print(f"\n  {n}  ·  {f.get('string')}  ·  {f.get('type')}{'  → ' + f['relation'] if f.get('relation') else ''}")
        if f.get("type") in ("char", "selection", "many2one", "boolean", "integer"):
            try:
                try:
                    g = o.call(model, "read_group", [[[n, "!=", False]], [n], [n]], {"lazy": True, "orderby": "__count desc", "limit": 25})
                except SystemExit:
                    g = o.call(model, "read_group", [[[n, "!=", False]], [n], [n]], {"lazy": True})
                    g = sorted(g, key=lambda x: -(x.get(n + "_count") or x.get("__count") or 0))[:25]
                for x in g:
                    print(f"      {x.get(n + '_count', x.get('__count', '?')):>7}  {as_text(x.get(n))}")
            except SystemExit as e:
                print("      (no se pudieron agrupar los valores)", str(e)[:150])

    if campos:
        print("\n=== EJEMPLOS (10 libros con datos en Metadata) ===")
        try:
            dom = ["|"] * (len(campos[:6]) - 1) + [[c, "!=", False] for c in campos[:6]]
            for r in o.call(model, "search_read", [dom], {"fields": ["name", "default_code"] + campos, "limit": 10}):
                print(f"\n  [{r.get('default_code') or ''}] {str(r.get('name'))[:60]}")
                for c in campos:
                    if r.get(c) not in (False, None, "", []):
                        print(f"      {pf[c].get('string')}: {as_text(r.get(c))[:80]}")
        except SystemExit as e:
            print("  (no se pudieron leer ejemplos)", str(e)[:200])


if __name__ == "__main__":
    main()
