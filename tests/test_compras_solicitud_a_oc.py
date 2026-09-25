# -*- coding: utf-8 -*-
"""
Cobertura nueva del módulo de Compras (SolicitudCompra -> OrdenCompra ->
Factura) -- dominio sin ningún test antes de este archivo. compras_service.py
ya envuelve toda escritura en try/except + rollback (patrón correcto desde
que se escribió, ver su docstring de módulo); estos tests caracterizan ese
comportamiento con datos borde: campos vacíos, texto especial, línea con
cantidad inválida a medio flujo, y una factura duplicada para la misma OC.
"""
import pytest

from backend.core.sql_database import db
from backend.models.sql_models import (
    DbProveedor, SolicitudCompra, OrdenCompraProveedor, LineaOrdenCompra,
    FacturaCompraOC,
)
from backend.services.compras_service import (
    SolicitudCompraService, SolicitudCompraError,
    OrdenCompraService, OrdenCompraError,
    FacturaCompraService, FacturaCompraError,
)

PREFIJO_TEST = "TEST-CMP-"
NIT_PROVEEDOR_TEST = f"{PREFIJO_TEST}NIT-1"
USUARIO_TEST = "TEST QA Robot"


@pytest.fixture()
def limpiar_compras_test(app_context):
    def _limpiar():
        ocs = OrdenCompraProveedor.query.filter(
            OrdenCompraProveedor.numero_oc.like(f"%{PREFIJO_TEST}%")
        ).all()
        # Las OC de compras reales las numera OcNumeradorService (OC-N), no
        # llevan nuestro prefijo en numero_oc -- para no dejar basura entre
        # corridas, limpiamos por proveedor_nit de test en vez de por
        # numero_oc.
        ocs = OrdenCompraProveedor.query.filter(
            OrdenCompraProveedor.proveedor_nit == NIT_PROVEEDOR_TEST
        ).all()
        for oc in ocs:
            LineaOrdenCompra.query.filter_by(id_oc=oc.id).delete(synchronize_session=False)
            FacturaCompraOC.query.filter_by(id_oc=oc.id).delete(synchronize_session=False)
        OrdenCompraProveedor.query.filter(
            OrdenCompraProveedor.proveedor_nit == NIT_PROVEEDOR_TEST
        ).delete(synchronize_session=False)
        SolicitudCompra.query.filter(
            SolicitudCompra.solicitado_por == USUARIO_TEST
        ).delete(synchronize_session=False)
        DbProveedor.query.filter(DbProveedor.nit == NIT_PROVEEDOR_TEST).delete(
            synchronize_session=False
        )
        db.session.commit()

    _limpiar()
    db.session.add(DbProveedor(nit=NIT_PROVEEDOR_TEST, proveedores='Proveedor Test S.A.S. "Ñandú" & Cía.'))
    db.session.commit()
    yield
    _limpiar()


class TestSolicitudCompra:
    def test_sin_producto_seleccionado_del_catalogo_se_rechaza(self, limpiar_compras_test):
        with pytest.raises(SolicitudCompraError):
            SolicitudCompraService.crear(
                item_descripcion="Algo escrito a mano",
                solicitado_por=USUARIO_TEST,
                codigo_producto="   ",  # texto libre sin seleccionar del catálogo
            )
        assert SolicitudCompra.query.filter_by(solicitado_por=USUARIO_TEST).count() == 0

    def test_texto_especial_en_descripcion_y_nota_se_guarda_intacto(self, limpiar_compras_test):
        descripcion = 'Repuesto "premium" Ñ/ñ 100% áéíóú <urgente>'
        nota = "Nota con salto\nde línea y comillas \"dobles\""
        solicitud = SolicitudCompraService.crear(
            item_descripcion=descripcion,
            solicitado_por=USUARIO_TEST,
            codigo_producto=f"{PREFIJO_TEST}COD-1",
            nota=nota,
        )
        assert solicitud.item_descripcion == descripcion
        assert solicitud.nota == nota
        assert solicitud.estado == "PENDIENTE"

    def test_cancelar_de_otro_usuario_sin_ser_admin_se_rechaza(self, limpiar_compras_test):
        solicitud = SolicitudCompraService.crear(
            item_descripcion="Repuesto X",
            solicitado_por=USUARIO_TEST,
            codigo_producto=f"{PREFIJO_TEST}COD-2",
        )
        with pytest.raises(SolicitudCompraError):
            SolicitudCompraService.cancelar(
                solicitud.id, usuario="TEST Otro Usuario", es_admin=False
            )
        db.session.refresh(solicitud)
        assert solicitud.estado == "PENDIENTE"


