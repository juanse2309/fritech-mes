"""
compras_recepcion_service.py
=============================
Módulo de Compras a Proveedores Externos (plan 2026-09-15). Etapa de
Recepción (Zoe) y sub-flujo de tránsito externo (Granallado/Zincado).

Regla de tolerancia por EXCESO (decisión del usuario 2026-09-15): si el
acumulado recibido de una línea excede lo pedido en más de 20 unidades, se
marca `excede_tolerancia` y se avisa a Diego por push -- dentro de la
tolerancia se registra normal, sin bloquear ni alertar.

Regla de tolerancia por DEFECTO (pedida 2026-09-16, "muchas veces no llega
completo"): si a una línea le faltan 10 unidades o menos para completar lo
pedido, se considera resuelta igual -- para que una OC no se quede
esperando en "Pendientes de recepción" para siempre por un faltante de 2-3
unidades que el proveedor nunca va a mandar. Es independiente de la de
exceso: una línea puede estar "cerrada por tolerancia baja" y aun así, si
por otra recepción posterior llega el resto, sigue sumando normal.
"""
import logging
from datetime import datetime, date

from sqlalchemy.exc import SQLAlchemyError

from backend.core.sql_database import db
from backend.models.sql_models import (
    OrdenCompraProveedor, LineaOrdenCompra, RecepcionOC, LineaRecepcionOC,
    TransitoExternoOC, HistorialTransitoExternoOC, AppConfig, Usuario,
)
from backend.utils.time_utils import get_colombia_time
from backend.utils.auth_middleware import ROL_ADMINS
from backend.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

TOLERANCIA_DEFECTO = 20
TOLERANCIA_BAJA_DEFECTO = 10
PROCESOS_VALIDOS = ('GRANALLADO', 'ZINCADO')


def _parse_fecha(valor, defecto=None):
    if not valor:
        return defecto
    if isinstance(valor, date):
        return valor
    return datetime.fromisoformat(str(valor)[:10]).date()


def _tolerancia_exceso_recepcion():
    fila = db.session.get(AppConfig, 'compras.tolerancia_exceso_recepcion')
    if fila and fila.valor not in (None, ''):
        try:
            return float(fila.valor)
        except (TypeError, ValueError):
            pass
    return TOLERANCIA_DEFECTO


def _tolerancia_baja_recepcion():
    fila = db.session.get(AppConfig, 'compras.tolerancia_baja_recepcion')
    if fila and fila.valor not in (None, ''):
        try:
            return float(fila.valor)
        except (TypeError, ValueError):
            pass
    return TOLERANCIA_BAJA_DEFECTO


class RecepcionOCError(Exception):
    """Error de negocio registrando una recepción de mercancía."""


