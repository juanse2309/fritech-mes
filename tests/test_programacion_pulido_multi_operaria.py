# -*- coding: utf-8 -*-
"""
Tests del cambio 2026-09-04 al panel de Programación de Pulido:

1. ProgramacionPulidoService.crear_items ahora recibe la operaria POR CADA
   tarea (antes era una sola operaria para todo el POST) -- permite repartir
   la cola de hoy entre varias personas en un solo guardado. orden_prioridad
   ya no llega del frontend: se calcula por operaria, continuando la cola
   existente de cada una.

2. ProgramacionPulidoService.reordenar_cola (endpoint nuevo, usado por el
   arrastrar-y-soltar del tablero) fija orden_prioridad = 1..N según el orden
   exacto de ids recibido, e ignora tarjetas que ya no estén en PROGRAMADO.

Corre contra la base de datos real configurada en DATABASE_URL (mismo patrón
que tests/test_registrar_lote.py). Usa operarias/OP/códigos con prefijo TEST-
y limpia todo lo que crea en setUp/tearDown.
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from datetime import date
from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProgramacionPulido
from backend.services.programacion_pulido_service import ProgramacionPulidoService

OPERARIA_A = "TEST OPERARIA CLAUDE A"
OPERARIA_B = "TEST OPERARIA CLAUDE B"
OP_TEST = "OP-TEST-CLAUDE-9001"


class TestCrearItemsMultiOperariaYReordenar(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(ProgramacionPulido).filter(
            ProgramacionPulido.operaria.in_([OPERARIA_A, OPERARIA_B])
        ).delete(synchronize_session=False)
        db.session.commit()

    def test_reparte_items_entre_dos_operarias_en_un_solo_guardado(self):
        fecha = date.today().strftime('%Y-%m-%d')
        items = [
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-9001', 'cantidad_objetivo': 10},
            {'operaria': OPERARIA_B, 'orden_produccion': OP_TEST, 'codigo': 'TEST-9002', 'cantidad_objetivo': 20},
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-9003', 'cantidad_objetivo': 30},
        ]
        resultado = ProgramacionPulidoService.crear_items(fecha, items, 'TEST ROBOT')
        self.assertEqual(len(resultado['creados']), 3)

        cola_a = ProgramacionPulidoService.obtener_cola_operaria(OPERARIA_A, fecha)
        cola_b = ProgramacionPulidoService.obtener_cola_operaria(OPERARIA_B, fecha)

        self.assertEqual([c['codigo'] for c in cola_a], ['TEST-9001', 'TEST-9003'])
        self.assertEqual([c['orden_prioridad'] for c in cola_a], [1, 2])
        self.assertEqual([c['codigo'] for c in cola_b], ['TEST-9002'])
        self.assertEqual([c['orden_prioridad'] for c in cola_b], [1])

    def test_continua_el_orden_existente_de_cada_operaria_por_separado(self):
        fecha = date.today().strftime('%Y-%m-%d')
        ProgramacionPulidoService.crear_items(fecha, [
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-P1', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-P2', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_B, 'orden_produccion': OP_TEST, 'codigo': 'TEST-P3', 'cantidad_objetivo': 5},
        ], 'TEST ROBOT')

        ProgramacionPulidoService.crear_items(fecha, [
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-P4', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_B, 'orden_produccion': OP_TEST, 'codigo': 'TEST-P5', 'cantidad_objetivo': 5},
        ], 'TEST ROBOT')

        cola_a = ProgramacionPulidoService.obtener_cola_operaria(OPERARIA_A, fecha)
        cola_b = ProgramacionPulidoService.obtener_cola_operaria(OPERARIA_B, fecha)
        self.assertEqual([c['orden_prioridad'] for c in cola_a], [1, 2, 3])
        self.assertEqual([c['orden_prioridad'] for c in cola_b], [1, 2])

    def test_falla_si_ninguna_tarea_trae_operaria(self):
        fecha = date.today().strftime('%Y-%m-%d')
        with self.assertRaises(ValueError):
            ProgramacionPulidoService.crear_items(fecha, [
                {'orden_produccion': OP_TEST, 'codigo': 'TEST-SINOP', 'cantidad_objetivo': 5},
            ], 'TEST ROBOT')

    def test_reordenar_cola_fija_orden_exacto_por_drag_and_drop(self):
        fecha = date.today().strftime('%Y-%m-%d')
        resultado = ProgramacionPulidoService.crear_items(fecha, [
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-R1', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-R2', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-R3', 'cantidad_objetivo': 5},
        ], 'TEST ROBOT')
        ids = resultado['creados']  # [id_r1, id_r2, id_r3] en orden 1,2,3

        # Simula arrastrar R3 al inicio: nuevo orden deseado R3, R1, R2.
        out = ProgramacionPulidoService.reordenar_cola(OPERARIA_A, [ids[2], ids[0], ids[1]])
        self.assertEqual(out['actualizados'], 3)

        cola = ProgramacionPulidoService.obtener_cola_operaria(OPERARIA_A, fecha)
        self.assertEqual([c['codigo'] for c in cola], ['TEST-R3', 'TEST-R1', 'TEST-R2'])
        self.assertEqual([c['orden_prioridad'] for c in cola], [1, 2, 3])

    def test_reordenar_cola_ignora_tarjetas_ya_iniciadas(self):
        fecha = date.today().strftime('%Y-%m-%d')
        resultado = ProgramacionPulidoService.crear_items(fecha, [
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-L1', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-L2', 'cantidad_objetivo': 5},
        ], 'TEST ROBOT')
        ids = resultado['creados']

        item_bloqueado = db.session.get(ProgramacionPulido, ids[0])
        item_bloqueado.estado = 'EN_PROCESO'
        db.session.commit()

        out = ProgramacionPulidoService.reordenar_cola(OPERARIA_A, [ids[0], ids[1]])
        self.assertEqual(out['actualizados'], 1, "solo debio tocar la tarjeta PROGRAMADO, no la EN_PROCESO")

        item_bloqueado_final = db.session.get(ProgramacionPulido, ids[0])
        self.assertEqual(item_bloqueado_final.estado, 'EN_PROCESO')
        self.assertEqual(item_bloqueado_final.orden_prioridad, 1, "una tarjeta bloqueada no debio renumerarse")

    def test_flujo_completo_reasignar_y_reordenar_como_el_drag_and_drop(self):
        """Reproduce exactamente lo que hace el frontend al soltar una
        tarjeta en la columna de otra operaria (ver
        pulido.js _persistirOrdenColumnaTrasDrag): primero reasigna la
        operaria (actualizar_item), luego fija el orden exacto de la
        columna destino (reordenar_cola)."""
        fecha = date.today().strftime('%Y-%m-%d')
        resultado = ProgramacionPulidoService.crear_items(fecha, [
            {'operaria': OPERARIA_A, 'orden_produccion': OP_TEST, 'codigo': 'TEST-D1', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_B, 'orden_produccion': OP_TEST, 'codigo': 'TEST-D2', 'cantidad_objetivo': 5},
            {'operaria': OPERARIA_B, 'orden_produccion': OP_TEST, 'codigo': 'TEST-D3', 'cantidad_objetivo': 5},
        ], 'TEST ROBOT')
        ids = resultado['creados']
        id_d1 = ids[0]

        # Arrastra D1 (de A) a la columna de B, soltándola ENTRE D2 y D3.
        ProgramacionPulidoService.actualizar_item(id_d1, {'operaria': OPERARIA_B})
        ProgramacionPulidoService.reordenar_cola(OPERARIA_B, [ids[1], id_d1, ids[2]])

        cola_b = ProgramacionPulidoService.obtener_cola_operaria(OPERARIA_B, fecha)
        self.assertEqual([c['codigo'] for c in cola_b], ['TEST-D2', 'TEST-D1', 'TEST-D3'])

        cola_a = ProgramacionPulidoService.obtener_cola_operaria(OPERARIA_A, fecha)
        self.assertEqual(cola_a, [], "A no debio quedar con nada tras la reasignacion")


if __name__ == '__main__':
    unittest.main()
