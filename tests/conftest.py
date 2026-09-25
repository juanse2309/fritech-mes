# -*- coding: utf-8 -*-
"""
Fixtures compartidos para tests/, usados por los tests NUEVOS agregados en
el Bloque 1 de la red de pruebas (inventario, compras, E2E). Los tests que
ya existían antes de este archivo siguen con su patrón manual de siempre
(import directo de `backend.app.app` + `app.app_context()` a mano) -- no se
tocaron, así que no corren ningún riesgo de regresión por este cambio.

Corre contra la base configurada en DATABASE_URL. Igual que en los tests
existentes: NUNCA apuntar esto a una base de producción -- ver el guard de
abajo, que corta la ejecución si no detecta que es una base de test.
"""
import os
import secrets

import pytest
from werkzeug.security import generate_password_hash

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")


@pytest.fixture(scope="session", autouse=True)
def _guard_db_de_test():
    """
    Corta toda la sesión de pytest si DATABASE_URL no parece apuntar a una
    base de test. Con este backend, el simple `import backend.app` ya
    ejecuta `db.create_all()` + varios `ALTER TABLE` contra lo que sea que
    apunte esa variable en ese momento -- este guard es la protección más
    barata contra correr esto sin querer contra una base real (ej. un .env
    local mal puesto).
    """
    url = os.environ.get("DATABASE_URL", "")
    permitido_explicito = os.environ.get("PYTEST_ALLOW_DB") == "1"
    if not permitido_explicito and "test" not in url.lower():
        pytest.exit(
            "DATABASE_URL no contiene 'test' -- me niego a correr tests "
            "(que hacen db.create_all() + escrituras) contra lo que podría "
            "ser una base real. Si de verdad quieres correr esto contra "
            "esa URL, exporta PYTEST_ALLOW_DB=1 explícitamente.",
            returncode=1,
        )


@pytest.fixture(scope="session")
def flask_app():
    from backend.app import app
    return app


@pytest.fixture()
def app_context(flask_app):
    ctx = flask_app.app_context()
    ctx.push()
    yield flask_app
    ctx.pop()


@pytest.fixture()
def test_client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def synthetic_staff_user(app_context):
    """
    Crea un Usuario de staff sintético (prefijo TEST-QA-) con password
    aleatoria generada en el momento (nunca hardcodeada) y lo borra al
    terminar el test. Da (username, password_en_texto_plano) para poder
    loguearse de verdad contra /api/auth/login.
    """
    from backend.core.sql_database import db
    from backend.models.sql_models import Usuario

    username = f"TEST-QA-{secrets.token_hex(4)}"
    password_plano = secrets.token_urlsafe(16)

    usuario = Usuario(
        username=username,
        password_hash=generate_password_hash(password_plano, method="scrypt"),
        nombre_completo="TEST QA Robot",
        rol="operario",
        activo=True,
    )
    db.session.add(usuario)
    db.session.commit()

    yield username, password_plano

    db.session.query(Usuario).filter(Usuario.username == username).delete(
        synchronize_session=False
    )
    db.session.commit()
