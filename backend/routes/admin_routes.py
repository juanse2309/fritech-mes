from flask import Blueprint, jsonify, render_template, Response, request
from backend.utils.auth_middleware import require_role, ROL_ADMINS, ROL_COMERCIALES
from backend.utils.cache_manager import cached_route, invalidate_cache

import difflib
import csv
import io
import logging

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin_bp', __name__)

# Import time helper
import time as _time

# Helper function to clean currency strings to int/float (Precision Fix)
def clean_currency(val):
    if not val: return 0.0
    if isinstance(val, (int, float)): return float(val)
    # Requerimiento: replace('$', '').replace('.', '').replace(',', '.')
    s = str(val).replace('$', '').replace('.', '').replace(',', '.').strip()
    try:
        return float(s)
    except:
        return 0.0

# Helper function to clean number strings
def clean_number(val):
    if not val: return 0
    if isinstance(val, (int, float)): return val
    s = str(val).replace('.', '').replace(',', '').strip()
    try:
        return int(s)
    except:
        return 0


@admin_bp.route('/api/admin/dashboard', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
@cached_route(namespace='admin', ttl=600)
def get_admin_dashboard_data():
    from flask import request
    from backend.repositories.dashboard_repository import DashboardRepository
    from backend.services.dashboard_service import DashboardService

    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')

    # --- SQL NATIVE DATA FETCHING ---
    try:
        metrics = DashboardRepository.get_admin_dashboard_metrics_sql(start_date_str, end_date_str)

        response_data = {
            "success": True,
            "status": "success",
            "data": {
                "mensual": metrics["mensual"],
                "top_productos": metrics["top_productos"],
                "peores_productos": metrics["peores_productos"],
                "backorder": DashboardService.mapear_fechas_despacho_backorder(metrics["backorder"]),
                "incumplimiento_unidades": metrics["incumplimiento_unidades"],
                "incumplimiento_dinero": metrics["incumplimiento_dinero"],
                "resumen_unidades": metrics.get("resumen_unidades", 0),
                "resumen_dinero": metrics.get("resumen_dinero", 0),
                "incumplimiento_consolidado": metrics.get("incumplimiento_consolidado", [])
            }
        }

        return jsonify(response_data)

    except Exception as e:
        import traceback
        logger.error(f"[/api/admin/dashboard] {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "message": "No fue posible calcular las métricas de Jefatura."}), 500

@admin_bp.route('/api/admin/backorder/detalle', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def get_backorder_detalle():
    """Retorna el detalle de productos pendientes para un cliente específico."""
    try:
        cliente_raw = request.args.get('cliente')
        start = request.args.get('desde')
        end = request.args.get('hasta')
        
        if not cliente_raw:
            return jsonify({"success": False, "message": "El parámetro 'cliente' es obligatorio"}), 400

        # exact=True: el 'cliente' que llega aquí es el nombre YA resuelto que el
        # listado de Incumplimiento le mostró al usuario (mismo valor devuelto por
        # incumplimiento_consolidado). BUGFIX: antes se re-normalizaba con
        # normalizar_cliente_alias (ILIKE difuso bidireccional) y la consulta de
        # detalle volvía a matchear por ILIKE '%cliente%' -- para un nombre corto
        # (ej. 'CHOHO') eso podía capturar/agrupar otro cliente real distinto cuyo
        # nombre o alias simplemente contenía esa subcadena, mostrando en el modal
        # una suma distinta a la fila en la que el usuario hizo click. Con match
        # exacto sobre la MISMA expresión de resolución de nombre que usa el
        # listado (dashboard_repository._get_admin_dashboard_metrics_sql_impl),
        # modal y listado quedan garantizados a coincidir para cualquier cliente.
        from backend.repositories.ventas_repository import VentasRepository
        detalle = VentasRepository.get_backorder_detalle_por_cliente(cliente_raw, start, end, exact=True)
        
        return jsonify({
            "success": True,
            "status": "success",
            "data": detalle
        })
    except Exception as e:
        import traceback
        logger.error(f"[/api/admin/backorder/detalle] {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "message": "No fue posible obtener el detalle de backorder."}), 500

@admin_bp.route('/api/admin/auditoria-fichas', methods=['GET'])
@require_role(ROL_ADMINS)
def auditoria_fichas_fuzzy():
    """
    Realiza una auditoría de nombres (Fuzzy Matching) entre la nueva ficha maestra
    y las tablas de producción en PostgreSQL.
    """
    try:
        from backend.models.sql_models import FichaMaestra, ProduccionInyeccion, ProduccionPulido
        from backend.core.sql_database import db
        
        # 1. Obtener datos de FichaMaestra
        fichas = FichaMaestra.query.all()
        nuevos_nombres = set()
        for f in fichas:
            if f.producto: nuevos_nombres.add(f.producto.strip())
            if f.subproducto: nuevos_nombres.add(f.subproducto.strip())

        # 2. Obtener nombres de producción
        nombres_iny = {r[0] for r in db.session.query(ProduccionInyeccion.id_codigo).distinct().all() if r[0]}
        nombres_pul = {r[0] for r in db.session.query(ProduccionPulido.codigo).distinct().all() if r[0]}

        existentes_map = {}
        for n in nombres_iny: existentes_map[n] = "INYECCION"
        for n in nombres_pul: existentes_map[n] = "PULIDO"
        existentes_lista = list(existentes_map.keys())

        # 3. Fuzzy Matching
        mapeo_propuesto = []
        for nombre_nuevo in sorted(list(nuevos_nombres)):
            coincidencias = difflib.get_close_matches(nombre_nuevo, existentes_lista, n=1, cutoff=0.3)
            mejor_match = coincidencias[0] if coincidencias else "SIN COINCIDENCIA"
            confianza = difflib.SequenceMatcher(None, nombre_nuevo, mejor_match).ratio() if coincidencias else 0.0
            origen = existentes_map.get(mejor_match, "N/A")

            mapeo_propuesto.append({
                "Nombre_Nuevo_Maestra": nombre_nuevo,
                "Mejor_Coincidencia_Actual": mejor_match,
                "Porcentaje_Confianza": f"{round(confianza * 100, 1)}%",
                "Hoja_Origen_Actual": origen
            })

        output = io.StringIO()
        fieldnames = ["Nombre_Nuevo_Maestra", "Mejor_Coincidencia_Actual", "Porcentaje_Confianza", "Hoja_Origen_Actual"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(mapeo_propuesto)
        
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=auditoria_fichas_mapping_sql.csv"}
        )
    except Exception as e:
        import traceback
        logger.error(f"Error auditoria SQL: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "message": "No fue posible generar la auditoría de fichas."}), 500
