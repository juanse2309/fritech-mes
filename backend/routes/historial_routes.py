from flask import Blueprint, request, current_app
from sqlalchemy import text
from backend.core.responses import api_success, api_error
from backend.core import task_runner
from datetime import datetime
import logging
import os
import tempfile
import psycopg2.extensions
# Registrar OID 25 (TEXT) como UNICODE para evitar errores de mapeo
psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)

from backend.core.sql_database import db
from backend.models.sql_models import (
    ProduccionInyeccion, ProduccionPulido, RawVentas, 
    Ensamble, Mezcla, BujeRevuelto,
    PncInyeccion, PncPulido, PncEnsamble
)
from backend.utils.auth_middleware import require_role, ROL_ADMINS
from backend.utils.formatters import to_float, to_int, calcular_metricas_inyeccion
from backend.services.historial_service import preparar_movimientos_para_excel, generar_excel_historial_global

historial_bp = Blueprint('historial_bp', __name__)
logger = logging.getLogger(__name__)

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

def _construir_movimientos_historial(f_desde, f_hasta, tipo_filtro):
    """
    Ejecuta las consultas SQL del Historial Global y arma la lista de movimientos.
    Extraida de obtener_historial_global() para que la exportacion a Excel pueda
    reutilizar los mismos datos en memoria sin pasar por jsonify()/get_json()
    (ese round-trip duplicaba la lista completa y fue causa de OOM en Render
    con rangos de fecha grandes -- ver exportar_excel_historial_global).
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


@historial_bp.route('/api/historial-global', methods=['GET'])
@require_role(ROL_ADMINS + ['AUXILIAR INVENTARIO'])
def obtener_historial_global():
    """
    Historial Global v5.0 SQL-Limpio (Dict Mapping).
    Sincronizado con llaves en Mayúscula para el frontend.
    """
    try:
        desde_str = request.args.get('desde', '')
        hasta_str = request.args.get('hasta', '')
        tipo_filtro = request.args.get('tipo', '')

        # Rango de fechas
        hoy = datetime.now().date()
        f_desde = datetime.strptime(desde_str, '%Y-%m-%d').date() if desde_str else hoy
        f_hasta = datetime.strptime(hasta_str, '%Y-%m-%d').date() if hasta_str else hoy

        logger.debug(f"🔍 [Historial] Consulta v5.0 SQL-Limpio ({f_desde} -> {f_hasta})")
        movimientos = _construir_movimientos_historial(f_desde, f_hasta, tipo_filtro)
        return api_success(data=movimientos)

    except Exception as e:
        logger.error(f"Error crítico Historial v4.2: {e}")
        return api_error(str(e), status_code=500)


@historial_bp.route('/api/historial/detalle', methods=['GET'])
@require_role(ROL_ADMINS + ['AUXILIAR INVENTARIO'])
def obtener_detalle_historial():
    """
    Devuelve todos los campos detallados de un registro específico
    para poder editarlos con sus valores reales en el modal.
    """
    try:
        import decimal
        hoja = request.args.get('hoja')
        fila = request.args.get('fila')
        
        if not hoja or not fila:
            return api_error('Faltan parámetros hoja o fila', status_code=400)

        # Determinar modelo
        model = None
        if hoja == 'db_inyeccion':
            model = ProduccionInyeccion
        elif hoja == 'db_pulido':
            model = ProduccionPulido
        elif hoja == 'db_ensambles':
            model = Ensamble
        elif hoja == 'db_mezcla':
            model = Mezcla
        elif hoja == 'db_ventas':
            model = RawVentas
        else:
            return api_error(f'Hoja no soportada: {hoja}', status_code=400)

        registro = model.query.get(fila)
        if not registro:
            return api_error('Registro no encontrado', status_code=404)
            
        # Convertir a dict serializable
        datos = {}
        for col in registro.__table__.columns:
            val = getattr(registro, col.name)
            # Formatear fechas y datetimes
            if isinstance(val, datetime):
                datos[col.name] = val.isoformat()
            elif hasattr(val, 'strftime') and val.__class__.__name__ == 'date':
                datos[col.name] = val.isoformat()
            elif isinstance(val, decimal.Decimal):
                datos[col.name] = float(val)
            else:
                datos[col.name] = val
                
        return api_success(data=datos)

    except Exception as e:
        logger.error(f"Error obteniendo detalle de registro: {e}")
        return api_error(str(e), status_code=500)


@historial_bp.route('/api/historial/actualizar', methods=['POST'])
@require_role(ROL_ADMINS + ['AUXILIAR INVENTARIO'])
def actualizar_registro_historial():
    """
    Endpoint para editar registros corregidos por Auditoría / Gerencia desde el Historial Global.
    Mapea campos visuales a las columnas reales en las diferentes tablas (db_inyeccion, db_pulido, etc).
    """
    try:
        data = request.json
        hoja = data.get('hoja')
        fila = data.get('fila')
        datos = data.get('datos', {})
        usuario = data.get('usuario', 'SISTEMA')
        
        if not hoja or not fila:
            return api_error('Faltan datos de hoja o fila', status_code=400)

        # Determinar modelo
        model = None
        if hoja == 'db_inyeccion':
            model = ProduccionInyeccion
        elif hoja == 'db_pulido':
            model = ProduccionPulido
        elif hoja == 'db_ensambles':
            model = Ensamble
        elif hoja == 'db_mezcla':
            model = Mezcla
        elif hoja == 'db_ventas':
            model = RawVentas
        else:
            return api_error(f'Hoja no soportada: {hoja}', status_code=400)

        registro = model.query.get(fila)
        if not registro:
            return api_error('Registro no encontrado en la base de datos', status_code=404)
            
        # Mapeo estricto a las columnas actuales (Blindaje ante cambios recientes)
        MAPEO = {
            'RESPONSABLE': 'responsable',
            'DEPARTAMENTO': 'departamento',
            'MAQUINA': 'maquina',
            'ORDEN PRODUCCION': 'orden_produccion',
            'ID CODIGO': 'id_codigo',
            'CODIGO': 'codigo',
            'CODIGO ENSAMBLE': 'codigo_ensamble',
            'FECHA INICIA': 'fecha_inicia',
            'FECHA': 'fecha',
            'FECHA FIN': 'fecha_fin',
            'HORA LLEGADA': 'hora_llegada',
            'HORA INICIO': 'hora_inicio',
            'HORA TERMINA': 'hora_termina',
            'HORA FIN': 'hora_fin',
            'No. CAVIDADES': 'cavidades',
            'CONTADOR MAQ.': 'cant_contador',
            'CANT. CONTADOR': 'cant_contador',
            'CANTIDAD REAL': 'cantidad_real',
            'ALMACEN DESTINO': 'almacen_destino',
            'PESO BUJES': 'peso_bujes',
            'OBSERVACIONES': 'observaciones',
            'CANTIDAD RECIBIDA': 'cantidad_recibida',
            'BUJES BUENOS': 'cantidad_real',
            'PNC': 'pnc_pulido',
            'PNC_INYECCION': 'pnc_inyeccion',
            'CANTIDAD': 'cantidad',
            'OP NUMERO': 'op_numero',
            'ID ENSAMBLE': 'id_ensamble',
            'BUJE ENSAMBLE': 'buje_ensamble',
            'QTY (Unitaria)': 'qty',
            'CONSUMO_TOTAL': 'consumo_total',
            'ALMACEN ORIGEN': 'almacen_para_descargar',
            'VIRGEN (Kg)': 'virgen_kg',
            'MOLIDO (Kg)': 'molido_kg',
            'PIGMENTO (Kg)': 'pigmento_kg',
            'LOTE_INTERNO': 'lote_interno',
            'LOTE': 'lote',
            'ESTADO': 'estado',
            'HORA': 'hora',
            'CLIENTE': 'nombres',
            'DOCUMENTO': 'documento',
            'PRODUCTO': 'productos',
            'CLASIFICACION': 'clasificacion',
            'TOTAL_INGRESOS': 'total_ingresos',
            'PRECIO_PROMEDIO': 'precio_promedio'
        }
        
        from backend.models.sql_models import OperacionLog
        try:
            nuevo_log = OperacionLog(
                modulo="HISTORIAL_GLOBAL",
                operario=usuario,
                accion=f"Edicion registro en {hoja} (ID {fila})",
                detalles=f"Cambios: {datos}"
            )
            db.session.add(nuevo_log)
        except Exception as log_e:
            logger.warning(f"No se pudo guardar OperacionLog: {log_e}")

        # Intentar determinar una fecha base para combinar con horas si es necesario
        fecha_base = None
        fecha_str = datos.get('FECHA') or datos.get('FECHA INICIA')
        if fecha_str:
            try:
                fecha_base = datetime.strptime(fecha_str.split('T')[0].split(' ')[0], '%Y-%m-%d').date()
            except ValueError:
                try:
                    fecha_base = datetime.strptime(fecha_str.split(' ')[0], '%d/%m/%Y').date()
                except ValueError:
                    pass

        if not fecha_base:
            for col_f in ['fecha', 'fecha_inicia']:
                if hasattr(registro, col_f) and getattr(registro, col_f):
                    val_f = getattr(registro, col_f)
                    if isinstance(val_f, datetime):
                        fecha_base = val_f.date()
                        break
                    elif hasattr(val_f, 'strftime') and val_f.__class__.__name__ == 'date':
                        fecha_base = val_f
                        break
        
        if not fecha_base:
            fecha_base = datetime.now().date()

        for key, value in datos.items():
            if key in MAPEO:
                col_name = MAPEO[key]
                
                # Resiliencia de mapeo para ID CODIGO / CODIGO
                if col_name == 'id_codigo' and not hasattr(registro, 'id_codigo') and hasattr(registro, 'codigo'):
                    col_name = 'codigo'
                elif col_name == 'codigo' and not hasattr(registro, 'codigo') and hasattr(registro, 'id_codigo'):
                    col_name = 'id_codigo'

                if hasattr(registro, col_name):
                    col_attr = getattr(model, col_name)
                    col_type = str(col_attr.type)
                    
                    if value == '' or value is None:
                        if 'Integer' in col_type or 'Numeric' in col_type or 'Float' in col_type or 'BigInteger' in col_type:
                            setattr(registro, col_name, 0)
                        else:
                            setattr(registro, col_name, None)
                        continue

                    # Conversión según el tipo de columna en SQLAlchemy
                    if 'DateTime' in col_type:
                        try:
                            # Caso 1: Es una hora en formato HH:MM o HH:MM:SS
                            if ':' in str(value) and len(str(value)) <= 8:
                                parts = str(value).split(':')
                                h = int(parts[0])
                                m = int(parts[1])
                                s = int(parts[2]) if len(parts) > 2 else 0
                                dt_value = datetime.combine(fecha_base, datetime.min.time().replace(hour=h, minute=m, second=s))
                                setattr(registro, col_name, dt_value)
                            # Caso 2: Es una fecha YYYY-MM-DD
                            else:
                                clean_val = str(value).split('T')[0].split(' ')[0]
                                try:
                                    dt_parsed = datetime.strptime(clean_val, '%Y-%m-%d')
                                except ValueError:
                                    dt_parsed = datetime.strptime(clean_val, '%d/%m/%Y')
                                
                                # Si ya tenía hora, intentar conservarla
                                old_val = getattr(registro, col_name)
                                if old_val and isinstance(old_val, datetime):
                                    dt_value = datetime.combine(dt_parsed.date(), old_val.time())
                                else:
                                    dt_value = dt_parsed
                                setattr(registro, col_name, dt_value)
                        except Exception as e_dt:
                            logger.warning(f"No se pudo parsear DateTime {value} para {col_name}: {e_dt}")

                    elif 'Date' in col_type:
                        try:
                            clean_val = str(value).split('T')[0].split(' ')[0]
                            try:
                                dt_parsed = datetime.strptime(clean_val, '%Y-%m-%d').date()
                            except ValueError:
                                dt_parsed = datetime.strptime(clean_val, '%d/%m/%Y').date()
                            setattr(registro, col_name, dt_parsed)
                        except Exception as e_d:
                            logger.warning(f"No se pudo parsear Date {value} para {col_name}: {e_d}")

                    elif 'Integer' in col_type or 'BigInteger' in col_type:
                        try:
                            setattr(registro, col_name, int(float(str(value).replace(',', '.'))))
                        except ValueError:
                            setattr(registro, col_name, 0)

                    elif 'Numeric' in col_type or 'Float' in col_type:
                        try:
                            setattr(registro, col_name, float(str(value).replace(',', '.')))
                        except ValueError:
                            setattr(registro, col_name, 0.0)

                    else:
                        setattr(registro, col_name, str(value).strip())
                    
        db.session.commit()
        logger.info(f"✅ [Historial] Registro ID {fila} en {hoja} modificado correctamente por {usuario}")
        return api_success(message='Registro actualizado correctamente')

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error actualizando registro desde historial: {e}")
        return api_error(str(e), status_code=500)


def _generar_excel_historial_task(task_id, f_desde, f_hasta, tipo_filtro):
    """
    Trabajo de fondo de exportar_excel_historial_global: arma el Excel completo
    (consulta + normalizacion + Workbook) fuera del hilo HTTP. Corre dentro del
    app_context que le da task_runner.run_in_background -- db.session y demas
    dependen de ese contexto para resolver correctamente en el hilo nuevo.
    """
    try:
        resultados = _construir_movimientos_historial(f_desde, f_hasta, tipo_filtro)
        logger.debug(f"📊 [Historial-Excel] Exportando {len(resultados)} movimientos ({f_desde} -> {f_hasta})")

        # Normalizacion estricta a 24h (delegada al servicio, ver FRITECH V4.5)
        resultados = preparar_movimientos_para_excel(resultados)

        # Construcción del Workbook delegada al servicio (arquitectura: rutas sin lógica de negocio)
        output = generar_excel_historial_global(resultados)

        # El BytesIO en memoria no sobrevive a este hilo: el endpoint de
        # descarga es un request HTTP aparte (y con gthread, posiblemente en
        # otro hilo), asi que se vuelca a un archivo temporal real en disco
        # para que send_file lo pueda abrir despues.
        fd, tmp_path = tempfile.mkstemp(suffix='.xlsx', prefix='historial_')
        with os.fdopen(fd, 'wb') as f:
            f.write(output.getvalue())

        fecha_archivo = datetime.now().strftime('%Y-%m-%d')
        filename = f"Historial_Global_{fecha_archivo}.xlsx"

        task_runner.set_completed(
            task_id, file_path=tmp_path, filename=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logger.error(f"Error exportando Excel Historial Global (task {task_id}): {e}")
        import traceback
        logger.error(traceback.format_exc())
        task_runner.set_failed(task_id, str(e))


@historial_bp.route('/api/exportar-historial-global', methods=['GET'])
@require_role(ROL_ADMINS + ['AUXILIAR INVENTARIO'])
def exportar_excel_historial_global():
    """
    Controller delgado: valida el rango de fechas, delega la generación
    completa del Excel a un hilo de fondo (ver _generar_excel_historial_task)
    y responde de inmediato con el task_id para que el frontend haga polling.
    Antes esto bloqueaba el único worker gunicorn de la app hasta terminar de
    armar el libro completo, tumbando el resto de requests concurrentes en
    rangos de fecha grandes.
    """
    desde_str = request.args.get('desde', '')
    hasta_str = request.args.get('hasta', '')
    tipo_filtro = request.args.get('tipo', '')

    hoy = datetime.now().date()
    try:
        f_desde = datetime.strptime(desde_str, '%Y-%m-%d').date() if desde_str else hoy
        f_hasta = datetime.strptime(hasta_str, '%Y-%m-%d').date() if hasta_str else hoy
    except ValueError:
        return api_error("Formato de fecha inválido, se espera YYYY-MM-DD", status_code=400)

    task_id = task_runner.create_task()
    app_obj = current_app._get_current_object()
    task_runner.run_in_background(
        task_id, app_obj, _generar_excel_historial_task,
        f_desde, f_hasta, tipo_filtro
    )

    return api_success(data={"task_id": task_id}, status_code=202)
