"""Aplica en Odoo los cambios aprobados en el app Revisión de clasificación.

Odoo es la fuente de verdad: este script es la ÚNICA pieza que escribe en Odoo, y solo hace lo que el usuario aprobó.

Entrada: sync/cambios/pendiente.json (lo descarga el app con "Preparar cambios para Odoo" y el usuario lo sube al repo):
  {
    "categorias":  [{"id": <id de product.template>, "titulo": "...", "antes": "2006 LIBROS / 30072 NARRATIVA", "despues": "2006 LIBROS / 30082 ..."}],
    "bazar":       [{"id": ..., "titulo": "...", "antes": "2006 LIBROS / 30114 BAZAR 40%", "despues": "2006 LIBROS / 30042 ...", "bazar": "BAZAR 40%"}],
    "autores":     [{"antes": "HOUSEL, MORGAN.", "despues": "HOUSEL, MORGAN"}],
    "editoriales": [{"antes": ["BOOKET.", "Booket"], "despues": "BOOKET"}]
  }

Seguros:
  - Por defecto SOLO SIMULA: muestra qué haría. Para escribir hay que pasar --aplicar.
  - Antes de cambiar un producto revisa que el dato siga como estaba ("antes"); si alguien ya lo cambió, no lo toca.
  - "bazar" (devolver la categoría a libros de bazar) solo se aplica con --incluir-bazar: primero debe existir la nueva
    forma de marcar el bazar (etiqueta y lista de precios), o las tiendas dejan de ver qué libros están en oferta.
  - Nunca borra nada: las editoriales duplicadas se archivan.
  - Guarda en sync/cambios/registro/ cada valor anterior, para poder deshacer con --deshacer <archivo>.
  - Tope de productos por corrida (--max-productos, 5000 por defecto).

Secretos: ODOO_URL, ODOO_DB y un usuario con permiso de escritura en ODOO_WRITE_USER / ODOO_WRITE_API_KEY
(distinto del usuario de solo lectura de la sincronización nocturna).

Uso:
  python sync/aplicar_cambios.py                    # simula
  python sync/aplicar_cambios.py --aplicar          # escribe en Odoo
  python sync/aplicar_cambios.py --aplicar --incluir-bazar
  python sync/aplicar_cambios.py --deshacer sync/cambios/registro/2026-11-27_1530.json --aplicar
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text, find_field, fields_get_es  # noqa: E402

CAMBIOS = os.path.join(HERE, "cambios")
PENDIENTE = os.path.join(CAMBIOS, "pendiente.json")
TXT = ("char", "text")


def limpio(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


class Escritor:
    """Escribe en Odoo (o solo cuenta, si simula) y anota cada valor anterior para poder deshacer."""

    def __init__(self, o, aplicar, maximo):
        self.o, self.aplicar, self.maximo = o, aplicar, maximo
        self.registro, self.productos = [], 0

    def write(self, model, ids, vals, antes):
        """antes: {id: {campo: valor anterior}}"""
        ids = list(ids)
        if not ids:
            return
        if model == "product.template":
            if self.productos + len(ids) > self.maximo:
                raise SystemExit(f"Se alcanzó el tope de {self.maximo:,} productos por corrida. Divide los cambios o sube --max-productos.")
            self.productos += len(ids)
        for i in ids:
            self.registro.append({"modelo": model, "id": i, "antes": antes.get(i, {}), "despues": vals})
        if self.aplicar:
            for k in range(0, len(ids), 200):
                self.o.call(model, "write", [ids[k:k + 200]], {"vals": vals})

    def create(self, model, vals):
        if not self.aplicar:
            return -1
        res = self.o.call(model, "create", [], {"vals_list": [vals]})
        nuevo = res[0] if isinstance(res, list) else res
        self.registro.append({"modelo": model, "id": nuevo, "creado": True, "despues": vals})
        return nuevo


def m2o_id(v):
    return v[0] if isinstance(v, (list, tuple)) and v else (v or False)


def aplicar_categorias(o, w, cambios, nombre):
    if not cambios:
        return
    cats = {limpio(c["complete_name"]): c["id"] for c in o.search_read_all("product.category", [], ["complete_name"])}
    ids = [c["id"] for c in cambios]
    actual = {p["id"]: p for p in o.call("product.template", "read", [ids], {"fields": ["categ_id", "display_name"]})}
    hechos = ya = falta = 0
    por_destino = {}
    for c in cambios:
        p = actual.get(c["id"])
        if not p:
            print(f"  ✗ producto {c['id']} ({c.get('titulo', '')}): ya no existe")
            falta += 1
            continue
        cat_actual = limpio(as_text(p["categ_id"]))
        if cat_actual != limpio(c["antes"]):
            print(f"  · {c.get('titulo') or p['display_name']}: ya no está en '{c['antes']}' (ahora '{cat_actual}'), no se toca")
            ya += 1
            continue
        destino = cats.get(limpio(c["despues"]))
        if not destino:
            print(f"  ✗ la categoría '{c['despues']}' no existe en Odoo")
            falta += 1
            continue
        por_destino.setdefault(destino, []).append((c["id"], m2o_id(p["categ_id"])))
    for destino, lista in por_destino.items():
        w.write("product.template", [i for i, _ in lista], {"categ_id": destino}, {i: {"categ_id": prev} for i, prev in lista})
        hechos += len(lista)
    print(f"{nombre}: {hechos:,} productos {'cambiados' if w.aplicar else 'por cambiar'} · {ya:,} ya estaban distintos · {falta:,} con problema")


def aplicar_autores(o, w, cambios, cfg):
    if not cambios:
        return
    pf = fields_get_es(o, "product.template", ["string", "type"])
    f = (cfg.get("campo_autor") and [cfg["campo_autor"]]) or find_field(pf, ["Autor", "AUTOR", "Autores"], TXT)
    if not f:
        print("Autores: no encontré el campo Autor (de texto) en los productos; no se cambia nada.")
        return
    f = f[0]
    total = 0
    for c in cambios:
        antes, despues = c["antes"], limpio(c["despues"])
        if not despues or antes == despues:
            continue
        prods = o.search_read_all("product.template", [[f, "=", antes]], [f])
        w.write("product.template", [p["id"] for p in prods], {f: despues}, {p["id"]: {f: p[f]} for p in prods})
        total += len(prods)
        print(f"  '{antes}' → '{despues}': {len(prods):,} productos")
    print(f"Autores: {total:,} productos {'cambiados' if w.aplicar else 'por cambiar'} (campo {f}). "
          "Si el nombre del producto repite el autor, ese nombre no se toca aquí.")


def aplicar_editoriales(o, w, cambios, cfg):
    if not cambios:
        return
    pf = fields_get_es(o, "product.template", ["string", "type", "relation"])
    f = (cfg.get("campo_editorial") and [cfg["campo_editorial"]]) or find_field(pf, ["Editorial", "Brand", "Marca"], TXT + ("many2one",))
    if not f:
        print("Editoriales: no encontré el campo Editorial/Brand en los productos; no se cambia nada.")
        return
    f = f[0]
    if pf[f].get("type") != "many2one":   # editorial de texto: igual que autores
        aplicar_autores(o, w, [{"antes": a, "despues": c["despues"]} for c in cambios for a in c["antes"]], dict(cfg, campo_autor=f))
        return
    modelo = pf[f]["relation"]
    mf = o.call(modelo, "fields_get", [], {"attributes": ["type"]})
    archivable = "active" in mf
    total = 0
    for c in cambios:
        despues = limpio(c["despues"])
        nombres = [n for n in c["antes"] if n != despues]
        regs = o.call(modelo, "search_read", [[["name", "in", nombres + [despues]]]], {"fields": ["name"], "context": {"active_test": False}})
        destino = next((r for r in regs if r["name"] == despues), None)
        if not destino:
            # La más usada pasa a llamarse bien (no se crea otra editorial)
            usadas = sorted(regs, key=lambda r: -o.call("product.template", "search_count", [[[f, "=", r["id"]]]]))
            if usadas:
                destino = usadas[0]
                w.write(modelo, [destino["id"]], {"name": despues}, {destino["id"]: {"name": destino["name"]}})
                print(f"  Editorial '{destino['name']}' se renombra '{despues}'")
            else:
                print(f"  ✗ no encontré en Odoo ninguna editorial llamada {nombres}")
                continue
        otras = [r for r in regs if r["id"] != destino["id"]]
        if not otras:
            continue
        prods = o.search_read_all("product.template", [[f, "in", [r["id"] for r in otras]]], [f])
        w.write("product.template", [p["id"] for p in prods], {f: destino["id"]}, {p["id"]: {f: m2o_id(p[f])} for p in prods})
        if archivable:
            w.write(modelo, [r["id"] for r in otras], {"active": False}, {r["id"]: {"active": True} for r in otras})
        total += len(prods)
        print(f"  {', '.join(repr(r['name']) for r in otras)} → '{despues}': {len(prods):,} productos" + (" (las sobrantes se archivan)" if archivable else ""))
    print(f"Editoriales: {total:,} productos {'cambiados' if w.aplicar else 'por cambiar'} (campo {f}, lista {modelo}).")


def deshacer(o, w, archivo):
    reg = json.load(open(archivo, encoding="utf-8"))
    n = 0
    for r in reversed(reg["cambios"]):
        if r.get("creado"):
            if w.aplicar:
                w.o.call(r["modelo"], "write", [[r["id"]]], {"vals": {"active": False}})
            print(f"  Creado en {r['modelo']} #{r['id']}: se archiva")
            continue
        if r["antes"]:
            if w.aplicar:
                w.o.call(r["modelo"], "write", [[r["id"]]], {"vals": r["antes"]})
            n += 1
    print(f"Deshacer: {n:,} registros {'devueltos' if w.aplicar else 'por devolver'} a como estaban.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="escribir en Odoo (sin esto, solo simula)")
    ap.add_argument("--incluir-bazar", action="store_true", help="también devolver la categoría a los libros de bazar")
    ap.add_argument("--max-productos", type=int, default=5000)
    ap.add_argument("--archivo", default=PENDIENTE)
    ap.add_argument("--deshacer", default="")
    a = ap.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_WRITE_USER", "ODOO_WRITE_API_KEY")}
    if not all(env.values()):
        raise SystemExit("Faltan los secretos ODOO_URL, ODOO_DB, ODOO_WRITE_USER u ODOO_WRITE_API_KEY (usuario con permiso de escritura).")
    cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8")) if os.path.exists(os.path.join(HERE, "config.json")) else {}
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_WRITE_USER"], env["ODOO_WRITE_API_KEY"])
    o.connect()
    w = Escritor(o, a.aplicar, a.max_productos)
    print(("ESCRIBIENDO EN ODOO" if a.aplicar else "SIMULACIÓN: no se cambia nada en Odoo") + "\n")

    if a.deshacer:
        deshacer(o, w, a.deshacer)
        return
    if not os.path.exists(a.archivo):
        raise SystemExit(f"No hay cambios pendientes: falta {os.path.relpath(a.archivo)}.")
    c = json.load(open(a.archivo, encoding="utf-8"))
    aplicar_categorias(o, w, c.get("categorias") or [], "Categorías")
    if c.get("bazar"):
        if a.incluir_bazar:
            aplicar_categorias(o, w, c["bazar"], "Recuperar bazar")
        else:
            print(f"Recuperar bazar: {len(c['bazar']):,} libros en espera. Se aplican con 'incluir bazar' cuando la nueva marca de bazar esté lista.")
    aplicar_autores(o, w, c.get("autores") or [], cfg)
    aplicar_editoriales(o, w, c.get("editoriales") or [], cfg)

    if a.aplicar and w.registro:
        os.makedirs(os.path.join(CAMBIOS, "registro"), exist_ok=True)
        hoy = dt.datetime.now(dt.timezone(dt.timedelta(hours=-6))).strftime("%Y-%m-%d_%H%M")
        ruta = os.path.join(CAMBIOS, "registro", f"{hoy}.json")
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump({"fecha": hoy, "archivo": os.path.basename(a.archivo), "cambios": w.registro}, fh, ensure_ascii=False, indent=1)
        os.makedirs(os.path.join(CAMBIOS, "aplicados"), exist_ok=True)
        if c.get("bazar") and not a.incluir_bazar:   # el bazar sigue pendiente; lo demás ya se aplicó
            with open(a.archivo, "w", encoding="utf-8") as fh:
                json.dump({"bazar": c["bazar"]}, fh, ensure_ascii=False, indent=1)
            shutil.copy(ruta, os.path.join(CAMBIOS, "aplicados", f"{hoy}.json"))
        else:
            shutil.move(a.archivo, os.path.join(CAMBIOS, "aplicados", f"{hoy}.json"))
        print(f"\nRegistro para deshacer: {os.path.relpath(ruta)}")
    print(f"\nTotal: {w.productos:,} productos {'cambiados' if a.aplicar else 'se cambiarían'}.")


if __name__ == "__main__":
    main()
