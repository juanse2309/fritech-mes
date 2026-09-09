import os
import uuid
import logging
from flask import Blueprint, request
from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.utils.auth_middleware import require_role, require_login, ROL_ADMINS, ROL_JEFES, _obtener_usuario_activo
from backend.services.audit_service import OwnershipMismatchException, ValidadorRequeridoException, TurnoInvalidoException
from backend.services.inyeccion_service import InyeccionService, LoteInyeccionNoEncontradoException, ProgramacionNoEncontradaException

logger = logging.getLogger(__name__)
inyeccion_bp = Blueprint('inyeccion_bp', __name__)

ROLES_INYECCION_ESCRITURA = ROL_ADMINS + ROL_JEFES + ['INYECCION', 'AUXILIAR INVENTARIO', 'INVENTARIO', 'STAFF FRIMETALS', 'CALIDAD', 'SUPERVISOR']
ROLES_MES_INYECCION = ROL_ADMINS + ROL_JEFES + ['INYECCION', 'ENSAMBLE']


@inyeccion_bp.route('/api/inyeccion/lote', methods=['POST'])
@require_role(ROLES_INYECCION_ESCRITURA)
def registrar_inyeccion_lote():
    """
    Registro de un lote de PRODUCCIÓN de Inyección (controller delgado).
    Parsea el request, delega toda la orquestación a InyeccionService.registrar_lote
    y traduce el resultado (o las excepciones de negocio) a JSON.

    No valida lotes: ese flujo es exclusivo de /api/inyeccion/validar/<id>, por lo
    que aquí no puede lanzarse ValidadorRequeridoException.

    RBAC: además de quien inyecta, calidad/inventario también necesita crear
    lotes aquí manualmente ("Nuevo Manual") cuando Producción no completó el
    ciclo normal de programación/MES para un turno — es la única vía que tienen
    para dejar ese registro. Mismo set de roles que /api/inyeccion/validar/<id>,
    donde además auditan y cierran el lote.
    """
    data = request.json or {}
    usuario_activo = _obtener_usuario_activo()

    try:
        resultado = InyeccionService.registrar_lote(data, usuario_activo)
        return api_success(data=resultado)

    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="INYECCION_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )

    except TurnoInvalidoException as e:
        return api_error(e.message, status_code=400, code="TURNO_DURACION_INVALIDA")

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        # Red de seguridad: InyeccionService.registrar_lote ya hace rollback
        # antes de propagar, pero si una ruta futura llega a tocar db.session
        # sin pasar por un service, esta línea evita dejar la sesión colgada
        # en el pool de Postgres.
        db.session.rollback()
        logger.error(f" ❌ Error en registrar_inyeccion_lote: {e}")
        return api_error(str(e), status_code=500)


@inyeccion_bp.route('/api/inyeccion/iniciar_turno', methods=['POST'])
@require_role(ROLES_MES_INYECCION)
def iniciar_turno_inyeccion():
    """
    [DEPRECATED] Lógica de lotes en vivo eliminada.
    Se mantiene ruta como dummy para retrocompatibilidad segura.
    """
    return api_success(
        data={"id_inyeccion": f"INY-DUMMY-{uuid.uuid4().hex[:4].upper()}"},
        message="Turno iniciado (Flujo Directo)",
        status_code=201
    )

@inyeccion_bp.route('/api/inyeccion/validar/<id_inyeccion>', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['AUXILIAR INVENTARIO', 'INVENTARIO', 'STAFF FRIMETALS', 'CALIDAD', 'SUPERVISOR'])
def validar_lote_inyeccion(id_inyeccion):
    """
    Controller delgado: parsea el request, delega a InyeccionService.validar_lote
    y traduce el resultado (o las excepciones de negocio) a JSON.
    """
    data = request.get_json(silent=True) or {}
    usuario_activo = _obtener_usuario_activo()

    try:
        resultado = InyeccionService.validar_lote(id_inyeccion, data, usuario_activo)
        return api_success(data=resultado)

    except LoteInyeccionNoEncontradoException as e:
        return api_error(e.message, status_code=404)

    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="INYECCION_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )

    except ValidadorRequeridoException as e:
        return api_error(e.message, status_code=400, code="VALIDADOR_REQUERIDO")

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error validando lote {id_inyeccion}: {e}")
        return api_error(str(e), status_code=500)


@inyeccion_bp.route('/api/inyeccion/dashboard_stats', methods=['GET'])
@require_role(ROL_ADMINS + ['JEFE INYECCION', 'INYECCION'])
def get_inyeccion_stats():
    # Placeholder
    return api_success(message="Estadísticas de inyección (WIP)")


