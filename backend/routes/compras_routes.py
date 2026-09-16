"""
compras_routes.py
==================
Módulo de Compras a Proveedores Externos (plan 2026-09-15). Blueprint
delgado a propósito: solo parsea el request y traduce el resultado a
JSON -- toda la lógica de negocio vive en compras_service.py y
compras_recepcion_service.py (regla de separación de capas del proyecto).

Reemplaza al módulo "Procura" (retirado 2026-08-20 por un endpoint sin
ningún control de acceso) -- cada endpoint de escritura aquí lleva
@require_role desde el día uno.
"""
import logging

from flask import Blueprint, request

from backend.core.responses import api_success, api_error
from backend.utils.auth_middleware import (
    require_role, ROL_ADMINS, _obtener_usuario_activo, obtener_identidad_segura,
)
from backend.services.compras_service import (
    SolicitudCompraService, SolicitudCompraError,
    OrdenCompraService, OrdenCompraError,
    FacturaCompraService, FacturaCompraError,
)
from backend.services.compras_recepcion_service import (
    RecepcionOCService, RecepcionOCError,
    TransitoExternoService, TransitoExternoError,
)

logger = logging.getLogger(__name__)
compras_bp = Blueprint('compras_bp', __name__)

# Roles: los ya existentes de cada persona (decisión del usuario, no se
# crearon roles nuevos). Riesgo aceptado y documentado en el plan: da
# acceso a cualquier usuario con ese rol, no solo a Albeiro/Zoe.
ROLES_COMPRAS_SOLICITAR = ROL_ADMINS + ['ENSAMBLE']
ROLES_COMPRAS_ADMIN = ROL_ADMINS
ROLES_COMPRAS_RECEPCION = ROL_ADMINS + ['JEFE AUXILIAR INVENTARIO']


def _es_admin():
    _, rol = obtener_identidad_segura(request)
    return str(rol or '').strip().upper() in {r.strip().upper() for r in ROL_ADMINS}


# ----------------------------------------------------------------------
# Serializadores (planos, a propósito -- mismo criterio que el resto del
# proyecto: dict comprehension local por endpoint, sin capa ORM->schema)
# ----------------------------------------------------------------------
def _ser_solicitud(s):
    return {
        'id': s.id,
        'item_descripcion': s.item_descripcion,
        'codigo_producto': s.codigo_producto,
        'urgencia': s.urgencia,
        'nota': s.nota,
        'solicitado_por': s.solicitado_por,
        'departamento': s.departamento,
        'estado': s.estado,
        'id_oc_vinculada': s.id_oc_vinculada,
        'motivo_rechazo': s.motivo_rechazo,
        'resuelto_por': s.resuelto_por,
        'resuelto_en': s.resuelto_en.isoformat() if s.resuelto_en else None,
        'creado_en': s.creado_en.isoformat() if s.creado_en else None,
    }


def _ser_proveedor(p):
    return {
        'nit': p.nit,
        'proveedores': p.proveedores,
        'direccion': p.direccion,
        'persona_de_contacto': p.persona_de_contacto,
        'telefono': p.telefono,
        'correo': p.correo,
        'proceso': p.proceso,
        'forma_de_pago': p.forma_de_pago,
    }


def _ser_orden(o):
    return {
        'id': o.id,
        'numero_oc': o.numero_oc,
        'consecutivo': o.consecutivo,
        'proveedor_nit': o.proveedor_nit,
        'proveedor_nombre': o.proveedor_nombre,
        'fecha_oc': o.fecha_oc.isoformat() if o.fecha_oc else None,
        'estado': o.estado,
        'nota': o.nota,
        'creado_por': o.creado_por,
        'creado_en': o.creado_en.isoformat() if o.creado_en else None,
        'anulada_motivo': o.anulada_motivo,
        'exportada_wo': o.exportada_wo,
        'exportada_en': o.exportada_en.isoformat() if o.exportada_en else None,
    }


