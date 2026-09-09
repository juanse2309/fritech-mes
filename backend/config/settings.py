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

