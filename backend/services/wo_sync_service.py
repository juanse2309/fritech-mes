# -*- coding: utf-8 -*-
"""
wo_sync_service.py — Capa de servicio/repositorio para la integración con
World Office (ERP). Concentra TODO el SQL crudo, parseo de payloads JSON y
manejo transaccional que antes vivía directamente en
backend/routes/wo_routes.py (regla FRITECH V4.5: cero SQL/lógica de negocio
en rutas). Los endpoints de wo_routes.py son thin controllers: extraen la
petición (headers/params), llaman a un método de WoSyncService y traducen el
resultado o la excepción de negocio a jsonify + status code.
"""
import os
import logging
import unicodedata
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.core.sql_database import db
from backend.models.sql_models import RawVentas, OperacionLog

logger = logging.getLogger(__name__)


# ============================================================================
# EXCEPCIONES DE NEGOCIO
# ============================================================================

class WoSyncError(Exception):
    """Excepción base de negocio del sincronizador de World Office."""


class SincronizacionVaciaError(WoSyncError):
    """El payload recibido de WO no trae datos utilizables (vacío o todo en cero)."""


class CaidaAnomalaError(WoSyncError):
    """El volumen recibido cae por debajo del umbral de seguridad frente al volumen actual en BD."""


class WoSyncConfigError(WoSyncError):
    """Falta configuración necesaria para conectar con World Office (driver, credenciales)."""


class WoSyncPersistenciaError(WoSyncError):
    """Fallo de base de datos (integridad/conexión) durante una operación de sincronización."""


