from flask import Blueprint, request
import logging
from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.models.sql_models import ProduccionRayada
from backend.services.audit_service import OwnershipMismatchException
from backend.services.rayada_service import RayadaService
from backend.utils.auth_middleware import require_role, ROL_ADMINS

rayada_bp = Blueprint('rayada_bp', __name__)
logger = logging.getLogger(__name__)

ROLES_RAYADA = ROL_ADMINS + ['AUXILIAR INVENTARIO', 'JEFE AUXILIAR INVENTARIO', 'ENSAMBLE', 'RAYADA']


@rayada_bp.route('/api/rayada/session_active', methods=['GET'])
@require_role(ROLES_RAYADA)
def get_active_rayada_session():
    responsable = request.args.get('responsable')
    if not responsable:
        return api_error("Responsable requerido", status_code=400)

    try:
        sesion = ProduccionRayada.query.filter(
            ProduccionRayada.responsable == responsable,
            ProduccionRayada.estado.in_(['EN_PROCESO', 'PAUSADO'])
        ).order_by(ProduccionRayada.id.desc()).first()

        if sesion:
            return api_success(data={
                "session": {
                    "id_rayada": sesion.id_rayada,
                    "id_ensamble": sesion.id_ensamble,
                    "id_codigo": sesion.id_codigo,
                    "estado": sesion.estado,
                    "hora_inicio_dt": sesion.hora_inicio.isoformat() if sesion.hora_inicio else None
                }
            })
        return api_success(data={"session": None})
    except Exception as e:
        return api_error(str(e), status_code=500)


@rayada_bp.route('/api/rayada/iniciar', methods=['POST'])
@require_role(ROLES_RAYADA)
def iniciar_rayada():
    data = request.get_json()
    try:
        resultado = RayadaService.iniciar(data)
        if resultado['ya_registrado']:
            return api_success(data={'id_rayada': resultado['id_rayada']}, message="Rayada ya registrada")
        return api_success(
            data={'id_rayada': resultado['id_rayada']},
            message="Rayada iniciada y persistida en SQL",
            status_code=201
        )
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="RAYADA_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en iniciar_rayada: {e}")
        return api_error(str(e), status_code=500)


@rayada_bp.route('/api/rayada/finalizar', methods=['POST'])
@require_role(ROLES_RAYADA)
def finalizar_rayada():
    data = request.get_json()
    try:
        resultado = RayadaService.finalizar(data)
        return api_success(data=resultado)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="RAYADA_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en finalizar_rayada: {e}")
        return api_error(str(e), status_code=500)
