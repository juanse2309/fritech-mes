import logging
import os
import tempfile
from datetime import datetime
from flask import Blueprint, jsonify, request, render_template, session, current_app
from backend.utils.auth_middleware import require_role, ROL_ADMINS, ROL_COMERCIALES, obtener_identidad_segura
from backend.core.responses import api_success, api_error
from backend.core import task_runner
from backend.services.comercial_service import ComercialHistoricoService
from backend.services.comercial_dashboard_service import ComercialDashboardService
from backend.schemas.comercial_schemas import ComercialDashboardQuery
from pydantic import ValidationError

logger = logging.getLogger(__name__)

comercial_bp = Blueprint('comercial', __name__)

ROLES_PERMITIDOS_COMERCIAL = ROL_ADMINS + ROL_COMERCIALES + ['GERENCIA']

@comercial_bp.route('/comercial/historico', methods=['GET'])
@require_role(ROLES_PERMITIDOS_COMERCIAL)
def render_comercial_historico():
    """Rinde la plantilla HTML independiente para la Analítica Comercial Histórica."""
    return render_template('comercial_historico.html')

@comercial_bp.route('/api/comercial/historico', methods=['GET'])
@require_role(ROLES_PERMITIDOS_COMERCIAL)
def api_obtener_comercial_historico():
    """
    Endpoint dedicado para la extracción analítica de ventas históricas.
    Retorna agregaciones pre-calculadas en PostgreSQL con aislamiento por rol.
    """
    try:
        user_name, user_role = obtener_identidad_segura(request)
        user_id = session.get('user_id') or session.get('usuario_id') or 0
        
        start_year = request.args.get('start_year', default=2024, type=int)
        end_year = request.args.get('end_year', default=2026, type=int)

        data = ComercialHistoricoService.obtener_analitica_historica(
            user_id=user_id,
            username=user_name,
            user_role=user_role,
            start_year=start_year,
            end_year=end_year
        )

        return jsonify(data), 200

    except Exception as e:
        logger.error(f"[COMERCIAL_ROUTES] Error en /api/comercial/historico: {e}")
        return jsonify({'success': False, 'error': 'Error interno extrayendo datos analíticos', 'detalle': str(e)}), 500


@comercial_bp.route('/api/comercial/historico/detalle', methods=['GET'])
@require_role(ROLES_PERMITIDOS_COMERCIAL)
def api_obtener_comercial_detalle():
    """
    Detalle consolidado paginado (Año, Mes, Zona, Cliente). Server-side: la agregación,
    el filtro de búsqueda y el LIMIT/OFFSET corren en PostgreSQL — nunca se envían
    más de `tam_pagina` filas (tope duro 200) al navegador.
    """
    try:
        user_name, user_role = obtener_identidad_segura(request)
        user_id = session.get('user_id') or session.get('usuario_id') or 0

        start_year = request.args.get('start_year', default=2024, type=int)
        end_year = request.args.get('end_year', default=2026, type=int)
        pagina = request.args.get('pagina', default=1, type=int)
        tam_pagina = request.args.get('tam_pagina', default=100, type=int)
        busqueda = request.args.get('busqueda', default='', type=str)

        data = ComercialHistoricoService.obtener_detalle_paginado(
            user_id=user_id,
            username=user_name,
            user_role=user_role,
            start_year=start_year,
            end_year=end_year,
            pagina=pagina,
            tam_pagina=tam_pagina,
            busqueda=busqueda
        )

        return jsonify(data), 200

    except Exception as e:
        logger.error(f"[COMERCIAL_ROUTES] Error en /api/comercial/historico/detalle: {e}")
        return jsonify({'success': False, 'error': 'Error interno extrayendo el detalle', 'detalle': str(e)}), 500


