"""
Conteo de referencias con venta real en 2025 y 2026 (2026-09-22), instancia
FRIPARTS.

"Vendida" = tiene al menos una línea en db_pedidos cuyo `estado` es un cierre
real: DESPACHADO, DESPACHADO PARCIAL, FACTURADO, CERRADO o EXPORTADO_WO.
Deliberadamente EXCLUYE CANCELADO (no fue venta) y estados abiertos como
PENDIENTE/ALISTADO (todavía no se concretan). Definición confirmada con el
usuario 2026-09-22 -- no es la única lectura posible de "se vendan", por eso
queda explícita acá en vez de asumida.

Cuenta códigos DISTINTOS normalizados con sql_expr_codigo_sin_prefijo_fr /
normalizar_codigo_sin_prefijo (mismo helper que usa el resto del proyecto)
para no duplicar 'FR-1234' y '1234' como si fueran dos referencias distintas.

Solo lectura -- no modifica nada. Se corre desde la terminal del contenedor
en Coolify de la instancia FRIPARTS (esta base de datos, no la de Frimetals,
que es una instancia/BD separada):
    python -m backend.sql.referencias_vendidas_2025_2026
"""
from sqlalchemy import extract, func

from backend.core.sql_database import db
from backend.app import app
from backend.models.sql_models import Pedido
from backend.utils.formatters import sql_expr_codigo_sin_prefijo_fr

ESTADOS_VENTA_REAL = {'DESPACHADO', 'DESPACHADO PARCIAL', 'FACTURADO', 'CERRADO', 'EXPORTADO_WO'}
ANIOS = (2025, 2026)


def contar_referencias_vendidas():
    with app.app_context():
        session = db.session
        codigo_norm = sql_expr_codigo_sin_prefijo_fr(Pedido.id_codigo)

        print(f"Estados considerados venta real: {sorted(ESTADOS_VENTA_REAL)}\n")

        codigos_por_anio = {}
        for anio in ANIOS:
            filas = session.query(codigo_norm.distinct()).filter(
                Pedido.estado.in_(ESTADOS_VENTA_REAL),
                Pedido.id_codigo.isnot(None),
                extract('year', Pedido.fecha) == anio,
            ).all()
            codigos = {c for (c,) in filas if c}
            codigos_por_anio[anio] = codigos
            print(f"{anio}: {len(codigos)} referencias distintas vendidas")

        union_ambos = set.union(*codigos_por_anio.values()) if codigos_por_anio else set()
        interseccion = set.intersection(*codigos_por_anio.values()) if len(codigos_por_anio) > 1 else set()
        print(f"\nUnion (vendidas en 2025 y/o 2026): {len(union_ambos)}")
        if len(ANIOS) > 1:
            print(f"Interseccion (vendidas en AMBOS anios): {len(interseccion)}")


if __name__ == '__main__':
    contar_referencias_vendidas()
