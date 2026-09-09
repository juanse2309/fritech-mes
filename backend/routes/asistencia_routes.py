import logging
import pandas as pd
from datetime import datetime
from flask import Blueprint, jsonify, request

from backend.core.sql_database import db
from backend.models.sql_models import RegistroAsistencia
from backend.models.nomina_models import RegistroAsistencia as RegistroAsistenciaDTO
from backend.services.nomina_service import (
    ReglasAsistencia,
    get_ultima_fecha_corte,
    filtrar_registros_post_corte,
    consolidar_horas,
    construir_detalle_diario,
    ejecutar_corte_db,
    get_consolidado_pendiente,
    get_detalle_diario_pendiente,
    obtener_colaboradores_visibles,
    obtener_registros_asistencia_colaborador,
)
from backend.utils.auth_middleware import (
    require_role,
    obtener_identidad_segura,
    ROL_ADMINS,
    ROL_JEFES,
    ROL_COMERCIALES,
    ROL_OPERARIOS
)

logger = logging.getLogger(__name__)
asistencia_bp = Blueprint('asistencia', __name__)

# Roles autorizados para gestionar nómina unificada cross-division (heredado del middleware central)
ROLES_NOMINA_GLOBAL = ROL_ADMINS

def seguro_formatear_fecha(valor, formato='%d/%m/%Y %H:%M'):
    """Convierte cualquier objeto (str, datetime, timestamp, null) a un string de fecha seguro."""
    if not valor: return ""
    try:
        dt = pd.to_datetime(valor)
        if pd.isna(dt): return ""
        return dt.strftime(formato)
    except:
        return str(valor)

