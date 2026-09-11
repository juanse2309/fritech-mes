import logging
from datetime import datetime, date, time
from sqlalchemy import text
from backend.core.sql_database import db
from backend.models.sql_models import Ensamble, Mezcla, RawVentas
from backend.utils.formatters import to_float, to_int, calcular_metricas_inyeccion

logger = logging.getLogger(__name__)

# Se intenta primero con AM/PM explicito (para resolver sin ambiguedad textos
# legacy tipo '09:00:00 PM'), y luego con 24 horas (formato ya correcto).
_FORMATOS_HORA_TEXTO = [
    '%I:%M:%S %p', '%I:%M %p',
    '%H:%M:%S', '%H:%M',
]


def _parsear_hora_texto(valor_str):
    """
    Intenta interpretar un string de hora en cualquiera de los formatos conocidos
    (12h con AM/PM o 24h) y devuelve un time() sin ambiguedad.
    Si el string es un '%H:%M' de 12 horas SIN indicador AM/PM (ej. formularios
    legacy que guardaron '09:00'), no existe forma de recuperar si era AM o PM;
    se respeta ese valor como 24h (09:00 = 9 de la mañana), unica lectura valida
    sin informacion adicional.
    """
    valor_str = valor_str.strip()
    if not valor_str:
        return None
    for fmt in _FORMATOS_HORA_TEXTO:
        try:
            return datetime.strptime(valor_str, fmt).time()
        except ValueError:
            continue
    return None


def normalizar_hora_24h(valor):
    """Normaliza cualquier valor de hora (datetime, time o string 12h/24h) a 'HH:MM:SS' en formato militar de 24 horas."""
    if valor is None or valor == '':
        return ''
    if isinstance(valor, datetime):
        return valor.strftime('%H:%M:%S')
    if isinstance(valor, time):
        return valor.strftime('%H:%M:%S')

    hora = _parsear_hora_texto(str(valor))
    if hora is None:
        logger.warning(f"[HistorialService] No se pudo normalizar hora a 24h, se deja el valor original: '{valor}'")
        return str(valor).strip()
    return hora.strftime('%H:%M:%S')


def normalizar_fecha_hora_24h(valor):
    """Normaliza un valor de fecha/hora completo a 'YYYY-MM-DD HH:MM:SS' en formato militar de 24 horas."""
    if valor is None or valor == '':
        return ''
    if isinstance(valor, datetime):
        return valor.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(valor, date):
        return datetime.combine(valor, time.min).strftime('%Y-%m-%d %H:%M:%S')

    valor_str = str(valor).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d/%m/%Y %H:%M:%S'):
        try:
            return datetime.strptime(valor_str, fmt).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    return valor_str


def preparar_movimientos_para_excel(movimientos):
    """
    Recibe la lista de movimientos del Historial Global (propiedad exclusiva del
    caller de exportacion, no compartida con nadie mas) y la normaliza in-place
    para exportacion a Excel: los campos de hora quedan estrictamente en formato
    militar de 24 horas (HH:MM:SS), eliminando cualquier ambiguedad AM/PM antes
    de llegar a OpenPyXL.
    Normaliza in-place (no copia la lista completa) para no duplicar el dataset
    en memoria -- con rangos de fecha grandes esa copia fue causa de OOM.
    """
    for mov in movimientos:
        mov['HORA_INICIO'] = normalizar_hora_24h(mov.get('HORA_INICIO'))
        mov['HORA_FIN'] = normalizar_hora_24h(mov.get('HORA_FIN'))
    return movimientos


