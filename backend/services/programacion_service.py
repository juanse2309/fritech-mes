import logging
import uuid
from datetime import datetime, date
from sqlalchemy import text
from backend.models.sql_models import db, Maquina, ProgramacionInyeccion, ProduccionInyeccion
from backend.services.op_numerador_service import OpNumeradorService
from backend.utils.formatters import resolver_operario, normalizar_codigo
from backend.utils.time_utils import get_colombia_time

logger = logging.getLogger(__name__)


class ProgramacionNoEncontradaException(Exception):
    """Excepción lanzada cuando no existe la ProgramacionInyeccion/ProduccionInyeccion consultada."""
    def __init__(self, message="Programación no encontrada"):
        self.message = message
        super().__init__(self.message)


class MaquinaOcupadaException(Exception):
    """Excepción lanzada al intentar iniciar producción en una máquina que ya tiene una orden activa."""
    def __init__(self, maquina):
        self.maquina = maquina
        self.message = (
            f'Máquina Ocupada: La {maquina} ya tiene una orden activa. '
            f'Finalice la orden actual antes de iniciar una nueva.'
        )
        super().__init__(self.message)


# Cache de lectura del MES: nunca se llegó a poblar/leer (`_mes_cache[k]['data']`
# no se usa en ningún lado), solo se invalida en cada escritura. Se preserva tal
# cual en la migración — purgarlo es un cambio de comportamiento fuera del
# alcance de esta extracción, no una decisión a tomar de forma unilateral.
_mes_cache = {
    'dashboard': {'data': None, 'ts': 0},
    'prog_todas': {'data': None, 'ts': 0},
    'pendientes_calidad': {'data': None, 'ts': 0},
    'pendientes_validacion': {'data': None, 'ts': 0}
}
MES_CACHE_TTL = 60  # Aumentado a 60s para proteger cuota de API


