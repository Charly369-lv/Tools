#!/usr/bin/env python3
"""
Sincroniza el Checador de Bazar y la app de Inventario de De Museo con Odoo.

Lee de Odoo los productos (variantes), sus precios, autor, editorial, tema y
precio de bazar, y las ubicaciones internas de inventario. Con eso genera los
archivos de datos que usan las dos apps en la carpeta del sitio.

No necesita librerías externas (solo Python 3.9+).

Variables de entorno (en GitHub van como "secretos"):
  ODOO_URL      ej. https://demuseo.odoo.com
  ODOO_DB       ej. demuseo
  ODOO_USER     correo del usuario de Odoo dueño de la clave
  ODOO_API_KEY  clave API generada en Odoo (Preferencias > Seguridad de la cuenta)

Uso:
  python sync/sync_odoo.py --out site            # sincroniza y escribe los datos
  python sync/sync_odoo.py --diagnostico         # solo muestra qué campos encontró

Los campos propios (Autor, Editorial, Tema, Precio Bazar) se buscan por su
etiqueta. Si algún día cambia una etiqueta, se puede fijar el nombre técnico en
sync/config.json.
"""
import argparse
import collections
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = 5000


# ----------------------------------------------------------------- conexión
class Odoo:
    """Cliente mínimo: intenta la API JSON-2 (Odoo 19+) y, si no está, usa /jsonrpc."""

    def __init__(self, url, db, user, key):
        url = url.strip().rstrip("/")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url  # tolera "demuseo.odoo.com" sin https://
        url = re.sub(r"/(web|odoo)(/.*)?$", "", url)  # tolera ".../web" o ".../odoo"
        self.url, self.db, self.user, self.key = url, db.strip(), user.strip(), key.strip()
        self.mode = None
        self.uid = None

    def _post(self, path, body, headers=None, tries=4):
        """Devuelve (estado_http, respuesta). Si Odoo responde con una página web en vez
        de datos, la respuesta es {"_html": True, ...} para poder dar un mensaje claro."""
        data = json.dumps(body).encode()
        h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "demuseo-sync/1.1"}
        h.update(headers or {})

        def parse(txt, final_url, ctype):
            try:
                return json.loads(txt or "null")
            except ValueError:
                return {"_html": True, "url_final": final_url, "tipo": ctype, "inicio": re.sub(r"\s+", " ", txt[:160])}

        for attempt in range(tries):
            req = urllib.request.Request(self.url + path, data=data, headers=h, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return r.status, parse(r.read().decode(errors="replace"), r.geturl(), r.headers.get("Content-Type", ""))
            except urllib.error.HTTPError as e:
                payload = parse(e.read().decode(errors="replace"), e.geturl(), e.headers.get("Content-Type", "") if e.headers else "")
                if e.code in (429, 502, 503, 504) and attempt < tries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                return e.code, payload
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < tries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise SystemExit(f"No se pudo conectar con {self.url}: {e}")

    def connect(self):
        # 1) API JSON-2 (Odoo 19 o más nuevo). Solo vale si responde datos, no una página web.
        st, res = self._post("/json/2/res.users/context_get", {}, self._j2h())
        if st == 200 and isinstance(res, dict) and not res.get("_html"):
            self.mode = "json2"
            print("Método de conexión: API JSON-2")
            return
        print("La API JSON-2 no está disponible; se usa la conexión clásica (JSON-RPC).")
        # 2) JSON-RPC clásico (todas las versiones)
        st, res = self._post("/jsonrpc", {"jsonrpc": "2.0", "method": "call", "params": {
            "service": "common", "method": "authenticate", "args": [self.db, self.user, self.key, {}]}})
        if st == 200 and isinstance(res, dict) and res.get("result"):
            self.mode, self.uid = "jsonrpc", res["result"]
            return
        if isinstance(res, dict) and res.get("_html"):
            raise SystemExit(
                f"La dirección {self.url} no respondió como Odoo (devolvió una página web).\n"
                f"Revisa que ODOO_URL sea exactamente https://demuseo.odoo.com\n"
                f"Detalle: estado {st}, terminó en {res.get('url_final')}, tipo {res.get('tipo')}")
        if isinstance(res, dict) and res.get("result") is False:
            raise SystemExit(
                "Odoo rechazó el usuario o la clave. Revisa:\n"
                "  - ODOO_USER: el correo exacto con el que ese usuario entra a Odoo\n"
                "  - ODOO_API_KEY: la clave API completa (genera una nueva si dudas)\n"
                f"  - ODOO_DB: el nombre de la base de datos (ahora: '{self.db}')")
        err = res.get("error", {}) if isinstance(res, dict) else {}
        msg = (err.get("data", {}) or {}).get("message") or err.get("message") or str(res)[:400]
        if "database" in str(msg).lower() or "does not exist" in str(msg).lower():
            raise SystemExit(f"Odoo no encontró la base de datos '{self.db}'. Revisa ODOO_DB.\nDetalle: {msg}")
        raise SystemExit(
            "Odoo rechazó la conexión. Revisa ODOO_URL, ODOO_DB, ODOO_USER y ODOO_API_KEY, "
            f"y que el plan de Odoo permita la API externa.\nDetalle (estado {st}): {msg}")

    def _j2h(self):
        return {"Authorization": "bearer " + self.key, "X-Odoo-Database": self.db}

    def call(self, model, method, args=None, kw=None):
        args, kw = args or [], kw or {}
        if self.mode == "json2":
            body = dict(kw)
            if method in ("search_read", "search_count", "search") and args:
                body["domain"] = args[0]
            elif args:
                body["ids"] = args[0]
            st, res = self._post(f"/json/2/{model}/{method}", body, self._j2h())
            if st != 200:
                raise SystemExit(f"Error de Odoo en {model}.{method}: {json.dumps(res, ensure_ascii=False)[:600]}")
            return res
        st, res = self._post("/jsonrpc", {"jsonrpc": "2.0", "method": "call", "params": {
            "service": "object", "method": "execute_kw",
            "args": [self.db, self.uid, self.key, model, method, args, kw]}})
        if st != 200 or not isinstance(res, dict) or "error" in res or res.get("_html"):
            raise SystemExit(f"Error de Odoo en {model}.{method}: {json.dumps(res, ensure_ascii=False)[:600]}")
        return res["result"]

    def search_read_all(self, model, domain, fields, order="id"):
        out, offset = [], 0
        while True:
            batch = self.call(model, "search_read", [domain], {"fields": fields, "limit": PAGE, "offset": offset, "order": order})
            out.extend(batch)
            print(f"  {model}: {len(out):,} registros…", flush=True)
            if len(batch) < PAGE:
                return out
            offset += PAGE


# ----------------------------------------------------------------- utilidades
def norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def as_text(v):
    """Convierte cualquier valor de Odoo (char, many2one, many2many, selección) en texto."""
    if v in (None, False):
        return ""
    if isinstance(v, (list, tuple)):
        if len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], str):
            return clean(v[1])
        return clean(", ".join(as_text(x) for x in v))
    if isinstance(v, dict):
        return clean(v.get("display_name") or v.get("name") or "")
    return clean(v)


