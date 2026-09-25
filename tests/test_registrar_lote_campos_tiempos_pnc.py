# -*- coding: utf-8 -*-
"""
Tests de caracterización de InyeccionService.registrar_lote, escritos ANTES de
descomponer el cuerpo del bloque `try` (backend/services/inyeccion_service.py,
~línea 421-593) en helpers separados -- hallazgo p5 de la auditoría, la parte
que quedó pendiente después de que p6 extrajera la resolución de `registro`
a queries batch.

Estos tests cubren los tres bloques que p6 NO tocó y que p5 sí va a mover a
métodos propios:
  1. Sincronización de campos (item/turno -> atributos de ProduccionInyeccion).
  2. Cálculo de tiempos: duración (sin descuento de pausas desde 2026-09-25:
     las máquinas de inyección no descansan) y el guard de duración
     imposible (TurnoInvalidoException).
  3. Lógica de PNC por item (detallado vs. fallback a pnc_total crudo) y PNC
     "huérfanas" (código del pnc_list que no pertenece a ningún item del lote).

Corre contra la base de datos real (mismo patrón que test_registrar_lote.py),
con datos TEST-AUDIT- limpiados en setUp/tearDown.
"""
import unittest
import os
from datetime import datetime

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProduccionInyeccion, PncInyeccion
from backend.services.inyeccion_service import InyeccionService
from backend.services.audit_service import TurnoInvalidoException

RESPONSABLE_TEST = "TEST AUDIT ROBOT"
MAQUINA_TEST = "TEST-MAQ-AUDIT-CAMPOS"


class _BaseRegistrarLoteTest(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(PncInyeccion).filter(
            PncInyeccion.id_inyeccion.like('TEST-AUDIT-CAMPOS-%')
        ).delete(synchronize_session=False)
        db.session.query(ProduccionInyeccion).filter(
            db.or_(
                ProduccionInyeccion.id_inyeccion.like('TEST-AUDIT-CAMPOS-%'),
                ProduccionInyeccion.maquina == MAQUINA_TEST,
            )
        ).delete(synchronize_session=False)
        db.session.commit()

    def _payload(self, turno=None, items=None, pnc_list=None):
        base_turno = {
            "fecha_inicio": "2026-01-15",
            "maquina": MAQUINA_TEST,
            "responsable": RESPONSABLE_TEST,
            "id_inyeccion": "TEST-AUDIT-CAMPOS-001",
        }
        if turno:
            base_turno.update(turno)
        base_items = items if items is not None else [{
            "codigo_producto": "8888001",
            "cantidad_real": 10,
            "no_cavidades": 2,
        }]
        data = {"turno": base_turno, "items": base_items}
        if pnc_list is not None:
            data["pnc_list"] = pnc_list
        return data

    def _fila(self, id_inyeccion="TEST-AUDIT-CAMPOS-001", id_codigo=None):
        q = db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=id_inyeccion)
        if id_codigo:
            q = q.filter_by(id_codigo=id_codigo)
        return q.first()