def _ser_linea_orden(l, extra=None):
    base = {
        'id': l.id,
        'id_oc': l.id_oc,
        'numero_oc': l.numero_oc,
        'codigo_producto': l.codigo_producto,
        'descripcion': l.descripcion,
        'cantidad_pedida': float(l.cantidad_pedida or 0),
        'unidad_medida': l.unidad_medida,
        'valor_unitario': float(l.valor_unitario) if l.valor_unitario is not None else None,
        'id_solicitud': l.id_solicitud,
    }
    if extra:
        base.update(extra)
    return base


def _ser_recepcion(r):
    return {
        'id': r.id,
        'id_oc': r.id_oc,
        'numero_oc': r.numero_oc,
        'fecha_recepcion': r.fecha_recepcion.isoformat() if r.fecha_recepcion else None,
        'estado_recepcion': r.estado_recepcion,
        'recibido_por': r.recibido_por,
        'observaciones': r.observaciones,
        'creado_en': r.creado_en.isoformat() if r.creado_en else None,
    }


def _ser_linea_recepcion(l):
    return {
        'id': l.id,
        'id_recepcion': l.id_recepcion,
        'id_linea_oc': l.id_linea_oc,
        'cantidad_recibida': float(l.cantidad_recibida or 0),
        'cantidad_rechazada': float(l.cantidad_rechazada or 0),
        'motivo_rechazo': l.motivo_rechazo,
        'excede_tolerancia': l.excede_tolerancia,
        'fecha_recepcion': l.fecha_recepcion.isoformat() if l.fecha_recepcion else None,
    }


def _ser_transito(t):
    return {
        'id': t.id,
        'id_linea_recepcion_oc': t.id_linea_recepcion_oc,
        'proceso': t.proceso,
        'proveedor_proceso_nit': t.proveedor_proceso_nit,
        'cantidad_enviada': float(t.cantidad_enviada or 0),
        'fecha_envio': t.fecha_envio.isoformat() if t.fecha_envio else None,
        'enviado_por': t.enviado_por,
        'estado': t.estado,
        'cantidad_retornada': float(t.cantidad_retornada) if t.cantidad_retornada is not None else None,
        'fecha_retorno': t.fecha_retorno.isoformat() if t.fecha_retorno else None,
        'retornado_por': t.retornado_por,
        'diferencia_envio_retorno': float(t.diferencia_envio_retorno) if t.diferencia_envio_retorno is not None else None,
        'creado_en': t.creado_en.isoformat() if t.creado_en else None,
    }


def _ser_historial_transito(h):
    return {
        'id': h.id,
        'id_transito': h.id_transito,
        'estado_anterior': h.estado_anterior,
        'estado_nuevo': h.estado_nuevo,
        'usuario': h.usuario,
        'fecha': h.fecha.isoformat() if h.fecha else None,
        'observaciones': h.observaciones,
    }


def _ser_factura(f):
    return {
        'id': f.id,
        'id_oc': f.id_oc,
        'numero_oc': f.numero_oc,
        'numero_factura': f.numero_factura,
        'fecha_factura': f.fecha_factura.isoformat() if f.fecha_factura else None,
        'cargada_por': f.cargada_por,
        'fecha_carga': f.fecha_carga.isoformat() if f.fecha_carga else None,
        'estado_conciliacion': f.estado_conciliacion,
        'observaciones': f.observaciones,
    }


def _ser_linea_factura(l):
    return {
        'id': l.id,
        'id_factura': l.id_factura,
        'id_linea_oc': l.id_linea_oc,
        'cantidad_facturada': float(l.cantidad_facturada or 0),
        'valor_unitario_facturado': float(l.valor_unitario_facturado) if l.valor_unitario_facturado is not None else None,
        'diferencia_vs_recibido': float(l.diferencia_vs_recibido) if l.diferencia_vs_recibido is not None else None,
    }


