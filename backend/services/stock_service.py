"""
Motor de movimientos de stock (entradas, salidas y transferencias entre etapas).

Extraído de backend/app.py. Orquesta ajustes RELATIVOS (delta: suma/resta una
cantidad) sobre db_productos y es COMPONIBLE dentro de una transacción más
grande del caller — usa `db.session.flush()`, nunca `commit()`, para que
operaciones multi-paso (ej. descuentos de BOM sobre varios componentes antes
de un único commit final) puedan encadenarse sin cerrar la transacción a
mitad de camino.

Distinto de `ProductoRepository.actualizar_saldo` (backend/repositories/producto_repository.py):
ese es un setter de CAPA DE DATOS — recibe el valor ABSOLUTO final de la
columna y hace `commit()` de inmediato. Lo usa en solitario `InventarioRepository`
(el motor paralelo con validación de `Almacenes`/`StockInsuficiente`, que hoy
solo tiene 2 callers reales vía InventarioService.registrar_entrada/salida).
Ambos motores conviven porque tienen contratos incompatibles (delta vs.
absoluto, flush vs. commit, dict vs. bool) y este StockService es, con
diferencia, el más usado en todo el backend — unificarlos exigiría reescribir
la cadena de InventarioRepository sin que nadie lo haya pedido.
"""
import logging
from backend.core.sql_database import db
from backend.models.sql_models import Producto
from backend.utils.formatters import normalizar_codigo

logger = logging.getLogger(__name__)


class StockService:

    @staticmethod
    def actualizar_stock(codigo_sistema, cantidad, almacen, operacion='sumar'):
        """
        Ajusta RELATIVAMENTE (delta) una columna de stock de db_productos.
        No hace commit — usa flush() para componerse con el resto de la
        transacción del caller. Devuelve (True, detalle_dict) o (False, mensaje_error).

        Resolución del código (CORREGIDO 2026-08-25): se busca PRIMERO el
        código tal como llegó, y solo si no existe se reintenta con el
        prefijo pelado por normalizar_codigo(). Antes se pelaba siempre, lo
        que causaba dos fallos verificados contra datos reales:

          1. Productos cuyo código vive CON prefijo en ambas columnas
             ('SP-15', 'TR-3/8*10', 'BSL-026') no se encontraban nunca --
             'SP-15' se pelaba a '15', que no existe. El descuento se perdía
             en silencio (el caller solo recibía un dict con 'error' que
             varios no revisaban).
          2. Peor: descuento CRUZADO entre productos distintos. La ficha
             pide 'PL-HR-XLD' (PLATINA PARA HERRADURA GIGANTE DELANTERA) y,
             al pelarse a 'HR-XLD', el stock salía de 'HERRADURA GIGANTE
             DELANTERA' -- otro producto. Igual con 'PL-HR-XLT'.

        Pelar sigue como respaldo porque los bujes sí viven como
        codigo_sistema='FR-9302' / id_codigo='9302', y hay casos ('PL-6000')
        que solo existen pelados.
        """
        try:
            cod_directo = str(codigo_sistema).strip().upper() if codigo_sistema else ''
            producto = Producto.query.filter(
                (Producto.codigo_sistema == cod_directo) |
                (Producto.id_codigo == cod_directo)
            ).first() if cod_directo else None

            codigo_norm = cod_directo
            if not producto:
                codigo_norm = normalizar_codigo(codigo_sistema)
                producto = Producto.query.filter(
                    (Producto.codigo_sistema == codigo_norm) |
                    (Producto.id_codigo == codigo_norm)
                ).first()

            if not producto:
                return False, f"Producto {codigo_norm} no encontrado en SQL"

            mapeo_sql = {
                'POR PULIR': 'por_pulir',
                'P. TERMINADO': 'p_terminado',
                'PRODUCTO ENSAMBLADO': 'producto_ensamblado',
                'STOCK_BODEGA': 'stock_bodega',
                'CLIENTE': 'comprometido',
                'PNC': 'comprometido'
            }

            columna = mapeo_sql.get(almacen)
            if not columna:
                return False, f"Almacén {almacen} no mapeado a SQL"

            stock_actual = float(getattr(producto, columna) or 0)

            if operacion == 'sumar':
                nuevo_valor = stock_actual + cantidad
            else:
                nuevo_valor = stock_actual - cantidad
                if nuevo_valor < 0:
                    logger.warning(f"⚠️ Stock negativo detectado en {codigo_norm} ({almacen}): {nuevo_valor}")

            tipo_mov = "ENTRADA" if operacion == 'sumar' else "SALIDA"
            res_dict = {
                "codigo": codigo_norm,
                "tipo_movimiento": tipo_mov,
                "stock_anterior": float(stock_actual),
                "cantidad_movida": float(cantidad),
                "stock_nuevo": float(nuevo_valor),
                "ubicacion": almacen
            }

            setattr(producto, columna, nuevo_valor)
            db.session.flush()
            logger.info(f" ✅ [SQL-STOCK] {codigo_norm} en {almacen}: {stock_actual} -> {nuevo_valor}")
            return True, res_dict
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error actualizando stock SQL: {e}")
            return False, str(e)

    @staticmethod
    def registrar_entrada(codigo_sistema, cantidad, almacen):
        """Registra una entrada de inventario (delta positivo)."""
        exito, res = StockService.actualizar_stock(codigo_sistema, cantidad, almacen, 'sumar')
        if exito:
            return res
        return {
            "codigo": codigo_sistema,
            "tipo_movimiento": "ENTRADA",
            "stock_anterior": 0.0,
            "cantidad_movida": float(cantidad),
            "stock_nuevo": 0.0,
            "ubicacion": almacen,
            "error": res
        }

    @staticmethod
    def registrar_salida(codigo_sistema, cantidad, almacen):
        """Registra una salida de inventario (delta negativo)."""
        exito, res = StockService.actualizar_stock(codigo_sistema, cantidad, almacen, 'restar')
        if exito:
            return res
        return {
            "codigo": codigo_sistema,
            "tipo_movimiento": "SALIDA",
            "stock_anterior": 0.0,
            "cantidad_movida": float(cantidad),
            "stock_nuevo": 0.0,
            "ubicacion": almacen,
            "error": res
        }

    @staticmethod
    def mover_inventario_entre_etapas(codigo_sistema, cantidad, origen, destino):
        """
        Mueve inventario de un almacén a otro (salida + entrada), revirtiendo
        la salida si la entrada de destino falla.

        NOTA de migración: no se detectó ningún caller de esta función en todo
        el backend (ni en app.py antes de moverla, ni en ningún otro módulo).
        Se preserva porque se pidió explícitamente extraerla, pero es candidata
        a limpieza de código muerto en una fase futura.
        """
        res_salida = StockService.registrar_salida(codigo_sistema, cantidad, origen)
        if "error" in res_salida:
            return False, res_salida["error"]

        res_entrada = StockService.registrar_entrada(codigo_sistema, cantidad, destino)
        if "error" in res_entrada:
            StockService.registrar_entrada(codigo_sistema, cantidad, origen)
            return False, res_entrada["error"]

        return True, f'Movimiento exitoso: {cantidad} de {origen} a {destino}'
