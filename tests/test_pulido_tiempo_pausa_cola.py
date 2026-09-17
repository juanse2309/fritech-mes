# -*- coding: utf-8 -*-
"""
Tests de PulidoService para el fix del 2026-09-17 (incidente Laura Lizeth
Vargas R., tarea 9672): el tiempo que un registro pasaba en PAUSADO o "en
cola" (PAUSADO_COLA vía swap_task/intercambiar_tarea) nunca se restaba del
tiempo_total_minutos/duracion_segundos final -- contaba como tiempo
trabajado. Cubren:

  1. pausar()/reanudar(): acumulación correcta de tiempo_pausa_acumulado.
  2. intercambiar_tarea(): swap atómico entre tareas, reutilizando
     pausar()/reanudar() (antes era SQL a mano en la ruta que nunca
     acumulaba nada al reactivar).
  3. ejecutar_persistencia_pulido(): descuento de tiempo_pausa_acumulado del
     total final, incluyendo el caso borde de finalizar directo desde
     PAUSADO sin haber pasado por reanudar() antes.

Corre contra la base de datos real (mismo patrón que test_registrar_lote.py),
con datos TEST-PAUSA- limpiados en setUp/tearDown. Usa el sandbox de pruebas
de PulidoService (_es_prueba: 'PRUEBA' en id_pulido) para no tocar inventario
real, y forzar_bloqueo=True para poder usar fechas fijas sin depender del
día real en que corran los tests.
"""
import unittest
from datetime import datetime, timedelta

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProduccionPulido, PulidoOverride
from backend.services.pulido_service import PulidoService

RESPONSABLE_TEST = "TEST PAUSA ROBOT"


