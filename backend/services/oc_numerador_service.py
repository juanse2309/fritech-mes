"""
oc_numerador_service.py
========================
Consecutivo de Orden de Compra (módulo Compras a Proveedores, plan
2026-09-15). Fase 2: confirmado contra WO real (2026-09-15, lectura
autorizada por el usuario) que Tipo_de_Documento='OC' es una serie PROPIA
e independiente -- no comparte bloque con OP ni con ningún otro tipo de
documento (293 documentos reales, numerados 1-297 al momento de
confirmar). Por eso el cálculo aquí es más simple que
OpNumeradorService: no hace falta lógica de "bloque activo" ni de
ámbito compartido entre varias áreas.

Reemplaza la Fase 1 (secuencia nativa `oc_id_seq`): una secuencia de
Postgres aislada no sabía nada de los números que ya existen en WO, así
que si alguien crea una OC directo en WO (fuera de FRITECH) el siguiente
número local podía colisionar. El patrón de "máximo conocido + 1" con
advisory lock es el mismo que ya usa OpNumeradorService.
"""
import logging
import os

from sqlalchemy import text

from backend.core.sql_database import db

logger = logging.getLogger(__name__)

MAX_REINTENTOS_COLISION = 5


class OcNumeradorException(Exception):
    """Fallo irrecuperable del numerador de OC (colisión persistente)."""


class OcNumeradorService:

    # ------------------------------------------------------------------
    # Cálculo del piso / siguiente consecutivo
    # ------------------------------------------------------------------
    @staticmethod
    def _piso_wo():
        """Máximo consecutivo visto en el espejo db_oc_wo_staging (poblado
        por agente_wo_comercial.py, on-premise, cada vez que corre)."""
        fila = db.session.execute(text("""
            SELECT MAX(consecutivo) FROM db_oc_wo_staging WHERE anulado = false
        """)).scalar()
        return int(fila) if fila is not None else 0

    @staticmethod
    def _piso_local():
        fila = db.session.execute(text("""
            SELECT MAX(consecutivo) FROM db_ordenes_compra WHERE estado <> 'ANULADA'
        """)).scalar()
        return int(fila) if fila is not None else 0

    @staticmethod
    def _piso_wo_en_vivo():
        """
        Consulta EN VIVO (no el espejo) el máximo Numero_de_Documento de
        tipo 'OC' -- mismo criterio best-effort que
        OpNumeradorService._piso_wo_en_vivo(): pyodbc no es dependencia del
        deploy web (solo lo tienen los agentes on-premise), y el servidor
        de WO solo es alcanzable desde la red local de la planta.

        A PROPÓSITO no se usa en el camino caliente de creación de una OC
        (ver _siguiente_consecutivo): el servidor web en la nube no tiene
        ruta de red hacia WO, así que cada intento fallaría con un timeout
        de varios segundos -- eso congelaría "Crear Orden de Compra" para
        Diego cada vez. Solo participa en diagnostico(), de solo lectura.
        """
        try:
            import pyodbc
        except ImportError:
            return None, "pyodbc no está instalado en este servidor (solo está disponible donde corren los agentes on-premise)"

        driver = os.getenv("WO_DB_DRIVER", "{ODBC Driver 17 for SQL Server}")
        server = os.getenv("WO_SERVER")
        database = os.getenv("WO_DB")
        uid = os.getenv("WO_USER")
        pwd = os.getenv("WO_PASSWORD")
        if not all([server, database, uid, pwd]):
            return None, "Credenciales de WO (WO_SERVER/WO_DB/WO_USER/WO_PASSWORD) no configuradas en este servidor"

        conn_str = (
            f"DRIVER={driver};SERVER={server};DATABASE={database};"
            f"UID={uid};PWD={pwd};Timeout=6;"
        )
        try:
            conn = pyodbc.connect(conn_str, timeout=6)
        except Exception as e:
            logger.warning(f"[OcNumerador] No se pudo conectar a WO en vivo para el diagnóstico: {e}")
            return None, f"No se pudo conectar a WO en vivo: {e}"

        try:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT MAX(Numero_de_Documento)
                FROM [{database}].[dbo].[Vista_Tabla_Encabezados]
                WHERE Tipo_de_Documento = 'OC' AND Anulado = 0
            """)
            fila = cur.fetchone()
            valor = int(fila[0]) if fila and fila[0] is not None else 0
            return valor, None
        except Exception as e:
            logger.warning(f"[OcNumerador] Falló la consulta en vivo a WO: {e}")
            return None, f"Falló la consulta en vivo a WO: {e}"
        finally:
            conn.close()

    @staticmethod
    def _siguiente_consecutivo():
        """max(piso_wo, piso_local) + 1 -- sin bloque activo, serie propia
        de 'OC' (ver docstring del módulo)."""
        piso = max(OcNumeradorService._piso_wo(), OcNumeradorService._piso_local())
        return piso + 1

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    @staticmethod
    def generar_siguiente_numero_oc(db_session):
        """
        Genera el siguiente 'OC-XXXX' de forma segura ante concurrencia:
        advisory lock transaccional (mismo patrón que
        OpNumeradorService.obtener_o_reservar) + reintento si ya existe
        una fila con ese numero_oc (constraint UNIQUE en
        OrdenCompraProveedor.numero_oc).

        No hace commit -- el llamador (OrdenCompraService.crear) decide
        cuándo confirmar, dentro de su propia transacción. El lock se
        libera solo al terminar esa transacción (commit o rollback), no
        aquí, así que ningún otro request puede calcular el mismo
        "siguiente" mientras tanto.
        """
        db_session.execute(text("SELECT pg_advisory_xact_lock(hashtext('oc_numerador:global'))"))

        ultimo_error = None
        for intento in range(MAX_REINTENTOS_COLISION):
            consecutivo = OcNumeradorService._siguiente_consecutivo()
            numero_oc = f"OC-{consecutivo}"
            existe = db_session.execute(
                text("SELECT 1 FROM db_ordenes_compra WHERE numero_oc = :n"),
                {"n": numero_oc},
            ).scalar()
            if not existe:
                return numero_oc, consecutivo
            logger.warning(
                f"[OcNumerador] Colisión en {numero_oc!r} (intento {intento + 1}/{MAX_REINTENTOS_COLISION})."
            )

        raise OcNumeradorException(
            f"No se pudo generar un numero_oc único tras {MAX_REINTENTOS_COLISION} intentos"
        ) from ultimo_error

    @staticmethod
    def diagnostico():
        """
        Solo lectura: expone el estado del numerador sin reservar nada.
        Incluye _piso_wo_en_vivo() -- lectura directa a WO -- que aquí SÍ
        participa (a diferencia de _siguiente_consecutivo) porque este
        endpoint es de diagnóstico manual, no el camino caliente de crear
        una OC.
        """
        piso_wo = OcNumeradorService._piso_wo()
        piso_local = OcNumeradorService._piso_local()
        piso_wo_vivo, error_vivo = OcNumeradorService._piso_wo_en_vivo()

        piso = max(piso_wo, piso_local, piso_wo_vivo or 0)

        return {
            'piso_wo_staging': piso_wo,
            'piso_local_generadas': piso_local,
            'piso_wo_en_vivo': piso_wo_vivo,
            'piso_wo_en_vivo_error': error_vivo,
            'espejo_desactualizado': bool(piso_wo_vivo is not None and piso_wo_vivo > piso_wo),
            'piso_efectivo': piso,
            'siguiente_numero_oc': f"OC-{piso + 1}",
        }
