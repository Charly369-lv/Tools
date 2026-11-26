# Salones y eventos

La página está en `site/salones/index.html` y se abre en https://charly369-lv.github.io/Tools/salones/. Aparece en el índice de Tienda y en el directorio principal.

Muestra lo que está programado en las 12 tiendas De Museo, agrupado por tienda. Cada tienda tiene su área de actividades. Tres tiendas tienen además salón (Majadas ONCE tiene dos), y Majadas ONCE y Vidēre tienen café. En total son 19 espacios.

Todos los usuarios ven todas las actividades, con su tipo, horario, cliente, teléfono y observaciones. Para crear y cambiar:

| Usuario | Puede crear y editar en |
|---|---|
| Ligia García (gestión cultural), Francisco (distribuidora), Claribel e Ingrid (gerencia) | Los 19 espacios |
| Majadas ONCE | Su área de actividades, salón 1 y salón 2 |
| Parque Las Américas, Centro Histórico y La Antigua | Su área de actividades y su salón |
| Las otras 8 tiendas | Su área de actividades |
| Café Majadas ONCE y Café Vidēre | Su café |

La página no deja guardar dos actividades a la misma hora en el mismo espacio. En espacios distintos de la misma tienda sí se puede, por ejemplo un taller en el área de actividades mientras el salón está rentado. Cada evento muestra quién lo registró y quién hizo el último cambio.

Cada evento lleva un tipo de actividad: presentación de libro, firma de libros, taller, cuentacuentos, club de lectura, charla o conversatorio, lanzamiento o activación de marca, evento privado (renta) u otro. Arriba del calendario hay filtros por tienda, por clase de espacio y por tipo de actividad.

Los eventos y los usuarios se guardan en Firebase (proyecto `salones-demuseo`). Los permisos se revisan en el servidor de Firebase con `salones/firestore.rules`, así que nadie puede editar un espacio ajeno aunque lo intente desde el navegador.

## Actualizar a esta versión

1. En Firebase, Authentication, Usuarios, cree los usuarios de Ligia (`alasyplumasediciones@gmail.com`) y de Francisco (`olydistribuciones@demuseo.com`).
2. En Firestore Database, pestaña Reglas, borre todo, pegue el contenido nuevo de `salones/firestore.rules` y haga clic en Publicar.
3. Suba a GitHub `site/salones/index.html`, `site/tienda/index.html`, `site/index.html`, `salones/firestore.rules` y `salones/LEEME.md`.
4. Pruebe: entre como `miraflores@demuseo.com` y cree una actividad en su área de actividades. Luego entre como `alasyplumasediciones@gmail.com` (Ligia) y compruebe que puede cambiarla.

Haga los pasos 2 y 3 seguidos. Si la página nueva queda publicada con las reglas viejas, las tiendas sin salón no podrán guardar, y si las reglas nuevas quedan con la página vieja, nadie podrá guardar eventos.

## Usuarios

Estos 18 usuarios deben existir en Firebase, en minúsculas:

```
majadasonce@demuseo.com          centrohistorico@demuseo.com
videre@demuseo.com               plazamagdalena@demuseo.com
lasamericas@demuseo.com          antigua@demuseo.com
miraflores@demuseo.com           interplazaxela@demuseo.com
pradera@demuseo.com              cafemajadasonce@demuseo.com
concepcion@demuseo.com           cafevidere@demuseo.com
portales@demuseo.com             alasyplumasediciones@gmail.com
sankrismall@demuseo.com          olydistribuciones@demuseo.com
claribel.morales@demuseo.com     ingrid@demuseo.com
```

En Authentication, Configuración, "Habilitar creación (registro)" debe estar desmarcado y `charly369-lv.github.io` debe estar en los dominios autorizados.

## Mantenimiento

Para cambiar una contraseña, elimine el usuario en Authentication y vuelva a crearlo con el mismo correo. Los eventos no se pierden. Hágalo también cuando alguien deje de trabajar en una tienda, porque el usuario de cada tienda es compartido.

Para dar a alguien permiso de editar todo, agregue su correo en `TIENDAS` con `todos: true` y en `todos()` de las reglas.

Para agregar un espacio:

1. Agréguelo en `ESPACIOS` de `site/salones/index.html` con un `id` nuevo, y ponga ese `id` en la lista `espacios` de la tienda que lo va a usar.
2. En las reglas, agregue el `id` en `espacios()` y en `permisos()` de esa tienda. Publique las reglas.

Para agregar un tipo de actividad, agréguelo en `TIPOS` de la página y su `id` en `tipos()` de las reglas.

Los `id` de espacios y tipos no se deben cambiar cuando ya hay eventos guardados con ellos. El nombre que se ve en pantalla sí se puede cambiar.

Los eventos se pueden revisar o corregir a mano en Firestore Database, colección `eventos`. El campo `salon` guarda el espacio, con ese nombre desde la primera versión.

## Límites

El plan gratuito de Firebase permite 50,000 lecturas al día. Cada vez que alguien abre la página se leen los eventos del último año en adelante, y con 18 usuarios eso queda muy por debajo del límite.

La revisión de choques la hace la página antes de guardar. Si dos personas con permiso sobre el mismo espacio guardan a la misma hora en el mismo segundo, podrían quedar dos eventos encimados; con tan pocos usuarios por espacio es muy poco probable, y se vería de inmediato en el calendario.

Una actividad puede terminar como tarde al día siguiente. Para eso está la casilla "Termina después de medianoche".
