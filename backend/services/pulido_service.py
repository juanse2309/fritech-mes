"""
pulido_service.py
================
Capa de servicio exclusiva para analítica de Pulido.
Toda la lógica de negocio (volumen físico, eficiencia, deduplicación, normalización)
reside aquí. Las rutas solo invocan métodos y retornan JSON.
"""
import logging
from datetime import date, datetime, timedelta
from backend.core.sql_database import db
from backend.models.sql_models import ProduccionPulido, AppConfig
from backend.utils.formatters import sql_normalizar_codigo_fr
from backend.utils.time_utils import get_colombia_time
from backend.services.audit_service import TurnoInvalidoException
from sqlalchemy import text

logger = logging.getLogger(__name__)


class FechaPulidoInvalidaException(Exception):
    """
    Bloqueo duro (plan 2026-08-28): Pulido solo puede reportar el mismo día
    en que se hizo el trabajo. Reportar un día distinto (turno olvidado,
    lote guardado hace semanas) requiere que un ADMIN fuerce el guardado
    con un motivo -- ver PulidoService.validar_bloqueo_fecha y
    PulidoOverride, la bitácora que deja el "reporte para restar puntos"
    que pidió la jefa.
    """
    def __init__(self, fecha_reporte, hoy, message=None):
        self.fecha_reporte = fecha_reporte
        self.hoy = hoy
        self.message = message or (
            f"Solo se puede reportar Pulido el mismo día del trabajo. "
            f"Fecha reportada: {fecha_reporte} -- hoy es {hoy}. "
            f"Si el lote es real pero atrasado, un ADMIN debe autorizarlo."
        )
        super().__init__(self.message)


class CantidadExcedeInyectadoException(Exception):
    """
    Bloqueo duro (plan 2026-08-28): la suma acumulada de buenas+PNC
    reportada en Pulido para una OP+referencia no puede superar lo que
    Inyección cerró para esa misma OP+referencia -- regla explícita del
    usuario: "si de inyección salieron 100 bujes, en pulido se deben
    reportar esas 100 así sean 2 dañadas y 98 buenas, pero completar las
    100". Solo aplica a OP reconocidas por el nuevo sistema de
    trazabilidad (ver PulidoService.es_op_reconocida) -- material anterior
    al corte, o sin OP real, se comporta libre como siempre.
    """
    def __init__(self, op, referencia, inyectado, ya_reportado, intento, message=None):
        self.op = op
        self.referencia = referencia
        self.inyectado = inyectado
        self.ya_reportado = ya_reportado
        self.intento = intento
        self.disponible = max(0, inyectado - ya_reportado)
        self.message = message or (
            f"La OP {op} / {referencia} ya tiene {ya_reportado} reportadas en Pulido "
            f"contra {inyectado} inyectadas -- solo quedan {self.disponible} disponibles, "
            f"se intentó reportar {intento}. Si el dato de inyección está mal, un ADMIN "
            f"debe autorizarlo."
        )
        super().__init__(self.message)

# Pulido no tiene turno nocturno: jornada única 07:00-17:00 (10h de span).
# Confirmado por el usuario el 2026-08-03 tras auditoría de horas mal digitadas.
DURACION_MAXIMA_TURNO_HORAS = 10

# TTL del Garbage Collector pasivo de sesiones zombi (ver PulidoService.limpiar_sesiones_zombis).
# Una sesión que lleva más de este tiempo en TRABAJANDO/EN_PROCESO/PAUSADO se asume abandonada
# (tablet apagada, crash de red, turno olvidado) y se autocierra para no bloquear al operario.
PULIDO_SESSION_TTL_HOURS = 14

ESTADOS_SESION_ACTIVA_GC = ['TRABAJANDO', 'EN_PROCESO', 'PAUSADO']


def _num(v, cast=float):
    """Convierte un valor numérico de forma segura."""
    try:
        return cast(v or 0)
    except (TypeError, ValueError):
        return cast(0)


