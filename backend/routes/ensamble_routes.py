from flask import Blueprint, request, session, current_app
import logging
import os
from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.models.sql_models import ProgramacionEnsamble, Ensamble
from backend.services.audit_service import OwnershipMismatchException
from backend.services.ensamble_service import EnsambleService, BomNoDisponibleException, StockInsuficienteException, ChecklistIncompletoException
from backend.config.constants import FALLBACK_OPERARIO
from backend.utils.auth_middleware import _obtener_usuario_activo, obtener_identidad_segura, require_role, ROL_ADMINS, ROL_JEFES, ROL_OPERARIOS

ensamble_bp = Blueprint('ensamble_bp', __name__)
logger = logging.getLogger(__name__)

ROLES_ENSAMBLE = ROL_ADMINS + ['AUXILIAR INVENTARIO', 'ENSAMBLE', 'JEFE ALISTAMIENTO']
# '/api/inyeccion/ensamble_desde_producto' se consume desde inyeccion.js (página
# 'inyeccion', con audiencia mucho más amplia que 'ensamble') -- usa el set
# de planta completo en vez de ROLES_ENSAMBLE.
ROLES_PLANTA = ROL_ADMINS + ROL_JEFES + ROL_OPERARIOS

@ensamble_bp.route('/api/ensamble/programacion', methods=['GET'])
@require_role(ROLES_ENSAMBLE)
def listar_programacion():
    try:
        # Listar todas las programaciones no completadas primero
        schedules = ProgramacionEnsamble.query.order_by(
            ProgramacionEnsamble.estado.desc(), 
            ProgramacionEnsamble.fecha_programada.asc()
        ).all()
        
        res = []
        for s in schedules:
            res.append({
                'id_prog': s.id_prog,
                'id_codigo': s.id_codigo,
                'cantidad_objetivo': s.cantidad_objetivo,
                'cantidad_realizada': s.cantidad_realizada,
                'fecha_programada': s.fecha_programada.strftime('%Y-%m-%d') if s.fecha_programada else '',
                'estado': s.estado
            })
        return api_success(data=res)
    except Exception as e:
        logger.error(f"Error al listar programación ensamble: {e}")
        return api_error(str(e), status_code=500)

@ensamble_bp.route('/api/ensamble/historial_metas', methods=['GET'])
@require_role(ROLES_ENSAMBLE)
def historial_metas():
    """Controlador puro: delega en EnsambleService el panel "Historial de Metas"
    (pendientes de cualquier día + completadas hoy, ver EnsambleService.listar_historial_metas)."""
    try:
        resultado = EnsambleService.listar_historial_metas()
        return api_success(data=resultado)
    except Exception as e:
        logger.error(f"Error al listar historial de metas ensamble: {e}")
        return api_error(str(e), status_code=500)

@ensamble_bp.route('/api/ensamble/session_active', methods=['GET'])
@require_role(ROLES_ENSAMBLE)
def get_active_ensamble_session():
    """Busca si el operario tiene un trabajo activo en db_ensambles."""
    responsable = request.args.get('responsable')
    if not responsable:
        return api_error("Responsable requerido", status_code=400)

    try:
        # Buscar en db_ensambles (plural como exige DBeaver)
        sesion = Ensamble.query.filter(
            Ensamble.responsable == responsable,
            Ensamble.estado.in_(['EN_PROCESO', 'PAUSADO', 'TRABAJANDO'])
        ).order_by(Ensamble.id.desc()).first()

        if sesion:
            return api_success(data={
                "session": {
                    "id_ensamble": sesion.id_ensamble,
                    "id_codigo": sesion.id_codigo,
                    "orden_produccion": sesion.op_numero,
                    "cantidad": float(sesion.cantidad or 0),
                    "estado": sesion.estado,
                    "hora_inicio_dt": sesion.hora_inicio.isoformat() if sesion.hora_inicio else None,
                    "tiempo_pausa_acumulado": sesion.tiempo_pausa_acumulado or 0,
                    "hora_pausa": sesion.hora_pausa.isoformat() if sesion.hora_pausa else None
                }
            })
        return api_success(data={"session": None})
    except Exception as e:
        return api_error(str(e), status_code=500)

