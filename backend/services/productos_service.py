# -*- coding: utf-8 -*-
"""
Servicio de negocio del dominio de productos. Primer archivo de este dominio:
antes de esto, sincronizar_precios_wo vivía completa (parseo de Excel, SQL y
todo) dentro de backend/routes/productos_routes.py, violando la regla de
capas del proyecto (nada de SQL ni lógica de negocio en *_routes.py).
"""
import io
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


class FormatoArchivoNoSoportadoError(ValueError):
    """El archivo subido no es .csv/.xlsx/.xls."""


class ColumnasNoEncontradasError(ValueError):
    """El archivo no tiene columnas reconocibles de código/precio."""

    def __init__(self, columnas_encontradas):
        self.columnas_encontradas = columnas_encontradas
        super().__init__(
            f'Columnas requeridas no encontradas. Se encontraron: {columnas_encontradas}. '
            'Se necesitan "Código" (o similar) y "Precio 1".'
        )


class ProductosService:
    """Namespace de métodos de negocio del dominio de productos (patrón
    clase + staticmethod, igual que PedidosService/EnsambleService)."""

    @staticmethod
    def sincronizar_precios_wo(nombre_archivo, contenido_bytes, db_session):
        """
        Fase 1 - Sincronización de Precios con World Office. Recibe el nombre
        y los bytes crudos de un archivo .csv/.xlsx exportado de WO, busca
        columnas 'Código' y 'Precio 1', y SOLO actualiza registros existentes
        en db_productos (sin insertar nuevos). Movido tal cual desde
        productos_routes.sincronizar_precios_wo -- mismo comportamiento
        fila-por-fila (commit/rollback individual, mismo shape de respuesta),
        solo cambia dónde vive.

        :raises FormatoArchivoNoSoportadoError: extensión no es .csv/.xlsx/.xls.
        :raises ColumnasNoEncontradasError: no se hallaron columnas código/precio.
        :return: dict con actualizados_count/omitidos_count/errores_count/
            detalles/detalles_sincronizacion (mismo shape que devolvía la ruta).
        """
        import pandas as pd
        from backend.utils.formatters import normalizar_codigo, preservar_o_normalizar_prefijo
        from backend.config.settings import Empresa

        nombre = (nombre_archivo or '').lower()

        if nombre.endswith('.csv'):
            try:
                df = pd.read_csv(io.BytesIO(contenido_bytes), sep=None, engine='python', dtype=str, encoding='utf-8-sig')
            except Exception:
                df = pd.read_csv(io.BytesIO(contenido_bytes), sep=None, engine='python', dtype=str, encoding='latin-1')
        elif nombre.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contenido_bytes), dtype=str)
        else:
            raise FormatoArchivoNoSoportadoError('Formato de archivo no soportado. Use .csv o .xlsx')

        df.columns = df.columns.astype(str).str.strip().str.lower() \
            .str.replace('ó', 'o') \
            .str.replace('á', 'a') \
            .str.replace('é', 'e') \
            .str.replace('í', 'i') \
            .str.replace('ú', 'u')

        # Buscar columnas flexiblemente
        col_codigo = None
        col_precio = None
        for col in df.columns:
            if 'codigo' in col or col == 'code' or col == 'referencia' or col == 'ref':
                col_codigo = col
            if 'precio 1' in col or 'precio1' in col or col == 'precio':
                col_precio = col

        if not col_codigo or not col_precio:
            raise ColumnasNoEncontradasError(list(df.columns))

        actualizados = 0
        omitidos = 0
        errores = 0
        detalles = []
        detalles_sincronizacion = {
            "exitosos": [],
            "no_encontrados": [],
            "errores": []
        }

        for _, row in df.iterrows():
            codigo_raw = ""
            try:
                codigo_raw = str(row[col_codigo] or '').strip()
                precio_raw = str(row[col_precio] or '').strip()

                if not codigo_raw or not precio_raw or codigo_raw.lower() in ('nan', 'none', ''):
                    omitidos += 1
                    detalles.append({
                        "codigo": codigo_raw or "(Vacío)",
                        "precio_archivo": precio_raw,
                        "status": "No encontrado en DB (código o precio inválido/vacío)"
                    })
                    detalles_sincronizacion["no_encontrados"].append(codigo_raw or "(Vacío)")
                    continue

                # Limpieza de Precios Ultra-Simple y directa (Fuerza float)
                try:
                    precio_final = float(precio_raw)
                except Exception:
                    omitidos += 1
                    detalles.append({
                        "codigo": codigo_raw,
                        "precio_archivo": precio_raw,
                        "status": "No encontrado en DB (formato de número inválido al castear)"
                    })
                    detalles_sincronizacion["no_encontrados"].append(codigo_raw)
                    continue

                # Normalizar códigos para búsqueda flexible. El 'FR-' se pide
                # EXPLÍCITAMENTE (opt-in) porque aquí solo se LEE db_productos,
                # donde la referencia FriParts histórica sí vive con prefijo; es
                # una variante más del WHERE, no una mutación de la referencia.
                codigo_sin_prefijo = normalizar_codigo(codigo_raw)
                codigo_con_prefijo = preservar_o_normalizar_prefijo(codigo_raw, Empresa.PREFIJO_PRODUCTO_PRINCIPAL)

                # Query de alta precisión contra codigo_sistema
                query = """
                    UPDATE db_productos
                    SET precio = :precio_archivo
                    WHERE LOWER(TRIM(codigo_sistema)) = LOWER(TRIM(:codigo_raw))
                       OR LOWER(TRIM(codigo_sistema)) = LOWER(TRIM(:codigo_con_prefijo))
                       OR LOWER(TRIM(codigo_sistema)) = LOWER(TRIM(:codigo_sin_prefijo))
                """
                bind_params = {
                    'precio_archivo': precio_final,
                    'codigo_raw': codigo_raw,
                    'codigo_con_prefijo': codigo_con_prefijo,
                    'codigo_sin_prefijo': codigo_sin_prefijo
                }

                # Transacción por fila (Rollback Obligatorio o Commit Inmediato)
                try:
                    result = db_session.execute(text(query), bind_params)
                    if result.rowcount > 0:
                        db_session.commit()
                        actualizados += 1
                        detalles.append({
                            "codigo": codigo_raw,
                            "precio_archivo": precio_final,
                            "status": "Actualizado"
                        })
                        detalles_sincronizacion["exitosos"].append(codigo_raw)
                    else:
                        db_session.rollback()
                        omitidos += 1
                        detalles.append({
                            "codigo": codigo_raw,
                            "precio_archivo": precio_final,
                            "status": "No encontrado en DB (0 filas afectadas)"
                        })
                        detalles_sincronizacion["no_encontrados"].append(codigo_raw)
                except Exception as e_sql:
                    db_session.rollback()
                    errores += 1
                    detalles.append({
                        "codigo": codigo_raw,
                        "precio_archivo": precio_final,
                        "status": "Error",
                        "motivo": str(e_sql)
                    })
                    detalles_sincronizacion["errores"].append({
                        "codigo": codigo_raw,
                        "motivo": str(e_sql)
                    })
                    logger.warning(f"⚠️ [SincronizarPrecios] Error ejecutando SQL para código ({codigo_raw}): {e_sql}")
                    continue

            except Exception as e_row:
                errores += 1
                detalles.append({
                    "codigo": codigo_raw or "Desconocido",
                    "status": "Error",
                    "motivo": str(e_row)
                })
                detalles_sincronizacion["errores"].append({
                    "codigo": codigo_raw or "Desconocido",
                    "motivo": str(e_row)
                })
                logger.warning(f"⚠️ [SincronizarPrecios] Error general en fila ({codigo_raw}): {e_row}")
                continue

        logger.info(f"✅ [SincronizarPrecios] Completado: {actualizados} actualizados, {omitidos} no encontrados, {errores} errores.")
        return {
            'actualizados_count': actualizados,
            'omitidos_count': omitidos,
            'errores_count': errores,
            'detalles': detalles,
            'detalles_sincronizacion': detalles_sincronizacion,
            'exitosos': detalles_sincronizacion["exitosos"],
            'no_encontrados': detalles_sincronizacion["no_encontrados"],
            'errores': detalles_sincronizacion["errores"],
            'mensaje': f'Sincronización exitosa: {actualizados} precios actualizados.'
        }
