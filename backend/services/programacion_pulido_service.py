"""
programacion_pulido_service.py
===============================
Programación diaria de Pulido (plan 2026-09-02): el ADMIN arma, por
operaria, la cola de qué pulir hoy y en qué orden. Separado de
PulidoService (que cubre la ejecución real / analítica) -- este servicio
solo cubre el plan (ProgramacionPulido) y su enganche liviano con la
ejecución real (ProduccionPulido), igual que ProgramacionService cubre el
agendamiento de Inyección separado de InyeccionService.
"""
import logging
from datetime import datetime
from backend.core.sql_database import db
from backend.models.sql_models import ProgramacionPulido
from backend.services.pulido_service import PulidoService
from backend.utils.time_utils import get_colombia_time

logger = logging.getLogger(__name__)


def _normalizar_referencia(referencia) -> str:
    """Mismo criterio que PulidoService._normalizar_referencia_bind: un
    código puramente numérico es de la división FR- por defecto."""
    ref = str(referencia or '').strip().upper()
    return f"FR-{ref}" if ref.isdigit() else ref


def _parsear_fecha(fecha_str):
    if fecha_str:
        try:
            return datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    return get_colombia_time().date()


class ProgramacionPulidoService:
    """Programación (plan) diaria de Pulido: crear cola, listarla por
    operaria o completa, y sincronizarla con la ejecución real."""

    # ------------------------------------------------------------------
    # SALDO DISPONIBLE PARA PROGRAMAR
    # ------------------------------------------------------------------
    @staticmethod
    def obtener_saldo_para_programar() -> list:
        """
        Saldo por pulir por OP+referencia (PulidoService.get_saldo_por_op),
        con lo que YA tiene cola programada (estado PROGRAMADO/EN_PROCESO,
        de cualquier operaria) restado -- para que el ADMIN vea cuánto le
        queda realmente disponible antes de sobre-asignar entre varias
        operarias.
        """
        saldo = PulidoService.get_saldo_por_op()
        if not saldo:
            return []

        asignados = db.session.query(
            ProgramacionPulido.orden_produccion,
            ProgramacionPulido.codigo,
            db.func.sum(ProgramacionPulido.cantidad_objetivo)
        ).filter(
            ProgramacionPulido.estado.in_(['PROGRAMADO', 'EN_PROCESO'])
        ).group_by(
            ProgramacionPulido.orden_produccion, ProgramacionPulido.codigo
        ).all()

        mapa_asignado = {}
        for op, cod, cant in asignados:
            key = (str(op or '').strip(), _normalizar_referencia(cod))
            mapa_asignado[key] = mapa_asignado.get(key, 0) + float(cant or 0)

        resultado = []
        for fila in saldo:
            key = (str(fila['orden_produccion'] or '').strip(), _normalizar_referencia(fila['referencia']))
            ya_asignado = mapa_asignado.get(key, 0)
            resultado.append({
                **fila,
                'ya_asignado': ya_asignado,
                'disponible': fila['saldo'] - ya_asignado,
            })
        return resultado

    # ------------------------------------------------------------------
    # CREACIÓN DE LA COLA
    # ------------------------------------------------------------------
    @staticmethod
    def crear_items(fecha_str, operaria, items, responsable_planta) -> dict:
        """
        items: [{orden_produccion, codigo, cantidad_objetivo, orden_prioridad, observaciones}]

        La validación de saldo es BLANDA a propósito (advertencia, no
        bloqueo): el ADMIN es quien decide con la info completa si de
        verdad quiere sobre-asignar (ej. corrigiendo un saldo que sabe que
        está mal) -- el bloqueo DURO real sigue viviendo donde ya vivía,
        en PulidoService.validar_saldo_op al momento de reportar.
        """
        if not operaria or not str(operaria).strip():
            raise ValueError('Falta indicar la operaria a la que se le asigna la programación')
        if not items:
            raise ValueError('No se enviaron tareas para programar')

        fecha_obj = _parsear_fecha(fecha_str)

        saldo_map = {
            (str(f['orden_produccion'] or '').strip(), _normalizar_referencia(f['referencia'])): f['disponible']
            for f in ProgramacionPulidoService.obtener_saldo_para_programar()
        }

        creados = []
        advertencias = []
        try:
            for item in items:
                op = str(item.get('orden_produccion') or '').strip()
                codigo = str(item.get('codigo') or '').strip()
                cantidad = float(item.get('cantidad_objetivo') or 0)
                if not op or not codigo or cantidad <= 0:
                    continue

                key = (op, _normalizar_referencia(codigo))
                disponible = saldo_map.get(key)
                if disponible is not None and cantidad > disponible + 0.0001:
                    advertencias.append(
                        f"{op} / {codigo}: se asignaron {cantidad:g}, disponible real {disponible:g}"
                    )
                if disponible is not None:
                    # Descontar en memoria: si el mismo POST trae varias
                    # tareas de la misma OP+referencia (para operarias
                    # distintas), la segunda también debe ver el saldo ya
                    # comprometido por la primera.
                    saldo_map[key] = disponible - cantidad

                nuevo = ProgramacionPulido(
                    fecha=fecha_obj,
                    orden_produccion=op,
                    codigo=codigo,
                    lote=(item.get('lote') or '').strip() or None,
                    cantidad_objetivo=cantidad,
                    operaria=operaria,
                    orden_prioridad=int(item.get('orden_prioridad') or 1),
                    estado='PROGRAMADO',
                    responsable_planta=responsable_planta,
                    observaciones=(item.get('observaciones') or '').strip() or None,
                )
                db.session.add(nuevo)
                db.session.flush()
                creados.append(nuevo.id)

            db.session.commit()
            return {'creados': creados, 'advertencias': advertencias}
        except Exception as e:
            db.session.rollback()
            if not isinstance(e, ValueError):
                logger.error(f"Error en ProgramacionPulidoService.crear_items: {e}")
            raise

    # ------------------------------------------------------------------
    # LECTURA DE LA COLA
    # ------------------------------------------------------------------
    @staticmethod
    def _serializar(item: ProgramacionPulido) -> dict:
        return {
            'id': item.id,
            'fecha': item.fecha.strftime('%Y-%m-%d') if item.fecha else None,
            'orden_produccion': item.orden_produccion,
            'codigo': item.codigo,
            'lote': item.lote or '',
            'cantidad_objetivo': float(item.cantidad_objetivo or 0),
            'operaria': item.operaria,
            'orden_prioridad': item.orden_prioridad,
            'estado': item.estado,
            'responsable_planta': item.responsable_planta,
            'observaciones': item.observaciones or '',
            'id_pulido': item.id_pulido,
        }

    @staticmethod
    def obtener_cola_operaria(operaria, fecha_str=None) -> list:
        """Cola ordenada del día para UNA operaria -- fuente de "Modo
        Programado". Solo tareas todavía abiertas (PROGRAMADO/EN_PROCESO);
        las FINALIZADAS no vuelven a aparecer como pendientes."""
        if not operaria:
            return []
        fecha_obj = _parsear_fecha(fecha_str)

        items = db.session.query(ProgramacionPulido).filter(
            ProgramacionPulido.operaria == operaria,
            ProgramacionPulido.fecha == fecha_obj,
            ProgramacionPulido.estado.in_(['PROGRAMADO', 'EN_PROCESO'])
        ).order_by(ProgramacionPulido.orden_prioridad.asc(), ProgramacionPulido.id.asc()).all()

        return [ProgramacionPulidoService._serializar(i) for i in items]

    @staticmethod
    def obtener_cola_admin(fecha_str=None) -> dict:
        """Toda la programación del día, agrupada por operaria -- fuente
        del panel de Programación del ADMIN (incluye ya finalizadas, para
        que el ADMIN vea el avance real de cada una, no solo lo pendiente)."""
        fecha_obj = _parsear_fecha(fecha_str)

        items = db.session.query(ProgramacionPulido).filter(
            ProgramacionPulido.fecha == fecha_obj
        ).order_by(
            ProgramacionPulido.operaria.asc(),
            ProgramacionPulido.orden_prioridad.asc(),
            ProgramacionPulido.id.asc()
        ).all()

        agrupado: dict = {}
        for i in items:
            agrupado.setdefault(i.operaria, []).append(ProgramacionPulidoService._serializar(i))
        return agrupado

    # ------------------------------------------------------------------
    # ENGANCHE CON LA EJECUCIÓN REAL (llamado desde
    # pulido_routes._ejecutar_persistencia_pulido) -- es la ejecución la
    # que le avisa a la programación que arrancó/terminó, nunca al revés.
    # Ambos métodos son deliberadamente silenciosos ante datos raros (item
    # ya cerrado, id inválido, etc.): un fallo de sincronización de la
    # tarjeta programada NUNCA debe tumbar el guardado real del reporte.
    # ------------------------------------------------------------------
    @staticmethod
    def vincular_inicio(id_item, id_pulido) -> None:
        """Al iniciar una tarjeta programada, la vincula con el id_pulido
        recién creado en ProduccionPulido y la pasa a EN_PROCESO."""
        if not id_item or not id_pulido:
            return
        try:
            item = db.session.get(ProgramacionPulido, int(id_item))
        except (TypeError, ValueError):
            return
        if not item or item.estado not in ('PROGRAMADO', 'EN_PROCESO'):
            return
        item.id_pulido = id_pulido
        item.estado = 'EN_PROCESO'

    @staticmethod
    def marcar_finalizada_si_corresponde(id_pulido, estado_produccion) -> None:
        """Si el registro de ProduccionPulido que se acaba de guardar está
        vinculado a una tarjeta programada y su estado ya es terminal
        (FINALIZADO/APROBADO), cierra también la tarjeta programada."""
        if not id_pulido or str(estado_produccion or '').upper() not in ('FINALIZADO', 'APROBADO'):
            return
        item = db.session.query(ProgramacionPulido).filter(
            ProgramacionPulido.id_pulido == id_pulido,
            ProgramacionPulido.estado != 'FINALIZADO'
        ).first()
        if item:
            item.estado = 'FINALIZADO'
