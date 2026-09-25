# -*- coding: utf-8 -*-
"""
Tests 2026-09-25:
  1. Validación guarda el 'Peso (kg)' de la tabla (peso_bujes) y recalcula
     peso_lote; con peso=None no lo toca (mismo contrato que disparos/cavidades).
  2. Inyección ya NO descuenta pausas programadas (las máquinas no descansan):
     reportar_trabajo deja la duración bruta y no escribe [AUTO_BREAK].
  3. WoExportService._asignar_porcentajes solo reparte por peso si TODAS las
     líneas tienen peso (con peso parcial las demás quedarían en 0 %).
  4. PDFGenerator._limpiar_observaciones quita el tag interno y escapa marcado.

Corre contra la base configurada en DATABASE_URL (fritech_local, NUNCA
producción). Datos con prefijo TEST- limpiados en setUp/tearDown.
"""
import os
import unittest

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ProduccionInyeccion, PncInyeccion, PncPulido
from backend.services.inyeccion_service import InyeccionService
from backend.services.wo_export_service import WoExportService
from backend.utils.report_service import PDFGenerator

MAQUINA_TEST = "TEST-Maq Peso"
ID_INY = "INY-TESTPESO"
CODIGO = "TEST-9101"


class _Base(unittest.TestCase):
    def setUp(self):
        self.ctx = app.test_request_context()
        self.ctx.push()
        self._limpiar()

    def tearDown(self):
        db.session.rollback()
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.rollback()
        db.session.query(PncInyeccion).filter(PncInyeccion.id_inyeccion == ID_INY).delete(synchronize_session=False)
        db.session.query(PncPulido).filter(PncPulido.id_pulido == ID_INY).delete(synchronize_session=False)
        db.session.query(ProduccionInyeccion).filter(
            ProduccionInyeccion.maquina == MAQUINA_TEST
        ).delete(synchronize_session=False)
        db.session.commit()

    def _lote_pendiente(self, cantidad=330, peso_bujes=0):
        db.session.add(ProduccionInyeccion(
            id_inyeccion=ID_INY, id_codigo=CODIGO, maquina=MAQUINA_TEST, cavidades=6,
            cant_contador=cantidad, cantidad_real=cantidad, produccion_teorica=cantidad,
            peso_bujes=peso_bujes, estado='PENDIENTE', orden_produccion="OP-TESTPESO",
            hora_inicio='07:00', hora_termina='16:00', responsable='Operario Test',
        ))
        db.session.commit()

    def _fila(self):
        db.session.expire_all()
        return db.session.query(ProduccionInyeccion).filter_by(id_inyeccion=ID_INY).first()

    def _validar(self, **item):
        base = {'codigo': CODIGO, 'pnc_inyeccion': 0, 'pnc_pulido': 0, 'pnc_list': [],
                'pnc_pulido_list': [], 'disparos': None, 'no_cavidades': None, 'buenas': 330}
        base.update(item)
        return InyeccionService.validar_lote(ID_INY, {'items': [base]}, 'Zoe Test')