# ----------------------------------------------------------------------
# Solicitudes (Albeiro)
# ----------------------------------------------------------------------
@compras_bp.route('/api/compras/solicitudes', methods=['POST'])
@require_role(ROLES_COMPRAS_SOLICITAR)
def crear_solicitud():
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        solicitud = SolicitudCompraService.crear(
            item_descripcion=data.get('item_descripcion'),
            solicitado_por=usuario,
            codigo_producto=data.get('codigo_producto'),
            urgencia=data.get('urgencia'),
            nota=data.get('nota'),
            departamento=data.get('departamento'),
        )
        return api_success(data=_ser_solicitud(solicitud), status_code=201)
    except SolicitudCompraError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error creando solicitud de compra: {e}")
        return api_error("Error interno creando la solicitud", status_code=500)


@compras_bp.route('/api/compras/solicitudes', methods=['GET'])
@require_role(ROLES_COMPRAS_SOLICITAR + ['JEFE AUXILIAR INVENTARIO'])
def listar_solicitudes():
    try:
        usuario = _obtener_usuario_activo()
        solo_propias = request.args.get('propias', 'false').lower() == 'true'
        solicitudes = SolicitudCompraService.listar(
            usuario=usuario, solo_propias=solo_propias, estado=request.args.get('estado'),
        )
        return api_success(data=[_ser_solicitud(s) for s in solicitudes])
    except Exception as e:
        logger.error(f"❌ Error listando solicitudes de compra: {e}")
        return api_error("Error interno listando solicitudes", status_code=500)


@compras_bp.route('/api/compras/solicitudes/<int:id_solicitud>', methods=['GET'])
@require_role(ROLES_COMPRAS_SOLICITAR + ['JEFE AUXILIAR INVENTARIO'])
def detalle_solicitud(id_solicitud):
    try:
        solicitud = SolicitudCompraService.obtener(id_solicitud)
        if not solicitud:
            return api_error("Solicitud no encontrada", status_code=404)
        return api_success(data=_ser_solicitud(solicitud))
    except Exception as e:
        logger.error(f"❌ Error consultando solicitud {id_solicitud}: {e}")
        return api_error("Error interno consultando la solicitud", status_code=500)


@compras_bp.route('/api/compras/solicitudes/<int:id_solicitud>/cancelar', methods=['PATCH'])
@require_role(ROLES_COMPRAS_SOLICITAR)
def cancelar_solicitud(id_solicitud):
    try:
        usuario = _obtener_usuario_activo()
        solicitud = SolicitudCompraService.cancelar(id_solicitud, usuario, es_admin=_es_admin())
        return api_success(data=_ser_solicitud(solicitud))
    except SolicitudCompraError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error cancelando solicitud {id_solicitud}: {e}")
        return api_error("Error interno cancelando la solicitud", status_code=500)


@compras_bp.route('/api/compras/solicitudes/<int:id_solicitud>/rechazar', methods=['PATCH'])
@require_role(ROLES_COMPRAS_ADMIN)
def rechazar_solicitud(id_solicitud):
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        solicitud = SolicitudCompraService.rechazar(id_solicitud, data.get('motivo'), usuario)
        return api_success(data=_ser_solicitud(solicitud))
    except SolicitudCompraError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error rechazando solicitud {id_solicitud}: {e}")
        return api_error("Error interno rechazando la solicitud", status_code=500)


# ----------------------------------------------------------------------
# Proveedores (catálogo, reutilizado de db_proveedores)
# ----------------------------------------------------------------------
@compras_bp.route('/api/compras/proveedores', methods=['GET'])
@require_role(ROLES_COMPRAS_ADMIN)
def listar_proveedores():
    try:
        proveedores = OrdenCompraService.listar_proveedores()
        return api_success(data=[_ser_proveedor(p) for p in proveedores])
    except Exception as e:
        logger.error(f"❌ Error listando proveedores: {e}")
        return api_error("Error interno listando proveedores", status_code=500)


