# -*- coding: utf-8 -*-
"""
Tests de PulidoService.detectar_y_notificar_cambio_lider (feature 2026-09-04):
al guardar un reporte de Pulido, si el líder del día en el Mix de Producción
cambia de una operaria a otra, se avisa por push al departamento PULIDO.
No debe notificar en el primer líder del día (solo siembra el estado en
AppConfig) ni cuando el líder se mantiene igual entre reportes.

PulidoService.get_ranking_leaderboard se mockea a propósito: es una consulta
global sobre TODO db_pulido (sin filtrar por operarias de prueba), así que
usar datos reales de prueba ahí competiría contra el líder real de producción
del día y el test sería no determinista. Se mockea también
NotificationService.enviar_notificacion_por_departamento (no depende de
VAPID_PRIVATE_KEY ni de suscripciones reales). El único acceso real es a
AppConfig (clave 'pulido.lider_notificado_hoy', nueva, sin uso en producción
todavía), contra la base configurada en DATABASE_URL -- igual que el resto de
tests del repo -- y se limpia en setUp/tearDown.
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from unittest.mock import patch
from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import AppConfig
from backend.services.pulido_service import PulidoService

OPERARIA_A = "TEST-OPERARIA A"
OPERARIA_B = "TEST-OPERARIA B"


def _leaderboard(nombre, buenas):
    return {nombre: {"buenas": buenas, "piezas_producidas": buenas, "pnc": 0,
                      "eficiencia": None, "yield_calidad": 100, "minutos": 0, "insight": ""}}


class TestPulidoLiderNotificacion(unittest.TestCase):

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        fila = db.session.get(AppConfig, PulidoService._LIDER_CONFIG_KEY)
        if fila:
            db.session.delete(fila)
        db.session.commit()

    @patch('backend.services.notification_service.NotificationService.enviar_notificacion_por_departamento')
    @patch('backend.services.pulido_service.PulidoService.get_ranking_leaderboard')
    def test_primer_lider_del_dia_no_notifica(self, mock_ranking, mock_push):
        mock_ranking.return_value = _leaderboard(OPERARIA_A, 10)

        PulidoService.detectar_y_notificar_cambio_lider()

        mock_push.assert_not_called()
        fila = db.session.get(AppConfig, PulidoService._LIDER_CONFIG_KEY)
        self.assertIsNotNone(fila)
        self.assertTrue(fila.valor.endswith(f"|{OPERARIA_A}"))

    @patch('backend.services.notification_service.NotificationService.enviar_notificacion_por_departamento')
    @patch('backend.services.pulido_service.PulidoService.get_ranking_leaderboard')
    def test_cambio_de_lider_notifica_una_vez(self, mock_ranking, mock_push):
        mock_ranking.return_value = _leaderboard(OPERARIA_A, 10)
        PulidoService.detectar_y_notificar_cambio_lider()
        mock_push.assert_not_called()

        # OPERARIA_B supera a OPERARIA_A -> debe notificar el sobrepaso.
        mock_ranking.return_value = _leaderboard(OPERARIA_B, 50)
        PulidoService.detectar_y_notificar_cambio_lider()

        mock_push.assert_called_once()
        args, _ = mock_push.call_args
        departamentos, titulo, cuerpo = args[0], args[1], args[2]
        self.assertIn('PULIDO', departamentos)
        self.assertIn(OPERARIA_B, cuerpo)
        self.assertIn(OPERARIA_A, cuerpo)

        fila = db.session.get(AppConfig, PulidoService._LIDER_CONFIG_KEY)
        self.assertTrue(fila.valor.endswith(f"|{OPERARIA_B}"))

    @patch('backend.services.notification_service.NotificationService.enviar_notificacion_por_departamento')
    @patch('backend.services.pulido_service.PulidoService.get_ranking_leaderboard')
    def test_mismo_lider_no_vuelve_a_notificar(self, mock_ranking, mock_push):
        mock_ranking.return_value = _leaderboard(OPERARIA_A, 10)
        PulidoService.detectar_y_notificar_cambio_lider()
        mock_push.assert_not_called()

        # Otro reporte de la misma operaria: sigue liderando, no hay "cambio".
        mock_ranking.return_value = _leaderboard(OPERARIA_A, 15)
        PulidoService.detectar_y_notificar_cambio_lider()

        mock_push.assert_not_called()

    @patch('backend.services.pulido_service.PulidoService.get_ranking_leaderboard')
    def test_appconfig_se_persiste_antes_del_intento_de_push(self, mock_ranking):
        """
        El método NO atrapa fallos de NotificationService (eso lo hace el
        llamador en pulido_routes._ejecutar_persistencia_pulido, envolviendo
        la llamada en try/except para no tumbar el guardado del reporte).
        Lo que sí garantiza el método: el commit del dedupe en AppConfig pasa
        ANTES del intento de push, así un fallo de push (ej. VAPID no
        configurada) no deja el dedupe desincronizado del líder real.
        """
        mock_ranking.return_value = _leaderboard(OPERARIA_A, 10)
        PulidoService.detectar_y_notificar_cambio_lider()

        mock_ranking.return_value = _leaderboard(OPERARIA_B, 50)
        with patch(
            'backend.services.notification_service.NotificationService.enviar_notificacion_por_departamento',
            side_effect=RuntimeError("VAPID no configurada")
        ):
            with self.assertRaises(RuntimeError):
                PulidoService.detectar_y_notificar_cambio_lider()

        fila = db.session.get(AppConfig, PulidoService._LIDER_CONFIG_KEY)
        self.assertTrue(fila.valor.endswith(f"|{OPERARIA_B}"))


if __name__ == '__main__':
    unittest.main()