@ensamble_bp.route('/api/ensamble/programacion', methods=['POST'])
@require_role(ROLES_ENSAMBLE)
def crear_programacion():
    """Controlador puro: delega el UPSERT de programación en EnsambleService."""
    data = request.get_json()
    try:
        resultado = EnsambleService.crear_o_actualizar_programacion(data)
        return api_success(data=resultado)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Error al crear programación ensamble: {e}")
        return api_error(str(e), status_code=500)

@ensamble_bp.route('/api/ensamble/bom_stock/<id_codigo>', methods=['GET'])
@require_role(ROLES_ENSAMBLE)
def obtener_bom_con_stock(id_codigo):
    """Controlador puro: delega el cálculo de BOM + stock en EnsambleService."""
    try:
        resultado = EnsambleService.obtener_bom_con_stock(id_codigo)
        return api_success(data=resultado)
    except BomNoDisponibleException as e:
        return api_error(e.message, status_code=404)
    except Exception as e:
        logger.error(f"Error al obtener BOM con stock: {e}")
        return api_error(str(e), status_code=500)

@ensamble_bp.route('/api/ensamble/tareas_pendientes', methods=['GET'])
@require_role(ROLES_ENSAMBLE)
def tareas_pendientes():
    """Controlador puro: delega el cálculo de faltantes en EnsambleService."""
    try:
        resultado = EnsambleService.listar_tareas_pendientes()
        return api_success(data=resultado)
    except Exception as e:
        logger.error(f"Error al listar tareas pendientes: {e}")
        return api_error(str(e), status_code=500)

@ensamble_bp.route('/api/ensamble/reportar', methods=['POST'])
@require_role(ROLES_ENSAMBLE)
def reportar_ensamble_multi():
    """Controlador puro: delega el reporte multi-registro en EnsambleService."""
    data = request.get_json()
    try:
        usuario_activo = _obtener_usuario_activo()
        resultado = EnsambleService.reportar_multi(data, usuario_activo)
        return api_success(
            data={
                'id_ensamble': resultado['id_ensamble'],
                'movimientos_inventario': resultado['movimientos_inventario']
            },
            message=f"Se procesaron {resultado['registros_procesados']} registros con éxito."
        )
    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="ENSAMBLE_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ ERROR REPORTE MULTI-ENSAMBLE: {e}")
        return api_error(str(e), status_code=500)


@ensamble_bp.route('/api/ensamble/cerrar_jornada', methods=['POST'])
@require_role(ROLES_ENSAMBLE)
def cerrar_jornada():
    """
    Cierre de jornada (reunión 2026-08-25): marca la OP del día como lista
    para que Zoe la descargue al día siguiente. Rechaza si queda checklist
    de procesos incompleto, salvo forzar=True -- que exige ser ROL_ADMINS
    (no basta con ROLES_ENSAMBLE) y traer un motivo.
    """
    data = request.get_json() or {}
    forzar = bool(data.get('forzar'))

    usuario, rol = obtener_identidad_segura(request)

    if forzar and not any(r in (rol or '').upper() for r in ROL_ADMINS):
        return api_error(
            "Cerrar la jornada de forma forzada requiere rol de administrador.",
            status_code=403
        )

    try:
        resultado = EnsambleService.cerrar_jornada(
            fecha_str=data.get('fecha'),
            usuario=usuario,
            forzar=forzar,
            motivo=data.get('motivo'),
        )
        return api_success(data=resultado, message=f"Jornada {resultado['numero_op']} cerrada correctamente.")
    except ChecklistIncompletoException as e:
        return api_error(
            e.message, status_code=409, code="CHECKLIST_INCOMPLETO",
            metas_incompletas=e.metas_incompletas
        )
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error cerrando jornada de ensamble: {e}")
        return api_error(str(e), status_code=500)


def _mask_token(token):
    """Enmascara un token para logging seguro -- ver wo_routes._mask_token
    (misma lógica, duplicada aquí para no acoplar este blueprint a otro)."""
    if not token:
        return "(vacío)"
    token = str(token)
    return f"{token[:4]}***" if len(token) > 4 else "***"


