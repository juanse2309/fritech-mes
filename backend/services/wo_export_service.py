"""
wo_export_service.py
=====================
Genera el archivo plano de Órdenes de Producción para subir a World Office
(reunión 2026-08-25). Reemplaza la digitación manual de OP en WO.

Servicio aparte y no una extensión de facturacion_routes.procesar_datos_wo a
propósito: ese está acoplado a Pedido, precios, clientes e IVA, y una OP no
tiene nada de eso. Mismo criterio que ya aplicó agente_wo_comercial al
separar la extracción de OP de la extracción comercial.

Un archivo POR ÁMBITO (INY / ENS / EMP), no uno combinado. Técnicamente WO
sabe separarlos por prefijo, pero: los tres se cierran en momentos distintos
del día, un error en una línea puede hacer que WO rechace el archivo
completo, y separados es obvio cuál reintentar.
"""
import io
import logging
import os
import tempfile
import zipfile
from datetime import datetime

import pandas as pd
from sqlalchemy import func

from backend.config.wo_templates import (
    COLUMNAS_OP, FIJOS_OP, TERCERO_EXTERNO_DEFECTO, TERCERO_INTERNO_DEFECTO,
    RESPONSABLE_POR_MAQUINA, RESPONSABLE_ENSAMBLE, RESPONSABLE_EMPAQUE,
    PREFIJO_POR_AMBITO, DELIMITADOR_TXT, ENCODING_TXT, FORMATO_DEFECTO, nota_op,
)
from backend.core.sql_database import db
from backend.models.sql_models import (
    AppConfig, Ensamble, OpGenerada, Producto, ProduccionEmpaque,
    ProduccionInyeccion, ProgramacionEnsamble,
)

logger = logging.getLogger(__name__)

ESTADOS_EXPORTABLES = ('RESERVADA', 'LISTA_EXPORTAR', 'EXPORTADA', 'CONFIRMADA_WO')


class WoExportException(Exception):
    """Error de negocio de la exportación (OP sin líneas, ámbito inválido...)."""
    pass


class ExportacionDeshabilitadaException(Exception):
    """El flag wo_export.op_habilitado está apagado. Es un guard deliberado:
    mientras queden valores de la plantilla sin confirmar contra WO, nadie
    debe poder generar un archivo que se suba a producción."""
    pass


