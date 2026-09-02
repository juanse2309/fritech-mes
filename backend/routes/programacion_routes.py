from flask import Blueprint, request, jsonify, session
import logging
import traceback

from backend.services.programacion_service import (
    ProgramacionService,
    ProgramacionNoEncontradaException,
    MaquinaOcupadaException,
)
from backend.utils.auth_middleware import require_login, require_role, ROL_ADMINS, ROL_JEFES, ROL_OPERARIOS

programacion_bp = Blueprint('programacion_bp', __name__)
logger = logging.getLogger(__name__)

ROLES_PLANTA = ROL_ADMINS + ROL_JEFES + ROL_OPERARIOS


# ====================================================================
# CATÁLOGO DE PLANTA
# ====================================================================

@programacion_bp.route('/api/obtener_maquinas', methods=['GET'])
@programacion_bp.route('/api/maquinas', methods=['GET'])
@require_login
def obtener_maquinas():
    """Retorna lista de máquinas activas desde db_maquinas (SQL-Native)."""
    try:
        return jsonify(ProgramacionService.obtener_maquinas_activas()), 200
    except Exception as e:
        logger.error(f"❌ Error obteniendo máquinas SQL: {e}")
        return jsonify([]), 200


@programacion_bp.route('/api/programacion/cavidades/<codigo>', methods=['GET'])
@require_login
def obtener_cavidades_referencia(codigo):
    """Moldes y cavidades disponibles para una referencia, según rel_producto_molde
    (catálogo real de planta) -- ver docstring del servicio."""
    try:
        return jsonify({'success': True, 'moldes': ProgramacionService.obtener_cavidades_referencia(codigo)}), 200
    except Exception as e:
        logger.error(f"❌ Error obteniendo cavidades para {codigo!r}: {e}")
        return jsonify({'success': False, 'moldes': []}), 200


@programacion_bp.route('/api/programacion/moldes', methods=['GET'])
@require_login
def listar_moldes_disponibles():
    """Catálogo completo de códigos de molde reales, para el selector con
    autocompletar del campo "Molde" en Programación -- ver docstring del
    servicio (ProgramacionService.listar_moldes_disponibles)."""
    try:
        return jsonify({'success': True, 'moldes': ProgramacionService.listar_moldes_disponibles()}), 200
    except Exception as e:
        logger.error(f"❌ Error listando catálogo de moldes: {e}")
        return jsonify({'success': False, 'moldes': []}), 200


# ====================================================================
# MES (MANUFACTURING EXECUTION SYSTEM) - AGENDAMIENTO INYECCIÓN
# ====================================================================

