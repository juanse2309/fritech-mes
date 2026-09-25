# -*- coding: utf-8 -*-
"""
E2E de login de staff (FRIPARTS) contra un servidor real -- primer eslabón
de la cadena de E2E; pedidos/pulido/facturación reusan `logged_in_page`.
"""


def test_login_staff_friparts_con_usuario_sintetico(page, e2e_staff_user):
    page.goto("/")

    page.get_by_text("STAFF FRIPARTS").click()
    page.wait_for_selector("#login-modal", state="visible")

    page.select_option("#login-usuario", label=e2e_staff_user["nombre_completo"])
    page.fill("#login-password", e2e_staff_user["password"])
    page.click("#btn-login-submit")

    # Tras un login exitoso el modal se cierra y queda la sesión guardada
    # en sessionStorage -- comprobamos ambas cosas para no dar falso
    # positivo si el modal se cierra sin haber logueado de verdad.
    page.wait_for_selector("#login-modal", state="hidden", timeout=10000)
    assert page.evaluate("() => !!sessionStorage.getItem('friparts_user')")

    usuario_guardado = page.evaluate("() => JSON.parse(sessionStorage.getItem('friparts_user'))")
    assert usuario_guardado.get("username") == e2e_staff_user["username"] or \
        usuario_guardado.get("user") == e2e_staff_user["username"]


def test_login_con_password_incorrecta_muestra_error_y_no_entra(page, e2e_staff_user):
    page.goto("/")
    page.get_by_text("STAFF FRIPARTS").click()
    page.wait_for_selector("#login-modal", state="visible")

    page.select_option("#login-usuario", label=e2e_staff_user["nombre_completo"])
    page.fill("#login-password", "0000000000")
    page.click("#btn-login-submit")

    page.wait_for_selector("#login-error-msg", state="visible", timeout=10000)
    assert page.is_visible("#login-modal")
    assert not page.evaluate("() => !!sessionStorage.getItem('friparts_user')")
