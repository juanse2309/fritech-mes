from flask import Blueprint, request, jsonify, current_app
from backend.utils.auth_middleware import require_role, ROL_ADMINS
from backend.services.facturacion_service import FacturacionService, FacturacionDatosInvalidosException
from backend.services.pedidos_service import PedidosService
from backend.core.responses import api_success, api_error
from backend.core import task_runner
import pandas as pd
import io
import os
import tempfile
from datetime import datetime
import logging
from backend.core.sql_database import db

facturacion_bp = Blueprint('facturacion_bp', __name__)
logger = logging.getLogger(__name__)


@facturacion_bp.route('/api/facturacion/pedidos-pendientes', methods=['GET'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO', 'COMERCIAL FRIMETALS'])
def obtener_pedidos_pendientes():
    """Pedidos exportables a WO -- ver FacturacionService.listar_pedidos_exportables."""
    try:
        resultado = FacturacionService.listar_pedidos_exportables(es_exportacion=False)
        return jsonify({'success': True, 'pedidos': resultado})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error en obtener_pedidos_pendientes SQL: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@facturacion_bp.route('/api/facturacion/pedidos-exportados-sin-confirmar', methods=['GET'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def obtener_pedidos_exportados_sin_confirmar():
    """
    Reconciliación: pedidos EXPORTADO_WO cuyo documento nunca volvió
    sincronizado desde World Office (ver PedidosService.detectar_exportados_sin_confirmar_wo).
    """
    try:
        pendientes = PedidosService.detectar_exportados_sin_confirmar_wo(db.session)
        return jsonify({'success': True, 'pedidos': pendientes})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error en obtener_pedidos_exportados_sin_confirmar: {e}")
        return jsonify({'success': False, 'error': 'No fue posible obtener la reconciliación de pedidos exportados.'}), 500

def _generar_excel_wo_task(task_id, ids_filter, consecutivo_inicial):
    """
    Trabajo de fondo de exportar_world_office: genera el DataFrame (que ya
    persiste el consecutivo WO y el estado EXPORTADO_WO vía FacturacionService.procesar_datos_wo)
    y arma el .xlsx fuera del hilo HTTP. Corre dentro del app_context que le
    da task_runner.run_in_background -- db.session depende de ese contexto.

    El conteo de items actualizados (antes viajaba en el header HTTP
    X-Pedidos-Actualizados de la respuesta síncrona) ahora se expone como
    result_meta.actualizados en /api/tasks/status/<task_id>.
    """
    try:
        df, cnt, ids_omitidos = FacturacionService.procesar_datos_wo(ids_filter, consecutivo_inicial)

        if df.empty:
            db.session.rollback()
            msg = 'No hay datos para exportar.'
            if ids_omitidos:
                msg += f' Pedidos omitidos por estado no exportable: {", ".join(ids_omitidos)}.'
            task_runner.set_failed(task_id, msg)
            return

        # PERSISTENCIA EN SQL
        db.session.commit()
        logger.info(f"✅ SQL Commit: {cnt} items exportados exitosamente.")
        if ids_omitidos:
            logger.warning(f"⚠️ Pedidos solicitados pero omitidos (estado no exportable): {ids_omitidos}")

        # GENERAR ARCHIVO
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='ImportarWO')
        output.seek(0)

        filename = f'PEDIDOS_WO_{datetime.now().strftime("%Y%m%d")}.xlsx'
        fd, tmp_path = tempfile.mkstemp(suffix='.xlsx', prefix='wo_')
        with os.fdopen(fd, 'wb') as f:
            f.write(output.getvalue())

        task_runner.set_completed(
            task_id, file_path=tmp_path, filename=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            result_meta={"actualizados": cnt, "omitidos": ids_omitidos}
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en exportación WO (task {task_id}): {e}")
        task_runner.set_failed(task_id, str(e))


@facturacion_bp.route('/api/exportar/world-office', methods=['POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO', 'COMERCIAL FRIMETALS'])
def exportar_world_office():
    """
    Controller delgado: dispara la generación (que incluye la persistencia
    SQL del consecutivo WO) en un hilo de fondo y responde de inmediato con
    el task_id. Antes esto bloqueaba el único worker gunicorn de la app hasta
    terminar de armar el libro completo.
    """
    data = request.get_json(silent=True) or {}
    ids_filter = data.get('ids', None)
    consecutivo_inicial = data.get('consecutivo_inicial', None)

    task_id = task_runner.create_task()
    app_obj = current_app._get_current_object()
    task_runner.run_in_background(
        task_id, app_obj, _generar_excel_wo_task,
        ids_filter, consecutivo_inicial
    )

    return api_success(data={"task_id": task_id}, status_code=202)

@facturacion_bp.route('/api/exportar/world-office/preview', methods=['GET', 'POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO', 'COMERCIAL FRIMETALS'])
def preview_world_office():
    """Vista previa sin persistencia (rollback automático de la sesión)."""
    try:
        ids_filter = None
        consecutivo_inicial = None
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            ids_filter = data.get('ids', None)
            consecutivo_inicial = data.get('consecutivo_inicial', None)
        
        df, _, ids_omitidos = FacturacionService.procesar_datos_wo(ids_filter, consecutivo_inicial, incluir_auditoria=True)

        # OBLIGATORIO: Hacer rollback para que el preview NO guarde cambios en la BD
        db.session.rollback()

        if df.empty: return jsonify({'success': True, 'data': [], 'omitidos': ids_omitidos})

        preview = df.fillna('').head(100).to_dict(orient='records')
        return jsonify({'success': True, 'data': preview, 'omitidos': ids_omitidos})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@facturacion_bp.route('/api/exportar/world-office/reimprimir', methods=['POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def reimprimir_pedidos_wo():
    """
    Regenera el archivo WO para pedidos que YA están en EXPORTADO_WO --
    recuperación cuando el archivo original se perdió o nunca se subió a
    World Office. Solo lectura (ver FacturacionService.reimprimir_pedidos_exportados):
    no reasigna consecutivo ni cambia estado, así que no hay riesgo de
    duplicar el documento en WO aunque se use varias veces sobre el mismo
    pedido. Síncrono (no usa task_runner): es para 1-2 pedidos puntuales,
    no para lotes grandes como el flujo normal de exportación.
    """
    data = request.get_json(silent=True) or {}
    ids_filter = data.get('ids') or []

    try:
        df, ids_omitidos = FacturacionService.reimprimir_pedidos_exportados(ids_filter)

        if df.empty:
            db.session.rollback()
            return jsonify({
                'success': False,
                'error': f'Ningún pedido en estado EXPORTADO_WO encontrado para: {", ".join(ids_filter)}'
            }), 404

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='ImportarWO')
        output.seek(0)

        db.session.rollback()  # Defensivo: esta ruta es solo lectura, no debe dejar nada pendiente de commit

        filename = f'PEDIDOS_WO_REIMPRESION_{datetime.now().strftime("%Y%m%d")}.xlsx'
        from flask import send_file
        return send_file(
            output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True, download_name=filename
        )
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error en reimprimir_pedidos_wo: {e}")
        return jsonify({'success': False, 'error': 'Error interno regenerando el archivo.'}), 500


# ====================================================================
# PEDIDOS DE EXPORTACIÓN (plantilla WO "Otra Moneda TRM", distinta de la
# nacional de arriba) — flujo separado a propósito para no arriesgar el de
# nacional: son ~57 vs 60 columnas con semántica distinta en varios campos
# (ver FacturacionService.generar_dataframe_exportacion). Se mantiene la
# tabla/selección de "Pedidos Pendientes" nacional intacta
# (FacturacionService.procesar_datos_wo ya excluye es_exportacion=True) y
# esto vive en su propia sección de la UI.
# ====================================================================

@facturacion_bp.route('/api/facturacion/pedidos-pendientes-exportacion', methods=['GET'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def obtener_pedidos_pendientes_exportacion():
    """Pedidos exportables marcados como exportación -- ver FacturacionService.listar_pedidos_exportables."""
    try:
        resultado = FacturacionService.listar_pedidos_exportables(es_exportacion=True)
        return jsonify({'success': True, 'pedidos': resultado})
    except Exception as e:
        logger.error(f"Error en obtener_pedidos_pendientes_exportacion: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _generar_excel_wo_exportacion_task(task_id, ids_filter, consecutivo_inicial):
    """Contraparte de _generar_excel_wo_task para la plantilla de exportación."""
    try:
        df, cnt = FacturacionService.generar_dataframe_exportacion(ids_filter, consecutivo_inicial)

        if df.empty:
            db.session.rollback()
            task_runner.set_failed(task_id, 'No hay pedidos de exportación pendientes para exportar.')
            return

        db.session.commit()
        logger.info(f"✅ SQL Commit: {cnt} pedidos de exportación marcados EXPORTADO_WO.")

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='ImportarWO_Exportacion')
        output.seek(0)

        filename = f'PEDIDOS_WO_EXPORTACION_{datetime.now().strftime("%Y%m%d")}.xlsx'
        fd, tmp_path = tempfile.mkstemp(suffix='.xlsx', prefix='wo_export_')
        with os.fdopen(fd, 'wb') as f:
            f.write(output.getvalue())

        task_runner.set_completed(
            task_id, file_path=tmp_path, filename=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            result_meta={"actualizados": cnt}
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en exportación WO de exportación (task {task_id}): {e}")
        task_runner.set_failed(task_id, str(e))


@facturacion_bp.route('/api/exportar/world-office-exportacion', methods=['POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def exportar_world_office_exportacion():
    data = request.get_json(silent=True) or {}
    ids_filter = data.get('ids', None)
    consecutivo_inicial = data.get('consecutivo_inicial', None)

    task_id = task_runner.create_task()
    app_obj = current_app._get_current_object()
    task_runner.run_in_background(
        task_id, app_obj, _generar_excel_wo_exportacion_task,
        ids_filter, consecutivo_inicial
    )

    return api_success(data={"task_id": task_id}, status_code=202)


@facturacion_bp.route('/api/exportar/world-office-exportacion/preview', methods=['GET', 'POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def preview_world_office_exportacion():
    """Vista previa sin persistencia (rollback automático de la sesión)."""
    try:
        ids_filter = None
        consecutivo_inicial = None
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            ids_filter = data.get('ids', None)
            consecutivo_inicial = data.get('consecutivo_inicial', None)

        df, _ = FacturacionService.generar_dataframe_exportacion(ids_filter, consecutivo_inicial)

        # OBLIGATORIO: rollback para que el preview NO guarde cambios en la BD
        db.session.rollback()

        if df.empty:
            return jsonify({'success': True, 'data': []})

        preview = df.fillna('').head(100).to_dict(orient='records')
        return jsonify({'success': True, 'data': preview})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ====================================================================
# REGISTRO LEGACY DIRECTO (distinto del flujo de exportación World Office
# de arriba) — movido desde backend/app.py
# ====================================================================

@facturacion_bp.route('/api/facturacion', methods=['POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def handle_facturacion():
    """Endpoint para registrar operaciones de facturacion."""
    data = request.get_json()
    try:
        resultado = FacturacionService.registrar(data)
        return jsonify({"status": "success", "success": True, "message": resultado['mensaje']}), 200
    except FacturacionDatosInvalidosException as e:
        return jsonify({"status": "error", "success": False, "message": e.message}), 400
    except Exception as e:
        logger.error(f"ERROR en facturacion: {type(e).__name__}: {str(e)}")
        return jsonify({"status": "error", "success": False, "message": "Error interno al registrar la operación de facturación."}), 500
