"""
Rutas de dashboard.
"""
from flask import Blueprint, jsonify, request, send_file, Response
from backend.core.sql_database import rollback_seguro
from backend.repositories.ventas_repository import VentasRepository
from backend.repositories.dashboard_repository import DashboardRepository
from backend.repositories.producto_repository import producto_repo
# Importados a nivel de módulo: si se resuelven dentro de un try/except propenso a
# fallos, un error de DB deja el nombre sin definir y degenera en NameError en cascada.
from backend.services.dashboard_service import DashboardService
from backend.services.pulido_service import PulidoService
from backend.utils.formatters import parsear_fecha_dashboard
import logging
from backend.utils.cache_manager import cached_route
from backend.utils.auth_middleware import require_role, ROL_ADMINS, ROL_COMERCIALES, ROL_DASHBOARD_OPERATIVO

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)




@dashboard_bp.route('/', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def obtener_dashboard():
    """Obtiene estadísticas del dashboard."""
    try:
        # REFACTOR: 100% SQL-First
        kpis = DashboardRepository.get_dashboard_kpis()
        
        return jsonify({
            'status': 'success', 'success': True,
            'produccion': {'total': kpis.get('inyeccion_ok', 0) + kpis.get('pulido_ok', 0)},
            'ventas': {'total': kpis.get('ventas_totales', 0)},
            'stock_critico': [],
            'pnc': {'total': kpis.get('scrap_total', 0)}
        }), 200


        
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en dashboard: {e}")
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Error obteniendo datos'
        }), 500

