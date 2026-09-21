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
    misSolicitudesData: [],

    inicializar: async function () {
        console.log('🔧 [Compras] Inicializando módulo...');
        await this.cargarProductos();
        this.aplicarVisibilidadPorRol();
    },

    desactivar: function () {
        this.solicitudesSeleccionadas.clear();
        this.cerrarTrazabilidad();
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

    // Líneas (con acumulado/pendiente/tolerancia) de varias OC en UNA sola
    // petición -- antes cada tarjeta de una lista hacía su propio fetch de
    // detalle, disparando una petición HTTP por tarjeta cada vez que se
    // abría la pestaña (lag real reportado 2026-09-16 con varias decenas
    // de OC). Nunca lanza: si falla, cada card cae de nuevo a "sin líneas".
    _lineasBatch: async function (numerosOc) {
        if (!numerosOc.length) return {};
        try {
            return await this._api('/api/compras/ordenes/lineas_batch', {
                method: 'POST', body: JSON.stringify({ numeros_oc: numerosOc }),
            }) || {};
        } catch (e) {
            console.error('[Compras] Error consultando líneas en lote:', e);
            return {};
        }
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
    // Registro de pares input/suggestionsDiv activos, para el listener
    // delegado de "cerrar al hacer click afuera" -- ver _bindOutsideClickOnce.
    _autocompleteRegistrados: [],

    // initAutocompleteProducto() se llama cada vez que se abre un modal
    // (Solicitud, Orden de Compra...), con elementos nuevos del DOM de Swal
    // cada vez. Antes ligaba un document.addEventListener('click', ...) por
    // llamada -- cada modal abierto en el turno dejaba un listener global
    // más, permanente, apuntando a nodos que ya ni existen. Ahora se liga
    // UNA sola vez y se revisa una lista de pares activos.
    _bindOutsideClickOnce: function () {
        if (this._outsideClickBound) return;
        this._outsideClickBound = true;
        document.addEventListener('click', (e) => {
            // Purga pares cuyo suggestionsDiv ya no está en el DOM (modal cerrado)
            this._autocompleteRegistrados = this._autocompleteRegistrados.filter(
                ({ suggestionsDiv }) => document.body.contains(suggestionsDiv)
            );
            this._autocompleteRegistrados.forEach(({ input, suggestionsDiv }) => {
                if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                    suggestionsDiv.classList.remove('active');
                }
            });
        });
    },

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
        this._autocompleteRegistrados.push({ input, suggestionsDiv });
        this._bindOutsideClickOnce();
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
            this.misSolicitudesData = data || [];
            this.renderSolicitudes(this.misSolicitudesData);
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
        const esAdmin = this._esAdmin(this._rolNormalizado());
        const borde = { PENDIENTE: 'border-start border-4 border-warning', EN_OC: 'border-start border-4 border-success', RECHAZADA: 'border-start border-4 border-danger', CANCELADA: 'border-start border-4 border-secondary' };
        cont.innerHTML = lista.map(s => `
            <div class="card shadow-sm border-0 ${borde[s.estado] || ''} rounded-4 p-3" style="cursor:pointer;" onclick="ModuloCompras.abrirTrazabilidadDesdeSolicitud(${s.id})">
                <div class="d-flex justify-content-between align-items-start">
                    <strong>${this._esc(s.item_descripcion)}</strong>
                    ${badgeEstado[s.estado] || s.estado}
                </div>
                ${s.codigo_producto ? `<div class="small text-muted">Código: ${this._esc(s.codigo_producto)}</div>` : ''}
                <div class="mt-1">
                    ${s.urgencia === 'URGENTE' ? '<span class="badge bg-danger me-1"><i class="fas fa-bolt"></i> Urgente</span>' : '<span class="badge bg-light text-dark border me-1">Normal</span>'}
                    <span class="small text-muted"><i class="fas fa-user"></i> ${this._esc(s.solicitado_por)}</span>
                </div>
                <div class="text-muted small mt-1"><i class="far fa-clock"></i> ${this._diasDesde(s.creado_en)} · ${new Date(s.creado_en).toLocaleDateString()}</div>
                ${s.estado === 'EN_OC' ? `<div class="text-success small"><i class="fas fa-check-circle"></i> Ya se pidió: ${this._esc(s.id_oc_vinculada || '')}</div>` : ''}
                ${s.estado === 'RECHAZADA' ? `<div class="text-danger small"><i class="fas fa-times-circle"></i> ${this._esc(s.motivo_rechazo || '')}</div>` : ''}
                ${s.estado === 'PENDIENTE' && (s.solicitado_por === usuarioActual || esAdmin) ? `<button class="btn btn-sm btn-outline-secondary mt-2" onclick="event.stopPropagation(); ModuloCompras.cancelarSolicitud(${s.id})">Cancelar</button>` : ''}
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
            <div class="card shadow-sm border-0 ${s.urgencia === 'URGENTE' ? 'border-start border-4 border-danger' : ''} rounded-4 p-3">
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" id="chk-sol-${s.id}"
                        ${this.solicitudesSeleccionadas.has(s.id) ? 'checked' : ''}
                        onchange="ModuloCompras.toggleSeleccionSolicitud(${s.id}, this.checked)">
                    <label class="form-check-label" for="chk-sol-${s.id}">
                        <strong>${this._esc(s.item_descripcion)}</strong>
                        ${s.codigo_producto ? `<span class="badge bg-light text-dark ms-1">${this._esc(s.codigo_producto)}</span>` : ''}
                        ${s.urgencia === 'URGENTE' ? '<span class="badge bg-danger ms-1"><i class="fas fa-bolt"></i> Urgente</span>' : ''}
                    </label>
                </div>
                <div class="text-muted small"><i class="fas fa-user"></i> ${this._esc(s.solicitado_por)} · <i class="far fa-clock"></i> ${this._diasDesde(s.creado_en)}</div>
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
        const bordeEstado = {
            ABIERTA: 'border-warning', PARCIALMENTE_RECIBIDA: 'border-info',
            RECIBIDA_TOTAL: 'border-success', RECHAZADA: 'border-danger', ANULADA: 'border-secondary',
        };

        // Se trae el detalle (líneas) de todas las OC en UNA sola petición
        // en lote -- antes la tarjeta pedía su propio detalle por separado
        // (feedback real 2026-09-16: tarjetas "genéricas" primero, lag por
        // el N+1 después).
        const lineasPorOc = await this._lineasBatch(lista.map(o => o.numero_oc));
        const tarjetas = lista.map((o) => {
            const lineas = lineasPorOc[o.numero_oc] || [];

            const total = lineas.reduce((s, l) => s + (l.cantidad_pedida * (l.valor_unitario || 0)), 0);
            const resumenLineas = lineas.map(l =>
                `<div class="small text-truncate">• ${this._esc(l.descripcion)} <span class="text-muted">(${l.cantidad_pedida} ${this._esc(l.unidad_medida || '')})</span></div>`
            ).join('');

            return `
                <div class="card shadow-sm border-0 border-start border-4 ${bordeEstado[o.estado] || 'border-secondary'} rounded-4 p-3" style="cursor:pointer;" onclick="ModuloCompras.abrirTrazabilidad('${this._esc(o.numero_oc)}')">
                    <div class="d-flex justify-content-between align-items-start">
                        <strong>${this._esc(o.numero_oc)}</strong>
                        <span class="badge ${badgeEstado[o.estado] || 'bg-secondary'}">${o.estado}</span>
                    </div>
                    <div class="fw-semibold small"><i class="fas fa-truck-loading text-muted"></i> ${this._esc(o.proveedor_nombre || o.proveedor_nit)}</div>
                    <div class="text-muted small"><i class="far fa-calendar"></i> ${o.fecha_oc} · <i class="fas fa-user"></i> ${this._esc(o.creado_por || '')} · ${this._diasDesde(o.creado_en)}</div>
                    <div class="small fw-semibold mt-2">${lineas.length} línea(s)${total > 0 ? ' · <span class="text-success">$' + total.toLocaleString('es-CO') + '</span>' : ''}</div>
                    <div class="mt-1">${resumenLineas}</div>
                    <div class="mt-2 d-flex gap-1 flex-wrap">
                        <button class="btn btn-sm btn-outline-secondary" onclick="event.stopPropagation(); ModuloCompras.exportarOrdenWO('${this._esc(o.numero_oc)}')">
                            <i class="fas fa-file-export"></i> Exportar a WO
                        </button>
                    </div>
                </div>
            `;
        });
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
            Swal.fire({ title: 'Generando el archivo…', allowOutsideClick: false, didOpen: () => Swal.showLoading() });
            this._esperarTareaExportWO(data.task_id);
        } catch (e) {
            Swal.fire('No se pudo exportar', e.message, 'error');
        }
    },

    // El archivo se genera en background (WoExportComprasService.generar_task) --
    // faltaba este sondeo: antes el botón lanzaba la tarea y se quedaba ahí,
    // sin avisar cuándo terminaba ni disparar la descarga. Mismo patrón que
    // ModuloExportacionWO.esperarTarea (exportacion_wo.js) para OP.
    _esperarTareaExportWO: function (taskId, intento = 0) {
        clearTimeout(this._pollTimerExportWO);
        if (intento > 60) {
            Swal.fire('Está tardando demasiado', 'El archivo no terminó de generarse. Intenta de nuevo.', 'warning');
            return;
        }
        this._pollTimerExportWO = setTimeout(async () => {
            try {
                const estado = await this._api(`/api/tasks/status/${taskId}`);
                if (estado.status === 'COMPLETED') {
                    Swal.close();
                    window.location.href = estado.download_url;
                    await this.cargarOrdenes();   // refresca exportada_wo en las tarjetas
                } else if (estado.status === 'FAILED') {
                    Swal.fire('No se pudo generar', estado.error || 'Error desconocido', 'error');
                } else {
                    this._esperarTareaExportWO(taskId, intento + 1);
                }
            } catch (e) {
                Swal.fire('No se pudo generar', e.message, 'error');
            }
        }, 1000);
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

        const lineasPorOc = await this._lineasBatch(lista.map(o => o.numero_oc));
        const tarjetas = lista.map((o) => {
            const lineasOc = lineasPorOc[o.numero_oc] || [];
            // Sin tabla a propósito: una tabla de 4 columnas no cabe en una
            // tarjeta angosta sin scroll horizontal (bug real reportado
            // 2026-09-16). Cada línea es su nombre + 3 badges que envuelven
            // libremente -- nunca se corta ni pide scroll.
            const filas = lineasOc.map(l => `
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
            const bordeEstado = o.estado === 'PARCIALMENTE_RECIBIDA' ? 'border-info' : 'border-warning';
            const badgeEstadoColor = o.estado === 'PARCIALMENTE_RECIBIDA' ? 'bg-info' : 'bg-warning text-dark';
            return `
                <div class="card shadow-sm border-0 border-start border-4 ${bordeEstado} rounded-4 p-3" style="cursor:pointer;" onclick="ModuloCompras.abrirTrazabilidad('${this._esc(o.numero_oc)}', 'recepcion')">
                    <div class="d-flex justify-content-between align-items-start">
                        <strong>${this._esc(o.numero_oc)}</strong>
                        <span class="badge ${badgeEstadoColor}">${o.estado}</span>
                    </div>
                    <div class="fw-semibold small"><i class="fas fa-truck-loading text-muted"></i> ${this._esc(o.proveedor_nombre || o.proveedor_nit)}</div>
                    <div class="text-muted small"><i class="far fa-calendar"></i> ${o.fecha_oc} · <i class="far fa-clock"></i> pedida ${this._diasDesde(o.creado_en)}</div>
                    <div class="mt-2">${filas}</div>
                </div>
            `;
        });
        cont.innerHTML = tarjetas.join('');
    },

    cargarRecibidas: async function () {
        try {
            const data = await this._api('/api/compras/ordenes/recibidas');
            await this.renderRecibidas(data || []);
        } catch (e) {
            console.error('[Compras] Error cargando OC recibidas:', e);
        }
    },

    renderRecibidas: async function (lista) {
        const cont = document.getElementById('compras-lista-recibidas');
        if (!cont) return;
        if (!lista.length) {
            cont.innerHTML = '<div class="text-center py-4 bg-light rounded-4 text-muted">Todavía no hay OC recibidas por completo ni rechazadas.</div>';
            return;
        }
        const badgeEstado = { RECIBIDA_TOTAL: 'bg-success', RECHAZADA: 'bg-danger' };
        const bordeEstado = { RECIBIDA_TOTAL: 'border-success', RECHAZADA: 'border-danger' };
        const lineasPorOc = await this._lineasBatch(lista.map(o => o.numero_oc));
        const tarjetas = lista.map((o) => {
            const numLineas = (lineasPorOc[o.numero_oc] || []).length;
            return `
                <div class="card shadow-sm border-0 border-start border-4 ${bordeEstado[o.estado] || 'border-secondary'} rounded-4 p-3" style="cursor:pointer;" onclick="ModuloCompras.abrirTrazabilidad('${this._esc(o.numero_oc)}')">
                    <div class="d-flex justify-content-between align-items-start">
                        <strong>${this._esc(o.numero_oc)}</strong>
                        <span class="badge ${badgeEstado[o.estado] || 'bg-secondary'}">${o.estado}</span>
                    </div>
                    <div class="fw-semibold small"><i class="fas fa-truck-loading text-muted"></i> ${this._esc(o.proveedor_nombre || o.proveedor_nit)}</div>
                    <div class="text-muted small"><i class="far fa-calendar"></i> ${o.fecha_oc} · ${numLineas} línea(s)</div>
                </div>
            `;
        });
        cont.innerHTML = tarjetas.join('');
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

    // ==================================================================
    // Panel de trazabilidad (plan 2026-09-21): línea de tiempo de una
    // compra completa -- Solicitud -> Orden de compra -> Recepción ->
    // Tránsito externo -> Factura -- sin importar desde cuál de las 3
    // pestañas se abrió. Reemplaza los modales sueltos que antes abrían
    // abrirRegistrarRecepcion / verRecepcionesParaTransito / enviarATransito /
    // abrirCargarFactura: esas acciones ahora viven inline dentro de cada
    // paso de este panel.
    // ==================================================================
    _tzNumeroOc: null,
    _tzPasoAbierto: null,
    _tzDatos: null,

    abrirTrazabilidad: async function (numero_oc, pasoAExpandir = null) {
        const overlay = document.getElementById('compras-tz-overlay');
        const cuerpo = document.getElementById('compras-tz-body');
        const titulo = document.getElementById('compras-tz-titulo');
        if (!overlay || !cuerpo || !titulo) return;

        this._tzNumeroOc = numero_oc;
        this._tzPasoAbierto = pasoAExpandir;
        titulo.innerHTML = `<p class="fw-bold mb-0">${this._esc(numero_oc)}</p>`;
        cuerpo.innerHTML = '<div class="text-center text-muted py-4"><i class="fas fa-spinner fa-spin"></i></div>';
        overlay.style.display = 'flex';

        try {
            const datos = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}/timeline`);
            this._tzDatos = datos;
            this._renderTrazabilidad(datos);
        } catch (e) {
            cuerpo.innerHTML = `<div class="alert alert-danger">${this._esc(e.message)}</div>`;
        }
    },

    // Desde una tarjeta de "Mis solicitudes": si ya tiene OC, abre el
    // timeline completo; si no, muestra solo el paso de Solicitud (sin
    // pegarle a la API -- ya se tiene el dato en mano).
    abrirTrazabilidadDesdeSolicitud: function (idSolicitud) {
        const solicitud = (this.misSolicitudesData || []).find(s => s.id === idSolicitud);
        if (!solicitud) return;
        if (solicitud.estado === 'EN_OC' && solicitud.id_oc_vinculada) {
            this.abrirTrazabilidad(solicitud.id_oc_vinculada);
            return;
        }
        const overlay = document.getElementById('compras-tz-overlay');
        if (!overlay) return;
        this._tzNumeroOc = null;
        this._tzDatos = null;
        document.getElementById('compras-tz-titulo').innerHTML =
            `<p class="fw-bold mb-0">${this._esc(solicitud.item_descripcion)}</p><p class="small text-muted mb-0">${this._esc(solicitud.codigo_producto || '')}</p>`;
        document.getElementById('compras-tz-body').innerHTML = this._tzPasosSoloSolicitudHtml(solicitud);
        overlay.style.display = 'flex';
    },

    cerrarTrazabilidad: function () {
        const overlay = document.getElementById('compras-tz-overlay');
        if (overlay) overlay.style.display = 'none';
        this._tzNumeroOc = null;
        this._tzDatos = null;
    },

    // Recarga el panel (si sigue abierto) y las listas de fondo -- para que
    // la tarjeta que abrió el panel no quede desactualizada al cerrarlo.
    _tzRefrescar: async function () {
        if (this._tzNumeroOc) await this.abrirTrazabilidad(this._tzNumeroOc, this._tzPasoAbierto);
        if (this.activeTab === 'solicitud') this.cargarMisSolicitudes();
        if (this.activeTab === 'orden') {
            this.cargarSolicitudesPendientes();
            this.cargarOrdenes();
        }
        if (this.activeTab === 'recepcion') {
            this.cargarPendientesRecepcion();
            this.cargarTransito();
            this.cargarRecibidas();
        }
    },

    _tzToggleStep: function (nombre) {
        this._tzPasoAbierto = (this._tzPasoAbierto === nombre) ? null : nombre;
        if (this._tzDatos) this._renderTrazabilidad(this._tzDatos);
    },

    _tzPasosSoloSolicitudHtml: function (s) {
        const pasos = [
            { nombre: 'Solicitud', hecho: true, resumen: `Pedida por ${this._esc(s.solicitado_por)} · ${new Date(s.creado_en).toLocaleDateString()}` },
            {
                nombre: 'Orden de compra', hecho: false,
                resumen: s.estado === 'PENDIENTE' ? 'Pendiente de que se arme la orden de compra'
                    : s.estado === 'RECHAZADA' ? `Rechazada: ${this._esc(s.motivo_rechazo || '')}` : 'Cancelada',
            },
            { nombre: 'Recepción', hecho: false, resumen: 'Aún no aplica' },
            { nombre: 'Tránsito externo', hecho: false, resumen: 'Solo aplica si algo va a Granallado o Zincado' },
            { nombre: 'Factura', hecho: false, resumen: 'Pendiente' },
        ];
        return pasos.map((p, i) => `
            <div class="compras-tz-step">
                <div class="compras-tz-step-rail">
                    <i class="fas ${p.hecho ? 'fa-check-circle text-success' : 'fa-clock text-muted'}"></i>
                    ${i < pasos.length - 1 ? '<div class="compras-tz-step-line"></div>' : ''}
                </div>
                <div class="compras-tz-step-body compras-tz-noclick">
                    <p class="mb-0 fw-semibold small">${this._esc(p.nombre)}</p>
                    <p class="mb-0 small ${p.hecho ? 'text-secondary' : 'text-muted'}">${p.resumen}</p>
                </div>
            </div>
        `).join('');
    },

    _renderTrazabilidad: function (datos) {
        const titulo = document.getElementById('compras-tz-titulo');
        const cuerpo = document.getElementById('compras-tz-body');
        const o = datos.orden;
        const badgeEstado = {
            ABIERTA: 'bg-warning text-dark', PARCIALMENTE_RECIBIDA: 'bg-info',
            RECIBIDA_TOTAL: 'bg-success', RECHAZADA: 'bg-danger', ANULADA: 'bg-secondary',
        };
        titulo.innerHTML = `
            <p class="fw-bold mb-0">${this._esc(o.numero_oc)} <span class="badge ${badgeEstado[o.estado] || 'bg-secondary'} ms-1">${o.estado}</span></p>
            <p class="small text-muted mb-0">${this._esc(o.proveedor_nombre || o.proveedor_nit)} · pedida ${o.fecha_oc}</p>
        `;

        const solicitudesHtml = datos.solicitudes_origen.length
            ? datos.solicitudes_origen.map(s => `<p class="small mb-1">${this._esc(s.item_descripcion)} <span class="text-muted">-- pedida por ${this._esc(s.solicitado_por)}</span></p>`).join('')
            : '<p class="small text-muted mb-0">Esta OC no quedó vinculada a ninguna solicitud (se armó directo).</p>';

        const lineasHtml = datos.lineas.map(l => `
            <div class="d-flex justify-content-between small border-bottom py-1">
                <span>${this._esc(l.descripcion)}</span>
                <span class="text-muted">${l.cantidad_pedida} ${this._esc(l.unidad_medida || '')}</span>
            </div>
        `).join('');

        const bloques = [
            {
                nombre: 'Solicitud', key: 'solicitud', hecho: true,
                resumenCerrado: `${datos.solicitudes_origen.length} solicitud(es) de origen`,
                detalle: solicitudesHtml,
            },
            {
                nombre: 'Orden de compra', key: 'oc', hecho: true,
                resumenCerrado: `${o.numero_oc} · creada por ${this._esc(o.creado_por)}`,
                detalle: lineasHtml,
            },
            this._tzBloqueRecepcion(datos),
            this._tzBloqueTransito(datos),
            this._tzBloqueFactura(datos),
        ];

        const abierto = this._tzPasoAbierto;
        cuerpo.innerHTML = bloques.map((b, i) => `
            <div class="compras-tz-step">
                <div class="compras-tz-step-rail">
                    <i class="fas ${b.hecho ? 'fa-check-circle text-success' : 'fa-clock text-muted'}"></i>
                    ${i < bloques.length - 1 ? '<div class="compras-tz-step-line"></div>' : ''}
                </div>
                <div class="compras-tz-step-body" onclick="ModuloCompras._tzToggleStep('${b.key}')">
                    <p class="mb-0 fw-semibold small">${this._esc(b.nombre)} <i class="fas fa-chevron-${abierto === b.key ? 'up' : 'down'} small text-muted ms-1"></i></p>
                    <p class="mb-0 small ${b.hecho ? 'text-secondary' : 'text-muted'}">${b.resumenCerrado}</p>
                    ${abierto === b.key ? `<div class="compras-tz-step-detalle" onclick="event.stopPropagation()">${b.detalle}</div>` : ''}
                </div>
            </div>
        `).join('');
    },

    _tzBloqueRecepcion: function (datos) {
        const lineaPorId = {};
        datos.lineas.forEach(l => { lineaPorId[l.id] = l; });
        const hoy = new Date().toISOString().split('T')[0];

        const historialHtml = datos.recepciones.map(({ recepcion: r, lineas }) => `
            <div class="border-bottom pb-2 mb-2">
                <p class="small fw-semibold mb-1">${r.fecha_recepcion} · ${r.estado_recepcion} <span class="text-muted fw-normal">(${this._esc(r.recibido_por)})</span></p>
                ${lineas.map(l => `<p class="small mb-0 text-muted">${this._esc((lineaPorId[l.id_linea_oc] || {}).descripcion || '')}: recibido ${l.cantidad_recibida}${l.cantidad_rechazada ? `, rechazado ${l.cantidad_rechazada}` : ''}${l.excede_tolerancia ? ' <span class="badge bg-danger">excede tolerancia</span>' : ''}</p>`).join('')}
            </div>
        `).join('');

        const puedeRecepcionar = this._puedeRecepcionar(this._rolNormalizado())
            && !['RECIBIDA_TOTAL', 'RECHAZADA', 'ANULADA'].includes(datos.orden.estado);
        const formHtml = puedeRecepcionar ? `
            <div class="mt-2 pt-2 border-top">
                <p class="small fw-bold mb-2">Registrar nueva recepción</p>
                <div class="row g-2 mb-2">
                    <div class="col-6">
                        <label class="form-label small mb-1">Fecha general</label>
                        <input id="tz-rec-fecha" type="date" class="form-control form-control-sm" value="${hoy}">
                    </div>
                    <div class="col-6">
                        <label class="form-label small mb-1">Estado</label>
                        <select id="tz-rec-estado" class="form-select form-select-sm">
                            <option value="RECIBIDA_TOTAL">Recibida total</option>
                            <option value="RECIBIDA_PARCIAL">Recibida parcial</option>
                            <option value="RECHAZADA">Rechazada</option>
                        </select>
                    </div>
                </div>
                ${datos.lineas.map(l => `
                    <div class="row g-2 mb-2 align-items-end">
                        <div class="col-12"><label class="form-label small mb-1">${this._esc(l.descripcion)} <span class="text-muted">(pendiente: ${l.pendiente})</span></label></div>
                        <div class="col-5"><input type="number" step="0.01" class="form-control form-control-sm tz-rec-linea-recibida" data-id-linea="${l.id}" placeholder="Recibida"></div>
                        <div class="col-4"><input type="number" step="0.01" class="form-control form-control-sm tz-rec-linea-rechazada" data-id-linea="${l.id}" placeholder="Rechazada"></div>
                        <div class="col-3"><input type="date" class="form-control form-control-sm tz-rec-linea-fecha" data-id-linea="${l.id}" value="${hoy}"></div>
                    </div>
                `).join('')}
                <button type="button" class="btn btn-sm btn-primary mt-1" onclick="ModuloCompras._tzGuardarRecepcion()">
                    <i class="fas fa-save"></i> Guardar recepción
                </button>
            </div>
        ` : '';

        return {
            nombre: 'Recepción', key: 'recepcion', hecho: datos.recepciones.length > 0,
            resumenCerrado: datos.recepciones.length ? `${datos.recepciones.length} evento(s) registrado(s)` : 'Aún no llega mercancía',
            detalle: (historialHtml || '<p class="small text-muted mb-0">Sin eventos todavía.</p>') + formHtml,
        };
    },

    _tzGuardarRecepcion: async function () {
        const numero_oc = this._tzNumeroOc;
        const fecha = document.getElementById('tz-rec-fecha')?.value;
        if (!fecha) { Swal.fire('Falta la fecha', 'Indica la fecha de recepción.', 'warning'); return; }
        const estado_recepcion = document.getElementById('tz-rec-estado')?.value;
        const lineas = Array.from(document.querySelectorAll('.tz-rec-linea-recibida'))
            .map(i => {
                const idLinea = i.dataset.idLinea;
                const rechazada = document.querySelector(`.tz-rec-linea-rechazada[data-id-linea="${idLinea}"]`);
                const fechaLinea = document.querySelector(`.tz-rec-linea-fecha[data-id-linea="${idLinea}"]`);
                return {
                    id_linea_oc: parseInt(idLinea),
                    cantidad_recibida: parseFloat(i.value || 0),
                    cantidad_rechazada: parseFloat((rechazada && rechazada.value) || 0),
                    fecha_recepcion: (fechaLinea && fechaLinea.value) || null,
                };
            })
            .filter(l => l.cantidad_recibida > 0 || l.cantidad_rechazada > 0);
        // Si deja todo en blanco no hay nada que registrar -- mismo aviso
        // que ya existía en el modal viejo (bug real reportado 2026-09-16).
        if (!lineas.length) {
            Swal.fire('Nada que registrar', 'Escribe cantidad recibida o rechazada en al menos una línea -- si no llegó nada todavía, no guardes.', 'warning');
            return;
        }
        try {
            const resultado = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}/recepciones`, {
                method: 'POST', body: JSON.stringify({ fecha_recepcion: fecha, estado_recepcion, lineas }),
            });
            if (resultado.lineas_con_exceso_tolerancia?.length) {
                await Swal.fire('Recepción registrada con exceso', 'Se avisó a Diego: llegó más de lo pedido por encima de la tolerancia.', 'warning');
            } else {
                Swal.fire({ icon: 'success', title: 'Recepción registrada', timer: 1500, showConfirmButton: false });
            }
            this._tzPasoAbierto = 'transito';
            await this._tzRefrescar();
        } catch (e) {
            Swal.fire('No se pudo registrar la recepción', e.message, 'error');
        }
    },

    _tzBloqueTransito: function (datos) {
        const lineaRecepcionPorId = {};
        datos.recepciones.forEach(({ recepcion: r, lineas }) => {
            lineas.forEach(l => { lineaRecepcionPorId[l.id] = { ...l, fecha: l.fecha_recepcion || r.fecha_recepcion }; });
        });
        const puedeRecepcionar = this._puedeRecepcionar(this._rolNormalizado());
        const hoy = new Date().toISOString().split('T')[0];
        const colorEstado = { ENVIADO: 'bg-warning text-dark', EN_PROCESO: 'bg-info', RETORNADO: 'bg-success', RETORNADO_PARCIAL: 'bg-danger' };

        const historialHtml = datos.transito.map(({ transito: t }) => `
            <div class="border-bottom pb-2 mb-2">
                <div class="d-flex justify-content-between align-items-center">
                    <span class="small fw-semibold">${this._esc(t.proceso)}</span>
                    <span class="badge ${colorEstado[t.estado] || 'bg-secondary'}">${t.estado}</span>
                </div>
                <p class="small text-muted mb-0">Enviado: ${t.cantidad_enviada} · ${t.fecha_envio}</p>
                ${t.cantidad_retornada !== null ? `<p class="small text-muted mb-0">Retornado: ${t.cantidad_retornada}${t.diferencia_envio_retorno ? ` (diferencia: ${t.diferencia_envio_retorno})` : ''}</p>` : ''}
                ${puedeRecepcionar && t.estado === 'ENVIADO' ? `<button type="button" class="btn btn-sm btn-outline-info mt-1 me-1" onclick="ModuloCompras._tzMarcarEnProceso(${t.id})">Marcar en proceso</button>` : ''}
                ${puedeRecepcionar && ['ENVIADO', 'EN_PROCESO'].includes(t.estado) ? `
                    <div class="row g-2 mt-1 align-items-end">
                        <div class="col-6"><input id="tz-ret-cantidad-${t.id}" type="number" step="0.01" class="form-control form-control-sm" placeholder="Retornado (de ${t.cantidad_enviada})"></div>
                        <div class="col-4"><input id="tz-ret-fecha-${t.id}" type="date" class="form-control form-control-sm" value="${hoy}"></div>
                        <div class="col-2"><button type="button" class="btn btn-sm btn-outline-success" onclick="ModuloCompras._tzRegistrarRetorno(${t.id})"><i class="fas fa-save"></i></button></div>
                    </div>
                ` : ''}
            </div>
        `).join('');

        const enviadoPorLinea = {};
        datos.transito.forEach(({ transito: t }) => {
            enviadoPorLinea[t.id_linea_recepcion_oc] = (enviadoPorLinea[t.id_linea_recepcion_oc] || 0) + parseFloat(t.cantidad_enviada || 0);
        });
        const disponiblesHtml = Object.values(lineaRecepcionPorId)
            .filter(l => l.cantidad_recibida > (enviadoPorLinea[l.id] || 0) && puedeRecepcionar)
            .map(l => {
                const disponible = l.cantidad_recibida - (enviadoPorLinea[l.id] || 0);
                return `
                    <div class="border-top pt-2 mt-2">
                        <p class="small mb-1">Lote del ${l.fecha}: <strong>${disponible}</strong> disponible para enviar</p>
                        <div class="row g-2 align-items-end">
                            <div class="col-4">
                                <select id="tz-tr-proceso-${l.id}" class="form-select form-select-sm">
                                    <option value="GRANALLADO">Granallado</option>
                                    <option value="ZINCADO">Zincado</option>
                                </select>
                            </div>
                            <div class="col-3"><input id="tz-tr-cantidad-${l.id}" type="number" step="0.01" max="${disponible}" class="form-control form-control-sm" placeholder="Cant."></div>
                            <div class="col-3"><input id="tz-tr-fecha-${l.id}" type="date" class="form-control form-control-sm" value="${hoy}"></div>
                            <div class="col-2"><button type="button" class="btn btn-sm btn-outline-primary" onclick="ModuloCompras._tzEnviarATransito(${l.id})"><i class="fas fa-paper-plane"></i></button></div>
                        </div>
                    </div>
                `;
            }).join('');

        return {
            nombre: 'Tránsito externo', key: 'transito', hecho: datos.transito.length > 0,
            resumenCerrado: datos.transito.length ? `${datos.transito.length} envío(s) a maquila` : 'Solo aplica si algo va a Granallado o Zincado',
            detalle: (historialHtml || '<p class="small text-muted mb-0">Nada enviado a maquila todavía.</p>') + disponiblesHtml,
        };
    },

    _tzEnviarATransito: async function (idLineaRecepcion) {
        const proceso = document.getElementById(`tz-tr-proceso-${idLineaRecepcion}`)?.value;
        const cantidad = parseFloat(document.getElementById(`tz-tr-cantidad-${idLineaRecepcion}`)?.value || 0);
        const fecha = document.getElementById(`tz-tr-fecha-${idLineaRecepcion}`)?.value;
        if (cantidad <= 0) { Swal.fire('Cantidad inválida', 'Indica cuánto se envía.', 'warning'); return; }
        try {
            await this._api(`/api/compras/recepciones/${idLineaRecepcion}/transito`, {
                method: 'POST', body: JSON.stringify({ proceso, cantidad_enviada: cantidad, fecha_envio: fecha }),
            });
            Swal.fire({ icon: 'success', title: 'Enviado', timer: 1500, showConfirmButton: false });
            await this._tzRefrescar();
        } catch (e) {
            Swal.fire('No se pudo enviar', e.message, 'error');
        }
    },

    _tzMarcarEnProceso: async function (idTransito) {
        try {
            await this._api(`/api/compras/transito/${idTransito}/estado`, {
                method: 'PATCH', body: JSON.stringify({ estado: 'EN_PROCESO' }),
            });
            await this._tzRefrescar();
        } catch (e) {
            Swal.fire('No se pudo actualizar', e.message, 'error');
        }
    },

    _tzRegistrarRetorno: async function (idTransito) {
        const cantidad = parseFloat(document.getElementById(`tz-ret-cantidad-${idTransito}`)?.value || 0);
        const fecha = document.getElementById(`tz-ret-fecha-${idTransito}`)?.value;
        try {
            await this._api(`/api/compras/transito/${idTransito}/retorno`, {
                method: 'POST', body: JSON.stringify({ cantidad_retornada: cantidad, fecha_retorno: fecha }),
            });
            Swal.fire({ icon: 'success', title: 'Retorno registrado', timer: 1500, showConfirmButton: false });
            await this._tzRefrescar();
        } catch (e) {
            Swal.fire('No se pudo registrar el retorno', e.message, 'error');
        }
    },

    _tzBloqueFactura: function (datos) {
        const esAdmin = this._esAdmin(this._rolNormalizado());
        const hoy = new Date().toISOString().split('T')[0];

        if (datos.factura) {
            const f = datos.factura.factura;
            const detalle = `
                <p class="small mb-1">Factura <strong>${this._esc(f.numero_factura)}</strong> · ${f.fecha_factura}</p>
                <p class="small text-muted mb-1">Cargada por ${this._esc(f.cargada_por)}</p>
                <p class="small mb-0">Estado: <span class="badge ${f.estado_conciliacion === 'COINCIDE' ? 'bg-success' : 'bg-warning text-dark'}">${f.estado_conciliacion}</span></p>
            `;
            return { nombre: 'Factura', key: 'factura', hecho: true, resumenCerrado: `${f.numero_factura} · ${f.estado_conciliacion}`, detalle };
        }

        const puedeFacturar = esAdmin && datos.orden.estado !== 'ANULADA';
        const formHtml = puedeFacturar ? `
            <div class="row g-2 mb-2">
                <div class="col-7">
                    <label class="form-label small mb-1">Número de factura</label>
                    <input id="tz-fc-numero" class="form-control form-control-sm" placeholder="Ej: FC-0001">
                </div>
                <div class="col-5">
                    <label class="form-label small mb-1">Fecha</label>
                    <input id="tz-fc-fecha" type="date" class="form-control form-control-sm" value="${hoy}">
                </div>
            </div>
            ${datos.lineas.map(l => `
                <div class="mb-2">
                    <label class="form-label small mb-1">${this._esc(l.descripcion)} <span class="text-muted">(recibido: ${l.cantidad_recibida_acumulada})</span></label>
                    <input type="number" step="0.01" class="form-control form-control-sm tz-fc-linea-cantidad" data-id-linea="${l.id}" placeholder="Cantidad facturada">
                </div>
            `).join('')}
            <button type="button" class="btn btn-sm btn-primary mt-1" onclick="ModuloCompras._tzGuardarFactura()">
                <i class="fas fa-save"></i> Cargar factura
            </button>
        ` : '<p class="small text-muted mb-0">Pendiente de cargar.</p>';

        return { nombre: 'Factura', key: 'factura', hecho: false, resumenCerrado: 'Pendiente de cargar', detalle: formHtml };
    },

    _tzGuardarFactura: async function () {
        const numero_oc = this._tzNumeroOc;
        const numero_factura = document.getElementById('tz-fc-numero')?.value.trim();
        const fecha_factura = document.getElementById('tz-fc-fecha')?.value;
        const lineas = Array.from(document.querySelectorAll('.tz-fc-linea-cantidad'))
            .filter(i => i.value)
            .map(i => ({ id_linea_oc: parseInt(i.dataset.idLinea), cantidad_facturada: parseFloat(i.value) }));
        if (!numero_factura || !lineas.length) {
            Swal.fire('Faltan datos', 'Indica el número de factura y al menos una cantidad facturada.', 'warning');
            return;
        }
        try {
            const factura = await this._api(`/api/compras/ordenes/${encodeURIComponent(numero_oc)}/factura`, {
                method: 'POST', body: JSON.stringify({ numero_factura, fecha_factura, lineas }),
            });
            const icono = factura.estado_conciliacion === 'COINCIDE' ? 'success' : 'warning';
            Swal.fire(factura.estado_conciliacion === 'COINCIDE' ? 'Factura conciliada' : 'Factura con discrepancia', `Estado: ${factura.estado_conciliacion}`, icono);
            await this._tzRefrescar();
        } catch (e) {
            Swal.fire('No se pudo cargar la factura', e.message, 'error');
        }
    },

    // ------------------------------------------------------------------
    _esc: function (str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    },

    // "Hace X días" para que se note de un vistazo qué lleva más tiempo
    // esperando -- pedido real 2026-09-16 (tarjetas "muy muertas").
    _diasDesde: function (fechaIso) {
        if (!fechaIso) return null;
        const dias = Math.floor((Date.now() - new Date(fechaIso).getTime()) / 86400000);
        if (dias <= 0) return 'hoy';
        if (dias === 1) return 'hace 1 día';
        return `hace ${dias} días`;
    },
};

window.ModuloCompras = ModuloCompras;