def generar_excel_historial_global(movimientos):
    """
    Construye el Workbook del Historial Global con 3 hojas:
      - 'Historial Completo': todos los movimientos (comportamiento anterior).
      - 'Inyección': solo Tipo == 'INYECCION'.
      - 'Control PNC': solo Tipo == 'PNC' (agrupa Inyección/Pulido/Ensamble,
        que en el DTO ya comparten el mismo Tipo 'PNC' — ver historial_routes.py).
    Devuelve el buffer BytesIO listo para send_file.

    Usa Workbook(write_only=True): en modo normal OpenPyXL retiene en memoria
    un objeto Cell por cada celda escrita durante toda la vida del Workbook;
    en write_only cada fila se serializa y se libera al hacer ws.append(), lo
    que evita mantener el dataset completo duplicado como arbol de objetos.
    Con rangos de fecha grandes esto fue la causa principal del OOM del server
    (Render free tier, 512MB, ver gunicorn.conf.py).
    """
    from openpyxl import Workbook
    from io import BytesIO

    columnas = [
        'Fecha', 'Hora Inicio', 'Hora Fin', 'Tipo', 'Responsable',
        'Producto', 'Orden Prod.', 'Máquina', 'Cantidad',
        'Peso Bujes (g)', 'Cavidades', 'Duración (s)', 'Tiempo Total (min)', 'Seg/Unidad',
        'Detalle'
    ]
    anchos = [12, 11, 11, 12, 20, 18, 15, 15, 10, 14, 10, 12, 16, 12, 40]

    wb = Workbook(write_only=True)

    ws_completo = wb.create_sheet("Historial Completo")
    _escribir_hoja_historial(ws_completo, movimientos, columnas, anchos)

    movimientos_inyeccion = [m for m in movimientos if m.get('Tipo') == 'INYECCION']
    ws_iny = wb.create_sheet("Inyección")
    _escribir_hoja_historial(ws_iny, movimientos_inyeccion, columnas, anchos)

    movimientos_pnc = [m for m in movimientos if m.get('Tipo') == 'PNC']
    ws_pnc = wb.create_sheet("Control PNC")
    _escribir_hoja_historial(ws_pnc, movimientos_pnc, columnas, anchos)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


_COLUMNAS_TEXTO_IZQ = {4, 5, 6, 7, 8, 15}  # Tipo, Responsable, Producto, Orden, Máquina, Detalle


