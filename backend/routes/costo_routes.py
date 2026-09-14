import logging
from flask import Blueprint, jsonify, request
from backend.utils.auth_middleware import require_role, ROL_ADMINS
from backend.core.sql_database import rollback_seguro

logger = logging.getLogger(__name__)

costo_bp = Blueprint('costo', __name__)


@costo_bp.route('/api/costos/listar', methods=['GET'])
@require_role(ROL_ADMINS)
def listar_costos():
    """Rentabilidad por pedido: costo de pieza vs precio de venta, sin costos operativos."""
    try:
        from backend.services.costo_service import CostoService

        desde = request.args.get('desde')
        hasta = request.args.get('hasta')
        pedidos = CostoService.listar_costos_agrupado(desde, hasta)
        return jsonify({"success": True, "pedidos": pedidos}), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error listando costos de pedidos: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener el costo de los pedidos."}), 500


@costo_bp.route('/api/costos/pedido/<id_pedido>', methods=['GET'])
@require_role(ROL_ADMINS)
def detalle_costo_pedido(id_pedido):
    """Detalle línea a línea de costo vs venta de un pedido puntual."""
    try:
        from backend.services.costo_service import CostoService

        items = CostoService.obtener_costo_detalle_pedido(id_pedido)
        return jsonify({"success": True, "items": items}), 200
    except Exception as e:
        rollback_seguro()
        logger.error(f"Error obteniendo costo del pedido {id_pedido}: {e}")
        return jsonify({"success": False, "error": "No fue posible obtener el detalle de costo del pedido."}), 500