class TestSincronizacionCampos(_BaseRegistrarLoteTest):
    """Caracteriza qué campos de `item`/`turno` terminan en qué atributos de ProduccionInyeccion."""

    def test_campos_basicos_del_item_se_persisten(self):
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 12,
            "no_cavidades": 4,
            "molde": "5002A",
            "cant_contador": 100,
            "peso_bujes": 2.5,
            "observaciones": "obs de prueba",
            "hora_llegada": "05:50",
            "almacen_destino": "PULIDO",
            "orden_produccion": "OP-TEST-9",
        }])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)

        fila = self._fila()
        self.assertIsNotNone(fila)
        self.assertEqual(fila.cantidad_real, 12)
        self.assertEqual(fila.cavidades, 4)
        # molde es texto (VARCHAR), no numerico: codigos reales de catalogo como
        # '5002A' -- ver inyeccion_service.py (str(item.get('molde'))).
        self.assertEqual(fila.molde, "5002A")
        self.assertEqual(fila.cant_contador, 100)
        self.assertEqual(fila.peso_bujes, 2.5)
        self.assertEqual(float(fila.peso_lote), round(12 * 2.5, 4))  # columna Numeric(18,4): vuelve como Decimal, no str
        self.assertEqual(fila.observaciones, "obs de prueba")
        self.assertEqual(fila.hora_llegada, "05:50")
        self.assertEqual(fila.almacen_destino, "PULIDO")
        self.assertEqual(fila.orden_produccion, "OP-TEST-9")
        self.assertEqual(fila.estado, "PENDIENTE")
        self.assertEqual(fila.departamento, "Inyeccion")
        self.assertEqual(fila.finalizado_por, RESPONSABLE_TEST)

    def test_almacen_destino_cae_al_del_turno_si_el_item_no_lo_trae(self):
        data = self._payload(
            turno={"almacen_destino": "ENSAMBLE"},
            items=[{"codigo_producto": "8888001", "cantidad_real": 5, "no_cavidades": 1}]
        )
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)
        fila = self._fila()
        self.assertEqual(fila.almacen_destino, "ENSAMBLE")

    def test_almacen_destino_default_por_pulir_si_nadie_lo_trae(self):
        data = self._payload(items=[{"codigo_producto": "8888001", "cantidad_real": 5, "no_cavidades": 1}])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)
        fila = self._fila()
        self.assertEqual(fila.almacen_destino, "POR PULIR")

    def test_produccion_teorica_se_calcula_de_disparos_por_cavidades_si_no_viene_explicita(self):
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 20,
            "no_cavidades": 3,
            "cant_contador": 8,
        }])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)
        fila = self._fila()
        self.assertEqual(fila.produccion_teorica, 24.0)  # 8 disparos * 3 cavidades


class TestCalculoTiemposYPausas(_BaseRegistrarLoteTest):
    """Caracteriza duración neta, descuento de pausas y el guard de duración imposible."""

    def test_duracion_sin_solape_de_pausas_no_descuenta_nada(self):
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 10,
            "no_cavidades": 1,
            "hora_inicio": "06:00",
            "hora_fin": "08:00",
        }])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)
        fila = self._fila()
        self.assertEqual(fila.duracion_segundos, 2 * 3600)
        self.assertNotIn("AUTO_BREAK", fila.observaciones or "")

    def test_duracion_con_solape_de_desayuno_NO_descuenta_las_maquinas_no_descansan(self):
        # 08:50 -> 09:30 cruza la ventana de desayuno 09:00-09:20 completa (20 min).
        # Desde 2026-09-25 Inyección no descuenta pausas (las máquinas siguen
        # trabajando): la duración es el bruto y no se marca [AUTO_BREAK].
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 10,
            "no_cavidades": 1,
            "hora_inicio": "08:50",
            "hora_fin": "09:30",
        }])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)
        fila = self._fila()
        self.assertEqual(fila.duracion_segundos, 40 * 60)
        self.assertNotIn("AUTO_BREAK", fila.observaciones or "")

    def test_turno_completo_con_desayuno_y_almuerzo_no_descuenta_nada(self):
        # 07:00 -> 16:00 cruza desayuno (20) y almuerzo (40): antes restaba 60 min.
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 10,
            "no_cavidades": 1,
            "hora_inicio": "07:00",
            "hora_fin": "16:00",
        }])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)
        fila = self._fila()
        self.assertEqual(fila.duracion_segundos, 9 * 3600)
        self.assertNotIn("AUTO_BREAK", fila.observaciones or "")

    def test_duracion_mayor_a_12h_lanza_turno_invalido_y_no_persiste_nada(self):
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 10,
            "no_cavidades": 1,
            "hora_inicio": "06:00",
            "hora_fin": "19:00",  # 13 horas > limite de 12h
        }])
        with self.assertRaises(TurnoInvalidoException):
            InyeccionService.registrar_lote(data, RESPONSABLE_TEST)

        fila = self._fila()
        self.assertIsNone(fila, "el rollback del except general no debio dejar la fila a medio crear")

    def test_sin_horas_no_calcula_duracion_pero_si_persiste_el_resto(self):
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 10,
            "no_cavidades": 1,
        }])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)
        fila = self._fila()
        self.assertIsNotNone(fila)
        # duracion_segundos es Integer con default=0 en el modelo: sin horas para
        # calcular, el bloque de tiempos ni siquiera se ejecuta y la columna se
        # queda en el default de SQLAlchemy, no en NULL.
        self.assertEqual(fila.duracion_segundos, 0)
        self.assertEqual(fila.cantidad_real, 10)


