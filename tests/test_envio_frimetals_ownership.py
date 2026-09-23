# -*- coding: utf-8 -*-
"""
Tests de PedidosService.actualizar_envio_frimetals: el chequeo explícito de
`tiene_pedido_frimetals` agregado como defensa en profundidad (auditoria de
seguridad 2026-09-23) no debe romper el flujo legítimo, y sí debe bloquear un
pedido que nunca se marcó como conjunto con Frimetals aunque sus líneas ya
estén (por cualquier vía) en estado ENVIADO_FRIPARTS.
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import Pedido
from backend.services.pedidos_service import (
    PedidosService,
    PedidoNoEncontradoError,
    EnvioFrimetalsIncompletoError,
)

ID_PEDIDO_TEST = "TEST-AUDIT-ENVIO-FRIMETALS-1"


class TestActualizarEnvioFrimetalsOwnership(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(Pedido).filter(Pedido.id_pedido == ID_PEDIDO_TEST).delete(
            synchronize_session=False
        )
        db.session.commit()

    def _crear_pedido(self, tiene_pedido_frimetals, estado_envio_frimetals):
        fila = Pedido(
            id_pedido=ID_PEDIDO_TEST,
            id_codigo="TEST-CODIGO-1",
            cliente="Cliente Test",
            tiene_pedido_frimetals=tiene_pedido_frimetals,
            estado_envio_frimetals=estado_envio_frimetals,
        )
        db.session.add(fila)
        db.session.commit()

    def test_pedido_no_marcado_conjunto_se_rechaza_aunque_lineas_esten_enviadas(self):
        # Caso borde: si por cualquier vía las líneas ya quedaron en
        # ENVIADO_FRIPARTS pero el pedido NUNCA se marcó como conjunto con
        # Frimetals, el nuevo guard debe bloquear -- antes de este fix, esto
        # solo lo impedía indirectamente el control de roles de otro endpoint.
        self._crear_pedido(tiene_pedido_frimetals=False, estado_envio_frimetals="ENVIADO_FRIPARTS")

        with self.assertRaises(ValueError) as ctx:
            PedidosService.actualizar_envio_frimetals(
                id_pedido=ID_PEDIDO_TEST,
                nuevo_estado="DESPACHADO_FRIPARTS",
                db_session=db.session,
            )
        self.assertIn("no está marcado como pedido conjunto", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, EnvioFrimetalsIncompletoError)

    def test_pedido_marcado_conjunto_pero_lineas_pendientes_conserva_error_original(self):
        # El guard nuevo no debe tapar/reemplazar el error de negocio ya
        # existente cuando SÍ es un pedido conjunto legítimo pero aún le
        # falta alistamiento.
        self._crear_pedido(tiene_pedido_frimetals=True, estado_envio_frimetals="PENDIENTE")

        with self.assertRaises(EnvioFrimetalsIncompletoError):
            PedidosService.actualizar_envio_frimetals(
                id_pedido=ID_PEDIDO_TEST,
                nuevo_estado="DESPACHADO_FRIPARTS",
                db_session=db.session,
            )

    def test_pedido_marcado_conjunto_y_lineas_listas_se_actualiza_correctamente(self):
        # Flujo legítimo: no debe romperse por el guard nuevo.
        self._crear_pedido(tiene_pedido_frimetals=True, estado_envio_frimetals="ENVIADO_FRIPARTS")

        resultado = PedidosService.actualizar_envio_frimetals(
            id_pedido=ID_PEDIDO_TEST,
            nuevo_estado="DESPACHADO_FRIPARTS",
            db_session=db.session,
        )
        db.session.commit()

        fila = Pedido.query.filter_by(id_pedido=ID_PEDIDO_TEST).first()
        self.assertEqual(fila.estado_envio_frimetals, "DESPACHADO_FRIPARTS")

    def test_pedido_inexistente_sigue_lanzando_no_encontrado(self):
        with self.assertRaises(PedidoNoEncontradoError):
            PedidosService.actualizar_envio_frimetals(
                id_pedido="TEST-AUDIT-NO-EXISTE-XYZ",
                nuevo_estado="DESPACHADO_FRIPARTS",
                db_session=db.session,
            )


if __name__ == "__main__":
    unittest.main()
