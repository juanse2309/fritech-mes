"""
Resolución de Tenant (empresa activa) a partir del JWT en la sesión Flask.

Estrategia: El rol del usuario está firmado dentro del token de sesión.
No se aceptan headers externos (como X-Tenant) para evitar suplantación.

Uso:
    from backend.core.tenant import get_tenant_from_request
    tenant = get_tenant_from_request()   # 'friparts' | 'frimetals'
"""
from flask import session
import logging

logger = logging.getLogger(__name__)

# Roles que pertenecen exclusivamente a Frimetals.
# Cualquier otro rol se trata como Friparts (default seguro).
# -----------------------------------------------------------------------
# Valores en MAYÚSCULAS -- coinciden con auth_middleware.require_role, que
# normaliza todo con .strip().upper() antes de comparar. El comentario
# original decía que session['role'] se guardaba en minúsculas
# (str(rol).lower()), pero eso no es lo que hace el código real: AuthService
# guarda user.rol.upper() para login_metals/login_staff (ver
# backend/routes/auth_routes.py, session['role'] = resultado['session']['role'])
# y el JWT PWA guarda rol.capitalize() -- ninguno de los dos es minúsculas.
# Con el set en minúsculas, `rol in _FRIMETALS_ROLES` era practicamente
# siempre False: get_tenant_from_request() resolvía 'friparts' incluso para
# roles reales de Frimetals (ej. 'STAFF FRIMETALS', 'JEFE DE PLANTA'),
# afectando qué catálogo de productos/pedidos veía ese usuario.
# -----------------------------------------------------------------------
_FRIMETALS_ROLES: frozenset = frozenset({
    "COMERCIAL FRIMETALS",
    "STAFF FRIMETALS",
    "ADMIN FRIMETALS",
    "PRODUCCION FRIMETALS",
    "JEFE DE PLANTA",
})

TENANT_FRIPARTS  = "friparts"
TENANT_FRIMETALS = "frimetals"


def get_tenant_from_request() -> str:
    """
    Determina el tenant activo leyendo el rol desde la sesión Flask.

    - Si el rol está en _FRIMETALS_ROLES  → retorna 'frimetals'
    - En cualquier otro caso              → retorna 'friparts'  (default seguro)

    Returns:
        str: 'friparts' o 'frimetals'
    """
    rol: str = str(session.get("role", "") or "").strip().upper()
    logger.info(f"[Tenant] session['role']={rol!r}, usuario={session.get('user', 'N/A')}")
    if rol in _FRIMETALS_ROLES:
        logger.info(f"[Tenant] ✅ Resuelto como 'frimetals' (rol={rol!r})")
        return TENANT_FRIMETALS

    logger.info(f"[Tenant] Resuelto como 'friparts' (rol={rol!r})")
    return TENANT_FRIPARTS


def is_frimetals_user() -> bool:
    """Atajo booleano para verificar si el usuario activo pertenece a Frimetals."""
    return get_tenant_from_request() == TENANT_FRIMETALS
