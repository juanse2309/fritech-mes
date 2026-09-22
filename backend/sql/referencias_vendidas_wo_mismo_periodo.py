"""
Comparación JUSTA de referencias vendidas 2025 vs 2026 según db_ventas
(World Office), instancia FRIPARTS (2026-09-22).

Complementa a referencias_vendidas_wo_2025_2026.py: ese script compara AÑO
COMPLETO 2025 contra 2026 aún en curso (hoy 2026-09-22), lo que subestima
2026 artificialmente -- 463 vs 443 no es "vendieron menos referencias este
año", es "12 meses vs 8.7 meses". Este script fija el mismo rango de
fechas (01-ene al 22-sep) en ambos años para que la comparación tenga
sentido.

Misma limitación conocida que el script hermano: clasificacion ILIKE
'%venta%' incluye FV/COT/NC/NCV/NCCL/DMC (ver agente_wo_comercial.py
líneas 552-568), no solo facturado.

Solo lectura -- no modifica nada.
    python -m backend.sql.referencias_vendidas_wo_mismo_periodo
"""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.core.sql_database import db, rollback_seguro
from backend.app import app

# Mismo corte que "hoy" en ambos años -- ajustar si se corre otro día.
MES_DIA_CORTE = (9, 22)
ANIOS = (2025, 2026)

SQL_CODIGOS_PERIODO = text("""
    WITH VentasPeriodo AS (
        SELECT
            CASE
                WHEN productos LIKE '%|%' THEN TRIM(SPLIT_PART(productos, '|', 1))
                WHEN productos LIKE '% %' THEN TRIM(SPLIT_PART(productos, ' ', 1))
                ELSE TRIM(productos)
            END AS raw_codigo
        FROM db_ventas
        WHERE clasificacion ILIKE '%venta%'
          AND CAST(fecha AS DATE) >= :inicio
          AND CAST(fecha AS DATE) <= :fin
    )
    SELECT DISTINCT UPPER(REPLACE(raw_codigo, 'FR-', '')) AS codigo
    FROM VentasPeriodo
    WHERE raw_codigo IS NOT NULL AND raw_codigo != ''
""")


def comparar_mismo_periodo():
    mes, dia = MES_DIA_CORTE
    with app.app_context():
        try:
            print("Fuente: db_ventas (World Office) -- mismo rango de fechas en ambos años")
            print(f"Rango: 01-01 al {dia:02d}-{mes:02d} en ambos años\n")

            codigos_por_anio = {}
            for anio in ANIOS:
                inicio = f"{anio}-01-01"
                fin = f"{anio}-{mes:02d}-{dia:02d}"
                codigos = {
                    r[0] for r in db.session.execute(
                        SQL_CODIGOS_PERIODO, {"inicio": inicio, "fin": fin}
                    ).all()
                }
                codigos_por_anio[anio] = codigos
                print(f"{anio} (01-ene a {dia:02d}-{mes:02d}): {len(codigos)} referencias distintas vendidas")

            union_ambos = set.union(*codigos_por_anio.values()) if codigos_por_anio else set()
            interseccion = set.intersection(*codigos_por_anio.values()) if len(codigos_por_anio) > 1 else set()
            solo_2025 = codigos_por_anio.get(2025, set()) - codigos_por_anio.get(2026, set())
            solo_2026 = codigos_por_anio.get(2026, set()) - codigos_por_anio.get(2025, set())

            print(f"\nUnion: {len(union_ambos)}")
            print(f"Interseccion (en ambos periodos): {len(interseccion)}")
            print(f"Solo en 2025 (dejaron de venderse en lo que va de 2026): {len(solo_2025)}")
            print(f"Solo en 2026 (nuevas, no vendidas en el mismo periodo de 2025): {len(solo_2026)}")
        except SQLAlchemyError as e:
            rollback_seguro()
            print(f"ERROR consultando db_ventas: {e}")
            raise


if __name__ == '__main__':
    comparar_mismo_periodo()
