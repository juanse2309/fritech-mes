import logging
import os
import re
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from datetime import datetime
from backend.models.sql_models import Pedido, DistribucionOpPedidos
from backend.utils.formatters import preservar_o_normalizar_prefijo

logger = logging.getLogger(__name__)


MAX_REINTENTOS_ID_PEDIDO = 3


class GeneracionIdPedidoError(RuntimeError):
    """Se agotaron los reintentos generando un id_pedido único (ver
    generar_siguiente_id_pedido). El caller (route) debe traducir esto a
    HTTP 409 Conflict."""


def generar_siguiente_id_pedido(db_session):
    """
    Genera el siguiente consecutivo PED-XXXX de forma atómica usando la
    secuencia nativa de PostgreSQL 'pedido_id_seq' (bootstrap en app.py,
    junto a las demás DDL idempotentes de arranque).

    Reemplaza al viejo patrón "SELECT id_pedido ... ORDER BY id DESC LIMIT 1"
    + incrementar en Python, que tenía una condición de carrera real: dos
    requests concurrentes podían leer el mismo último id_pedido y generar el
    mismo consecutivo (dos comerciales registrando pedidos al mismo tiempo).

    nextval() es no transaccional y atómico a nivel de motor: dos llamadas
    concurrentes NUNCA devuelven el mismo valor, sin necesidad de bloquear
    filas de db_pedidos (SELECT ... FOR UPDATE no aplica aquí porque no hay
    una fila existente que bloquear -- el consecutivo se calcula antes de
    que exista cualquier fila del pedido nuevo).

    Se reintenta ante IntegrityError/OperationalError como red de seguridad
    (p.ej. si en el futuro se agrega una constraint única sobre id_pedido,
    o ante un fallo transitorio de conexión), no porque la secuencia en sí
    pueda colisionar.
    """
    ultimo_error = None
    for intento in range(1, MAX_REINTENTOS_ID_PEDIDO + 1):
        try:
            numero = db_session.execute(text("SELECT nextval('pedido_id_seq')")).scalar()
            return f"PED-{numero}"
        except (IntegrityError, OperationalError) as e:
            ultimo_error = e
            db_session.rollback()
            logger.warning(
                f"Colisión/fallo generando id_pedido (intento {intento}/{MAX_REINTENTOS_ID_PEDIDO}): {e}"
            )

    logger.error(f"Se agotaron los reintentos generando id_pedido: {ultimo_error}")
    raise GeneracionIdPedidoError(
        f"No fue posible generar un id_pedido único tras {MAX_REINTENTOS_ID_PEDIDO} intentos"
    ) from ultimo_error


def normalizar_llaves_dict(row_dict):
    """
    Parser para normalizar claves del payload/CSV para que reconozca indistintamente 
    variaciones (Sanitización).
    """
    mapped = {}
    for k, v in row_dict.items():
        if not k:
            continue
        key_lower = str(k).lower().strip()
        
        if key_lower in ('direccion', 'dirección', 'direccion_tercero', 'direccin'):
            mapped['direccion'] = v
        elif key_lower in ('forma_pago', 'forma_de_pago', 'formapago'):
            mapped['forma_pago'] = v
        elif key_lower in ('vendedor', 'nombres_tercero_interno', 'vendedor_interno', 'elaborado_vendedor'):
            if 'vendedor' not in mapped or not mapped['vendedor']: # Priorizar si hay multiples
                mapped['vendedor'] = v
        elif key_lower in ('ciudad', 'ciudad_encabezado', 'ciudad_destino'):
            mapped['ciudad'] = v
        elif key_lower in ('nit', 'nit_cliente', 'identificacion_tercero', 'identificacion'):
            mapped['nit'] = v
        elif key_lower in ('observaciones', 'nota', 'nota_cabecera'):
            mapped['observaciones'] = v
        elif key_lower in ('descripcion', 'nota_detalle'):
            mapped['descripcion'] = v
        elif key_lower in ('cliente', 'nombres', 'nombre_tercero_externo'):
            mapped['cliente'] = v
        elif key_lower in ('producto', 'productos', 'id_codigo', 'referencia'):
            mapped['productos'] = v
        else:
            mapped[key_lower] = v
    return mapped

