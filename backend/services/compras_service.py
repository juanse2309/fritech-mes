"""
compras_service.py
===================
Módulo de Compras a Proveedores Externos (plan 2026-09-15). Etapas
Solicitud (Albeiro) y Orden de Compra + cierre con Factura (Diego).
Recepción y Tránsito externo (Zoe) viven en compras_recepcion_service.py.

Reemplaza al módulo "Procura" (retirado 2026-08-20 por un endpoint que
mutaba inventario/OC sin ningún control de acceso) -- este servicio no
decide autorización (eso vive en @require_role en compras_routes.py),
pero toda escritura va protegida con try/except + rollback, siguiendo la
regla del proyecto.
"""
import logging
from datetime import datetime, date

from sqlalchemy.exc import SQLAlchemyError

from backend.core.sql_database import db
from backend.models.sql_models import (
    SolicitudCompra, OrdenCompraProveedor, LineaOrdenCompra,
    LineaRecepcionOC, FacturaCompraOC, LineaFacturaCompraOC, DbProveedor,
)
from backend.utils.time_utils import get_colombia_time
from backend.services.oc_numerador_service import OcNumeradorService

logger = logging.getLogger(__name__)


def _parse_fecha(valor, defecto=None):
    if not valor:
        return defecto
    if isinstance(valor, date):
        return valor
    return datetime.fromisoformat(str(valor)[:10]).date()


class SolicitudCompraError(Exception):
    """Error de negocio de una solicitud (estado inválido, no encontrada...)."""


class SolicitudCompraService:

    @staticmethod
    def crear(item_descripcion, solicitado_por, codigo_producto=None, urgencia='NORMAL', nota=None, departamento=None):
        item_descripcion = (item_descripcion or '').strip()
        codigo_producto = (codigo_producto or '').strip()
        if not item_descripcion or not codigo_producto:
            raise SolicitudCompraError(
                "Debes elegir un producto del catálogo -- no se acepta texto libre sin seleccionar de la lista"
            )

        try:
            solicitud = SolicitudCompra(
                item_descripcion=item_descripcion,
                codigo_producto=codigo_producto,
                urgencia=(urgencia or 'NORMAL').strip().upper(),
                nota=(nota or '').strip() or None,
                solicitado_por=solicitado_por,
                departamento=departamento,
                estado='PENDIENTE',
            )
            db.session.add(solicitud)
            db.session.commit()
            return solicitud
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error creando solicitud de compra: {e}")
            raise

    @staticmethod
    def listar(usuario=None, solo_propias=False, estado=None):
        query = SolicitudCompra.query
        if solo_propias and usuario:
            query = query.filter(SolicitudCompra.solicitado_por == usuario)
        if estado:
            query = query.filter(SolicitudCompra.estado == estado.upper())
        return query.order_by(SolicitudCompra.creado_en.desc()).all()

    @staticmethod
    def obtener(id_solicitud):
        return db.session.get(SolicitudCompra, id_solicitud)

    @staticmethod
    def cancelar(id_solicitud, usuario, es_admin=False):
        try:
            solicitud = db.session.get(SolicitudCompra, id_solicitud)
            if not solicitud:
                raise SolicitudCompraError("Solicitud no encontrada")
            if not es_admin and solicitud.solicitado_por != usuario:
                raise SolicitudCompraError("Solo quien creó la solicitud puede cancelarla")
            if solicitud.estado != 'PENDIENTE':
                raise SolicitudCompraError(
                    f"No se puede cancelar: la solicitud ya está en estado {solicitud.estado}"
                )
            solicitud.estado = 'CANCELADA'
            solicitud.resuelto_por = usuario
            solicitud.resuelto_en = get_colombia_time()
            db.session.commit()
            return solicitud
        except SolicitudCompraError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error cancelando solicitud {id_solicitud}: {e}")
            raise

    @staticmethod
    def rechazar(id_solicitud, motivo, usuario):
        motivo = (motivo or '').strip()
        if not motivo:
            raise SolicitudCompraError("Rechazar una solicitud requiere indicar un motivo")
        try:
            solicitud = db.session.get(SolicitudCompra, id_solicitud)
            if not solicitud:
                raise SolicitudCompraError("Solicitud no encontrada")
            if solicitud.estado != 'PENDIENTE':
                raise SolicitudCompraError(
                    f"No se puede rechazar: la solicitud ya está en estado {solicitud.estado}"
                )
            solicitud.estado = 'RECHAZADA'
            solicitud.motivo_rechazo = motivo
            solicitud.resuelto_por = usuario
            solicitud.resuelto_en = get_colombia_time()
            db.session.commit()
            return solicitud
        except SolicitudCompraError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error rechazando solicitud {id_solicitud}: {e}")
            raise


