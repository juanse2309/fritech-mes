from flask import Blueprint, request, jsonify, current_app
from backend.utils.auth_middleware import require_role, ROL_ADMINS
from backend.utils.formatters import limpiar_identificacion_tercero
from backend.services.facturacion_service import FacturacionService, FacturacionDatosInvalidosException
from backend.core.responses import api_success, api_error
from backend.core import task_runner
import pandas as pd
import io
import os
import tempfile
from datetime import datetime
import logging
from backend.core.sql_database import db
from backend.models.sql_models import Pedido, Producto, DbCostos
from sqlalchemy import text, or_

facturacion_bp = Blueprint('facturacion_bp', __name__)
logger = logging.getLogger(__name__)


def procesar_datos_wo(ids_filter=None, consecutivo_inicial=None, incluir_auditoria=False):
    """Lógica centralizada: Genera Excel y Actualiza SQL simultáneamente con Auto-Sanado de Precios."""
    
    # 1. Obtener ítems originales haciendo un LEFT JOIN con Producto (db_productos)
    query = db.session.query(Pedido, Producto.precio).select_from(Pedido).outerjoin(
        Producto,
        or_(
            Pedido.id_codigo == Producto.id_codigo,
            Pedido.id_codigo == Producto.codigo_sistema
        )
    ).filter(Pedido.estado == 'PENDIENTE')
    
    if ids_filter:
        query = query.filter(Pedido.id_pedido.in_(ids_filter))
    
    results = query.order_by(Pedido.id_pedido.asc()).all()
    if not results:
        return pd.DataFrame(), 0

    # 2. Preparar Maestros (Clientes para NITs)
    # Un mismo nombre puede tener varias filas (histórico manual + sincronizado
    # desde WO). El ORDER BY deja de último —y por tanto ganando el dict— la
    # fila que trae id_direccion_wo, que es la que vino de World Office.
    try:
        res_clientes = db.session.execute(text(
            "SELECT nombre, identificacion FROM db_clientes "
            "ORDER BY (id_direccion_wo IS NOT NULL), id"
        )).mappings().all()
        mapa_clientes = {str(c['nombre']).strip().upper(): str(c['identificacion']).strip() for c in res_clientes}
    except:
        mapa_clientes = {}

    # 3. Mapeo WO Estricto (57 columnas)
    columnas_wo = [
        'Encab: Empresa', 'Encab: Tipo Documento', 'Encab: Prefijo', 'Encab: Documento Número',
        'Encab: Fecha', 'Encab: Tercero Interno', 'Encab: Tercero Externo', 'Encab: Nota',
        'Encab: FormaPago', 'Encab: Fecha Entrega', 'Encab: Prefijo Documento Externo',
        'Encab: Número_Documento_Externo', 'Encab: Verificado', 'Encab: Anulado',
        'Encab: Personalizado 1', 'Encab: Personalizado 2', 'Encab: Personalizado 3',
        'Encab: Personalizado 4', 'Encab: Personalizado 5', 'Encab: Personalizado 6',
        'Encab: Personalizado 7', 'Encab: Personalizado 8', 'Encab: Personalizado 9',
        'Encab: Personalizado 10', 'Encab: Personalizado 11', 'Encab: Personalizado 12',
        'Encab: Personalizado 13', 'Encab: Personalizado 14', 'Encab: Personalizado 15',
        'Encab: Sucursal', 'Encab: Clasificación', 'Detalle: Producto', 'Detalle: Bodega',
        'Detalle: UnidadDeMedida', 'Detalle: Cantidad', 'Detalle: IVA', 'Detalle: Valor Unitario',
        'Detalle: Descuento', 'Detalle: Vencimiento', 'Detalle: Nota', 'Detalle: Centro costos',
        'Detalle: Personalizado1', 'Detalle: Personalizado2', 'Detalle: Personalizado3',
        'Detalle: Personalizado4', 'Detalle: Personalizado5', 'Detalle: Personalizado6',
        'Detalle: Personalizado7', 'Detalle: Personalizado8', 'Detalle: Personalizado9',
        'Detalle: Personalizado10', 'Detalle: Personalizado11', 'Detalle: Personalizado12',
        'Detalle: Personalizado13', 'Detalle: Personalizado14', 'Detalle: Personalizado15',
        'Detalle: Código Centro Costos'
    ]

    rows_finales = []
    mapeo_internos = {}
    
    # Manejo seguro del consecutivo
    try:
        curr_cons = int(str(consecutivo_inicial).strip()) if consecutivo_inicial and str(consecutivo_inicial).strip() else None
    except:
        curr_cons = None

    items_con_exito = 0

    for item, precio_maestro in results:
        id_orig = item.id_pedido
        
        # Asignar/Recuperar consecutivo para este pedido
        if id_orig not in mapeo_internos:
            if curr_cons:
                val_cons = str(curr_cons)
                curr_cons += 1
            else:
                # MANTENER FORMATO COMPLETO (Ej: PED-1001)
                val_cons = str(id_orig).strip().upper()
            mapeo_internos[id_orig] = val_cons
        
        doc_nro = mapeo_internos[id_orig]

        # --- ACTUALIZACIÓN SQL (Quemado de ID) ---
        old_id = item.id_pedido
        item.id_pedido = doc_nro  # Se sobreescribe el ID original con el consecutivo de WO
        item.wo_consecutivo = doc_nro
        item.estado = 'EXPORTADO_WO'
        
        logger.info(f"🔄 ID de pedido actualizado: {old_id} -> {doc_nro}")
        items_con_exito += 1

        # --- CORRECCIÓN AUTOMÁTICA DE PRECIO Y FINANZAS ---
        prod_cod = str(item.id_codigo or '').strip().upper()
        precio_hist = float(item.precio_unitario or 0)
        
        # Obtener el precio maestro de db_productos
        precio_maest = float(precio_maestro) if precio_maestro is not None else 0.0
        
        # Regla de Auto-Sanado: si el precio histórico es 0, nulo, o difiere del maestro
        if precio_maest > 0:
            if precio_hist == 0 or abs(precio_hist - precio_maest) > 0.01:
                precio_final = precio_maest
            else:
                precio_final = precio_hist
        else:
            precio_final = precio_hist

        cant = float(item.cantidad or 0)
        total_recalculado = cant * precio_final
        total_hist = float(item.total or 0)
        
        # Actualizamos en el objeto de la base de datos (se persistirá en commit)
        if abs(precio_hist - precio_final) > 0.01 or abs(total_hist - total_recalculado) > 0.01:
            logger.info(f"💲 [Auto-Sanado] Corrección automática para {prod_cod}: precio {precio_hist} -> {precio_final}, total {total_hist} -> {total_recalculado}")
            item.precio_unitario = precio_final
            item.total = total_recalculado

        # --- CONSTRUCCIÓN FILA EXCEL ---
        nit_raw = mapa_clientes.get(str(item.cliente or '').upper(), item.nit or '')
        nit_limpio = limpiar_identificacion_tercero(nit_raw)
        
        f_pag = str(item.forma_de_pago or 'Contado').replace('é', 'e').replace('á', 'a').replace('í', 'i').replace('ó', 'o')
        
        # Resolución Dinámica del Vendedor — consulta directa a db_usuarios
        vendedor_db = str(item.vendedor or '').strip()
        v_id = '900315300'  # Fallback: NIT Friparts (sólo último recurso)
        if vendedor_db:
            try:
                row_user = db.session.execute(
                    text("SELECT cedula FROM db_usuarios "
                         "WHERE UPPER(TRIM(nombre_completo)) = UPPER(TRIM(:nombre))"),
                    {"nombre": vendedor_db}
                ).first()
                if row_user and row_user[0]:
                    v_id = str(row_user[0]).strip()
            except Exception as ue:
                logger.warning(f"[WO] No se pudo resolver cédula para '{vendedor_db}': {ue}")
        
        # Trazabilidad Crítica
        print(f"DEBUG WO: Pedido {id_orig} | Vendedor DB: {item.vendedor} | ID Asignado: {v_id}")
        
        try:
            d_val = str(item.descuento or '0').replace('%', '').strip()
            desc = float(d_val) / 100.0 if d_val else 0.0
        except:
            desc = 0.0

        row = {col: "" for col in columnas_wo}
        row.update({
            'Encab: Empresa': 'FRIPARTS SAS', 'Encab: Tipo Documento': 'PED', 'Encab: Prefijo': 'PED',
            'Encab: Documento Número': doc_nro, 
            'Encab: Fecha': item.fecha.strftime('%d/%m/%Y') if item.fecha else datetime.now().strftime('%d/%m/%Y'),
            'Encab: Tercero Interno': v_id, 'Encab: Tercero Externo': nit_limpio,
            'Encab: Nota': 'PEDIDO', 'Encab: FormaPago': f_pag, 
            'Encab: Fecha Entrega': item.fecha.strftime('%d/%m/%Y') if item.fecha else datetime.now().strftime('%d/%m/%Y'),
            'Detalle: Producto': item.id_codigo, 'Detalle: Bodega': 'Principal', 'Detalle: UnidadDeMedida': 'Und.',
            'Detalle: Cantidad': cant, 'Detalle: IVA': 0.19, 
            'Detalle: Valor Unitario': precio_final,
            'Detalle: Descuento': desc, 
            'Detalle: Vencimiento': item.fecha.strftime('%d/%m/%Y') if item.fecha else datetime.now().strftime('%d/%m/%Y'),
            
            # Columnas de auditoría
            'precio_historico': precio_hist,
            'precio_maestro': precio_maest
        })
        rows_finales.append(row)

    df = pd.DataFrame(rows_finales)
    if not df.empty:
        if incluir_auditoria:
            df = df[columnas_wo + ['precio_historico', 'precio_maestro']]
        else:
            df = df[columnas_wo]
    
    return df, items_con_exito

