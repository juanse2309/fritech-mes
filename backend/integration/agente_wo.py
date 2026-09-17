# -*- coding: utf-8 -*-
"""
Agente Sincronizador Local - World Office <-> FriTech
---------------------------------------------------
Este script se ejecuta localmente en la red on-premise de la empresa.
Extrae datos de la base de datos de SQL Server y los envía de forma segura
al endpoint de FriTech en la nube (Render).

Requisitos:
    pip install pyodbc requests python-dotenv

Ejecución:
    python agente_wo.py
"""

import os
import sys
import json
import logging
from decimal import Decimal
import datetime
import pyodbc
import requests
from dotenv import load_dotenv

# Cargar variables de entorno locales si existe un archivo .env
load_dotenv()

# Configurar logging local para monitoreo
# Forzar UTF-8 en la salida de consola (evita crash con emojis en Windows cp1252)
import io
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente_wo.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')),
        # Log persistente en disco: sin esto, la única prueba de que el
        # agente corrió (y cuándo) era la consola, que se pierde en cuanto
        # se cierra la ventana o corre por tarea programada sin consola.
        logging.FileHandler(_LOG_PATH, encoding='utf-8')
    ]
)
logger = logging.getLogger("AgenteWO")

# ====================================================================
# CONFIGURACIÓN DE CONEXIÓN Y SEGURIDAD
# ====================================================================
# Configuración de base de datos SQL Server (World Office)
DB_DRIVER = os.getenv("WO_DB_DRIVER", "{ODBC Driver 17 for SQL Server}")
DB_SERVER = os.getenv("WO_DB_SERVER", r"SERVERWO\WORLDOFFICE17")
DB_DATABASE = os.getenv("WO_DB_DATABASE", "FRIPARTS2021")
DB_UID = os.getenv("WO_DB_UID", "wo_cliente")
DB_PWD = os.getenv("WO_DB_PWD") or os.getenv("WO_PASSWORD")
if not DB_PWD:
    raise RuntimeError("WO_DB_PWD / WO_PASSWORD no está configurada")

# URL de la API de FriTech en Render
# NOTA: Cambiar esta URL por la URL real de producción en Render cuando esté desplegado.
FRITECH_API_URL = os.getenv("FRITECH_API_URL", "https://proyecto-friparts.onrender.com/api/wo/recibir_datos")

# API Key para autenticar contra la app en la nube
WO_SYNC_API_KEY = os.getenv("WO_SYNC_API_KEY")
if not WO_SYNC_API_KEY:
    raise RuntimeError("WO_SYNC_API_KEY no está configurada")


# ====================================================================
# SERIALIZACIÓN PERSONALIZADA PARA JSON
# ====================================================================
class SQLServerJSONEncoder(json.JSONEncoder):
    """
    Encoder de JSON personalizado para manejar tipos de datos típicos de SQL Server:
    - Decimal (para valores monetarios o de precisión)
    - datetime / date
    - bytes
    """
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='ignore')
        return super(SQLServerJSONEncoder, self).default(obj)


