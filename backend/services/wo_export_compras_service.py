"""
wo_export_compras_service.py
=============================
Genera el archivo plano de Órdenes de Compra (proveedores externos) para
subir a World Office (plan 2026-09-15). Servicio aparte y no una
extensión de WoExportService (OP) a propósito: son tipos de documento
distintos en WO, con terceros/campos fijos diferentes -- mismo criterio
que ya separó WoExportService de FacturacionService.procesar_datos_wo.

Deshabilitado por defecto (ver esta_habilitado): ningún valor de
wo_templates_compras.FIJOS_OC está confirmado contra WO todavía.
"""
import logging
import os
import tempfile
from datetime import datetime

import pandas as pd

from backend.config.wo_templates_compras import (
    COLUMNAS_OC, FIJOS_OC, DELIMITADOR_TXT, ENCODING_TXT, FORMATO_DEFECTO, nota_oc,
)
from backend.core.sql_database import db
from backend.models.sql_models import AppConfig, OrdenCompraProveedor, LineaOrdenCompra

logger = logging.getLogger(__name__)


class WoExportComprasException(Exception):
    """Error de negocio de la exportación (OC sin líneas, no encontrada...)."""


class ExportacionDeshabilitadaException(Exception):
    """El flag wo_export.compras_habilitado está apagado -- kill-switch
    manual por si algo falla con el importador de WO más adelante. Activo
    por defecto: los valores de la plantilla ya se confirmaron contra una
    carga de prueba real (2026-09-15), no hace falta un paso extra para
    empezar a usarlo."""


