# -*- coding: utf-8 -*-
"""
Tests del guard 2026-09-23 en InyeccionService.registrar_pnc: un lote CERRADO
(ya auditado en Validación) no debe perder su desglose de PNC ni su
cantidad_real, que es lo que sale a WO como 'Cantidad Recibida'.

Corre contra la base configurada en DATABASE_URL (fritech_local, NUNCA
producción). Usa maquina/id_inyeccion con prefijo TEST- y limpia en
setUp/tearDown.
"""
import os
import unittest

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProduccionInyeccion, PncInyeccion
from backend.services.inyeccion_service import InyeccionService
from backend.utils.formatters import normalizar_codigo

MAQUINA_TEST = "TEST-Maq Claude PNC"
ID_INY_TEST = "INY-TESTPNCCERRADO"
CODIGO_TEST = normalizar_codigo("TEST-9001")


class TestRegistrarPncLoteCerrado(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        db.session.rollback()
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(PncInyeccion).filter(
            PncInyeccion.id_inyeccion == ID_INY_TEST
        ).delete(synchronize_session=False)
        db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.maquina == MAQUINA_TEST
        ).delete(synchronize_session=False)
        db.session.commit()

    def _crear_lote(self, estado, contador, cantidad_real, pnc_total=0):
        reg = ProduccionInyeccion(
            id_inyeccion=ID_INY_TEST, id_codigo=CODIGO_TEST, maquina=MAQUINA_TEST,
            cavidades=1, cant_contador=contador, cantidad_real=cantidad_real,
            pnc_total=pnc_total, estado=estado,
        )
        db.session.add(reg)
        db.session.commit()
        return reg

    def _pnc_rows(self):
        return db.session.query(PncInyeccion).filter_by(
            id_inyeccion=ID_INY_TEST, id_codigo=CODIGO_TEST
        ).all()

    def test_lote_cerrado_no_pierde_cantidad_real_ni_desglose_pnc(self):
        # Zoe validó: contador 243, buenas 230, PNC 13.
        self._crear_lote('CERRADO', contador=243, cantidad_real=230, pnc_total=13)
        db.session.add(PncInyeccion(
            id_pnc_inyeccion="TESTPNC1", id_inyeccion=ID_INY_TEST, id_codigo=CODIGO_TEST,
            cantidad=13, criterio="Rebaba: 13 (validado)", rebaba_excesiva=13,
        ))
        db.session.commit()

        # Reenvío tardío del reporte de MES con otros defectos (y caracteres raros).
        res = InyeccionService.registrar_pnc({
            'id_inyeccion': ID_INY_TEST, 'id_codigo': CODIGO_TEST,
            'defectos': {"Quemado/ñ 'x'\"": 5},
        })

        self.assertFalse(res['success'])
        db.session.expire_all()
        reg = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=ID_INY_TEST).first()
        self.assertEqual(int(reg.cantidad_real), 230, "no debe pisar lo validado")
        self.assertEqual(int(reg.cant_contador), 243)
        self.assertEqual(int(reg.pnc_total), 13)
        filas = self._pnc_rows()
        self.assertEqual(len(filas), 1, "el desglose validado no debe borrarse")
        self.assertEqual(filas[0].criterio, "Rebaba: 13 (validado)")

    def test_lote_cerrado_sin_defectos_tampoco_se_toca(self):
        # El camino "sin defectos" dejaba cantidad_real = cant_contador.
        self._crear_lote('CERRADO', contador=243, cantidad_real=230, pnc_total=13)

        res = InyeccionService.registrar_pnc({
            'id_inyeccion': ID_INY_TEST, 'id_codigo': CODIGO_TEST, 'defectos': {},
        })

        self.assertFalse(res['success'])
        db.session.expire_all()
        reg = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=ID_INY_TEST).first()
        self.assertEqual(int(reg.cantidad_real), 230)
        self.assertEqual(int(reg.pnc_total), 13)

    def test_lote_pendiente_con_defectos_sigue_restando_pnc(self):
        # Flujo normal de MES: no debe romperse por el guard.
        self._crear_lote('PENDIENTE', contador=243, cantidad_real=243)

        res = InyeccionService.registrar_pnc({
            'id_inyeccion': ID_INY_TEST, 'id_codigo': CODIGO_TEST,
            'defectos': {"Rebaba": 10, "Quemado": 3},
        })

        self.assertTrue(res['success'])
        db.session.expire_all()
        reg = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=ID_INY_TEST).first()
        self.assertEqual(int(reg.cantidad_real), 230)
        self.assertEqual(int(reg.pnc_total), 13)
        self.assertEqual(len(self._pnc_rows()), 1)

    def test_lote_pendiente_sin_defectos_deja_cantidad_real_igual_al_contador(self):
        self._crear_lote('PENDIENTE', contador=243, cantidad_real=200, pnc_total=43)

        res = InyeccionService.registrar_pnc({
            'id_inyeccion': ID_INY_TEST, 'id_codigo': CODIGO_TEST, 'defectos': {},
        })

        self.assertTrue(res['success'])
        db.session.expire_all()
        reg = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=ID_INY_TEST).first()
        self.assertEqual(int(reg.cantidad_real), 243)
        self.assertEqual(int(reg.pnc_total), 0)

    def test_lote_inexistente_no_revienta(self):
        # id_inyeccion sin fila en db_inyeccion: no hay nada que proteger ni tocar.
        res = InyeccionService.registrar_pnc({
            'id_inyeccion': ID_INY_TEST, 'id_codigo': CODIGO_TEST,
            'defectos': {"Rebaba": 2},
        })
        self.assertTrue(res['success'])


if __name__ == '__main__':
    unittest.main()
