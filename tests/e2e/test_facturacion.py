# -*- coding: utf-8 -*-
"""
E2E de Facturación: crea un pedido propio vía API (con la sesión ya
logueada de `logged_in_page`, no reutiliza el pedido de test_pedidos.py --
cada E2E debe poder correr solo) -> lo selecciona en la grilla de
"Pendientes de exportar" -> genera la vista previa -> confirma la
exportación a World Office y espera la notificación de éxito.
"""
import json
from datetime import date

CLIENTE_NOMBRE = 'SEED-E2E-Cliente Ñandú & Cía. "Especial"'
PRODUCTO_CODIGO = "SEED-E2E-PROD-1"


def _crear_pedido_propio(page, vendedor):
    payload = {
        "fecha": date.today().isoformat(),
        "vendedor": vendedor,
        "cliente": CLIENTE_NOMBRE,
        "nit": "900123456-1",
        "forma_pago": "Contado",
        "productos": [{
            "codigo": PRODUCTO_CODIGO,
            "descripcion": "Item de prueba E2E facturación",
            "cantidad": 1,
            "precio_unitario": 15000,
        }],
    }
    resp = page.request.post(
        "/api/pedidos/registrar",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
    )
    assert resp.ok, f"No se pudo crear el pedido de prueba: {resp.status} {resp.text()}"
    data = resp.json()
    assert data.get("success"), f"registrar_pedido devolvió error: {data}"
    return data["data"]["id_pedido"]


def test_exportar_pedido_a_world_office(logged_in_page, e2e_staff_user):
    page = logged_in_page
    id_pedido = _crear_pedido_propio(page, e2e_staff_user["nombre_completo"])

    page.evaluate("() => window.AuthModule.navigateTo('facturacion')")
    page.wait_for_selector("#facturacion-page.active", timeout=10000)
    page.wait_for_selector("#global-loader", state="hidden", timeout=15000)

    # HALLAZGO (no se corrige acá, ver Bloque 3 del backlog de frontend):
    # el checkbox de esta fila tiene onchange="togglePedido(id);
    # event.stopPropagation()", pero eso solo detiene la propagación del
    # evento 'change' -- el 'click' sigue burbujeando hasta el onclick de
    # la <tr>, que hace su PROPIO toggle manual de checkbox.checked y de
    # togglePedido(id). Resultado: un clic directo sobre el checkbox
    # dispara el toggle DOS veces (una vez nativo, otra vez por la fila) y
    # se cancelan entre sí -- la selección nunca queda marcada. Confirmado
    # depurando este mismo E2E. Workaround acá: clickear la fila en una
    # celda que NO sea el checkbox, que es el único camino que de verdad
    # aplica un solo toggle.
    fila = page.locator(f"tr:has(input.check-pedido-wo[value='{id_pedido}'])")
    fila.wait_for(state="visible", timeout=15000)
    fila.locator("td").nth(1).click()
    page.wait_for_function(
        "(id) => window.ModuloFacturacion.pedidosSeleccionados.has(id)",
        arg=id_pedido,
        timeout=10000,
    )

    page.click("button:has-text('Generar Archivo')")
    page.wait_for_selector("#modal-preview-wo", state="visible", timeout=10000)
    page.wait_for_selector("#tabla-preview-wo tbody tr", timeout=15000)

    # descargarExcelWO hace polling de la tarea en el backend y, al
    # terminar, dispara la descarga real del Excel generado -- esa
    # descarga (no un modal) es la señal de éxito más confiable bajo
    # automatización: en Chromium headless, el clic programático en el
    # <a href=...> de descarga puede interrumpir la ejecución del script
    # antes de llegar al Swal.fire de éxito (confirmado depurando este
    # mismo E2E -- el backend sí completa la exportación en los logs del
    # servidor en ambos casos), así que no se puede depender del Swal.
    with page.expect_download(timeout=30000) as download_info:
        page.click("#btn-confirmar-exportar-wo")
    descarga = download_info.value
    assert descarga.suggested_filename.lower().endswith((".xlsx", ".xls"))
