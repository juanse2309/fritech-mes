# -*- coding: utf-8 -*-
"""
Tests del texto que se sintetiza en el anuncio por voz del líder de Pulido
(2026-09-24). Puros: no llaman a gTTS ni a la base.

Contexto: los nombres llegan en MAYÚSCULAS (UPPER(TRIM(responsable)) en
get_ranking_leaderboard) y gTTS deletrea las palabras en mayúsculas que no
reconoce (NIMISICA, LIZETH, YESICA se midieron 2-3 veces más largas). El texto
se normaliza a formato normal antes de sintetizar.
"""
import os
import unittest

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.services.pulido_service import PulidoService

fmt = PulidoService.formatear_nombre_para_voz
texto = PulidoService.texto_anuncio_lider


class TestNombreParaVoz(unittest.TestCase):

    def test_mayusculas_pasan_a_formato_normal(self):
        self.assertEqual(fmt("PAOLA NIMISICA"), "Paola Nimisica")
        self.assertEqual(fmt("LAURA LIZETH VARGAS R."), "Laura Lizeth Vargas R.")
        self.assertEqual(fmt("ADRIANA QUINTERO POVEDA"), "Adriana Quintero Poveda")

    def test_tildes_y_enie(self):
        self.assertEqual(fmt("MARÍA PEÑA"), "María Peña")
        self.assertEqual(fmt("ÑANDÚ"), "Ñandú")

    def test_apostrofe(self):
        self.assertEqual(fmt("D'ANGELO"), "D'Angelo")

    def test_espacios_raros_se_colapsan(self):
        self.assertEqual(fmt("  YUDI    MONTERO \n"), "Yudi Montero")

    def test_ya_en_formato_normal_no_cambia(self):
        self.assertEqual(fmt("Solange Guerrero"), "Solange Guerrero")

    def test_vacio_o_nulo_no_rompe(self):
        for valor in (None, "", "   "):
            self.assertEqual(fmt(valor), "Una operaria")

    def test_no_interpreta_html_ni_comillas(self):
        # Se sintetiza como texto plano; solo se verifica que no truene ni altere caracteres.
        self.assertEqual(fmt("<B>ANA</B> \"X\""), "<B>Ana</B> \"X\"")


class TestTextoAnuncio(unittest.TestCase):

    def test_frase_completa(self):
        self.assertEqual(
            texto("PAOLA NIMISICA", 1234),
            "Paola Nimisica se puso a la cabeza en Pulido con 1234 piezas.",
        )

    def test_float_no_se_lee_con_punto_cero(self):
        self.assertIn("con 1234 piezas", texto("ANA", 1234.0))
        self.assertNotIn(".0", texto("ANA", 1234.0))

    def test_cantidad_nula_o_invalida_no_rompe(self):
        self.assertIn("con 0 piezas", texto("ANA", None))
        self.assertIn("con 0 piezas", texto("ANA", "no-es-numero"))

    def test_nombre_nulo_no_rompe(self):
        self.assertTrue(texto(None, 10).startswith("Una operaria se puso"))


if __name__ == "__main__":
    unittest.main()
