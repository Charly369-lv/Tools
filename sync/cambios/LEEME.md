# Cambios para Odoo

- `pendiente.json`: lo que se aprobó en el app Revisión de clasificación (botón "Preparar cambios para Odoo"). Súbelo aquí.
- En GitHub: Actions → "Aplicar cambios en Odoo" → Run workflow. Primero en modo **simular**; si lo que muestra está bien, otra vez en modo **aplicar**.
- `registro/`: lo que se cambió y cómo estaba antes. Para deshacer, corre el mismo proceso poniendo la ruta del registro en "deshacer".
- `aplicados/`: archivos de cambios que ya se aplicaron.

Los libros de bazar quedan en espera hasta marcar "incluir bazar", cuando la nueva forma de marcar la oferta esté lista.

## Preparar el bazar nuevo

En "Aplicar cambios en Odoo", la tarea **preparar bazar** crea las etiquetas "Bazar 40%", "Bazar 60%"… y la lista de precios
"Bazar De Museo" con el precio de etiqueta de cada libro. No cambia categorías ni el punto de venta. Se puede correr cuantas
veces haga falta: solo ajusta lo que cambió (precios nuevos, libros que entran o salen del bazar).
