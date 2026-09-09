"""
Repositorio de clientes 100% SQL-First.
Centraliza el acceso a la tabla db_clientes en PostgreSQL.
"""
import logging
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from backend.core.sql_database import db, rollback_seguro
from backend.models.sql_models import DbClientes

logger = logging.getLogger(__name__)

BATCH_SIZE_UPSERT_CLIENTES = 500


class ClienteRepository:
    """Repositorio para operaciones de lectura y sincronizacion sobre clientes."""

    @staticmethod
    def get_all():
        """Retorna todos los clientes desde SQL con llaves en minúscula para el frontend."""
        try:
            rows = db.session.execute(text('SELECT * FROM db_clientes')).mappings().all()

            def _get(row, candidates):
                for c in candidates:
                    if c in row and row[c]:
                        return str(row[c]).strip()
                return ''

            result = []
            for row in rows:
                nombre = _get(row, ['nombre', 'cliente', 'razon_social', 'nombre_empresa'])
                if not nombre:
                    continue
                result.append({
                    'nombre':    nombre,
                    'nit':       _get(row, ['nit', 'identificacion', 'nit_empresa']),
                    'direccion': _get(row, ['direccion']),
                    'ciudad':    _get(row, ['ciudad']),
                    'telefono':  _get(row, ['telefonos', 'telefono', 'celular']),
                    'email':     _get(row, ['email', 'correo', 'e_mail']),
                })
            logger.info(f"[ClienteRepository.get_all] {len(result)} clientes cargados con mapeo correcto.")
            return result
        except Exception as e:
            rollback_seguro()
            logger.error(f"[ClienteRepository.get_all] {e}")
            return []

    @staticmethod
    def upsert_clientes_wo(lote_clientes):
        """
        UPSERT masivo de direcciones/sucursales de clientes sincronizadas desde
        World Office (Vista_Tabla_Direcciones).

        Llave de conflicto: id_direccion_wo (IdTerceroDireccion de WO),
        protegida por constraint UNIQUE en el modelo. Un mismo NIT
        (identificacion) puede repetirse en varias filas: cada fila es una
        sede/direccion distinta del mismo tercero (confirmado contra WO,
        ej. NIT 830008309 tiene sede en Bogota y en Funza) — NO se deduplica
        por NIT.

        lote_clientes: lista de dicts con id_direccion_wo/identificacion/
        nombre/direccion/telefonos/ciudad. Se procesa en sub-lotes de
        BATCH_SIZE_UPSERT_CLIENTES dentro de una unica transaccion atomica.
        """
        if not lote_clientes:
            return 0

        registros = [
            r for r in lote_clientes
            if r.get('id_direccion_wo') is not None and str(r.get('identificacion') or '').strip()
        ]
        if not registros:
            return 0

        try:
            procesados = 0
            for i in range(0, len(registros), BATCH_SIZE_UPSERT_CLIENTES):
                batch = registros[i:i + BATCH_SIZE_UPSERT_CLIENTES]

                stmt = pg_insert(DbClientes).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['id_direccion_wo'],
                    set_={
                        'identificacion': stmt.excluded.identificacion,
                        'nombre':         stmt.excluded.nombre,
                        'direccion':      stmt.excluded.direccion,
                        'telefonos':      stmt.excluded.telefonos,
                        'ciudad':         stmt.excluded.ciudad,
                    }
                )
                db.session.execute(stmt)
                procesados += len(batch)

            db.session.commit()
            logger.info(f"[ClienteRepository.upsert_clientes_wo] UPSERT completado: {procesados} direcciones de cliente procesadas.")
            return procesados
        except Exception as e:
            rollback_seguro()
            logger.error(f"[ClienteRepository.upsert_clientes_wo] Error en el UPSERT: {e}")
            raise e

    @staticmethod
    def contar() -> int:
        """Total de filas en db_clientes -- usado por el circuit breaker de
        sincronizar_clientes (wo_routes.py) para detectar una caída anómala."""
        return db.session.execute(text("SELECT COUNT(*) FROM db_clientes")).scalar() or 0
