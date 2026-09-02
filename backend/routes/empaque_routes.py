"""
empaque_routes.py
==================
Blueprint delgado del módulo de Empaque (reunión 2026-08-25): rutas solo
parsean request/manejan códigos HTTP -- toda la lógica vive en
EmpaqueService.
"""
from flask import Blueprint, request
import logging

from backend.core.sql_database import db
from backend.core.responses import api_success, api_error
from backend.utils.auth_middleware import require_role, _obtener_usuario_activo, ROL_ADMINS
from backend.services.empaque_service import EmpaqueService
from backend.services.ensamble_service import BomNoDisponibleException, StockInsuficienteException

empaque_bp = Blueprint('empaque_bp', __name__)
logger = logging.getLogger(__name__)

ROLES_EMPAQUE = ROL_ADMINS + ['JEFE ALISTAMIENTO', 'ALISTAMIENTO', 'JEFE AUXILIAR INVENTARIO']


@empaque_bp.route('/api/empaque/reportar', methods=['POST'])
@require_role(ROLES_EMPAQUE)
def reportar_empaque():
    """El formulario: referencia + cantidad. Ver EmpaqueService.reportar
    para el flujo completo (BOM, descuento con prelación, OP perezosa)."""
    data = request.get_json() or {}
    try:
        usuario_activo = _obtener_usuario_activo()
        resultado = EmpaqueService.reportar(data, usuario_activo)
        return api_success(data=resultado, message=f"{resultado['cantidad']} x {resultado['id_codigo']} registrado bajo {resultado['op_numero']}.")
    except BomNoDisponibleException as e:
        return api_error(e.message, status_code=404, code="BOM_NO_DISPONIBLE")
    except StockInsuficienteException as e:
        return api_error(e.message, status_code=409, code="STOCK_INSUFICIENTE")
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error en reportar_empaque: {e}")
        return api_error(str(e), status_code=500)


@empaque_bp.route('/api/empaque/reportes', methods=['GET'])
@require_role(ROLES_EMPAQUE)
def listar_reportes_empaque():
    """Historial del rango (por defecto, solo hoy)."""
    try:
        resultado = EmpaqueService.listar_reportes(
            fecha_desde=request.args.get('fecha_desde'),
            fecha_hasta=request.args.get('fecha_hasta'),
        )
        return api_success(data=resultado)
    except ValueError as e:
        return api_error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"❌ Error en listar_reportes_empaque: {e}")
        return api_error(str(e), status_code=500)


@empaque_bp.route('/api/empaque/ficha/<codigo>', methods=['GET'])
@require_role(ROLES_EMPAQUE)
def previsualizar_ficha_empaque(codigo):
    """Vista previa de qué componentes se van a descontar (y de dónde)
    antes de que la operaria confirme el reporte."""
    try:
        cantidad = int(request.args.get('cantidad', 1))
        resultado = EmpaqueService.previsualizar_ficha(codigo, cantidad)
        return api_success(data=resultado)
    except BomNoDisponibleException as e:
        return api_error(e.message, status_code=404, code="BOM_NO_DISPONIBLE")
    except Exception as e:
        logger.error(f"❌ Error en previsualizar_ficha_empaque: {e}")
        return api_error(str(e), status_code=500)
