# -*- coding: utf-8 -*-
"""
Cobertura nueva de InventarioService -- dominio sin ningún test antes de
este archivo. Cubre la lógica de semáforo de stock (pura, sin BD) y el
camino de escritura real (`crear_producto`) con casos borde: caracteres
especiales/tildes/comillas en la descripción, precio NULL, y
codigo_sistema duplicado (debe fallar limpio, sin dejar la sesión de
Postgres colgada a medio commit).
"""
import pytest

from backend.services.inventario_service import InventarioService
from backend.core.sql_database import db
from backend.models.sql_models import Producto

PREFIJO_TEST = "TEST-INV-"


@pytest.fixture()
def limpiar_productos_test(app_context):
    def _limpiar():
        db.session.query(Producto).filter(
            Producto.codigo_sistema.like(f"{PREFIJO_TEST}%")
        ).delete(synchronize_session=False)
        db.session.commit()

    _limpiar()
    yield
    _limpiar()


class TestCalcularMetricasSemaforo:
    """`_calcular_metricas_semaforo` es pura (sin BD) -- prueba directa de
    los umbrales tal como los implementa el servicio."""

    def test_stock_cero_es_agotado(self):
        r = InventarioService._calcular_metricas_semaforo(0, p_min=10, p_reorden=5, p_max=100)
        assert r["estado"] == "AGOTADO"
        assert r["color"] == "dark"

    def test_stock_negativo_tambien_es_agotado(self):
        # Caso borde: un stock negativo no debería existir en teoría, pero
        # si llega (ej. por un ajuste manual mal hecho), el semáforo no debe
        # reventar ni mostrar un estado distinto de AGOTADO.
        r = InventarioService._calcular_metricas_semaforo(-5, p_min=10, p_reorden=5, p_max=100)
        assert r["estado"] == "AGOTADO"

    def test_stock_exactamente_en_el_punto_de_reorden_es_critico(self):
        # Borde inclusive: <= p_reorden es CRÍTICO, no "por pedir".
        r = InventarioService._calcular_metricas_semaforo(5, p_min=10, p_reorden=5, p_max=100)
        assert r["estado"] == "CRÍTICO"
        assert r["color"] == "red"

    def test_stock_entre_reorden_y_minimo_es_por_pedir(self):
        r = InventarioService._calcular_metricas_semaforo(7, p_min=10, p_reorden=5, p_max=100)
        assert r["estado"] == "POR PEDIR"
        assert r["color"] == "yellow"

    def test_stock_en_o_sobre_minimo_es_stock_ok(self):
        r = InventarioService._calcular_metricas_semaforo(10, p_min=10, p_reorden=5, p_max=100)
        assert r["estado"] == "STOCK OK"
        assert r["color"] == "green"

    def test_sin_configuracion_de_max_no_muestra_mensaje_aunque_haya_stock(self):
        # p_max None/0/999999 significa "sin configurar" -- el mensaje se
        # vacía a propósito cuando además hay stock (no es un error, es
        # "no hay semáforo configurado para este producto").
        r = InventarioService._calcular_metricas_semaforo(50, p_min=10, p_reorden=5, p_max=None)
        assert r["configurado"] is False
        assert r["mensaje"] == ""


class TestCrearProducto:
    def test_caracteres_especiales_tildes_y_comillas_se_guardan_intactos(self, limpiar_productos_test):
        descripcion = 'Buje Ñ especial 3/4" - "Edición" limitada (áéíóú)'
        InventarioService.crear_producto(
            id_codigo=f"{PREFIJO_TEST}ESPECIAL-1",
            codigo_sistema_raw=f"{PREFIJO_TEST}ESPECIAL-1",
            descripcion=descripcion,
            precio=None,
            stock_inicial=0,
        )

        fila = Producto.query.filter_by(codigo_sistema=f"{PREFIJO_TEST}ESPECIAL-1").first()
        assert fila is not None
        assert fila.descripcion == descripcion
        # precio=None debe persistir como NULL (o el default de la columna),
        # nunca reventar la escritura.
        assert fila.precio in (None, 0)

    def test_id_codigo_vacio_lanza_value_error_y_no_escribe_nada(self, limpiar_productos_test):
        with pytest.raises(ValueError):
            InventarioService.crear_producto(
                id_codigo="   ",
                codigo_sistema_raw=f"{PREFIJO_TEST}VACIO-1",
                descripcion="Algo",
                precio=10,
                stock_inicial=0,
            )

        fila = Producto.query.filter_by(codigo_sistema=f"{PREFIJO_TEST}VACIO-1").first()
        assert fila is None

    def test_descripcion_vacia_lanza_value_error_y_no_escribe_nada(self, limpiar_productos_test):
        with pytest.raises(ValueError):
            InventarioService.crear_producto(
                id_codigo=f"{PREFIJO_TEST}SINDESC-1",
                codigo_sistema_raw=f"{PREFIJO_TEST}SINDESC-1",
                descripcion="   ",
                precio=10,
                stock_inicial=0,
            )

    def test_codigo_sistema_duplicado_falla_limpio_y_no_deja_la_sesion_rota(self, limpiar_productos_test):
        codigo = f"{PREFIJO_TEST}DUP-1"
        InventarioService.crear_producto(
            id_codigo=codigo,
            codigo_sistema_raw=codigo,
            descripcion="Original",
            precio=5,
            stock_inicial=0,
        )

        with pytest.raises(Exception):
            InventarioService.crear_producto(
                id_codigo=codigo,
                codigo_sistema_raw=codigo,
                descripcion="Duplicado",
                precio=5,
                stock_inicial=0,
            )

        # La escritura fallida ya hizo rollback dentro del propio servicio
        # (try/except + db.session.rollback()) -- si NO lo hubiera hecho,
        # esta siguiente consulta fallaría con
        # "current transaction is aborted" en vez de devolver datos.
        fila = Producto.query.filter_by(codigo_sistema=codigo).first()
        assert fila is not None
        assert fila.descripcion == "Original"


class TestBuscarAlternativas:
    def test_es_case_insensitive_y_respeta_tildes_en_el_id_codigo(self, limpiar_productos_test):
        InventarioService.crear_producto(
            id_codigo=f"{PREFIJO_TEST}alt-Ñoño",
            codigo_sistema_raw=f"{PREFIJO_TEST}ALT-1",
            descripcion="Producto con interno en minúsculas y ñ",
            precio=1,
            stock_inicial=3,
        )

        resultado = InventarioService.buscar_alternativas(f"{PREFIJO_TEST}ALT-ÑOÑO")
        assert len(resultado) == 1
        assert resultado[0]["CODIGO"] == f"{PREFIJO_TEST}ALT-1"
