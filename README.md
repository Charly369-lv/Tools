# Herramientas De Museo conectadas a Odoo

Este paquete publica el **Checador de precios del Bazar** y la app de **Inventario** en internet, y las mantiene actualizadas con los datos de **demuseo.odoo.com**. Ya no hace falta exportar ningún Excel.

- Cada día a las **2:00 a. m. (hora de Guatemala)**, GitHub se conecta a Odoo, descarga productos, precios, autor, editorial, tema, precio de bazar y ubicaciones, y vuelve a publicar las dos apps.
- Cuando quieras, puedes actualizar **al instante** con un botón (paso 7).
- Todo es gratis: usa GitHub y GitHub Pages.

Tiempo estimado: **30 minutos**, una sola vez.

---

## Paso 1 · Crear un usuario de Odoo solo para la sincronización (recomendado)

Así la clave solo sirve para leer datos y puedes desactivarla sin afectar a nadie.

1. En Odoo ve a **Ajustes → Usuarios y compañías → Usuarios → Nuevo**.
2. Nombre: `Sincronización Checador`. Correo: uno que controles (por ejemplo `sync@demuseo.com`).
3. Permisos: **Inventario: Usuario** y **Ventas: Usuario** (solo lectura de productos y ubicaciones es suficiente). Nada de administrador.
4. Guarda. Si Odoo pide contraseña, asígnale una desde **Acción → Cambiar contraseña**.

> Si prefieres usar tu propio usuario, también funciona; solo genera la clave desde tu cuenta.

## Paso 2 · Generar la clave API en Odoo

1. Entra a Odoo **con el usuario del paso 1**.
2. Arriba a la derecha, haz clic en tu nombre → **Mis preferencias** (o **Mi perfil**).
3. Pestaña **Seguridad de la cuenta** → **Nueva clave API**.
4. Confirma tu contraseña, escribe el nombre `Checador GitHub`, elige **sin vencimiento** (o un año) y pulsa **Generar clave**.
5. **Copia la clave** y guárdala un momento en un lugar seguro. Odoo solo la muestra una vez.

> No pegues la clave en chats ni correos. Solo va en GitHub (paso 5).

## Paso 3 · Crear la cuenta de GitHub

1. Entra a **github.com** → **Sign up**.
2. Usa un correo de la empresa (por ejemplo `idemuseo@gmail.com`) y elige un nombre de usuario corto, por ejemplo `demuseo-gt`. Ese nombre aparece en el enlace final.
3. Confirma el correo.

## Paso 4 · Crear el repositorio y subir los archivos

1. En GitHub, botón **+** (arriba a la derecha) → **New repository**.
2. Nombre: `herramientas`. Marca **Public** (GitHub Pages gratis lo requiere; solo contiene las apps y los precios públicos, nunca la clave).
3. Marca **Add a README file** y pulsa **Create repository**.
4. Dentro del repositorio: **Add file → Upload files**.
5. Descomprime el ZIP en tu computadora, abre la carpeta `herramientas` y **arrastra su contenido** (las carpetas `site`, `sync` y el archivo `README.md`) a la página de GitHub.
6. Abajo pulsa **Commit changes**.

**El archivo de la automatización se crea aparte** (las carpetas que empiezan con punto a veces no se suben arrastrando):

7. **Add file → Create new file**.
8. En el nombre escribe exactamente: `.github/workflows/sincronizar.yml` (al escribir las `/` GitHub crea las carpetas).
9. Abre el archivo `sincronizar.yml` que viene en la carpeta `.github/workflows` del ZIP (con el Bloc de notas), copia todo y pégalo.
10. Pulsa **Commit changes**.

## Paso 5 · Guardar los datos de Odoo como secretos

En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**. Crea estos cuatro, uno por uno:

| Nombre (exacto) | Valor |
|---|---|
| `ODOO_URL` | `https://demuseo.odoo.com` |
| `ODOO_DB` | `demuseo` |
| `ODOO_USER` | el correo del usuario del paso 1 |
| `ODOO_API_KEY` | la clave del paso 2 |

> Si no sabes el nombre de la base de datos, en Odoo Online casi siempre es la parte antes de `.odoo.com` (`demuseo`). También aparece en **Ajustes → Activar modo desarrollador**, arriba del menú.

## Paso 6 · Activar GitHub Pages

1. **Settings → Pages**.
2. En **Source** elige **GitHub Actions**.

## Paso 7 · Primera sincronización

1. Pestaña **Actions**. Si pregunta, pulsa **I understand my workflows, go ahead and enable them**.
2. A la izquierda elige **Sincronizar con Odoo** → botón **Run workflow** → **Run workflow**.
3. Espera 2 o 3 minutos hasta que aparezca la marca verde ✅.
4. Tus apps quedan en:
   - Inicio: `https://TU-USUARIO.github.io/herramientas/`
   - Checador: `https://TU-USUARIO.github.io/herramientas/checador/`
   - Inventario: `https://TU-USUARIO.github.io/herramientas/inventario/`

Desde ese momento se actualiza solo cada madrugada. Para actualizar al instante (por ejemplo, después de cambiar precios de bazar), repite el paso 7.

---

## Si algo sale mal

En **Actions**, abre la ejecución con ❌ y luego el paso **Descargar datos de Odoo**. El mensaje dice qué pasó:

| Mensaje | Qué hacer |
|---|---|
| `Faltan estos secretos` | Revisa el paso 5: los nombres deben ser exactos, en mayúsculas. |
| `Odoo rechazó la conexión` | Clave, correo o base de datos incorrectos, o el plan de Odoo no permite la API externa (requiere plan Personalizado). Genera una clave nueva y actualiza `ODOO_API_KEY`. |
| `Solo llegaron N productos` | El usuario no tiene permiso para ver los productos. Dale permiso de Inventario/Ventas. No se publica nada para no dejar el checador vacío. |
| Un campo dice `NO ENCONTRADO` | Se cambió la etiqueta del campo en Odoo. Escribe su nombre técnico en `sync/config.json` (ver abajo). |

Si la sincronización falla, **las apps siguen funcionando** con los datos de la última sincronización buena.

### Cómo detecta los campos

El programa busca los campos por su **etiqueta** en Odoo:

- **Autor**: `Autor` o `AUTOR`
- **Editorial**: `Editorial`
- **Tema**: `Tema`
- **Precio de bazar**: `Precio Bazar`. Si un producto no lo tiene, usa la categoría (`#1BAZAR 60%`, `#2BAZAR 75%`) para calcularlo.
- **Precio normal**: el precio de venta del producto.

Para fijar un campo a mano, edita `sync/config.json` en GitHub (ícono del lápiz) y escribe el nombre técnico, por ejemplo `"campo_precio_bazar": "x_studio_precio_bazar"`. El nombre técnico se ve en Odoo con el modo desarrollador activo, pasando el mouse sobre el campo.

### Reglas que aplica

- Precio de bazar redondeado al quetzal (Q79.60 → Q80).
- Solo productos activos.
- Productos sin código de barras ni referencia interna se omiten.
- Ubicaciones del inventario: todas las ubicaciones internas activas de Odoo (por ejemplo `MIRAF/Stock`).

## Privacidad

- La clave API vive solo en los secretos de GitHub; no aparece en el código ni en las páginas.
- El sitio publicado contiene nombres de productos y precios (lo mismo que ve un cliente en tienda). No incluye costos, existencias ni proveedores.
