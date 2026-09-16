// compras.js — Módulo de Compras a Proveedores Externos (plan 2026-09-15)
// Flujo: Albeiro (Solicitud, sin cantidades, eligiendo el producto de una
// lista) -> Diego (Orden de Compra + cierre con Factura) -> Zoe (Recepción
// + Tránsito Granallado/Zincado). Navegación por pestañas (una por rol) +
// sub-pestañas, mismo patrón que #ensamble-tabs/ModuloEnsamble.switchTab --
// los formularios de creación se abren en un modal (Swal), no quedan
// apilados permanentemente en la página. El backend ya gatea cada endpoint
// con @require_role; esto es solo UX, no el control de acceso real.

const ModuloCompras = {
    productosData: [],
    proveedoresData: [],
    solicitudesPendientesData: [],
    solicitudesSeleccionadas: new Set(),
    activeTab: null,
    lineasModalContador: 0,

    inicializar: async function () {
        console.log('🔧 [Compras] Inicializando módulo...');
        await this.cargarProductos();
        this.aplicarVisibilidadPorRol();
    },

    desactivar: function () {
        this.solicitudesSeleccionadas.clear();
    },

    // ------------------------------------------------------------------
    // Rol / visibilidad de pestañas
    // ------------------------------------------------------------------
    _rolNormalizado: function () {
        if (typeof AuthModule === 'undefined' || !AuthModule.currentUser) return '';
        return AuthModule.normalizeRole(AuthModule.currentUser.rol || AuthModule.currentUser.role);
    },
    _esAdmin: function (rol) {
        return ['ADMIN', 'ADMINISTRACION', 'ADMINISTRADOR', 'GERENCIA'].includes(rol);
    },
    _puedeSolicitar: function (rol) {
        return this._esAdmin(rol) || rol === 'ENSAMBLE';
    },
    _puedeRecepcionar: function (rol) {
        return this._esAdmin(rol) || rol === 'JEFE AUXILIAR INVENTARIO';
    },

    aplicarVisibilidadPorRol: function () {
        const rol = this._rolNormalizado();
        const tabs = [
            { id: 'compras-tab-btn-solicitud', tab: 'solicitud', permitido: this._puedeSolicitar(rol) },
            { id: 'compras-tab-btn-orden', tab: 'orden', permitido: this._esAdmin(rol) },
            { id: 'compras-tab-btn-recepcion', tab: 'recepcion', permitido: this._puedeRecepcionar(rol) },
        ];
        let primeraPermitida = null;
        tabs.forEach(t => {
            const li = document.getElementById(t.id);
            if (li) li.style.display = t.permitido ? '' : 'none';
            if (t.permitido && !primeraPermitida) primeraPermitida = t.tab;
        });
        if (primeraPermitida) this.switchTab(primeraPermitida);
    },

    // ------------------------------------------------------------------
    // Navegación por pestañas (nivel 1) y sub-pestañas (nivel 2)
    // ------------------------------------------------------------------
    switchTab: function (nombre) {
        this.activeTab = nombre;
        document.querySelectorAll('.compras-tab-content').forEach(el => el.style.display = 'none');
        document.getElementById(`compras-tab-${nombre}`)?.style.setProperty('display', 'block');
        document.querySelectorAll('#compras-tabs .nav-link').forEach(el => el.classList.remove('active'));
        document.querySelector(`#compras-tabs .nav-link[data-tab="${nombre}"]`)?.classList.add('active');

        if (nombre === 'solicitud') this.cargarMisSolicitudes();
        if (nombre === 'orden') {
            this.cargarProveedores();
            this.switchSubtabOrden('pendientes');
        }
        if (nombre === 'recepcion') this.switchSubtabRecepcion('pendientes');
    },

    switchSubtabOrden: function (nombre) {
        document.querySelectorAll('#compras-tab-orden .compras-subtab-content').forEach(el => el.style.display = 'none');
        document.getElementById(`compras-orden-sub-${nombre}`)?.style.setProperty('display', 'block');
        document.querySelectorAll('#compras-orden-subtabs .nav-link').forEach(el => el.classList.remove('active'));
        document.querySelector(`#compras-orden-subtabs .nav-link[data-subtab="${nombre}"]`)?.classList.add('active');

        if (nombre === 'pendientes') this.cargarSolicitudesPendientes();
        if (nombre === 'listado') this.cargarOrdenes();
    },

    switchSubtabRecepcion: function (nombre) {
        document.querySelectorAll('#compras-tab-recepcion .compras-subtab-content').forEach(el => el.style.display = 'none');
        document.getElementById(`compras-recepcion-sub-${nombre}`)?.style.setProperty('display', 'block');
        document.querySelectorAll('#compras-recepcion-subtabs .nav-link').forEach(el => el.classList.remove('active'));
        document.querySelector(`#compras-recepcion-subtabs .nav-link[data-subtab="${nombre}"]`)?.classList.add('active');

        if (nombre === 'pendientes') this.cargarPendientesRecepcion();
        if (nombre === 'transito') this.cargarTransito();
        if (nombre === 'recibidas') this.cargarRecibidas();
    },

    // ------------------------------------------------------------------
    // Helper de API (misma convención que el resto de módulos: cookie de
    // sesión, sin Authorization manual)
    // ------------------------------------------------------------------
    _api: async function (path, options) {
        const res = await fetch(path, Object.assign({
            headers: { 'Content-Type': 'application/json' },
        }, options));
        let data = null;
        try { data = await res.json(); } catch (e) { /* respuesta sin cuerpo */ }
        if (!res.ok || (data && data.success === false)) {
            throw new Error((data && (data.error || data.message)) || `Error HTTP ${res.status}`);
        }
        return data ? data.data : null;
    },

    // ------------------------------------------------------------------
    // Catálogo de productos (compartido con Ensamble/PNC/etc, mismo
    // endpoint y misma caché de window.AppState.sharedData.productos) y
    // buscador reutilizable con el helper global renderProductSuggestions
    // (utils.js) -- así Albeiro/Diego ELIGEN un producto real en vez de
    // escribir texto libre.
    // ------------------------------------------------------------------
    cargarProductos: async function () {
        try {
            if (window.AppState?.sharedData?.productos?.length > 0) {
                this.productosData = window.AppState.sharedData.productos;
                return;
            }
            const res = await fetch('/api/productos/listar');
            const data = await res.json();
            this.productosData = data?.items || data?.productos || (Array.isArray(data) ? data : []);
        } catch (e) {
            console.error('[Compras] Error cargando productos:', e);
            this.productosData = [];
        }
    },

    // input/suggestionsDiv/hiddenInput: elementos reales del DOM (sirven
    // igual estando en la página o dentro de un modal Swal ya abierto).
    // Cualquier edición manual del texto borra la selección previa -- no
    // se puede dejar pasar texto libre sin elegir de la lista.
    initAutocompleteProducto: function (input, suggestionsDiv, hiddenInput, onSelectExtra) {
        if (!input || !suggestionsDiv) return;
        input.addEventListener('input', (e) => {
            if (hiddenInput) hiddenInput.value = '';
            const query = e.target.value.toLowerCase();
            if (query.length < 1) { suggestionsDiv.classList.remove('active'); return; }
            const terms = query.split(/\s+/).filter(t => t.length > 0);
            const resultados = this.productosData.filter(p => {
                const codigo = String(p.codigo_sistema || p.codigo || '').toLowerCase();
                const descripcion = String(p.descripcion || '').toLowerCase();
                return terms.every(t => codigo.includes(t) || descripcion.includes(t));
            }).slice(0, 15);
            renderProductSuggestions(suggestionsDiv, resultados, (item) => {
                const codigo = item.codigo_sistema || item.codigo;
                input.value = item.descripcion || codigo;
                if (hiddenInput) hiddenInput.value = codigo;
                suggestionsDiv.classList.remove('active');
                if (onSelectExtra) onSelectExtra(item);
            });
        });
        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });
    },

    // ==================================================================
    // Pestaña Solicitar (Albeiro)
    // ==================================================================
    abrirFormularioSolicitud: async function () {
        const { value: formValues } = await Swal.fire({
            title: 'Señalar qué hace falta comprar',
            html: `
                <div class="text-start">
                    <div class="mb-3" style="position:relative;">
                        <label class="form-label small fw-bold">Producto</label>
                        <input id="modal-sol-descripcion" class="form-control" autocomplete="off" placeholder="Busca por código o nombre...">
                        <input type="hidden" id="modal-sol-codigo">
                        <div id="modal-sol-suggestions" class="autocomplete-suggestions"></div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-bold">Urgencia</label>
                        <select id="modal-sol-urgencia" class="form-select">
                            <option value="NORMAL">Normal</option>
                            <option value="URGENTE">Urgente</option>
                        </select>
                    </div>
                    <div class="mb-1">
                        <label class="form-label small fw-bold">Nota (opcional)</label>
                        <input id="modal-sol-nota" class="form-control" placeholder="Detalle adicional...">
                    </div>
                </div>
            `,
            focusConfirm: false,
            showCancelButton: true,
            confirmButtonText: 'Enviar solicitud',
            didOpen: () => {
                this.initAutocompleteProducto(
                    document.getElementById('modal-sol-descripcion'),
                    document.getElementById('modal-sol-suggestions'),
                    document.getElementById('modal-sol-codigo'),
                );
            },
            preConfirm: () => {
                const item_descripcion = document.getElementById('modal-sol-descripcion').value.trim();
                const codigo_producto = document.getElementById('modal-sol-codigo').value.trim();
                if (!item_descripcion || !codigo_producto) {
                    Swal.showValidationMessage('Busca y selecciona un producto de la lista');
                    return false;
                }
                return {
                    item_descripcion, codigo_producto,
                    urgencia: document.getElementById('modal-sol-urgencia').value,
                    nota: document.getElementById('modal-sol-nota').value.trim(),
                };
            },
        });
        if (!formValues) return;

        try {
            await this._api('/api/compras/solicitudes', { method: 'POST', body: JSON.stringify(formValues) });
            Swal.fire({ icon: 'success', title: 'Solicitud enviada', timer: 1500, showConfirmButton: false });
            await this.cargarMisSolicitudes();
        } catch (e) {
            Swal.fire('No se pudo enviar', e.message, 'error');
        }
    },

    cargarMisSolicitudes: async function () {
        try {
            // Sin ?propias=true a propósito: antes cada quien solo veía lo
            // que él mismo había pedido, así que si dos personas pedían lo
            // mismo por separado nadie se daba cuenta (pedido real
            // 2026-09-16: "en mis solicitudes debería salir la de todos con
            // quien la pidió"). El botón "Cancelar" solo se muestra en las
            // propias -- ver renderSolicitudes.
            const data = await this._api('/api/compras/solicitudes');
            this.renderSolicitudes(data || []);
        } catch (e) {
            console.error('[Compras] Error cargando solicitudes:', e);
        }
    },

    renderSolicitudes: function (lista) {
        const cont = document.getElementById('compras-lista-solicitudes');
        if (!cont) return;
        if (!lista.length) {
            cont.innerHTML = '<div class="text-center py-4 bg-light rounded-4 text-muted">Todavía no hay ninguna solicitud.</div>';
            return;
        }
        const badgeEstado = {
            PENDIENTE: '<span class="badge bg-warning text-dark">Pendiente</span>',
            EN_OC: '<span class="badge bg-success">Ya se pidió</span>',
            RECHAZADA: '<span class="badge bg-danger">Rechazada</span>',
            CANCELADA: '<span class="badge bg-secondary">Cancelada</span>',
        };
        const usuarioActual = (typeof AuthModule !== 'undefined' && AuthModule.currentUser) ? AuthModule.currentUser.username : null;
        const esAdmin = (typeof AuthModule !== 'undefined' && AuthModule.currentUser) ? AuthModule.currentUser.rol === 'ADMIN' : false;
        cont.innerHTML = lista.map(s => `
            <div class="card shadow-sm border-0 rounded-4 p-3">
                <div class="d-flex justify-content-between align-items-start">
                    <strong>${this._esc(s.item_descripcion)}</strong>
                    ${badgeEstado[s.estado] || s.estado}
                </div>
                ${s.codigo_producto ? `<div class="small text-muted">Código: ${this._esc(s.codigo_producto)}</div>` : ''}
                <div class="text-muted small">Pidió: ${this._esc(s.solicitado_por)} · Urgencia: ${s.urgencia} · ${new Date(s.creado_en).toLocaleDateString()}</div>
                ${s.estado === 'EN_OC' ? `<div class="text-success small"><i class="fas fa-check-circle"></i> Ya se pidió: ${this._esc(s.id_oc_vinculada || '')}</div>` : ''}
                ${s.estado === 'RECHAZADA' ? `<div class="text-danger small"><i class="fas fa-times-circle"></i> ${this._esc(s.motivo_rechazo || '')}</div>` : ''}
                ${s.estado === 'PENDIENTE' && (s.solicitado_por === usuarioActual || esAdmin) ? `<button class="btn btn-sm btn-outline-secondary mt-2" onclick="ModuloCompras.cancelarSolicitud(${s.id})">Cancelar</button>` : ''}
            </div>
        `).join('');
    },

    cancelarSolicitud: async function (id) {
        try {
            await this._api(`/api/compras/solicitudes/${id}/cancelar`, { method: 'PATCH' });
            await this.cargarMisSolicitudes();
        } catch (e) {
            Swal.fire('No se pudo cancelar', e.message, 'error');
        }
    },

    // ==================================================================
    // Pestaña Órdenes de Compra (Diego)
    // ==================================================================
    cargarProveedores: async function () {
        if (this.proveedoresData.length) return;
        try {
            this.proveedoresData = await this._api('/api/compras/proveedores') || [];
        } catch (e) {
            console.error('[Compras] Error cargando proveedores:', e);
        }
    },

    cargarSolicitudesPendientes: async function () {
        try {
            const data = await this._api('/api/compras/solicitudes?estado=PENDIENTE');
            this.solicitudesPendientesData = data || [];
            // Limpia selecciones de solicitudes que ya no estén pendientes.
            const idsVigentes = new Set(this.solicitudesPendientesData.map(s => s.id));
            Array.from(this.solicitudesSeleccionadas).forEach(id => {
                if (!idsVigentes.has(id)) this.solicitudesSeleccionadas.delete(id);
            });
            this.renderSolicitudesPendientes();
        } catch (e) {
            console.error('[Compras] Error cargando solicitudes pendientes:', e);
        }
    },

    renderSolicitudesPendientes: function () {
        const lista = this.solicitudesPendientesData || [];
        const cont = document.getElementById('compras-lista-solicitudes-pendientes');
        const badge = document.getElementById('badge-solicitudes-pendientes');
        if (badge) badge.textContent = lista.length;
        if (!cont) return;
        if (!lista.length) {
            cont.innerHTML = '<div class="text-center py-4 bg-light rounded-4 text-muted">No hay solicitudes pendientes.</div>';
            return;
        }
        cont.innerHTML = lista.map(s => `
            <div class="card shadow-sm border-0 rounded-4 p-3">
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" id="chk-sol-${s.id}"
                        ${this.solicitudesSeleccionadas.has(s.id) ? 'checked' : ''}
                        onchange="ModuloCompras.toggleSeleccionSolicitud(${s.id}, this.checked)">
                    <label class="form-check-label" for="chk-sol-${s.id}">
                        <strong>${this._esc(s.item_descripcion)}</strong>
                        ${s.codigo_producto ? `<span class="badge bg-light text-dark ms-1">${this._esc(s.codigo_producto)}</span>` : ''}
                        ${s.urgencia === 'URGENTE' ? '<span class="badge bg-danger ms-1">Urgente</span>' : ''}
                    </label>
                </div>
                <div class="text-muted small">Pidió: ${this._esc(s.solicitado_por)} · ${new Date(s.creado_en).toLocaleDateString()}</div>
                <button class="btn btn-sm btn-outline-danger mt-1" onclick="ModuloCompras.rechazarSolicitud(${s.id})">Rechazar</button>
            </div>
        `).join('');
    },

    toggleSeleccionSolicitud: function (id, marcada) {
        if (marcada) this.solicitudesSeleccionadas.add(id);
        else this.solicitudesSeleccionadas.delete(id);
    },

    rechazarSolicitud: async function (id) {
        const { value: motivo } = await Swal.fire({
            title: 'Rechazar solicitud',
            input: 'text',
            inputLabel: 'Motivo',
            showCancelButton: true,
        });
        if (!motivo) return;
        try {
            await this._api(`/api/compras/solicitudes/${id}/rechazar`, {
                method: 'PATCH', body: JSON.stringify({ motivo }),
            });
            this.solicitudesSeleccionadas.delete(id);
            await this.cargarSolicitudesPendientes();
        } catch (e) {
            Swal.fire('No se pudo rechazar', e.message, 'error');
        }
    },

    // Fila de línea de OC, reutilizable dentro del modal de creación.
    // prefill = {descripcion, codigo_producto, id_solicitud} -- si viene de
    // una solicitud marcada, descripcion/código quedan fijos (Diego ya no
    // tiene que volver a escribirlos, solo llena cantidad/valor).
    _agregarLineaOC: function (contenedorId, prefill) {
        const id = ++this.lineasModalContador;
        const cont = document.getElementById(contenedorId);
        if (!cont) return;
        const soloLectura = !!(prefill && prefill.id_solicitud);
        const fila = document.createElement('div');
        fila.className = 'oc-linea-row border rounded-3 p-2 mb-2 bg-light bg-opacity-50';
        fila.dataset.lineaId = id;
        if (soloLectura) fila.dataset.idSolicitud = prefill.id_solicitud;

        fila.innerHTML = `
            <div class="mb-2" style="position:relative;">
                <label class="form-label small fw-bold mb-1">Descripción / referencia ${soloLectura ? '<span class="badge bg-info">De solicitud</span>' : ''}</label>
                <input type="text" class="oc-linea-descripcion form-control form-control-sm" autocomplete="off" required
                    value="${this._esc((prefill && prefill.descripcion) || '')}"
                    ${soloLectura ? 'readonly' : 'placeholder="Busca el producto..."'}>
                <input type="hidden" class="oc-linea-codigo" value="${this._esc((prefill && prefill.codigo_producto) || '')}">
                ${soloLectura ? '' : '<div class="autocomplete-suggestions oc-linea-suggestions"></div>'}
            </div>
            <div class="row g-2">
                <div class="col-4">
                    <label class="form-label small text-muted mb-1">Cantidad pedida</label>
                    <input type="number" step="0.01" min="0.01" class="oc-linea-cantidad form-control form-control-sm" placeholder="0" required>
                </div>
                <div class="col-3">
                    <label class="form-label small text-muted mb-1">Unidad</label>
                    <input type="text" class="oc-linea-unidad form-control form-control-sm" value="Und.">
                </div>
                <div class="col-4">
                    <label class="form-label small text-muted mb-1">Valor unitario</label>
                    <input type="number" step="0.01" class="oc-linea-valor form-control form-control-sm" placeholder="Opcional">
                </div>
                <div class="col-1 d-flex align-items-end">
                    <button type="button" class="btn btn-sm btn-outline-danger" onclick="ModuloCompras._quitarLineaOC(this)">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
            </div>
        `;
        cont.appendChild(fila);

        if (!soloLectura) {
            this.initAutocompleteProducto(
                fila.querySelector('.oc-linea-descripcion'),
                fila.querySelector('.oc-linea-suggestions'),
                fila.querySelector('.oc-linea-codigo'),
            );
        }
    },

    _quitarLineaOC: function (btn) {
        const fila = btn.closest('.oc-linea-row');
        const idSolicitud = fila?.dataset.idSolicitud;
        if (idSolicitud) this.solicitudesSeleccionadas.delete(parseInt(idSolicitud));
        fila?.remove();
    },

    abrirFormularioOC: async function () {
        if (!this.proveedoresData.length) await this.cargarProveedores();
        this.lineasModalContador = 0;
        const seleccionadas = (this.solicitudesPendientesData || []).filter(s => this.solicitudesSeleccionadas.has(s.id));
        const opcionesProveedor = this.proveedoresData.map(p =>
            `<option value="${this._esc(p.nit)}">${this._esc(p.proveedores)} (${this._esc(p.nit)})</option>`
        ).join('');

        const { value: formValues } = await Swal.fire({
            title: 'Nueva Orden de Compra',
            html: `
                <div class="text-start">
                    <div class="row g-2 mb-2">
                        <div class="col-8">
                            <label class="form-label small fw-bold">Proveedor</label>
                            <select id="modal-oc-proveedor" class="form-select">
                                <option value="">Selecciona el proveedor...</option>
                                ${opcionesProveedor}
                            </select>
                        </div>
                        <div class="col-4">
                            <label class="form-label small fw-bold">Fecha</label>
                            <input id="modal-oc-fecha" type="date" class="form-control" value="${new Date().toISOString().split('T')[0]}">
                        </div>
                    </div>
                    <div class="mb-3">
                        <label class="form-label small fw-bold">Nota (opcional)</label>
                        <input id="modal-oc-nota" class="form-control" placeholder="Detalle adicional...">
                    </div>
                    <label class="form-label small fw-bold mb-1">Líneas</label>
                    <div id="modal-oc-lineas"></div>
                    <button type="button" id="modal-oc-agregar-linea" class="btn btn-sm btn-secondary mt-1">
                        <i class="fas fa-plus"></i> Agregar línea
                    </button>
                </div>
            `,
            width: 620,
            focusConfirm: false,
            showCancelButton: true,
            confirmButtonText: 'Crear Orden de Compra',
            didOpen: () => {
                document.getElementById('modal-oc-agregar-linea').addEventListener('click', () => {
                    this._agregarLineaOC('modal-oc-lineas');
                });
                if (seleccionadas.length) {
                    seleccionadas.forEach(s => this._agregarLineaOC('modal-oc-lineas', {
                        descripcion: s.item_descripcion, codigo_producto: s.codigo_producto, id_solicitud: s.id,
                    }));
                } else {
                    this._agregarLineaOC('modal-oc-lineas');
                }
            },
            preConfirm: () => {
                const proveedor_nit = document.getElementById('modal-oc-proveedor').value;
                const fecha_oc = document.getElementById('modal-oc-fecha').value;
                if (!proveedor_nit || !fecha_oc) {
                    Swal.showValidationMessage('Selecciona el proveedor y la fecha');
                    return false;
                }

                // Bug real reportado (2026-09-16): si se marcan 2+
                // solicitudes, cada una llega precargada como línea -- pero
                // si Diego olvida llenar la cantidad de UNA de ellas (fácil
                // de pasar por alto con varias apiladas), esa línea antes
                // se descartaba EN SILENCIO (el "required" del input no
                // hace nada dentro de un modal Swal, no es un <form> real)
                // y la solicitud quedaba huérfana en "Pendiente" para
                // siempre, sin que nadie se enterara. Ahora se bloquea y se
                // avisa exactamente cuál línea le falta la cantidad, en vez
                // de simplemente omitirla.
                const filas = Array.from(document.querySelectorAll('#modal-oc-lineas .oc-linea-row'));
                const incompletas = [];
                const lineas = [];
                filas.forEach(fila => {
                    const descripcion = fila.querySelector('.oc-linea-descripcion').value.trim();
                    if (!descripcion) return; // fila que nadie tocó (ej. "+Agregar línea" de más) -- se ignora sin avisar
                    const cantidadStr = fila.querySelector('.oc-linea-cantidad').value;
                    const cantidad_pedida = parseFloat(cantidadStr || 0);
                    if (!cantidadStr || cantidad_pedida <= 0) {
                        incompletas.push(descripcion);
                        return;
                    }
                    lineas.push({
                        descripcion,
                        cantidad_pedida,
                        codigo_producto: fila.querySelector('.oc-linea-codigo').value.trim() || null,
                        unidad_medida: fila.querySelector('.oc-linea-unidad').value.trim() || 'Und.',
                        valor_unitario: fila.querySelector('.oc-linea-valor').value ? parseFloat(fila.querySelector('.oc-linea-valor').value) : null,
                        id_solicitud: fila.dataset.idSolicitud ? parseInt(fila.dataset.idSolicitud) : null,
                    });
                });

                if (incompletas.length) {
                    Swal.showValidationMessage(
                        `Falta la cantidad en: ${incompletas.join(', ')} -- complétala o quita esa línea con la papelera antes de crear la orden.`
                    );
                    return false;
                }
                if (!lineas.length) {
                    Swal.showValidationMessage('Agrega al menos una línea con descripción y cantidad');
                    return false;
                }
                return {
                    proveedor_nit, fecha_oc,
                    nota: document.getElementById('modal-oc-nota').value.trim(),
                    lineas,
                };
            },
        });
        if (!formValues) return;

        try {
            const orden = await this._api('/api/compras/ordenes', { method: 'POST', body: JSON.stringify(formValues) });
            Swal.fire({ icon: 'success', title: `OC creada: ${orden.numero_oc}`, timer: 2000, showConfirmButton: false });
            this.solicitudesSeleccionadas.clear();
            await this.cargarSolicitudesPendientes();
        } catch (e) {
            Swal.fire('No se pudo crear la OC', e.message, 'error');
        }
    },

    cargarOrdenes: async function () {
        try {
            const data = await this._api('/api/compras/ordenes');
            await this.renderOrdenes(data || []);
        } catch (e) {
            console.error('[Compras] Error cargando órdenes de compra:', e);
        }
        this.cargarEstadoExportWO();
    },

    cargarEstadoExportWO: async function () {
        try {
            const res = await fetch('/api/wo/compras/exportables');
            const body = await res.json();
            this.renderEstadoExportWO(!!body?.data?.exportacion_habilitada);
        } catch (e) {
            console.error('[Compras] Error consultando estado de exportación a WO:', e);
        }
    },

    renderEstadoExportWO: function (habilitada) {
        const cont = document.getElementById('compras-wo-export-estado');
        if (!cont) return;
        if (habilitada) {
            cont.innerHTML = `
                <div class="alert alert-success d-flex justify-content-between align-items-center py-2 px-3 mb-0">
                    <span><i class="fas fa-check-circle"></i> Exportación a World Office activada.</span>
                    <button class="btn btn-sm btn-outline-secondary" onclick="ModuloCompras.cambiarExportWO(false)">Desactivar</button>
                </div>`;
        } else {
            cont.innerHTML = `
                <div class="alert alert-warning d-flex justify-content-between align-items-center py-2 px-3 mb-0">
                    <span><i class="fas fa-exclamation-triangle"></i> Exportación a World Office desactivada -- el botón "Exportar a WO" no hace nada hasta activarla.</span>
                    <button class="btn btn-sm btn-primary" onclick="ModuloCompras.cambiarExportWO(true)">Activar</button>
                </div>`;
        }
    },

    cambiarExportWO: async function (activar) {
        if (activar) {
            const { isConfirmed } = await Swal.fire({
                title: '¿Activar exportación a World Office?',
                html: 'Desde ahora, al darle "Exportar a WO" a una orden de compra se va a generar un archivo real para subir al importador de WO. Los valores fijos (bodega, forma de pago, IVA, tercero) ya se confirmaron contra una carga de prueba real el 2026-09-15.',
                icon: 'warning', showCancelButton: true, confirmButtonText: 'Sí, activar',
            });
            if (!isConfirmed) return;
        }
        try {
            await this._api('/api/wo/compras/habilitar', {
                method: 'PATCH', body: JSON.stringify({ habilitado: activar }),
            });
            this.cargarEstadoExportWO();
        } catch (e) {
            Swal.fire('No se pudo cambiar el estado', e.message, 'error');
        }
    },

    renderOrdenes: async function (lista) {
        const cont = document.getElementById('compras-lista-ordenes');
        if (!cont) return;
        if (!lista.length) {
            cont.innerHTML = '<div class="text-center py-4 bg-light rounded-4 text-muted">No hay órdenes de compra todavía.</div>';
            return;
        }
        const badgeEstado = {
            ABIERTA: 'bg-warning text-dark', PARCIALMENTE_RECIBIDA: 'bg-info',
            RECIBIDA_TOTAL: 'bg-success', RECHAZADA: 'bg-danger', ANULADA: 'bg-secondary',
        };

        // Se trae el detalle (líneas) de cada OC -- antes la tarjeta solo
        // mostraba encabezado y quedaba "genérica", sin decir qué se pidió
        // ni por cuánto (feedback real 2026-09-16).
        const tarjetas = await Promise.all(lista.map(async (o) => {
            let lineas = [];
            try {
                const detalle = await this._api(`/api/compras/ordenes/${encodeURIComponent(o.numero_oc)}`);
                lineas = detalle.lineas || [];
            } catch (e) { /* si falla el detalle, la tarjeta igual muestra el encabezado */ }

            const total = lineas.reduce((s, l) => s + (l.cantidad_pedida * (l.valor_unitario || 0)), 0);
            const resumenLineas = lineas.map(l =>
                `<div class="small text-truncate">• ${this._esc(l.descripcion)} <span class="text-muted">(${l.cantidad_pedida} ${this._esc(l.unidad_medida || '')})</span></div>`
            ).join('');

            return `
                <div class="card shadow-sm border-0 rounded-4 p-3">
                    <div class="d-flex justify-content-between align-items-start">
                        <strong>${this._esc(o.numero_oc)}</strong>
                        <span class="badge ${badgeEstado[o.estado] || 'bg-secondary'}">${o.estado}</span>
                    </div>
                    <div class="text-muted small">${this._esc(o.proveedor_nombre || o.proveedor_nit)} · ${o.fecha_oc}</div>
                    <div class="small fw-semibold mt-2">${lineas.length} línea(s)${total > 0 ? ' · $' + total.toLocaleString('es-CO') : ''}</div>
                    <div class="mt-1">${resumenLineas}</div>
                    <div class="mt-2 d-flex gap-1 flex-wrap">
                        <button class="btn btn-sm btn-outline-primary" onclick="ModuloCompras.abrirCargarFactura('${this._esc(o.numero_oc)}')">
                            <i class="fas fa-file-invoice-dollar"></i> Factura (FC)
                        </button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="ModuloCompras.exportarOrdenWO('${this._esc(o.numero_oc)}')">
                            <i class="fas fa-file-export"></i> Exportar a WO
                        </button>
                    </div>
                </div>
            `;
        }));
        cont.innerHTML = tarjetas.join('');
    },

    exportarOrdenWO: async function (numero_oc) {
        try {
            const check = await fetch('/api/wo/compras/exportables');
            const checkData = await check.json();
            if (!checkData?.data?.exportacion_habilitada) {
                Swal.fire('Exportación desactivada', 'La exportación a World Office está desactivada manualmente. Actívala desde el botón de arriba, en esta misma pestaña.', 'info');
                return;
            }
            const data = await this._api('/api/wo/compras/exportar', {
                method: 'POST', body: JSON.stringify({ numeros_oc: [numero_oc] }),
            });
            Swal.fire({ icon: 'success', title: 'Exportación en proceso', text: `Tarea: ${data.task_id}`, timer: 2000, showConfirmButton: false });
        } catch (e) {
            Swal.fire('No se pudo exportar', e.message, 'error');
        }
    },

    abrirCargarFactura: async function (numero_oc) {
        try {
            const detalle = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}`);
            const filasHtml = detalle.lineas.map(l => `
                <div class="mb-2">
                    <label class="form-label small">${this._esc(l.descripcion)} <span class="text-muted">(recibido: ${l.cantidad_recibida_acumulada})</span></label>
                    <input type="number" step="0.01" class="form-control form-control-sm fc-linea-cantidad" data-id-linea="${l.id}" placeholder="Cantidad facturada">
                </div>
            `).join('');

            const { value: formValues } = await Swal.fire({
                title: `Cargar Factura (FC) — ${numero_oc}`,
                html: `
                    <div class="text-start">
                        <div class="row g-2 mb-3">
                            <div class="col-7">
                                <label class="form-label small fw-bold">Número de factura</label>
                                <input id="fc-numero" class="form-control" placeholder="Ej: FC-0001">
                            </div>
                            <div class="col-5">
                                <label class="form-label small fw-bold">Fecha</label>
                                <input id="fc-fecha" type="date" class="form-control">
                            </div>
                        </div>
                        ${filasHtml}
                    </div>
                `,
                focusConfirm: false,
                showCancelButton: true,
                preConfirm: () => {
                    const lineas = Array.from(document.querySelectorAll('.fc-linea-cantidad'))
                        .filter(i => i.value)
                        .map(i => ({ id_linea_oc: parseInt(i.dataset.idLinea), cantidad_facturada: parseFloat(i.value) }));
                    return {
                        numero_factura: document.getElementById('fc-numero').value.trim(),
                        fecha_factura: document.getElementById('fc-fecha').value,
                        lineas,
                    };
                },
            });

            if (!formValues || !formValues.numero_factura || !formValues.lineas.length) return;

            const factura = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}/factura`, {
                method: 'POST', body: JSON.stringify(formValues),
            });
            const icono = factura.estado_conciliacion === 'COINCIDE' ? 'success' : 'warning';
            Swal.fire(
                factura.estado_conciliacion === 'COINCIDE' ? 'Factura conciliada' : 'Factura con discrepancia',
                `Estado: ${factura.estado_conciliacion}`,
                icono
            );
        } catch (e) {
            Swal.fire('No se pudo cargar la factura', e.message, 'error');
        }
    },

    // ==================================================================
    // Pestaña Recepción (Zoe)
    // ==================================================================
    cargarPendientesRecepcion: async function () {
        try {
            const data = await this._api('/api/compras/ordenes/pendientes_recepcion');
            await this.renderPendientesRecepcion(data || []);
        } catch (e) {
            console.error('[Compras] Error cargando OC pendientes de recepción:', e);
        }
    },

    renderPendientesRecepcion: async function (lista) {
        const cont = document.getElementById('compras-lista-recepcion');
        if (!cont) return;
        if (!lista.length) {
            cont.innerHTML = '<div class="text-center py-4 bg-light rounded-4 text-muted">No hay órdenes de compra pendientes de recepción.</div>';
            return;
        }

        const tarjetas = await Promise.all(lista.map(async (o) => {
            let detalle;
            try {
                detalle = await this._api(`/api/compras/ordenes/${encodeURIComponent(o.numero_oc)}`);
            } catch (e) {
                return '';
            }
            // Sin tabla a propósito: una tabla de 4 columnas no cabe en una
            // tarjeta angosta sin scroll horizontal (bug real reportado
            // 2026-09-16). Cada línea es su nombre + 3 badges que envuelven
            // libremente -- nunca se corta ni pide scroll.
            const filas = detalle.lineas.map(l => `
                <div class="border-bottom py-2">
                    <div class="small fw-semibold">${this._esc(l.descripcion)}</div>
                    <div class="d-flex gap-1 flex-wrap mt-1">
                        <span class="badge bg-light text-dark border">Pedido: ${l.cantidad_pedida}</span>
                        <span class="badge bg-light text-dark border">Recibido: ${l.cantidad_recibida_acumulada}</span>
                        <span class="badge ${l.pendiente > 0 ? 'bg-warning text-dark' : 'bg-success'}">Pendiente: ${l.pendiente}</span>
                        ${l.dentro_tolerancia_baja ? '<span class="badge bg-info text-dark" title="Faltan pocas unidades, dentro de la tolerancia -- ya no bloquea el cierre">Dentro de tolerancia</span>' : ''}
                    </div>
                </div>
            `).join('');
            return `
                <div class="card shadow-sm border-0 rounded-4 p-3">
                    <div class="d-flex justify-content-between align-items-start">
                        <strong>${this._esc(o.numero_oc)}</strong>
                        <span class="badge bg-warning text-dark">${o.estado}</span>
                    </div>
                    <div class="text-muted small">${this._esc(o.proveedor_nombre || o.proveedor_nit)} · ${o.fecha_oc}</div>
                    <div class="mt-2">${filas}</div>
                    <div class="d-flex gap-1 flex-wrap mt-2">
                        <button class="btn btn-sm btn-primary" onclick="ModuloCompras.abrirRegistrarRecepcion('${this._esc(o.numero_oc)}')">
                            <i class="fas fa-truck-loading"></i> Registrar recepción
                        </button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="ModuloCompras.verRecepcionesParaTransito('${this._esc(o.numero_oc)}')">
                            <i class="fas fa-shield-alt"></i> Enviar lote a maquila
                        </button>
                    </div>
                </div>
            `;
        }));
        cont.innerHTML = tarjetas.join('');
    },

    cargarRecibidas: async function () {
        try {
            const data = await this._api('/api/compras/ordenes/recibidas');
            this.renderRecibidas(data || []);
        } catch (e) {
            console.error('[Compras] Error cargando OC recibidas:', e);
        }
    },

    renderRecibidas: function (lista) {
        const cont = document.getElementById('compras-lista-recibidas');
        if (!cont) return;
        if (!lista.length) {
            cont.innerHTML = '<div class="text-center py-4 bg-light rounded-4 text-muted">Todavía no hay OC recibidas por completo ni rechazadas.</div>';
            return;
        }
        const badgeEstado = { RECIBIDA_TOTAL: 'bg-success', RECHAZADA: 'bg-danger' };
        cont.innerHTML = lista.map(o => `
            <div class="card shadow-sm border-0 rounded-4 p-3">
                <div class="d-flex justify-content-between align-items-start">
                    <strong>${this._esc(o.numero_oc)}</strong>
                    <span class="badge ${badgeEstado[o.estado] || 'bg-secondary'}">${o.estado}</span>
                </div>
                <div class="text-muted small">${this._esc(o.proveedor_nombre || o.proveedor_nit)} · ${o.fecha_oc}</div>
            </div>
        `).join('');
    },

    abrirRegistrarRecepcion: async function (numero_oc) {
        try {
            const detalle = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}`);
            const hoy = new Date().toISOString().split('T')[0];
            // Fecha por línea -- los productos de una misma OC no siempre
            // llegan el mismo día (pedido real 2026-09-16). Cada input
            // arranca en "hoy" (mismo default que la fecha general) y Zoe
            // solo la cambia en la línea puntual que llegó otro día.
            const filasHtml = detalle.lineas.map(l => `
                <div class="row g-2 mb-2 align-items-end">
                    <div class="col-12"><label class="form-label small mb-1">${this._esc(l.descripcion)} <span class="text-muted">(pendiente: ${l.pendiente})</span></label></div>
                    <div class="col-5">
                        <input type="number" step="0.01" class="form-control form-control-sm rec-linea-recibida" data-id-linea="${l.id}" placeholder="Cantidad recibida">
                    </div>
                    <div class="col-4">
                        <input type="number" step="0.01" class="form-control form-control-sm rec-linea-rechazada" data-id-linea="${l.id}" placeholder="Cantidad rechazada">
                    </div>
                    <div class="col-3">
                        <input type="date" class="form-control form-control-sm rec-linea-fecha" data-id-linea="${l.id}" value="${hoy}" title="Fecha en que llegó este producto">
                    </div>
                </div>
            `).join('');

            const { value: formValues } = await Swal.fire({
                title: `Registrar recepción — ${numero_oc}`,
                html: `
                    <div class="text-start">
                        <div class="row g-2 mb-3">
                            <div class="col-6">
                                <label class="form-label small fw-bold">Fecha general</label>
                                <input id="rec-fecha" type="date" class="form-control" value="${hoy}">
                            </div>
                            <div class="col-6">
                                <label class="form-label small fw-bold">Estado</label>
                                <select id="rec-estado" class="form-select">
                                    <option value="RECIBIDA_TOTAL">Recibida total</option>
                                    <option value="RECIBIDA_PARCIAL">Recibida parcial</option>
                                    <option value="RECHAZADA">Rechazada</option>
                                </select>
                            </div>
                        </div>
                        <div class="text-muted small mb-2">Si algún producto llegó otro día, cambia su fecha en la casilla junto a la cantidad.</div>
                        ${filasHtml}
                    </div>
                `,
                focusConfirm: false,
                showCancelButton: true,
                width: 650,
                preConfirm: () => {
                    if (!document.getElementById('rec-fecha').value) {
                        Swal.showValidationMessage('Falta la fecha de recepción');
                        return false;
                    }
                    const lineas = Array.from(document.querySelectorAll('.rec-linea-recibida'))
                        .map(i => {
                            const idLinea = i.dataset.idLinea;
                            const rechazada = document.querySelector(`.rec-linea-rechazada[data-id-linea="${idLinea}"]`);
                            const fecha = document.querySelector(`.rec-linea-fecha[data-id-linea="${idLinea}"]`);
                            return {
                                id_linea_oc: parseInt(idLinea),
                                cantidad_recibida: parseFloat(i.value || 0),
                                cantidad_rechazada: parseFloat((rechazada && rechazada.value) || 0),
                                fecha_recepcion: (fecha && fecha.value) || null,
                            };
                        })
                        .filter(l => l.cantidad_recibida > 0 || l.cantidad_rechazada > 0);
                    // Si deja todo en blanco no hay nada que registrar -- antes
                    // esto cerraba el modal en silencio como si hubiera
                    // guardado algo (bug real reportado 2026-09-16, misma
                    // familia del silent-drop ya corregido en creación de OC).
                    if (!lineas.length) {
                        Swal.showValidationMessage('Escribe cantidad recibida o rechazada en al menos una línea -- si no llegó nada todavía, cierra sin confirmar');
                        return false;
                    }
                    return {
                        fecha_recepcion: document.getElementById('rec-fecha').value,
                        estado_recepcion: document.getElementById('rec-estado').value,
                        lineas,
                    };
                },
            });

            if (!formValues) return;

            const resultado = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}/recepciones`, {
                method: 'POST', body: JSON.stringify(formValues),
            });

            if (resultado.lineas_con_exceso_tolerancia?.length) {
                await Swal.fire('Recepción registrada con exceso', 'Se avisó a Diego: llegó más de lo pedido por encima de la tolerancia.', 'warning');
            } else {
                await Swal.fire({ icon: 'success', title: 'Recepción registrada', timer: 1500, showConfirmButton: false });
            }

            await this.cargarPendientesRecepcion();
            await this.ofrecerEnvioATransito(numero_oc, resultado.lineas || []);
        } catch (e) {
            Swal.fire('No se pudo registrar la recepción', e.message, 'error');
        }
    },

    // ------------------------------------------------------------------
    // Tránsito externo (Granallado / Zincado)
    // ------------------------------------------------------------------
    ofrecerEnvioATransito: async function (numero_oc, lineasCreadas) {
        const conRecibo = lineasCreadas.filter(l => l.cantidad_recibida > 0);
        if (!conRecibo.length) return;

        const { isConfirmed } = await Swal.fire({
            title: '¿Enviar algo a maquila externa?',
            text: `Se recibieron ${conRecibo.length} línea(s) en ${numero_oc}. ¿Alguna va a Granallado/Zincado ahora?`,
            showCancelButton: true,
            confirmButtonText: 'Sí, elegir',
            cancelButtonText: 'No, más tarde',
        });
        if (!isConfirmed) return;
        this.verRecepcionesParaTransito(numero_oc);
    },

    verRecepcionesParaTransito: async function (numero_oc) {
        try {
            const recepciones = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}/recepciones`);
            const lineas = (recepciones || []).flatMap(r => r.lineas.map(l => ({ ...l, fecha: l.fecha_recepcion || r.recepcion.fecha_recepcion })))
                .filter(l => l.cantidad_recibida > 0);

            if (!lineas.length) {
                Swal.fire('Sin lotes recibidos', 'Esta OC todavía no tiene cantidades recibidas.', 'info');
                return;
            }

            const html = lineas.map(l => `
                <div class="d-flex justify-content-between align-items-center border-bottom py-1">
                    <span class="small">Lote del ${l.fecha}: ${l.cantidad_recibida} recibido</span>
                    <button class="btn btn-sm btn-outline-primary" onclick="ModuloCompras.enviarATransito(${l.id}, ${l.cantidad_recibida})">Enviar</button>
                </div>
            `).join('');

            Swal.fire({ title: `Lotes recibidos — ${numero_oc}`, html, width: 500 });
        } catch (e) {
            Swal.fire('No se pudo consultar', e.message, 'error');
        }
    },

    cargarTransito: async function () {
        try {
            const data = await this._api('/api/compras/transito');
            this.renderTransito(data || []);
        } catch (e) {
            console.error('[Compras] Error cargando tránsito externo:', e);
        }
    },

    renderTransito: function (lista) {
        const cont = document.getElementById('compras-lista-transito');
        if (!cont) return;
        if (!lista.length) {
            cont.innerHTML = '<div class="text-center py-4 bg-light rounded-4 text-muted">No hay lotes en tránsito hacia Granallado/Zincado.</div>';
            return;
        }
        const colorEstado = { ENVIADO: 'bg-warning text-dark', EN_PROCESO: 'bg-info', RETORNADO: 'bg-success', RETORNADO_PARCIAL: 'bg-danger' };
        cont.innerHTML = lista.map(t => `
            <div class="card shadow-sm border-0 rounded-4 p-3">
                <div class="d-flex justify-content-between align-items-start">
                    <strong>${t.proceso}</strong>
                    <span class="badge ${colorEstado[t.estado] || 'bg-secondary'}">${t.estado}</span>
                </div>
                <div class="text-muted small">Enviado: ${t.cantidad_enviada} · ${t.fecha_envio}</div>
                ${t.cantidad_retornada !== null ? `<div class="text-muted small">Retornado: ${t.cantidad_retornada}${t.diferencia_envio_retorno ? ` (diferencia: ${t.diferencia_envio_retorno})` : ''}</div>` : ''}
                <div class="mt-2 d-flex gap-1 flex-wrap">
                    ${t.estado === 'ENVIADO' ? `<button class="btn btn-sm btn-outline-info" onclick="ModuloCompras.marcarEnProceso(${t.id})">Marcar En proceso</button>` : ''}
                    ${['ENVIADO', 'EN_PROCESO'].includes(t.estado) ? `<button class="btn btn-sm btn-outline-success" onclick="ModuloCompras.registrarRetornoTransito(${t.id}, ${t.cantidad_enviada})">Registrar retorno</button>` : ''}
                </div>
            </div>
        `).join('');
    },

    marcarEnProceso: async function (idTransito) {
        try {
            await this._api(`/api/compras/transito/${idTransito}/estado`, {
                method: 'PATCH', body: JSON.stringify({ estado: 'EN_PROCESO' }),
            });
            await this.cargarTransito();
        } catch (e) {
            Swal.fire('No se pudo actualizar', e.message, 'error');
        }
    },

    enviarATransito: async function (idLineaRecepcion, cantidadDisponible) {
        const { value: formValues } = await Swal.fire({
            title: 'Enviar a maquila externa',
            html: `
                <div class="text-start">
                    <div class="mb-2">
                        <label class="form-label small fw-bold">Proceso</label>
                        <select id="tr-proceso" class="form-select">
                            <option value="GRANALLADO">Granallado</option>
                            <option value="ZINCADO">Zincado</option>
                        </select>
                    </div>
                    <div class="row g-2">
                        <div class="col-6">
                            <label class="form-label small fw-bold">Cantidad a enviar</label>
                            <input id="tr-cantidad" type="number" step="0.01" max="${cantidadDisponible}" class="form-control" placeholder="Máx. ${cantidadDisponible}">
                        </div>
                        <div class="col-6">
                            <label class="form-label small fw-bold">Fecha</label>
                            <input id="tr-fecha" type="date" class="form-control" value="${new Date().toISOString().split('T')[0]}">
                        </div>
                    </div>
                </div>
            `,
            showCancelButton: true,
            preConfirm: () => ({
                proceso: document.getElementById('tr-proceso').value,
                cantidad_enviada: parseFloat(document.getElementById('tr-cantidad').value || 0),
                fecha_envio: document.getElementById('tr-fecha').value,
            }),
        });
        if (!formValues || formValues.cantidad_enviada <= 0) return;

        try {
            await this._api(`/api/compras/recepciones/${idLineaRecepcion}/transito`, {
                method: 'POST', body: JSON.stringify(formValues),
            });
            Swal.fire({ icon: 'success', title: 'Enviado', timer: 1500, showConfirmButton: false });
            await this.cargarTransito();
        } catch (e) {
            Swal.fire('No se pudo enviar', e.message, 'error');
        }
    },

    registrarRetornoTransito: async function (idTransito, cantidadEnviada) {
        const { value: formValues } = await Swal.fire({
            title: 'Registrar retorno',
            html: `
                <div class="text-start">
                    <div class="mb-2">
                        <label class="form-label small fw-bold">Cantidad retornada <span class="text-muted">(de ${cantidadEnviada} enviado)</span></label>
                        <input id="tr-retorno-cantidad" type="number" step="0.01" class="form-control" placeholder="0">
                    </div>
                    <div>
                        <label class="form-label small fw-bold">Fecha</label>
                        <input id="tr-retorno-fecha" type="date" class="form-control" value="${new Date().toISOString().split('T')[0]}">
                    </div>
                </div>
            `,
            showCancelButton: true,
            preConfirm: () => ({
                cantidad_retornada: parseFloat(document.getElementById('tr-retorno-cantidad').value || 0),
                fecha_retorno: document.getElementById('tr-retorno-fecha').value,
            }),
        });
        if (!formValues) return;

        try {
            await this._api(`/api/compras/transito/${idTransito}/retorno`, {
                method: 'POST', body: JSON.stringify(formValues),
            });
            Swal.fire({ icon: 'success', title: 'Retorno registrado', timer: 1500, showConfirmButton: false });
            await this.cargarTransito();
        } catch (e) {
            Swal.fire('No se pudo registrar el retorno', e.message, 'error');
        }
    },

    // ------------------------------------------------------------------
    _esc: function (str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    },
};

window.ModuloCompras = ModuloCompras;