class TestValidacionGuardaPeso(_Base):
    def test_peso_del_payload_se_guarda_y_calcula_peso_lote(self):
        self._lote_pendiente()
        self._validar(peso_bujes=0.0125, buenas=300)
        f = self._fila()
        self.assertEqual(f.estado, 'CERRADO')
        self.assertAlmostEqual(float(f.peso_bujes), 0.0125, places=4)
        self.assertAlmostEqual(float(f.peso_lote), 300 * 0.0125, places=3)  # neta x peso

    def test_peso_none_no_toca_el_peso_guardado_pero_recalcula_peso_lote_con_la_neta(self):
        self._lote_pendiente(peso_bujes=0.02)
        self._validar(peso_bujes=None, buenas=300)
        f = self._fila()
        self.assertAlmostEqual(float(f.peso_bujes), 0.02, places=4)
        self.assertAlmostEqual(float(f.peso_lote), 300 * 0.02, places=3)

    def test_sin_peso_en_ningun_lado_peso_lote_queda_en_cero_sin_error(self):
        self._lote_pendiente()
        self._validar(peso_bujes=None)
        f = self._fila()
        self.assertEqual(float(f.peso_bujes or 0), 0.0)
        self.assertEqual(float(f.peso_lote or 0), 0.0)

    def test_peso_negativo_o_basura_no_deja_valor_invalido(self):
        self._lote_pendiente()
        self._validar(peso_bujes=-5)
        self.assertEqual(float(self._fila().peso_bujes), 0.0)
        self._limpiar(); self._lote_pendiente()
        self._validar(peso_bujes="abc")
        self.assertEqual(float(self._fila().peso_bujes), 0.0)

    def test_peso_con_coma_decimal_de_texto(self):
        self._lote_pendiente()
        self._validar(peso_bujes="0,5", buenas=100)
        # to_float quita comas de miles: "0,5" -> 5.0. Se documenta el comportamiento actual.
        self.assertAlmostEqual(float(self._fila().peso_bujes), 5.0, places=4)

    def test_pendientes_devuelve_peso_bujes_para_precargar_la_tabla(self):
        self._lote_pendiente(peso_bujes=0.03)
        res = InyeccionService.obtener_pendientes_validacion() if hasattr(InyeccionService, 'obtener_pendientes_validacion') else None
        if res is None:
            self.skipTest("nombre del método de pendientes distinto")
        lote = [d for d in res['data'] if d['id_inyeccion'] == ID_INY][0]
        self.assertAlmostEqual(lote['peso_bujes'], 0.03, places=4)


class TestValidacionGuardaEntradaSalidaYPesoVela(_Base):
    """Entrada/Salida/Peso Vela del formulario de Validación (2026-09-25)."""

    def _validar_con_turno(self, turno, **item):
        base = {'codigo': CODIGO, 'pnc_inyeccion': 0, 'pnc_pulido': 0, 'pnc_list': [],
                'pnc_pulido_list': [], 'disparos': None, 'no_cavidades': None, 'buenas': 330}
        base.update(item)
        return InyeccionService.validar_lote(ID_INY, {'turno': turno, 'items': [base]}, 'Zoe Test')

    def test_guarda_los_tres_campos_del_turno(self):
        self._lote_pendiente()
        self._validar_con_turno({'entrada_manual': 120.5, 'salida_manual': 20.25, 'peso_vela_maquina': 1.75})
        f = self._fila()
        self.assertAlmostEqual(float(f.entrada), 120.5, places=2)
        self.assertAlmostEqual(float(f.salida), 20.25, places=2)
        self.assertAlmostEqual(float(f.peso_vela_maquina), 1.75, places=4)

    def test_none_no_toca_lo_guardado(self):
        self._lote_pendiente()
        f0 = self._fila(); f0.entrada = "7"; f0.salida = "3"; f0.peso_vela_maquina = "0.9"; db.session.commit()
        self._validar_con_turno({'entrada_manual': None, 'salida_manual': None, 'peso_vela_maquina': None})
        f = self._fila()
        self.assertEqual((float(f.entrada), float(f.salida), float(f.peso_vela_maquina)), (7.0, 3.0, 0.9))

    def test_sin_clave_turno_en_el_payload_no_rompe_ni_toca(self):
        self._lote_pendiente()
        self._validar()   # payload sin 'turno' (llamador viejo)
        f = self._fila()
        self.assertEqual(f.estado, 'CERRADO')
        self.assertEqual(float(f.peso_vela_maquina or 0), 0.0)

    def test_turno_null_o_no_dict_no_rompe(self):
        self._lote_pendiente()
        InyeccionService.validar_lote(ID_INY, {'turno': None, 'items': [
            {'codigo': CODIGO, 'pnc_inyeccion': 0, 'pnc_pulido': 0, 'pnc_list': [], 'pnc_pulido_list': [],
             'disparos': None, 'no_cavidades': None, 'buenas': 330}]}, 'Zoe Test')
        self.assertEqual(self._fila().estado, 'CERRADO')

    def test_negativos_y_basura_quedan_en_cero(self):
        self._lote_pendiente()
        self._validar_con_turno({'entrada_manual': -10, 'salida_manual': 'abc', 'peso_vela_maquina': -0.5})
        f = self._fila()
        self.assertEqual((float(f.entrada), float(f.salida), float(f.peso_vela_maquina)), (0.0, 0.0, 0.0))

    def test_cero_explicito_si_pisa(self):
        self._lote_pendiente()
        f0 = self._fila(); f0.peso_vela_maquina = "2"; db.session.commit()
        self._validar_con_turno({'peso_vela_maquina': 0})
        self.assertEqual(float(self._fila().peso_vela_maquina), 0.0)

    def test_pendientes_devuelve_entrada_salida_y_peso_vela(self):
        self._lote_pendiente()
        f0 = self._fila(); f0.entrada = "5"; f0.salida = "2"; f0.peso_vela_maquina = "1.5"; db.session.commit()
        res = InyeccionService.obtener_pendientes_validacion()
        lote = [d for d in res['data'] if d['id_inyeccion'] == ID_INY][0]
        self.assertEqual((lote['entrada'], lote['salida'], lote['peso_vela_maquina']), (5, 2, 1.5))

    def test_registrar_lote_manual_tambien_guarda_peso_vela(self):
        item = {"codigo_producto": "9101", "cantidad_real": 10, "no_cavidades": 1, "hora_inicio": "07:00", "hora_fin": "08:00"}
        turno = {"fecha_inicio": "2026-09-25", "maquina": MAQUINA_TEST, "responsable": "Operario Test",
                 "peso_vela_maquina": 2.5, "entrada_manual": 4, "salida_manual": 1, "orden_produccion": "OP-TESTPESO"}
        InyeccionService.registrar_lote({"turno": turno, "items": [item], "pnc_list": []}, "Operario Test")
        db.session.expire_all()
        f = db.session.query(ProduccionInyeccion).filter_by(maquina=MAQUINA_TEST).first()
        self.assertIsNotNone(f)
        self.assertAlmostEqual(float(f.peso_vela_maquina), 2.5, places=4)
        self.assertEqual((float(f.entrada), float(f.salida)), (4.0, 1.0))