class OrdenCompraError(Exception):
    """Error de negocio de una Orden de Compra (proveedor inválido, sin líneas...)."""


class OrdenCompraService:

    @staticmethod
    def listar_proveedores():
        return DbProveedor.query.order_by(DbProveedor.proveedores).all()

    @staticmethod
    def crear(proveedor_nit, fecha_oc, creado_por, lineas, nota=None, ids_solicitudes=None):
        """
        lineas: lista de dicts {descripcion, cantidad_pedida, codigo_producto,
        unidad_medida, valor_unitario, id_solicitud}. ids_solicitudes:
        solicitudes de Albeiro que se marcan EN_OC (independiente de si
        cada línea trae o no su propio id_solicitud).
        """
        proveedor_nit = (proveedor_nit or '').strip()
        if not proveedor_nit:
            raise OrdenCompraError("Debes indicar el proveedor")
        if not lineas:
            raise OrdenCompraError("La orden de compra necesita al menos una línea")

        proveedor = db.session.get(DbProveedor, proveedor_nit)
        if not proveedor:
            raise OrdenCompraError(f"Proveedor {proveedor_nit!r} no existe en el catálogo")

        try:
            numero_oc, consecutivo = OcNumeradorService.generar_siguiente_numero_oc(db.session)

            orden = OrdenCompraProveedor(
                numero_oc=numero_oc,
                consecutivo=consecutivo,
                proveedor_nit=proveedor_nit,
                proveedor_nombre=proveedor.proveedores,
                fecha_oc=_parse_fecha(fecha_oc, get_colombia_time().date()),
                estado='ABIERTA',
                nota=(nota or '').strip() or None,
                creado_por=creado_por,
            )
            db.session.add(orden)
            db.session.flush()  # necesita orden.id para las líneas

            for linea in lineas:
                cantidad = linea.get('cantidad_pedida')
                descripcion = (linea.get('descripcion') or '').strip()
                if not descripcion or cantidad is None or float(cantidad) <= 0:
                    raise OrdenCompraError("Cada línea necesita descripción y cantidad pedida mayor a 0")
                db.session.add(LineaOrdenCompra(
                    id_oc=orden.id,
                    numero_oc=numero_oc,
                    codigo_producto=(linea.get('codigo_producto') or '').strip() or None,
                    descripcion=descripcion,
                    cantidad_pedida=cantidad,
                    unidad_medida=(linea.get('unidad_medida') or 'Und.').strip(),
                    valor_unitario=linea.get('valor_unitario'),
                    id_solicitud=linea.get('id_solicitud'),
                ))

            ids_solicitudes = set(ids_solicitudes or [])
            for linea in lineas:
                if linea.get('id_solicitud'):
                    ids_solicitudes.add(linea['id_solicitud'])

            if ids_solicitudes:
                solicitudes = SolicitudCompra.query.filter(
                    SolicitudCompra.id.in_(ids_solicitudes)
                ).all()
                for sol in solicitudes:
                    if sol.estado != 'PENDIENTE':
                        raise OrdenCompraError(
                            f"La solicitud #{sol.id} ya no está pendiente (estado actual: {sol.estado})"
                        )
                    sol.estado = 'EN_OC'
                    sol.id_oc_vinculada = numero_oc
                    sol.resuelto_por = creado_por
                    sol.resuelto_en = get_colombia_time()

            db.session.commit()
            return orden
        except OrdenCompraError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error creando orden de compra: {e}")
            raise

    @staticmethod
    def listar(estado=None, proveedor_nit=None, fecha_desde=None, fecha_hasta=None):
        query = OrdenCompraProveedor.query
        if estado:
            query = query.filter(OrdenCompraProveedor.estado == estado.upper())
        if proveedor_nit:
            query = query.filter(OrdenCompraProveedor.proveedor_nit == proveedor_nit)
        if fecha_desde:
            query = query.filter(OrdenCompraProveedor.fecha_oc >= _parse_fecha(fecha_desde))
        if fecha_hasta:
            query = query.filter(OrdenCompraProveedor.fecha_oc <= _parse_fecha(fecha_hasta))
        return query.order_by(OrdenCompraProveedor.creado_en.desc()).all()

    @staticmethod
    def pendientes_recepcion():
        return OrdenCompraProveedor.query.filter(
            OrdenCompraProveedor.estado.in_(['ABIERTA', 'PARCIALMENTE_RECIBIDA'])
        ).order_by(OrdenCompraProveedor.fecha_oc.asc()).all()

    @staticmethod
    def recibidas_o_cerradas():
        """OC que ya salieron de 'pendientes de recepción' (recibidas del
        todo o rechazadas). Sin esto, Zoe pierde de vista una OC apenas
        termina de recibirla -- no tiene ningún otro lugar donde volver a
        verla (bug real reportado 2026-09-16: 'en recepción... también
        desaparecía')."""
        return OrdenCompraProveedor.query.filter(
            OrdenCompraProveedor.estado.in_(['RECIBIDA_TOTAL', 'RECHAZADA'])
        ).order_by(OrdenCompraProveedor.creado_en.desc()).limit(50).all()

    @staticmethod
    def obtener_por_numero(numero_oc):
        return OrdenCompraProveedor.query.filter_by(numero_oc=numero_oc).first()

    @staticmethod
    def detalle(numero_oc):
        """Encabezado + líneas con acumulado recibido/pendiente por línea
        (SUM sobre LineaRecepcionOC, nunca desde un solo evento)."""
        orden = OrdenCompraService.obtener_por_numero(numero_oc)
        if not orden:
            return None

        from backend.services.compras_recepcion_service import _tolerancia_baja_recepcion
        tolerancia_baja = _tolerancia_baja_recepcion()

        lineas = LineaOrdenCompra.query.filter_by(id_oc=orden.id).all()
        detalle_lineas = []
        for linea in lineas:
            recibido = db.session.query(
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_recibida), 0)
            ).filter(LineaRecepcionOC.id_linea_oc == linea.id).scalar()
            rechazado = db.session.query(
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_rechazada), 0)
            ).filter(LineaRecepcionOC.id_linea_oc == linea.id).scalar()
            recibido = float(recibido or 0)
            rechazado = float(rechazado or 0)
            pendiente = float(linea.cantidad_pedida) - recibido - rechazado
            detalle_lineas.append({
                'linea': linea,
                'cantidad_recibida_acumulada': recibido,
                'pendiente': pendiente,
                # Faltante pequeño ya perdonado por la tolerancia baja (ver
                # RecepcionOCService._recalcular_estado_oc) -- se muestra en
                # la tarjeta para que no parezca que sobraron unidades sin
                # explicación cuando la OC ya cerró como RECIBIDA_TOTAL.
                'dentro_tolerancia_baja': 0 < pendiente <= tolerancia_baja and (recibido > 0 or rechazado > 0),
            })
        return {'orden': orden, 'lineas': detalle_lineas}

    @staticmethod
    def detalle_lineas_batch(numeros_oc):
        """Igual que detalle() pero para varias OC a la vez, en dos consultas
        agregadas en vez de N+1 -- el frontend pedía el detalle de cada OC
        por separado para armar cada tarjeta (Pendientes de recepción,
        Órdenes de Compra, Ya recibidas), disparando una petición HTTP por
        tarjeta cada vez que se abría esa pestaña (lag real reportado
        2026-09-16 con varias decenas de OC)."""
        if not numeros_oc:
            return {}

        from backend.services.compras_recepcion_service import _tolerancia_baja_recepcion
        tolerancia_baja = _tolerancia_baja_recepcion()

        lineas = LineaOrdenCompra.query.filter(LineaOrdenCompra.numero_oc.in_(numeros_oc)).all()
        ids_linea = [l.id for l in lineas]

        acumulados = {}
        if ids_linea:
            filas = db.session.query(
                LineaRecepcionOC.id_linea_oc,
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_recibida), 0),
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_rechazada), 0),
            ).filter(LineaRecepcionOC.id_linea_oc.in_(ids_linea)).group_by(LineaRecepcionOC.id_linea_oc).all()
            acumulados = {id_linea: (float(rec or 0), float(rech or 0)) for id_linea, rec, rech in filas}

        resultado = {}
        for linea in lineas:
            recibido, rechazado = acumulados.get(linea.id, (0.0, 0.0))
            pendiente = float(linea.cantidad_pedida) - recibido - rechazado
            resultado.setdefault(linea.numero_oc, []).append({
                'linea': linea,
                'cantidad_recibida_acumulada': recibido,
                'pendiente': pendiente,
                'dentro_tolerancia_baja': 0 < pendiente <= tolerancia_baja and (recibido > 0 or rechazado > 0),
            })
        return resultado

    @staticmethod
    def timeline(numero_oc):
        """Todo el recorrido de una OC en una sola llamada: solicitud(es)
        de origen -> OC -> recepciones -> tránsito externo -> factura.
        Pensado para el panel de trazabilidad del frontend (plan
        2026-09-21) -- sin esto el frontend tendría que armar la misma
        historia pegándole a 4-5 endpoints sueltos."""
        orden = OrdenCompraService.obtener_por_numero(numero_oc)
        if not orden:
            return None

        from backend.services.compras_recepcion_service import (
            RecepcionOCService, TransitoExternoService, _tolerancia_baja_recepcion,
        )

        tolerancia_baja = _tolerancia_baja_recepcion()
        lineas = LineaOrdenCompra.query.filter_by(id_oc=orden.id).all()

        ids_solicitudes = {l.id_solicitud for l in lineas if l.id_solicitud}
        solicitudes_origen = (
            SolicitudCompra.query.filter(SolicitudCompra.id.in_(ids_solicitudes)).all()
            if ids_solicitudes else []
        )

        detalle_lineas = []
        for linea in lineas:
            recibido = db.session.query(
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_recibida), 0)
            ).filter(LineaRecepcionOC.id_linea_oc == linea.id).scalar()
            rechazado = db.session.query(
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_rechazada), 0)
            ).filter(LineaRecepcionOC.id_linea_oc == linea.id).scalar()
            recibido = float(recibido or 0)
            rechazado = float(rechazado or 0)
            pendiente = float(linea.cantidad_pedida) - recibido - rechazado
            detalle_lineas.append({
                'linea': linea,
                'cantidad_recibida_acumulada': recibido,
                'pendiente': pendiente,
                'dentro_tolerancia_baja': 0 < pendiente <= tolerancia_baja and (recibido > 0 or rechazado > 0),
            })

        recepciones = RecepcionOCService.listar_recepciones(numero_oc)
        ids_lineas_recepcion = [lr.id for _, lineas_r in recepciones for lr in lineas_r]
        transitos_raw = TransitoExternoService.por_lineas_recepcion(ids_lineas_recepcion)
        transitos = [(t, TransitoExternoService.historial(t.id)) for t in transitos_raw]

        factura = FacturaCompraService.obtener(numero_oc)

        return {
            'orden': orden,
            'solicitudes_origen': solicitudes_origen,
            'lineas': detalle_lineas,
            'recepciones': recepciones,
            'transitos': transitos,
            'factura': factura,
        }

    @staticmethod
    def anular(numero_oc, motivo, usuario):
        motivo = (motivo or '').strip()
        if not motivo:
            raise OrdenCompraError("Anular una OC requiere indicar un motivo")
        try:
            orden = OrdenCompraService.obtener_por_numero(numero_oc)
            if not orden:
                raise OrdenCompraError(f"OC {numero_oc!r} no existe")
            if orden.estado in ('RECIBIDA_TOTAL', 'ANULADA'):
                raise OrdenCompraError(f"No se puede anular una OC en estado {orden.estado}")
            orden.estado = 'ANULADA'
            orden.anulada_motivo = f"{motivo} (por {usuario})"
            db.session.commit()
            return orden
        except OrdenCompraError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error anulando OC {numero_oc}: {e}")
            raise


