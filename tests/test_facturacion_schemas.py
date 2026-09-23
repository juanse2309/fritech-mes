# -*- coding: utf-8 -*-
"""
Tests de ExportarWorldOfficeSchema (backend/schemas/facturacion_schemas.py),
primer schema Pydantic del proyecto -- endpoint de exportacion a World
Office, dinero/trazabilidad critica (regla 4 de CLAUDE.md).
"""
import unittest

from pydantic import ValidationError

from backend.schemas.facturacion_schemas import ExportarWorldOfficeSchema


class TestExportarWorldOfficeSchema(unittest.TestCase):
    def test_payload_vacio_es_valido_con_defaults_none(self):
        payload = ExportarWorldOfficeSchema(**{})
        self.assertIsNone(payload.ids)
        self.assertIsNone(payload.consecutivo_inicial)

    def test_consecutivo_string_vacio_del_formulario_no_rompe(self):
        # Caso real y mas comun: <input>.value de un campo vacio es '' en JS,
        # nunca None -- ver facturacion.js linea 369/286. Sin el validador
        # 'before' que lo mapea a None, esto rechazaba el flujo normal de
        # "exportar sin fijar un consecutivo manual".
        payload = ExportarWorldOfficeSchema(ids=[], consecutivo_inicial='')
        self.assertIsNone(payload.consecutivo_inicial)
        self.assertEqual(payload.ids, [])

    def test_payload_completo_valido(self):
        payload = ExportarWorldOfficeSchema(ids=["104561", "104562"], consecutivo_inicial=1500)
        self.assertEqual(payload.ids, ["104561", "104562"])
        self.assertEqual(payload.consecutivo_inicial, 1500)

    def test_consecutivo_cero_o_negativo_se_rechaza(self):
        with self.assertRaises(ValidationError):
            ExportarWorldOfficeSchema(consecutivo_inicial=0)
        with self.assertRaises(ValidationError):
            ExportarWorldOfficeSchema(consecutivo_inicial=-5)

    def test_consecutivo_no_numerico_se_rechaza(self):
        with self.assertRaises(ValidationError):
            ExportarWorldOfficeSchema(consecutivo_inicial="no-es-un-numero")

    def test_ids_string_suelto_en_vez_de_lista_se_rechaza(self):
        # Antes de este schema, mandar un string en vez de lista se colaba
        # hasta `Pedido.id_pedido.in_(ids_filter)` y SQLAlchemy iteraba
        # caracter por caracter en silencio -- el schema debe cortarlo antes.
        with self.assertRaises(ValidationError):
            ExportarWorldOfficeSchema(ids="104561")

    def test_ids_con_numeros_crudos_se_rechaza(self):
        # id_pedido es VARCHAR en db_pedidos (ver models/sql_models.py) --
        # Pydantic v2 no coacciona int a str en List[str] por defecto, lo
        # cual es el comportamiento correcto acá: obliga al caller a mandar
        # el mismo tipo que ya usa toda la app (string), sin ambigüedad.
        with self.assertRaises(ValidationError):
            ExportarWorldOfficeSchema(ids=[104561, 104562])


if __name__ == "__main__":
    unittest.main()
