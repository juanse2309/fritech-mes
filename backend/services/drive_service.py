"""
drive_service.py
=================
Sube archivos a Google Drive -- usado para el PDF de validación de
inyección (reunión 2026-08-25).

Contexto: antes de este cambio, `_generar_pdf_lote` en inyeccion_service.py
generaba el PDF, tenía la subida a Drive deshabilitada por comentario
explícito, y lo BORRABA en el `finally` -- reportando 'pdf_generated: True'
de un archivo que ya no existía en ningún lado. Este servicio es la mitad
que faltaba: la subida real.

⚠️ NO usa cuenta de servicio, usa OAuth delegado -- verificado 2026-08-25:
la carpeta destino (`db_op_wo_staging`... perdón, carpeta de Drive
"HISTORICO PDF INYECCION") es de una cuenta Gmail normal
(friparts09@gmail.com, 5 TB propios), no de Google Workspace. Las cuentas
de servicio NO TIENEN cuota de almacenamiento propia y no pueden crear
archivos en una carpeta de "Mi unidad" de una persona -- Google responde
403 storageQuotaExceeded sin importar cuántos permisos de "Editor" se le
den a la carpeta. Los Gmail gratuitos tampoco tienen Unidades Compartidas
(exclusivo de Workspace de pago), así que esa vía tampoco aplicaba.

La solución real: un cliente OAuth de "Aplicación de escritorio" que
friparts09@gmail.com autorizó UNA VEZ (pantalla de consentimiento de
Google), entregando un refresh_token que no expira. Con ese token, FRITECH
sube archivos *como si los subiera la propia cuenta* -- cuentan contra su
cuota normal, sin el problema de las cuentas de servicio.

Variables de entorno (ninguna es un archivo en el repo):
  GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET -- del cliente de
      escritorio creado en Google Cloud Console (Auth Platform > Clientes).
  GOOGLE_OAUTH_REFRESH_TOKEN -- obtenido una sola vez con
      scratch/oauth_drive_setup.py (flujo interactivo, requiere que
      friparts09@gmail.com apruebe en su navegador). No expira salvo que
      se revoque desde myaccount.google.com/permissions.
  DRIVE_REPORTS_FOLDER_ID -- carpeta destino.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

# drive.file: la app solo puede tocar los archivos que ELLA MISMA crea, no
# todo el Drive de la cuenta que autorizó -- mínimo privilegio necesario
# para subir reportes.
SCOPES = ['https://www.googleapis.com/auth/drive.file']


class DriveNoConfiguradoException(Exception):
    """Faltan credenciales o carpeta destino. Es un error de configuración,
    no de red -- el llamador debe tratarlo distinto de un fallo transitorio."""
    pass


class DriveService:
    # Cliente autenticado cacheado a nivel de proceso -- construirlo en cada
    # subida sería trabajo repetido sin necesidad. Las credenciales OAuth se
    # refrescan solas por dentro (google-auth) cuando el access_token vence;
    # no hay que invalidar este cache por eso.
    _service = None

    @staticmethod
    def _cliente():
        if DriveService._service is not None:
            return DriveService._service

        refresh_token = os.getenv('GOOGLE_OAUTH_REFRESH_TOKEN')
        client_id = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
        client_secret = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

        if not (refresh_token and client_id and client_secret):
            raise DriveNoConfiguradoException(
                "Faltan GOOGLE_OAUTH_REFRESH_TOKEN / GOOGLE_OAUTH_CLIENT_ID / "
                "GOOGLE_OAUTH_CLIENT_SECRET -- correr scratch/oauth_drive_setup.py "
                "para generarlos (requiere autorización interactiva una sola vez)."
            )

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,  # se obtiene solo al primer uso, vía refresh_token
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        # cache_discovery=False: evita que la librería intente escribir un
        # archivo de caché de descubrimiento en disco (ruidoso en logs y
        # sin sentido en un filesystem efímero como el de Render).
        DriveService._service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        return DriveService._service

    @staticmethod
    def _carpeta_destino():
        carpeta = os.getenv('DRIVE_REPORTS_FOLDER_ID')
        if not carpeta:
            raise DriveNoConfiguradoException(
                "DRIVE_REPORTS_FOLDER_ID no está configurada -- no hay carpeta destino."
            )
        return carpeta

    @staticmethod
    def subir_archivo(local_path, nombre_destino, carpeta_id=None, mimetype='application/pdf'):
        """
        Sube local_path a Drive con el nombre nombre_destino, dentro de
        carpeta_id (o DRIVE_REPORTS_FOLDER_ID si no se especifica). Devuelve
        la URL de vista (webViewLink).

        Lanza DriveNoConfiguradoException si faltan credenciales/carpeta, o
        la excepción real de la API de Google ante cualquier otro fallo
        (permisos, cuota, red) -- el llamador decide si eso es fatal o
        best-effort; este método no oculta errores.
        """
        from googleapiclient.http import MediaFileUpload

        service = DriveService._cliente()
        carpeta = carpeta_id or DriveService._carpeta_destino()

        metadata = {'name': nombre_destino, 'parents': [carpeta]}
        media = MediaFileUpload(local_path, mimetype=mimetype, resumable=False)

        archivo = service.files().create(
            body=metadata, media_body=media, fields='id, webViewLink'
        ).execute()

        return archivo.get('webViewLink')