def _escribir_hoja_historial(ws, movimientos, columnas, anchos):
    """
    Escribe cabecera en negrita, filas saneadas, cebreado y anchos en una hoja
    del Historial Global (worksheet en modo write_only: las celdas se arman
    fila por fila con WriteOnlyCell y se entregan via ws.append()).
    """
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.utils import get_column_letter

    header_font = Font(name='Calibri', bold=True, color='FFFFFF', size=11)
    header_fill = PatternFill(start_color='2C3E50', end_color='2C3E50', fill_type='solid')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin', color='D5D8DC'),
        right=Side(style='thin', color='D5D8DC'),
        top=Side(style='thin', color='D5D8DC'),
        bottom=Side(style='thin', color='D5D8DC')
    )
    zebra_fill = PatternFill(start_color='F2F3F4', end_color='F2F3F4', fill_type='solid')
    data_align = Alignment(horizontal='center', vertical='center')
    text_align = Alignment(horizontal='left', vertical='center', wrap_text=True)

    # En modo write_only el anchor/freeze deben fijarse ANTES del primer
    # ws.append(): el writer streamea <cols>/panes al abrir la hoja, y una
    # vez que arrancó <sheetData> ya no puede insertarlos (quedan en 13.0
    # default / sin freeze, sin error visible).
    for i, w in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'

    fila_header = []
    for titulo_col in columnas:
        cell = WriteOnlyCell(ws, value=titulo_col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border
        fila_header.append(cell)
    ws.append(fila_header)

    for row_idx, r in enumerate(movimientos, 2):
        fila = [
            r.get('Fecha', ''),
            r.get('HORA_INICIO', ''),
            r.get('HORA_FIN', ''),
            r.get('Tipo', ''),
            r.get('Responsable', ''),
            r.get('Producto', ''),
            r.get('Orden', ''),
            r.get('maquina', 'N/A'),
            r.get('Cant', 0),
            r.get('peso_bujes'),
            r.get('cavidades'),
            r.get('duracion_segundos'),
            r.get('tiempo_total_minutos'),
            r.get('segundos_por_unidad'),
            r.get('Detalle', '')
        ]

        es_par = (row_idx % 2 == 0)
        fila_celdas = []
        for col_idx, valor in enumerate(fila, 1):
            # Purgar estrictamente cualquier representación de nulo a None para celda vacía en Excel
            if valor is None or (isinstance(valor, float) and (valor != valor)) or str(valor).strip().lower() in ('nan', 'none', 'null'):
                cell_val = None
            else:
                cell_val = valor

            cell = WriteOnlyCell(ws, value=cell_val)
            cell.border = thin_border

            # Columnas 2 y 3 = Hora Inicio / Hora Fin: forzar formato Texto
            # para que OpenPyXL/Excel nunca reinterprete el string 24h
            # normalizado como una hora 12h dependiente del locale.
            if col_idx in (2, 3):
                cell.number_format = '@'

            cell.alignment = text_align if col_idx in _COLUMNAS_TEXTO_IZQ else data_align

            if es_par:
                cell.fill = zebra_fill

            fila_celdas.append(cell)

        ws.append(fila_celdas)


def safe_str(val):
    """Convierte cualquier valor a string de forma segura."""
    if val is None: return ''
    return str(val).strip()


def format_time_py(dt_obj):
    """Formatea objetos DateTime de Python a HH:MM."""
    if not dt_obj: return ''
    if hasattr(dt_obj, 'strftime'):
        return dt_obj.strftime('%H:%M')
    # Si ya es un string, intentar limpiar
    return safe_str(dt_obj)


def format_maquina(val, tipo_proceso=None):
    """
    Normaliza el campo máquina al estándar 'Máquina No. X' o 'N/A'.
    """
    procesos_sin_maquina = {'PULIDO', 'ENSAMBLE', 'VENTA', 'VENTAS', 'FACTURACION', 'PNC'}
    if tipo_proceso and str(tipo_proceso).upper() in procesos_sin_maquina:
        return 'N/A'
    if val is None:
        return 'N/A'
    val_str = str(val).strip()
    if not val_str or val_str.upper() in ['NONE', 'NULL', 'UNDEFINED', 'N/A', '-', '']:
        return 'N/A'
    if val_str.isdigit():
        return f"Máquina No. {int(val_str)}"
    import re
    match = re.search(r'\d+', val_str)
    if match:
        return f"Máquina No. {int(match.group(0))}"
    return val_str


def construir_movimientos_historial(f_desde, f_hasta, tipo_filtro):
    """
    Ejecuta las consultas SQL del Historial Global y arma la lista de movimientos.
    Extraida de obtener_historial_global() (historial_routes.py) para que la
    exportacion a Excel pueda reutilizar los mismos datos en memoria sin pasar
    por jsonify()/get_json() (ese round-trip duplicaba la lista completa y fue
    causa de OOM en Render con rangos de fecha grandes -- ver
    exportar_excel_historial_global).
    """
    movimientos = []

    # 1. INYECCIÓN — SQL nativo con CAST para evitar comparación Date vs DateTime (timestamp)
    if not tipo_filtro or tipo_filtro == 'INYECCION':
        try:
            sql_iny = """
                SELECT
                    id, id_inyeccion::TEXT, fecha_inicia, fecha_fin,
                    id_codigo::TEXT, responsable::TEXT, maquina::TEXT,
                    cantidad_real, estado::TEXT, molde, cavidades,
                    hora_llegada::TEXT, hora_inicio::TEXT, hora_termina::TEXT,
                    cant_contador, almacen_destino::TEXT, codigo_ensamble::TEXT,
                    orden_produccion::TEXT, observaciones::TEXT,
                    pnc_total, departamento::TEXT,
                    peso_bujes, duracion_segundos, tiempo_total_minutos, segundos_por_unidad
                FROM db_inyeccion
                WHERE CAST(fecha_inicia AS DATE) BETWEEN :desde AND :hasta
                ORDER BY fecha_inicia DESC
            """
            logger.debug(
                f"🔍 [Historial-INYECCION] SQL enviado a PostgreSQL: "
                f"SELECT ... FROM db_inyeccion WHERE CAST(fecha_inicia AS DATE) "
                f"BETWEEN '{f_desde}' AND '{f_hasta}'"
            )
            res_raw = db.session.execute(text(sql_iny), {"desde": f_desde, "hasta": f_hasta})
            res_iny = [dict(row._mapping) for row in res_raw]
            logger.debug(f"✅ [Historial-INYECCION] Registros encontrados: {len(res_iny)} (rango {f_desde} → {f_hasta})")

            for r in res_iny:
                try:
                    fi = r.get('fecha_inicia')
                    cant_real = to_float(r.get('cantidad_real'))
                    dur_seg = to_int(r.get('duracion_segundos'))
                    tmp_min = to_float(r.get('tiempo_total_minutos'))
                    seg_uni = to_float(r.get('segundos_por_unidad'))

                    if (tmp_min == 0.0 or seg_uni == 0.0) and dur_seg > 0:
                        calc_min, calc_seg_uni = calcular_metricas_inyeccion(dur_seg, cant_real)
                        if tmp_min == 0.0: tmp_min = calc_min
                        if seg_uni == 0.0: seg_uni = calc_seg_uni

                    movimientos.append({
                        'Fecha': fi.strftime('%d/%m/%Y') if fi else '',
                        'Tipo': 'INYECCION',
                        'Producto': safe_str(r.get('id_codigo', '')),
                        'Responsable': safe_str(r.get('responsable', 'SISTEMA')),
                        'Cant': cant_real,
                        'Orden': safe_str(r.get('orden_produccion', '')) or safe_str(r.get('id_inyeccion', '')),
                        'maquina': format_maquina(r.get('maquina'), 'INYECCION'),
                        'peso_bujes': round(to_float(r.get('peso_bujes')), 4),
                        'cavidades': to_int(r.get('cavidades'), 1),
                        'duracion_segundos': dur_seg,
                        'tiempo_total_minutos': round(tmp_min, 2),
                        'segundos_por_unidad': round(seg_uni, 2),
                        'Extra': f"Molde: {r.get('molde', '')}",
                        'Detalle': safe_str(r.get('observaciones', '')),
                        'HORA_INICIO': safe_str(r.get('hora_inicio', '')),
                        'HORA_FIN': safe_str(r.get('hora_termina', '')),
                        'hoja': 'db_inyeccion',
                        'fila': to_int(r.get('id', 0))
                    })
                except Exception as e_row:
                    logger.error(f"❌ [Historial-INYECCION] Error procesando fila (ID {r.get('id', '?')}): {e_row}")
                    continue
        except Exception as e:
            logger.error(f"❌ [Historial-INYECCION] Error crítico en bloque: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # 2. PULIDO (Lógica Quirúrgica v4.4)
    if not tipo_filtro or tipo_filtro == 'PULIDO':
        try:
            # Consulta SQL con Casts explícitos
            sql_pul = """
                SELECT
                    id, id_pulido::TEXT, fecha, codigo::TEXT, responsable::TEXT,
                    cantidad_real, orden_produccion::TEXT, observaciones::TEXT,
                    hora_inicio, hora_fin
                FROM db_pulido
                WHERE CAST(fecha AS DATE) BETWEEN :desde AND :hasta
            """
            res_raw = db.session.execute(text(sql_pul), {"desde": f_desde, "hasta": f_hasta})
            res_pul = [dict(row._mapping) for row in res_raw]

            # Batch Pre-fetch Revueltos (v5.2 - Zero queries in loop)
            all_pul_ids = [str(r.get('id_pulido') or '').strip() for r in res_pul]
            all_pul_ids = [pid for pid in all_pul_ids if pid]

            revueltos_map = {}
            if all_pul_ids:
                placeholders = ', '.join([f':pid_{i}' for i in range(len(all_pul_ids))])
                sql_revs = f"SELECT id_pulido::TEXT as id_pulido, id_codigo::TEXT as id_codigo, COALESCE(cantidad, 0) as cantidad FROM db_bujes_revueltos WHERE id_pulido IN ({placeholders})"
                params_revs = {f'pid_{i}': pid for i, pid in enumerate(all_pul_ids)}
                revs_raw = db.session.execute(text(sql_revs), params_revs)
                for rv in revs_raw:
                    rv_dict = dict(rv._mapping)
                    pid = str(rv_dict['id_pulido'])
                    if pid not in revueltos_map:
                        revueltos_map[pid] = []
                    revueltos_map[pid].append(rv_dict)

            for r in res_pul:
                try:
                    p_id = str(r.get('id_pulido') or '').strip()
                    cant_real = to_float(r.get('cantidad_real'))

                    # Lookup directo en memoria (cero DB calls)
                    revs = revueltos_map.get(p_id, [])
                    det_revueltos = ""
                    if revs:
                        det_revueltos = " | REVUELTOS: " + ", ".join([f"{str(rv['id_codigo'])}({to_float(rv['cantidad'])})" for rv in revs])

                    obs = str(r.get('observaciones') or '').strip()
                    detalle_final = f"Obs: {obs}{det_revueltos}" if obs else det_revueltos.strip(" | ")

                    movimientos.append({
                        'Fecha': r['fecha'].strftime('%d/%m/%Y') if r['fecha'] else '',
                        'Tipo': 'PULIDO',
                        'Producto': str(r['codigo'] or ''),
                        'Responsable': str(r['responsable'] or 'SISTEMA'),
                        'cantidad_real': cant_real,
                        'Cant': cant_real,
                        'Orden': str(r['orden_produccion'] or p_id or '-'),
                        'maquina': 'N/A',
                        'peso_bujes': None,
                        'cavidades': None,
                        'duracion_segundos': None,
                        'tiempo_total_minutos': None,
                        'segundos_por_unidad': None,
                        'Extra': f"OP: {str(r['orden_produccion'] or '')}",
                        'Detalle': str(detalle_final.strip()),
                        'HORA_INICIO': format_time_py(r['hora_inicio']),
                        'HORA_FIN': format_time_py(r['hora_fin']),
                        'hoja': 'db_pulido',
                        'fila': to_int(r['id'])
                    })
                except Exception as e_row:
                    db.session.rollback()
                    logger.debug(f'Error en fila Pulido: {e_row}')
                    logger.error(f"❌ Error procesando fila Pulido (ID {r.get('id', '?')}): {e_row}")
                    continue
        except Exception as e_block:
            logger.debug(f'Error en Pulido: {e_block}')
            logger.error(f"❌ ERROR CRÍTICO EN BLOQUE PULIDO: {e_block}")
            import traceback
            logger.error(traceback.format_exc())

    # 3. ENSAMBLE
    if not tipo_filtro or tipo_filtro == 'ENSAMBLE':
        try:
            # Ensamble.fecha es TIMESTAMP: .between(date, date) compara contra
            # medianoche del :hasta y descarta los registros con hora real
            # (mismo bug que el resto de filtros de fecha del dashboard) --
            # se castea a DATE para comparar por día completo.
            res = Ensamble.query.filter(db.func.cast(Ensamble.fecha, db.Date).between(f_desde, f_hasta)).all()
            for r in res:
                movimientos.append({
                    'Fecha': getattr(r.fecha, 'strftime', lambda x: '')('%d/%m/%Y') if r.fecha else '',
                    'Tipo': 'ENSAMBLE',
                    'Producto': safe_str(getattr(r, 'id_codigo', '')),
                    'Responsable': safe_str(getattr(r, 'responsable', 'SISTEMA')),
                    'Cant': to_float(getattr(r, 'cantidad', 0)),
                    'Orden': safe_str(getattr(r, 'op_numero', '')) or safe_str(getattr(r, 'id_ensamble', '')),
                    'maquina': 'N/A',
                    'peso_bujes': None,
                    'cavidades': None,
                    'duracion_segundos': None,
                    'tiempo_total_minutos': None,
                    'segundos_por_unidad': None,
                    'Extra': safe_str(getattr(r, 'buje_ensamble', '')),
                    'Detalle': safe_str(getattr(r, 'observaciones', '')),
                    'HORA_INICIO': format_time_py(getattr(r, 'hora_inicio', None)),
                    'HORA_FIN': format_time_py(getattr(r, 'hora_fin', None)),
                    'hoja': 'db_ensambles',
                    'fila': to_int(getattr(r, 'id', 0))
                })
        except Exception as e:
            logger.error(f"Error Ensamble: {e}")

    # 4. MEZCLA
    if not tipo_filtro or tipo_filtro == 'MEZCLA':
        try:
            res = Mezcla.query.filter(Mezcla.fecha.between(f_desde, f_hasta)).all()
            for r in res:
                movimientos.append({
                    'Fecha': getattr(r.fecha, 'strftime', lambda x: '')('%d/%m/%Y') if r.fecha else '',
                    'Tipo': 'MEZCLA',
                    'Producto': 'PREPARACION MATERIAL',
                    'Responsable': safe_str(getattr(r, 'responsable', 'SISTEMA')),
                    'Cant': f"{to_float(getattr(r, 'virgen_kg', 0))}Kg V",
                    'maquina': format_maquina(getattr(r, 'maquina', None), 'MEZCLA'),
                    'peso_bujes': None,
                    'cavidades': None,
                    'duracion_segundos': None,
                    'tiempo_total_minutos': None,
                    'segundos_por_unidad': None,
                    'Extra': f"{to_float(getattr(r, 'molido_kg', 0))}Kg M",
                    'Detalle': safe_str(getattr(r, 'observaciones', '')),
                    'HORA_INICIO': '',
                    'HORA_FIN': '',
                    'hoja': 'db_mezcla',
                    'fila': to_int(getattr(r, 'id', 0))
                })
        except Exception as e:
            logger.error(f"Error Mezcla: {e}")

    # 5. VENTAS
    if not tipo_filtro or tipo_filtro in ['VENTA', 'VENTAS', 'FACTURACION']:
        try:
            # RawVentas.fecha es TIMESTAMP -- mismo bug de CAST que Ensamble arriba.
            res = RawVentas.query.filter(db.func.cast(RawVentas.fecha, db.Date).between(f_desde, f_hasta)).all()
            for r in res:
                movimientos.append({
                    'Fecha': getattr(r.fecha, 'strftime', lambda x: '')('%d/%m/%Y') if r.fecha else '',
                    'Tipo': 'VENTA',
                    'Producto': safe_str(getattr(r, 'productos', '')),
                    'Responsable': safe_str(getattr(r, 'nombres', 'CLIENTE DESCONOCIDO')),
                    'Cant': to_float(getattr(r, 'cantidad', 0)),
                    'Orden': safe_str(getattr(r, 'documento', '')),
                    'maquina': 'N/A',
                    'peso_bujes': None,
                    'cavidades': None,
                    'duracion_segundos': None,
                    'tiempo_total_minutos': None,
                    'segundos_por_unidad': None,
                    'Extra': safe_str(getattr(r, 'clasificacion', '')),
                    'Detalle': f"Ingreso: ${to_float(getattr(r, 'total_ingresos', 0))}",
                    'HORA_INICIO': '',
                    'HORA_FIN': '',
                    'hoja': 'db_ventas',
                    'fila': to_int(getattr(r, 'id', 0))
                })
        except Exception as e:
            logger.error(f"Error Ventas: {e}")

    # 5.5 METALS (FRIMETALS) -- metals_produccion.fecha es VARCHAR 'DD/MM/YYYY'
    # (ver registrar_produccion_metals en metals_routes.py), no Date/Timestamp
    # como el resto de las tablas de este archivo, por eso usa TO_DATE en vez
    # de .between(). Gate por Empresa.NOMBRE: FriParts comparte la misma tabla
    # metals_produccion en su base (legado), pero ese dato nunca debe
    # mezclarse en el Historial Global de FriParts.
    from backend.config.settings import Empresa
    if Empresa.NOMBRE.upper() == 'FRIMETALS' and (not tipo_filtro or tipo_filtro.upper() == 'METALS'):
        try:
            sql_metals = """
                SELECT id, fecha, responsable, proceso, maquina, id_pedido,
                       codigo, descripcion, cantidad_ok, pnc, hora_inicio, hora_fin,
                       tiempo, observaciones
                FROM metals_produccion
                WHERE TO_DATE(fecha, 'DD/MM/YYYY') BETWEEN :desde AND :hasta
                ORDER BY TO_DATE(fecha, 'DD/MM/YYYY') DESC, id DESC
            """
            res_raw = db.session.execute(text(sql_metals), {"desde": f_desde, "hasta": f_hasta})
            for r in [dict(row._mapping) for row in res_raw]:
                movimientos.append({
                    'Fecha': r.get('fecha') or '',
                    'Tipo': safe_str(r.get('proceso', '')).upper() or 'METALS',
                    'Producto': safe_str(r.get('codigo', '')),
                    'Responsable': safe_str(r.get('responsable', 'SISTEMA')),
                    'Cant': to_float(r.get('cantidad_ok')),
                    'Orden': safe_str(r.get('id_pedido', '')),
                    'maquina': safe_str(r.get('maquina', '')) or 'N/A',
                    'peso_bujes': None,
                    'cavidades': None,
                    'duracion_segundos': None,
                    'tiempo_total_minutos': None,
                    'segundos_por_unidad': None,
                    'Extra': safe_str(r.get('descripcion', '')),
                    'Detalle': safe_str(r.get('observaciones', '')),
                    'HORA_INICIO': safe_str(r.get('hora_inicio', '')),
                    'HORA_FIN': safe_str(r.get('hora_fin', '')),
                    'hoja': 'metals_produccion',
                    'fila': to_int(r.get('id', 0)),
                    'pnc_metals': to_float(r.get('pnc'))
                })
        except Exception as e:
            logger.error(f"Error Metals Produccion: {e}")

    # 6. PNC
    if not tipo_filtro or tipo_filtro == 'PNC':
        try:
            # NOTA CRITICA (reemplaza el outerjoin ORM anterior): el campo
            # de enlace (id_inyeccion / id_pulido / id_ensamble) NO es
            # unico por fila en las tablas de produccion -- un mismo lote
            # multi-SKU agrupa varias filas (una por id_codigo), y ademas
            # se detectaron colisiones REALES entre lotes NO relacionados
            # (ej. id_inyeccion 'INY-890801A3' con 7 filas del lote real
            # + 1 fila intrusa de otro dia/otra orden; varios id_pulido
            # cortos tipo 'PUL-52248' compartidos por producciones sin
            # relacion). Un JOIN directo contra la tabla completa
            # multiplicaba cada fila de PNC una vez por cada match (bug
            # de fan-out: un solo PNC aparecia triplicado/quintuplicado
            # en el Historial Global).
            #
            # Fix: cada bloque arma una fila "representante" por lote via
            # DISTINCT ON, y ademas cuenta cuantas combinaciones DISTINTAS
            # de (fecha, orden) existen bajo ese mismo id de lote. Si hay
            # mas de una (n_combinaciones > 1) el id esta en colision real
            # entre eventos distintos: NO se adivina cual es el correcto,
            # se marca 'ID AMBIGUO' en vez de mostrar una fecha/orden que
            # podria ser la equivocada.

            sql_pnc_iny = text("""
                WITH combos AS (
                    SELECT id_inyeccion, fecha_inicia, orden_produccion, maquina
                    FROM db_inyeccion
                    WHERE id_inyeccion IS NOT NULL
                    GROUP BY id_inyeccion, fecha_inicia, orden_produccion, maquina
                ),
                ambiguedad AS (
                    SELECT id_inyeccion, COUNT(*) as n_combinaciones
                    FROM combos
                    GROUP BY id_inyeccion
                ),
                representante AS (
                    SELECT DISTINCT ON (id_inyeccion) id_inyeccion, fecha_inicia, orden_produccion, maquina
                    FROM db_inyeccion
                    WHERE id_inyeccion IS NOT NULL
                    ORDER BY id_inyeccion, fecha_inicia DESC
                )
                SELECT
                    p.id_row, p.id_codigo, p.cantidad, p.criterio, p.codigo_ensamble, p.id_inyeccion,
                    r.fecha_inicia, r.orden_produccion, r.maquina,
                    COALESCE(a.n_combinaciones, 1) as n_combinaciones
                FROM db_pnc_inyeccion p
                LEFT JOIN representante r ON p.id_inyeccion = r.id_inyeccion
                LEFT JOIN ambiguedad a ON r.id_inyeccion = a.id_inyeccion
                WHERE r.id_inyeccion IS NULL
                   OR COALESCE(a.n_combinaciones, 1) > 1
                   OR (CAST(r.fecha_inicia AS DATE) BETWEEN :desde AND :hasta)
            """)
            res_pnc_iny = db.session.execute(sql_pnc_iny, {"desde": f_desde, "hasta": f_hasta}).mappings().all()
            for r in res_pnc_iny:
                ambiguo = (r.get('n_combinaciones') or 1) > 1
                fecha_inicia = r.get('fecha_inicia') if not ambiguo else None
                movimientos.append({
                    'Fecha': fecha_inicia.strftime('%d/%m/%Y') if fecha_inicia else ('ID AMBIGUO' if ambiguo else 'S/F'),
                    'Tipo': 'PNC',
                    'Producto': safe_str(r.get('id_codigo', '')),
                    'Responsable': 'INYECCION',
                    'Cant': to_float(r.get('cantidad', 0)),
                    'Orden': (safe_str(r.get('orden_produccion', '')) if not ambiguo else '') or safe_str(r.get('id_inyeccion', '')),
                    'maquina': format_maquina(r.get('maquina'), 'INYECCION') if (r.get('maquina') and not ambiguo) else 'N/A',
                    'peso_bujes': None,
                    'cavidades': None,
                    'duracion_segundos': None,
                    'tiempo_total_minutos': None,
                    'segundos_por_unidad': None,
                    'Extra': 'PNC Inyeccion',
                    'Detalle': f"Criterio: {safe_str(r.get('criterio', ''))} | Notas: {safe_str(r.get('codigo_ensamble', ''))}",
                    'HORA_INICIO': '',
                    'HORA_FIN': '',
                    'hoja': 'db_pnc_inyeccion',
                    'fila': to_int(r.get('id_row', 0))
                })

            # PNC PULIDO — mismo patron. Pulido no maneja concepto de
            # maquina (format_maquina ya fuerza 'N/A' para este proceso).
            sql_pnc_pul = text("""
                WITH combos AS (
                    SELECT id_pulido::text as id_pulido, fecha, orden_produccion
                    FROM db_pulido
                    GROUP BY id_pulido::text, fecha, orden_produccion
                ),
                ambiguedad AS (
                    SELECT id_pulido, COUNT(*) as n_combinaciones
                    FROM combos
                    GROUP BY id_pulido
                ),
                representante AS (
                    SELECT DISTINCT ON (id_pulido::text) id_pulido::text as id_pulido, fecha, orden_produccion
                    FROM db_pulido
                    ORDER BY id_pulido::text, fecha DESC
                )
                SELECT
                    p.id_row, p.codigo, p.cantidad, p.criterio, p.codigo_ensamble, p.id_pulido,
                    r.fecha, r.orden_produccion,
                    COALESCE(a.n_combinaciones, 1) as n_combinaciones
                FROM db_pnc_pulido p
                LEFT JOIN representante r ON p.id_pulido::text = r.id_pulido
                LEFT JOIN ambiguedad a ON r.id_pulido = a.id_pulido
                WHERE r.id_pulido IS NULL
                   OR COALESCE(a.n_combinaciones, 1) > 1
                   OR (CAST(r.fecha AS DATE) BETWEEN :desde AND :hasta)
            """)
            res_pnc_pul = db.session.execute(sql_pnc_pul, {"desde": f_desde, "hasta": f_hasta}).mappings().all()
            for r in res_pnc_pul:
                ambiguo = (r.get('n_combinaciones') or 1) > 1
                fecha = r.get('fecha') if not ambiguo else None
                movimientos.append({
                    'Fecha': fecha.strftime('%d/%m/%Y') if fecha else ('ID AMBIGUO' if ambiguo else 'S/F'),
                    'Tipo': 'PNC',
                    'Producto': safe_str(r.get('codigo', '')),
                    'Responsable': 'PULIDO',
                    'Cant': to_float(r.get('cantidad', 0)),
                    'Orden': (safe_str(r.get('orden_produccion', '')) if not ambiguo else '') or safe_str(r.get('id_pulido', '')),
                    'maquina': 'N/A',
                    'peso_bujes': None,
                    'cavidades': None,
                    'duracion_segundos': None,
                    'tiempo_total_minutos': None,
                    'segundos_por_unidad': None,
                    'Extra': 'PNC Pulido',
                    'Detalle': f"Criterio: {safe_str(r.get('criterio', ''))} | Notas: {safe_str(r.get('codigo_ensamble', ''))}",
                    'HORA_INICIO': '',
                    'HORA_FIN': '',
                    'hoja': 'db_pnc_pulido',
                    'fila': to_int(r.get('id_row', 0))
                })

            # PNC ENSAMBLE — mismo patron (auditado: 0 grupos ambiguos
            # hoy, pero se deja la misma guarda por si aparecen a futuro;
            # Ensamble tampoco maneja concepto de maquina).
            sql_pnc_ens = text("""
                WITH combos AS (
                    SELECT id_ensamble, fecha, op_numero
                    FROM db_ensambles
                    GROUP BY id_ensamble, fecha, op_numero
                ),
                ambiguedad AS (
                    SELECT id_ensamble, COUNT(*) as n_combinaciones
                    FROM combos
                    GROUP BY id_ensamble
                ),
                representante AS (
                    SELECT DISTINCT ON (id_ensamble) id_ensamble, fecha, op_numero
                    FROM db_ensambles
                    ORDER BY id_ensamble, fecha DESC
                )
                SELECT
                    p.id_row, p.id_codigo, p.cantidad, p.criterio, p.codigo_ensamble, p.id_ensamble,
                    r.fecha, r.op_numero,
                    COALESCE(a.n_combinaciones, 1) as n_combinaciones
                FROM db_pnc_ensamble p
                LEFT JOIN representante r ON p.id_ensamble = r.id_ensamble
                LEFT JOIN ambiguedad a ON r.id_ensamble = a.id_ensamble
                WHERE r.id_ensamble IS NULL
                   OR COALESCE(a.n_combinaciones, 1) > 1
                   OR (CAST(r.fecha AS DATE) BETWEEN :desde AND :hasta)
            """)
            res_pnc_ens = db.session.execute(sql_pnc_ens, {"desde": f_desde, "hasta": f_hasta}).mappings().all()
            for r in res_pnc_ens:
                ambiguo = (r.get('n_combinaciones') or 1) > 1
                fecha = r.get('fecha') if not ambiguo else None
                movimientos.append({
                    'Fecha': fecha.strftime('%d/%m/%Y') if fecha else ('ID AMBIGUO' if ambiguo else 'S/F'),
                    'Tipo': 'PNC',
                    'Producto': safe_str(r.get('id_codigo', '')),
                    'Responsable': 'ENSAMBLE',
                    'Cant': to_float(r.get('cantidad', 0)),
                    'Orden': (safe_str(r.get('op_numero', '')) if not ambiguo else '') or safe_str(r.get('id_ensamble', '')),
                    'maquina': 'N/A',
                    'peso_bujes': None,
                    'cavidades': None,
                    'duracion_segundos': None,
                    'tiempo_total_minutos': None,
                    'segundos_por_unidad': None,
                    'Extra': 'PNC Ensamble',
                    'Detalle': f"Criterio: {safe_str(r.get('criterio', ''))} | Notas: {safe_str(r.get('codigo_ensamble', ''))}",
                    'HORA_INICIO': '',
                    'HORA_FIN': '',
                    'hoja': 'db_pnc_ensamble',
                    'fila': to_int(r.get('id_row', 0))
                })

        except Exception as e:
            logger.error(f"Error PNC en historial: {e}")

    return movimientos