@asistencia_bp.route('/colaboradores', methods=['GET'])
@asistencia_bp.route('/personal_a_cargo', methods=['GET'])
@require_role(ROL_ADMINS + ROL_JEFES + ['AUXILIAR INVENTARIO', 'JEFE AUXILIAR INVENTARIO'])
def obtener_colaboradores():
    """Obtiene lista de colaboradores filtrada por Áreas de Responsabilidad. Incluye al Jefe."""
    user, role = obtener_identidad_segura(request)

    try:
        user_name = user
        user_role = str(role).upper() if role else ''

        colaboradores = obtener_colaboradores_visibles(user_name, user_role)

        return jsonify({
            'status': 'success', 'success': True,
            'count': len(colaboradores),
            'colaboradores': colaboradores
        }), 200

    except Exception as e:
        logger.error(f"Error en personal_a_cargo (SQL-RBAC): {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible obtener la lista de colaboradores.'}), 500

@asistencia_bp.route('/guardar', methods=['POST'])
@asistencia_bp.route('/registrar_masivo', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['JEFE AUXILIAR INVENTARIO'])
def guardar_asistencia():
    """Guarda los registros de asistencia masivos en PostgreSQL recalculando horas server-side."""
    user, role = obtener_identidad_segura(request)

    try:
        data = request.json
        if not data or 'registros' not in data:
            return jsonify({'status': 'error', 'success': False, 'message': 'Datos inválidos o vacíos'}), 400

        registros_recibidos = data['registros']
        usuario_registra = user
        conteo = 0

        for reg in registros_recibidos:
            nombre = reg.get('colaborador') or reg.get('nombre')
            if not nombre: continue

            # Determinar fecha (ISO -> Date) de forma estricta
            f_str = reg.get('fecha') or datetime.now().strftime('%Y-%m-%d')
            try:
                fecha_dt = datetime.strptime(f_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                fecha_dt = datetime.now().date()

            ing_real = reg.get('ingreso_real') or reg.get('hora_entrada', '')
            sal_real = reg.get('salida_real') or reg.get('hora_salida', '')

            # Recálculo de Reglas de Negocio en Servidor (Descarte de horas provenientes del cliente)
            dto = RegistroAsistenciaDTO(
                fecha=fecha_dt,
                ingreso_real=ing_real,
                salida_real=sal_real,
                colaborador=nombre
            )
            calculo = ReglasAsistencia.calcular_jornada_y_extras(dto)
            h_ord = calculo['horas_ordinarias']
            h_ext = calculo['horas_extras']

            # Buscar si ya existe registro para ese colaborador-día para evitar duplicados
            existente = RegistroAsistencia.query.filter_by(
                fecha=fecha_dt, 
                colaborador=nombre
            ).first()

            if existente:
                # Protección de Periodos Liquidados (Inmutabilidad Contable)
                if existente.estado_pago == 'PROCESADO' or getattr(existente, 'corte_id', None) is not None:
                    logger.warning(
                        f"[INMUTABILIDAD] Omiso intento de sobreescritura en registro sellado "
                        f"ID {existente.id} del colaborador '{nombre}'."
                    )
                    continue

                # Actualización de registro en periodo abierto
                existente.ingreso_real = ing_real
                existente.salida_real = sal_real
                existente.horas_ordinarias = h_ord
                existente.horas_extras = h_ext
                existente.estado = reg.get('estado', 'REGISTRADO')
                existente.comentarios = reg.get('comentarios', '')
                existente.registrado_por = usuario_registra
            else:
                # Inserción de nuevo registro
                nuevo = RegistroAsistencia(
                    fecha=fecha_dt,
                    colaborador=nombre,
                    ingreso_real=ing_real,
                    salida_real=sal_real,
                    horas_ordinarias=h_ord,
                    horas_extras=h_ext,
                    estado=reg.get('estado', 'REGISTRADO'),
                    estado_pago='PENDIENTE',
                    comentarios=reg.get('comentarios', ''),
                    registrado_por=usuario_registra
                )
                db.session.add(nuevo)
            
            conteo += 1

        db.session.commit()
        logger.info(f"💾 SQL: {conteo} registros de asistencia procesados exitosamente por usuario '{usuario_registra}'.")
        
        return jsonify({
            'status': 'success', 'success': True,
            'message': f'Se procesaron {conteo} registros en SQL correctamente.'
        }), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error guardando asistencia masiva: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible guardar los registros de asistencia.'}), 500

@asistencia_bp.route('/guardar_ausencia', methods=['POST'])
@require_role(ROL_ADMINS + ROL_JEFES + ['JEFE AUXILIAR INVENTARIO'])
def guardar_ausencia():
    """Guarda un registro de ausencia en SQL."""
    user_name, user_role = obtener_identidad_segura(request)

    try:
        data = request.json
        if not data or 'registro' not in data:
            return jsonify({'status': 'error', 'success': False, 'message': 'Datos inválidos'}), 400

        reg = data['registro']
        from backend.models.sql_models import RegistroAsistencia
        from backend.core.sql_database import db

        # 1. SQL
        nueva_ausencia = RegistroAsistencia(
            fecha=reg.get('fecha'),
            colaborador=reg.get('colaborador'),
            ingreso_real='AUSENTE',
            salida_real='',
            horas_ordinarias=0,
            horas_extras=0,
            estado='AUSENTE',
            estado_pago='PENDIENTE',
            motivo=reg.get('motivo', ''),
            comentarios=reg.get('comentarios', ''),
            registrado_por=user_name
        )
        db.session.add(nueva_ausencia)
        db.session.commit()

        return jsonify({'status': 'success', 'success': True, 'message': 'Ausencia registrada correctamente en SQL'}), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error guardando ausencia: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible guardar la ausencia.'}), 500

@asistencia_bp.route('/mis_horas', methods=['GET'])
def obtener_mis_horas():
    """Obtiene el historial de asistencia del usuario logueado usando SQL crudo con blindaje total."""
    user, role = obtener_identidad_segura(request)
    if not user:
        return jsonify({'status': 'error', 'success': False, 'message': 'No autorizado'}), 401

    try:
        colaborador = user
        user_role = str(role).upper() if role else 'OPERARIO'

        # Obtener nombre completo para búsqueda robusta
        from backend.models.sql_models import Usuario
        u = Usuario.query.filter_by(username=colaborador).first()
        nombre_buscar = u.nombre_completo if u and u.nombre_completo else colaborador

        rows = obtener_registros_asistencia_colaborador(colaborador, nombre_buscar)

        mis_registros = []
        for row in rows:
            # Formateo de fecha robusto con Pandas
            fecha_str = seguro_formatear_fecha(row['fecha'], '%d/%m (%a)')
            # Traducir días si es necesario (Pandas .strftime('%a') suele dar ingles)
            dias_map = {'Mon': 'Lun', 'Tue': 'Mar', 'Wed': 'Mié', 'Thu': 'Jue', 'Fri': 'Vie', 'Sat': 'Sáb', 'Sun': 'Dom'}
            for eng, esp in dias_map.items():
                fecha_str = fecha_str.replace(eng, esp)

            mis_registros.append({
                'fecha': fecha_str,
                'llegada': row['ingreso_real'] or '-',
                'salida': row['salida_real'] or '-',
                'horas_normales': round(float(row['horas_ordinarias'] or 0), 2),
                'horas_extras': round(float(row['horas_extras'] or 0), 2),
                'estado': row['estado'],
                'motivo': row['motivo'],
                'estado_pago': row['estado_pago'] or 'PENDIENTE'
            })
        
        return jsonify({
            'status': 'success', 'success': True,
            'registros': mis_registros,
            'rol': user_role
        }), 200
        
    except Exception as e:
        import logging
        logging.error(f"FALLO CRÍTICO en obtener_mis_horas: {e}")
        # Blindaje: Devolver lista vacía para no romper el frontend
        return jsonify({
            'status': 'success', 'success': True,
            'registros': [],
            'error_info': str(e)
        }), 200

@asistencia_bp.route('/registros_dia', methods=['GET'])
def obtener_registros_dia():
    """Obtiene registros de una fecha específica desde SQL."""
    user, role = obtener_identidad_segura(request)
    if not user:
        return jsonify({'status': 'error', 'success': False, 'message': 'No autorizado'}), 401

    fecha = request.args.get('fecha')
    if not fecha: return jsonify({'status': 'error', 'success': False, 'message': 'Fecha inválida'}), 400
        
    try:
        from backend.models.sql_models import RegistroAsistencia
        registros = RegistroAsistencia.query.filter_by(fecha=fecha).all()
        
        res = []
        for r in registros:
            res.append({
                'id': r.id, 'colaborador': r.colaborador, 'ingreso_real': r.ingreso_real,
                'salida_real': r.salida_real, 'horas_ordinarias': r.horas_ordinarias,
                'horas_extras': r.horas_extras, 'estado': r.estado, 'motivo': r.motivo,
                'estado_pago': r.estado_pago
            })
        
        return jsonify({'status': 'success', 'success': True, 'registros': res}), 200
    except Exception as e:
        logger.error(f"Error en obtener_registros_dia: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible obtener los registros del día.'}), 500

@asistencia_bp.route('/consolidado_pendiente', methods=['GET'])
@require_role(ROL_ADMINS)
def obtener_consolidado_pendiente():
    """Orquestador de respuesta. Lógica de negocio en nomina_service."""
    # 1. Validar identidad (JWT o sesión Flask)
    user, role = obtener_identidad_segura(request)
    if not user:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Sesión inválida o nula. Debe autenticarse en el sistema.'
        }), 401

    try:
        from backend.models.sql_models import CorteNomina

        division = request.args.get('division', 'friparts').lower()

        # 2. Validar rol para permitir bypass ('all')
        user_role = str(role or '').upper()
        is_global_admin = user_role in ROLES_NOMINA_GLOBAL

        if division == 'all':
            if not is_global_admin:
                return jsonify({
                    'status': 'error', 'success': False,
                    'message': 'Acceso denegado: Se requiere rol administrativo global para consultar información consolidada unificada.'
                }), 403
        elif division not in ('friparts', 'frimetals'):
            return jsonify({
                'status': 'error', 'success': False,
                'message': f'División inválida: {division!r}. Las opciones válidas son "friparts", "frimetals" o "all".'
            }), 400

        # Delegar al servicio
        consolidado_array = get_consolidado_pendiente(division)
        detalle_diario = get_detalle_diario_pendiente(division)

        # Último corte (solo para el label informativo del frontend)
        ultimo_corte = db.session.query(CorteNomina).order_by(CorteNomina.fecha_corte.desc()).first()
        ultima_fecha_str = seguro_formatear_fecha(ultimo_corte.fecha_corte) if ultimo_corte else "Sin cortes previos"

        return jsonify({
            'status': 'success', 'success': True,
            'ultima_fecha_corte': ultima_fecha_str,
            'fecha': ultima_fecha_str,
            'consolidado': consolidado_array,
            'detalle_diario': detalle_diario,
            'total_registros_pendientes': len(detalle_diario)
        }), 200

    except Exception as e:
        logger.error(f"FALLO CRÍTICO CONSOLIDADO: {e}")
        return jsonify({
            'status': 'error', 'success': False,
            'message': f'Error en el servidor al consolidar la nómina: {str(e)}',
            'consolidado': [],
            'detalle_diario': [],
            'total_registros_pendientes': 0,
            'ultima_fecha_corte': 'Error en el servidor',
            'fecha': 'Error'
        }), 500

@asistencia_bp.route('/ejecutar_corte', methods=['POST'])
@require_role(ROL_ADMINS)
def ejecutar_corte():
    """Orquestador de respuesta. Lógica de negocio en nomina_service.ejecutar_corte_db()."""
    # 1. Validar identidad (JWT o sesión Flask) — única fuente de verdad para la auditoría
    user_name, role = obtener_identidad_segura(request)
    if not user_name:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'Sesión inválida o nula. Debe autenticarse en el sistema.'
        }), 401

    # 2. Obtener y validar el payload de manera segura
    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        return jsonify({
            'status': 'error', 'success': False,
            'message': 'El cuerpo de la solicitud no es un JSON válido.'
        }), 400

    division = data.get('division', '').strip().lower()

    # 3. Validar privilegios sobre la división seleccionada
    user_role = str(role or '').upper()
    is_global_admin = user_role in ROLES_NOMINA_GLOBAL

    if division == 'all':
        if not is_global_admin:
            return jsonify({
                'status': 'error', 'success': False,
                'message': 'Acceso denegado: Se requiere rol administrativo global para ejecutar cortes consolidados (ALL).'
            }), 403
    elif division not in ('friparts', 'frimetals'):
        return jsonify({
            'status': 'error', 'success': False,
            'message': f'División inválida: {division!r}. Las opciones válidas son "friparts", "frimetals" o "all".'
        }), 400

    try:
        resultado = ejecutar_corte_db(division=division, usuario_auditoria=user_name)
        return jsonify({
            'status': 'success', 'success': True,
            'periodo': (
                f"{seguro_formatear_fecha(resultado['p_inicio'], '%d/%m')} "
                f"a {seguro_formatear_fecha(resultado['p_fin'], '%d/%m')}"
            ),
            'message': f"Corte {resultado['id_corte']} ejecutado con éxito."
        }), 200
    except ValueError as e:
        # Sin datos pendientes
        return jsonify({'status': 'error', 'success': False, 'message': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error ejecución corte: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'No fue posible ejecutar el corte de nómina.'}), 500