class TestValidacionHorasEditadas(_Base):
    """Horas editadas en Validación: se guardan y recalculan duración/métricas (2026-09-25)."""

    def _con_turno(self, turno, **item):
        base = {'codigo': CODIGO, 'pnc_inyeccion': 0, 'pnc_pulido': 0, 'pnc_list': [],
                'pnc_pulido_list': [], 'disparos': None, 'no_cavidades': None, 'buenas': 330}
        base.update(item)
        return InyeccionService.validar_lote(ID_INY, {'turno': turno, 'items': [base]}, 'Zoe Test')

    def _lote_con_fechas(self):
        from datetime import datetime as dt
        self._lote_pendiente()
        f = self._fila()
        f.fecha_inicia = dt(2026, 9, 24, 7, 0); f.fecha_fin = dt(2026, 9, 24, 16, 0)
        f.duracion_segundos = 9 * 3600; f.hora_llegada = None
        db.session.commit()

    def test_sin_horas_en_el_payload_no_toca_tiempos_y_conserva_fecha_fin(self):
        from datetime import datetime as dt
        self._lote_con_fechas()
        self._con_turno({'hora_inicio': None, 'hora_termina': None, 'hora_llegada': None})
        f = self._fila()
        self.assertEqual(f.estado, 'CERRADO')
        self.assertEqual(f.fecha_fin, dt(2026, 9, 24, 16, 0), "fecha_fin ya no se pisa con la hora de validacion")
        self.assertEqual(f.duracion_segundos, 9 * 3600)
        self.assertEqual((f.hora_inicio, f.hora_termina), ('07:00', '16:00'))

    def test_hora_termina_editada_recalcula_duracion_y_fecha_fin(self):
        from datetime import datetime as dt
        self._lote_con_fechas()
        self._con_turno({'hora_termina': '17:30'})
        f = self._fila()
        self.assertEqual(f.hora_termina, '17:30')
        self.assertEqual(f.duracion_segundos, int(10.5 * 3600))   # sin descuento de pausas
        self.assertEqual(f.fecha_fin, dt(2026, 9, 24, 17, 30))
        self.assertEqual(f.fecha_inicia, dt(2026, 9, 24, 7, 0))
        self.assertGreater(float(f.tiempo_total_minutos), 0)
        self.assertNotIn("AUTO_BREAK", f.observaciones or "")

    def test_hora_inicio_editada_con_formato_corto_se_normaliza(self):
        self._lote_con_fechas()
        self._con_turno({'hora_inicio': '6:30'})
        f = self._fila()
        self.assertEqual(f.hora_inicio, '06:30')
        self.assertEqual(f.duracion_segundos, int(9.5 * 3600))

    def test_duracion_imposible_aborta_toda_la_validacion_sin_dejar_nada_a_medias(self):
        from backend.services.audit_service import TurnoInvalidoException
        self._lote_con_fechas()
        with self.assertRaises(TurnoInvalidoException):
            self._con_turno({'hora_termina': '20:30'}, buenas=100)   # 13.5 h > limite 12 h
        f = self._fila()
        self.assertEqual(f.estado, 'PENDIENTE', "no debe quedar cerrado")
        self.assertEqual(f.hora_termina, '16:00')
        self.assertEqual(int(f.cantidad_real), 330, "la cantidad real tampoco se toca")
        self.assertIsNone(f.validado_por)

    def test_hora_basura_es_valueerror_y_no_persiste_nada(self):
        self._lote_con_fechas()
        for mala in ('25:99', 'abc', '12:5', '7', '24:00', '12:60'):
            with self.assertRaises(ValueError, msg=mala):
                self._con_turno({'hora_termina': mala})
            f = self._fila()
            self.assertEqual(f.estado, 'PENDIENTE', mala)
            self.assertEqual(f.hora_termina, '16:00', mala)

    def test_hora_vacia_o_espacios_se_trata_como_no_tocar(self):
        self._lote_con_fechas()
        self._con_turno({'hora_inicio': '', 'hora_termina': '   '})
        f = self._fila()
        self.assertEqual((f.hora_inicio, f.hora_termina), ('07:00', '16:00'))

    def test_hora_con_segundos_se_acepta_y_se_recorta(self):
        self._lote_con_fechas()
        self._con_turno({'hora_termina': '16:45:30'})
        self.assertEqual(self._fila().hora_termina, '16:45')

    def test_hora_llegada_se_guarda_y_none_no_la_toca(self):
        self._lote_con_fechas()
        self._con_turno({'hora_llegada': '06:15'})
        self.assertEqual(self._fila().hora_llegada, '06:15')

    def test_horas_editadas_con_pnc_usan_las_piezas_inyectadas_para_la_metrica(self):
        self._lote_con_fechas()
        self._con_turno({'hora_termina': '15:00'}, buenas=300, pnc_inyeccion=30)   # inyectadas = 330
        f = self._fila()
        self.assertEqual(f.duracion_segundos, 8 * 3600)
        self.assertEqual(int(f.segundos_por_unidad), round(8 * 3600 / 330))

    def test_lote_sin_fecha_fin_previa_la_rellena(self):
        self._lote_pendiente()
        f = self._fila(); f.fecha_fin = None; db.session.commit()
        self._con_turno({})
        self.assertIsNotNone(self._fila().fecha_fin)

    def test_pendientes_devuelve_hora_llegada(self):
        self._lote_pendiente()
        f = self._fila(); f.hora_llegada = '06:10'; db.session.commit()
        res = InyeccionService.obtener_pendientes_validacion()
        lote = [d for d in res['data'] if d['id_inyeccion'] == ID_INY][0]
        self.assertEqual(lote['hora_llegada'], '06:10')