def reiniciar_pedido_wo(id_pedido_numero, db_session):
    """
    Reinicia transaccionalmente un pedido consultando desde World Office,
    borrando los detalles anteriores y dejándolo en 0% de progreso con la 
    cabecera correctamente mapeada.
    """
    import pyodbc  # Import local: driver ODBC no instalado en todos los entornos;
    # aislarlo aqui evita que su ausencia tumbe el resto de PedidosService
    # (afectaba antes a actualizar_alistamiento, que no lo necesita).

    id_pedido_str = f"PED-{id_pedido_numero}"

    # Configuracion de la base de datos de World Office
    DB_SERVER   = os.environ.get("WO_SERVER", r"SERVERWO\WORLDOFFICE17")
    DB_DATABASE = os.environ.get("WO_DB", "FRIPARTS2021")
    DB_UID      = os.environ.get("WO_USER", "wo_cliente")
    DB_PWD      = os.environ.get("WO_PASSWORD")
    if not DB_PWD:
        raise RuntimeError("WO_PASSWORD no está configurada")

    conn_str = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        f"UID={DB_UID};"
        f"PWD={DB_PWD};"
        "Timeout=15;"
    )

    try:
        # Paso 1: Limpieza Transaccional Atómica
        logger.info(f"Limpiando detalles de alistamiento y lineas previas para {id_pedido_str}")
        db_session.query(Pedido).filter(Pedido.id_pedido == id_pedido_str).delete()
        db_session.query(DistribucionOpPedidos).filter(DistribucionOpPedidos.id_pedido == id_pedido_str).delete()
        
        # Paso 2: Re-ingesta Limpia desde WO
        logger.info(f"Conectando a World Office para extraer el pedido {id_pedido_numero}...")
        conn = pyodbc.connect(conn_str, timeout=15)
        cursor = conn.cursor()
        
        # Obtener mapeo
        cursor.execute("SELECT Autonumerico, Codigo_Producto FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Inventarios]")
        mapping = {}
        for row in cursor.fetchall():
            mapping[str(row[0])] = row[1]
            
        # Extraer pedido con todos los metadatos comerciales
        sql = """
            SELECT 
                E.Fecha AS fecha,
                (E.prefijo + '-' + CAST(E.Numero_de_Documento AS VARCHAR)) AS documento,
                E.Nombre_tercero_externo AS nombres,
                E.Nombres_tercero_interno AS vendedor,
                E.Identificacion_Tercero AS nit,
                E.Direccion AS direccion,
                E.Ciudad_Encabezado AS ciudad,
                E.Forma_de_Pago AS forma_pago,
                E.Nota AS observaciones,
                D.Nota AS descripcion,
                D.Producto AS productos,
                CAST(D.Cantidad AS FLOAT) AS cantidad,
                CAST(D.Valor_Unitario AS FLOAT) AS precio_unitario
            FROM [FRIPARTS2021].[dbo].[Vista_Tabla_Encabezados] E
            INNER JOIN [FRIPARTS2021].[dbo].[Vista_Tabla_Movimientos_Inventario] D 
                ON E.Autonumerico = D.Pertenece_A
            WHERE E.Numero_de_Documento = ?
              AND E.Tipo_de_Documento = 'PED'
              AND E.Anulado = 0
              AND D.Cantidad > 0;
        """
        cursor.execute(sql, (id_pedido_numero,))
        columnas = [column[0] for column in cursor.description]
        
        filas = cursor.fetchall()
        if not filas:
            raise Exception(f"No se encontro el pedido {id_pedido_numero} en World Office.")
            
        items_insertados = 0
        hora_actual = datetime.now().strftime('%I:%M %p')
        cabecera_mapeada = None
        
        for row in filas:
            raw_dict = dict(zip(columnas, row))
            item = normalizar_llaves_dict(raw_dict)
            
            prod_id = str(item.get('productos', '')).strip()
            mapped_prod_raw = str(mapping.get(prod_id, prod_id)).strip()
            # reiniciar_pedido_wo() solo escribe en Pedido (db_pedidos), la tabla
            # exclusiva de pedidos FriParts -- Frimetals vive aparte en
            # metals_pedidos. Un codigo numerico huerfano que venga de WO en este
            # flujo es siempre FriParts, así que se antepone 'FR-' vía opt-in
            # explicito (igual que en pedidos_routes.registrar_pedido); de lo
            # contrario procesar_datos_wo() vuelve a exportar la referencia
            # incompleta y World Office rechaza la carga del archivo plano.
            mapped_prod = preservar_o_normalizar_prefijo(mapped_prod_raw, prefijo_defecto='FR-')

            cantidad = float(item.get('cantidad', 0))
            precio = float(item.get('precio_unitario', 0))
            fecha_str = item.get('fecha')
            try:
                fecha_dt = datetime.strptime(str(fecha_str)[:10], '%Y-%m-%d').date()
            except:
                fecha_dt = datetime.now().date()
            
            if cabecera_mapeada is None:
                cabecera_mapeada = item
                
            # Creacion forzando progreso 0 y estado PENDIENTE con metadatos comerciales completos
            nuevo_registro = Pedido(
                id_pedido=id_pedido_str,
                fecha=fecha_dt,
                hora=hora_actual,
                cliente=str(item.get('cliente') or ''),
                nit=str(item.get('nit') or ''),
                direccion=str(item.get('direccion') or ''),
                ciudad=str(item.get('ciudad') or ''),
                vendedor=str(item.get('vendedor') or ''),
                forma_de_pago=str(item.get('forma_pago') or ''),
                observaciones=str(item.get('observaciones') or ''),
                id_codigo=mapped_prod,
                descripcion=str(item.get('descripcion') or ''),
                cantidad=cantidad,
                precio_unitario=precio,
                total=cantidad * precio,
                estado='PENDIENTE', 
                cant_alistada='0',
                progreso='0%'
            )
            db_session.add(nuevo_registro)
            items_insertados += 1
            
        conn.close()
        
        # Completar la transacción lógica en memoria
        db_session.flush()
        
        return items_insertados, cabecera_mapeada
        
    except Exception as e:
        logger.error(f"Error reiniciando pedido {id_pedido_numero}: {e}")
        db_session.rollback()
        raise e