class WoExportService:

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
        return str(WoExportService._config('wo_export.op_habilitado', 'false')).lower() in ('true', '1', 'si', 'sí')

    @staticmethod
    def _tercero_externo():
        """
        NIT de FriParts, siempre (decisión del usuario 2026-08-28) -- ver
        TERCERO_EXTERNO_DEFECTO. Antes resolvía la cédula de quien registró
        el lote; se quitó a propósito: una OP es un documento interno de la
        empresa, el tercero externo es la empresa. La persona responsable va
        en 'Encab: Tercero Interno' (ver _tercero_interno).
        """
        return WoExportService._config('wo_export.tercero_externo_defecto', TERCERO_EXTERNO_DEFECTO)

    @staticmethod
    def _tercero_interno(ambito, maquina):
        """
        Cédula del "Responsable Producción" (= Encab: Tercero Interno en el
        archivo, confirmado con prueba real 2026-08-26: con el NIT de
        FriParts ahí, el campo queda en blanco en WO). Fijo por
        máquina/ámbito -- decisión 2026-08-27, ver wo_templates.py -- porque
        ninguno de los operarios reales tiene cédula en ningún lado de
        FRITECH todavía.

        Si la persona correspondiente aún no tiene cédula cargada
        (RESPONSABLE_* en None), cae al NIT de FriParts -- mismo
        comportamiento que había antes de este cambio, no bloquea la carga.
        """
        if ambito == 'INYECCION':
            clave = (maquina or '').strip().upper()
            mapa_upper = {k.upper(): v for k, v in RESPONSABLE_POR_MAQUINA.items()}
            cedula = mapa_upper.get(clave)
        elif ambito == 'ENSAMBLE':
            cedula = RESPONSABLE_ENSAMBLE
        elif ambito == 'EMPAQUE':
            cedula = RESPONSABLE_EMPAQUE
        else:
            cedula = None
        return cedula or TERCERO_INTERNO_DEFECTO

    # ------------------------------------------------------------------
    # Líneas por ámbito
    # ------------------------------------------------------------------
    @staticmethod
    def _lineas_inyeccion(numero_op):
        """
        Solo lotes CERRADOS: antes de validar_lote, cantidad_real/pnc_total no
        están auditados contra PNC, así que exportarlos mandaría cifras
        provisionales a WO como si fueran definitivas.

        peso_lote se arrastra para calcular el reparto de costo -- ver
        _asignar_porcentajes.
        """
        filas = db.session.query(
            ProduccionInyeccion.id_codigo,
            func.sum(ProduccionInyeccion.cantidad_real).label('buenas'),
            func.sum(func.coalesce(ProduccionInyeccion.pnc_total, 0)).label('pnc'),
            func.sum(func.coalesce(ProduccionInyeccion.peso_lote, 0)).label('peso'),
        ).filter(
            ProduccionInyeccion.orden_produccion == numero_op,
            ProduccionInyeccion.estado == 'CERRADO',
        ).group_by(ProduccionInyeccion.id_codigo).all()

        return [{
            'codigo': f.id_codigo,
            'buenas': float(f.buenas or 0),
            'pnc': float(f.pnc or 0),
            'peso': float(f.peso or 0),
        } for f in filas]

    @staticmethod
    def _lineas_ensamble(numero_op):
        """
        Fuente: ProgramacionEnsamble.cantidad_realizada, NO SUM(Ensamble.cantidad).

        db_ensambles mezcla el producto final con los renglones de consumo de
        componentes del BOM (comparten id_prog y solo se distinguen por
        id_codigo -- ver el recálculo en reportar_multi). Sumar esa tabla por
        op_numero metería los componentes consumidos como si fueran producción.
        ProgramacionEnsamble solo contiene productos finales y su
        cantidad_realizada ya viene recalculada con el filtro correcto.

        Se agrupa por id_codigo por si el mismo producto tiene varias metas
        el mismo día.
        """
        filas = db.session.query(
            ProgramacionEnsamble.id_codigo,
            func.sum(ProgramacionEnsamble.cantidad_realizada).label('realizado'),
        ).filter(
            ProgramacionEnsamble.op_numero == numero_op,
        ).group_by(ProgramacionEnsamble.id_codigo).all()

        return [{
            'codigo': f.id_codigo,
            'buenas': float(f.realizado or 0),
            'pnc': 0.0,
            'peso': 0.0,
        } for f in filas if float(f.realizado or 0) > 0]

    @staticmethod
    def _lineas_empaque(numero_op):
        filas = db.session.query(
            ProduccionEmpaque.id_codigo,
            func.sum(ProduccionEmpaque.cantidad).label('cant'),
        ).filter(
            ProduccionEmpaque.op_numero == numero_op,
        ).group_by(ProduccionEmpaque.id_codigo).all()

        return [{
            'codigo': f.id_codigo,
            'buenas': float(f.cant or 0),
            'pnc': 0.0,
            'peso': 0.0,
        } for f in filas]

    @staticmethod
    def _obtener_lineas(op):
        if op.ambito == 'INYECCION':
            return WoExportService._lineas_inyeccion(op.numero_op)
        if op.ambito == 'ENSAMBLE':
            return WoExportService._lineas_ensamble(op.numero_op)
        if op.ambito == 'EMPAQUE':
            return WoExportService._lineas_empaque(op.numero_op)
        raise WoExportException(f"Ámbito desconocido: {op.ambito!r}")

    # ------------------------------------------------------------------
    # Traducción de código interno -> código real de WO
    # ------------------------------------------------------------------
    @staticmethod
    def _resolver_codigos_wo(codigos):
        """
        Traduce id_codigo interno (lo que guardan db_inyeccion/db_ensambles/
        db_empaque) al codigo_sistema real que WO tiene en su catálogo de
        Inventarios. (Detalle:Valor Unitario NO sale de aquí: costos
        confirmó 2026-08-26 que ese campo va fijo en 0 -- ver FIJOS_OP.)

        Verificado con una carga de prueba real a WO (2026-08-26): de 8
        referencias con id_codigo crudo (ej. '9631'), WO rechazó 7 con "el
        código debe estar tal como está creado en World Office" -- porque
        id_codigo casi nunca lleva el prefijo real de división (FR-/MT-/
        CAR-...), que sí vive en codigo_sistema. Mismo problema, mismo
        criterio de resolución que bom_service._resolver_codigo_inventario:
        NUNCA se infiere el prefijo por el formato del código, se consulta
        el catálogo (ver memoria del proyecto).

        Si un código no aparece en db_productos, se deja tal cual llegó --
        preferible que WO lo rechace explícitamente (y quede claro cuál
        falta en el catálogo) a inventarle un prefijo.

        Busca por id_codigo Y por codigo_sistema porque los módulos no son
        consistentes entre sí: inyección/empaque guardan el código crudo sin
        prefijo (id_codigo='9380'), pero ensamble ya guarda el código elegido
        en el buscador, que viene CON el prefijo real (id_codigo='FR-9380',
        que coincide con codigo_sistema, no con id_codigo de otro producto).
        Buscar solo por id_codigo dejaría esos casos sin traducir aunque el
        producto sí exista en catálogo.
        """
        if not codigos:
            return {}
        codigos_set = set(codigos)
        mapa = {}

        for p in Producto.query.filter(Producto.id_codigo.in_(codigos_set)).all():
            if p.id_codigo not in mapa:
                mapa[p.id_codigo] = p.codigo_sistema or p.id_codigo

        faltantes = codigos_set - set(mapa.keys())
        if faltantes:
            for p in Producto.query.filter(Producto.codigo_sistema.in_(faltantes)).all():
                mapa.setdefault(p.codigo_sistema, p.codigo_sistema)

        return mapa

    # ------------------------------------------------------------------
    # Reparto del costo (Detalle:Porcentaje de Distribución)
    # ------------------------------------------------------------------
    @staticmethod
    def _asignar_porcentajes(lineas):
        """
        Reparte el costo de la OP entre sus referencias. Verificado contra la
        plantilla de WO: los porcentajes de un documento SUMAN EXACTAMENTE 100
        y no son proporcionales a la cantidad (un documento de ejemplo tiene
        cantidades de 100 a 500 y todas las líneas al 20%).

        Criterio: en inyección el costo dominante es la materia prima, y el
        material consumido ES el peso -- así que se reparte por peso_lote, que
        db_inyeccion ya calcula (cantidad_real x peso_bujes). Cascada cuando
        no hay peso capturado: por cantidad, y en último caso equitativo.
        Ensamble y empaque no capturan peso: siempre van por cantidad.

        El redondeo cierra en 100 EXACTO asignando a la última línea el
        remanente (100 - suma de las anteriores). Sin esto, tres líneas
        iguales darían 33.33 x 3 = 99.99 y WO puede rechazar el documento.
        """
        if not lineas:
            return

        total_peso = sum(l['peso'] for l in lineas)
        total_cant = sum(l['buenas'] + l['pnc'] for l in lineas)

        if total_peso > 0:
            base = [l['peso'] for l in lineas]
            total = total_peso
        elif total_cant > 0:
            base = [l['buenas'] + l['pnc'] for l in lineas]
            total = total_cant
        else:
            base = [1] * len(lineas)
            total = len(lineas)

        acumulado = 0.0
        for i, linea in enumerate(lineas):
            if i == len(lineas) - 1:
                linea['porcentaje'] = round(100.0 - acumulado, 2)
            else:
                pct = round(base[i] / total * 100.0, 2)
                linea['porcentaje'] = pct
                acumulado += pct

    # ------------------------------------------------------------------
    # Construcción del DataFrame
    # ------------------------------------------------------------------
    @staticmethod
    def construir_dataset(numeros_op):
        """
        Arma el DataFrame con las 40 columnas de la plantilla. SOLO LECTURA:
        no cambia el estado de ninguna OP -- eso lo hace exportar().
        """
        ops = db.session.query(OpGenerada).filter(
            OpGenerada.numero_op.in_(numeros_op),
            OpGenerada.estado != 'ANULADA',
        ).order_by(OpGenerada.consecutivo).all()

        if not ops:
            raise WoExportException("No se encontró ninguna OP activa con esos números")

        filas = []
        meta = {'ops': [], 'total_lineas': 0, 'sin_lineas': []}

        for op in ops:
            lineas = WoExportService._obtener_lineas(op)
            if not lineas:
                meta['sin_lineas'].append(op.numero_op)
                continue

            mapa_codigos = WoExportService._resolver_codigos_wo([l['codigo'] for l in lineas])
            for l in lineas:
                l['codigo_wo'] = mapa_codigos.get(l['codigo'], l['codigo'])

            WoExportService._asignar_porcentajes(lineas)

            fecha = op.fecha_produccion
            nota = nota_op(op.ambito, fecha)
            tercero_ext = WoExportService._tercero_externo()
            tercero_int = WoExportService._tercero_interno(op.ambito, op.maquina)
            prefijo = op.prefijo or PREFIJO_POR_AMBITO.get(op.ambito, '')

            for linea in lineas:
                fila = dict(FIJOS_OP)
                fila.update({
                    'Encab: Prefijo': prefijo,
                    'Encab: Documento Número': op.consecutivo,
                    'Encab: Fecha': fecha,
                    'Encab: Tercero Interno': tercero_int,
                    'Encab: Tercero Externo': tercero_ext,
                    'Encab: Fecha Inicial': fecha,
                    'Encab: Fecha Final': fecha,
                    'Encab: Nota': nota,
                    'Detalle:Producto': linea['codigo_wo'],
                    # Cantidad = todo lo que se produjo (PNC incluido: el
                    # material se consumió igual). Cantidad Recibida = lo bueno
                    # que entra a stock. La diferencia es la merma -- WO no
                    # necesita el desglose de PNC, le basta con estas dos.
                    # int, no float: verificado contra el archivo real de WO
                    # (columnas 31/32 son int puro, ej. 120/118). Una unidad
                    # producida siempre es entera; dejarlo en float escribiría
                    # '500.0' en vez de '500' en el archivo final.
                    'Detalle:Cantidad': int(round(linea['buenas'] + linea['pnc'])),
                    'Detalle:Cantidad Recibida': int(round(linea['buenas'])),
                    'Detalle:Nota': nota,
                    'Detalle:Porcentaje de Distribución': linea['porcentaje'],
                    'Detalle:Vencimiento': fecha,
                })
                for col in COLUMNAS_OP:
                    fila.setdefault(col, '')
                filas.append(fila)

            meta['ops'].append({
                'numero_op': op.numero_op, 'ambito': op.ambito,
                'lineas': len(lineas), 'estado': op.estado,
            })
            meta['total_lineas'] += len(lineas)

        if not filas:
            raise WoExportException(
                "Las OP seleccionadas no tienen líneas exportables. "
                "En inyección solo se exportan lotes ya validados (CERRADO)."
            )

        df = pd.DataFrame(filas, columns=COLUMNAS_OP)
        WoExportService._normalizar_tipos(df)
        return df, meta

    @staticmethod
    def _normalizar_tipos(df):
        """
        Fuerza tipo numérico consistente en las columnas que el archivo real
        de WO trae como enteros -- en el DataFrame llegan mezcladas (las
        constantes de terceros son str, las de FIJOS_OP int) porque las
        distintas fuentes no coinciden en tipo. Un importador que valide tipo
        por columna puede aceptar unas filas y rechazar otras sin avisar por
        qué; forzarlo aquí, una sola vez, es más seguro que confiar en que
        cada fuente ya entregue el tipo correcto.

        In-place: modifica el DataFrame recibido, no devuelve uno nuevo.
        """
        for col in ('Encab: Tercero Interno', 'Encab: Tercero Externo', 'Encab: Documento Número'):
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')

    # ------------------------------------------------------------------
    # Listado y exportación
    # ------------------------------------------------------------------
    @staticmethod
    def listar_ops_exportables(fecha_desde=None, fecha_hasta=None, ambito=None):
        """Lista para la vista de descarga, con el conteo real de líneas de
        cada OP para que se vea de antemano cuáles traen algo."""
        q = db.session.query(OpGenerada).filter(OpGenerada.estado.in_(ESTADOS_EXPORTABLES))
        if fecha_desde:
            q = q.filter(OpGenerada.fecha_produccion >= fecha_desde)
        if fecha_hasta:
            q = q.filter(OpGenerada.fecha_produccion <= fecha_hasta)
        if ambito:
            q = q.filter(OpGenerada.ambito == ambito)

        resultado = []
        for op in q.order_by(OpGenerada.fecha_produccion.desc(), OpGenerada.consecutivo.desc()).all():
            try:
                lineas = WoExportService._obtener_lineas(op)
            except Exception as e:
                logger.warning(f"[WO-EXPORT] No se pudieron leer líneas de {op.numero_op}: {e}")
                lineas = []
            resultado.append({
                'numero_op': op.numero_op,
                'ambito': op.ambito,
                'maquina': op.maquina,
                'fecha_produccion': op.fecha_produccion.strftime('%Y-%m-%d') if op.fecha_produccion else '',
                'estado': op.estado,
                'lineas': len(lineas),
                'total_unidades': sum(l['buenas'] for l in lineas),
                'exportada_en': op.exportada_en.strftime('%Y-%m-%d %H:%M') if op.exportada_en else None,
            })
        return resultado

    @staticmethod
    def exportar(numeros_op, usuario, formato=None):
        """
        Construye el dataset y marca las OP como EXPORTADA.

        Reexportar una OP ya EXPORTADA devuelve el mismo contenido SIN
        re-estampar exportada_en/exportada_por: el registro de quién la bajó
        primero es el que importa para auditar, y volver a descargar el
        archivo no es un evento nuevo.
        """
        if not WoExportService.esta_habilitado():
            raise ExportacionDeshabilitadaException(
                "La exportación a World Office está deshabilitada "
                "(AppConfig['wo_export.op_habilitado']). Se activa cuando los valores "
                "de la plantilla estén confirmados contra WO."
            )

        df, meta = WoExportService.construir_dataset(numeros_op)

        ahora = datetime.now()
        for op in db.session.query(OpGenerada).filter(OpGenerada.numero_op.in_(numeros_op)).all():
            if op.estado in ('RESERVADA', 'LISTA_EXPORTAR'):
                op.estado = 'EXPORTADA'
                op.exportada_por = usuario
                op.exportada_en = ahora
        db.session.commit()

        meta['formato'] = formato or FORMATO_DEFECTO
        return df, meta

    # ------------------------------------------------------------------
    # Escritura de archivos
    # ------------------------------------------------------------------
    @staticmethod
    def escribir_archivo(df, ruta, formato):
        if formato == 'txt':
            df.to_csv(ruta, sep=DELIMITADOR_TXT, index=False, encoding=ENCODING_TXT)
        else:
            df.to_excel(ruta, index=False, engine='openpyxl')
        return ruta

    @staticmethod
    def generar_archivos_por_ambito(numeros_op, usuario, formato=None):
        """
        Un archivo por ámbito (ver docstring del módulo). Devuelve
        [(nombre_archivo, DataFrame)] y la metadata combinada.
        """
        formato = formato or FORMATO_DEFECTO
        ops = db.session.query(OpGenerada).filter(
            OpGenerada.numero_op.in_(numeros_op),
            OpGenerada.estado != 'ANULADA',
        ).all()

        por_ambito = {}
        for op in ops:
            por_ambito.setdefault(op.ambito, []).append(op.numero_op)

        salidas = []
        meta_total = {'ops': [], 'total_lineas': 0, 'sin_lineas': [], 'formato': formato}

        for ambito, numeros in sorted(por_ambito.items()):
            try:
                df, meta = WoExportService.exportar(numeros, usuario, formato)
            except WoExportException as e:
                # Un ámbito sin líneas no debe tumbar la descarga de los otros.
                logger.warning(f"[WO-EXPORT] {ambito} sin líneas exportables: {e}")
                meta_total['sin_lineas'].extend(numeros)
                continue

            prefijo = PREFIJO_POR_AMBITO.get(ambito, ambito)
            fecha_tag = datetime.now().strftime('%Y%m%d')
            ext = 'txt' if formato == 'txt' else 'xlsx'
            salidas.append((f"OP_{prefijo}_{fecha_tag}.{ext}", df))

            meta_total['ops'].extend(meta['ops'])
            meta_total['total_lineas'] += meta['total_lineas']
            meta_total['sin_lineas'].extend(meta['sin_lineas'])

        if not salidas:
            raise WoExportException(
                "Ninguna de las OP seleccionadas tiene líneas exportables. "
                "En inyección solo se exportan lotes ya validados (CERRADO)."
            )

        return salidas, meta_total

    @staticmethod
    def generar_task(task_id, numeros_op, usuario, formato=None):
        """
        Target de task_runner.run_in_background: deja la tarea COMPLETED con
        el archivo listo para descargar. Un solo ámbito baja como archivo
        suelto; varios, como ZIP.
        """
        from backend.core import task_runner

        try:
            salidas, meta = WoExportService.generar_archivos_por_ambito(numeros_op, usuario, formato)
            tmp_dir = tempfile.mkdtemp(prefix='wo_export_')

            if len(salidas) == 1:
                nombre, df = salidas[0]
                ruta = os.path.join(tmp_dir, nombre)
                WoExportService.escribir_archivo(df, ruta, meta['formato'])
                mimetype = ('text/plain' if meta['formato'] == 'txt'
                            else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                task_runner.set_completed(task_id, ruta, nombre, mimetype, meta)
                return

            nombre_zip = f"OP_WorldOffice_{datetime.now().strftime('%Y%m%d')}.zip"
            ruta_zip = os.path.join(tmp_dir, nombre_zip)
            with zipfile.ZipFile(ruta_zip, 'w', zipfile.ZIP_DEFLATED) as z:
                for nombre, df in salidas:
                    ruta = os.path.join(tmp_dir, nombre)
                    WoExportService.escribir_archivo(df, ruta, meta['formato'])
                    z.write(ruta, arcname=nombre)
                    os.remove(ruta)
            task_runner.set_completed(task_id, ruta_zip, nombre_zip, 'application/zip', meta)

        except Exception as e:
            db.session.rollback()
            logger.error(f"[WO-EXPORT] Falló la generación de la tarea {task_id}: {e}")
            task_runner.set_failed(task_id, str(e))
