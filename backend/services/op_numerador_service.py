"""
op_numerador_service.py
========================
FRITECH asigna el numero de OP -- antes lo hacia un humano tecleandolo en
World Office. Reunion 2026-08-25, corte 31-ago-2026.

Regla "sagrada" (palabras del usuario): desde el corte, ninguna OP se crea
a mano en ningun lado. Inyeccion al programar, ensamble al programar,
empaque al reportar -- los tres toman la ultima que hay y asignan la
siguiente, automaticamente.

Diseno verificado contra datos reales de db_op_wo_staging (13.022 filas,
2026-08-25):
  - La serie de consecutivos es GLOBAL entre INY/ENS/EMP, no una por
    prefijo (0 numeros repetidos entre prefijos distintos en toda la
    tabla). El piso se calcula con un solo MAX.
  - El prefijo 'AJ' usa formato de fecha (AJ-202402), no consecutivo --
    se excluye del calculo del piso o dispara el numero a millones.
  - WO respeta el numero impuesto por el archivo plano, y si ya existe
    RECHAZA la carga completa -- red de seguridad adicional ante una
    colision no detectada aqui.

Correccion 2026-08-26 (confirmado por la jefa de FriParts, via WO): los
numeros que empiezan por 3 (bloque 3xxxxx) son los que WO identifica para
algo de costos -- no es una diferencia de "como calcula", es que el bloque
en si mismo tiene un significado en WO. El bloque 5xxxxx (donde vive el
EMP historico desde julio -- ver plan) NO debe usarse para OP nuevas.
Por eso el piso ya NO es el maximo global entre todos los bloques: se
restringe a BLOQUE_ACTIVO (3xxxxx), y los tres ambitos (INY/ENS/EMP)
siguen compartiendo una sola serie dentro de ese bloque, como ya lo hacia
WO -- decision explicita de la jefa: "seguir con ese, para todo,
inyeccion ensamble y empaque, con los OP seguidos como ya estan".
"""
import logging
from datetime import date as date_cls

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.core.sql_database import db
from backend.models.sql_models import OpGenerada, AppConfig

logger = logging.getLogger(__name__)

AMBITO_A_CLAVE_PREFIJO = {
    'INYECCION': 'op_wo.prefijo_inyeccion',
    'ENSAMBLE':  'op_wo.prefijo_ensamble',
    'EMPAQUE':   'op_wo.prefijo_empaque',
}

PREFIJOS_POR_DEFECTO = {
    'INYECCION': 'INY',
    'ENSAMBLE':  'ENS',
    'EMPAQUE':   'EMP',
}

# Excluido del calculo del piso: usa formato de fecha (AJ-202402), no
# consecutivo -- ver docstring del modulo.
PREFIJOS_EXCLUIDOS_DEL_PISO = ('AJ',)

# Bloque de consecutivos donde WO identifica algo de costos (confirmado por
# la jefa 2026-08-26) -- ver docstring del modulo. Toda OP nueva debe caer
# aqui; el bloque 5xxxxx queda fuera del calculo del piso a proposito.
BLOQUE_ACTIVO_MIN = 300000
BLOQUE_ACTIVO_MAX = 399999

MAX_REINTENTOS_COLISION = 5


class OpNumeradorException(Exception):
    """Fallo irrecuperable del numerador (ambito invalido, colision persistente)."""
    pass