# ----------------------------------------------------------------------
# Órdenes de Compra (Diego)
# ----------------------------------------------------------------------
@compras_bp.route('/api/compras/ordenes', methods=['POST'])
@require_role(ROLES_COMPRAS_ADMIN)
def crear_orden():
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        orden = OrdenCompraService.crear(
            proveedor_nit=data.get('proveedor_nit'),
            fecha_oc=data.get('fecha_oc'),
            creado_por=usuario,
            lineas=data.get('lineas') or [],
            nota=data.get('nota'),
            ids_solicitudes=data.get('ids_solicitudes'),
        )
        return api_success(data=_ser_orden(orden), status_code=201)
    except OrdenCompraError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error creando orden de compra: {e}")
        return api_error("Error interno creando la orden de compra", status_code=500)


@compras_bp.route('/api/compras/ordenes', methods=['GET'])
@require_role(ROLES_COMPRAS_ADMIN)
def listar_ordenes():
    try:
        ordenes = OrdenCompraService.listar(
            estado=request.args.get('estado'),
            proveedor_nit=request.args.get('proveedor_nit'),
            fecha_desde=request.args.get('fecha_desde'),
            fecha_hasta=request.args.get('fecha_hasta'),
        )
        return api_success(data=[_ser_orden(o) for o in ordenes])
    except Exception as e:
        logger.error(f"❌ Error listando órdenes de compra: {e}")
        return api_error("Error interno listando órdenes de compra", status_code=500)


@compras_bp.route('/api/compras/ordenes/pendientes_recepcion', methods=['GET'])
@require_role(ROLES_COMPRAS_RECEPCION)
def listar_ordenes_pendientes_recepcion():
    try:
        ordenes = OrdenCompraService.pendientes_recepcion()
        return api_success(data=[_ser_orden(o) for o in ordenes])
    except Exception as e:
        logger.error(f"❌ Error listando OC pendientes de recepción: {e}")
        return api_error("Error interno listando OC pendientes de recepción", status_code=500)


@compras_bp.route('/api/compras/ordenes/recibidas', methods=['GET'])
@require_role(ROLES_COMPRAS_RECEPCION)
def listar_ordenes_recibidas():
    try:
        ordenes = OrdenCompraService.recibidas_o_cerradas()
        return api_success(data=[_ser_orden(o) for o in ordenes])
    except Exception as e:
        logger.error(f"❌ Error listando OC recibidas: {e}")
        return api_error("Error interno listando OC recibidas", status_code=500)


@compras_bp.route('/api/compras/ordenes/lineas_batch', methods=['POST'])
@require_role(ROLES_COMPRAS_ADMIN + ['JEFE AUXILIAR INVENTARIO'])
def lineas_batch():
    """Líneas (con acumulado/pendiente/tolerancia) de varias OC en una sola
    consulta -- reemplaza el N+1 que hacía el frontend antes (una petición
    de detalle por cada tarjeta de la lista, lag real reportado 2026-09-16)."""
    data = request.get_json() or {}
    numeros_oc = data.get('numeros_oc') or []
    try:
        por_oc = OrdenCompraService.detalle_lineas_batch(numeros_oc)
        return api_success(data={
            numero_oc: [
                _ser_linea_orden(d['linea'], extra={
                    'cantidad_recibida_acumulada': d['cantidad_recibida_acumulada'],
                    'pendiente': d['pendiente'],
                    'dentro_tolerancia_baja': d['dentro_tolerancia_baja'],
                })
                for d in lineas
            ]
            for numero_oc, lineas in por_oc.items()
        })
    except Exception as e:
        logger.error(f"❌ Error consultando líneas en lote: {e}")
        return api_error("Error interno consultando líneas en lote", status_code=500)


