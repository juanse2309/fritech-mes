# -*- coding: utf-8 -*-
"""
Tests de ProductosService.sincronizar_precios_wo, movida desde
productos_routes.py (auditoria de seguridad 2026-09-23, regla de capas del
proyecto). Toca precios reales -- casos borde obligatorios antes de dar el
refactor por bueno (regla 4 de CLAUDE.md: datos sinteticos con caracteres
especiales, nulos, duplicados en logica de escritura critica de dinero).
"""
import unittest
import os

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import Producto
from backend.services.productos_service import (
    ProductosService,
    FormatoArchivoNoSoportadoError,
    ColumnasNoEncontradasError,
)

PREFIJO_TEST = "TEST-AUDIT-PRECIOS"


class TestSincronizarPreciosWO(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self._limpiar()
        db.session.add(Producto(codigo_sistema=f"{PREFIJO_TEST}-001", precio=100))
        db.session.commit()

    def tearDown(self):
        self._limpiar()
        self.ctx.pop()

    def _limpiar(self):
        db.session.query(Producto).filter(
            Producto.codigo_sistema.like(f"{PREFIJO_TEST}%")
        ).delete(synchronize_session=False)
        db.session.commit()

    def test_formato_no_soportado_lanza_excepcion_especifica(self):
        with self.assertRaises(FormatoArchivoNoSoportadoError):
            ProductosService.sincronizar_precios_wo("lista.txt", b"cualquier cosa", db.session)

    def test_columnas_no_encontradas_lanza_excepcion_especifica(self):
        csv = "columna_x,columna_y\nabc,123\n".encode("utf-8")
        with self.assertRaises(ColumnasNoEncontradasError):
            ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)

    def test_fila_valida_actualiza_precio(self):
        csv = f"Código,Precio 1\n{PREFIJO_TEST}-001,250.50\n".encode("utf-8")
        resultado = ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)
        self.assertEqual(resultado["actualizados_count"], 1)
        self.assertEqual(resultado["omitidos_count"], 0)
        self.assertEqual(resultado["errores_count"], 0)
        fila = Producto.query.filter_by(codigo_sistema=f"{PREFIJO_TEST}-001").first()
        self.assertEqual(float(fila.precio), 250.50)

    def test_codigo_no_encontrado_se_omite_sin_romper(self):
        csv = f"Código,Precio 1\n{PREFIJO_TEST}-NOEXISTE,250\n".encode("utf-8")
        resultado = ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)
        self.assertEqual(resultado["actualizados_count"], 0)
        self.assertEqual(resultado["omitidos_count"], 1)
        self.assertIn(f"{PREFIJO_TEST}-NOEXISTE", resultado["no_encontrados"])

    def test_precio_invalido_se_omite_sin_romper_ni_tocar_precio_existente(self):
        csv = f"Código,Precio 1\n{PREFIJO_TEST}-001,no-es-numero\n".encode("utf-8")
        resultado = ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)
        self.assertEqual(resultado["omitidos_count"], 1)
        fila = Producto.query.filter_by(codigo_sistema=f"{PREFIJO_TEST}-001").first()
        self.assertEqual(float(fila.precio), 100)

    def test_codigo_vacio_se_omite_sin_romper(self):
        csv = "Código,Precio 1\n,250\n".encode("utf-8")
        resultado = ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)
        self.assertEqual(resultado["omitidos_count"], 1)

    def test_codigo_con_espacios_hace_match_por_trim(self):
        csv = f"Código,Precio 1\n  {PREFIJO_TEST}-001  ,300\n".encode("utf-8")
        resultado = ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)
        self.assertEqual(resultado["actualizados_count"], 1)

    def test_codigos_duplicados_en_archivo_se_procesan_independientemente(self):
        csv = (
            f"Código,Precio 1\n"
            f"{PREFIJO_TEST}-001,111\n"
            f"{PREFIJO_TEST}-001,222\n"
        ).encode("utf-8")
        resultado = ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)
        self.assertEqual(resultado["actualizados_count"], 2)
        fila = Producto.query.filter_by(codigo_sistema=f"{PREFIJO_TEST}-001").first()
        self.assertEqual(float(fila.precio), 222)

    def test_caracteres_especiales_en_codigo_no_rompe_el_proceso(self):
        csv = f'Código,Precio 1\n{PREFIJO_TEST}-Ñ&/\\"\'<>,150\n'.encode("utf-8")
        resultado = ProductosService.sincronizar_precios_wo("lista.csv", csv, db.session)
        self.assertEqual(resultado["errores_count"], 0)
        self.assertEqual(resultado["omitidos_count"], 1)


if __name__ == "__main__":
    unittest.main()