@facturacion_bp.route('/api/facturacion/pedidos-pendientes', methods=['GET'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def obtener_pedidos_pendientes():
    """Obtiene pedidos PENDIENTES desde SQL."""
    try:
        pendientes = Pedido.query.filter(Pedido.estado == 'PENDIENTE').all()
        agrupados = {}
        for r in pendientes:
            id_ped = r.id_pedido
            if id_ped not in agrupados:
                agrupados[id_ped] = {
                    'id': id_ped, 'fecha': str(r.fecha), 'cliente': r.cliente,
                    'vendedor': r.vendedor, 'items_count': 0, 'total': 0, 'items': []
                }
            cant = float(r.cantidad or 0); prec = float(r.precio_unitario or 0)
            agrupados[id_ped]['items_count'] += 1
            agrupados[id_ped]['total'] += (cant * prec)
            agrupados[id_ped]['items'].append({'cod': r.id_codigo, 'cant': cant})
            
        resultado = sorted(agrupados.values(), key=lambda x: x['fecha'], reverse=True)
        return jsonify({'success': True, 'pedidos': resultado})
    except Exception as e:
        logger.error(f"Error en obtener_pedidos_pendientes SQL: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def _generar_excel_wo_task(task_id, ids_filter, consecutivo_inicial):
    """
    Trabajo de fondo de exportar_world_office: genera el DataFrame (que ya
    persiste el consecutivo WO y el estado EXPORTADO_WO vía procesar_datos_wo)
    y arma el .xlsx fuera del hilo HTTP. Corre dentro del app_context que le
    da task_runner.run_in_background -- db.session depende de ese contexto.

    El conteo de items actualizados (antes viajaba en el header HTTP
    X-Pedidos-Actualizados de la respuesta síncrona) ahora se expone como
    result_meta.actualizados en /api/tasks/status/<task_id>.
    """
    try:
        df, cnt = procesar_datos_wo(ids_filter, consecutivo_inicial)

        if df.empty:
            db.session.rollback()
            task_runner.set_failed(task_id, 'No hay datos para exportar.')
            return

        # PERSISTENCIA EN SQL
        db.session.commit()
        logger.info(f"✅ SQL Commit: {cnt} items exportados exitosamente.")

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
            result_meta={"actualizados": cnt}
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en exportación WO (task {task_id}): {e}")
        task_runner.set_failed(task_id, str(e))


@facturacion_bp.route('/api/exportar/world-office', methods=['POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
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
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def preview_world_office():
    """Vista previa sin persistencia (rollback automático de la sesión)."""
    try:
        ids_filter = None
        consecutivo_inicial = None
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            ids_filter = data.get('ids', None)
            consecutivo_inicial = data.get('consecutivo_inicial', None)
        
        df, _ = procesar_datos_wo(ids_filter, consecutivo_inicial, incluir_auditoria=True)
        
        # OBLIGATORIO: Hacer rollback para que el preview NO guarde cambios en la BD
        db.session.rollback()
        
        if df.empty: return jsonify({'success': True, 'data': []})
        
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
