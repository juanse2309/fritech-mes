# -*- coding: utf-8 -*-
"""
Tests del fix 2026-09-04: /api/dashboard/stats devolvía 403 para el rol
'PULIDO' (operaria de línea) -- ROL_DASHBOARD_OPERATIVO solo incluía
'JEFE PULIDO' vía ROL_JEFES, no la operaria rasa -- pese a que el menú del
frontend (auth.js: this.permissions['PULIDO'] incluye 'dashboard') ya la
dejaba entrar a "Dashboard IA" y las 3 secciones que ella debe ver (Ranking
Pulido / Mix de Producción / Tabla Pulido, data-role-access="ADMIN,PULIDO"
en index.html) ya estaban listas del lado del frontend. El Dashboard le
quedaba completamente vacío (fetch en 403, sin datos que renderizar).

El fix da acceso Y además evita calcular lo que ella no va a ver (Inyección,
máquinas, tendencia, stock, insights IA) para que la respuesta sea más
rápida -- pedido explícito del usuario: "que no se les cargue todo para que
no se demore". Ver backend/routes/dashboard_routes.py:
_es_operaria_pulido, _stats_cache_key.

Corre contra la base de datos real configurada en DATABASE_URL (mismo patrón
que tests/test_registrar_lote.py) -- solo hace lecturas, no escribe nada.
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.utils.time_utils import get_colombia_time


class TestDashboardStatsScopePorRol(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def _login_como(self, rol):
        with self.client.session_transaction() as sess:
            sess['user'] = 'TEST ROBOT'
            sess['role'] = rol

    def test_pulido_ya_no_recibe_403(self):
        self._login_como('PULIDO')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertTrue(body.get('success'))

    def test_pulido_recibe_las_secciones_pesadas_vacias(self):
        self._login_como('PULIDO')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        data = resp.get_json()['data']
        self.assertEqual(data['analytics_inyeccion'], {}, "Inyeccion no debio calcularse para una operaria de Pulido")
        self.assertEqual(data['maquinas'], [], "Ranking de maquinas no debio calcularse para Pulido")
        self.assertEqual(data['tendencia'], [], "Tendencia no debio calcularse para Pulido")
        self.assertEqual(data['insights_ia'], [], "Insights IA (Inyeccion/stock) no debian generarse para Pulido")
        self.assertEqual(data['rankings']['inyeccion_ops'], [])
        self.assertEqual(data['kpis']['stock_critico'], [])

    def test_pulido_SI_recibe_sus_propias_secciones(self):
        self._login_como('PULIDO')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        data = resp.get_json()['data']
        self.assertIn('pulido_profundo', data['rankings'])
        self.assertIn('pulido_evolucion', data['rankings'])
        self.assertIn('operario_referencia', data['analytics_pulido'])
        self.assertIn('kpis', data)
        self.assertIn('pulido_ok', data['kpis'])
        self.assertIn('scrap_total', data['kpis'])

    def test_pulido_sin_filtro_de_fecha_se_acota_a_hoy(self):
        # Sin esto, la primera carga (inputs de fecha vacíos) barría TODO el
        # histórico de db_pulido -- ~18s medido en vivo. Ver
        # dashboard_routes.obtener_metricas_bi.
        self._login_como('PULIDO')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        data = resp.get_json()['data']
        hoy_str = get_colombia_time().date().strftime('%Y-%m-%d')
        self.assertEqual(data['rango']['desde'], hoy_str)
        self.assertEqual(data['rango']['hasta'], hoy_str)

    def test_admin_sin_filtro_de_fecha_sigue_siendo_historico_completo(self):
        # El acotado a "hoy" es SOLO del camino liviano de Pulido -- el
        # comportamiento de Admin (histórico completo por defecto) no debe
        # cambiar con este fix.
        self._login_como('ADMIN')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        data = resp.get_json()['data']
        self.assertEqual(data['rango']['desde'], 'Inicio')
        self.assertEqual(data['rango']['hasta'], 'Fin')

    def test_admin_sigue_recibiendo_todo(self):
        self._login_como('ADMIN')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()['data']
        # Para admin estas claves deben seguir existiendo (calculadas de
        # verdad, no salteadas) -- pueden venir vacías si no hay datos en el
        # rango, pero eso es distinto de "nunca se calcularon".
        self.assertIn('inyeccion_ops', data['rankings'])
        self.assertIn('maquinas', data)
        self.assertIn('tendencia', data)
        self.assertIn('analytics_inyeccion', data)

    def test_rol_sin_permiso_sigue_bloqueado(self):
        self._login_como('ENSAMBLE')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        self.assertEqual(resp.status_code, 403)

    def test_jefe_pulido_sigue_recibiendo_todo_no_el_camino_liviano(self):
        # 'JEFE PULIDO' ya entraba antes por ROL_JEFES -- confirmar que el
        # nuevo camino liviano NO lo atrapa por error (comparación EXACTA de
        # rol, no por substring 'PULIDO'). No se asume volumen de datos real
        # (podría legítimamente no haber máquinas/tendencia en el rango por
        # defecto) -- solo que siguió el camino de cálculo completo, no 403.
        self._login_como('JEFE PULIDO')
        resp = self.client.get('/api/dashboard/stats?nocache=1')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()['data']
        self.assertIn('inyeccion_ops', data['rankings'])
        self.assertIn('maquinas', data)
        self.assertIn('analytics_inyeccion', data)


if __name__ == '__main__':
    unittest.main()
