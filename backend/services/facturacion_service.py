"""
Servicio de Facturación (registro legacy directo, distinto del flujo de
exportación World Office que ya vive en facturacion_routes.py).
Extraído de backend/app.py.
"""
import logging
from datetime import datetime
import pandas as pd
from sqlalchemy import or_, text
from backend.core.sql_database import db
from backend.config.settings import Empresa
from backend.models.sql_models import Producto, Pedido, AppConfig
from backend.utils.formatters import normalizar_codigo, limpiar_identificacion_tercero
from backend.services.pedidos_service import ESTADOS_INMUTABLES_PEDIDO, ESTADOS_SENSIBLES_PEDIDO

logger = logging.getLogger(__name__)

# 57 columnas de la plantilla WO nacional (distinta de las 60 de
# COLUMNAS_WO_EXPORTACION más abajo -- ver su docstring para el detalle de
# las diferencias). Usada por FacturacionService.procesar_datos_wo.
COLUMNAS_WO_NACIONAL = [
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

# 60 columnas de la plantilla WO "Otra Moneda TRM" (uso real, exportación
# pedido 104561, 2026-09-11) -- distinta de las 57 de la plantilla nacional
# en facturacion_routes.procesar_datos_wo: agrega Moneda/Trm (encabezado y
# detalle), Fecha Emision e Importacion; usa 'Pref Dto Ext'/'No. Dto Ext' en
# vez de los nombres largos; no trae Fecha Entrega/Sucursal/Clasificación.
COLUMNAS_WO_EXPORTACION = [
    'Encab: Empresa', 'Encab: Tipo Documento', 'Encab: Prefijo', 'Encab: Documento Número',
    'Encab: Fecha', 'Encab: Tercero Interno', 'Encab: Tercero Externo', 'Encab: Pref Dto Ext',
    'Encab: No. Dto Ext', 'Encab: Nota', 'Encab: FormaPago', 'Encab: Moneda', 'Encab: Trm',
    'Encab: Verificado', 'Encab: Anulado', 'Encab: Fecha Emision',
    'Encab: Personalizado 1', 'Encab: Personalizado 2', 'Encab: Personalizado 3',
    'Encab: Personalizado 4', 'Encab: Personalizado 5', 'Encab: Personalizado 6',
    'Encab: Personalizado 7', 'Encab: Personalizado 8', 'Encab: Personalizado 9',
    'Encab: Personalizado 10', 'Encab: Personalizado 11', 'Encab: Personalizado 12',
    'Encab: Personalizado 13', 'Encab: Personalizado 14', 'Encab: Personalizado 15',
    'Encab: Importacion',
    'Detalle: Producto', 'Detalle: Bodega', 'Detalle: UnidadDeMedida', 'Detalle: Cantidad',
    'Detalle: IVA', 'Detalle: Valor Unitario', 'Detalle: Descuento', 'Detalle: Vencimiento',
    'Detalle: Nota', 'Detalle: Centro costos', 'Detalle: Moneda', 'Detalle: Trm',
    'Detalle: Personalizado1', 'Detalle: Personalizado2', 'Detalle: Personalizado3',
    'Detalle: Personalizado4', 'Detalle: Personalizado5', 'Detalle: Personalizado6',
    'Detalle: Personalizado7', 'Detalle: Personalizado8', 'Detalle: Personalizado9',
    'Detalle: Personalizado10', 'Detalle: Personalizado11', 'Detalle: Personalizado12',
    'Detalle: Personalizado13', 'Detalle: Personalizado14', 'Detalle: Personalizado15',
    'Detalle: Código Centro Costos',
]


class FacturacionDatosInvalidosException(Exception):
    """
    Validación de negocio de FacturacionService.registrar (campos faltantes,
    cantidad inválida, stock insuficiente). Deliberadamente NO es un
    ValueError plano: antes del fix del ticket task_651f2d99 el bug de
    unpacking en registrar() también lanzaba `ValueError` de forma nativa, y
    un controlador que atrapara ValueError genérico lo habría convertido en
    400 ocultando ese 500. Se mantiene el tipo separado tras el fix por si
    algún otro `ValueError` inesperado aparece más adelante en el método.
    """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


def _obtener_stock_terminado(codigo_sistema):
    """
    Stock actual en P. TERMINADO para un código. Réplica autocontenida de la
    cadena buscar_producto_en_inventario()->obtener_stock() que vivía en
    app.py — ahora que Facturación se muda de ahí, esa cadena queda sin
    ningún otro caller (confirmado por grep antes de purgarla de app.py).
    """
    codigo_norm = normalizar_codigo(codigo_sistema)
    producto = Producto.query.filter(
        (Producto.codigo_sistema == codigo_norm) |
        (Producto.id_codigo == codigo_norm)
    ).first()
    if not producto:
        return 0
    try:
        return int(float(producto.p_terminado or 0))
    except Exception:
        return 0


def registrar_log_operacion(modulo, datos):
    """Registra auditoría de operaciones en la base de datos SQL (db_logs)."""
    try:
        import json
        from backend.models.sql_models import OperacionLog

        detalles_json = json.dumps(datos) if isinstance(datos, (dict, list)) else str(datos)
        operario = "Sistema"
        if isinstance(datos, dict):
            operario = datos.get('OPERARIO') or datos.get('RESPONSABLE') or "Sistema"

        nuevo_log = OperacionLog(
            modulo=modulo,
            operario=str(operario),
            accion=f"Registro en {modulo}",
            detalles=detalles_json
        )
        db.session.add(nuevo_log)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        logger.warning(f" ⚠️ [SQL-LOG] Fallo al registrar log: {e}")
        return False


def registrar_log_facturacion(fila):
    """Registra una facturacion en LOG_FACTURACION correctamente."""
    return registrar_log_operacion('FACTURACION', fila)


class FacturacionService:

    @staticmethod
    def registrar(data):
        """
        Registro legacy directo de una facturación (POST /api/facturacion,
        distinto del flujo de exportación masiva a World Office).

        FIX (ticket task_651f2d99): `StockService.registrar_salida` devuelve
        un único dict, nunca una tupla `(bool, str)`. El código original (y
        su migración tal cual desde backend/app.py) lo desempaquetaba en 2
        variables, lo que lanzaba `ValueError: too many values to unpack`
        siempre que la operación llegaba hasta aquí — todo POST
        /api/facturacion crasheaba con 500 antes de llegar a persistir nada.
        Se corrige comprobando la clave "error" del dict, igual que ya hace
        StockService.mover_inventario_entre_etapas.

        FIX adicional (bug #2, quedaba enmascarado por el de arriba): más
        abajo se llamaba `formatear_fecha_para_sheet(...)`, una función que
        nunca existió en este proyecto (ni en app.py original, ni en ningún
        otro módulo — confirmado por grep) y que habría lanzado NameError en
        cuanto el bug del unpacking dejara de dispararse primero. El destino
        de esa fecha es `registrar_log_facturacion` -> `registrar_log_operacion`,
        que serializa la fila como JSON en db_logs (ya no hay export a Google
        Sheets en este flujo, ver docstring del módulo) — no hace falta
        ningún formateo especial de "hoja de cálculo", se usa la fecha tal
        como llega en el payload.
        """
        if not data:
            raise FacturacionDatosInvalidosException('No se recibieron datos')

        errors = []
        required_fields = ["fecha_inicio", "cliente", "codigo_producto", "cantidad_vendida"]
        for field in required_fields:
            if not data.get(field):
                errors.append(f"Campo '{field}' es obligatorio")
        if errors:
            raise FacturacionDatosInvalidosException(", ".join(errors))

        try:
            cantidad_vendida = int(data['cantidad_vendida'])
            if cantidad_vendida <= 0:
                errors.append("La cantidad vendida debe ser mayor a 0")
        except Exception:
            errors.append("La cantidad vendida debe ser un numero valido")
        if errors:
            raise FacturacionDatosInvalidosException(", ".join(errors))

        codigo_sis = normalizar_codigo(data['codigo_producto'])
        stock_disponible = _obtener_stock_terminado(codigo_sis)

        if stock_disponible < cantidad_vendida:
            raise FacturacionDatosInvalidosException(
                f"Stock insuficiente en P. TERMINADO. Disponible: {stock_disponible}, Solicitado: {cantidad_vendida}"
            )

        nit_cliente = "S/N"
        try:
            from backend.models.sql_models import DbClientes
            cliente_db = DbClientes.query.filter_by(nombre=data['cliente']).first()
            if cliente_db:
                nit_cliente = cliente_db.identificacion or "S/N"
        except Exception as e:
            logger.error(f"Error obteniendo NIT del cliente SQL: {e}")
            nit_cliente = "S/N"

        if not nit_cliente:
            nit_cliente = "S/N"

        from backend.services.stock_service import StockService
        resultado_salida = StockService.registrar_salida(codigo_sis, cantidad_vendida, "P. TERMINADO")

        if "error" in resultado_salida:
            raise FacturacionDatosInvalidosException(resultado_salida["error"])

        import uuid
        id_factura = f"FAC-{str(uuid.uuid4())[:8].upper()}"

        try:
            total_venta = float(data.get('total_venta', 0))
        except Exception:
            total_venta = 0

        fila_factura = [
            id_factura,
            data['cliente'],
            str(data['fecha_inicio']),
            nit_cliente,
            cantidad_vendida,
            total_venta,
            codigo_sis
        ]

        if not registrar_log_facturacion(fila_factura):
            raise RuntimeError("Error al guardar en LOG_FACTURACION")

        mensaje = f" Facturacion registrada: {cantidad_vendida} piezas de {codigo_sis} para {data['cliente']} (NIT: {nit_cliente})"
        return {'mensaje': mensaje}

    @staticmethod
    def listar_pedidos_exportables(es_exportacion=False):
        """
        Pedidos candidatos para exportar a WO -- cualquier estado que
        procesar_datos_wo/generar_dataframe_exportacion aceptarían si se
        seleccionan explícitamente (ver su docstring: con ids_filter solo
        se bloquean ESTADOS_INMUTABLES_PEDIDO | ESTADOS_SENSIBLES_PEDIDO,
        no se exige 'PENDIENTE').

        Antes esta lista solo mostraba estado=='PENDIENTE' -- más estricto
        que lo que el motor de exportación real permite. Un pedido ya en
        'LISTO PARA DESPACHO' o 'EN ALISTAMIENTO' que seguía sin llegar a
        WO quedaba invisible aquí (caso real 2026-09-17, Frimetals: PED-1004
        listo para despacho el mismo día, solo se pudo exportar llamando
        /api/exportar/world-office directo con ids_filter, a mano).

        BUGFIX 2026-09-22: filtrar solo por 'estado' no bastaba -- ese campo
        también lo pisa PedidosService.actualizar_alistamiento() cada vez que
        Almacén guarda progreso de picking (SIN mirar si el pedido ya estaba
        EXPORTADO_WO), así que un pedido genuinamente ya exportado a WO podía
        volver a aparecer aquí como pendiente en cuanto Almacén tocara de
        nuevo su alistamiento. Confirmado en producción: pedidos 104651-104654
        seguían con wo_consecutivo == su propio id_pedido (prueba de que
        procesar_datos_wo ya corrió y generó el documento real en WO, ver
        db_ventas), pero su 'estado' había vuelto a 'EN ALISTAMIENTO'.
        wo_consecutivo NUNCA lo toca actualizar_alistamiento, así que es la
        señal confiable de "ya existe un documento en WO" -- independiente
        de en qué estado de picking esté el pedido.
        :return: lista de dicts agrupados por pedido (id/fecha/cliente/
            vendedor/estado/wo_consecutivo/items_count/total/items), orden
            fecha desc.
        """
        try:
            estados_bloqueados = ESTADOS_INMUTABLES_PEDIDO | ESTADOS_SENSIBLES_PEDIDO
            query = Pedido.query.filter(~Pedido.estado.in_(estados_bloqueados))
            query = query.filter(or_(Pedido.wo_consecutivo.is_(None), Pedido.wo_consecutivo == ''))
            query = query.filter(Pedido.es_exportacion.is_(True) if es_exportacion else Pedido.es_exportacion.isnot(True))

            agrupados = {}
            for r in query.all():
                id_ped = r.id_pedido
                if id_ped not in agrupados:
                    agrupados[id_ped] = {
                        'id': id_ped, 'fecha': str(r.fecha), 'cliente': r.cliente,
                        'vendedor': r.vendedor, 'estado': r.estado, 'wo_consecutivo': r.wo_consecutivo,
                        'items_count': 0, 'total': 0, 'items': []
                    }
                cant = float(r.cantidad or 0)
                prec = float(r.precio_unitario or 0)
                agrupados[id_ped]['items_count'] += 1
                agrupados[id_ped]['total'] += (cant * prec)
                agrupados[id_ped]['items'].append({'cod': r.id_codigo, 'cant': cant})

            return sorted(agrupados.values(), key=lambda x: x['fecha'], reverse=True)
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def procesar_datos_wo(ids_filter=None, consecutivo_inicial=None, incluir_auditoria=False):
        """
        Lógica centralizada: Genera Excel y Actualiza SQL simultáneamente con
        Auto-Sanado de Precios. Contraparte de generar_dataframe_exportacion
        pero para la plantilla WO nacional (57 columnas, ver COLUMNAS_WO_NACIONAL)
        -- excluye es_exportacion=True, que va por el otro método.

        Caso real (Pedido PED-66585859, 2026-09-14): cuando NO se pasa
        ids_filter (exportación automática por lote), solo se consideran
        candidatos los pedidos en 'PENDIENTE' -- correcto, porque nadie los
        seleccionó a mano todavía. Pero cuando SÍ se pasa ids_filter (alguien
        los marcó explícitamente en la pantalla de Facturación), exigir además
        estado=='PENDIENTE' es lo que causó el bug: si Almacén ya le dio
        alistamiento a ese pedido entre que se cargó la lista y que corrió esta
        función (corre en background, hay demora), su estado ya no es
        'PENDIENTE' y quedaba EXCLUIDO en silencio -- nunca se le asignaba
        wo_consecutivo ni se marcaba EXPORTADO_WO, sin ningún aviso. Por eso,
        con selección explícita, solo se bloquean los estados que sí importan
        proteger (ya exportado / ya facturado / cancelado / etc.), no cualquier
        estado intermedio de picking.

        Muta los objetos ORM (quema id_pedido/wo_consecutivo a doc_nro,
        estado=EXPORTADO_WO) -- el caller decide si hace commit o rollback
        (ver preview_world_office para el patrón de vista previa sin
        persistencia).

        :return: (df, items_con_exito, ids_omitidos)
        """
        # 1. Obtener ítems originales haciendo un LEFT JOIN con Producto (db_productos)
        # Excluye es_exportacion=True (isnot(True) también cubre NULL de pedidos
        # históricos previos a esta columna): esos van por
        # FacturacionService.generar_dataframe_exportacion, con su propia
        # plantilla WO de 60 columnas -- no se debe mezclar en la nacional.
        query = db.session.query(Pedido, Producto.precio).select_from(Pedido).outerjoin(
            Producto,
            or_(
                Pedido.id_codigo == Producto.id_codigo,
                Pedido.id_codigo == Producto.codigo_sistema
            )
        ).filter(Pedido.es_exportacion.isnot(True))

        if ids_filter:
            # Selección explícita: no exigir 'PENDIENTE', solo bloquear estados
            # ya protegidos (evita re-exportar/duplicar en WO un pedido que ya
            # tiene documento, o tocar uno cerrado/cancelado/facturado).
            #
            # BUGFIX 2026-09-22: bloquear solo por 'estado' no alcanza --
            # actualizar_alistamiento() puede resetear 'estado' de un pedido
            # ya EXPORTADO_WO de vuelta a EN ALISTAMIENTO (ver docstring de
            # listar_pedidos_exportables), y ese estado ya no cae en
            # estados_bloqueados_reexportacion. Sin este segundo filtro, una
            # selección explícita (ids_filter) directa a este endpoint podía
            # volver a exportar un pedido que YA tiene documento real en WO
            # (wo_consecutivo ya asignado), generando un duplicado/choque de
            # numeración en el ERP.
            estados_bloqueados_reexportacion = ESTADOS_INMUTABLES_PEDIDO | ESTADOS_SENSIBLES_PEDIDO
            query = query.filter(
                Pedido.id_pedido.in_(ids_filter),
                ~Pedido.estado.in_(estados_bloqueados_reexportacion),
                or_(Pedido.wo_consecutivo.is_(None), Pedido.wo_consecutivo == '')
            )
        else:
            query = query.filter(
                Pedido.estado == 'PENDIENTE',
                or_(Pedido.wo_consecutivo.is_(None), Pedido.wo_consecutivo == '')
            )

        results = query.order_by(Pedido.id_pedido.asc()).all()

        # Diff entre lo que se pidió exportar y lo que realmente califica --
        # antes esto se perdía en silencio (ver docstring). ids_filter puede
        # traer duplicados o formato con espacios; se normaliza igual que
        # Pedido.id_pedido para comparar de forma justa.
        ids_omitidos = []
        if ids_filter:
            ids_encontrados = {str(r[0].id_pedido) for r in results}
            ids_omitidos = sorted({str(i) for i in ids_filter} - ids_encontrados)

        if not results:
            return pd.DataFrame(), 0, ids_omitidos

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
        except Exception:
            mapa_clientes = {}

        # 3. Mapeo WO Estricto (57 columnas)
        columnas_wo = COLUMNAS_WO_NACIONAL

        rows_finales = []
        mapeo_internos = {}

        # Manejo seguro del consecutivo
        try:
            curr_cons = int(str(consecutivo_inicial).strip()) if consecutivo_inicial and str(consecutivo_inicial).strip() else None
        except Exception:
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
            v_id = Empresa.NIT_WO_DEFECTO  # Fallback: NIT de esta empresa en WO (sólo último recurso)
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
            except Exception:
                desc = 0.0

            row = {col: "" for col in columnas_wo}
            row.update({
                'Encab: Empresa': Empresa.RAZON_SOCIAL_WO, 'Encab: Tipo Documento': 'PED',
                'Encab: Prefijo': Empresa.PREFIJO_DOCUMENTO_WO_PEDIDO,
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

        return df, items_con_exito, ids_omitidos

    @staticmethod
    def reimprimir_pedidos_exportados(ids_filter):
        """
        Regenera el archivo WO (plantilla nacional, 57 columnas) para
        pedidos que YA están en EXPORTADO_WO -- recuperación cuando el
        archivo original se perdió o nunca se subió a World Office
        (confirmado caso a caso por el usuario, 2026-09-17: PED-1005/
        PED-1009 en Frimetals nunca llegaron a WO).

        SOLO LECTURA, a propósito -- no muta el pedido:
        - NO reasigna wo_consecutivo/id_pedido (reutiliza el que el pedido
          YA tiene grabado desde su exportación original).
        - NO vuelve a aplicar el Auto-Sanado de precios de
          procesar_datos_wo (el pedido ya está cerrado para efectos de WO;
          corregir el precio ahora no tendría a quién avisarle).
        - NO cambia `estado` ni hace commit.

        Exige exactamente lo contrario que procesar_datos_wo: aquí el
        pedido DEBE estar en EXPORTADO_WO, porque el propósito es
        reimprimir un documento ya generado, no crear uno nuevo. Nunca usar
        esto para exportar un pedido por primera vez.

        :param ids_filter: lista de id_pedido a reimprimir (obligatorio,
            no soporta "todos los pendientes" -- eso es procesar_datos_wo).
        :return: (df, ids_omitidos) -- ids_omitidos son los pedidos
            solicitados que no están en EXPORTADO_WO (o no existen).
        """
        if not ids_filter:
            raise ValueError("Se requiere al menos un id_pedido para reimprimir")

        query = db.session.query(Pedido, Producto.precio).select_from(Pedido).outerjoin(
            Producto,
            or_(
                Pedido.id_codigo == Producto.id_codigo,
                Pedido.id_codigo == Producto.codigo_sistema
            )
        ).filter(
            Pedido.id_pedido.in_(ids_filter),
            Pedido.estado == 'EXPORTADO_WO',
            Pedido.es_exportacion.isnot(True),
        )
        results = query.order_by(Pedido.id_pedido.asc()).all()

        ids_encontrados = {str(r[0].id_pedido) for r in results}
        ids_omitidos = sorted({str(i) for i in ids_filter} - ids_encontrados)

        if not results:
            return pd.DataFrame(), ids_omitidos

        try:
            res_clientes = db.session.execute(text(
                "SELECT nombre, identificacion FROM db_clientes "
                "ORDER BY (id_direccion_wo IS NOT NULL), id"
            )).mappings().all()
            mapa_clientes = {str(c['nombre']).strip().upper(): str(c['identificacion']).strip() for c in res_clientes}
        except Exception:
            mapa_clientes = {}

        columnas_wo = COLUMNAS_WO_NACIONAL
        rows_finales = []

        for item, _precio_maestro in results:
            doc_nro = item.wo_consecutivo or item.id_pedido

            nit_raw = mapa_clientes.get(str(item.cliente or '').upper(), item.nit or '')
            nit_limpio = limpiar_identificacion_tercero(nit_raw)

            f_pag = str(item.forma_de_pago or 'Contado').replace('é', 'e').replace('á', 'a').replace('í', 'i').replace('ó', 'o')

            vendedor_db = str(item.vendedor or '').strip()
            v_id = Empresa.NIT_WO_DEFECTO
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
                    logger.warning(f"[WO-REIMPRIMIR] No se pudo resolver cédula para '{vendedor_db}': {ue}")

            try:
                d_val = str(item.descuento or '0').replace('%', '').strip()
                desc = float(d_val) / 100.0 if d_val else 0.0
            except Exception:
                desc = 0.0

            fecha_str = item.fecha.strftime('%d/%m/%Y') if item.fecha else ''
            cant = float(item.cantidad or 0)
            precio = float(item.precio_unitario or 0)

            row = {col: "" for col in columnas_wo}
            row.update({
                'Encab: Empresa': Empresa.RAZON_SOCIAL_WO, 'Encab: Tipo Documento': 'PED',
                'Encab: Prefijo': Empresa.PREFIJO_DOCUMENTO_WO_PEDIDO,
                'Encab: Documento Número': doc_nro,
                'Encab: Fecha': fecha_str,
                'Encab: Tercero Interno': v_id, 'Encab: Tercero Externo': nit_limpio,
                'Encab: Nota': 'PEDIDO', 'Encab: FormaPago': f_pag,
                'Encab: Fecha Entrega': fecha_str,
                'Detalle: Producto': item.id_codigo, 'Detalle: Bodega': 'Principal', 'Detalle: UnidadDeMedida': 'Und.',
                'Detalle: Cantidad': cant, 'Detalle: IVA': 0.19,
                'Detalle: Valor Unitario': precio,
                'Detalle: Descuento': desc,
                'Detalle: Vencimiento': fecha_str,
            })
            rows_finales.append(row)

        df = pd.DataFrame(rows_finales, columns=columnas_wo)
        return df, ids_omitidos

    @staticmethod
    def _forma_pago_exportacion_defecto():
        fila = db.session.get(AppConfig, 'wo_export.pedidos_formapago_defecto')
        if fila and fila.valor not in (None, ''):
            return fila.valor
        return 'FOB-BUENAVENTURA'

    @staticmethod
    def generar_dataframe_exportacion(ids_filter=None, consecutivo_inicial=None):
        """
        Arma el DataFrame de exportación (60 columnas, plantilla WO "Otra
        Moneda TRM") para pedidos PENDIENTE marcados es_exportacion=True.
        Contraparte de facturacion_routes.procesar_datos_wo, pero para la
        plantilla de exportación -- ver COLUMNAS_WO_EXPORTACION.

        'Detalle: Valor Unitario' y 'Detalle: IVA' quedan SIEMPRE en 0 a
        propósito: cada cliente de exportación negocia un precio distinto en
        USD, y quien gestiona la cuenta lo completa directo en World Office
        junto con la TRM del día real de la negociación (decisión explícita
        2026-09-11, confirmado contra el archivo real usado para el pedido
        104561 -- sus 43 líneas trajeron Valor Unitario=0 a propósito, no por
        error). 'Encab/Detalle: Trm' sí se prellena con la TRM oficial del
        día en que se genera el archivo como punto de partida razonable, pero
        se espera que se corrija en WO si la negociación fue otro día.
        'Encab: FormaPago' (aquí es Incoterm+puerto, no forma de pago) usa un
        default configurable en AppConfig porque cada cliente puede variar.

        Muta los objetos ORM (quema id_pedido/wo_consecutivo a doc_nro,
        estado=EXPORTADO_WO) igual que procesar_datos_wo -- el caller decide
        si hace commit o rollback (ver preview_world_office para el patrón
        de vista previa sin persistencia).

        :return: (df, cnt) -- cnt es la cantidad de pedidos (no líneas)
            actualizados a EXPORTADO_WO.
        """
        query = db.session.query(Pedido, Producto.precio).select_from(Pedido).outerjoin(
            Producto,
            or_(
                Pedido.id_codigo == Producto.id_codigo,
                Pedido.id_codigo == Producto.codigo_sistema
            )
        ).filter(Pedido.estado == 'PENDIENTE', Pedido.es_exportacion.is_(True))

        if ids_filter:
            query = query.filter(Pedido.id_pedido.in_(ids_filter))

        results = query.order_by(Pedido.id_pedido.asc()).all()
        if not results:
            return pd.DataFrame(), 0

        try:
            res_clientes = db.session.execute(text(
                "SELECT nombre, identificacion FROM db_clientes "
                "ORDER BY (id_direccion_wo IS NOT NULL), id"
            )).mappings().all()
            mapa_clientes = {str(c['nombre']).strip().upper(): str(c['identificacion']).strip() for c in res_clientes}
        except Exception:
            mapa_clientes = {}

        try:
            from backend.services.trm_service import obtener_trm_oficial, TrmNoDisponibleError
            trm_hoy = obtener_trm_oficial()['trm']
        except Exception as e:
            logger.warning(f"⚠️ No se pudo consultar la TRM oficial para el archivo de exportación, queda en 0: {e}")
            trm_hoy = 0.0

        forma_pago_defecto = FacturacionService._forma_pago_exportacion_defecto()

        rows_finales = []
        mapeo_internos = {}
        try:
            curr_cons = int(str(consecutivo_inicial).strip()) if consecutivo_inicial and str(consecutivo_inicial).strip() else None
        except Exception:
            curr_cons = None

        pedidos_actualizados = set()

        for item, _precio_maestro in results:
            id_orig = item.id_pedido
            if id_orig not in mapeo_internos:
                if curr_cons:
                    val_cons = str(curr_cons)
                    curr_cons += 1
                else:
                    val_cons = str(id_orig).strip().upper()
                mapeo_internos[id_orig] = val_cons
            doc_nro = mapeo_internos[id_orig]

            item.id_pedido = doc_nro
            item.wo_consecutivo = doc_nro
            item.estado = 'EXPORTADO_WO'
            pedidos_actualizados.add(id_orig)

            nit_raw = mapa_clientes.get(str(item.cliente or '').upper(), item.nit or '')
            nit_limpio = limpiar_identificacion_tercero(nit_raw)

            vendedor_db = str(item.vendedor or '').strip()
            v_id = Empresa.NIT_WO_DEFECTO  # Fallback: NIT de esta empresa en WO, mismo criterio que procesar_datos_wo
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
                    logger.warning(f"[WO-EXPORT] No se pudo resolver cédula para '{vendedor_db}': {ue}")

            fecha_str = item.fecha.strftime('%d/%m/%Y') if item.fecha else datetime.now().strftime('%d/%m/%Y')
            cant = float(item.cantidad or 0)

            row = {col: "" for col in COLUMNAS_WO_EXPORTACION}
            row.update({
                'Encab: Empresa': Empresa.RAZON_SOCIAL_WO, 'Encab: Tipo Documento': 'PED',
                'Encab: Prefijo': Empresa.PREFIJO_DOCUMENTO_WO_PEDIDO,
                'Encab: Documento Número': doc_nro,
                'Encab: Fecha': fecha_str,
                'Encab: Tercero Interno': v_id, 'Encab: Tercero Externo': nit_limpio,
                'Encab: Nota': 'PEDIDO', 'Encab: FormaPago': forma_pago_defecto,
                'Encab: Moneda': 'Dolares', 'Encab: Trm': trm_hoy,
                'Encab: Fecha Emision': fecha_str,
                'Detalle: Producto': item.id_codigo, 'Detalle: Bodega': 'Principal', 'Detalle: UnidadDeMedida': 'Und.',
                'Detalle: Cantidad': cant, 'Detalle: IVA': 0,
                'Detalle: Valor Unitario': 0,
                'Detalle: Descuento': 0,
                'Detalle: Vencimiento': fecha_str,
                'Detalle: Moneda': 'Dolares', 'Detalle: Trm': trm_hoy,
            })
            rows_finales.append(row)

        df = pd.DataFrame(rows_finales, columns=COLUMNAS_WO_EXPORTACION)
        return df, len(pedidos_actualizados)