@inyeccion_bp.route('/api/programacion/guardar', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['INYECCION'])
def guardar_programacion_diaria():
    """
    Controller delgado: parsea el request, delega a InyeccionService.guardar_programacion
    y traduce el resultado (o las excepciones de negocio) a JSON.
    """
    data = request.get_json()

    try:
        resultado = InyeccionService.guardar_programacion(data)
        return api_success(data=resultado, status_code=201)

    except ValueError as val_err:
        logger.warning(f"⚠️ Error de validación en guardar_programacion_diaria: {val_err}")
        return api_error(str(val_err), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error crítico en guardar_programacion_diaria: {e}")
        return api_error("Error interno del servidor al guardar la programación", status_code=500)


@inyeccion_bp.route('/api/pedidos/pendientes/<codigo>', methods=['GET'])
def obtener_pedidos_pendientes(codigo):
    """
    Controller delgado: delega la consulta a InyeccionService.obtener_pedidos_pendientes
    y traduce el resultado (o las excepciones de negocio) a JSON.
    """
    try:
        resultado = InyeccionService.obtener_pedidos_pendientes(codigo)
        return api_success(data=resultado)

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en obtener_pedidos_pendientes para el código {codigo}: {e}")
        return api_error("Error interno del servidor al consultar pedidos", status_code=500)


@inyeccion_bp.route('/api/produccion/verificar_demanda/<codigo>', methods=['GET'])
def verificar_demanda_b2b(codigo):
    """
    Controller delgado: delega la consulta a InyeccionService.obtener_demanda_b2b
    y traduce el resultado (o las excepciones de negocio) a JSON.
    """
    try:
        resultado = InyeccionService.obtener_demanda_b2b(codigo)
        return api_success(data=resultado)

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en verificar_demanda para {codigo}: {e}")
        return api_error("Error interno al verificar la demanda", status_code=500)



@inyeccion_bp.route('/api/mes/pendientes_validacion', methods=['GET'])
def mes_pendientes_validacion():
    """Obtiene todos los lotes en estado PENDIENTE/FINALIZADO para validación."""
    resultado = InyeccionService.obtener_pendientes_validacion()
    if resultado.get('success'):
        return api_success(data=resultado.get('data'))
    return api_error(resultado.get('error', 'Error obteniendo pendientes de validación'), status_code=500)


@inyeccion_bp.route('/api/mes/iniciar_trabajo', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['INYECCION', 'ENSAMBLE'])
def mes_iniciar_trabajo():
    """
    Controller delgado: parsea el request, delega a InyeccionService.iniciar_trabajo
    y traduce el resultado (o las excepciones de negocio) a JSON.
    """
    data = request.get_json() or {}
    usuario_activo = _obtener_usuario_activo()

    try:
        resultado = InyeccionService.iniciar_trabajo(data, usuario_activo)
        return api_success(data=resultado)

    except ProgramacionNoEncontradaException as e:
        return api_error(e.message, status_code=404)

    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="INYECCION_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error al iniciar trabajo en el MES: {e}")
        return api_error("Error interno al iniciar el trabajo", status_code=500)


@inyeccion_bp.route('/api/mes/reportar', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['INYECCION', 'ENSAMBLE'])
def mes_reportar():
    """
    Controller delgado: parsea el request, delega a InyeccionService.reportar_trabajo
    y traduce el resultado (o las excepciones de negocio) a JSON.
    """
    data = request.get_json() or {}
    usuario_activo = _obtener_usuario_activo()

    try:
        resultado = InyeccionService.reportar_trabajo(data, usuario_activo)
        return api_success(data=resultado)

    except LoteInyeccionNoEncontradoException as e:
        return api_error(e.message, status_code=404)

    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="INYECCION_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )

    except TurnoInvalidoException as e:
        return api_error(e.message, status_code=400, code="TURNO_DURACION_INVALIDA")

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error al reportar turno en el MES: {e}")
        return api_error("Error interno al finalizar el turno", status_code=500)


@inyeccion_bp.route('/api/mes/reportar_parcial', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['INYECCION', 'ENSAMBLE'])
def mes_reportar_parcial():
    """
    Reporte parcial de avance (pedido del usuario 2026-09-04, normalmente a
    las 11am y 3pm). Controller delgado: parsea el request, delega a
    InyeccionService.registrar_lectura_parcial y traduce el resultado (o las
    excepciones de negocio) a JSON.

    A diferencia de /api/mes/reportar, esto NO cierra ni finaliza el lote --
    solo deja una lectura de auditoría del contador a media jornada.
    """
    data = request.get_json() or {}
    usuario_activo = _obtener_usuario_activo()

    try:
        resultado = InyeccionService.registrar_lectura_parcial(data, usuario_activo)
        return api_success(data=resultado)

    except LoteInyeccionNoEncontradoException as e:
        return api_error(e.message, status_code=404)

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error al registrar reporte parcial en el MES: {e}")
        return api_error("Error interno al registrar el reporte parcial", status_code=500)