class TestOrdenCompra:
    def test_proveedor_inexistente_se_rechaza(self, limpiar_compras_test):
        with pytest.raises(OrdenCompraError):
            OrdenCompraService.crear(
                proveedor_nit=f"{PREFIJO_TEST}NIT-NO-EXISTE",
                fecha_oc="2026-01-15",
                creado_por=USUARIO_TEST,
                lineas=[{"descripcion": "Item", "cantidad_pedida": 1}],
            )

    def test_linea_con_cantidad_cero_se_rechaza_y_no_deja_la_oc_huerfana(self, limpiar_compras_test):
        # Caso borde real: el servicio hace add(orden) + flush() ANTES de
        # validar cada línea, así que si una línea es inválida, el rollback
        # debe deshacer también la cabecera de la OC ya "flusheada" -- no
        # solo la línea. Verificamos que no quede ninguna OC huérfana para
        # este proveedor.
        with pytest.raises(OrdenCompraError):
            OrdenCompraService.crear(
                proveedor_nit=NIT_PROVEEDOR_TEST,
                fecha_oc="2026-01-15",
                creado_por=USUARIO_TEST,
                lineas=[{"descripcion": "Item válido", "cantidad_pedida": 0}],
            )
        assert OrdenCompraProveedor.query.filter_by(proveedor_nit=NIT_PROVEEDOR_TEST).count() == 0

    def test_codigo_producto_none_en_una_linea_se_acepta(self, limpiar_compras_test):
        orden = OrdenCompraService.crear(
            proveedor_nit=NIT_PROVEEDOR_TEST,
            fecha_oc="2026-01-15",
            creado_por=USUARIO_TEST,
            lineas=[{
                "descripcion": "Item sin código de catálogo (compra puntual)",
                "cantidad_pedida": 3,
                "codigo_producto": None,
            }],
        )
        lineas = LineaOrdenCompra.query.filter_by(id_oc=orden.id).all()
        assert len(lineas) == 1
        assert lineas[0].codigo_producto is None


class TestFacturaCompra:
    def _crear_oc_de_prueba(self):
        return OrdenCompraService.crear(
            proveedor_nit=NIT_PROVEEDOR_TEST,
            fecha_oc="2026-01-15",
            creado_por=USUARIO_TEST,
            lineas=[{"descripcion": "Item facturable", "cantidad_pedida": 5, "valor_unitario": 1000}],
        )

    def test_factura_duplicada_para_la_misma_oc_se_rechaza(self, limpiar_compras_test):
        orden = self._crear_oc_de_prueba()
        linea = LineaOrdenCompra.query.filter_by(id_oc=orden.id).first()

        FacturaCompraService.cargar_factura(
            numero_oc=orden.numero_oc,
            numero_factura=f"{PREFIJO_TEST}FC-1",
            fecha_factura="2026-01-16",
            cargada_por=USUARIO_TEST,
            lineas=[{"id_linea_oc": linea.id, "cantidad_facturada": 5}],
        )

        with pytest.raises(FacturaCompraError):
            FacturaCompraService.cargar_factura(
                numero_oc=orden.numero_oc,
                numero_factura=f"{PREFIJO_TEST}FC-2",
                fecha_factura="2026-01-17",
                cargada_por=USUARIO_TEST,
                lineas=[{"id_linea_oc": linea.id, "cantidad_facturada": 5}],
            )

        assert FacturaCompraOC.query.filter_by(id_oc=orden.id).count() == 1

    def test_factura_con_cantidad_distinta_a_lo_recibido_marca_discrepancia(self, limpiar_compras_test):
        orden = self._crear_oc_de_prueba()
        linea = LineaOrdenCompra.query.filter_by(id_oc=orden.id).first()

        factura = FacturaCompraService.cargar_factura(
            numero_oc=orden.numero_oc,
            numero_factura=f"{PREFIJO_TEST}FC-3",
            fecha_factura="2026-01-16",
            cargada_por=USUARIO_TEST,
            # Nada se ha recibido aún (0 recibido) pero se factura 5 ->
            # diferencia != 0 -> DISCREPANCIA.
            lineas=[{"id_linea_oc": linea.id, "cantidad_facturada": 5}],
        )
        assert factura.estado_conciliacion == "DISCREPANCIA"
