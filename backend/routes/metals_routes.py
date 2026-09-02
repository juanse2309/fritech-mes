from flask import Blueprint, jsonify, request
from backend.models.sql_models import db, MetalsProduccion, MetalsPersonal, MetalsPedido
from backend.repositories.producto_repository import ProductoRepository
from backend.services.audit_service import AuditService, OwnershipMismatchException
from backend.config.constants import FALLBACK_OPERARIO
from backend.utils.auth_middleware import require_role, ROL_ADMINS, ROL_COMERCIALES
import logging
import uuid
import json
import time # Juan Sebastian: Para manejo de caché
from datetime import datetime, timedelta, timezone

metals_bp = Blueprint('metals', __name__)
logger = logging.getLogger(__name__)

# Roles de Frimetals segun backend/core/tenant.py (_FRIMETALS_ROLES) + admins
# globales y el set comercial ya existente (incluye 'STAFF FRIMETALS' y
# 'COMERCIAL FRIMETALS'). 'JEFE DE PLANTA' faltaba aquí (y en
# _FRIMETALS_ROLES) aunque es el jefe real de planta de Frimetals -- quedaba
# con 403 en toda la API de metals pese a que el frontend sí le mostraba el
# módulo (auth.js lo redirige a 'metals-produccion').
ROLES_METALS = ROL_ADMINS + ROL_COMERCIALES + ['ADMIN FRIMETALS', 'PRODUCCION FRIMETALS', 'JEFE DE PLANTA']

# ====================================================================
# PRODUCTOS
# ====================================================================

@metals_bp.route('/api/metals/productos/listar', methods=['GET'])
@require_role(ROLES_METALS)
def listar_productos_metals():
    """Lista productos de Frimetals usando el repositorio unificado (DRY)."""
    try:
        logger.debug("🌐 [API] Consultando productos de Frimetals via ProductoRepository")
        repo = ProductoRepository(tenant="frimetals")
        productos = repo.listar_todos()
        return jsonify({"success": True, "productos": productos})
    except Exception as e:
        logger.error(f"Error listando productos metals: {e}")
        return jsonify({"success": False, "message": "No fue posible listar los productos de Frimetals."}), 500


# ====================================================================
# PRODUCCIÓN — REGISTRO SIMPLIFICADO (sin flujo de lotes)
# ====================================================================

