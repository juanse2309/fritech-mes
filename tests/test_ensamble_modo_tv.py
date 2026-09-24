# -*- coding: utf-8 -*-
"""
Tests de la pantalla "Metas de Ensamble" del Modo TV (2026-09-24). El endpoint
GET /api/ensamble/modo_tv es un envoltorio de EnsambleService.listar_historial_
metas con los roles de ROL_MODO_TV, así que estos tests cubren el contrato de
datos que consume modo_tv.js: campos de cada meta, checklist completo aunque no
exista la fila en db_checklist_ensamble, y datos borde (objetivo 0, OP nula,
más avance que objetivo, meta atrasada, meta ya completada, código con
comillas/HTML/tildes).

A propósito NO hay una lista de "quién está ensamblando ahora": la UI de
Ensamble no escribe un inicio en la BD (solo al pausar/reanudar o finalizar),
así que esa lista sería siempre incompleta o vacía y mostrarla en la TV daría
información falsa.

Corren contra la base configurada en DATABASE_URL (igual que el resto de tests
del repo) con filas TEST-ENSTV-* que se limpian en setUp/tearDown.
listar_historial_metas es global y limita a 12 filas, así que estos tests
asumen una BD de pruebas/CI casi vacía y afirman solo sobre sus propias filas.
"""
import os
import unittest
from datetime import timedelta

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ChecklistEnsamble, ProgramacionEnsamble
from backend.services.ensamble_service import EnsambleService, PROCESOS_CHECKLIST
from backend.utils.time_utils import get_colombia_time

PREFIJO = "TEST-ENSTV"


class TestMetasEnsambleModoTV(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()
        self.hoy = get_colombia_time().date()

    def tearDown(self):
        db.session.rollback()
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        ids = [r.id_prog for r in ProgramacionEnsamble.query.filter(
            ProgramacionEnsamble.id_codigo.like(f"{PREFIJO}%")).all()]
        if ids:
            ChecklistEnsamble.query.filter(ChecklistEnsamble.id_prog.in_(ids)).delete(synchronize_session=False)
        ProgramacionEnsamble.query.filter(ProgramacionEnsamble.id_codigo.like(f"{PREFIJO}%")).delete(synchronize_session=False)
        db.session.commit()

    def _meta(self, sufijo, objetivo, realizada, estado, fecha=None, op=None):
        m = ProgramacionEnsamble(
            id_codigo=f"{PREFIJO}-{sufijo}", op_numero=op, cantidad_objetivo=objetivo,
            cantidad_realizada=realizada, fecha_programada=fecha or self.hoy, estado=estado)
        db.session.add(m)
        db.session.commit()
        return m

    def _mias(self):
        return {m['id_codigo']: m for m in EnsambleService.listar_historial_metas()
                if m['id_codigo'].startswith(PREFIJO)}

    def test_campos_que_consume_la_tv(self):
        self._meta('A', 100, 40, 'EN_PROCESO', op='300451')

        m = self._mias()[f"{PREFIJO}-A"]

        for campo in ('id_codigo', 'cantidad_objetivo', 'cantidad_realizada',
                      'fecha_programada', 'estado', 'op_numero', 'checklist'):
            self.assertIn(campo, m)
        self.assertEqual((m['cantidad_objetivo'], m['cantidad_realizada']), (100, 40))
        self.assertEqual(m['fecha_programada'], self.hoy.strftime('%Y-%m-%d'))

    def test_checklist_completo_aunque_no_exista_fila(self):
        self._meta('SIN-CK', 10, 0, 'PENDIENTE')

        ck = self._mias()[f"{PREFIJO}-SIN-CK"]['checklist']

        self.assertEqual(set(ck), set(PROCESOS_CHECKLIST))
        self.assertEqual(set(ck.values()), {'PENDIENTE'})

    def test_checklist_guardado_se_devuelve_tal_cual(self):
        meta = self._meta('CK', 10, 5, 'EN_PROCESO')
        db.session.add(ChecklistEnsamble(id_prog=meta.id_prog, ensamble_crudo_estado='HECHO',
                                         rayada_carcaza_estado='NO_APLICA', pintura_estado='PENDIENTE'))
        db.session.commit()

        ck = self._mias()[f"{PREFIJO}-CK"]['checklist']

        self.assertEqual(ck['ensamble_crudo'], 'HECHO')
        self.assertEqual(ck['rayada_carcaza'], 'NO_APLICA')
        self.assertEqual(ck['pintura'], 'PENDIENTE')

    def test_datos_borde_no_rompen_el_listado(self):
        ayer = self.hoy - timedelta(days=1)
        self._meta('OBJ0', 0, 0, 'PENDIENTE', op=None)                    # objetivo 0, OP nula
        self._meta('SOBRE', 300, 360, 'EN_PROCESO', op='300462')          # más avance que objetivo
        self._meta('ATRASADA', 500, 0, 'PENDIENTE', fecha=ayer)           # pendiente de ayer
        self._meta('HECHA', 800, 800, 'COMPLETADO', op='300460')          # completada hoy
        self._meta('<b>"Ñ\'&</b>', 5, 0, 'PENDIENTE')                     # comillas, HTML, tilde

        mias = self._mias()

        self.assertEqual(len(mias), 5)
        self.assertEqual(mias[f"{PREFIJO}-<b>\"Ñ'&</b>"]['id_codigo'], f"{PREFIJO}-<b>\"Ñ'&</b>")
        self.assertIsNone(mias[f"{PREFIJO}-OBJ0"]['op_numero'])
        self.assertEqual(mias[f"{PREFIJO}-ATRASADA"]['fecha_programada'], ayer.strftime('%Y-%m-%d'))


if __name__ == "__main__":
    unittest.main()