@comercial_bp.route('/api/comercial/crecimiento-clientes', methods=['GET'])
@require_role(ROLES_PERMITIDOS_COMERCIAL)
def api_obtener_crecimiento_clientes():
    """
    Controlador delgado: captura y sanitiza los parámetros de QueryString
    (anio_base, anio_comparacion, zona, busqueda, ytd) y delega toda la
    agregación SQL y el cálculo de crecimiento YoY al servicio.
    """
    try:
        user_name, user_role = obtener_identidad_segura(request)
        user_id = session.get('user_id') or session.get('usuario_id') or 0

        anio_actual = datetime.now().year
        anio_base = request.args.get('anio_base', default=anio_actual - 1, type=int)
        anio_comparacion = request.args.get('anio_comparacion', default=anio_actual, type=int)
        zona = request.args.get('zona', default='', type=str).strip()
        busqueda = request.args.get('busqueda', default='', type=str).strip()

        # Fallback: si el cliente no envía 'ytd' explícito, se activa solo cuando
        # anio_comparacion es el año en curso (ahí es donde comparar 12 meses
        # contra un año parcial produce crecimientos negativos irreales). Si el
        # cliente sí envía 'true'/'false', se respeta esa decisión explícita.
        ytd_param = request.args.get('ytd', default=None, type=str)
        if ytd_param is None:
            aplicar_ytd = (anio_comparacion == anio_actual)
        else:
            aplicar_ytd = ytd_param.strip().lower() == 'true'

        data = ComercialHistoricoService.obtener_crecimiento_clientes(
            user_id=user_id,
            username=user_name,
            user_role=user_role,
            anio_base=anio_base,
            anio_comparacion=anio_comparacion,
            zona=zona,
            busqueda=busqueda,
            aplicar_ytd=aplicar_ytd
        )

        return jsonify(data), 200

    except Exception as e:
        logger.error(f"[COMERCIAL_ROUTES] Error en /api/comercial/crecimiento-clientes: {e}")
        return jsonify({'success': False, 'error': 'Error interno calculando el crecimiento de clientes', 'detalle': str(e)}), 500


@comercial_bp.route('/api/comercial/dashboard', methods=['GET'])
@require_role(ROL_ADMINS)
def api_comercial_dashboard():
    """
    Dashboard comercial con filtro por rango de fechas (?desde=&hasta=, ambos
    opcionales). Solo administración: incluye ventas de todos los vendedores.
    Controlador delgado: valida el query string y delega todo al servicio.
    """
    try:
        filtros = ComercialDashboardQuery.model_validate(request.args.to_dict())
    except ValidationError as e:
        detalles = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        return api_error('Parámetros inválidos', 400, detalles=detalles)

    try:
        data = ComercialDashboardService.obtener_dashboard(filtros.desde, filtros.hasta)
        return api_success(data)
    except Exception as e:
        logger.error(f"[COMERCIAL_ROUTES] Error en /api/comercial/dashboard: {e}")
        return api_error('No fue posible calcular el dashboard comercial.', 500)


def _generar_excel_comercial_task(task_id, user_id, username, user_role, start_year, end_year, vendedor_filtro, fecha_corte):
    """
    Trabajo de fondo de exportar_comercial_excel: genera el .xlsx (SQL +
    pandas + openpyxl, todo delegado al servicio) fuera del hilo HTTP. Corre
    dentro del app_context que le da task_runner.run_in_background.
    """
    try:
        buffer, nombre_archivo = ComercialHistoricoService.generar_excel_ytd_stream(
            user_id=user_id,
            username=username,
            user_role=user_role,
            start_year=start_year,
            end_year=end_year,
            vendedor_filtro=vendedor_filtro,
            fecha_corte=fecha_corte
        )

        fd, tmp_path = tempfile.mkstemp(suffix='.xlsx', prefix='comercial_')
        with os.fdopen(fd, 'wb') as f:
            f.write(buffer.getvalue())

        task_runner.set_completed(
            task_id, file_path=tmp_path, filename=nombre_archivo,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"[COMERCIAL_ROUTES] Error generando Excel comercial (task {task_id}): {e}")
        task_runner.set_failed(task_id, str(e))


@comercial_bp.route('/api/comercial/historico/excel', methods=['GET'])
@require_role(ROLES_PERMITIDOS_COMERCIAL)
def exportar_comercial_excel():
    """
    Controlador delgado: captura parámetros e identidad, y delega la
    generación completa del Excel a un hilo de fondo
    (ver _generar_excel_comercial_task).
    """
    try:
        user_name, user_role = obtener_identidad_segura(request)
        user_id = session.get('user_id') or session.get('usuario_id') or 0

        start_year = request.args.get('start_year', default=2024, type=int)
        end_year = request.args.get('end_year', default=2026, type=int)
        vendedor_filtro = request.args.get('vendedor', default='', type=str)
        fecha_corte = request.args.get('corte', default=None, type=str)  # YYYY-MM-DD opcional

        task_id = task_runner.create_task()
        app_obj = current_app._get_current_object()
        task_runner.run_in_background(
            task_id, app_obj, _generar_excel_comercial_task,
            user_id, user_name, user_role, start_year, end_year, vendedor_filtro, fecha_corte
        )

        return api_success(data={"task_id": task_id}, status_code=202)

    except Exception as e:
        logger.error(f"[COMERCIAL_ROUTES] Error en /api/comercial/historico/excel: {e}")
        return api_error("Error interno generando el Excel", status_code=500, detalle=str(e))