@dashboard_bp.route('/stats', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
@cached_route(namespace='dashboard', ttl=600)
def obtener_metricas_bi():
    """
    Endpoint Unificado para Visión de Científico de Datos.
    Soporta ?desde=YYYY-MM-DD&hasta=YYYY-MM-DD
    """
    try:
        desde_str = request.args.get('desde')
        hasta_str = request.args.get('hasta')
        
        desde = parsear_fecha_dashboard(desde_str) if desde_str else None
        hasta = parsear_fecha_dashboard(hasta_str) if hasta_str else None

        # --- RECOPILACIÓN DE DATOS (Separación de responsabilidades) ---
        try:
            kpis = DashboardRepository.get_dashboard_kpis(desde, hasta)
            ranking_iny_ops = DashboardRepository.get_ranking_operarios_inyeccion(desde, hasta)
            ranking_maquinas_raw = DashboardRepository.get_ranking_maquinas(desde, hasta)

            maquinas_con_pct = DashboardService.calcular_porcentajes_maquinas(ranking_maquinas_raw)

            # 2. Analítica de Pulido → PulidoService (lógica de negocio aislada)
            pulido_profundo  = PulidoService.get_ranking_leaderboard(desde, hasta)
            pulido_evolucion = PulidoService.get_evolucion_operarias(desde, hasta)
            analytics_pulido = PulidoService.get_analytics_completo(desde, hasta)
            analytics_pulido["eficiencia_referencia"] = DashboardService.calcular_eficiencia_pulido_por_referencia(desde, hasta)
            analytics_inyeccion = DashboardRepository.get_analytics_inyeccion(desde, hasta)

            stock_critico = producto_repo.get_stock_critico_sql()
            tendencia = DashboardRepository.get_tendencia_produccion_sql(desde, hasta)
        except Exception as db_err:
            rollback_seguro()
            logger.error(f"❌ Error en consultas SQL de Dashboard BI: {db_err}")
            # PROHIBIDO degradar a ceros y seguir: eso produciría un HTTP 200 falso
            # que cached_route congelaría durante 10 minutos. Se propaga para responder 500.
            raise

        # 3. KPI de negocio (cumplimiento de pedidos) — cálculo delegado al servicio
        fulfillment_rate = DashboardService.calcular_fulfillment_rate(
            kpis.get('inyeccion_ok', 0), kpis.get('pedidos_solicitados', 0)
        )

        # IA INSIGHTS (Reporte Avanzado del Bot de Planta)
        insights = DashboardService.generar_insights_bot_planta(
            kpis=kpis,
            stock_critico=stock_critico,
            pulido_profundo=pulido_profundo,
            ranking_iny_ops=ranking_iny_ops
        )

        # 4. Estructura de salida compatible con Dashboard.js (Safe Access)
        result_data = {
            "rango": {"desde": str(desde) if desde else "Inicio", "hasta": str(hasta) if hasta else "Fin"},
            "kpis": {
                "inyeccion_ok": kpis.get('inyeccion_ok', 0),
                "pulido_ok": kpis.get('pulido_ok', 0),
                "ensamble_ok": kpis.get('ensambles_ok', 0),
                "pedidos_solicitado": kpis.get('pedidos_solicitados', 0),
                "cumplimiento_pct": fulfillment_rate,
                "perdida_calidad_dinero": kpis.get('perdida_calidad_dinero', 0),
                "scrap_total": kpis.get('scrap_total', 0),
                "scrap_detalle": kpis.get('scrap_detalle', {}),
                "scrap_almacen_desglose": kpis.get('scrap_almacen_desglose', []),
                "stock_critico": stock_critico
            },
            "rankings": {
                "inyeccion_ops": [
                    {"nombre": op['nombre'], "valor": op['valor'], "insight": "Top Inyección"}
                    for op in ranking_iny_ops
                ],
                # pulido_profundo es el DTO limpio generado por PulidoService
                "pulido_profundo": pulido_profundo,
                # % de cambio en volumen/eficiencia vs el período anterior de igual
                # duración -- ver PulidoService.get_evolucion_operarias.
                "pulido_evolucion": pulido_evolucion
            },
            "maquinas": maquinas_con_pct,
            "tendencia": tendencia,
            "analytics_pulido": analytics_pulido,
            "analytics_inyeccion": analytics_inyeccion,
            "insights_ia": insights,
            "insight_ia": insights[0] if insights else "Sin novedades operativas."
        }
        
        return jsonify({"status": "success", "success": True, "data": result_data}), 200

    except Exception as e:
        rollback_seguro()
        import traceback
        logger.error(f"Error en dashboard BI: {e}\n{traceback.format_exc()}")
        # HTTP 500 obligatorio. cached_route SOLO almacena respuestas 200, de modo que
        # un fallo transitorio de DB ya no queda congelado en caché durante 10 minutos.
        # El detalle técnico queda en el log del servidor, no en la respuesta al cliente.
        return jsonify({
            "status": "error", "success": False,
            "error": "No fue posible calcular las métricas del dashboard.",
            "data": None
        }), 500

# Mantener rutas viejas por compatibilidad si es necesario o redireccionarlas
@dashboard_bp.route('/inyeccion', methods=['GET'])
def metricas_inyeccion_legacy():
    return obtener_metricas_bi()

@dashboard_bp.route('/pulido', methods=['GET'])
def metricas_pulido_legacy():
    return obtener_metricas_bi()

@dashboard_bp.route('/ventas/desglose-mensual', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def get_desglose_mensual():
    """Endpoint para el Drill-down del gráfico mensual."""
    try:
        mes = request.args.get('mes')
        anio = request.args.get('anio')
        tipo_vista = request.args.get('tipo_vista', 'money')
        if not mes or not anio:
            return jsonify({"success": False, "error": "Faltan parámetros mes y anio"}), 400

        data = VentasRepository.get_desglose_mensual_ventas(mes, anio, tipo_vista)
        return jsonify({"success": True, "data": data})
    except ValueError as e:
        # Fail-closed: mes/anio inválidos nunca deben degradar a un barrido sin filtro de fecha.
        logger.warning(f"[get_desglose_mensual] Parámetros inválidos: {e}")
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en get_desglose_mensual: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener el desglose de ventas."}), 500

@dashboard_bp.route('/ventas/exportar-desglose', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def exportar_desglose_mensual():
    """Genera Excel del desglose mensual con formato profesional."""
    try:
        mes = request.args.get('mes')
        anio = request.args.get('anio')
        tipo_vista = request.args.get('tipo_vista', 'money')
        if not mes or not anio:
            return jsonify({"success": False, "error": "Faltan parámetros mes y anio"}), 400
        
        data = VentasRepository.get_desglose_mensual_ventas(mes, anio, tipo_vista)
        if not data.get("productos") and not data.get("clientes"):
            return jsonify({"success": False, "error": "No hay datos para este periodo"}), 404

        output = DashboardService.generar_excel_desglose(data, mes, anio)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'Reporte_Ventas_{mes}_{anio}.xlsx'
        )
    except ValueError as e:
        logger.warning(f"[exportar_desglose_mensual] Parámetros inválidos: {e}")
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        rollback_seguro()
        logger.error(f"❌ Error Crítico en Exportación Excel: {e}")
        return jsonify({"success": False, "error": "No fue posible generar el reporte Excel."}), 500


# ── NUEVO ENDPOINT: Rendimiento Mensual Dedicado ──────────────────────────────

@dashboard_bp.route('/performance/monthly', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def get_monthly_performance():
    """
    Endpoint exclusivo para el gráfico 'Análisis Comparativo Anual'.
    Retorna comparativa mensual Ventas vs Pedidos (Año Actual vs Anterior).
    Requiere rol ADMIN, GERENCIA o COMERCIAL.
    """
    try:
        desde_str = request.args.get('desde')
        hasta_str = request.args.get('hasta')
        desde = parsear_fecha_dashboard(desde_str) if desde_str else None
        hasta = parsear_fecha_dashboard(hasta_str) if hasta_str else None

        # Normalizar a string ISO para el servicio (acepta str o None)
        start = str(desde) if desde else None
        end = str(hasta) if hasta else None

        data = DashboardRepository.get_monthly_performance_comparison(start, end)
        return jsonify({"success": True, "data": data}), 200

    except Exception as e:
        rollback_seguro()
        import traceback
        logger.error(f"[/performance/monthly] {e}\n{traceback.format_exc()}")
        # HTTP 500: devolver 200 con años en 0 hacía que el gráfico dibujara un
        # comparativo vacío como si fuera un dato real.
        return jsonify({
            "success": False,
            "error": "No fue posible calcular el comparativo mensual.",
            "data": None
        }), 500

@dashboard_bp.route('/cartera', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def get_dashboard_cartera():
    """Consulta la tabla cartera_wo para mostrar KPIs de cartera en el dashboard."""
    try:
        datos_cartera = DashboardService.get_cartera_wo_stats()
        
        return jsonify({
            "success": True,
            "data": datos_cartera
        }), 200

    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en endpoint /dashboard/cartera: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener las métricas de cartera."}), 500

@dashboard_bp.route('/rendimiento', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def get_dashboard_rendimiento():
    """Retorna el rendimiento mensual para el tacómetro."""
    try:
        desde_str = request.args.get('desde')
        hasta_str = request.args.get('hasta')
        desde = parsear_fecha_dashboard(desde_str) if desde_str else None
        hasta = parsear_fecha_dashboard(hasta_str) if hasta_str else None
        
        datos_rendimiento = DashboardService.get_rendimiento(desde, hasta)
        
        return jsonify({
            "success": True,
            "data": datos_rendimiento
        }), 200

    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en endpoint /dashboard/rendimiento: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener el rendimiento mensual."}), 500

@dashboard_bp.route('/cartera/exportar', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def exportar_cartera():
    """
    Exporta la cartera completa a CSV mediante streaming para no colapsar la RAM.
    Protegido por RBAC (Admin y Comercial).
    """
    from backend.services.cartera_service import CarteraService
    
    # Se genera el CSV al vuelo y se transmite directamente
    generador_csv = CarteraService.generar_export_csv()
    
    return Response(
        generador_csv,
        mimetype='text/csv',
        headers={
            "Content-Disposition": "attachment; filename=estado_cartera.csv",
            "Cache-Control": "no-cache"
        }
    )

@dashboard_bp.route('/scrap-detalle', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def get_scrap_detalle():
    """
    Devuelve el desglose de scrap/mermas por fecha y máquina de origen para una referencia.
    Delegado 100% a DashboardService.
    """
    try:
        item_id = request.args.get('item_id') or request.args.get('referencia')
        if not item_id:
            return jsonify({"success": False, "error": "Falta parámetro item_id o referencia"}), 400

        desde_str = request.args.get('desde')
        hasta_str = request.args.get('hasta')
        desde = parsear_fecha_dashboard(desde_str) if desde_str else None
        hasta = parsear_fecha_dashboard(hasta_str) if hasta_str else None

        data = DashboardService.get_scrap_detalle(item_id, desde, hasta)
        return jsonify({"success": True, "item_id": item_id, "data": data}), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en /scrap-detalle: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener el detalle de scrap."}), 500

@dashboard_bp.route('/sin-rotacion', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES)
def get_sin_rotacion():
    """
    Devuelve la lista de productos activos de baja rotación en los últimos 12 meses.
    Soporta filtro opcional ?q=referencia y parámetro dinámico ?max_ventas=0..50.
    Delegado 100% a DashboardService.
    """
    try:
        q = request.args.get('q')
        max_v = request.args.get('max_ventas', default=0, type=int)
        data = DashboardService.get_productos_sin_rotacion(q=q, max_ventas=max_v)
        return jsonify({"success": True, "data": data}), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en /sin-rotacion: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener los productos de baja rotación."}), 500

@dashboard_bp.route('/drilldown/inyeccion', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def drilldown_inyeccion_fecha():
    """
    Endpoint estricto de Drill-Down por fecha exacta (ISO YYYY-MM-DD) para auditoría de producción de inyección.
    """
    try:
        import re
        from datetime import datetime

        fecha_str = request.args.get('fecha', '').strip()
        if not fecha_str:
            return jsonify({
                "success": False,
                "error": "El parámetro 'fecha' es obligatorio y debe cumplir el formato ISO YYYY-MM-DD (ej. 2026-07-24)."
            }), 400

        if not re.match(r'^\d{4}-\d{2}-\d{2}$', fecha_str):
            return jsonify({
                "success": False,
                "error": f"Formato de fecha inválido: '{fecha_str}'. Se requiere estrictamente el formato ISO YYYY-MM-DD."
            }), 400

        try:
            datetime.strptime(fecha_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                "success": False,
                "error": f"La fecha '{fecha_str}' no es una fecha válida del calendario ISO YYYY-MM-DD."
            }), 400

        res = DashboardService.get_drilldown_inyeccion_por_fecha(fecha_str)
        return jsonify(res), 200 if res.get('success') else 500
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en /api/dashboard/drilldown/inyeccion: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener el detalle de inyección."}), 500

@dashboard_bp.route('/drilldown/inyeccion/operador', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def drilldown_inyeccion_operador():
    """
    Detalle atómico por lote de un operador para el modal 'Top Rendimiento Inyección'.
    """
    try:
        responsable = request.args.get('responsable', '').strip()
        if not responsable:
            return jsonify({
                "success": False,
                "error": "El parámetro 'responsable' es obligatorio."
            }), 400

        desde_str = request.args.get('desde')
        hasta_str = request.args.get('hasta')
        desde = parsear_fecha_dashboard(desde_str) if desde_str else None
        hasta = parsear_fecha_dashboard(hasta_str) if hasta_str else None

        res = DashboardService.get_detalle_operador_inyeccion(responsable, desde, hasta)
        return jsonify(res), 200 if res.get('success') else 500
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en /api/dashboard/drilldown/inyeccion/operador: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener el detalle del operador."}), 500


# ====================================================================
# DASHBOARD AVANZADO (indicadores/rankings de Inyección y Pulido)
# Movido desde backend/app.py
# ====================================================================

@dashboard_bp.route('/avanzado/indicador_inyeccion_sql', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def indicador_inyeccion_sql():
    """Calcula indicador de eficiencia de inyección usando SQL."""
    try:
        kpis = DashboardRepository.get_dashboard_kpis()
        ok = kpis.get('inyeccion_ok', 0)
        pnc = kpis.get('inyeccion_pnc', 0)
        eficiencia = (ok / (ok + pnc) * 100) if (ok + pnc) > 0 else 100

        return jsonify({
            'status': 'success', 'success': True,
            'ok': ok,
            'pnc': pnc,
            'total': ok + pnc,
            'eficiencia': round(eficiencia, 2)
        }), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en indicador_inyeccion_sql: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible calcular el indicador de inyección.'}), 500


@dashboard_bp.route('/avanzado/indicador_pulido', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def indicador_pulido():
    """Calcula indicador de eficiencia de pulido usando SQL."""
    try:
        kpis = DashboardRepository.get_dashboard_kpis()
        ok = kpis.get('pulido_ok', 0)
        pnc = kpis.get('pulido_pnc', 0)
        eficiencia = (ok / (ok + pnc) * 100) if (ok + pnc) > 0 else 100

        return jsonify({
            'status': 'success', 'success': True,
            'ok': ok,
            'pnc': pnc,
            'total': ok + pnc,
            'eficiencia': round(eficiencia, 2)
        }), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en indicador_pulido: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible calcular el indicador de pulido.'}), 500


@dashboard_bp.route('/avanzado/produccion_maquina_avanzado', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def produccion_maquina_avanzado():
    """Analiza la producción por máquina usando SQL-Native."""
    try:
        return jsonify({'status': 'success', 'success': True, **DashboardRepository.get_produccion_por_maquina()}), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en produccion_maquina_avanzado: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible calcular la producción por máquina.'}), 500


@dashboard_bp.route('/avanzado/produccion_operario_ranking', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def produccion_operario_ranking():
    """Ranking consolidado de operarios (Inyección + Pulido) vía SQL."""
    try:
        ranking_iny = DashboardRepository.get_ranking_operarios_inyeccion()
        ranking_pul = DashboardRepository.get_ranking_operarios_pulido()

        consolidado = {}
        for r in ranking_iny:
            nom = r['nombre']
            consolidado[nom] = consolidado.get(nom, 0) + r['valor']
        for r in ranking_pul:
            nom = r['nombre']
            consolidado[nom] = consolidado.get(nom, 0) + r['valor']

        ranking_final = sorted(consolidado.items(), key=lambda x: x[1], reverse=True)

        return jsonify({
            'status': 'success', 'success': True,
            'ranking': dict(ranking_final),
            'total_operarios': len(consolidado)
        }), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en produccion_operario_ranking: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible calcular el ranking de operarios.'}), 500


@dashboard_bp.route('/avanzado/ranking_inyeccion', methods=['GET'])
@require_role(ROL_DASHBOARD_OPERATIVO)
def ranking_inyeccion():
    """Ranking específico de inyectores vía SQL."""
    try:
        ranking = DashboardRepository.get_ranking_operarios_inyeccion()
        resultado = {r['nombre']: r['valor'] for r in ranking}

        return jsonify({
            'status': 'success', 'success': True,
            'ranking': resultado,
            'total': len(ranking)
        }), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error en ranking_inyeccion: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible calcular el ranking de inyección.'}), 500


@dashboard_bp.route('/real', methods=['GET'])
def dashboard_real_redirect():
    from flask import redirect
    return redirect('/api/dashboard/stats')