class WoExportComprasService:

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------
    @staticmethod
    def _config(clave, defecto=None):
        fila = db.session.get(AppConfig, clave)
        if fila and fila.valor not in (None, ''):
            return fila.valor
        return defecto

    @staticmethod
    def esta_habilitado():
        return str(WoExportComprasService._config('wo_export.compras_habilitado', 'true')).lower() in ('true', '1', 'si', 'sí')

    @staticmethod
    def fijar_habilitado(activo):
        """Prende/apaga el guard. Los valores de FIJOS_OC (wo_templates_compras.py)
        ya se confirmaron contra una carga de prueba real en el importador
        de WO (2026-09-15) -- esto solo persiste la decisión de negocio de
        activarlo, no reemplaza esa verificación."""
        try:
            fila = db.session.get(AppConfig, 'wo_export.compras_habilitado')
            valor = 'true' if activo else 'false'
            if fila:
                fila.valor = valor
            else:
                db.session.add(AppConfig(clave='wo_export.compras_habilitado', valor=valor))
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    # ------------------------------------------------------------------
    # Construcción del dataset
    # ------------------------------------------------------------------
    @staticmethod
    def _fila_encabezado(orden):
        fila = {col: FIJOS_OC.get(col, '') for col in COLUMNAS_OC}
        fila.update({
            # El número PURO (297, 298...), no 'OC-297' -- confirmado
            # contra un documento real de WO: 'Numero_de_Documento' es
            # numérico, el prefijo 'OC-' es una convención solo interna de
            # FRITECH para mostrarlo/enlazarlo, WO no lo espera aquí.
            'Encab: Documento Número': orden.consecutivo,
            'Encab: Fecha': orden.fecha_oc,
            # Tercero Externo = el proveedor real de esta OC (a diferencia de
            # OP, donde el tercero externo siempre es FriParts) -- aquí SÍ
            # hay un tercero distinto de la empresa en cada documento.
            'Encab: Tercero Externo': orden.proveedor_nit,
            'Encab: Nota': nota_oc(orden.numero_oc, orden.fecha_oc),
        })
        return fila

    @staticmethod
    def construir_dataset(numeros_oc):
        """Un archivo puede agrupar varias OC (mismo patrón Encab/Detalle
        que OP): cada OC aporta su propio encabezado repetido por línea."""
        if not numeros_oc:
            raise WoExportComprasException("Debes indicar al menos una OC")

        ordenes = OrdenCompraProveedor.query.filter(
            OrdenCompraProveedor.numero_oc.in_(numeros_oc),
            OrdenCompraProveedor.estado != 'ANULADA',
        ).all()
        if not ordenes:
            raise WoExportComprasException("Ninguna de las OC seleccionadas existe o no está anulada")

        filas = []
        meta = {'ordenes': [], 'total_lineas': 0, 'sin_lineas': []}
        lineas_sin_precio = []

        for orden in ordenes:
            lineas = LineaOrdenCompra.query.filter_by(id_oc=orden.id).all()
            if not lineas:
                meta['sin_lineas'].append(orden.numero_oc)
                continue

            encabezado = WoExportComprasService._fila_encabezado(orden)
            for linea in lineas:
                # El precio es opcional al crear la línea (Albeiro solicita
                # sin cantidades/precio, y Diego a veces arma la OC antes de
                # cerrar precio con el proveedor) -- pero exportar a WO es
                # un documento de compra real, así que aquí SÍ es
                # obligatorio: no debe salir un valor unitario en $0 sin
                # que nadie se dé cuenta.
                if not linea.valor_unitario:
                    lineas_sin_precio.append(f"{orden.numero_oc}: {linea.descripcion}")
                    continue

                fila = dict(encabezado)
                fila.update({
                    'Detalle: Producto': linea.codigo_producto or linea.descripcion,
                    'Detalle: UnidadDeMedida': linea.unidad_medida or 'Und.',
                    'Detalle: Cantidad': float(linea.cantidad_pedida),
                    'Detalle: Valor Unitario': float(linea.valor_unitario),
                    'Detalle: Nota': encabezado['Encab: Nota'],
                    # Confirmado en la carga de prueba real (2026-09-15):
                    # el importador de WO espera esta columna con la misma
                    # fecha que 'Encab: Fecha', no vacía.
                    'Detalle: Vencimiento': orden.fecha_oc,
                })
                filas.append(fila)

            meta['ordenes'].append(orden.numero_oc)
            meta['total_lineas'] += len(lineas)

        if lineas_sin_precio:
            raise WoExportComprasException(
                "Estas líneas no tienen valor unitario -- ponle el precio pactado con "
                "el proveedor antes de exportar: " + "; ".join(lineas_sin_precio)
            )

        if not filas:
            raise WoExportComprasException(
                "Ninguna de las OC seleccionadas tiene líneas para exportar"
            )

        df = pd.DataFrame(filas, columns=COLUMNAS_OC)
        return df, meta

    # ------------------------------------------------------------------
    # Exportación
    # ------------------------------------------------------------------
    @staticmethod
    def exportar(numeros_oc, usuario, formato=None):
        if not WoExportComprasService.esta_habilitado():
            raise ExportacionDeshabilitadaException(
                "La exportación de Compras a World Office está deshabilitada "
                "(AppConfig['wo_export.compras_habilitado']). Se activa cuando "
                "los valores de la plantilla estén confirmados contra WO."
            )

        df, meta = WoExportComprasService.construir_dataset(numeros_oc)

        ahora = datetime.now()
        for orden in db.session.query(OrdenCompraProveedor).filter(
            OrdenCompraProveedor.numero_oc.in_(numeros_oc)
        ).all():
            if not orden.exportada_wo:
                orden.exportada_wo = True
                orden.exportada_por = usuario
                orden.exportada_en = ahora
        db.session.commit()

        meta['formato'] = formato or FORMATO_DEFECTO
        return df, meta

    @staticmethod
    def escribir_archivo(df, ruta, formato):
        if formato == 'txt':
            df.to_csv(ruta, sep=DELIMITADOR_TXT, index=False, encoding=ENCODING_TXT)
        else:
            df.to_excel(ruta, index=False, engine='openpyxl')
        return ruta

    @staticmethod
    def generar_task(task_id, numeros_oc, usuario, formato=None):
        """Target de task_runner.run_in_background -- mismo patrón que
        WoExportService.generar_task, sin ZIP (una OC = un solo archivo,
        volumen bajo esperado frente a OP)."""
        from backend.core import task_runner

        try:
            df, meta = WoExportComprasService.exportar(numeros_oc, usuario, formato)
            formato = meta['formato']
            tmp_dir = tempfile.mkdtemp(prefix='wo_export_compras_')
            ext = 'txt' if formato == 'txt' else 'xlsx'
            nombre = f"OC_WorldOffice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
            ruta = os.path.join(tmp_dir, nombre)
            WoExportComprasService.escribir_archivo(df, ruta, formato)
            mimetype = ('text/plain' if formato == 'txt'
                        else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            task_runner.set_completed(task_id, ruta, nombre, mimetype, meta)
        except Exception as e:
            db.session.rollback()
            logger.error(f"[WO-EXPORT-COMPRAS] Falló la generación de la tarea {task_id}: {e}")
            task_runner.set_failed(task_id, str(e))

    @staticmethod
    def listar_ordenes_exportables():
        """Lista para la vista de descarga de Diego: OC que ya tienen
        líneas y no están anuladas."""
        ordenes = OrdenCompraProveedor.query.filter(
            OrdenCompraProveedor.estado != 'ANULADA'
        ).order_by(OrdenCompraProveedor.fecha_oc.desc()).all()

        resultado = []
        for orden in ordenes:
            n_lineas = LineaOrdenCompra.query.filter_by(id_oc=orden.id).count()
            resultado.append({
                'numero_oc': orden.numero_oc,
                'proveedor_nombre': orden.proveedor_nombre,
                'fecha_oc': orden.fecha_oc.strftime('%Y-%m-%d') if orden.fecha_oc else '',
                'estado': orden.estado,
                'lineas': n_lineas,
                'exportada_wo': orden.exportada_wo,
                'exportada_en': orden.exportada_en.strftime('%Y-%m-%d %H:%M') if orden.exportada_en else None,
            })
        return resultado
