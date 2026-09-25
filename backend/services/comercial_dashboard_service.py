# -*- coding: utf-8 -*-
"""
Dashboard comercial (ventas de World Office) con filtro por rango de fechas.

Nació para Frimetals: el dashboard de FriParts está moldeado para
Inyección/Pulido y sus vistas materializadas (mv_*) excluyen a FRIPARTS y no
tienen granularidad diaria ni vendedor, así que este servicio consulta
db_ventas directamente. Con el volumen de una instancia como Frimetals
(~10k filas) el costo es despreciable; si una instancia crece a 100k+ filas
habrá que revisar el plan de ejecución antes de exponerlo ahí.

Criterio contable IDÉNTICO al de ComercialHistoricoService (solo
clasificacion='VENTA', notas crédito restando) para que las cifras cuadren
entre la Analítica Comercial y este dashboard. FRIPARTS SAS NO se excluye:
solo se marca como venta entre empresas (Empresa.CLIENTES_ENTRE_EMPRESAS).
"""
import logging
from datetime import date, timedelta

from sqlalchemy import text

from backend.config.settings import Empresa
from backend.core.sql_database import db, rollback_seguro
from backend.services.comercial_service import ComercialHistoricoService, FILTRO_SOLO_VENTA

logger = logging.getLogger(__name__)

TOP_CLIENTES = 10
TOP_ZONAS = 10
TOP_VENDEDORES = 15
TOP_PRODUCTOS = 15
TOP_CLIENTES_NUEVOS = 8
# Tope defensivo del ranking completo de clientes (de él salen el top 10, la
# concentración y la parte "entre empresas"). Frimetals tiene ~140.
MAX_CLIENTES_AGREGADOS = 5000
# Un cliente cuenta como "recuperado" si compró en el periodo, ya había
# comprado antes, pero no compró nada en los 365 días previos al periodo.
DIAS_INACTIVIDAD_RECUPERADO = 365

# Cada columna de orden es una constante interna (nunca viene del request):
# permite reutilizar la misma consulta de productos para "más vendido en $" y
# "más vendido en unidades" sin interpolar texto del usuario en el SQL.
_ORDEN_PRODUCTOS = {
    'ventas': 'total_ventas DESC, total_unidades DESC',
    'unidades': 'total_unidades DESC, total_ventas DESC',
}


def _num(valor) -> float:
    return float(valor or 0)


def _variacion_pct(actual: float, previo: float):
    """% de cambio contra el periodo previo; None si no hay base para comparar."""
    if not previo:
        return None
    return round((actual - previo) / previo * 100, 1)


def _cumplimiento_pct(ventas: float, pedidos: float):
    """Ventas / pedidos en %; sin pedidos no hay contra qué medir: None (no 0 ni infinito)."""
    if not pedidos:
        return None
    return round(ventas / pedidos * 100, 1)