def _mask_token(token):
    """Enmascara un token para logging seguro -- ver ensamble_routes._mask_token
    (misma lógica, duplicada aquí para no acoplar este blueprint a otro)."""
    if not token:
        return "(vacío)"
    token = str(token)
    return f"{token[:4]}***" if len(token) > 4 else "***"


@inyeccion_bp.route('/api/mes/recordar_reporte_parcial', methods=['GET', 'POST'])
def mes_recordar_reporte_parcial():
    """
    Red de seguridad del reporte de avance (pedido del usuario 2026-09-04):
    pensada para dos Tareas Programadas de Windows, a las 11:00 y a las
    15:00 -- mismo patrón exacto que ensamble_routes.cerrar_jornada_auto,
    token compartido, NO sesión/JWT de usuario humano.

    A diferencia de ese endpoint, este NUNCA escribe en la base de datos:
    solo avisa por Web Push a Inyección/Ensamble si hay máquinas
    EN_PROCESO. Parámetro opcional ?momento=11|15 solo cambia el texto del
    aviso, ver InyeccionService.recordar_reporte_parcial.
    """
    token_recibido = request.args.get('token') or request.headers.get('X-Sync-Token')
    token_esperado = os.getenv('SYNC_TOKEN')

    if token_esperado is None:
        logger.error("❌ Variable de entorno SYNC_TOKEN no configurada en el servidor (es None).")
        return api_error("Error de configuración de seguridad: SYNC_TOKEN es None", status_code=500)

    if token_recibido != token_esperado:
        logger.warning(f"⚠️ Intento de recordatorio de reporte parcial no autorizado. Token recibido: {_mask_token(token_recibido)}")
        return api_error("No autorizado. Token inválido.", status_code=403)

    try:
        resultado = InyeccionService.recordar_reporte_parcial(momento=request.args.get('momento'))
        return api_success(data=resultado)
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en recordatorio de reporte parcial: {e}")
        return api_error(str(e), status_code=500)


@inyeccion_bp.route('/api/pnc/registrar_inyeccion', methods=['POST'])
@require_role(ROLES_MES_INYECCION)
def registrar_pnc_inyeccion():
    """
    Controller delgado: parsea el request, delega a InyeccionService.registrar_pnc
    y traduce el resultado (o las excepciones de negocio) a JSON.
    """
    data = request.get_json() or {}

    try:
        resultado = InyeccionService.registrar_pnc(data)
        return api_success(data=resultado)

    except ValueError as e:
        return api_error(str(e), status_code=400)

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en registrar_pnc_inyeccion: {e}")
        return api_error(str(e), status_code=500)


# ====================================================================
# RESIDUAL LEGACY (registro directo, config de cavidades, cálculo de
# producción) — movido desde backend/app.py
# ====================================================================

@inyeccion_bp.route('/api/inyeccion', methods=['POST'])
@require_role(ROLES_INYECCION_ESCRITURA)
def registrar_inyeccion():
    """Registra una operación de inyección en SQL-Native con descuento de BOM (flujo legacy)."""
    data = request.get_json()
    try:
        resultado = InyeccionService.registrar_directa(data)
        return api_success(data={'id': resultado['id']}, message='Inyección registrada en SQL con BOM', status_code=201)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en registrar_inyeccion SQL: {e}")
        return api_error(str(e), status_code=500)


@inyeccion_bp.route('/api/cavidades/config', methods=['GET'])
def obtener_config_cavidades():
    """Obtiene la configuración de cavidades disponibles."""
    try:
        return api_success(data=InyeccionService.obtener_config_cavidades())
    except Exception as e:
        return api_error(str(e), status_code=500)


@inyeccion_bp.route('/api/inyeccion/calcular', methods=['POST'])
@require_login
def calcular_inyeccion():
    """Calcula la producción total basada en cantidad y cavidades."""
    data = request.get_json() or {}
    try:
        resultado = InyeccionService.calcular_produccion(data.get('cantidad'), data.get('cavidades'), data.get('pnc', 0))
        return api_success(data=resultado)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        return api_error(str(e), status_code=500)


