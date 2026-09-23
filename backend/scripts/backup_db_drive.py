"""
backup_db_drive.py
===================
Backup diario de la base de datos de producción (pg_dump) subido a Google
Drive. Reemplaza el cron de backups que corría como recurso separado en
Render -- se perdió al dar de baja Render (2026-09-14/15, ver memoria del
proyecto "Migracion Render -> DigitalOcean"), sin que quedara registrado en
este repo porque vivía solo en la configuración de Render.

Mismo destino y formato de archivo que el cron viejo (carpeta Drive
"fritech_backups", nombre <empresa>_YYYYMMDD_HHMMSS.sql.gz), para no romper
continuidad con los backups anteriores que ya están ahí. Ahora corre como
Tarea Programada de Coolify DENTRO de este mismo contenedor de la app --
no depende de SSH, de ninguna PC externa, ni de ningún recurso/servicio
adicional (cero costo extra).

El prefijo del nombre de archivo sale de EMPRESA_NOMBRE (ver
backend/config/settings.py::Empresa), NO está quemado a "friparts" --
cada instancia (FriParts, Frimetals, la siguiente que se despliegue) ya
define esa variable para su propia identidad de negocio, así que reusarla
aquí evita que los backups de un cliente nuevo queden nombrados como si
fueran de FriParts en la carpeta compartida de Drive.

Reutiliza la misma cuenta/token OAuth que ya usa drive_service.py para los
PDF de validación de Inyección (GOOGLE_OAUTH_*) -- mismo Drive personal de
friparts09@gmail.com (5 TB propios), sin pagar almacenamiento externo (S3,
etc). Por diseño, TODAS las instancias (FriParts, Frimetals, futuros
clientes) comparten la misma carpeta "fritech_backups" -- se distinguen
solo por el prefijo del nombre de archivo. Si en algún momento eso deja de
ser aceptable (aislamiento de datos entre clientes, por ejemplo), hay que
separar por carpeta o por cuenta de Drive; hoy no está separado.

Variables de entorno:
  DATABASE_URL -- ya la tiene el contenedor de la app (Postgres real).
  DRIVE_BACKUP_FOLDER_ID -- ID de la carpeta "fritech_backups" en Drive
    (mismo valor para todas las instancias hoy).
  EMPRESA_NOMBRE -- ya la tiene el contenedor de la app; determina el
    prefijo del archivo (ver Empresa.NOMBRE).

Invocación (Tarea Programada de Coolify, dentro del contenedor):
  python3 -m backend.scripts.backup_db_drive

IMPORTANTE para un cliente nuevo: esta Tarea Programada de Coolify hay que
crearla A MANO en el servicio de ESE cliente. No se hereda de FriParts ni
de ningún otro -- ver checklist completo en CLAUDE.md, sección
"Backups de base de datos".
"""
import gzip
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

# Permite invocar este archivo tanto como módulo (python3 -m backend.scripts.
# backup_db_drive, desde /app) como archivo suelto -- mismo patrón que
# agente_wo_comercial.py, por si Coolify o quien sea termina llamándolo de
# la segunda forma.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.config.settings import Empresa

logger = logging.getLogger("backup_db_drive")
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


def _pg_dump_a_gz(database_url, ruta_gz):
    """
    Corre pg_dump en formato de texto plano y comprime la salida con gzip
    en streaming (sin materializar el .sql sin comprimir en disco) -- mismo
    formato (.sql.gz) que ya usaba el cron de Render, para que quede en la
    misma carpeta de Drive junto a los backups viejos sin romper el patrón
    de nombres.

    --no-owner --no-privileges: el dump queda restaurable en cualquier rol/
    servidor, no solo en uno donde exista exactamente el mismo usuario
    'postgres' con los mismos privilegios que en producción.
    """
    proceso = subprocess.Popen(
        ['pg_dump', database_url, '--no-owner', '--no-privileges'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with gzip.open(ruta_gz, 'wb') as f_gz:
        while True:
            chunk = proceso.stdout.read(1024 * 1024)
            if not chunk:
                break
            f_gz.write(chunk)
    codigo = proceso.wait()
    if codigo != 0:
        error = proceso.stderr.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"pg_dump salió con código {codigo}: {error}")


def main():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("❌ DATABASE_URL no está configurada -- no hay qué respaldar.")
        sys.exit(1)

    carpeta_id = os.environ.get('DRIVE_BACKUP_FOLDER_ID')
    if not carpeta_id:
        logger.error("❌ DRIVE_BACKUP_FOLDER_ID no está configurada -- no hay carpeta destino en Drive.")
        sys.exit(1)

    # Sanitizado defensivo: EMPRESA_NOMBRE es texto libre en .env (pensado
    # para mostrarse en UI, no para ir en un nombre de archivo), así que se
    # reduce a algo seguro para Drive/filesystem en vez de asumir que ya
    # viene limpio.
    prefijo = re.sub(r'[^a-z0-9]+', '', Empresa.NOMBRE.lower()) or 'empresa'

    ahora = datetime.now()
    nombre_archivo = f"{prefijo}_{ahora.strftime('%Y%m%d_%H%M%S')}.sql.gz"

    with tempfile.TemporaryDirectory() as tmp_dir:
        ruta_gz = os.path.join(tmp_dir, nombre_archivo)

        logger.info("Iniciando pg_dump...")
        try:
            _pg_dump_a_gz(database_url, ruta_gz)
        except Exception as e:
            logger.error(f"❌ Falló pg_dump: {e}")
            sys.exit(1)

        tamano_mb = os.path.getsize(ruta_gz) / (1024 * 1024)
        logger.info(f"✅ Dump generado: {nombre_archivo} ({tamano_mb:.1f} MB)")

        logger.info("Subiendo a Google Drive (fritech_backups)...")
        try:
            from backend.services.drive_service import DriveService
            url = DriveService.subir_archivo(
                ruta_gz, nombre_archivo, carpeta_id=carpeta_id, mimetype='application/gzip'
            )
            logger.info(f"✅ Backup subido a Drive: {url}")
        except Exception as e:
            # El dump local se descarta igual (TemporaryDirectory) -- si Drive
            # falla, no queda ni el backup remoto ni uno local de respaldo.
            # Aceptable: el siguiente run del día siguiente lo vuelve a intentar,
            # y esto no debe dejar archivos sueltos acumulándose en el contenedor.
            logger.error(f"❌ Falló la subida a Drive: {e}")
            sys.exit(1)


if __name__ == '__main__':
    main()