def _a_fecha(valor):
    """PostgreSQL devuelve date; se tolera también un string ISO."""
    if valor is None:
        return None
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def _mismo_dia_anio_previo(d: date) -> date:
    """d menos un año; el 29-feb cae al 28-feb del año no bisiesto."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _meses_del_rango(desde: date, hasta: date) -> list:
    """['YYYY-MM', ...] de cada mes tocado por [desde, hasta], en orden."""
    meses = []
    anio, mes = desde.year, desde.month
    while (anio, mes) <= (hasta.year, hasta.month):
        meses.append(f"{anio:04d}-{mes:02d}")
        mes += 1
        if mes > 12:
            anio, mes = anio + 1, 1
    return meses


def _mes_anio_previo(ym: str) -> str:
    anio, mes = ym.split('-')
    return f"{int(anio) - 1:04d}-{mes}"


def _es_entre_empresas(nombre: str) -> bool:
    nombre_up = str(nombre or '').upper()
    return any(fragmento in nombre_up for fragmento in Empresa.CLIENTES_ENTRE_EMPRESAS)


class ComercialDashboardService:

    @staticmethod
    def resolver_rango(desde, hasta):
        """Default: 1-ene del año de 'hasta' hasta hoy. Nunca devuelve None."""
        hasta = hasta or date.today()
        desde = desde or date(hasta.year, 1, 1)
        return desde, hasta

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    @staticmethod
    def _kpis(params: dict, ventas_expr: str, cantidad_expr: str) -> dict:
        fila = db.session.execute(text(f"""
            SELECT
                COALESCE(SUM({ventas_expr}), 0)                                   AS ventas,
                COALESCE(SUM({cantidad_expr}), 0)                                 AS unidades,
                COUNT(v.id)                                                       AS transacciones,
                COUNT(DISTINCT NULLIF(TRIM(COALESCE(v.documento, '')), ''))       AS documentos,
                COUNT(DISTINCT NULLIF(UPPER(TRIM(COALESCE(v.nombres, ''))), ''))  AS clientes
            FROM db_ventas v
            WHERE v.fecha >= :desde AND v.fecha <= :hasta
              AND {FILTRO_SOLO_VENTA}
        """), params).mappings().first()
        ventas = _num(fila['ventas'])
        documentos = int(fila['documentos'] or 0)
        return {
            'ventas': round(ventas, 2),
            'unidades': round(_num(fila['unidades']), 2),
            'transacciones': int(fila['transacciones'] or 0),
            'documentos': documentos,
            'clientes': int(fila['clientes'] or 0),
            'ticket_promedio': round(ventas / documentos, 2) if documentos else 0.0,
        }

    @staticmethod
    def _mensual(params: dict, ventas_expr: str) -> dict:
        filas = db.session.execute(text(f"""
            SELECT TO_CHAR(v.fecha, 'YYYY-MM') AS ym,
                   COALESCE(SUM({ventas_expr}), 0) AS ventas
            FROM db_ventas v
            WHERE v.fecha >= :desde AND v.fecha <= :hasta
              AND {FILTRO_SOLO_VENTA}
            GROUP BY 1
        """), params).mappings().all()
        return {f['ym']: _num(f['ventas']) for f in filas}

    @staticmethod
    def _pedidos_vs_ventas(params: dict, ventas_expr: str) -> dict:
        """Por mes: (facturado, pedidos de WO). Facturado usa el mismo criterio que el resto."""
        filas = db.session.execute(text(f"""
            SELECT TO_CHAR(v.fecha, 'YYYY-MM') AS ym,
                   COALESCE(SUM(CASE WHEN {FILTRO_SOLO_VENTA} THEN {ventas_expr} ELSE 0 END), 0) AS ventas,
                   COALESCE(SUM(CASE WHEN UPPER(TRIM(COALESCE(v.clasificacion, ''))) LIKE '%PEDIDO%'
                                     THEN COALESCE(v.total_ingresos, 0) ELSE 0 END), 0) AS pedidos
            FROM db_ventas v
            WHERE v.fecha >= :desde AND v.fecha <= :hasta
            GROUP BY 1
        """), params).mappings().all()
        return {f['ym']: (_num(f['ventas']), _num(f['pedidos'])) for f in filas}

    @staticmethod
    def _agrupado(params: dict, ventas_expr: str, cantidad_expr: str,
                  expr_grupo: str, limite: int, extra_select: str = '') -> list:
        """
        Ranking genérico (cliente / zona / vendedor). `expr_grupo` y
        `extra_select` son constantes internas de este módulo, nunca texto
        del request.
        """
        filas = db.session.execute(text(f"""
            SELECT {expr_grupo} AS nombre,
                   {extra_select}
                   COALESCE(SUM({ventas_expr}), 0)   AS total_ventas,
                   COALESCE(SUM({cantidad_expr}), 0) AS total_unidades,
                   COUNT(v.id)                       AS transacciones
            FROM db_ventas v
            WHERE v.fecha >= :desde AND v.fecha <= :hasta
              AND {FILTRO_SOLO_VENTA}
            GROUP BY 1
            ORDER BY total_ventas DESC
            LIMIT :limite
        """), {**params, 'limite': limite}).mappings().all()
        return [dict(f) for f in filas]

    @staticmethod
    def _productos(params: dict, ventas_expr: str, cantidad_expr: str, orden: str) -> list:
        filas = db.session.execute(text(f"""
            SELECT COALESCE(NULLIF(TRIM(v.productos), ''), 'SIN CÓDIGO') AS codigo,
                   MAX(NULLIF(TRIM(v.descripcion_producto), ''))         AS descripcion,
                   COALESCE(SUM({ventas_expr}), 0)   AS total_ventas,
                   COALESCE(SUM({cantidad_expr}), 0) AS total_unidades
            FROM db_ventas v
            WHERE v.fecha >= :desde AND v.fecha <= :hasta
              AND {FILTRO_SOLO_VENTA}
            GROUP BY 1
            ORDER BY {_ORDEN_PRODUCTOS[orden]}
            LIMIT :limite
        """), {**params, 'limite': TOP_PRODUCTOS}).mappings().all()
        return [{
            'codigo': f['codigo'],
            'descripcion': f['descripcion'],
            'total_ventas': round(_num(f['total_ventas']), 2),
            'total_unidades': round(_num(f['total_unidades']), 2),
        } for f in filas]

    @staticmethod
    def _historia_clientes(desde: date, hasta: date, ventas_expr: str) -> list:
        """
        Por cliente con ventas en el periodo: su primera compra en TODA la
        historia de db_ventas y cuántas compras hizo en los 365 días previos
        al periodo. De ahí salen "nuevos" y "recuperados".
        """
        desde_ref = desde - timedelta(days=DIAS_INACTIVIDAD_RECUPERADO)
        filas = db.session.execute(text(f"""
            SELECT MAX(TRIM(v.nombres)) AS nombre,
                   MIN(v.fecha)         AS primera_compra,
                   COALESCE(SUM(CASE WHEN v.fecha >= :desde AND v.fecha <= :hasta
                                     THEN {ventas_expr} ELSE 0 END), 0) AS ventas_periodo,
                   COALESCE(SUM(CASE WHEN v.fecha >= :desde_ref AND v.fecha < :desde
                                     THEN 1 ELSE 0 END), 0)             AS compras_previas
            FROM db_ventas v
            WHERE {FILTRO_SOLO_VENTA}
              AND NULLIF(TRIM(COALESCE(v.nombres, '')), '') IS NOT NULL
              AND v.fecha <= :hasta
            GROUP BY UPPER(TRIM(v.nombres))
            HAVING COALESCE(SUM(CASE WHEN v.fecha >= :desde AND v.fecha <= :hasta
                                     THEN {ventas_expr} ELSE 0 END), 0) <> 0
        """), {'desde': desde, 'hasta': hasta, 'desde_ref': desde_ref}).mappings().all()
        return [dict(f) for f in filas]

    @staticmethod
    def _primera_venta_historica():
        return db.session.execute(text(f"""
            SELECT MIN(v.fecha) FROM db_ventas v WHERE {FILTRO_SOLO_VENTA}
        """)).scalar()

    @staticmethod
    def _cartera_total() -> float:
        return _num(db.session.execute(text(
            "SELECT COALESCE(SUM(saldo_documento), 0) FROM cartera_wo WHERE saldo_documento > 0"
        )).scalar())

    @staticmethod
    def _pedidos_en_planta(params: dict) -> list:
        """
        Pedidos creados en la app (db_pedidos) por estado, dentro del rango.
        Un pedido tiene una fila por línea: se cuenta por id_pedido distinto.
        Si sus líneas estuvieran en estados distintos aparecería en cada uno.
        """
        filas = db.session.execute(text("""
            SELECT COALESCE(NULLIF(TRIM(estado), ''), 'SIN ESTADO') AS estado,
                   COUNT(DISTINCT id_pedido)                        AS pedidos,
                   COALESCE(SUM(total), 0)                          AS valor
            FROM db_pedidos
            WHERE fecha >= :desde AND fecha <= :hasta
            GROUP BY 1
            ORDER BY pedidos DESC, valor DESC
        """), params).mappings().all()
        return [{'estado': f['estado'], 'pedidos': int(f['pedidos'] or 0),
                 'valor': round(_num(f['valor']), 2)} for f in filas]

    # ------------------------------------------------------------------
    # Armado de bloques
    # ------------------------------------------------------------------
    @staticmethod
    def _con_participacion(filas: list, total: float, entre_empresas: bool = False) -> list:
        salida = []
        for f in filas:
            ventas = round(_num(f['total_ventas']), 2)
            item = {
                'nombre': f['nombre'],
                'total_ventas': ventas,
                'total_unidades': round(_num(f['total_unidades']), 2),
                'transacciones': int(f['transacciones'] or 0),
                'participacion_pct': round(ventas / total * 100, 1) if total else 0.0,
            }
            if 'clientes' in f:
                item['clientes'] = int(f['clientes'] or 0)
            if entre_empresas:
                item['entre_empresas'] = _es_entre_empresas(f['nombre'])
            salida.append(item)
        return salida

    @staticmethod
    def _tabla_mensual(desde: date, hasta: date, ventas_mes: dict, previo_mes: dict, ped_vs_ven: dict) -> dict:
        """
        Pedidos vs facturado mes a mes con variación contra el año previo y
        acumulados. Diferencia = pedidos - facturado (positivo: pedido aún sin
        facturar; negativo: se facturó más de lo pedido ese mes).
        """
        filas = []
        acum = acum_previo = 0.0
        tot_ped = tot_fac = tot_prev = 0.0
        for ym in _meses_del_rango(desde, hasta):
            facturado, pedidos = ped_vs_ven.get(ym, (0.0, 0.0))
            previo = previo_mes.get(_mes_anio_previo(ym), 0.0)
            acum += facturado
            acum_previo += previo
            tot_ped += pedidos
            tot_fac += facturado
            tot_prev += previo
            filas.append({
                'mes': ym,
                'pedidos': round(pedidos, 2),
                'facturado': round(facturado, 2),
                'diferencia': round(pedidos - facturado, 2),
                'cumplimiento_pct': _cumplimiento_pct(facturado, pedidos),
                'facturado_previo': round(previo, 2),
                'variacion_pct': _variacion_pct(facturado, previo),
                'acumulado': round(acum, 2),
                'acumulado_previo': round(acum_previo, 2),
            })
        return {
            'filas': filas,
            'total': {
                'pedidos': round(tot_ped, 2),
                'facturado': round(tot_fac, 2),
                'diferencia': round(tot_ped - tot_fac, 2),
                'cumplimiento_pct': _cumplimiento_pct(tot_fac, tot_ped),
                'facturado_previo': round(tot_prev, 2),
                'variacion_pct': _variacion_pct(tot_fac, tot_prev),
            },
        }

    @staticmethod
    def _concentracion(clientes_todos: list, total: float) -> dict:
        """
        Cuánto pesan los 5 mayores clientes y la parte que viene de otras
        empresas del grupo. Se calcula sobre TODOS los clientes del periodo,
        no sobre el top 10 mostrado.
        """
        if not total:
            return {'top5_pct': None, 'entre_empresas_pct': None, 'entre_empresas_clientes': []}
        top5 = sum(_num(c['total_ventas']) for c in clientes_todos[:5])
        grupo = [c for c in clientes_todos if _es_entre_empresas(c['nombre'])]
        return {
            'top5_pct': round(top5 / total * 100, 1),
            'entre_empresas_pct': round(sum(_num(c['total_ventas']) for c in grupo) / total * 100, 1),
            'entre_empresas_clientes': [c['nombre'] for c in grupo[:3]],
        }

    @staticmethod
    def _clientes_nuevos(desde: date, hasta: date, historia: list, primera_venta) -> dict:
        """
        NUEVO: su primera compra en toda la historia cae dentro del periodo.
        RECUPERADO: ya había comprado, pero ninguna compra en los 365 días
        previos al periodo. Si el periodo arranca en o antes de la primera
        venta registrada no hay historia contra qué comparar (todos los
        clientes parecerían nuevos): disponible=False, no un número falso.
        """
        primera_venta = _a_fecha(primera_venta)
        if primera_venta is None or desde <= primera_venta:
            return {'disponible': False, 'nuevos': None, 'recuperados': None}

        limite_recuperado = desde - timedelta(days=DIAS_INACTIVIDAD_RECUPERADO)
        nuevos, recuperados = [], []
        for h in historia:
            primera = _a_fecha(h['primera_compra'])
            ventas = round(_num(h['ventas_periodo']), 2)
            if primera >= desde:
                nuevos.append({'nombre': h['nombre'], 'total_ventas': ventas})
            elif primera < limite_recuperado and int(h['compras_previas'] or 0) == 0:
                recuperados.append({'nombre': h['nombre'], 'total_ventas': ventas})

        nuevos.sort(key=lambda c: c['total_ventas'], reverse=True)
        recuperados.sort(key=lambda c: c['total_ventas'], reverse=True)
        return {
            'disponible': True,
            'nuevos': {'cantidad': len(nuevos), 'total': round(sum(c['total_ventas'] for c in nuevos), 2),
                       'top': nuevos[:TOP_CLIENTES_NUEVOS]},
            'recuperados': {'cantidad': len(recuperados), 'total': round(sum(c['total_ventas'] for c in recuperados), 2)},
            'dias_inactividad': DIAS_INACTIVIDAD_RECUPERADO,
        }

    @staticmethod
    def _dias_cartera(ventas_periodo: float, desde: date, hasta: date):
        """
        Cartera total (foto de hoy) / venta diaria promedio del periodo. Es un
        indicador aproximado: la cartera es de hoy y la venta es del rango
        elegido. La cartera es complementaria: si su tabla falla, el resto del
        dashboard sigue y este bloque queda en None.
        """
        try:
            cartera = ComercialDashboardService._cartera_total()
        except Exception as e:
            rollback_seguro()
            logger.warning(f"[COMERCIAL_DASHBOARD] No se pudo leer cartera_wo para días de cartera: {e}")
            return None
        dias_periodo = (hasta - desde).days + 1
        venta_diaria = ventas_periodo / dias_periodo if dias_periodo > 0 else 0
        if venta_diaria <= 0:
            return {'dias': None, 'cartera': round(cartera, 2), 'venta_diaria': 0.0}
        return {'dias': round(cartera / venta_diaria), 'cartera': round(cartera, 2),
                'venta_diaria': round(venta_diaria, 2)}

    @staticmethod
    def obtener_dashboard(desde=None, hasta=None) -> dict:
        """
        Todo el dashboard de una sola vez para el rango [desde, hasta] y el
        mismo rango un año antes. Solo lectura, pero se protege igual con
        rollback: una consulta fallida deja la transacción de PostgreSQL en
        estado 'aborted' y contaminaría la sesión del pool.
        """
        S = ComercialDashboardService
        desde, hasta = S.resolver_rango(desde, hasta)
        desde_previo, hasta_previo = _mismo_dia_anio_previo(desde), _mismo_dia_anio_previo(hasta)

        ventas_expr = ComercialHistoricoService._expr_ajustado_nc('total_ingresos')
        cantidad_expr = ComercialHistoricoService._expr_ajustado_nc('cantidad')

        p_actual = {'desde': desde, 'hasta': hasta}
        p_previo = {'desde': desde_previo, 'hasta': hasta_previo}

        try:
            kpi_actual = S._kpis(p_actual, ventas_expr, cantidad_expr)
            kpi_previo = S._kpis(p_previo, ventas_expr, cantidad_expr)

            ventas_mes = S._mensual(p_actual, ventas_expr)
            previo_mes = S._mensual(p_previo, ventas_expr)
            ped_vs_ven = S._pedidos_vs_ventas(p_actual, ventas_expr)

            clientes_todos = S._agrupado(
                p_actual, ventas_expr, cantidad_expr,
                "COALESCE(NULLIF(TRIM(v.nombres), ''), 'CLIENTE DESCONOCIDO')", MAX_CLIENTES_AGREGADOS)
            zonas = S._agrupado(
                p_actual, ventas_expr, cantidad_expr,
                "COALESCE(NULLIF(TRIM(v.zona), ''), 'SIN ZONA')", TOP_ZONAS)
            vendedores = S._agrupado(
                p_actual, ventas_expr, cantidad_expr,
                "COALESCE(NULLIF(TRIM(v.vendedor), ''), 'SIN VENDEDOR')", TOP_VENDEDORES,
                extra_select="COUNT(DISTINCT NULLIF(UPPER(TRIM(COALESCE(v.nombres, ''))), '')) AS clientes,")

            productos_ventas = S._productos(p_actual, ventas_expr, cantidad_expr, 'ventas')
            productos_unidades = S._productos(p_actual, ventas_expr, cantidad_expr, 'unidades')

            historia = S._historia_clientes(desde, hasta, ventas_expr)
            primera_venta = S._primera_venta_historica()
        except Exception as e:
            rollback_seguro()
            logger.error(f"[COMERCIAL_DASHBOARD] Error consultando db_ventas: {e}")
            raise

        # Bloques complementarios: si fallan, el dashboard de ventas igual sale.
        try:
            pedidos_planta = S._pedidos_en_planta(p_actual)
        except Exception as e:
            rollback_seguro()
            logger.warning(f"[COMERCIAL_DASHBOARD] No se pudo leer db_pedidos: {e}")
            pedidos_planta = None

        total = kpi_actual['ventas']
        clientes_top = S._con_participacion(clientes_todos[:TOP_CLIENTES], total, entre_empresas=True)

        return {
            'periodo': {
                'desde': desde.isoformat(), 'hasta': hasta.isoformat(),
                'desde_previo': desde_previo.isoformat(), 'hasta_previo': hasta_previo.isoformat(),
            },
            'kpis': {
                'actual': kpi_actual,
                'previo': kpi_previo,
                'variacion_pct': {
                    'ventas': _variacion_pct(kpi_actual['ventas'], kpi_previo['ventas']),
                    'unidades': _variacion_pct(kpi_actual['unidades'], kpi_previo['unidades']),
                    'ticket_promedio': _variacion_pct(kpi_actual['ticket_promedio'], kpi_previo['ticket_promedio']),
                },
            },
            'mensual': [{
                'mes': ym,
                'ventas': round(ventas_mes.get(ym, 0.0), 2),
                'ventas_previo': round(previo_mes.get(_mes_anio_previo(ym), 0.0), 2),
            } for ym in _meses_del_rango(desde, hasta)],
            'tabla_mensual': S._tabla_mensual(desde, hasta, ventas_mes, previo_mes, ped_vs_ven),
            'dias_cartera': S._dias_cartera(total, desde, hasta),
            'concentracion': S._concentracion(clientes_todos, total),
            'clientes_nuevos': S._clientes_nuevos(desde, hasta, historia, primera_venta),
            'pedidos_planta': pedidos_planta,
            'clientes': clientes_top,
            'zonas': S._con_participacion(zonas, total),
            'vendedores': S._con_participacion(vendedores, total),
            'productos_por_ventas': productos_ventas,
            'productos_por_unidades': productos_unidades,
        }