class ProgramacionService:
    """
    Dominio de Programación y Planta (MES): catálogo de máquinas, cola de
    programación de Inyección (ProgramacionInyeccion) y su transición a
    producción activa (ProduccionInyeccion). La ejecución en sí (reportar
    turno, validar lote) vive en InyeccionService — este servicio cubre el
    agendamiento y los catálogos/estado de planta.
    """

    @staticmethod
    def clear_mes_cache():
        """Limpia todos los caches de lectura del MES (llamar en toda operación de escritura)."""
        logger.info("🗑️ [CACHE] Invalidando cache MES por operacion de escritura")
        for key in _mes_cache:
            _mes_cache[key]['ts'] = 0

    @staticmethod
    def obtener_maquinas_activas():
        """Lista de nombres de máquinas activas, con fallback a query directa si el ORM no devuelve nada."""
        maquinas = Maquina.query.filter_by(activa=True).order_by(Maquina.nombre).all()
        nombres = [m.nombre for m in maquinas]
        if not nombres:
            rows = db.session.execute(text("SELECT nombre FROM db_maquinas ORDER BY nombre")).fetchall()
            nombres = [r[0] for r in rows if r[0]]
        return nombres

    @staticmethod
    def obtener_cavidades_referencia(codigo):
        """
        Requisito de la reunión 2026-08-25: al programar, mostrar cuántas
        cavidades tiene disponibles una referencia para que el Jefe de Planta
        sepa qué poner sin adivinar.

        Fuente: rel_producto_molde (350 filas reales, verificado 2026-08-25) --
        NO db_moldes.cavidades_max, que hoy está VACÍA en producción (0 filas).

        ACTUALIZACIÓN 2026-08-28: ProgramacionInyeccion.molde dejó de ser una
        capacidad numérica que se digitaba y se comparaba contra la suma de
        cavidades del montaje (esa regla se eliminó). Ahora es el código real
        de molde físico (mismo alfabeto que codigo_molde aquí, ej. '5002A'),
        elegido por la persona del equipo que conoce el estado de los moldes
        (cuál está en arreglo, qué cavidad está dañada) -- ver también
        listar_moldes_disponibles(), que expone el catálogo completo para el
        selector del formulario.

        Devuelve una lista porque una referencia puede fabricarse en más de un
        molde (combo/moneda alternativa, ver tipo_vinculo).
        """
        codigo_norm = normalizar_codigo(codigo)
        if not codigo_norm:
            return []

        rows = db.session.execute(text("""
            SELECT codigo_molde, cavidades, tipo_vinculo
            FROM rel_producto_molde
            WHERE codigo_referencia = :c AND activo = true
            ORDER BY codigo_molde
        """), {'c': codigo_norm}).mappings().all()

        return [
            {
                'codigo_molde': r['codigo_molde'],
                'cavidades': r['cavidades'],
                'tipo_vinculo': r['tipo_vinculo'],
            }
            for r in rows
        ]

    @staticmethod
    def listar_moldes_disponibles():
        """
        Catálogo de portamoldes reales (17 letras, A-P más Ñ), para el
        selector con autocompletar del campo "Molde" en Programación --
        reemplaza el campo de texto libre que antes se comparaba contra la
        suma de cavidades del montaje.

        CORRECCIÓN 2026-08-31: esta función apuntaba antes a
        rel_producto_molde.codigo_molde (314 códigos), pero esa tabla lista
        referencias de producto/molde-die, no lo que Oscar (el operario que
        llena este campo) llama "molde" en planta -- una letra simple del
        portamolde físico (confirmado por el usuario: "es una simple letra
        'N' por ejemplo"). La fuente correcta es db_portamoldes.codigo, que
        sí son esas 17 letras reales.
        """
        rows = db.session.execute(text("""
            SELECT codigo
            FROM db_portamoldes
            WHERE activo = true
            ORDER BY codigo
        """)).scalars().all()
        return list(rows)

    @staticmethod
    def crear_programacion(maquina, fecha_str, productos, responsable, observaciones, molde_capacidad):
        """
        Fase 1: el Jefe de Planta 'pone en cola' uno o varios productos para una
        máquina. Hace UPSERT por (fecha, maquina, codigo_sistema, molde) — si ya
        existe la fila, actualiza cavidades/observaciones/estado en vez de duplicar.

        OP automática (reunión 2026-08-25): ruta legacy paralela a
        InyeccionService.guardar_programacion, misma tabla. Como la reserva de
        OpNumeradorService es idempotente por (fecha, ambito, maquina), ambas
        rutas convergen en la MISMA OP sin coordinarse entre sí -- no hace
        falta unificarlas.
        """
        try:
            if fecha_str:
                try:
                    fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
                except ValueError:
                    fecha_obj = date.today()
            else:
                fecha_obj = date.today()

            if not productos:
                raise ValueError('No se enviaron productos para programar')

            op_generada = OpNumeradorService.obtener_o_reservar(
                'INYECCION', fecha_obj, maquina, usuario=responsable
            )

            ids_creados = []
            for p in productos:
                cod_sistema = str(p.get('codigo')).strip()
                cav_val = int(p.get('cavidades', 1))

                existente = db.session.query(ProgramacionInyeccion).filter(
                    ProgramacionInyeccion.fecha == fecha_obj,
                    ProgramacionInyeccion.maquina == maquina,
                    ProgramacionInyeccion.codigo_sistema == cod_sistema,
                    db.func.coalesce(ProgramacionInyeccion.molde, '') == (molde_capacidad or '')
                ).first()

                if existente:
                    existente.cavidades = cav_val
                    existente.responsable_planta = responsable
                    existente.observaciones = observaciones
                    existente.estado = 'PROGRAMADO'
                    if 'cantidad' in p:
                        existente.cantidad = float(p.get('cantidad', 0))
                    # Solo si sigue vacía -- ver mismo comentario en
                    # InyeccionService.guardar_programacion: si la máquina ya
                    # arrancó, reprogramar aquí no debe pisar la OP real.
                    if not existente.op_world_office:
                        existente.op_world_office = op_generada.numero_op
                    db.session.flush()
                    ids_creados.append(existente.id)
                else:
                    nueva_prog = ProgramacionInyeccion(
                        fecha=fecha_obj,
                        codigo_sistema=cod_sistema,
                        maquina=maquina,
                        molde=molde_capacidad,
                        cavidades=cav_val,
                        responsable_planta=responsable,
                        observaciones=observaciones,
                        estado='PROGRAMADO',
                        op_world_office=op_generada.numero_op
                    )
                    if 'cantidad' in p:
                        nueva_prog.cantidad = float(p.get('cantidad', 0))
                    db.session.add(nueva_prog)
                    db.session.flush()
                    ids_creados.append(nueva_prog.id)

            db.session.commit()
            return {'ids_programacion': ids_creados, 'count': len(productos), 'orden_produccion': op_generada.numero_op}
        except Exception as e:
            db.session.rollback()
            if not isinstance(e, ValueError):
                logger.error(f"Error en ProgramacionService.crear_programacion: {e}")
            raise

    @staticmethod
    def cancelar(id_target):
        """
        Fase 1b: libera una máquina cancelando por ID. Polimórfico: intenta
        primero en la cola de ProgramacionInyeccion y, si no está ahí, en
        ProduccionInyeccion (trabajo ya en curso) — un mismo botón de
        'cancelar' cubre ambos casos.
        """
        try:
            encontrado = False
            prog = db.session.get(ProgramacionInyeccion, id_target)
            if prog:
                db.session.delete(prog)
                encontrado = True

            if not encontrado:
                inyeccion = db.session.get(ProduccionInyeccion, id_target)
                if inyeccion:
                    db.session.delete(inyeccion)
                    encontrado = True

            if not encontrado:
                raise ProgramacionNoEncontradaException(f"ID {id_target} no encontrado en Ninguna Tabla")

            db.session.commit()
            ProgramacionService.clear_mes_cache()
        except Exception as e:
            db.session.rollback()
            if not isinstance(e, ProgramacionNoEncontradaException):
                logger.error(f"Error en ProgramacionService.cancelar SQL (Polimórfico): {e}")
            raise

    @staticmethod
    def obtener_programaciones_activas(maquina):
        """Cola de trabajo activa (PROGRAMADO/EN_PROCESO) desde hoy en adelante, opcionalmente filtrada por máquina."""
        maquina_upper = maquina.upper()
        today_date = date.today()

        query = db.session.query(ProgramacionInyeccion).filter(
            ProgramacionInyeccion.estado.in_(['PROGRAMADO', 'EN_PROCESO']),
            ProgramacionInyeccion.fecha >= today_date
        )
        lotes = query.all()

        logger.info(f"DEBUG: Cargando {len(lotes)} lotes para la cola de trabajo del {today_date.strftime('%Y-%m-%d')}")

        if maquina_upper != 'TODAS':
            lotes = [r for r in lotes if (r.maquina or '').upper() == maquina_upper]

        return [
            {
                'id': r.id,
                'fecha': r.fecha.strftime('%Y-%m-%d') if r.fecha else '',
                'codigo_sistema': r.codigo_sistema,
                'maquina': (r.maquina or '').upper(),
                'molde': r.molde,
                'cavidades': r.cavidades,
                'cantidad': float(r.cantidad or 0),
                'estado': (r.estado or 'PENDIENTE').upper(),
                'orden_produccion': r.op_world_office or 'SIN_OP'
            }
            for r in lotes
        ]

    @staticmethod
    def obtener_dashboard_mes():
        """Estado completo de las máquinas: trabajo activo (ProduccionInyeccion EN_PROCESO) + cola (ProgramacionInyeccion PROGRAMADO), agrupado por máquina."""
        maquinas_sql = db.session.query(Maquina).filter(Maquina.activa == True).all()
        maquinas_set = [m.nombre for m in maquinas_sql]
        if not maquinas_set:
            maquinas_set = ['MAQUINA No.1', 'MAQUINA No.2', 'MAQUINA No.3', 'MAQUINA No.4']

        en_proceso = db.session.query(ProduccionInyeccion).filter(ProduccionInyeccion.estado == 'EN_PROCESO').all()
        programaciones = db.session.query(ProgramacionInyeccion).filter(ProgramacionInyeccion.estado == 'PROGRAMADO').all()

        resultado = []
        for maquina_nom in maquinas_set:
            maquina_upper = maquina_nom.upper()
            activos_maq = [r for r in en_proceso if (r.maquina or '').upper() == maquina_upper]

            trabajo_activo = None
            estado_maquina = 'LIBRE'

            if activos_maq:
                primer = activos_maq[0]
                trabajo_activo = {
                    'id_inyeccion': primer.id_inyeccion,
                    'molde': primer.molde,
                    'hora_inicio': primer.fecha_inicia.strftime('%H:%M') if primer.fecha_inicia else '',
                    'producto': ", ".join([str(r.id_codigo or '') for r in activos_maq]),
                    'cavidades': sum([int(r.cavidades or 0) for r in activos_maq]),
                    'orden_produccion': primer.orden_produccion,
                    'productos_activos': [
                        {
                            'id_inyeccion': r.id_inyeccion,
                            'codigo_sistema': r.id_codigo,
                            'cavidades': r.cavidades,
                            'molde': r.molde,
                            'hora_inicio': r.hora_inicio or (r.fecha_inicia.strftime('%H:%M') if r.fecha_inicia else '')
                        } for r in activos_maq
                    ]
                }
                estado_maquina = 'EN_PROCESO'

            cola = [
                {
                    'id_programacion': r.id,
                    'codigo_sistema': r.codigo_sistema,
                    'molde': r.molde,
                    'cavidades': r.cavidades,
                    'cantidad': float(r.cantidad or 0),
                    'orden_produccion': r.op_world_office
                }
                for r in programaciones if (r.maquina or '').upper() == maquina_upper
            ]

            if cola and estado_maquina == 'LIBRE':
                estado_maquina = 'PROGRAMADO'

            resultado.append({
                'nombre': maquina_nom,
                'estado': estado_maquina,
                'trabajo_activo': trabajo_activo,
                'cola': cola
            })

        return resultado

    @staticmethod
    def obtener_pendientes_calidad():
        """Registros de inyección en estado PENDIENTE_CALIDAD, traducidos al formato legacy esperado por el frontend."""
        pendientes_sql = ProduccionInyeccion.query.filter(
            ProduccionInyeccion.estado.ilike('PENDIENTE_CALIDAD')
        ).all()

        return [
            {
                'ID INYECCION': r.id_inyeccion,
                'FECHA': r.fecha_inicia.strftime('%Y-%m-%d') if r.fecha_inicia else '',
                'RESPONSABLE': r.responsable,
                'ID CODIGO': r.id_codigo,
                'CANTIDAD REAL': float(r.cantidad_real or 0),
                'MAQUINA': r.maquina,
                'ESTADO': r.estado
            }
            for r in pendientes_sql
        ]

    @staticmethod
    def obtener_productos_de_programacion(id_prog):
        """Productos vinculados a una fila de db_programacion (hoy siempre una sola SKU por fila)."""
        prog = ProgramacionInyeccion.query.get(id_prog)
        if not prog:
            raise ProgramacionNoEncontradaException("No se encontró la programación")

        return [{
            'codigo': prog.codigo_sistema,
            'cavidades': int(prog.cavidades or 1),
            'molde': str(prog.molde or '')
        }]

    @staticmethod
    def obtener_status_maquina(maquina):
        """
        Estado actual de una máquina agrupando montajes Multi-SKU: primero busca
        producción EN_PROCESO/ABIERTO; si no hay, el siguiente bloque PROGRAMADO
        en cola (mismo fecha+molde+OP); si no hay nada, LIBRE.
        """
        maquina_upper = str(maquina).strip().upper()

        activos = db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.maquina == maquina_upper,
            ProduccionInyeccion.estado.in_(['EN_PROCESO', 'ABIERTO'])
        ).all()

        if activos:
            primer = activos[0]
            codigos_concatenados = ", ".join([str(r.id_codigo or '') for r in activos])
            total_cavidades = sum([int(r.cavidades or 0) for r in activos])

            return {
                'estado': primer.estado,
                'id_inyeccion': primer.id_inyeccion,
                'id_programacion': primer.id_inyeccion,
                'producto': codigos_concatenados,
                'molde': str(primer.molde or ''),
                'cavidades': total_cavidades,
                'inicio': primer.fecha_inicia.strftime('%H:%M') if primer.fecha_inicia else '',
                'teorica': 0,
                'orden_produccion': primer.orden_produccion
            }

        primer_programado = db.session.query(ProgramacionInyeccion).filter(
            ProgramacionInyeccion.maquina == maquina_upper,
            ProgramacionInyeccion.estado == 'PROGRAMADO'
        ).order_by(ProgramacionInyeccion.id.asc()).first()

        if primer_programado:
            programados_bloque = db.session.query(ProgramacionInyeccion).filter(
                ProgramacionInyeccion.maquina == maquina_upper,
                ProgramacionInyeccion.fecha == primer_programado.fecha,
                ProgramacionInyeccion.molde == primer_programado.molde,
                ProgramacionInyeccion.op_world_office == primer_programado.op_world_office,
                ProgramacionInyeccion.estado == 'PROGRAMADO'
            ).all()

            codigos_concatenados = ", ".join([str(r.codigo_sistema or '') for r in programados_bloque])
            total_cavidades = sum([int(r.cavidades or 0) for r in programados_bloque])

            return {
                'estado': 'PROGRAMADO',
                'id_programacion': primer_programado.id,
                'producto': codigos_concatenados,
                'molde': str(primer_programado.molde or ''),
                'cavidades': total_cavidades,
                'orden_produccion': primer_programado.op_world_office
            }

        return {'estado': 'LIBRE'}

    @staticmethod
    def iniciar_produccion_batch(id_programacion, responsable_raw):
        """
        Fase 2a: el operario inicia físicamente la máquina. Arranca TODO el
        bloque programado para esa máquina (mismo día+molde) en un solo lote
        de producción compartiendo un único id_inyeccion (Batch ID).
        """
        try:
            operario = resolver_operario(responsable_raw)

            prog_trigger = db.session.get(ProgramacionInyeccion, id_programacion)
            if not prog_trigger:
                raise ProgramacionNoEncontradaException("Programación base no encontrada")

            maquina_nom = prog_trigger.maquina

            activos = db.session.query(ProduccionInyeccion).filter(
                ProduccionInyeccion.maquina == maquina_nom,
                ProduccionInyeccion.estado.in_(['ABIERTO', 'EN_PROCESO'])
            ).first()
            if activos:
                raise MaquinaOcupadaException(maquina_nom)

            progs_en_cola = db.session.query(ProgramacionInyeccion).filter(
                ProgramacionInyeccion.maquina == maquina_nom,
                ProgramacionInyeccion.fecha == prog_trigger.fecha,
                ProgramacionInyeccion.molde == prog_trigger.molde,
                ProgramacionInyeccion.estado == 'PROGRAMADO'
            ).all()
            if not progs_en_cola:
                raise ValueError(f'No hay programaciones cargadas para {maquina_nom}')

            id_inyeccion = f"INY-{str(uuid.uuid4())[:8].upper()}"
            ahora = get_colombia_time()

            for p in progs_en_cola:
                db.session.add(ProduccionInyeccion(
                    id_inyeccion=id_inyeccion,
                    fecha_inicia=ahora,
                    id_codigo=p.codigo_sistema,
                    responsable=operario,
                    maquina=p.maquina,
                    molde=p.molde,
                    cavidades=p.cavidades,
                    estado='EN_PROCESO',
                    programado_por=p.responsable_planta,
                    iniciado_por=operario
                ))
                p.estado = 'EN_PROCESO'

            db.session.commit()
            ProgramacionService.clear_mes_cache()
            return {'id_inyeccion': id_inyeccion, 'count': len(progs_en_cola)}
        except Exception as e:
            db.session.rollback()
            if not isinstance(e, (ProgramacionNoEncontradaException, MaquinaOcupadaException, ValueError)):
                logger.error(f"❌ Error en ProgramacionService.iniciar_produccion_batch: {e}")
            raise
