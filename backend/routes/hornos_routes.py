from flask import Blueprint, request
import logging
from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.models.sql_models import ProduccionHorno
from backend.services.audit_service import OwnershipMismatchException
from backend.services.horno_service import HornoService
from backend.utils.auth_middleware import require_role, ROL_ADMINS

hornos_bp = Blueprint('hornos_bp', __name__)
logger = logging.getLogger(__name__)

ROLES_HORNOS = ROL_ADMINS + ['AUXILIAR INVENTARIO', 'JEFE AUXILIAR INVENTARIO', 'ENSAMBLE', 'HORNOS']


@hornos_bp.route('/api/hornos/session_active', methods=['GET'])
@require_role(ROLES_HORNOS)
def get_active_horno_session():
    responsable = request.args.get('responsable')
    if not responsable:
        return api_error("Responsable requerido", status_code=400)

    try:
        sesion = ProduccionHorno.query.filter(
            ProduccionHorno.responsable == responsable,
            ProduccionHorno.estado == 'EN_HORNO'
        ).order_by(ProduccionHorno.id.desc()).first()

        if sesion:
            return api_success(data={
                "session": {
                    "id_horno_registro": sesion.id_horno_registro,
                    "id_ensamble": sesion.id_ensamble,
                    "id_codigo": sesion.id_codigo,
                    "horno_numero": sesion.horno_numero,
                    "cantidad": float(sesion.cantidad or 0),
                    "temperatura_ingreso_c": float(sesion.temperatura_ingreso_c) if sesion.temperatura_ingreso_c is not None else None,
                    "estado": sesion.estado,
                    "hora_inicio_dt": sesion.hora_inicio.isoformat() if sesion.hora_inicio else None
                }
            })
        return api_success(data={"session": None})
    except Exception as e:
        return api_error(str(e), status_code=500)


@hornos_bp.route('/api/hornos/iniciar', methods=['POST'])
@require_role(ROLES_HORNOS)
def iniciar_horno():
    data = request.get_json()
    try:
        resultado = HornoService.iniciar(data)
        if resultado['ya_registrado']:
            return api_success(data={'id_horno_registro': resultado['id_horno_registro']}, message="Registro de horno ya existente")
        return api_success(
            data={'id_horno_registro': resultado['id_horno_registro']},
            message="Ingreso a horno persistido en SQL",
            status_code=201
        )
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="HORNO_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en iniciar_horno: {e}")
        return api_error(str(e), status_code=500)


@hornos_bp.route('/api/hornos/finalizar', methods=['POST'])
@require_role(ROLES_HORNOS)
def finalizar_horno():
    data = request.get_json()
    try:
        resultado = HornoService.finalizar(data)
        return api_success(data=resultado)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="HORNO_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en finalizar_horno: {e}")
        return api_error(str(e), status_code=500)
