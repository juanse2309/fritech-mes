"""
AuthService — Lógica de negocio de autenticación y gestión de usuarios/clientes.

Consultas SQL, hashing/verificación de contraseñas y generación de JWT viven
aquí. Las rutas en `auth_routes.py` solo parsean el request, llaman a este
servicio, escriben la sesión Flask (concern de transporte/HTTP, no de negocio)
y traducen las excepciones de este módulo a códigos HTTP.
"""
import os
import logging
import datetime
import jwt
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from backend.core.sql_database import db
from backend.models.sql_models import Usuario

logger = logging.getLogger(__name__)


class UsuarioNoEncontradoException(Exception):
    """El usuario/cliente buscado no existe."""
    def __init__(self, message="Usuario no encontrado"):
        self.message = message
        super().__init__(self.message)


class CredencialesInvalidasException(Exception):
    """Usuario existe pero la contraseña no coincide."""
    def __init__(self, message="Contraseña incorrecta"):
        self.message = message
        super().__init__(self.message)


class CuentaPendienteException(Exception):
    """Cuenta de cliente en sandboxing (rol CLIENTE_PENDIENTE), aún no aprobada por un admin."""
    def __init__(self, message="Tu cuenta está en proceso de revisión por un administrador"):
        self.message = message
        super().__init__(self.message)


class CuentaInactivaException(Exception):
    """Usuario desactivado por un administrador."""
    def __init__(self, message="Usuario inactivo"):
        self.message = message
        super().__init__(self.message)


class AccesoDenegadoException(Exception):
    """Credenciales válidas pero el rol no tiene permiso para este portal/acción."""
    def __init__(self, message="Acceso denegado"):
        self.message = message
        super().__init__(self.message)


class CorreoDuplicadoException(Exception):
    def __init__(self, message="El correo ya está registrado"):
        self.message = message
        super().__init__(self.message)


class NitDuplicadoException(Exception):
    def __init__(self, message="El NIT ya está registrado"):
        self.message = message
        super().__init__(self.message)


