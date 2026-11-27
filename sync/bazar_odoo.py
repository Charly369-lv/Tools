"""Prepara en Odoo 18 la nueva forma de marcar el bazar: etiqueta de producto + lista de precios.

Hoy el bazar se marca moviendo el libro a una categoría "BAZAR 40%", "BAZAR 60%"… y así pierde su tema. La nueva forma:
  1. Etiqueta de producto "Bazar 40%", "Bazar 60%"… (una por porcentaje).
  2. Lista de precios "Bazar De Museo" con una regla por libro a PRECIO FIJO: el mismo precio de la etiqueta del
     checador (precio normal menos el %, redondeado al quetzal). Así la caja cobra lo mismo que dice la etiqueta.
  3. El punto de venta de cada tienda usa esa lista de precios y los descuentos manuales quedan solo para gerentes
     (eso se configura a mano en Odoo, tienda por tienda, empezando por una de prueba).

Este script NO cambia categorías ni el punto de venta. Solo agrega o ajusta etiquetas y reglas de la lista de precios.
Mientras existan las categorías de bazar, el porcentaje sale de ellas; cuando se devuelvan las categorías originales,
la etiqueta pasa a ser la marca y este mismo script mantiene la lista de precios al día (cambios de precio, libros que
entran o salen del bazar).

Por defecto SOLO SIMULA. Con --aplicar escribe. Guarda registro en sync/cambios/registro/.
Secretos: ODOO_URL, ODOO_DB, ODOO_WRITE_USER, ODOO_WRITE_API_KEY.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text, fields_get_es, find_field, round_half_up  # noqa: E402

LISTA = "Bazar De Museo"
CAT_PCT = re.compile(r"BAZAR\D{0,3}(\d{1,2})\s*%", re.I)       # igual que el checador
TAG_PCT = re.compile(r"^Bazar (\d{1,2})%$", re.I)


def precio_bazar(precio, pct):
    """Igual que el checador: precio − %, redondeado al quetzal (mitad hacia arriba), mínimo Q1."""
    return max(1, round_half_up(precio * (1 - pct / 100)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_WRITE_USER", "ODOO_WRITE_API_KEY")}
    if not all(env.values()):
        raise SystemExit("Faltan los secretos ODOO_URL, ODOO_DB, ODOO_WRITE_USER u ODOO_WRITE_API_KEY.")
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_WRITE_USER"], env["ODOO_WRITE_API_KEY"])
    o.connect()
    print(("ESCRIBIENDO EN ODOO" if a.aplicar else "SIMULACIÓN: no se cambia nada en Odoo") + "\n")
    registro = []

    # 1. Libros en bazar: por categoría (hoy) o por etiqueta (cuando ya se hayan devuelto las categorías)
    tags = {t["id"]: t["name"] for t in o.search_read_all("product.tag", [], ["name"])}
    tag_bazar = {i: int(TAG_PCT.match(n.strip()).group(1)) for i, n in tags.items() if TAG_PCT.match(n.strip())}
    # Si existe el campo "Precio Bazar", el checador usa ese precio: aquí también, para que caja y etiqueta coincidan
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8")) if os.path.exists(os.path.join(HERE, "config.json")) else {}
    pf = fields_get_es(o, "product.template", ["string", "type"])
    f_pb = (cfg.get("campo_precio_bazar") and [cfg["campo_precio_bazar"]]) or find_field(pf, ["Precio Bazar", "Precio de Bazar", "Bazar Price"], ("float", "monetary"))
    f_pb = f_pb[0] if f_pb and f_pb[0] in pf else None
    prods = o.search_read_all("product.template", ["|", ["categ_id.complete_name", "ilike", "BAZAR"], ["product_tag_ids", "in", list(tag_bazar) or [0]]],
                              ["display_name", "categ_id", "list_price", "product_tag_ids"] + ([f_pb] if f_pb else []))
    quiere = {}   # id producto → pct
    for p in prods:
        m = CAT_PCT.search(as_text(p["categ_id"]))
        pct = int(m.group(1)) if m else next((tag_bazar[t] for t in p["product_tag_ids"] if t in tag_bazar), 0)
        if 0 < pct < 100 and float(p["list_price"] or 0) > 0:
            quiere[p["id"]] = pct
    por_pct = collections.Counter(quiere.values())
    print("Libros en bazar: " + ", ".join(f"{n:,} al {p}%" for p, n in sorted(por_pct.items())) + f" (total {len(quiere):,})")

    # 2. Etiquetas "Bazar NN%"
    id_tag = {pct: i for i, pct in tag_bazar.items()}
    for pct in sorted(por_pct):
        if pct not in id_tag:
            print(f"  Etiqueta nueva: Bazar {pct}%")
            if a.aplicar:
                r = o.call("product.tag", "create", [], {"vals_list": [{"name": f"Bazar {pct}%"}]})
                id_tag[pct] = r[0] if isinstance(r, list) else r
                registro.append({"modelo": "product.tag", "id": id_tag[pct], "creado": True})
            else:
                id_tag[pct] = -pct
    poner = collections.defaultdict(list)
    quitar = collections.defaultdict(list)
    for p in prods:
        tiene = [t for t in p["product_tag_ids"] if t in tag_bazar]
        pct = quiere.get(p["id"])
        if pct and id_tag[pct] not in tiene:
            poner[pct].append(p["id"])
        for t in tiene:
            if t != id_tag.get(pct):
                quitar[t].append(p["id"])
    for pct, ids in sorted(poner.items()):
        print(f"  Poner etiqueta Bazar {pct}% a {len(ids):,} libros")
        if a.aplicar:
            for k in range(0, len(ids), 200):
                o.call("product.template", "write", [ids[k:k + 200]], {"vals": {"product_tag_ids": [[4, id_tag[pct]]]}})
            registro += [{"modelo": "product.template", "id": i, "etiqueta_puesta": id_tag[pct]} for i in ids]
    for t, ids in quitar.items():
        print(f"  Quitar etiqueta {tags[t]} a {len(ids):,} libros (ya no corresponde)")
        if a.aplicar:
            for k in range(0, len(ids), 200):
                o.call("product.template", "write", [ids[k:k + 200]], {"vals": {"product_tag_ids": [[3, t]]}})
            registro += [{"modelo": "product.template", "id": i, "etiqueta_quitada": t} for i in ids]

    # 3. Lista de precios con precio fijo por libro
    listas = o.call("product.pricelist", "search_read", [[["name", "=", LISTA]]], {"fields": ["id"], "context": {"active_test": False}})
    if listas:
        lista = listas[0]["id"]
    else:
        print(f"  Lista de precios nueva: {LISTA}")
        lista = -1
        if a.aplicar:
            r = o.call("product.pricelist", "create", [], {"vals_list": [{"name": LISTA}]})
            lista = r[0] if isinstance(r, list) else r
            registro.append({"modelo": "product.pricelist", "id": lista, "creado": True})
    reglas = {}
    if lista > 0:
        for r in o.search_read_all("product.pricelist.item", [["pricelist_id", "=", lista]], ["product_tmpl_id", "fixed_price", "compute_price", "applied_on"]):
            if r["applied_on"] == "1_product" and r["product_tmpl_id"]:
                reglas[r["product_tmpl_id"][0]] = r
    precio = {p["id"]: float(p["list_price"] or 0) for p in prods}
    crear, cambiar, sobran = [], [], [r["id"] for t, r in reglas.items() if t not in quiere]
    fijo = {p["id"]: float(p.get(f_pb) or 0) for p in prods} if f_pb else {}
    for pid, pct in quiere.items():
        pb = max(1, round_half_up(fijo[pid])) if 0 < fijo.get(pid, 0) < precio[pid] else precio_bazar(precio[pid], pct)
        r = reglas.get(pid)
        if not r:
            crear.append({"pricelist_id": lista, "applied_on": "1_product", "product_tmpl_id": pid, "compute_price": "fixed", "fixed_price": pb, "min_quantity": 0})
        elif r["compute_price"] != "fixed" or abs(float(r["fixed_price"] or 0) - pb) > 0.001:
            cambiar.append((r["id"], pb, r["fixed_price"]))
    print(f"  Reglas de precio: {len(crear):,} nuevas, {len(cambiar):,} con precio actualizado, {len(sobran):,} de libros que ya no están en bazar")
    if a.aplicar:
        for k in range(0, len(crear), 200):
            r = o.call("product.pricelist.item", "create", [], {"vals_list": crear[k:k + 200]})
            registro += [{"modelo": "product.pricelist.item", "id": i, "creado": True} for i in (r if isinstance(r, list) else [r])]
        for rid, pb, antes in cambiar:
            o.call("product.pricelist.item", "write", [[rid]], {"vals": {"compute_price": "fixed", "fixed_price": pb}})
            registro.append({"modelo": "product.pricelist.item", "id": rid, "antes": {"fixed_price": antes}})
        if sobran:   # son reglas de esta lista, no productos: se quitan para que el libro vuelva a su precio normal
            o.call("product.pricelist.item", "unlink", [sobran])
            registro += [{"modelo": "product.pricelist.item", "id": i, "borrada": True} for i in sobran]

    # 4. Qué lista usa cada punto de venta (se cambia a mano, empezando por una tienda de prueba)
    try:
        cajas = o.search_read_all("pos.config", [], ["name", "pricelist_id", "available_pricelist_ids", "use_pricelist"])
        print("\nPuntos de venta:")
        for c in cajas:
            usa = as_text(c.get("pricelist_id")) or "—"
            print(f"  {c['name']}: lista {usa}" + (" · ya puede usar Bazar De Museo" if lista in (c.get("available_pricelist_ids") or []) else ""))
    except SystemExit as e:
        print("  (no se pudieron leer los puntos de venta:", str(e)[:200], ")")

    if a.aplicar and registro:
        os.makedirs(os.path.join(HERE, "cambios", "registro"), exist_ok=True)
        hoy = dt.datetime.now(dt.timezone(dt.timedelta(hours=-6))).strftime("%Y-%m-%d_%H%M")
        ruta = os.path.join(HERE, "cambios", "registro", f"bazar_{hoy}.json")
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump({"fecha": hoy, "tarea": "preparar bazar", "cambios": registro}, fh, ensure_ascii=False, indent=1)
        print(f"\nRegistro: {os.path.relpath(ruta)}")


if __name__ == "__main__":
    main()
