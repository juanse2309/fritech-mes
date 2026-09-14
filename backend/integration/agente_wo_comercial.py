import os
import sys
import io
import re
import logging
import pyodbc
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno locales si existe un archivo .env (y para que tome el token correcto)
load_dotenv()

# Log persistente en disco (mismo patrón que agente_wo.py): sin esto, la
# única prueba de que este agente corrió -- y si falló -- era la consola de
# la tarea programada, que nadie mira. Forzar UTF-8 evita crash con emojis
# en la consola cp1252 de Windows.
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente_wo_comercial.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')),
        logging.FileHandler(_LOG_PATH, encoding='utf-8')
    ]
)
logger = logging.getLogger("AgenteWOComercial")

# ====================================================================
# CONFIGURACIÓN DE CONEXIÓN Y SEGURIDAD
# ====================================================================
DB_DRIVER   = os.getenv("WO_DB_DRIVER",  "{ODBC Driver 17 for SQL Server}")
DB_SERVER   = os.getenv("WO_SERVER",     r"SERVERWO\WORLDOFFICE17")
DB_DATABASE = os.getenv("WO_DB",         "FRIPARTS2021")
DB_UID      = os.getenv("WO_USER",       "wo_cliente")
DB_PWD      = os.getenv("WO_PASSWORD")
if not DB_PWD:
    raise RuntimeError("WO_PASSWORD no está configurada")

API_URL = os.getenv("API_RENDER_URL_COMERCIAL", "https://proyecto-friparts.onrender.com/api/wo/recibir_comercial")
API_KEY = os.getenv("WO_SYNC_API_KEY")
if not API_KEY:
    raise RuntimeError("WO_SYNC_API_KEY no está configurada")

# Base para verificar_sync/solicitar_sync (mismo patron y misma variable que
# agente_wo_cartera.py / agente_recordatorio_reporte_parcial.py /
# agente_cierre_jornada_ensamble.py). Antes estas dos URLs estaban
# hardcodeadas a Render sin pasar por ninguna variable de entorno, asi que
# migrar solo API_RENDER_URL_COMERCIAL no bastaba: el chequeo/limpieza del
# flag de sync seguia pegandole a Render sin importar el .env.
SYNC_API_URL = os.getenv("SYNC_API_URL", "https://proyecto-friparts.onrender.com")

# Fase 2 (conciliacion OP): permite `from backend.models.sql_models import
# OpWoStaging` aunque este script se invoque como archivo suelto (python
# backend/integration/agente_wo_comercial.py) desde cualquier cwd -- sin esto
# el import falla porque "backend" no queda en sys.path. Ninguno de los otros
# agente_wo*.py necesita esto porque nunca tocan Postgres directamente; este
# si, exclusivamente para el staging de OP (ver guardar_staging_op).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Construir el string de conexión a SQL Server exactamente como en agente_wo.py
conn_str = (
    f"DRIVER={DB_DRIVER};"
    f"SERVER={DB_SERVER};"
    f"DATABASE={DB_DATABASE};"
    f"UID={DB_UID};"
    f"PWD={DB_PWD};"
    # Opciones recomendadas para redes locales/on-premise
    "Timeout=30;"
)

CHUNK_SIZE = 2000

