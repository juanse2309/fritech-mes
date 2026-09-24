# -*- coding: utf-8 -*-
"""
Schemas Pydantic del dominio de seguridad industrial (contador de días sin
accidentes del Modo TV). Estándar del proyecto: ver facturacion_schemas.py.
"""
import re
from datetime import date

from pydantic import BaseModel, field_validator

_FORMATO_FECHA = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# Piso de sanidad contra un año mal digitado (0202, 1926...). La regla de "no puede
# ser futura" depende de la hora de Colombia y vive en SeguridadService.
FECHA_MINIMA_ACCIDENTE = date(2000, 1, 1)


class UltimoAccidenteSchema(BaseModel):
    """
    Payload de POST /api/seguridad/ultimo_accidente: {"fecha": "YYYY-MM-DD"}.

    Solo se acepta ese formato en texto. Pydantic por defecto también convertiría
    un entero (timestamp Unix) o un datetime completo a fecha en silencio, y un
    valor así no debe pasar como "fecha del último accidente".
    """
    fecha: date

    @field_validator('fecha', mode='before')
    @classmethod
    def _solo_texto_iso(cls, v):
        if not isinstance(v, str) or not _FORMATO_FECHA.match(v.strip()):
            raise ValueError('fecha debe ser texto con formato YYYY-MM-DD')
        return v.strip()

    @field_validator('fecha')
    @classmethod
    def _no_absurdamente_antigua(cls, v):
        if v < FECHA_MINIMA_ACCIDENTE:
            raise ValueError('fecha demasiado antigua')
        return v
