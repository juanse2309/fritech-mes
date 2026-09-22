"""
pulido_service.py
================
Capa de servicio exclusiva para analítica de Pulido.
Toda la lógica de negocio (volumen físico, eficiencia, deduplicación, normalización)
reside aquí. Las rutas solo invocan métodos y retornan JSON.
"""
import json
import logging
import os
import tempfile
import uuid
from datetime import date, datetime, timedelta
from backend.core.sql_database import db
from backend.models.sql_models import (
    ProduccionPulido, AppConfig, PncInyeccion, PncPulido, PncEnsamble,
    BujeRevuelto, Producto, PulidoOverride
)
from backend.utils.formatters import (
    sql_normalizar_codigo_fr, preservar_o_normalizar_prefijo,
    normalizar_codigo, normalizar_codigo_sin_prefijo
)
from backend.utils.time_utils import get_colombia_time
from backend.services.audit_service import TurnoInvalidoException
from backend.services.pausas_service import PausasService
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


class CantidadRealCeroException(Exception):
    """
    Bloqueo duro: un reporte de Pulido que cierra el ciclo (cualquier estado
    que no sea uno de los "en progreso" -- ver ESTADOS_PULIDO_EN_PROGRESO)
    no puede quedar con cantidad_real (piezas buenas) en 0. Un cierre en 0
    casi siempre es un dato mal digitado o un envío a medio terminar, no una
    producción real -- si el 100% del lote salió defectuoso, el flujo
    correcto sigue siendo registrar el PNC, nunca dejar todo en cero.
    Los checkpoints intermedios (pausa/cola, inicio de sesión) sí legítimamente
    mandan cantidad_real=0 porque el operario aún no ha terminado -- por eso
    esta validación solo aplica fuera de ESTADOS_PULIDO_EN_PROGRESO.
    """
    def __init__(self, message=None):
        self.message = message or (
            "La cantidad de piezas buenas (cantidad_real) no puede ser 0 al "
            "cerrar un reporte de Pulido. Verifica el dato antes de guardar."
        )
        super().__init__(self.message)

# Estados de un registro de Pulido que todavía no cerraron el ciclo --
# cantidad_real=0 es legítimo en cualquiera de estos (checkpoint de
# pausa/cola, o inicio de sesión con el cronómetro corriendo). Ver
# CantidadRealCeroException y PulidoService.validar_cantidad_real.
ESTADOS_PULIDO_EN_PROGRESO = ['TRABAJANDO', 'EN_PROCESO', 'PAUSADO', 'PAUSADO_COLA']

# Pulido no tiene turno nocturno: jornada única 07:00-17:00 (10h de span).
# Confirmado por el usuario el 2026-08-03 tras auditoría de horas mal digitadas.
DURACION_MAXIMA_TURNO_HORAS = 10

# TTL del Garbage Collector pasivo de sesiones zombi (ver PulidoService.limpiar_sesiones_zombis).
# Una sesión que lleva más de este tiempo en TRABAJANDO/EN_PROCESO/PAUSADO se asume abandonada
# (tablet apagada, crash de red, turno olvidado) y se autocierra para no bloquear al operario.
PULIDO_SESSION_TTL_HOURS = 14

ESTADOS_SESION_ACTIVA_GC = ['TRABAJANDO', 'EN_PROCESO', 'PAUSADO']