@programacion_bp.route('/api/mes/programar', methods=['POST'])
@require_role(ROLES_PLANTA)
def mes_programar():
    """Fase 1: el Jefe de Planta pone en cola uno o varios productos para una máquina."""
    data = request.json or {}
    try:
        resultado = ProgramacionService.crear_programacion(
            maquina=data.get('maquina'),
            fecha_str=data.get('fecha'),
            productos=data.get('productos', []),
            responsable=session.get('user', 'SISTEMA'),
            observaciones=data.get('observaciones', ''),
            # Texto, no int(): el código real de molde no es numérico -- ver
            # migrate_molde_texto.py.
            molde_capacidad=str(data.get('molde') or '').strip() or None,
        )
        return jsonify({'success': True, **resultado}), 200
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error en mes_programar SQL: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@programacion_bp.route('/api/mes/retomar', methods=['POST'])
@require_role(ROLES_PLANTA)
def mes_retomar():
    """Fase 1c: repite la última programación conocida para una máquina, varias, o todas ('Retomar General')."""
    data = request.json or {}
    try:
        resultado = ProgramacionService.retomar_programacion(
            maquinas=data.get('maquinas', []),
            fecha_str=data.get('fecha'),
            responsable=session.get('user', 'SISTEMA'),
        )
        return jsonify({'success': True, **resultado}), 200
    except Exception as e:
        logger.error(f"Error en mes_retomar SQL: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@programacion_bp.route('/api/mes/cancelar/<id_target>', methods=['POST'])
@require_role(ROLES_PLANTA)
def mes_cancelar(id_target):
    """
    Fase 1b: libera máquina cancelando por ID (Programación o Producción
    activa). Sin convertidor <int:...> a propósito: el trabajo activo se
    identifica por id_inyeccion (string tipo 'INY-62DF93AD'), no por PK
    entero -- ver ProgramacionService.cancelar para el porqué. Con
    <int:...> este endpoint devolvía 404 antes de ejecutar nada cada vez
    que 'Liberar Máquina' intentaba soltar el trabajo en curso.
    """
    try:
        ProgramacionService.cancelar(id_target)
        return jsonify({'success': True}), 200
    except ProgramacionNoEncontradaException as e:
        return jsonify({'success': False, 'error': e.message}), 404
    except Exception as e:
        logger.error(f"Error en mes_cancelar SQL (Polimórfico): {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@programacion_bp.route('/api/mes/programaciones/<maquina>', methods=['GET'])
@require_role(ROLES_PLANTA)
def mes_get_programaciones(maquina):
    """Obtiene programaciones activas desde SQL (db_programacion), para la fecha ?fecha=YYYY-MM-DD (hoy por defecto)."""
    try:
        return jsonify(ProgramacionService.obtener_programaciones_activas(maquina, request.args.get('fecha'))), 200
    except Exception as e:
        logger.error(f"❌ Error en mes_get_programaciones SQL: {e}")
        return jsonify([]), 200


@programacion_bp.route('/api/mes/dashboard', methods=['GET'])
@require_role(ROLES_PLANTA)
def mes_dashboard():
    """Estado completo de las máquinas (MES), para la fecha ?fecha=YYYY-MM-DD (hoy por defecto)."""
    try:
        return jsonify({'maquinas': ProgramacionService.obtener_dashboard_mes(request.args.get('fecha'))}), 200
    except Exception as e:
        logger.error(f"❌ Error en mes_dashboard SQL: {e}")
        return jsonify({'maquinas': []}), 200


@programacion_bp.route('/api/mes/pendientes_calidad', methods=['GET'])
@require_role(ROLES_PLANTA)
def mes_get_pendientes_calidad():
    """Obtiene registros de inyección en estado PENDIENTE_CALIDAD."""
    try:
        return jsonify(ProgramacionService.obtener_pendientes_calidad()), 200
    except Exception as e:
        logger.error(f"Error en mes_get_pendientes_calidad SQL: {e}\n{traceback.format_exc()}")
        return jsonify([]), 200


@programacion_bp.route('/api/mes/programacion/<id_prog>/productos', methods=['GET'])
@require_role(ROLES_PLANTA)
def mes_get_productos_programacion(id_prog):
    """Obtiene productos vinculados a una programación."""
    try:
        productos = ProgramacionService.obtener_productos_de_programacion(id_prog)
        return jsonify({'success': True, 'productos': productos}), 200
    except ProgramacionNoEncontradaException as e:
        return jsonify({'success': False, 'error': e.message}), 404
    except Exception as e:
        logger.error(f"Error fetching productos de programacion SQL: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@programacion_bp.route('/api/mes/status/<maquina>', methods=['GET'])
@require_role(ROLES_PLANTA)
def mes_get_status_maquina(maquina):
    """Obtiene el estado actual de una máquina (Activo, Programado o Libre)."""
    try:
        return jsonify(ProgramacionService.obtener_status_maquina(maquina)), 200
    except Exception as e:
        error_detail = traceback.format_exc()
        logger.error(f"Error en status_maquina: {e}\n{error_detail}")
        return jsonify({
            'status': 'error', 'success': False,
            'message': str(e),
            'traceback': error_detail
        }), 500


@programacion_bp.route('/api/mes/iniciar', methods=['POST'])
@require_role(ROLES_PLANTA)
def mes_iniciar():
    """Fase 2a: el operario inicia físicamente la máquina (Batch de todo lo programado)."""
    data = request.json or {}
    try:
        resultado = ProgramacionService.iniciar_produccion_batch(
            id_programacion=data.get('id_programacion'),
            responsable_raw=data.get('responsable') or data.get('operario'),
        )
        return jsonify({'success': True, **resultado}), 200
    except ProgramacionNoEncontradaException as e:
        return jsonify({'success': False, 'error': e.message}), 404
    except MaquinaOcupadaException as e:
        return jsonify({'success': False, 'error': e.message}), 400
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"❌ Error en mes_iniciar Batch SQL: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
