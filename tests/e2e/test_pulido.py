# -*- coding: utf-8 -*-
"""
E2E de Pulido: login -> Modo Planta PRO (el cronómetro en vivo con
iniciar/pausar/terminar vive en #panel-pulido-pro, NO en "Modo Satélite" --
ese modo es un formulario de captura manual de totales, un flujo distinto)
-> iniciar ciclo (persiste en SQL vía POST /api/pulido) -> pausar/reanudar.
El botón de "Terminar" abre un reporte final con más campos (peso,
novedades) fuera del alcance de este primer E2E -- ese flujo ya tiene
cobertura de characterization tests a nivel de servicio (ver
tests/test_validar_lote_peso_y_tiempos_sin_break.py para el patrón
equivalente en Inyección); acá solo se cubre el camino de UI real de
iniciar/pausar/reanudar.
"""
from datetime import date

PRODUCTO_CODIGO = "SEED-E2E-PROD-1"


def test_iniciar_pausar_y_reanudar_ciclo_de_pulido(logged_in_page, e2e_staff_user):
    page = logged_in_page

    page.evaluate("() => window.AuthModule.navigateTo('pulido')")
    page.wait_for_selector("#pulido-page.active", timeout=10000)

    page.click("#btn-modo-pro")

    page.fill("#fecha-pulido", date.today().isoformat())
    # Nombre único por corrida (no un literal fijo): si un run anterior deja
    # una sesión de Pulido sin cerrar para el mismo responsable,
    # verificarTrabajoActivo() la detecta al cargar la página y bloquea los
    # campos compartidos para forzar su recuperación -- eso rompía este
    # test en corridas consecutivas hasta agregar la limpieza en
    # e2e_staff_user (ver conftest.py).
    page.fill("#responsable-pulido-input", e2e_staff_user["nombre_completo"])
    page.fill("#buscador-productos", PRODUCTO_CODIGO)
    page.fill("#lote-pulido", date.today().isoformat())

    # validarBotonInicioPro() habilita el botón vía los listeners de
    # input/change de esos 3 campos -- esperamos a que deje de estar
    # disabled en vez de asumir que ya lo está.
    page.wait_for_selector("#btn-iniciar-pulido:not([disabled])", timeout=10000)
    page.click("#btn-iniciar-pulido")

    page.wait_for_selector("#pulido-active-msg", state="visible", timeout=10000)
    assert PRODUCTO_CODIGO in page.inner_text("#current-pulido-job")

    # Pausar
    page.click("#btn-pausar-pulido")
    page.wait_for_selector("#pulido-pausa-msg", state="visible", timeout=10000)

    # Reanudar (mismo botón, cambia de texto/handler a "Reanudar")
    page.click("#btn-pausar-pulido")
    page.wait_for_selector("#pulido-pausa-msg", state="hidden", timeout=10000)