def enviar_datos_por_lotes(datos, url_api, headers):
    """
    Divide el payload masivo en lotes pequeños para evitar 
    timeouts en Render y locks en PostgreSQL.
    """
    total_registros = len(datos)
    for i in range(0, total_registros, CHUNK_SIZE):
        lote = datos[i:i + CHUNK_SIZE]
        payload = {
            "is_chunk": True,
            "index": i // CHUNK_SIZE,
            "total_chunks": (total_registros + CHUNK_SIZE - 1) // CHUNK_SIZE,
            "data": lote
        }
        
        try:
            # Enviamos cada lote con un timeout prudente
            response = requests.post(url_api, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            logger.info(f"[OK] Lote {(i // CHUNK_SIZE) + 1}/{(total_registros + CHUNK_SIZE - 1) // CHUNK_SIZE} enviado correctamente.")
        except requests.exceptions.RequestException as e:
            logger.error(f"[ERROR] Falló el envío del lote {i // CHUNK_SIZE}: {e}")
            if e.response is not None:
                logger.error(e.response.text)
            raise e

def extraer_ordenes_produccion(cursor, mapping):
    """
    Fase 2 del plan de conciliacion OP: extrae documentos
    Tipo_de_Documento='OP' desde las MISMAS vistas de WO que ya usa la
    extraccion comercial (Vista_Tabla_Encabezados + Movimientos_Inventario) --
    no requiere conexion ni vista nueva.

    Se mantiene como consulta separada de la comercial a proposito: el
    SELECT/procesamiento de mas abajo esta modelado para ventas/pedidos
    (cliente, IVA, vendedor) y viaja hacia recibir_comercial. Una OP no tiene
    cliente real ni IVA -- meterla en ese mismo query corromperia lo que
    recibir_comercial escribe en db_ventas/db_pedidos. Por eso esta funcion
    no envia nada por HTTP: guardar_staging_op() la persiste directo.

    `cursor` y `mapping` (Autonumerico -> Codigo_Producto) se reciben ya
    abiertos/cargados desde ejecutar_extraccion() para no repetir el
    round-trip de Vista_Tabla_Inventarios.
    """
    logger.info(">> Extrayendo Ordenes de Produccion (Tipo_de_Documento='OP')...")

    sql_op = """
    SELECT
        E.prefijo,
        E.Numero_de_Documento AS numero_documento,
        E.Fecha AS fecha,
        E.Anulado AS anulado,
        E.Verificado AS verificado,
        D.Producto AS producto_id,
        CAST(D.Cantidad AS FLOAT) AS cantidad,
        D.Bodega AS bodega
    FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Encabezados] E
    INNER JOIN [FRIPARTS2021].[dbo].[Vista_Tabla_Movimientos_Inventario] D
        ON E.Autonumerico = D.Pertenece_A
    WHERE E.Tipo_de_Documento = 'OP'
      AND YEAR(E.Fecha) >= 2024
    """
    cursor.execute(sql_op)
    columnas = [c[0] for c in cursor.description]

    registros = []
    for row in cursor.fetchall():
        item = dict(zip(columnas, row))

        prefijo = str(item.get('prefijo') or '').strip()
        numero_doc = item.get('numero_documento')
        # Mismo formato ya observado en planta (db_inyeccion.orden_produccion,
        # db_programacion.op_world_office, etc.): cuando WO no trae prefijo se
        # reporta el numero puro, sin guion inventado.
        numero_op = f"{prefijo}-{int(numero_doc)}" if prefijo else str(int(numero_doc))

        # Mapeo de producto en memoria -- mismo criterio que la extraccion
        # comercial: se asigna exactamente lo que devolvio el catalogo de WO
        # (o el id crudo si no se halla), sin prefijos inventados.
        prod_id = str(item.get('producto_id', '')).strip()
        codigo_producto = str(mapping.get(prod_id, prod_id) or '').strip()

        registros.append({
            "numero_op": numero_op,
            "codigo_producto": codigo_producto,
            "cantidad": item.get('cantidad') or 0,
            "fecha": item.get('fecha'),
            "anulado": bool(item.get('anulado') or 0),
            "verificado": bool(item.get('verificado') or 0),
            "bodega": str(item.get('bodega') or '').strip() or None,
            "cantidad_ept": None,   # se completa en _adjuntar_ept()
            "numero_ept": None,     # se completa en _adjuntar_ept()
        })

    _adjuntar_ept(cursor, mapping, registros)

    logger.info(f"[OK] {len(registros)} filas de OP extraidas de WO (documentos, no lineas por producto).")
    return registros


def _adjuntar_ept(cursor, mapping, registros):
    """
    Completa `cantidad_ept` y `numero_ept` en cada fila de OP con lo que
    realmente ingreso a inventario segun su EPT (Entrada de Producto
    Terminado).

    El vinculo EPT->OP no existe como columna en WO: vive en la nota del
    encabezado ("EPT GENERADA POR OP No 304048", o "... No EMP 500458"). Se
    verifico contra produccion que el 100% de las EPT (2024+) siguen ese
    formato, por lo que basta tomar el ultimo numero de la nota. Se cruza por
    digitos porque la nota a veces trae el prefijo separado ("OP No EMP 500458")
    y otras no.

    numero_ept es el propio identificador del documento EPT (prefijo+numero
    de WO), para que planta lo pueda ubicar directo en World Office. Como una
    OP casi siempre genera UNA sola EPT (3.562 EPT para 3.566 OP, verificado),
    basta con quedarse con el primer numero de documento visto por esa clave.

    Se agrega por (numero_op_digitos, codigo_producto) para poder comparar
    linea a linea, no solo el total del documento.
    """
    logger.info(">> Extrayendo EPT (Entrada de Producto Terminado) para cruzar contra las OP...")

    cursor.execute("""
        SELECT E.Nota AS nota, E.prefijo AS prefijo, E.Numero_de_Documento AS numero_documento,
               D.Producto AS producto_id, CAST(D.Cantidad AS FLOAT) AS cantidad
        FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Encabezados] E
        INNER JOIN [FRIPARTS2021].[dbo].[Vista_Tabla_Movimientos_Inventario] D
            ON E.Autonumerico = D.Pertenece_A
        WHERE E.Tipo_de_Documento = 'EPT'
          AND YEAR(E.Fecha) >= 2024
          AND E.Anulado = 0
    """)

    cantidades = {}
    documentos = {}
    for nota, prefijo, numero_documento, producto_id, cantidad in cursor.fetchall():
        numeros = re.findall(r'\d+', str(nota or ''))
        if not numeros:
            continue
        op_dig = numeros[-1]
        prod_id = str(producto_id or '').strip()
        codigo = str(mapping.get(prod_id, prod_id) or '').strip().upper()
        clave = (op_dig, codigo)

        cantidades[clave] = cantidades.get(clave, 0.0) + float(cantidad or 0)
        if clave not in documentos:
            pref = str(prefijo or '').strip()
            documentos[clave] = f"{pref}-{int(numero_documento)}" if pref else str(int(numero_documento))

    con_ept = 0
    for r in registros:
        clave = (re.sub(r'\D', '', r["numero_op"]), (r["codigo_producto"] or '').upper())
        if clave in cantidades:
            r["cantidad_ept"] = cantidades[clave]
            r["numero_ept"] = documentos.get(clave)
            con_ept += 1

    logger.info(f"[OK] EPT cruzada: {con_ept} de {len(registros)} lineas de OP tienen entrada registrada.")


def guardar_staging_op(registros):
    """
    Persistencia directa a Postgres -- no pasa por recibir_comercial ni por
    ninguna ruta web. db_op_wo_staging es una tabla interna de
    diagnostico/conciliacion sin dato financiero ni de cliente, por lo que no
    necesita el mismo envoltorio de seguridad (freno manual, chunking, API key)
    que si aplica a la sync comercial.

    Truncate + bulk insert dentro de UNA sola transaccion (engine.begin()):
    si el insert falla, el truncate se revierte tambien -- la tabla nunca
    queda vacia a medias.
    """
    from sqlalchemy import create_engine, text
    from backend.models.sql_models import OpWoStaging

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("[OP-STAGING] DATABASE_URL no configurada -- se omite la persistencia del staging de OP.")
        return

    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    engine = create_engine(db_url)
    try:
        OpWoStaging.__table__.create(engine, checkfirst=True)

        # create(checkfirst=True) NO agrega columnas nuevas a una tabla que ya
        # existe. Sin esto, la primera corrida despues de ampliar el modelo
        # falla al insertar cantidad_ept contra una tabla creada antes. El
        # lock_timeout evita que el agente quede colgado indefinidamente si
        # algo mas esta leyendo la tabla: si no consigue el lock, sigue sin la
        # columna en vez de bloquear la sincronizacion.
        with engine.begin() as conn:
            conn.execute(text("SET lock_timeout = '10s'"))
            for columna, tipo in (("cantidad_ept", "NUMERIC(18,2)"), ("numero_ept", "VARCHAR(50)")):
                conn.execute(text(
                    f"ALTER TABLE db_op_wo_staging ADD COLUMN IF NOT EXISTS {columna} {tipo}"
                ))

        with engine.begin() as conn:
            conn.execute(OpWoStaging.__table__.delete())
            if registros:
                conn.execute(OpWoStaging.__table__.insert(), registros)
        logger.info(f"[OK] db_op_wo_staging actualizada: {len(registros)} OP (truncate+insert atomico).")
    finally:
        engine.dispose()


def ejecutar_extraccion():
    logger.info("=" * 60)
    logger.info("[>>] INICIANDO EXTRACCION COMERCIAL (AÑO ACTUAL) DESDE WO")
    logger.info("=" * 60)
    
    conn = None
    try:
        conn = pyodbc.connect(conn_str, timeout=15)
        cursor = conn.cursor()
        
        # 1. Crear Diccionario de Mapeo en Memoria (Catálogo Maestro)
        # Auto-detectamos la columna de descripción del producto (igual que
        # agente_wo.py hace para Vista_Existencias) porque no hay forma de
        # confirmar su nombre exacto sin conexión directa a WO. Si no se
        # detecta, la descripción viaja vacía en vez de romper la extracción.
        logger.info(">> Cargando catálogo maestro de inventarios en memoria...")
        cursor.execute("SELECT TOP 1 * FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Inventarios]")
        cols_inventarios = [col[0] for col in cursor.description]
        cursor.fetchall()

        def _normalizar(nombre_col):
            return nombre_col.lower().replace('ó', 'o').replace('é', 'e').replace('á', 'a').replace('í', 'i')

        # Confirmado contra WO real (2026-08-12, ver commit): la vista trae
        # DOS columnas de descripcion -- "Descripción" (con tilde, llega
        # corrupta como "Descripci�n" via este driver/codepage) y
        # "Descripcion" (limpia, ASCII). Se prioriza el match exacto por la
        # version limpia; el fallback generico queda solo por si el nombre
        # cambia en otro entorno/version de WO.
        col_descripcion_inv = None
        for c in cols_inventarios:
            if _normalizar(c) == 'descripcion':
                col_descripcion_inv = c
                break
        if not col_descripcion_inv:
            for c in cols_inventarios:
                cn = _normalizar(c)
                if 'descripcion' in cn or 'nombre' in cn:
                    col_descripcion_inv = c
                    break
        logger.info(f"[AUDITORIA] Columna de descripción detectada en Vista_Tabla_Inventarios: '{col_descripcion_inv}'")

        select_desc_inv = f", [{col_descripcion_inv}]" if col_descripcion_inv else ""
        cursor.execute(
            f"SELECT Autonumerico, Codigo_Producto{select_desc_inv} "
            f"FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Inventarios]"
        )
        mapping = {}
        descripciones = {}
        for row in cursor.fetchall():
            # Autonumerico puede venir como numérico o string, lo forzamos a string para cruzar
            mapping[str(row[0])] = row[1]
            if col_descripcion_inv:
                descripciones[str(row[0])] = str(row[2] or '').strip()

        # 1.a Fase 2 (conciliacion OP): extraer y persistir OP con el mismo
        # cursor y el mismo mapping ya cargados. Aislado en su propio
        # try/except -- un fallo aqui (ej. DATABASE_URL no disponible desde la
        # maquina de planta) nunca debe tumbar la sync comercial que sigue
        # abajo.
        try:
            registros_op = extraer_ordenes_produccion(cursor, mapping)
            guardar_staging_op(registros_op)
        except Exception as e:
            logger.error(f"[OP-STAGING] Fallo extrayendo/guardando OP (no afecta la sync comercial): {e}")

        # 1.b Auto-detectar columna de IVA en el detalle y de identificación/NIT
        # del tercero externo en el encabezado. Mismo motivo: no hay forma de
        # confirmar estos nombres sin acceso directo a la vista de WO.
        cursor.execute("SELECT TOP 1 * FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Movimientos_Inventario]")
        cols_movimientos = [col[0] for col in cursor.description]
        cursor.fetchall()
        col_iva = None
        for c in cols_movimientos:
            cn = _normalizar(c)
            if col_iva is None and 'iva' in cn:
                col_iva = c
        logger.info(f"[AUDITORIA] Columna de IVA detectada en Vista_Tabla_Movimientos_Inventario: '{col_iva}'")

        cursor.execute("SELECT TOP 1 * FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Encabezados]")
        cols_encabezados = [col[0] for col in cursor.description]
        cursor.fetchall()
        # Confirmado contra WO real (2026-08-12): la columna correcta es
        # "Identificacion_Tercero" (el NUMERO de NIT/CC). OJO: tambien existe
        # "Tipo_identificacion_tercero_externo", que es solo el TIPO de
        # documento (NIT/CC/CE) -- contiene "identificacion" y "externo" en
        # el nombre, por lo que la heuristica original (que exigia
        # "externo") la habria escogido por error en vez del numero real.
        # Por eso ahora se prioriza el match exacto y luego se excluye
        # explicitamente cualquier columna que empiece por "tipo_".
        col_nit = None
        for c in cols_encabezados:
            if _normalizar(c) == 'identificacion_tercero':
                col_nit = c
                break
        if not col_nit:
            for c in cols_encabezados:
                cn = _normalizar(c)
                if cn.startswith('tipo_'):
                    continue
                if 'identificacion' in cn or 'nit' in cn:
                    col_nit = c
                    break
        logger.info(f"[AUDITORIA] Columna de NIT/identificación detectada en Vista_Tabla_Encabezados: '{col_nit}'")

        # OJO: la columna Iva en Vista_Tabla_Movimientos_Inventario es la TASA
        # del renglon (0.19 = 19%, 0.00 = exento), no el monto en pesos --
        # confirmado contra produccion (2026-08-12): valores observados son
        # exactamente 0.19 / 0.00 / -0.19, nunca montos grandes. Por eso aqui
        # se multiplica por el subtotal del renglon (misma formula que
        # total_ingresos) para obtener el monto real de IVA en pesos.
        select_iva = (
            f"CAST((D.Cantidad * D.Valor_Unitario * (1 - (D.Descuento/100.0)) * D.[{col_iva}]) AS FLOAT)"
            if col_iva else "NULL"
        )
        select_nit = f"E.[{col_nit}]" if col_nit else "NULL"

        # 2. Consulta SQL Definitiva simplificada
        sql = f"""
        SELECT
            E.Fecha AS fecha,
            (E.prefijo + '-' + CAST(E.Numero_de_Documento AS VARCHAR)) AS documento,
            E.Nombre_tercero_externo AS nombres,
            E.Nombres_tercero_interno AS vendedor,
            E.Ciudad_Encabezado AS zona,
            D.Producto AS productos, -- ESTE ES EL ID QUE USAREMOS PARA EL MAPEO
            CAST(D.Cantidad AS FLOAT) AS cantidad,
            CAST((D.Cantidad * D.Valor_Unitario * (1 - (D.Descuento/100.0))) AS FLOAT) AS total_ingresos,
            CAST(D.Valor_Unitario AS FLOAT) AS precio_promedio,
            {select_iva} AS iva,
            {select_nit} AS nit_cliente,
            E.Tipo_de_Documento AS tipo_doc
        FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Encabezados] E
        INNER JOIN [FRIPARTS2021].[dbo].[Vista_Tabla_Movimientos_Inventario] D
            ON E.Autonumerico = D.Pertenece_A
        WHERE YEAR(E.Fecha) >= 2024
          AND E.Tipo_de_Documento IN ('FV', 'PED', 'COT', 'NC', 'NCV', 'NCCL', 'DMC')
          AND E.Anulado = 0;
        """

        logger.info(">> Ejecutando consulta SQL...")
        cursor.execute(sql)
        
        columnas = [column[0] for column in cursor.description]
        datos = []
        total_ventas = 0.0
        
        for row in cursor.fetchall():
            item = dict(zip(columnas, row))
            
            # Formatear la fecha para que sea serializable en JSON
            if item['fecha']:
                item['fecha'] = item['fecha'].strftime('%Y-%m-%d')
            
            # Mapeo de Clasificación y Ajuste Matemático
            tipo_doc = item.get('tipo_doc', '').strip()
            
            if tipo_doc == 'PED':
                item['clasificacion'] = 'pedido'
            elif tipo_doc in ['FV', 'COT', 'NC', 'NCV', 'NCCL', 'DMC']:
                item['clasificacion'] = 'venta'

                # Ajuste matemático para devoluciones/notas crédito/devolución de mercancía
                if tipo_doc in ['NC', 'NCV', 'NCCL', 'DMC']:
                    # Se asume que total_ingresos viene en positivo, lo volvemos negativo
                    item['total_ingresos'] = float(item.get('total_ingresos', 0)) * -1
                    if item.get('iva') is not None:
                        item['iva'] = float(item['iva']) * -1

                # Para el control de seguridad local (solo FV suma al control)
                if tipo_doc == 'FV':
                    total_ventas += float(item.get('total_ingresos', 0))
            else:
                item['clasificacion'] = 'desconocido'
            # Mapeo de Producto en Memoria
            prod_id = str(item.get('productos', '')).strip()
            # Buscar en el diccionario el código real, si no lo halla, usar el original
            mapped_prod = mapping.get(prod_id, prod_id)

            # Priorización de datos reales: asignamos exactamente lo que devolvió el mapeo (o el original si falló) sin prefijos inventados
            item['productos'] = str(mapped_prod or '').strip()

            # Descripción del producto (vacía si no se detectó la columna en WO)
            item['descripcion'] = descripciones.get(prod_id, '')

            # IVA y NIT quedan en None si no se pudo detectar la columna real en WO
            item['iva'] = float(item['iva']) if item.get('iva') is not None else None
            item['nit_cliente'] = str(item['nit_cliente']).strip() if item.get('nit_cliente') is not None else None

            # Eliminar la columna tipo_doc original
            if 'tipo_doc' in item:
                del item['tipo_doc']
                
            datos.append(item)
            
        conn.close()

        logger.info(f"[OK] Extraccion completada. {len(datos)} registros encontrados.")

        # 🔒 FRENO DE SEGURIDAD OBLIGATORIO
        logger.info("=" * 60)
        logger.info(f"[SECURITY] FRENO DE SEGURIDAD - AUDITORIA FINANCIERA:")
        logger.info(f"   TOTAL VENTAS (FEV) = $ {total_ventas:,.2f}")
        logger.info("=" * 60)

        is_auto = "--auto" in sys.argv or os.getenv("AUTO_SYNC") == "True"
        if not is_auto:
            confirmacion = input(f"\nPresiona ENTER para enviar los datos a {API_URL} (o Ctrl+C para cancelar)...")
        else:
            logger.info("[INFO] Modo automático detectado. Omitiendo freno de seguridad manual...")

        # Envío POST
        logger.info(f">> Enviando datos a {API_URL} por lotes...")
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
            "X-Sync-Token": API_KEY
        }

        enviar_datos_por_lotes(datos, API_URL, headers)
        logger.info("[OK] Sincronización comercial finalizada exitosamente.")

    except Exception as e:
        logger.error(f"[FATAL] Error fatal en el proceso: {e}")
        sys.exit(1)
    finally:
        # Garantía de cierre de recursos: Cero fugas de sockets en SQL Server
        if 'conn' in locals() and conn:
            try:
                conn.close()
                logger.info("[INFO] Conexión a SQL Server cerrada limpiamente.")
            except Exception as close_err:
                logger.info(f"[INFO] La conexión a SQL Server ya estaba cerrada o no requiere cierre explícito: {close_err}")

