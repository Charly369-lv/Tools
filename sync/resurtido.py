"""Resurtido automático de la bodega (BOD) a las tiendas.

Cada noche calcula, para cada tienda, qué mandarle según lo que vende, lo que tiene, lo que ya va en camino y lo
que hay en bodega. Buenas prácticas de la industria:
  - Clasificación ABC por tienda (por venta en quetzales de las últimas 12 semanas):
      A = productos que hacen el 80% de la venta, B = el siguiente 15%, C = el resto.
  - Cobertura objetivo en semanas de venta (A: 4, B: 3). C: se repone lo vendido en las últimas 4 semanas.
  - Ritmo de venta ponderado: las últimas 4 semanas pesan 50%, las 4 anteriores 30% y las 4 anteriores 20%.
  - Sugerido = objetivo − existencia en tienda − lo que ya va en camino.
  - Si la bodega no alcanza para todas, se reparte unidad por unidad a la tienda con menos semanas de cobertura
    (primero las A).
  - Oportunidades: lo que más se vende en la cadena, hay en bodega y la tienda no tiene → probar 2.
  - Para devolver: lo que la tienda tiene sin ninguna venta en 12 semanas (y con más de 90 días de alta).
  - Ranking de proveedores: venta, existencia, porcentaje vendido y semanas de cobertura.

Opcional: crea el pedido en borrador (BOD → tienda) los días que le tocan a cada tienda, para que el supervisor lo
apruebe en la app. Se enciende en config.json → "resurtido": {"crear_borradores": true, "calendario": {...}}.

El resultado se guarda cifrado en site/resurtido/resurtido.enc (misma clave CLAVE_EXISTENCIAS).

Uso: python sync/resurtido.py --out site
"""
import argparse
import collections
import datetime as dt
import gzip
import heapq
import json
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync_odoo import Odoo, as_text, clean, find_field, fields_get_es  # noqa: E402
from existencias import cifrar  # noqa: E402

UTC = dt.timezone.utc
GT = dt.timezone(dt.timedelta(hours=-6))
ORIGEN_TXT = "Resurtido automático"
DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
BAZAR = re.compile(r"BAZAR\D{0,3}\d{1,2}\s*%", re.I)

DEF = {
    "origen": "BOD",
    "semanas_A": 4, "semanas_B": 3, "minimo_A": 2,
    "oportunidad_unidades": 2, "oportunidad_min_bodega": 6, "oportunidades_max": 150,
    "devolver_max": 300,
    "excluir": ["BOD", "CDV", "DPROV", "REVIS", "BDAÑA", "TEMPS", "TEMBS", "ALDIS", "BDSA", "FLYS", "BDCS", "SG", "WALLM", "MOVIL"],
    "crear_borradores": False,
    "calendario": {},
}


def pref(loc):
    return as_text(loc).split("/")[0].strip().upper()


