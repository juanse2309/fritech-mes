# -*- coding: utf-8 -*-
"""
Schemas Pydantic del dominio de facturación. Primer archivo de
backend/schemas/ -- ver CLAUDE.md para el estándar: los endpoints nuevos, o
los que se toquen y manejen dinero/trazabilidad, validan el payload con un
schema aquí en vez de checks manuales `data.get(...)` sueltos en la ruta.
"""
from typing import List, Optional

from pydantic import BaseModel, field_validator


class ExportarWorldOfficeSchema(BaseModel):
    """
    Payload de /api/exportar/world-office y su /preview. Antes ids_filter y
    consecutivo_inicial llegaban a FacturacionService.procesar_datos_wo sin
    validar tipo: un `consecutivo_inicial` mal formado se descartaba en
    silencio (fallback a None dentro del service) en vez de avisar al
    usuario, y un `ids` que no fuera lista rompía el filtro SQL
    (`Pedido.id_pedido.in_(ids_filter)`) de forma no obvia -- especialmente
    grave en /world-office porque corre en background (task_runner), así que
    un dato mal formado se perdía sin ningún error visible en el momento.
    """
    ids: Optional[List[str]] = None
    consecutivo_inicial: Optional[int] = None

    @field_validator('consecutivo_inicial', mode='before')
    @classmethod
    def _vacio_es_none(cls, v):
        # El campo del formulario llega como '' (string vacío) cuando el
        # usuario no escribe nada -- input.value de un <input> vacío nunca es
        # None en JS. Sin este paso, Pydantic intenta parsear '' como int y
        # rechaza el caso más común (exportar sin fijar un consecutivo
        # manual), rompiendo el flujo real.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator('consecutivo_inicial')
    @classmethod
    def _consecutivo_positivo(cls, v):
        if v is not None and v <= 0:
            raise ValueError('consecutivo_inicial debe ser un entero positivo')
        return v
