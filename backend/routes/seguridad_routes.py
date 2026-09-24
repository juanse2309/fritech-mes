# -*- coding: utf-8 -*-
"""Seguridad industrial: contador de días sin accidentes (Modo TV). Controladores puros."""
import logging

from flask import Blueprint, request
from pydantic import ValidationError

from backend.core.responses import api_success, api_error
from backend.schemas.seguridad_schemas import UltimoAccidenteSchema
from backend.services.seguridad_service import SeguridadService
from backend.utils.auth_middleware import (
    require_role, ROL_ADMINS, ROL_MODO_TV, obtener_identidad_segura, es_rol_admin
)

seguridad_bp = Blueprint('seguridad_bp', __name__)
logger = logging.getLogger(__name__)


@seguridad_bp.route('/api/seguridad/ultimo_accidente', methods=['GET'])
@require_role(ROL_MODO_TV)
def obtener_ultimo_accidente():
    """Fecha del último accidente y días transcurridos (ambos None si nunca se registró).
    'puede_editar' le dice al frontend si mostrar el control de edición (solo ADMIN)."""
    try:
        data = SeguridadService.obtener_ultimo_accidente()
        _, rol = obtener_identidad_segura(request)
        data['puede_editar'] = es_rol_admin(rol)
        return api_success(data=data)
    except Exception as e:
        logger.error(f"Error en obtener_ultimo_accidente: {e}")
        return api_error(str(e), status_code=500)


@seguridad_bp.route('/api/seguridad/ultimo_accidente', methods=['POST'])
@require_role(ROL_ADMINS)
def registrar_ultimo_accidente():
    """Fija la fecha del último accidente. Solo ADMIN. Body: {"fecha": "YYYY-MM-DD"}."""
    try:
        payload = UltimoAccidenteSchema.model_validate(request.get_json(silent=True) or {})
    except ValidationError as e:
        return api_error(f"Fecha inválida: {e.errors()[0]['msg']}", status_code=400)

    try:
        usuario, _ = obtener_identidad_segura(request)
        return api_success(data=SeguridadService.registrar_ultimo_accidente(payload.fecha, usuario))
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Error en registrar_ultimo_accidente: {e}")
        return api_error(str(e), status_code=500)
