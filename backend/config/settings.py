"""
Configuración centralizada de la aplicación.
Carga variables desde .env y valida que existan.
"""
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

class Settings:
    """Configuración global de la aplicación."""
    
    # Cache
    CACHE_TTL = int(os.getenv('CACHE_TTL', 120))
    CACHE_ENABLED = os.getenv('CACHE_ENABLED', 'true').lower() == 'true'
    
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY')
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    ENV = os.getenv('FLASK_ENV', 'development')
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'logs/app.log')


class Empresa:
    """
    Identidad de negocio de la empresa que corre esta instancia -- separado
    de Settings (config técnica de Flask) porque esto es dato de negocio que
    un cliente nuevo debe poder fijar por variable de entorno al desplegar su
    propia instancia, sin tocar código.
    """
    NOMBRE = os.getenv('EMPRESA_NOMBRE', 'FRIPARTS')
    # Texto corto que acompaña al nombre en el sidebar y el título de la
    # pestaña del navegador (ej. "Sistema de Producción").
    SUBTITULO = os.getenv('EMPRESA_SUBTITULO', 'Sistema de Producción')
    # Archivo bajo frontend/static/img/ usado como logo del sidebar, del
    # modal de login y del landing de esta instancia (imagen rectangular,
    # el isologo tal cual). Debe existir ahí antes de desplegar -- no se
    # sube automáticamente.
    LOGO_ARCHIVO = os.getenv('EMPRESA_LOGO_ARCHIVO', 'logo_friparts.png')
    # Íconos CUADRADOS (con padding, pensados para un mask circular/redondeado)
    # usados como favicon, apple-touch-icon y en el manifest.json de la PWA --
    # un logo rectangular ahí se ve recortado/aplastado. Deben existir en
    # frontend/static/img/ antes de desplegar; NO se generan automáticamente.
    ICONO_PWA_192 = os.getenv('EMPRESA_ICONO_PWA_192', 'icon-192.png')
    ICONO_PWA_512 = os.getenv('EMPRESA_ICONO_PWA_512', 'icon-512.png')
    # Color de acento de la UI (badges, encabezados de tabla, paginación,
    # scrollbars -- ver frontend/static/css/styles.css). Defaults = el morado
    # que ya usa FRIPARTS hoy, para no cambiarle nada a esa instancia si no
    # se configuran estas variables. "_OSCURO" es el tono de hover/activo de
    # cada uno (ya existía como color fijo separado, no se deriva por
    # cálculo para no arriesgar un tono distinto al que ya está en producción).
    COLOR_PRIMARIO = os.getenv('EMPRESA_COLOR_PRIMARIO', '#6366f1')
    COLOR_PRIMARIO_OSCURO = os.getenv('EMPRESA_COLOR_PRIMARIO_OSCURO', '#4f46e5')
    COLOR_SECUNDARIO = os.getenv('EMPRESA_COLOR_SECUNDARIO', '#8b5cf6')
    COLOR_SECUNDARIO_OSCURO = os.getenv('EMPRESA_COLOR_SECUNDARIO_OSCURO', '#7c3aed')

    @classmethod
    def color_primario_rgb(cls) -> str:
        """'#6366f1' -> '99, 102, 241', para los rgba() de sombra ligados
        al color primario (ver .badge.bg-primary, focus rings, etc)."""
        h = cls.COLOR_PRIMARIO.lstrip('#')
        return ', '.join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))

    # Prefijo que se antepone a un código numérico huérfano SOLO cuando el
    # llamador pide explícitamente ese opt-in (ver
    # formatters.preservar_o_normalizar_prefijo) -- nunca se infiere de otra
    # forma. Es la única "división por defecto" que tiene esta instancia.
    PREFIJO_PRODUCTO_PRINCIPAL = os.getenv('EMPRESA_PREFIJO_PRODUCTO', 'FR-')

    # Qué tarjetas de "Staff <división>" se muestran en la pantalla de
    # ingreso (frontend/templates/index.html). Por defecto las dos, para no
    # cambiar el comportamiento de la instancia actual (que sirve ambas
    # divisiones); una instancia nueva de un solo cliente define
    # EMPRESA_DIVISIONES_STAFF=FRIPARTS (o el nombre que corresponda) para
    # que solo aparezca la suya.
    DIVISIONES_STAFF = [
        d.strip().upper() for d in os.getenv('EMPRESA_DIVISIONES_STAFF', 'FRIPARTS,FRIMETALS').split(',') if d.strip()
    ]
    # Si esta instancia atiende clientes externos (portal de pedidos) o es
    # solo de uso interno de staff.
    MOSTRAR_PORTAL_CLIENTES = os.getenv('EMPRESA_PORTAL_CLIENTES', 'true').lower() == 'true'

    # Compatibilidad con el catálogo legado 'metals_productos' (tabla
    # separada de db_productos, ver MetalsProducto/ProductoRepository). La
    # instancia compartida original (FRIPARTS+FRIMETALS en una sola base,
    # hoy en Render) todavía depende de esta tabla -- default True para no
    # cambiarle el comportamiento. Una instancia nueva de un solo cliente
    # (ej. el piloto de FRIMETALS standalone, con su catálogo ya migrado a
    # db_productos) define EMPRESA_CATALOGO_METALS_LEGACY=false para que
    # ?division=frimetals / tenant="frimetals" usen el modelo Producto
    # estándar en vez de buscar una tabla metals_productos que no existe ahí.
    CATALOGO_METALS_LEGACY = os.getenv('EMPRESA_CATALOGO_METALS_LEGACY', 'true').lower() == 'true'

    # Nombre EXACTO del tercero de esta empresa tal como está registrado en
    # su World Office ("Encab: Empresa" en los archivos planos de
    # exportación de Facturación -- ver FacturacionService.procesar_datos_wo
    # / generar_dataframe_exportacion). WO es estricto con el string exacto:
    # 'FRIPARTS S.A.S' (con puntos) fue rechazado por el migrador real de WO,
    # solo pasó 'FRIPARTS SAS' (prueba 2026-08-26, ver wo_templates.py) --
    # nunca inventar este valor para un cliente nuevo, confirmarlo contra su
    # WO real antes de desplegar.
    RAZON_SOCIAL_WO = os.getenv('EMPRESA_RAZON_SOCIAL_WO', 'FRIPARTS SAS')
    # NIT de esta empresa usado como tercero (interno/externo) por defecto
    # en esos mismos archivos, cuando no se puede resolver el vendedor/
    # cliente real de la fila. Igual que arriba, es el NIT tal como está
    # registrado en el World Office de cada cliente -- confirmar antes de
    # desplegar, no asumir.
    NIT_WO_DEFECTO = os.getenv('EMPRESA_NIT_WO_DEFECTO', '900315300')


class Almacenes:
    """Nombres de almacenes estandarizados."""
    POR_PULIR = "POR PULIR"
    TERMINADO = "P. TERMINADO"
    ENSAMBLADO = "PRODUCTO ENSAMBLADO"
    CLIENTE = "CLIENTE"
    
    # Mapeo para normalización (FIX: typo duplicado)
    MAPEO = {
        'POR PULIR': 'POR PULIR',
        'P. TERMINADO': 'P. TERMINADO',
        'PRODUCTO ENSAMBLADO': 'PRODUCTO ENSAMBLADO',
        'PRODUCTO ENSAMBLado': 'PRODUCTO ENSAMBLADO',
        'CLIENTE': 'CLIENTE'
    }
    
    @classmethod
    def normalizar(cls, almacen: str) -> str:
        return cls.MAPEO.get(almacen, almacen)
    
    @classmethod
    def es_valido(cls, almacen: str) -> bool:
        return almacen in cls.MAPEO

