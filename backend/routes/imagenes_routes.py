from flask import Blueprint, Response, jsonify
import requests
from functools import lru_cache
import logging
from backend.utils.auth_middleware import require_role, ROL_ADMINS

imagenes_bp = Blueprint('imagenes', __name__)
logger = logging.getLogger(__name__)

# Caché en memoria para 1000 imágenes. Solo cachea ÉXITOS: lru_cache no
# cachea una llamada que termina en excepción, así que un fallo (Drive caído,
# timeout, respuesta no-200) se señaliza lanzando en vez de devolver
# (None, None) -- antes ese (None, None) SÍ quedaba cacheado para siempre
# (hasta reiniciar el proceso o pulsar "limpiar-cache"), así que una falla
# transitoria de Drive en la primera carga dejaba esa imagen rota en TODA
# la app de forma permanente, mientras que otras imágenes (nunca solicitadas
# durante esa falla) seguían funcionando -- de ahí que "aparecen en algunos
# lugares y en otros no" sin patrón aparente.
@lru_cache(maxsize=1000)
def obtener_imagen_google_drive(file_id):
    '''Obtiene imagen de Google Drive con caché en memoria, manejando disclaimers de virus.'''
    session = requests.Session()
    # Formato de descarga directa que suele ser más estable para el proxy
    url = f"https://drive.google.com/uc?export=download&id={file_id}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    response = session.get(url, headers=headers, timeout=10, stream=True)

    # Si Google pide confirmación por archivo grande/virus (típico en Drive API sin auth)
    if response.status_code == 200 and ("confirm=" in response.text or "download" not in response.headers.get('Content-Disposition', '')):
        import re
        match = re.search(r'confirm=([a-zA-Z0-9_-]+)', response.text)
        if match:
            confirm_token = match.group(1)
            url = f"https://drive.google.com/uc?export=download&confirm={confirm_token}&id={file_id}"
            response = session.get(url, headers=headers, timeout=10, stream=True)

    if response.status_code != 200:
        raise RuntimeError(f"status {response.status_code}")

    return response.content, response.headers.get('Content-Type', 'image/jpeg')

@imagenes_bp.route('/proxy/<file_id>')
def proxy_imagen(file_id):
    '''Endpoint de proxy con caché. Acepta IDs de archivo de Google Drive.'''

    if not file_id or len(file_id) < 5:
        return jsonify({'error': 'ID de archivo inválido'}), 400

    # Si el ID parece una URL completa (raro pero posible si se pasa mal), intentar extraer el ID
    if 'drive.google.com' in file_id:
        import re
        match = re.search(r'(?:id=|[ /])([a-zA-Z0-9_-]{25,})', file_id)
        if match:
            file_id = match.group(1)

    try:
        content, content_type = obtener_imagen_google_drive(file_id)
    except Exception as e:
        logger.error(f"❌ Proxy falló para file_id {file_id}: {e}")
        return jsonify({'error': 'No se pudo obtener la imagen del servidor de Google'}), 502

    return Response(
        content,
        mimetype=content_type,
        headers={
            'Cache-Control': 'public, max-age=31536000',
            'Access-Control-Allow-Origin': '*'
        }
    )

@imagenes_bp.route('/limpiar-cache', methods=['POST'])
@require_role(ROL_ADMINS)
def limpiar_cache():
    '''Endpoint para limpiar el caché manualmente.'''
    obtener_imagen_google_drive.cache_clear()
    return jsonify({'mensaje': 'Caché limpiado exitosamente'}), 200
