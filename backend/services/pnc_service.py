"""
pnc_service.py — Capa de negocio consolidada para Producto No Conforme (PNC) y Scrap.

Reemplaza dos fuentes de fragmentación que existían antes:
  1. `normalizar_criterio()` estaba duplicada, con catálogos DISTINTOS, en
     gerencia_routes.py e inyeccion_routes.py. Ahora hay una sola versión aquí.
  2. El desglose de Inyección para el Dashboard se calculaba re-parseando con
     keywords el campo de texto libre `criterio` de db_pnc_inyeccion, lo que
     atribuía TODA la cantidad de una fila a un solo tipo de defecto aunque
     la fila mezclara varios (bug de atribución). Ahora se leen directamente
     las 5 columnas tipadas de esa tabla, que son la fuente de verdad real.

DashboardRepository (capa SQL pura) sigue sin conocer negocio: expone datos
crudos costeados; este servicio es quien normaliza, agrupa y arma el DTO.
"""
import logging
from datetime import datetime
from backend.core.sql_database import db, rollback_seguro
from backend.repositories.dashboard_repository import DashboardRepository
from backend.utils.time_utils import get_colombia_time
from sqlalchemy import text

logger = logging.getLogger(__name__)


class PncDatosInvalidosException(Exception):
    """
    Validación de negocio de PncService.registrar (código/cantidad faltantes
    o inválidos). Deliberadamente NO es un ValueError plano: antes del fix
    del ticket task_651f2d99 el bug de unpacking en registrar() también
    lanzaba `ValueError` de forma nativa, y un controlador que atrapara
    ValueError genérico lo habría convertido en 400 ocultando ese 500. Se
    mantiene el tipo separado tras el fix por si algún otro `ValueError`
    inesperado aparece más adelante en el mismo método.
    """
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


# ─────────────────────────────────────────────────────────────
# Catálogo ÚNICO de criterios normalizados (antes había dos, uno por archivo)
# Los nombres de Inyección coinciden 1:1 con COLUMNAS_TIPADAS_INYECCION para
# que el texto libre (usado solo al escribir un resumen legible) y las
# columnas tipadas (usadas para leer/agregar en el Dashboard) nunca diverjan.
# ─────────────────────────────────────────────────────────────
CRITERIOS_INYECCION = ["Quemado/Manchado", "Incompleto", "Rebaba", "Burbujas/Porosidad", "Rechupe/Deformado", "Otros"]
CRITERIOS_PULIDO = ["Rayado", "Porosidad", "Exceso de Rebaba", "Medida Incorrecta", "Mal Acabado", "Otros"]
CRITERIOS_ENSAMBLE = ["Falta de Componente", "Mal Ajuste", "Inserto Defectuoso", "Daño Físico", "Otros"]

# Mapeo directo columna tipada -> criterio normalizado, para Inyección.
# Fuente de verdad real: son 5 columnas numéricas de db_pnc_inyeccion, no texto
# libre a adivinar. Ver PncService._agrupar_inyeccion_tipado.
COLUMNAS_TIPADAS_INYECCION = {
    "quemado_manchado": "Quemado/Manchado",
    "incompleto_falta_llenado": "Incompleto",
    "rebaba_excesiva": "Rebaba",
    "burbuja_porosidad": "Burbujas/Porosidad",
    "deformacion_rechupado": "Rechupe/Deformado",
}
CRITERIO_INYECCION_SIN_CLASIFICAR = "Sin Clasificar"