def main():
    modo_forzado = "--forzar" in sys.argv

    check_url = f"{SYNC_API_URL}/api/wo/verificar_sync"
    sync_requerida = False

    logger.info("Verificando si hay solicitud de sincronización en el servidor...")
    try:
        resp = requests.get(check_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("sync_pendiente"):
                logger.info("[>>] Solicitud de sincronización pendiente detectada.")
                sync_requerida = True
            else:
                logger.info("[>>] No hay solicitud pendiente.")
        else:
            logger.warning(f"[WARN] No se pudo verificar el flag (HTTP {resp.status_code}).")
    except Exception as e:
        logger.warning(f"[WARN] Error al conectar con el servidor para verificar flag: {e}")

    if sync_requerida or modo_forzado or ("--auto" in sys.argv):
        if sync_requerida:
            os.environ["AUTO_SYNC"] = "True"

        ejecutar_extraccion()

        if sync_requerida:
            logger.info(">> Limpiando flag de sincronización en el servidor...")
            try:
                requests.post(f"{SYNC_API_URL}/api/wo/solicitar_sync", json={"sync_pendiente": False}, timeout=10)
                logger.info("[OK] Flag limpio.")
            except Exception as e:
                logger.warning(f"[WARN] No se pudo limpiar el flag: {e}")
    else:
        logger.info("[>>] Ejecución cancelada. Usa --forzar o --auto para extraer de todas formas.")

if __name__ == "__main__":
    main()