class RecepcionOCService:

    @staticmethod
    def _recalcular_estado_oc(orden):
        """Recalcula el estado de la OC a partir del acumulado real de
        TODAS sus líneas (nunca desde un solo evento aislado, porque puede
        haber varias recepciones parciales). Una línea se considera
        resuelta cuando lo recibido + lo rechazado ya cubre lo pedido --
        así una línea 100% rechazada no deja la OC en limbo (ver plan de
        pruebas, caso 5)."""
        lineas = LineaOrdenCompra.query.filter_by(id_oc=orden.id).all()
        tolerancia_baja = _tolerancia_baja_recepcion()
        todas_resueltas = True
        hubo_recibo_real = False
        hubo_algo = False

        for linea in lineas:
            recibido = db.session.query(
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_recibida), 0)
            ).filter(LineaRecepcionOC.id_linea_oc == linea.id).scalar()
            rechazado = db.session.query(
                db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_rechazada), 0)
            ).filter(LineaRecepcionOC.id_linea_oc == linea.id).scalar()
            recibido = float(recibido or 0)
            rechazado = float(rechazado or 0)

            if recibido > 0:
                hubo_recibo_real = True
            if recibido > 0 or rechazado > 0:
                hubo_algo = True
            # Una línea con faltante <= tolerancia_baja se da por resuelta
            # igual -- sin esto, una OC se queda en "Pendientes de recepción"
            # para siempre por 2-3 unidades que el proveedor nunca completa.
            # OJO: solo aplica si la línea SÍ tuvo algún movimiento -- una
            # línea nunca tocada (recibido=rechazado=0) no puede darse por
            # resuelta solo porque pidieron pocas unidades.
            pendiente = float(linea.cantidad_pedida) - recibido - rechazado
            tiene_movimiento = recibido > 0 or rechazado > 0
            if pendiente > 0 and not (tiene_movimiento and pendiente <= tolerancia_baja):
                todas_resueltas = False

        if todas_resueltas and hubo_algo:
            orden.estado = 'RECIBIDA_TOTAL' if hubo_recibo_real else 'RECHAZADA'
        elif hubo_algo:
            orden.estado = 'PARCIALMENTE_RECIBIDA'
        # si no hubo nada (no debería pasar aquí), no se toca el estado

    @staticmethod
    def registrar_recepcion(numero_oc, fecha_recepcion, recibido_por, lineas, estado_recepcion, observaciones=None):
        """
        lineas: lista de dicts {id_linea_oc, cantidad_recibida, cantidad_rechazada, motivo_rechazo}.
        Devuelve (recepcion, lineas_con_exceso) -- el caller (route) decide
        si avisar o no; aquí ya se dispara el push.
        """
        if not lineas:
            raise RecepcionOCError("La recepción necesita al menos una línea")
        if estado_recepcion not in ('RECIBIDA_TOTAL', 'RECIBIDA_PARCIAL', 'RECHAZADA'):
            raise RecepcionOCError(f"estado_recepcion inválido: {estado_recepcion!r}")

        try:
            orden = OrdenCompraProveedor.query.filter_by(numero_oc=numero_oc).first()
            if not orden:
                raise RecepcionOCError(f"OC {numero_oc!r} no existe")
            if orden.estado in ('ANULADA',):
                raise RecepcionOCError(f"No se puede recibir contra una OC {orden.estado}")

            recepcion = RecepcionOC(
                id_oc=orden.id,
                numero_oc=numero_oc,
                fecha_recepcion=_parse_fecha(fecha_recepcion, get_colombia_time().date()),
                estado_recepcion=estado_recepcion,
                recibido_por=recibido_por,
                observaciones=(observaciones or '').strip() or None,
            )
            db.session.add(recepcion)
            db.session.flush()

            tolerancia = _tolerancia_exceso_recepcion()
            lineas_con_exceso = []
            lineas_creadas = []

            for linea_payload in lineas:
                id_linea_oc = linea_payload.get('id_linea_oc')
                linea_oc = db.session.get(LineaOrdenCompra, id_linea_oc) if id_linea_oc else None
                if not linea_oc or linea_oc.id_oc != orden.id:
                    raise RecepcionOCError(f"La línea {id_linea_oc!r} no pertenece a la OC {numero_oc!r}")

                cantidad_recibida = float(linea_payload.get('cantidad_recibida') or 0)
                cantidad_rechazada = float(linea_payload.get('cantidad_rechazada') or 0)

                previo = db.session.query(
                    db.func.coalesce(db.func.sum(LineaRecepcionOC.cantidad_recibida), 0)
                ).filter(LineaRecepcionOC.id_linea_oc == id_linea_oc).scalar()
                acumulado = float(previo or 0) + cantidad_recibida
                exceso = acumulado - float(linea_oc.cantidad_pedida)
                excede_tolerancia = exceso > tolerancia

                linea_recepcion = LineaRecepcionOC(
                    id_recepcion=recepcion.id,
                    id_linea_oc=id_linea_oc,
                    cantidad_recibida=cantidad_recibida,
                    cantidad_rechazada=cantidad_rechazada,
                    motivo_rechazo=(linea_payload.get('motivo_rechazo') or '').strip() or None,
                    excede_tolerancia=excede_tolerancia,
                )
                db.session.add(linea_recepcion)
                lineas_creadas.append(linea_recepcion)

                if excede_tolerancia:
                    lineas_con_exceso.append({
                        'descripcion': linea_oc.descripcion,
                        'cantidad_pedida': float(linea_oc.cantidad_pedida),
                        'cantidad_recibida_acumulada': acumulado,
                        'exceso': exceso,
                    })

            db.session.flush()  # asigna .id a cada LineaRecepcionOC antes del commit
            RecepcionOCService._recalcular_estado_oc(orden)
            db.session.commit()

            if lineas_con_exceso:
                RecepcionOCService._avisar_exceso_a_admins(numero_oc, lineas_con_exceso)

            return recepcion, lineas_creadas, lineas_con_exceso
        except RecepcionOCError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error registrando recepción de OC {numero_oc}: {e}")
            raise

    @staticmethod
    def listar_recepciones(numero_oc):
        """Historial de eventos de recepción de una OC, cada uno con sus
        líneas -- usado por GET /api/compras/ordenes/<numero_oc>/recepciones
        (la ruta solo serializa, la consulta vive aquí)."""
        recepciones = RecepcionOC.query.filter_by(numero_oc=numero_oc).order_by(RecepcionOC.creado_en.asc()).all()
        resultado = []
        for r in recepciones:
            lineas = LineaRecepcionOC.query.filter_by(id_recepcion=r.id).all()
            resultado.append((r, lineas))
        return resultado

    @staticmethod
    def _avisar_exceso_a_admins(numero_oc, lineas_con_exceso):
        """Best-effort: un fallo de push nunca debe afectar la recepción ya
        confirmada (commit ya ocurrió antes de llamar esto)."""
        try:
            filtros = [Usuario.rol.ilike(r) for r in ROL_ADMINS]
            admins = Usuario.query.filter(Usuario.activo == True).filter(db.or_(*filtros)).all()
            resumen = "; ".join(
                f"{l['descripcion']}: recibido {l['cantidad_recibida_acumulada']:g} de {l['cantidad_pedida']:g} pedido"
                for l in lineas_con_exceso
            )
            for admin in admins:
                NotificationService.enviar_notificacion_push(
                    user_id=admin.username,
                    titulo=f"Exceso en recepción de {numero_oc}",
                    cuerpo=f"Llegó más de lo pedido (fuera de tolerancia): {resumen}",
                    url_destino='/',
                )
        except Exception as e:
            logger.warning(f"[Compras] No se pudo avisar el exceso de recepción de {numero_oc}: {e}")


