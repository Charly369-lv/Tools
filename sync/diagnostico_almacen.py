"""Diagnóstico de cómo están configurados los almacenes, para preparar los despachos con "En tránsito".

Solo lee. Muestra almacenes, pasos de entrega/recepción, ubicaciones de tránsito, tipos de operación,
rutas de reabastecimiento y los últimos traslados que salen de la bodega.

Uso: python sync/diagnostico_almacen.py [--origen BOD]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origen", default="BOD")
    a = ap.parse_args()
    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()

    def sr(model, dom, fields, **kw):
        try:
            return o.call(model, "search_read", [dom], dict({"fields": fields}, **kw))
        except SystemExit as e:
            print(f"  (no se pudo leer {model}: {e})")
            return []

    print("\n=== ALMACENES ===")
    for w in sr("stock.warehouse", [], ["name", "code", "lot_stock_id", "reception_steps", "delivery_steps", "resupply_wh_ids", "view_location_id"]):
        print(f"  {w['code']:8s} {w['name'][:40]:40s} existencias={as_text(w['lot_stock_id'])}  recepción={w['reception_steps']}  entrega={w['delivery_steps']}"
              f"  reabastece_desde={w.get('resupply_wh_ids')}")

    print("\n=== UBICACIONES DE TRÁNSITO ===")
    for l in sr("stock.location", [["usage", "=", "transit"]], ["complete_name", "active", "company_id"]):
        print(f"  {l['id']:6d} {l['complete_name']}")

    print("\n=== TIPOS DE OPERACIÓN (internos, entregas y recepciones) ===")
    for t in sr("stock.picking.type", [["code", "in", ["internal", "outgoing", "incoming"]]],
                ["name", "code", "warehouse_id", "default_location_src_id", "default_location_dest_id", "sequence_code"], order="warehouse_id, code"):
        print(f"  {t['id']:5d} {as_text(t['warehouse_id'])[:14]:14s} {t['code']:9s} {t['name'][:34]:34s} {as_text(t['default_location_src_id'])} -> {as_text(t['default_location_dest_id'])}")

    print("\n=== RUTAS ===")
    for r in sr("stock.route", [], ["name", "warehouse_selectable", "product_selectable", "active"]):
        print(f"  {r['id']:5d} {r['name']}")

    print(f"\n=== ÚLTIMOS 15 TRASLADOS QUE SALEN DE {a.origen} ===")
    for p in sr("stock.picking", [["location_id.complete_name", "=ilike", a.origen + "/%"]],
                ["name", "picking_type_id", "location_id", "location_dest_id", "state", "origin", "scheduled_date"], order="id desc", limit=15):
        print(f"  {p['name']:22s} {as_text(p['picking_type_id'])[:30]:30s} {as_text(p['location_id'])} -> {as_text(p['location_dest_id'])}  [{p['state']}]  {p.get('origin') or ''}")

    print("\n=== PERMISOS DEL USUARIO DE LA CONEXIÓN ===")
    for m in ("stock.picking", "stock.move"):
        for op in ("read", "write"):
            try:
                ok = o.call(m, "check_access_rights", [op], {"raise_exception": False})
            except SystemExit:
                ok = "?"
            print(f"  {m:14s} {op:6s} {ok}")


if __name__ == "__main__":
    main()