# Estados que una operaria debe poder ver y retomar ella misma desde su propia
# tablet en "Trabajos en cola" -- antes solo incluía PENDIENTE/PAUSADO_COLA,
# dejando invisibles (solo visibles/reanudables desde el panel de admin) las
# tareas que quedaron en PAUSADO simple por el botón genérico "Pausar" en vez
# del flujo de swap/cola. Ver incidente Laura Lizeth Vargas R. 2026-09-17:
# el 9672 quedó en PAUSADO y no aparecía en su cola, solo en supervisión.
ESTADOS_TAREA_RECUPERABLE_OPERARIA = ['PENDIENTE', 'PAUSADO', 'PAUSADO_COLA']


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
    def listar_tareas_pendientes(responsable):
        """
        Tareas de UNA operaria que puede ver y retomar desde su propia tablet
        en el widget "Trabajos en cola" (PENDIENTE/PAUSADO/PAUSADO_COLA --
        ver ESTADOS_TAREA_RECUPERABLE_OPERARIA). Antes excluía PAUSADO simple,
        lo que dejaba tareas pausadas con el botón genérico "Pausar" invisibles
        para la propia operaria (solo el panel de admin las mostraba).
        """
        try:
            tareas = ProduccionPulido.query.filter(
                ProduccionPulido.responsable == responsable,
                ProduccionPulido.estado.in_(ESTADOS_TAREA_RECUPERABLE_OPERARIA)
            ).order_by(ProduccionPulido.id.desc()).all()

            return [{
                "id_pulido": t.id_pulido,
                "codigo": t.codigo,
                "lote": t.lote,
                "orden_produccion": t.orden_produccion,
                "estado": t.estado
            } for t in tareas]
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error en PulidoService.listar_tareas_pendientes: {e}")
            raise

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
                filt += " AND CAST(p.fecha AS DATE) BETWEEN :desde AND :hasta"
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
    # NOTIFICACIÓN: Cambio de líder del día (Mix de Producción)
    # ---------------------------------------------------------------
    _LIDER_CONFIG_KEY = 'pulido.lider_notificado_hoy'

    @staticmethod
    def detectar_y_notificar_cambio_lider():
        """
        Compara el líder actual del Mix de Producción (hoy) contra el
        último líder notificado (AppConfig, clave 'pulido.lider_notificado_hoy',
        valor "YYYY-MM-DD|NOMBRE") y, si cambió, avisa por push al
        departamento PULIDO. No notifica en el primer líder del día (solo
        siembra el estado) ni si el líder se mantiene igual.

        Se llama después de cada guardado de un reporte (ver
        PulidoService.ejecutar_persistencia_pulido y reporte_masivo);
        el llamador debe envolverla en try/except -- un fallo acá nunca
        debe tumbar el guardado real del reporte.
        """
        hoy = get_colombia_time().date()
        top = PulidoService.get_ranking_leaderboard(hoy, hoy, limit=1)
        if not top:
            return
        nombre_actual, datos = next(iter(top.items()))
        buenas_actual = datos.get('buenas', 0)

        fila = db.session.get(AppConfig, PulidoService._LIDER_CONFIG_KEY)
        valor_previo = fila.valor if fila else ''
        fecha_previa, _, nombre_previo = valor_previo.partition('|')

        nuevo_valor = f"{hoy.isoformat()}|{nombre_actual}"
        es_primer_lider_del_dia = (fecha_previa != hoy.isoformat())
        hubo_cambio_real = (not es_primer_lider_del_dia) and (nombre_previo != nombre_actual)

        if fila:
            fila.valor = nuevo_valor
        else:
            db.session.add(AppConfig(clave=PulidoService._LIDER_CONFIG_KEY, valor=nuevo_valor))
        db.session.commit()

        if hubo_cambio_real:
            from backend.services.notification_service import NotificationService
            NotificationService.enviar_notificacion_por_departamento(
                ['PULIDO'],
                "🏆 Cambio de líder en Pulido",
                f"{nombre_actual} se puso a la cabeza con {buenas_actual} piezas hoy, superando a {nombre_previo}.",
                url_destino='/'
            )

    @staticmethod
    def generar_audio_lider(texto: str) -> str:
        """
        Genera (con cache en disco por texto) el audio del anuncio del
        líder de Pulido usando gTTS -- llama a la API pública de Google
        Translate TTS, funciona igual en Windows y en Linux/Render (a
        diferencia de pyttsx3/SAPI5, que se probó primero y es exclusivo
        de Windows). Requiere que el servidor tenga salida a internet.

        Fallback para pantallas/TVs cuyo navegador no soporta
        window.speechSynthesis (confirmado en un Android TV con
        navegadores "Navegador" genérico y TV Bro, ninguno lo implementa,
        aunque sí reproducen audio normal).

        Cache por hash del texto: evita regenerar el mismo anuncio (ej. si
        varios dispositivos consultan el mismo cambio de líder casi a la
        vez).
        """
        import hashlib
        cache_dir = os.path.join(tempfile.gettempdir(), 'pulido_audio_cache')
        os.makedirs(cache_dir, exist_ok=True)
        nombre_archivo = hashlib.md5(texto.encode('utf-8')).hexdigest() + '.mp3'
        ruta = os.path.join(cache_dir, nombre_archivo)

        if not os.path.exists(ruta):
            from gtts import gTTS
            gTTS(text=texto, lang='es').save(ruta)

        return ruta

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
                filt += " AND CAST(p.fecha AS DATE) BETWEEN :desde AND :hasta"
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
                    # Segundos/pieza calculado desde 'minutos' crudo (no desde
                    # min_por_pieza ya redondeado a 2 decimales) -- pedido del
                    # usuario 2026-09-02: el frontend lo muestra en seg/pz y
                    # multiplicar el redondeo intermedio de minutos perdía
                    # precisión (~0.6s de error por el redondeo previo).
                    "seg_por_pieza":  round((minutos * 60) / qty_ct, 1) if minutos > 0 and qty_ct > 0 else None,
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

    @staticmethod
    def obtener_historial_raw(f_inicio: str = '', f_fin: str = '', operario: str = '', id_codigo: str = '') -> list:
        """
        Filas crudas de db_pulido para el historial (endpoint JSON) y la
        exportación a Excel -- antes era el mismo SELECT duplicado en las
        dos rutas de pulido_routes.py. Incluye tiempo_total_minutos aunque
        el endpoint JSON no lo use, para que ambos llamadores compartan
        exactamente la misma consulta sin variantes.
        """
        try:
            sql = """
                SELECT
                    id, id_pulido::TEXT as id_pulido, fecha,
                    codigo::TEXT as codigo, responsable::TEXT as responsable,
                    cantidad_real, pnc_inyeccion, pnc_pulido,
                    hora_inicio, hora_fin, tiempo_total_minutos,
                    orden_produccion::TEXT as orden_produccion,
                    observaciones::TEXT as observaciones,
                    cantidad_recibida
                FROM db_pulido
                WHERE 1=1
            """
            params = {}

            if f_inicio and f_fin:
                sql += " AND CAST(fecha AS DATE) BETWEEN :f_inicio AND :f_fin"
                params['f_inicio'] = f_inicio
                params['f_fin'] = f_fin
            elif f_inicio:
                sql += " AND CAST(fecha AS DATE) >= :f_inicio"
                params['f_inicio'] = f_inicio
            elif f_fin:
                sql += " AND CAST(fecha AS DATE) <= :f_fin"
                params['f_fin'] = f_fin

            if operario and operario.upper() != 'TODOS':
                sql += " AND UPPER(TRIM(responsable)) LIKE :operario"
                params['operario'] = f"%{operario.strip().upper()}%"

            if id_codigo and id_codigo.upper() != 'TODOS':
                sql += " AND UPPER(TRIM(codigo)) LIKE :codigo"
                params['codigo'] = f"%{id_codigo.strip().upper()}%"

            sql += " ORDER BY fecha DESC, id DESC"

            result = db.session.execute(text(sql), params)
            return [dict(row._mapping) for row in result]
        except Exception as e:
            db.session.rollback()
            logger.error(f"[PulidoService.obtener_historial_raw] {e}")
            return []

    @staticmethod
    def obtener_revueltos_por_ids(ids_pulido: list) -> dict:
        """
        { id_pulido: [ {id_codigo, cantidad}, ... ] } para los ids dados,
        en una sola consulta batch (evita N+1 al armar el historial).
        """
        if not ids_pulido:
            return {}
        try:
            placeholders = ', '.join([f':pid_{i}' for i in range(len(ids_pulido))])
            sql = (
                "SELECT id_pulido::TEXT as id_pulido, id_codigo::TEXT as id_codigo, "
                f"COALESCE(cantidad, 0) as cantidad FROM db_bujes_revueltos WHERE id_pulido IN ({placeholders})"
            )
            params = {f'pid_{i}': pid for i, pid in enumerate(ids_pulido)}
            rows = db.session.execute(text(sql), params)
            revueltos_map = {}
            for rv in rows:
                rv_dict = dict(rv._mapping)
                pid = str(rv_dict['id_pulido'])
                revueltos_map.setdefault(pid, []).append(rv_dict)
            return revueltos_map
        except Exception as e:
            db.session.rollback()
            logger.error(f"[PulidoService.obtener_revueltos_por_ids] {e}")
            return {}

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
    def validar_cantidad_real(cantidad_real: float, estado: str) -> None:
        """
        Rechaza el reporte si cantidad_real (piezas buenas) es 0 y el estado
        no es uno de ESTADOS_PULIDO_EN_PROGRESO -- ver CantidadRealCeroException.
        """
        if (estado or '').strip().upper() in ESTADOS_PULIDO_EN_PROGRESO:
            return
        if not cantidad_real or cantidad_real <= 0:
            raise CantidadRealCeroException()

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
    # PAUSAR / REANUDAR e persistencia del reporte -- movidos desde
    # pulido_routes.py (violaban la separación de capas: lógica de negocio
    # y SQL directo no deben vivir en un archivo de rutas). Relocalización
    # pura, sin cambios de comportamiento -- ver plan de refactor 2026-09-17.
    # ---------------------------------------------------------------

    @staticmethod
    def pausar(id_pulido, hora_pausa=None, estado_destino='PAUSADO'):
        """
        Registra el inicio de la pausa en el servidor. Retorna el registro
        actualizado, o None si id_pulido no existe (la ruta traduce eso a 404).

        estado_destino: 'PAUSADO' (pausa manual normal) o 'PAUSADO_COLA'
        (usado por intercambiar_tarea al bajar de TRABAJANDO a la cola).
        """
        registro = ProduccionPulido.query.filter_by(id_pulido=id_pulido).first()
        if not registro:
            return None
        try:
            # Blindaje: Forzar timestamp de Colombia (Bogotá)
            ahora = get_colombia_time()

            # Si el frontend envía una hora específica, intentar usarla para la parte de tiempo
            if hora_pausa and ':' in hora_pausa:
                try:
                    h, m = hora_pausa.split(':')
                    ahora = ahora.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                except: pass

            registro.estado = estado_destino
            registro.hora_pausa = ahora.replace(tzinfo=None) # Guardar como naive Bogota
            db.session.add(registro)
            db.session.commit()

            logger.debug(f" [PAUSA] Actividad {id_pulido} pausada ({estado_destino}) a las {registro.hora_pausa}")
            return registro
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def reanudar(id_pulido, hora_reanudar=None):
        """
        Calcula el tiempo de la pausa y lo suma al acumulador. Retorna el
        registro actualizado, o None si id_pulido no existe (la ruta traduce eso a 404).
        """
        registro = ProduccionPulido.query.filter_by(id_pulido=id_pulido).first()
        if not registro:
            return None
        try:
            if registro.hora_pausa:
                ahora = get_colombia_time()

                # Si el frontend envía una hora específica de reanudación
                if hora_reanudar and ':' in hora_reanudar:
                    try:
                        h, m = hora_reanudar.split(':')
                        ahora = ahora.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
                    except: pass

                ahora_naive = ahora.replace(tzinfo=None)
                diferencia = ahora_naive - registro.hora_pausa
                segundos_pausa = int(diferencia.total_seconds())
                if segundos_pausa < 0: segundos_pausa = 0 # Evitar pausas negativas por drift

                registro.tiempo_pausa_acumulado = (registro.tiempo_pausa_acumulado or 0) + segundos_pausa

            registro.estado = 'TRABAJANDO'
            registro.hora_pausa = None
            db.session.commit()
            return registro
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def intercambiar_tarea(responsable, id_pulido_nuevo):
        """
        Swap atómico de tarea activa para multitarea/urgencia ("Cambiar
        Referencia/Urgencia" o "Retomar" desde Trabajos en cola): pausa a
        PAUSADO_COLA todo lo que esté TRABAJANDO para este responsable y
        reactiva la tarea elegida. Retorna el registro reactivado, o None si
        id_pulido_nuevo no existe (la ruta traduce eso a 404).

        Reutiliza pausar()/reanudar() -- antes esto vivía como SQL directo en
        la ruta (swap_task) y nunca acumulaba tiempo_pausa_acumulado al
        reactivar, dejando el tiempo que la tarea pasó en cola contando como
        tiempo trabajado en el reporte final (hallazgo 2026-09-17, incidente
        Laura Lizeth Vargas R. con la tarea 9672).
        """
        trabajos_activos = ProduccionPulido.query.filter(
            ProduccionPulido.responsable == responsable,
            ProduccionPulido.estado == 'TRABAJANDO'
        ).all()
        for t in trabajos_activos:
            PulidoService.pausar(t.id_pulido, estado_destino='PAUSADO_COLA')

        return PulidoService.reanudar(id_pulido_nuevo)

    @staticmethod
    def _es_prueba(orden_produccion, id_pulido):
        """Sandbox de pruebas (9999/PRUEBA) -- factorizado para poder evaluarlo
        también contra el estado ANTERIOR de un registro al revertir su efecto
        en inventario (ver ejecutar_persistencia_pulido)."""
        return (
            '9999' in str(orden_produccion or '').upper() or 'PRUEBA' in str(orden_produccion or '').upper()
            or '9999' in str(id_pulido or '').upper() or 'PRUEBA' in str(id_pulido or '').upper()
        )

    @staticmethod
    def ejecutar_persistencia_pulido(registro, data, responsable, ahora,
                                      forzar_bloqueo=False, motivo_forzado=None, autorizado_por=None):
        """
        Encapsula la persistencia y la lógica de negocio compleja del guardado
        de un reporte de Pulido. Llamada por pulido_routes.registrar_pulido()
        y pulido_routes.autorizar_pendiente_pulido().

        forzar_bloqueo/motivo_forzado/autorizado_por: bypass de los bloqueos duros
        de fecha y cantidad (plan 2026-08-28) -- el guard de que solo un ADMIN
        puede forzar vive en la ruta (registrar_pulido), no aquí. Cuando se
        fuerza y algún bloqueo SÍ se habría disparado, queda una fila en
        PulidoOverride (el "reporte para restar puntos" que pidió la jefa).
        """
        id_pulido = data.get('id_pulido')

        # Snapshot del efecto en inventario YA APLICADO por este registro, ANTES
        # de sobreescribir sus campos -- necesario para revertirlo abajo. Sin
        # esto, editar un reporte ya guardado (o que la tablet lo reenvíe por un
        # reintento de red) vuelve a descontar por_pulir y a sumar p_terminado
        # una segunda vez para la MISMA producción física (hallazgo 2026-08-27,
        # el "flujo directo" no era idempotente: solo Inyección tenía el guard
        # `estado == 'CERRADO'`, Pulido nunca lo tuvo).
        efecto_anterior = None
        if registro:
            rev_anterior = sum(
                float(r.cantidad or 0) for r in
                db.session.query(BujeRevuelto).filter_by(id_pulido=registro.id_pulido).all()
            )
            efecto_anterior = {
                'codigo': registro.codigo,
                'buenas': float(registro.cantidad_real or 0),
                'total': (
                    float(registro.cantidad_real or 0) + float(registro.pnc_inyeccion or 0)
                    + float(registro.pnc_pulido or 0) + rev_anterior
                ),
                'es_prueba': PulidoService._es_prueba(registro.orden_produccion, registro.id_pulido),
            }

        if not registro:
            # Si no existe (o fue borrado de la DB), crear uno nuevo para evitar Error 500
            if id_pulido:
                logger.warning(f" [RECOVERY] id_pulido {id_pulido} no encontrado en DB. Creando nuevo registro.")
            registro = ProduccionPulido(id_pulido=id_pulido or f"PUL-{ahora.strftime('%Y%m%d%H%M%S')}", fecha_registro=ahora)
            db.session.add(registro)

        if not getattr(registro, 'fecha_registro', None):
            registro.fecha_registro = ahora

        # Mapeo y Estandarización
        registro.fecha = datetime.strptime(data.get('fecha_inicio', ahora.strftime('%Y-%m-%d')), '%Y-%m-%d').date()
        # ── Blindaje: se preserva el prefijo que traiga (MT-, CAR-, FR-) y los
        #    números puros quedan intactos; nunca se les antepone una división ──
        registro.codigo = preservar_o_normalizar_prefijo(data.get('codigo_producto'))
        registro.responsable = responsable
        registro.cantidad_real = float(data.get('cantidad_real') or 0)
        registro.pnc_inyeccion = int(data.get('pnc_inyeccion') or 0)
        registro.pnc_pulido = int(data.get('pnc_pulido') or 0)
        registro.criterio_pnc_inyeccion = data.get('criterio_pnc_inyeccion')
        registro.criterio_pnc_pulido = data.get('criterio_pnc_pulido')
        registro.orden_produccion = data.get('orden_produccion') or 'SIN OP'
        registro.observaciones = data.get('observaciones', '')
        registro.estado = data.get('estado', 'FINALIZADO')
        registro.departamento = 'Pulido'  # Estandarización exigida
        registro.lote = data.get('lote') or 'SIN LOTE'
        registro.cantidad_recibida = float(data.get('cantidad_recibida') or 0)
        registro.almacen_destino = data.get('almacen_destino', 'P. TERMINADO')

        # Bloqueo duro: cantidad_real=0 solo es válido en un checkpoint
        # intermedio (pausa/cola, sesión recién iniciada) -- ver
        # ESTADOS_PULIDO_EN_PROGRESO. Cualquier cierre de ciclo con 0 piezas
        # buenas se rechaza antes de tocar inventario/consistencia.
        PulidoService.validar_cantidad_real(registro.cantidad_real, registro.estado)

        # Validación de consistencia (Bujes Buenos + PNC <= Total)
        total_reportado = registro.cantidad_real + registro.pnc_inyeccion + registro.pnc_pulido
        if registro.cantidad_recibida < total_reportado:
            logger.warning(f" [VALIDATION] Inconsistencia en {registro.id_pulido}: Total {registro.cantidad_recibida} < Suma {total_reportado}")

        # ── Bloqueos duros (plan 2026-08-28): fecha same-day + cantidad <= inyectado ──
        # Se evalúan SIN forzar primero para saber si el forzado realmente saltó
        # algo (y así no dejar filas de override "fantasma" cuando forzar_bloqueo
        # viene en true pero nada se habría bloqueado). Si no se fuerza, la
        # excepción sube tal cual y ejecutar_persistencia_pulido no persiste nada
        # (todavía no hubo commit).
        overrides_aplicados = []
        try:
            PulidoService.validar_bloqueo_fecha(registro.fecha, forzado=False)
        except FechaPulidoInvalidaException as exc_fecha:
            if not forzar_bloqueo:
                raise
            overrides_aplicados.append(('FECHA', str(exc_fecha)))

        try:
            PulidoService.validar_saldo_op(
                registro.orden_produccion, registro.codigo, total_reportado,
                id_pulido_actual=registro.id_pulido, forzado=False
            )
        except CantidadExcedeInyectadoException as exc_cant:
            if not forzar_bloqueo:
                raise
            overrides_aplicados.append(('CANTIDAD', str(exc_cant)))

        # Si el ciclo se está cerrando de verdad (estado sale de
        # ESTADOS_PULIDO_EN_PROGRESO) y quedó una pausa abierta sin cerrar
        # (ej. "Terminar y Reportar" presionado directo desde PAUSADO, sin
        # pasar por /api/pulido/reanudar antes), cerrarla aquí sumando lo que
        # faltaba a tiempo_pausa_acumulado -- así el descuento de abajo
        # siempre refleja el tiempo real en pausa/cola, sin importar el
        # camino por el que se llegó a finalizar (hallazgo 2026-09-17).
        if registro.estado not in ESTADOS_PULIDO_EN_PROGRESO and registro.hora_pausa:
            segundos_pausa_abierta = int((ahora.replace(tzinfo=None) - registro.hora_pausa).total_seconds())
            if segundos_pausa_abierta > 0:
                registro.tiempo_pausa_acumulado = (registro.tiempo_pausa_acumulado or 0) + segundos_pausa_abierta
            registro.hora_pausa = None

        # Manejo de Horas y Cálculos de Tiempo
        if data.get('hora_inicio'):
            h_h, h_m = data['hora_inicio'].split(':')
            dt_ini = ahora.replace(hour=int(h_h), minute=int(h_m), second=0, microsecond=0)
            registro.hora_inicio = dt_ini.replace(tzinfo=None)

        if data.get('hora_fin'):
            h_h, h_m = data['hora_fin'].split(':')
            dt_fin = ahora.replace(hour=int(h_h), minute=int(h_m), second=0, microsecond=0)
            registro.hora_fin = dt_fin.replace(tzinfo=None)

        # Cálculo de Métricas
        if data.get('hora_inicio') and data.get('hora_fin'):
            hi_h, hi_m = data['hora_inicio'].split(':')
            hf_h, hf_m = data['hora_fin'].split(':')

            t_ini = ahora.replace(hour=int(hi_h), minute=int(hi_m), second=0, microsecond=0)
            t_fin = ahora.replace(hour=int(hf_h), minute=int(hf_m), second=0, microsecond=0)

            diff = t_fin - t_ini
            segundos_segmento = int(diff.total_seconds())
            if segundos_segmento < 0: segundos_segmento += 86400

            # Barrera arquitectónica: rechaza duraciones imposibles (típico error de
            # digitar 3:20 en vez de 13:20) antes de persistir nada.
            PulidoService.validar_duracion_turno(segundos_segmento)

            tiempo_acumulado_ms = float(data.get('tiempo_acumulado_ms') or 0)
            segundos_totales = segundos_segmento + int(tiempo_acumulado_ms / 1000)

            # Restar el tiempo que el registro pasó en PAUSADO/PAUSADO_COLA en
            # toda su vida (pausas manuales + tiempo "en cola" por swap_task/
            # intercambiar_tarea) -- antes tiempo_pausa_acumulado solo se usaba
            # para congelar el cronómetro EN PANTALLA, nunca se restaba del
            # total que quedaba guardado, así que ese tiempo contaba como
            # trabajado en el reporte final (hallazgo 2026-09-17, incidente
            # Laura Lizeth Vargas R. con la tarea 9672).
            segundos_totales = max(0, segundos_totales - int(registro.tiempo_pausa_acumulado or 0))

            # Autoridad matemática del descuento por pausas programadas: PausasService.
            # El frontend solo aporta hora_inicio/hora_fin crudas (Zero Trust) — el
            # cálculo de intersección de intervalos vive exclusivamente en la capa de servicio.
            descuento_info = PausasService.calcular_descuento_pausas_programadas(t_ini, t_fin)
            segundos_descuento = descuento_info['segundos_descuento']
            if segundos_descuento > 0:
                segundos_totales = max(0, segundos_totales - segundos_descuento)

            registro.duracion_segundos = segundos_totales
            registro.tiempo_total_minutos = float(round(segundos_totales / 60.0, 2))

            cant = float(registro.cantidad_real or 0)
            if cant > 0:
                registro.segundos_por_unidad = float(round(segundos_totales / cant, 2))
            else:
                registro.segundos_por_unidad = 0.0

            if descuento_info['detalle']:
                payload = {
                    "descuento_programado_min": round(segundos_descuento / 60.0, 2),
                    "detalle": descuento_info['detalle']
                }
                tag = f"[AUTO_BREAK]{json.dumps(payload, ensure_ascii=False)}[/AUTO_BREAK]"
                obs = (registro.observaciones or "")
                if "[AUTO_BREAK]" in obs and "[/AUTO_BREAK]" in obs:
                    pre = obs.split("[AUTO_BREAK]")[0]
                    post = obs.split("[/AUTO_BREAK]")[-1]
                    registro.observaciones = (pre + tag + post).strip()
                else:
                    registro.observaciones = (obs + "\n" + tag).strip() if obs else tag

            logger.debug(f" [TIME-DEBUG] {registro.id_pulido} -> Seg: {segundos_segmento}s, Acum: {tiempo_acumulado_ms}ms, DescProgramado: {segundos_descuento}s, Total: {segundos_totales}s")
        else:
            registro.duracion_segundos = 0
            registro.tiempo_total_minutos = 0.0
            registro.segundos_por_unidad = 0.0

        db.session.flush()

        # Sincronización de PNC Detallado
        # registro.codigo puede llegar con o sin prefijo 'FR-' (según lo reportó la
        # planta), pero las tablas de PNC se indexan sin prefijo para no fragmentar
        # 'FR-1005' / '1005' en filas distintas — se sanitiza aquí explícitamente
        # antes de tocar el ORM. Los prefijos de otras divisiones (MT-, CAR-) se
        # conservan: normalizar_codigo_sin_prefijo() solo quita 'FR-'.
        codigo_pnc = normalizar_codigo_sin_prefijo(registro.codigo)
        # Operaria a la que se atribuye toda merma de pulido de este turno. Se
        # resuelve una sola vez: si no hay persona identificable, el reporte no
        # debe persistirse con la merma huérfana.
        operaria_responsable = PulidoService.resolver_operaria_responsable(registro)
        # Dueño de la merma de INYECCIÓN detectada en pulido: el operario que fabricó
        # las piezas, rastreado por el lote/OP de origen. Puede ser None si el lote
        # padre no es rastreable — en ese caso la fila queda sin atribuir a propósito.
        operario_inyeccion_origen = PulidoService.resolver_operario_inyeccion_origen(registro)
        pnc_detail = data.get('pnc_detail', [])
        db.session.query(PncInyeccion).filter_by(id_inyeccion=registro.id_pulido).delete()
        db.session.query(PncPulido).filter_by(id_pulido=registro.id_pulido).delete()
        db.session.query(PncEnsamble).filter_by(id_ensamble=registro.id_pulido).delete()

        if pnc_detail:
            for pnc_item in pnc_detail:
                proc = pnc_item.get('proceso', '').upper()
                cant = float(pnc_item.get('cantidad') or 0)
                crit = pnc_item.get('criterio', '')
                if cant <= 0: continue

                if proc == 'INYECCION':
                    db.session.add(PncInyeccion(
                        id_pnc_inyeccion=uuid.uuid4().hex[:8],
                        id_inyeccion=registro.id_pulido,
                        id_codigo=codigo_pnc,
                        cantidad=cant,
                        criterio=crit,
                        responsable=operario_inyeccion_origen
                    ))
                elif proc == 'PULIDO':
                    db.session.add(PncPulido(
                        id_pnc_pulido=uuid.uuid4().hex[:8],
                        id_pulido=registro.id_pulido,
                        codigo=codigo_pnc,
                        cantidad=cant,
                        criterio=crit,
                        responsable=operaria_responsable
                    ))
                elif proc == 'ENSAMBLE':
                    db.session.add(PncEnsamble(
                        id_pnc_ensamble=uuid.uuid4().hex[:8],
                        id_ensamble=registro.id_pulido,
                        id_codigo=codigo_pnc,
                        cantidad=cant,
                        criterio=crit
                    ))
        else:
            # Fix de Sincronización: si el payload trae el agregado (pnc_pulido/
            # pnc_inyeccion) SIN el desglose itemizado, la tabla hija no puede
            # quedar huérfana de la maestra — o el Dashboard (que lee de
            # db_pnc_pulido/db_pnc_inyeccion) subcuenta este PNC en silencio.
            # Se reutiliza el criterio de texto libre del header si vino, y si
            # no, se cae a un genérico explícito para no perder trazabilidad.
            if registro.pnc_pulido and registro.pnc_pulido > 0:
                db.session.add(PncPulido(
                    id_pnc_pulido=uuid.uuid4().hex[:8],
                    id_pulido=registro.id_pulido,
                    codigo=codigo_pnc,
                    cantidad=registro.pnc_pulido,
                    criterio=registro.criterio_pnc_pulido or 'Diferencia/Sobrante (Sin Desglose)',
                    responsable=operaria_responsable
                ))
            if registro.pnc_inyeccion and registro.pnc_inyeccion > 0:
                db.session.add(PncInyeccion(
                    id_pnc_inyeccion=uuid.uuid4().hex[:8],
                    id_inyeccion=registro.id_pulido,
                    id_codigo=codigo_pnc,
                    cantidad=registro.pnc_inyeccion,
                    criterio=registro.criterio_pnc_inyeccion or 'Diferencia/Sobrante (Sin Desglose)',
                    responsable=operario_inyeccion_origen
                ))

        db.session.flush()

        # Manejo de Bujes Revueltos
        revueltos_list = data.get('revueltos', [])
        logger.debug(f" [REVUELTOS-DEBUG] Payload recibido: {revueltos_list}")

        db.session.query(BujeRevuelto).filter_by(id_pulido=registro.id_pulido).delete()

        for rev_item in revueltos_list:
            cod_rev = preservar_o_normalizar_prefijo(rev_item.get('id_codigo'))
            cant_rev = float(rev_item.get('cantidad') or 0)

            if cant_rev <= 0 or not cod_rev:
                continue

            db.session.add(BujeRevuelto(
                id_bujes_revueltos=uuid.uuid4().hex[:8],
                id_pulido=registro.id_pulido,
                id_codigo=cod_rev,
                cantidad=cant_rev,
                codigo_ensamble=cod_rev,
                responsable=registro.responsable
            ))

        # Distribución FIFO OP/Pedidos
        op_actual = registro.orden_produccion
        if op_actual and str(op_actual).strip() != 'SIN OP':
            from backend.models.sql_models import DistribucionOpPedidos
            op_limpia = str(registro.orden_produccion or '').strip()
            codigo_limpio = normalizar_codigo(registro.codigo)

            cubetas = db.session.query(DistribucionOpPedidos).filter(
                DistribucionOpPedidos.op_world_office == op_limpia,
                DistribucionOpPedidos.codigo_producto == codigo_limpio
            ).order_by(DistribucionOpPedidos.id_distribucion.asc()).all()

            piezas_por_repartir = float(registro.cantidad_real or 0)

            if not cubetas and piezas_por_repartir > 0:
                pedido_asoc = db.session.query(DistribucionOpPedidos.id_pedido).filter(
                    DistribucionOpPedidos.op_world_office == op_limpia
                ).first()
                id_pedido_final = pedido_asoc[0] if (pedido_asoc and pedido_asoc[0]) else f"PED-IMPREVISTO-{op_limpia}"

                nueva_cubeta = DistribucionOpPedidos(
                    op_world_office=op_limpia,
                    id_pedido=id_pedido_final,
                    codigo_producto=codigo_limpio,
                    cant_requerida=piezas_por_repartir,
                    cant_inyectada=piezas_por_repartir,
                    cant_pulida=piezas_por_repartir,
                    cant_ensamblada=0,
                    cant_alistada=0
                )
                db.session.add(nueva_cubeta)
                db.session.flush()
                cubetas = [nueva_cubeta]
                piezas_por_repartir = 0.0

            for cubeta in cubetas:
                if piezas_por_repartir <= 0:
                    break
                falta = max(0, (cubeta.cant_requerida or 0) - (cubeta.cant_pulida or 0))
                if falta > 0:
                    if piezas_por_repartir >= falta:
                        cubeta.cant_pulida = (cubeta.cant_pulida or 0) + falta
                        piezas_por_repartir -= falta
                    else:
                        cubeta.cant_pulida = (cubeta.cant_pulida or 0) + piezas_por_repartir
                        piezas_por_repartir = 0

        # Flujo directo: Actualizar inventario final en db_productos sin usar TrazabilidadLote
        try:
            buenas = float(registro.cantidad_real or 0)
            pnc_total = float(registro.pnc_inyeccion or 0) + float(registro.pnc_pulido or 0)
            rev_total = sum(float(r.get('cantidad', 0)) for r in revueltos_list)
            total_descuento_por_pulir = buenas + pnc_total + rev_total

            es_prueba = PulidoService._es_prueba(registro.orden_produccion, registro.id_pulido)

            # Revertir el efecto que este MISMO registro ya haya aplicado antes
            # de aplicar el nuevo -- así una edición o un reenvío queda neto en
            # el delta real, en vez de descontar por_pulir dos veces (ver
            # snapshot al inicio de la función). Si el código cambió entre
            # ediciones, esto también corrige el producto correcto: revierte
            # sobre el código viejo y aplica sobre el nuevo.
            if efecto_anterior and not efecto_anterior['es_prueba']:
                codigo_ant = preservar_o_normalizar_prefijo(efecto_anterior['codigo'])
                # Match por codigo_sistema O id_codigo (no solo codigo_sistema):
                # el frontend de Pulido (normalizarCodigo) le quita el prefijo
                # 'FR-' antes de enviar -- preservar_o_normalizar_prefijo NO se
                # lo vuelve a poner a propósito (no debe inventar división), así
                # que un código FR- puro llega aquí como '9308', no 'FR-9308'.
                # db_productos.codigo_sistema SIEMPRE lleva el prefijo real, pero
                # id_codigo guarda la referencia tal cual -- sin este OR, CADA
                # reporte de Pulido de una referencia FR- (la división por
                # defecto, la mayoría del catálogo) no encontraba el producto y
                # el descuento de inventario se saltaba en silencio (hallazgo
                # 2026-08-31, probado en vivo en ambos modos Satélite y PRO).
                prod_anterior = db.session.query(Producto).filter(
                    (Producto.codigo_sistema == codigo_ant) | (Producto.id_codigo == codigo_ant)
                ).first()
                if prod_anterior:
                    prod_anterior.por_pulir = float(prod_anterior.por_pulir or 0) + efecto_anterior['total']
                    p_terminado_revertido = float(prod_anterior.p_terminado or 0) - efecto_anterior['buenas']
                    if p_terminado_revertido < 0:
                        logger.warning(
                            f"⚠️ [PULIDO-INVENTARIO] Al editar {registro.id_pulido}, revertir P.Terminado de "
                            f"{efecto_anterior['codigo']} lo manda a negativo ({p_terminado_revertido}) -- "
                            f"probablemente ya se consumió aguas abajo (empaque/despacho). Se deja en 0."
                        )
                    prod_anterior.p_terminado = max(0, p_terminado_revertido)

            if not es_prueba and total_descuento_por_pulir > 0:
                codigo_actual = preservar_o_normalizar_prefijo(registro.codigo)
                prod_wip = db.session.query(Producto).filter(
                    (Producto.codigo_sistema == codigo_actual) | (Producto.id_codigo == codigo_actual)
                ).first()
                if prod_wip:
                    # Restar de por_pulir, evitando negativos de forma preventiva
                    prod_wip.por_pulir = max(0, float(prod_wip.por_pulir or 0) - total_descuento_por_pulir)
                    # Sumar las buenas a p_terminado
                    prod_wip.p_terminado = float(prod_wip.p_terminado or 0) + buenas
            else:
                logger.debug(f"🧪 [SANDBOX] Lote de prueba {registro.id_pulido}. Se ignoró impacto en inventario.")
        except Exception as err:
            logger.error(f"Error actualizando inventario directo en pulido: {err}")

        # ── Sincronización con la Programación de Pulido (plan 2026-09-02) ──
        # Silenciosa a propósito: un fallo acá jamás debe tumbar el guardado
        # real del reporte, solo dejar la tarjeta programada desincronizada
        # (se puede corregir a mano desde el panel de Programación).
        # Import local (no arriba del archivo): programacion_pulido_service.py
        # importa PulidoService a nivel de módulo, así que un import en
        # sentido contrario arriba crearía un ciclo de imports.
        try:
            from backend.services.programacion_pulido_service import ProgramacionPulidoService
            id_programacion_pulido = data.get('id_programacion_pulido')
            if id_programacion_pulido:
                ProgramacionPulidoService.vincular_inicio(id_programacion_pulido, registro.id_pulido)
            ProgramacionPulidoService.marcar_finalizada_si_corresponde(registro.id_pulido, registro.estado)
        except Exception as err_prog:
            logger.warning(
                f"⚠️ [PROGRAMACION-PULIDO] No se pudo sincronizar la tarjeta programada de {registro.id_pulido}: {err_prog}"
            )

        for tipo_bloqueo, detalle_bloqueo in overrides_aplicados:
            db.session.add(PulidoOverride(
                id_pulido=registro.id_pulido,
                tipo=tipo_bloqueo,
                operaria=responsable,
                autorizado_por=autorizado_por,
                motivo=motivo_forzado,
                detalle=detalle_bloqueo,
            ))
            logger.warning(
                f"⚠️ [PULIDO-OVERRIDE] Bloqueo {tipo_bloqueo} de {registro.id_pulido} "
                f"forzado por {autorizado_por!r} (operaria {responsable!r}). Motivo: {motivo_forzado!r}."
            )

        db.session.commit()

        # ── Notificación de cambio de líder en Mix de Producción (Pulido) ──
        # Silenciosa a propósito, igual que la sync de Programación arriba: un
        # fallo acá no debe tumbar el guardado real del reporte.
        try:
            PulidoService.detectar_y_notificar_cambio_lider()
        except Exception as err_lider:
            logger.warning(f"⚠️ [PULIDO-LIDER] No se pudo evaluar cambio de líder: {err_lider}")

        return {
            "success": True,
            "message": "Registro de pulido sincronizado (Flujo directo)",
            "id_pulido": registro.id_pulido,
            "upsert": "UPDATE" if id_pulido and registro.id else "INSERT",
            "overrides_aplicados": [t for t, _ in overrides_aplicados],
        }

    # ---------------------------------------------------------------
    # MÓDULO DE CORRECCIÓN DE PULIDO (plan 2026-09-22)
    # ---------------------------------------------------------------
    # Reemplaza la mala práctica de corregir errores de operación (persona
    # equivocada iniciando una tarea, referencia/OP mal seleccionada) editando
    # la base de datos a mano. Dos niveles, según si la sesión ya movió
    # inventario o no -- ver diseño acordado con el usuario 2026-09-22:
    #   Nivel 1 (cancelar_inicio_equivocado): sesión en cero, se descarta.
    #   Nivel 2 (corregir_codigo_op): sesión con avance real, se corrige
    #   revirtiendo/reaplicando el efecto de inventario bajo el código correcto.

    @staticmethod
    def cancelar_inicio_equivocado(id_pulido, motivo, autorizado_por, nueva_operaria=None):
        """
        Nivel 1: descarta una sesión que se inició por error (persona
        equivocada, tarea equivocada) y todavía no reportó ninguna pieza --
        ni buenas, ni PNC, ni revueltos. En ese estado el inventario NUNCA
        se tocó (ver ejecutar_persistencia_pulido: total_descuento_por_pulir
        solo se aplica si es > 0), así que no hay nada que revertir: se
        borra la fila y, si venía de una tarjeta programada, esa tarjeta
        vuelve a PROGRAMADO para que se pueda retomar por Modo Satélite
        normal -- sin tocar la base de datos a mano.

        `nueva_operaria` (opcional, pedido del usuario 2026-09-22): si el
        error de fondo era "esto no era de esta persona", reasigna la
        tarjeta reaparecida a la operaria correcta en el mismo paso -- ver
        ProgramacionPulidoService.revertir_a_programado. Sin esto, la
        tarjeta vuelve tal cual estaba, sin operaria nueva.

        Cubre también una sesión PAUSADA con tiempo acumulado: el tiempo de
        pausa no afecta inventario, solo métricas de eficiencia -- se
        descarta junto con el resto, correcto porque nunca fue producción
        real.

        Si la sesión SÍ tiene avance real (cantidad_real, PNC o revueltos
        > 0), se rechaza: ese caso ya movió inventario y debe corregirse
        con PulidoService.corregir_codigo_op (Nivel 2), no cancelarse.

        :raises ValueError: id_pulido inexistente, sesión ya cerrada, o con
            avance real.
        """
        if not motivo or not motivo.strip():
            raise ValueError("El motivo es obligatorio para cancelar una sesión.")

        registro = ProduccionPulido.query.filter_by(id_pulido=id_pulido).first()
        if not registro:
            raise ValueError(f"No existe ninguna sesión de Pulido con id_pulido={id_pulido}")

        if (registro.estado or '').strip().upper() not in ESTADOS_PULIDO_EN_PROGRESO:
            raise ValueError(
                f"La sesión {id_pulido} ya está en estado {registro.estado} -- solo se pueden "
                f"cancelar sesiones que siguen en curso (TRABAJANDO/EN_PROCESO/PAUSADO)."
            )

        cantidad_real = float(registro.cantidad_real or 0)
        pnc_total = float(registro.pnc_inyeccion or 0) + float(registro.pnc_pulido or 0)
        revueltos_count = db.session.query(BujeRevuelto).filter_by(id_pulido=id_pulido).count()

        if cantidad_real > 0 or pnc_total > 0 or revueltos_count > 0:
            raise ValueError(
                f"La sesión {id_pulido} ya tiene avance real registrado "
                f"({cantidad_real:g} buenas, {pnc_total:g} PNC, {revueltos_count} revueltos) -- "
                f"no se puede cancelar. Usa la corrección de código/OP en su lugar."
            )

        nueva_operaria = (nueva_operaria or '').strip() or None
        detalle_log = (
            f"id_pulido={id_pulido} | codigo={registro.codigo} | "
            f"OP={registro.orden_produccion} | lote={registro.lote} | "
            f"responsable={registro.responsable} | motivo={motivo.strip()}"
            + (f" | reasignado_a={nueva_operaria}" if nueva_operaria else "")
        )

        try:
            try:
                from backend.services.programacion_pulido_service import ProgramacionPulidoService
                ProgramacionPulidoService.revertir_a_programado(id_pulido, nueva_operaria)
            except Exception as err_prog:
                # Silencioso a propósito, mismo criterio que vincular_inicio:
                # un fallo acá no debe impedir cancelar la sesión real.
                logger.warning(f"⚠️ [PULIDO-CANCELAR] No se pudo revertir la tarjeta programada de {id_pulido}: {err_prog}")

            db.session.query(PncInyeccion).filter_by(id_inyeccion=id_pulido).delete()
            db.session.query(PncPulido).filter_by(id_pulido=id_pulido).delete()
            db.session.query(PncEnsamble).filter_by(id_ensamble=id_pulido).delete()
            db.session.query(BujeRevuelto).filter_by(id_pulido=id_pulido).delete()
            db.session.delete(registro)

            from backend.models.sql_models import OperacionLog
            db.session.add(OperacionLog(
                modulo="PULIDO_SUPERVISION",
                operario=autorizado_por,
                accion=f"Cancelar inicio equivocado ({id_pulido})",
                detalles=detalle_log,
            ))

            db.session.commit()
            logger.info(f"✅ [PULIDO-CANCELAR] Sesión {id_pulido} cancelada por {autorizado_por}. {detalle_log}")
            return {"id_pulido": id_pulido, "cancelado": True}
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ [PULIDO-CANCELAR] Error cancelando {id_pulido}: {e}")
            raise

    @staticmethod
    def corregir_codigo_op(id_pulido, nuevo_codigo, nueva_op, motivo, autorizado_por):
        """
        Nivel 2: corrige la referencia y/o la OP de un reporte que YA tiene
        avance real (cantidad_real/PNC/revueltos > 0) -- ese avance ya
        movió inventario (por_pulir/p_terminado) bajo el código viejo, así
        que hay que revertirlo ahí y aplicarlo bajo el código nuevo, no
        solo cambiar el texto.

        A propósito NO reutiliza ejecutar_persistencia_pulido completo: esa
        función recalcula duracion_segundos/tiempo_total_minutos combinando
        la fecha de HOY con las horas del payload (pensada para cuando la
        operaria reporta en el momento) -- reusarla para corregir un
        reporte de un día anterior movería silenciosamente sus métricas de
        tiempo. Esta función solo toca código/orden_produccion e
        inventario -- nada de horas, duración, PNC ni revueltos.

        NO reasigna las cubetas de DistribucionOpPedidos si cambia la OP
        (fuera de alcance v1): la cantidad ya distribuida FIFO bajo la OP
        vieja se queda ahí. Si eso importa para el caso puntual, hay que
        revisarlo a mano en Auditoría de OP.

        :raises ValueError: id_pulido inexistente, sin avance real (debería
            cancelarse por Nivel 1, no corregirse), o sin ningún cambio real.
        """
        if not motivo or not motivo.strip():
            raise ValueError("El motivo es obligatorio para corregir una sesión.")

        registro = ProduccionPulido.query.filter_by(id_pulido=id_pulido).first()
        if not registro:
            raise ValueError(f"No existe ninguna sesión de Pulido con id_pulido={id_pulido}")

        cantidad_real = float(registro.cantidad_real or 0)
        pnc_total = float(registro.pnc_inyeccion or 0) + float(registro.pnc_pulido or 0)
        rev_total = float(
            db.session.query(db.func.coalesce(db.func.sum(BujeRevuelto.cantidad), 0))
            .filter_by(id_pulido=id_pulido).scalar() or 0
        )

        if cantidad_real == 0 and pnc_total == 0 and rev_total == 0:
            raise ValueError(
                f"La sesión {id_pulido} no tiene avance real -- usa 'Cancelar inicio equivocado' en vez de corregirla."
            )

        codigo_viejo = registro.codigo
        op_vieja = registro.orden_produccion

        nuevo_codigo_norm = preservar_o_normalizar_prefijo(nuevo_codigo.strip()) if (nuevo_codigo or '').strip() else None
        nueva_op_final = nueva_op.strip() if (nueva_op or '').strip() else None

        cambia_codigo = bool(nuevo_codigo_norm) and nuevo_codigo_norm != codigo_viejo
        cambia_op = bool(nueva_op_final) and nueva_op_final != op_vieja

        if not cambia_codigo and not cambia_op:
            raise ValueError("No se envió ningún cambio real de código u OP.")

        try:
            if cambia_codigo:
                total_efecto = cantidad_real + pnc_total + rev_total
                es_prueba = PulidoService._es_prueba(op_vieja, id_pulido)

                if not es_prueba:
                    codigo_viejo_norm = preservar_o_normalizar_prefijo(codigo_viejo)
                    prod_viejo = db.session.query(Producto).filter(
                        (Producto.codigo_sistema == codigo_viejo_norm) | (Producto.id_codigo == codigo_viejo_norm)
                    ).first()
                    if prod_viejo:
                        prod_viejo.por_pulir = float(prod_viejo.por_pulir or 0) + total_efecto
                        p_terminado_revertido = float(prod_viejo.p_terminado or 0) - cantidad_real
                        if p_terminado_revertido < 0:
                            logger.warning(
                                f"⚠️ [PULIDO-CORRECCION] Al corregir {id_pulido}, revertir P.Terminado de "
                                f"{codigo_viejo} lo manda a negativo ({p_terminado_revertido}) -- probablemente "
                                f"ya se consumió aguas abajo. Se deja en 0."
                            )
                        prod_viejo.p_terminado = max(0, p_terminado_revertido)

                    prod_nuevo = db.session.query(Producto).filter(
                        (Producto.codigo_sistema == nuevo_codigo_norm) | (Producto.id_codigo == nuevo_codigo_norm)
                    ).first()
                    if prod_nuevo:
                        prod_nuevo.por_pulir = max(0, float(prod_nuevo.por_pulir or 0) - total_efecto)
                        prod_nuevo.p_terminado = float(prod_nuevo.p_terminado or 0) + cantidad_real

                codigo_pnc_nuevo = normalizar_codigo_sin_prefijo(nuevo_codigo_norm)
                db.session.query(PncInyeccion).filter_by(id_inyeccion=id_pulido).update({"id_codigo": codigo_pnc_nuevo})
                db.session.query(PncPulido).filter_by(id_pulido=id_pulido).update({"codigo": codigo_pnc_nuevo})
                db.session.query(PncEnsamble).filter_by(id_ensamble=id_pulido).update({"id_codigo": codigo_pnc_nuevo})

                registro.codigo = nuevo_codigo_norm

            if cambia_op:
                registro.orden_produccion = nueva_op_final

            db.session.add(PulidoOverride(
                id_pulido=id_pulido,
                tipo="CORRECCION_CODIGO_OP",
                operaria=registro.responsable,
                autorizado_por=autorizado_por,
                motivo=motivo.strip(),
                detalle=(
                    f"codigo: {codigo_viejo} -> {registro.codigo} | "
                    f"OP: {op_vieja} -> {registro.orden_produccion}"
                ),
            ))

            db.session.commit()
            logger.info(
                f"✅ [PULIDO-CORRECCION] Sesión {id_pulido} corregida por {autorizado_por}: "
                f"codigo {codigo_viejo}->{registro.codigo}, OP {op_vieja}->{registro.orden_produccion}"
            )
            return {
                "id_pulido": id_pulido,
                "codigo": registro.codigo,
                "orden_produccion": registro.orden_produccion,
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ [PULIDO-CORRECCION] Error corrigiendo {id_pulido}: {e}")
            raise

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