@compras_bp.route('/api/compras/ordenes/<numero_oc>', methods=['GET'])
@require_role(ROLES_COMPRAS_ADMIN + ['JEFE AUXILIAR INVENTARIO'])
def detalle_orden(numero_oc):
    try:
        detalle = OrdenCompraService.detalle(numero_oc)
        if not detalle:
            return api_error("OC no encontrada", status_code=404)
        return api_success(data={
            'orden': _ser_orden(detalle['orden']),
            'lineas': [
                _ser_linea_orden(d['linea'], extra={
                    'cantidad_recibida_acumulada': d['cantidad_recibida_acumulada'],
                    'pendiente': d['pendiente'],
                    'dentro_tolerancia_baja': d['dentro_tolerancia_baja'],
                })
                for d in detalle['lineas']
            ],
        })
    except Exception as e:
        logger.error(f"❌ Error consultando OC {numero_oc}: {e}")
        return api_error("Error interno consultando la OC", status_code=500)


@compras_bp.route('/api/compras/ordenes/<numero_oc>/anular', methods=['PATCH'])
@require_role(ROLES_COMPRAS_ADMIN)
def anular_orden(numero_oc):
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        orden = OrdenCompraService.anular(numero_oc, data.get('motivo'), usuario)
        return api_success(data=_ser_orden(orden))
    except OrdenCompraError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error anulando OC {numero_oc}: {e}")
        return api_error("Error interno anulando la OC", status_code=500)


# ----------------------------------------------------------------------
# Recepción (Zoe)
# ----------------------------------------------------------------------
@compras_bp.route('/api/compras/ordenes/<numero_oc>/recepciones', methods=['POST'])
@require_role(ROLES_COMPRAS_RECEPCION)
def registrar_recepcion(numero_oc):
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        recepcion, lineas_creadas, lineas_con_exceso = RecepcionOCService.registrar_recepcion(
            numero_oc=numero_oc,
            fecha_recepcion=data.get('fecha_recepcion'),
            recibido_por=usuario,
            lineas=data.get('lineas') or [],
            estado_recepcion=data.get('estado_recepcion'),
            observaciones=data.get('observaciones'),
        )
        return api_success(data={
            'recepcion': _ser_recepcion(recepcion),
            'lineas': [_ser_linea_recepcion(l) for l in lineas_creadas],
            'lineas_con_exceso_tolerancia': lineas_con_exceso,
        }, status_code=201)
    except RecepcionOCError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error registrando recepción de OC {numero_oc}: {e}")
        return api_error("Error interno registrando la recepción", status_code=500)


@compras_bp.route('/api/compras/ordenes/<numero_oc>/recepciones', methods=['GET'])
@require_role(ROLES_COMPRAS_ADMIN + ['JEFE AUXILIAR INVENTARIO'])
def listar_recepciones(numero_oc):
    try:
        recepciones = RecepcionOCService.listar_recepciones(numero_oc)
        resultado = [
            {'recepcion': _ser_recepcion(r), 'lineas': [_ser_linea_recepcion(l) for l in lineas]}
            for r, lineas in recepciones
        ]
        return api_success(data=resultado)
    except Exception as e:
        logger.error(f"❌ Error listando recepciones de OC {numero_oc}: {e}")
        return api_error("Error interno listando recepciones", status_code=500)


# ----------------------------------------------------------------------
# Factura de Compra / conciliación (Diego)
# ----------------------------------------------------------------------
@compras_bp.route('/api/compras/ordenes/<numero_oc>/factura', methods=['POST'])
@require_role(ROLES_COMPRAS_ADMIN)
def cargar_factura(numero_oc):
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        factura = FacturaCompraService.cargar_factura(
            numero_oc=numero_oc,
            numero_factura=data.get('numero_factura'),
            fecha_factura=data.get('fecha_factura'),
            cargada_por=usuario,
            lineas=data.get('lineas') or [],
            observaciones=data.get('observaciones'),
        )
        return api_success(data=_ser_factura(factura), status_code=201)
    except FacturaCompraError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error cargando factura de OC {numero_oc}: {e}")
        return api_error("Error interno cargando la factura", status_code=500)