class WoSyncService:
    """Operaciones de sincronización con World Office (ERP)."""

    INVENTARIO_BATCH_SIZE = 500
    VENTAS_BATCH_SIZE = 500
    # Umbral del circuit breaker de ingesta comercial: si el volumen total
    # recibido en la sincronización cae por debajo de este porcentaje del
    # volumen actual en db_ventas, se aborta el volcado a la tabla principal
    # (mismo patrón que VentasRepository.upsert_cartera_wo / ClienteRepository
    # para cartera_wo y db_clientes).
    UMBRAL_CAIDA_ANOMALA_VENTAS = 0.5

    # ------------------------------------------------------------------
    # Utilidades internas de normalización
    # ------------------------------------------------------------------

    @staticmethod
    def _normalizar_llaves(d):
        """Normaliza las llaves de un dict: minúsculas, sin acentos, espacios -> _"""
        if not isinstance(d, dict):
            return d

        def limpiar_texto(s):
            s = s.lower().strip()
            s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
            s = s.replace(' ', '_')
            return s

        return {limpiar_texto(k): v for k, v in d.items()}

    @staticmethod
    def _parse_date(date_val):
        if not date_val:
            return None
        if isinstance(date_val, datetime):
            return date_val.date()
        try:
            # Intentar formato ISO 'YYYY-MM-DD'
            return datetime.fromisoformat(str(date_val).replace('Z', '')).date()
        except ValueError:
            try:
                return datetime.strptime(str(date_val)[:10], '%Y-%m-%d').date()
            except ValueError:
                return None

    @staticmethod
    def refrescar_mv_dashboard_ventas():
        """
        Refresca en cascada las vistas materializadas derivadas de db_ventas que
        alimentan el panel de Jefatura tras una sincronización exitosa de World Office:
          - mv_dashboard_ventas_analitica (Backorder/Top/Peores Productos)
          - mv_rendimiento_mensual (comparativo mensual Ventas vs Pedidos)
        Ver backend/sql/create_mv_dashboard_ventas_analitica.sql y
        backend/sql/create_mv_rendimiento_mensual.sql.

        REFRESH ... CONCURRENTLY no puede correr dentro de una transacción, así que se
        abre una conexión aparte en autocommit. Cada vista se refresca de forma
        independiente: si una falla, la otra igual se intenta. Un fallo aquí se loggea
        pero no debe tumbar la respuesta de sync: los datos de db_ventas ya se
        confirmaron con éxito, y el dashboard simplemente seguirá sirviendo el
        snapshot anterior de la vista que falló hasta el próximo refresh exitoso.
        """
        vistas = ["mv_dashboard_ventas_analitica", "mv_rendimiento_mensual"]
        try:
            conn = db.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            try:
                for vista in vistas:
                    try:
                        conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {vista}"))
                        logger.debug(f"✅ {vista} refrescada tras sincronización de WO.")
                    except Exception as e_vista:
                        logger.error(f"⚠️ Fallo al refrescar {vista} tras sync de WO: {e_vista}")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"⚠️ Fallo al abrir conexión para refrescar vistas materializadas tras sync de WO: {e}")

    # ------------------------------------------------------------------
    # /api/wo/recibir_datos (Inventario WO)
    # ------------------------------------------------------------------

    @staticmethod
    def recibir_datos(payload):
        """
        Punto de entrada de negocio para /api/wo/recibir_datos. Parsea el
        payload del agente local; si corresponde a una vista de inventario,
        persiste en inventario_wo (ver _procesar_inventario). Cualquier otra
        vista solo se confirma como recibida, sin tocar la base de datos.

        :raises SincronizacionVaciaError: si todos los registros de inventario
            recibidos reportan stock 0 (circuit breaker anti-borrado accidental).
        :raises WoSyncPersistenciaError: fallo de base de datos al persistir.
        :return: dict listo para jsonify.
        """
        if isinstance(payload, dict):
            nombre_vista = payload.get("nombre_vista", "Desconocida")
            datos = payload.get("datos", [])
        else:
            nombre_vista = "Desconocida (Lista directa)"
            datos = payload

        if not isinstance(datos, list):
            datos = [datos] if datos else []

        logger.info(f"Datos de WO recibidos ({nombre_vista}): {len(datos)} registros")

        if nombre_vista in ("Vista_Tabla_Inventarios", "Vista_Existencias"):
            WoSyncService._procesar_inventario(datos)

        return {
            "success": True,
            "message": f"Datos de la vista '{nombre_vista}' recibidos con éxito",
            "recibidos": len(datos),
            "nombre_vista": nombre_vista
        }

    @staticmethod
    def _procesar_inventario(datos):
        """UPSERT atómico (DELETE + INSERT por lotes) de inventario_wo."""
        datos_normalizados = [WoSyncService._normalizar_llaves(item) for item in datos]

        logger.debug(f"[DEBUG] Recibidos {len(datos_normalizados)} registros para insertar.")
        if datos_normalizados:
            logger.debug(f"[DEBUG] Llaves del primer registro: {list(datos_normalizados[0].keys())}")
            logger.debug("[DEBUG NUBE] Muestra primeros 5 registros recibidos:")
            for i, r in enumerate(datos_normalizados[:5]):
                cod = r.get('codigo_producto') or r.get('codigo') or '(sin_cod)'
                stk = r.get('stock_wo') or r.get('existencia') or r.get('stock') or 0
                logger.debug(f"  [{i}] Ref: {cod} | Stock: {stk}")

        # PRE-AUDITORÍA DE STOCK: circuit breaker si TODOS los registros vienen con stock 0
        all_zeros = True
        for r in datos_normalizados:
            stock_raw = (r.get('stock_wo') or r.get('stock') or r.get('cantidad') or
                         r.get('saldo') or r.get('saldos') or r.get('existencia') or 0)
            try:
                if float(stock_raw) > 0:
                    all_zeros = False
                    break
            except (ValueError, TypeError):
                pass

        if all_zeros and len(datos_normalizados) > 0:
            logger.critical("❌ [CRÍTICO] El archivo/reporte de WO reporta stock 0.00 para TODOS los items. "
                             "Se aborta la sincronización para evitar borrar el inventario real.")
            raise SincronizacionVaciaError(
                "El reporte de World Office viene vacío o todos los saldos son 0. "
                "Verifique las columnas del reporte exportado."
            )

        try:
            logger.debug("[AUDITORIA] Estrategia de UPSERT Atómica iniciada. No se usará TRUNCATE.")
            rows_insertados = 0

            for i in range(0, len(datos_normalizados), WoSyncService.INVENTARIO_BATCH_SIZE):
                batch = datos_normalizados[i:i + WoSyncService.INVENTARIO_BATCH_SIZE]
                values_parts = []
                params = {}
                ids_lote = []

                for j, r in enumerate(batch):
                    codigo_producto = str(
                        r.get('codigo_producto') or r.get('codigo') or
                        r.get('codigo_alterno') or ""
                    ).strip()
                    if not codigo_producto:
                        continue

                    ids_lote.append(codigo_producto)

                    descripcion    = str(r.get('descripcion') or r.get('nombre') or "").strip()[:500]
                    codigo_alterno = str(r.get('codigo_alterno') or "").strip()[:100]
                    referencia     = str(r.get('referencia') or r.get('ref') or "").strip()[:100]

                    # Mapeo Explícito y Constante de Columna
                    columna_stock_leida = 'stock_wo' if 'stock_wo' in r else 'existencia' if 'existencia' in r else 'stock'
                    stock_raw = r.get(columna_stock_leida)

                    if j == 0 and i == 0:
                        logger.debug(f"[AUDITORIA EXPLICITA] Leyendo valor de stock desde la clave: '{columna_stock_leida}'")
                    if j < 5 and i == 0:
                        logger.debug(f"[CARGA WO] Procesando Ref: {codigo_producto} | Valor Detectado: {stock_raw}")

                    if stock_raw is None or str(stock_raw).strip() == "":
                        stock_wo = None  # Se preservará como NULL para no sobreescribir con 0
                    else:
                        try:
                            stock_wo = float(stock_raw)
                        except (ValueError, TypeError):
                            stock_wo = None

                    try:
                        precio_wo = float(r.get('precio_wo') or r.get('precio') or r.get('precio_venta') or 0)
                    except (ValueError, TypeError):
                        precio_wo = 0.0

                    k = f"r{i}_{j}"
                    params[f"cod_{k}"]    = codigo_producto
                    params[f"desc_{k}"]   = descripcion
                    params[f"stock_{k}"]  = stock_wo
                    params[f"precio_{k}"] = precio_wo
                    params[f"alt_{k}"]    = codigo_alterno
                    params[f"ref_{k}"]    = referencia
                    values_parts.append(
                        f"(:cod_{k}, :desc_{k}, :stock_{k}, :precio_{k}, :alt_{k}, :ref_{k}, NOW())"
                    )

                if values_parts and ids_lote:
                    # DELETE atómico de los IDs que vamos a actualizar (evita duplicados sin vaciar la tabla)
                    ids_placeholders = [f":del_{idx}" for idx in range(len(ids_lote))]
                    del_params = {f"del_{idx}": id_val for idx, id_val in enumerate(ids_lote)}
                    sql_del = text(f"DELETE FROM inventario_wo WHERE codigo_producto IN ({', '.join(ids_placeholders)})")
                    db.session.execute(sql_del, del_params)

                if values_parts:
                    sql = text(
                        "INSERT INTO inventario_wo "
                        "(codigo_producto, descripcion, stock_wo, precio_wo, codigo_alterno, referencia, fecha_sincronizacion) "
                        f"VALUES {', '.join(values_parts)}"
                    )
                    db.session.execute(sql, params)
                    rows_insertados += len(values_parts)

            db.session.commit()

            count_res = db.session.execute(text("SELECT COUNT(*) FROM inventario_wo")).scalar()
            logger.debug(f"[DEBUG] Registros guardados en inventario_wo tras INSERT: {count_res}")
            logger.info(f"✅ INSERT masivo completado: {rows_insertados} filas insertadas en inventario_wo.")

        except SQLAlchemyError as e_sql:
            db.session.rollback()
            logger.error(f"❌ Error de base de datos en INSERT masivo de inventario_wo: {e_sql}")
            raise WoSyncPersistenciaError(str(e_sql)) from e_sql

    # ------------------------------------------------------------------
    # /api/wo/recibir_comercial (Ventas/Pedidos/Devoluciones)
    # ------------------------------------------------------------------

    @staticmethod
    def _mapear_fila_comercial(item):
        """Mapea y sanitiza una fila cruda de WO al esquema de db_ventas_staging."""
        clasif = item.get('clasificacion')
        if clasif not in ('pedido', 'venta', 'devolucion'):
            # Fallback por si acaso el mapeo falló o viene directo del agente
            prefijo = item.get('prefijo_doc') or ''
            if prefijo == 'FV':
                clasif = 'venta'
            elif prefijo == 'PD':
                clasif = 'pedido'
            elif prefijo == 'NC':
                clasif = 'devolucion'
            else:
                clasif = 'desconocido'

        try:
            cantidad = float(item.get('cantidad') or 0)
        except (ValueError, TypeError):
            cantidad = 0.0

        try:
            total_ingresos = float(item.get('total_ingresos') or 0)
        except (ValueError, TypeError):
            total_ingresos = 0.0

        try:
            precio_promedio = float(item.get('precio_promedio') or 0)
        except (ValueError, TypeError):
            precio_promedio = 0.0

        # Detalle de factura WO: nullable, el agente local los envía en None
        # cuando no logró detectar la columna real en WO -- no se fuerza un
        # valor por defecto.
        iva = item.get('iva')
        try:
            iva = float(iva) if iva is not None else None
        except (ValueError, TypeError):
            iva = None

        return {
            'fecha': WoSyncService._parse_date(item.get('fecha')),
            'documento': str(item.get('documento') or '').strip()[:80],
            'nombres': str(item.get('nombres') or '').strip()[:200],
            'productos': str(item.get('productos') or '').strip()[:100],
            'cantidad': cantidad,
            'total_ingresos': total_ingresos,
            'precio_promedio': precio_promedio,
            'clasificacion': clasif,
            'vendedor': str(item.get('vendedor') or '').strip()[:150],
            'zona': str(item.get('zona') or '').strip()[:100],
            'descripcion_producto': (str(item.get('descripcion') or '').strip()[:255] or None),
            'iva': iva,
            'identificacion_cliente': (str(item.get('nit_cliente') or '').strip()[:50] or None),
        }

    @staticmethod
    def recibir_comercial(payload):
        """
        Procesa un chunk de sincronización comercial (ventas/pedidos/
        devoluciones) desde World Office. Inserta el chunk en la tabla
        staging; en el último chunk, aplica el circuit breaker de volumen y,
        si pasa, vuelca staging -> producción en una transacción atómica.

        :raises SincronizacionVaciaError: volumen total recibido == 0 (al
            cierre del último chunk).
        :raises CaidaAnomalaError: volumen total recibido por debajo del
            umbral de seguridad frente al volumen actual en db_ventas.
        :raises WoSyncPersistenciaError: fallo de base de datos.
        :return: dict con el resumen de la operación (lote_actual, total_lotes, estado...).
        """
        is_chunk = bool(payload.get("is_chunk", False)) if isinstance(payload, dict) else False

        if is_chunk:
            index = payload.get("index", 0)
            total_chunks = payload.get("total_chunks", 1)
            datos = payload.get("data", [])
            logger.debug(f"Recibiendo lote {index + 1}/{total_chunks} de datos comerciales de WO: {len(datos)} registros")
        else:
            index = 0
            total_chunks = 1
            datos = payload.get("datos", []) if isinstance(payload, dict) else payload
            if not isinstance(datos, list):
                datos = [datos] if datos else []
            logger.info(f"Datos comerciales de WO recibidos (sin chunking): {len(datos)} registros")

        mappings_chunk = [WoSyncService._mapear_fila_comercial(item) for item in datos]

        try:
            # Paso 1: si es el primer lote, inicializamos la tabla Staging
            if index == 0:
                logger.debug("Lote inicial (index 0). Preparando tabla Staging 'db_ventas_staging'...")
                db.session.execute(text("TRUNCATE db_ventas_staging"))
                db.session.commit()

            # Paso 2: persistir el chunk actual en Staging
            if mappings_chunk:
                sql_insert = text("""
                    INSERT INTO db_ventas_staging
                    (fecha, documento, nombres, productos, cantidad, total_ingresos, precio_promedio, clasificacion, vendedor, zona, descripcion_producto, iva, identificacion_cliente)
                    VALUES (:fecha, :documento, :nombres, :productos, :cantidad, :total_ingresos, :precio_promedio, :clasificacion, :vendedor, :zona, :descripcion_producto, :iva, :identificacion_cliente)
                """)
                db.session.execute(sql_insert, mappings_chunk)
                db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"❌ Error de base de datos insertando chunk comercial en staging: {e}")
            raise WoSyncPersistenciaError(str(e)) from e

        # Paso 3: en el último chunk, circuit breaker + volcado atómico a producción
        estado = "En progreso (Staging)"
        if index == total_chunks - 1:
            estado = WoSyncService._volcar_staging_a_produccion()

        return {
            "total_insertados_chunk": len(mappings_chunk),
            "lote_actual": index + 1,
            "total_lotes": total_chunks,
            "estado": estado,
        }

    @staticmethod
    def _volcar_staging_a_produccion():
        """
        Circuit Breaker de ingesta comercial + volcado atómico staging ->
        db_ventas. Se ejecuta únicamente en el último chunk de una
        sincronización comercial.

        Si el volumen total recibido (contado en db_ventas_staging, que ya
        acumula todos los chunks de esta sincronización) es 0, o cae por
        debajo de UMBRAL_CAIDA_ANOMALA_VENTAS respecto al volumen actual en
        db_ventas, se ABORTA sin ejecutar el TRUNCATE/INSERT sobre la tabla
        principal y se lanza un error de negocio explícito -- db_ventas
        queda intacta con su snapshot anterior.
        """
        logger.debug("Último lote recibido. Evaluando circuit breaker antes del volcado a producción...")

        total_recibidos = db.session.execute(text("SELECT COUNT(*) FROM db_ventas_staging")).scalar() or 0
        count_actual = db.session.execute(text("SELECT COUNT(*) FROM db_ventas")).scalar() or 0

        if total_recibidos == 0:
            logger.critical(
                "❌ [CRÍTICO] La sincronización comercial de WO no trajo ningún registro "
                "utilizable. Se aborta el volcado a db_ventas para no vaciar la tabla."
            )
            raise SincronizacionVaciaError(
                "La sincronización comercial no trajo registros. Se aborta el volcado a db_ventas."
            )

        if count_actual > 0 and total_recibidos < count_actual * WoSyncService.UMBRAL_CAIDA_ANOMALA_VENTAS:
            logger.critical(
                f"❌ [CRÍTICO] Caída anómala en la sincronización comercial: "
                f"recibidos={total_recibidos} vs actuales={count_actual} "
                f"(umbral {WoSyncService.UMBRAL_CAIDA_ANOMALA_VENTAS:.0%}). Se aborta el volcado a db_ventas."
            )
            raise CaidaAnomalaError(
                f"Caída anómala: se recibieron {total_recibidos} registros frente a {count_actual} "
                "existentes en db_ventas. Verifique la extracción en el agente local."
            )

        try:
            logger.debug("Circuit breaker superado. Iniciando Volcado Atómico desde Staging a db_ventas...")
            db.session.execute(text("TRUNCATE db_ventas"))
            db.session.execute(text("""
                INSERT INTO db_ventas (fecha, documento, nombres, productos, cantidad, total_ingresos, precio_promedio, clasificacion, vendedor, zona, descripcion_producto, iva, identificacion_cliente)
                SELECT fecha, documento, nombres, productos, cantidad, total_ingresos, precio_promedio, clasificacion, vendedor, zona, descripcion_producto, iva, identificacion_cliente
                FROM db_ventas_staging
            """))
            db.session.commit()
            logger.info("✅ Sincronización comercial completada con volcado atómico desde Staging Table.")
            WoSyncService.refrescar_mv_dashboard_ventas()
            return "Completado y Volcado a Producción"
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"❌ Error de base de datos en volcado de staging a producción: {e}")
            raise WoSyncPersistenciaError(str(e)) from e

    # ------------------------------------------------------------------
    # /api/wo/sincronizar_automatica (extracción directa pyodbc)
    # ------------------------------------------------------------------

    @staticmethod
    def sincronizar_automatica():
        """
        Extrae ventas/pedidos/devoluciones directamente desde SQL Server (WO)
        vía pyodbc y las vuelca en db_ventas (DELETE total + bulk insert en
        una única transacción). Registra inicio/fin/error en OperacionLog.

        :raises WoSyncConfigError: pyodbc no instalado o WO_PASSWORD ausente.
        :raises WoSyncPersistenciaError: fallo de conexión/consulta a WO o de
            persistencia en Postgres.
        :return: dict con 'registros' (total insertado en db_ventas).
        """
        try:
            import pyodbc
        except ImportError:
            raise WoSyncConfigError("pyodbc no está disponible en este servidor")

        db_server   = os.getenv("WO_SERVER",   r"SERVERWO\WORLDOFFICE17")
        db_database = os.getenv("WO_DB",       "FRIPARTS2021")
        db_uid      = os.getenv("WO_USER",     "wo_cliente")
        db_pwd      = os.getenv("WO_PASSWORD")
        if not db_pwd:
            raise WoSyncConfigError("WO_PASSWORD no está configurada en el servidor")

        conn_str = (
            f"DRIVER={{SQL Server}};"
            f"SERVER={db_server};"
            f"DATABASE={db_database};"
            f"UID={db_uid};"
            f"PWD={db_pwd};"
            "Timeout=30;"
        )

        db.session.add(OperacionLog(
            fecha=datetime.now(),
            modulo="SincronizacionComercial",
            operario="Sistema (Auto)",
            accion="Inicio Sincronización Automática",
            detalles="Iniciando conexión local a World Office..."
        ))
        db.session.commit()

        conn = None
        try:
            conn = pyodbc.connect(conn_str, timeout=15)
            cursor = conn.cursor()

            # Catálogo maestro en memoria (Autonumerico -> Codigo_Producto)
            cursor.execute(f"SELECT Autonumerico, Codigo_Producto FROM [{db_database}].[dbo].[Vista_Tabla_Inventarios]")
            mapping = {str(row[0]): row[1] for row in cursor.fetchall()}

            sql = f"""
            SELECT
                E.Fecha AS fecha,
                (E.prefijo + '-' + CAST(E.Numero_de_Documento AS VARCHAR)) AS documento,
                E.Nombre_tercero_externo AS nombres,
                E.Nombres_tercero_interno AS vendedor,
                E.Ciudad_Encabezado AS zona,
                D.Producto AS productos,
                CAST(D.Cantidad AS FLOAT) AS cantidad,
                CAST((D.Cantidad * D.Valor_Unitario * (1 - (D.Descuento/100.0))) AS FLOAT) AS total_ingresos,
                CAST(D.Valor_Unitario AS FLOAT) AS precio_promedio,
                E.Tipo_de_Documento AS tipo_doc
            FROM [{db_database}].[dbo].[Vista_Tabla_Encabezados] E
            INNER JOIN [{db_database}].[dbo].[Vista_Tabla_Movimientos_Inventario] D
                ON E.Autonumerico = D.Pertenece_A
            WHERE YEAR(E.Fecha) >= YEAR(GETDATE()) - 1
              AND E.Tipo_de_Documento IN ('FV', 'PED', 'COT', 'NC', 'NCV', 'NCCL', 'DMC')
              AND E.Anulado = 0;
            """
            cursor.execute(sql)
            columnas = [column[0] for column in cursor.description]

            datos_mapeados = []
            cant_pd = 0
            cant_fv = 0

            for row in cursor.fetchall():
                item = dict(zip(columnas, row))

                tipo_doc = item.get('tipo_doc', '').strip()
                if tipo_doc == 'PED':
                    clasif = 'pedido'
                    cant_pd += 1
                elif tipo_doc in ('FV', 'COT', 'NC', 'NCV', 'NCCL', 'DMC'):
                    clasif = 'venta'
                    if tipo_doc == 'FV':
                        cant_fv += 1
                    # Ajuste matemático para devoluciones/notas crédito/devolución de mercancía
                    if tipo_doc in ('NC', 'NCV', 'NCCL', 'DMC'):
                        item['total_ingresos'] = float(item.get('total_ingresos', 0) or 0) * -1
                else:
                    clasif = 'desconocido'

                prod_id = str(item.get('productos', '')).strip()
                mapped_prod = mapping.get(prod_id, prod_id)
                mapped_prod_str = str(mapped_prod or '').strip()

                datos_mapeados.append({
                    'fecha': item.get('fecha'),
                    'documento': str(item.get('documento') or '').strip()[:80],
                    'nombres': str(item.get('nombres') or '').strip()[:200],
                    'productos': mapped_prod_str[:100],
                    'cantidad': float(item.get('cantidad') or 0.0),
                    'total_ingresos': float(item.get('total_ingresos') or 0.0),
                    'precio_promedio': float(item.get('precio_promedio') or 0.0),
                    'clasificacion': clasif,
                    'vendedor': str(item.get('vendedor') or '').strip()[:150],
                    'zona': str(item.get('zona') or '').strip()[:100]
                })

            conn.close()
            conn = None

            # Borrado total de db_ventas e inserción masiva en transacción atómica
            db.session.query(RawVentas).delete(synchronize_session=False)
            if datos_mapeados:
                db.session.bulk_insert_mappings(RawVentas, datos_mapeados)

            db.session.add(OperacionLog(
                fecha=datetime.now(),
                modulo="SincronizacionComercial",
                operario="Sistema (Auto)",
                accion="Fin Sincronización Automática",
                detalles=f"Exito. Procesados: {len(datos_mapeados)} (Ventas: {cant_fv}, Pedidos: {cant_pd})"
            ))
            db.session.commit()
            WoSyncService.refrescar_mv_dashboard_ventas()

            return {"registros": len(datos_mapeados)}

        except Exception as e:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            db.session.rollback()

            db.session.add(OperacionLog(
                fecha=datetime.now(),
                modulo="SincronizacionComercial",
                operario="Sistema (Auto)",
                accion="Error Sincronización Automática",
                detalles=f"Falla crítica: {str(e)}"
            ))
            db.session.commit()

            logger.error(f"❌ Error en WoSyncService.sincronizar_automatica: {e}")
            raise WoSyncPersistenciaError(str(e)) from e