class AuthService:
    """Servicio centralizado de autenticación y gestión de usuarios/clientes."""

    # ── HELPERS INTERNOS ──────────────────────────────────────

    @staticmethod
    def _generar_pwa_token(username, rol):
        """JWT de larga duración (15 días) para el modo offline de la PWA."""
        secret = os.environ.get('JWT_PWA_SECRET')
        if not secret:
            raise RuntimeError("JWT_PWA_SECRET no está configurada")
        return jwt.encode({
            'user': username,
            'role': rol,
            'exp': datetime.datetime.utcnow() + datetime.timedelta(days=15)
        }, secret, algorithm='HS256')

    @staticmethod
    def normalizar_credencial(value):
        """
        Normaliza una credencial (documento o password) para comparación robusta.
        Maneja el caso común de Excel/Sheets donde '12345' viene como 12345.0
        """
        if value is None:
            return ""
        s_val = str(value).strip()
        if s_val.endswith(".0"):
            s_val = s_val[:-2]
        return s_val.replace(".", "").replace(",", "")

    @staticmethod
    def enriquecer_datos_cliente(user_data):
        """Completa dirección/ciudad de un cliente desde db_clientes por NIT, si faltan."""
        nit = user_data.get('nit', '')
        if not nit:
            return user_data
        try:
            from backend.models.sql_models import DbClientes
            match = DbClientes.query.filter(DbClientes.identificacion.ilike(f"%{nit}%")).first()
            if match:
                if not user_data.get('direccion'):
                    user_data['direccion'] = match.direccion
                if not user_data.get('ciudad'):
                    user_data['ciudad'] = match.ciudad
                logger.debug(f"✅ Datos enriquecidos para {nit} desde SQL (db_clientes)")
        except Exception as e:
            logger.error(f"⚠️ Error enriqueciendo datos desde SQL: {e}")
        return user_data

    # ── FRIMETALS AUTH ─────────────────────────────────────────

    @staticmethod
    def obtener_responsables_metals():
        """Lista de responsables de Metales (staff frimetals / administración / jefe de planta)."""
        usuarios_db = Usuario.query.filter(
            Usuario.activo == True,
            Usuario.rol.in_(['staff frimetals', 'administracion', 'jefe de planta'])
        ).order_by(Usuario.nombre_completo).all()

        return [{
            'username': u.username,
            'nombre_completo': u.nombre_completo if u.nombre_completo else u.username,
            'nombre': u.nombre_completo if u.nombre_completo else u.username,
            'departamento': u.departamento or 'Planta'
        } for u in usuarios_db]

    @staticmethod
    def obtener_staff_frimetals_admin_activo():
        """
        Usuarios activos de staff frimetals / administración / comercial
        frimetals, para poblar el selector de la página principal
        (index.html). Filtro histórico por `ilike` — deliberadamente NO se
        unificó con `obtener_responsables_metals` (que además incluye 'jefe
        de planta' y usa `in_`) para no arriesgar cambiar el comportamiento
        de la página principal al extraer esta consulta de app.py.

        'comercial frimetals' se agregó 2026-09-14: sin ese rol, personal
        comercial de Frimetals no podía ni siquiera aparecer en el selector
        de login (quedaba con rol 'comercial' a secas, que además tenant.py
        no reconoce como Frimetals). El sufijo 'frimetals' en el nombre del
        rol es namespacing deliberado (ver _FRIMETALS_ROLES en
        backend/core/tenant.py) -- no matchea ningún rol real de FRIPARTS,
        así que ensanchar este filtro no le agrega nadie al selector de esa
        instancia.
        """
        return Usuario.query.filter(
            Usuario.activo == True,
            (Usuario.rol.ilike('staff frimetals'))
            | (Usuario.rol.ilike('administracion'))
            | (Usuario.rol.ilike('comercial frimetals'))
        ).order_by(Usuario.nombre_completo).all()

    @staticmethod
    def obtener_responsables_generales(rol_filtro=None):
        """
        Lista de responsables activos para dropdowns generales. Prioriza
        db_usuarios; si no hay resultados, cae a colaboradores distintos
        registrados en db_asistencia.

        Excluye siempre cuentas administrativas/de sistema (rol que contenga
        'admin' o 'sistema', o username que contenga 'sistema'): estas no son
        personal operativo y no deben aparecer en selectores de
        responsable/operaria (ej. filtraban a comerciales y administradores
        en el selector de Operaria Pulido de Inyección).

        rol_filtro (opcional): sub-cadena de db_usuarios.rol (ILIKE,
        case-insensitive, ej. 'PULIDO') para acotar el catálogo a un área
        operativa específica. Valores reales de rol son minúsculas
        ('pulido', 'jefe pulido', 'inyeccion', ...), pero ILIKE ya cubre eso.
        """
        query = Usuario.query.filter(
            Usuario.activo == True,
            ~Usuario.rol.ilike('%admin%'),
            ~Usuario.rol.ilike('%sistema%'),
            ~Usuario.rol.ilike('%cliente%'),
            ~Usuario.username.ilike('%sistema%'),
        )
        if rol_filtro:
            query = query.filter(Usuario.rol.ilike(f'%{rol_filtro}%'))

        usuarios = query.order_by(Usuario.nombre_completo).all()
        datos = [{
            "nombre": u.nombre_completo if u.nombre_completo else u.username,
            "departamento": u.departamento or "",
            "username": u.username
        } for u in usuarios]

        # El fallback de db_asistencia no tiene rol/departamento que filtrar:
        # usarlo cuando se pidió un rol_filtro devolvería la lista general sin
        # acotar, deshaciendo el filtro solicitado.
        if not datos and not rol_filtro:
            from backend.core.sql_database import db
            from sqlalchemy import text
            rows = db.session.execute(text(
                "SELECT DISTINCT colaborador FROM db_asistencia WHERE colaborador IS NOT NULL ORDER BY colaborador"
            )).fetchall()
            datos = [{"nombre": r[0], "departamento": "", "username": r[0]} for r in rows if r[0]]

        return datos

    @staticmethod
    def login_metals(usuario_nombre, password):
        """
        Valida credenciales de Metales y genera el token PWA.
        Puede lanzar ValueError, UsuarioNoEncontradoException o
        CredencialesInvalidasException — el controlador las traduce a HTTP.
        Devuelve el payload de sesión (para que el controller escriba
        flask.session) y la respuesta ya armada para el frontend.
        """
        if not usuario_nombre or not password:
            raise ValueError("Faltan datos")

        user = Usuario.query.filter_by(username=usuario_nombre, activo=True).first()
        logger.debug(f"Intento de login metals: usuario='{usuario_nombre}', encontrado={user is not None}")

        if not user:
            raise UsuarioNoEncontradoException()

        if not check_password_hash(user.password_hash, password):
            raise CredencialesInvalidasException()

        rol_upper = user.rol.upper()

        try:
            user.ultimo_acceso = datetime.datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en login_metals actualizando ultimo_acceso ({usuario_nombre}): {e}")
            raise

        pwa_token = AuthService._generar_pwa_token(user.username, rol_upper)

        return {
            "session": {"user": user.username, "role": rol_upper},
            "pwa_token": pwa_token,
            "user": {
                "nombre": user.username,
                "rol": user.rol.capitalize(),
                "tipo": "METALS_STAFF"
            }
        }

    # ── STAFF AUTH (Responsables) ──────────────────────────────

    @staticmethod
    def obtener_responsables_staff():
        """Lista de operarios y staff administrativo (todo rol != cliente)."""
        users = Usuario.query.filter(Usuario.rol != 'cliente', Usuario.activo == True).all()

        responsables = []
        for u in users:
            nombre_final = u.nombre_completo if u.nombre_completo else u.username
            responsables.append({
                "nombre": nombre_final,
                "departamento": u.departamento or u.rol.capitalize(),
                "rol": u.rol,
                "username": u.username
            })
        return responsables

    @staticmethod
    def login_staff(usuario_nombre, password):
        """
        Login de Staff (operarios/admin) por username o cédula.
        Puede lanzar ValueError, UsuarioNoEncontradoException,
        CuentaPendienteException, CuentaInactivaException o
        CredencialesInvalidasException.
        """
        if not usuario_nombre or not password:
            raise ValueError("Faltan datos")

        user = Usuario.query.filter(or_(Usuario.username == usuario_nombre, Usuario.cedula == usuario_nombre)).first()

        logger.debug("--- DEBUG LOGIN (STAFF) ---")
        logger.debug(f"1. Identificador buscado (email/cedula): '{usuario_nombre}'")
        logger.debug(f"2. ¿Usuario encontrado en BD?: {user is not None}")
        if user:
            logger.debug(f"3. Rol del usuario: {user.rol}")
            logger.debug(f"4. Hash en BD a comparar: '{user.password_hash[:15]}...'")
        logger.debug("-------------------")

        if not user:
            raise UsuarioNoEncontradoException()

        if user.rol == 'CLIENTE_PENDIENTE':
            raise CuentaPendienteException()

        if getattr(user, 'activo', None) is False:
            raise CuentaInactivaException()

        if not check_password_hash(user.password_hash, password):
            raise CredencialesInvalidasException("Contraseña incorrecta.")

        try:
            user.ultimo_acceso = datetime.datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en login_staff actualizando ultimo_acceso ({usuario_nombre}): {e}")
            raise

        rol_display = "ADMIN" if user.rol.lower() in ['admin', 'administrador', 'administracion'] else user.rol.capitalize()
        pwa_token = AuthService._generar_pwa_token(user.username, rol_display)

        return {
            "session": {"user": user.username, "role": user.rol.upper()},
            "pwa_token": pwa_token,
            "user": {
                "nombre": user.nombre_completo if user.nombre_completo else user.username,
                "username": user.username,
                "rol": rol_display,
                "tipo": "STAFF",
                "departamento": user.departamento
            }
        }

    # ── CLIENT AUTH (Portal B2B) ───────────────────────────────

    @staticmethod
    def crear_cuenta_cliente(nit, nombre_empresa, email):
        """
        ADMIN: crea una cuenta de cliente con password temporal = NIT.
        Puede lanzar ValueError o CorreoDuplicadoException.
        """
        if not nit or not email:
            raise ValueError("NIT y Email son obligatorios")

        if Usuario.query.filter_by(username=email).first():
            raise CorreoDuplicadoException()

        hashed_pw = generate_password_hash(nit, method='scrypt')
        new_user = Usuario(
            username=email,
            password_hash=hashed_pw,
            nombre_completo=nombre_empresa,
            rol='cliente',
            nit_empresa=nit
        )
        try:
            db.session.add(new_user)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creando cliente SQL ({email}): {e}")
            raise

        return {"password_temporal": nit}

    @staticmethod
    def login_cliente(email, password):
        """
        Login del portal B2B. Puede lanzar UsuarioNoEncontradoException,
        CuentaPendienteException, AccesoDenegadoException,
        CuentaInactivaException o CredencialesInvalidasException.
        """
        user = Usuario.query.filter(or_(Usuario.username == email, Usuario.cedula == email)).first()

        logger.debug("--- DEBUG LOGIN (CLIENTE) ---")
        logger.debug(f"1. Identificador buscado (email/cedula): '{email}'")
        logger.debug(f"2. ¿Usuario encontrado en BD?: {user is not None}")
        if user:
            logger.debug(f"3. Rol del usuario: {user.rol}")
            logger.debug(f"4. Hash en BD a comparar: '{user.password_hash[:15]}...'")
        logger.debug("-------------------")

        # Mensaje deliberadamente genérico ("Usuario o contraseña incorrectos")
        # en no-encontrado e incorrecta: no revelar si el email existe.
        if not user:
            raise UsuarioNoEncontradoException("Usuario o contraseña incorrectos")

        if user.rol == 'CLIENTE_PENDIENTE':
            raise CuentaPendienteException()

        if user.rol.lower() != 'cliente':
            raise AccesoDenegadoException("Acceso denegado al portal B2B")

        if getattr(user, 'activo', None) is False:
            raise CuentaInactivaException()

        if not check_password_hash(user.password_hash, password):
            raise CredencialesInvalidasException("Usuario o contraseña incorrectos")

        pwa_token = AuthService._generar_pwa_token(user.username, 'CLIENTE')

        return {
            "session": {"user": user.username, "role": "CLIENTE"},
            "pwa_token": pwa_token,
            "requires_password_change": False,
            "user": {
                "nombre": user.nombre_completo,
                "email": user.username,
                "nit": user.nit_empresa,
                "rol": "Cliente",
                "tipo": "CLIENTE"
            }
        }

    @staticmethod
    def registrar_cliente(email, password, nit, nombre_empresa):
        """
        Registro público B2B con patrón sandboxing (rol CLIENTE_PENDIENTE
        hasta aprobación admin). Puede lanzar ValueError,
        CorreoDuplicadoException o NitDuplicadoException.
        """
        if not all([email, password, nit, nombre_empresa]):
            raise ValueError("Faltan datos obligatorios")

        if Usuario.query.filter_by(username=email).first():
            raise CorreoDuplicadoException()

        if Usuario.query.filter_by(nit_empresa=nit).first():
            raise NitDuplicadoException()

        hashed_pw = generate_password_hash(password, method='scrypt')
        nuevo_cliente = Usuario(
            username=email,
            password_hash=hashed_pw,
            nombre_completo=nombre_empresa,
            rol='CLIENTE_PENDIENTE',
            nit_empresa=nit
        )
        try:
            db.session.add(nuevo_cliente)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error en el registro público de cliente ({email}): {e}")
            raise

    @staticmethod
    def cambiar_password_cliente(email, old_password, new_password):
        """
        Cambio de contraseña de un cliente autenticado. Puede lanzar
        ValueError, UsuarioNoEncontradoException o CredencialesInvalidasException.
        """
        if not new_password:
            raise ValueError("La nueva contraseña es obligatoria")

        user = Usuario.query.filter_by(username=email, rol='cliente').first()
        if not user:
            raise UsuarioNoEncontradoException()

        if not check_password_hash(user.password_hash, old_password):
            raise CredencialesInvalidasException("La contraseña actual es incorrecta")

        try:
            user.password_hash = generate_password_hash(new_password, method='scrypt')
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error actualizando password de cliente ({email}): {e}")
            raise

    @staticmethod
    def listar_clientes():
        """ADMIN: lista todos los clientes registrados."""
        users = Usuario.query.filter_by(rol='cliente').all()
        return [{
            "nit": u.nit_empresa,
            "nombre_empresa": u.nombre_completo,
            "email": u.username,
            "estado": "ACTIVO" if u.activo else "INACTIVO",
            "ultimo_acceso": u.ultimo_acceso.strftime("%Y-%m-%d %H:%M:%S") if u.ultimo_acceso else ""
        } for u in users]

    @staticmethod
    def resetear_password_cliente(email):
        """ADMIN: resetea la contraseña de un cliente al valor de su NIT."""
        user = Usuario.query.filter_by(username=email, rol='cliente').first()
        if not user:
            raise UsuarioNoEncontradoException()

        try:
            user.password_hash = generate_password_hash(user.nit_empresa, method='scrypt')
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error reseteando password de cliente ({email}): {e}")
            raise

        return {"password_temporal": user.nit_empresa}

    @staticmethod
    def toggle_estado_cliente(email, nuevo_estado):
        """ADMIN: activa/desactiva una cuenta de cliente."""
        user = Usuario.query.filter_by(username=email, rol='cliente').first()
        if not user:
            raise UsuarioNoEncontradoException()

        try:
            user.activo = (str(nuevo_estado).strip().upper() == 'ACTIVO')
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error cambiando estado de cliente ({email}): {e}")
            raise

        return {"activo": user.activo}
