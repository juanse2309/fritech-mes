import os
import uuid
import json
import logging
from datetime import datetime, date, timedelta
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from backend.models.sql_models import db, ProduccionInyeccion, PncInyeccion, PncPulido, ProgramacionInyeccion, DistribucionOpPedidos, Producto, TrazabilidadLote, Pedido, ProduccionEmpaque
from backend.services.audit_service import AuditService, OwnershipMismatchException, ValidadorRequeridoException, TurnoInvalidoException
from backend.services.op_numerador_service import OpNumeradorService
from backend.utils.time_utils import get_colombia_time
from backend.utils.formatters import sql_normalizar_codigo_fr

logger = logging.getLogger(__name__)

# Ventana de reenvío para registrar_directa (ver docstring del método): un
# reintento de red o un doble toque no debería tardar más que esto en llegar
# de nuevo al servidor. Lo bastante corto para no confundir dos reportes
# legítimos y distintos del mismo producto en la misma máquina.
SEGUNDOS_VENTANA_DUPLICADO = 20

# Estados de db_pedidos.estado que representan un pedido AÚN NO despachado
# por completo (hallazgo 2026-08-31, revisando por qué "Demanda Activa" no
# reflejaba un pedido real): la lista anterior ('PENDIENTE', 'ABIERTO',
# 'Alistamiento', 'ALISTADO') no corresponde a los valores reales que usa el
# flujo de Gestión de Pedidos -- verificado contra la base real, los estados
# que de verdad existen son estos. Solo 'DESPACHADO' es terminal (excluido).
ESTADOS_PEDIDO_ACTIVO = ['EN ALISTAMIENTO', 'LISTO PARA DESPACHO', 'DESPACHADO PARCIAL', 'DESPACHO PARCIAL', 'EXPORTADO_WO']


def _normalizar_codigo_fr_python(referencia: str) -> str:
    """
    Equivalente en Python de sql_normalizar_codigo_fr, para el lado del bind
    param (ver PulidoService._normalizar_referencia_bind -- mismo criterio,
    duplicado aquí para no acoplar los dos servicios): un código puramente
    numérico se compara con prefijo 'FR-', el resto se deja tal cual.
    """
    ref = str(referencia or '').strip().upper()
    return f"FR-{ref}" if ref.isdigit() else ref


class LoteInyeccionNoEncontradoException(Exception):
    """Excepción lanzada cuando no existe ningún registro para el id_inyeccion consultado."""
    def __init__(self, id_inyeccion, message="Reporte de inyección no encontrado"):
        self.id_inyeccion = id_inyeccion
        self.message = message
        super().__init__(self.message)


class ProgramacionNoEncontradaException(Exception):
    """Excepción lanzada cuando no existe ninguna ProgramacionInyeccion para el id_programacion consultado."""
    def __init__(self, id_programacion):
        self.id_programacion = id_programacion
        self.message = "Programación no encontrada"
        super().__init__(self.message)

# Inyección: jornada normal 06:00-18:00 (12h de span, incluye horas extra habituales).
# Confirmado por el usuario el 2026-08-03 tras auditoría de horas mal digitadas.
DURACION_MAXIMA_TURNO_HORAS = 12

_MESES_ES = (
    'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
    'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'
)


