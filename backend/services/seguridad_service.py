# -*- coding: utf-8 -*-
"""
Seguridad industrial: fecha del último accidente y contador de días sin
accidentes para el Modo TV de planta.

Se guarda en AppConfig (clave/valor de propósito general, ya existe en cada
instancia -- una instancia por cliente, cada una con su propio contador), así
que no requiere migración. Un valor ausente NO es "0 días": significa "sin
dato", y el Modo TV no debe mostrar nada inventado.
"""
import logging
from datetime import date

from backend.core.sql_database import db
from backend.models.sql_models import AppConfig, OperacionLog
from backend.utils.time_utils import get_colombia_time

logger = logging.getLogger(__name__)

CLAVE_ULTIMO_ACCIDENTE = 'seguridad.ultimo_accidente'


class SeguridadService:

    @staticmethod
    def _leer_fecha(fila):
        """Fecha guardada, o None si no hay o el valor está corrupto (se ignora, no se rompe la TV)."""
        if not fila or not fila.valor:
            return None
        try:
            return date.fromisoformat(fila.valor.strip())
        except ValueError:
            logger.warning(f"[Seguridad] Valor inválido en {CLAVE_ULTIMO_ACCIDENTE}: {fila.valor!r}")
            return None

    @staticmethod
    def _resumen(fecha):
        hoy = get_colombia_time().date()
        # Una fecha futura no debería existir (POST la rechaza); si aparece por
        # edición manual en la BD, mejor "sin dato" que un contador negativo.
        if fecha is None or fecha > hoy:
            return {'fecha': None, 'dias': None}
        return {'fecha': fecha.isoformat(), 'dias': (hoy - fecha).days}

    @staticmethod
    def obtener_ultimo_accidente() -> dict:
        """{'fecha': 'YYYY-MM-DD'|None, 'dias': int|None}; días según la fecha de hoy en Colombia."""
        try:
            fila = db.session.get(AppConfig, CLAVE_ULTIMO_ACCIDENTE)
            return SeguridadService._resumen(SeguridadService._leer_fecha(fila))
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error leyendo último accidente: {e}")
            raise

    @staticmethod
    def registrar_ultimo_accidente(fecha: date, usuario: str) -> dict:
        """
        Fija la fecha del último accidente (upsert) y deja constancia en db_logs
        de quién la cambió y de qué a qué -- es un indicador de seguridad y
        alterarlo sin rastro no debe ser posible. Rechaza fechas futuras.
        """
        hoy = get_colombia_time().date()
        if fecha > hoy:
            raise ValueError('La fecha del último accidente no puede ser futura.')

        try:
            fila = db.session.get(AppConfig, CLAVE_ULTIMO_ACCIDENTE)
            previa = SeguridadService._leer_fecha(fila)

            if fila:
                fila.valor = fecha.isoformat()
            else:
                db.session.add(AppConfig(clave=CLAVE_ULTIMO_ACCIDENTE, valor=fecha.isoformat()))

            db.session.add(OperacionLog(
                modulo='SEGURIDAD',
                operario=str(usuario or 'DESCONOCIDO')[:150],
                accion='ULTIMO_ACCIDENTE',
                detalles=f"{previa.isoformat() if previa else 'sin dato'} -> {fecha.isoformat()}"
            ))
            db.session.commit()
            logger.info(f"✅ [Seguridad] Último accidente = {fecha.isoformat()} (por {usuario})")
            return SeguridadService._resumen(fecha)
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error registrando último accidente: {e}")
            raise