@metals_bp.route('/api/metals/produccion/registrar', methods=['POST'])
@require_role(ROLES_METALS)
def registrar_produccion_metals():
    try:
        data = request.json

        proceso = data.get('proceso', '')
        proceso_label = data.get('proceso_label', proceso)
        maquina = data.get('maquina', '')
        codigo_producto = data.get('codigo_producto', '')
        descripcion = data.get('descripcion_producto', '')
        # Guard de ownership centralizado con AuditService
        try:
            responsable = AuditService.resolver_y_validar_propietario(None, data.get('responsable', ''))
        except OwnershipMismatchException as e:
            return jsonify({
                "success": False,
                "error": e.message,
                "code": "METALS_SESSION_OWNERSHIP_MISMATCH",
                "responsable_db": e.responsable_db,
                "responsable_in": e.responsable_in
            }), 409
        fecha_js = data.get('fecha', '')
        hora_inicio = data.get('hora_inicio', '')
        hora_fin = data.get('hora_fin', '')
        tiempo_min = data.get('tiempo_min', '')
        cantidad_ok = data.get('cantidad_ok', 0)
        pnc = data.get('pnc', 0)
        id_pedido = data.get('id_pedido', '')
        observaciones = data.get('observaciones', '')
        campos_extra = data.get('campos_extra', {})

        if not codigo_producto or not proceso or not maquina:
            return jsonify({"success": False, "message": "Faltan datos: Producto, Proceso o Máquina"}), 400

        # Obtener departamento del responsable desde SQL
        departamento = ''
        try:
            operario = MetalsPersonal.query.filter_by(responsable=responsable).first()
            if operario:
                departamento = operario.departamento or ''
        except Exception as pe:
            db.session.rollback()
            logger.warning(f"No se pudo obtener departamento SQL: {pe}")
            departamento = '' # Fallback seguro

        # Formatear fecha DD/MM/YYYY
        fecha_sheet = fecha_js
        if '-' in fecha_js:
            partes = fecha_js.split('-')
            if len(partes) == 3:
                fecha_sheet = f"{partes[2]}/{partes[1]}/{partes[0]}"

        # Formatear tiempo
        tiempo_str = ''
        if tiempo_min is not None and tiempo_min != '':
            try:
                mins = int(tiempo_min)
                tiempo_str = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
            except:
                pass

        # Serializar campos extra como JSON string
        campos_extra_str = json.dumps(campos_extra, ensure_ascii=False) if campos_extra else ''

        try:
            nuevo_registro = MetalsProduccion(
                # El id se maneja automáticamente por el Serial de la DB
                fecha=fecha_sheet,
                responsable=responsable,
                departamento=departamento,
                proceso=proceso_label,
                maquina=maquina,
                id_pedido=id_pedido,
                codigo=codigo_producto,
                descripcion=descripcion,
                cantidad_ok=float(cantidad_ok or 0),
                pnc=float(pnc or 0),
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
                tiempo=tiempo_str,
                observaciones=observaciones,
                campos_extra=campos_extra_str
            )
            db.session.add(nuevo_registro)
            db.session.commit()
            
            # --- TRIGGER DE SINCRONIZACIÓN AUTOMÁTICA (Juan Sebastian) ---
            if id_pedido and codigo_producto:
                try:
                    # 1. Sumar producción histórica para este item en este pedido
                    total_producido = db.session.query(db.func.sum(MetalsProduccion.cantidad_ok)).filter(
                        MetalsProduccion.id_pedido == id_pedido,
                        MetalsProduccion.codigo == codigo_producto
                    ).scalar() or 0
                    
                    # 2. Buscar el item en el pedido original para saber el total requerido
                    item_pedido = MetalsPedido.query.filter_by(id_pedido=id_pedido, id_codigo=codigo_producto).first()
                    
                    if item_pedido and item_pedido.cantidad > 0:
                        nuevo_progreso = min(100, int((total_producido / item_pedido.cantidad) * 100))
                        
                        # 3. Actualizar progreso del item
                        item_pedido.progreso = nuevo_progreso
                        
                        # 4. Si es el 100%, opcionalmente cambiar estado
                        if nuevo_progreso == 100:
                            item_pedido.estado = 'FINALIZADO'
                        elif item_pedido.estado == 'PENDIENTE':
                            item_pedido.estado = 'PRODUCCION'
                            
                        db.session.commit()
                        logger.info(f"🔄 [Sync] Pedido {id_pedido} actualizado al {nuevo_progreso}%")
                except Exception as sync_e:
                    logger.error(f"⚠️ Error en sincronización de progreso: {sync_e}")

            logger.info(f"✅ [Metals SQL] Registro guardado — {proceso_label}")
            return jsonify({"success": True})
        except Exception as e_sql:
            db.session.rollback()
            logger.error(f"Error SQL al registrar: {e_sql}")
            return jsonify({"success": False, "message": "Error en base de datos. Verifique si el registro ya existe."}), 500

    except Exception as e:
        logger.error(f"Error registrando producción metals: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ====================================================================
# HISTORIAL
# ====================================================================

@metals_bp.route('/api/metals/produccion/historial', methods=['GET'])
@require_role(ROLES_METALS)
def get_metals_historial():
    """Retorna el historial de producción de Metales desde SQL."""
    try:
        registros_db = MetalsProduccion.query.order_by(MetalsProduccion.id.desc()).limit(100).all()
        
        records = []
        for r in registros_db:
            records.append({
                'ID': r.id,
                'FECHA': r.fecha,
                'RESPONSABLE': r.responsable,
                'DEPARTAMENTO': r.departamento,
                'PROCESO': r.proceso,
                'MAQUINA': r.maquina,
                'CÓDIGO': r.codigo,
                'DESCRIPCIÓN': r.descripcion,
                'CANTIDAD_OK': float(r.cantidad_ok or 0),
                'PNC': float(r.pnc or 0),
                'HORA_INICIO': r.hora_inicio,
                'HORA_FIN': r.hora_fin,
                'TIEMPO': r.tiempo,
                'OBSERVACIONES': r.observaciones,
                'CAMPOS_EXTRA': r.campos_extra
            })

        # Estadísticas (Juan Sebastian)
        bogota_tz = timezone(timedelta(hours=-5))
        hoy_dt = datetime.now(bogota_tz)
        hoy_str = hoy_dt.strftime("%d/%m/%Y")
        mes_str = hoy_dt.strftime("/%m/%Y")
        
        stats = {"hoy": 0, "mes": 0, "pnc": 0, "procesos": 0}
        procesos_hoy = set()

        for r in records:
            if r['FECHA'] == hoy_str:
                stats["hoy"] += int(r['CANTIDAD_OK'])
                procesos_hoy.add(r['PROCESO'])
            if mes_str in r['FECHA']:
                stats["mes"] += 1
                stats["pnc"] += int(r['PNC'])

        stats["procesos"] = len(procesos_hoy)
        return jsonify({"success": True, "registros": records, "stats": stats})
    except Exception as e:
        logger.error(f"Error en historial metals: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@metals_bp.route('/api/metals/dashboard/stats', methods=['GET'])
@require_role(ROLES_METALS)
def get_metals_dashboard_stats():
    """Endpoint para el Dashboard de Metales con manejo robusto de errores."""
    try:
        # 1. Fecha actual en Colombia (Bogotá: UTC-5)
        # Esto asegura que el dashboard sea preciso independientemente del servidor (Render/AWS/etc)
        bogota_tz = timezone(timedelta(hours=-5))
        hoy_dt = datetime.now(bogota_tz)
        hoy_str = hoy_dt.strftime("%d/%m/%Y")
        
        logger.debug(f"📊 [Dashboard Metales] Consultando estadísticas para fecha: {hoy_str}")
        
        # KPI 1: Piezas producidas hoy (Suma de cantidad_ok)
        piezas_hoy = 0
        try:
            # func.sum() ahora directo sobre Numeric
            piezas_hoy_raw = db.session.query(db.func.sum(MetalsProduccion.cantidad_ok)).filter(
                MetalsProduccion.fecha == hoy_str
            ).scalar()
            piezas_hoy = int(piezas_hoy_raw or 0)
        except Exception as e_piezas:
            db.session.rollback() # Reset transacción
            logger.error(f"Error sumando piezas hoy: {e_piezas}")
        
        # KPI 2: Pedidos activos (al menos un item con progreso < 100)
        pedidos_activos = 0
        try:
            pedidos_activos = db.session.query(MetalsPedido.id_pedido).filter(
                MetalsPedido.progreso < 100
            ).distinct().count()
        except Exception as e_ped:
            db.session.rollback() # Reset transacción
            logger.error(f"Error contando pedidos activos: {e_ped}")

        # KPI 3: PNC Total hoy
        pnc_hoy = 0
        try:
            pnc_hoy_raw = db.session.query(db.func.sum(MetalsProduccion.pnc)).filter(
                MetalsProduccion.fecha == hoy_str
            ).scalar()
            pnc_hoy = int(pnc_hoy_raw or 0)
        except Exception as e_pnc:
            db.session.rollback()
            logger.error(f"Error sumando PNC hoy: {e_pnc}")

        # --- ANÁLISIS HISTÓRICO (Últimos 7 Días) ---
        ultimos_7_dias_str = [(hoy_dt - timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
        
        # 1. Rendimiento Diario (Tendencia)
        rendimiento_diario = {}
        try:
            query_rend = db.session.query(
                MetalsProduccion.fecha,
                db.func.sum(MetalsProduccion.cantidad_ok)
            ).filter(MetalsProduccion.fecha.in_(ultimos_7_dias_str)).group_by(MetalsProduccion.fecha).all()
            rendimiento_diario = {row[0]: int(row[1] or 0) for row in query_rend}
        except Exception as e_r:
            db.session.rollback()
            logger.error(f"Error rendimiento_diario: {e_r}")

        # 2. Participación de Operarios (Semanal para incluir a Arturo y otros)
        participacion_operarios = {}
        try:
            query_op = db.session.query(
                MetalsProduccion.responsable,
                db.func.count(MetalsProduccion.id)
            ).filter(MetalsProduccion.fecha.in_(ultimos_7_dias_str)).group_by(MetalsProduccion.responsable).all()
            participacion_operarios = {row[0]: int(row[1] or 0) for row in query_op}
        except Exception as e_o:
            db.session.rollback()
            logger.error(f"Error participacion_operarios semanal: {e_o}")

        # --- AGREGACIONES DE HOY ---
        
        # 3. Producción OK por Máquina (Hoy)
        produccion_por_maquina = {}
        try:
            query_maquina = db.session.query(
                MetalsProduccion.maquina, 
                db.func.sum(MetalsProduccion.cantidad_ok)
            ).filter(MetalsProduccion.fecha == hoy_str).group_by(MetalsProduccion.maquina).all()
            produccion_por_maquina = {row[0]: int(row[1] or 0) for row in query_maquina}
        except Exception as e_m:
            db.session.rollback()
            logger.error(f"Error produccion_por_maquina: {e_m}")

        # 4. PNC por Proceso (Hoy)
        pnc_por_proceso = {}
        try:
            query_pnc_proceso = db.session.query(
                MetalsProduccion.proceso, 
                db.func.sum(MetalsProduccion.pnc)
            ).filter(MetalsProduccion.fecha == hoy_str).group_by(MetalsProduccion.proceso).all()
            pnc_por_proceso = {row[0]: int(row[1] or 0) for row in query_pnc_proceso}
        except Exception as e_p:
            db.session.rollback()
            logger.error(f"Error pnc_por_proceso: {e_p}")

        # 5. Tiempos por Máquina (Hoy) - Suma en minutos
        tiempos_por_maquina = {}
        try:
            registros_hoy = MetalsProduccion.query.filter_by(fecha=hoy_str).all()
            def parse_tiempo_minutos(t_str):
                if not t_str: return 0
                total = 0
                try:
                    if 'h' in t_str:
                        partes = t_str.split('h')
                        total += int(partes[0].strip()) * 60
                        if 'm' in partes[1]:
                            total += int(partes[1].replace('m', '').strip())
                    elif 'm' in t_str:
                        total += int(t_str.replace('m', '').strip())
                except: pass
                return total

            for r in registros_hoy:
                maq = r.maquina or 'Sin Máquina'
                mins = parse_tiempo_minutos(r.tiempo)
                tiempos_por_maquina[maq] = tiempos_por_maquina.get(maq, 0) + mins
        except Exception as e_t:
            logger.error(f"Error tiempos_por_maquina: {e_t}")

        # TAREA EXTRA: Proceso más activo de hoy
        proceso_top_nombre = "N/A"
        proceso_top_cantidad = 0
        try:
            top_query = db.session.query(
                MetalsProduccion.proceso, 
                db.func.sum(MetalsProduccion.cantidad_ok).label('total_vol')
            ).filter(MetalsProduccion.fecha == hoy_str).group_by(MetalsProduccion.proceso).order_by(db.desc('total_vol')).first()
            
            if top_query:
                proceso_top_nombre = top_query[0]
                proceso_top_cantidad = int(top_query[1] or 0)
        except Exception as e_top:
            db.session.rollback()
            logger.error(f"Error calculando proceso top: {e_top}")

        # Widget de Actividad: Últimos 10 registros (Enriquecidos)
        actividad = []
        try:
            recientes = MetalsProduccion.query.order_by(MetalsProduccion.id.desc()).limit(10).all()
            for r in recientes:
                actividad.append({
                    "id": r.id,
                    "fecha": r.fecha or '',
                    "maquina": r.maquina or 'N/A',
                    "producto": r.descripcion or 'Sin descripción',
                    "proceso": r.proceso or 'Sin proceso',
                    "responsable": r.responsable or 'Sin responsable',
                    "cantidad_ok": int(r.cantidad_ok or 0),
                    "tiempo": r.tiempo or '0m',
                    "pnc": int(r.pnc or 0)
                })
        except Exception as e_rec:
            db.session.rollback()
            logger.error(f"Error listando actividad reciente: {e_rec}")
            
        logger.debug(f"✅ Dashboard Data Loaded. Activity Items: {len(actividad)}")
            
        return jsonify({
            "success": True,
            "piezas_hoy": piezas_hoy,
            "pedidos_activos": pedidos_activos,
            "pnc_hoy": pnc_hoy,
            "proceso_top": {
                "nombre": proceso_top_nombre,
                "cantidad": proceso_top_cantidad
            },
            "graficas": {
                "rendimiento_diario": rendimiento_diario,
                "produccion_maquina": produccion_por_maquina,
                "pnc_proceso": pnc_por_proceso,
                "tiempos_maquina": tiempos_por_maquina,
                "participacion_operarios": participacion_operarios
            },
            "actividad_reciente": actividad
        })
    except Exception as e:
        logger.error(f"Error general en dashboard stats: {e}")
        db.session.rollback()
        return jsonify({
            "success": False, 
            "error": str(e), 
            "piezas_hoy": 0, 
            "pedidos_activos": 0,
            "actividad_reciente": []
        }), 200
