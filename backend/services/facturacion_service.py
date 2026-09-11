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
from backend.models.sql_models import Producto, Pedido, AppConfig
from backend.utils.formatters import normalizar_codigo, limpiar_identificacion_tercero

logger = logging.getLogger(__name__)

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
            v_id = '900315300'  # Fallback: NIT Friparts, mismo criterio que procesar_datos_wo
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
                'Encab: Empresa': 'FRIPARTS SAS', 'Encab: Tipo Documento': 'PED', 'Encab: Prefijo': 'PED',
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