@asistencia_bp.route('/editar/<int:id>', methods=['PUT'])
@require_role(ROL_ADMINS + ROL_JEFES + ['JEFE AUXILIAR INVENTARIO'])
def editar_asistencia(id):
    """Edita un registro existente de asistencia aplicando auditoría."""
    user, role = obtener_identidad_segura(request)
    if not user:
        return jsonify({'status': 'error', 'success': False, 'message': 'No autorizado'}), 401
        
    from backend.services.nomina_service import actualizar_registro_asistencia
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'success': False, 'message': 'Datos no proporcionados'}), 400
            
        ing_real = data.get('ingreso_real')
        sal_real = data.get('salida_real')
        motivo = data.get('motivo_edicion')
        
        if not motivo or not str(motivo).strip():
            return jsonify({'status': 'error', 'success': False, 'message': 'El motivo de edición es obligatorio'}), 400
            
        usuario = user
        
        resultado = actualizar_registro_asistencia(
            registro_id=id,
            nuevo_ingreso=ing_real,
            nueva_salida=sal_real,
            motivo=motivo,
            usuario_actual=usuario
        )
        
        return jsonify({
            'status': 'success', 'success': True,
            'message': 'Registro actualizado exitosamente',
            'datos': resultado
        }), 200
        
    except ValueError as ve:
        return jsonify({'status': 'error', 'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error al editar registro {id}: {e}")
        return jsonify({'status': 'error', 'success': False, 'message': 'Error interno del servidor'}), 500
