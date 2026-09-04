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


class ProgramacionPulidoNoEncontradaError(Exception):
    """Se lanza cuando el id de una tarjeta de la cola no existe (ya la
    borraron desde otra pantalla, o el id llegó mal). Se traduce a 404."""
    def __init__(self, id_item):
        self.message = f"No existe la tarea programada #{id_item}"
        super().__init__(self.message)


class ProgramacionPulidoBloqueadaError(Exception):
    """
    Se lanza al intentar editar o borrar una tarjeta de la cola que ya dejó
    de ser solo un plan. Una tarjeta en EN_PROCESO ya está amarrada a una
    fila real de ProduccionPulido por `id_pulido` (la operaria le dio
    "Iniciar" y está reportando contra ella): cambiarle la OP o la
    referencia ahí dejaría el reporte en curso apuntando a otra cosa, y
    borrarla dejaría huérfana la fila de ejecución. FINALIZADO es historia
    ya cerrada. Se traduce a 409 en la ruta, no a 400: el payload puede ser
    perfectamente válido, lo que no se puede es aplicarlo en ese estado.
    """
    def __init__(self, estado):
        self.estado = estado
        self.message = (
            f"La tarea ya está en estado {estado} -- solo se pueden editar o eliminar "
            f"las que siguen en PROGRAMADO (la operaria todavía no las inició)."
        )
        super().__init__(self.message)