def fecha_odoo(d):
    return d.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    a = ap.parse_args()
    clave = os.environ.get("CLAVE_EXISTENCIAS", "").strip()
    if not clave:
        print("No está el secreto CLAVE_EXISTENCIAS: no se genera el resurtido.")
        return
    cfg = dict(DEF)
    if os.path.exists(os.path.join(HERE, "config.json")):
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cfg.update(json.load(f).get("resurtido", {}))
    ORIG = cfg["origen"].upper()
    excluir = {x.upper() for x in cfg["excluir"]} | {ORIG}

    env = {k: os.environ.get(k, "").strip() for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY")}
    o = Odoo(env["ODOO_URL"], env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"])
    o.connect()

    # ---------------------------------------------------------------- almacenes
    whs = o.search_read_all("stock.warehouse", [], ["code", "name", "lot_stock_id"])
    WH = {w["code"].upper(): w for w in whs if w.get("code")}
    if ORIG not in WH:
        raise SystemExit(f"No existe el almacén de origen {ORIG}.")

    # ---------------------------------------------------------------- ventas 12 semanas
    hoy = dt.datetime.now(GT).replace(hour=0, minute=0, second=0, microsecond=0)
    c4, c8, c12 = hoy - dt.timedelta(days=28), hoy - dt.timedelta(days=56), hoy - dt.timedelta(days=84)
    mf = o.call("stock.move", "fields_get", [], {"attributes": ["type"]})
    qf = next((f for f in ("quantity", "quantity_done", "product_qty", "product_uom_qty") if f in mf), "product_uom_qty")
    base = [["state", "=", "done"], ["date", ">=", fecha_odoo(c12)], ["date", "<", fecha_odoo(hoy)]]
    print("Ventas de las últimas 12 semanas…")
    sal = o.search_read_all("stock.move", base + [["location_dest_id.usage", "=", "customer"], ["location_id.usage", "=", "internal"]], ["product_id", "location_id", "date", qf])
    dev = o.search_read_all("stock.move", base + [["location_id.usage", "=", "customer"], ["location_dest_id.usage", "=", "internal"]], ["product_id", "location_dest_id", "date", qf])
    V = collections.defaultdict(lambda: [0.0, 0.0, 0.0])   # (tienda, pid) → [últimas 4, 5–8, 9–12 semanas]

    def sumar(pid, t, fecha, q):
        f = dt.datetime.strptime(fecha[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        V[(t, pid)][0 if f >= c4 else 1 if f >= c8 else 2] += q
    for m in sal:
        sumar(m["product_id"][0], pref(m["location_id"]), m["date"], float(m.get(qf) or 0))
    for m in dev:
        sumar(m["product_id"][0], pref(m["location_dest_id"]), m["date"], -float(m.get(qf) or 0))
    tiendas = sorted({t for (t, _) in V if t in WH and t not in excluir})
    print(f"  {len(sal):,} salidas, {len(dev):,} devoluciones. Tiendas con venta: {', '.join(tiendas)}")

    # ---------------------------------------------------------------- existencias
    print("Existencias…")
    quants = o.search_read_all("stock.quant", [["location_id.usage", "=", "internal"]], ["product_id", "location_id", "quantity", "reserved_quantity"])
    EX = collections.defaultdict(float)     # (tienda, pid) → existencia
    BODD = collections.defaultdict(float)   # pid → disponible en bodega (sin lo apartado)
    for q in quants:
        if not q.get("product_id"):
            continue
        t, pid, qty = pref(q["location_id"]), q["product_id"][0], float(q.get("quantity") or 0)
        if t == ORIG:
            BODD[pid] += qty - float(q.get("reserved_quantity") or 0)
        else:
            EX[(t, pid)] += qty

    # ---------------------------------------------------------------- en camino / ya pedido
    print("Pedidos y traslados en camino…")
    nuestros = {p["id"]: p for p in o.search_read_all("stock.picking", [["origin", "=", ORIGEN_TXT], ["state", "=", "draft"]], ["name", "location_dest_id"])}
    pend_m = o.search_read_all("stock.move", [["state", "not in", ["done", "cancel"]], ["location_dest_id.usage", "=", "internal"],
                                              ["location_id.usage", "in", ["internal", "transit"]]],
                               ["product_id", "location_id", "location_dest_id", "product_uom_qty", "picking_id"])
    PEN = collections.defaultdict(float)
    for m in pend_m:
        if m.get("picking_id") and m["picking_id"][0] in nuestros:
            continue
        d, s = pref(m["location_dest_id"]), pref(m["location_id"])
        if d != s:
            PEN[(d, m["product_id"][0])] += float(m.get("product_uom_qty") or 0)

    # ---------------------------------------------------------------- productos
    vend = sorted({pid for (_, pid) in V})
    con_ex = sorted({pid for (_, pid) in EX} | set(BODD))
    todos = sorted(set(vend) | set(con_ex))
    pf = fields_get_es(o, "product.product", ["string", "type"])
    f_edi = (find_field(pf, ["Editorial"], ("char", "many2one", "selection")) or [None])[0]
    campos = ["barcode", "default_code", "name", "list_price", "categ_id", "product_tmpl_id", "create_date", "uom_id"] + ([f_edi] if f_edi else [])
    print(f"Datos de {len(todos):,} productos…")
    P = {}
    for i in range(0, len(todos), 5000):
        for p in o.call("product.product", "read", [todos[i:i + 5000]], {"fields": campos, "context": {"active_test": False}}):
            P[p["id"]] = p
    print("Proveedores…")
    prov_t = {}
    for s in o.search_read_all("product.supplierinfo", [], ["partner_id", "product_tmpl_id", "sequence"], order="sequence, id"):
        if s.get("product_tmpl_id") and s.get("partner_id"):
            prov_t.setdefault(s["product_tmpl_id"][0], as_text(s["partner_id"]))
    PROV, PROVI = [], {}

    def prov_de(pid):
        p = P.get(pid) or {}
        n = prov_t.get((p.get("product_tmpl_id") or [0])[0]) or (as_text(p.get(f_edi)) if f_edi else "") or "Sin proveedor"
        n = clean(n).upper()
        if n not in PROVI:
            PROVI[n] = len(PROV)
            PROV.append(n)
        return PROVI[n]
    precio = lambda pid: float((P.get(pid) or {}).get("list_price") or 0)
    es_bazar = lambda pid: bool(BAZAR.search(as_text((P.get(pid) or {}).get("categ_id"))))

    # ---------------------------------------------------------------- cálculo por tienda
    sA, sB, minA = float(cfg["semanas_A"]), float(cfg["semanas_B"]), int(cfg["minimo_A"])
    CL, NEED, RATE = {}, {}, {}
    for t in tiendas:
        filas = [(pid, v) for (tt, pid), v in V.items() if tt == t and sum(v) > 0]
        filas.sort(key=lambda x: -sum(x[1]) * max(precio(x[0]), 1))
        total = sum(sum(v) * max(precio(pid), 1) for pid, v in filas) or 1
        acum = 0
        for pid, v in filas:
            share = acum / total
            acum += sum(v) * max(precio(pid), 1)
            clase = "A" if share < 0.80 else "B" if share < 0.95 else "C"
            r = 0.5 * v[0] / 4 + 0.3 * v[1] / 4 + 0.2 * v[2] / 4
            ex, pe = max(0.0, EX.get((t, pid), 0)), PEN.get((t, pid), 0)
            if clase == "A":
                obj = max(minA, math.ceil(r * sA))
            elif clase == "B":
                obj = math.ceil(r * sB)
            else:
                obj = max(int(round(v[0])), 1 if ex <= 0 else 0)
            CL[(t, pid)], RATE[(t, pid)] = clase, r
            n = int(obj - ex - pe)
            if n > 0:
                NEED[(t, pid)] = n

    # Reparto de lo que hay en bodega: unidad por unidad a la tienda con menos semanas de cobertura
    print("Repartiendo la existencia de bodega…")
    por_prod = collections.defaultdict(list)
    for (t, pid), n in NEED.items():
        por_prod[pid].append(t)
    ASIG = collections.Counter()
    rango = {"A": 0, "B": 1, "C": 2}
    for pid, ts in por_prod.items():
        disp = int(max(0, BODD.get(pid, 0)))
        if disp <= 0:
            continue
        h = []
        for t in ts:
            r = max(RATE[(t, pid)], 0.05)
            heapq.heappush(h, (rango[CL[(t, pid)]], (EX.get((t, pid), 0) + PEN.get((t, pid), 0)) / r, t))
        while disp > 0 and h:
            rg, cob, t = heapq.heappop(h)
            ASIG[(t, pid)] += 1
            disp -= 1
            if ASIG[(t, pid)] < NEED[(t, pid)]:
                r = max(RATE[(t, pid)], 0.05)
                heapq.heappush(h, (rg, (EX.get((t, pid), 0) + PEN.get((t, pid), 0) + ASIG[(t, pid)]) / r, t))
    REST = {pid: max(0, int(BODD.get(pid, 0))) - sum(ASIG[(t, pid)] for t in tiendas) for pid in BODD}

    # Lo más vendido de la cadena (para oportunidades)
    cadena = collections.Counter()
    nA = collections.Counter()
    for (t, pid), v in V.items():
        if t in tiendas:
            cadena[pid] += sum(v)
            if CL.get((t, pid)) == "A":
                nA[pid] += 1
    top = [pid for pid, u in cadena.most_common(1500) if u > 0]

    usados = set()
    S = {}
    alta_lim = (hoy - dt.timedelta(days=90)).strftime("%Y-%m-%d")
    for t in tiendas:
        L = []
        for (tt, pid), n in NEED.items():
            if tt != t:
                continue
            asg = ASIG[(t, pid)]
            if asg == 0 and CL[(t, pid)] == "C":
                continue   # C sin bodega: no vale la pena listarlo
            v = V[(t, pid)]
            motivo = 0 if asg >= n else (1 if asg > 0 else 2)   # 0 completo, 1 parcial (bodega no alcanza), 2 no hay en bodega
            L.append([pid, CL[(t, pid)], int(v[0]), int(sum(v)), int(EX.get((t, pid), 0)), int(PEN.get((t, pid), 0)),
                      int(max(0, BODD.get(pid, 0))), asg, n, motivo])
            usados.add(pid)
        L.sort(key=lambda x: (rango[x[1]], -x[3]))
        O = []
        for pid in top:
            if len(O) >= int(cfg["oportunidades_max"]):
                break
            if (nA[pid] >= 3 or cadena[pid] >= 20) and EX.get((t, pid), 0) <= 0 and sum(V.get((t, pid), [0, 0, 0])) <= 0 \
                    and PEN.get((t, pid), 0) <= 0 and REST.get(pid, 0) >= int(cfg["oportunidad_min_bodega"]) and not es_bazar(pid):
                O.append([pid, int(cadena[pid]), nA[pid], int(REST[pid]), int(cfg["oportunidad_unidades"])])
                usados.add(pid)
        D = []
        for (tt, pid), ex in EX.items():
            if tt == t and ex > 0 and sum(V.get((t, pid), [0, 0, 0])) <= 0 and str((P.get(pid) or {}).get("create_date") or "9999")[:10] < alta_lim:
                D.append([pid, int(ex)])
        D.sort(key=lambda x: -x[1])
        D = D[:int(cfg["devolver_max"])]
        usados.update(pid for pid, _ in D)
        aA = sum(1 for (tt, pid), c in CL.items() if tt == t and c == "A" and EX.get((t, pid), 0) <= 0)
        S[t] = {"l": L, "o": O, "d": D, "agA": aA, "nA": sum(1 for (tt, _), c in CL.items() if tt == t and c == "A")}

    # Ranking de proveedores (cadena y por tienda)
    R = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])   # unidades, venta Q, existencia tiendas, existencia bodega, productos vendidos
    RS = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))
    for (t, pid), v in V.items():
        if t not in tiendas:
            continue
        i, u = prov_de(pid), sum(v)
        R[i][0] += u
        R[i][1] += u * precio(pid)
        RS[t][i][0] += u
        RS[t][i][1] += u * precio(pid)
    for pid in {pid for (_, pid), v in V.items() if sum(v) > 0}:
        R[prov_de(pid)][4] += 1
    for (t, pid), ex in EX.items():
        if t in tiendas and ex > 0:
            R[prov_de(pid)][2] += ex
    for pid, d in BODD.items():
        if d > 0:
            R[prov_de(pid)][3] += d
    PR = sorted(([i] + [round(x) for x in r] for i, r in R.items() if r[0] > 0 or r[3] > 0), key=lambda x: -x[2])
    PRS = {t: {str(i): [round(x[0]), round(x[1])] for i, x in m.items() if x[0] > 0} for t, m in RS.items()}

    # ---------------------------------------------------------------- borradores en el sistema
    BOR = {}
    for pk in nuestros.values():
        BOR[pref(pk["location_dest_id"])] = {"id": pk["id"], "nombre": pk["name"]}
    dia = DIAS[dt.datetime.now(GT).weekday()]
    if cfg.get("crear_borradores"):
        tipo = o.call("stock.picking.type", "search_read", [[["code", "=", "internal"], ["warehouse_id", "=", WH[ORIG]["id"]]]], {"fields": ["id"], "limit": 1})
        cal = {k.upper(): [d.lower()[:3] for d in v] for k, v in (cfg.get("calendario") or {}).items()}
        for t in tiendas:
            if dia not in cal.get(t, []):
                continue
            lineas = [(x[0], x[7]) for x in S[t]["l"] if x[7] > 0]
            try:
                if t in BOR:
                    o.call("stock.picking", "unlink", [[BOR[t]["id"]]])
                    del BOR[t]
                if not lineas or not tipo:
                    continue
                desde, hacia = WH[ORIG]["lot_stock_id"][0], WH[t]["lot_stock_id"][0]
                pid_ = o.call("stock.picking", "create", [{
                    "picking_type_id": tipo[0]["id"], "location_id": desde, "location_dest_id": hacia, "origin": ORIGEN_TXT,
                    "move_ids": [[0, 0, {"name": as_text(P[p].get("name"))[:120] or "Producto", "product_id": p, "product_uom_qty": q, "product_uom": P[p]["uom_id"][0],
                                         "location_id": desde, "location_dest_id": hacia}] for p, q in lineas]}])
                nombre = o.call("stock.picking", "read", [[pid_]], {"fields": ["name"]})[0]["name"]
                BOR[t] = {"id": pid_, "nombre": nombre, "nuevo": True}
                print(f"  Borrador {nombre} para {t}: {len(lineas)} productos, {sum(q for _, q in lineas)} unidades")
            except SystemExit as e:
                print(f"  No se pudo crear el borrador de {t}: {str(e)[:300]}")
    else:
        print("Creación de borradores apagada (config.json → resurtido.crear_borradores).")

    # ---------------------------------------------------------------- guardar cifrado
    def prod_fila(pid):
        p = P.get(pid) or {}
        code = re.sub(r"\D", "", str(p.get("barcode") or ""))
        ref = clean(p.get("default_code") or "")
        nom = clean(p.get("name") or "")
        if ref and nom.startswith(f"[{ref}]"):
            nom = nom[len(ref) + 2:].strip()
        pr = precio(pid)
        return [code, ref, nom, prov_de(pid), int(pr) if pr == int(pr) else round(pr, 2), 1 if es_bazar(pid) else 0]
    PP = {str(pid): prod_fila(pid) for pid in sorted(usados)}
    version = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    datos = {"v": version, "origen": ORIG, "dia": dia, "tiendas": tiendas, "nombres": {t: WH[t]["name"] for t in tiendas},
             "cfg": {k: cfg[k] for k in ("semanas_A", "semanas_B", "minimo_A", "crear_borradores", "calendario")},
             "prov": PROV, "p": PP, "s": S, "bor": BOR, "pr": PR, "prs": PRS}
    sobre = cifrar(gzip.compress(json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9), clave)
    sobre["v"] = version
    os.makedirs(os.path.join(a.out, "resurtido"), exist_ok=True)
    with open(os.path.join(a.out, "resurtido", "resurtido.enc"), "w", encoding="utf-8") as f:
        json.dump(sobre, f, separators=(",", ":"))
    tot = sum(x[7] for t in tiendas for x in S[t]["l"])
    print(f"Resurtido: {len(tiendas)} tiendas, {tot:,} unidades sugeridas, {len(PP):,} productos ({len(sobre['d']) // 1024:,} KB cifrados).")


if __name__ == "__main__":
    main()
