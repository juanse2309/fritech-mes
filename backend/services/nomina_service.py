"""
nomina_service.py — Servicio de Nómina (SQL-First, SRP compliant).

Responsabilidades:
  - Toda la lógica de negocio del corte de nómina (consultas SQL y actualizaciones masivas).
  - No conoce ni toca Flask: sin request, sin session, sin jsonify.
  - La capa de ruta SOLO orquesta request/response e invoca estos métodos.

Cambios respecto a la versión anterior:
  - Se extrae ejecutar_corte_db() de asistencia_routes.py → elimina deuda técnica.
  - JOIN protege contra discrepancias entre colaborador/username con OR en nombre_completo.
"""

import re
import uuid
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text, func
from backend.core.sql_database import db
from backend.models.sql_models import CorteNomina, RegistroAsistencia as RegistroAsistenciaSQL, Usuario
from backend.models.nomina_models import RegistroAsistencia
from backend.config.nomina_config import PERFILES_HORARIO, MAPEO_PERFILES

logger = logging.getLogger(__name__)

# Cache en RAM de resoluciones nombre_completo/username -> username normalizado.
# Vive durante el ciclo de vida del proceso (no se persiste, no expira).
_cache_nombres_usuarios = {}


# ── Helpers privados ──────────────────────────────────────────────────────────

