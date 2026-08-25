"""
Migración: recrea mv_rendimiento_mensual con la exclusion NOT ILIKE '%FRIPARTS%'
para que quede consistente con mv_dashboard_ventas_analitica (fuente de
Backorder/Top/Peores Productos), que ya excluye FRIPARTS desde su creacion.

Sin esto, cualquier pedido/venta facturado bajo un nombre que contenga
'FRIPARTS' se sumaba en el comparativo Pedidos vs Ventas (tacometro /
Rendimiento Mensual) pero era invisible para el Backorder, generando una
brecha entre "Pedidos - Ventas" y el total de Backorder que nunca podia
reconciliar (confirmado en produccion: brecha de ~$127M).

Las vistas materializadas no soportan ALTER de su query: hay que hacer
DROP + CREATE. Se corre en autocommit fuera de una transaccion normal
porque CREATE UNIQUE INDEX / REFRESH CONCURRENTLY no pueden envolverse en
un BEGIN explicito junto con el DROP.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS mv_rendimiento_mensual"

CREATE_SQL = """
CREATE MATERIALIZED VIEW mv_rendimiento_mensual AS
SELECT
    EXTRACT(YEAR FROM fecha)::INTEGER AS ano,
    EXTRACT(MONTH FROM fecha)::INTEGER AS mes,
    SUM(CASE WHEN clasificacion ILIKE '%venta%'  THEN COALESCE(total_ingresos, 0) ELSE 0 END) AS ventas_dinero,
    SUM(CASE WHEN clasificacion ILIKE '%pedido%' THEN COALESCE(total_ingresos, 0) ELSE 0 END) AS pedidos_dinero,
    SUM(CASE WHEN clasificacion ILIKE '%venta%'  THEN COALESCE(cantidad, 0) ELSE 0 END) AS ventas_unidades,
    SUM(CASE WHEN clasificacion ILIKE '%pedido%' THEN COALESCE(cantidad, 0) ELSE 0 END) AS pedidos_unidades
FROM db_ventas
WHERE fecha IS NOT NULL
  AND UPPER(TRIM(nombres)) NOT ILIKE '%FRIPARTS%'
GROUP BY 1, 2
"""

CREATE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_rendimiento_mensual_pk "
    "ON mv_rendimiento_mensual (ano, mes)"
)

with app.app_context():
    conn = db.engine.connect()
    conn = conn.execution_options(isolation_level="AUTOCOMMIT")
    try:
        conn.execute(text(DROP_SQL))
        print("OK: DROP MATERIALIZED VIEW mv_rendimiento_mensual")
        conn.execute(text(CREATE_SQL))
        print("OK: CREATE MATERIALIZED VIEW mv_rendimiento_mensual (excluye FRIPARTS)")
        conn.execute(text(CREATE_INDEX_SQL))
        print("OK: idx_mv_rendimiento_mensual_pk")
    except Exception as e:
        print(f"ERROR migrando mv_rendimiento_mensual: {e}")
    finally:
        conn.close()