class ProgramacionPulidoService:
    """Programación (plan) diaria de Pulido: crear cola, listarla por
    operaria o completa, y sincronizarla con la ejecución real."""

    # ------------------------------------------------------------------
    # SALDO DISPONIBLE PARA PROGRAMAR
    # ------------------------------------------------------------------
    @staticmethod
    def obtener_saldo_para_programar(excluir_id=None) -> list:
        """
        Saldo por pulir por OP+referencia (PulidoService.get_saldo_por_op),
        con lo que YA tiene cola programada (estado PROGRAMADO/EN_PROCESO,
        de cualquier operaria) restado -- para que el ADMIN vea cuánto le
        queda realmente disponible antes de sobre-asignar entre varias
        operarias.

        `excluir_id` saca UNA tarjeta del conteo de "ya asignado". Es lo que
        necesita la edición (actualizar_item): la tarjeta que se está
        editando ya está contada dentro de su propio `ya_asignado`, así que
        sin excluirla, dejar la cantidad igual o incluso bajarla se
        advertiría a sí misma como sobre-asignación.
        """
        saldo = PulidoService.get_saldo_por_op()
        if not saldo:
            return []

        query_asignados = db.session.query(
            ProgramacionPulido.orden_produccion,
            ProgramacionPulido.codigo,
            db.func.sum(ProgramacionPulido.cantidad_objetivo)
        ).filter(
            ProgramacionPulido.estado.in_(['PROGRAMADO', 'EN_PROCESO'])
        )
        if excluir_id:
            query_asignados = query_asignados.filter(ProgramacionPulido.id != int(excluir_id))
        asignados = query_asignados.group_by(
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
    def crear_items(fecha_str, items, responsable_planta) -> dict:
        """
        items: [{operaria, orden_produccion, codigo, cantidad_objetivo, lote, observaciones}]

        Cada tarea trae su PROPIA operaria (pedido del usuario 2026-09-04:
        repartir la cola de hoy entre varias personas en un solo guardado,
        no una operaria por POST como antes). orden_prioridad NO llega del
        frontend: se calcula aquí, continuando la cola existente de cada
        operaria, en el orden en que sus tareas aparecen dentro de `items`.

        La validación de saldo es BLANDA a propósito (advertencia, no
        bloqueo): el ADMIN es quien decide con la info completa si de
        verdad quiere sobre-asignar (ej. corrigiendo un saldo que sabe que
        está mal) -- el bloqueo DURO real sigue viviendo donde ya vivía,
        en PulidoService.validar_saldo_op al momento de reportar.
        """
        if not items:
            raise ValueError('No se enviaron tareas para programar')

        fecha_obj = _parsear_fecha(fecha_str)

        operarias_en_items = {str(it.get('operaria') or '').strip() for it in items}
        operarias_en_items.discard('')
        if not operarias_en_items:
            raise ValueError('Falta indicar la operaria en al menos una tarea')

        # Próximo orden_prioridad libre por operaria, calculado UNA vez por
        # cada una (no una sola base global): así el mismo guardado puede
        # repartir tareas entre varias personas sin pisar el orden de nadie.
        siguiente_orden = {}
        for operaria in operarias_en_items:
            maximo = db.session.query(
                db.func.max(ProgramacionPulido.orden_prioridad)
            ).filter(
                ProgramacionPulido.operaria == operaria,
                ProgramacionPulido.fecha == fecha_obj,
            ).scalar()
            siguiente_orden[operaria] = int(maximo or 0) + 1

        saldo_map = {
            (str(f['orden_produccion'] or '').strip(), _normalizar_referencia(f['referencia'])): f['disponible']
            for f in ProgramacionPulidoService.obtener_saldo_para_programar()
        }

        creados = []
        advertencias = []
        try:
            for item in items:
                operaria = str(item.get('operaria') or '').strip()
                op = str(item.get('orden_produccion') or '').strip()
                codigo = str(item.get('codigo') or '').strip()
                cantidad = float(item.get('cantidad_objetivo') or 0)
                if not operaria or not op or not codigo or cantidad <= 0:
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
                    orden_prioridad=siguiente_orden[operaria],
                    estado='PROGRAMADO',
                    responsable_planta=responsable_planta,
                    observaciones=(item.get('observaciones') or '').strip() or None,
                )
                siguiente_orden[operaria] += 1
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

    @staticmethod
    def reordenar_cola(operaria, ids_en_orden) -> dict:
        """
        Fija orden_prioridad = 1..N para las tareas PROGRAMADO de `operaria`
        según el orden exacto de `ids_en_orden` (arrastrar y soltar en el
        tablero del ADMIN). Ids que no existan, no sean de esa operaria o ya
        no estén en PROGRAMADO simplemente se ignoran -- una tarjeta
        bloqueada no debería llegar aquí desde el frontend, pero se
        revalida por si el estado cambió justo entre que se cargó el panel
        y se soltó la tarjeta (ver ProgramacionPulidoBloqueadaError).
        """
        if not operaria or not ids_en_orden:
            return {'actualizados': 0}
        try:
            ids_int = []
            for i in ids_en_orden:
                try:
                    ids_int.append(int(i))
                except (TypeError, ValueError):
                    continue

            items = db.session.query(ProgramacionPulido).filter(
                ProgramacionPulido.operaria == operaria,
                ProgramacionPulido.estado == 'PROGRAMADO',
                ProgramacionPulido.id.in_(ids_int)
            ).all()
            por_id = {i.id: i for i in items}

            orden = 1
            actualizados = 0
            for id_ in ids_int:
                item = por_id.get(id_)
                if not item:
                    continue
                item.orden_prioridad = orden
                orden += 1
                actualizados += 1

            db.session.commit()
            return {'actualizados': actualizados}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en ProgramacionPulidoService.reordenar_cola: {e}")
            raise

    # ------------------------------------------------------------------
    # EDICIÓN Y BORRADO DE LA COLA
    # ------------------------------------------------------------------
    @staticmethod
    def _obtener_editable(id_item) -> ProgramacionPulido:
        """Trae la tarjeta y verifica que todavía sea SOLO un plan. Punto
        único de esa regla para editar y para borrar -- ver
        ProgramacionPulidoBloqueadaError para el porqué del estado."""
        try:
            item = db.session.get(ProgramacionPulido, int(id_item))
        except (TypeError, ValueError):
            item = None
        if not item:
            raise ProgramacionPulidoNoEncontradaError(id_item)
        if item.estado != 'PROGRAMADO':
            raise ProgramacionPulidoBloqueadaError(item.estado)
        return item

    @staticmethod
    def actualizar_item(id_item, data) -> dict:
        """
        Edita una tarjeta que sigue en PROGRAMADO. Actualización PARCIAL a
        propósito: solo se toca lo que venga en `data`, así una pantalla que
        mande únicamente `cantidad_objetivo` no borra en silencio el lote ni
        las observaciones.

        Reasignar de operaria manda la tarjeta al FINAL de la cola de la
        operaria destino (mismo día): dejarle el orden_prioridad viejo la
        metería en medio de una cola ya armada por otra persona, o
        empatada con una tarjeta existente.

        La validación de saldo es blanda, igual que en crear_items -- y aquí
        se calcula excluyendo esta misma tarjeta del "ya asignado", o
        editarla sin cambiarle la cantidad se advertiría a sí misma.
        """
        item = ProgramacionPulidoService._obtener_editable(id_item)
        data = data or {}

        if 'codigo' in data:
            codigo = str(data.get('codigo') or '').strip()
            if not codigo:
                raise ValueError('La referencia no puede quedar vacía')
            item.codigo = codigo

        if 'orden_produccion' in data:
            op = str(data.get('orden_produccion') or '').strip()
            if not op:
                raise ValueError('La OP no puede quedar vacía')
            item.orden_produccion = op

        if 'cantidad_objetivo' in data:
            try:
                cantidad = float(data.get('cantidad_objetivo') or 0)
            except (TypeError, ValueError):
                raise ValueError('Cantidad objetivo inválida')
            if cantidad <= 0:
                raise ValueError('La cantidad objetivo debe ser mayor a 0')
            item.cantidad_objetivo = cantidad

        if 'lote' in data:
            item.lote = str(data.get('lote') or '').strip() or None

        if 'observaciones' in data:
            item.observaciones = str(data.get('observaciones') or '').strip() or None

        if 'operaria' in data:
            operaria = str(data.get('operaria') or '').strip()
            if not operaria:
                raise ValueError('Falta indicar la operaria')
            if operaria != item.operaria:
                maximo = db.session.query(
                    db.func.max(ProgramacionPulido.orden_prioridad)
                ).filter(
                    ProgramacionPulido.operaria == operaria,
                    ProgramacionPulido.fecha == item.fecha,
                    ProgramacionPulido.id != item.id,
                ).scalar()
                item.operaria = operaria
                item.orden_prioridad = int(maximo or 0) + 1

        advertencias = []
        saldo_map = {
            (str(f['orden_produccion'] or '').strip(), _normalizar_referencia(f['referencia'])): f['disponible']
            for f in ProgramacionPulidoService.obtener_saldo_para_programar(excluir_id=item.id)
        }
        key = (str(item.orden_produccion or '').strip(), _normalizar_referencia(item.codigo))
        disponible = saldo_map.get(key)
        cantidad_final = float(item.cantidad_objetivo or 0)
        if disponible is not None and cantidad_final > disponible + 0.0001:
            advertencias.append(
                f"{item.orden_produccion} / {item.codigo}: quedaron {cantidad_final:g}, disponible real {disponible:g}"
            )

        try:
            db.session.commit()
            return {'item': ProgramacionPulidoService._serializar(item), 'advertencias': advertencias}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en ProgramacionPulidoService.actualizar_item({id_item}): {e}")
            raise

    @staticmethod
    def eliminar_item(id_item) -> dict:
        """Borra una tarjeta que sigue en PROGRAMADO (nunca una ya iniciada
        -- ver _obtener_editable). No renumera el resto de la cola: el orden
        que ve la operaria sale de ORDER BY orden_prioridad, así que un
        hueco en la numeración (1, 2, 4) no cambia en nada la secuencia."""
        item = ProgramacionPulidoService._obtener_editable(id_item)
        resumen = ProgramacionPulidoService._serializar(item)
        try:
            db.session.delete(item)
            db.session.commit()
            return {'eliminado': resumen}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en ProgramacionPulidoService.eliminar_item({id_item}): {e}")
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