@ensamble_bp.route('/api/ensamble/cerrar_jornada_auto', methods=['GET', 'POST'])
def cerrar_jornada_auto():
    """
    Red de seguridad para cuando el responsable de Ensamble olvida darle al
    botón de "Cerrar Jornada" (pedido del usuario 2026-08-28). Pensada para
    una Tarea Programada de Windows en planta a las 22:00 -- mismo patrón
    exacto que agente_wo_cartera.py/sincronizar_automatica: token
    compartido, NO sesión/JWT de usuario humano, canal aparte para procesos
    automatizados.

    Solo cierra si el checklist de procesos YA está completo -- si de
    verdad quedó trabajo pendiente, no se fuerza. Ver
    EnsambleService.cerrar_jornada_automatica.
    """
    token_recibido = request.args.get('token') or request.headers.get('X-Sync-Token')
    token_esperado = os.getenv('SYNC_TOKEN')

    if token_esperado is None:
        logger.error("❌ Variable de entorno SYNC_TOKEN no configurada en el servidor (es None).")
        return api_error("Error de configuración de seguridad: SYNC_TOKEN es None", status_code=500)

    if token_recibido != token_esperado:
        logger.warning(f"⚠️ Intento de cierre automático de jornada no autorizado. Token recibido: {_mask_token(token_recibido)}")
        return api_error("No autorizado. Token inválido.", status_code=403)

    try:
        resultado = EnsambleService.cerrar_jornada_automatica(fecha_str=request.args.get('fecha'))
        return api_success(data=resultado, message="Cierre automático ejecutado.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en cierre automático de jornada: {e}")
        return api_error(str(e), status_code=500)


# ====================================================================
# EJECUCIÓN DE ENSAMBLE (iniciar/finalizar sesión + BOM)
# Movido desde backend/app.py
# ====================================================================

@ensamble_bp.route('/api/inyeccion/ensamble_desde_producto', methods=['GET'])
@require_role(ROLES_PLANTA)
def obtener_ensamble_desde_producto():
    """Dado un código de producto, retorna su BOM completo desde NUEVA_FICHA_MAESTRA."""
    codigo_entrada = request.args.get('codigo', '').strip()
    try:
        resultado = EnsambleService.obtener_bom_desde_producto(codigo_entrada)
        return api_success(data=resultado)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f" Error en obtener_ensamble_desde_producto: {str(e)}")
        return api_error(str(e), status_code=500)


@ensamble_bp.route('/api/ensamble/iniciar', methods=['POST'])
@require_role(ROLES_ENSAMBLE)
def iniciar_ensamble():
    """
    Persistencia inmediata al iniciar ensamble.
    Crea un registro EN_PROCESO en db_ensambles para que sea visible en el PC de inmediato.
    Sigue el patrón de Pulido: persistirInicioSQL.
    """
    data = request.get_json()
    try:
        resultado = EnsambleService.iniciar(data)
        if resultado['ya_registrado']:
            return api_success(data={'id_ensamble': resultado['id_ensamble']}, message="Ensamble ya registrado")
        return api_success(
            data={'id_ensamble': resultado['id_ensamble']},
            message="Ensamble iniciado y persistido en SQL",
            status_code=201
        )
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error en iniciar_ensamble: {e}")
        return api_error(str(e), status_code=500)


@ensamble_bp.route('/api/ensamble/finalizar', methods=['POST'])
@require_role(ROLES_ENSAMBLE)
def finalizar_ensamble():
    """
    Finaliza un ensamble con explosión de materiales (BOM) y descarga de inventario (Fases 1-5).
    """
    data = request.json
    try:
        resultado = EnsambleService.finalizar(data)
        return api_success(data={'id_ensamble': resultado['id_ensamble']})
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except BomNoDisponibleException as e:
        return api_error(e.message, status_code=400)
    except StockInsuficienteException as e:
        return api_error(e.message, status_code=422)
    except Exception as e:
        logger.error(f"❌ Error en finalizar_ensamble: {e}")
        return api_error(str(e), status_code=500)