ESTADOS_INMUTABLES_PEDIDO = {'DESPACHADO', 'DESPACHADO PARCIAL', 'FACTURADO', 'CERRADO', 'CANCELADO'}

# Estados "sensibles": no bloquean la reasignación de forma permanente (a
# diferencia de ESTADOS_INMUTABLES_PEDIDO), pero exigen autorización explícita
# porque ya existe un documento correspondiente en un sistema externo.
# EXPORTADO_WO se fija en facturacion_routes.py cuando el pedido ya generó un
# documento real en World Office (wo_consecutivo) -- corregir cliente/nit aquí
# solo actualiza el MES (Postgres), NUNCA el documento en World Office.
ESTADOS_SENSIBLES_PEDIDO = {'EXPORTADO_WO'}


class ReasignacionClienteError(ValueError):
    """Excepción base de negocio para reasignar_cliente_pedido. Todas las
    subclases heredan de ValueError para no romper callers que capturen
    ValueError de forma genérica."""


class ValidacionReasignacionError(ReasignacionClienteError):
    """Datos de entrada inválidos (motivo faltante, NIT sin formato válido)."""


class PedidoNoEncontradoError(ReasignacionClienteError):
    """El id_pedido no existe en db_pedidos, ni en su variante con/sin 'PED-'."""


class PedidoEstadoInmutableError(ReasignacionClienteError):
    """El pedido está en un estado que no admite reasignación de cliente."""


class DesacopleWoNoAutorizadoError(ReasignacionClienteError):
    """El pedido ya fue exportado a World Office y falta autorización explícita
    (permitir_desacople_wo=True) para desacoplar el cliente en MES."""


def _resolver_filas_pedido(id_pedido, db_session):
    """
    Busca las filas de un pedido tolerando ambos formatos de id_pedido vistos
    en producción: el crudo ('10396', dejado por facturacion_routes.py al
    sobreescribir id_pedido con el consecutivo de World Office) y el
    prefijado ('PED-10396', usado por generar_siguiente_id_pedido).

    :return: (filas, id_pedido_real_en_bd)
    """
    id_norm = str(id_pedido).strip().upper()
    filas = db_session.query(Pedido).filter(Pedido.id_pedido == id_norm).all()
    if filas:
        return filas, id_norm

    alterno = id_norm[4:] if id_norm.startswith('PED-') else f"PED-{id_norm}"
    filas = db_session.query(Pedido).filter(Pedido.id_pedido == alterno).all()
    if filas:
        return filas, alterno

    return [], id_norm


def _validar_nit(nit):
    """
    Valida que el NIT tenga contenido real. No impone un formato estricto de
    NIT colombiano (dígito de verificación, guiones) porque la data existente
    en db_pedidos ya trae formatos inconsistentes (ej. 'NIT 900315300 2') --
    solo exige que, tras limpiar caracteres no numéricos, queden dígitos
    suficientes para ser un identificador real.
    """
    if not nit or not str(nit).strip():
        raise ValidacionReasignacionError("El NIT es requerido y no puede estar vacío")
    digitos = re.sub(r'\D', '', str(nit))
    if len(digitos) < 5:
        raise ValidacionReasignacionError(
            f"El NIT '{nit}' no tiene un formato válido (se esperan al menos 5 dígitos)"
        )


