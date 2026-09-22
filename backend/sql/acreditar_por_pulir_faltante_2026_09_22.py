"""
Script de un solo uso (2026-09-22): acredita el por_pulir que se quedo sin
sumar en los 8 lotes de agosto validados hoy (ver
validar_lotes_agosto_confirmados_wo_2026_09_22.py), por el bug ya corregido
en InyeccionService.validar_lote (buscaba Producto.codigo_sistema == codigo
SIN prefijo, pero codigo_sistema en la base real SI lleva prefijo -- el
match nunca encontraba nada, confirmado en el log real: "Producto X no
encontrado en db_productos para actualizar por_pulir" en los 18 codigos).

Los montos vienen EXACTOS de los mismos payloads que se usaron para validar
esos 8 lotes (buenas_por_pulir = buenas, porque pnc_inyeccion fue 0 en todos
menos 9757) -- no son un calculo nuevo, son la suma que debio aplicarse hoy
mismo y no se aplico.

NO toca nada anterior a hoy: el usuario confirmo que por_pulir casi no
tenia nada antes de esto (ya habia hecho un ajuste manual de lo unico que
habia), asi que este script se limita estrictamente a los 8 lotes de hoy --
la pregunta de cuanto mas pudo faltar historicamente antes de hoy queda
pendiente, aparte, sin tocar.

Mismo patron de busqueda ya corregido (codigo_sistema con prefijo O
id_codigo sin prefijo) para no repetir el bug al aplicar el parche.

Se corre una sola vez desde la terminal del contenedor en Coolify:
    python -m backend.sql.acreditar_por_pulir_faltante_2026_09_22
"""
from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import Producto
from backend.utils.formatters import preservar_o_normalizar_prefijo, normalizar_codigo_sin_prefijo

# codigo (tal como se reporto, sin normalizar) -> por_pulir que falta sumar
FALTANTES = {
    "9988": 60,
    "9001": 123,   # 102 (INY-D4CDA2D6) + 21 (INY-735D4146)
    "9319": 200,
    "9865": 198,
    "9828": 200,
    "9888": 199,
    "9708": 199,
    "9725": 200,
    "9863": 183,
    "9736": 188,
    "9308": 176,
    "9757": 184,
    "9631": 185,
    "9311": 181,
    "9701": 180,
    "9962": 184,
    "9765": 399,   # 240 (INY-C3AB78C2) + 159 (INY-7A8E8702)
    "9742": 134,
}

with app.app_context():
    for codigo_reportado, monto in FALTANTES.items():
        codigo_sin_prefijo = normalizar_codigo_sin_prefijo(codigo_reportado)
        codigo_con_prefijo = preservar_o_normalizar_prefijo(codigo_reportado)

        producto = db.session.query(Producto).filter(
            (Producto.codigo_sistema == codigo_con_prefijo) | (Producto.id_codigo == codigo_sin_prefijo)
        ).first()

        if not producto:
            print(f"ERROR: {codigo_reportado} -> no se encontro en db_productos, revisar a mano")
            continue

        antes = float(producto.por_pulir or 0)
        producto.por_pulir = antes + monto
        db.session.commit()
        print(f"OK: {producto.codigo_sistema} -> por_pulir {antes} + {monto} = {producto.por_pulir}")

print("Listo.")