class _BasePulidoPausaTest(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(PulidoOverride).filter(
            PulidoOverride.id_pulido.like('TEST-PAUSA-%')
        ).delete(synchronize_session=False)
        db.session.query(ProduccionPulido).filter(
            db.or_(
                ProduccionPulido.id_pulido.like('TEST-PAUSA-%'),
                ProduccionPulido.responsable == RESPONSABLE_TEST,
            )
        ).delete(synchronize_session=False)
        db.session.commit()

    def _crear_registro(self, id_pulido, estado='TRABAJANDO', hora_inicio=None,
                         hora_pausa=None, tiempo_pausa_acumulado=0):
        registro = ProduccionPulido(
            id_pulido=id_pulido,
            responsable=RESPONSABLE_TEST,
            codigo='9672',
            orden_produccion='SIN OP',
            estado=estado,
            fecha_registro=datetime(2026, 1, 15, 6, 0),
            hora_inicio=hora_inicio,
            hora_pausa=hora_pausa,
            tiempo_pausa_acumulado=tiempo_pausa_acumulado,
            cantidad_real=0,
        )
        db.session.add(registro)
        db.session.commit()
        return registro

    def _refrescar(self, id_pulido):
        return ProduccionPulido.query.filter_by(id_pulido=id_pulido).first()


class TestPausarReanudarAcumulan(_BasePulidoPausaTest):
    def test_reanudar_acumula_el_tiempo_que_estuvo_en_pausa(self):
        from backend.utils.time_utils import get_colombia_time
        inicio_pausa = get_colombia_time() - timedelta(minutes=10)
        self._crear_registro('TEST-PAUSA-PRUEBA-001', estado='PAUSADO', hora_pausa=inicio_pausa)

        registro = PulidoService.reanudar('TEST-PAUSA-PRUEBA-001')

        self.assertEqual(registro.estado, 'TRABAJANDO')
        self.assertIsNone(registro.hora_pausa)
        # Tolerancia de unos segundos por el tiempo real de ejecución del test.
        self.assertTrue(590 <= registro.tiempo_pausa_acumulado <= 620,
                         f"esperaba ~600s acumulados, dio {registro.tiempo_pausa_acumulado}")

    def test_reanudar_sobre_tarea_sin_pausa_no_acumula_nada(self):
        self._crear_registro('TEST-PAUSA-PRUEBA-002', estado='PENDIENTE', hora_pausa=None,
                              tiempo_pausa_acumulado=0)
        registro = PulidoService.reanudar('TEST-PAUSA-PRUEBA-002')
        self.assertEqual(registro.estado, 'TRABAJANDO')
        self.assertEqual(registro.tiempo_pausa_acumulado, 0)

    def test_reanudar_id_inexistente_retorna_none(self):
        self.assertIsNone(PulidoService.reanudar('TEST-PAUSA-PRUEBA-NO-EXISTE'))


class TestIntercambiarTarea(_BasePulidoPausaTest):
    def test_pausa_lo_trabajando_y_activa_la_elegida(self):
        self._crear_registro('TEST-PAUSA-PRUEBA-A', estado='TRABAJANDO')
        self._crear_registro('TEST-PAUSA-PRUEBA-B', estado='PENDIENTE')

        resultado = PulidoService.intercambiar_tarea(RESPONSABLE_TEST, 'TEST-PAUSA-PRUEBA-B')

        tarea_a = self._refrescar('TEST-PAUSA-PRUEBA-A')
        tarea_b = self._refrescar('TEST-PAUSA-PRUEBA-B')

        self.assertEqual(tarea_a.estado, 'PAUSADO_COLA')
        self.assertIsNotNone(tarea_a.hora_pausa)
        self.assertEqual(tarea_b.estado, 'TRABAJANDO')
        self.assertIsNone(tarea_b.hora_pausa)
        self.assertEqual(resultado.id_pulido, 'TEST-PAUSA-PRUEBA-B')

    def test_retomar_una_tarea_que_estuvo_horas_en_cola_acumula_ese_tiempo(self):
        from backend.utils.time_utils import get_colombia_time
        hace_2h = get_colombia_time() - timedelta(hours=2)
        self._crear_registro('TEST-PAUSA-PRUEBA-C', estado='TRABAJANDO')
        self._crear_registro(
            'TEST-PAUSA-PRUEBA-D', estado='PAUSADO_COLA',
            hora_pausa=hace_2h, tiempo_pausa_acumulado=0
        )

        PulidoService.intercambiar_tarea(RESPONSABLE_TEST, 'TEST-PAUSA-PRUEBA-D')

        tarea_d = self._refrescar('TEST-PAUSA-PRUEBA-D')
        self.assertEqual(tarea_d.estado, 'TRABAJANDO')
        self.assertIsNone(tarea_d.hora_pausa)
        segundos_esperados = 2 * 3600
        self.assertTrue(
            segundos_esperados - 30 <= tarea_d.tiempo_pausa_acumulado <= segundos_esperados + 30,
            f"esperaba ~{segundos_esperados}s acumulados, dio {tarea_d.tiempo_pausa_acumulado}"
        )

        tarea_c = self._refrescar('TEST-PAUSA-PRUEBA-C')
        self.assertEqual(tarea_c.estado, 'PAUSADO_COLA')

    def test_tarea_a_retomar_inexistente_retorna_none(self):
        self._crear_registro('TEST-PAUSA-PRUEBA-E', estado='TRABAJANDO')
        resultado = PulidoService.intercambiar_tarea(RESPONSABLE_TEST, 'TEST-PAUSA-PRUEBA-NO-EXISTE')
        self.assertIsNone(resultado)
        # La tarea que sí existía y estaba TRABAJANDO igual quedó pausada --
        # comportamiento heredado del swap_task original (pausa primero,
        # activa después); se documenta aquí para no romperlo sin querer.
        tarea_e = self._refrescar('TEST-PAUSA-PRUEBA-E')
        self.assertEqual(tarea_e.estado, 'PAUSADO_COLA')


class TestDescuentoTiempoPausaAlFinalizar(_BasePulidoPausaTest):
    def _payload_finalizar(self, id_pulido, hora_inicio, hora_fin, cantidad_real=10):
        return {
            'id_pulido': id_pulido,
            'fecha_inicio': '2026-01-15',
            'hora_inicio': hora_inicio,
            'hora_fin': hora_fin,
            'codigo_producto': '9672',
            'cantidad_real': cantidad_real,
            'cantidad_recibida': cantidad_real,
            'orden_produccion': 'SIN OP',
            'lote': 'TEST-PAUSA-LOTE',
        }

    def test_sin_pausas_no_descuenta_nada(self):
        registro = self._crear_registro(
            'TEST-PAUSA-PRUEBA-FIN-001', estado='TRABAJANDO', tiempo_pausa_acumulado=0
        )
        data = self._payload_finalizar('TEST-PAUSA-PRUEBA-FIN-001', '06:00', '08:00')
        ahora = datetime(2026, 1, 15, 8, 0)

        PulidoService.ejecutar_persistencia_pulido(
            registro, data, RESPONSABLE_TEST, ahora, forzar_bloqueo=True, motivo_forzado='test'
        )

        self.assertEqual(registro.duracion_segundos, 2 * 3600)

    def test_resta_el_tiempo_pausa_acumulado_del_total(self):
        registro = self._crear_registro(
            'TEST-PAUSA-PRUEBA-FIN-002', estado='TRABAJANDO', tiempo_pausa_acumulado=3600
        )
        data = self._payload_finalizar('TEST-PAUSA-PRUEBA-FIN-002', '06:00', '08:00')
        ahora = datetime(2026, 1, 15, 8, 0)

        PulidoService.ejecutar_persistencia_pulido(
            registro, data, RESPONSABLE_TEST, ahora, forzar_bloqueo=True, motivo_forzado='test'
        )

        # Bruto 2h (7200s) - 1h (3600s) ya acumulada de pausas/cola = 1h neta.
        self.assertEqual(registro.duracion_segundos, 3600)

    def test_cierra_una_pausa_abierta_al_finalizar_directo_desde_pausado(self):
        # Nunca se llamó reanudar(): finalizar directo desde PAUSADO debe
        # cerrar esa pausa abierta sola (sumarla a tiempo_pausa_acumulado)
        # antes de calcular el total, sin importar el camino frontend.
        registro = self._crear_registro(
            'TEST-PAUSA-PRUEBA-FIN-003', estado='PAUSADO',
            hora_pausa=datetime(2026, 1, 15, 7, 30), tiempo_pausa_acumulado=0
        )
        data = self._payload_finalizar('TEST-PAUSA-PRUEBA-FIN-003', '06:00', '08:00')
        ahora = datetime(2026, 1, 15, 8, 0)

        PulidoService.ejecutar_persistencia_pulido(
            registro, data, RESPONSABLE_TEST, ahora, forzar_bloqueo=True, motivo_forzado='test'
        )

        # Pausa abierta de 7:30 a 8:00 (30 min = 1800s) se cierra y se resta:
        # bruto 2h (7200s) - 1800s = 5400s.
        self.assertEqual(registro.tiempo_pausa_acumulado, 1800)
        self.assertIsNone(registro.hora_pausa)
        self.assertEqual(registro.duracion_segundos, 7200 - 1800)

    def test_pausa_acumulada_mayor_al_bruto_no_deja_duracion_negativa(self):
        registro = self._crear_registro(
            'TEST-PAUSA-PRUEBA-FIN-004', estado='TRABAJANDO', tiempo_pausa_acumulado=99999
        )
        data = self._payload_finalizar('TEST-PAUSA-PRUEBA-FIN-004', '06:00', '08:00')
        ahora = datetime(2026, 1, 15, 8, 0)

        PulidoService.ejecutar_persistencia_pulido(
            registro, data, RESPONSABLE_TEST, ahora, forzar_bloqueo=True, motivo_forzado='test'
        )

        self.assertEqual(registro.duracion_segundos, 0)
        self.assertEqual(registro.segundos_por_unidad, 0.0)

    def test_pausa_intermedia_en_checkpoint_no_calcula_duracion_ni_toca_hora_pausa(self):
        # Checkpoint de "enviar a cola" (estado PAUSADO_COLA, sin hora_fin):
        # no debe calcular duracion_segundos ni tocar hora_pausa -- todavía
        # no se está cerrando el ciclo.
        momento_pausa = datetime(2026, 1, 15, 7, 45)
        registro = self._crear_registro(
            'TEST-PAUSA-PRUEBA-FIN-005', estado='PAUSADO_COLA',
            hora_pausa=momento_pausa, tiempo_pausa_acumulado=0
        )
        data = {
            'id_pulido': 'TEST-PAUSA-PRUEBA-FIN-005',
            'fecha_inicio': '2026-01-15',
            'hora_inicio': '06:00',
            'codigo_producto': '9672',
            'cantidad_real': 0,
            'orden_produccion': 'SIN OP',
            'lote': 'TEST-PAUSA-LOTE',
            'estado': 'PAUSADO_COLA',
        }
        ahora = datetime(2026, 1, 15, 8, 0)

        PulidoService.ejecutar_persistencia_pulido(
            registro, data, RESPONSABLE_TEST, ahora, forzar_bloqueo=True, motivo_forzado='test'
        )

        self.assertEqual(registro.duracion_segundos, 0)
        self.assertEqual(registro.hora_pausa, momento_pausa, "un checkpoint PAUSADO_COLA no debe cerrar su propia pausa")
        self.assertEqual(registro.tiempo_pausa_acumulado, 0)


if __name__ == '__main__':
    unittest.main()
