import os
import jwt
import logging
import unicodedata
from functools import wraps
from flask import session, jsonify, request, current_app

logger = logging.getLogger(__name__)

# Define allowed roles constants (UPPERCASE for strict matching)
ROL_ADMINS = ['ADMIN', 'ADMINISTRACION', 'ADMINISTRADOR', 'GERENCIA']
ROL_JEFES = ['JEFE ALMACEN', 'JEFE INYECCION', 'JEFE PULIDO', 'JEFE DE PLANTA', 'JEFE ALISTAMIENTO', 'JEFE AUXILIAR INVENTARIO']
ROL_COMERCIALES = ['COMERCIAL', 'COMERCIAL FRIMETALS', 'STAFF FRIMETALS']
ROL_OPERARIOS = ['INYECCION', 'PULIDO', 'ALISTAMIENTO', 'ENSAMBLE', 'AUXILIAR INVENTARIO']
# Roles autorizados para omitir el Ownership Guard (ver AuditService._usuario_autenticado_puede_override).
# Mantener sincronizado con los roles usados en @require_role de las rutas de validación.
ROLES_VALIDACION_OVERRIDE = ROL_ADMINS + ROL_JEFES + ['AUXILIAR INVENTARIO', 'INVENTARIO', 'CALIDAD', 'STAFF FRIMETALS', 'SUPERVISOR']
# Endpoints operativos del Dashboard IA (KPIs de producción, rankings, drill-down):
# visibles para jefaturas de planta y calidad, no solo Admin/Comercial. Los endpoints
# monetarios/financieros (cartera, ventas, rendimiento) permanecen en ROL_ADMINS + ROL_COMERCIALES.
ROL_DASHBOARD_OPERATIVO = ROL_ADMINS + ROL_COMERCIALES + ROL_JEFES + ['CALIDAD']
# Precalculado una sola vez al importar el módulo: require_role lo reutiliza
# en cada request en vez de reconstruirlo (ver "God Mode" más abajo).
_ROL_ADMINS_SET = set(r.strip().upper() for r in ROL_ADMINS)

def _obtener_jwt_secrets():
    """
    Retorna la lista ordenada de posibles claves secretas para decodificar JWT,
    desduplicando y omitiendo valores nulos o vacíos.

    Solo considera JWT_PWA_SECRET (env var y/o app.config) -- YA NO cae de
    vuelta a SECRET_KEY/app.secret_key. Reutilizar el secreto de firma de
    sesión Flask como secreto de JWT ampliaba la superficie de ataque:
    comprometer un canal comprometía el otro. app.py exige JWT_PWA_SECRET de
    forma fail-fast al arrancar (RuntimeError si falta), así que en
    producción esta lista nunca debería llegar vacía.
    """
    candidatas = []

    # 1. Variable de entorno explícita
    env_secret = os.environ.get('JWT_PWA_SECRET')
    if env_secret:
        candidatas.append(env_secret)

    # 2. Config de la aplicación Flask (poblado desde la misma env var en app.py)
    try:
        cfg_pwa = current_app.config.get('JWT_PWA_SECRET')
        if cfg_pwa:
            candidatas.append(cfg_pwa)
    except Exception:
        pass

    # Desduplicar preservando orden de prioridad
    unicas = []
    for s in candidatas:
        if s and s not in unicas:
            unicas.append(s)

    return unicas

def decode_pwa_token(request):
    """
    Extrae y decodifica el token JWT del header Authorization o del parámetro query ('token'/'pwa_token')
    probando secuencialmente contra las claves secretas configuradas.
    """
    token = None
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
    elif request.args.get('token'):
        token = request.args.get('token')
    elif request.args.get('pwa_token'):
        token = request.args.get('pwa_token')
    elif request.args.get('jwt'):
        token = request.args.get('jwt')

    if not token:
        return None

    secrets = _obtener_jwt_secrets()
    for secret in secrets:
        try:
            return jwt.decode(token, secret, algorithms=['HS256'])
        except jwt.InvalidSignatureError:
            continue
        except Exception as e:
            logger.warning(f"[AUTH] Error decodificando JWT en {request.path}: {e}")
            return None

    logger.warning(f"[AUTH] Firma de JWT no válida con ninguna clave configurada en {request.path}")
    return None