def round_half_up(x):
    return int(math.floor(x + 0.5 + 1e-9))


def find_field(fields, labels, types=None, exclude=()):
    """Busca un campo por su etiqueta visible (sin importar mayúsculas ni acentos)."""
    want = [norm(l) for l in labels]
    cands = []
    for name, f in fields.items():
        if name in exclude:
            continue
        if types and f.get("type") not in types:
            continue
        lab = norm(f.get("string"))
        for rank, w in enumerate(want):
            if lab == w:
                cands.append((rank, 0 if name.startswith("x_") else 1, name))
    cands.sort()
    return list(dict.fromkeys(c[2] for c in cands))


# ----------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site", help="carpeta del sitio (con checador/ e inventario/)")
    ap.add_argument("--diagnostico", action="store_true", help="solo mostrar los campos detectados")
    a = ap.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit("Faltan estos secretos: " + ", ".join(missing))
    cfg = {}
    if os.path.exists(os.path.join(HERE, "config.json")):
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)

    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()
    print(f"Conectado a {env['ODOO_URL']} ({o.mode})")

    pf = o.call("product.product", "fields_get", [], {"attributes": ["string", "type", "relation", "selection"]})
    text_types = ("char", "text", "many2one", "many2many", "selection")

    def pick(key, labels, types):
        if cfg.get(key):
            return [cfg[key]] if isinstance(cfg[key], str) else list(cfg[key])
        return find_field(pf, labels, types)

    F = {
        "autor": pick("campo_autor", ["Autor", "AUTOR", "Autores"], text_types),
        "editorial": pick("campo_editorial", ["Editorial"], text_types),
        "tema": pick("campo_tema", ["Tema"], text_types),
        "precio_bazar": pick("campo_precio_bazar", ["Precio Bazar", "Precio de Bazar", "Bazar"], ("float", "monetary", "integer")),
        "bazar_flag": pick("campo_bazar", ["Bazar", "En Bazar", "Bazar de descuentos"], ("boolean", "selection", "char", "many2one")),
    }
    price_field = cfg.get("campo_precio") or ("lst_price" if "lst_price" in pf else "list_price")
    print("\nCampos detectados en Producto:")
    for k, v in F.items():
        found = ", ".join("%s (%s)" % (x, pf[x].get("string")) for x in v if x in pf)
        print(f"  {k:13s} -> {found or 'NO ENCONTRADO'}")
    print(f"  precio        -> {price_field}")
    if a.diagnostico:
        extra = [f"{n} ({f.get('string')}, {f.get('type')})" for n, f in sorted(pf.items()) if n.startswith("x_")]
        print("\nCampos propios (x_) disponibles:\n  " + "\n  ".join(extra or ["(ninguno)"]))
        return

    fields = ["id", "default_code", "barcode", "name", "display_name", price_field, "categ_id", "active"]
    for v in F.values():
        fields += [x for x in v if x in pf]
    fields = list(dict.fromkeys(fields))

    print("\nDescargando productos…")
    domain = cfg.get("dominio_productos", [["active", "=", True]])
    prods = o.search_read_all("product.product", domain, fields)

    print("Descargando ubicaciones…")
    try:
        locs = o.search_read_all("stock.location", [["usage", "=", "internal"], ["active", "=", True]], ["complete_name"], order="complete_name")
        locations = sorted({clean(l["complete_name"]) for l in locs if l.get("complete_name")})
    except SystemExit as e:
        print("  Aviso: no se pudieron leer las ubicaciones; se usan las anteriores.", e)
        locations = None

    # ---- transformar
    def first_text(rec, names):
        for n in names:
            t = as_text(rec.get(n))
            if t:
                return t
        return ""

    cat_pct = re.compile(r"BAZAR\s*(\d{1,2})\s*%", re.I)
    rows, seen = [], set()
    stats = collections.Counter()
    for p in prods:
        code = re.sub(r"\D", "", str(p.get("barcode") or ""))
        ref = clean(p.get("default_code") or "")
        if not code and not ref:
            stats["sin_codigo_ni_ref"] += 1
            continue
        if code and code in seen:
            stats["codigo_repetido"] += 1
            continue
        if code:
            seen.add(code)
        name = clean(p.get("name") or p.get("display_name") or "")
        if ref and name.startswith(f"[{ref}]"):
            name = name[len(ref) + 2:].strip()
        autor = first_text(p, F["autor"])
        editorial = first_text(p, F["editorial"])
        tema = first_text(p, F["tema"])
        if tema.upper() in ("S/E", "S/V"):
            tema = ""
        titulo = name
        if editorial and titulo.upper().startswith(editorial.upper() + " "):
            titulo = titulo[len(editorial) + 1:]
        precio = float(p.get(price_field) or 0)
        precio = int(precio) if precio == int(precio) else round(precio, 2)

        # Bazar: precio bazar del campo propio; si no tiene, por la categoría "BAZAR 60%"
        pb = 0.0
        for n in F["precio_bazar"]:
            try:
                pb = float(p.get(n) or 0)
            except (TypeError, ValueError):
                pb = 0.0
            if pb:
                break
        m = cat_pct.search(as_text(p.get("categ_id")))
        if not pb and m and precio:
            pb = precio * (1 - int(m.group(1)) / 100)
        en_bazar = bool(precio and 0 < pb < precio)
        row = [code, ref, titulo, autor, editorial, precio, tema]
        if en_bazar:
            pbr = round_half_up(pb)
            row += [pbr, round((1 - pb / precio) * 100)]
            stats["en_bazar"] += 1
        rows.append(row)
        stats["productos"] += 1

    if stats["productos"] < 1000:
        raise SystemExit(f"Solo llegaron {stats['productos']} productos: parece un error. No se publica nada para no borrar el catálogo.")

    version = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    write_checador(os.path.join(a.out, "checador"), rows, version)
    write_inventario(os.path.join(a.out, "inventario"), prods, locations, version)
    with open(os.path.join(a.out, "ultima-sincronizacion.json"), "w", encoding="utf-8") as f:
        json.dump({"fecha_utc": version, "productos": stats["productos"], "en_bazar": stats["en_bazar"],
                   "ubicaciones": len(locations) if locations else None, "modo_api": o.mode}, f, ensure_ascii=False, indent=1)
    print(f"\nListo: {stats['productos']:,} productos, {stats['en_bazar']:,} en bazar"
          + (f", {len(locations)} ubicaciones" if locations else "") + f". Omitidos: {dict((k, v) for k, v in stats.items() if k not in ('productos', 'en_bazar'))}")


