"""
empaque_service.py
===================
Reporte de Empaque (reunión 2026-08-25): el módulo nuevo más simple de los
tres. Nadie programa -- el trabajo lo dicta el pedido, la operaria ya sabe
qué armar viendo la vista de gestión de pedidos (decisión explícita del
usuario: "si ellas saben que tienen que hacer con solo verlo... está bien
sin mostrarles nada"). Se reporta referencia + cantidad, y el sistema:
  1. reserva (o reutiliza) la OP EMP del día, con reserva perezosa,
  2. explota la ficha técnica del muñeco/kit (reutiliza bom_service, sin
     tocarlo -- es la misma mecánica que ya usa Ensamble),
  3. descuenta los componentes con prelación P. TERMINADO primero, POR
     PULIR si no alcanza (regla del usuario),
  4. acredita el muñeco armado en P. TERMINADO,
  5. deja el registro en db_empaque.

Todo en un solo commit atómico: si falta stock de cualquier componente, NO
se descuenta nada (ni ese componente ni los demás) -- se verifica primero,
se descuenta después. Nunca un descuento parcial.
"""
import logging
from datetime import datetime, date as date_cls, timedelta

from backend.core.sql_database import db
from backend.models.sql_models import ProduccionEmpaque, Producto
from backend.services.bom_service import calcular_descuentos_ensamble
from backend.services.ensamble_service import BomNoDisponibleException, StockInsuficienteException
from backend.services.op_numerador_service import OpNumeradorService
from backend.services.stock_service import StockService
from backend.utils.formatters import preservar_o_normalizar_prefijo, normalizar_codigo
from backend.utils.time_utils import get_colombia_time

logger = logging.getLogger(__name__)

# Ventana de reenvío (hallazgo 2026-08-27): reportar() siempre crea una fila
# nueva, sin upsert -- un reintento de red o un doble toque de la operaria
# duplicaría el descuento de componentes y el crédito del muñeco armado sin
# que nada lo note. Lo bastante corto para no confundir dos reportes
# legítimos y distintos de la misma referencia el mismo día.
SEGUNDOS_VENTANA_DUPLICADO = 20


