"""
Servicio de Ejecución de Ensamble (iniciar/finalizar sesión + BOM).
Extraído de backend/app.py.
"""
import json
import logging
import uuid
from datetime import datetime
from sqlalchemy import or_
from backend.core.sql_database import db
from backend.models.sql_models import ChecklistEnsamble, Ensamble, OperacionLog, PncEnsamble, ProgramacionEnsamble, Producto, OpGenerada
from backend.services.audit_service import AuditService, OwnershipMismatchException
from backend.services.bom_service import calcular_descuentos_ensamble
from backend.services.op_numerador_service import OpNumeradorService
from backend.services.stock_service import StockService
from backend.utils.formatters import normalizar_codigo, preservar_o_normalizar_prefijo
from backend.utils.time_utils import get_colombia_time

logger = logging.getLogger(__name__)

# Procesos de planta del checklist de Ensamble, en el orden en que se
# muestran/reportan. "ensamble_crudo" va primero: es el único proceso que
# SIEMPRE aplica (productos simples que no llevan nada más). "ensamble" (a
# secas) es un segundo armado que se hace después de Horno 1, con la pieza
# ya curada -- distinto del crudo. Los demás son adicionales y pueden
# marcarse NO_APLICA en productos que no los requieren.
PROCESOS_CHECKLIST = [
    'ensamble_crudo', 'rayada_carcaza', 'rayada_interno',
    'pintura', 'horno1', 'ensamble', 'cerrada', 'horno2',
]
ESTADOS_CHECKLIST_VALIDOS = {'PENDIENTE', 'HECHO', 'NO_APLICA'}


def _checklist_default():
    """Checklist en blanco: los 8 procesos en PENDIENTE."""
    return {proc: 'PENDIENTE' for proc in PROCESOS_CHECKLIST}


def _checklist_a_dict(row):
    """Serializa una fila de ChecklistEnsamble (o None) a dict {proceso: estado}."""
    if not row:
        return _checklist_default()
    return {proc: getattr(row, f'{proc}_estado') or 'PENDIENTE' for proc in PROCESOS_CHECKLIST}


def _condicion_checklist_incompleto():
    """
    Condición SQL: la fila de checklist no existe o tiene algún proceso en
    PENDIENTE. Requiere que la query venga de un outerjoin contra
    ChecklistEnsamble (ChecklistEnsamble.id_prog == ProgramacionEnsamble.id_prog).

    Se usa para que una meta no se dé por "resuelta" en las listas de
    trabajo solo porque cantidad_realizada llegó a cantidad_objetivo -- ese
    es un eje independiente del checklist de procesos (ver reportar_multi).
    """
    return or_(
        ChecklistEnsamble.id_prog.is_(None),
        or_(*[getattr(ChecklistEnsamble, f'{proc}_estado') == 'PENDIENTE' for proc in PROCESOS_CHECKLIST])
    )


class BomNoDisponibleException(Exception):
    """Se lanza cuando calcular_descuentos_ensamble no puede resolver la BOM del producto."""
    def __init__(self, message="No se pudo calcular la BOM del producto"):
        self.message = message
        super().__init__(self.message)


class StockInsuficienteException(Exception):
    """Se lanza cuando StockService no puede completar el descuento de un componente del BOM (stock insuficiente o producto no encontrado en SQL)."""
    def __init__(self, message="No se pudo descontar el stock de un componente del BOM"):
        self.message = message
        super().__init__(self.message)


class ChecklistIncompletoException(Exception):
    """Se lanza al intentar cerrar_jornada con checklists de procesos sin terminar.
    Cierre de jornada (reunión 2026-08-25): señal explícita, no un cron a una
    hora fija -- si Albeiro reporta tarde y Zoe ya descargó, el archivo queda
    desfasado contra WO sin que nadie se entere. forzar=True (solo ROL_ADMINS,
    validado en la ruta) es la única forma de saltarse esto."""
    def __init__(self, metas_incompletas):
        self.metas_incompletas = metas_incompletas
        self.message = (
            f"Hay {len(metas_incompletas)} meta(s) con checklist de procesos incompleto. "
            f"No se puede cerrar la jornada hasta terminarlas (o forzar el cierre como admin)."
        )
        super().__init__(self.message)


