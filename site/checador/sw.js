/* Checador De Museo · funcionamiento sin internet
   Guarda la página, el logo, los sonidos y las fuentes, y la última copia de los datos.
   - Página y archivos fijos: abre al instante con lo guardado y se actualiza en segundo plano.
   - bazar.json, promocion.json, existencias: intenta internet (hasta 4 s); si no hay, usa la última copia.
   - Partes del catálogo (c/ y r/): llevan versión en la dirección; se guardan y se reutilizan.
   - catalogo.json no se guarda aquí: la página ya lo guarda en el equipo (IndexedDB). */
const VERSION = "checador-v1";
const FIJOS = [
  "./", "logo.png", "sonido-normal.mp3", "sonido-bazar.mp3",
  "../fuentes/fuentes.css",
  "../fuentes/archivo-latin-wght-normal.woff2",
  "../fuentes/bodoni-moda-latin-opsz-normal.woff2",
  "../fuentes/bodoni-moda-latin-opsz-italic.woff2",
  "../fuentes/ibm-plex-mono-latin-400-normal.woff2",
  "../fuentes/ibm-plex-mono-latin-500-normal.woff2",
  "../fuentes/ibm-plex-mono-latin-600-normal.woff2",
  "../favicon-32.png", "../icono-180.png"
];
const DATOS = /\/(bazar\.json|promocion\.json|existencias\.enc)$/;
const PARTES = /\/checador\/[cr]\/\w+\.json$/;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => Promise.all(FIJOS.map(u => c.add(new Request(u, { cache: "reload" })).catch(() => {})))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

// Clave sin "?t=..." ni "?v=..." para los datos que cambian cada noche
const sinQuery = req => req.url.split("?")[0];

async function redPrimero(req) {
  const c = await caches.open(VERSION), k = sinQuery(req);
  try {
    const r = await Promise.race([fetch(req), new Promise((_, no) => setTimeout(() => no(new Error("lento")), 4000))]);
    if (r.ok) c.put(k, r.clone());
    return r;
  } catch (e) {
    const g = await c.match(k);
    if (g) return g;
    throw e;
  }
}

async function guardadoPrimero(req) {
  const c = await caches.open(VERSION);
  const g = await c.match(req);
  if (g) return g;
  const r = await fetch(req);
  if (r.ok) c.put(req, r.clone());
  return r;
}

async function rapidoYActualiza(e) {
  const req = e.request, c = await caches.open(VERSION);
  const k = req.mode === "navigate" ? new URL("./", self.registration.scope).href : sinQuery(req);
  const g = await c.match(k);
  const red = fetch(req).then(r => { if (r.ok) c.put(k, r.clone()); return r; });
  if (g) { e.waitUntil(red.catch(() => {})); return g; }
  return red;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (/\/catalogo\.json$/.test(url.pathname)) return;           // lo guarda la página
  if (DATOS.test(url.pathname)) return e.respondWith(redPrimero(req));
  if (PARTES.test(url.pathname)) return e.respondWith(guardadoPrimero(req));
  if (req.mode === "navigate" || FIJOS.some(u => url.href === new URL(u, self.registration.scope).href))
    return e.respondWith(rapidoYActualiza(e));
});
