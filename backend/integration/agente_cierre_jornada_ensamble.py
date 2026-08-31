"""
agente_cierre_jornada_ensamble.py
==================================
Red de seguridad para el cierre de jornada de Ensamble (pedido del usuario
2026-08-28): si el responsable (Albeiro) olvida darle al botón manual de
"Cerrar Jornada", este agente le pega al endpoint de cierre automático una
vez al día -- pensado para correr como Tarea Programada de Windows a las
22:00, mismo patrón operativo que agente_wo_cartera.py.

A diferencia de los agentes de World Office, este NO necesita pyodbc ni
acceso a un SQL Server local -- es solo una llamada HTTP al backend, que ya
tiene todo lo necesario en Postgres. El endpoint (ver
backend/routes/ensamble_routes.py, cerrar_jornada_auto) solo cierra si el
checklist de procesos de ese día ya está completo; si de verdad quedó
trabajo sin terminar, no fuerza nada y lo deja para revisión manual/admin
al día siguiente.

Configuración de la Tarea Programada (Windows Task Scheduler):
  - Programa: el intérprete de Python del entorno (ej. python.exe)
  - Argumentos: la ruta completa a este archivo
  - Disparador: diario, 22:00
  - Variables de entorno necesarias (mismo .env que los demás agentes):
      SYNC_API_URL   (opcional, por defecto la URL de producción)
      SYNC_TOKEN     (obligatoria -- la misma que usan los agentes de WO)
"""
import os
import sys
import io
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

# Log persistente en disco (mismo patrón que agente_wo_cartera.py): sin
# esto, la única prueba de que este agente corrió -- y si falló -- era la
# consola de la tarea programada, que nadie mira. Forzar UTF-8 evita crash
# con emojis/tildes en la consola cp1252 de Windows.
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente_cierre_jornada_ensamble.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')),
        logging.FileHandler(_LOG_PATH, encoding='utf-8')
    ]
)
logger = logging.getLogger("AgenteCierreJornadaEnsamble")

API_URL = os.getenv("SYNC_API_URL", "https://proyecto-friparts.onrender.com")
SYNC_TOKEN = os.getenv("SYNC_TOKEN")
if not SYNC_TOKEN:
    raise RuntimeError("SYNC_TOKEN no está configurada")


def cerrar_jornada_automatica():
    endpoint = f"{API_URL}/api/ensamble/cerrar_jornada_auto"
    try:
        response = requests.get(endpoint, params={"token": SYNC_TOKEN}, timeout=30)

        if response.status_code != 200:
            logger.error(f"[-] Error en cierre automático: HTTP {response.status_code}")
            logger.error(response.text)
            return

        cuerpo = response.json()
        resultado = cuerpo.get("data") or {}
        accion = resultado.get("accion")

        if accion == 'CERRADA_AUTO':
            logger.info(f"[+] {resultado.get('numero_op')} cerrada automáticamente (el checklist ya estaba completo).")
        elif accion == 'CHECKLIST_INCOMPLETO':
            metas = resultado.get('metas_incompletas') or []
            logger.warning(
                f"[!] {resultado.get('numero_op')} sigue con {len(metas)} meta(s) de checklist "
                f"incompletas -- NO se forzó el cierre. Requiere revisión manual."
            )
        elif accion == 'SIN_CAMBIOS':
            logger.info(f"[=] {resultado.get('numero_op')} ya estaba en estado {resultado.get('estado_actual')!r}. Nada que hacer.")
        elif accion == 'SIN_OP':
            logger.info("[=] No hay ninguna OP de ensamble programada hoy. Nada que hacer.")
        else:
            logger.warning(f"[?] Respuesta inesperada del servidor: {cuerpo}")

    except Exception as e:
        logger.error(f"[-] Error crítico en el Agente de Cierre de Jornada: {e}")


if __name__ == "__main__":
    cerrar_jornada_automatica()
