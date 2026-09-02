"""
Rutas de productos.
Endpoints REST para operaciones con productos.
"""
from flask import Blueprint, jsonify, request
from backend.services.inventario_service import inventario_service
from backend.repositories.producto_repository import producto_repo, ProductoRepository
from backend.core.tenant import get_tenant_from_request
import logging
import time # Juan Sebastian: Para manejo de caché
import os
from backend.utils.formatters import normalizar_codigo
from backend.models.sql_models import Producto
from backend.core.sql_database import db as sql_db
from backend.utils.auth_middleware import require_login, require_role, ROL_ADMINS, ROL_JEFES
from backend.utils.cache_manager import cached_route

logger = logging.getLogger(__name__)

productos_bp = Blueprint('productos', __name__)

# Configuración de rutas de imágenes
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRODUCTOS_IMG_DIR = os.path.join(BASE_DIR, 'frontend', 'static', 'img', 'productos')

def resolver_ruta_imagen(imagen_db, codigo_sistema):
    """Resuelve la ruta de la imagen para un producto."""
    if imagen_db and imagen_db.strip():
        return imagen_db.strip()
    
    # Buscar localmente
    codigo_norm = normalizar_codigo(codigo_sistema)
    posibles = [f"{codigo_norm}.jpg", f"{codigo_norm}.png", f"{codigo_norm}.JPG", f"{codigo_norm}.PNG"]
    for filename in posibles:
        if os.path.exists(os.path.join(PRODUCTOS_IMG_DIR, filename)):
            return f"/static/img/productos/{filename}"
            
    return "/static/img/no-image.svg"

@productos_bp.route('/detalle/<codigo_sistema>', methods=['GET'])
@require_login
def detalle_producto(codigo_sistema):
    """Obtiene el detalle completo de un producto con auditoría de stock detallada."""
    try:
        import re
        codigo_raw = str(codigo_sistema).strip().upper()
        # Eliminar CUALQUIER prefijo de letras seguido de guión (FR-, MT-, CAR-, INT-, ENS-, etc.)
        codigo_numerico = re.sub(r'^[A-Z]+-', '', codigo_raw).strip()
        
        # Búsqueda Robusta: exacta por el código tal cual, por el numérico limpio, o flexible con ILIKE
        p_sql = Producto.query.filter(
            (Producto.id_codigo == codigo_raw) | 
            (Producto.codigo_sistema == codigo_raw) |
            (Producto.id_codigo == codigo_numerico) |
            (Producto.codigo_sistema == codigo_numerico)
        ).first()
        
        # Fallback ILIKE si la búsqueda exacta no dio resultados
        if not p_sql:
            p_sql = Producto.query.filter(
                (Producto.id_codigo.ilike(f'%{codigo_numerico}')) |
                (Producto.codigo_sistema.ilike(f'%{codigo_numerico}'))
            ).first()
        
        if p_sql:
            # MÉTRICA MATEMÁTICA PURA
            v_por_pulir = float(p_sql.por_pulir or 0)
            v_p_terminado = float(p_sql.p_terminado or 0)
            v_bodega = float(p_sql.stock_bodega or 0)
            
            # La suma que solicita el usuario: Por Pulir + Terminado
            stock_total_calculado = v_por_pulir + v_p_terminado
            comp = float(p_sql.comprometido or 0)
            
            # Cálculo de Backorder (Pedidos Pendientes)
            from backend.models.sql_models import Pedido
            pedidos_activos = sql_db.session.query(Pedido).filter(
                Pedido.id_codigo == p_sql.id_codigo,
                Pedido.estado.in_(['PENDIENTE', 'ABIERTO', 'Alistamiento', 'ALISTADO'])
            ).all()
            total_pendientes = 0
            for p_obj in pedidos_activos:
                try:
                    import re
                    c_sol_str = re.sub(r'[^0-9.]', '', str(p_obj.cantidad or '0'))
                    c_sol = float(c_sol_str) if c_sol_str else 0.0
                    c_ali_str = re.sub(r'[^0-9.]', '', str(p_obj.cant_alistada or '0'))
                    c_ali = float(c_ali_str) if c_ali_str else 0.0
                except:
                    c_sol = 0.0
                    c_ali = 0.0
                total_pendientes += max(0.0, c_sol - c_ali)
            
            res_data = {
                "id_codigo": p_sql.id_codigo,
                "codigo_sistema": p_sql.codigo_sistema,
                "descripcion": p_sql.descripcion,
                "p_terminado": v_p_terminado,
                "por_pulir": v_por_pulir,
                "comprometido": comp,
                "stock_disponible": v_p_terminado - comp,
                "disponible": v_p_terminado - comp,
                "stock_bodega": v_bodega,
                "imagen": p_sql.imagen or "",
                "imagen_valida": resolver_ruta_imagen(p_sql.imagen, p_sql.codigo_sistema),
                "pedidos_pendientes": total_pendientes,
                "precio": float(p_sql.precio or 0)
            }

            return jsonify({"status": "success", "success": True, "producto": res_data}), 200
        else:
            return jsonify({"status": "error", "success": False, "message": f"Producto [{codigo_norm}] no encontrado"}), 200
            
    except Exception as e:
        logger.error(f"Error crítico en detalle producto SQL: {e}")
        return jsonify({"status": "error", "success": False, "message": str(e)}), 200

