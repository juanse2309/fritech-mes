import logging
from backend.core.sql_database import db
from backend.models.sql_models import SuscripcionesPush

logger = logging.getLogger(__name__)

class NotificationService:
    @staticmethod
    def guardar_suscripcion(usuario_id, suscripcion_data):
        """
        Guarda o actualiza una suscripción Push en la base de datos SQL.
        Implementa UPSERT Idempotente y Blindaje Transaccional estricto.
        """
        try:
            endpoint = suscripcion_data.get('endpoint')
            keys = suscripcion_data.get('keys', {})
            p256dh = keys.get('p256dh')
            auth = keys.get('auth')

            if not endpoint or not p256dh or not auth:
                raise ValueError("Datos de suscripción incompletos")

            # Lógica UPSERT Idempotente: Filtrar por endpoint
            sub = SuscripcionesPush.query.filter_by(endpoint=endpoint).first()
            if sub:
                sub.user_id = usuario_id
                sub.p256dh = p256dh
                sub.auth = auth
            else:
                sub = SuscripcionesPush(
                    user_id=usuario_id,
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth
                )
                db.session.add(sub)
            
            db.session.commit()
            return True
        except Exception as e:
            # Blindaje Transaccional: Rollback obligatorio ante cualquier fallo
            db.session.rollback()
            logger.error(f"Error crítico guardando suscripción push: {e}")
            raise e

    @staticmethod
    def enviar_notificacion_push(user_id, titulo, cuerpo, url_destino='/', image_url=None):
        """
        Envía una notificación push a todos los endpoints activos de un usuario.
        Implementa limpieza automática de endpoints caducados (Self-Healing).
        Params:
            image_url (str|None): URL de imagen para campañas B2B Marketing.
        """
        import os
        import json
        from pywebpush import webpush, WebPushException

        vapid_private_key = os.environ.get('VAPID_PRIVATE_KEY')
        vapid_subject = os.environ.get('VAPID_SUBJECT', 'mailto:admin@friparts.com')

        if not vapid_private_key:
            logger.error("VAPID_PRIVATE_KEY no configurada. Imposible enviar notificaciones.")
            return False

        suscripciones = SuscripcionesPush.query.filter_by(user_id=user_id).all()
        if not suscripciones:
            logger.info(f"El usuario {user_id} no tiene suscripciones push activas.")
            return False

        payload_dict = {
            "title": titulo,
            "body": cuerpo,
            "icon": "/static/img/icon-192.png",
            "badge": "/static/img/icon-192.png",
            "data": {
                "url": url_destino
            }
        }
        # Guard: Sanitización de image_url (previene Payload Overflow > 4KB)
        if image_url:
            if image_url.startswith('data:image') or len(image_url) > 250:
                logger.warning(f"image_url descartada por seguridad de payload (len={len(image_url) if image_url else 0}).")
                image_url = None
        if image_url:
            payload_dict["image"] = image_url
        payload_str = json.dumps(payload_dict)
        enviados = 0

        for sub in suscripciones:
            sub_info = {
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh,
                    "auth": sub.auth
                }
            }
            try:
                webpush(
                    subscription_info=sub_info,
                    data=payload_str,
                    vapid_private_key=vapid_private_key,
                    vapid_claims={"sub": vapid_subject}
                )
                enviados += 1
            except WebPushException as ex:
                # Limpieza de muertos (Self-Healing)
                if ex.response is not None and ex.response.status_code in [404, 410]:
                    logger.warning(f"Suscripción inactiva (410/404) para {user_id}. Limpiando basura...")
                    try:
                        db.session.delete(sub)
                        db.session.commit()
                    except Exception as e_del:
                        db.session.rollback()
                        logger.error(f"Error al eliminar suscripción muerta: {e_del}")
                else:
                    logger.error(f"Error WebPush enviando a {user_id}: {ex}")
            except Exception as e:
                logger.error(f"Error inesperado enviando notificación a {user_id}: {e}")
        
        return enviados > 0

    @staticmethod
    def enviar_notificacion_por_departamento(departamentos, titulo, cuerpo, url_destino='/'):
        """
        Envía un push a todos los usuarios activos de uno o más departamentos
        de planta (ej. ['INYECCION', 'ENSAMBLE']) -- pensado para
        recordatorios automáticos por horario (ver
        InyeccionService.recordar_reporte_parcial), no para flujos
        disparados por un usuario. Reutiliza enviar_notificacion_push por
        usuario, que ya trae la limpieza self-healing de endpoints caducados.
        """
        from backend.models.sql_models import Usuario

        if isinstance(departamentos, str):
            departamentos = [departamentos]

        filtros = [Usuario.departamento.ilike(d) for d in departamentos]
        usuarios = Usuario.query.filter(Usuario.activo == True).filter(db.or_(*filtros)).all()

        enviados = 0
        for u in usuarios:
            try:
                if NotificationService.enviar_notificacion_push(u.username, titulo, cuerpo, url_destino):
                    enviados += 1
            except Exception as e:
                logger.error(f"Error enviando recordatorio a {u.username}: {e}")

        return enviados > 0

    @staticmethod
    def enviar_notificacion_masiva(payload_dict):
        """
        Delega el envío iterativo de notificaciones al executor en background.
        """
        from flask import current_app
        from backend.services.task_manager import pwa_executor
        
        # Obtenemos el objeto real de la app para el contexto de background
        app = current_app._get_current_object()
        pwa_executor.submit(NotificationService._tarea_envio_masivo, app, payload_dict)

    @staticmethod
    def _tarea_envio_masivo(app, payload_dict):
        import os
        import json
        from pywebpush import webpush, WebPushException

        with app.app_context():
            from backend.models.sql_models import Usuario
            from backend.core.sql_database import db

            logger.info("Iniciando envío masivo de notificaciones Push...")
            segmento = payload_dict.get('segmento', 'Todos')
            
            # Construir query con JOIN a la tabla Usuario para poder filtrar
            query = db.session.query(SuscripcionesPush).join(
                Usuario, SuscripcionesPush.user_id == Usuario.username
            )

            if segmento == 'Clientes':
                query = query.filter(Usuario.rol.ilike('cliente'))
            elif segmento == 'Empleados':
                query = query.filter(~Usuario.rol.ilike('cliente'))
            
            suscripciones = query.all()
            logger.info(f"==> Suscripciones encontradas en BD para segmento '{segmento}': {len(suscripciones)}")

            vapid_private_key = os.environ.get('VAPID_PRIVATE_KEY')
            vapid_subject = os.environ.get('VAPID_SUBJECT', 'mailto:admin@friparts.com')

            if not vapid_private_key:
                logger.error("VAPID_PRIVATE_KEY no está configurada.")
                return

            payload_str = json.dumps(payload_dict)

            for sub in suscripciones:
                sub_info = {
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth": sub.auth
                    }
                }
                try:
                    webpush(
                        subscription_info=sub_info,
                        data=payload_str,
                        vapid_private_key=vapid_private_key,
                        vapid_claims={"sub": vapid_subject}
                    )
                except WebPushException as ex:
                    # AUTO-LIMPIEZA ATÓMICA
                    if ex.response is not None and ex.response.status_code in [404, 410]:
                        logger.warning(f"Suscripción inactiva detectada (user_id {sub.user_id}). Eliminando...")
                        try:
                            db.session.delete(sub)
                            db.session.commit()
                        except Exception as delete_ex:
                            db.session.rollback()
                            logger.error(f"Error limpiando suscripción {sub.id}: {delete_ex}")
                    else:
                        logger.error(f"Error enviando notificación a {sub.user_id}: {ex}")
                except Exception as e:
                    logger.error(f"Error inesperado enviando push a {sub.user_id}: {e}")
            
            logger.info(f"Envío masivo finalizado. Segmento: {segmento}. Total intentados: {len(suscripciones)}")