def _parse_hours(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _normalizar_hora(hora_str) -> str:
    """Normaliza strings de hora sucios o localizados (ej: '5:00:00 p. m.') a 'HH:MM' 24h."""
    if not hora_str or not isinstance(hora_str, str):
        return ""

    h = hora_str.strip().lower().replace('.', '')
    h = re.sub(r'\s+', ' ', h)
    h = h.replace('a m', 'am').replace('p m', 'pm')

    formatos = [
        '%I:%M:%S %p', '%I:%M %p',
        '%H:%M:%S', '%H:%M',
        '%I:%M:%S%p', '%I:%M%p',
    ]
    for fmt in formatos:
        try:
            return datetime.strptime(h, fmt).strftime('%H:%M')
        except ValueError:
            continue

    match = re.search(r'(\d{1,2}):(\d{2})', h)
    if match:
        hh, mm = int(match.group(1)), int(match.group(2))
        if 'p' in h and hh < 12:
            hh += 12
        return f"{hh:02d}:{mm:02d}"

    return ""


def _condicion_rol(division: str) -> str:
    """Devuelve el fragmento SQL que aísla la división correcta."""
    if division.lower() == 'all':
        return ""
    if division == 'frimetals':
        return "AND u.rol ILIKE 'staff frimetals'"
    return "AND u.rol NOT ILIKE 'staff frimetals'"


def _join_colaborador() -> str:
    """
    Fragmento del JOIN que soporta tanto colaborador = username
    como colaborador = nombre_completo.
    Centralizado aquí para que todos los queries sean consistentes.
    """
    return "(a.colaborador = u.username OR a.colaborador = u.nombre_completo)"


# ── API pública — Corte ───────────────────────────────────────────────────────

def get_periodo_pendiente(division: str) -> tuple:
    """
    Detecta la fecha mínima y máxima de registros PENDIENTES para la división dada.
    Retorna (p_inicio, p_fin) como objetos datetime, o (None, None) si no hay datos.
    """
    cond = _condicion_rol(division)
    join = _join_colaborador()

    sql = text(f"""
        SELECT MIN(a.fecha), MAX(a.fecha)
        FROM db_asistencia a
        JOIN db_usuarios u ON {join}
        WHERE COALESCE(a.estado_pago, 'PENDIENTE') = 'PENDIENTE'
        {cond}
    """)
    row = db.session.execute(sql).fetchone()
    if not row or not row[0] or not row[1]:
        return None, None
    return row[0], row[1]


def registrar_corte_nomina(division: str, usuario: str, p_inicio, p_fin, total_registros: Optional[int] = None) -> str:
    """
    Persiste el registro del corte en db_cortes_nomina.
    Retorna el id_corte generado.
    No hace commit — quien llama decide si hacer el commit junto con el UPDATE.
    """
    div_registro = 'GLOBAL_CONSOLIDADO' if division.lower() == 'all' else division.upper()
    id_corte = f"{str(uuid.uuid4())[:8].upper()}-{div_registro}"
    
    nuevo = CorteNomina(
        id_corte=id_corte,
        fecha_corte=datetime.now(),
        usuario_que_corta=usuario,
        periodo_inicio=p_inicio,
        periodo_fin=p_fin,
        total_registros=total_registros,
        usuario_autoriza=usuario,
        estado='PROCESADO',
        division=div_registro
    )
    db.session.add(nuevo)
    return id_corte


def marcar_registros_procesados(division: str, p_inicio, p_fin) -> int:
    """
    Actualiza estado_pago → 'PROCESADO' en db_asistencia para la división y rango dados.
    Retorna el número de filas afectadas.
    No hace commit.
    """
    cond = _condicion_rol(division)
    join = _join_colaborador()

    sql = text(f"""
        UPDATE db_asistencia AS a
        SET estado_pago = 'PROCESADO'
        FROM db_usuarios u
        WHERE {join}
          AND CAST(a.fecha AS DATE) >= :p_inicio
          AND CAST(a.fecha AS DATE) <= :p_fin
          AND COALESCE(a.estado_pago, 'PENDIENTE') != 'PROCESADO'
          {cond}
    """)
    result = db.session.execute(sql, {"p_inicio": p_inicio, "p_fin": p_fin})
    return result.rowcount


def ejecutar_corte_db(division: str, usuario_auditoria: str) -> dict:
    """
    Orquesta el corte completo dentro de una transacción atómica:
      1. Detecta periodo pendiente.
      2. Actualiza masivamente db_asistencia.
      3. Crea el registro histórico en db_cortes_nomina con el total de filas.
      4. Commit único.

    usuario_auditoria debe ser la identidad resuelta server-side (JWT/sesión vía
    obtener_identidad_segura), nunca un valor tomado del payload del cliente:
    es lo único que queda grabado en usuario_que_corta/usuario_autoriza.

    Retorna un dict con claves: id_corte, periodo, filas_afectadas.
    Lanza ValueError si no hay registros pendientes.
    Lanza Exception en cualquier fallo de BD (el llamador hace rollback).
    """
    p_inicio, p_fin = get_periodo_pendiente(division)
    if not p_inicio or not p_fin:
        raise ValueError("No hay registros pendientes para procesar.")

    filas = marcar_registros_procesados(division, p_inicio, p_fin)
    id_corte = registrar_corte_nomina(division, usuario_auditoria, p_inicio, p_fin, total_registros=filas)
    db.session.commit()

    logger.info(
        f"✅ Corte {id_corte} ({division}) completado: "
        f"{filas} registros de {p_inicio} a {p_fin} marcados como PROCESADO por '{usuario_auditoria}'."
    )
    return {
        "id_corte": id_corte,
        "p_inicio": p_inicio,
        "p_fin": p_fin,
        "filas_afectadas": filas,
    }


def actualizar_registro_asistencia(registro_id: int, nuevo_ingreso: str, nueva_salida: str, motivo: str, usuario_actual: str) -> dict:
    """
    Actualiza horas de un registro verificando que no esté bloqueado (PROCESADO).
    Ejecuta el recálculo con el motor de reglas y guarda auditoría.
    """
    motivo_limpio = motivo.strip() if motivo else ""
    if not motivo_limpio:
        raise ValueError("El motivo de edición es obligatorio y no puede estar vacío.")

    registro = db.session.query(RegistroAsistenciaSQL).get(registro_id)
    if not registro:
        raise ValueError("El registro no existe.")
        
    if registro.estado_pago == 'PROCESADO':
        raise ValueError("No se puede editar un registro perteneciente a una nómina procesada.")

    dto = RegistroAsistencia(
        fecha=registro.fecha,
        ingreso_real=nuevo_ingreso,
        salida_real=nueva_salida,
        colaborador=registro.colaborador
    )
    
    calculo = ReglasAsistencia.calcular_jornada_y_extras(dto)
    
    registro.ingreso_real = nuevo_ingreso
    registro.salida_real = nueva_salida
    registro.horas_ordinarias = calculo["horas_ordinarias"]
    registro.horas_extras = calculo["horas_extras"]
    registro.editado_por = usuario_actual
    registro.fecha_edicion = datetime.now()
    registro.motivo_edicion = motivo
    
    db.session.commit()
    return {
        "horas_ordinarias": registro.horas_ordinarias,
        "horas_extras": registro.horas_extras
    }


# ── API pública — Consultas de Consolidado ────────────────────────────────────

def get_consolidado_pendiente(division: str) -> list:
    """
    Retorna lista de colaboradores con sus horas ordinarias y extras PENDIENTES.
    Usado por el endpoint /consolidado_pendiente.
    """
    cond = _condicion_rol(division)
    join = _join_colaborador()

    sql = text(f"""
        SELECT
            u.username AS colaborador,
            u.departamento AS departamento,
            COALESCE(SUM(CAST(a.horas_ordinarias AS NUMERIC)), 0) AS horas_ordinarias,
            COALESCE(SUM(CAST(a.horas_extras AS NUMERIC)), 0) AS horas_extras,
            COUNT(a.id) AS registros_contados
        FROM db_usuarios u
        LEFT JOIN db_asistencia a
            ON {join}
            AND COALESCE(a.estado_pago, 'PENDIENTE') = 'PENDIENTE'
        WHERE u.activo = true
        {cond}
        GROUP BY u.username, u.departamento
        ORDER BY u.username ASC
    """)
    rows = db.session.execute(sql).mappings().all()

    return [
        {
            "colaborador": r["colaborador"],
            "departamento": r["departamento"] or "N/A",
            "horas_ordinarias": round(float(r["horas_ordinarias"]), 2),
            "horas_extras": round(float(r["horas_extras"]), 2),
            "estado": "PENDIENTE",
            "registros": int(r["registros_contados"]),
        }
        for r in rows
    ]


def get_detalle_diario_pendiente(division: str) -> list:
    """
    Retorna el detalle día a día de registros PENDIENTES.
    Usado para construir el CSV de exportación.
    """
    cond = _condicion_rol(division)
    join = _join_colaborador()

    sql = text(f"""
        SELECT
            a.fecha, a.colaborador, a.ingreso_real, a.salida_real,
            a.horas_ordinarias, a.horas_extras, a.motivo, a.comentarios
        FROM db_asistencia a
        JOIN db_usuarios u ON {join}
        WHERE COALESCE(a.estado_pago, 'PENDIENTE') = 'PENDIENTE'
        {cond}
        ORDER BY a.colaborador, a.fecha
    """)
    rows = db.session.execute(sql).mappings().all()

    result = []
    for r in rows:
        try:
            import pandas as pd
            fecha_str = pd.to_datetime(r["fecha"]).strftime("%d/%m/%Y")
        except Exception:
            fecha_str = str(r["fecha"])

        result.append({
            "colaborador": r["colaborador"],
            "fecha": fecha_str,
            "ingreso": r["ingreso_real"],
            "salida": r["salida_real"],
            "horas_ordinarias": round(_parse_hours(r["horas_ordinarias"]), 2),
            "horas_extras": round(_parse_hours(r["horas_extras"]), 2),
            "motivo": r["motivo"] or "",
            "comentarios": r["comentarios"] or "",
        })
    return result


# ── API pública — Legacy (compat con imports existentes) ─────────────────────

def get_ultima_fecha_corte():
    """Lee la tabla db_cortes_nomina y retorna la fecha del último corte."""
    try:
        ultimo = db.session.query(CorteNomina).order_by(CorteNomina.fecha_corte.desc()).first()
        return ultimo.fecha_corte if ultimo else None
    except Exception as e:
        logger.warning(f"Error leyendo cortes_nomina: {e}")
        return None


def filtrar_registros_post_corte(registros: list, ultima_fecha_corte) -> list:
    """Filtra lista en memoria. Mantenida por compatibilidad."""
    if not ultima_fecha_corte:
        return registros
    if isinstance(ultima_fecha_corte, datetime):
        ultima_fecha_corte = ultima_fecha_corte.date()
    filtrados = []
    for r in registros:
        fecha_reg = r.fecha if hasattr(r, "fecha") else r.get("fecha")
        if not fecha_reg:
            continue
        if isinstance(fecha_reg, str):
            try:
                fecha_reg = datetime.strptime(fecha_reg, "%Y-%m-%d").date()
            except Exception:
                continue
        if fecha_reg > ultima_fecha_corte:
            filtrados.append(r)
    return filtrados


def consolidar_horas(registros_filtrados: list) -> list:
    """Agrupa horas por colaborador. Mantenida por compatibilidad."""
    consolidado_dict = {}
    for r in registros_filtrados:
        colab = r.colaborador if hasattr(r, "colaborador") else r.get("colaborador", "Desconocido")
        h_ord = _parse_hours(r.horas_ordinarias if hasattr(r, "horas_ordinarias") else r.get("horas_ordinarias", 0))
        h_ext = _parse_hours(r.horas_extras if hasattr(r, "horas_extras") else r.get("horas_extras", 0))
        if colab not in consolidado_dict:
            consolidado_dict[colab] = {"ordinarias": 0.0, "extras": 0.0}
        consolidado_dict[colab]["ordinarias"] += h_ord
        consolidado_dict[colab]["extras"] += h_ext
    return [
        {"colaborador": n, "horas_ordinarias": round(h["ordinarias"], 2), "horas_extras": round(h["extras"], 2)}
        for n, h in consolidado_dict.items()
    ]


def construir_detalle_diario(registros_filtrados: list) -> list:
    """Construye detalle para exportar. Mantenida por compatibilidad."""
    detalle = []
    for r in registros_filtrados:
        colab = r.colaborador if hasattr(r, "colaborador") else r.get("colaborador", "Desconocido")
        fecha = r.fecha if hasattr(r, "fecha") else r.get("fecha")
        fecha_str = fecha.strftime("%Y-%m-%d") if hasattr(fecha, "strftime") else str(fecha)
        detalle.append({
            "colaborador": colab,
            "fecha": fecha_str,
            "ingreso": r.ingreso_real if hasattr(r, "ingreso_real") else r.get("ingreso_real", ""),
            "salida": r.salida_real if hasattr(r, "salida_real") else r.get("salida_real", ""),
            "horas_ordinarias": _parse_hours(r.horas_ordinarias if hasattr(r, "horas_ordinarias") else r.get("horas_ordinarias", 0)),
            "horas_extras": _parse_hours(r.horas_extras if hasattr(r, "horas_extras") else r.get("horas_extras", 0)),
            "motivo": r.motivo if hasattr(r, "motivo") else r.get("motivo", ""),
            "comentarios": r.comentarios if hasattr(r, "comentarios") else r.get("comentarios", ""),
        })
    detalle.sort(key=lambda x: (x["colaborador"], x["fecha"]))
    return detalle


class ReglasAsistencia:
    _usuarios_precargados = False

    @classmethod
    def _resolver_username_colaborador(cls, nombre_crudo: str) -> str:
        """
        Resuelve nombre_completo o username crudo -> username normalizado (minúsculas, sin espacios).
        Bulk Preload: en la primera invocación por proceso, carga TODOS los usuarios en
        _cache_nombres_usuarios con una única consulta (elimina el N+1 del lazy loading individual).
        """
        if not cls._usuarios_precargados:
            usuarios = db.session.query(Usuario.username, Usuario.nombre_completo).all()
            for user in usuarios:
                _cache_nombres_usuarios[user.username.strip().lower()] = user.username.strip().lower()
                if user.nombre_completo:
                    _cache_nombres_usuarios[user.nombre_completo.strip().lower()] = user.username.strip().lower()
            cls._usuarios_precargados = True

        if not nombre_crudo:
            return ''

        nombre_normalizado = nombre_crudo.strip().lower()
        resultado = _cache_nombres_usuarios.get(nombre_normalizado)

        if resultado is None:
            # Invalidación reactiva: posible empleado creado después del preload. Consulta puntual.
            usuario = db.session.query(Usuario).filter(
                func.lower(Usuario.username) == nombre_normalizado
            ).first()
            if not usuario:
                usuario = db.session.query(Usuario).filter(
                    func.lower(func.trim(Usuario.nombre_completo)) == nombre_normalizado
                ).first()

            if usuario:
                resultado = usuario.username.strip().lower()
            else:
                # No existe en db_usuarios: se asume operario legado, ya es el username.
                resultado = nombre_normalizado.replace(' ', '')

            _cache_nombres_usuarios[nombre_normalizado] = resultado

        return resultado

    @classmethod
    def calcular_jornada_y_extras(cls, registro: RegistroAsistencia) -> dict:
        if not registro.ingreso_real or not registro.salida_real or registro.ingreso_real.upper() == 'AUSENTE':
            return {"horas_ordinarias": 0.0, "horas_extras": 0.0}

        ingreso_norm = _normalizar_hora(registro.ingreso_real)
        salida_norm = _normalizar_hora(registro.salida_real)

        try:
            fmt = "%H:%M"
            t_in = datetime.strptime(ingreso_norm, fmt)
            t_out = datetime.strptime(salida_norm, fmt)
        except ValueError:
            logger.warning(
                f"[NOMINA] Formato de hora irreconocible en fecha={registro.fecha}: "
                f"ingreso_real='{registro.ingreso_real}' salida_real='{registro.salida_real}'. "
                f"Registrado como 0.0/0.0 — revisar origen del dato."
            )
            return {"horas_ordinarias": 0.0, "horas_extras": 0.0}

        t_in_mins = t_in.hour * 60 + t_in.minute
        t_out_mins = t_out.hour * 60 + t_out.minute

        if t_out_mins <= t_in_mins:
            logger.warning(
                f"[NOMINA] Salida <= Ingreso en fecha={registro.fecha}: "
                f"ingreso={ingreso_norm} salida={salida_norm}. Registrado como 0.0/0.0."
            )
            return {"horas_ordinarias": 0.0, "horas_extras": 0.0}

        total_mins = t_out_mins - t_in_mins
        
        if isinstance(registro.fecha, str):
            try:
                dt_fecha = datetime.strptime(registro.fecha, '%Y-%m-%d').date()
                weekday = dt_fecha.weekday()
            except ValueError:
                weekday = 0
        else:
            weekday = registro.fecha.weekday()

        if weekday >= 5: # Sábado y Domingo
            return {
                "horas_ordinarias": 0.0,
                "horas_extras": round(total_mins / 60.0, 2)
            }

        username_limpio = cls._resolver_username_colaborador(registro.colaborador)
        perfil_id = MAPEO_PERFILES.get(username_limpio, 'ESTANDAR')
        regla_dia = PERFILES_HORARIO[perfil_id][weekday]

        w_start_dt = datetime.strptime(regla_dia['inicio'], fmt)
        w_start_mins = w_start_dt.hour * 60 + w_start_dt.minute

        w_end_dt = datetime.strptime(regla_dia['fin'], fmt)
        deduccion_mins = regla_dia['deduccion']

        w_end_mins = w_end_dt.hour * 60 + w_end_dt.minute

        # Tiempo ordinario: intersección
        ord_start = max(t_in_mins, w_start_mins)
        ord_end = min(t_out_mins, w_end_mins)
        ord_mins = max(0, ord_end - ord_start)

        if ord_mins > deduccion_mins:
            ord_mins -= deduccion_mins
        elif ord_mins > 0:
            ord_mins = 0

        # Tiempo extra: antes del inicio o después del fin
        extra_start_mins = max(0, w_start_mins - t_in_mins)
        extra_end_mins = max(0, t_out_mins - w_end_mins)
        extra_mins = extra_start_mins + extra_end_mins

        # Candado aritmético: las horas extra solo son válidas si la jornada
        # ordinaria oficial del día fue efectivamente completada. Un retiro
        # anticipado (salida_real < fin oficial) purga cualquier extra,
        # incluida la generada por llegada anticipada (extra_start_mins).
        jornada_oficial_mins = w_end_mins - w_start_mins - deduccion_mins
        if t_out_mins < w_end_mins or ord_mins < jornada_oficial_mins:
            extra_mins = 0

        return {
            "horas_ordinarias": round(ord_mins / 60.0, 2),
            "horas_extras": round(extra_mins / 60.0, 2)
        }
