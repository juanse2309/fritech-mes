# -*- coding: utf-8 -*-
"""
Tests de PausasService (puros, sin base de datos):

  1. obtener_ventanas(): formato que consume el frontend (Panel de
     Supervisión y Modo TV de Pulido) -- las horas viven solo en el backend.
  2. calcular_descuento_pausas_programadas(): casos borde del solape con
     Desayuno (09:00-09:20) y Almuerzo (13:00-13:40). Es la regla que el
     cronómetro de Modo TV replica en JS (calcularDescuentoBreakMs), así que
     estos mismos casos se usaron para comprobar que ambos dan igual.
"""
import unittest
from datetime import datetime

from backend.services.pausas_service import PausasService


def _dt(h, m, s=0, dia=24):
    return datetime(2026, 9, dia, h, m, s)


class TestObtenerVentanas(unittest.TestCase):

    def test_formato_para_el_frontend(self):
        ventanas = PausasService.obtener_ventanas()
        self.assertEqual(ventanas, [
            {"tipo": "DESAYUNO", "nombre": "Desayuno", "inicio": "09:00", "fin": "09:20"},
            {"tipo": "ALMUERZO", "nombre": "Almuerzo", "inicio": "13:00", "fin": "13:40"},
        ])

    def test_devuelve_copia_no_la_tupla_interna(self):
        ventanas = PausasService.obtener_ventanas()
        ventanas.append({"tipo": "X"})
        self.assertEqual(len(PausasService.obtener_ventanas()), 2)


class TestDescuentoPausasProgramadas(unittest.TestCase):

    def _seg(self, ini, fin):
        return PausasService.calcular_descuento_pausas_programadas(ini, fin)["segundos_descuento"]

    def test_sin_solape(self):
        self.assertEqual(self._seg(_dt(7, 0), _dt(8, 59)), 0)

    def test_desayuno_completo(self):
        self.assertEqual(self._seg(_dt(8, 0), _dt(10, 0)), 20 * 60)

    def test_desayuno_parcial_al_inicio_y_al_final(self):
        self.assertEqual(self._seg(_dt(8, 0), _dt(9, 10)), 10 * 60)
        self.assertEqual(self._seg(_dt(9, 15), _dt(10, 0)), 5 * 60)

    def test_limites_exactos_no_descuentan(self):
        self.assertEqual(self._seg(_dt(9, 20), _dt(13, 0)), 0)

    def test_ambas_ventanas(self):
        self.assertEqual(self._seg(_dt(8, 0), _dt(14, 0)), (20 + 40) * 60)

    def test_sesion_entera_dentro_del_break(self):
        self.assertEqual(self._seg(_dt(9, 5), _dt(9, 10)), 5 * 60)

    def test_intervalos_invalidos_o_vacios(self):
        self.assertEqual(self._seg(None, _dt(10, 0)), 0)
        self.assertEqual(self._seg(_dt(10, 0), None), 0)
        self.assertEqual(self._seg(_dt(10, 0), _dt(10, 0)), 0)
        self.assertEqual(self._seg(_dt(11, 0), _dt(10, 0)), 0)

    def test_cruce_de_medianoche_no_descuenta(self):
        self.assertEqual(self._seg(_dt(8, 0, dia=24), _dt(10, 0, dia=25)), 0)


if __name__ == "__main__":
    unittest.main()