def _obtener_usuario_activo():
    """
    Extrae la identidad del usuario activo desde el token JWT o la sesión Flask.
    """
    user, _ = obtener_identidad_segura(request)
    return user

def obtener_identidad_segura(req):
    """
    Extrae la identidad (user, role) desde el header Authorization (JWT) o la sesión de Flask.
    Registra una advertencia si no se encuentra autenticación válida.
    """
    user = None
    role = None

    # 1. Intentar JWT
    try:
        payload = decode_pwa_token(req)
        if payload:
            user = payload.get('username') or payload.get('user') or payload.get('nombre')
            role = payload.get('rol') or payload.get('role')
    except Exception as e:
        logger.warning('[AUTH] Error decodificando JWT en %s: %s', req.path, e)

    # 2. Fallback a sesión de Flask si no hay JWT
    if not user and 'user' in session:
        user = session.get('user')
        role = session.get('role')

    # 3. Log explicativo si no hay credenciales válidas
    if not user:
        logger.warning('[AUTH] Fallo de autenticación en %s: No hay JWT válido ni sesión activa', req.path)
        logger.warning('[AUTH] Petición a %s rechazada. Authorization Header presente: %s', req.path, 'Authorization' in req.headers)
        return None, None

    return user, role

def require_login(f):
    """
    Exige únicamente una identidad autenticada (vía JWT o sesión Flask),
    sin restricción de rol específico. Pensado para endpoints de datos de
    referencia consumidos transversalmente por todos los roles (incluido
    el portal de clientes), donde el requisito real es "no anónimo", no
    un rol concreto.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user, _ = obtener_identidad_segura(request)
        if not user:
            return jsonify({'status': 'error', 'message': 'No autorizado'}), 401
        return f(*args, **kwargs)
    return decorated_function


def require_role(allowed_roles_input):
    """
    Examines the current user's role obtained via obtener_identidad_segura with strict UPPERCASE normalization.
    allowed_roles_input: A list of strings or a single role string.
    Accepts both flat lists and our predefined constants.

    Comparación por pertenencia EXACTA a un conjunto (no substring). Antes,
    `allowed in user_role` comparaba por contención: un rol de texto libre
    que simplemente CONTUVIERA un rol permitido como substring (p.ej. un
    futuro rol mal tipeado que incluyera "ADMIN") heredaba acceso no
    previsto. Con pertenencia exacta, si un endpoint debe ser accesible
    tanto para un operario como para su jefe, ambos roles deben listarse
    explícitamente (patrón ya usado en la mayoría de rutas: ROL_ADMINS +
    ROL_JEFES + ROL_OPERARIOS).
    """
    # 3. Handle input: Convert nested lists (from constants) a un set plano, exacto y en mayúsculas.
    # allowed_roles_input es fijo por endpoint (se conoce al aplicar el decorador) -- se
    # normaliza UNA vez aquí en vez de en cada request dentro de decorated_function.
    allowed_roles = []
    if isinstance(allowed_roles_input, list):
        for r in allowed_roles_input:
            if isinstance(r, list): # handle ROL_ADMINS + ['JEFE']
                allowed_roles.extend([str(x).strip().upper() for x in r])
            else:
                allowed_roles.append(str(r).strip().upper())
    else:
        allowed_roles = [str(allowed_roles_input).strip().upper()]

    allowed_roles_set = set(allowed_roles)

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user, raw_role = obtener_identidad_segura(request)

            if not user or not raw_role:
                return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

            # 2. Extract user's role and normalize (Accents removed + UPPERCASE + strip)
            raw_role_str = str(raw_role).strip().upper() # ⚡ ALWAYS UPPERCASE
            user_role = ''.join((c for c in unicodedata.normalize('NFD', raw_role_str) if unicodedata.category(c) != 'Mn'))

            # 4. Global God Mode: Admins variation always matches (pertenencia exacta a ROL_ADMINS)
            if user_role in _ROL_ADMINS_SET:
                return f(*args, **kwargs)

            # 5. Check specific access: pertenencia EXACTA al conjunto, nunca substring
            if user_role in allowed_roles_set:
                return f(*args, **kwargs)

            return jsonify({
                'status': 'error',
                'message': f'Acceso denegado: permisos insuficientes para el rol {user_role}.'
            }), 403

        return decorated_function
    return decorator
