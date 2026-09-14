"""
CostoService - Rentabilidad por pedido (costo de fabricar la pieza vs precio de
venta cobrado), para la vista "Costo" restringida a Administración.

Deliberadamente NO incluye costos operativos (mano de obra, indirectos,
depreciación, etc.) -- eso ya lo calcula World Office. Esta vista es solo
costo de pieza (db_costos.costo_total) vs precio de venta real del pedido
(db_pedidos.precio_unitario), para responder "¿este pedido es rentable solo
viendo la pieza?".
"""
import logging
from sqlalchemy import text
from backend.core.sql_database import db, rollback_seguro
from backend.utils.formatters import sql_normalizar_codigo_fr

logger = logging.getLogger(__name__)


class CostoService:
    @staticmethod
    def listar_costos_agrupado(desde=None, hasta=None):
        """
        Un renglón por pedido: venta total, costo total de piezas y margen,
        agregando todas las líneas de db_pedidos que comparten id_pedido.

        El JOIN agrupa db_costos por referencia YA normalizada (no la referencia
        cruda) antes de unir contra db_pedidos: si no se hiciera así, una misma
        referencia cargada en db_costos con variantes tipo '9304' y 'FR-9304'
        generaría dos filas que matchean la misma línea de pedido (fan-out),
        multiplicando el costo real silenciosamente. Ver el mismo patrón (sin
        esta protección extra de normalizar antes de agrupar) en
        dashboard_repository.py.
        """
        try:
            filtro_fecha = ""
            params = {}
            if desde and hasta:
                filtro_fecha = " AND p.fecha BETWEEN :desde AND :hasta"
                params['desde'] = desde
                params['hasta'] = hasta

            norm_pedido = sql_normalizar_codigo_fr('p.id_codigo')
            norm_costo = sql_normalizar_codigo_fr('referencia')

            sql = text(f"""
                SELECT
                    p.id_pedido AS id_pedido,
                    MAX(p.cliente) AS cliente,
                    MAX(p.fecha) AS fecha,
                    MAX(p.estado) AS estado,
                    COUNT(*) AS num_lineas,
                    COUNT(c.costo_total) AS lineas_con_costo,
                    COALESCE(SUM(p.cantidad * p.precio_unitario), 0) AS venta_total,
                    COALESCE(SUM(p.cantidad * c.costo_total), 0) AS costo_total
                FROM db_pedidos p
                LEFT JOIN (
                    -- costo_total > 0: contra datos reales, ~22% de las referencias con
                    -- match en db_costos tienen costo_total = 0 (dato no diligenciado,
                    -- no un buje que de verdad cuesta $0 fabricar). Tratarlo como 0 real
                    -- inflaria el margen a ~100% de forma enganosa, asi que se descarta
                    -- igual que un NULL (sin costo) en vez de sumarlo.
                    SELECT {norm_costo} AS referencia_norm, MAX(costo_total) AS costo_total
                    FROM db_costos
                    WHERE costo_total > 0
                    GROUP BY {norm_costo}
                ) c ON {norm_pedido} = c.referencia_norm
                WHERE p.id_pedido IS NOT NULL {filtro_fecha}
                GROUP BY p.id_pedido
                ORDER BY MAX(p.fecha) DESC
            """)

            rows = db.session.execute(sql, params).mappings().all()

            resultado = []
            for row in rows:
                venta_total = float(row["venta_total"] or 0)
                costo_total = float(row["costo_total"] or 0)
                num_lineas = int(row["num_lineas"] or 0)
                lineas_con_costo = int(row["lineas_con_costo"] or 0)
                # Costeo parcial (referencias sin match en db_costos) no se
                # presenta como margen exacto -- un costo faltante tratado como
                # 0 inflaría el margen y podría hacer ver rentable un pedido
                # que en realidad no se pudo costear del todo.
                costeo_completo = num_lineas > 0 and lineas_con_costo == num_lineas

                margen = (venta_total - costo_total) if costeo_completo else None
                margen_pct = round((margen / venta_total) * 100, 1) if (margen is not None and venta_total > 0) else None

                resultado.append({
                    "id_pedido": row["id_pedido"],
                    "cliente": row["cliente"],
                    "fecha": row["fecha"].strftime('%Y-%m-%d') if row["fecha"] else None,
                    "estado": row["estado"],
                    "venta_total": venta_total,
                    "costo_total": costo_total if lineas_con_costo > 0 else None,
                    "margen": margen,
                    "margen_pct": margen_pct,
                    "num_lineas": num_lineas,
                    "lineas_con_costo": lineas_con_costo,
                    "costeo_completo": costeo_completo
                })
            return resultado
        except Exception as e:
            rollback_seguro()
            logger.error(f"[CostoService.listar_costos_agrupado] Error: {e}")
            raise

    @staticmethod
    def obtener_costo_detalle_pedido(id_pedido):
        """Detalle línea a línea de costo vs venta de un pedido puntual (drill-down)."""
        try:
            norm_pedido = sql_normalizar_codigo_fr('p.id_codigo')
            norm_costo = sql_normalizar_codigo_fr('referencia')

            sql = text(f"""
                SELECT
                    p.id_codigo AS id_codigo, p.descripcion AS descripcion,
                    p.cantidad AS cantidad, p.precio_unitario AS precio_unitario,
                    c.costo_total AS costo_unitario
                FROM db_pedidos p
                LEFT JOIN (
                    -- costo_total > 0: contra datos reales, ~22% de las referencias con
                    -- match en db_costos tienen costo_total = 0 (dato no diligenciado,
                    -- no un buje que de verdad cuesta $0 fabricar). Tratarlo como 0 real
                    -- inflaria el margen a ~100% de forma enganosa, asi que se descarta
                    -- igual que un NULL (sin costo) en vez de sumarlo.
                    SELECT {norm_costo} AS referencia_norm, MAX(costo_total) AS costo_total
                    FROM db_costos
                    WHERE costo_total > 0
                    GROUP BY {norm_costo}
                ) c ON {norm_pedido} = c.referencia_norm
                WHERE p.id_pedido = :id_pedido
                ORDER BY p.id ASC
            """)

            rows = db.session.execute(sql, {"id_pedido": id_pedido}).mappings().all()

            items = []
            for row in rows:
                cantidad = float(row["cantidad"] or 0)
                precio_unitario = float(row["precio_unitario"] or 0)
                venta_linea = cantidad * precio_unitario

                costo_unitario = row["costo_unitario"]
                if costo_unitario is not None:
                    costo_unitario = float(costo_unitario)
                    costo_linea = cantidad * costo_unitario
                    margen_linea = venta_linea - costo_linea
                    margen_pct = round((margen_linea / venta_linea) * 100, 1) if venta_linea > 0 else None
                else:
                    costo_linea = None
                    margen_linea = None
                    margen_pct = None

                items.append({
                    "codigo": row["id_codigo"],
                    "descripcion": row["descripcion"],
                    "cantidad": cantidad,
                    "precio_unitario": precio_unitario,
                    "venta_linea": venta_linea,
                    "costo_unitario": costo_unitario,
                    "costo_linea": costo_linea,
                    "margen_linea": margen_linea,
                    "margen_pct": margen_pct
                })
            return items
        except Exception as e:
            rollback_seguro()
            logger.error(f"[CostoService.obtener_costo_detalle_pedido] id_pedido={id_pedido} Error: {e}")
            raise
