"""
wo_export_routes.py
====================
Rutas del numerador de OP y (en fases siguientes de este mismo proyecto)
de la exportación a texto plano para World Office. Blueprint delgado:
solo parsea request/maneja códigos HTTP -- toda la lógica vive en los
servicios (backend/services/op_numerador_service.py, backend/services/
wo_export_service.py cuando exista).
"""
from flask import Blueprint, request, current_app
import logging

from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.core import task_runner
from backend.utils.auth_middleware import require_role, _obtener_usuario_activo, ROL_ADMINS, ROL_JEFES
from backend.services.op_numerador_service import OpNumeradorService, OpNumeradorException
from backend.services.wo_export_service import (
    WoExportService, WoExportException, ExportacionDeshabilitadaException,
)

wo_export_bp = Blueprint('wo_export', __name__)
logger = logging.getLogger(__name__)

# Rol acordado en la reunión para la vista de descarga: administración,
# jefes de planta y auxiliar de inventario.
ROLES_WO_EXPORT = ROL_ADMINS + ROL_JEFES + ['AUXILIAR INVENTARIO']


@wo_export_bp.route('/api/wo/op/numerador/diagnostico', methods=['GET'])
@require_role(ROLES_WO_EXPORT)
def numerador_diagnostico():
    """Solo lectura: piso calculado desde db_op_wo_staging y db_op_generadas,
    y el siguiente número que se asignaría a cada ámbito. No reserva nada."""
    try:
        resultado = OpNumeradorService.diagnostico()
        return api_success(data=resultado)
    except Exception as e:
        logger.error(f"❌ Error en diagnóstico del numerador de OP: {e}")
        return api_error("Error interno consultando el diagnóstico del numerador", status_code=500)


@wo_export_bp.route('/api/wo/op/exportables', methods=['GET'])
@require_role(ROLES_WO_EXPORT)
def listar_exportables():
    """Lista de OP con su conteo de líneas, para la vista de descarga."""
    try:
        resultado = WoExportService.listar_ops_exportables(
            fecha_desde=request.args.get('fecha_desde'),
            fecha_hasta=request.args.get('fecha_hasta'),
            ambito=request.args.get('ambito'),
        )
        return api_success(data={
            'ops': resultado,
            'exportacion_habilitada': WoExportService.esta_habilitado(),
        })
    except Exception as e:
        logger.error(f"❌ Error listando OP exportables: {e}")
        return api_error(str(e), status_code=500)


@wo_export_bp.route('/api/wo/op/preview', methods=['POST'])
@require_role(ROLES_WO_EXPORT)
def preview_exportacion():
    """
    Vista previa del contenido SIN marcar nada como exportado ni activar el
    flag: rollback explícito al terminar para que ni un efecto colateral
    accidental del ORM quede persistido.
    """
    data = request.get_json() or {}
    numeros = data.get('numeros_op') or []
    if not numeros:
        return api_error("Debes indicar al menos una OP", status_code=400)

    try:
        df, meta = WoExportService.construir_dataset(numeros)
        muestra = df.head(30).astype(str).to_dict(orient='records')
        return api_success(data={
            'columnas': list(df.columns),
            'filas': muestra,
            'total_filas': len(df),
            'meta': meta,
        })
    except WoExportException as e:
        return api_error(str(e), status_code=409, code="SIN_LINEAS_EXPORTABLES")
    except Exception as e:
        logger.error(f"❌ Error en preview de exportación WO: {e}")
        return api_error(str(e), status_code=500)
    finally:
        db.session.rollback()


@wo_export_bp.route('/api/wo/op/exportar', methods=['POST'])
@require_role(ROLES_WO_EXPORT)
def exportar_op():
    """
    Genera el/los archivo(s) en background y devuelve 202 con el task_id.
    La descarga se hace después contra /api/tasks/download/<task_id>, que ya
    es genérico -- no hace falta plomería nueva.
    """
    data = request.get_json() or {}
    numeros = data.get('numeros_op') or []
    formato = data.get('formato')

    if not numeros:
        return api_error("Debes indicar al menos una OP", status_code=400)

    if not WoExportService.esta_habilitado():
        return api_error(
            "La exportación a World Office está deshabilitada. Se activa cuando los "
            "valores de la plantilla estén confirmados contra WO.",
            status_code=409, code="EXPORTACION_DESHABILITADA"
        )

    try:
        usuario = _obtener_usuario_activo()
        task_id = task_runner.create_task()
        task_runner.run_in_background(
            task_id, current_app._get_current_object(),
            WoExportService.generar_task, numeros, usuario, formato,
        )
        return api_success(data={'task_id': task_id}, status_code=202)
    except ExportacionDeshabilitadaException as e:
        return api_error(str(e), status_code=409, code="EXPORTACION_DESHABILITADA")
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error lanzando la exportación WO: {e}")
        return api_error(str(e), status_code=500)


@wo_export_bp.route('/api/wo/op/<numero_op>/anular', methods=['POST'])
@require_role(ROL_ADMINS)
def anular_op(numero_op):
    """Anular una OP libera su clave (fecha+ámbito+máquina) para que se pueda
    generar otra, conservando el número en el historial."""
    data = request.get_json() or {}
    motivo = (data.get('motivo') or '').strip()
    if not motivo:
        return api_error("Anular una OP requiere indicar un motivo", status_code=400)

    try:
        usuario = _obtener_usuario_activo()
        op = OpNumeradorService.anular(numero_op, motivo, usuario)
        db.session.commit()
        return api_success(data={'numero_op': op.numero_op, 'estado': op.estado})
    except OpNumeradorException as e:
        db.session.rollback()
        return api_error(str(e), status_code=404)
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error anulando OP {numero_op}: {e}")
        return api_error(str(e), status_code=500)
