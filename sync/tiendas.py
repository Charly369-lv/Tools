"""Nombre de cada tienda para mostrar en correos y reportes.

En Odoo cada tienda se identifica por el código de su bodega (MIRAF/Stock → MIRAF). Ese código se sigue usando
por dentro (archivos, destinatarios del secreto CORREOS), pero lo que ven las personas es el nombre de la tienda.
Se pueden corregir o agregar nombres en config.json → "nombres_tiendas", sin tocar este archivo.
"""
import json
import os

NOMBRES = {
    "PRADE": "Galerías La Pradera",
    "CONCE": "Pradera Concepción",
    "ANTIG": "La Antigua Guatemala",
    "MIRAF": "Miraflores",
    "PORTA": "Portales",
    "INTER": "Interplaza Xela",
    "COBAN": "Plaza Magdalena Cobán",
    "AMERI": "Parque Las Américas",
    "ZONA1": "Centro Histórico",
    "SANC": "SanKris Mall",
    "VIDER": "Vidēre",
    "MAJAD": "Majadas ONCE",
    "BOD": "Bodega Central",
}
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"), encoding="utf-8") as _f:
        NOMBRES.update({str(k).upper(): v for k, v in (json.load(_f).get("nombres_tiendas") or {}).items()})
except (OSError, ValueError):
    pass


def nombre(codigo):
    """'MIRAF' → 'Miraflores'. Si el código no está en la lista, lo devuelve igual."""
    c = str(codigo or "").strip()
    return NOMBRES.get(c.upper(), c)
