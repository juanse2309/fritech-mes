# -*- coding: utf-8 -*-
"""
Tests del fix 2026-09-14: incidente donde 'Liberar Máquina' borró el trabajo
EN_PROCESO de 3 máquinas sin dejar ningún rastro de quién lo hizo ni qué se
perdió (ver CancelacionMesLog en sql_models.py). ProgramacionService.cancelar
ahora copia cada fila a CancelacionMesLog ANTES del DELETE, en la misma
transacción.

Corre contra la base de datos real configurada en DATABASE_URL (mismo patrón
que tests/test_retomar_maquina_case_insensitive.py -- localhost/fritech_local
en este entorno, NUNCA producción). Usa códigos/máquinas con prefijo TEST- y
limpia todo en setUp/tearDown.
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from datetime import date
from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProgramacionInyeccion, ProduccionInyeccion, CancelacionMesLog
from backend.services.programacion_service import ProgramacionService, ProgramacionNoEncontradaException

MAQUINA_TEST = "TEST-Maq Claude Cancelar"
ID_INYECCION_TEST = "INY-TESTCANCELAR01"


class TestCancelarMaquinaAuditLog(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(ProgramacionInyeccion).filter(
            ProgramacionInyeccion.maquina == MAQUINA_TEST
        ).delete(synchronize_session=False)
        db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.maquina == MAQUINA_TEST
        ).delete(synchronize_session=False)
        db.session.query(CancelacionMesLog).filter(
            CancelacionMesLog.maquina == MAQUINA_TEST
        ).delete(synchronize_session=False)
        db.session.commit()

    def test_cancelar_fila_de_cola_deja_log_antes_de_borrar(self):
        fila = ProgramacionInyeccion(
            fecha=date.today(), maquina=MAQUINA_TEST, codigo_sistema="TEST-1111",
            molde="M", cavidades=2, cantidad=50, estado="PROGRAMADO",
            op_world_office="OP-TEST-COLA",
        )
        db.session.add(fila)
        db.session.commit()
        id_fila = fila.id

        ProgramacionService.cancelar(str(id_fila), responsable="TEST ROBOT")

        self.assertIsNone(db.session.get(ProgramacionInyeccion, id_fila), "la fila debio borrarse")

        log = db.session.query(CancelacionMesLog).filter(
            CancelacionMesLog.tabla_origen == "db_programacion",
            CancelacionMesLog.id_original == id_fila,
        ).first()
        self.assertIsNotNone(log, "debio quedar un registro de auditoria antes del borrado")
        self.assertEqual(log.responsable, "TEST ROBOT")
        self.assertEqual(log.maquina, MAQUINA_TEST)
        self.assertEqual(log.id_codigo, "TEST-1111")
        self.assertEqual(float(log.cantidad), 50.0)
        self.assertEqual(log.estado_al_borrar, "PROGRAMADO")

    def test_cancelar_trabajo_activo_multi_sku_loguea_cada_fila(self):
        # Reproduce el caso real del incidente: un montaje EN_PROCESO con
        # varias referencias (mismo id_inyeccion), 'Liberar Máquina' manda el
        # id_inyeccion (string de negocio), no un PK entero.
        filas = [
            ProduccionInyeccion(
                id_inyeccion=ID_INYECCION_TEST, maquina=MAQUINA_TEST,
                id_codigo=f"TEST-ACTIVO-{i}", molde="Ñ", cavidades=1,
                estado="EN_PROCESO", cantidad_real=0,
            ) for i in range(3)
        ]
        db.session.add_all(filas)
        db.session.commit()

        ProgramacionService.cancelar(ID_INYECCION_TEST, responsable="TEST ROBOT")

        restantes = db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.id_inyeccion == ID_INYECCION_TEST
        ).all()
        self.assertEqual(restantes, [], "debieron borrarse TODAS las filas del lote, no solo una")

        logs = db.session.query(CancelacionMesLog).filter(
            CancelacionMesLog.tabla_origen == "db_inyeccion",
            CancelacionMesLog.id_inyeccion == ID_INYECCION_TEST,
        ).all()
        self.assertEqual(len(logs), 3, "debio quedar un log por cada fila del montaje borrado")
        codigos_logueados = {l.id_codigo for l in logs}
        self.assertEqual(codigos_logueados, {"TEST-ACTIVO-0", "TEST-ACTIVO-1", "TEST-ACTIVO-2"})

    def test_id_no_encontrado_no_deja_log_huerfano(self):
        with self.assertRaises(ProgramacionNoEncontradaException):
            ProgramacionService.cancelar("ID-QUE-NO-EXISTE-NUNCA", responsable="TEST ROBOT")

        huerfanos = db.session.query(CancelacionMesLog).filter(
            CancelacionMesLog.id_inyeccion == "ID-QUE-NO-EXISTE-NUNCA"
        ).all()
        self.assertEqual(huerfanos, [], "no debio crear ningun log si no encontro nada que borrar")

    def test_caracteres_especiales_nulos_y_responsable_ausente(self):
        # Datos borde: comillas simples, ñ/acentos, molde/cantidad NULL,
        # responsable None (sin sesion) -- no debe romper el INSERT del log.
        fila = ProgramacionInyeccion(
            fecha=date.today(), maquina=MAQUINA_TEST,
            codigo_sistema="TEST-O'BRIEN-ÑÁ", molde=None, cavidades=1,
            cantidad=None, estado="PROGRAMADO", op_world_office=None,
        )
        db.session.add(fila)
        db.session.commit()
        id_fila = fila.id

        ProgramacionService.cancelar(str(id_fila), responsable=None)

        log = db.session.query(CancelacionMesLog).filter(
            CancelacionMesLog.tabla_origen == "db_programacion",
            CancelacionMesLog.id_original == id_fila,
        ).first()
        self.assertIsNotNone(log)
        self.assertIsNone(log.responsable)
        self.assertIsNone(log.molde)
        # ProgramacionInyeccion.cantidad tiene default=0 en el modelo: pasar
        # None explicito no persiste NULL, cae al default en el flush -- el
        # log debe reflejar fielmente ese mismo valor real (0.00), no None.
        self.assertEqual(float(log.cantidad), 0.0)
        self.assertEqual(log.id_codigo, "TEST-O'BRIEN-ÑÁ")


if __name__ == '__main__':
    unittest.main()