# ====================================================================
# FUNCIONES DE EXTRACT & SYNC
# ====================================================================
def probar_y_sincronizar_productos():
    """
    Realiza una extracción de prueba de 5 productos de la vista V_PRODUCTOS
    y los envía al backend en la nube.
    """
    # Construir el string de conexión a SQL Server
    conn_str = (
        f"DRIVER={DB_DRIVER};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        f"UID={DB_UID};"
        f"PWD={DB_PWD};"
        # Opciones recomendadas para redes locales/on-premise
        "Timeout=30;"
    )

    logger.info("📡 Iniciando proceso de sincronización local...")
    logger.info(f"🔑 Servidor SQL Server: {DB_SERVER}")
    logger.info(f"📂 Base de Datos: {DB_DATABASE}")
    logger.info(f"🌐 Enviando datos a: {FRITECH_API_URL}")

    conn = None
    try:
        # 1. Establecer conexión con la BD local
        logger.info("Conectando a la base de datos SQL Server de World Office...")
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        # 2. Inspeccionar Vista_Tabla_Inventarios para encontrar la columna de SKU
        nombre_vista = "Vista_Existencias"
        registros = []

        logger.info("Inspeccionando columnas de Vista_Tabla_Inventarios para detectar el SKU...")
        cursor.execute(f"SELECT TOP 1 * FROM [{DB_DATABASE}].[dbo].[Vista_Tabla_Inventarios]")
        cols_inv = [col[0] for col in cursor.description]
        cursor.fetchall()
        logger.info(f"[AUDITORIA] Columnas de Vista_Tabla_Inventarios: {cols_inv}")

        # Auto-detectar columna de ID interno, SKU/Código, Descripción, Precio
        # y Clasificacion. Descripción/Precio son necesarios para que el UPSERT
        # en la nube pueda crear productos nuevos con datos de catálogo reales
        # (no solo el saldo) -- ver ProductoRepository.upsert_productos_wo.
        #
        # Clasificacion es OBLIGATORIA para filtrar: Vista_Tabla_Inventarios
        # mezcla productos de inventario real (Clasificacion='Producto': IPT
        # terminados, IMP materias primas, PNF no fabricados, IPP en proceso)
        # con cuentas contables que NO son productos (Clasificacion='Gasto',
        # 'Activo Fijo', 'Servicio', 'Diferido', etc. -- ej. "Honorarios
        # Auditoria Externa", "Gastos medicos y drogas"). Confirmado contra
        # SQL Server real (scratch/investigar_clasificacion_wo.py, 2026-08-10):
        # sin este filtro, ~774 de 1600 filas sincronizadas eran cuentas
        # contables, no productos -- el UPSERT las hubiera insertado como
        # "productos" en db_productos.
        col_id_inv   = None  # La FK que une con Vista_Existencias (IdInventario)
        col_sku      = None  # El código alfanumérico del producto (ej: FR-123)
        col_desc     = None  # Descripción del producto
        col_precio   = None  # Precio de venta
        col_clasif   = None  # 'Producto' vs 'Gasto'/'Activo Fijo'/'Servicio'/'Diferido'
        for c in cols_inv:
            cn = c.lower().replace('ó','o').replace('é','e').replace('á','a').replace('í','i')
            if col_id_inv is None and cn in ('idinventario', 'id_inventario', 'id'):
                col_id_inv = c
            if col_sku is None and ('codigo' in cn or 'referencia' in cn or 'sku' in cn or 'articulo' in cn):
                col_sku = c
            if col_desc is None and ('descripcion' in cn or 'nombre' in cn):
                col_desc = c
            if col_precio is None and 'precio' in cn:
                col_precio = c
            if col_clasif is None and 'clasificacion' in cn:
                col_clasif = c

        logger.info(f"[AUDITORIA] col_id_inv='{col_id_inv}' | col_sku='{col_sku}' | "
                    f"col_desc='{col_desc}' | col_precio='{col_precio}' | col_clasif='{col_clasif}'")

        if col_id_inv and col_sku:
            select_desc = f"i.[{col_desc}]" if col_desc else "NULL"
            select_precio = f"i.[{col_precio}]" if col_precio else "NULL"
            # Si no se detecta la columna de clasificacion, NO se filtra (se
            # preserva el comportamiento anterior) pero se advierte fuerte en
            # el log, porque sin este filtro pueden colarse cuentas contables.
            filtro_clasif = f"AND i.[{col_clasif}] = 'Producto'" if col_clasif else ""
            if not col_clasif:
                logger.warning("[ADVERTENCIA] No se detecto columna de Clasificacion -- "
                                "NO se esta filtrando por 'Producto'. Cuentas contables "
                                "de WO podrian colarse como si fueran productos.")
            query_join = f"""
                SELECT
                    i.[{col_sku}] AS codigo_producto,
                    SUM(e.[Existencia]) AS stock_wo,
                    MAX({select_desc}) AS descripcion,
                    MAX({select_precio}) AS precio_venta
                FROM [{DB_DATABASE}].[dbo].[Vista_Existencias] e
                INNER JOIN [{DB_DATABASE}].[dbo].[Vista_Tabla_Inventarios] i
                    ON e.[IdInventario] = i.[{col_id_inv}]
                WHERE e.[Existencia] IS NOT NULL
                {filtro_clasif}
                GROUP BY i.[{col_sku}]
            """
            try:
                logger.info(f"Ejecutando JOIN: {query_join.strip()}")
                cursor.execute(query_join)
                for row in cursor.fetchall():
                    codigo_val = str(row[0] or '').strip()
                    if not codigo_val:
                        continue
                    try:
                        stock_val = float(row[1])
                    except (ValueError, TypeError):
                        stock_val = 0.0
                    descripcion_val = str(row[2] or '').strip()
                    try:
                        precio_val = float(row[3]) if row[3] is not None else 0.0
                    except (ValueError, TypeError):
                        precio_val = 0.0
                    registros.append({
                        'codigo_producto': codigo_val,
                        'stock_wo': stock_val,
                        'descripcion': descripcion_val,
                        'precio_wo': precio_val,
                    })
                logger.info(f"JOIN exitoso. {len(registros)} productos con SKU real obtenidos.")
            except pyodbc.Error as e_join:
                logger.warning(f"JOIN fallido (2do intento): {e_join}")
                logger.info(f"[DIAG] Todas las columnas disponibles en Vista_Tabla_Inventarios: {cols_inv}")
        
        # Fallback si el JOIN falló o no se detectaron columnas. Sin SKU real
        # tampoco hay como resolver descripcion/precio de forma confiable, así
        # que estos registros viajan solo con stock (igual que antes de esta
        # ampliación) -- el UPSERT en la nube los tratará como actualización de
        # saldo únicamente si el código ya existe en db_productos.
        if not registros:
            logger.warning("FALLBACK: usando IdInventario directamente (sin SKU real). "
                           "Revisa el log [AUDITORIA] para ajustar col_id_inv y col_sku.")
            cursor.execute(f"""
                SELECT [IdInventario] AS codigo_producto, SUM([Existencia]) AS stock_wo
                FROM [{DB_DATABASE}].[dbo].[Vista_Existencias]
                WHERE [IdInventario] IS NOT NULL
                GROUP BY [IdInventario]
            """)
            for row in cursor.fetchall():
                codigo_val = str(row[0] or '').strip()
                if not codigo_val:
                    continue
                try:
                    stock_val = float(row[1])
                except (ValueError, TypeError):
                    stock_val = 0.0
                registros.append({'codigo_producto': codigo_val, 'stock_wo': stock_val})
            logger.info(f"Fallback exitoso. {len(registros)} registros obtenidos.")

        logger.info(f"Total final a enviar: {len(registros)} registros.")
        
        # Cerrar conexión de base de datos
        cursor.close()
        conn.close()
        conn = None

        # 3. Enviar datos a la nube
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": WO_SYNC_API_KEY
        }
        
        # Envolver los datos agregando el nombre de la vista consultada
        payload = {
            "nombre_vista": nombre_vista,
            "datos": registros
        }
        
        # DEBUG LOCAL: Verificar que los datos tienen stock real antes de enviar
        print("MUESTRA DEL PAYLOAD A ENVIAR (primeros 5):")
        for item in registros[:5]:
            print(f"  Ref: {item.get('codigo_producto')} | stock_wo: {item.get('stock_wo')} | "
                  f"descripcion: {item.get('descripcion', '')} | precio_wo: {item.get('precio_wo', 0)}")
        
        # Serializar los datos usando el encoder robusto
        payload_json = json.dumps(payload, cls=SQLServerJSONEncoder)
        
        logger.info(f"[DEBUG] URL de Envío Exacta: {FRITECH_API_URL}")
        logger.info(f"[DEBUG] Headers de Envío: {headers}")
        logger.info(f"[DEBUG] Tamaño del Payload: {len(payload_json)} bytes")
        
        import time as _time

        MAX_INTENTOS = 3
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                logger.info(f"Enviando datos al servidor (intento {intento}/{MAX_INTENTOS})...")
                response = requests.post(
                    FRITECH_API_URL,
                    data=payload_json,
                    headers=headers,
                    timeout=60
                )

                logger.info(f"[DEBUG] Código de Respuesta del Servidor: {response.status_code}")
                logger.info(f"[DEBUG] Respuesta Completa del Servidor: {response.text}")

                if response.status_code == 200:
                    logger.info("Sincronizacion exitosa!")
                    return True
                elif response.status_code == 401:
                    logger.error("Error 401: No autorizado. Verifica la API Key (WO_SYNC_API_KEY).")
                    return False  # No tiene sentido reintentar en 401
                elif response.status_code == 400:
                    logger.error(f"Error 400 (datos inválidos): {response.text}")
                    return False  # No tiene sentido reintentar en 400
                else:
                    logger.warning(f"Error en el servidor (Status {response.status_code}). Reintentando...")

            except requests.exceptions.RequestException as e_net:
                logger.warning(f"Error de red (intento {intento}): {e_net}")

            if intento < MAX_INTENTOS:
                espera = 5 * intento  # 5s, 10s
                logger.info(f"Esperando {espera}s antes del siguiente intento...")
                _time.sleep(espera)

        logger.error(f"Fallaron los {MAX_INTENTOS} intentos de envío al servidor.")
        return False
        
    except pyodbc.Error as e_sql:
        logger.error(f"❌ Error de base de datos (SQL Server): {e_sql}")
        return False
    except Exception as e:
        logger.error(f"❌ Error inesperado en el agente de sincronización: {e}")
        return False
    finally:
        # Asegurar el cierre de la conexión en caso de falla
        if conn:
            try:
                conn.close()
                logger.info("Conexión de base de datos cerrada en cláusula cleanup.")
            except Exception:
                pass


if __name__ == '__main__':
    # Ejecutar la prueba
    exito = probar_y_sincronizar_productos()
    if exito:
        logger.info("Proceso terminado con éxito.")
        sys.exit(0)
    else:
        logger.error("Proceso terminado con errores.")
        sys.exit(1)
