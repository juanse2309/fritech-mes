"""
Repositorio de Dashboard/Analytics 100% SQL-First.
Centraliza KPIs de producción, rankings, costeo de PNC y métricas de Jefatura.
"""
import logging
from datetime import datetime
from sqlalchemy import text
from backend.core.sql_database import db, rollback_seguro
from backend.config.business_rules import CatalogExclusionConfig
from backend.utils.numeric_helpers import _num

logger = logging.getLogger(__name__)


# Último despacho por referencia. Se agrupa por el código YA normalizado y no por
# id_codigo crudo: en crudo 'FR-9843' y '9843' quedarían como dos filas distintas y
# el LEFT JOIN normalizado devolvería dos matches para el mismo producto, duplicando
# la fila de Backorder. Con el GROUP BY sobre la expresión normalizada la CTE es 1:1
# por referencia, así que el JOIN no puede multiplicar filas por más despachos
# parciales históricos que tenga el ítem.
_SQL_CTE_ULTIMOS_DESPACHOS = """
    ultimos_despachos AS (
        SELECT
            TRIM(UPPER(REPLACE(id_codigo::TEXT, 'FR-', ''))) AS ref_despacho,
            MAX(fecha) AS ultima_fecha_despacho
        FROM db_despachos_pedido
        WHERE id_codigo IS NOT NULL
        GROUP BY 1
    )
"""


def _sql_ref_desde_producto(col):
    """
    db_ventas.productos y mv_dashboard_ventas_analitica.producto son texto libre de
    World Office ('FR-9843 BUJE ...'): se toma el primer token sin prefijo 'FR-',
    mismo criterio que VentasRepository.get_backorder_detalle_por_cliente, para que
    ambos lados del JOIN contra db_despachos_pedido.id_codigo queden comparables.
    """
    return f"TRIM(UPPER(split_part(REPLACE({col}::TEXT, 'FR-', ''), ' ', 1)))"