class TransitoExternoError(Exception):
    """Error de negocio del sub-flujo de maquila externa (Granallado/Zincado)."""


class TransitoExternoService:

    @staticmethod
    def _escribir_historial(id_transito, estado_anterior, estado_nuevo, usuario, observaciones=None):
        db.session.add(HistorialTransitoExternoOC(
            id_transito=id_transito,
            estado_anterior=estado_anterior,
            estado_nuevo=estado_nuevo,
            usuario=usuario,
            observaciones=observaciones,
        ))

    @staticmethod
    def enviar(id_linea_recepcion, proceso, cantidad_enviada, fecha_envio, enviado_por, proveedor_proceso_nit=None):
        proceso = (proceso or '').strip().upper()
        if proceso not in PROCESOS_VALIDOS:
            raise TransitoExternoError(f"proceso debe ser uno de {PROCESOS_VALIDOS}")
        cantidad_enviada = float(cantidad_enviada or 0)
        if cantidad_enviada <= 0:
            raise TransitoExternoError("cantidad_enviada debe ser mayor a 0")

        try:
            linea_recepcion = db.session.get(LineaRecepcionOC, id_linea_recepcion)
            if not linea_recepcion:
                raise TransitoExternoError(f"Línea de recepción {id_linea_recepcion!r} no existe")

            ya_enviado = db.session.query(
                db.func.coalesce(db.func.sum(TransitoExternoOC.cantidad_enviada), 0)
            ).filter(TransitoExternoOC.id_linea_recepcion_oc == id_linea_recepcion).scalar()
            disponible = float(linea_recepcion.cantidad_recibida) - float(ya_enviado or 0)
            if cantidad_enviada > disponible:
                raise TransitoExternoError(
                    f"No hay suficiente cantidad recibida disponible para enviar a {proceso} "
                    f"(disponible: {disponible:g}, solicitado: {cantidad_enviada:g})"
                )

            transito = TransitoExternoOC(
                id_linea_recepcion_oc=id_linea_recepcion,
                proceso=proceso,
                proveedor_proceso_nit=(proveedor_proceso_nit or '').strip() or None,
                cantidad_enviada=cantidad_enviada,
                fecha_envio=_parse_fecha(fecha_envio, get_colombia_time().date()),
                enviado_por=enviado_por,
                estado='ENVIADO',
            )
            db.session.add(transito)
            db.session.flush()

            TransitoExternoService._escribir_historial(transito.id, None, 'ENVIADO', enviado_por)
            db.session.commit()
            return transito
        except TransitoExternoError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error enviando a {proceso}: {e}")
            raise

    @staticmethod
    def cambiar_estado(id_transito, nuevo_estado, usuario, observaciones=None):
        """Ciclo simple del chip (ENVIADO <-> EN_PROCESO). El retorno con
        cantidad va por registrar_retorno, no por aquí -- necesita capturar
        cantidad_retornada, no es un simple cambio de etiqueta."""
        nuevo_estado = (nuevo_estado or '').strip().upper()
        if nuevo_estado not in ('ENVIADO', 'EN_PROCESO'):
            raise TransitoExternoError(
                "Para marcar el retorno usa el endpoint de retorno, no cambiar_estado"
            )
        try:
            transito = db.session.get(TransitoExternoOC, id_transito)
            if not transito:
                raise TransitoExternoError(f"Tránsito {id_transito!r} no existe")
            if transito.estado in ('RETORNADO', 'RETORNADO_PARCIAL'):
                raise TransitoExternoError("Este tránsito ya fue retornado, no se puede cambiar su estado")

            anterior = transito.estado
            transito.estado = nuevo_estado
            TransitoExternoService._escribir_historial(id_transito, anterior, nuevo_estado, usuario, observaciones)
            db.session.commit()
            return transito
        except TransitoExternoError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error cambiando estado de tránsito {id_transito}: {e}")
            raise

    @staticmethod
    def registrar_retorno(id_transito, cantidad_retornada, fecha_retorno, retornado_por):
        cantidad_retornada = float(cantidad_retornada or 0)
        if cantidad_retornada < 0:
            raise TransitoExternoError("cantidad_retornada no puede ser negativa")

        try:
            transito = db.session.get(TransitoExternoOC, id_transito)
            if not transito:
                raise TransitoExternoError(f"Tránsito {id_transito!r} no existe")
            if transito.estado in ('RETORNADO', 'RETORNADO_PARCIAL'):
                raise TransitoExternoError("Este tránsito ya tiene un retorno registrado")

            diferencia = float(transito.cantidad_enviada) - cantidad_retornada
            anterior = transito.estado
            nuevo_estado = 'RETORNADO' if diferencia == 0 else 'RETORNADO_PARCIAL'

            transito.cantidad_retornada = cantidad_retornada
            transito.fecha_retorno = _parse_fecha(fecha_retorno, get_colombia_time().date())
            transito.retornado_por = retornado_por
            transito.diferencia_envio_retorno = diferencia
            transito.estado = nuevo_estado

            observaciones = f"Retornó {cantidad_retornada:g} de {float(transito.cantidad_enviada):g} enviado(s)"
            TransitoExternoService._escribir_historial(id_transito, anterior, nuevo_estado, retornado_por, observaciones)
            db.session.commit()
            return transito
        except TransitoExternoError:
            db.session.rollback()
            raise
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"[Compras] Error registrando retorno de tránsito {id_transito}: {e}")
            raise

    @staticmethod
    def listar(estado=None):
        query = TransitoExternoOC.query
        if estado:
            query = query.filter(TransitoExternoOC.estado == estado.upper())
        return query.order_by(TransitoExternoOC.fecha_envio.desc()).all()

    @staticmethod
    def historial(id_transito):
        return HistorialTransitoExternoOC.query.filter_by(
            id_transito=id_transito
        ).order_by(HistorialTransitoExternoOC.fecha.asc()).all()
