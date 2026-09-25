# -*- coding: utf-8 -*-
"""
Schemas Pydantic del dominio comercial (dashboard comercial de Frimetals).
Ver backend/schemas/facturacion_schemas.py para el estándar del proyecto.
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

# Techo del rango consultable: evita que un rango absurdo (ej. 1900-2100)
# convierta el dashboard en un escaneo completo de db_ventas.
MAX_DIAS_RANGO = 366 * 5


class ComercialDashboardQuery(BaseModel):
    """
    Query string de GET /api/comercial/dashboard. Ambas fechas son opcionales:
    el servicio aplica el default (1-ene del año en curso hasta hoy).
    """
    desde: Optional[date] = None
    hasta: Optional[date] = None

    @field_validator('desde', 'hasta', mode='before')
    @classmethod
    def _vacio_es_none(cls, v):
        # Un <input type="date"> vacío manda '' (no None) -- sin este paso
        # Pydantic intenta parsear '' como fecha y rechaza el caso más común
        # (abrir el dashboard sin tocar el filtro). Mismo motivo que
        # ExportarWorldOfficeSchema.consecutivo_inicial.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode='after')
    def _rango_coherente(self):
        if self.desde and self.hasta:
            if self.desde > self.hasta:
                raise ValueError("'desde' no puede ser posterior a 'hasta'")
            if (self.hasta - self.desde).days > MAX_DIAS_RANGO:
                raise ValueError('El rango no puede superar 5 años')
        return self
