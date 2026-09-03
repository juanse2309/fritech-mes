from flask import Blueprint, request, session
import logging

from backend.core.responses import api_success, api_error
from backend.services.programacion_pulido_service import (
    ProgramacionPulidoService,
    ProgramacionPulidoBloqueadaError,
    ProgramacionPulidoNoEncontradaError,
)
from backend.utils.auth_middleware import require_login, require_role, ROL_ADMINS

logger = logging.getLogger(__name__)
programacion_pulido_bp = Blueprint('programacion_pulido_bp', __name__)


# ====================================================================
# PROGRAMACIÓN DE PULIDO (plan 2026-09-02): pantalla de ADMIN para armar
# la cola diaria de cada operaria -- restringido a ADMIN (más estrecho que
# Supervisión, que también deja pasar a JEFE PULIDO, por decisión explícita
# del usuario).
# ====================================================================

@programacion_pulido_bp.route('/api/pulido/programacion/saldo', methods=['GET'])
@require_role(ROL_ADMINS)
def obtener_saldo_programacion():
    """Saldo por pulir por OP+referencia, con lo ya asignado en la cola
    de programación restado -- para armar la cola sin sobre-asignar."""
    try:
        return api_success(data={'saldo': ProgramacionPulidoService.obtener_saldo_para_programar()})
    except Exception as e:
        logger.error(f"❌ Error obteniendo saldo para programación de Pulido: {e}")
        return api_error(str(e), status_code=500)


@programacion_pulido_bp.route('/api/pulido/programacion', methods=['POST'])
@require_role(ROL_ADMINS)
def crear_programacion_pulido():
    """Agrega una o varias tareas a la cola de una operaria para una fecha."""
    data = request.get_json() or {}
    try:
        resultado = ProgramacionPulidoService.crear_items(
            fecha_str=data.get('fecha'),
            operaria=data.get('operaria'),
            items=data.get('items', []),
            responsable_planta=session.get('user', 'ADMIN'),
        )
        return api_success(data=resultado, status_code=201)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error creando programación de Pulido: {e}")
        return api_error(str(e), status_code=500)


@programacion_pulido_bp.route('/api/pulido/programacion/<int:id_item>', methods=['PUT'])
@require_role(ROL_ADMINS)
def actualizar_programacion_pulido(id_item):
    """Edita una tarea de la cola (referencia/OP/lote/cantidad/observaciones/operaria).
    Solo mientras siga en PROGRAMADO -- ver ProgramacionPulidoBloqueadaError."""
    data = request.get_json() or {}
    try:
        return api_success(data=ProgramacionPulidoService.actualizar_item(id_item, data))
    except ProgramacionPulidoNoEncontradaError as e:
        return api_error(e.message, status_code=404)
    except ProgramacionPulidoBloqueadaError as e:
        return api_error(e.message, status_code=409)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error actualizando programación de Pulido #{id_item}: {e}")
        return api_error(str(e), status_code=500)


@programacion_pulido_bp.route('/api/pulido/programacion/<int:id_item>', methods=['DELETE'])
@require_role(ROL_ADMINS)
def eliminar_programacion_pulido(id_item):
    """Quita una tarea de la cola. Solo mientras siga en PROGRAMADO."""
    try:
        return api_success(data=ProgramacionPulidoService.eliminar_item(id_item))
    except ProgramacionPulidoNoEncontradaError as e:
        return api_error(e.message, status_code=404)
    except ProgramacionPulidoBloqueadaError as e:
        return api_error(e.message, status_code=409)
    except Exception as e:
        logger.error(f"❌ Error eliminando programación de Pulido #{id_item}: {e}")
        return api_error(str(e), status_code=500)


@programacion_pulido_bp.route('/api/pulido/programacion/admin', methods=['GET'])
@require_role(ROL_ADMINS)
def obtener_programacion_admin():
    """Toda la cola del día, agrupada por operaria -- vista del ADMIN."""
    try:
        fecha = request.args.get('fecha')
        return api_success(data={'operarias': ProgramacionPulidoService.obtener_cola_admin(fecha)})
    except Exception as e:
        logger.error(f"❌ Error listando programación de Pulido (admin): {e}")
        return api_error(str(e), status_code=500)


@programacion_pulido_bp.route('/api/pulido/programacion/cola', methods=['GET'])
@require_login
def obtener_cola_programacion():
    """Cola ordenada del día para una operaria -- fuente de 'Modo Programado'."""
    try:
        operaria = request.args.get('responsable')
        fecha = request.args.get('fecha')
        if not operaria:
            return api_error("Falta responsable", status_code=400)
        return api_success(data={'tareas': ProgramacionPulidoService.obtener_cola_operaria(operaria, fecha)})
    except Exception as e:
        logger.error(f"❌ Error obteniendo cola programada de Pulido: {e}")
        return api_error(str(e), status_code=500)