def reasignar_cliente_pedido(id_pedido, nuevo_cliente, nuevo_nit, nueva_direccion,
                              nueva_ciudad, usuario, motivo, db_session,
                              permitir_desacople_wo=False):
    """
    Reasigna la cabecera comercial (cliente/nit/direccion/ciudad) de TODAS las
    filas de un pedido existente, dentro de una transacción atómica.

    No hace commit: el caller (route) es responsable de confirmar o abortar,
    igual que reiniciar_pedido_wo.

    Reglas de inmutabilidad:
      - ESTADOS_INMUTABLES_PEDIDO bloquea siempre (ya hay stock/documentos
        físicos atados, ver db_despachos_pedido).
      - ESTADOS_SENSIBLES_PEDIDO (EXPORTADO_WO) bloquea salvo que el caller
        pase permitir_desacople_wo=True: la corrección solo toca el MES
        (Postgres), nunca el documento ya emitido en World Office.

    :raises ValidacionReasignacionError: motivo vacío o NIT con formato inválido.
    :raises PedidoNoEncontradoError: id_pedido inexistente (en ninguna variante).
    :raises PedidoEstadoInmutableError: estado en ESTADOS_INMUTABLES_PEDIDO.
    :raises DesacopleWoNoAutorizadoError: estado EXPORTADO_WO sin autorización.
    :return: (estado_anterior, estado_nuevo, id_pedido_real) -- dicts con la
        cabecera antes/después y el id_pedido tal como está en BD.
    """
    if not motivo or not str(motivo).strip():
        raise ValidacionReasignacionError("Se requiere un motivo explícito para reasignar el cliente")

    _validar_nit(nuevo_nit)

    try:
        filas, id_pedido_real = _resolver_filas_pedido(id_pedido, db_session)
        if not filas:
            raise PedidoNoEncontradoError(f"Pedido {id_pedido} no encontrado")

        estado_actual = str(filas[0].estado or '').strip().upper()

        if estado_actual in ESTADOS_INMUTABLES_PEDIDO:
            raise PedidoEstadoInmutableError(
                f"El pedido {id_pedido_real} está en estado '{estado_actual}' y no admite "
                f"reasignación de cliente."
            )

        if estado_actual in ESTADOS_SENSIBLES_PEDIDO and not permitir_desacople_wo:
            raise DesacopleWoNoAutorizadoError(
                "El pedido ya fue exportado a World Office. Se requiere autorización "
                "explícita para desacoplar el cliente en MES."
            )

        estado_anterior = {
            "cliente": filas[0].cliente,
            "nit": filas[0].nit,
            "direccion": filas[0].direccion,
            "ciudad": filas[0].ciudad,
        }

        for fila in filas:
            fila.cliente = nuevo_cliente
            fila.nit = nuevo_nit
            fila.direccion = nueva_direccion
            fila.ciudad = nueva_ciudad

        estado_nuevo = {
            "cliente": nuevo_cliente,
            "nit": nuevo_nit,
            "direccion": nueva_direccion,
            "ciudad": nueva_ciudad,
        }

        detalles_log = (
            f"'{estado_anterior['cliente']}' (NIT {estado_anterior['nit']}) -> "
            f"'{nuevo_cliente}' (NIT {nuevo_nit}). Motivo: {motivo}. Autoriza: {usuario}"
        )
        if estado_actual in ESTADOS_SENSIBLES_PEDIDO:
            detalles_log += " [Desacople MES/World Office autorizado explícitamente]"

        from backend.models.sql_models import OperacionLog
        db_session.add(OperacionLog(
            modulo="PEDIDOS",
            operario=usuario,
            accion=f"REASIGNACION_CLIENTE {id_pedido_real}",
            detalles=detalles_log
        ))

        db_session.flush()
        logger.info(f"🔄 [REASIGNACION_CLIENTE] Pedido {id_pedido_real}: {estado_anterior} -> {estado_nuevo} (por {usuario})")
        return estado_anterior, estado_nuevo, id_pedido_real

    except Exception as e:
        logger.error(f"Error reasignando cliente en pedido {id_pedido}: {e}")
        db_session.rollback()
        raise e


