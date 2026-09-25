# -*- coding: utf-8 -*-
"""
E2E de registro de pedidos: login (via `logged_in_page`) -> navegar a
Pedidos -> seleccionar el cliente sintético (sembrado por seed_test_data)
desde el autocomplete real -> añadir un ítem con el producto sintético ->
enviar y confirmar. Usa los mismos datos que `backend/scripts/seed_test_data.py`
siembra -- correr ese script antes de esta suite (ver tests/README.md).
"""
from datetime import date

PRODUCTO_CODIGO = "SEED-E2E-PROD-1"
CLIENTE_NOMBRE_PARCIAL = "SEED-E2E-Cliente"


def test_registrar_pedido_con_cliente_y_producto_sembrados(logged_in_page):
    page = logged_in_page

    page.evaluate("() => window.AuthModule.navigateTo('pedidos')")
    page.wait_for_selector("#pedidos-page.active", timeout=10000)

    # Fecha es required por HTML5 -- el navegador bloquea el submit antes de
    # que el JS del form corra si queda vacía.
    page.fill("#ped-fecha", date.today().isoformat())

    # Autocomplete real de cliente: escribir, esperar la sugerencia, hacer
    # clic en ella (así se setea this.clienteSeleccionado, que el submit
    # exige explícitamente).
    page.fill("#ped-cliente", CLIENTE_NOMBRE_PARCIAL)
    page.wait_for_selector("#ped-cliente-suggestions .suggestion-item", timeout=10000)
    page.click("#ped-cliente-suggestions .suggestion-item")
    assert PRODUCTO_CODIGO not in page.input_value("#ped-cliente")  # sanity: no quedó el código pegado
    assert page.input_value("#ped-nit") != ""

    # Producto: el input se parsea como "CODIGO - descripcion" (o solo
    # código si no hay " - "), no requiere seleccionar sugerencia -- ver
    # ModuloPedidos.agregarItemAlCarrito en pedidos.js.
    page.fill("#ped-producto", PRODUCTO_CODIGO)
    page.fill("#ped-cantidad", "2")
    page.fill("#ped-precio", "15000")
    page.click("#btn-agregar-item")

    page.wait_for_selector("#items-pedido-body tr:not(.empty-state)", timeout=10000)

    page.click("#form-pedidos button[type='submit']")

    # Modal de confirmación propio de Pedidos (no es SweetAlert2).
    page.wait_for_selector("#modal-confirmar", state="visible", timeout=10000)
    page.click("#modal-confirmar")

    # Tras el registro exitoso, el formulario se limpia (ModuloPedidos.
    # registrarPedido llama limpiarFormulario + resetea la lista de items).
    page.wait_for_selector("#items-pedido-body tr.empty-state", timeout=15000)
    ultimo_id = page.evaluate("() => window.ModuloPedidos.ultimoIdRegistrado")
    assert ultimo_id, "registrarPedido no dejó un id_pedido registrado"