class FacturaCompraError(Exception):
    """Error de negocio conciliando la Factura de Compra (FC) contra la OC."""


class FacturaCompraService:
    """Cierre del ciclo: Diego carga la FC (ya autorizada por Contabilidad
    fuera de FRITECH) y el sistema la concilia contra lo que Zoe contó."""

    @staticmethod
    def cargar_factura(numero_oc, numero_factura, fecha_factura, cargada_por, lineas, observaciones=None):
        """lineas: lista de dicts {id_linea_oc, cantidad_facturada, valor_unitario_facturado}."""
        numero_factura = (numero_factura or '').strip()
        if not numero_factura:
            raise FacturaCompraError("Debes indicar el número de la factura (FC)")
        if not lineas:
            raise FacturaCompraError("La factura necesita al menos una línea")

        try:
            orden = OrdenCompraService.obtener_por_numero(numero_oc)
            if not orden:
                raise FacturaCompraError(f"OC {numero_oc!r} no existe")

            existente = FacturaCompraOC.query.filter_by(id_oc=orden.id).first()
            if existente:
                raise FacturaCompraError(
                    f"La OC {numero_oc!r} ya tiene una factura cargada ({existente.numero_factura})"
                )

            factura = FacturaCompraOC(
                id_oc=orden.id,
                numero_oc=numero_oc,
                numero_factura=numero_factura,
                fecha_factura=_parse_fecha(fecha_factura, get_colombia_time().date()),
                cargada_por=cargada_por,
                observaciones=(observaciones or '').strip() or None,
            )
            db.session.add(factura)
            db.session.flush()  # necesita factura.id

            hay_discrepancia = False
            for linea in lineas:
                id_linea_oc = linea.get('id_linea_oc')
                cantidad_facturada = linea.get('cantidad_facturada')
                if id_linea_oc is None or cantidad_facturada is None:
                    raise FacturaCompraError("Cada línea de la factura necesita id_linea_oc y cantidad_facturada")

                recibido = db.session.query(
                    db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_recibida), 0)
                ).filter(LineaRecepcionOC.id_linea_oc == id_linea_oc).scalar()

                diferencia = float(cantidad_facturada) - float(recibido or 0)
                if diferencia != 0:
                    hay_discrepancia = True

                db.session.add(LineaFacturaCompraOC(
                    id_factura=factura.id,
                    id_linea_oc=id_linea_oc,
                    cantidad_facturada=cantidad_facturada,
                    valor_unitario_facturado=linea.get('valor_unitario_facturado'),
                    diferencia_vs_recibido=diferencia,
                ))

            factura.estado_conciliacion = 'DISCREPANCIA' if hay_discrepancia else 'COINCIDE'
            db.session.commit()
            return factura
        except FacturaCompraError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error cargando factura de OC {numero_oc}: {e}")
            raise

    @staticmethod
    def obtener(numero_oc):
        orden = OrdenCompraService.obtener_por_numero(numero_oc)
        if not orden:
            return None
        factura = FacturaCompraOC.query.filter_by(id_oc=orden.id).first()
        if not factura:
            return None
        lineas = LineaFacturaCompraOC.query.filter_by(id_factura=factura.id).all()
        return {'factura': factura, 'lineas': lineas}
