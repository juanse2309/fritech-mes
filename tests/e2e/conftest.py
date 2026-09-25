# -*- coding: utf-8 -*-
"""
Fixtures de Playwright para tests/e2e/. Corren contra un servidor Flask
real (arrancado aparte -- ver tests/README.md) usando la misma
DATABASE_URL de test que el resto de la suite. El guard de
tests/conftest.py (DATABASE_URL debe contener "test") aplica igual acá
porque `e2e_staff_user` escribe directo a esa base.
"""
import os
import secrets

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.security import generate_password_hash

os.environ.setdefault("WO_SYNC_API_KEY", "clave_de_prueba_secreta_123")

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:5005")

# Este sandbox trae Chromium preinstalado en una ruta fija (ver
# /root/.ccr/README.md del entorno de desarrollo). En CI, el paso
# `playwright install --with-deps chromium` del workflow deja el navegador
# donde Playwright lo espera por defecto y esta ruta no existe -- por eso
# el fixture cae al comportamiento default de Playwright si no la encuentra.
_CHROMIUM_SANDBOX_PATH = "/opt/pw-browsers/chromium"


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        kwargs = {"headless": True}
        if os.path.exists(_CHROMIUM_SANDBOX_PATH):
            kwargs["executable_path"] = _CHROMIUM_SANDBOX_PATH
        b = p.chromium.launch(**kwargs)
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    # Viewport alto a propósito: varias páginas de este SPA (Facturación,
    # Pedidos) son formularios/tablas largas y el viewport 1280x720 por
    # defecto de Playwright deja filas de tabla fuera de vista -- eso hace
    # que document.elementFromPoint() devuelva null ahí y los clicks fallen
    # de forma intermitente (confirmado depurando el checkbox de Facturación).
    context = browser.new_context(
        base_url=BASE_URL, viewport={"width": 1440, "height": 2200}, accept_downloads=True
    )
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture()
def e2e_staff_user(app_context):
    """
    Usuario de staff sintético con rol ADMINISTRACION: es uno de los
    únicos roles que aparecen en el selector de login de la página
    principal (AuthService.obtener_staff_frimetals_admin_activo) y
    además cubre los guards @require_role(ROL_ADMINS + ...) que protegen
    pedidos/pulido/facturación. Password NUMÉRICA a propósito -- el campo
    de login está pensado para un número de documento (placeholder
    "Ingrese número de documento", filtro de solo dígitos en el JS), así
    que una password alfanumérica random no representaría el flujo real.
    """
    from backend.core.sql_database import db
    from backend.models.sql_models import Usuario

    username = f"TEST-E2E-{secrets.token_hex(4)}"
    password_plano = str(secrets.randbelow(10 ** 10)).zfill(10)
    nombre_completo = f"TEST E2E {username}"

    usuario = Usuario(
        username=username,
        password_hash=generate_password_hash(password_plano, method="scrypt"),
        nombre_completo=nombre_completo,
        rol="ADMINISTRACION",
        activo=True,
    )
    db.session.add(usuario)
    db.session.commit()

    yield {"username": username, "password": password_plano, "nombre_completo": nombre_completo}

    # Limpieza de cualquier Pedido/sesión de Pulido que un E2E haya creado
    # a nombre de este usuario sintético (test_pedidos.py/test_facturacion.py
    # registran pedidos reales vía /api/pedidos/registrar, que usa el
    # numerador pedido_id_seq compartido -- no llevan un prefijo TEST- en su
    # id_pedido, así que se identifican por vendedor/responsable en vez de
    # por id). Sin esto, una sesión de Pulido iniciada y no cerrada por un
    # test queda "activa" en SQL y bloquea el siguiente run que reuse el
    # mismo nombre de responsable (ver test_pulido.py).
    from backend.models.sql_models import Pedido, ProduccionPulido

    db.session.query(Pedido).filter(Pedido.vendedor == nombre_completo).delete(
        synchronize_session=False
    )
    db.session.query(ProduccionPulido).filter(
        ProduccionPulido.responsable == nombre_completo
    ).delete(synchronize_session=False)
    db.session.query(Usuario).filter(Usuario.username == username).delete(
        synchronize_session=False
    )
    db.session.commit()


@pytest.fixture()
def logged_in_page(page, e2e_staff_user):
    """Página ya logueada como el usuario sintético -- para los specs que
    no necesitan probar el login en sí, solo partir de una sesión activa."""
    page.goto("/")
    page.get_by_text("STAFF FRIPARTS").click()
    page.wait_for_selector("#login-modal", state="visible")
    page.select_option("#login-usuario", label=e2e_staff_user["nombre_completo"])
    page.fill("#login-password", e2e_staff_user["password"])
    page.click("#btn-login-submit")
    page.wait_for_selector("#login-modal", state="hidden", timeout=10000)
    return page
