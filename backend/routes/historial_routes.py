from flask import Blueprint, request, current_app
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
    PncInyeccion, PncPulido, PncEnsamble, MetalsProduccion
)
from backend.utils.auth_middleware import require_role, ROL_ADMINS
from backend.services.historial_service import (
    preparar_movimientos_para_excel, generar_excel_historial_global,
    construir_movimientos_historial,
)

historial_bp = Blueprint('historial_bp', __name__)
logger = logging.getLogger(__name__)


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
        movimientos = construir_movimientos_historial(f_desde, f_hasta, tipo_filtro)
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
        elif hoja == 'metals_produccion':
            # Solo lectura (ver detalle) -- el endpoint de edición de abajo
            # (actualizar_registro_historial) no soporta esta hoja porque su
            # MAPEO de campos está armado para el esquema de FriParts
            # (fecha_inicia, orden_produccion, id_codigo...), que no
            # corresponde a las columnas de metals_produccion.
            model = MetalsProduccion
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
        resultados = construir_movimientos_historial(f_desde, f_hasta, tipo_filtro)
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
