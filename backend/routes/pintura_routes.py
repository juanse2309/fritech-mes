from flask import Blueprint, request
import logging
from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.models.sql_models import ProduccionPintura
from backend.services.audit_service import OwnershipMismatchException
from backend.services.pintura_service import PinturaService
from backend.utils.auth_middleware import require_role, ROL_ADMINS

pintura_bp = Blueprint('pintura_bp', __name__)
logger = logging.getLogger(__name__)

ROLES_PINTURA = ROL_ADMINS + ['AUXILIAR INVENTARIO', 'JEFE AUXILIAR INVENTARIO', 'ENSAMBLE', 'PINTURA']


@pintura_bp.route('/api/pintura/session_active', methods=['GET'])
@require_role(ROLES_PINTURA)
def get_active_pintura_session():
    responsable = request.args.get('responsable')
    if not responsable:
        return api_error("Responsable requerido", status_code=400)

    try:
        sesion = ProduccionPintura.query.filter(
            ProduccionPintura.responsable == responsable,
            ProduccionPintura.estado.in_(['EN_PROCESO', 'PAUSADO'])
        ).order_by(ProduccionPintura.id.desc()).first()

        if sesion:
            return api_success(data={
                "session": {
                    "id_pintura": sesion.id_pintura,
                    "id_ensamble": sesion.id_ensamble,
                    "id_codigo": sesion.id_codigo,
                    "insumo_pintura": sesion.insumo_pintura,
                    "estado": sesion.estado,
                    "hora_inicio_dt": sesion.hora_inicio.isoformat() if sesion.hora_inicio else None
                }
            })
        return api_success(data={"session": None})
    except Exception as e:
        return api_error(str(e), status_code=500)


@pintura_bp.route('/api/pintura/iniciar', methods=['POST'])
@require_role(ROLES_PINTURA)
def iniciar_pintura():
    data = request.get_json()
    try:
        resultado = PinturaService.iniciar(data)
        if resultado['ya_registrado']:
            return api_success(data={'id_pintura': resultado['id_pintura']}, message="Pintura ya registrada")
        return api_success(
            data={'id_pintura': resultado['id_pintura']},
            message="Pintura iniciada y persistida en SQL",
            status_code=201
        )
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="PINTURA_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en iniciar_pintura: {e}")
        return api_error(str(e), status_code=500)


@pintura_bp.route('/api/pintura/finalizar', methods=['POST'])
@require_role(ROLES_PINTURA)
def finalizar_pintura():
    data = request.get_json()
    try:
        resultado = PinturaService.finalizar(data)
        return api_success(data=resultado)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except OwnershipMismatchException as e:
        return api_error(
            e.message, status_code=409, code="PINTURA_SESSION_OWNERSHIP_MISMATCH",
            responsable_db=e.responsable_db, responsable_in=e.responsable_in
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en finalizar_pintura: {e}")
        return api_error(str(e), status_code=500)