class PedidosService:
    """
    Namespace de métodos de negocio del dominio de pedidos (patrón
    clase + staticmethod, igual que EnsambleService/InventarioService). Las
    funciones de módulo existentes en este archivo (generar_siguiente_id_pedido,
    reasignar_cliente_pedido, reiniciar_pedido_wo) se mantienen tal cual para
    no romper a sus callers actuales -- código nuevo se agrega aquí.
    """

    @staticmethod
    def _clean_num(val):
        """
        Convierte entradas como '10', '10.0', '10%', '$1,200' a float.
        Mantiene el punto decimal; remueve símbolos y separadores de miles.
        """
        if val is None:
            return 0.0
        s = str(val).strip()
        if s in ('', 'None', 'null', 'undefined'):
            return 0.0
        s = s.replace('%', '').replace('$', '').replace(' ', '')
        s = s.replace(',', '')
        try:
            return float(s)
        except Exception:
            return 0.0

    @staticmethod
    def _clean_pct_str(val, default="0%"):
        if val is None:
            return default
        s = str(val).strip()
        if not s:
            return default
        if not s.endswith('%'):
            s = f"{s}%"
        return s

    @staticmethod
    def _distribuir_fifo_cubetas(id_pedido, codigo_front, cant_segura, item, db_session):
        """
        Distribuye FIFO la cantidad recién alistada (cant_segura) entre las
        cubetas de db_distribucion_op_pedidos de este pedido/producto. Si no
        existen cubetas (alistamiento express, sin programación previa), crea
        una cubeta de contingencia con la OP asociada más cercana que
        encuentre (por pedido, luego por producto), o una OP sintética como
        último recurso.
        """
        codigo_limpio = str(codigo_front).replace('FR-', '').strip()

        cubetas = db_session.query(DistribucionOpPedidos).filter(
            DistribucionOpPedidos.id_pedido == id_pedido,
            DistribucionOpPedidos.codigo_producto == codigo_limpio
        ).order_by(DistribucionOpPedidos.id_distribucion.asc()).all()

        piezas_por_repartir = cant_segura

        if not cubetas and piezas_por_repartir > 0:
            op_asoc = db_session.query(DistribucionOpPedidos.op_world_office).filter(
                (DistribucionOpPedidos.id_pedido == id_pedido) & (DistribucionOpPedidos.op_world_office.isnot(None))
            ).first()
            if not op_asoc:
                op_asoc = db_session.query(DistribucionOpPedidos.op_world_office).filter(
                    (DistribucionOpPedidos.codigo_producto == codigo_limpio) & (DistribucionOpPedidos.op_world_office.isnot(None))
                ).first()

            op_final = op_asoc[0] if (op_asoc and op_asoc[0]) else getattr(item, 'wo_consecutivo', None) or f"OP-IMPREVISTA-{id_pedido}"

            logger.info(f" ⚠️ [ALMACEN-CONTINGENCIA] Creando cubeta temporal para Pedido: {id_pedido}, Producto: {codigo_limpio}, OP: {op_final}")
            nueva_cubeta = DistribucionOpPedidos(
                op_world_office=op_final,
                id_pedido=id_pedido,
                codigo_producto=codigo_limpio,
                cant_requerida=piezas_por_repartir,
                cant_inyectada=piezas_por_repartir,
                cant_pulida=piezas_por_repartir,
                cant_ensamblada=piezas_por_repartir,
                cant_alistada=piezas_por_repartir
            )
            db_session.add(nueva_cubeta)
            db_session.flush()  # Sincronizar temporalmente en sesión
            cubetas = [nueva_cubeta]
            piezas_por_repartir = 0.0  # Consumido por completo
        else:
            # Reiniciar cant_alistada en las cubetas para esta referencia del pedido antes de distribuir
            for cubeta in cubetas:
                cubeta.cant_alistada = 0.0

        logger.debug(f" 📦 [ALMACEN-FIFO] Distribuyendo {piezas_por_repartir} piezas alistadas FIFO para Pedido: {id_pedido}, Producto: {codigo_limpio} (Original: {codigo_front})")

        for cubeta in cubetas:
            if piezas_por_repartir <= 0:
                break

            falta = max(0.0, (cubeta.cant_requerida or 0) - (cubeta.cant_alistada or 0))
            if falta > 0:
                if piezas_por_repartir >= falta:
                    cubeta.cant_alistada = (cubeta.cant_alistada or 0) + falta
                    piezas_por_repartir -= falta
                else:
                    cubeta.cant_alistada = (cubeta.cant_alistada or 0) + piezas_por_repartir
                    piezas_por_repartir = 0.0

    @staticmethod
    def actualizar_alistamiento(id_pedido, estado_general, progreso_pedido,
                                 progreso_despacho_pedido, detalles, db_session):
        """
        Actualiza el progreso de alistamiento de bodega de un pedido:
        distribuye FIFO las piezas alistadas entre las cubetas de OP
        (db_distribucion_op_pedidos), calcula progreso/estado por ítem y
        promueve automáticamente el pedido completo a "LISTO PARA DESPACHO"
        cuando todos sus ítems quedan 100% alistados.

        No hace commit: el caller (route) es responsable de confirmar o
        abortar la transacción completa (mismo contrato que
        reasignar_cliente_pedido/reiniciar_pedido_wo), preservando la
        atomicidad de todos los updates (ítems + cubetas + cabecera) como
        una sola unidad -- si el caller hace rollback tras una excepción,
        ningún cambio parcial de esta función queda persistido.

        :raises PedidoNoEncontradoError: id_pedido sin filas en db_pedidos.
        :return: dict con 'items_actualizados' y 'movimientos_inventario'
            (lista siempre vacía hoy -- se conserva por compatibilidad de
            forma con el contrato de respuesta del endpoint).
        """
        items_sql = db_session.query(Pedido).filter_by(id_pedido=id_pedido).all()
        if not items_sql:
            raise PedidoNoEncontradoError(f"Pedido {id_pedido} no encontrado en SQL")

        movimientos_inventario = []
        items_actualizados = 0

        for d in detalles:
            codigo_front = str(d.get("codigo", "")).strip().upper()
            cant_lista_front = PedidosService._clean_num(d.get("cant_lista", 0))

            # Buscar el match en SQL para este código dentro del pedido
            item = next((i for i in items_sql if str(i.id_codigo).strip().upper() == codigo_front), None)
            if not item:
                continue

            # Obtener tope máximo y aplicar filtro min() de seguridad
            cantidad_total = PedidosService._clean_num(item.cantidad)
            cant_segura = min(cant_lista_front, cantidad_total)

            # Persistir cantidad segura (como entero para evitar bug de .0 -> 000)
            item.cant_alistada = str(int(cant_segura))

            # Lógica de progreso y estado por item
            if cantidad_total > 0:
                porcentaje = (cant_segura / cantidad_total) * 100
                item.progreso = f"{int(porcentaje)}%"

                if cant_segura >= cantidad_total:
                    item.estado = 'ALISTADO'
                    item.progreso = '100%'
                else:
                    item.estado = estado_general or 'EN ALISTAMIENTO'

            PedidosService._distribuir_fifo_cubetas(id_pedido, codigo_front, cant_segura, item, db_session)

            items_actualizados += 1

        # Validar si el pedido completo quedó 100% alistado en bodega
        todo_alistado = True
        for it in items_sql:
            cant_req_item = PedidosService._clean_num(it.cantidad)
            cant_ali_item = PedidosService._clean_num(it.cant_alistada)
            if cant_ali_item < cant_req_item:
                todo_alistado = False
                break

        if todo_alistado and len(items_sql) > 0:
            estado_general = "LISTO PARA DESPACHO"
            progreso_pedido = "100%"
            logger.info(f" 🏆 [ALISTAMIENTO-COMPLETO] Pedido {id_pedido} completamente alistado. Promocionando a LISTO PARA DESPACHO.")

        # Persistir estado/progresos a nivel pedido (en TODAS las filas),
        # porque el listado agrupa tomando el primer item del pedido.
        if estado_general or progreso_pedido or progreso_despacho_pedido:
            update_data = {}
            if estado_general:
                update_data["estado"] = estado_general
            if progreso_pedido is not None:
                update_data["progreso"] = PedidosService._clean_pct_str(progreso_pedido)
            if progreso_despacho_pedido is not None:
                update_data["progreso_despacho"] = PedidosService._clean_pct_str(progreso_despacho_pedido)
            if update_data:
                db_session.query(Pedido).filter_by(id_pedido=id_pedido).update(update_data)

        db_session.flush()
        logger.info(f"✅ [SQL-ALISTAMIENTO] {items_actualizados} items actualizados para {id_pedido}")

        return {
            "items_actualizados": items_actualizados,
            "movimientos_inventario": movimientos_inventario,
        }
