// ============================================
// facturacion.js - Lógica de Exportación World Office - NAMESPACED
// ============================================

const ModuloFacturacion = {

    // Estado local
    pedidosPendientes: [],
    pedidosSeleccionados: new Set(),
    pedidosPendientesExportacion: [],
    pedidosSeleccionadosExportacion: new Set(),

    /**
     * Inicializar módulo
     */
    inicializar: function () {
        console.log('🔧 [Exportación WO] Inicializando...');
        this.cargarPedidosPendientes();
        this.cargarPedidosPendientesExportacion();
        this.cargarPedidosSinConfirmar();
        console.log('✅ [Exportación WO] Módulo inicializado');
    },

    /**
     * Reconciliación: pedidos marcados EXPORTADO_WO cuyo documento nunca
     * volvió sincronizado desde World Office (ver
     * PedidosService.detectar_exportados_sin_confirmar_wo en el backend).
     */
    cargarPedidosSinConfirmar: async function () {
        const alertBox = document.getElementById('wo-sin-confirmar-alert');
        const tbody = document.getElementById('tbody-wo-sin-confirmar');
        const countSpan = document.getElementById('wo-sin-confirmar-count');
        if (!alertBox || !tbody || !countSpan) return;

        try {
            const response = await fetch('/api/facturacion/pedidos-exportados-sin-confirmar');
            const data = await response.json();

            if (!data.success || !Array.isArray(data.pedidos) || data.pedidos.length === 0) {
                alertBox.style.display = 'none';
                return;
            }

            countSpan.textContent = data.pedidos.length;
            tbody.innerHTML = data.pedidos.map(p => `
                <tr>
                    <td class="fw-bold">${p.id_pedido}</td>
                    <td>${p.cliente || ''}</td>
                    <td>${p.vendedor || ''}</td>
                    <td>${p.fecha || ''}</td>
                    <td class="text-center">${p.dias_sin_confirmar ?? ''}</td>
                    <td class="text-end">$ ${formatNumber(p.total)}</td>
                </tr>
            `).join('');
            alertBox.style.display = 'block';
        } catch (error) {
            console.error('Error cargando reconciliación de pedidos exportados:', error);
            alertBox.style.display = 'none';
        }
    },

    /**
     * Cargar pedidos pendientes desde el backend
     */
    cargarPedidosPendientes: async function () {
        try {
            const tbody = document.getElementById('tbody-pedidos-exportar');
            if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="text-center py-5 text-muted"><i class="fas fa-spinner fa-spin fa-2x mb-2"></i><br>Cargando pedidos pendientes...</td></tr>';

            const response = await fetch('/api/facturacion/pedidos-pendientes');
            const data = await response.json();

            if (data.success) {
                this.pedidosPendientes = data.pedidos;
                this.pedidosSeleccionados.clear();
                this.actualizarContadorSeleccion();
                this.renderizarTablaExportar();

                // Actualizar check all
                const checkAll = document.getElementById('check-all-wo');
                if (checkAll) checkAll.checked = false;

                if (this.pedidosPendientes.length === 0) {
                    mostrarNotificacion('No hay pedidos pendientes', 'info');
                }
            } else {
                if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-danger">Error: ${data.error}</td></tr>`;
            }
        } catch (error) {
            console.error('Error cargando pedidos pendientes:', error);
            const tbody = document.getElementById('tbody-pedidos-exportar');
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-danger">Error de conexión</td></tr>`;
        }
    },

    /**
     * Renderizar tabla de pedidos
     */
    renderizarTablaExportar: function () {
        const tbody = document.getElementById('tbody-pedidos-exportar');
        if (!tbody) return;

        if (this.pedidosPendientes.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center py-5 text-muted"><i class="fas fa-inbox fa-3x mb-3 text-light"></i><br>No hay pedidos en estado PENDIENTE</td></tr>';
            return;
        }

        tbody.innerHTML = this.pedidosPendientes.map(p => `
            <tr onclick="ModuloFacturacion.toggleRowClick('${p.id}', event)" style="cursor: pointer;">
                <td class="text-center">
                    <div class="form-check d-flex justify-content-center">
                        <input class="form-check-input check-pedido-wo" type="checkbox" value="${p.id}" 
                               ${this.pedidosSeleccionados.has(p.id) ? 'checked' : ''}
                               onchange="ModuloFacturacion.togglePedido('${p.id}'); event.stopPropagation();">
                    </div>
                </td>
                <td>
                    <span class="fw-bold text-primary">${p.id}</span>
                </td>
                <td>
                    <div class="fw-bold text-dark">${p.cliente}</div>
                    <small class="text-muted"><i class="fas fa-id-card me-1"></i>${p.nit || 'Sin NIT'}</small>
                </td>
                <td>${p.fecha}</td>
                <td>${p.vendedor}</td>
                <td class="text-center">
                    <span class="badge bg-light text-dark border rounded-pill px-3">${p.items_count}</span>
                </td>
                <td class="text-end fw-bold text-success pe-4">$ ${formatNumber(p.total)}</td>
            </tr>
        `).join('');
    },

    /**
     * Cargar pedidos de EXPORTACIÓN pendientes desde el backend (plantilla
     * WO distinta -- ver /api/facturacion/pedidos-pendientes-exportacion).
     */
    cargarPedidosPendientesExportacion: async function () {
        try {
            const tbody = document.getElementById('tbody-pedidos-exportar-exp');
            if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="text-center py-5 text-muted"><i class="fas fa-spinner fa-spin fa-2x mb-2"></i><br>Cargando pedidos de exportación...</td></tr>';

            const response = await fetch('/api/facturacion/pedidos-pendientes-exportacion');
            const data = await response.json();

            if (data.success) {
                this.pedidosPendientesExportacion = data.pedidos;
                this.pedidosSeleccionadosExportacion.clear();
                this.actualizarContadorSeleccionExportacion();
                this.renderizarTablaExportarExportacion();

                const checkAll = document.getElementById('check-all-wo-exp');
                if (checkAll) checkAll.checked = false;
            } else {
                if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-danger">Error: ${data.error}</td></tr>`;
            }
        } catch (error) {
            console.error('Error cargando pedidos de exportación pendientes:', error);
            const tbody = document.getElementById('tbody-pedidos-exportar-exp');
            if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-danger">Error de conexión</td></tr>`;
        }
    },

    renderizarTablaExportarExportacion: function () {
        const tbody = document.getElementById('tbody-pedidos-exportar-exp');
        if (!tbody) return;

        if (this.pedidosPendientesExportacion.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center py-5 text-muted"><i class="fas fa-plane-departure fa-3x mb-3 text-light"></i><br>No hay pedidos de exportación en estado PENDIENTE</td></tr>';
            return;
        }

        tbody.innerHTML = this.pedidosPendientesExportacion.map(p => `
            <tr onclick="ModuloFacturacion.toggleRowClickExportacion('${p.id}', event)" style="cursor: pointer;">
                <td class="text-center">
                    <div class="form-check d-flex justify-content-center">
                        <input class="form-check-input check-pedido-wo-exp" type="checkbox" value="${p.id}"
                               ${this.pedidosSeleccionadosExportacion.has(p.id) ? 'checked' : ''}
                               onchange="ModuloFacturacion.togglePedidoExportacion('${p.id}'); event.stopPropagation();">
                    </div>
                </td>
                <td>
                    <span class="fw-bold text-info">${p.id}</span>
                </td>
                <td>
                    <div class="fw-bold text-dark">${p.cliente}</div>
                    <small class="text-muted"><i class="fas fa-id-card me-1"></i>${p.nit || 'Sin NIT'}</small>
                </td>
                <td>${p.fecha}</td>
                <td>${p.vendedor}</td>
                <td class="text-center">
                    <span class="badge bg-light text-dark border rounded-pill px-3">${p.items_count}</span>
                </td>
                <td class="text-end fw-bold text-success pe-4">$ ${formatNumber(p.total)}</td>
            </tr>
        `).join('');
    },

    toggleRowClickExportacion: function (id, event) {
        const checkbox = document.querySelector(`.check-pedido-wo-exp[value="${id}"]`);
        if (checkbox) {
            checkbox.checked = !checkbox.checked;
            this.togglePedidoExportacion(id);
        }
    },

    togglePedidoExportacion: function (id) {
        if (this.pedidosSeleccionadosExportacion.has(id)) {
            this.pedidosSeleccionadosExportacion.delete(id);
        } else {
            this.pedidosSeleccionadosExportacion.add(id);
        }
        this.actualizarCheckAllExportacion();
        this.actualizarContadorSeleccionExportacion();
    },

    toggleSelectAllExportacion: function (checkbox) {
        const checkboxes = document.querySelectorAll('.check-pedido-wo-exp');
        checkboxes.forEach(cb => {
            cb.checked = checkbox.checked;
            if (checkbox.checked) {
                this.pedidosSeleccionadosExportacion.add(cb.value);
            } else {
                this.pedidosSeleccionadosExportacion.delete(cb.value);
            }
        });
        this.actualizarContadorSeleccionExportacion();
    },

    actualizarCheckAllExportacion: function () {
        const checkAll = document.getElementById('check-all-wo-exp');
        const checkboxes = document.querySelectorAll('.check-pedido-wo-exp');
        if (checkAll) {
            checkAll.checked = checkboxes.length > 0 && checkboxes.length === this.pedidosSeleccionadosExportacion.size;
        }
    },

    actualizarContadorSeleccionExportacion: function () {
        const countDiv = document.getElementById('wo-exp-selection-count');
        const countSpan = document.getElementById('wo-exp-count-val');
        if (countDiv && countSpan) {
            const count = this.pedidosSeleccionadosExportacion.size;
            countSpan.textContent = count;
            countDiv.style.display = count > 0 ? 'block' : 'none';
        }
    },

    /**
     * Iniciar proceso de exportación para pedidos de EXPORTACIÓN (plantilla
     * WO distinta). Reusa el mismo modal de preview que la nacional,
     * marcándolo con esExportacion=true para que apunte a los endpoints
     * /api/exportar/world-office-exportacion*.
     */
    abrirPreviewWOExportacion: function () {
        const ids = Array.from(this.pedidosSeleccionadosExportacion);
        const consecutivoInicial = document.getElementById('consecutivo-inicial-wo-exp')?.value || '';

        if (ids.length === 0) {
            mostrarNotificacion('Seleccione al menos un pedido de exportación', 'warning');
            return;
        }
        this.mostrarModalPreview(ids, consecutivoInicial, true);
    },

    /**
     * Manejar click en la fila para seleccionar
     */
    toggleRowClick: function (id, event) {
        // Evitar doble toggle si se hace click en el checkbox directamente (ya manejado por stopPropagation)
        const checkbox = document.querySelector(`.check-pedido-wo[value="${id}"]`);
        if (checkbox) {
            checkbox.checked = !checkbox.checked;
            this.togglePedido(id);
        }
    },

    /**
     * Toggle selección individual
     */
    togglePedido: function (id) {
        if (this.pedidosSeleccionados.has(id)) {
            this.pedidosSeleccionados.delete(id);
        } else {
            this.pedidosSeleccionados.add(id);
        }
        this.actualizarCheckAll();
        this.actualizarContadorSeleccion();
    },

    /**
     * Seleccionar/Deseleccionar todos
     */
    toggleSelectAll: function (checkbox) {
        const checkboxes = document.querySelectorAll('.check-pedido-wo');
        checkboxes.forEach(cb => {
            cb.checked = checkbox.checked;
            if (checkbox.checked) {
                this.pedidosSeleccionados.add(cb.value);
            } else {
                this.pedidosSeleccionados.delete(cb.value);
            }
        });
        this.actualizarContadorSeleccion();
    },

    /**
     * Actualizar estado del checkbox maestro
     */
    actualizarCheckAll: function () {
        const checkAll = document.getElementById('check-all-wo');
        const checkboxes = document.querySelectorAll('.check-pedido-wo');
        if (checkAll) {
            checkAll.checked = checkboxes.length > 0 && checkboxes.length === this.pedidosSeleccionados.size;
        }
    },

    /**
     * Actualizar contador visual de selección
     */
    actualizarContadorSeleccion: function () {
        const countDiv = document.getElementById('wo-selection-count');
        const countSpan = document.getElementById('wo-count-val');

        if (countDiv && countSpan) {
            const count = this.pedidosSeleccionados.size;
            countSpan.textContent = count;
            countDiv.style.display = count > 0 ? 'block' : 'none';
        }
    },

    /**
     * Iniciar proceso de exportación
     */
    /**
     * Iniciar proceso de exportación (V2 con Preview)
     */
    abrirPreviewWO: function () {
        const ids = Array.from(this.pedidosSeleccionados);
        const consecutivoInicial = document.getElementById('consecutivo-inicial-wo')?.value || '';

        console.log("DEBUG: [abrirPreviewWO] ids:", ids.length, "consecutivo:", consecutivoInicial);

        if (ids.length === 0) {
            // Si no hay selección, preguntar si exportar todo
            if (typeof Swal !== 'undefined') {
                Swal.fire({
                    title: '¿Exportar todo?',
                    text: "No ha seleccionado pedidos específicos. ¿Desea exportar TODOS los pedidos pendientes?",
                    icon: 'question',
                    showCancelButton: true,
                    confirmButtonColor: '#3085d6',
                    cancelButtonColor: '#d33',
                    confirmButtonText: 'Sí, exportar todo'
                }).then((result) => {
                    if (result.isConfirmed) {
                        this.mostrarModalPreview([]); // Array vacío = Todo
                    }
                });
            } else {
                if (confirm("No ha seleccionado pedidos. ¿Desea exportar TODOS los pendientes?")) {
                    this.mostrarModalPreview([]);
                }
            }
        } else {
            this.mostrarModalPreview(ids, consecutivoInicial);
        }
    },

    mostrarModalPreview: function (ids, consecutivoInicial = '', esExportacion = false) {
        const modal = document.getElementById('modal-preview-wo');
        if (modal) {
            modal.style.display = 'flex'; // Usar FLEX para mantener el centrado
            this.cargarPreviewWO(ids, consecutivoInicial, esExportacion);

            // Guardar IDs y Consecutivo para la descarga final
            modal.dataset.idsToExport = JSON.stringify(ids);
            modal.dataset.consecutivoInicial = consecutivoInicial;
            modal.dataset.esExportacion = esExportacion ? 'true' : 'false';

            const titulo = document.getElementById('modal-preview-wo-titulo');
            if (titulo) titulo.textContent = esExportacion ? 'Vista Previa -- Plantilla de Exportación' : 'Vista Previa -- World Office';

            // Helpers para asignar eventos (evita cloneNode que puede fallar con referencias)
            // YA NO ES NECESARIO: Se asignó onclick directamente en HTML para mayor robustez
        }
    },

    cerrarPreviewWO: function () {
        const modal = document.getElementById('modal-preview-wo');
        if (modal) {
            modal.style.display = 'none';
            modal.dataset.idsToExport = ''; // Limpiar
            modal.dataset.consecutivoInicial = '';
            modal.dataset.esExportacion = 'false';
            const tbody = document.querySelector('#tabla-preview-wo tbody');
            if (tbody) tbody.innerHTML = '';
        }
    },

    cargarPreviewWO: async function (ids, consecutivoInicial = '', esExportacion = false) {
        const tbody = document.querySelector('#tabla-preview-wo tbody');
        const thead = document.querySelector('#tabla-preview-wo thead');

        if (!tbody || !thead) return;

        tbody.innerHTML = '<tr><td colspan="10" class="text-center"><i class="fas fa-spinner fa-spin"></i> Cargando vista previa...</td></tr>';

        const endpoint = esExportacion ? '/api/exportar/world-office-exportacion/preview' : '/api/exportar/world-office/preview';
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ids: ids,
                    consecutivo_inicial: consecutivoInicial
                })
            });
            const result = await response.json();
            console.log("DEBUG: [cargarPreviewWO] Respuesta servidor:", result);

            if (result.success && result.data.length > 0) {
                // Render Headers (Excluyendo columnas auxiliares de auditoría)
                const firstRow = result.data[0];
                const columns = Object.keys(firstRow).filter(col => col !== 'precio_historico' && col !== 'precio_maestro');
                thead.innerHTML = '<tr>' + columns.map(col => `<th>${col}</th>`).join('') + '</tr>';

                // Render Rows
                tbody.innerHTML = result.data.map(row => {
                    const precioHist = row.precio_historico !== undefined && row.precio_historico !== null ? parseFloat(row.precio_historico) : null;
                    const precioMaest = row.precio_maestro !== undefined && row.precio_maestro !== null ? parseFloat(row.precio_maestro) : null;
                    const hasPriceDiff = precioHist !== null && precioMaest !== null && Math.abs(precioHist - precioMaest) > 0.01;

                    const rowStyle = hasPriceDiff 
                        ? 'style="background-color: #fff3cd !important; border-left: 4px solid #fd7e14;" class="table-warning"' 
                        : '';

                    return `<tr ${rowStyle}>` + columns.map(col => {
                        let cellVal = row[col] !== null ? row[col] : '';
                        
                        if (col === 'Detalle: Valor Unitario' && hasPriceDiff) {
                            const tooltip = `Desfase detectado: El cliente pidió a $${precioHist.toLocaleString('es-CO')}, pero el precio maestro actual es $${precioMaest.toLocaleString('es-CO')}`;
                            let displayVal = cellVal;
                            try {
                                const parsedVal = parseFloat(cellVal);
                                if (!isNaN(parsedVal)) {
                                    displayVal = `$ ${parsedVal.toLocaleString('es-CO')}`;
                                }
                            } catch (e) {}
                            cellVal = `<span class="text-danger fw-bold d-inline-flex align-items-center gap-1" title="${tooltip}" style="cursor: help;">
                                ${displayVal} <span style="font-size: 1.1rem;">⚠️</span>
                            </span>`;
                        }
                        
                        return `<td>${cellVal}</td>`;
                    }).join('') + '</tr>';
                }).join('');

            } else {
                tbody.innerHTML = '<tr><td colspan="10" class="text-center text-warning"><i class="fas fa-exclamation-triangle"></i> No hay datos para exportar con los filtros seleccionados.</td></tr>';
            }
        } catch (error) {
            console.error("Error cargando preview:", error);
            tbody.innerHTML = `<tr><td colspan="10" class="text-center text-danger">Error: ${error.message}</td></tr>`;
        }
    },

    descargarExcelWO: async function () {
        const modal = document.getElementById('modal-preview-wo');
        const ids = modal ? JSON.parse(modal.dataset.idsToExport || '[]') : [];
        const consecutivoInicial = modal ? modal.dataset.consecutivoInicial : '';
        const esExportacion = modal ? modal.dataset.esExportacion === 'true' : false;

        console.log("DEBUG: [descargarExcelWO] ids:", ids.length, "consecutivo:", consecutivoInicial, "esExportacion:", esExportacion);

        const btn = document.getElementById('btn-confirmar-exportar-wo');
        if (btn) {
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generando...';
            btn.disabled = true;
        }

        try {
            const endpoint = esExportacion ? '/api/exportar/world-office-exportacion' : '/api/exportar/world-office';
            const res = await fetchData(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ids: ids,
                    consecutivo_inicial: consecutivoInicial
                })
            });
            if (!res) return; // fetchData ya notificó el error de red/HTTP
            if (!res.success || !res.data?.task_id) {
                throw new Error(res.error || 'No se pudo iniciar la exportación');
            }

            if (typeof mostrarNotificacion === 'function') {
                mostrarNotificacion('Generando Excel de World Office... esto puede tardar unos segundos.', 'info');
            }

            const { downloadUrl, actualizadosCount } = await this._sondearExportacionWO(res.data.task_id);

            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = downloadUrl;
            document.body.appendChild(a);
            a.click();
            a.remove();

            let msg = `✅ Archivo descargado con éxito.`;
            if (actualizadosCount > 0) {
                msg += ` Se marcaron ${actualizadosCount} pedidos como EXPORTADO_WO.`;
            }

            if (typeof Swal !== 'undefined') {
                Swal.fire('Exportación Exitosa', msg, 'success');
            } else if (typeof mostrarNotificacion === 'function') {
                mostrarNotificacion(msg, 'success');
            } else {
                alert(msg);
            }

            this.cerrarPreviewWO();
            // Reload the table since the exported orders are now EXPORTADO_WO
            if (esExportacion) {
                this.cargarPedidosPendientesExportacion();
            } else {
                this.cargarPedidosPendientes();
            }
        } catch (error) {
            console.error("Error descarga WO:", error);
            const msgHumano = error.message || 'No se pudo completar la exportación a World Office. Intenta de nuevo.';
            if (typeof Swal !== 'undefined') Swal.fire('Error', msgHumano, 'error');
            else alert(msgHumano);
        } finally {
            if (btn) {
                btn.innerHTML = '<i class="fas fa-check"></i> Confirmar y Descargar';
                btn.disabled = false;
            }
        }
    },

    /**
     * Sondea /api/tasks/status/<task_id> hasta COMPLETED/FAILED. Devuelve
     * {downloadUrl, actualizadosCount} o lanza si falla/expira.
     */
    _sondearExportacionWO: async function (taskId, intentos = 0) {
        const POLL_MS = 2000;
        const MAX_INTENTOS = 150; // ~5 min de margen

        if (intentos >= MAX_INTENTOS) {
            throw new Error('La exportación está tardando demasiado. Intenta de nuevo más tarde.');
        }

        const estado = await fetchData(`/api/tasks/status/${taskId}`);
        if (!estado) throw new Error('Error de conexión consultando el estado de la exportación');
        if (!estado.success) throw new Error(estado.error || 'Error consultando el estado de la exportación');

        const { status, download_url, error, result_meta } = estado.data;
        if (status === 'COMPLETED') {
            return { downloadUrl: download_url, actualizadosCount: result_meta?.actualizados || 0 };
        }
        if (status === 'FAILED') {
            throw new Error(error || 'Error generando el Excel de World Office');
        }

        // PENDING/RUNNING: seguir esperando
        await new Promise(resolve => setTimeout(resolve, POLL_MS));
        return this._sondearExportacionWO(taskId, intentos + 1);
    }
};

// Exportar
window.ModuloFacturacion = ModuloFacturacion;
window.initFacturacion = () => ModuloFacturacion.inicializar();