class TestLogicaPnc(_BaseRegistrarLoteTest):
    """Caracteriza la clasificación de PNC por item, el fallback a pnc_total crudo y las PNC huérfanas."""

    def test_pnc_detallado_prevalece_sobre_pnc_total_crudo(self):
        data = self._payload(
            items=[{
                "codigo_producto": "8888001",
                "cantidad_real": 10,
                "no_cavidades": 1,
                "pnc_total": 3,  # deberia ser IGNORADO porque hay detalle
            }],
            pnc_list=[
                {"codigo": "8888001", "cantidad": 5, "criterio": "rebaba excesiva"},
                {"codigo": "8888001", "cantidad": 2, "criterio": "quemado"},
            ]
        )
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)

        fila = self._fila()
        self.assertEqual(fila.pnc_total, 7)  # 5 + 2, no el pnc_total=3 del item

        pncs = db.session.query(PncInyeccion).filter_by(
            id_inyeccion="TEST-AUDIT-CAMPOS-001"
        ).all()
        self.assertEqual(len(pncs), 1)
        self.assertEqual(pncs[0].rebaba_excesiva, 5.0)
        self.assertEqual(pncs[0].quemado_manchado, 2.0)

    def test_sin_pnc_detallado_usa_fallback_pnc_total_como_deformacion(self):
        data = self._payload(items=[{
            "codigo_producto": "8888001",
            "cantidad_real": 10,
            "no_cavidades": 1,
            "pnc_total": 4,
        }])
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)

        fila = self._fila()
        self.assertEqual(fila.pnc_total, 4)

        pncs = db.session.query(PncInyeccion).filter_by(
            id_inyeccion="TEST-AUDIT-CAMPOS-001"
        ).all()
        self.assertEqual(len(pncs), 1)
        self.assertEqual(pncs[0].deformacion_rechupado, 4.0)

    def test_reenvio_del_mismo_item_reemplaza_la_fila_pnc_previa_no_la_duplica(self):
        data1 = self._payload(
            items=[{"codigo_producto": "8888001", "cantidad_real": 10, "no_cavidades": 1}],
            pnc_list=[{"codigo": "8888001", "cantidad": 5, "criterio": "rebaba"}]
        )
        InyeccionService.registrar_lote(data1, RESPONSABLE_TEST)

        data2 = self._payload(
            items=[{"codigo_producto": "8888001", "cantidad_real": 10, "no_cavidades": 1}],
            pnc_list=[{"codigo": "8888001", "cantidad": 9, "criterio": "rebaba"}]
        )
        InyeccionService.registrar_lote(data2, RESPONSABLE_TEST)

        pncs = db.session.query(PncInyeccion).filter_by(
            id_inyeccion="TEST-AUDIT-CAMPOS-001"
        ).all()
        self.assertEqual(len(pncs), 1, "el reenvio debio reemplazar la fila PNC anterior, no acumular una segunda")
        self.assertEqual(pncs[0].rebaba_excesiva, 9.0)

    def test_pnc_huerfana_con_codigo_fuera_del_lote_se_guarda_bajo_id_inyeccion_del_lote(self):
        data = self._payload(
            items=[{"codigo_producto": "8888001", "cantidad_real": 10, "no_cavidades": 1}],
            pnc_list=[
                {"codigo": "8888001", "cantidad": 5, "criterio": "rebaba"},
                {"codigo": "8888999-HUERFANO", "cantidad": 3, "criterio": "quemado"},
            ]
        )
        InyeccionService.registrar_lote(data, RESPONSABLE_TEST)

        pnc_item = db.session.query(PncInyeccion).filter_by(
            id_inyeccion="TEST-AUDIT-CAMPOS-001", id_codigo="8888001"
        ).first()
        self.assertIsNotNone(pnc_item)
        self.assertEqual(pnc_item.rebaba_excesiva, 5.0)

        pnc_huerfano = db.session.query(PncInyeccion).filter_by(
            id_inyeccion="TEST-AUDIT-CAMPOS-001", id_codigo="8888999-HUERFANO"
        ).first()
        self.assertIsNotNone(pnc_huerfano, "la PNC huerfana debio guardarse bajo el id_inyeccion del lote (turno)")
        self.assertEqual(pnc_huerfano.quemado_manchado, 3.0)


if __name__ == '__main__':
    unittest.main()