class TestInyeccionSinDescuentoDePausas(_Base):
    def _reportar(self, hi, hf, cierres=55):
        db.session.add(ProduccionInyeccion(
            id_inyeccion=ID_INY, id_codigo=CODIGO, maquina=MAQUINA_TEST, cavidades=6,
            cantidad_real=0, estado='EN_PROCESO', orden_produccion="OP-TESTPESO",
            hora_inicio=hi, responsable='Operario Test',
        ))
        db.session.commit()
        InyeccionService.reportar_trabajo(
            {'id_inyeccion': ID_INY, 'cierres': cierres, 'hora_inicio': hi, 'hora_fin': hf, 'responsable': 'Operario Test'}, 'tester')

    def test_turno_07_a_16_con_desayuno_y_almuerzo_dura_9_horas_sin_tag(self):
        self._reportar('07:00', '16:00')
        f = self._fila()
        self.assertEqual(f.duracion_segundos, 9 * 3600)
        self.assertNotIn("AUTO_BREAK", f.observaciones or "")
        self.assertEqual(int(f.cantidad_real), 55 * 6)

    def test_turno_sin_solape_igual_que_antes(self):
        self._reportar('06:00', '08:00')
        self.assertEqual(self._fila().duracion_segundos, 2 * 3600)


class TestPorcentajesWoConPesoParcial(unittest.TestCase):
    def _lineas(self, pesos, cants):
        return [{'codigo': str(i), 'buenas': c, 'bruto': c, 'pnc': 0.0, 'peso': p}
                for i, (p, c) in enumerate(zip(pesos, cants))]

    def test_todas_con_peso_reparte_por_peso_y_suma_100(self):
        ls = self._lineas([10, 30, 60], [100, 100, 100])
        WoExportService._asignar_porcentajes(ls)
        self.assertEqual([l['porcentaje'] for l in ls], [10.0, 30.0, 60.0])

    def test_peso_parcial_cae_a_cantidad_y_ninguna_linea_queda_en_cero(self):
        ls = self._lineas([50, 0, 0], [100, 100, 200])
        WoExportService._asignar_porcentajes(ls)
        self.assertEqual([l['porcentaje'] for l in ls], [25.0, 25.0, 50.0])
        self.assertAlmostEqual(sum(l['porcentaje'] for l in ls), 100.0, places=2)

    def test_sin_peso_ni_cantidad_reparte_equitativo(self):
        ls = self._lineas([0, 0, 0], [0, 0, 0])
        WoExportService._asignar_porcentajes(ls)
        self.assertAlmostEqual(sum(l['porcentaje'] for l in ls), 100.0, places=2)

    def test_una_sola_linea_es_100(self):
        ls = self._lineas([0], [330])
        WoExportService._asignar_porcentajes(ls)
        self.assertEqual(ls[0]['porcentaje'], 100.0)


class TestObservacionesPdf(unittest.TestCase):
    OBS = '[AUTO_BREAK]{"descuento_programado_min": 60.0}[/AUTO_BREAK]'

    def test_solo_tag_interno_es_ninguna(self):
        self.assertEqual(PDFGenerator._limpiar_observaciones(self.OBS), "Ninguna")

    def test_none_y_vacio_y_texto_None(self):
        for v in (None, "", "   ", "None"):
            self.assertEqual(PDFGenerator._limpiar_observaciones(v), "Ninguna")

    def test_conserva_novedad_real_y_quita_el_tag(self):
        self.assertEqual(PDFGenerator._limpiar_observaciones(self.OBS + "\nMolde atorado"), "Molde atorado")

    def test_escapa_marcado_para_paragraph(self):
        self.assertEqual(PDFGenerator._limpiar_observaciones("a <b> & c"), "a &lt;b&gt; &amp; c")

    def test_tag_multilinea(self):
        obs = "[AUTO_BREAK]{\n\"x\": 1\n}[/AUTO_BREAK] ok"
        self.assertEqual(PDFGenerator._limpiar_observaciones(obs), "ok")


if __name__ == '__main__':
    unittest.main()