@compras_bp.route('/api/compras/ordenes/<numero_oc>/factura', methods=['GET'])
@require_role(ROLES_COMPRAS_ADMIN)
def obtener_factura(numero_oc):
    try:
        resultado = FacturaCompraService.obtener(numero_oc)
        if not resultado:
            return api_error("Esta OC todavía no tiene factura cargada", status_code=404)
        return api_success(data={
            'factura': _ser_factura(resultado['factura']),
            'lineas': [_ser_linea_factura(l) for l in resultado['lineas']],
        })
    except Exception as e:
        logger.error(f"❌ Error consultando factura de OC {numero_oc}: {e}")
        return api_error("Error interno consultando la factura", status_code=500)


# ----------------------------------------------------------------------
# Tránsito externo (Granallado / Zincado) -- Zoe
# ----------------------------------------------------------------------
@compras_bp.route('/api/compras/recepciones/<int:id_linea_recepcion>/transito', methods=['POST'])
@require_role(ROLES_COMPRAS_RECEPCION)
def enviar_a_transito(id_linea_recepcion):
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        transito = TransitoExternoService.enviar(
            id_linea_recepcion=id_linea_recepcion,
            proceso=data.get('proceso'),
            cantidad_enviada=data.get('cantidad_enviada'),
            fecha_envio=data.get('fecha_envio'),
            enviado_por=usuario,
            proveedor_proceso_nit=data.get('proveedor_proceso_nit'),
        )
        return api_success(data=_ser_transito(transito), status_code=201)
    except TransitoExternoError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error enviando línea de recepción {id_linea_recepcion} a tránsito externo: {e}")
        return api_error("Error interno enviando a tránsito externo", status_code=500)


@compras_bp.route('/api/compras/transito/<int:id_transito>/estado', methods=['PATCH'])
@require_role(ROLES_COMPRAS_RECEPCION)
def cambiar_estado_transito(id_transito):
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        transito = TransitoExternoService.cambiar_estado(
            id_transito=id_transito,
            nuevo_estado=data.get('estado'),
            usuario=usuario,
            observaciones=data.get('observaciones'),
        )
        return api_success(data=_ser_transito(transito))
    except TransitoExternoError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error cambiando estado de tránsito {id_transito}: {e}")
        return api_error("Error interno cambiando el estado del tránsito", status_code=500)


@compras_bp.route('/api/compras/transito/<int:id_transito>/retorno', methods=['POST'])
@require_role(ROLES_COMPRAS_RECEPCION)
def registrar_retorno_transito(id_transito):
    data = request.get_json() or {}
    try:
        usuario = _obtener_usuario_activo()
        transito = TransitoExternoService.registrar_retorno(
            id_transito=id_transito,
            cantidad_retornada=data.get('cantidad_retornada'),
            fecha_retorno=data.get('fecha_retorno'),
            retornado_por=usuario,
        )
        return api_success(data=_ser_transito(transito))
    except TransitoExternoError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error registrando retorno de tránsito {id_transito}: {e}")
        return api_error("Error interno registrando el retorno", status_code=500)


@compras_bp.route('/api/compras/transito', methods=['GET'])
@require_role(ROLES_COMPRAS_ADMIN + ['JEFE AUXILIAR INVENTARIO'])
def listar_transito():
    try:
        transitos = TransitoExternoService.listar(estado=request.args.get('estado'))
        return api_success(data=[_ser_transito(t) for t in transitos])
    except Exception as e:
        logger.error(f"❌ Error listando tránsito externo: {e}")
        return api_error("Error interno listando tránsito externo", status_code=500)


@compras_bp.route('/api/compras/transito/<int:id_transito>/historial', methods=['GET'])
@require_role(ROLES_COMPRAS_ADMIN + ['JEFE AUXILIAR INVENTARIO'])
def historial_transito(id_transito):
    try:
        historial = TransitoExternoService.historial(id_transito)
        return api_success(data=[_ser_historial_transito(h) for h in historial])
    except Exception as e:
        logger.error(f"❌ Error consultando historial de tránsito {id_transito}: {e}")
        return api_error("Error interno consultando el historial", status_code=500)