class OpNumeradorService:
    """FRITECH asigna el numero de OP -- ver docstring del modulo."""

    # ------------------------------------------------------------------
    # Lectura de configuracion (AppConfig, clave/valor)
    # ------------------------------------------------------------------
    @staticmethod
    def _leer_config(clave, defecto=None):
        fila = db.session.get(AppConfig, clave)
        if fila and fila.valor not in (None, ''):
            return fila.valor
        return defecto

    @staticmethod
    def _prefijo(ambito):
        clave = AMBITO_A_CLAVE_PREFIJO.get(ambito)
        if not clave:
            raise OpNumeradorException(f"Ambito desconocido: {ambito!r}")
        return OpNumeradorService._leer_config(clave, PREFIJOS_POR_DEFECTO[ambito])

    @staticmethod
    def _offset_seguridad():
        try:
            return int(OpNumeradorService._leer_config('op_numerador.offset_seguridad', '0') or 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------
    # Calculo del piso / siguiente consecutivo
    # ------------------------------------------------------------------
    @staticmethod
    def _piso_wo():
        """
        Maximo consecutivo visto en db_op_wo_staging DENTRO DE
        BLOQUE_ACTIVO (3xxxxx), GLOBAL entre prefijos (no por prefijo --
        ver docstring del modulo), excluyendo anuladas y el prefijo AJ
        (formato de fecha, no consecutivo).

        Restringido al bloque activo a proposito: el bloque 5xxxxx (donde
        vive el EMP historico desde julio) puede tener numeros mas altos,
        pero WO identifica algo de costos por el bloque 3xxxxx -- usar el
        maximo global metería OP nuevas en el bloque equivocado.
        """
        placeholders = ', '.join(f"'{p}-%%'" for p in PREFIJOS_EXCLUIDOS_DEL_PISO)
        filtro_excluidos = (
            f"AND numero_op NOT ILIKE ALL (ARRAY[{placeholders}])"
            if PREFIJOS_EXCLUIDOS_DEL_PISO else ""
        )

        fila = db.session.execute(text(f"""
            SELECT MAX(CAST(REGEXP_REPLACE(numero_op, '^[A-Za-z]+-', '') AS BIGINT)) AS piso
            FROM db_op_wo_staging
            WHERE anulado = false
              AND numero_op ~ '^([A-Za-z]+-)?[0-9]+$'
              AND CAST(REGEXP_REPLACE(numero_op, '^[A-Za-z]+-', '') AS BIGINT)
                  BETWEEN {BLOQUE_ACTIVO_MIN} AND {BLOQUE_ACTIVO_MAX}
              {filtro_excluidos}
        """)).scalar()
        return int(fila) if fila is not None else 0

    @staticmethod
    def _piso_local():
        fila = db.session.execute(text("""
            SELECT MAX(consecutivo) FROM db_op_generadas WHERE estado <> 'ANULADA'
        """)).scalar()
        return int(fila) if fila is not None else 0

    @staticmethod
    def _siguiente_consecutivo():
        """
        max(piso_wo, piso_local) + 1 + offset. Serie global: no distingue
        prefijo, porque en los datos reales de WO tampoco lo hace.
        """
        piso = max(OpNumeradorService._piso_wo(), OpNumeradorService._piso_local(), 0)
        return piso + 1 + OpNumeradorService._offset_seguridad()

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------
    @staticmethod
    def obtener_o_reservar(ambito, fecha, maquina=None, usuario=None):
        """
        Devuelve la OpGenerada de (fecha, ambito, maquina), creandola si no
        existe. NO hace commit -- el llamador decide cuando confirmar,
        dentro de su propia transaccion (programacion, reporte de empaque,
        etc.).

        Idempotente: dos llamados con la misma clave devuelven la MISMA
        fila, gracias al indice unico parcial uq_op_generada_dia_ambito.
        Esto es lo que permite:
          - las dos rutas de programacion de inyeccion (nueva y legacy)
            converger en la misma OP sin coordinarse entre si.
          - empaque reservar "la OP del dia" con el primer reporte, y que
            los siguientes reportes del mismo dia caigan en la misma OP
            sin que nadie programe nada de antemano.

        ambito: 'INYECCION' | 'ENSAMBLE' | 'EMPAQUE'
        fecha: date (o string ISO) -- fecha de produccion, no de registro.
        maquina: solo aplica a INYECCION; NULL en los otros dos ambitos.
        """
        if ambito not in AMBITO_A_CLAVE_PREFIJO:
            raise OpNumeradorException(f"Ambito desconocido: {ambito!r}")
        if isinstance(fecha, str):
            fecha = date_cls.fromisoformat(fecha[:10])

        maquina_norm = (str(maquina).strip().upper() if maquina else None)

        # Camino rapido: si ya existe una activa para esta clave, no hay
        # que pelear por el lock ni tocar el numerador para nada.
        existente = OpNumeradorService._buscar_activa(ambito, fecha, maquina_norm)
        if existente:
            return existente

        prefijo = OpNumeradorService._prefijo(ambito)

        # Lock transaction-scoped: se libera solo al terminar la
        # transaccion del LLAMADOR (commit o rollback), no aqui. Evita que
        # dos requests concurrentes calculen el mismo "siguiente" antes de
        # que ninguno haya insertado. hashtext() sobre un string fijo (no
        # por-prefijo, porque la serie es global) asegura que ambos peleen
        # por el MISMO lock.
        db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('op_numerador:global'))"))

        # Re-chequeo dentro del lock: otro request pudo haber creado la
        # fila mientras esperabamos el advisory lock.
        existente = OpNumeradorService._buscar_activa(ambito, fecha, maquina_norm)
        if existente:
            return existente

        ultimo_error = None
        for intento in range(MAX_REINTENTOS_COLISION):
            consecutivo = OpNumeradorService._siguiente_consecutivo()
            numero_op = f"{prefijo}-{consecutivo}"

            nueva = OpGenerada(
                prefijo=prefijo,
                consecutivo=consecutivo,
                numero_op=numero_op,
                ambito=ambito,
                maquina=maquina_norm,
                fecha_produccion=fecha,
                estado='RESERVADA',
                creado_por=usuario,
            )
            db.session.add(nueva)
            try:
                db.session.flush()
                return nueva
            except IntegrityError as e:
                db.session.rollback()
                # Re-adquirir el lock tras el rollback (el rollback lo libera).
                db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('op_numerador:global'))"))
                ultimo_error = e
                logger.warning(
                    f"[OpNumerador] Colision al reservar {numero_op!r} "
                    f"(intento {intento + 1}/{MAX_REINTENTOS_COLISION}). Recalculando."
                )

        raise OpNumeradorException(
            f"No se pudo reservar una OP para ({ambito}, {fecha}, {maquina_norm}) "
            f"tras {MAX_REINTENTOS_COLISION} intentos. Ultimo error: {ultimo_error}"
        )

    @staticmethod
    def _buscar_activa(ambito, fecha, maquina_norm):
        return db.session.query(OpGenerada).filter(
            OpGenerada.ambito == ambito,
            OpGenerada.fecha_produccion == fecha,
            db.func.coalesce(OpGenerada.maquina, '') == (maquina_norm or ''),
            OpGenerada.estado != 'ANULADA',
        ).first()

    @staticmethod
    def anular(numero_op, motivo, usuario=None):
        """Marca una OP como ANULADA. No la borra -- libera la clave (fecha,
        ambito, maquina) para que uq_op_generada_dia_ambito permita otra,
        pero conserva el numero_op en el historial para auditoria."""
        op = db.session.query(OpGenerada).filter(OpGenerada.numero_op == numero_op).first()
        if not op:
            raise OpNumeradorException(f"OP {numero_op!r} no existe")
        op.estado = 'ANULADA'
        op.anulada_motivo = f"{motivo} (por {usuario})" if usuario else motivo
        db.session.flush()
        return op

    @staticmethod
    def diagnostico():
        """
        Solo lectura: expone el estado del numerador sin reservar nada.
        Pensado para GET /api/wo/op/numerador/diagnostico y para verificar
        manualmente que piso_wo coincide con lo que se ve en WO.
        """
        piso_wo = OpNumeradorService._piso_wo()
        piso_local = OpNumeradorService._piso_local()
        piso = max(piso_wo, piso_local, 0)
        offset = OpNumeradorService._offset_seguridad()
        siguiente = piso + 1 + offset

        por_ambito = {}
        for ambito in AMBITO_A_CLAVE_PREFIJO:
            prefijo = OpNumeradorService._prefijo(ambito)
            por_ambito[ambito] = {
                'prefijo': prefijo,
                'siguiente_numero_op': f"{prefijo}-{siguiente}",
            }

        return {
            'piso_wo_staging': piso_wo,
            'piso_local_generadas': piso_local,
            'piso_efectivo': piso,
            'offset_seguridad': offset,
            'siguiente_consecutivo': siguiente,
            'por_ambito': por_ambito,
            'prefijos_excluidos_del_piso': list(PREFIJOS_EXCLUIDOS_DEL_PISO),
        }