class EnsambleService:

    @staticmethod
    def obtener_bom_desde_producto(codigo_entrada):
        """Dado un código de producto, retorna su BOM completo desde NUEVA_FICHA_MAESTRA."""
        if not codigo_entrada:
            raise ValueError('Codigo producto requerido')

        codigo_sistema = normalizar_codigo(codigo_entrada)
        bom_res = calcular_descuentos_ensamble(codigo_sistema, 1)

        if bom_res.get('success'):
            componentes_bom = bom_res['componentes']
            opcion = {
                'codigo_ensamble': codigo_entrada,
                'buje_origen': codigo_sistema,
                'qty': componentes_bom[0].get('cantidad_por_kit', 1) if componentes_bom else 1,
                'tipo': 'producto',
                'componentes': [
                    {'buje_origen': c['codigo_inventario'], 'qty': c['cantidad_por_kit']} for c in componentes_bom
                ]
            }
            return {'codigo_sistema': codigo_sistema, 'opciones': [opcion]}
        return {'codigo_sistema': codigo_sistema, 'opciones': []}

    @staticmethod
    def crear_o_actualizar_programacion(data):
        """
        Crea o actualiza (UPSERT) una meta de programación de ensamble. Ante
        conflicto en uq_programacion_ensamble_activa (fecha_programada +
        id_codigo + op_numero, solo entre metas no completadas) actualiza
        cantidad_objetivo y estado.

        El índice único es parcial (WHERE estado <> 'COMPLETADO') a propósito:
        si la única fila existente con esa clave ya está COMPLETADA, este
        INSERT no encuentra conflicto y crea una fila nueva (con su propio
        id_prog) en vez de reabrir la completada. Reabrir la fila completada
        arrastraría su cantidad_realizada vieja, porque esa columna se
        recalcula en reportar_multi sumando producción por id_prog -- ver
        migrate_programacion_ensamble_unique_activa.py.

        OP automática (reunión 2026-08-25): si el payload no trae op_numero,
        se reserva vía OpNumeradorService.obtener_o_reservar('ENSAMBLE',
        fecha_prog) -- SIN máquina, por diseño: la reserva es por día, así
        que TODO lo que programen Daniel/Nathalia para el mismo día cae bajo
        la MISMA OP (una OP diaria multi-línea, como se acordó). Verificado
        contra la base real 2026-08-25: cero filas activas con op_numero NULL
        en este momento, así que no hay riesgo de que este cambio choque con
        una fila vieja y duplique -- si el tablero llegara a tener algo
        pendiente sin OP al momento de desplegar esto, hay que vaciarlo antes
        (ver riesgo documentado en el plan).
        """
        if not data:
            raise ValueError('No data provided')

        id_codigo = data.get('id_codigo')
        cantidad_objetivo = int(data.get('cantidad_objetivo', 0))
        fecha_str = data.get('fecha_programada')

        if not id_codigo or cantidad_objetivo <= 0 or not fecha_str:
            raise ValueError('Datos incompletos')

        fecha_prog = datetime.strptime(fecha_str, '%Y-%m-%d').date()

        # Retrofit de metas viejas sin OP (hallazgo 2026-08-31, día de
        # lanzamiento): el índice único de este UPSERT incluye
        # COALESCE(op_numero, '') -- una fila existente con op_numero NULL
        # tiene clave distinta de cualquier intento nuevo (que siempre trae
        # un op_numero recién reservado), así que NUNCA colisionan. El
        # comentario original de esta función asumía "cero filas pendientes
        # sin OP al momento de desplegar" como precondición para que el
        # UPSERT normal fuera seguro -- si esa precondición deja de ser
        # cierta (como pasó hoy: 4 metas reales de antes de esta feature
        # seguían con op_numero NULL), el UPSERT de abajo no actualiza la
        # fila vieja, crea una fila DUPLICADA con el mismo id_codigo/fecha,
        # dejando dos metas activas para lo mismo -- una sin OP acumulando
        # el avance real reportado, y otra con OP nueva que nunca se mueve.
        # Este bloque cierra el hueco de raíz: si ya existe una meta PENDIENTE/
        # EN_PROCESO para (fecha, id_codigo) sin OP, se le asigna el número
        # en la MISMA fila en vez de intentar un insert que sea siempre
        # divergente.
        op_numero = data.get('op_numero')
        if not op_numero:
            existente_sin_op = db.session.query(ProgramacionEnsamble).filter(
                ProgramacionEnsamble.fecha_programada == fecha_prog,
                ProgramacionEnsamble.id_codigo == id_codigo,
                ProgramacionEnsamble.op_numero.is_(None),
                ProgramacionEnsamble.estado != 'COMPLETADO',
            ).first()
            if existente_sin_op:
                op_generada = OpNumeradorService.obtener_o_reservar('ENSAMBLE', fecha_prog)
                existente_sin_op.op_numero = op_generada.numero_op
                existente_sin_op.cantidad_objetivo = cantidad_objetivo
                db.session.commit()
                return {'id_prog': existente_sin_op.id_prog, 'op_numero': existente_sin_op.op_numero}

            op_generada = OpNumeradorService.obtener_o_reservar('ENSAMBLE', fecha_prog)
            op_numero = op_generada.numero_op

        from sqlalchemy.dialects.postgresql import insert
        from sqlalchemy import text

        stmt = insert(ProgramacionEnsamble).values(
            id_codigo=id_codigo,
            cantidad_objetivo=cantidad_objetivo,
            op_numero=op_numero,
            fecha_programada=fecha_prog,
            estado='PENDIENTE'
        )

        stmt = stmt.on_conflict_do_update(
            index_elements=['fecha_programada', 'id_codigo', text("COALESCE(op_numero, '')")],
            index_where=text("estado <> 'COMPLETADO'"),
            set_={
                'cantidad_objetivo': stmt.excluded.cantidad_objetivo,
                'estado': stmt.excluded.estado
            }
        ).returning(ProgramacionEnsamble.id_prog)

        try:
            res = db.session.execute(stmt).fetchone()
            db.session.commit()
            return {'id_prog': res[0] if res else None, 'op_numero': op_numero}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error al crear/actualizar programación ensamble: {e}")
            raise

    @staticmethod
    def cerrar_jornada(fecha_str, usuario, forzar=False, motivo=None):
        """
        Cierre de jornada de ensamble (reunión 2026-08-25): marca la OP del
        día como LISTA_EXPORTAR para que la vista de Zoe pueda descargarla al
        día siguiente. Es un acto explícito, no un cron a una hora fija --
        ver ChecklistIncompletoException.

        Rechaza el cierre si queda alguna meta de ese día con el checklist de
        procesos incompleto (mismo predicado que ya usan listar_tareas_pendientes
        / listar_historial_metas -- SIN filtrar por estado de unidades, porque
        una meta completada en cantidad puede seguir con procesos pendientes,
        son ejes independientes). forzar=True lo salta -- el guard de que solo
        ROL_ADMINS puede forzar vive en la ruta, no aquí; este método solo
        exige que venga un motivo cuando se fuerza, para que quede auditado.

        Puede lanzar ValueError (fecha inválida, sin OP reservada ese día,
        o forzar sin motivo) o ChecklistIncompletoException.
        """
        if not fecha_str:
            raise ValueError('fecha es obligatoria')

        try:
            fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(f"Formato de fecha inválido: {fecha_str!r}. Usar YYYY-MM-DD")

        if forzar and not (motivo or '').strip():
            raise ValueError('Cerrar la jornada de forma forzada requiere indicar un motivo')

        metas_incompletas = ProgramacionEnsamble.query.outerjoin(
            ChecklistEnsamble, ChecklistEnsamble.id_prog == ProgramacionEnsamble.id_prog
        ).filter(
            ProgramacionEnsamble.fecha_programada == fecha,
            _condicion_checklist_incompleto()
        ).all()

        if metas_incompletas and not forzar:
            raise ChecklistIncompletoException([
                {'id_prog': t.id_prog, 'id_codigo': t.id_codigo, 'estado': t.estado}
                for t in metas_incompletas
            ])

        op = db.session.query(OpGenerada).filter(
            OpGenerada.ambito == 'ENSAMBLE',
            OpGenerada.fecha_produccion == fecha,
            OpGenerada.estado != 'ANULADA'
        ).first()

        if not op:
            raise ValueError(f"No hay ninguna OP de ensamble reservada para {fecha} -- no se programó nada ese día")

        op.estado = 'LISTA_EXPORTAR'
        db.session.commit()

        if forzar:
            logger.warning(
                f"[CIERRE JORNADA] {op.numero_op} cerrada de forma FORZADA por {usuario!r}. "
                f"Motivo: {motivo!r}. Metas con checklist incompleto: {len(metas_incompletas)}."
            )
        else:
            logger.info(f"[CIERRE JORNADA] {op.numero_op} cerrada por {usuario!r}.")

        return {
            'numero_op': op.numero_op,
            'estado': op.estado,
            'forzado': forzar,
            'metas_con_checklist_incompleto': len(metas_incompletas)
        }

    @staticmethod
    def cerrar_jornada_automatica(fecha_str=None, usuario='SISTEMA (auto 22:00)'):
        """
        Red de seguridad (plan 2026-08-28): si a las 22:00 la OP de ensamble
        del día sigue en RESERVADA porque el responsable olvidó darle al
        botón manual, se cierra sola -- pero SOLO si el checklist de
        procesos YA está completo. Si sigue con procesos de verdad
        pendientes, NO se fuerza: eso taparía trabajo sin terminar, justo lo
        que ChecklistIncompletoException existe para evitar. Ese caso queda
        para revisión manual/admin al día siguiente, no se silencia.

        Deliberadamente NO toca la OP si su estado ya NO es RESERVADA
        (Albeiro ya la cerró él mismo, o Zoe ya la exportó): cerrar_jornada()
        sobreescribe el estado sin comparar el actual, así que llamarlo
        sobre una OP ya EXPORTADA/CONFIRMADA_WO la regresaría a
        LISTA_EXPORTAR -- un downgrade de estado real, no un no-op.

        Pensada para un proceso programado externo (Tarea Programada de
        Windows en planta, mismo patrón que agente_wo_cartera.py) -- ver
        POST/GET /api/ensamble/cerrar_jornada_auto, protegido con SYNC_TOKEN.
        """
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date() if fecha_str else get_colombia_time().date()

        op = db.session.query(OpGenerada).filter(
            OpGenerada.ambito == 'ENSAMBLE',
            OpGenerada.fecha_produccion == fecha,
            OpGenerada.estado != 'ANULADA'
        ).first()

        if not op:
            return {'accion': 'SIN_OP', 'numero_op': None, 'fecha': str(fecha)}

        if op.estado != 'RESERVADA':
            return {'accion': 'SIN_CAMBIOS', 'numero_op': op.numero_op, 'estado_actual': op.estado}

        try:
            resultado = EnsambleService.cerrar_jornada(fecha_str=fecha.strftime('%Y-%m-%d'), usuario=usuario, forzar=False)
            logger.info(f"[CIERRE AUTO 22:00] {resultado['numero_op']} cerrada automáticamente (checklist ya estaba completo).")
            return {'accion': 'CERRADA_AUTO', **resultado}
        except ChecklistIncompletoException as e:
            logger.warning(
                f"[CIERRE AUTO 22:00] {op.numero_op} sigue con checklist incompleto "
                f"({len(e.metas_incompletas)} meta(s)) -- NO se fuerza. Queda para revisión manual."
            )
            return {
                'accion': 'CHECKLIST_INCOMPLETO', 'numero_op': op.numero_op,
                'metas_incompletas': e.metas_incompletas,
            }

    @staticmethod
    def obtener_bom_con_stock(id_codigo):
        """
        Retorna el BOM de un producto con el stock actual de cada componente
        en P. TERMINADO y cuántas unidades del producto final alcanza a armar
        ese stock (stock_almacen // cantidad_por_kit).
        """
        bom_res = calcular_descuentos_ensamble(id_codigo, 1)  # Cantidad 1 para ver el ratio

        if not bom_res.get('success'):
            raise BomNoDisponibleException(bom_res.get('error') or 'BOM no disponible')

        componentes = bom_res.get('componentes', [])
        resultado = []

        for comp in componentes:
            codigo_inv = comp['codigo_inventario']
            producto = Producto.query.filter_by(codigo_sistema=codigo_inv).first()

            # La ficha técnica mezcla piezas físicas (bujes, carcazas) con
            # insumos/químicos (pegantes, TPU, silicona) que no se llevan en
            # db_productos -- estos últimos no tienen a qué almacén
            # descontarles stock, así que se excluyen del BOM de ensamble en
            # vez de generar un registro de consumo fantasma sin movimiento
            # de inventario real detrás.
            if not producto:
                logger.debug(f"[ENSAMBLE-BOM] Excluyendo componente sin ficha de inventario: {codigo_inv} ({comp['codigo_ficha']})")
                continue

            stock = float(producto.p_terminado or 0)
            ratio = float(comp['cantidad_por_kit'])
            alcanza = int(stock // ratio) if ratio > 0 else 0

            resultado.append({
                'componente': comp['codigo_ficha'],
                'codigo_inventario': codigo_inv,
                'stock_almacen': stock,
                'cantidad_por_unidad': ratio,
                'alcanza_para': alcanza
            })

        return {
            'id_codigo': id_codigo,
            'componentes': resultado
        }

    @staticmethod
    def listar_tareas_pendientes():
        """Lista programaciones con trabajo real pendiente -- en unidades
        (estado != COMPLETADO) o en checklist de procesos (ver
        _condicion_checklist_incompleto). Una meta que ya llegó al 100% en
        unidades pero todavía tiene procesos sin marcar NO desaparece de
        esta lista: son dos ejes independientes, y al operario le sigue
        faltando terminar de marcar el checklist."""
        tareas = ProgramacionEnsamble.query.outerjoin(
            ChecklistEnsamble, ChecklistEnsamble.id_prog == ProgramacionEnsamble.id_prog
        ).filter(
            or_(
                ProgramacionEnsamble.estado != 'COMPLETADO',
                _condicion_checklist_incompleto()
            )
        ).order_by(ProgramacionEnsamble.fecha_programada.asc()).all()

        ids_prog = [t.id_prog for t in tareas]
        checklists_por_id_prog = {}
        if ids_prog:
            filas_checklist = ChecklistEnsamble.query.filter(
                ChecklistEnsamble.id_prog.in_(ids_prog)
            ).all()
            checklists_por_id_prog = {row.id_prog: _checklist_a_dict(row) for row in filas_checklist}

        resultado = []
        for t in tareas:
            faltante = max(0, t.cantidad_objetivo - t.cantidad_realizada)
            resultado.append({
                'id_prog': t.id_prog,
                'id_codigo': t.id_codigo,
                'cantidad_objetivo': t.cantidad_objetivo,
                'cantidad_realizada': t.cantidad_realizada,
                'faltante': faltante,
                'fecha_programada': t.fecha_programada.strftime('%Y-%m-%d') if t.fecha_programada else '',
                'estado': t.estado,
                'checklist': checklists_por_id_prog.get(t.id_prog, _checklist_default())
            })
        return resultado

    @staticmethod
    def listar_historial_metas():
        """
        Panel "Historial de Metas" (pestaña Programación): todo lo que
        todavía necesita algo -- en unidades (estado != COMPLETADO, sin
        importar el día, para que algo pendiente de ayer se siga viendo
        hoy) o en checklist de procesos (ver _condicion_checklist_incompleto,
        mismo criterio que listar_tareas_pendientes) -- más lo que quedó
        totalmente resuelto HOY (unidades y checklist), como confirmación
        rápida de cierre sin tener que abrir el archivo completo de
        "Completadas". Lo resuelto de hace más de un día ya no aparece aquí;
        eso es justamente lo que evita que este panel crezca sin límite con
        años de historial (a diferencia de GET /programacion, que sigue
        trayendo todo para el modal de "Completadas").
        """
        hoy = get_colombia_time().date()
        schedules = ProgramacionEnsamble.query.outerjoin(
            ChecklistEnsamble, ChecklistEnsamble.id_prog == ProgramacionEnsamble.id_prog
        ).filter(
            or_(
                ProgramacionEnsamble.estado != 'COMPLETADO',
                _condicion_checklist_incompleto(),
                ProgramacionEnsamble.fecha_programada == hoy
            )
        ).order_by(
            ProgramacionEnsamble.estado.desc(),
            ProgramacionEnsamble.fecha_programada.asc()
        ).limit(12).all()

        ids_prog = [s.id_prog for s in schedules]
        checklists_por_id_prog = {}
        if ids_prog:
            filas_checklist = ChecklistEnsamble.query.filter(
                ChecklistEnsamble.id_prog.in_(ids_prog)
            ).all()
            checklists_por_id_prog = {row.id_prog: _checklist_a_dict(row) for row in filas_checklist}

        return [{
            'id_prog': s.id_prog,
            'id_codigo': s.id_codigo,
            'cantidad_objetivo': s.cantidad_objetivo,
            'cantidad_realizada': s.cantidad_realizada,
            'fecha_programada': s.fecha_programada.strftime('%Y-%m-%d') if s.fecha_programada else '',
            'estado': s.estado,
            # La OP que asignó el numerador al programar -- se expone para
            # mostrarla en el panel de metas (pedido del usuario 2026-08-28:
            # la OP se generaba sola pero no se veía en ningún lado).
            'op_numero': s.op_numero,
            'checklist': checklists_por_id_prog.get(s.id_prog, _checklist_default())
        } for s in schedules]

    @staticmethod
    def iniciar(data):
        """
        Persistencia inmediata al iniciar ensamble. Crea un registro EN_PROCESO
        en db_ensambles para que sea visible en el PC de inmediato.

        NOTA DE SEGURIDAD: el `ALTER TABLE db_ensambles ADD COLUMN IF NOT EXISTS
        estado ...` que existía en la versión original (backend/app.py) fue
        eliminado por completo en esta migración — las mutaciones de esquema en
        caliente están prohibidas en la arquitectura de FRITECH. La columna
        `estado` es responsabilidad de las migraciones del esquema, no de un
        endpoint de negocio.
        """
        if not data:
            raise ValueError('No data provided')

        responsable = str(data.get('responsable', '')).strip()
        id_codigo = normalizar_codigo(data.get('id_codigo', ''))

        if not responsable or not id_codigo:
            raise ValueError('Responsable y código requeridos')

        try:
            ahora = get_colombia_time()

            id_ensamble = data.get('id_ensamble') or f"ENS-{uuid.uuid4().hex[:8].upper()}"

            existente = db.session.query(Ensamble).filter_by(id_ensamble=id_ensamble).first()
            if existente:
                return {'ya_registrado': True, 'id_ensamble': id_ensamble}

            h_inicio = data.get('hora_inicio')
            if h_inicio:
                try:
                    hi_h, hi_m = h_inicio.split(':')
                    dt_inicio = ahora.replace(hour=int(hi_h), minute=int(hi_m), second=0, microsecond=0).replace(tzinfo=None)
                except Exception:
                    dt_inicio = ahora.replace(tzinfo=None)
            else:
                dt_inicio = ahora.replace(tzinfo=None)

            nuevo_ensamble = Ensamble(
                id_ensamble=id_ensamble,
                id_codigo=id_codigo,
                buje_ensamble=id_codigo,
                responsable=responsable,
                op_numero=data.get('orden_produccion', ''),
                fecha=ahora.date(),
                hora_inicio=dt_inicio,
                departamento='Ensamble',
                cantidad=0,  # Se actualizará al finalizar
                estado='EN_PROCESO'
            )
            db.session.add(nuevo_ensamble)
            db.session.commit()

            logger.debug(f"✅ [Ensamble] Inicio persistido: {id_ensamble} ({responsable})")
            return {'ya_registrado': False, 'id_ensamble': id_ensamble}
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error en EnsambleService.iniciar: {e}")
            raise

    @staticmethod
    def finalizar(data):
        """
        Finaliza un ensamble con explosión de materiales (BOM) y descarga de
        inventario. Upsert sobre el registro creado por `iniciar` (si existe).
        """
        if not data:
            raise ValueError('No data provided')

        id_codigo = data.get('id_codigo', '').strip()
        cantidad = int(data.get('cantidad', 0))
        if not id_codigo or cantidad <= 0:
            raise ValueError('Código y cantidad requeridos')

        responsable = data.get('responsable', '').strip()
        defectos = data.get('defectos', [])

        try:
            ahora = get_colombia_time()

            # FASE 1: BOM
            bom_res = calcular_descuentos_ensamble(id_codigo, cantidad)
            if not bom_res.get('success'):
                raise BomNoDisponibleException(bom_res.get('error'))

            # FASE 2: Descarga de Inventario (Híbrida por Prefijo)
            almacen_origen = data.get('almacen_origen', 'STOCK_BODEGA')
            for comp in bom_res['componentes']:
                codigo_comp = str(comp['codigo_inventario']).upper()

                # REGLA: CAR/INT -> BODEGA | Otros -> P. TERMINADO
                if codigo_comp.startswith('CAR') or codigo_comp.startswith('INT'):
                    almacen_a_descontar = 'STOCK_BODEGA'
                else:
                    almacen_a_descontar = 'P. TERMINADO'

                res_salida = StockService.registrar_salida(codigo_comp, comp['cantidad_total_descontar'], almacen_a_descontar)
                if "error" in res_salida:
                    # Fuga de inventario eliminada: un fallo en el descuento de un
                    # componente del BOM debe abortar TODA la transacción (el except
                    # de abajo hace rollback), no solo loguear una advertencia.
                    raise StockInsuficienteException(
                        f"No se pudo descontar {comp['cantidad_total_descontar']} de {codigo_comp} en {almacen_a_descontar}: {res_salida['error']}"
                    )

            # FASE 3: Ensamble (Mapeo completo de columnas SQL)
            id_ensamble_master = data.get('id_ensamble') or uuid.uuid4().hex[:8]

            primer_comp = bom_res['componentes'][0]['codigo_inventario'] if bom_res.get('componentes') else ''
            consumo_total = sum(float(c['cantidad_total_descontar']) for c in bom_res['componentes']) if bom_res.get('componentes') else 0

            id_codigo_clean = normalizar_codigo(id_codigo)

            # CÁLCULO DE TIEMPOS REALES (Procesar horas del frontend)
            duracion_s = 0
            tiempo_m = 0.0
            s_por_u = 0.0
            dt_inicio = ahora.replace(tzinfo=None)
            dt_fin = ahora.replace(tzinfo=None)

            h_ini = data.get('hora_inicio')
            h_fin = data.get('hora_fin')
            if h_ini and h_fin:
                try:
                    hi_h, hi_m = h_ini.split(':')
                    hf_h, hf_m = h_fin.split(':')
                    dt_inicio = ahora.replace(hour=int(hi_h), minute=int(hi_m), second=0, microsecond=0).replace(tzinfo=None)
                    dt_fin = ahora.replace(hour=int(hf_h), minute=int(hf_m), second=0, microsecond=0).replace(tzinfo=None)

                    diff = dt_fin - dt_inicio
                    duracion_s = int(diff.total_seconds())
                    if duracion_s < 0:
                        duracion_s += 86400  # Cruce de medianoche
                    tiempo_m = float(round(duracion_s / 60.0, 2))
                    if cantidad > 0:
                        s_por_u = float(round(duracion_s / cantidad, 2))
                    logger.debug(f"⏱️ [Ensamble] Tiempos: {h_ini}->{h_fin} = {duracion_s}s ({tiempo_m}min)")
                except Exception as e_time:
                    logger.warning(f"Error calculando tiempos ensamble: {e_time}")

            # Upsert: Si existe registro previo (de iniciar), actualizar; si no, crear nuevo
            existente = db.session.query(Ensamble).filter_by(id_ensamble=id_ensamble_master).first()
            if existente:
                nuevo_ensamble = existente
                nuevo_ensamble.id_codigo = id_codigo_clean
                nuevo_ensamble.buje_ensamble = id_codigo_clean
                nuevo_ensamble.cantidad = float(cantidad)
                nuevo_ensamble.qty = float(data.get('qty', 1) or 1)
                nuevo_ensamble.responsable = responsable
                nuevo_ensamble.op_numero = data.get('orden_produccion', '')
                nuevo_ensamble.almacen_para_descargar = almacen_origen
                nuevo_ensamble.almacen_destino = data.get('almacen_destino', '')
                nuevo_ensamble.buje_origen = primer_comp
                nuevo_ensamble.consumo_total = float(consumo_total)
                nuevo_ensamble.hora_inicio = dt_inicio
                nuevo_ensamble.hora_fin = dt_fin
                nuevo_ensamble.estado = 'FINALIZADO'
            else:
                nuevo_ensamble = Ensamble(
                    id_ensamble=id_ensamble_master,
                    id_codigo=id_codigo_clean,
                    buje_ensamble=id_codigo_clean,
                    cantidad=float(cantidad),
                    qty=float(data.get('qty', 1) or 1),
                    responsable=responsable,
                    op_numero=data.get('orden_produccion', ''),
                    almacen_para_descargar=almacen_origen,
                    almacen_destino=data.get('almacen_destino', ''),
                    buje_origen=primer_comp,
                    consumo_total=float(consumo_total),
                    fecha=ahora.date(),
                    hora_inicio=dt_inicio,
                    hora_fin=dt_fin,
                    departamento='Ensamble'
                )
                db.session.add(nuevo_ensamble)

            nuevo_ensamble.duracion_segundos = duracion_s
            nuevo_ensamble.tiempo_total_minutos = tiempo_m
            nuevo_ensamble.segundos_por_unidad = s_por_u

            # FASE 4: Calidad (id_pnc_ensamble TEXT UUID)
            for d in defectos:
                cant_pnc = float(d.get('cantidad', 0))
                if cant_pnc > 0:
                    db.session.add(PncEnsamble(
                        id_pnc_ensamble=uuid.uuid4().hex[:8],
                        id_ensamble=id_ensamble_master,
                        id_codigo=id_codigo,
                        cantidad=cant_pnc,
                        criterio=d.get('criterio', 'Defecto Ensamble')
                    ))

            # Cargar producto terminado
            StockService.registrar_entrada(id_codigo, cantidad, "PRODUCTO TERMINADO")

            # FASE 5: Transacción
            db.session.commit()
            logger.info(f"✅ ENSAMBLE EXITOSO: {id_codigo} (ID: {id_ensamble_master})")
            return {'id_ensamble': id_ensamble_master}
        except Exception as e:
            db.session.rollback()
            if not isinstance(e, (BomNoDisponibleException, StockInsuficienteException)):
                logger.error(f"❌ Error en EnsambleService.finalizar: {e}")
            raise

    @staticmethod
    def reportar_multi(payload_completo, usuario_activo):
        """
        Procesa el reporte multi-registro de ensamble (producto final +
        componentes del BOM) en una única transacción atómica: upsert de
        Ensamble, PNC (campo único del formulario, opcionalmente desglosado
        por componente), descuentos/acreditaciones de stock, propagación
        FIFO a DistribucionOpPedidos y sincronización de ProgramacionEnsamble.
        Un solo commit al final; cualquier excepción hace rollback completo.

        Guardia de idempotencia: si el registro final de este id_ensamble ya
        estaba FINALIZADO antes de esta llamada, los efectos NO idempotentes
        de una sola vez (movimientos de stock y propagación FIFO, ambos
        deltas aditivos) no se repiten — solo se re-confirma el estado ya
        persistido. Esto cubre reintentos del cliente tras un timeout de red
        donde el commit anterior sí llegó a completarse en el servidor.
        """
        if not payload_completo:
            raise ValueError('No data provided')

        registros_data = payload_completo.get('registros', [])
        if not registros_data:
            raise ValueError('No se recibieron registros')

        id_ensamble_global = registros_data[0].get('id_ensamble')
        movimientos_inventario = []

        main_reg = next((r for r in registros_data if r.get('es_final')), registros_data[0])
        estado_final = main_reg.get('estado', 'EN_PROCESO')

        # .with_for_update(): bloquea la fila hasta el commit/rollback de esta
        # transacción para que dos peticiones concurrentes (doble clic) no
        # lean ambas 'estado != FINALIZADO' antes de que cualquiera confirme
        # -- sin esto, la guardia de idempotencia de abajo es una condición de
        # carrera TOCTOU (ambas pasan la validación y duplican stock/FIFO).
        registro_final_db = Ensamble.query.filter_by(
            id_ensamble=id_ensamble_global,
            buje_ensamble=main_reg.get('buje_ensamble')
        ).with_for_update().first()

        candidato_responsable = main_reg.get('responsable') or usuario_activo
        if not candidato_responsable or str(candidato_responsable).strip().upper() in ['', 'SISTEMA']:
            raise ValueError('Se requiere una identidad de operario o responsable válida para registrar el ensamble')

        # Puede levantar OwnershipMismatchException — se propaga sin transformar
        responsable = AuditService.resolver_y_validar_propietario(registro_final_db, candidato_responsable)

        # Fila final ya FINALIZADA antes de este request => efectos de una sola
        # vez (stock, FIFO) ya corrieron en un commit anterior; no repetirlos.
        ya_finalizado_previamente = bool(registro_final_db and registro_final_db.estado == 'FINALIZADO')

        logger.debug(f"[ENSAMBLE-MULTI] Procesando {len(registros_data)} registros para id_ensamble={id_ensamble_global}")

        try:
            for reg_data in registros_data:
                id_codigo_ancla = preservar_o_normalizar_prefijo(reg_data.get('id_codigo'))
                buje_detalle = reg_data.get('buje_ensamble')
                cantidad = float(reg_data.get('cantidad', 0) or 0)
                es_final_flag = reg_data.get('es_final', False)

                registro = None
                if es_final_flag:
                    registro = registro_final_db

                if not registro:
                    if not es_final_flag:
                        registro = Ensamble.query.filter_by(
                            id_ensamble=id_ensamble_global,
                            buje_ensamble=buje_detalle
                        ).first()
                    if not registro:
                        registro = Ensamble(id_ensamble=id_ensamble_global, id_codigo=id_codigo_ancla)
                        if es_final_flag:
                            registro.hora_inicio = datetime.now()
                        db.session.add(registro)

                # Mapeo de datos (solo columnas físicas en db_ensambles)
                registro.id_codigo = id_codigo_ancla
                registro.buje_ensamble = buje_detalle
                registro.responsable = responsable
                registro.cantidad = cantidad
                # Solo el renglón "es_final" (producto terminado) representa
                # producción real contra la meta -- los renglones de componentes
                # del BOM comparten el mismo id_codigo "ancla" que el producto
                # final (es el id_ensamble de sesión, no un id por renglón), así
                # que si se les tagueara con id_prog también, el SUM de más abajo
                # los contaría como si fueran unidades producidas.
                if es_final_flag:
                    id_prog_reg = reg_data.get('id_prog')
                    registro.id_prog = int(id_prog_reg) if id_prog_reg else None
                registro.qty = float(reg_data.get('qty', 1) or 1)
                registro.estado = reg_data.get('estado', 'FINALIZADO')
                registro.op_numero = reg_data.get('op_numero', '')
                registro.observaciones = reg_data.get('observaciones', '')
                registro.buje_origen = reg_data.get('buje_origen', '')
                registro.almacen_para_descargar = reg_data.get('almacen_para_descargar')
                registro.almacen_destino = reg_data.get('almacen_destino')
                registro.fecha = datetime.strptime(reg_data.get('fecha'), '%Y-%m-%d').date() if reg_data.get('fecha') else datetime.now().date()

                # Inventario (delta aditivo, NO idempotente) — solo si no corrió ya
                if (estado_final == 'FINALIZADO' or registro.estado == 'CONSUMO') and not ya_finalizado_previamente:
                    if registro.almacen_para_descargar:
                        almacen = registro.almacen_para_descargar.upper()
                        bodega = "P. TERMINADO" if 'TERMINADO' in almacen else "PRODUCTO ENSAMBLADO"
                        res_mov = StockService.registrar_salida(buje_detalle, cantidad, bodega)
                        if res_mov and "error" not in res_mov:
                            movimientos_inventario.append(res_mov)

                        db.session.add(OperacionLog(
                            modulo='ENSAMBLE', operario=responsable, accion='CONSUMO_MULTI',
                            detalles=f"Descontado {cantidad} de {buje_detalle} para ensamble {id_ensamble_global}"
                        ))

                    if registro.almacen_destino:
                        almacen = registro.almacen_destino.upper()
                        bodega = "PRODUCTO ENSAMBLADO" if 'ENSAMBLADO' in almacen else "P. TERMINADO"
                        res_mov = StockService.registrar_entrada(buje_detalle, cantidad, bodega)
                        if res_mov and "error" not in res_mov:
                            movimientos_inventario.append(res_mov)

                        db.session.add(OperacionLog(
                            modulo='ENSAMBLE', operario=responsable, accion='ENTRADA_MULTI',
                            detalles=f"Ingresado {cantidad} de {buje_detalle} desde ensamble {id_ensamble_global}"
                        ))

                # Lógica de Tiempos, KPIs y PNC (exclusiva del producto final)
                if es_final_flag:
                    if estado_final == 'PAUSADO':
                        registro.hora_pausa = datetime.now()
                    elif estado_final in ['EN_PROCESO', 'TRABAJANDO', 'FINALIZADO'] and registro.hora_pausa:
                        diff = datetime.now() - registro.hora_pausa
                        registro.tiempo_pausa_acumulado = (registro.tiempo_pausa_acumulado or 0) + int(diff.total_seconds())
                        registro.hora_pausa = None

                    if estado_final == 'FINALIZADO':
                        registro.hora_fin = datetime.now()
                        h_ini_str = reg_data.get('hora_inicio')
                        h_fin_str = reg_data.get('hora_fin')
                        if h_ini_str:
                            registro.hora_inicio = datetime.combine(registro.fecha, datetime.strptime(h_ini_str, '%H:%M').time())
                        if h_fin_str:
                            registro.hora_fin = datetime.combine(registro.fecha, datetime.strptime(h_fin_str, '%H:%M').time())

                        if registro.hora_inicio and registro.hora_fin:
                            duracion = (registro.hora_fin - registro.hora_inicio).total_seconds() - (registro.tiempo_pausa_acumulado or 0)
                            registro.duracion_segundos = int(max(0, duracion))
                            registro.tiempo_total_minutos = round(registro.duracion_segundos / 60, 2)
                            if cantidad > 0:
                                registro.segundos_por_unidad = round(duracion / cantidad, 2)

                        # PNC desglosado por componente BOM (aditivo, NO idempotente)
                        if not ya_finalizado_previamente:
                            pnc_cant = int(reg_data.get('pnc', 0) or 0)
                            if pnc_cant > 0:
                                pnc_detalles_raw = reg_data.get('pnc_detalles', [])
                                if isinstance(pnc_detalles_raw, str):
                                    try:
                                        pnc_detalles_list = json.loads(pnc_detalles_raw)
                                    except Exception:
                                        pnc_detalles_list = []
                                else:
                                    pnc_detalles_list = pnc_detalles_raw

                                if pnc_detalles_list and isinstance(pnc_detalles_list, list):
                                    for item in pnc_detalles_list:
                                        comp_codigo = item.get('codigo_componente')
                                        comp_cant = int(item.get('cantidad', 0) or 0)
                                        comp_criterio = item.get('criterio', 'NO ESPECIFICADO')
                                        if comp_cant > 0:
                                            db.session.add(PncEnsamble(
                                                id_ensamble=id_ensamble_global,
                                                id_codigo=id_codigo_ancla,
                                                cantidad=comp_cant,
                                                criterio=comp_criterio,
                                                codigo_ensamble=comp_codigo
                                            ))
                                else:
                                    db.session.add(PncEnsamble(
                                        id_ensamble=id_ensamble_global,
                                        id_codigo=id_codigo_ancla,
                                        cantidad=pnc_cant,
                                        criterio=str(pnc_detalles_raw) or "Defecto general sin desglose",
                                        codigo_ensamble=id_codigo_ancla
                                    ))

            # --- Propagación de avances a cubetas FIFO (delta aditivo, NO idempotente) ---
            if not ya_finalizado_previamente:
                op_actual = main_reg.get('op_numero')
                id_prod_final = main_reg.get('id_codigo')
                cantidad_real = float(main_reg.get('cantidad', 0) or 0)

                if estado_final == 'FINALIZADO' and op_actual and str(op_actual).strip() != 'SIN OP' and cantidad_real > 0:
                    from backend.models.sql_models import DistribucionOpPedidos

                    op_limpia = str(op_actual or '').strip()
                    codigo_limpio = str(id_prod_final or '').replace('FR-', '').strip()

                    cubetas = db.session.query(DistribucionOpPedidos).filter(
                        DistribucionOpPedidos.op_world_office == op_limpia,
                        DistribucionOpPedidos.codigo_producto == codigo_limpio
                    ).order_by(DistribucionOpPedidos.id_distribucion.asc()).all()

                    piezas_por_repartir = cantidad_real

                    if not cubetas and piezas_por_repartir > 0:
                        pedido_asoc = db.session.query(DistribucionOpPedidos.id_pedido).filter(
                            DistribucionOpPedidos.op_world_office == op_limpia
                        ).first()
                        id_pedido_final = pedido_asoc[0] if (pedido_asoc and pedido_asoc[0]) else f"PED-IMPREVISTO-{op_limpia}"

                        logger.info(f" ⚠️ [ENSAMBLE-CONTINGENCIA] Creando cubeta temporal para OP: {op_limpia}, Producto: {codigo_limpio}, Pedido: {id_pedido_final}")
                        nueva_cubeta = DistribucionOpPedidos(
                            op_world_office=op_limpia,
                            id_pedido=id_pedido_final,
                            codigo_producto=codigo_limpio,
                            cant_requerida=piezas_por_repartir,
                            cant_inyectada=piezas_por_repartir,
                            cant_pulida=piezas_por_repartir,
                            cant_ensamblada=piezas_por_repartir,
                            cant_alistada=0
                        )
                        db.session.add(nueva_cubeta)
                        db.session.flush()
                        cubetas = [nueva_cubeta]
                        piezas_por_repartir = 0.0

                    logger.debug(f" 📦 [ENSAMBLE-FIFO] Propagando {piezas_por_repartir} piezas a {len(cubetas)} cubetas. OP: {op_limpia}, Producto: {codigo_limpio}")

                    for cubeta in cubetas:
                        if piezas_por_repartir <= 0:
                            break
                        falta = max(0, (cubeta.cant_requerida or 0) - (cubeta.cant_ensamblada or 0))
                        if falta > 0:
                            if piezas_por_repartir >= falta:
                                cubeta.cant_ensamblada = (cubeta.cant_ensamblada or 0) + falta
                                piezas_por_repartir -= falta
                            else:
                                cubeta.cant_ensamblada = (cubeta.cant_ensamblada or 0) + piezas_por_repartir
                                piezas_por_repartir = 0

            # --- Sincronizar Programación (recálculo SUM, seguro de re-ejecutar) ---
            id_prog_raw = main_reg.get('id_prog')
            id_prog = int(id_prog_raw) if id_prog_raw else None
            id_prod_final = main_reg.get('id_codigo')

            if id_prog:
                # Filtrado por id_prog (la meta específica), no solo por
                # (id_codigo, op_numero) -- ese filtro viejo sumaba producción
                # histórica de CUALQUIER meta del mismo producto/OP (sobre todo
                # con op_numero en blanco), haciendo que metas nuevas aparecieran
                # ya completadas sin haber recibido un solo reporte. Se conserva
                # el filtro por id_codigo para no contar aquí los renglones de
                # consumo de componentes del BOM, que comparten el mismo id_prog
                # pero no son producción del producto final.
                total_realizado = db.session.query(db.func.sum(Ensamble.cantidad)).filter(
                    Ensamble.id_prog == id_prog,
                    Ensamble.id_codigo == id_prod_final,
                    Ensamble.estado == 'FINALIZADO'
                ).scalar() or 0

                prog = ProgramacionEnsamble.query.get(id_prog)
                if prog:
                    prog.cantidad_realizada = total_realizado
                    if estado_final == 'FINALIZADO' and total_realizado >= prog.cantidad_objetivo:
                        prog.estado = 'COMPLETADO'
                    elif prog.estado == 'PENDIENTE':
                        prog.estado = 'EN_PROCESO'

            # --- Checklist de procesos (mismo commit, sin eje propio) ---
            # Independiente de cantidad_realizada/estado de arriba: ese es el
            # eje de unidades (reportes parciales), este es el eje de qué
            # procesos de planta se marcaron para esta meta. Se guarda tal
            # como lo dejó el operario en pantalla -- ningún proceso se
            # fuerza a HECHO ni a NO_APLICA del lado del servidor.
            checklist_payload = payload_completo.get('checklist')
            if id_prog and isinstance(checklist_payload, dict):
                fila_checklist = ChecklistEnsamble.query.filter_by(id_prog=id_prog).first()
                if not fila_checklist:
                    fila_checklist = ChecklistEnsamble(id_prog=id_prog)
                    db.session.add(fila_checklist)

                for proceso in PROCESOS_CHECKLIST:
                    estado_proceso = checklist_payload.get(proceso)
                    if estado_proceso in ESTADOS_CHECKLIST_VALIDOS:
                        setattr(fila_checklist, f'{proceso}_estado', estado_proceso)

                fila_checklist.actualizado_en = datetime.now()
                fila_checklist.actualizado_por = responsable

            # --- Único commit atómico de todo el flujo ---
            db.session.commit()
            logger.info(f"✅ [Ensamble] Reporte multi persistido: {id_ensamble_global} ({len(registros_data)} registros)")

            return {
                'id_ensamble': id_ensamble_global,
                'movimientos_inventario': movimientos_inventario,
                'registros_procesados': len(registros_data)
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error en EnsambleService.reportar_multi: {e}")
            raise
