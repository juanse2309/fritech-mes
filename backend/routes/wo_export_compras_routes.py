"""
wo_export_compras_routes.py
============================
Rutas de exportación de Órdenes de Compra a World Office (plan
2026-09-15). Blueprint delgado, calcado de wo_export_routes.py (OP):
toda la lógica vive en wo_export_compras_service.py.
"""
from flask import Blueprint, request, current_app
import logging

from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.core import task_runner
from backend.utils.auth_middleware import require_role, _obtener_usuario_activo, ROL_ADMINS
from backend.services.wo_export_compras_service import (
    WoExportComprasService, WoExportComprasException, ExportacionDeshabilitadaException,
)
from backend.services.oc_numerador_service import OcNumeradorService

wo_export_compras_bp = Blueprint('wo_export_compras', __name__)
logger = logging.getLogger(__name__)


@wo_export_compras_bp.route('/api/wo/compras/numerador/diagnostico', methods=['GET'])
@require_role(ROL_ADMINS)
def numerador_diagnostico():
    """Solo lectura: piso calculado desde db_oc_wo_staging y
    db_ordenes_compra, y el siguiente numero_oc que se asignaría. No
    reserva nada -- mismo endpoint que ya existe para OP."""
    try:
        resultado = OcNumeradorService.diagnostico()
        return api_success(data=resultado)
    except Exception as e:
        logger.error(f"❌ Error en diagnóstico del numerador de OC: {e}")
        return api_error("Error interno consultando el diagnóstico del numerador", status_code=500)


@wo_export_compras_bp.route('/api/wo/compras/exportables', methods=['GET'])
@require_role(ROL_ADMINS)
def listar_exportables():
    """Lista de OC con su conteo de líneas, para la vista de descarga de
    Diego. Incluye si la exportación sigue deshabilitada."""
    try:
        resultado = WoExportComprasService.listar_ordenes_exportables()
        return api_success(data={
            'ordenes': resultado,
            'exportacion_habilitada': WoExportComprasService.esta_habilitado(),
        })
    except Exception as e:
        logger.error(f"❌ Error listando OC exportables: {e}")
        return api_error(str(e), status_code=500)


@wo_export_compras_bp.route('/api/wo/compras/habilitar', methods=['PATCH'])
@require_role(ROL_ADMINS)
def habilitar_exportacion():
    """Prende/apaga el guard de exportación (ver esta_habilitado). Los
    valores fijos de la plantilla ya se confirmaron contra una carga de
    prueba real en WO -- esto solo registra la decisión de activarlo."""
    data = request.get_json() or {}
    try:
        WoExportComprasService.fijar_habilitado(bool(data.get('habilitado')))
        return api_success(data={'exportacion_habilitada': WoExportComprasService.esta_habilitado()})
    except Exception as e:
        logger.error(f"❌ Error cambiando el flag de exportación de Compras a WO: {e}")
        return api_error("Error interno cambiando el flag de exportación", status_code=500)


@wo_export_compras_bp.route('/api/wo/compras/preview', methods=['POST'])
@require_role(ROL_ADMINS)
def preview_exportacion():
    """Vista previa del contenido SIN marcar nada como exportado -- rollback
    explícito al terminar."""
    data = request.get_json() or {}
    numeros = data.get('numeros_oc') or []
    if not numeros:
        return api_error("Debes indicar al menos una OC", status_code=400)

    try:
        df, meta = WoExportComprasService.construir_dataset(numeros)
        muestra = df.head(30).astype(str).to_dict(orient='records')
        return api_success(data={
            'columnas': list(df.columns),
            'filas': muestra,
            'total_filas': len(df),
            'meta': meta,
        })
    except WoExportComprasException as e:
        return api_error(str(e), status_code=409, code="SIN_LINEAS_EXPORTABLES")
    except Exception as e:
        logger.error(f"❌ Error en preview de exportación de Compras a WO: {e}")
        return api_error(str(e), status_code=500)
    finally:
        db.session.rollback()


@wo_export_compras_bp.route('/api/wo/compras/exportar', methods=['POST'])
@require_role(ROL_ADMINS)
def exportar_oc():
    """Genera el archivo en background y devuelve 202 con el task_id
    (descarga después contra /api/tasks/download/<task_id>)."""
    data = request.get_json() or {}
    numeros = data.get('numeros_oc') or []
    formato = data.get('formato')

    if not numeros:
        return api_error("Debes indicar al menos una OC", status_code=400)

    if not WoExportComprasService.esta_habilitado():
        return api_error(
            "La exportación de Compras a World Office está desactivada manualmente. "
            "Actívala desde la pestaña 'Órdenes de Compra'.",
            status_code=409, code="EXPORTACION_DESHABILITADA"
        )

    try:
        usuario = _obtener_usuario_activo()
        task_id = task_runner.create_task()
        task_runner.run_in_background(
            task_id, current_app._get_current_object(),
            WoExportComprasService.generar_task, numeros, usuario, formato,
        )
        return api_success(data={'task_id': task_id}, status_code=202)
    except ExportacionDeshabilitadaException as e:
        return api_error(str(e), status_code=409, code="EXPORTACION_DESHABILITADA")
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error lanzando la exportación de Compras a WO: {e}")
        return api_error(str(e), status_code=500)