class PncService:

    # ── Catálogo canónico expuesto al frontend ────────────────
    @staticmethod
    def obtener_catalogos_criterios():
        """
        Fuente única de los criterios que puede elegir el usuario. El frontend
        los consume vía GET /api/pnc/criterios en vez de hardcodear su propia
        lista: los catálogos del front divergían de estos, así que un defecto
        elegido en pantalla ('Retención', 'Contaminado') no correspondía a
        ningún bucket canónico y terminaba cayendo en 'Otros' al agregar.
        """
        return {
            "inyeccion": list(CRITERIOS_INYECCION),
            "pulido": list(CRITERIOS_PULIDO),
            "ensamble": list(CRITERIOS_ENSAMBLE),
        }

    # ── Normalización de texto libre ──────────────────────────
    @staticmethod
    def normalizar_criterio(criterio, area):
        """
        Normalizador único de texto libre de criterio -> bucket canónico.

        Para Inyección esto NO se usa para agregar el Dashboard (ver
        _agrupar_inyeccion_tipado, que lee las columnas tipadas — la fuente
        de verdad real, y el origen del fix del bug de mala atribución).
        Sigue existiendo aquí porque inyeccion_routes.py lo necesita en
        escritura, para componer el resumen legible que guarda en
        `pnc_detalle`; por eso usa el mismo catálogo canónico que
        COLUMNAS_TIPADAS_INYECCION, así ambos nunca divergen.
        """
        if not criterio:
            return "Otros"

        import re
        crit_lower = re.sub(r'\s*\(\d+\)\s*', '', str(criterio).lower().strip()).strip()

        if area == "inyeccion":
            if "incompleto" in crit_lower or "falta" in crit_lower or "escaso" in crit_lower or "llenado" in crit_lower:
                return "Incompleto"
            if "quemado" in crit_lower or "mancha" in crit_lower or "contamina" in crit_lower:
                return "Quemado/Manchado"
            if "rebaba" in crit_lower:
                return "Rebaba"
            if "burbuja" in crit_lower or "porosidad" in crit_lower:
                return "Burbujas/Porosidad"
            if "deform" in crit_lower or "rechupe" in crit_lower or "hundido" in crit_lower or "retenc" in crit_lower or "flujo" in crit_lower:
                return "Rechupe/Deformado"
            for c in CRITERIOS_INYECCION[:-1]:
                if c.lower() in crit_lower:
                    return c
            return "Otros"

        if area == "pulido":
            if "rayado" in crit_lower or "raya" in crit_lower:
                return "Rayado"
            if "porosidad" in crit_lower or "poros" in crit_lower or "burbuja" in crit_lower:
                return "Porosidad"
            if "rebaba" in crit_lower:
                return "Exceso de Rebaba"
            if "medida" in crit_lower or "incorrecta" in crit_lower or "desgaste" in crit_lower or "deform" in crit_lower:
                return "Medida Incorrecta"
            if "acabado" in crit_lower or "brillo" in crit_lower:
                return "Mal Acabado"
            for c in CRITERIOS_PULIDO[:-1]:
                if c.lower() in crit_lower:
                    return c
            return "Otros"

        if area == "ensamble":
            if "componente" in crit_lower or "falta" in crit_lower:
                return "Falta de Componente"
            if "ajuste" in crit_lower:
                return "Mal Ajuste"
            if "inserto" in crit_lower or "defectuoso" in crit_lower:
                return "Inserto Defectuoso"
            if "daño" in crit_lower or "fisico" in crit_lower or "físico" in crit_lower:
                return "Daño Físico"
            for c in CRITERIOS_ENSAMBLE[:-1]:
                if c.lower() in crit_lower:
                    return c
            return "Otros"

        return "Otros"

    @staticmethod
    def _parse_fechas(fecha_inicio, fecha_fin):
        start_date = None
        end_date = None
        if fecha_inicio:
            try:
                start_date = datetime.strptime(str(fecha_inicio)[:10], '%Y-%m-%d')
            except Exception as ex:
                logger.warning(f"[PncService] fecha_inicio inválida: {fecha_inicio} ({ex})")
        if fecha_fin:
            try:
                end_date = datetime.strptime(str(fecha_fin)[:10], '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            except Exception as ex:
                logger.warning(f"[PncService] fecha_fin inválida: {fecha_fin} ({ex})")
        return start_date, end_date

    # ── Método principal: reemplaza el bloque de ~140 líneas de gerencia_routes.py ──
    @classmethod
    def obtener_metricas_pnc_consolidadas(cls, fecha_inicio=None, fecha_fin=None):
        """
        Dashboard PNC consolidado (Inyección + Pulido + Ensamble), con fecha
        opcional. Cantidades: Inyección por columnas tipadas (sin bug de
        atribución); Pulido/Ensamble por criterio de texto normalizado.
        Dinero: delegado a DashboardRepository, agrupado por criterio.
        """
        try:
            start_date, end_date = cls._parse_fechas(fecha_inicio, fecha_fin)
            params = {'start': start_date, 'end': end_date}
            filt_iny = " WHERE CAST(i.fecha_inicia AS DATE) BETWEEN :start AND :end" if start_date and end_date else " WHERE 1=1"
            filt_pul = " WHERE CAST(d.fecha AS DATE) BETWEEN :start AND :end" if start_date and end_date else " WHERE 1=1"
            filt_ens = " WHERE CAST(e.fecha AS DATE) BETWEEN :start AND :end" if start_date and end_date else " WHERE 1=1"
            filt_gen_iny = " WHERE CAST(fecha_inicia AS DATE) BETWEEN :start AND :end" if start_date and end_date else " WHERE 1=1"
            filt_gen_pul = " WHERE CAST(fecha AS DATE) BETWEEN :start AND :end" if start_date and end_date else " WHERE 1=1"
            filt_gen_ens = " WHERE CAST(fecha AS DATE) BETWEEN :start AND :end" if start_date and end_date else " WHERE 1=1"

            # 1. Producción buena por área (base para FPY / % PNC)
            buenas_iny = cls._sum_buenas('db_inyeccion', 'cantidad_real', filt_gen_iny, params)
            buenas_pul = cls._sum_buenas('db_pulido', 'cantidad_real', filt_gen_pul, params)
            buenas_ens = cls._sum_buenas('db_ensambles', 'cantidad', filt_gen_ens, params)

            # 2. Inyección: SUM vertical de las 5 columnas tipadas (fix del bug de atribución)
            iny_por_criterio, iny_por_ref = cls._agrupar_inyeccion_tipado(filt_iny, params)

            # 3. Pulido / Ensamble: agrupación por criterio de texto libre normalizado
            pul_por_criterio, pul_por_ref = cls._agrupar_texto_libre('pulido', filt_pul, params)
            ens_por_criterio, ens_por_ref = cls._agrupar_texto_libre('ensamble', filt_ens, params)

            total_iny_pnc = sum(iny_por_criterio.values())
            total_pul_pnc = sum(pul_por_criterio.values())
            total_ens_pnc = sum(ens_por_criterio.values())

            totales_area = {
                "inyeccion": {"pnc": total_iny_pnc, "buenas": buenas_iny},
                "pulido":    {"pnc": total_pul_pnc, "buenas": buenas_pul},
                "ensamble":  {"pnc": total_ens_pnc, "buenas": buenas_ens},
            }
            modos_falla_area = {
                "inyeccion": iny_por_criterio,
                "pulido": pul_por_criterio,
                "ensamble": ens_por_criterio,
            }

            # 4. Pareto de referencias
            pareto_dict = {}
            for origen in (iny_por_ref, pul_por_ref, ens_por_ref):
                for ref, qty in origen.items():
                    pareto_dict[ref] = pareto_dict.get(ref, 0.0) + qty
            sorted_pareto = sorted(pareto_dict.items(), key=lambda x: x[1], reverse=True)[:10]
            pareto_referencias = [{"referencia": ref, "cantidad": val} for ref, val in sorted_pareto]

            # 5. FPY Global / % PNC (idéntico al cálculo original, sin cambios de fórmula)
            def _yield(buenas, pnc):
                denom = buenas + pnc
                return buenas / denom if denom > 0 else 1.0

            fpy_global = round(
                _yield(buenas_iny, total_iny_pnc)
                * _yield(buenas_pul, total_pul_pnc)
                * _yield(buenas_ens, total_ens_pnc)
                * 100, 2
            )
            pnc_global_percentage = round(100 - fpy_global, 2)

            # 6. Dinero por criterio (Inyección + Pulido), vía DashboardRepository
            modos_falla_dinero_area = cls._costear_por_criterio(
                fecha_inicio=start_date.strftime('%Y-%m-%d') if start_date else None,
                fecha_fin=end_date.strftime('%Y-%m-%d') if end_date else None,
            )

            return {
                "success": True,
                "totales_area": totales_area,
                "modos_falla_area": modos_falla_area,
                "modos_falla_dinero_area": modos_falla_dinero_area,
                "pareto_referencias": pareto_referencias,
                "pnc_global_percentage": pnc_global_percentage,
                "fpy_global": fpy_global,
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"[PncService.obtener_metricas_pnc_consolidadas] {e}")
            return {
                "success": False,
                "error": "Error al calcular métricas de PNC",
                "totales_area": {}, "modos_falla_area": {}, "modos_falla_dinero_area": {},
                "pareto_referencias": [], "pnc_global_percentage": 0, "fpy_global": 100,
            }

    # ── Registro/consulta de PNC directo (db_pnc) — movido desde app.py ────
    @staticmethod
    def registrar(data):
        """
        Registra un evento PNC en db_pnc (distinto de registrar_pnc_detalle,
        que escribe en las tablas tipadas por proceso) y descuenta inventario
        de STOCK_BODEGA.

        FIX (ticket task_651f2d99): `StockService.registrar_salida` devuelve
        un único dict, nunca una tupla `(bool, str)`. El código original (y
        su migración tal cual desde backend/app.py) lo desempaquetaba en 2
        variables, lo que lanzaba `ValueError: too many values to unpack`
        siempre que la operación llegaba hasta aquí — todo POST /api/pnc
        crasheaba con 500. Se corrige comprobando la clave "error" del dict,
        igual que ya hace StockService.mover_inventario_entre_etapas.
        """
        if not data:
            raise PncDatosInvalidosException('No se recibieron datos')

        from backend.models.sql_models import Pnc

        codigo_entrada = str(data.get('codigo_producto', '')).strip()
        if not codigo_entrada:
            raise PncDatosInvalidosException('Cód. de producto requerido')

        id_codigo = codigo_entrada.split(' ')[0].upper()
        cantidad = float(data.get("cantidad", 0))

        if cantidad <= 0:
            raise PncDatosInvalidosException('Cantidad debe ser mayor a 0')

        responsable = str(data.get('responsable') or '').strip()
        if not responsable:
            # Fallback defensivo: si el frontend no envió responsable (fallo
            # de fetch, catálogo vacío, etc.) se atribuye al usuario
            # autenticado en vez de rechazar el registro, para no bloquear
            # la trazabilidad de la merma por un problema de UI ajeno al dato.
            from flask import request
            from backend.utils.auth_middleware import obtener_identidad_segura
            usuario_identidad, _rol = obtener_identidad_segura(request)
            responsable = str(usuario_identidad or '').strip()
        if not responsable:
            raise PncDatosInvalidosException('Responsable requerido: toda merma debe quedar atribuida a una persona')

        try:
            ahora = get_colombia_time()
            fecha_str = data.get("fecha", ahora.strftime("%Y-%m-%d"))
            fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")

            nuevo_pnc = Pnc(
                id_pnc=data.get("id_pnc") or f"PNC-{ahora.strftime('%Y%m%d%H%M%S')}",
                fecha=fecha_dt,
                id_codigo=id_codigo,
                cantidad=cantidad,
                criterio=data.get("criterio", "No especificado"),
                codigo_ensamble=data.get("notas", ""),  # Mapeo solicitado: Notas -> codigo_ensamble
                responsable=responsable
            )
            db.session.add(nuevo_pnc)

            from backend.services.stock_service import StockService
            resultado_salida = StockService.registrar_salida(id_codigo, cantidad, "STOCK_BODEGA")
            if "error" in resultado_salida:
                logger.warning(f" ⚠️ [PNC SQL] Advertencia en inventario: {resultado_salida['error']}")
                # Mantenemos la política de "By-pass" si el usuario lo prefiere,
                # pero aquí el registro de calidad es la prioridad.

            db.session.commit()
            logger.info(f" ✅ PNC Guardado en SQL: {id_codigo} ({cantidad} piezas)")

            return {
                'mensaje': f"PNC registrado en SQL y descontado de BODEGA: {cantidad} piezas de {id_codigo}",
                'id_pnc': nuevo_pnc.id_pnc
            }
        except Exception as e:
            db.session.rollback()
            if not isinstance(e, PncDatosInvalidosException):
                logger.error(f" ❌ ERROR PncService.registrar: {str(e)}")
            raise

    @staticmethod
    def obtener_consolidado():
        """
        Registros de PNC consolidados (Inyección + Pulido) para el panel de calidad.

        `responsable` sale de la columna homónima de cada tabla: es la persona
        real (operario de inyección / operaria de pulido) que produjo la merma.
        Antes se emitía el literal del área ('Inyección' / 'Pulido'), que no
        identificaba a nadie. Las filas anteriores a la migración no tienen
        persona atribuible y se marcan como tal en vez de inventar una.
        """
        from backend.models.sql_models import PncInyeccion, PncPulido

        consolidado = []

        for p in PncInyeccion.query.order_by(PncInyeccion.id_row.desc()).limit(100).all():
            consolidado.append({
                'id': p.id_pnc_inyeccion,
                'fecha': p.id_inyeccion.split('-')[1] if '-' in p.id_inyeccion else 'S/F',
                'proceso': 'inyeccion',
                'codigo_producto': p.id_codigo,
                'responsable': p.responsable or 'Sin registrar',
                'validado_por': p.validado_por or '',
                'cantidad': p.cantidad,
                'criterio_pnc': p.criterio,
                'estado': 'pendiente',
                'observaciones': p.codigo_ensamble or ''
            })

        for p in PncPulido.query.order_by(PncPulido.id_row.desc()).limit(100).all():
            consolidado.append({
                'id': p.id_pnc_pulido,
                'fecha': 'S/F',
                'proceso': 'pulido',
                'codigo_producto': p.codigo,
                'responsable': p.responsable or 'Sin registrar',
                'validado_por': p.validado_por or '',
                'cantidad': p.cantidad,
                'criterio_pnc': p.criterio,
                'estado': 'pendiente',
                'observaciones': ''
            })

        return consolidado

    @staticmethod
    def resolver(id_pnc):
        """Marca un PNC como resuelto. Stub: no existe todavía una columna 'estado' oficial en db_pnc."""
        return f"PNC {id_pnc} marcado como resuelto"

    # ── Helpers privados ──────────────────────────────────────

    @staticmethod
    def _sum_buenas(tabla, columna, filt, params):
        sql = text(f"SELECT COALESCE(SUM({columna}), 0) FROM {tabla} {filt}")
        row = db.session.execute(sql, params).fetchone()
        return float(row[0] or 0) if row else 0.0

    @staticmethod
    def _agrupar_inyeccion_tipado(filt_iny, params):
        """
        UNPIVOT vertical de las 5 columnas tipadas de db_pnc_inyeccion: cada
        columna se convierte en su propio bucket de criterio ya normalizado,
        sin adivinar nada por texto. Un bucket 'Sin Clasificar' recoge el
        remanente de filas donde `cantidad` > 0 pero las 5 columnas tipadas
        están en 0 (p.ej. el PNC de cierre/validación de lote, que usa un
        criterio de texto libre y no llena las columnas tipadas) — así el
        total nunca se pierde silenciosamente, solo cae en un bucket honesto.
        """
        union_cols = " UNION ALL ".join(
            f"""SELECT TRIM(REPLACE(p.id_codigo::TEXT, 'FR-', '')) as ref,
                       '{criterio}' as criterio, SUM(COALESCE(p.{col}, 0)) as qty
                FROM db_pnc_inyeccion p
                LEFT JOIN (
                    SELECT DISTINCT ON (id_inyeccion) id_inyeccion, fecha_inicia
                    FROM db_inyeccion WHERE fecha_inicia IS NOT NULL
                    ORDER BY id_inyeccion, fecha_inicia DESC
                ) i ON p.id_inyeccion = i.id_inyeccion
                {filt_iny}
                GROUP BY 1"""
            for col, criterio in COLUMNAS_TIPADAS_INYECCION.items()
        )

        sql = text(f"""
            WITH iny_pivot AS (
                {union_cols}
                UNION ALL
                SELECT
                    TRIM(REPLACE(p.id_codigo::TEXT, 'FR-', '')) as ref,
                    '{CRITERIO_INYECCION_SIN_CLASIFICAR}' as criterio,
                    SUM(GREATEST(COALESCE(p.cantidad, 0) - (
                        COALESCE(p.quemado_manchado, 0) + COALESCE(p.incompleto_falta_llenado, 0) +
                        COALESCE(p.rebaba_excesiva, 0) + COALESCE(p.burbuja_porosidad, 0) +
                        COALESCE(p.deformacion_rechupado, 0)
                    ), 0)) as qty
                FROM db_pnc_inyeccion p
                LEFT JOIN (
                    SELECT DISTINCT ON (id_inyeccion) id_inyeccion, fecha_inicia
                    FROM db_inyeccion WHERE fecha_inicia IS NOT NULL
                    ORDER BY id_inyeccion, fecha_inicia DESC
                ) i ON p.id_inyeccion = i.id_inyeccion
                {filt_iny}
                GROUP BY 1
            )
            SELECT criterio, ref, SUM(qty) as qty
            FROM iny_pivot
            GROUP BY criterio, ref
            HAVING SUM(qty) > 0
        """)
        rows = db.session.execute(sql, params).fetchall()

        por_criterio, por_ref = {}, {}
        for criterio, ref, qty in rows:
            qty_f = float(qty or 0)
            por_criterio[criterio] = por_criterio.get(criterio, 0.0) + qty_f
            if ref:
                ref_key = str(ref).strip().upper()
                por_ref[ref_key] = por_ref.get(ref_key, 0.0) + qty_f
        return por_criterio, por_ref

    @staticmethod
    def _agrupar_texto_libre(area, filt, params):
        """Pulido / Ensamble: sin columnas tipadas, se normaliza el texto libre de `criterio`."""
        if area == 'pulido':
            sql = text(f"""
                SELECT p.criterio, p.cantidad, p.codigo
                FROM db_pnc_pulido p
                LEFT JOIN (
                    SELECT DISTINCT ON (id_pulido::text) id_pulido::text as id_pulido, fecha
                    FROM db_pulido WHERE fecha IS NOT NULL
                    ORDER BY id_pulido::text, fecha DESC
                ) d ON p.id_pulido::text = d.id_pulido
                {filt}
            """)
        else:
            sql = text(f"""
                SELECT p.criterio, p.cantidad, p.id_codigo
                FROM db_pnc_ensamble p
                LEFT JOIN db_ensambles e ON p.id_ensamble = e.id_ensamble
                {filt}
            """)
        rows = db.session.execute(sql, params).fetchall()

        por_criterio, por_ref = {}, {}
        for criterio_raw, cantidad, ref in rows:
            qty = float(cantidad or 0)
            if qty <= 0:
                continue
            crit_key = PncService.normalizar_criterio(criterio_raw, area)
            por_criterio[crit_key] = por_criterio.get(crit_key, 0.0) + qty
            if ref:
                ref_key = str(ref).strip().upper()
                por_ref[ref_key] = por_ref.get(ref_key, 0.0) + qty
        return por_criterio, por_ref

    @classmethod
    def _costear_por_criterio(cls, fecha_inicio=None, fecha_fin=None):
        """
        Trae las filas crudas costeadas de DashboardRepository (Inyección ya
        pivotada por columna tipada, Pulido con criterio de texto libre) y
        las agrupa por criterio normalizado. La normalización vive aquí
        (capa de negocio); DashboardRepository solo hizo el JOIN contra
        db_costos y la multiplicación cantidad × costo_unitario.
        """
        filas = DashboardRepository.get_pnc_detalle_costeado(
            desde=fecha_inicio,
            hasta=fecha_fin,
            columnas_tipadas_inyeccion=COLUMNAS_TIPADAS_INYECCION,
        )

        resultado = {"inyeccion": {}, "pulido": {}, "ensamble": {}}
        for fila in filas:
            area = fila["area"]
            costo = float(fila["costo_total"] or 0)
            if costo <= 0 or area not in resultado:
                continue

            if area == "inyeccion":
                # Ya viene con el criterio tipado correcto, no requiere normalizar.
                crit_key = fila["criterio"]
            else:
                crit_key = cls.normalizar_criterio(fila["criterio"], area)

            resultado[area][crit_key] = resultado[area].get(crit_key, 0.0) + costo

        return resultado


pnc_service = PncService()