class DashboardRepository:
    """Repositorio para KPIs, rankings y analítica de Dashboard/Jefatura vía SQL crudo."""

    @classmethod
    def get_dashboard_kpis(cls, desde=None, hasta=None, incluir_costeo_scrap=True):
        """
        Calcula KPIs de producción 100% SQL-Native con CAST ROBUSTO.
        Resistente a strings inválidos como '30/12/1899' en columnas TEXT.

        incluir_costeo_scrap=False (pedido del usuario 2026-09-04, vista
        liviana de Pulido en /api/dashboard/stats): se salta
        get_perdida_economica_scrap -- costea TODO el detalle de PNC contra
        db_costos y solo alimenta 'Resumen' y 'Análitica de Scrap y PNC',
        ninguna de las 2 secciones que una operaria de Pulido puede ver.
        """
        try:
            params = {'desde': desde, 'hasta': hasta}
            # CAST(... AS DATE): fecha/fecha_inicia son TIMESTAMP, no DATE. Un BETWEEN
            # directo contra un string 'YYYY-MM-DD' interpreta :hasta como medianoche
            # (00:00:00) y descarta CASI TODOS los registros reales de ese día (guardados
            # con hora de turno, ej. 06:40) -- confirmado en producción: filtrar un solo
            # día de Inyección devolvía 0 piezas aunque sí había producción esa fecha.
            filt_iny = " WHERE CAST(fecha_inicia AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"
            filt_gen = " WHERE CAST(fecha AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"

            # Las columnas de db_mezcla/db_ventas ya son NUMERIC nativo en Postgres:
            # el cast previo pasaba por texto+regex y arrastraba dos bugs con
            # devoluciones/NC negativas — [^0-9.] borraba el signo '-', y
            # '[$. ]' borraba también el punto decimal (corriendo la escala x100).
            # COALESCE directo evita ambos por construcción.
            def _user_cast(col):
                return f"COALESCE({col}, 0)"

            def _sql_cast_num(col):
                return f"COALESCE({col}, 0)"

            # Filtros específicos con prefijos
            filt_iny_pnc = " WHERE CAST(i.fecha_inicia AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"
            filt_pul_pnc = " WHERE CAST(d.fecha AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"

            # --- KPIs consolidados en UN solo round-trip ---
            # Antes: 9 db.session.execute() secuenciales (iny/pul/ens OK+PNC, ventas,
            # pedidos, mezcla), cada uno pagando latencia de red completa por separado.
            # Ahora: una sola query con subqueries escalares independientes; el motor
            # de Postgres las resuelve dentro del mismo plan de ejecución/round-trip.
            sql_kpis = f"""
                SELECT
                    (SELECT SUM({_user_cast('cantidad_real')}) FROM db_inyeccion {filt_iny}) AS iny_ok,
                    (SELECT SUM({_user_cast('p.cantidad')})
                       FROM db_pnc_inyeccion p
                       LEFT JOIN db_inyeccion i ON p.id_inyeccion = i.id_inyeccion
                       {filt_iny_pnc}) AS iny_pnc,
                    (SELECT SUM({_user_cast('cantidad_real')}) FROM db_pulido {filt_gen}) AS pul_ok,
                    (SELECT SUM({_user_cast('p.cantidad')})
                       FROM db_pnc_pulido p
                       LEFT JOIN db_pulido d ON p.id_pulido = d.id_pulido
                       {filt_pul_pnc}) AS pul_pnc,
                    (SELECT SUM({_user_cast('cantidad')}) FROM db_ensambles {filt_gen}) AS ens_ok,
                    (SELECT SUM({_user_cast('p.cantidad')})
                       FROM db_pnc_ensamble p
                       LEFT JOIN db_ensambles d ON p.id_ensamble = d.id_ensamble
                       {filt_pul_pnc}) AS ens_pnc,
                    (SELECT COALESCE(SUM({_sql_cast_num('total_ingresos')}), 0) FROM db_ventas {filt_gen}) AS ventas_totales,
                    (SELECT COALESCE(SUM({_sql_cast_num('cantidad')}), 0) FROM db_ventas {filt_gen}) AS ventas_unidades,
                    (SELECT COALESCE(SUM({_sql_cast_num('cantidad')}), 0) FROM db_ventas {filt_gen} AND clasificacion ILIKE '%pedido%') AS pedidos_sum,
                    (SELECT SUM({_user_cast('virgen_kg')} + {_user_cast('molido_kg')}) FROM db_mezcla {filt_gen}) AS mezcla_total
            """
            r = db.session.execute(text(sql_kpis), params).mappings().fetchone()

            iny_ok = _num(r['iny_ok'])
            iny_pnc = _num(r['iny_pnc'])
            pul_ok = _num(r['pul_ok'])
            pul_pnc = _num(r['pul_pnc'])
            ens_ok = _num(r['ens_ok'])
            ens_pnc = _num(r['ens_pnc'])
            ventas_totales = _num(r['ventas_totales'])
            ventas_unidades = _num(r['ventas_unidades'])
            pedidos_sum = _num(r['pedidos_sum'])
            mezcla_total = _num(r['mezcla_total'])

            # --- Pérdida en Dinero ---
            # Costeo por referencia (JOIN a db_costos + agregación fila a fila): no es
            # un escalar simple, se mantiene como llamada propia fuera del consolidado.
            if incluir_costeo_scrap:
                scrap_resumen = cls.get_perdida_economica_scrap(desde, hasta)
                perdida_dinero = scrap_resumen.get("total_perdida", 0)
                scrap_ranking = scrap_resumen.get("ranking_productos", [])
            else:
                perdida_dinero = 0
                scrap_ranking = []

            # --- Cálculo FPY y % PNC Global ---
            scrap_tot = iny_pnc + pul_pnc + ens_pnc
            total_piezas_prod = iny_ok + pul_ok + ens_ok + scrap_tot
            pct_pnc = round((scrap_tot / total_piezas_prod * 100.0), 2) if total_piezas_prod > 0 else 0.0
            fpy_glob = round(max(0.0, 100.0 - pct_pnc), 2)

            return {
                'inyeccion_ok':  iny_ok,
                'inyeccion_pnc': iny_pnc,
                'pulido_ok':     pul_ok,
                'pulido_pnc':    pul_pnc,
                'ensambles_ok':  ens_ok,
                'ensamble_pnc':  ens_pnc,
                'ventas_totales': ventas_totales,
                'ventas_unidades': ventas_unidades,
                'pedidos_solicitados': pedidos_sum,
                'mezcla_total_kg': mezcla_total,
                'scrap_total':   scrap_tot,
                'pct_pnc_total': pct_pnc,
                'fpy_global':    fpy_glob,
                'scrap_detalle': {
                    'inyeccion': iny_pnc,
                    'pulido':    pul_pnc,
                    'ensamble':  ens_pnc,
                    'almacen':   0
                },
                'perdida_calidad_dinero': perdida_dinero,
                'scrap_almacen_desglose': scrap_ranking
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_dashboard_kpis] {e}")
            return {
                'inyeccion_ok': 0, 'inyeccion_pnc': 0,
                'pulido_ok': 0, 'pulido_pnc': 0,
                'ensambles_ok': 0, 'ensamble_pnc': 0,
                'scrap_total': 0, 'ventas_totales': 0, 'ventas_unidades': 0,
                'pedidos_solicitados': 0,
                'perdida_calidad_dinero': 0,
                'scrap_detalle': {'inyeccion': 0, 'pulido': 0, 'ensamble': 0, 'almacen': 0}
            }

    @classmethod
    def get_ranking_operarios_inyeccion(cls, desde=None, hasta=None, limit=20):
        """Ranking de operarios de inyección (SQL-Native) con CAST ROBUSTO."""
        try:
            # Ranking resiliente a basura y basado en detalle de scrap
            sql = f"""
                SELECT
                    i.responsable,
                    SUM(COALESCE(i.cantidad_real, 0)) as total,
                    SUM(COALESCE(pnc.total_pnc, 0)) as scrap
                FROM db_inyeccion i
                LEFT JOIN (
                    SELECT id_inyeccion,
                           SUM(COALESCE(NULLIF(regexp_replace(cantidad::text, '[^0-9.]', '', 'g'), ''), '0')::NUMERIC) as total_pnc
                    FROM db_pnc_inyeccion
                    GROUP BY id_inyeccion
                ) pnc ON i.id_inyeccion = pnc.id_inyeccion
                WHERE 1=1
            """
            params = {'lim': limit}
            if desde and hasta:
                sql += ' AND CAST(i.fecha_inicia AS DATE) BETWEEN :desde AND :hasta'
                params['desde'] = desde
                params['hasta'] = hasta
            sql += ' GROUP BY i.responsable ORDER BY total DESC LIMIT :lim'

            rows = db.session.execute(text(sql), params).fetchall()
            return [{'nombre': str(r[0] or 'Desconocido').strip(), 'valor': _num(r[1]), 'pnc': _num(r[2])} for r in rows]
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_ranking_operarios_inyeccion] {e}")
            return []

    @classmethod
    def get_ranking_maquinas(cls, desde=None, hasta=None):
        """Distribución por máquina (SQL-Native) con CAST ROBUSTO."""
        try:
            sql = f"SELECT maquina, SUM(COALESCE(cantidad_real, 0)) as total FROM db_inyeccion WHERE 1=1"
            params = {}
            if desde and hasta:
                sql += ' AND CAST(fecha_inicia AS DATE) BETWEEN :desde AND :hasta'
                params['desde'] = desde
                params['hasta'] = hasta
            sql += ' GROUP BY maquina ORDER BY total DESC'

            rows = db.session.execute(text(sql), params).fetchall()
            return [{'maquina': str(r[0] or 'Desconocida').strip(), 'valor': _num(r[1])} for r in rows]
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_ranking_maquinas] {e}")
            return []

    @classmethod
    def get_ranking_operarios_pulido(cls, desde=None, hasta=None, limit=20):
        """Ranking extendido de pulido (SQL-Native) por volumen físico y Eficiencia."""
        try:
            from backend.utils.formatters import sql_normalizar_codigo_fr
            params = {'lim': limit}
            filt = ""
            if desde and hasta:
                filt = " AND CAST(p.fecha AS DATE) BETWEEN :desde AND :hasta"
                params['desde'] = desde
                params['hasta'] = hasta

            sql = f"""
                SELECT
                    UPPER(TRIM(p.responsable)) as responsable,
                    COALESCE(SUM(NULLIF(regexp_replace(p.cantidad_real::text, '[^0-9]', '', 'g'), '')::INTEGER), 0) as buenas,
                    COALESCE(SUM(NULLIF(regexp_replace(p.pnc_pulido::text, '[^0-9]', '', 'g'), '')::INTEGER), 0) as pnc,
                    COALESCE(SUM(NULLIF(regexp_replace(p.tiempo_total_minutos::text, '[^0-9]', '', 'g'), '')::INTEGER), 0) as tiempo_real,
                    -- tiempo_real tampoco incluye los lotes sin tiempo, y mezclar poblaciones distintas
                    -- en el numerador/denominador de la eficiencia dispara el ratio.
                    COALESCE(SUM(CASE WHEN COALESCE(p.tiempo_total_minutos, 0) > 0 THEN NULLIF(regexp_replace(p.cantidad_real::text, '[^0-9]', '', 'g'), '')::INTEGER ELSE 0 END * COALESCE(NULLIF(regexp_replace(REPLACE(c.tiempo_estandar::text, ',', '.'), '[^0-9.]', '', 'g'), ''), '0')::NUMERIC), 0) as tiempo_std
                FROM db_pulido p
                LEFT JOIN db_costos c ON {sql_normalizar_codigo_fr('p.codigo')} = {sql_normalizar_codigo_fr('c.referencia')}
                WHERE 1=1 {filt}
                GROUP BY UPPER(TRIM(p.responsable))
                ORDER BY buenas DESC
                LIMIT :lim
            """
            rows = db.session.execute(text(sql), params).fetchall()

            resultado = []
            for r in rows:
                nombre = str(r[0] or 'Desconocido').strip()
                buenas = int(r[1])
                pnc = int(r[2])
                t_real = int(r[3])
                t_std = float(r[4])

                # Eficiencia = (Tiempo Standard / Tiempo Real) * 100
                eficiencia = round((t_std / t_real * 100), 1) if t_real > 0 else 0

                resultado.append({
                    'nombre': nombre,
                    'valor': buenas,
                    'pnc': pnc,
                    'eficiencia': eficiencia,
                    'minutos': t_real
                })
            return resultado
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_ranking_operarios_pulido] {e}")
            return []

    @classmethod
    def get_analytics_inyeccion(cls, desde=None, hasta=None):
        """Genera detalle de referencias por operario para Inyección (Buenas y PNC)"""
        try:
            params = {}
            filt_iny = ""
            filt_pnc = ""
            if desde and hasta:
                filt_iny = " AND CAST(fecha_inicia AS DATE) BETWEEN :desde AND :hasta"
                filt_pnc = " AND CAST(i.fecha_inicia AS DATE) BETWEEN :desde AND :hasta"
                params['desde'] = desde
                params['hasta'] = hasta

            # 1. Obtener buenas por operario y referencia
            sql_buenas = f"""
                SELECT
                    UPPER(TRIM(responsable)) as responsable,
                    TRIM(id_codigo::TEXT) as referencia,
                    SUM(COALESCE(cantidad_real, 0)) as buenas
                FROM db_inyeccion
                WHERE 1=1 {filt_iny}
                GROUP BY 1, 2
            """
            rows_buenas = db.session.execute(text(sql_buenas), params).fetchall()

            # 2. Obtener pnc por operario y referencia
            sql_pnc = f"""
                SELECT
                    UPPER(TRIM(i.responsable)) as responsable,
                    TRIM(p.id_codigo::TEXT) as referencia,
                    SUM(COALESCE(p.cantidad, 0)) as pnc
                FROM db_pnc_inyeccion p
                JOIN db_inyeccion i ON p.id_inyeccion = i.id_inyeccion
                WHERE 1=1 {filt_pnc}
                GROUP BY 1, 2
            """
            rows_pnc = db.session.execute(text(sql_pnc), params).fetchall()

            # Merge
            refs_map = {}

            for r in rows_buenas:
                res = str(r[0] or 'Desconocido').strip()
                ref = str(r[1] or 'Sin Referencia').strip()
                buenas = int(r[2] or 0)

                if res not in refs_map: refs_map[res] = {}
                if ref not in refs_map[res]: refs_map[res][ref] = {"buenas": 0, "pnc": 0}
                refs_map[res][ref]["buenas"] += buenas

            for r in rows_pnc:
                res = str(r[0] or 'Desconocido').strip()
                ref = str(r[1] or 'Sin Referencia').strip()
                pnc = int(r[2] or 0)

                if res not in refs_map: refs_map[res] = {}
                if ref not in refs_map[res]: refs_map[res][ref] = {"buenas": 0, "pnc": 0}
                refs_map[res][ref]["pnc"] += pnc

            return {
                "operario_referencia": refs_map
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_analytics_inyeccion] {e}")
            return {"operario_referencia": {}}

    @classmethod
    def get_pnc_detalle_costeado(cls, desde=None, hasta=None, columnas_tipadas_inyeccion=None):
        """
        Fuente cruda 100% SQL, costeada contra db_costos por referencia:
        una fila por (área, referencia, criterio). Inyección viene YA
        pivotada por columna tipada (quemado_manchado, rebaba_excesiva, ...)
        en vez de texto libre — evita que esta capa SQL tenga que adivinar
        nada. Pulido/Ensamble traen su `criterio` de texto libre tal cual;
        normalizarlo es responsabilidad de la capa de negocio (PncService),
        no de este repositorio.

        `columnas_tipadas_inyeccion`: dict {columna_sql: criterio_normalizado}
        inyectado por el caller (PncService es la fuente de verdad del
        catálogo) para no crear un import circular entre ambos módulos.

        Devuelve: [{area, ref, criterio, cantidad, costo_unitario, costo_total}, ...]
        """
        try:
            # Fallback local si no se inyecta el mapeo (mantiene el método usable standalone)
            columnas_iny = columnas_tipadas_inyeccion or {
                "quemado_manchado": "Quemado/Manchado",
                "incompleto_falta_llenado": "Incompleto",
                "rebaba_excesiva": "Rebaba",
                "burbuja_porosidad": "Burbujas/Porosidad",
                "deformacion_rechupado": "Rechupe/Deformado",
            }

            params = {'desde': desde, 'hasta': hasta}
            filt_iny = " WHERE CAST(i.fecha_inicia AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"
            filt_pul = " WHERE CAST(d.fecha AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"
            filt_ens = " WHERE CAST(e.fecha AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"
            # Filtro empujado DENTRO de las CTEs de representante (abajo): sin esto,
            # el DISTINCT ON + ORDER BY escanea y ordena la tabla db_inyeccion/db_pulido
            # COMPLETA incluso cuando el usuario pide un solo día, y el filtro de fecha
            # (filt_iny/filt_pul de arriba) recién se aplica DESPUÉS, sobre el resultado
            # ya materializado. Al acotar aquí, Postgres solo ordena las filas del rango.
            filt_iny_cte = " AND CAST(fecha_inicia AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else ""
            filt_pul_cte = " AND CAST(fecha AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else ""

            def _user_cast(col):
                # costo_total ya es NUMERIC nativo: COALESCE directo, sin el
                # bug de perder el signo '-' del cast via texto+regex.
                return f"COALESCE({col}, 0)"

            # EXPLAIN ANALYZE mostró que el cuello de botella real de esta query NO es
            # el JOIN contra db_costos (386 filas, ~0.5ms) sino que las 6 ramas de
            # inyección (una por columna tipada + "Sin Clasificar") repetían, cada una,
            # el mismo Seq Scan + Hash Join de db_pnc_inyeccion (2589 filas) x iny_repr
            # (7905 filas) — ~500-600ms de los ~700ms totales. Se calculan las 6 sumas
            # en UN solo GROUP BY (una sola pasada por la tabla) y se "unpivotea" con
            # VALUES + LATERAL, en vez de una tabla más chica no hay índice B-Tree que
            # acelere esto — el problema era de forma de la query, no de índices.
            sum_cols_sql = ",\n                        ".join(
                f"SUM(COALESCE(p.{col}, 0)) as qty_{col}" for col in columnas_iny.keys()
            )
            columnas_suma_sql = " + ".join(f"COALESCE(p.{c}, 0)" for c in columnas_iny.keys())
            values_rows_sql = ",\n                        ".join(
                f"('{criterio}', qty_{col})" for col, criterio in columnas_iny.items()
            )

            sql = text(f"""
                WITH unique_costs AS (
                    SELECT
                        TRIM(UPPER(REPLACE(referencia::TEXT, 'FR-', ''))) as ref_costo,
                        MAX({_user_cast('costo_total')}) as cost
                    FROM db_costos
                    GROUP BY 1
                ),
                -- Fila representante por id_inyeccion, calculada UNA sola vez.
                iny_repr AS (
                    SELECT DISTINCT ON (id_inyeccion) id_inyeccion, fecha_inicia
                    FROM db_inyeccion WHERE fecha_inicia IS NOT NULL{filt_iny_cte}
                    ORDER BY id_inyeccion, fecha_inicia DESC
                ),
                -- Una sola pasada por db_pnc_inyeccion x iny_repr calculando las 6 sumas
                -- (5 columnas tipadas + remanente sin clasificar) simultáneamente, en vez
                -- de 6 escaneos independientes del mismo JOIN.
                inyeccion_agg AS (
                    SELECT
                        TRIM(REPLACE(p.id_codigo::TEXT, 'FR-', '')) as ref,
                        {sum_cols_sql},
                        SUM(GREATEST(COALESCE(p.cantidad, 0) - ({columnas_suma_sql}), 0)) as qty_sin_clasificar
                    FROM db_pnc_inyeccion p
                    LEFT JOIN iny_repr i ON p.id_inyeccion = i.id_inyeccion
                    {filt_iny}
                    GROUP BY 1
                ),
                -- Unpivot: cada columna qty_* de inyeccion_agg se convierte en una fila
                -- (criterio, qty), preservando exactamente la forma que pnc_crudo espera.
                inyeccion_unpivot AS (
                    SELECT 'inyeccion' as area, a.ref, v.criterio, v.qty
                    FROM inyeccion_agg a
                    CROSS JOIN LATERAL (VALUES
                        {values_rows_sql},
                        ('Sin Clasificar', a.qty_sin_clasificar)
                    ) AS v(criterio, qty)
                    WHERE v.qty > 0
                ),
                pnc_crudo AS (
                    SELECT area, ref, criterio, qty FROM inyeccion_unpivot

                    UNION ALL

                    -- Pulido: criterio de texto libre tal cual (se normaliza en PncService)
                    SELECT
                        'pulido' as area,
                        TRIM(REPLACE(p.codigo::TEXT, 'FR-', '')) as ref,
                        COALESCE(NULLIF(TRIM(p.criterio), ''), 'Otros') as criterio,
                        SUM({_user_cast('p.cantidad')}) as qty
                    FROM db_pnc_pulido p
                    LEFT JOIN (
                        SELECT DISTINCT ON (id_pulido::text) id_pulido::text as id_pulido, fecha
                        FROM db_pulido WHERE fecha IS NOT NULL{filt_pul_cte}
                        ORDER BY id_pulido::text, fecha DESC
                    ) d ON p.id_pulido::text = d.id_pulido
                    {filt_pul}
                    GROUP BY 1, 2, 3

                    UNION ALL

                    -- Ensamble: criterio de texto libre tal cual (se normaliza en PncService)
                    SELECT
                        'ensamble' as area,
                        TRIM(REPLACE(p.id_codigo::TEXT, 'FR-', '')) as ref,
                        COALESCE(NULLIF(TRIM(p.criterio), ''), 'Otros') as criterio,
                        SUM({_user_cast('p.cantidad')}) as qty
                    FROM db_pnc_ensamble p
                    LEFT JOIN db_ensambles e ON p.id_ensamble = e.id_ensamble
                    {filt_ens}
                    GROUP BY 1, 2, 3
                )
                SELECT
                    s.area, s.ref, s.criterio, s.qty as cantidad,
                    COALESCE(c.cost, 0) as costo_unitario,
                    s.qty * COALESCE(c.cost, 0) as costo_total
                FROM pnc_crudo s
                LEFT JOIN unique_costs c ON s.ref = c.ref_costo
                WHERE s.qty > 0
            """)

            rows = db.session.execute(sql, params).mappings().all()
            return [{
                "area": r["area"],
                "ref": r["ref"],
                "criterio": r["criterio"],
                "cantidad": _num(r["cantidad"]),
                "costo_unitario": _num(r["costo_unitario"]),
                "costo_total": _num(r["costo_total"]),
            } for r in rows]
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_pnc_detalle_costeado] ERROR: {e}")
            return []

    @classmethod
    def get_perdida_economica_scrap(cls, desde=None, hasta=None):
        """
        Calcula la pérdida financiera por scrap agrupada por referencia.
        Reutiliza get_pnc_detalle_costeado (fuente única costeada) en vez de
        repetir su propia CTE — antes esta función y el desglose por
        criterio del Dashboard consultaban la misma data con dos queries
        independientes que podían divergir.
        """
        try:
            filas = cls.get_pnc_detalle_costeado(desde=desde, hasta=hasta)

            total_perdida = sum(f["costo_total"] for f in filas)

            por_ref = {}
            for f in filas:
                ref = f["ref"]
                if not ref:
                    continue
                acc = por_ref.setdefault(ref, {"cantidad": 0.0, "costo": 0.0})
                acc["cantidad"] += f["cantidad"]
                acc["costo"] += f["costo_total"]

            ranking = sorted(
                [{"producto": ref, "cantidad": v["cantidad"], "costo": v["costo"]} for ref, v in por_ref.items()],
                key=lambda x: (x["costo"], x["cantidad"]),
                reverse=True
            )[:10]

            return {
                "total_perdida": total_perdida,
                "ranking_productos": ranking
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_perdida_economica_scrap] ERROR: {e}")
            return {"total_perdida": 0, "ranking_productos": []}

    @classmethod
    def get_rendimiento_mensual_sql(cls, start_date=None, end_date=None):
        """
        Calcula el rendimiento mensual (comparativo Año Actual vs Año Anterior) 100% SQL-Native.
        Si se proveen fechas, calcula automáticamente el periodo espejo del año anterior.

        Sin fechas (caso por defecto): lee de mv_rendimiento_mensual, que ya viene
        agregada por (año, mes) — ver backend/sql/create_mv_rendimiento_mensual.sql.
        Con fechas: la vista no tiene granularidad diaria (un filtro parcial de mes
        perdería precisión), así que se mantiene el camino en vivo original sobre
        db_ventas con el rango espejo año-anterior.
        """
        try:
            meses_map = {1:'ene',2:'feb',3:'mar',4:'abr',5:'may',6:'jun',7:'jul',8:'ago',9:'sep',10:'oct',11:'nov',12:'dic'}

            def _sql_cast_num(col):
                # Columna ya NUMERIC nativo: COALESCE directo evita el bug de perder
                # el signo '-' de devoluciones/NC que tenia el cast via texto+regex.
                return f"COALESCE({col}, 0)"

            params = {}
            year_actual = 2026
            year_prev = 2025
            con_filtro_fecha = False

            if start_date and end_date:
                try:
                    sd = datetime.strptime(start_date, '%Y-%m-%d')
                    ed = datetime.strptime(end_date, '%Y-%m-%d')

                    # Periodo espejo año anterior
                    sd_prev = sd.replace(year=sd.year - 1)
                    ed_prev = ed.replace(year=ed.year - 1)

                    # NOT ILIKE FRIPARTS: mismo criterio de exclusion que
                    # mv_dashboard_ventas_analitica / Backorder (ver
                    # _get_admin_dashboard_metrics_sql_impl y create_mv_dashboard_ventas_analitica.sql).
                    # Sin esto, pedidos/ventas facturados bajo un nombre que contiene
                    # 'FRIPARTS' se contaban aqui pero quedaban invisibles para el
                    # Backorder, generando una brecha entre "Pedidos - Ventas" del
                    # tacometro/rendimiento mensual y el total de Backorder que nunca
                    # podia reconciliar (confirmado: brecha de ~$127M en produccion).
                    filt = (
                        " WHERE ((CAST(fecha AS DATE) BETWEEN :start AND :end) OR (CAST(fecha AS DATE) BETWEEN :start_prev AND :end_prev))"
                        " AND UPPER(TRIM(nombres)) NOT ILIKE '%FRIPARTS%'"
                    )
                    params = {
                        "start": start_date,
                        "end": end_date,
                        "start_prev": sd_prev.strftime('%Y-%m-%d'),
                        "end_prev": ed_prev.strftime('%Y-%m-%d')
                    }
                    year_actual = sd.year
                    year_prev = year_actual - 1
                    con_filtro_fecha = True
                except Exception as e:
                    logger.warning(f"Error parsing dates for YoY: {e}. Falling back to default years.")

            if con_filtro_fecha:
                sql = f"""
                    SELECT
                        EXTRACT(MONTH FROM fecha)::INTEGER as mes,
                        EXTRACT(YEAR FROM fecha)::INTEGER as ano,
                        SUM(COALESCE(CASE WHEN clasificacion ILIKE '%venta%' THEN {_sql_cast_num('total_ingresos')} ELSE 0 END, 0)) as ventas,
                        SUM(COALESCE(CASE WHEN clasificacion ILIKE '%pedido%' THEN {_sql_cast_num('total_ingresos')} ELSE 0 END, 0)) as pedidos,
                        SUM(COALESCE(CASE WHEN clasificacion ILIKE '%venta%' THEN {_sql_cast_num('cantidad')} ELSE 0 END, 0)) as v_unds,
                        SUM(COALESCE(CASE WHEN clasificacion ILIKE '%pedido%' THEN {_sql_cast_num('cantidad')} ELSE 0 END, 0)) as p_unds
                    FROM db_ventas
                    {filt}
                    GROUP BY ano, mes
                    ORDER BY ano, mes
                """
                rows = db.session.execute(text(sql), params).fetchall()
            else:
                # Sin filtro: mismo año_actual/año_previo hardcoded (2026/2025) que el
                # camino en vivo usaba por defecto — se preserva para no cambiar el
                # comportamiento existente del comparativo YoY.
                sql = """
                    SELECT mes, ano, ventas_dinero as ventas, pedidos_dinero as pedidos,
                           ventas_unidades as v_unds, pedidos_unidades as p_unds
                    FROM mv_rendimiento_mensual
                    WHERE ano IN (2025, 2026)
                    ORDER BY ano, mes
                """
                rows = db.session.execute(text(sql)).fetchall()

            # Organizar por mes (1-12)
            data_map = {m: {
                "mes": meses_map[m],
                "mes_num": m,  # Número real de calendario (1-12): fuente de verdad para el frontend, no reconstruible por string
                "actual_dinero": 0, "actual_pedidos": 0,
                "prev_dinero": 0, "prev_pedidos": 0,
                "actual_unidades": 0, "actual_pedidos_unidades": 0,
                "prev_unidades": 0, "prev_pedidos_unidades": 0,
                "ventas_dinero": 0, "pedidos_dinero": 0,
                "ventas_qty": 0, "pedidos_qty": 0
            } for m in range(1, 13)}

            for r in rows:
                m, y = r[0], r[1]
                if not m or m not in data_map: continue
                v, p, v_u, p_u = _num(r[2]), _num(r[3]), _num(r[4]), _num(r[5])

                if y == year_actual:
                    data_map[m].update({
                        "actual_dinero": v, "actual_pedidos": p,
                        "actual_unidades": v_u, "actual_pedidos_unidades": p_u,
                        "ventas_dinero": v, "pedidos_dinero": p,
                        "ventas_qty": v_u, "pedidos_qty": p_u
                    })
                elif y == year_prev:
                    data_map[m].update({
                        "prev_dinero": v, "prev_pedidos": p,
                        "prev_unidades": v_u, "prev_pedidos_unidades": p_u
                    })

            # Truncar estrictamente la lista de meses según el rango enviado.
            # BUGFIX: antes requería start_date Y end_date simultáneamente; si el usuario
            # solo fijaba 'hasta' (caso común en el filtro del dashboard), la condición
            # completa se saltaba y se devolvían los 12 meses sin truncar, filtrando
            # meses futuros (ej. Julio) aunque 'hasta' fuera Junio.
            if end_date:
                try:
                    ed = datetime.strptime(end_date, '%Y-%m-%d')
                    max_m = min(12, ed.month)
                    min_m = 1
                    if start_date:
                        sd = datetime.strptime(start_date, '%Y-%m-%d')
                        min_m = max(1, min(sd.month, ed.month))
                    min_m = min(min_m, max_m)
                    return [data_map[m] for m in range(min_m, max_m + 1)]
                except Exception as parse_err:
                    logger.warning(f"No se pudo truncar la serie mensual por fecha: {parse_err}")

            return list(data_map.values())

        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_rendimiento_mensual_sql] {e}")
            return []

    @classmethod
    def get_admin_dashboard_metrics_sql(cls, start_date=None, end_date=None):
        """
        Orquestador de Jefatura: delega en _get_admin_dashboard_metrics_sql_impl
        y aplica la red de seguridad transaccional de nivel superior.

        Los 5 sub-bloques internos ya absorben sus propios fallos parciales
        (que un producto falle no debe tumbar todo el panel), pero cualquier
        error que escape de esa capa debe abortar la transacción y propagarse:
        jamás un diccionario vacío silencioso que el controlador convertiría
        en un HTTP 200 falso y la caché congelaría 10 minutos.
        """
        try:
            # SET LOCAL work_mem: EXPLAIN ANALYZE mostró que el bloque de Incumplimiento/
            # Backorder agrupa hasta ~35k grupos posibles sobre 100k+ filas de db_ventas y
            # el HashAggregate hacía spill a disco (33 batches medidos). Se sube solo para
            # esta transacción — SET LOCAL nunca persiste más allá del commit/rollback, no
            # es un cambio de configuración global de la base de datos.
            db.session.execute(text("SET LOCAL work_mem = '64MB'"))
            return cls._get_admin_dashboard_metrics_sql_impl(start_date, end_date)
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_admin_dashboard_metrics_sql] Error crítico en el orquestador: {e}")
            raise
        finally:
            # Garantiza que la conexión vuelva limpia al pool: todo el bloque es de
            # solo lectura, así que un rollback aquí es seguro y además resetea el
            # SET LOCAL antes de que la conexión se reutilice para otra petición.
            rollback_seguro()

    @classmethod
    def _get_admin_dashboard_metrics_sql_impl(cls, start_date=None, end_date=None):
        """Encapsula todas las métricas de Jefatura (SQL-Native) coincidiendo con frontend."""
        params = {'start': start_date, 'end': end_date}
        filt = " WHERE CAST(fecha AS DATE) BETWEEN :start AND :end" if start_date and end_date else " WHERE 1=1"
        con_filtro_fecha = bool(start_date and end_date)

        def _sql_cast_num(col):
            # Columna ya NUMERIC nativo: COALESCE directo evita el bug de perder
            # el signo '-' de devoluciones/NC que tenia el cast via texto+regex.
            return f"COALESCE({col}, 0)"

        # 1-2. Top y Peores Productos.
        # Sin filtro de fecha (caso por defecto): se lee de mv_dashboard_ventas_analitica,
        # que ya viene agregada por (cliente, producto) — ver
        # backend/sql/create_mv_dashboard_ventas_analitica.sql. Con filtro de fecha: la
        # vista no tiene granularidad diaria, así que se cae al camino en vivo sobre
        # db_ventas (single-scan vía CTE materializada) para no romper el filtro.
        top_d = []
        peor_d = []
        try:
            col_excl = 'producto' if not con_filtro_fecha else 'productos'
            excl_top, excl_top_params = CatalogExclusionConfig.get_sql_exclusion_clause(col_excl, mode='top')
            excl_peor, excl_peor_params = CatalogExclusionConfig.get_sql_exclusion_clause(col_excl, mode='menos_vendidos')

            if not con_filtro_fecha:
                sql_top_peor = f"""
                    SELECT 'top' as bucket, producto, total_dinero, total_unidades FROM (
                        SELECT producto,
                               SUM(ventas_dinero) as total_dinero,
                               SUM(ventas_qty) as total_unidades
                        FROM mv_dashboard_ventas_analitica
                        WHERE 1=1 {excl_top}
                        GROUP BY producto
                        ORDER BY total_dinero DESC
                        LIMIT 20
                    ) t
                    UNION ALL
                    SELECT 'peor' as bucket, producto, total_dinero, total_unidades FROM (
                        SELECT producto,
                               SUM(ventas_dinero) as total_dinero,
                               SUM(ventas_qty) as total_unidades
                        FROM mv_dashboard_ventas_analitica
                        WHERE 1=1 {excl_peor}
                        GROUP BY producto
                        ORDER BY total_dinero ASC
                        LIMIT 20
                    ) p
                """
                rows_top_peor = db.session.execute(
                    text(sql_top_peor), {**excl_top_params, **excl_peor_params}
                ).mappings().all()
            else:
                sql_top_peor = f"""
                    WITH base_ventas AS MATERIALIZED (
                        SELECT productos, clasificacion,
                               {_sql_cast_num('total_ingresos')} as total_ingresos,
                               {_sql_cast_num('cantidad')} as cantidad
                        FROM db_ventas
                        {filt}
                    )
                    SELECT 'top' as bucket, productos as producto, total_dinero, total_unidades FROM (
                        SELECT productos,
                               SUM(total_ingresos) as total_dinero,
                               SUM(cantidad) as total_unidades
                        FROM base_ventas
                        WHERE clasificacion ILIKE '%venta%' {excl_top}
                        GROUP BY productos
                        ORDER BY total_dinero DESC
                        LIMIT 20
                    ) t
                    UNION ALL
                    SELECT 'peor' as bucket, productos as producto, total_dinero, total_unidades FROM (
                        SELECT productos,
                               SUM(total_ingresos) as total_dinero,
                               SUM(cantidad) as total_unidades
                        FROM base_ventas
                        WHERE clasificacion ILIKE '%venta%' {excl_peor}
                        GROUP BY productos
                        ORDER BY total_dinero ASC
                        LIMIT 20
                    ) p
                """
                rows_top_peor = db.session.execute(
                    text(sql_top_peor), {**params, **excl_top_params, **excl_peor_params}
                ).mappings().all()

            top_d = [r for r in rows_top_peor if r['bucket'] == 'top']
            peor_d = [r for r in rows_top_peor if r['bucket'] == 'peor']
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_admin_dashboard_metrics_sql - top/peor productos] {e}")

        # 3. Incumplimiento (Backorder).
        # Sin filtro de fecha: lectura directa de la vista materializada (ya trae el
        # JOIN de alias y el NOT ILIKE '%FRIPARTS%' resueltos). Con filtro de fecha:
        # camino en vivo original (JOIN equality-only + fallback defensivo).
        back_rows = []
        if not con_filtro_fecha:
            try:
                sql_inc_mv = f"""
                    WITH {_SQL_CTE_ULTIMOS_DESPACHOS}
                    SELECT v.nombres, v.producto, '' as ref_final,
                           v.pedidos_qty as p_qty, v.ventas_qty as v_qty,
                           (v.pedidos_qty - v.ventas_qty) as diff_qty,
                           (v.pedidos_qty - v.ventas_qty) * v.avg_price as diff_money,
                           ud.ultima_fecha_despacho
                    FROM mv_dashboard_ventas_analitica v
                    LEFT JOIN ultimos_despachos ud
                        ON ud.ref_despacho = {_sql_ref_desde_producto('v.producto')}
                    WHERE (v.pedidos_qty - v.ventas_qty) > 0
                    ORDER BY diff_money DESC
                """
                # Sin LIMIT: back_rows debe traer el universo completo de backorder para
                # que los totales/consolidado por cliente (mas abajo) sean el numero real,
                # no un subconteo. El recorte a "top N" para mostrar en pantalla se hace
                # en Python sobre backorder_list, despues de calcular los totales.
                back_rows = db.session.execute(text(sql_inc_mv)).fetchall()
            except Exception as e:
                rollback_seguro()
                logger.error(f"[DashboardRepository.get_admin_dashboard_metrics_sql - backorder MV]: {e}")
        else:
            # Regla de negocio ya vigente en DashboardService.normalizar_cliente_alias, portada
            # a SQL: cubre el caso 'DISTRIBUJES Y CAUCHOS FC SAS' cuando aún no existe la fila
            # equivalente en db_cliente_equivalencias (el JOIN por sí solo no lo resuelve).
            # Coincidencia flexible por patrón (solo 'DISTRIBUJES') para no depender de que
            # 'FC' o 'CAUCHOS' estén presentes en toda variante de escritura del cliente.
            # Se resuelve DENTRO de la CTE 'totals', antes del SUM y del GROUP BY, para que
            # pedidos y ventas de todas las variantes se agreguen bajo la misma entidad.
            _case_alias_hardcoded = (
                "CASE WHEN UPPER(TRIM(b.nombres)) ILIKE '%DISTRIBUJES%' "
                "THEN 'FELIPE DUARTE MORENO' ELSE COALESCE(e.nombre_canonical, b.nombres) END"
            )
            try:
                sql_inc = f"""
                    WITH {_SQL_CTE_ULTIMOS_DESPACHOS},
                    totals AS (
                        SELECT
                            {_case_alias_hardcoded} as nombres,
                            b.productos as producto,
                            SUM(CASE WHEN b.clasificacion ILIKE '%pedido%' THEN {_sql_cast_num('b.cantidad')} ELSE 0 END) as p_qty,
                            SUM(CASE WHEN b.clasificacion ILIKE '%venta%' THEN {_sql_cast_num('b.cantidad')} ELSE 0 END) as v_qty,
                            MAX({_sql_cast_num('b.precio_promedio')}) as avg_price
                        FROM db_ventas b
                        LEFT JOIN db_cliente_equivalencias e
                            ON UPPER(TRIM(b.nombres)) = UPPER(TRIM(e.alias))
                        {filt.replace('CAST(fecha AS DATE)', 'CAST(b.fecha AS DATE)')}
                        AND UPPER(TRIM(b.nombres)) NOT ILIKE '%FRIPARTS%'
                        GROUP BY 1, b.productos
                    )
                    SELECT
                        t.nombres,
                        t.producto,
                        '' as ref_final,
                        COALESCE(t.p_qty, 0) as p_qty,
                        COALESCE(t.v_qty, 0) as v_qty,
                        (COALESCE(t.p_qty, 0) - COALESCE(t.v_qty, 0)) as diff_qty,
                        COALESCE((COALESCE(t.p_qty, 0) - COALESCE(t.v_qty, 0)) * COALESCE(t.avg_price, 0), 0) as diff_money,
                        ud.ultima_fecha_despacho
                    FROM totals t
                    LEFT JOIN ultimos_despachos ud
                        ON ud.ref_despacho = {_sql_ref_desde_producto('t.producto')}
                    WHERE (COALESCE(t.p_qty, 0) - COALESCE(t.v_qty, 0)) > 0
                    ORDER BY diff_money DESC
                """
                back_rows = db.session.execute(text(sql_inc), params).fetchall()
            except Exception as e_join:
                logger.warning(f"[DashboardRepository.get_admin_dashboard_metrics_sql - backorder JOIN fallback]: {e_join}")
                rollback_seguro()
                try:
                    sql_inc_fallback = f"""
                        WITH {_SQL_CTE_ULTIMOS_DESPACHOS},
                        totals AS (
                            SELECT
                                CASE WHEN UPPER(TRIM(nombres)) ILIKE '%DISTRIBUJES%'
                                     THEN 'FELIPE DUARTE MORENO' ELSE nombres END as nombres,
                                productos as producto,
                                SUM(CASE WHEN clasificacion ILIKE '%pedido%' THEN {_sql_cast_num('cantidad')} ELSE 0 END) as p_qty,
                                SUM(CASE WHEN clasificacion ILIKE '%venta%' THEN {_sql_cast_num('cantidad')} ELSE 0 END) as v_qty,
                                MAX({_sql_cast_num('precio_promedio')}) as avg_price
                            FROM db_ventas
                            {filt}
                            AND UPPER(TRIM(nombres)) NOT ILIKE '%FRIPARTS%'
                            GROUP BY 1, 2
                        )
                        SELECT
                            t.nombres,
                            t.producto,
                            '' as ref_final,
                            COALESCE(t.p_qty, 0) as p_qty,
                            COALESCE(t.v_qty, 0) as v_qty,
                            (COALESCE(t.p_qty, 0) - COALESCE(t.v_qty, 0)) as diff_qty,
                            COALESCE((COALESCE(t.p_qty, 0) - COALESCE(t.v_qty, 0)) * COALESCE(t.avg_price, 0), 0) as diff_money,
                            ud.ultima_fecha_despacho
                        FROM totals t
                        LEFT JOIN ultimos_despachos ud
                            ON ud.ref_despacho = {_sql_ref_desde_producto('t.producto')}
                        WHERE (COALESCE(t.p_qty, 0) - COALESCE(t.v_qty, 0)) > 0
                        ORDER BY diff_money DESC
                    """
                    back_rows = db.session.execute(text(sql_inc_fallback), params).fetchall()
                except Exception as e_fb:
                    rollback_seguro()
                    logger.error(f"[DashboardRepository.get_admin_dashboard_metrics_sql - backorder fallback error]: {e_fb}")

        # Mapeo de resultados de Backorder. back_rows trae el universo COMPLETO
        # (ya sin LIMIT en SQL) para que los totales de más abajo sean el número
        # real; inc_unidades/inc_dinero solo alimentan un top-N por dinero en el
        # frontend, así que se recortan después junto con backorder_list para no
        # inflar el payload de /api/admin/dashboard con miles de filas que nadie
        # renderiza fila por fila.
        inc_unidades = []
        inc_dinero = []
        backorder_list = []

        for r in back_rows:
            cli = str(r[0] or 'Sin Cliente').strip()
            prod = str(r[1] or 'Sin Producto').strip()
            ref_fin = str(r[2] or '').strip()
            p_qty = _num(r[3])
            v_qty = _num(r[4])
            diff_q = _num(r[5])
            diff_m = _num(r[6])

            inc_unidades.append({"cliente": cli, "producto": prod, "unidades_fallidas": diff_q})
            inc_dinero.append({"cliente": cli, "producto": prod, "dinero_perdido": diff_m})
            backorder_list.append({
                "producto": prod,
                "cliente": cli,
                "clean_ref": ref_fin,
                "pedidos_qty": p_qty,
                "ventas_qty": v_qty,
                "pendiente_qty": diff_q,
                "pendiente_money": diff_m,
                # Crudo desde la CTE; el formateo a string lo hace DashboardService.
                "ultima_fecha_despacho": r[7]
            })

        # 4. Scrap para el resumen
        # get_perdida_economica_scrap() devuelve un dict {"total_perdida", "ranking_productos"};
        # se extrae el escalar aquí para no filtrar el dict completo como "resumen_dinero".
        scrap_perdida_dinero = 0
        try:
            scrap_resumen = cls.get_perdida_economica_scrap(start_date, end_date)
            scrap_perdida_dinero = scrap_resumen.get('total_perdida', 0)
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_admin_dashboard_metrics_sql - scrap]: {e}")

        # 5. Rendimiento mensual
        mensual_list = []
        try:
            mensual_list = cls.get_rendimiento_mensual_sql(start_date, end_date)
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_admin_dashboard_metrics_sql - mensual]: {e}")

        # Consolidado por cliente y resumen global: SIEMPRE sobre backorder_list
        # completo (sin el LIMIT que traía back_rows antes). BUGFIX: con el LIMIT 50
        # aplicado en SQL, tanto el consolidado por cliente como resumen_unidades
        # solo sumaban las 50 combinaciones (cliente, producto) con mayor diff_money
        # a nivel GLOBAL -- un cliente con muchos pendientes pequeños podía no
        # aparecer, o aparecer con un total parcial, y la cifra jamás reconciliaba
        # con "Pedidos - Ventas" del panel mensual.
        from collections import defaultdict
        consolidado_map = defaultdict(lambda: {"unidades_fallidas": 0.0, "dinero_perdido": 0.0})
        for item in backorder_list:
            acc = consolidado_map[item["cliente"]]
            acc["unidades_fallidas"] += item["pendiente_qty"]
            acc["dinero_perdido"] += item["pendiente_money"]

        return {
            "mensual": mensual_list,
            "top_productos": [{"producto": str(r['producto'] or 'Sin Producto').strip(), "ventas_dinero": _num(r['total_dinero']), "ventas_unidades": _num(r['total_unidades'])} for r in top_d],
            "peores_productos": [{"producto": str(r['producto'] or 'Sin Producto').strip(), "ventas_dinero": _num(r['total_dinero']), "ventas_unidades": _num(r['total_unidades'])} for r in peor_d],
            # Top 50 por dinero_perdido, SOLO para el listado en pantalla -- los
            # totales de arriba ya se calcularon sobre el universo completo.
            "backorder": backorder_list[:50],
            "incumplimiento_consolidado": [
                {"cliente": cli, **vals} for cli, vals in sorted(consolidado_map.items())
            ],
            "incumplimiento_dinero": inc_dinero[:50],
            "incumplimiento_unidades": inc_unidades[:50],
            "resumen_unidades": sum(item["pendiente_qty"] for item in backorder_list),
            "resumen_dinero": scrap_perdida_dinero
        }

    @classmethod
    def get_tendencia_produccion_sql(cls, desde=None, hasta=None):
        """Retorna tendencia diaria de producción (Inyección y Pulido)."""
        try:
            params = {'desde': desde, 'hasta': hasta}
            filt_iny = " WHERE CAST(fecha_inicia AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"
            filt_pul = " WHERE CAST(fecha AS DATE) BETWEEN :desde AND :hasta" if desde and hasta else " WHERE 1=1"

            sql = f"""
                WITH iny AS (
                    SELECT DATE(fecha_inicia) as d, SUM(COALESCE(cantidad_real, 0)) as qty
                    FROM db_inyeccion {filt_iny} GROUP BY 1
                ),
                pul AS (
                    SELECT DATE(fecha) as d, SUM(COALESCE(cantidad_real, 0)) as qty
                    FROM db_pulido {filt_pul} GROUP BY 1
                ),
                fechas AS (
                    SELECT d FROM iny UNION SELECT d FROM pul
                )
                SELECT f.d, COALESCE(iny.qty, 0), COALESCE(pul.qty, 0)
                FROM fechas f
                LEFT JOIN iny ON f.d = iny.d
                LEFT JOIN pul ON f.d = pul.d
                ORDER BY f.d ASC
            """
            rows = db.session.execute(text(sql), params).fetchall()
            return [{"fecha": str(r[0]), "iny": int(r[1]), "pul": int(r[2])} for r in rows]
        except Exception as e:
            rollback_seguro()
            logger.error(f"[DashboardRepository.get_tendencia_produccion_sql] {e}")
            return []

    @classmethod
    def get_monthly_performance_comparison(cls, start_date: str = None, end_date: str = None) -> dict:
        """
        Fachada semántica para el endpoint /api/dashboard/performance/monthly.
        Retorna comparativa mensual Ventas vs Pedidos (Año Actual vs Anterior)
        con metadatos de año incluidos, garantizando 0 en meses sin datos.
        """
        # Determinar años desde los parámetros (mismo criterio que get_rendimiento_mensual_sql)
        year_actual = datetime.now().year
        year_prev = year_actual - 1
        if start_date:
            try:
                year_actual = datetime.strptime(start_date, '%Y-%m-%d').year
                year_prev = year_actual - 1
            except ValueError:
                pass

        mensual = cls.get_rendimiento_mensual_sql(start_date, end_date)

        return {
            "year_actual": year_actual,
            "year_prev": year_prev,
            "mensual": mensual,        # Lista de 12 meses, 0s en meses sin datos
        }

    @classmethod
    def get_produccion_por_maquina(cls):
        """
        Producción de Inyección agregada por máquina (total, días trabajados,
        promedio diario, estado activa/inactiva). Movido desde backend/app.py
        (Dashboard Avanzado).
        """
        from backend.models.sql_models import ProduccionInyeccion, Maquina

        maquinas_db = {m.nombre: m for m in Maquina.query.all()}

        query = db.session.query(
            ProduccionInyeccion.maquina,
            db.func.sum(ProduccionInyeccion.cantidad_real).label('total'),
            db.func.count(db.func.distinct(db.func.date(ProduccionInyeccion.fecha_inicia))).label('dias')
        ).group_by(ProduccionInyeccion.maquina).all()

        resultado = {}
        for row in query:
            nom_maq = row.maquina or 'Sin Maquina'
            total_qty = float(row.total or 0)
            dias_op = int(row.dias or 1)

            resultado[nom_maq] = {
                'produccion_total': total_qty,
                'dias_trabajados': dias_op,
                'produccion_promedio': round(total_qty / dias_op if dias_op > 0 else 0),
                'estado': 'Activa' if nom_maq in maquinas_db and maquinas_db[nom_maq].activa else 'Inactiva'
            }

        maquinas_ordenadas = sorted(
            resultado.items(),
            key=lambda x: x[1]['produccion_total'],
            reverse=True
        )

        return {
            'maquinas': dict(maquinas_ordenadas),
            'total_maquinas': len(resultado)
        }