class EmpaqueService:

    @staticmethod
    def _plan_de_descuento(componentes):
        """
        Para cada componente del BOM, calcula de dónde sale (P. TERMINADO
        primero, POR PULIR si no alcanza) SIN escribir nada todavía.
        StockService.actualizar_stock no impide stock negativo por sí solo
        (solo loguea una advertencia) -- por eso esta verificación vive
        aquí, antes de tocar la base de datos.

        Devuelve (plan, faltantes). Si faltantes no está vacío, plan se
        descarta -- no se ejecuta ningún descuento parcial.
        """
        plan = []
        faltantes = []

        for comp in componentes:
            codigo_inv = comp['codigo_inventario']
            necesario = float(comp['cantidad_total_descontar'])

            producto = Producto.query.filter(
                (Producto.codigo_sistema == codigo_inv) | (Producto.id_codigo == codigo_inv)
            ).first()

            if not producto:
                faltantes.append({
                    'codigo': codigo_inv, 'necesario': necesario,
                    'disponible': 0, 'faltante': necesario,
                    'motivo': 'Producto no encontrado en inventario'
                })
                continue

            disp_terminado = float(producto.p_terminado or 0)
            disp_pulir = float(producto.por_pulir or 0)

            de_terminado = min(necesario, disp_terminado)
            resto = necesario - de_terminado
            de_pulir = min(resto, disp_pulir)
            resto -= de_pulir

            if resto > 0.0001:  # tolerancia de punto flotante
                faltantes.append({
                    'codigo': codigo_inv, 'necesario': necesario,
                    'disponible': disp_terminado + disp_pulir, 'faltante': round(resto, 4)
                })
            else:
                plan.append({'codigo': codigo_inv, 'de_terminado': de_terminado, 'de_pulir': de_pulir})

        return plan, faltantes

    @staticmethod
    def previsualizar_ficha(id_codigo, cantidad=1):
        """
        Vista previa (GET /api/empaque/ficha/<codigo>): qué componentes se
        van a descontar y de dónde, ANTES de que la operaria confirme el
        reporte. No escribe nada.
        """
        codigo_norm = preservar_o_normalizar_prefijo(id_codigo)
        bom_res = calcular_descuentos_ensamble(codigo_norm, cantidad)

        if not bom_res.get('success'):
            raise BomNoDisponibleException(bom_res.get('error') or 'BOM no disponible')

        plan, faltantes = EmpaqueService._plan_de_descuento(bom_res['componentes'])

        return {
            'id_codigo': codigo_norm,
            'cantidad': cantidad,
            'componentes': [
                {
                    'codigo': p['codigo'],
                    'de_terminado': p['de_terminado'],
                    'de_pulir': p['de_pulir'],
                } for p in plan
            ] + [
                {
                    'codigo': f['codigo'], 'de_terminado': 0, 'de_pulir': 0,
                    'faltante': f['faltante'], 'disponible': f['disponible'],
                } for f in faltantes
            ],
            'stock_suficiente': not faltantes,
        }

    @staticmethod
    def reportar(data, usuario):
        """
        Único método de escritura del módulo. Ver docstring del archivo
        para el flujo completo.

        Puede lanzar ValueError (payload inválido), BomNoDisponibleException
        (la referencia no tiene ficha técnica) o StockInsuficienteException
        (falta stock de algún componente -- no se descuenta nada).

        Anti-duplicado (ver SEGUNDOS_VENTANA_DUPLICADO): si en la ventana ya
        existe un reporte idéntico (misma referencia, cantidad y
        responsable), se asume que es el mismo envío repetido y se devuelve
        ese reporte sin volver a descontar ni acreditar stock.
        """
        if not data:
            raise ValueError('No se recibieron datos')

        id_codigo_raw = data.get('id_codigo')
        if not id_codigo_raw:
            raise ValueError('id_codigo es obligatorio')

        try:
            cantidad = int(float(data.get('cantidad', 0)))
        except (ValueError, TypeError):
            raise ValueError('cantidad inválida')
        if cantidad <= 0:
            raise ValueError('cantidad debe ser mayor a 0')

        id_codigo = preservar_o_normalizar_prefijo(id_codigo_raw)
        observaciones = data.get('observaciones')

        fecha_str = data.get('fecha')
        if fecha_str:
            try:
                fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValueError(f"Formato de fecha inválido: {fecha_str!r}. Usar YYYY-MM-DD")
        else:
            fecha = get_colombia_time().date()

        usuario_norm = str(usuario or '').strip()
        ventana = get_colombia_time() - timedelta(seconds=SEGUNDOS_VENTANA_DUPLICADO)
        duplicado = ProduccionEmpaque.query.filter(
            ProduccionEmpaque.id_codigo == id_codigo,
            ProduccionEmpaque.cantidad == cantidad,
            ProduccionEmpaque.responsable == usuario_norm,
            ProduccionEmpaque.fecha_registro >= ventana,
        ).order_by(ProduccionEmpaque.id.desc()).first()
        if duplicado:
            logger.warning(
                f"⚠️ [ANTI-DUPLICADO] Empaque: reenvío detectado para "
                f"{id_codigo}/{usuario_norm} ({cantidad} u.) -- se devuelve "
                f"{duplicado.id_empaque} sin volver a tocar stock."
            )
            return {
                'id_empaque': duplicado.id_empaque,
                'id_codigo': duplicado.id_codigo,
                'cantidad': duplicado.cantidad,
                'op_numero': duplicado.op_numero,
                'componentes_descontados': [],
            }

        bom_res = calcular_descuentos_ensamble(id_codigo, cantidad)
        if not bom_res.get('success'):
            raise BomNoDisponibleException(bom_res.get('error') or f'BOM no disponible para {id_codigo}')

        plan, faltantes = EmpaqueService._plan_de_descuento(bom_res['componentes'])
        if faltantes:
            detalle = '; '.join(
                f"{f['codigo']}: faltan {f['faltante']} (necesita {f['necesario']}, hay {f['disponible']})"
                for f in faltantes
            )
            raise StockInsuficienteException(f"Stock insuficiente para armar {cantidad} x {id_codigo} -- {detalle}")

        try:
            # Reserva perezosa: el primer reporte del día crea la OP EMP de
            # hoy; los siguientes la reutilizan (idempotente por diseño del
            # numerador -- ver Fase 1 del plan).
            op_generada = OpNumeradorService.obtener_o_reservar('EMPAQUE', fecha, usuario=usuario)

            # StockService.registrar_salida/entrada NO lanzan excepción: ante
            # un fallo devuelven un dict con la clave 'error'. Hay que
            # revisarlo explícitamente -- ignorarlo produce exactamente el
            # bug que este módulo debe evitar: el reporte queda registrado y
            # el inventario nunca se descuenta, sin que nadie se entere.
            def _verificar(res, descripcion):
                if isinstance(res, dict) and res.get('error'):
                    raise StockInsuficienteException(f"{descripcion}: {res['error']}")

            for item in plan:
                if item['de_terminado'] > 0:
                    _verificar(
                        StockService.registrar_salida(item['codigo'], item['de_terminado'], 'P. TERMINADO'),
                        f"No se pudo descontar {item['de_terminado']} de {item['codigo']} en P. TERMINADO"
                    )
                if item['de_pulir'] > 0:
                    _verificar(
                        StockService.registrar_salida(item['codigo'], item['de_pulir'], 'POR PULIR'),
                        f"No se pudo descontar {item['de_pulir']} de {item['codigo']} en POR PULIR"
                    )

            # Acreditar el muñeco/kit armado.
            _verificar(
                StockService.registrar_entrada(id_codigo, cantidad, 'P. TERMINADO'),
                f"No se pudo acreditar {cantidad} de {id_codigo} en P. TERMINADO"
            )

            registro = ProduccionEmpaque(
                id_empaque=f"EMP-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                fecha=fecha,
                # Explícito, no el default de la columna (datetime.utcnow):
                # el resto del proyecto usa get_colombia_time() como fuente
                # de verdad para timestamps -- ver SEGUNDOS_VENTANA_DUPLICADO,
                # que compara contra este mismo reloj.
                fecha_registro=get_colombia_time(),
                id_codigo=id_codigo,
                cantidad=cantidad,
                responsable=usuario,
                op_numero=op_generada.numero_op,
                observaciones=observaciones,
            )
            db.session.add(registro)
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            if not isinstance(e, (ValueError, BomNoDisponibleException, StockInsuficienteException)):
                logger.error(f"❌ Error en EmpaqueService.reportar: {e}")
            raise

        logger.info(f"✅ [Empaque] {registro.id_empaque}: {cantidad} x {id_codigo} bajo OP {op_generada.numero_op}")
        return {
            'id_empaque': registro.id_empaque,
            'id_codigo': id_codigo,
            'cantidad': cantidad,
            'op_numero': op_generada.numero_op,
            'componentes_descontados': plan,
        }

    @staticmethod
    def listar_reportes(fecha_desde=None, fecha_hasta=None):
        """Historial del rango (por defecto, solo hoy) -- para el listado
        debajo del formulario y para verificación manual."""
        hoy = get_colombia_time().date()
        try:
            desde = datetime.strptime(fecha_desde, '%Y-%m-%d').date() if fecha_desde else hoy
            hasta = datetime.strptime(fecha_hasta, '%Y-%m-%d').date() if fecha_hasta else hoy
        except ValueError:
            raise ValueError("Formato de fecha inválido. Usar YYYY-MM-DD")

        registros = ProduccionEmpaque.query.filter(
            ProduccionEmpaque.fecha >= desde,
            ProduccionEmpaque.fecha <= hasta,
        ).order_by(ProduccionEmpaque.fecha_registro.desc()).all()

        return [{
            'id_empaque': r.id_empaque,
            'fecha': r.fecha.strftime('%Y-%m-%d') if r.fecha else '',
            'fecha_registro': r.fecha_registro.strftime('%Y-%m-%d %H:%M') if r.fecha_registro else '',
            'id_codigo': r.id_codigo,
            'cantidad': r.cantidad,
            'responsable': r.responsable,
            'op_numero': r.op_numero,
            'observaciones': r.observaciones,
        } for r in registros]
