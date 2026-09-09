"""
agente_recordatorio_reporte_parcial.py
=======================================
Recordatorio del reporte parcial de avance de Inyección (pedido del usuario
2026-09-04): aparte de Iniciar/Finalizar, ahora se quiere que las máquinas
reporten los cierres del contador a media jornada -- a las 11:00 am y a las
3:00 pm. La lectura la sigue tomando un operario en planta (es un dato
físico, no hay sensor), así que este agente NO reporta nada por sí solo:
solo golpea un endpoint que manda un Web Push de recordatorio si hay
máquinas EN_PROCESO en ese momento. Mismo patrón operativo que
agente_cierre_jornada_ensamble.py / agente_wo_cartera.py.

Configuración de las Tareas Programadas (Windows Task Scheduler) -- se
necesitan DOS, una por franja:
  - Programa: el intérprete de Python del entorno (ej. python.exe)
  - Argumentos: la ruta completa a este archivo, seguida de "11" o "15"
    (ej. ...\\agente_recordatorio_reporte_parcial.py 11)
  - Disparadores: diario a las 11:00 (con argumento "11") y diario a las
    15:00 (con argumento "15")
  - Variables de entorno necesarias (mismo .env que los demás agentes):
      SYNC_API_URL   (opcional, por defecto la URL de producción)
      SYNC_TOKEN     (obligatoria -- la misma que usan los demás agentes)
"""
import os
import sys
import io
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente_recordatorio_reporte_parcial.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')),
        logging.FileHandler(_LOG_PATH, encoding='utf-8')
    ]
)
logger = logging.getLogger("AgenteRecordatorioReporteParcial")

API_URL = os.getenv("SYNC_API_URL", "https://proyecto-friparts.onrender.com")
SYNC_TOKEN = os.getenv("SYNC_TOKEN")
if not SYNC_TOKEN:
    raise RuntimeError("SYNC_TOKEN no está configurada")


def recordar_reporte_parcial(momento=None):
    endpoint = f"{API_URL}/api/mes/recordar_reporte_parcial"
    params = {"token": SYNC_TOKEN}
    if momento:
        params["momento"] = momento

    try:
        response = requests.get(endpoint, params=params, timeout=30)

        if response.status_code != 200:
            logger.error(f"[-] Error en recordatorio de reporte parcial: HTTP {response.status_code}")
            logger.error(response.text)
            return

        cuerpo = response.json()
        resultado = cuerpo.get("data") or {}

        if resultado.get("enviado"):
            logger.info(f"[+] Recordatorio enviado (momento={momento or 'N/A'}).")
        else:
            logger.info(f"[=] Nada que avisar (momento={momento or 'N/A'}): {resultado.get('motivo', 'sin máquinas EN_PROCESO')}")

    except Exception as e:
        logger.error(f"[-] Error crítico en el Agente de Recordatorio de Reporte Parcial: {e}")


if __name__ == "__main__":
    momento_arg = sys.argv[1] if len(sys.argv) > 1 else None
    recordar_reporte_parcial(momento_arg)
