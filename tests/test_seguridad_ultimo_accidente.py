# -*- coding: utf-8 -*-
"""
Tests del contador de días sin accidentes (Modo TV, 2026-09-24): schema,
servicio y permisos de las rutas reales (test_client con sesión Flask).

Corren contra la base de DATABASE_URL: se guarda el valor previo de la clave
'seguridad.ultimo_accidente' en AppConfig y se restaura al terminar (setUp/
tearDown), y se borran los db_logs de módulo SEGURIDAD que generen los tests.

Datos borde: sin dato, fecha hoy (0 días), fecha futura, fecha absurdamente
antigua, formatos inválidos (vacío, timestamp entero, datetime completo,
texto libre), valor corrupto guardado en la BD, y roles (ADMIN puede editar,
JEFE PULIDO / INYECCION solo leen, PULIDO y anónimo no entran).
"""
import os
import unittest
from datetime import timedelta

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from pydantic import ValidationError

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import AppConfig, OperacionLog
from backend.schemas.seguridad_schemas import UltimoAccidenteSchema
from backend.services.seguridad_service import CLAVE_ULTIMO_ACCIDENTE, SeguridadService
from backend.utils.time_utils import get_colombia_time

URL = '/api/seguridad/ultimo_accidente'


class TestSchema(unittest.TestCase):

    def test_acepta_fecha_iso(self):
        self.assertEqual(UltimoAccidenteSchema(fecha='2026-09-16').fecha.isoformat(), '2026-09-16')
        self.assertEqual(UltimoAccidenteSchema(fecha=' 2026-09-16 ').fecha.isoformat(), '2026-09-16')

    def test_rechaza_formatos_que_pydantic_convertiria_en_silencio(self):
        for malo in ('', '   ', 'ayer', '16/09/2026', '2026-9-16', '2026-09-16T10:00:00',
                     1789000000, 20260916, None, ['2026-09-16'], '2026-13-45'):
            with self.subTest(valor=malo):
                with self.assertRaises(ValidationError):
                    UltimoAccidenteSchema(fecha=malo)

    def test_rechaza_ano_absurdo(self):
        with self.assertRaises(ValidationError):
            UltimoAccidenteSchema(fecha='0202-09-16')
        with self.assertRaises(ValidationError):
            UltimoAccidenteSchema(fecha='1926-09-16')