class InyeccionService:

    @staticmethod
    def validar_duracion_turno(segundos_delta) -> None:
        """
        Rechaza duraciones de turno imposibles para Inyección (jornada 06:00-18:00
        con horas extra, sin turno nocturno estándar). Debe llamarse con el delta
        CRUDO hora_fin-hora_inicio (segundos), antes de aplicar clamp/wraparound —
        una duración negativa también se considera inválida, no se debe silenciar
        a 0 como hacía el código anterior.
        """
        limite_seg = DURACION_MAXIMA_TURNO_HORAS * 3600
        if segundos_delta < 0 or segundos_delta > limite_seg:
            horas = segundos_delta / 3600.0
            raise TurnoInvalidoException(
                horas_calculadas=horas,
                horas_maximas=DURACION_MAXIMA_TURNO_HORAS,
            )

    @staticmethod
    def _formatear_fecha_es(fecha_dt):
        """DD/Mes/YYYY en español a partir de un datetime ya en hora Colombia (naive)."""
        if not fecha_dt:
            return ''
        return f"{fecha_dt.day:02d}/{_MESES_ES[fecha_dt.month - 1]}/{fecha_dt.year}"

    @staticmethod
    def obtener_pendientes_validacion():
        """
        Consulta los lotes de Inyección en estado PENDIENTE/FINALIZADO listos
        para validación y arma el DTO que consume el frontend.
        """
        try:
            sql = """
                SELECT
                    i.id as id_sql,
                    i.id_inyeccion,
                    i.fecha_inicia as fecha,
                    i.fecha_fin,
                    i.id_codigo,
                    i.responsable,
                    i.maquina,
                    i.molde,
                    i.cavidades,
                    i.estado,
                    i.cantidad_real,
                    i.hora_inicio,
                    i.hora_termina as hora_fin,
                    i.cant_contador,
                    i.almacen_destino,
                    i.orden_produccion,
                    i.observaciones,
                    COALESCE(i.pnc_total, 0) as pnc_total,
                    i.pnc_detalle,
                    i.peso_lote,
                    i.entrada,
                    i.salida
                FROM db_inyeccion i
                WHERE i.estado IN ('PENDIENTE', 'FINALIZADO')
                ORDER BY i.fecha_inicia DESC
            """
            pendientes = db.session.execute(text(sql)).mappings().all()

            data = []
            import re

            def _clean_num(val):
                if val is None:
                    return 0
                if isinstance(val, (int, float)):
                    return val
                clean = re.sub(r'[^0-9.]', '', str(val))
                return float(clean) if clean else 0

            for p in pendientes:
                buenas = _clean_num(p['cantidad_real'])
                pnc = _clean_num(p['pnc_total'])

                data.append({
                    'id_sql': p['id_sql'],
                    'id_inyeccion': p['id_inyeccion'],
                    'fecha': p['fecha'].isoformat() if p['fecha'] else '',
                    'fecha_display': InyeccionService._formatear_fecha_es(p['fecha']),
                    'hora_inicio': p['hora_inicio'] or (p['fecha'].strftime('%H:%M') if p['fecha'] else ''),
                    'hora_fin': p['hora_fin'] or (p['fecha_fin'].strftime('%H:%M') if p.get('fecha_fin') else ''),
                    'id_codigo': p['id_codigo'],
                    'responsable': p['responsable'],
                    'cantidad_inyectada': buenas + pnc,  # total bruto inyectado
                    'cantidad_real': buenas,  # buenas reportadas
                    'pnc': pnc,
                    'revueltos': 0,
                    'wip': 0,
                    'maquina': p['maquina'],
                    'molde': p['molde'],
                    'cavidades': p['cavidades'],
                    'cant_contador': _clean_num(p['cant_contador']),
                    'almacen_destino': p['almacen_destino'],
                    'orden_produccion': p['orden_produccion'],
                    'observaciones': p['observaciones'],
                    'pnc_detalle': p['pnc_detalle'],
                    'entrada': _clean_num(p['entrada']),
                    'salida': _clean_num(p['salida']),
                    'peso_lote': _clean_num(p['peso_lote'])
                })

            return {'success': True, 'data': data}

        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error en InyeccionService.obtener_pendientes_validacion: {e}")
            return {'success': False, 'error': str(e)}

    @staticmethod
    def _clasificar_pnc_tipado(pnc_items):
        """
        Clasifica una lista de {cantidad, criterio} en las 5 columnas tipadas de
        db_pnc_inyeccion (quemado_manchado, incompleto_falta_llenado,
        rebaba_excesiva, burbuja_porosidad, deformacion_rechupado).

        Única implementación de este if/elif, reutilizada por todas las rutas
        de escritura de Inyección para que clasifiquen exactamente igual.

        Devuelve (dict_columnas_tipadas, total).
        """
        from backend.utils.formatters import to_float

        acumulado = {
            'quemado_manchado': 0.0,
            'incompleto_falta_llenado': 0.0,
            'rebaba_excesiva': 0.0,
            'burbuja_porosidad': 0.0,
            'deformacion_rechupado': 0.0,
        }
        for p_def in pnc_items or []:
            c_pnc = to_float(p_def.get('cantidad') or 0)
            if c_pnc <= 0:
                continue
            crit = str(p_def.get('criterio') or p_def.get('motivo') or 'Otro').lower().strip()

            if any(x in crit for x in ["quemado", "mancha", "contaminado"]):
                acumulado['quemado_manchado'] += c_pnc
            elif any(x in crit for x in ["incompleto", "escaso", "falta", "llenado"]):
                acumulado['incompleto_falta_llenado'] += c_pnc
            elif "rebaba" in crit:
                acumulado['rebaba_excesiva'] += c_pnc
            elif any(x in crit for x in ["burbuja", "porosidad"]):
                acumulado['burbuja_porosidad'] += c_pnc
            elif any(x in crit for x in ["deform", "rechupe", "chupado", "hundido", "flujo"]):
                acumulado['deformacion_rechupado'] += c_pnc
            else:
                acumulado['deformacion_rechupado'] += c_pnc

        return acumulado, sum(acumulado.values())

    @staticmethod
    def _criterio_texto_tipado(tipado):
        """Texto legible compuesto a partir del dict de columnas tipadas (para el campo `criterio`)."""
        return (
            f"Quemado: {int(tipado['quemado_manchado'])}, "
            f"Falta Llenado: {int(tipado['incompleto_falta_llenado'])}, "
            f"Rebaba: {int(tipado['rebaba_excesiva'])}, "
            f"Burbujas: {int(tipado['burbuja_porosidad'])}, "
            f"Deformacion: {int(tipado['deformacion_rechupado'])}"
        )

    @staticmethod
    def _sincronizar_campos_registro(registro, item, turno, fecha_dt, id_cod, maquina, responsable, usuario_activo):
        """
        Copia los campos de `item`/`turno` a los atributos del `registro`
        (ProduccionInyeccion), incluyendo el estado inicial 'PENDIENTE' del
        lote. Extraído de registrar_lote (hallazgo p5 de la auditoría) sin
        cambiar el orden ni las fuentes de cada campo -- caracterizado por
        tests/test_registrar_lote_campos_tiempos_pnc.py::TestSincronizacionCampos.

        Devuelve (cant_real, pnc_val): valores que el resto de registrar_lote
        (cálculo de tiempos y PNC) necesita después de esta sincronización.
        """
        from backend.utils.formatters import to_float, to_int
        from backend.services.pnc_service import pnc_service

        # El lote de producción siempre nace 'PENDIENTE' de auditoría.
        # `responsable` se asigna también al crear (antes solo se asignaba al
        # actualizar, y los lotes nuevos quedaban con responsable NULL — sin
        # dueño al que atribuir después la merma).
        registro.responsable    = responsable
        registro.finalizado_por = usuario_activo or 'SISTEMA'
        registro.estado         = 'PENDIENTE'
        registro.id_codigo      = id_cod
        registro.maquina        = maquina
        registro.fecha_inicia   = datetime.combine(fecha_dt, datetime.min.time())
        registro.departamento   = 'Inyeccion'

        num_cavidades = to_int(item.get('no_cavidades') or item.get('cavidades') or 1)
        cant_real     = to_int(item.get('cantidad_real') or 0)

        registro.cantidad_real = cant_real
        registro.cavidades     = num_cavidades
        # Texto, no to_int(): el código real de molde no es numérico
        # ('5002A', '9304 moneda') -- to_int() lo habría corrompido a 0 en
        # silencio, sin error. Ver migrate_molde_texto.py.
        registro.molde         = str(item.get('molde') or '').strip() or None

        disparos = to_float(item.get('cant_contador') or item.get('disparos') or 0)
        registro.cant_contador      = to_int(disparos)
        registro.produccion_teorica = to_float(item.get('produccion_teorica') or (disparos * num_cavidades))
        registro.peso_bujes         = to_float(item.get('peso_bujes') or 0)
        registro.peso_lote          = str(round(cant_real * registro.peso_bujes, 4))

        registro.observaciones   = item.get('observaciones') or ''
        registro.hora_llegada    = item.get('hora_llegada') or item.get('horaLlegada') or turno.get('hora_llegada')
        registro.hora_inicio     = item.get('hora_inicio') or turno.get('hora_inicio')
        registro.hora_termina    = item.get('hora_fin') or item.get('hora_termina') or turno.get('hora_fin') or turno.get('hora_termina')
        registro.almacen_destino = item.get('almacen_destino') or turno.get('almacen_destino', 'POR PULIR')
        registro.codigo_ensamble = item.get('codigo_ensamble')
        registro.orden_produccion = item.get('orden_produccion') or turno.get('orden_produccion')

        pnc_val = to_int(item.get('pnc') or item.get('pnc_total') or 0)
        registro.pnc_total   = pnc_val
        pnc_det = item.get('criterio_pnc') or item.get('pnc_detalle')
        registro.pnc_detalle = pnc_service.normalizar_criterio(pnc_det, "inyeccion") if pnc_det else None

        registro.entrada = str(to_float(item.get('entrada') or turno.get('entrada_manual') or 0))
        registro.salida  = str(to_float(item.get('salida') or turno.get('salida_manual') or 0))

        return cant_real, pnc_val

    @staticmethod
    def _calcular_tiempos_lote(registro, fecha_dt, cant_real):
        """
        Calcula duración neta, descuento de pausas programadas y métricas de
        tiempo del `registro` a partir de sus horas ya sincronizadas
        (registro.hora_inicio / registro.hora_termina). Si no hay ambas horas,
        no hace nada (el registro queda con los defaults de la columna).

        Extraído de registrar_lote (hallazgo p5) sin cambiar comportamiento --
        caracterizado por tests/test_registrar_lote_campos_tiempos_pnc.py::
        TestCalculoTiemposYPausas, incluido el guard de duración imposible
        (TurnoInvalidoException, que SIEMPRE se re-lanza) y el resto de
        errores de parseo, que solo se loguean (best-effort, no aborta el lote).
        """
        from backend.utils.formatters import calcular_metricas_inyeccion
        from backend.services.pausas_service import PausasService

        h_inicio = registro.hora_inicio
        h_fin = registro.hora_termina
        if not (h_inicio and h_fin):
            return

        try:
            hi_h, hi_m = h_inicio.split(':')
            hf_h, hf_m = h_fin.split(':')

            dt_inicio = datetime(fecha_dt.year, fecha_dt.month, fecha_dt.day, int(hi_h), int(hi_m), 0)
            dt_fin = datetime(fecha_dt.year, fecha_dt.month, fecha_dt.day, int(hf_h), int(hf_m), 0)

            diff = dt_fin - dt_inicio
            segundos = int(diff.total_seconds())
            if segundos < 0:
                segundos += 86400  # Cruce medianoche

            # Barrera arquitectónica: rechaza duraciones imposibles (típico
            # error de digitar 6:50 en vez de 18:50) antes de persistir nada.
            InyeccionService.validar_duracion_turno(segundos)

            descuento_info = PausasService.calcular_descuento_pausas_programadas(dt_inicio, dt_fin)
            segundos_descuento = descuento_info['segundos_descuento']
            segundos_netos = max(0, segundos - segundos_descuento)

            registro.duracion_segundos = segundos_netos
            registro.tiempo_total_minutos, registro.segundos_por_unidad = calcular_metricas_inyeccion(segundos_netos, cant_real)

            if descuento_info['detalle']:
                payload = {
                    "descuento_programado_min": round(segundos_descuento / 60.0, 2),
                    "detalle": descuento_info['detalle']
                }
                tag = f"[AUTO_BREAK]{json.dumps(payload, ensure_ascii=False)}[/AUTO_BREAK]"
                obs = (registro.observaciones or "")
                if "[AUTO_BREAK]" in obs and "[/AUTO_BREAK]" in obs:
                    pre = obs.split("[AUTO_BREAK]")[0]
                    post = obs.split("[/AUTO_BREAK]")[-1]
                    registro.observaciones = (pre + tag + post).strip()
                else:
                    registro.observaciones = (obs + "\n" + tag).strip() if obs else tag

            registro.fecha_inicia = dt_inicio
            registro.fecha_fin = dt_fin
        except TurnoInvalidoException:
            raise
        except Exception as e_time:
            logger.warning(f"Error calculando tiempos inyeccion: {e_time}")

    @staticmethod
    def _procesar_pnc_item(registro, item, id_iny, id_cod, codigo_raw, pnc_val, pnc_list_global, responsable):
        """
        Reemplaza (delete + insert) la fila PncInyeccion de este item con el
        desglose tipado de `pnc_list_global` que le corresponde por código; si
        no hay desglose detallado, cae al fallback de `pnc_val` crudo
        clasificado como deformacion_rechupado.

        Extraído de registrar_lote (hallazgo p5) sin cambiar comportamiento --
        caracterizado por tests/test_registrar_lote_campos_tiempos_pnc.py::TestLogicaPnc.
        """
        from backend.utils.formatters import normalizar_codigo

        # Registro de PNC detallado. db_pnc_inyeccion.id_codigo se persiste
        # SIN prefijo (blindaje @validates en el modelo), así que el filtro/
        # comparación debe hacerse contra la forma normalizada, no id_cod crudo.
        id_cod_sin_prefijo = normalizar_codigo(id_cod)
        db.session.query(PncInyeccion).filter_by(id_inyeccion=id_iny, id_codigo=id_cod_sin_prefijo).delete()

        pnc_items_para_este_codigo = [
            p for p in pnc_list_global
            if p.get('codigo') == codigo_raw or normalizar_codigo(p.get('codigo')) == id_cod_sin_prefijo
        ]

        tipado, total_pnc_detallado = InyeccionService._clasificar_pnc_tipado(pnc_items_para_este_codigo)

        # `responsable` es el operario que produjo la merma; `validado_por`
        # queda NULL a propósito: este lote todavía no ha sido auditado.
        if total_pnc_detallado > 0:
            nuevo_pnc = PncInyeccion(
                id_pnc_inyeccion=uuid.uuid4().hex[:8],
                id_inyeccion=id_iny,
                id_codigo=id_cod,
                cantidad=total_pnc_detallado,
                criterio=InyeccionService._criterio_texto_tipado(tipado),
                codigo_ensamble=registro.codigo_ensamble,
                responsable=responsable,
                **tipado
            )
            db.session.add(nuevo_pnc)
            registro.pnc_total = int(round(total_pnc_detallado))
            registro.pnc_detalle = nuevo_pnc.criterio
        elif pnc_val > 0:
            nuevo_pnc = PncInyeccion(
                id_pnc_inyeccion=uuid.uuid4().hex[:8],
                id_inyeccion=id_iny,
                id_codigo=id_cod,
                cantidad=float(pnc_val),
                criterio=registro.pnc_detalle or 'Otro',
                codigo_ensamble=registro.codigo_ensamble,
                responsable=responsable,
                deformacion_rechupado=float(pnc_val)
            )
            db.session.add(nuevo_pnc)

    @staticmethod
    def _procesar_pnc_huerfanas(items, pnc_list, id_iny_lote):
        """
        PNC "huérfanas" del modal de cierre: entradas de pnc_list cuyo código
        no corresponde a ningún item de este lote (p.ej. PNC de una referencia
        distinta a las que se están inyectando). _procesar_pnc_item ya procesó
        y clasificó todo lo que sí coincide con algún item del lote --
        reprocesarlo aquí lo duplicaría, por eso se filtra explícitamente por
        código.

        Extraído de registrar_lote (hallazgo p5) sin cambiar comportamiento --
        caracterizado por tests/test_registrar_lote_campos_tiempos_pnc.py::
        TestLogicaPnc::test_pnc_huerfana_con_codigo_fuera_del_lote_se_guarda_bajo_id_inyeccion_del_lote.
        """
        from backend.utils.formatters import normalizar_codigo

        codigos_items_lote = {
            normalizar_codigo(it.get('codigo_producto') or it.get('id_codigo'))
            for it in items
        }
        pnc_huerfanos_por_codigo = {}
        for p in pnc_list:
            cod_norm = normalizar_codigo(p.get('codigo'))
            if cod_norm and cod_norm not in codigos_items_lote:
                pnc_huerfanos_por_codigo.setdefault(cod_norm, []).append(p)

        if not pnc_huerfanos_por_codigo:
            return

        logger.info(f" 🚩 Procesando PNC huérfanas (código fuera del lote) para {id_iny_lote}: {list(pnc_huerfanos_por_codigo.keys())}")

        for cod_norm, defs in pnc_huerfanos_por_codigo.items():
            db.session.query(PncInyeccion).filter_by(id_inyeccion=id_iny_lote, id_codigo=cod_norm).delete()

            tipado_h, total_h = InyeccionService._clasificar_pnc_tipado(defs)
            if total_h > 0:
                db.session.add(PncInyeccion(
                    id_pnc_inyeccion=uuid.uuid4().hex[:8],
                    id_inyeccion=id_iny_lote,
                    id_codigo=cod_norm,
                    cantidad=total_h,
                    criterio=InyeccionService._criterio_texto_tipado(tipado_h),
                    **tipado_h
                ))

    @staticmethod
    def _generar_y_subir_pdf_lote(turno, items):
        """
        Genera el PDF del lote y lo sube a Drive (reunión 2026-08-25).

        Reemplaza a la versión anterior (_generar_pdf_lote), que generaba el
        archivo, tenía la subida deshabilitada por comentario explícito, y lo
        BORRABA en el finally -- reportando éxito de un archivo que ya no
        existía en ningún lado. Ahora:
          - Solo se borra el local si la subida a Drive fue exitosa.
          - El estado devuelto es real: 'subido' / 'generado_no_subido' / 'fallido'.
          - Se llama desde validar_lote (no desde registrar_lote): antes de
            validar, cantidad_real/pnc_total no están auditados, así que un
            PDF generado en ese punto reportaría cifras provisionales.

        Resiliente a propósito: el llamador la invoca DESPUÉS del commit de
        la validación, fuera de esa transacción -- un fallo aquí (Drive caído,
        cuota, credenciales) nunca debe deshacer una validación ya confirmada.

        Retorna dict: {'status': 'subido'|'generado_no_subido'|'fallido',
                        'url': str|None, 'error': str|None}
        """
        local_file = None
        try:
            from backend.utils.report_service import PDFGenerator

            maquina = str(turno.get('maquina') or 'S-M').replace(" ", "-")
            fecha_raw = str(turno.get('fecha_inicio') or datetime.now().strftime('%Y-%m-%d'))
            fecha_clean = fecha_raw.split(' ')[0].replace('/', '-')
            op = str(turno.get('orden_produccion') or 'S-OP').replace(" ", "-")
            nombre_archivo = f"{fecha_clean}_{op}_{maquina}.pdf".replace(" ", "_")

            import re
            nombre_archivo = re.sub(r'[\\/*?:"<>|]', "", nombre_archivo)
            tmp_path = os.path.join(os.getcwd(), "temp_reports")
            if not os.path.exists(tmp_path):
                os.makedirs(tmp_path)

            local_file = os.path.join(tmp_path, nombre_archivo)

            if not PDFGenerator.generar_reporte_inyeccion_lote(turno, items, local_file):
                return {'status': 'fallido', 'url': None, 'error': 'No se pudo generar el archivo PDF'}

            logger.debug(f" ✅ PDF generado localmente: {nombre_archivo}")

            try:
                from backend.services.drive_service import DriveService
                url = DriveService.subir_archivo(local_file, nombre_archivo)
                os.remove(local_file)  # ya está a salvo en Drive
                logger.info(f" ✅ PDF subido a Drive: {nombre_archivo} -> {url}")
                return {'status': 'subido', 'url': url, 'error': None}
            except Exception as e_drive:
                # No se borra: el archivo queda en disco (efímero en Render,
                # pero al menos no se pierde antes de saber si se pudo subir).
                logger.error(f" ⚠️ PDF generado pero NO subido a Drive ({nombre_archivo}): {e_drive}")
                return {'status': 'generado_no_subido', 'url': None, 'error': str(e_drive)}

        except Exception as e:
            logger.error(f" ❌ ERROR CRÍTICO generando el PDF de validación: {e}")
            if local_file and os.path.exists(local_file):
                try:
                    os.remove(local_file)
                except Exception:
                    pass
            return {'status': 'fallido', 'url': None, 'error': str(e)}

    @staticmethod
    def _construir_turno_items_para_pdf(registros_iny):
        """
        Arma (turno, items) para PDFGenerator.generar_reporte_inyeccion_lote
        a partir de los registros YA CERRADOS de ProduccionInyeccion (post-
        validación) -- no del payload crudo, que en validar_lote solo trae el
        desglose de PNC, no el lote completo.

        Mapeo de 'buenas' (cantidad neta): PDFGenerator calcula
        buenas = cantidad_real - pnc cuando no se le da manual_buenas. Como
        reg.cantidad_real YA es el neto (pnc se trackea aparte en
        reg.pnc_total), hay que pasarlo como manual_buenas explícito -- si no,
        el PDF restaría el PNC dos veces.
        """
        primero = registros_iny[0]
        turno = {
            'maquina': primero.maquina,
            'fecha_inicio': primero.fecha_inicia.strftime('%Y-%m-%d') if primero.fecha_inicia else '',
            'orden_produccion': primero.orden_produccion,
            'responsable': primero.responsable,
            'hora_inicio': primero.hora_inicio or '',
            'hora_termina': datetime.now().strftime('%H:%M'),
            'entrada_manual': float(primero.entrada or 0),
            'salida_manual': float(primero.salida or 0),
            'observaciones': primero.observaciones or '',
            'almacen_destino': primero.almacen_destino or '',
        }
        items = [{
            'codigo_producto': r.id_codigo,
            'no_cavidades': r.cavidades,
            'disparos': r.cant_contador,
            'cantidad_real': r.cantidad_real,
            'manual_buenas': r.cantidad_real,  # ver docstring: evita restar PNC dos veces
            'pnc': r.pnc_total,
            'peso_bujes': r.peso_bujes,
            'observaciones': r.observaciones,
        } for r in registros_iny]
        return turno, items

    @staticmethod
    def registrar_lote(data, usuario_activo):
        """
        Orquesta el registro de un lote de PRODUCCIÓN de Inyección: por cada item
        sincroniza o crea su fila en ProduccionInyeccion, calcula tiempos/PNC y
        confirma la transacción. El lote queda en 'PENDIENTE'.

        El PDF de validación ya NO se genera aquí (reunión 2026-08-25) -- se
        movió a validar_lote, porque antes de validar cantidad_real/pnc_total
        no están auditados contra PNC. Ver _generar_y_subir_pdf_lote.

        Esta ruta NO valida. El cierre de lote —firma de auditoría, descuento de
        BOM, acreditación de `por_pulir` y propagación FIFO a cubetas— vive
        exclusivamente en `validar_lote` (POST /api/inyeccion/validar/<id>).
        Antes existía aquí un camino paralelo activado por `turno.es_validacion`
        que escribía la merma de pulido dentro de db_pnc_inyeccion y firmaba
        `validado_por` con el operario del formulario; se eliminó por completo.

        Puede lanzar ValueError, OwnershipMismatchException o
        TurnoInvalidoException — el controlador las traduce a códigos HTTP.
        Cualquier excepción hace rollback antes de propagar, para no dejar la
        sesión de SQLAlchemy colgada (pool Postgres).
        """
        from backend.utils.formatters import (
            preservar_o_normalizar_prefijo, resolver_operario,
            normalizar_codigo_sin_prefijo, sql_expr_codigo_sin_prefijo_fr,
        )

        turno = data.get('turno', {})
        items = data.get('items', [])
        if not items:
            raise ValueError('No hay items para registrar')

        ahora_col = get_colombia_time()
        fecha_str = turno.get('fecha_inicio', '')
        try:
            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d').date() if fecha_str else ahora_col.date()
        except Exception:
            fecha_dt = ahora_col.date()

        maquina = str(turno.get('maquina', '')).strip()
        id_iny_lote = turno.get('id_inyeccion') or f"INY-{uuid.uuid4().hex[:8].upper()}"

        # Filas ORM tocadas en este lote, para armar el DTO de respuesta DESPUÉS
        # del commit (con id_sql y valores ya definitivos). Se guardan las
        # referencias a los objetos, no una copia — sus atributos siguen
        # mutando durante el resto del loop hasta el cierre de cada item.
        registros_procesados = []

        try:
            # --- Resolución batch del registro existente por item (hallazgo p6
            # de auditoría: antes eran hasta 3 queries POR ITEM -- 10-60 queries
            # en un lote de 10-20 códigos). Se resuelven los 3 niveles de
            # prioridad (id_sql -> id_inyeccion+código -> máquina+responsable+
            # código+fecha+estado) en 3 queries TOTALES para todo el lote, no
            # por item. Comportamiento caracterizado y protegido por
            # tests/test_registrar_lote.py y tests/test_normalizacion_codigo_
            # sql_vs_python.py -- cualquier cambio aquí debe seguir pasando esos
            # tests, en particular:
            #   - la prioridad tier1 > tier2 > tier3 no se invierte;
            #   - el desempate de tier3 es "la fila EN_PROCESO más reciente";
            #   - dos items del MISMO lote que resuelven a la misma fila se
            #     autodeduplican (el código original lo lograba por el
            #     autoflush de SQLAlchemy antes de cada query en vivo; aquí se
            #     replica registrando cada fila resuelta/creada en los mapas
            #     ANTES de procesar el siguiente item);
            #   - la normalización SQL de código (quita 'FR-' en cualquier
            #     posición) y la de Python (solo como prefijo) NO son
            #     intercambiables, por eso el valor normalizado se pide como
            #     columna calculada (expr_cod.label('cod_norm')) en vez de
            #     re-derivarlo en Python sobre el código crudo de cada fila.
            expr_cod = sql_expr_codigo_sin_prefijo_fr(ProduccionInyeccion.id_codigo)
            responsable_busqueda = resolver_operario(turno.get('responsable'))

            claves_por_item = []
            ids_sql = set()
            ids_iny_tier2 = set()
            for item in items:
                id_sql = item.get('id_sql') or item.get('id')
                id_iny = item.get('id_inyeccion') or id_iny_lote
                codigo_raw = item.get('codigo_producto') or item.get('id_codigo')
                # db_inyeccion persiste la referencia tal como la reportó la planta:
                # ni normalizar_codigo() (que arranca cualquier prefijo, pisando
                # 'MT-'/'CAR-') ni la vieja inyección automática de 'FR-'.
                id_cod = preservar_o_normalizar_prefijo(codigo_raw)
                # Clave de BÚSQUEDA tolerante al prefijo: las filas abiertas por
                # mes_iniciar_trabajo antes de retirar la inyección de 'FR-' viven
                # como 'FR-9843' y las nuevas como '9843'. Comparar en crudo crearía
                # una fila duplicada en vez de cerrar el lote ya abierto.
                cod_lookup = normalizar_codigo_sin_prefijo(id_cod)
                claves_por_item.append({
                    'item': item, 'id_sql': id_sql, 'id_iny': id_iny,
                    'id_cod': id_cod, 'cod_lookup': cod_lookup,
                })
                if id_sql:
                    ids_sql.add(id_sql)
                if id_iny:
                    ids_iny_tier2.add(id_iny)

            mapa_tier1 = {}
            if ids_sql:
                for fila in db.session.query(ProduccionInyeccion).filter(
                    ProduccionInyeccion.id.in_(ids_sql)
                ).all():
                    mapa_tier1[fila.id] = fila

            mapa_tier2 = {}
            if ids_iny_tier2:
                for fila, cod_norm in db.session.query(
                    ProduccionInyeccion, expr_cod.label('cod_norm')
                ).filter(
                    ProduccionInyeccion.id_inyeccion.in_(ids_iny_tier2)
                ).all():
                    mapa_tier2.setdefault((fila.id_inyeccion, cod_norm), fila)

            codigos_tier3 = {c['cod_lookup'] for c in claves_por_item if not c['id_sql']}
            mapa_tier3 = {}
            if codigos_tier3:
                filas_tier3 = db.session.query(
                    ProduccionInyeccion, expr_cod.label('cod_norm')
                ).filter(
                    ProduccionInyeccion.maquina == maquina,
                    ProduccionInyeccion.responsable == responsable_busqueda,
                    expr_cod.in_(codigos_tier3),
                    db.func.date(ProduccionInyeccion.fecha_inicia) == fecha_dt,
                    ProduccionInyeccion.estado == 'EN_PROCESO'
                ).order_by(ProduccionInyeccion.id.desc()).all()
                for fila, cod_norm in filas_tier3:
                    mapa_tier3.setdefault(cod_norm, fila)

            for clave in claves_por_item:
                item = clave['item']
                id_sql = clave['id_sql']
                id_iny = clave['id_iny']
                id_cod = clave['id_cod']
                cod_lookup = clave['cod_lookup']

                # 1. Búsqueda robusta del registro existente (evitar duplicados),
                # resuelta contra los mapas precargados arriba.
                registro = None
                if id_sql:
                    registro = mapa_tier1.get(id_sql)

                if not registro and id_iny:
                    registro = mapa_tier2.get((id_iny, cod_lookup))

                if not registro and not id_sql:
                    registro = mapa_tier3.get(cod_lookup)

                # Guard de ownership y asignación de autoría. usuario_activo proviene
                # exclusivamente de la identidad autenticada (JWT/sesión) — nunca se
                # rellena con datos autorreportados del payload, para que
                # finalizado_por no pueda ser falsificado.
                responsable_input = turno.get('responsable') or data.get('responsable')
                responsable = AuditService.resolver_y_validar_propietario(registro, responsable_input)

                if not registro:
                    logger.debug(f"🆕 [Inyeccion] Creando nuevo registro: {id_iny} - {id_cod}")
                    registro = ProduccionInyeccion(id_inyeccion=id_iny, id_codigo=id_cod)
                    db.session.add(registro)
                else:
                    logger.debug(f"🔄 [Inyeccion] Actualizando registro existente (ID SQL: {registro.id})")

                # Autodeduplicación dentro del MISMO lote: si otro item más
                # adelante en este mismo `items` comparte id_iny+cod_lookup,
                # debe encontrar esta fila (recién creada o recién resuelta) en
                # vez de crear una segunda. El código pre-batch lo lograba vía
                # autoflush de SQLAlchemy antes de cada query en vivo; con los
                # mapas precargados hay que registrar la fila explícitamente
                # (ver tests/test_registrar_lote.py::test_dos_items_del_mismo_lote_...).
                # Solo tier2 aplica: tier3 exige estado=='EN_PROCESO', y unas
                # líneas más abajo este registro pasa a 'PENDIENTE'.
                if id_iny:
                    mapa_tier2[(id_iny, cod_lookup)] = registro

                # --- Sincronización de campos, cálculo de tiempos y PNC del item ---
                # (extraído a helpers propios -- hallazgo p5 de la auditoría; ver
                # tests/test_registrar_lote_campos_tiempos_pnc.py)
                cant_real, pnc_val = InyeccionService._sincronizar_campos_registro(
                    registro, item, turno, fecha_dt, id_cod, maquina, responsable, usuario_activo
                )
                InyeccionService._calcular_tiempos_lote(registro, fecha_dt, cant_real)
                InyeccionService._procesar_pnc_item(
                    registro, item, id_iny, id_cod, codigo_raw, pnc_val,
                    data.get('pnc_list', []), responsable
                )

                registros_procesados.append(registro)

            # --- Confirmar transacción SQL ---
            id_prog = turno.get('id_programacion')
            if id_prog and id_prog != 'LEGACY':
                db.session.query(ProgramacionInyeccion).filter_by(id=id_prog).update({'estado': 'COMPLETADO'})

            InyeccionService._procesar_pnc_huerfanas(items, data.get('pnc_list', []), id_iny_lote)

            db.session.commit()
            logger.info(f" ✅ Lote {id_iny_lote} (PENDIENTE) procesado con {len(items)} items.")

        except Exception as e:
            db.session.rollback()
            if not isinstance(e, (OwnershipMismatchException, TurnoInvalidoException)):
                logger.error(f" ❌ Error en InyeccionService.registrar_lote: {e}")
            raise

        # NOTA (reunión 2026-08-25): el PDF ya NO se genera aquí -- se movió a
        # validar_lote. Antes de validar, cantidad_real/pnc_total no están
        # auditados contra PNC, así que un PDF generado en este punto
        # reportaría cifras provisionales bajo la apariencia de definitivas.

        # DTO por item, construido DESPUÉS del commit para reflejar valores
        # definitivos (id_sql autoincremental incluido). Contrato explícito:
        # el frontend ya no debe adivinar qué se persistió a partir del payload
        # que él mismo envió — el server es la fuente de verdad de operario,
        # responsable, contador y desglose de PNC de cada fila del lote.
        items_resultado = [{
            'id_sql':          r.id,
            'id_inyeccion':    r.id_inyeccion,
            'codigo_producto': r.id_codigo,
            'responsable':     r.responsable,
            'cantidad_real':   r.cantidad_real,
            'cant_contador':   r.cant_contador,
            'pnc_total':       r.pnc_total,
            'pnc_detalle':     r.pnc_detalle,
        } for r in registros_procesados]

        return {
            'success': True,
            'mensaje': 'Lote registrado exitosamente. Queda PENDIENTE de validación.',
            'id_inyeccion': id_iny_lote,
            'items': items_resultado
        }

    @staticmethod
    def validar_lote(id_inyeccion, data, usuario_activo):
        """
        Valida (cierra) un lote completo de Inyección: cruza PNC estructurado por
        item, descuenta materia prima según BOM, acredita `por_pulir` en Producto
        y marca cada registro como CERRADO. Único punto de entrada de validación
        del módulo — /api/inyeccion/lote ya no procesa cierres.

        Trazabilidad de las mermas que genera:
          - `validado_por` (cabecera y ambas tablas de PNC) = `usuario_activo`,
            la identidad autenticada; el payload no participa.
          - `PncInyeccion.responsable` = operario de inyección del lote.
          - `PncPulido.responsable`    = `items[].operaria_pulido` del DTO,
            obligatoria cuando el item reporta merma de pulido.

        Transaccional de punta a punta: cualquier fallo hace rollback completo
        antes de propagar, para no dejar registros a medio cerrar ni la sesión
        de SQLAlchemy colgada.

        Puede lanzar LoteInyeccionNoEncontradoException, ValueError,
        ValidadorRequeridoException u OwnershipMismatchException — el
        controlador las traduce a HTTP.
        """
        from backend.utils.formatters import normalizar_codigo_sin_prefijo, to_float, to_int
        from backend.services.bom_service import calcular_descuentos_ensamble
        from backend.services.stock_service import StockService

        items_payload = data.get('items', [])
        # Clave normalizada sin prefijo 'FR-': reg.id_codigo puede traerlo (lote
        # creado vía flujo MES) o no (flujo manual), así que ambos lados del
        # cruce deben comparar en el mismo formato o el lookup falla en silencio.
        items_dict = {
            normalizar_codigo_sin_prefijo(item.get('codigo', '')): item
            for item in items_payload
        }

        # Firma de auditoría: EXCLUSIVAMENTE la identidad autenticada (JWT). No
        # se pasa `data.get('validador')` a propósito — AuditService le da
        # precedencia al payload, y el frontend enviaba ahí el responsable del
        # formulario (el operario de inyección del lote), con lo que
        # `validado_por` terminaba firmando con quien produjo, no con quien
        # auditó. La firma no puede depender de un dato autorreportado.
        validador_actual = AuditService.resolver_y_validar_validador(None, usuario_activo)

        registros_iny = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=id_inyeccion).all()
        if not registros_iny:
            # Fallback a ID numérico de SQL por si el front manda id_sql
            single_reg = db.session.get(ProduccionInyeccion, id_inyeccion)
            if single_reg:
                registros_iny = [single_reg]

        if not registros_iny:
            raise LoteInyeccionNoEncontradoException(id_inyeccion)

        # DTO por código, expuesto en la respuesta para que el frontend pueda
        # confirmar sin ambigüedad qué operario/operaria y qué desglose de PNC
        # quedó realmente auditado en cada referencia del lote.
        items_resultado = []

        try:
            for reg in registros_iny:
                if reg.estado == 'CERRADO':
                    continue

                codigo = normalizar_codigo_sin_prefijo(reg.id_codigo)
                item_payload = items_dict.get(codigo, {})

                # Sobreescritura oficial desde el Payload de Validación
                pnc_inyeccion = float(item_payload.get('pnc_inyeccion', 0) or 0)
                pnc_pulido = to_float(item_payload.get('pnc_pulido') or 0)

                # Buenas/Contador/Cav. editados en la pantalla de Validación:
                # si el payload los trae, son la fuente de verdad para lo que
                # se descuenta de materia prima, se acredita a por_pulir y
                # queda auditado en `cantidad_real` (de ahí sale WO). Sin
                # payload (llamador viejo) se cae al registro original, tal
                # como se comportaba antes.
                buenas_payload = item_payload.get('buenas', None)
                if buenas_payload is not None:
                    buenas_neta = max(0.0, to_float(buenas_payload))
                    cantidad_inyectada = buenas_neta + pnc_inyeccion
                else:
                    cantidad_inyectada = reg.cantidad_real or 0
                    buenas_neta = max(0.0, cantidad_inyectada - pnc_inyeccion)

                disparos_payload = item_payload.get('disparos', None)
                cavidades_payload = item_payload.get('no_cavidades', None)
                # Desglose estructurado que el frontend YA envía por item.
                pnc_list_item = item_payload.get('pnc_list', [])
                pnc_pulido_list_item = item_payload.get('pnc_pulido_list', [])

                # Atribución de personas. El operario de inyección es dato del
                # lote (no del payload, que es autorreportado); la operaria de
                # pulido sí llega en el DTO porque no existe en db_inyeccion —
                # el lote no sabe quién pulió.
                operario_inyeccion = reg.responsable
                operaria_pulido = str(item_payload.get('operaria_pulido') or '').strip()

                total_pulido_item = sum(to_float(p.get('cantidad') or 0) for p in pnc_pulido_list_item)
                hay_pnc_pulido = total_pulido_item > 0 or pnc_pulido > 0
                if hay_pnc_pulido and not operaria_pulido:
                    # Guard de servidor: sin esto la merma de pulido volvería a
                    # quedar sin dueño, que es justo el defecto que se corrige.
                    # El front ya lo exige, pero la regla vive aquí.
                    raise ValueError(
                        f"Debe indicar la operaria de pulido responsable de la merma de {codigo}"
                    )

                # Eliminar registros PNC anteriores creados en validaciones previas para este lote/código
                db.session.query(PncInyeccion).filter_by(id_inyeccion=reg.id_inyeccion, id_codigo=codigo).delete()
                db.session.query(PncPulido).filter_by(id_pulido=reg.id_inyeccion, codigo=codigo).delete()

                tipado, total_tipado = InyeccionService._clasificar_pnc_tipado(pnc_list_item)
                if total_tipado > 0:
                    # Desglose disponible: se clasifica igual que registrar_lote,
                    # llenando las columnas tipadas en vez de dejar solo el agregado.
                    db.session.add(PncInyeccion(
                        id_pnc_inyeccion=uuid.uuid4().hex[:8],
                        id_inyeccion=reg.id_inyeccion,
                        id_codigo=codigo,
                        cantidad=total_tipado,
                        criterio=InyeccionService._criterio_texto_tipado(tipado),
                        codigo_ensamble='AUDITORIA INYECCION',
                        responsable=operario_inyeccion,
                        validado_por=validador_actual,
                        **tipado
                    ))
                elif pnc_inyeccion > 0:
                    # Fallback: sin desglose estructurado en el payload, se registra
                    # el agregado como antes para que la fila no quede huérfana.
                    db.session.add(PncInyeccion(
                        id_pnc_inyeccion=uuid.uuid4().hex[:8],
                        id_inyeccion=reg.id_inyeccion,
                        id_codigo=codigo,
                        cantidad=int(pnc_inyeccion),
                        criterio='PNC Reportado en Validación',
                        codigo_ensamble='AUDITORIA INYECCION',
                        responsable=operario_inyeccion,
                        validado_por=validador_actual
                    ))

                # PNC de Pulido detectado durante esta validación de Inyección
                # (el desglose vive en db_pnc_pulido, no en db_pnc_inyeccion).
                if total_pulido_item > 0:
                    criterio_pulido_str = ", ".join(
                        f"{p.get('criterio') or p.get('motivo') or 'Otro'}: {int(to_float(p.get('cantidad') or 0))}"
                        for p in pnc_pulido_list_item if to_float(p.get('cantidad') or 0) > 0
                    )
                    db.session.add(PncPulido(
                        id_pnc_pulido=uuid.uuid4().hex[:8],
                        id_pulido=reg.id_inyeccion,
                        codigo=codigo,
                        cantidad=total_pulido_item,
                        criterio=criterio_pulido_str or 'PNC Pulido Reportado en Validación de Inyección',
                        responsable=operaria_pulido,
                        validado_por=validador_actual
                    ))
                elif pnc_pulido > 0:
                    # Simétrico al fallback de inyección: la validadora tecleó la
                    # cantidad en la celda sin abrir el modal de criterios.
                    db.session.add(PncPulido(
                        id_pnc_pulido=uuid.uuid4().hex[:8],
                        id_pulido=reg.id_inyeccion,
                        codigo=codigo,
                        cantidad=pnc_pulido,
                        criterio='PNC Pulido Reportado en Validación de Inyección',
                        responsable=operaria_pulido,
                        validado_por=validador_actual
                    ))

                # Flujo Directo de Inventario: Sumamos a por_pulir lo que se inyectó (menos defectuosas PNC)
                buenas_por_pulir = max(0, cantidad_inyectada - pnc_inyeccion)

                es_prueba = '9999' in str(reg.id_inyeccion) or 'PRUEBA' in str(reg.id_inyeccion).upper() or '9999' in str(reg.orden_produccion) or 'PRUEBA' in str(reg.orden_produccion).upper()

                if not es_prueba:
                    # Descontar materia prima según el BOM
                    if cantidad_inyectada > 0:
                        bom_res = calcular_descuentos_ensamble(codigo, cantidad_inyectada)
                        if bom_res.get('success'):
                            for comp in bom_res.get('componentes', []):
                                StockService.registrar_salida(comp['codigo_inventario'], comp['cantidad_total_descontar'], "STOCK_BODEGA")

                    # Actualizar inventario final (Validation is single point of entry para Inyeccion)
                    if buenas_por_pulir > 0:
                        producto = db.session.query(Producto).filter_by(codigo_sistema=codigo).first()
                        if producto:
                            producto.por_pulir = (producto.por_pulir or 0) + buenas_por_pulir
                        else:
                            logger.warning(f"Producto {codigo} no encontrado en db_productos para actualizar por_pulir")
                else:
                    logger.info(f"🧪 [SANDBOX] Reporte {reg.id_inyeccion} validado en MODO PRUEBA. Se ignoró cruce de inventario.")

                # Marcar registro de inyeccion como CERRADO
                reg.validado_por = validador_actual
                reg.estado = 'CERRADO'
                reg.fecha_fin = datetime.now()
                reg.pnc_total = pnc_inyeccion
                # `cantidad_real` queda con la neta (buenas) auditada en esta
                # validación -- antes se dejaba el bruto original del reporte
                # de máquina sin tocar, y WoExportService._lineas_inyeccion
                # sale de esta misma columna para el 'buenas' que sube a WO.
                reg.cantidad_real = buenas_neta
                if disparos_payload is not None:
                    reg.cant_contador = to_int(disparos_payload)
                if cavidades_payload is not None:
                    reg.cavidades = to_int(cavidades_payload)
                if disparos_payload is not None or cavidades_payload is not None:
                    reg.produccion_teorica = to_float(reg.cant_contador) * to_float(reg.cavidades or 1)

                items_resultado.append({
                    'codigo':             codigo,
                    'id_inyeccion':       reg.id_inyeccion,
                    'operario_inyeccion': operario_inyeccion,
                    'operaria_pulido':    operaria_pulido or None,
                    'pnc_inyeccion':      pnc_inyeccion,
                    'pnc_pulido':         total_pulido_item if total_pulido_item > 0 else pnc_pulido,
                    'validado_por':       validador_actual,
                })

            db.session.commit()

        except Exception as e:
            db.session.rollback()
            if not isinstance(e, (OwnershipMismatchException, ValidadorRequeridoException)):
                logger.error(f"❌ Error validando lote {id_inyeccion}: {e}")
            raise

        # Invalidar caché del MES (best-effort, no debe tumbar una validación ya confirmada)
        try:
            from backend.services.programacion_service import ProgramacionService
            ProgramacionService.clear_mes_cache()
        except Exception:
            pass

        # PDF de validación (reunión 2026-08-25): se genera AQUÍ, no en
        # registrar_lote -- ver docstring de _generar_y_subir_pdf_lote. Igual
        # de best-effort: un PDF/Drive caído no debe tumbar una validación ya
        # confirmada y comiteada.
        pdf_resultado = {'status': 'fallido', 'url': None, 'error': 'sin registros para generar el PDF'}
        try:
            if registros_iny:
                turno_pdf, items_pdf = InyeccionService._construir_turno_items_para_pdf(registros_iny)
                pdf_resultado = InyeccionService._generar_y_subir_pdf_lote(turno_pdf, items_pdf)
        except Exception as e_pdf:
            logger.error(f"❌ Error inesperado generando el PDF de validación para {id_inyeccion}: {e_pdf}")
            pdf_resultado = {'status': 'fallido', 'url': None, 'error': str(e_pdf)}

        logger.info(f"✅ [Validación] Lote {id_inyeccion} cerrado y validado correctamente por {validador_actual}.")
        return {
            "success": True,
            "message": f"Lote {id_inyeccion} validado e inventario actualizado",
            "validado_por": validador_actual,
            "items": items_resultado,
            "pdf_status": pdf_resultado['status'],
            "pdf_url": pdf_resultado['url'],
        }

    @staticmethod
    def iniciar_trabajo(data, usuario_activo=None):
        """
        Fase 4 MES: inicia el trabajo en máquina a partir de una programación
        vespertina. Agrupa el montaje completo (misma máquina/fecha/molde),
        propaga la OP de World Office a las cubetas de pedidos, crea el lote
        físico en ProduccionInyeccion y su lote de trazabilidad por referencia.
        Transaccional: cualquier fallo hace rollback completo antes de propagar.

        OP heredada de la programación (reunión 2026-08-25): desde que
        guardar_programacion asigna la OP automáticamente, el operario ya no
        debería teclearla. Precedencia: override explícito del payload (botón
        "editar OP", solo ROL_ADMINS) -> la que ya trae prog_inicial.op_world_office
        (camino normal) -> si ninguna existe, se sigue exigiendo -- cubre
        programaciones creadas antes de este cambio, sin backfill.

        Puede lanzar ValueError, ProgramacionNoEncontradaException u
        OwnershipMismatchException — el controlador las traduce a HTTP.
        """
        from backend.utils.formatters import preservar_o_normalizar_prefijo

        id_prog = data.get('id_programacion')
        if not id_prog:
            raise ValueError("El campo id_programacion es obligatorio")

        prog_inicial = db.session.query(ProgramacionInyeccion).get(id_prog)
        if not prog_inicial:
            raise ProgramacionNoEncontradaException(id_prog)

        op_world_office = data.get('op_world_office') or prog_inicial.op_world_office
        if not op_world_office:
            raise ValueError(
                "Esta programación no tiene OP asignada automáticamente "
                "(fue creada antes del cambio). Indique op_world_office manualmente."
            )

        op_world_office = str(op_world_office).strip().upper()

        # Además de máquina/fecha/molde, se exige la misma OP del montaje que
        # dispara el inicio: dos montajes distintos programados para la misma
        # máquina y fecha pueden compartir letra de molde (ej. uno para el
        # arranque de jornada y otro para las 12) y antes se fusionaban en un
        # solo lote al iniciar cualquiera de los dos -- ver obtener_status_maquina,
        # que ya exigía esta misma coincidencia de OP.
        programaciones_bloque = db.session.query(ProgramacionInyeccion).filter(
            ProgramacionInyeccion.maquina == prog_inicial.maquina,
            ProgramacionInyeccion.fecha == prog_inicial.fecha,
            ProgramacionInyeccion.estado == 'PROGRAMADO',
            ProgramacionInyeccion.molde == prog_inicial.molde,
            ProgramacionInyeccion.op_world_office == prog_inicial.op_world_office
        ).all()

        if prog_inicial not in programaciones_bloque:
            programaciones_bloque.append(prog_inicial)

        id_inyeccion_bloque = f"INY-{uuid.uuid4().hex[:8].upper()}"
        ahora = get_colombia_time()

        logger.debug(f"⚡ Iniciando trabajo para la máquina {prog_inicial.maquina}, Referencias: {len(programaciones_bloque)}")

        # Guard de ownership centralizado con AuditService: puede lanzar
        # OwnershipMismatchException antes de tocar la sesión (no hay nada que
        # rollback-ear todavía), el controlador la traduce a 409.
        operario_inicia = AuditService.resolver_y_validar_propietario(
            None, data.get('responsable') or data.get('operario') or prog_inicial.responsable_planta
        )

        try:
            for prog in programaciones_bloque:
                prog.estado = 'EN_PROCESO'
                prog.op_world_office = op_world_office

                db.session.query(DistribucionOpPedidos).filter(
                    DistribucionOpPedidos.codigo_producto == prog.codigo_sistema,
                    DistribucionOpPedidos.op_world_office.is_(None)
                ).update({DistribucionOpPedidos.op_world_office: op_world_office}, synchronize_session=False)

                # Crear lote en ProduccionInyeccion (db_inyeccion). NOTA:
                # db_programacion guarda el código tal como lo emitió la orden de
                # producción ('9304', 'MT-500'); se propaga INTACTO a db_inyeccion.
                # No se le antepone 'FR-': la división es un dato de la OP, no una
                # deducción del formateador.
                hora_inicio_str = ahora.strftime('%H:%M')
                id_codigo_norm = preservar_o_normalizar_prefijo(prog.codigo_sistema)
                nueva_prod = ProduccionInyeccion(
                    id_inyeccion=id_inyeccion_bloque,
                    fecha_inicia=ahora,
                    id_codigo=id_codigo_norm,
                    responsable=operario_inicia,
                    maquina=prog.maquina,
                    molde=prog.molde,
                    cavidades=prog.cavidades,
                    estado='EN_PROCESO',
                    orden_produccion=op_world_office,
                    observaciones=prog.observaciones,
                    programado_por=prog.responsable_planta,
                    iniciado_por=operario_inicia,
                    hora_inicio=hora_inicio_str,
                    cantidad_real=0,
                    cant_contador=0,
                    almacen_destino='POR PULIR',
                    departamento='Inyeccion'
                )
                db.session.add(nueva_prod)

                # --- MES PASO 1: Crear Lote de Trazabilidad por cada referencia del montaje ---
                id_lote_mes = f"{prog.fecha.strftime('%Y%m%d')}-{prog.maquina.replace(' ', '')}-{op_world_office}-{prog.codigo_sistema}"
                if not db.session.get(TrazabilidadLote, id_lote_mes):
                    lote_traz = TrazabilidadLote(
                        id_lote=id_lote_mes,
                        orden_produccion=op_world_office,
                        id_codigo=id_codigo_norm,
                        maquina=prog.maquina,
                        id_inyeccion=id_inyeccion_bloque,
                        estado_actual='ABIERTO_PRODUCCION',
                        fecha_creacion=ahora,
                        responsable=prog.responsable_planta or "Supervisor",
                        cantidad_inyectada=0,
                        por_pulir=0
                    )
                    db.session.add(lote_traz)
                    logger.debug(f"🟢 [Trazabilidad] Lote MES creado: {id_lote_mes} | OP: {op_world_office} | Ref: {prog.codigo_sistema}")

            db.session.commit()

        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error al iniciar trabajo en el MES: {e}")
            raise

        try:
            from backend.services.programacion_service import ProgramacionService
            ProgramacionService.clear_mes_cache()
        except Exception:
            pass

        logger.info(f"🚀 Trabajo iniciado en bloque en {prog_inicial.maquina}. OP: {op_world_office}. Lote único: {id_inyeccion_bloque}")

        return {
            "success": True,
            "message": f"Trabajo iniciado con éxito en la {prog_inicial.maquina} para {len(programaciones_bloque)} referencias del montaje.",
            "id_inyeccion": id_inyeccion_bloque
        }

    @staticmethod
    def reportar_trabajo(data, usuario_activo=None):
        """
        Fase 4 MES: finalización de turno y reporte de Inyección. Cierra cada
        producto del lote a partir del contador físico (cierres x cavidades),
        calcula tiempos/pausas, sincroniza la trazabilidad y propaga por FIFO
        a las cubetas de pedidos de la OP asociada. Transaccional: cualquier
        fallo hace rollback completo antes de propagar.

        Puede lanzar ValueError, LoteInyeccionNoEncontradoException,
        OwnershipMismatchException o TurnoInvalidoException — el controlador
        las traduce a HTTP.
        """
        from backend.utils.formatters import normalizar_codigo, calcular_metricas_inyeccion
        from backend.services.pausas_service import PausasService

        id_iny = data.get('id_inyeccion')
        cierres = int(data.get('cierres', 0))
        hf_str = data.get('hora_fin')
        hi_str = data.get('hora_inicio')

        if not id_iny:
            raise ValueError("El campo id_inyeccion es obligatorio")

        prods_en_lote = db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.id_inyeccion == id_iny
        ).all()

        if not prods_en_lote:
            raise LoteInyeccionNoEncontradoException(id_iny, "Lote de producción no encontrado")

        # Guard de ownership centralizado con AuditService: puede lanzar
        # OwnershipMismatchException antes de tocar la sesión, el controlador
        # la traduce a 409.
        operario_final = AuditService.resolver_y_validar_propietario(
            prods_en_lote[0],
            data.get('responsable') or data.get('operario')
        )

        ahora_rep = get_colombia_time()
        total_teorica = 0

        try:
            for prod in prods_en_lote:
                cavidades = prod.cavidades or 1
                piezas_inyectadas = cierres * cavidades
                total_teorica += piezas_inyectadas

                if operario_final:
                    prod.responsable = operario_final

                # Hora inicio y fin: primero la del frontend (si viene), sino timestamp del servidor
                if hi_str:
                    prod.hora_inicio = hi_str

                hora_fin_resuelta = hf_str or ahora_rep.strftime('%H:%M')
                prod.hora_termina = hora_fin_resuelta

                fecha_base = prod.fecha_inicia or ahora_rep
                try:
                    if hi_str:
                        h_i, m_i = map(int, hi_str.split(':'))
                        fecha_base = fecha_base.replace(hour=h_i, minute=m_i, second=0, microsecond=0)
                        prod.fecha_inicia = fecha_base

                    h_f, m_f = map(int, hora_fin_resuelta.split(':'))
                    prod.fecha_fin = fecha_base.replace(hour=h_f, minute=m_f, second=0, microsecond=0)

                    # Calcular duración y métricas. Barrera arquitectónica: rechaza
                    # duraciones imposibles (negativas o >12h) antes de persistir.
                    delta_seg = int((prod.fecha_fin - fecha_base).total_seconds())
                    InyeccionService.validar_duracion_turno(delta_seg)

                    descuento_info = PausasService.calcular_descuento_pausas_programadas(fecha_base, prod.fecha_fin)
                    segundos_descuento = descuento_info['segundos_descuento']
                    prod.duracion_segundos = max(0, delta_seg - segundos_descuento)
                    prod.tiempo_total_minutos, prod.segundos_por_unidad = calcular_metricas_inyeccion(prod.duracion_segundos, piezas_inyectadas)

                    if descuento_info['detalle']:
                        payload = {
                            "descuento_programado_min": round(segundos_descuento / 60.0, 2),
                            "detalle": descuento_info['detalle']
                        }
                        tag = f"[AUTO_BREAK]{json.dumps(payload, ensure_ascii=False)}[/AUTO_BREAK]"
                        obs = (prod.observaciones or "")
                        if "[AUTO_BREAK]" in obs and "[/AUTO_BREAK]" in obs:
                            pre = obs.split("[AUTO_BREAK]")[0]
                            post = obs.split("[/AUTO_BREAK]")[-1]
                            prod.observaciones = (pre + tag + post).strip()
                        else:
                            prod.observaciones = (obs + "\n" + tag).strip() if obs else tag
                except TurnoInvalidoException:
                    raise
                except Exception as ex:
                    logger.warning(f"⚠️ Error al calcular tiempos en reportar: {ex}")
                    prod.fecha_fin = ahora_rep

                prod.cantidad_real = piezas_inyectadas
                prod.cant_contador = piezas_inyectadas
                prod.produccion_teorica = piezas_inyectadas
                prod.estado = 'PENDIENTE'

                # MES PASO 2: actualizar cantidad_inyectada en TODOS los lotes de
                # trazabilidad (un montaje multi-referencia genera un lote por
                # referencia, no solo el primero).
                lotes_cierre = db.session.query(TrazabilidadLote).filter_by(
                    id_inyeccion=id_iny,
                    id_codigo=prod.id_codigo
                ).all()
                if lotes_cierre:
                    for lote_cierre in lotes_cierre:
                        lote_cierre.cantidad_inyectada = piezas_inyectadas
                        lote_cierre.por_pulir = piezas_inyectadas
                        logger.debug(f"🔄 [Trazabilidad] Lote {lote_cierre.id_lote} ({prod.id_codigo}) → {piezas_inyectadas} piezas | por_pulir: {piezas_inyectadas}")
                else:
                    lote_generico = db.session.query(TrazabilidadLote).filter_by(id_inyeccion=id_iny).first()
                    if lote_generico:
                        lote_generico.cantidad_inyectada = piezas_inyectadas
                        lote_generico.por_pulir = piezas_inyectadas
                        logger.warning(f"⚠️ [Trazabilidad] Fallback – lote {lote_generico.id_lote} actualizado con {piezas_inyectadas} piezas (por_pulir: {piezas_inyectadas})")

                # 3. Lógica FIFO para cubetas (db_distribucion_op_pedidos)
                op_actual = prod.orden_produccion
                if op_actual and str(op_actual).strip() != 'SIN OP':
                    op_limpia = str(op_actual or '').strip()
                    codigo_limpio = normalizar_codigo(prod.id_codigo)

                    cubetas = db.session.query(DistribucionOpPedidos).filter(
                        DistribucionOpPedidos.op_world_office == op_limpia,
                        DistribucionOpPedidos.codigo_producto == codigo_limpio
                    ).order_by(DistribucionOpPedidos.id_distribucion.asc()).all()

                    piezas_por_repartir = piezas_inyectadas

                    if not cubetas and piezas_por_repartir > 0:
                        pedido_asoc = db.session.query(DistribucionOpPedidos.id_pedido).filter(
                            DistribucionOpPedidos.op_world_office == op_limpia
                        ).first()
                        id_pedido_final = pedido_asoc[0] if (pedido_asoc and pedido_asoc[0]) else f"PED-IMPREVISTO-{op_limpia}"

                        logger.info(f" ⚠️ [INYECCION-CONTINGENCIA] Creando cubeta temporal para OP: {op_limpia}, Producto: {codigo_limpio}, Pedido: {id_pedido_final}")
                        nueva_cubeta = DistribucionOpPedidos(
                            op_world_office=op_limpia,
                            id_pedido=id_pedido_final,
                            codigo_producto=codigo_limpio,
                            cant_requerida=piezas_por_repartir,
                            cant_inyectada=piezas_por_repartir,
                            cant_pulida=0,
                            cant_ensamblada=0,
                            cant_alistada=0
                        )
                        db.session.add(nueva_cubeta)
                        db.session.flush()
                        cubetas = [nueva_cubeta]
                        piezas_por_repartir = 0.0

                    logger.debug(f" 📦 [INYECCION-FIFO] Propagando {piezas_por_repartir} piezas a {len(cubetas)} cubetas. OP: {op_limpia}, Producto: {codigo_limpio}")

                    for cubeta in cubetas:
                        if piezas_por_repartir <= 0:
                            break

                        falta = max(0, (cubeta.cant_requerida or 0) - (cubeta.cant_inyectada or 0))
                        if falta > 0:
                            if piezas_por_repartir >= falta:
                                cubeta.cant_inyectada = (cubeta.cant_inyectada or 0) + falta
                                piezas_por_repartir -= falta
                            else:
                                cubeta.cant_inyectada = (cubeta.cant_inyectada or 0) + piezas_por_repartir
                                piezas_por_repartir = 0

                # 4. Finalizar programación asociada en db_programacion
                db.session.query(ProgramacionInyeccion).filter(
                    ProgramacionInyeccion.codigo_sistema == prod.id_codigo,
                    ProgramacionInyeccion.maquina == prod.maquina,
                    ProgramacionInyeccion.estado == 'EN_PROCESO'
                ).update({"estado": 'FINALIZADO'}, synchronize_session=False)

            db.session.commit()

        except Exception as e:
            db.session.rollback()
            if not isinstance(e, TurnoInvalidoException):
                logger.error(f"❌ Error al reportar turno en el MES: {e}")
            raise

        try:
            from backend.services.programacion_service import ProgramacionService
            ProgramacionService.clear_mes_cache()
        except Exception:
            pass

        logger.info(f"✅ Lote {id_iny} reportado y finalizado exitosamente. Total piezas: {total_teorica}")

        return {
            "success": True,
            "count": len(prods_en_lote),
            "teorica": total_teorica,
            "message": "Turno finalizado con éxito. Cubetas de prioridad actualizadas por FIFO."
        }

    @staticmethod
    def guardar_programacion(data):
        """
        Registra la planificación del turno de la tarde anterior en
        ProgramacionInyeccion (UPSERT por fecha+maquina+codigo_sistema+molde) y
        crea su distribución asociada de cubetas por pedido en
        DistribucionOpPedidos. Soporta múltiples productos en el mismo montaje
        de forma atómica: cualquier fallo hace rollback completo antes de
        propagar.

        OP automática (reunión 2026-08-25): op_world_office se reserva vía
        OpNumeradorService.obtener_o_reservar('INYECCION', fecha, maquina) --
        una sola OP por máquina/día, idéntica sin importar cuántas referencias
        tenga el montaje ni si se reprograma varias veces (la reserva es
        idempotente). Las cubetas (DistribucionOpPedidos) siguen naciendo con
        op_world_office=None a propósito: la propagación real ocurre en
        iniciar_trabajo, que ya sabe limpiar/reasignar cubetas sin duplicar.

        Puede lanzar ValueError (validación de payload) — el controlador la
        traduce a 400.
        """
        if not data:
            raise ValueError("No se recibieron datos")

        maquina = data.get('maquina')
        fecha_str = data.get('fecha')

        if not all([maquina, fecha_str]):
            raise ValueError("Los campos maquina y fecha son obligatorios")

        try:
            fecha_dt = datetime.strptime(fecha_str.split('T')[0], '%Y-%m-%d').date()
        except Exception as e_date:
            logger.error(f"Error parseando fecha '{fecha_str}': {e_date}")
            raise ValueError("Formato de fecha inválido. Usar YYYY-MM-DD")

        responsable_planta = data.get('responsable_planta')
        observaciones = data.get('observaciones')
        pedidos_asignados = data.get('pedidos_asignados', [])

        # Texto, no número: el código real de molde no es numérico ('5002A',
        # '9304 moneda' -- ver rel_producto_molde). Ver migrate_molde_texto.py.
        molde_val = str(data.get('molde') or '').strip() or None

        productos = data.get('productos')
        if not productos:
            codigo_sistema = data.get('codigo_sistema')
            if not codigo_sistema:
                raise ValueError("Debes especificar al menos un producto (codigo_sistema o productos)")

            try:
                cavidades_val = int(float(data.get('cavidades'))) if data.get('cavidades') is not None else 1
                cantidad_val = float(data.get('cantidad') or 0)
            except (ValueError, TypeError) as e_num:
                raise ValueError(f"Error en formato de cavidades/cantidad: {str(e_num)}")

            productos = [{
                "codigo_sistema": codigo_sistema,
                "cavidades": cavidades_val,
                "cantidad": cantidad_val
            }]

        programaciones_ids = []

        try:
            # OP automática: una reserva por (fecha, máquina), compartida por
            # todo el montaje. Idempotente -- reprogramar el mismo día/máquina
            # devuelve la MISMA OP, no crea una nueva. No hace commit propio:
            # vive dentro de esta misma transacción atómica.
            op_generada = OpNumeradorService.obtener_o_reservar(
                'INYECCION', fecha_dt, maquina, usuario=responsable_planta
            )

            for prod_data in productos:
                cod_sistema = prod_data.get('codigo_sistema')
                if not cod_sistema:
                    raise ValueError("Cada producto del montaje debe especificar 'codigo_sistema'")

                try:
                    cav_val = int(float(prod_data.get('cavidades') or 1))
                    cant_val = float(prod_data.get('cantidad') or 0)
                except (ValueError, TypeError) as e_num:
                    raise ValueError(f"Error en campos numéricos para producto {cod_sistema}: {e_num}")

                try:
                    # Buscar si ya existe la programación para hacer UPSERT
                    existente = db.session.query(ProgramacionInyeccion).filter(
                        ProgramacionInyeccion.fecha == fecha_dt,
                        ProgramacionInyeccion.maquina == maquina,
                        ProgramacionInyeccion.codigo_sistema == cod_sistema,
                        db.func.coalesce(ProgramacionInyeccion.molde, '') == (molde_val or '')
                    ).first()

                    if existente:
                        existente.cantidad = cant_val
                        existente.cavidades = cav_val
                        existente.responsable_planta = responsable_planta
                        existente.observaciones = observaciones
                        existente.estado = 'PROGRAMADO'
                        # Solo si sigue vacía: si la máquina ya arrancó
                        # (iniciar_trabajo puso estado='EN_PROCESO' y una OP
                        # real), reprogramar aquí NO debe pisarla.
                        if not existente.op_world_office:
                            existente.op_world_office = op_generada.numero_op
                        db.session.flush()
                        programaciones_ids.append(existente.id)
                    else:
                        nueva_prog = ProgramacionInyeccion(
                            fecha=fecha_dt,
                            codigo_sistema=cod_sistema,
                            maquina=maquina,
                            cantidad=cant_val,
                            estado='PROGRAMADO',
                            molde=molde_val,
                            cavidades=cav_val,
                            responsable_planta=responsable_planta,
                            observaciones=observaciones,
                            op_world_office=op_generada.numero_op
                        )
                        db.session.add(nueva_prog)
                        db.session.flush()
                        programaciones_ids.append(nueva_prog.id)

                except SQLAlchemyError as err:
                    logger.error(f"Error en UPSERT de programación {cod_sistema}: {err}")
                    raise ValueError(f"No se pudo guardar la programación para el producto {cod_sistema}")

            # Para evitar cubetas duplicadas al reprogramar (UPSERT): limpiamos las
            # distribuciones planificadas previas (con OP = None) de los productos del montaje.
            for prod_data in productos:
                cod_sistema = prod_data.get('codigo_sistema')
                if cod_sistema:
                    db.session.query(DistribucionOpPedidos).filter(
                        DistribucionOpPedidos.codigo_producto == cod_sistema,
                        DistribucionOpPedidos.op_world_office.is_(None)
                    ).delete(synchronize_session=False)

            for pedido in pedidos_asignados:
                id_pedido = pedido.get('id_pedido')
                cant_req = pedido.get('cant_requerida')
                cod_prod_cubeta = pedido.get('codigo_producto') or productos[0].get('codigo_sistema')

                if not id_pedido or cant_req is None:
                    raise ValueError("Cada pedido asignado debe incluir 'id_pedido' y 'cant_requerida'")

                try:
                    cant_req_val = int(float(cant_req))
                except (ValueError, TypeError):
                    raise ValueError(f"Cantidad requerida inválida para el pedido {id_pedido}")

                nueva_dist = DistribucionOpPedidos(
                    op_world_office=None,
                    id_pedido=id_pedido,
                    codigo_producto=cod_prod_cubeta,
                    cant_requerida=cant_req_val,
                    cant_inyectada=0,
                    cant_pulida=0,
                    cant_ensamblada=0,
                    cant_alistada=0
                )
                db.session.add(nueva_dist)

            db.session.commit()

        except Exception as e:
            db.session.rollback()
            if not isinstance(e, ValueError):
                logger.error(f"❌ Error crítico en InyeccionService.guardar_programacion: {e}")
            raise

        logger.info(f"✅ {len(programaciones_ids)} Programación(es) creada(s)/actualizada(s) exitosamente. Pedidos distribuidos: {len(pedidos_asignados)}")
        return {
            "success": True,
            "message": f"Programación diaria ({len(programaciones_ids)} referencias) y cubetas creadas correctamente",
            "id_programacion": programaciones_ids[0] if programaciones_ids else None,
            "orden_produccion": op_generada.numero_op
        }

    @staticmethod
    def registrar_pnc(data):
        """
        Registra un reporte de PNC "suelto" (fuera del flujo de lote) para un
        id_inyeccion + id_codigo dados: mapea los defectos reportados a las 5
        columnas tipadas de PncInyeccion y sincroniza pnc_total/pnc_detalle/
        cantidad_real en el registro de ProduccionInyeccion correspondiente.

        Puede lanzar ValueError (validación) — el controlador la traduce a 400.
        Cualquier otro error hace rollback antes de propagar.
        """
        from backend.utils.formatters import normalizar_codigo

        id_iny = data.get('id_inyeccion')
        id_codigo = data.get('id_codigo') or data.get('codigo')
        defectos = data.get('defectos') or {}

        if not id_iny:
            raise ValueError("El campo id_inyeccion es obligatorio")

        id_cod = normalizar_codigo(id_codigo) if id_codigo else None

        # Fallback de producto si no se recibe (misma normalizar_codigo() de
        # arriba, para no mezclar dos convenciones distintas de id_cod).
        if not id_cod:
            first_prod = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=id_iny).first()
            if first_prod:
                id_cod = normalizar_codigo(first_prod.id_codigo)

        if not id_cod:
            raise ValueError("El campo id_codigo es obligatorio")

        try:
            db.session.query(PncInyeccion).filter_by(id_inyeccion=id_iny, id_codigo=id_cod).delete()

            quemado_manchado = 0
            incompleto_falta_llenado = 0
            rebaba_excesiva = 0
            burbuja_porosidad = 0
            deformacion_rechupado = 0

            if isinstance(defectos, list):
                defectos_dict = {}
                for item in defectos:
                    crit = item.get('criterio') or item.get('motivo')
                    cant = float(item.get('cantidad') or 0)
                    if crit and cant > 0:
                        defectos_dict[crit] = defectos_dict.get(crit, 0) + cant
                defectos = defectos_dict

            for key, val in defectos.items():
                cant = float(val or 0)
                if cant <= 0:
                    continue

                key_lower = str(key).lower().strip()
                if any(x in key_lower for x in ["quemado", "mancha", "contaminado"]):
                    quemado_manchado += cant
                elif any(x in key_lower for x in ["incompleto", "escaso", "falta", "llenado"]):
                    incompleto_falta_llenado += cant
                elif "rebaba" in key_lower:
                    rebaba_excesiva += cant
                elif any(x in key_lower for x in ["burbuja", "porosidad"]):
                    burbuja_porosidad += cant
                elif any(x in key_lower for x in ["deform", "rechupe", "chupado", "hundido", "flujo"]):
                    deformacion_rechupado += cant
                else:
                    deformacion_rechupado += cant

            total_cantidad = quemado_manchado + incompleto_falta_llenado + rebaba_excesiva + burbuja_porosidad + deformacion_rechupado

            prod_iny = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=id_iny, id_codigo=id_cod).first()

            if total_cantidad > 0:
                codigo_ensamble = prod_iny.codigo_ensamble if prod_iny else None

                nuevo_pnc = PncInyeccion(
                    id_pnc_inyeccion=uuid.uuid4().hex[:8],
                    id_inyeccion=id_iny,
                    id_codigo=id_cod,
                    cantidad=total_cantidad,
                    criterio=f"Quemado: {int(quemado_manchado)}, Falta Llenado: {int(incompleto_falta_llenado)}, Rebaba: {int(rebaba_excesiva)}, Burbujas: {int(burbuja_porosidad)}, Deformacion: {int(deformacion_rechupado)}",
                    codigo_ensamble=codigo_ensamble,
                    quemado_manchado=quemado_manchado,
                    incompleto_falta_llenado=incompleto_falta_llenado,
                    rebaba_excesiva=rebaba_excesiva,
                    burbuja_porosidad=burbuja_porosidad,
                    deformacion_rechupado=deformacion_rechupado
                )
                db.session.add(nuevo_pnc)

                if prod_iny:
                    prod_iny.pnc_total = int(round(total_cantidad))
                    prod_iny.pnc_detalle = nuevo_pnc.criterio
                    cant_bruta = prod_iny.cant_contador or prod_iny.cantidad_real or 0
                    prod_iny.cantidad_real = max(0, int(round(cant_bruta - total_cantidad)))

                db.session.commit()
                logger.info(f"✅ PNC registrado para {id_cod} en {id_iny}: Total: {total_cantidad}")
                return {
                    "success": True,
                    "message": "PNC registrado correctamente en db_pnc_inyeccion",
                    "total_pnc": total_cantidad
                }
            else:
                if prod_iny:
                    prod_iny.pnc_total = 0
                    prod_iny.pnc_detalle = ""
                    cant_bruta = prod_iny.cant_contador or prod_iny.cantidad_real or 0
                    prod_iny.cantidad_real = int(round(cant_bruta))
                db.session.commit()
                return {
                    "success": True,
                    "message": "No se reportaron defectos de PNC",
                    "total_pnc": 0
                }

        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error en InyeccionService.registrar_pnc: {e}")
            raise

    @staticmethod
    def obtener_pedidos_pendientes(codigo):
        """
        Devuelve los pedidos abiertos (PENDIENTE, ABIERTO, Alistamiento,
        ALISTADO) o con saldo pendiente de despacho para un código de producto.
        Solo lectura: no requiere escudo transaccional.

        Puede lanzar ValueError (código faltante) — el controlador la traduce a 400.
        """
        if not codigo:
            raise ValueError("Código de producto es requerido")

        # Match EXACTO (normalizado), no ilike('%codigo%') -- un substring
        # amplio confunde referencias que comparten dígitos pero son productos
        # distintos (ej. buscar "5002" también traía pedidos de "FR-5002A",
        # "FR-5002B", "KIT-03-5002"... 246 pares así en la tabla real,
        # hallazgo 2026-08-31). El lado columna se normaliza en SQL
        # (sql_normalizar_codigo_fr) y el parámetro se normaliza en Python
        # ANTES de bindearlo -- pasarlo envuelto en sql_normalizar_codigo_fr
        # dentro de un :param de text() rompe el parser de SQLAlchemy (:: se
        # interpreta como escape de dos puntos literales, no cast de
        # Postgres), ver memoria del proyecto (mismo bug ya encontrado hoy
        # en PulidoService).
        codigo_norm = _normalizar_codigo_fr_python(codigo)
        pedidos_activos = db.session.query(Pedido).filter(
            text(f"{sql_normalizar_codigo_fr('id_codigo')} = :codigo_norm"),
            Pedido.estado.in_(ESTADOS_PEDIDO_ACTIVO)
        ).params(codigo_norm=codigo_norm).all()

        resultados = []
        for p in pedidos_activos:
            try:
                cant_solicitada = float(p.cantidad or 0)
                cant_alistada = float(p.cant_alistada or 0)
            except (ValueError, TypeError):
                cant_solicitada = 0.0
                cant_alistada = 0.0

            cant_pendiente = max(0.0, cant_solicitada - cant_alistada)

            if cant_pendiente > 0:
                resultados.append({
                    "id_pedido": p.id_pedido,
                    "cliente": p.cliente or "CLIENTE GENERAL",
                    "cantidad_solicitada": int(cant_solicitada),
                    "cantidad_pendiente": int(cant_pendiente)
                })

        # Evitar duplicados agrupando por ID de pedido (consolidación real)
        pedidos_unicos = {}
        for res in resultados:
            id_ped = res["id_pedido"]
            if id_ped not in pedidos_unicos:
                pedidos_unicos[id_ped] = res
            else:
                pedidos_unicos[id_ped]["cantidad_solicitada"] += res["cantidad_solicitada"]
                pedidos_unicos[id_ped]["cantidad_pendiente"] += res["cantidad_pendiente"]

        return {"success": True, "pedidos": list(pedidos_unicos.values())}

    @staticmethod
    def obtener_demanda_b2b(codigo):
        """
        Agrega las unidades de pedidos B2B pendientes contra el stock actual
        (terminado + bodega) para un código de producto. Solo lectura.

        Puede lanzar ValueError (código faltante) — el controlador la traduce a 400.
        """
        if not codigo:
            raise ValueError("El código es requerido")

        # Match EXACTO (normalizado) en vez de ilike('%codigo%') -- ver
        # docstring de obtener_pedidos_pendientes: un substring amplio mezcla
        # referencias que comparten dígitos pero son productos distintos
        # (ej. "5002" también traía "FR-5002A", "FR-5002B", "KIT-03-5002").
        # Encontrado 2026-08-31 revisando por qué "Demanda Activa" podía
        # salir inflada en Nueva Programación.
        codigo_norm = _normalizar_codigo_fr_python(codigo)
        pedidos_activos = db.session.query(Pedido).filter(
            text(f"{sql_normalizar_codigo_fr('id_codigo')} = :codigo_norm"),
            Pedido.estado.in_(ESTADOS_PEDIDO_ACTIVO)
        ).params(codigo_norm=codigo_norm).all()

        unidades_pedidas_b2b = 0.0
        # Alistado, pendiente despacho (pedido de la jefa 2026-08-31): unidades
        # que YA se separaron del inventario para estos pedidos pero todavía no
        # salen -- se muestran aparte de la demanda real para no verse como
        # "no hay nada pendiente" cuando en realidad hay unidades comprometidas
        # esperando despacho (hallazgo: el pedido de MT-7004-B con cant_alistada
        # = cantidad total quedaba invisible, mezclado silenciosamente dentro
        # de una Demanda Activa en 0).
        alistado_pendiente_despacho = 0.0
        for p in pedidos_activos:
            try:
                cant_solicitada = float(p.cantidad or 0)
                cant_alistada = float(p.cant_alistada or 0)
            except (ValueError, TypeError):
                cant_solicitada = 0.0
                cant_alistada = 0.0

            cant_alistada = min(cant_alistada, cant_solicitada)
            cant_pendiente = max(0.0, cant_solicitada - cant_alistada)
            unidades_pedidas_b2b += cant_pendiente
            alistado_pendiente_despacho += cant_alistada

        # Mismo criterio: match exacto sobre codigo_sistema (la fuente de
        # verdad para el catálogo -- ver memoria del proyecto) en vez de
        # ilike, que podía traer el producto equivocado si "codigo" es
        # substring de otro id_codigo/codigo_sistema real.
        prod = db.session.query(Producto).filter(
            (Producto.codigo_sistema == codigo_norm) | (Producto.id_codigo == codigo_norm)
        ).first()

        stock_terminado = 0.0
        stock_bodega = 0.0
        stock_por_pulir = 0.0

        if prod:
            stock_terminado = float(prod.p_terminado or 0)
            stock_bodega = float(prod.stock_bodega or 0)
            stock_por_pulir = float(prod.por_pulir or 0)

        stock_actual_disponible = stock_terminado + stock_bodega

        logger.info(f"[AUDITORÍA DE DEMANDA] {codigo}: B2B pedido={unidades_pedidas_b2b}, stock disponible={stock_actual_disponible}")

        # Empacado hoy (pedido de la jefa 2026-08-31, Opción A): cuánto de
        # esta referencia ya se armó/empacó hoy -- para no programar más
        # producción de algo que ya salió armado en el día. Match exacto
        # normalizado, mismo criterio que el resto de esta función.
        empacado_hoy = db.session.query(db.func.coalesce(db.func.sum(ProduccionEmpaque.cantidad), 0)).filter(
            text(f"{sql_normalizar_codigo_fr('id_codigo')} = :codigo_norm"),
            ProduccionEmpaque.fecha == get_colombia_time().date()
        ).params(codigo_norm=codigo_norm).scalar()

        return {
            "success": True,
            "codigo": codigo,
            "unidades_pedidas_b2b": int(round(unidades_pedidas_b2b)),
            "stock_actual_disponible": int(round(stock_actual_disponible)),
            "stock_terminado": int(round(stock_terminado)),
            "stock_bodega": int(round(stock_bodega)),
            "stock_por_pulir": int(round(stock_por_pulir)),
            "empacado_hoy": int(round(float(empacado_hoy or 0))),
            "alistado_pendiente_despacho": int(round(alistado_pendiente_despacho))
        }

    # ---------------------------------------------------------------
    # RESIDUAL LEGACY (registro directo fuera del flujo de lote MES,
    # config de cavidades, cálculo de producción) — movido desde app.py
    # ---------------------------------------------------------------
    @staticmethod
    def registrar_directa(data):
        """
        Registro legacy de inyección (POST /api/inyeccion), distinto del
        flujo de lote (registrar_lote/validar_lote/iniciar_trabajo): crea un
        único ProduccionInyeccion suelto, descuenta la BOM y suma a POR PULIR.
        No pasa por Programación/MES.

        Sin protección contra reenvío (hallazgo 2026-08-27, mismo problema
        que EmpaqueService.reportar): cada llamada crea una fila nueva con un
        UUID propio, así que un reintento de red o un doble toque duplica el
        descuento de BOM y el crédito a POR PULIR sin que nada lo detecte.
        Se cierra igual que en Empaque: si en los últimos
        SEGUNDOS_VENTANA_DUPLICADO segundos ya existe un registro idéntico
        (mismo código, responsable, máquina y cantidad), se asume que es el
        mismo envío repetido y se devuelve ese registro sin tocar stock de
        nuevo.
        """
        if not data:
            raise ValueError('No se recibieron datos')

        codigo_raw = str(data.get('codigo_producto', '')).strip()
        responsable = str(data.get('responsable', '')).strip()
        if not codigo_raw or not responsable:
            raise ValueError('Producto y Responsable son obligatorios')

        try:
            from backend.utils.formatters import normalizar_codigo
            from backend.services.bom_service import calcular_descuentos_ensamble
            from backend.services.stock_service import StockService

            codigo_norm = normalizar_codigo(codigo_raw)

            disparos = int(float(data.get('disparos', 0) or 0))
            cavidades = int(float(data.get('no_cavidades', 1) or 1))
            pnc = float(data.get('pnc', 0) or 0)
            cantidad_final = float(data.get('cantidad_real', disparos * cavidades))
            maquina = data.get('maquina')
            ahora = get_colombia_time()

            ventana = ahora - timedelta(seconds=SEGUNDOS_VENTANA_DUPLICADO)
            duplicado = ProduccionInyeccion.query.filter(
                ProduccionInyeccion.id_codigo == codigo_norm,
                ProduccionInyeccion.responsable == responsable,
                ProduccionInyeccion.maquina == maquina,
                ProduccionInyeccion.cantidad_real == cantidad_final,
                ProduccionInyeccion.fecha_inicia >= ventana,
            ).order_by(ProduccionInyeccion.id.desc()).first()
            if duplicado:
                logger.warning(
                    f"⚠️ [ANTI-DUPLICADO] registrar_directa: reenvío detectado para "
                    f"{codigo_norm}/{responsable}/{maquina} ({cantidad_final} u.) -- "
                    f"se devuelve {duplicado.id_inyeccion} sin volver a tocar stock."
                )
                return {'id': duplicado.id_inyeccion}

            id_inyeccion = f"INY-{uuid.uuid4().hex[:8].upper()}"

            nuevo_registro = ProduccionInyeccion(
                id_inyeccion=id_inyeccion,
                fecha_inicia=ahora,
                id_codigo=codigo_norm,
                responsable=responsable,
                maquina=maquina,
                cavidades=cavidades,
                cant_contador=disparos,
                cantidad_real=cantidad_final,
                pnc_total=pnc,
                estado='PENDIENTE',
                observaciones=data.get('observaciones', '')
            )
            db.session.add(nuevo_registro)

            bom_res = calcular_descuentos_ensamble(codigo_norm, int(cantidad_final))
            if bom_res.get('success'):
                for comp in bom_res.get('componentes', []):
                    StockService.registrar_salida(comp['codigo_inventario'], comp['cantidad_total_descontar'], "STOCK_BODEGA")

            StockService.registrar_entrada(codigo_norm, cantidad_final - pnc, "POR PULIR")

            db.session.commit()

            from backend.services.programacion_service import ProgramacionService
            ProgramacionService.clear_mes_cache()

            return {'id': id_inyeccion}
        except Exception as e:
            db.session.rollback()
            if not isinstance(e, ValueError):
                logger.error(f"❌ Error en InyeccionService.registrar_directa: {e}")
            raise

    @staticmethod
    def obtener_config_cavidades():
        """Configuración estática de cavidades disponibles por máquina (sin DB)."""
        return {
            "cavidades_disponibles": [1, 2, 3, 4, 5, 6],
            "cavidades_por_defecto": 1,
            "maquinas_config": {
                "INYECTORA 1": {"cavidades": [1, 2, 3, 4, 5, 6], "default": 1},
                "INYECTORA 2": {"cavidades": [1, 2, 3, 4, 5, 6], "default": 2},
                "INYECTORA 3": {"cavidades": [2, 3, 4, 5, 6], "default": 3},
                "INYECTORA 4": {"cavidades": [4, 6, 8, 12, 16], "default": 8},
                "INYECTORA 5": {"cavidades": [8, 12, 16, 24, 32], "default": 12},
                "INYECTORA 6": {"cavidades": [16, 24, 32, 48], "default": 24}
            }
        }

    @staticmethod
    def calcular_produccion(cantidad, cavidades, pnc=0):
        """Calcula la producción total (disparos x cavidades) y la eficiencia dado un PNC opcional (sin DB)."""
        if not cantidad or not cavidades:
            raise ValueError('Se requieren cantidad y cavidades')
        try:
            cantidad_i = int(cantidad)
            cavidades_i = int(cavidades)
        except (TypeError, ValueError):
            raise ValueError('Cantidad y cavidades deben ser numeros validos')

        if cantidad_i <= 0 or cavidades_i <= 0:
            raise ValueError('La cantidad y cavidades deben ser mayores a 0')

        total_piezas = cantidad_i * cavidades_i
        pnc_i = int(pnc or 0)
        piezas_ok = total_piezas - pnc_i

        return {
            "disparos": cantidad_i,
            "cavidades": cavidades_i,
            "total_piezas": total_piezas,
            "pnc": pnc_i,
            "piezas_ok": piezas_ok,
            "eficiencia": round((piezas_ok / total_piezas * 100), 2) if total_piezas > 0 else 0
        }


inyeccion_service = InyeccionService()