def key2(s):
    return s[-2:].zfill(2)


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def write_checador(out, rows, version):
    C, R = collections.defaultdict(dict), collections.defaultdict(dict)
    for r in rows:
        if r[0]:
            C[key2(r[0])][r[0]] = r
        if r[1]:
            R[key2(re.sub(r"\D", "", r[1]) or "00")][r[1]] = r
    for i in range(100):
        k = f"{i:02d}"
        dump(f"{out}/c/{k}.json", C.get(k, {}))
        dump(f"{out}/r/{k}.json", R.get(k, {}))
    dump(f"{out}/bazar.json", {"v": version, "total": len(rows), "bz": [r for r in rows if len(r) > 7]})

    def dic(i):
        c = collections.Counter(r[i] for r in rows)
        vals = [v for v, _ in c.most_common()]
        return vals, {v: j for j, v in enumerate(vals)}
    A, Ai = dic(3)
    E, Ei = dic(4)
    T, Ti = dic(6)
    comp = [[r[0], r[1], r[2], Ai[r[3]], Ei[r[4]], r[5], Ti[r[6]]] + r[7:] for r in rows]
    dump(f"{out}/catalogo.json", {"v": version, "a": A, "e": E, "t": T, "p": comp})


def write_inventario(out, prods, locations, version):
    C, R = collections.defaultdict(dict), collections.defaultdict(dict)
    for p in prods:
        code = re.sub(r"\D", "", str(p.get("barcode") or ""))
        ref = clean(p.get("default_code") or "")
        if not code and not ref:
            continue
        name = clean(p.get("name") or "")
        if ref and name.startswith(f"[{ref}]"):
            name = name[len(ref) + 2:].strip()
        if code and code not in C[key2(code)]:
            C[key2(code)][code] = [ref, name]
        if ref:
            R[key2(re.sub(r"\D", "", ref) or "00")][ref] = [code, name]
    for i in range(100):
        k = f"{i:02d}"
        dump(f"{out}/c/{k}.json", C.get(k, {}))
        dump(f"{out}/r/{k}.json", R.get(k, {}))
    if locations:
        dump(f"{out}/locs.json", {"v": version, "locs": locations})


if __name__ == "__main__":
    main()