class _Base(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        fila = db.session.get(AppConfig, CLAVE_ULTIMO_ACCIDENTE)
        self._existia = fila is not None
        self._valor_previo = fila.valor if fila else None
        self.hoy = get_colombia_time().date()
        self._limpiar_logs()

    def tearDown(self):
        db.session.rollback()
        fila = db.session.get(AppConfig, CLAVE_ULTIMO_ACCIDENTE)
        if self._existia:
            if fila:
                fila.valor = self._valor_previo
            else:
                db.session.add(AppConfig(clave=CLAVE_ULTIMO_ACCIDENTE, valor=self._valor_previo))
        elif fila:
            db.session.delete(fila)
        db.session.commit()
        self._limpiar_logs()
        self.ctx.pop()

    def _limpiar_logs(self):
        OperacionLog.query.filter(OperacionLog.modulo == 'SEGURIDAD',
                                  OperacionLog.operario.like('TEST-SEG%')).delete(synchronize_session=False)
        db.session.commit()

    def _borrar_config(self):
        fila = db.session.get(AppConfig, CLAVE_ULTIMO_ACCIDENTE)
        if fila:
            db.session.delete(fila)
            db.session.commit()

    def _poner_config(self, valor):
        fila = db.session.get(AppConfig, CLAVE_ULTIMO_ACCIDENTE)
        if fila:
            fila.valor = valor
        else:
            db.session.add(AppConfig(clave=CLAVE_ULTIMO_ACCIDENTE, valor=valor))
        db.session.commit()


class TestServicio(_Base):

    def test_sin_dato_no_inventa_cero(self):
        self._borrar_config()
        self.assertEqual(SeguridadService.obtener_ultimo_accidente(), {'fecha': None, 'dias': None})

    def test_cuenta_dias_desde_la_fecha(self):
        f = self.hoy - timedelta(days=8)
        SeguridadService.registrar_ultimo_accidente(f, 'TEST-SEG admin')
        self.assertEqual(SeguridadService.obtener_ultimo_accidente(), {'fecha': f.isoformat(), 'dias': 8})

    def test_accidente_hoy_son_cero_dias(self):
        r = SeguridadService.registrar_ultimo_accidente(self.hoy, 'TEST-SEG admin')
        self.assertEqual(r['dias'], 0)

    def test_fecha_futura_se_rechaza_y_no_cambia_nada(self):
        self._poner_config((self.hoy - timedelta(days=3)).isoformat())
        with self.assertRaises(ValueError):
            SeguridadService.registrar_ultimo_accidente(self.hoy + timedelta(days=1), 'TEST-SEG admin')
        self.assertEqual(SeguridadService.obtener_ultimo_accidente()['dias'], 3)

    def test_valor_corrupto_o_futuro_en_bd_se_trata_como_sin_dato(self):
        for valor in ('no-es-fecha', '', '2999-01-01', '2026-99-99'):
            with self.subTest(valor=valor):
                self._poner_config(valor)
                self.assertEqual(SeguridadService.obtener_ultimo_accidente(), {'fecha': None, 'dias': None})

    def test_cada_cambio_deja_rastro_en_db_logs(self):
        self._borrar_config()
        SeguridadService.registrar_ultimo_accidente(self.hoy - timedelta(days=5), 'TEST-SEG uno')
        SeguridadService.registrar_ultimo_accidente(self.hoy - timedelta(days=1), 'TEST-SEG dos')

        logs = OperacionLog.query.filter(OperacionLog.modulo == 'SEGURIDAD',
                                         OperacionLog.operario.like('TEST-SEG%')).order_by(OperacionLog.id).all()

        self.assertEqual([l.operario for l in logs], ['TEST-SEG uno', 'TEST-SEG dos'])
        self.assertTrue(logs[0].detalles.startswith('sin dato -> '))
        self.assertIn((self.hoy - timedelta(days=5)).isoformat() + ' -> ', logs[1].detalles)


class TestRutas(_Base):

    def _cliente(self, rol, usuario='TEST-SEG user'):
        c = app.test_client()
        if rol:
            with c.session_transaction() as s:
                s['user'] = usuario
                s['role'] = rol
        return c

    def test_anonimo_no_entra(self):
        c = self._cliente(None)
        self.assertEqual(c.get(URL).status_code, 401)
        self.assertEqual(c.post(URL, json={'fecha': '2026-09-16'}).status_code, 401)

    def test_admin_lee_y_edita(self):
        c = self._cliente('ADMIN')
        f = (self.hoy - timedelta(days=8)).isoformat()

        r = c.post(URL, json={'fecha': f})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['data'], {'fecha': f, 'dias': 8})

        r = c.get(URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['data'], {'fecha': f, 'dias': 8, 'puede_editar': True})

    def test_roles_de_solo_lectura_ven_pero_no_editan(self):
        for rol in ('JEFE PULIDO', 'JEFE INYECCION', 'INYECCION'):
            with self.subTest(rol=rol):
                c = self._cliente(rol)
                r = c.get(URL)
                self.assertEqual(r.status_code, 200)
                self.assertFalse(r.get_json()['data']['puede_editar'])
                self.assertEqual(c.post(URL, json={'fecha': self.hoy.isoformat()}).status_code, 403)

    def test_rol_sin_modo_tv_no_entra(self):
        c = self._cliente('PULIDO')
        self.assertEqual(c.get(URL).status_code, 403)
        self.assertEqual(c.post(URL, json={'fecha': self.hoy.isoformat()}).status_code, 403)

    def test_payloads_invalidos_dan_400_y_no_tocan_la_bd(self):
        self._poner_config((self.hoy - timedelta(days=2)).isoformat())
        c = self._cliente('ADMIN')
        futura = (self.hoy + timedelta(days=1)).isoformat()

        for cuerpo in ({}, {'fecha': ''}, {'fecha': 'ayer'}, {'fecha': 1789000000},
                       {'fecha': '2026-09-16T10:00:00'}, {'fecha': futura}, {'otra': 'cosa'}):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(c.post(URL, json=cuerpo).status_code, 400)

        # sin body / body no JSON
        self.assertEqual(c.post(URL, data='texto', content_type='text/plain').status_code, 400)
        self.assertEqual(SeguridadService.obtener_ultimo_accidente()['dias'], 2)

    def test_el_log_guarda_quien_lo_hizo(self):
        self._borrar_config()
        c = self._cliente('ADMIN', usuario='TEST-SEG Juan')
        c.post(URL, json={'fecha': (self.hoy - timedelta(days=4)).isoformat()})

        log = OperacionLog.query.filter(OperacionLog.modulo == 'SEGURIDAD',
                                        OperacionLog.operario == 'TEST-SEG Juan').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.accion, 'ULTIMO_ACCIDENTE')


if __name__ == "__main__":
    unittest.main()
