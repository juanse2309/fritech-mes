"""
Conteo de referencias vendidas en 2025 y 2026 según db_ventas (World Office),
instancia FRIPARTS (2026-09-22).

A diferencia de referencias_vendidas_2025_2026.py (que lee db_pedidos, la
tabla operativa de la app -- sin historial anterior al lanzamiento del
31-ago-2026), esta lee db_ventas, que se sincroniza desde World Office y sí
trae histórico desde 2024 (ver WHERE YEAR(E.Fecha) >= 2024 en
agente_wo_comercial.py).

LIMITACIÓN CONOCIDA (confirmada con el usuario 2026-09-22, decidió proceder
igual): clasificacion='venta' en db_ventas junta bajo la misma etiqueta
tipo_doc FV (factura real), COT (cotización, no necesariamente vendida) y
NC/NCV/NCCL/DMC (notas crédito/devolución) -- ver agente_wo_comercial.py
líneas 552-568. El tipo_doc original se descarta antes de persistir, así
que desde db_ventas no se puede aislar "solo facturado" sin tocar la
extracción. Este script usa clasificacion ILIKE '%venta%' tal cual, igual
que el resto del dashboard (VentasRepository.get_desglose_mensual_ventas,
mv_dashboard_ventas_analitica) -- consistente con lo que ya ve el usuario en
el panel de Jefatura, pero puede sobreestimar "vendida" si hay cotizaciones
sin facturar de por medio.

Extrae el código de referencia de `productos` con la misma lógica que
VentasRepository.get_desglose_mensual_ventas (split por '|' o por espacio) y
normaliza quitando el prefijo 'FR-' para no duplicar 'FR-1234' y '1234'
como referencias distintas.

Solo lectura -- no modifica nada.
    python -m backend.sql.referencias_vendidas_wo_2025_2026
"""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.core.sql_database import db, rollback_seguro
from backend.app import app

ANIOS = (2025, 2026)

SQL_CODIGOS_ANIO = text("""
    WITH VentasPeriodo AS (
        SELECT
            CASE
                WHEN productos LIKE '%|%' THEN TRIM(SPLIT_PART(productos, '|', 1))
                WHEN productos LIKE '% %' THEN TRIM(SPLIT_PART(productos, ' ', 1))
                ELSE TRIM(productos)
            END AS raw_codigo
        FROM db_ventas
        WHERE clasificacion ILIKE '%venta%'
          AND EXTRACT(YEAR FROM CAST(fecha AS DATE)) = :anio
    )
    SELECT DISTINCT UPPER(REPLACE(raw_codigo, 'FR-', '')) AS codigo
    FROM VentasPeriodo
    WHERE raw_codigo IS NOT NULL AND raw_codigo != ''
""")


def contar_referencias_vendidas_wo():
    with app.app_context():
        try:
            print("Fuente: db_ventas (sincronizado desde World Office)")
            print("Filtro: clasificacion ILIKE '%venta%' (incluye FV/COT/NC/NCV/NCCL/DMC, ver docstring)\n")

            codigos_por_anio = {}
            for anio in ANIOS:
                codigos = {
                    r[0] for r in db.session.execute(SQL_CODIGOS_ANIO, {"anio": anio}).all()
                }
                codigos_por_anio[anio] = codigos
                print(f"{anio}: {len(codigos)} referencias distintas vendidas")

            union_ambos = set.union(*codigos_por_anio.values()) if codigos_por_anio else set()
            interseccion = set.intersection(*codigos_por_anio.values()) if len(codigos_por_anio) > 1 else set()
            print(f"\nUnion (vendidas en 2025 y/o 2026): {len(union_ambos)}")
            if len(ANIOS) > 1:
                print(f"Interseccion (vendidas en AMBOS anios): {len(interseccion)}")
        except SQLAlchemyError as e:
            rollback_seguro()
            print(f"ERROR consultando db_ventas: {e}")
            raise


if __name__ == '__main__':
    contar_referencias_vendidas_wo()
