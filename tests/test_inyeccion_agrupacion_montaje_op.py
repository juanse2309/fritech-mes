# -*- coding: utf-8 -*-
"""
Tests del fix 2026-09-04: iniciar un montaje de Inyección agrupaba por
máquina+fecha+molde SIN exigir también la OP (backend/services/inyeccion_service.py
InyeccionService.iniciar_trabajo, y su duplicado
backend/services/programacion_service.py ProgramacionService.iniciar_produccion_batch).
Dos montajes distintos programados para la misma máquina/fecha que comparten
letra de molde (ej. uno al inicio de jornada y otro a las 12) se fusionaban en
un solo lote al iniciar cualquiera de los dos. El fix agrega
ProgramacionInyeccion.op_world_office al filtro de agrupación, igual que ya
exigía obtener_status_maquina.

Corre contra la base de datos real configurada en DATABASE_URL (mismo patrón
que tests/test_registrar_lote.py -- no hay BD de staging separada en este
proyecto). Usa máquina/molde/OP con el prefijo TEST- para no colisionar con
datos reales, y limpia todo lo que crea en setUp/tearDown.
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProgramacionInyeccion, ProduccionInyeccion, TrazabilidadLote
from backend.services.inyeccion_service import InyeccionService
from backend.services.programacion_service import ProgramacionService
from datetime import date

MAQUINA_TEST = "TEST-MAQ-CLAUDE-GROUP"
MOLDE_TEST = "TESTMOLDEGROUP"


class TestAgrupacionMontajePorOP(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(TrazabilidadLote).filter(
            TrazabilidadLote.maquina == MAQUINA_TEST
        ).delete(synchronize_session=False)
        db.session.query(ProduccionInyeccion).filter(
            db.func.upper(ProduccionInyeccion.maquina) == MAQUINA_TEST.upper()
        ).delete(synchronize_session=False)
        db.session.query(ProgramacionInyeccion).filter(
            ProgramacionInyeccion.maquina == MAQUINA_TEST
        ).delete(synchronize_session=False)
        db.session.commit()

    def _crear_dos_montajes_mismo_molde(self):
        """Dos montajes DISTINTOS (OP-TEST-A / OP-TEST-B) para la misma
        máquina, fecha y letra de molde -- el caso exacto que el usuario
        reportó (uno al inicio de jornada, otro a las 12)."""
        fila_a = ProgramacionInyeccion(
            fecha=date.today(), maquina=MAQUINA_TEST, codigo_sistema='TEST-A001',
            molde=MOLDE_TEST, cavidades=2, estado='PROGRAMADO', op_world_office='OP-TEST-A'
        )
        fila_b = ProgramacionInyeccion(
            fecha=date.today(), maquina=MAQUINA_TEST, codigo_sistema='TEST-B001',
            molde=MOLDE_TEST, cavidades=4, estado='PROGRAMADO', op_world_office='OP-TEST-B'
        )
        db.session.add_all([fila_a, fila_b])
        db.session.commit()
        return fila_a.id, fila_b.id

    def test_iniciar_trabajo_no_fusiona_montajes_con_distinta_op(self):
        """Ruta real del botón 'Iniciar Montaje' del dashboard (POST /api/mes/iniciar_trabajo)."""
        id_a, id_b = self._crear_dos_montajes_mismo_molde()

        resultado = InyeccionService.iniciar_trabajo(
            {'id_programacion': id_a, 'responsable': 'TEST ROBOT'}, usuario_activo=None
        )
        self.assertTrue(resultado['success'])

        fila_a_final = db.session.get(ProgramacionInyeccion, id_a)
        fila_b_final = db.session.get(ProgramacionInyeccion, id_b)
        self.assertEqual(fila_a_final.estado, 'EN_PROCESO', "el montaje A debio iniciarse")
        self.assertEqual(fila_b_final.estado, 'PROGRAMADO',
                          "el montaje B (misma maquina/fecha/molde pero otra OP) NO debio arrancarse junto con A")

        produccion = db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.id_inyeccion == resultado['id_inyeccion']
        ).all()
        self.assertEqual(len(produccion), 1, "solo debio crear produccion para la referencia del montaje A")
        self.assertEqual(produccion[0].molde, MOLDE_TEST)

    def test_iniciar_produccion_batch_no_fusiona_montajes_con_distinta_op(self):
        """Ruta legacy /api/mes/iniciar (Modo Satélite de Inyección)."""
        id_a, id_b = self._crear_dos_montajes_mismo_molde()

        resultado = ProgramacionService.iniciar_produccion_batch(id_a, 'TEST ROBOT')
        self.assertEqual(resultado['count'], 1, "solo debio arrancar la programacion del montaje A")

        fila_a_final = db.session.get(ProgramacionInyeccion, id_a)
        fila_b_final = db.session.get(ProgramacionInyeccion, id_b)
        self.assertEqual(fila_a_final.estado, 'EN_PROCESO')
        self.assertEqual(fila_b_final.estado, 'PROGRAMADO',
                          "el montaje B (otra OP) NO debio arrancarse junto con A")

        produccion = db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.id_inyeccion == resultado['id_inyeccion']
        ).all()
        self.assertEqual(len(produccion), 1)

    def test_iniciar_trabajo_SI_agrupa_dos_referencias_de_la_misma_op(self):
        """Caracterización de que el fix no rompió el caso normal: dos filas
        de la MISMA OP+molde+máquina+fecha (montaje multi-SKU real) se siguen
        agrupando e iniciando juntas, como siempre."""
        fila_1 = ProgramacionInyeccion(
            fecha=date.today(), maquina=MAQUINA_TEST, codigo_sistema='TEST-C001',
            molde=MOLDE_TEST, cavidades=2, estado='PROGRAMADO', op_world_office='OP-TEST-C'
        )
        fila_2 = ProgramacionInyeccion(
            fecha=date.today(), maquina=MAQUINA_TEST, codigo_sistema='TEST-C002',
            molde=MOLDE_TEST, cavidades=2, estado='PROGRAMADO', op_world_office='OP-TEST-C'
        )
        db.session.add_all([fila_1, fila_2])
        db.session.commit()
        id_1 = fila_1.id

        resultado = InyeccionService.iniciar_trabajo(
            {'id_programacion': id_1, 'responsable': 'TEST ROBOT'}, usuario_activo=None
        )
        self.assertTrue(resultado['success'])

        produccion = db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.id_inyeccion == resultado['id_inyeccion']
        ).all()
        self.assertEqual(len(produccion), 2, "las dos referencias de la MISMA OP debian iniciarse juntas")


if __name__ == '__main__':
    unittest.main()
