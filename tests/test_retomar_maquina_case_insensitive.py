# -*- coding: utf-8 -*-
"""
Tests del fix 2026-09-04: ProgramacionService.retomar_programacion comparaba
ProgramacionInyeccion.maquina == maquina con `maquina` siempre en MAYÚSCULAS
(viene de `.upper()` en el propio método), pero la columna real se guarda con
el nombre tal cual llega del formulario (ej. 'MAQUINA No. 1', con 'No.' en
minúscula sostenida). Un match exacto nunca coincidía: 'Retomar' decía
'ya en uso' por el chequeo de ProduccionInyeccion (ese sí ya era
case-insensitive) y, tras liberar la máquina, jamás encontraba el historial
del día anterior. El fix vuelve case-insensitive las 3 consultas por máquina
en ese método y conserva el casing original al recrear la fila.

Corre contra la base de datos real configurada en DATABASE_URL (mismo patrón
que tests/test_registrar_lote.py). Usa un nombre de máquina con prefijo TEST-
y casing mixto a propósito, y limpia todo lo que crea en setUp/tearDown.
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from datetime import date, timedelta
from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProgramacionInyeccion
from backend.services.programacion_service import ProgramacionService

# Casing mixto a propósito, igual que las máquinas reales ('MAQUINA No. 1').
MAQUINA_MIXTA = "TEST-Maq Claude No. 1"


class TestRetomarMaquinaCaseInsensitive(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(ProgramacionInyeccion).filter(
            db.func.upper(ProgramacionInyeccion.maquina) == MAQUINA_MIXTA.upper()
        ).delete(synchronize_session=False)
        db.session.commit()

    def test_encuentra_historial_de_ayer_pese_a_mayusculas_distintas(self):
        ayer = date.today() - timedelta(days=1)
        hoy_str = date.today().strftime('%Y-%m-%d')

        fila_ayer = ProgramacionInyeccion(
            fecha=ayer, maquina=MAQUINA_MIXTA, codigo_sistema='TEST-9999',
            molde='TESTMOLDERETOMA', cavidades=2, estado='EN_PROCESO',
            op_world_office='OP-TEST-AYER'
        )
        db.session.add(fila_ayer)
        db.session.commit()

        # La ruta HTTP siempre uppercasea el nombre de maquina antes de llamar aqui
        # (ver programacion_routes.mes_retomar) -- se reproduce ese mismo input.
        resultado = ProgramacionService.retomar_programacion(
            maquinas=[MAQUINA_MIXTA.upper()], fecha_str=hoy_str, responsable='TEST ROBOT'
        )

        self.assertEqual(resultado['omitidas'], [], f"no debio omitirse: {resultado['omitidas']}")
        self.assertEqual(len(resultado['retomadas']), 1)

        fila_hoy = db.session.query(ProgramacionInyeccion).filter(
            db.func.upper(ProgramacionInyeccion.maquina) == MAQUINA_MIXTA.upper(),
            ProgramacionInyeccion.fecha == date.today()
        ).first()
        self.assertIsNotNone(fila_hoy, "debio crear la programacion de hoy a partir del historial de ayer")
        self.assertEqual(fila_hoy.codigo_sistema, 'TEST-9999')
        self.assertEqual(fila_hoy.molde, 'TESTMOLDERETOMA')
        # No debio inventar una variante de mayusculas nueva en la DB.
        self.assertEqual(fila_hoy.maquina, MAQUINA_MIXTA,
                          "debio conservar el casing original de la fila historica, no el de 'objetivo' en mayusculas")

    def test_ya_tiene_programacion_hoy_se_detecta_pese_a_mayusculas_distintas(self):
        # Fila YA existente hoy pero guardada con otro casing (minusculas).
        fila_hoy_otra_case = ProgramacionInyeccion(
            fecha=date.today(), maquina=MAQUINA_MIXTA.lower(), codigo_sistema='TEST-7777',
            molde='TESTMOLDERETOMA2', cavidades=1, estado='PROGRAMADO',
            op_world_office='OP-TEST-HOY'
        )
        db.session.add(fila_hoy_otra_case)
        db.session.commit()

        resultado = ProgramacionService.retomar_programacion(
            maquinas=[MAQUINA_MIXTA.upper()],
            fecha_str=date.today().strftime('%Y-%m-%d'),
            responsable='TEST ROBOT'
        )
        self.assertEqual(resultado['retomadas'], [])
        self.assertEqual(len(resultado['omitidas']), 1)
        self.assertIn('Ya tiene programación', resultado['omitidas'][0]['motivo'])

        # No debio crear una fila duplicada para hoy.
        filas_hoy = db.session.query(ProgramacionInyeccion).filter(
            db.func.upper(ProgramacionInyeccion.maquina) == MAQUINA_MIXTA.upper(),
            ProgramacionInyeccion.fecha == date.today()
        ).all()
        self.assertEqual(len(filas_hoy), 1)

    def test_sin_historial_reporta_el_motivo_correcto(self):
        # Caracterización del camino sin datos -- ninguna fila para esta máquina.
        resultado = ProgramacionService.retomar_programacion(
            maquinas=[MAQUINA_MIXTA.upper()],
            fecha_str=date.today().strftime('%Y-%m-%d'),
            responsable='TEST ROBOT'
        )
        self.assertEqual(resultado['retomadas'], [])
        self.assertEqual(len(resultado['omitidas']), 1)
        self.assertEqual(resultado['omitidas'][0]['motivo'], 'Sin historial previo de programación')


if __name__ == '__main__':
    unittest.main()
