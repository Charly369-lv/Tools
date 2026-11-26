# Salones de eventos

La página está en `site/salones/index.html` y se abre en https://charly369-lv.github.io/Tools/salones/. Aparece en el índice de Tienda y en el directorio principal.

Las 12 tiendas y los dos cafés entran con su usuario y ven los eventos de los cinco salones, con la hora, el cliente, el teléfono y las observaciones. Gerencia (Claribel e Ingrid) puede crear, cambiar y eliminar eventos en los cinco salones. De las tiendas, solo cuatro pueden hacerlo, y cada una únicamente en sus propios salones:

| Tienda | Salones que edita |
|---|---|
| Centro Histórico | Salón Zona 1 |
| Majadas ONCE | Salón 1 y salón 2 |
| La Antigua Guatemala | Salón Antigua |
| Parque Las Américas | Salón Las Américas |

La página no deja guardar un evento que se cruce con otro en el mismo salón. Cada evento muestra qué tienda lo registró y quién hizo el último cambio.

Los eventos y los usuarios se guardan en Firebase, el servicio de Google, porque GitHub Pages no puede guardar datos. Los permisos se revisan en el servidor de Firebase con el archivo `salones/firestore.rules`, así que una tienda no puede editar el salón de otra aunque lo intente desde el navegador. Para este volumen, Firebase es gratis.

Mientras la página no tenga los datos de Firebase, abre en modo de prueba: usted elige qué tienda simular y nada se guarda. Sirve para enseñársela a las tiendas antes de configurar.

## Configuración, una sola vez

Toma unos 30 minutos.

### 1. Crear el proyecto en Firebase

1. Entre a https://console.firebase.google.com con la cuenta de Google de la empresa.
2. Cree un proyecto llamado `salones-demuseo`. Google Analytics no hace falta.
3. Dentro del proyecto, haga clic en el ícono `</>` para registrar una app web y póngale de nombre `salones`. No marque Firebase Hosting.
4. Firebase muestra un bloque que empieza con `const firebaseConfig = {`. Abra `site/salones/index.html`, busque `FIREBASE_CONFIG` al inicio del script y cambie cada `PEGAR_AQUI` por el valor que le corresponde.

Esos valores no son secretos. Firebase los pone en la página a propósito. Lo que protege los datos son las contraseñas y las reglas.

### 2. Crear los usuarios

1. En el menú de la izquierda, entre a Authentication y haga clic en Comenzar.
2. En Método de acceso, active Correo electrónico/contraseña.
3. En Usuarios, agregue estos 16 usuarios en minúsculas, cada uno con su propia contraseña de al menos 10 caracteres. Son las 12 tiendas, los dos cafés y gerencia:

```
centrohistorico@demuseo.com
majadasonce@demuseo.com
antigua@demuseo.com
lasamericas@demuseo.com
videre@demuseo.com
miraflores@demuseo.com
pradera@demuseo.com
concepcion@demuseo.com
portales@demuseo.com
sankrismall@demuseo.com
plazamagdalena@demuseo.com
interplazaxela@demuseo.com
cafemajadasonce@demuseo.com
cafevidere@demuseo.com
claribel.morales@demuseo.com
ingrid@demuseo.com
```

   No tienen que ser correos reales, solo funcionan como usuario. Si prefiere otros, cámbielos en la lista `TIENDAS` de `site/salones/index.html` y en `salones/firestore.rules`, con la misma escritura en los dos archivos.

4. En Configuración, Acciones del usuario, desmarque "Habilitar creación (registro)". Así nadie puede crearse una cuenta por su cuenta.
5. En Configuración, Dominios autorizados, agregue `charly369-lv.github.io`. Sin este paso no se puede entrar desde la página.

### 3. Crear la base de datos y pegar las reglas

1. Entre a Firestore Database y haga clic en Crear base de datos. Elija `nam5 (United States)` y el modo de producción.
2. En la pestaña Reglas, borre lo que aparece, pegue todo el contenido de `salones/firestore.rules` y haga clic en Publicar.

### 4. Subir los archivos a GitHub

Suba los archivos del .zip con las mismas rutas:

- `site/salones/index.html`, ya con los datos de Firebase
- `site/tienda/index.html` y `site/index.html`, que agregan el enlace
- `salones/firestore.rules` y `salones/LEEME.md`, que quedan en el repo como respaldo y no se publican

GitHub Pages publica los cambios en uno o dos minutos.

### 5. Probar los permisos

1. Entre como `antigua@demuseo.com` y cree un evento en el Salón Antigua.
2. Intente crear otro que se cruce con ese horario. La página debe avisar del choque.
3. Salga y entre como `videre@demuseo.com`. Debe ver el evento con todos sus datos, pero sin el botón Nuevo evento y sin poder cambiarlo.
4. Entre como `majadasonce@demuseo.com`. Puede ver el evento de Antigua, pero no cambiarlo, y solo puede crear eventos en sus dos salones.

Si los cuatro pasos salen así, ya puede mandar a cada tienda la dirección, su usuario y su contraseña. Conviene mandar la contraseña por otro medio, por ejemplo en una llamada.

## Mantenimiento

Para cambiar la contraseña de una tienda, entre a Authentication, Usuarios, elimine ese usuario y vuelva a crearlo con el mismo correo y la contraseña nueva. Los eventos no se pierden. Hágalo también cuando alguien deje de trabajar en una tienda, porque el usuario es compartido.

Para agregar un salón:

1. Agréguelo en `SALONES` dentro de `site/salones/index.html`, con un `id` nuevo, y ponga ese `id` en la lista `salones` de su tienda dentro de `TIENDAS`.
2. Agregue el mismo `id` a esa tienda en `permisos()` de `salones/firestore.rules` y publique las reglas otra vez en Firebase.

Para agregar o quitar una tienda, cree o elimine su usuario en Authentication y cámbiela en `TIENDAS` y en `tiendas()` de las reglas. Si tiene salón, cámbiela también en `permisos()`.

Los eventos se pueden revisar o corregir a mano en Firestore Database, colección `eventos`.

## Límites

El plan gratuito de Firebase permite 50,000 lecturas al día. Cada vez que alguien abre la página se leen los eventos del último año en adelante, y con 16 usuarios eso queda muy por debajo del límite.

La revisión de choques de horario la hace la página antes de guardar. Como cada salón lo edita una sola tienda, dos tiendas no pueden apartar el mismo salón al mismo tiempo.

Un evento puede terminar como tarde al día siguiente. Para eso está la casilla "Termina después de medianoche".

Si se va el internet con la página abierta, los cambios se envían cuando vuelve la conexión.