@productos_bp.route('/buscar/<query>', methods=['GET'])
@require_login
def buscar_productos(query):
    """
    Busca productos uniendo con la tabla de costos (SQL-Native).
    Permite obtener Stock y Precio en una sola consulta.
    Si division=frimetals, busca en metals_productos.
    """
    try:
        from sqlalchemy import text
        from backend.models.sql_models import MetalsProducto

        division = request.args.get('division', '').lower()
        limite = request.args.get('limite', 30, type=int)
        termino = f"%{query.strip().upper()}%"

        # --- Lógica de Switch para FriMetals ---
        if division == 'frimetals':
            # LINEA CLAVE: Cambio a tabla metals_productos para DBeaver
            rows = MetalsProducto.query.filter(
                (MetalsProducto.codigo.ilike(termino)) |
                (MetalsProducto.descripcion.ilike(termino))
            ).limit(limite).all()
            
            resultado = []
            for r in rows:
                # Conversión defensiva de precio (que es String en el modelo ahora)
                # Simplificado: El campo ahora es INTEGER en DB
                precio_val = r.precio or 0

                resultado.append({
                    "codigo": r.codigo,
                    "descripcion": r.descripcion,
                    "precio": "{:.2f}".format(precio_val)
                })
            return jsonify({'status': 'success', 'success': True, 'items': resultado}), 200

        PREFIX_PATTERN = "'^(FR-|CAR-|INT-|ENS-|CB-|DE-|HR-|KIT-|AL-)'"
        sql = f"""
            WITH precios_norm AS (
                SELECT 
                    codigo,
                    precio,
                    REGEXP_REPLACE(codigo, {PREFIX_PATTERN}, '', 'i') as cod_norm,
                    ROW_NUMBER() OVER(PARTITION BY REGEXP_REPLACE(codigo, {PREFIX_PATTERN}, '', 'i') ORDER BY codigo) as rn
                FROM db_precio_venta
            ),
            pedidos_cte AS (
                SELECT id_codigo, SUM(
                    GREATEST(0, 
                        COALESCE(NULLIF(REGEXP_REPLACE(CAST(cantidad AS TEXT), '[^0-9.]', '', 'g'), ''), '0')::NUMERIC - 
                        COALESCE(NULLIF(REGEXP_REPLACE(CAST(cant_alistada AS TEXT), '[^0-9.]', '', 'g'), ''), '0')::NUMERIC
                    )
                ) as total_pendiente
                FROM db_pedidos
                WHERE estado IN ('PENDIENTE', 'ABIERTO', 'Alistamiento', 'ALISTADO')
                GROUP BY id_codigo
            ),
            productos_base AS (
                SELECT 
                    *,
                    REGEXP_REPLACE(id_codigo, {PREFIX_PATTERN}, '', 'i') as id_norm
                FROM db_productos
                WHERE 
                    id_codigo ILIKE :t OR 
                    codigo_sistema ILIKE :t OR 
                    descripcion ILIKE :t OR
                    oem ILIKE :t
            )
            SELECT 
                p.id_codigo, 
                p.descripcion as nombre_producto, 
                p.p_terminado, 
                p.comprometido, 
                p.stock_bodega, 
                p.por_pulir, 
                p.codigo_sistema, 
                p.imagen,
                p.oem,
                COALESCE(pv1.precio, pv2.precio, p.precio, 0) as precio_raw,
                COALESCE(ped.total_pendiente, 0) as pedidos_pendientes
            FROM productos_base p
            LEFT JOIN db_precio_venta pv1 ON pv1.codigo = p.id_codigo
            LEFT JOIN precios_norm pv2 ON pv2.cod_norm = p.id_norm AND pv2.rn = 1
            LEFT JOIN pedidos_cte ped ON ped.id_codigo = p.id_codigo
            ORDER BY p.codigo_sistema
            LIMIT :l
        """
        
        result_query = sql_db.session.execute(text(sql), {"t": termino, "l": limite}).mappings().all()
        
        def limpiar_precio(p_str):
            if not p_str or str(p_str).strip() in ['', 'None']: return 0
            l = str(p_str).replace('$', '').replace('.', '').replace(',', '').strip()
            try: return float(l)
            except: return 0
 
        resultado = []
        for r in result_query:
            p_term = float(r['p_terminado'] or 0)
            comp = float(r['comprometido'] or 0)
            
            disponible_final = p_term - comp
            precio_final = limpiar_precio(r['precio_raw'])
            
            resultado.append({
                "id_codigo": r['id_codigo'],
                "codigo": r['id_codigo'], # Alias para compatibilidad
                "nombre_producto": r['nombre_producto'],
                "descripcion": r['nombre_producto'], # Alias para compatibilidad
                "p_terminado": p_term,
                "comprometido": comp,
                "disponible": disponible_final,
                "stock_disponible": disponible_final, # Alias para compatibilidad
                "stock_bodega": float(r['stock_bodega'] or 0),
                "por_pulir": float(r['por_pulir'] or 0),
                "codigo_sistema": r['codigo_sistema'],
                "imagen": resolver_ruta_imagen(r['imagen'], r['codigo_sistema']),
                "oem": r['oem'] or "",
                "precio": precio_final,
                "pedidos_pendientes": float(r['pedidos_pendientes'] or 0)
            })
            
        return jsonify({
            'status': 'success', 'success': True,
            'resultados': resultado
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Error en búsqueda SQL-JOIN: {e}")
        return jsonify({
            'status': 'error', 'success': False,
            'message': str(e)
        }), 500

@productos_bp.route('/listar', methods=['GET'])
@require_login
@cached_route(namespace='productos_listar', ttl=120)
def listar_productos():
    """
    Lista todos los productos.
    Si division=frimetals, retorna desde metals_productos.
    """
    try:
        from sqlalchemy import text
        from backend.models.sql_models import MetalsProducto

        division = request.args.get('division', '').lower()

        # --- Lógica de Switch para FriMetals ---
        if division == 'frimetals':
            # LINEA CLAVE: Cambio a tabla metals_productos para DBeaver
            rows = MetalsProducto.query.all()
            resultado = []
            for r in rows:
                # Simplificado: El campo ahora es INTEGER en DB
                precio_val = r.precio or 0

                resultado.append({
                    "codigo": r.codigo,
                    "descripcion": r.descripcion,
                    "precio": "{:.2f}".format(precio_val)
                })
            return jsonify({"items": resultado}), 200
            
        # --- Lógica Estándar (FriParts) ---
        # JOIN optimizado con CTE para pre-normalización
        PREFIX_PATTERN = "'^(FR-|CAR-|INT-|ENS-|CB-|DE-|HR-|KIT-|AL-)'"
        sql = f"""
            WITH precios_norm AS (
                SELECT 
                    codigo,
                    precio,
                    REGEXP_REPLACE(codigo, {PREFIX_PATTERN}, '', 'i') as cod_norm,
                    ROW_NUMBER() OVER(PARTITION BY REGEXP_REPLACE(codigo, {PREFIX_PATTERN}, '', 'i') ORDER BY codigo) as rn
                FROM db_precio_venta
            ),
            pedidos_cte AS (
                SELECT id_codigo, SUM(
                    GREATEST(0, 
                        COALESCE(NULLIF(REGEXP_REPLACE(CAST(cantidad AS TEXT), '[^0-9.]', '', 'g'), ''), '0')::NUMERIC - 
                        COALESCE(NULLIF(REGEXP_REPLACE(CAST(cant_alistada AS TEXT), '[^0-9.]', '', 'g'), ''), '0')::NUMERIC
                    )
                ) as total_pendiente
                FROM db_pedidos
                WHERE estado IN ('PENDIENTE', 'ABIERTO', 'Alistamiento', 'ALISTADO')
                GROUP BY id_codigo
            ),
            productos_base AS (
                SELECT 
                    *,
                    REGEXP_REPLACE(id_codigo, {PREFIX_PATTERN}, '', 'i') as id_norm
                FROM db_productos
            )
            SELECT 
                p.id_codigo, 
                p.descripcion as nombre_producto, 
                p.p_terminado, 
                p.comprometido, 
                p.stock_bodega, 
                p.por_pulir, 
                p.codigo_sistema, 
                p.imagen,
                COALESCE(pv1.precio, pv2.precio, p.precio, 0) as precio_raw,
                COALESCE(ped.total_pendiente, 0) as pedidos_pendientes
            FROM productos_base p
            LEFT JOIN db_precio_venta pv1 ON pv1.codigo = p.id_codigo
            LEFT JOIN precios_norm pv2 ON pv2.cod_norm = p.id_norm AND pv2.rn = 1
            LEFT JOIN pedidos_cte ped ON ped.id_codigo = p.id_codigo
            ORDER BY p.codigo_sistema
        """
        
        result_query = sql_db.session.execute(text(sql)).mappings().all()
        
        def limpiar_precio(val):
            if val is None or str(val).strip() in ['', 'None']: return 0
            if isinstance(val, (int, float)): return float(val)
            from decimal import Decimal
            if isinstance(val, Decimal): return float(val)
            
            s = str(val).replace('$', '').replace(' ', '')
            if ',' in s and '.' in s:
                s = s.replace('.', '').replace(',', '.')
            elif '.' in s and len(s.split('.')[-1]) == 3:
                s = s.replace('.', '')
            elif ',' in s:
                s = s.replace(',', '.')
                
            try: return float(s)
            except: return 0
   
        resultado = []
        for r in result_query:
            p_term = float(r['p_terminado'] or 0)
            comp = float(r['comprometido'] or 0)
            
            disponible_final = p_term - comp
            precio_final = limpiar_precio(r['precio_raw'])
            
            resultado.append({
                "id_codigo": r['id_codigo'],
                "codigo": r['id_codigo'], # Alias
                "nombre_producto": r['nombre_producto'],
                "descripcion": r['nombre_producto'], # Alias para compatibilidad
                "p_terminado": p_term,
                "comprometido": comp,
                "disponible": disponible_final,
                "stock_disponible": disponible_final, # Alias para compatibilidad
                "stock_bodega": float(r['stock_bodega'] or 0),
                "por_pulir": float(r['por_pulir'] or 0),
                "codigo_sistema": r['codigo_sistema'],
                "imagen": resolver_ruta_imagen(r['imagen'], r['codigo_sistema']),
                "precio": precio_final,
                "pedidos_pendientes": float(r['pedidos_pendientes'] or 0)
            })
            
        return jsonify({"items": resultado}), 200

    except Exception as e:
        logger.error(f"❌ Error en /api/productos/listar (JOIN Costos): {e}")
        return jsonify([]), 200

@productos_bp.route('/comprometidos/<codigo_sistema>', methods=['GET'])
@require_login
def listar_comprometidos(codigo_sistema):
    """
    Desglose por pedido del valor 'COMPROMETIDO' de un producto -- drill-down
    para la columna COMPROMETIDO en Productos y Existencias (antes solo
    mostraba el número, sin poder ver qué pedidos lo componen).

    Mismo cálculo de pendiente (cantidad - cant_alistada) que ya usa
    detalle_producto() más arriba, solo que aquí se expone la lista completa
    en vez del total agregado -- pero el filtro de "pedido activo" es el de
    VentasRepository.get_pedidos_pendientes() (exclusión de estados cerrados),
    no el de detalle_producto(): ese usa una lista fija de estados
    ('PENDIENTE', 'ABIERTO', 'Alistamiento', 'ALISTADO') que no existen en los
    datos reales de producción (los estados reales son 'LISTO PARA DESPACHO',
    'EN ALISTAMIENTO', 'DESPACHO PARCIAL', 'EXPORTADO_WO', etc.) y por eso
    nunca hace match -- confirmado en vivo 2026-09-02, devolvía 0 pedidos para
    cualquier producto con comprometido > 0.
    """
    try:
        import re
        from backend.models.sql_models import Pedido

        codigo_raw = str(codigo_sistema).strip().upper()
        codigo_numerico = re.sub(r'^[A-Z]+-', '', codigo_raw).strip()

        ESTADOS_CERRADOS = ['COMPLETADO', 'DESPACHADO', 'ENTREGADO', 'FACTURADO', 'CANCELADO']
        pedidos = Pedido.query.filter(
            (Pedido.id_codigo == codigo_raw) | (Pedido.id_codigo == codigo_numerico),
            Pedido.estado.isnot(None),
            sql_db.func.upper(Pedido.estado).notin_(ESTADOS_CERRADOS)
        ).order_by(Pedido.fecha.asc()).all()

        items = []
        for p in pedidos:
            try:
                cant = float(re.sub(r'[^0-9.]', '', str(p.cantidad or '0')) or 0)
            except (ValueError, TypeError):
                cant = 0.0
            try:
                alistada = float(re.sub(r'[^0-9.]', '', str(p.cant_alistada or '0')) or 0)
            except (ValueError, TypeError):
                alistada = 0.0
            pendiente = max(0.0, cant - alistada)
            if pendiente <= 0:
                continue
            items.append({
                'id_pedido': p.id_pedido,
                'cliente': p.cliente,
                'fecha': p.fecha.strftime('%Y-%m-%d') if p.fecha else '',
                'estado': p.estado,
                'cantidad': cant,
                'cant_alistada': alistada,
                'pendiente': pendiente,
                'wo_consecutivo': p.wo_consecutivo
            })

        total = sum(i['pendiente'] for i in items)
        return jsonify({
            'status': 'success', 'success': True,
            'items': items,
            'total_comprometido': total
        }), 200
    except Exception as e:
        logger.error(f"Error listando comprometidos de {codigo_sistema}: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 200


@productos_bp.route('/historial/<codigo>', methods=['GET'])
@require_login
def historial_producto(codigo):
    """
    Obtiene la trazabilidad 360 de un producto 100% SQL-Native.
    """
    try:
        from sqlalchemy import text, or_
        from backend.models.sql_models import (
            ProduccionInyeccion, ProduccionPulido, Ensamble, 
            Pedido, Pnc, RawVentas
        )
        from datetime import datetime

        codigo_norm = normalizar_codigo(codigo).strip().upper()
        codigo_limpio = codigo_norm.replace('FR-', '').replace('CB-', '').replace('ENS-', '').strip()
        
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 500))
        
        eventos = []
        radar = { 'INYECCION': 0, 'PULIDO': 0, 'ENSAMBLE': 0, 'COMERCIAL': 0, 'PNC': 0 }

        # 1. BARRIDO DE INYECCIÓN
        try:
            res_iny = ProduccionInyeccion.query.filter(
                or_(
                    ProduccionInyeccion.id_codigo.ilike(f"%{codigo_norm}%"),
                    ProduccionInyeccion.id_codigo.ilike(f"%{codigo_limpio}%")
                )
            ).all()
            radar['INYECCION'] = len(res_iny)
            for r in res_iny:
                eventos.append({
                    'tipo': 'INYECCION',
                    'fecha_dt': r.fecha_inicia if hasattr(r.fecha_inicia, 'year') else datetime.now(),
                    'fecha': r.fecha_inicia.strftime('%d/%m/%Y') if hasattr(r.fecha_inicia, 'strftime') else str(r.fecha_inicia or ''),
                    'responsable': str(r.responsable or 'SISTEMA'),
                    'cant': int(float(r.cantidad_real or 0)),
                    'detalle': f"Máquina: {r.maquina or ''} | Molde: {r.molde or ''}"
                })
        except Exception as e:
            sql_db.session.rollback()
            logger.error(f"Falla bloque Inyección para {codigo}: {e}")

        # 2. BARRIDO DE PULIDO
        try:
            res_pul = ProduccionPulido.query.filter(
                or_(
                    ProduccionPulido.codigo.ilike(f"%{codigo_norm}%"),
                    ProduccionPulido.codigo.ilike(f"%{codigo_limpio}%")
                )
            ).all()
            radar['PULIDO'] = len(res_pul)
            for r in res_pul:
                eventos.append({
                    'tipo': 'PULIDO',
                    'fecha': r.fecha.strftime('%d/%m/%Y') if hasattr(r.fecha, 'strftime') else str(r.fecha),
                    'cant': int(r.cantidad_real or 0),
                    'responsable': r.responsable,
                    'detalle': f"Hora: {r.hora_inicio} - {r.hora_fin} | OP: {r.orden_produccion or 'N/A'}",
                    'fecha_dt': r.fecha if hasattr(r.fecha, 'year') else datetime.now()
                })
        except Exception as e:
            sql_db.session.rollback()
            logger.error(f"Falla bloque Pulido para {codigo}: {e}")

        # 3. BARRIDO DE ENSAMBLE
        try:
            sql_ens = text("""
                SELECT id, fecha, responsable, cantidad, op_numero, buje_ensamble 
                FROM db_ensambles 
                WHERE id_codigo ILIKE :o OR id_codigo ILIKE :l
            """)
            res_ens = sql_db.session.execute(sql_ens, {"o": f"%{codigo_norm}%", "l": f"%{codigo_limpio}%"}).mappings().all()
            radar['ENSAMBLE'] = len(res_ens)
            for r in res_ens:
                f_dt = r['fecha'] if hasattr(r['fecha'], 'year') else None
                eventos.append({
                    'tipo': 'ENSAMBLE',
                    'fecha_dt': f_dt or datetime.now(),
                    'fecha': f_dt.strftime('%d/%m/%Y') if f_dt else str(r['fecha'] or ''),
                    'responsable': str(r['responsable'] or 'SISTEMA'),
                    'cant': int(float(r['cantidad'] or 0)),
                    'detalle': f"OP: {r['op_numero'] or ''} | {r['buje_ensamble'] or ''}"
                })
        except Exception as e:
            sql_db.session.rollback()
            logger.error(f"Falla bloque Ensamble para {codigo}: {e}")

        # 4. BARRIDO DE VENTAS / PEDIDOS
        try:
            # 4.1 Pedidos (Comercial)
            res_ped = Pedido.query.filter(Pedido.id_codigo.ilike(f"%{codigo_norm}%")).all()
            radar['COMERCIAL'] += len(res_ped)
            for r in res_ped:
                eventos.append({
                    'tipo': 'PEDIDO',
                    'fecha_dt': r.fecha if hasattr(r.fecha, 'year') else datetime.now(),
                    'fecha': r.fecha.strftime('%d/%m/%Y') if hasattr(r.fecha, 'strftime') else str(r.fecha or ''),
                    'responsable': str(r.cliente or 'CLIENTE'),
                    'cant': int(float(r.cantidad or 0)),
                    'detalle': f"Orden: {r.id_pedido} | Estado: {r.estado or 'N/A'} | Vendedor: {r.vendedor or ''}"
                })

            # 4.2 Ventas Reales (db_ventas - SQL Quirúrgico)
            sql_ven = text("""
                SELECT id, fecha, productos, nombres, cantidad, documento, clasificacion, total_ingresos
                FROM db_ventas 
                WHERE productos ILIKE :o
            """)
            res_ven = sql_db.session.execute(sql_ven, {"o": f"%{codigo_norm}%"}).mappings().all()
            radar['COMERCIAL'] += len(res_ven)
            for r in res_ven:
                f_dt = r['fecha'] if hasattr(r['fecha'], 'year') else None
                eventos.append({
                    'tipo': 'VENTA',
                    'fecha_dt': f_dt or datetime.now(),
                    'fecha': f_dt.strftime('%d/%m/%Y') if f_dt else str(r['fecha'] or ''),
                    'responsable': str(r['nombres'] or 'CLIENTE'),
                    'cant': int(float(r['cantidad'] or 0)),
                    'detalle': f"Doc: {r['documento'] or ''} | Clasif: {r['clasificacion'] or ''}"
                })
        except Exception as e:
            sql_db.session.rollback()
            logger.error(f"Falla bloque Ventas/Pedidos para {codigo}: {e}")

        # 5. BARRIDO DE PNC
        try:
            res_pnc = Pnc.query.filter(Pnc.id_codigo.ilike(f"%{codigo_norm}%")).all()
            radar['PNC'] = len(res_pnc)
            for r in res_pnc:
                eventos.append({
                    'tipo': 'PNC',
                    'fecha_dt': r.fecha if hasattr(r.fecha, 'year') else datetime.now(),
                    'fecha': r.fecha.strftime('%d/%m/%Y') if hasattr(r.fecha, 'strftime') else str(r.fecha or ''),
                    'responsable': 'CONTROL CALIDAD',
                    'cant': int(float(r.cantidad or 0)),
                    'detalle': f"Criterio: {r.criterio or ''} | Ref: {r.codigo_ensamble or ''}"
                })
        except Exception as e:
            sql_db.session.rollback()
            logger.error(f"Falla bloque PNC para {codigo}: {e}")

        eventos.sort(key=lambda x: x['fecha_dt'] if x['fecha_dt'] else datetime.min, reverse=True)

        kpis = { 'INYECCION': 0, 'PULIDO': 0, 'ENSAMBLE': 0, 'COMERCIAL': 0 }
        for e in eventos:
            c = e.get('cant', 0)
            if e['tipo'] == 'INYECCION': kpis['INYECCION'] += c
            elif e['tipo'] == 'PULIDO': kpis['PULIDO'] += c
            elif e['tipo'] == 'ENSAMBLE': kpis['ENSAMBLE'] += c
            else: kpis['COMERCIAL'] += c
            if 'fecha_dt' in e: del e['fecha_dt']

        total = len(eventos)
        start = (page - 1) * limit
        end = start + limit
        paginados = eventos[start:end]

        return jsonify({
            'status': 'success', 'success': True,
            'resultados': paginados,
            'kpis': kpis,
            'radar': radar,
            'total': total,
            'has_more': end < total
        }), 200

    except Exception as e:
        logger.error(f"❌ Error crítico en Timeline 360 (SQL Final): {str(e)}")
        return jsonify({'status': 'error', 'success': False, 'message': f"Error interno: {str(e)}"}), 500


@productos_bp.route('/sincronizar_precios', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['AUXILIAR INVENTARIO', 'METALS_ADMIN'])
def sincronizar_precios_wo():
    """
    Fase 1 - Sincronización de Precios con World Office.
    Recibe un archivo .csv o .xlsx exportado de WO.
    Busca columnas 'Código' y 'Precio 1'.
    SOLO actualiza registros existentes en db_productos (sin insertar nuevos).
    """
    import re as _re
    try:
        if 'archivo' not in request.files:
            return jsonify({'success': False, 'error': 'No se recibió ningún archivo'}), 400

        archivo = request.files['archivo']
        nombre = archivo.filename.lower() if archivo.filename else ''

        # ── Leer el archivo con pandas ──────────────────────────────────────
        try:
            import pandas as pd
            import io

            contenido = archivo.read()

            if nombre.endswith('.csv'):
                # Resiliencia de codificación y separador
                try:
                    df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python', dtype=str, encoding='utf-8-sig')
                except Exception:
                    df = pd.read_csv(io.BytesIO(contenido), sep=None, engine='python', dtype=str, encoding='latin-1')
            elif nombre.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(io.BytesIO(contenido), dtype=str)
            else:
                return jsonify({'success': False, 'error': 'Formato de archivo no soportado. Use .csv o .xlsx'}), 400

        except ImportError:
            return jsonify({'success': False, 'error': 'Librería pandas no instalada en el servidor'}), 500
        except Exception as e_read:
            logger.error(f"❌ [SincronizarPrecios] Error leyendo archivo: {e_read}")
            return jsonify({'success': False, 'error': f'No se pudo leer el archivo: {str(e_read)}'}), 400

        # ── Normalizar nombres de columnas ─────────────────────────────────
        try:
            df.columns = df.columns.astype(str).str.strip().str.lower() \
                .str.replace('ó', 'o') \
                .str.replace('á', 'a') \
                .str.replace('é', 'e') \
                .str.replace('í', 'i') \
                .str.replace('ú', 'u')
        except Exception as e_cols:
            logger.error(f"❌ [SincronizarPrecios] Error normalizando columnas: {e_cols}")
            return jsonify({'success': False, 'error': f'Error al procesar columnas del archivo: {str(e_cols)}'}), 400

        # Buscar columnas flexiblemente
        col_codigo = None
        col_precio = None
        for col in df.columns:
            if 'codigo' in col or col == 'code' or col == 'referencia' or col == 'ref':
                col_codigo = col
            if 'precio 1' in col or 'precio1' in col or col == 'precio':
                col_precio = col

        if not col_codigo or not col_precio:
            return jsonify({
                'success': False,
                'error': f'Columnas requeridas no encontradas. Se encontraron: {list(df.columns)}. Se necesitan "Código" (o similar) y "Precio 1".'
            }), 400

        # ── Procesar renglón por renglón ───────────────────────────────────
        from sqlalchemy import text
        from backend.core.sql_database import db as sql_db
        from backend.utils.formatters import normalizar_codigo, preservar_o_normalizar_prefijo

        # Detección defensiva de columnas en db_productos en tiempo de ejecución
        try:
            columns_result = sql_db.session.execute(text("SELECT * FROM db_productos LIMIT 1"))
            table_columns = [col.lower() for col in columns_result.keys()]
        except Exception as e_schema:
            logger.error(f"❌ [SincronizarPrecios] Error obteniendo esquema: {e_schema}")
            table_columns = ['id_codigo', 'codigo_sistema']  # Fallback histórico

        actualizados = 0
        omitidos = 0
        errores = 0
        detalles = []
        detalles_sincronizacion = {
            "exitosos": [],
            "no_encontrados": [],
            "errores": []
        }

        # Mapeo defensivo de columnas en base de datos
        col_id_codigo = 'id_codigo' if 'id_codigo' in table_columns else None
        col_codigo_sistema = 'codigo_sistema' if 'codigo_sistema' in table_columns else None
        col_código = 'código' if 'código' in table_columns else ('codigo' if 'codigo' in table_columns else None)

        for _, row in df.iterrows():
            codigo_raw = ""
            try:
                codigo_raw = str(row[col_codigo] or '').strip()
                precio_raw = str(row[col_precio] or '').strip()

                if not codigo_raw or not precio_raw or codigo_raw.lower() in ('nan', 'none', ''):
                    omitidos += 1
                    detalles.append({
                        "codigo": codigo_raw or "(Vacío)",
                        "precio_archivo": precio_raw,
                        "status": "No encontrado en DB (código o precio inválido/vacío)"
                    })
                    detalles_sincronizacion["no_encontrados"].append(codigo_raw or "(Vacío)")
                    continue

                # Limpieza de Precios Ultra-Simple y directa (Fuerza float)
                try:
                    precio_final = float(precio_raw)
                except Exception:
                    omitidos += 1
                    detalles.append({
                        "codigo": codigo_raw,
                        "precio_archivo": precio_raw,
                        "status": "No encontrado en DB (formato de número inválido al castear)"
                    })
                    detalles_sincronizacion["no_encontrados"].append(codigo_raw)
                    continue

                # Normalizar códigos para búsqueda flexible. El 'FR-' se pide
                # EXPLÍCITAMENTE (opt-in) porque aquí solo se LEE db_productos,
                # donde la referencia FriParts histórica sí vive con prefijo; es
                # una variante más del WHERE, no una mutación de la referencia.
                codigo_sin_prefijo = normalizar_codigo(codigo_raw)
                codigo_con_prefijo = preservar_o_normalizar_prefijo(codigo_raw, 'FR-')

                # Query de alta precisión contra codigo_sistema
                query = """
                    UPDATE db_productos
                    SET precio = :precio_archivo
                    WHERE LOWER(TRIM(codigo_sistema)) = LOWER(TRIM(:codigo_raw))
                       OR LOWER(TRIM(codigo_sistema)) = LOWER(TRIM(:codigo_con_prefijo))
                       OR LOWER(TRIM(codigo_sistema)) = LOWER(TRIM(:codigo_sin_prefijo))
                """
                bind_params = {
                    'precio_archivo': precio_final,
                    'codigo_raw': codigo_raw,
                    'codigo_con_prefijo': codigo_con_prefijo,
                    'codigo_sin_prefijo': codigo_sin_prefijo
                }

                # Transacción por fila (Rollback Obligatorio o Commit Inmediato)
                try:
                    result = sql_db.session.execute(text(query), bind_params)
                    if result.rowcount > 0:
                        sql_db.session.commit()
                        actualizados += 1
                        detalles.append({
                            "codigo": codigo_raw,
                            "precio_archivo": precio_final,
                            "status": "Actualizado"
                        })
                        detalles_sincronizacion["exitosos"].append(codigo_raw)
                        print(f"¡CAMBIO REAL: {codigo_raw}!")
                    else:
                        sql_db.session.rollback()
                        omitidos += 1
                        detalles.append({
                            "codigo": codigo_raw,
                            "precio_archivo": precio_final,
                            "status": "No encontrado en DB (0 filas afectadas)"
                        })
                        detalles_sincronizacion["no_encontrados"].append(codigo_raw)
                except Exception as e_sql:
                    sql_db.session.rollback()
                    errores += 1
                    detalles.append({
                        "codigo": codigo_raw,
                        "precio_archivo": precio_final,
                        "status": "Error",
                        "motivo": str(e_sql)
                    })
                    detalles_sincronizacion["errores"].append({
                        "codigo": codigo_raw,
                        "motivo": str(e_sql)
                    })
                    logger.warning(f"⚠️ [SincronizarPrecios] Error ejecutando SQL para código ({codigo_raw}): {e_sql}")
                    continue

            except Exception as e_row:
                errores += 1
                detalles.append({
                    "codigo": codigo_raw or "Desconocido",
                    "status": "Error",
                    "motivo": str(e_row)
                })
                detalles_sincronizacion["errores"].append({
                    "codigo": codigo_raw or "Desconocido",
                    "motivo": str(e_row)
                })
                logger.warning(f"⚠️ [SincronizarPrecios] Error general en fila ({codigo_raw}): {e_row}")
                continue

        logger.info(f"✅ [SincronizarPrecios] Completado: {actualizados} actualizados, {omitidos} no encontrados, {errores} errores.")
        return jsonify({
            'success': True,
            'actualizados_count': actualizados,
            'omitidos_count': omitidos,
            'errores_count': errores,
            'detalles': detalles,
            'detalles_sincronizacion': detalles_sincronizacion,
            'exitosos': detalles_sincronizacion["exitosos"],
            'no_encontrados': detalles_sincronizacion["no_encontrados"],
            'errores': detalles_sincronizacion["errores"],
            'mensaje': f'Sincronización exitosa: {actualizados} precios actualizados.'
        }), 200

    except Exception as e:
        logger.error(f"❌ [SincronizarPrecios] Error crítico: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500