class PulidoService:
    """Analítica completa del módulo de Pulido."""

    # ---------------------------------------------------------------
    # Constante interna: lista normalizada de responsables ignorados
    # ---------------------------------------------------------------
    _IGNORAR = {
        'SISTEMA', 'SIN RESPONSABLE', 'ADMIN', '',
        'NOHEMY', 'LAURA JIMENEZ', 'LAURA JIMÉNEZ',
        'EDIMAR MENDEZ', 'EDIMAR MÉNDEZ', 'EDIMAR',
        'JUAN SEBASTIAN NOVOA CEPEDA', 'JUAN SEBASTIAN NOVOA', 'JUAN SEBASTIÁN NOVOA CEPEDA',
        'JUAN SEBASTIAN', 'JUAN SEBASTIÁN', 'NOVOA'
    }

    # ---------------------------------------------------------------
    # Placeholders que NO identifican a una persona. Deliberadamente
    # separado de _IGNORAR: esa lista excluye operarias REALES de los
    # KPIs, y usarla aquí rechazaría registros legítimos suyos.
    # ---------------------------------------------------------------
    _RESPONSABLES_PLACEHOLDER = {'', 'SISTEMA', 'SIN RESPONSABLE', 'NONE', 'NULL', 'ADMIN'}

    @staticmethod
    def _normalizar_nombre(nombre: str) -> str:
        """Normaliza a UPPER + TRIM para unificar variantes de escritura."""
        return (nombre or '').upper().strip()

    @staticmethod
    def resolver_operaria_responsable(registro) -> str:
        """
        Resuelve la operaria a la que se atribuye una merma de db_pnc_pulido.

        Única fuente válida: `db_pulido.responsable` del turno que produjo la
        merma — la operaria que físicamente procesó las piezas. No se acepta
        NULL ni un placeholder genérico: una merma sin dueño es justamente el
        vacío de trazabilidad que la columna `responsable` vino a cerrar, y
        rellenarla con 'SISTEMA' o el nombre del área lo reintroduce disfrazado.

        :param registro: instancia de ProduccionPulido (o None).
        :raises ValueError: si no hay una persona real que atribuir.
        """
        nombre = str(getattr(registro, 'responsable', '') or '').strip()
        if not nombre or PulidoService._normalizar_nombre(nombre) in PulidoService._RESPONSABLES_PLACEHOLDER:
            raise ValueError(
                "No se puede registrar PNC de pulido sin una operaria responsable "
                "identificada en el turno (db_pulido.responsable)"
            )
        return nombre

    @staticmethod
    def resolver_operario_inyeccion_origen(registro):
        """
        Rastrea el operario de INYECCIÓN que fabricó las piezas que este turno de
        pulido está procesando, para atribuirle la merma de inyección detectada
        durante el pulido (db_pnc_inyeccion.responsable).

        NO se usa `db_trazabilidad_lotes.responsable`: esa columna guarda al
        programador de planta (`ProgramacionInyeccion.responsable_planta`), no a
        quien operó la máquina. Verificado contra datos reales — para el mismo
        lote, trazabilidad dice 'Juan Sebastian Novoa Cepeda' (supervisor) e
        inyección dice 'Oscar Prieto' (operario). La trazabilidad sirve solo como
        puente hacia `id_inyeccion`; el operario real vive en db_inyeccion.

        Estrategias, en orden:
          1. db_pulido.lote -> db_trazabilidad_lotes.id_lote -> id_inyeccion
             -> db_inyeccion.responsable   (flujo MES con lote en vivo)
          2. orden_produccion + código normalizado -> db_inyeccion.responsable,
             tomando el lote más reciente  (flujo directo, sin lote MES)

        La estrategia 2 no es un adorno: hoy `db_pulido.lote` guarda una FECHA
        ('9/4/2026'), no un id_lote, así que la vía 1 no resuelve ninguno de los
        registros históricos y sin el fallback la columna seguiría en NULL.

        :return: nombre del operario de inyección, o None si no es rastreable.
                 Deliberadamente NO inventa un valor: atribuir la merma a la
                 pulidora o a un genérico es peor que dejar el campo vacío.
        """
        from backend.models.sql_models import TrazabilidadLote, ProduccionInyeccion

        codigo = str(getattr(registro, 'codigo', '') or '').strip()
        if not codigo:
            return None

        def _responsable_valido(nombre):
            nombre = str(nombre or '').strip()
            if not nombre or PulidoService._normalizar_nombre(nombre) in PulidoService._RESPONSABLES_PLACEHOLDER:
                return None
            return nombre

        # ── Estrategia 1: puente por lote de trazabilidad ──────────────
        lote = str(getattr(registro, 'lote', '') or '').strip()
        if lote and lote != 'SIN LOTE':
            fila = db.session.execute(
                text(f"""
                    SELECT i.responsable
                    FROM db_trazabilidad_lotes t
                    JOIN db_inyeccion i
                      ON i.id_inyeccion = t.id_inyeccion
                     AND {sql_normalizar_codigo_fr('i.id_codigo')} = {sql_normalizar_codigo_fr('t.id_codigo')}
                    WHERE t.id_lote = :lote
                      AND {sql_normalizar_codigo_fr('t.id_codigo')} = UPPER(TRIM(:codigo))
                      AND i.responsable IS NOT NULL
                    ORDER BY i.fecha_inicia DESC NULLS LAST
                    LIMIT 1
                """),
                {'lote': lote, 'codigo': codigo}
            ).fetchone()
            if fila and _responsable_valido(fila[0]):
                return _responsable_valido(fila[0])

        # ── Estrategia 2: cruce por OP + referencia ────────────────────
        op = str(getattr(registro, 'orden_produccion', '') or '').strip()
        if op and op != 'SIN OP':
            fila = db.session.execute(
                text(f"""
                    SELECT i.responsable
                    FROM db_inyeccion i
                    WHERE i.orden_produccion = :op
                      AND {sql_normalizar_codigo_fr('i.id_codigo')} = UPPER(TRIM(:codigo))
                      AND i.responsable IS NOT NULL
                    ORDER BY i.fecha_inicia DESC NULLS LAST
                    LIMIT 1
                """),
                {'op': op, 'codigo': codigo}
            ).fetchone()
            if fila and _responsable_valido(fila[0]):
                return _responsable_valido(fila[0])

        logger.warning(
            f"⚠️ [PNC-Inyeccion] No se pudo rastrear el operario de inyección del turno "
            f"{getattr(registro, 'id_pulido', '?')} (lote={lote!r}, OP={op!r}, cod={codigo!r}). "
            f"La merma queda sin atribuir en vez de asignarse a un dueño incorrecto."
        )
        return None

    @staticmethod
    def validar_duracion_turno(segundos_segmento: int) -> None:
        """
        Rechaza duraciones de turno imposibles para Pulido (jornada única 07:00-17:00,
        sin turno nocturno). Debe llamarse con el delta CRUDO hora_fin-hora_inicio
        (ya con el wraparound de medianoche aplicado si corresponde), antes de sumar
        tiempo_acumulado_ms o descontar pausas.
        """
        limite_seg = DURACION_MAXIMA_TURNO_HORAS * 3600
        if segundos_segmento > limite_seg:
            raise TurnoInvalidoException(
                horas_calculadas=segundos_segmento / 3600.0,
                horas_maximas=DURACION_MAXIMA_TURNO_HORAS,
            )

    # ---------------------------------------------------------------
    # GARBAGE COLLECTOR DE SESIONES (TTL)
    # ---------------------------------------------------------------
    @staticmethod
    def limpiar_sesiones_zombis(responsable=None):
        """
        Garbage Collector pasivo (TTL): autocierra sesiones de Pulido en
        TRABAJANDO/EN_PROCESO/PAUSADO que superan PULIDO_SESSION_TTL_HOURS de
        antigüedad. Se invoca antes de cualquier evaluación de "sesión activa"
        (iniciar turno, consultar estado, session_active) para que un turno
        abandonado no bloquee indefinidamente al operario en un lote nuevo.

        Antigüedad = ahora - (fecha_registro o hora_inicio como fallback).
        Retorna la cantidad de sesiones autocerradas.
        """
        try:
            ahora = get_colombia_time()
            query = db.session.query(ProduccionPulido).filter(
                ProduccionPulido.estado.in_(ESTADOS_SESION_ACTIVA_GC)
            )
            if responsable:
                query = query.filter(ProduccionPulido.responsable == responsable)

            cerradas = 0
            for sesion in query.all():
                referencia = sesion.fecha_registro or sesion.hora_inicio
                if not referencia:
                    continue
                horas_abierta = (ahora - referencia).total_seconds() / 3600.0
                if horas_abierta > PULIDO_SESSION_TTL_HOURS:
                    sesion.estado = 'DESCARTADO_AUTO'
                    logger.info(
                        f"🛡️ [TTL Garbage Collector] Sesión ID {sesion.id_pulido} de {sesion.responsable} "
                        f"autocerrada por superar {PULIDO_SESSION_TTL_HOURS}h"
                    )
                    cerradas += 1

            if cerradas:
                db.session.commit()
            return cerradas
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error en PulidoService.limpiar_sesiones_zombis: {e}")
            return 0

    # ---------------------------------------------------------------
    # PANEL DE ADMIN: ver/pausar/reanudar sesiones de TODAS las operarias
    # (plan 2026-08-31, pedido de la jefa para cuando retomen tomas de
    # sesión en tablets compartidas). Pausar/reanudar reutilizan los
    # endpoints existentes (ya son por id_pulido, sin candado de dueño).
    # Corregir un reporte reutiliza el POST /api/pulido normal: el
    # Ownership Guard (AuditService.resolver_y_validar_propietario) YA
    # deja pasar a roles admin/jefe preservando el responsable original --
    # no hace falta un endpoint de escritura nuevo para eso.
    # ---------------------------------------------------------------
    @staticmethod
    def listar_sesiones_activas():
        """
        Todas las sesiones de Pulido en TRABAJANDO/EN_PROCESO/PAUSADO/
        PAUSADO_COLA, de cualquier operaria -- fuente del panel de admin.
        Corre el TTL Garbage Collector primero para no listar sesiones
        zombi ya abandonadas hace más de PULIDO_SESSION_TTL_HOURS.
        """
        PulidoService.limpiar_sesiones_zombis()
        ahora = get_colombia_time()

        sesiones = db.session.query(ProduccionPulido).filter(
            ProduccionPulido.estado.in_(['TRABAJANDO', 'EN_PROCESO', 'PAUSADO', 'PAUSADO_COLA'])
        ).order_by(ProduccionPulido.hora_inicio.asc().nullslast()).all()

        resultado = []
        for s in sesiones:
            referencia = s.hora_inicio or s.fecha_registro
            minutos_abierta = round((ahora - referencia).total_seconds() / 60.0, 1) if referencia else None
            resultado.append({
                'id_pulido': s.id_pulido,
                'responsable': s.responsable,
                'codigo': s.codigo,
                'lote': s.lote,
                'orden_produccion': s.orden_produccion,
                'estado': s.estado,
                'fecha': s.fecha.strftime('%Y-%m-%d') if s.fecha else None,
                'hora_inicio': s.hora_inicio.strftime('%H:%M') if s.hora_inicio else None,
                'hora_inicio_dt': s.hora_inicio.isoformat() if s.hora_inicio else None,
                'hora_fin': s.hora_fin.strftime('%H:%M') if s.hora_fin else None,
                'hora_pausa_dt': s.hora_pausa.isoformat() if (s.estado in ('PAUSADO', 'PAUSADO_COLA') and s.hora_pausa) else None,
                'tiempo_pausa_acumulado': int(s.tiempo_pausa_acumulado or 0),
                'minutos_abierta': minutos_abierta,
                'cantidad_real': float(s.cantidad_real or 0),
                'pnc_inyeccion': int(s.pnc_inyeccion or 0),
                'pnc_pulido': int(s.pnc_pulido or 0),
                'cantidad_recibida': float(s.cantidad_recibida or 0),
                'observaciones': s.observaciones or '',
                'criterio_pnc_inyeccion': s.criterio_pnc_inyeccion or '',
                'criterio_pnc_pulido': s.criterio_pnc_pulido or '',
                'almacen_destino': s.almacen_destino or 'P. TERMINADO',
            })
        return resultado

    @staticmethod
    def _es_responsable_ignorado(nombre: str) -> bool:
        """
        Determina si un responsable debe ser purgado de los KPIs y Rankings de Pulido.

        Solo coincidencia EXACTA contra _IGNORAR (ya cubre todas las variantes de
        tildes necesarias). Antes existía un fallback por substring que buscaba
        fragmentos genéricos ('EDIMAR', 'JUAN SEBASTIAN', 'NOVOA') dentro del nombre
        normalizado — ese mecanismo fue el que invisibilizó a la operaria activa
        'LAURA LIZETH VARGAS R.' en cuanto el patrón coincidía con un substring de su
        nombre. Se elimina por completo: cualquier variante real que deba ignorarse
        debe agregarse explícitamente a _IGNORAR, nunca por coincidencia parcial.
        """
        if not nombre:
            return True
        norm = PulidoService._normalizar_nombre(nombre)
        return norm in PulidoService._IGNORAR

    # ---------------------------------------------------------------
    # RANKING: Leaderboard por Volumen (Piezas) y Eficiencia
    # ---------------------------------------------------------------
    @staticmethod
    def get_ranking_leaderboard(desde=None, hasta=None, limit: int = 20) -> dict:
        """
        Retorna el diccionario 'pulido_profundo' listo para el frontend.

        Estructura de cada entrada:
        {
            "NOMBRE OPERARIA": {
                "buenas": int,
                "pnc": int,
                "eficiencia": float,          # % (Tiempo Std / Tiempo Real * 100)
                "yield_calidad": float,        # % (buenas / (buenas+pnc) * 100)
                "minutos": int,
                "insight": str
            }
        }

        Fuente de datos:
        - db_pulido: registros FINALIZADOS (estado IN ('FINALIZADO','APROBADO'))
        - db_costos: tiempo_estandar por referencia
        - Deduplicación: UPPER(TRIM(responsable)) evita duplicados por case.
        - El JOIN con db_costos usa UPPER(TRIM) en ambos lados para evitar misses.
        """
        try:
            params = {'lim': limit}
            filt = " AND p.estado IN ('FINALIZADO', 'APROBADO')"
            if desde and hasta:
                filt += " AND p.fecha BETWEEN :desde AND :hasta"
                params['desde'] = desde
                params['hasta'] = hasta

            sql = f"""
                SELECT
                    UPPER(TRIM(p.responsable))                                        AS responsable,
                    SUM(COALESCE(p.cantidad_real, 0))                                 AS buenas,
                    SUM(COALESCE(p.pnc_pulido, 0) + COALESCE(p.pnc_inyeccion, 0))    AS pnc,
                    SUM(COALESCE(p.tiempo_total_minutos, 0))                          AS t_real,
                    -- t_std solo suma cantidad_real de lotes CON tiempo_total_minutos capturado:
                    -- t_real tampoco incluye los lotes sin tiempo, así que ambos lados de la
                    -- razón de eficiencia deben compartir la misma población o el ratio se dispara.
                    SUM(
                        CASE WHEN COALESCE(p.tiempo_total_minutos, 0) > 0 THEN COALESCE(p.cantidad_real, 0) ELSE 0 END
                        * COALESCE(
                            NULLIF(
                                regexp_replace(
                                    REPLACE(COALESCE(c.tiempo_estandar::TEXT,'0'), ',', '.'),
                                    '[^0-9.]', '', 'g'
                                ), ''
                            )::NUMERIC, 0
                        )
                    )                                                                  AS t_std
                FROM db_pulido p
                LEFT JOIN db_costos c
                       ON {sql_normalizar_codigo_fr('p.codigo')} = {sql_normalizar_codigo_fr('c.referencia')}
                WHERE 1=1 {filt}
                GROUP BY UPPER(TRIM(p.responsable))
                ORDER BY buenas DESC
                LIMIT :lim
            """
            rows = db.session.execute(text(sql), params).fetchall()

            resultado = {}
            for r in rows:
                nombre = PulidoService._normalizar_nombre(str(r[0] or 'Desconocido'))
                if PulidoService._es_responsable_ignorado(nombre):
                    continue
                buenas  = _num(r[1], int)
                pnc     = _num(r[2], int)
                t_real  = _num(r[3], float)
                t_std   = _num(r[4], float)

                # None (no 0) cuando no hay ningun lote con tiempo_total_minutos capturado:
                # "sin dato" no es lo mismo que "0% de rendimiento".
                eficiencia   = round((t_std / t_real * 100), 1) if t_real > 0 else None
                total        = buenas + pnc
                yield_cal    = round((buenas / total * 100), 1) if total > 0 else 100

                resultado[nombre] = {
                    # ── Métrica VOLUMÉTRICA (física) ────────────────────
                    "buenas":            buenas,        # alias canónico para el leaderboard
                    "piezas_producidas": buenas,        # alias explícito — SOLO unidades OK
                    "pnc":               pnc,
                    # ── Eficiencia y calidad ─────────────────────────────
                    "eficiencia":        eficiencia,
                    "yield_calidad":     yield_cal,
                    "minutos":           int(t_real),
                    "insight":           PulidoService._generar_insight(nombre, buenas, pnc, eficiencia, yield_cal)
                }
            return resultado

        except Exception as e:
            db.session.rollback()
            logger.error(f"[PulidoService.get_ranking_leaderboard] {e}")
            return {}

    # ---------------------------------------------------------------
    # EVOLUCIÓN: cambio de volumen/eficiencia vs el período anterior
    # ---------------------------------------------------------------
    @staticmethod
    def get_evolucion_operarias(desde=None, hasta=None, limit: int = 200) -> dict:
        """
        Compara cada operaria contra el período INMEDIATAMENTE ANTERIOR de
        igual duración -- plan 2026-08-28: "poner evolución de pulido en
        dashboard, como mejoran o desmejoran en % las de pulido", corregido
        después a solo volumen/cantidad y tiempos (SIN cruzar con
        asistencia/días trabajados, ver memoria de la sesión).

        Reutiliza get_ranking_leaderboard para ambos períodos -- misma
        fuente de verdad que ya usa el leaderboard normal, cero lógica de
        agregación duplicada. limit=200 (no el default de 20): a diferencia
        del leaderboard visible, aquí se necesita a TODA operaria con
        actividad en cualquiera de los dos períodos, no solo el top.

        Sin desde/hasta no hay un "período anterior" bien definido (¿anterior
        a qué?) -- se devuelve vacío en vez de inventar un rango.
        """
        if not desde or not hasta:
            return {}

        dias = (hasta - desde).days + 1
        hasta_anterior = desde - timedelta(days=1)
        desde_anterior = hasta_anterior - timedelta(days=dias - 1)

        actual = PulidoService.get_ranking_leaderboard(desde, hasta, limit=limit)
        anterior = PulidoService.get_ranking_leaderboard(desde_anterior, hasta_anterior, limit=limit)

        def _pct_cambio(actual_val, anterior_val):
            # Sin base de comparación real (antes 0, o sin dato) -- un % de
            # cambio ahí es matemáticamente indefinido, no "infinito mejor".
            if anterior_val is None or actual_val is None or anterior_val <= 0:
                return None
            return round((actual_val - anterior_val) / anterior_val * 100, 1)

        resultado = {}
        for nombre in set(actual.keys()) | set(anterior.keys()):
            a = actual.get(nombre, {})
            p = anterior.get(nombre, {})
            buenas_actual = a.get('buenas', 0)
            buenas_anterior = p.get('buenas', 0)
            eficiencia_actual = a.get('eficiencia')
            eficiencia_anterior = p.get('eficiencia')

            resultado[nombre] = {
                'buenas_actual': buenas_actual,
                'buenas_anterior': buenas_anterior,
                'pct_volumen': _pct_cambio(buenas_actual, buenas_anterior),
                'eficiencia_actual': eficiencia_actual,
                'eficiencia_anterior': eficiencia_anterior,
                'pct_eficiencia': _pct_cambio(eficiencia_actual, eficiencia_anterior),
            }
        return resultado

    # ---------------------------------------------------------------
    # DETALLE POR REFERENCIA (modal de operaria)
    # ---------------------------------------------------------------
    @staticmethod
    def _fmt_hora(dt) -> str:
        """
        Las horas de db_pulido ya se guardan en hora local de Colombia
        (get_colombia_time), asi que se formatean tal cual: convertirlas en
        el navegador con new Date() volveria a aplicar el offset y mostraria
        horas corridas 5h -- el mismo desfase que ya se documento con el
        picker de 12h en Modo Satelite.
        """
        if not dt:
            return None
        try:
            return dt.strftime('%d/%m %H:%M')
        except Exception:
            return None

    @staticmethod
    def get_detalle_por_referencia(desde=None, hasta=None) -> dict:
        """
        Retorna: {
            "NOMBRE": {
                "REF": {
                    cantidad_total, costo_unidad,
                    hora_inicio, hora_fin,   # 'dd/mm HH:MM' ya en hora Colombia
                    minutos,                 # tiempo total trabajado en esa ref
                    min_por_pieza,           # promedio min/pz (None si no hay tiempo)
                    lotes                    # cuantos reportes componen la fila
                }
            }
        }
        """
        try:
            params = {}
            filt = " AND p.estado IN ('FINALIZADO', 'APROBADO')"
            if desde and hasta:
                filt += " AND p.fecha BETWEEN :desde AND :hasta"
                params['desde'] = desde
                params['hasta'] = hasta

            ref_norm = sql_normalizar_codigo_fr('p.codigo')
            sql = f"""
                SELECT
                    UPPER(TRIM(p.responsable))                                         AS responsable,
                    {ref_norm}                                                          AS referencia,
                    SUM(COALESCE(p.cantidad_real, 0))                                  AS qty,
                    MAX(COALESCE(
                        NULLIF(
                            regexp_replace(
                                REPLACE(COALESCE(c.costo_total::TEXT,'0'), ',', '.'),
                                '[^0-9.]', '', 'g'
                            ), ''
                        )::NUMERIC, 0
                    ))                                                                  AS costo_u,
                    MIN(p.hora_inicio)                                                  AS hora_ini,
                    MAX(p.hora_fin)                                                     AS hora_fin,
                    SUM(COALESCE(p.tiempo_total_minutos, 0))                            AS minutos,
                    -- El promedio min/pz solo puede dividir por las piezas de los
                    -- lotes que SI tienen tiempo capturado; mezclar poblaciones
                    -- (igual que en el ratio de eficiencia) hunde el promedio.
                    SUM(
                        CASE WHEN COALESCE(p.tiempo_total_minutos, 0) > 0
                             THEN COALESCE(p.cantidad_real, 0) ELSE 0 END
                    )                                                                   AS qty_con_tiempo,
                    COUNT(*)                                                            AS lotes
                FROM db_pulido p
                LEFT JOIN db_costos c
                       ON {ref_norm} = {sql_normalizar_codigo_fr('c.referencia')}
                WHERE 1=1 {filt}
                GROUP BY 1, 2
                ORDER BY 1, qty DESC
            """
            rows = db.session.execute(text(sql), params).fetchall()

            refs_map: dict = {}
            for r in rows:
                resp  = PulidoService._normalizar_nombre(str(r[0] or 'Desconocido'))
                ref   = str(r[1] or 'Sin Referencia').strip()
                qty   = _num(r[2], int)
                costo = _num(r[3], float)
                minutos = _num(r[6], float)
                qty_ct  = _num(r[7], int)
                lotes   = _num(r[8], int)
                if PulidoService._es_responsable_ignorado(resp):
                    continue
                if resp not in refs_map:
                    refs_map[resp] = {}
                refs_map[resp][ref] = {
                    "cantidad_total": qty,
                    "costo_unidad":   costo,
                    "hora_inicio":    PulidoService._fmt_hora(r[4]),
                    "hora_fin":       PulidoService._fmt_hora(r[5]),
                    "minutos":        round(minutos, 1),
                    # None (no 0) cuando no hay tiempo capturado: "sin dato" no
                    # es lo mismo que "0 min por pieza".
                    "min_por_pieza":  round(minutos / qty_ct, 2) if minutos > 0 and qty_ct > 0 else None,
                    "lotes":          lotes
                }
            return refs_map

        except Exception as e:
            db.session.rollback()
            logger.error(f"[PulidoService.get_detalle_por_referencia] {e}")
            return {}

    # ---------------------------------------------------------------
    # MÉTODO COMPUESTO: DTO completo para el dashboard
    # ---------------------------------------------------------------
    @staticmethod
    def get_analytics_completo(desde=None, hasta=None) -> dict:
        """
        DTO único que el endpoint /api/dashboard/stats consume.
        Retorna:
        {
            "operario_referencia": { "NOMBRE": { "REF": {...} } }
        }
        """
        return {
            "operario_referencia": PulidoService.get_detalle_por_referencia(desde, hasta)
        }

    # ---------------------------------------------------------------
    # FASE 7: SALDO REAL DE "POR PULIR" POR OP
    # ---------------------------------------------------------------
    @staticmethod
    def _fecha_corte() -> date:
        """
        Punto de partida limpio (decisión del usuario, plan 2026-08-25:
        "hacer como que la app empezó ese [corte] a tomar datos"). Antes del
        corte las OP no son confiables para este cálculo -- se digitaban a
        mano en WO, sin el numerador nuevo, así que cruzar inyección/pulido
        por ellas daría saldos falsos.

        Fallback 2026-08-31: fecha de lanzamiento de la nueva versión
        confirmada por el usuario 2026-08-27, usada mientras AppConfig
        'op_wo.fecha_corte' no tenga un valor propio.
        """
        fila = db.session.get(AppConfig, 'op_wo.fecha_corte')
        if fila and fila.valor:
            try:
                return datetime.strptime(str(fila.valor)[:10], '%Y-%m-%d').date()
            except ValueError:
                logger.warning(f"[PulidoService._fecha_corte] Valor inválido en AppConfig: {fila.valor!r}")
        return date(2026, 8, 31)

    @staticmethod
    def get_saldo_por_op() -> list:
        """
        inyectado - pulido = saldo, por (OP, referencia). 'inyectado' es
        SOLO lo ya validado (ProduccionInyeccion.cantidad_real WHERE
        estado='CERRADO') -- un lote sin validar todavía puede cambiar. Se
        ignoran orden_produccion NULL/'SIN OP' (sin trazabilidad real) y
        todo lo anterior al corte (ver _fecha_corte).

        Cruce por referencia normalizada (sql_normalizar_codigo_fr), mismo
        criterio que ya usa este servicio para cruzar contra db_costos --
        inyección y pulido comparten el mismo universo de códigos FR.

        FULL OUTER JOIN a propósito: una OP con inyección pero sin pulido
        reportado aún debe verse con pulido=0, no desaparecer; y viceversa
        (que no debería pasar en un flujo sano, pero si pasa, mejor que se
        vea el saldo negativo que quede oculto).
        """
        try:
            fecha_corte = PulidoService._fecha_corte()
            ref_iny = sql_normalizar_codigo_fr('i.id_codigo')
            ref_pul = sql_normalizar_codigo_fr('p.codigo')

            sql = f"""
                WITH inyectado AS (
                    SELECT i.orden_produccion AS op, {ref_iny} AS referencia,
                           SUM(COALESCE(i.cantidad_real, 0)) AS cantidad
                    FROM db_inyeccion i
                    WHERE i.estado = 'CERRADO'
                      AND i.orden_produccion IS NOT NULL AND i.orden_produccion <> 'SIN OP'
                      AND i.fecha_inicia >= :fecha_corte
                    GROUP BY i.orden_produccion, {ref_iny}
                ),
                pulido AS (
                    SELECT p.orden_produccion AS op, {ref_pul} AS referencia,
                           SUM(COALESCE(p.cantidad_real, 0)) AS cantidad
                    FROM db_pulido p
                    WHERE p.orden_produccion IS NOT NULL AND p.orden_produccion <> 'SIN OP'
                      AND p.fecha >= :fecha_corte
                    GROUP BY p.orden_produccion, {ref_pul}
                )
                SELECT
                    COALESCE(i.op, pu.op)                 AS orden_produccion,
                    COALESCE(i.referencia, pu.referencia)  AS referencia,
                    COALESCE(i.cantidad, 0)                AS inyectado,
                    COALESCE(pu.cantidad, 0)                AS pulido,
                    COALESCE(i.cantidad, 0) - COALESCE(pu.cantidad, 0) AS saldo
                FROM inyectado i
                FULL OUTER JOIN pulido pu
                  ON i.op = pu.op AND i.referencia = pu.referencia
                ORDER BY orden_produccion, referencia
            """
            rows = db.session.execute(text(sql), {'fecha_corte': fecha_corte}).fetchall()

            return [{
                'orden_produccion': r.orden_produccion,
                'referencia':       r.referencia,
                'inyectado':        _num(r.inyectado, int),
                'pulido':           _num(r.pulido, int),
                'saldo':            _num(r.saldo, int),
            } for r in rows]

        except Exception as e:
            db.session.rollback()
            logger.error(f"[PulidoService.get_saldo_por_op] {e}")
            return []

    # ---------------------------------------------------------------
    # BLOQUEOS DUROS (plan 2026-08-28): fecha same-day + cantidad <= inyectado
    # ---------------------------------------------------------------
    @staticmethod
    def _normalizar_referencia_bind(referencia: str) -> str:
        """
        Equivalente en Python de sql_normalizar_codigo_fr, para el lado del
        bind param: un CASE de Postgres sobre un parámetro con cast ::text
        (":referencia::text") rompe el parser de placeholders de
        sqlalchemy.text() (:: se interpreta como escape de dos puntos
        literales, no como cast) -- normalizar aquí evita ese choque y es
        equivalente porque el parámetro ya es un str de Python, no necesita
        cast.
        """
        ref = str(referencia or '').strip().upper()
        return f"FR-{ref}" if ref.isdigit() else ref

    @staticmethod
    def es_op_reconocida(op: str, referencia: str) -> bool:
        """
        Una OP está "reconocida" por el nuevo sistema de trazabilidad si
        existe inyección CERRADA para esa OP+referencia desde la fecha de
        corte (mismo criterio que get_saldo_por_op). Si no -- OP vieja,
        digitada a mano, o material sin trazabilidad real -- los bloqueos
        de fecha/cantidad NO aplican: el campo se comporta libre, exactamente
        como hoy. Este es el mecanismo que evita bloquear el backlog de
        material ya inyectado antes del corte.
        """
        op = str(op or '').strip()
        if not op or op.upper() == 'SIN OP':
            return False
        try:
            fecha_corte = PulidoService._fecha_corte()
            ref_iny = sql_normalizar_codigo_fr('i.id_codigo')
            fila = db.session.execute(text(f"""
                SELECT 1 FROM db_inyeccion i
                WHERE i.orden_produccion = :op
                  AND {ref_iny} = :referencia
                  AND i.estado = 'CERRADO'
                  AND i.fecha_inicia >= :fecha_corte
                LIMIT 1
            """), {
                'op': op,
                'referencia': PulidoService._normalizar_referencia_bind(referencia),
                'fecha_corte': fecha_corte,
            }).first()
            return fila is not None
        except Exception as e:
            db.session.rollback()
            logger.error(f"[PulidoService.es_op_reconocida] {e}")
            return False

    @staticmethod
    def validar_bloqueo_fecha(fecha_reporte: date, forzado: bool = False) -> None:
        """
        Rechaza el reporte si la fecha reportada no es hoy, salvo que un
        ADMIN lo fuerce (el guard de rol vive en la ruta, no aquí -- ver
        pulido_routes.registrar_pulido). Pulido tiene jornada única
        07:00-17:00 (ver DURACION_MAXIMA_TURNO_HORAS), sin turno nocturno,
        así que "hoy" no necesita margen de medianoche.
        """
        if forzado or not fecha_reporte:
            return
        hoy = get_colombia_time().date()
        if fecha_reporte != hoy:
            raise FechaPulidoInvalidaException(fecha_reporte, hoy)

    @staticmethod
    def validar_saldo_op(op: str, referencia: str, cantidad_nueva_total: float,
                          id_pulido_actual: str = None, forzado: bool = False) -> None:
        """
        Verifica que (ya reportado en Pulido para esta OP+referencia,
        excluyendo el registro que se está editando) + cantidad_nueva_total
        no supere lo que Inyección cerró para esa misma OP+referencia. Solo
        aplica si es_op_reconocida(op, referencia) -- ver esa función para
        el porqué. cantidad_nueva_total = buenas + pnc_inyeccion + pnc_pulido
        del registro que se está guardando (NO incluye revueltos: esos se
        descuentan de un producto distinto, no cuentan contra la cuota de
        esta referencia).
        """
        if forzado:
            return
        if not PulidoService.es_op_reconocida(op, referencia):
            return

        try:
            fecha_corte = PulidoService._fecha_corte()
            ref_iny = sql_normalizar_codigo_fr('i.id_codigo')
            ref_pul = sql_normalizar_codigo_fr('p.codigo')
            referencia_norm = PulidoService._normalizar_referencia_bind(referencia)

            inyectado = db.session.execute(text(f"""
                SELECT COALESCE(SUM(i.cantidad_real), 0)
                FROM db_inyeccion i
                WHERE i.orden_produccion = :op
                  AND {ref_iny} = :referencia
                  AND i.estado = 'CERRADO'
                  AND i.fecha_inicia >= :fecha_corte
            """), {'op': op, 'referencia': referencia_norm, 'fecha_corte': fecha_corte}).scalar()

            params = {'op': op, 'referencia': referencia_norm}
            excluir_sql = ""
            if id_pulido_actual:
                excluir_sql = " AND p.id_pulido <> :id_actual"
                params['id_actual'] = id_pulido_actual

            ya_reportado = db.session.execute(text(f"""
                SELECT COALESCE(SUM(
                    COALESCE(p.cantidad_real, 0) + COALESCE(p.pnc_inyeccion, 0) + COALESCE(p.pnc_pulido, 0)
                ), 0)
                FROM db_pulido p
                WHERE p.orden_produccion = :op
                  AND {ref_pul} = :referencia
                  {excluir_sql}
            """), params).scalar()

            inyectado = _num(inyectado, float)
            ya_reportado = _num(ya_reportado, float)
            cantidad_nueva_total = _num(cantidad_nueva_total, float)

            if ya_reportado + cantidad_nueva_total > inyectado + 0.0001:
                raise CantidadExcedeInyectadoException(op, referencia, inyectado, ya_reportado, cantidad_nueva_total)
        except CantidadExcedeInyectadoException:
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(f"[PulidoService.validar_saldo_op] {e}")

    # ---------------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------------
    @staticmethod
    def _generar_insight(nombre: str, buenas: int, pnc: int, eficiencia: float, yield_cal: float) -> str:
        total = buenas + pnc
        if total == 0:
            return f"{nombre} no tiene registros en el período."
        partes = []
        if yield_cal >= 98:
            partes.append(f"Excelente calidad ({yield_cal}% yield).")
        elif yield_cal < 90:
            partes.append(f"⚠️ Yield bajo ({yield_cal}%). Revisar causas de PNC.")
        if eficiencia is None:
            partes.append("Sin lotes con tiempo capturado para calcular eficiencia.")
        elif eficiencia >= 100:
            partes.append(f"Eficiencia sobre estándar ({eficiencia}%).")
        elif eficiencia > 0 and eficiencia < 70:
            partes.append(f"Eficiencia por debajo del 70% ({eficiencia}%).")
        partes.append(f"{buenas:,} piezas OK en el período.")
        return " ".join(partes) if partes else f"{nombre}: {buenas:,} piezas OK."
