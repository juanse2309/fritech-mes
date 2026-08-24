// ============================================
// ensamble.js - Sub-módulo de Programación y Reporte Flexible
// Versión RE-ESTABLECIDA: Captura Infalible de Usuario y Corrección de IDs
// ============================================

// Procesos del checklist de Ensamble, en el orden en que se muestran y se
// envían al backend (debe coincidir con PROCESOS_CHECKLIST en
// backend/services/ensamble_service.py). "ensamble" va primero porque es
// el único proceso que SIEMPRE aplica -- los otros 6 pueden marcarse
// "No aplica" en productos que no los requieren.
const PROCESOS_CHECKLIST = [
    { key: 'ensamble', label: 'Ensamble' },
    { key: 'rayada_carcaza', label: 'Rayada Carcaza' },
    { key: 'rayada_interno', label: 'Rayada Interno' },
    { key: 'pintura', label: 'Pintura' },
    { key: 'horno1', label: 'Horno 1' },
    { key: 'cerrada', label: 'Cerrada' },
    { key: 'horno2', label: 'Horno 2' },
];
const CHECKLIST_CICLO = ['PENDIENTE', 'HECHO', 'NO_APLICA'];
// Estilo de "chip" con tinte suave (fondo pastel + borde + texto del mismo
// tono) en vez de los badges de relleno sólido de Bootstrap -- más cercano
// al boceto aprobado que a un botón genérico.
const CHECKLIST_UI = {
    PENDIENTE: { label: 'Pendiente', icono: 'fa-circle', style: 'background:#fdf3e2;color:#96650b;border-color:#f3ddac;' },
    HECHO: { label: 'Hecho', icono: 'fa-check', style: 'background:#e3f3e8;color:#1f7a44;border-color:#b9e2c6;' },
    NO_APLICA: { label: 'No aplica', icono: 'fa-ban', style: 'background:#eef0f2;color:#8b95a1;border-color:#dfe3e8;text-decoration:line-through;' },
};

function checklistPorDefecto() {
    const estados = {};
    PROCESOS_CHECKLIST.forEach(p => { estados[p.key] = 'PENDIENTE'; });
    return estados;
}

const ModuloEnsamble = {
    productosData: [],
    responsablesData: [],
    currentTab: 'programacion',
    isManualMode: false,
    pncDetalles: [],
    checklistEstados: {}, // {proceso: 'PENDIENTE'|'HECHO'|'NO_APLICA'} de la tarea seleccionada
    isSubmitting: false, // Guardia anti doble-clic para reportarAvance/pausarReporte

    // Estado de Sesión Persistente
    sessionId: null,
    sesionActiva: false,
    enPausa: false,
    startTime: null,
    totalPausaMs: 0,
    timerInterval: null,

    inicializar: async function () {
        console.log('🔧 [Ensamble] Inicializando módulo con Captura Infalible...');

        // Capturar usuario actual para validaciones de voz
        this.usuarioActual = document.getElementById('current_user_fullname')?.value || '';
        this.intentarAutoSeleccionarResponsable(); // Sincronizar UI inmediatamente

        this.configurarTabs();
        await this.cargarDatos();
        this.configurarEventos();

        // Autocompletes
        this.initAutocomplete('prog-producto', 'prog-producto-suggestions', true);
        this.initAutocomplete('reporte-producto-manual', 'reporte-producto-manual-suggestions', true);

        this.listarProgramacion();
        this.listarTareasPendientes();

        // Inicializar fecha hoy
        const hoy = new Date().toISOString().split('T')[0];
        if (document.getElementById('reporte-fecha')) document.getElementById('reporte-fecha').value = hoy;
        if (document.getElementById('prog-fecha')) document.getElementById('prog-fecha').value = hoy;

        this.intentarAutoSeleccionarResponsable();

        // --- VERIFICACIÓN DE SESIÓN ACTIVA (Evita Duplicados) ---
        await this.verificarSesionActiva();

        // --- SUBPROCESOS DE ENSAMBLE (Pintura / Rayada / Hornos) ---
        // Cada uno gestiona su propio ciclo iniciar/finalizar de forma
        // independiente (ver SubprocesoEnsambleController); solo se
        // bootstrapean aquí porque comparten la página 'ensamble'.
        window.ModuloPintura?.inicializar();
        window.ModuloRayada?.inicializar();
        window.ModuloHornos?.inicializar();
    },

    verificarSesionActiva: async function () {
        const responsable = document.getElementById('current_user_fullname')?.value || document.getElementById('responsable')?.value;
        if (!responsable) return;

        try {
            console.log(`📡 [Ensamble] Buscando sesión activa para: ${responsable}...`);
            const res = await fetch(`/api/ensamble/session_active?responsable=${encodeURIComponent(responsable)}`);
            const data = await res.json();
            const session = data.data?.session;

            if (data.success && session) {
                console.log("✅ [Ensamble] Sesión recuperada de DB:", session);
                this.sessionId = session.id_ensamble;
                this.sesionActiva = true;
                this.enPausa = (session.estado === 'PAUSADO');
                this.startTime = new Date(session.hora_inicio_dt);
                this.totalPausaMs = (session.tiempo_pausa_acumulado || 0) * 1000;

                // Poblar UI y Bloquear
                const prodInput = document.getElementById('reporte-producto-manual');
                if (prodInput) prodInput.value = session.id_codigo;
                const opInput = document.getElementById('reporte-op');
                if (opInput) opInput.value = session.orden_produccion || '';
                const cantInput = document.getElementById('reporte-cantidad');
                if (cantInput) cantInput.value = session.cantidad;

                const bujeOrigen = document.getElementById('reporte-buje-origen');
                if (bujeOrigen) bujeOrigen.value = session.id_codigo;

                // --- REHIDRATACIÓN BOM ---
                this.renderBOMCheckboxes(session.id_codigo, 'reporte-bom-check-container');

                document.getElementById('form-reporte-ensamble-container').style.display = 'block';
                this.bloquearFormulario(true);

                // FORZAR UI DE BOTONES
                this.actualizarUIBotones();

                if (this.timerInterval) clearInterval(this.timerInterval);
                this.timerInterval = setInterval(() => this.actualizarTimer(), 1000);
            }
        } catch (e) {
            console.error("[Ensamble] Error al verificar sesión activa:", e);
        }
    },

    bloquearFormulario: function (lock) {
        ['reporte-producto-manual', 'reporte-op', 'reporte-fecha', 'reporte-almacen-origen', 'reporte-almacen-destino'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.disabled = lock;
        });
    },

    actualizarUIBotones: function () {
        const btnFinalizar = document.getElementById('btn-reportar-avance'); // Alineado con HTML
        const container = document.getElementById('form-reporte-ensamble');

        console.log(`[Ensamble] Actualizando UI: Activa=${this.sesionActiva}, Pausa=${this.enPausa}`);

        if (this.sesionActiva) {
            // Asegurarnos que el botón de Finalizar sea visible y tenga el estilo correcto
            if (btnFinalizar) {
                btnFinalizar.style.display = 'inline-block';
                btnFinalizar.className = 'btn btn-success btn-lg w-100 shadow py-3 rounded-4';
            }

            // Inyectar botón de Pausa si no existe en el DOM
            let btnPausar = document.getElementById('btn-pausar-ensamble-dinamico');
            if (!btnPausar && container) {
                const row = btnFinalizar.closest('.row');
                const col = document.createElement('div');
                col.className = 'col-md-12 mt-2';
                col.innerHTML = `
                    <button type="button" id="btn-pausar-ensamble-dinamico" class="btn btn-warning btn-lg w-100 py-3 rounded-4">
                        <i class="fas fa-pause me-2"></i>Pausar Producción
                    </button>
                `;
                row.appendChild(col);
                btnPausar = document.getElementById('btn-pausar-ensamble-dinamico');
                btnPausar.onclick = () => this.pausarReporte();
            }

            if (btnPausar) {
                btnPausar.innerHTML = this.enPausa ? '<i class="fas fa-play me-2"></i>REANUDAR PRODUCCIÓN' : '<i class="fas fa-pause me-2"></i>PAUSAR PRODUCCIÓN';
                btnPausar.className = this.enPausa ? 'btn btn-info btn-lg w-100 py-3 rounded-4' : 'btn btn-warning btn-lg w-100 py-3 rounded-4';
            }

            // Validar inmediatamente
            setTimeout(() => this.validarFormulario(), 300);
        }
    },

    actualizarTimer: function () {
        if (this.enPausa || !this.startTime) return;
        const now = new Date();
        const diff = now - this.startTime - this.totalPausaMs;
        const h = Math.floor(diff / 3600000).toString().padStart(2, '0');
        const m = Math.floor((diff % 3600000) / 60000).toString().padStart(2, '0');
        const s = Math.floor((diff % 60000) / 1000).toString().padStart(2, '0');
        const display = document.getElementById('ensamble-timer-display');
        if (display) display.innerText = `${h}:${m}:${s}`;
    },

    configurarTabs: function () {
        document.querySelectorAll('#ensamble-tabs .nav-link').forEach(tab => {
            tab.addEventListener('click', (e) => {
                this.switchTab(e.target.closest('.nav-link').dataset.tab);
            });
        });
    },

    switchTab: function (tabName) {
        this.currentTab = tabName;
        document.querySelectorAll('.ensamble-tab-content').forEach(el => el.style.display = 'none');
        document.getElementById(`ens-tab-${tabName}`).style.display = 'block';

        document.querySelectorAll('#ensamble-tabs .nav-link').forEach(el => el.classList.remove('active'));
        document.querySelector(`#ensamble-tabs .nav-link[data-tab="${tabName}"]`).classList.add('active');

        if (tabName === 'reporte') {
            this.listarTareasPendientes();
            if (!this.sesionActiva) this.resetFormulario(true);
        }
        if (tabName === 'programacion') this.listarProgramacion();
    },

    cargarDatos: async function () {
        try {
            mostrarLoading(true);

            // 1. Cargar Productos
            if (window.AppState?.sharedData?.productos?.length > 0) {
                this.productosData = window.AppState.sharedData.productos;
            } else {
                const prods = await fetchData('/api/productos/listar');
                this.productosData = prods?.productos || prods?.items || (Array.isArray(prods) ? prods : []);
            }

            // 2. Cargar Responsables (Operarios) para Datalist e IA
            const resp = await fetchData('/api/auth/responsables?division=friparts');
            this.responsablesData = resp?.data || resp || [];
            this.renderResponsablesDatalist();

            mostrarLoading(false);
        } catch (error) {
            console.error('Error cargando datos ensamble:', error);
            mostrarLoading(false);
        }
    },

    renderResponsablesDatalist: function () {
        const datalist = document.getElementById('responsables-list');
        if (!datalist) return;

        datalist.innerHTML = '';
        this.responsablesData.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.nombre;
            datalist.appendChild(opt);
        });
        console.log(`✅ [Ensamble] Datalist poblado con ${this.responsablesData.length} responsables.`);
    },

    buscarMatchResponsable: function (input) {
        if (!input || input.trim().length < 3) return input;

        const search = input.toLowerCase().trim();

        // 1. Coincidencia exacta
        const exact = this.responsablesData.find(r => r.nombre.toLowerCase() === search);
        if (exact) return exact.nombre;

        // 2. Coincidencia parcial (empieza por...)
        const partial = this.responsablesData.find(r => r.nombre.toLowerCase().includes(search));
        if (partial) {
            console.log(`🎯 [IA] Match inteligente: '${input}' -> '${partial.nombre}'`);
            return partial.nombre;
        }

        return input; // Devolver original si no hay match
    },

    formatearFechaParaInput: function (fechaStr) {
        if (!fechaStr) return new Date().toISOString().split('T')[0];

        // Si ya viene en formato correcto YYYY-MM-DD
        const regexISO = /^\d{4}-\d{2}-\d{2}$/;
        if (regexISO.test(fechaStr)) return fechaStr;

        try {
            // Intentar parsear si viene algo como "15 de mayo" o "hoy"
            const d = new Date(fechaStr);
            if (!isNaN(d.getTime())) {
                return d.toISOString().split('T')[0];
            }
        } catch (e) {
            console.warn("[Ensamble] No se pudo formatear fecha:", fechaStr);
        }

        return new Date().toISOString().split('T')[0]; // Fallback a hoy
    },

    initAutocomplete: function (inputId, suggestionsId, isProduct) {
        const input = document.getElementById(inputId);
        const suggestionsDiv = document.getElementById(suggestionsId);
        if (!input || !suggestionsDiv) return;

        input.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            if (query.length < 1) {
                suggestionsDiv.classList.remove('active');
                return;
            }

            const terms = query.split(/\s+/).filter(t => t.length > 0);
            const resultados = this.productosData.filter(p => {
                const codigo = String(p.codigo_sistema || '').toLowerCase();
                const descripcion = String(p.descripcion || '').toLowerCase();
                return terms.every(term => 
                    codigo.includes(term) || 
                    descripcion.includes(term) ||
                    codigo.replace(/[-\s]/g, '').includes(term.replace(/[-\s]/g, ''))
                );
            }).slice(0, 15);

            renderProductSuggestions(suggestionsDiv, resultados, (item) => {
                input.value = item.codigo_sistema;
                suggestionsDiv.classList.remove('active');
                if (inputId === 'prog-producto') this.consultarBOMStock(item.codigo_sistema);
                if (inputId === 'reporte-producto-manual') this.seleccionarProductoManual(item);
            });
        });

        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });
    },

    // --- VISTA NATHALIA ---
    consultarBOMStock: async function (idCodigo) {
        try {
            const res = await fetchData(`/api/ensamble/bom_stock/${idCodigo}`);
            const container = document.getElementById('prog-bom-container');
            const alertBox = document.getElementById('prog-stock-alert');

            if (!res || !res.success) {
                container.innerHTML = '<div class="alert alert-warning">Ficha técnica no encontrada.</div>';
                return;
            }

            const meta = parseInt(document.getElementById('prog-cantidad').value) || 0;
            let stockInsuficiente = false;

            let html = `
                <div class="table-responsive">
                    <table class="table align-middle border shadow-sm rounded-3 overflow-hidden responsive-mobile">
                        <thead class="table-dark">
                            <tr>
                                <th class="py-3 px-3">Componente</th>
                                <th class="py-3 text-center">Stock Actual</th>
                                <th class="py-3 text-center">Capacidad Máxima</th>
                                <th class="py-3 text-center">Estado</th>
                            </tr>
                        </thead>
                        <tbody>
            `;

            res.data.componentes.forEach(c => {
                const isShort = c.alcanza_para < meta && meta > 0;
                if (isShort) stockInsuficiente = true;
                const rowClass = isShort ? 'table-warning bg-warning bg-opacity-25' : 'table-success bg-success bg-opacity-10';

                html += `
                    <tr class="${rowClass}">
                        <td class="fw-bold ps-3" data-label="Componente">${c.componente}<br><small class="text-muted">${c.codigo_inventario}</small></td>
                        <td class="text-center fw-bold fs-5 text-dark" data-label="Stock Actual">${c.stock_almacen}</td>
                        <td class="text-center fw-bold fs-5 text-primary" data-label="Capacidad Máxima">${c.alcanza_para} und</td>
                        <td class="text-center" data-label="Estado">
                            <span class="badge ${isShort ? 'bg-warning text-dark' : 'bg-success'}">
                                ${isShort ? 'INSUFICIENTE' : 'OK'}
                            </span>
                        </td>
                    </tr>
                `;
            });

            html += '</tbody></table></div>';
            container.innerHTML = html;

            if (stockInsuficiente) {
                alertBox.innerHTML = `
                    <div class="alert border-warning bg-warning bg-opacity-10 mt-2 py-3 rounded-4">
                        <i class="fas fa-exclamation-triangle me-2"></i> Poka-Yoke: El stock resaltado no cubre la meta.
                    </div>
                `;
                alertBox.style.display = 'block';
            } else {
                alertBox.style.display = 'none';
            }
        } catch (e) { console.error(e); }
    },

    // --- FLUJO DE REPORTE ---
    renderBOMCheckboxes: async function (idCodigo, containerId) {
        try {
            const res = await fetchData(`/api/ensamble/bom_stock/${idCodigo}`);
            const container = document.getElementById(containerId);
            if (!res || !res.success) {
                this.currentBOM = [];
                container.innerHTML = '<div class="col-12 text-center text-muted">Sin BOM definido.</div>';
                return;
            }

            this.currentBOM = res.data.componentes || []; // Guardar para el envío multiregistro
            let html = '';
            res.data.componentes.forEach((c, index) => {
                html += `
                    <div class="col-md-4">
                        <div class="card border shadow-sm p-3 rounded-3 h-100">
                            <div class="form-check">
                                <input class="form-check-input bom-checkbox" type="checkbox" value="${c.codigo_inventario}" id="bom-check-${index}" checked style="width: 20px; height: 20px;">
                                <label class="form-check-label fw-bold ms-2" for="bom-check-${index}">
                                    ${c.componente}<br><small class="text-muted">${c.codigo_inventario}</small>
                                </label>
                            </div>
                        </div>
                    </div>
                `;
            });
            container.innerHTML = html;
        } catch (e) {
            console.error(e);
            this.currentBOM = [];
        }
    },

    // --- CHECKLIST DE PROCESOS ---
    renderChecklist: function () {
        const wrapper = document.getElementById('reporte-checklist-wrapper');
        const container = document.getElementById('reporte-checklist-container');
        if (!container || !wrapper) return;

        wrapper.style.display = 'block';

        const chips = PROCESOS_CHECKLIST.map(p => {
            const estado = this.checklistEstados[p.key] || 'PENDIENTE';
            const ui = CHECKLIST_UI[estado];
            return `
                <button type="button" class="checklist-chip fw-bold rounded-pill px-3 py-2"
                        style="${ui.style}" onclick="ModuloEnsamble.toggleChecklistProceso('${p.key}')">
                    <i class="fas ${ui.icono} me-1"></i>${p.label}
                    <span style="${estado === 'NO_APLICA' ? 'text-decoration:line-through;' : ''}opacity:.85;font-weight:500;"> · ${ui.label}</span>
                </button>
            `;
        }).join('');

        const leyenda = `
            <div class="d-flex flex-wrap gap-3 small text-muted mt-2">
                <span><i class="fas fa-circle me-1" style="color:#96650b;"></i>Pendiente</span>
                <span><i class="fas fa-check me-1" style="color:#1f7a44;"></i>Hecho</span>
                <span><i class="fas fa-ban me-1" style="color:#8b95a1;"></i>No aplica (clic para ciclar)</span>
            </div>
        `;

        container.innerHTML = `<div class="d-flex flex-wrap gap-2">${chips}</div>${leyenda}`;
    },

    toggleChecklistProceso: function (proceso) {
        const actual = this.checklistEstados[proceso] || 'PENDIENTE';
        const siguiente = CHECKLIST_CICLO[(CHECKLIST_CICLO.indexOf(actual) + 1) % CHECKLIST_CICLO.length];
        this.checklistEstados[proceso] = siguiente;
        this.renderChecklist();
    },

    ocultarChecklist: function () {
        this.checklistEstados = {};
        const wrapper = document.getElementById('reporte-checklist-wrapper');
        if (wrapper) wrapper.style.display = 'none';
        const container = document.getElementById('reporte-checklist-container');
        if (container) container.innerHTML = '';
    },

    seleccionarTarea: function (tarea) {
        if (this.sesionActiva) {
            Swal.fire('Sesión Activa', 'Termina el trabajo actual antes de iniciar una nueva tarea.', 'warning');
            return;
        }
        this.isManualMode = false;
        this.resetFormulario(false);

        document.getElementById('reporte-id-prog').value = tarea.id_prog;
        document.getElementById('reporte-producto-manual').value = tarea.id_codigo;
        document.getElementById('reporte-buje-origen').value = tarea.id_codigo;
        document.getElementById('reporte-cantidad').value = tarea.faltante;

        this.checklistEstados = Object.assign(checklistPorDefecto(), tarea.checklist || {});
        this.renderChecklist();

        this.intentarAutoSeleccionarResponsable();
        this.renderBOMCheckboxes(tarea.id_codigo, 'reporte-bom-check-container');

        const container = document.getElementById('form-reporte-ensamble-container');
        if (container) container.style.display = 'block';

        let modalEl = document.getElementById('modalReporteEnsamble') || document.getElementById('modalEnsamble');
        if (modalEl) {
            let inst = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
            inst.show();
        } else {
            if (container) container.scrollIntoView({ behavior: 'smooth' });
        }
    },

    seleccionarProductoManual: async function (producto) {
        if (this.sesionActiva) return;
        document.getElementById('reporte-id-prog').value = '';
        document.getElementById('reporte-buje-origen').value = producto.codigo_sistema;

        this.intentarAutoSeleccionarResponsable();
        return await this.renderBOMCheckboxes(producto.codigo_sistema, 'reporte-bom-check-container');
    },

    abrirModalManual: function () {
        this.isManualMode = true;

        // 1. Limpiar todos los campos (Formulario en blanco)
        this.resetFormulario(true);

        // Limpieza extra explícita
        const idsToClear = ['reporte-id-prog', 'reporte-producto-manual', 'reporte-cantidad', 'reporte-op', 'reporte-pnc', 'reporte-observaciones'];
        idsToClear.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });

        const bomContainer = document.getElementById('reporte-bom-check-container');
        if (bomContainer) bomContainer.innerHTML = '<div class="col-12 text-center text-muted py-2">Escribe un código para cargar el BOM.</div>';

        this.intentarAutoSeleccionarResponsable();

        // 2. Forzar visualización del contenedor
        const container = document.getElementById('form-reporte-ensamble-container');
        if (container) container.style.display = 'block';

        // 3. Forzar apertura de Bootstrap Modal explícitamente
        const modalId = document.getElementById('modalReporteEnsamble') ? 'modalReporteEnsamble' : 'modalEnsamble';
        const modalEl = document.getElementById(modalId);

        if (modalEl) {
            try {
                let inst = bootstrap.Modal.getInstance(modalEl);
                if (!inst) {
                    inst = new bootstrap.Modal(modalEl);
                }
                inst.show();
            } catch (e) {
                console.error("[Ensamble] Error forzando modal:", e);
            }
        } else {
            if (container) container.scrollIntoView({ behavior: 'smooth' });
        }
    },

    // Deshabilita/rehabilita e inyecta estado de carga en los botones de
    // acción del reporte (Finalizar/Pausar) mientras hay un envío en vuelo.
    _setBotonesEnvioCargando: function (cargando) {
        ['btn-reportar-avance', 'btn-pausar-ensamble-dinamico'].forEach(id => {
            const btn = document.getElementById(id);
            if (!btn) return;
            btn.disabled = cargando;
            if (cargando) {
                if (!btn.dataset.originalHtml) btn.dataset.originalHtml = btn.innerHTML;
                btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status"></span>Procesando...';
            } else if (btn.dataset.originalHtml) {
                btn.innerHTML = btn.dataset.originalHtml;
                delete btn.dataset.originalHtml;
            }
        });
    },

    reportarAvance: async function (estado = 'EN_PROCESO') {
        // Guardia anti doble-clic/doble-envío: bloquea reentradas mientras
        // una petición ya está en vuelo (evita duplicar descuentos de stock).
        if (this.isSubmitting) {
            console.warn('[Ensamble] Envío en curso, se ignora el clic duplicado.');
            return;
        }
        this.isSubmitting = true;
        this._setBotonesEnvioCargando(true);

        try {
        const rawIdCodigo = document.getElementById('reporte-producto-manual').value;
        // Normalización: Eliminar prefijo FR-
        const idCodigo = rawIdCodigo.replace(/^FR-/i, '').trim();
        const cantidad = parseInt(document.getElementById('reporte-cantidad').value) || 0;
        const responsable = document.getElementById('responsable').value;

        if (!idCodigo || (cantidad < 0 && estado !== 'PAUSADO') || !responsable) {
            mostrarNotificacion('Faltan datos obligatorios', 'error');
            return;
        }

        // ID de Ensamble Único para la operación
        if (!this.sessionId) {
            this.sessionId = 'ENS-' + Math.random().toString(36).substr(2, 9).toUpperCase();
            this.startTime = new Date();
        }

        const commonData = {
            id_ensamble: this.sessionId,
            id_prog: document.getElementById('reporte-id-prog').value || null,
            responsable: responsable,
            estado: estado,
            op_numero: document.getElementById('reporte-op').value,
            fecha: document.getElementById('reporte-fecha').value,
            pnc: parseInt(document.getElementById('reporte-pnc').value) || 0,
            pnc_detalles: this.pncDetalles,
            observaciones: document.getElementById('reporte-observaciones').value,
            buje_origen: document.getElementById('reporte-buje-origen').value.replace(/^FR-/i, ''),
        };

        const registrosPayload = [];

        // 1. Registro del Producto Final (Entrada a Almacén)
        registrosPayload.push({
            ...commonData,
            id_codigo: idCodigo, // El Ancla
            buje_ensamble: idCodigo, // El Detalle
            cantidad: cantidad,
            qty: 1, // Ratio del producto final es siempre 1
            almacen_destino: document.getElementById('reporte-almacen-destino').value,
            almacen_para_descargar: null,
            es_final: true
        });

        // 2. Registros de Componentes (Salida de Almacén) - Solo si es FINALIZADO
        if (estado === 'FINALIZADO') {
            document.querySelectorAll('.bom-checkbox:checked').forEach(chk => {
                const codComp = chk.value;
                const compInfo = (this.currentBOM || []).find(c => c.codigo_inventario === codComp);
                const ratio = compInfo ? parseFloat(compInfo.cantidad_por_unidad || 1) : 1;

                registrosPayload.push({
                    ...commonData,
                    id_codigo: idCodigo, // El Ancla
                    buje_ensamble: codComp, // El Detalle
                    cantidad: cantidad * ratio,
                    qty: ratio, // Guardamos el ratio/factor del componente
                    almacen_para_descargar: document.getElementById('reporte-almacen-origen').value,
                    almacen_destino: null,
                    es_final: false
                });
            });
        }

        try {
            console.log(`📤 [Ensamble] Enviando ${registrosPayload.length} registros`, registrosPayload);

            mostrarLoading(true, estado === 'FINALIZADO' ? 'Procesando multi-registro e inventario...' : 'Guardando avance...');
            const res = await fetch('/api/ensamble/reportar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ registros: registrosPayload, checklist: this.checklistEstados })
            });
            const data = await res.json();
            mostrarLoading(false);

            if (data && data.success) {
                if (estado === 'FINALIZADO') {
                    // 1. Matar sesión fantasma inmediatamente
                    this.sesionActiva = false;
                    this.sessionId = null;
                    if (this.timerInterval) clearInterval(this.timerInterval);
                    localStorage.removeItem('ensamble_session_id');
                    localStorage.removeItem('ensamble_session_data');

                    // 2. Cierre FORZOSO del modal (blindado con try/catch)
                    try {
                        // Intentar con el ID real del HTML
                        ['modalReporteEnsamble', 'modalEnsamble'].forEach(id => {
                            const el = document.getElementById(id);
                            if (el) {
                                const inst = bootstrap.Modal.getInstance(el);
                                if (inst) inst.hide();
                            }
                        });
                        // Fallback: ocultar backdrop si quedó colgado
                        document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
                        document.body.classList.remove('modal-open');
                        document.body.style.removeProperty('padding-right');
                    } catch (modalErr) {
                        console.warn('[Ensamble] Error cerrando modal:', modalErr);
                    }

                    // 3. Reset y ocultar formulario
                    this.resetFormulario(true);
                    const cont = document.getElementById('form-reporte-ensamble-container');
                    if (cont) cont.style.display = 'none';

                    // 4. Refrescar lista de metas
                    this.listarTareasPendientes();
                    if (typeof ModuloMesControl !== 'undefined' && ModuloMesControl.cargarProgramaciones) {
                        ModuloMesControl.cargarProgramaciones();
                    }

                    // Recargar productos reactivamente para actualizar stock
                    if (window.DataReloadHelpers && window.DataReloadHelpers.recargarProductos) {
                        window.DataReloadHelpers.recargarProductos().catch(err => console.error("[Ensamble] Error actualizando stock:", err));
                    }

                    // 5. Notificación de éxito
                    Swal.fire({
                        icon: 'success',
                        title: '¡Reporte Finalizado!',
                        text: 'Inventario actualizado y programación cerrada.',
                        timer: 2500,
                        showConfirmButton: false
                    });
                } else {
                    this.sesionActiva = true;
                    this.bloquearFormulario(true);
                    this.actualizarUIBotones();
                    if (!this.timerInterval) this.timerInterval = setInterval(() => this.actualizarTimer(), 1000);

                    // Recargar productos reactivamente para actualizar stock de avance parcial si aplica
                    if (window.DataReloadHelpers && window.DataReloadHelpers.recargarProductos) {
                        window.DataReloadHelpers.recargarProductos().catch(err => console.error("[Ensamble] Error actualizando stock parcial:", err));
                    }

                    mostrarNotificacion('Avance parcial guardado.', 'success');
                }
            } else {
                mostrarNotificacion(data.error || 'Error en el servidor', 'error');
            }
        } catch (e) {
            mostrarLoading(false);
            console.error('[Ensamble] Error en reporte:', e);
            mostrarNotificacion('Error de conexión con el servidor', 'error');
        }
        } finally {
            this.isSubmitting = false;
            this._setBotonesEnvioCargando(false);
        }
    },

    pausarReporte: async function () {
        if (this.isSubmitting) {
            console.warn('[Ensamble] Envío en curso, se ignora el clic duplicado.');
            return;
        }
        const nuevoEstado = this.enPausa ? 'TRABAJANDO' : 'PAUSADO';
        await this.reportarAvance(nuevoEstado);
        this.enPausa = !this.enPausa;
        this.actualizarUIBotones();
    },


    resetFormulario: function (fullReset = true) {
        if (fullReset) {
            const form = document.getElementById('form-reporte-ensamble');
            if (form) form.reset();

            const idProg = document.getElementById('reporte-id-prog');
            if (idProg) idProg.value = '';

            const bomContainer = document.getElementById('reporte-bom-check-container');
            if (bomContainer) bomContainer.innerHTML = '<div class="col-12 text-center text-muted py-2">Selecciona un producto.</div>';

            this.pncDetalles = [];
            this.ocultarChecklist(); // Sin tarea seleccionada no hay id_prog contra el cual guardar el checklist
            this.bloquearFormulario(false);
            this.actualizarUIBotones();

            const hoy = new Date().toISOString().split('T')[0];
            const fechaInput = document.getElementById('reporte-fecha');
            if (fechaInput) fechaInput.value = hoy;
            this.intentarAutoSeleccionarResponsable();
        }
        if (!this.sesionActiva) {
            const container = document.getElementById('form-reporte-ensamble-container');
            if (container) container.style.display = 'none';
        }
    },

    abrirModalPNC: async function () {
        // 1. Obtener id_codigo actual
        const rawIdCodigo = document.getElementById('reporte-producto-manual').value;
        const idCodigo = rawIdCodigo ? rawIdCodigo.replace(/^FR-/i, '').trim() : null;

        if (!idCodigo) {
            mostrarNotificacion('Seleccione un producto primero', 'warning');
            return;
        }

        mostrarLoading(true, "Consultando componentes (BOM)...");
        const bomData = await fetchData(`/api/ensamble/bom_stock/${idCodigo}`);

        // 2. Cargar criterios dinámicos
        let opcionesHTML = '<option value="">Seleccione defecto...</option>';
        let criteriosProcesados = [];
        try {
            const respuestaCriterios = await fetchData('/api/pnc/criterios');
            // El catálogo viene agrupado por área: { success, criterios: { ensamble: [...], ... } }
            criteriosProcesados = (respuestaCriterios && respuestaCriterios.criterios && respuestaCriterios.criterios.ensamble) || [];

            criteriosProcesados.forEach(crit => {
                // Si el criterio es un objeto, extraer el nombre o valor; si es un string, usarlo directo
                let valorCriterio = (typeof crit === 'object' && crit !== null) ? (crit.nombre || crit.criterio || crit.descripcion) : crit;

                if (valorCriterio) {
                    // --- FIX DE ENCODING ANTIFANTASMA ---
                    try {
                        // Forzar la conversión de caracteres rotos de UTF-8 legacy a texto limpio
                        valorCriterio = decodeURIComponent(escape(valorCriterio));
                    } catch (e) {
                        // Si el string ya venía limpio y falla escape(), usar el valor original sin alterar
                    }

                    opcionesHTML += `<option value="${valorCriterio}">${valorCriterio}</option>`;
                }
            });
        } catch (e) {
            console.error('Error cargando criterios:', e);
        }
        mostrarLoading(false);

        if (!bomData || !bomData.success || !bomData.data.componentes || bomData.data.componentes.length === 0) {
            mostrarNotificacion('No se encontró BOM para este producto. Agregue manualmente.', 'warning');
            return;
        }

        // 3. Inyectar dinámicamente HTML por componente
        let htmlFilas = '<div class="table-responsive"><table class="table table-sm align-middle text-start"><thead><tr><th>Componente</th><th style="width: 100px;">Cantidad</th><th>Motivo de Rechazo</th></tr></thead><tbody>';

        bomData.data.componentes.forEach(c => {
            htmlFilas += `
                <tr>
                    <td class="fw-bold small">${c.componente}<br><span class="text-muted" style="font-size:0.75rem">${c.codigo_inventario}</span></td>
                    <td>
                        <input type="number" class="form-control form-control-sm pnc-bom-cantidad" data-codigo="${c.codigo_inventario}" min="0" value="0">
                    </td>
                    <td>
                        <select class="form-select form-select-sm pnc-bom-criterio" data-codigo="${c.codigo_inventario}">
                            ${opcionesHTML}
                        </select>
                    </td>
                </tr>
            `;
        });
        htmlFilas += '</tbody></table></div>';

        console.log("📋 Criterios procesados para el modal PNC:", criteriosProcesados);

        // Modal Estructurado con SweetAlert
        const { value: formValues } = await Swal.fire({
            title: 'Reporte Estructurado de PNC',
            html: htmlFilas,
            width: '800px',
            showCancelButton: true,
            confirmButtonText: '<i class="fas fa-save"></i> Guardar PNC',
            cancelButtonText: 'Cancelar',
            preConfirm: () => {
                const resultados = [];
                let totalPnc = 0;

                bomData.data.componentes.forEach(c => {
                    const cantInput = document.querySelector(`.pnc-bom-cantidad[data-codigo="${c.codigo_inventario}"]`);
                    const critSelect = document.querySelector(`.pnc-bom-criterio[data-codigo="${c.codigo_inventario}"]`);

                    const cantidad = parseInt(cantInput.value) || 0;
                    if (cantidad > 0) {
                        const criterio = critSelect.value;
                        if (!criterio) {
                            Swal.showValidationMessage(`Seleccione un motivo de rechazo para ${c.codigo_inventario}`);
                            return false;
                        }
                        resultados.push({
                            codigo_componente: c.codigo_inventario,
                            cantidad: cantidad,
                            criterio: criterio
                        });
                        totalPnc += cantidad;
                    }
                });
                return { resultados, totalPnc };
            }
        });

        if (formValues) {
            // 4. Guardar arreglo de objetos
            this.pncDetalles = formValues.resultados;

            // Actualizar el valor total en el input del layout global
            const inputPnc = document.getElementById('reporte-pnc');
            if (inputPnc) {
                inputPnc.value = formValues.totalPnc;
                inputPnc.dispatchEvent(new Event('change', { bubbles: true }));
            }

            if (formValues.totalPnc > 0) {
                mostrarNotificacion(`Se registraron ${formValues.totalPnc} PNC desglosados`, 'success');
            }
        }
    },

    cargarCriteriosPNC: async function () {
        try {
            const select = document.getElementById('crit-inyeccion');
            if (!select) return;

            // Fetch de criterios específicos de ENSAMBLE
            const response = await fetch('/api/obtener_criterios_pnc/ensamble');
            const criterios = await response.json();

            if (criterios && Array.isArray(criterios)) {
                select.innerHTML = '<option value="">Seleccionar Defecto...</option>';
                criterios.forEach(c => {
                    const opt = document.createElement('option');
                    opt.value = c.toUpperCase();
                    opt.textContent = c;
                    select.appendChild(opt);
                });
                console.log('✅ [Ensamble] Criterios PNC sincronizados con el servidor.');
            }
        } catch (e) {
            console.error('❌ Error cargando criterios PNC para ensamble:', e);
        }
    },

    listarTareasPendientes: async function () {
        try {
            const res = await fetchData('/api/ensamble/tareas_pendientes');
            const container = document.getElementById('tareas-pendientes-container');
            if (!res || !res.success) return;

            if (res.data.length === 0) {
                container.innerHTML = '<div class="text-center py-4 bg-light rounded-4">No hay tareas pendientes</div>';
                return;
            }

            const leyendaChecklist = `
                <div class="d-flex flex-wrap gap-3 small text-muted mb-3">
                    <span><span class="checklist-dot" style="background:#1f7a44;"></span> Hecho</span>
                    <span><span class="checklist-dot" style="background:#96650b;"></span> Pendiente</span>
                    <span><span class="checklist-dot" style="background:#adb5bd;"></span> No aplica</span>
                </div>
            `;

            let html = leyendaChecklist + '<div class="row g-3">';
            res.data.forEach(t => {
                const porc = Math.round((t.cantidad_realizada / t.cantidad_objetivo) * 100);
                const porcColor = porc >= 80 ? '#16a34a' : (porc >= 40 ? '#f8961e' : '#4361ee');

                const checklist = t.checklist || checklistPorDefecto();
                const dots = PROCESOS_CHECKLIST.map(p => {
                    const estado = checklist[p.key] || 'PENDIENTE';
                    const color = estado === 'HECHO' ? '#1f7a44' : (estado === 'NO_APLICA' ? '#adb5bd' : '#96650b');
                    return `<span class="checklist-dot" title="${p.label}: ${CHECKLIST_UI[estado].label}" style="background:${color};"></span>`;
                }).join('');
                const aplicables = PROCESOS_CHECKLIST.filter(p => checklist[p.key] !== 'NO_APLICA');
                const hechos = aplicables.filter(p => checklist[p.key] === 'HECHO').length;
                const checklistLabel = aplicables.length === 0 ? 'N/A' : `${hechos}/${aplicables.length}`;

                html += `
                    <div class="col-12 col-md-6 col-lg-4">
                        <div class="card task-card-ensamble shadow-sm border-0 rounded-4 hover-lift cursor-pointer bg-white h-100" style="border-left: 4px solid ${porcColor} !important;" onclick="ModuloEnsamble.seleccionarTarea(${JSON.stringify(t).replace(/"/g, '&quot;')})">
                            <div class="card-body p-3">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span class="badge rounded-pill px-3 py-2" style="background:#4361ee; color:#fff; font-weight:600;">TAREA #${t.id_prog}</span>
                                    <span class="badge rounded-pill px-3 py-2" style="background:${porcColor}; color:#fff; font-weight:600;">${porc}%</span>
                                </div>
                                <h5 class="fw-bold mb-1">${t.id_codigo}</h5>
                                <div class="progress mb-2" style="height: 8px; border-radius: 4px;">
                                    <div class="progress-bar" style="width: ${porc}%; background:${porcColor};"></div>
                                </div>
                                <p class="small text-muted mb-3">Faltan: <span class="text-danger fw-bold">${t.faltante}</span> / Objetivo: ${t.cantidad_objetivo}</p>
                                <div class="checklist-strip d-flex align-items-center justify-content-between mb-3">
                                    <span class="d-flex gap-1 align-items-center">${dots}</span>
                                    <span class="small fw-bold text-muted"><i class="fas fa-tasks me-1"></i>${checklistLabel}</span>
                                </div>
                                <div class="d-flex align-items-center justify-content-between small fw-bold" style="color:#4361ee;">
                                    <span><i class="fas fa-clipboard-check me-1"></i> Reportar avance</span>
                                    <i class="fas fa-chevron-right"></i>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            container.innerHTML = html;
        } catch (e) { console.error(e); }
    },

    // Muestra en un modal las metas de ensamble ya completadas al 100%
    // (estado COMPLETADO), reutilizando el mismo listado de /api/ensamble/programacion.
    mostrarCompletadas: async function () {
        try {
            mostrarLoading(true, 'Cargando metas completadas...');
            const res = await fetchData('/api/ensamble/programacion');
            mostrarLoading(false);
            if (!res || !res.success) {
                mostrarNotificacion('No se pudo cargar el listado', 'error');
                return;
            }

            const completadas = res.data.filter(p => p.estado === 'COMPLETADO');

            let html;
            if (completadas.length === 0) {
                html = '<div class="text-center text-muted py-4">Aún no hay metas completadas al 100%.</div>';
            } else {
                html = '<div class="table-responsive text-start"><table class="table table-sm align-middle"><thead><tr><th>Fecha</th><th>Código</th><th class="text-end">Objetivo</th><th class="text-end">Realizado</th></tr></thead><tbody>';
                completadas.forEach(p => {
                    html += `
                        <tr>
                            <td>${p.fecha_programada || '-'}</td>
                            <td class="fw-bold">${p.id_codigo}</td>
                            <td class="text-end">${p.cantidad_objetivo}</td>
                            <td class="text-end text-success fw-bold">${p.cantidad_realizada}</td>
                        </tr>
                    `;
                });
                html += '</tbody></table></div>';
            }

            Swal.fire({
                title: `<i class="fas fa-check-circle text-success me-2"></i>Metas Completadas (${completadas.length})`,
                html,
                width: '700px',
                confirmButtonText: 'Cerrar',
                confirmButtonColor: '#16a34a'
            });
        } catch (e) {
            mostrarLoading(false);
            console.error(e);
            mostrarNotificacion('Error consultando metas completadas', 'error');
        }
    },

    // Panel "Historial de Metas" (pestaña Programación): pendientes de
    // cualquier día + lo completado HOY -- ver EnsambleService.listar_historial_metas.
    // No es el archivo completo (eso es "Completadas"), por eso no acota con
    // slice() acá: el propio backend ya devuelve un conjunto acotado y con sentido.
    listarProgramacion: async function () {
        try {
            const res = await fetchData('/api/ensamble/historial_metas');
            const container = document.getElementById('prog-lista-container');
            if (!res || !res.success) return;

            if (res.data.length === 0) {
                container.innerHTML = '<div class="p-3 text-center text-muted small">Sin actividad pendiente ni completada hoy.</div>';
                return;
            }

            let html = '<div class="p-3">';
            res.data.forEach(p => {
                const completada = p.estado === 'COMPLETADO';
                const porc = Math.min(100, Math.round((p.cantidad_realizada / p.cantidad_objetivo) * 100));
                html += `
                    <div class="mb-2 p-2 border-bottom">
                        <div class="d-flex justify-content-between align-items-center small">
                            <span>${p.id_codigo}${completada ? ' <span class="badge rounded-pill ms-1" style="background:#e3f3e8;color:#1f7a44;font-weight:600;">✓ Completada hoy</span>' : ''}</span>
                            <span>${p.cantidad_realizada}/${p.cantidad_objetivo}</span>
                        </div>
                        <div class="progress" style="height: 3px;">
                            <div class="progress-bar ${completada ? 'bg-success' : ''}" style="width: ${porc}%"></div>
                        </div>
                    </div>
                `;
            });
            html += '</div>';
            container.innerHTML = html;
        } catch (e) { console.error(e); }
    },

    guardarProgramacion: async function () {
        const idCodigo = document.getElementById('prog-producto').value;
        const cantidad = document.getElementById('prog-cantidad').value;
        const fecha = document.getElementById('prog-fecha').value;
        if (!idCodigo || !cantidad || !fecha) return mostrarNotificacion('Faltan datos', 'error');

        try {
            // Si ya existe una meta COMPLETADA para este producto+fecha, el
            // backend crea una meta nueva independiente en vez de reabrir la
            // completada -- se avisa para que el usuario no piense que está
            // editando la meta ya cumplida.
            const existentes = await fetchData('/api/ensamble/programacion');
            const yaCompletada = existentes?.success && existentes.data.some(
                p => p.id_codigo === idCodigo && p.fecha_programada === fecha && p.estado === 'COMPLETADO'
            );
            if (yaCompletada) {
                const confirmacion = await Swal.fire({
                    icon: 'warning',
                    title: 'Ya hay una meta completada',
                    text: `${idCodigo} ya tiene una meta COMPLETADA para el ${fecha}. Se creará una meta nueva e independiente (no se modifica la ya cumplida). ¿Continuar?`,
                    showCancelButton: true,
                    confirmButtonText: 'Sí, crear meta nueva',
                    cancelButtonText: 'Cancelar'
                });
                if (!confirmacion.isConfirmed) return;
            }

            mostrarLoading(true);
            const res = await fetch('/api/ensamble/programacion', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id_codigo: idCodigo, cantidad_objetivo: cantidad, fecha_programada: fecha })
            });
            const data = await res.json();
            mostrarLoading(false);
            if (data.success) {
                mostrarNotificacion('Meta programada', 'success');
                this.listarProgramacion();
                this.listarTareasPendientes();
            }
        } catch (e) { mostrarLoading(false); console.error(e); }
    },

    configurarEventos: function () {
        document.getElementById('btn-guardar-prog')?.addEventListener('click', () => this.guardarProgramacion());
        document.getElementById('btn-reportar-avance')?.addEventListener('click', () => this.reportarAvance('FINALIZADO'));
        document.getElementById('btn-registro-manual-directo')?.addEventListener('click', () => this.abrirModalManual());
        document.getElementById('btn-manual-mode')?.addEventListener('click', () => this.abrirModalManual());
        document.getElementById('btn-detalle-pnc')?.addEventListener('click', () => this.abrirModalPNC());
        document.getElementById('btn-cancelar-reporte')?.addEventListener('click', () => this.resetFormulario(true));
        document.getElementById('btn-ver-completadas')?.addEventListener('click', () => this.mostrarCompletadas());

        document.getElementById('btn-ia-voice-ensamble')?.addEventListener('click', () => this.toggleGrabacionVoz());
        document.getElementById('btn-cancelar-voz-ensamble')?.addEventListener('click', () => this.cancelarGrabacionVoz());
        document.getElementById('btn-forzar-relleno-ensamble')?.addEventListener('click', () => {
            if (this.ultimoJsonVozEnsamble) {
                this.poblarFormularioDesdeVoz(this.ultimoJsonVozEnsamble);
            }
        });

        // El campo PNC solo se pinta de alerta cuando de verdad hay defectos
        // reportados (>0) -- en 0 se ve neutral para no alarmar por defecto.
        document.getElementById('reporte-pnc')?.addEventListener('input', (e) => {
            const conDefectos = (parseInt(e.target.value) || 0) > 0;
            e.target.classList.toggle('border-danger', conDefectos);
            e.target.classList.toggle('text-danger', conDefectos);
            e.target.classList.toggle('bg-danger', conDefectos);
            e.target.classList.toggle('bg-opacity-10', conDefectos);
        });

        document.getElementById('prog-cantidad')?.addEventListener('input', () => {
            const idCodigo = document.getElementById('prog-producto').value;
            if (idCodigo) this.consultarBOMStock(idCodigo);
        });

        // Trigger manual BOM load on input change
        document.getElementById('reporte-producto-manual')?.addEventListener('change', (e) => {
            const val = e.target.value.trim().toUpperCase();
            if (val) {
                e.target.value = val;
                this.seleccionarProductoManual({ codigo_sistema: val });
            }
        });
    },

    // --- PUERTO PARA IA DE VOZ ---
    ultimoJsonVozEnsamble: null,
    poblarFormularioDesdeVoz: async function (data) {
        if (this.sesionActiva) {
            Swal.fire('Sesión Activa', 'Termina el trabajo actual antes de cargar uno por voz.', 'warning');
            return;
        }

        console.log('🎤 [Voz] Procesando datos recibidos:', data);

        // 1. Abrir formulario
        this.abrirModalManual();

        // Helper for triggering changes
        const triggerChange = (el) => {
            if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
        };

        // 2. Poblar campos
        if (data.id_codigo) {
            const inputProd = document.getElementById('reporte-producto-manual');
            if (inputProd) {
                inputProd.value = data.id_codigo.toUpperCase();
                triggerChange(inputProd);
                await this.seleccionarProductoManual({ codigo_sistema: data.id_codigo.toUpperCase() });
            }
        }

        if (data.cantidad) {
            const inputCant = document.getElementById('reporte-cantidad');
            if (inputCant) {
                inputCant.value = data.cantidad;
                triggerChange(inputCant);
            }
        }

        // 3. Manejo de Responsable Inteligente (Voz o Sesión)
        const inputResp = document.getElementById('responsable');
        const inputDisplay = document.getElementById('responsable-display');
        const userLogged = this.usuarioActual;

        if (data.responsable && data.responsable.trim().length > 2) {
            // Aplicar búsqueda inteligente (Match parcial -> Nombre completo)
            const nombreFinal = this.buscarMatchResponsable(data.responsable);
            console.log(`🎤 [Voz] Responsable procesado: ${data.responsable} -> ${nombreFinal}`);

            if (inputResp) {
                inputResp.value = nombreFinal;
                triggerChange(inputResp);
            }
            if (inputDisplay) inputDisplay.value = nombreFinal;
        } else {
            console.log(`🎤 [Voz] No se detectó responsable. Manteniendo usuario logueado: ${userLogged}`);
            if (inputResp && !inputResp.value) {
                inputResp.value = userLogged;
                triggerChange(inputResp);
            }
            if (inputDisplay && !inputDisplay.value) inputDisplay.value = userLogged;
        }

        // Asegurar que sea editable siempre
        if (inputResp) {
            inputResp.readOnly = false;
            inputResp.classList.remove('bg-light');
        }

        if (data.op_numero) {
            const inputOp = document.getElementById('reporte-op');
            if (inputOp) {
                inputOp.value = data.op_numero;
                triggerChange(inputOp);
            }
        }

        if (data.fecha) {
            const inputFecha = document.getElementById('reporte-fecha');
            if (inputFecha) {
                inputFecha.value = this.formatearFechaParaInput(data.fecha);
                triggerChange(inputFecha);
            }
        }

        // Mapeo de Horas
        if (data.hora_inicio) {
            const inputHoraIni = document.getElementById('reporte-hora-inicio');
            if (inputHoraIni) {
                inputHoraIni.value = data.hora_inicio;
                triggerChange(inputHoraIni);
            }
        }
        if (data.hora_fin) {
            const inputHoraFin = document.getElementById('reporte-hora-fin');
            if (inputHoraFin) {
                inputHoraFin.value = data.hora_fin;
                triggerChange(inputHoraFin);
            }
        }

        // Trigger de PNC
        if (data.pnc > 0) {
            const inputPnc = document.getElementById('reporte-pnc');
            if (inputPnc) {
                inputPnc.value = data.pnc;
                triggerChange(inputPnc);
            }
            // Ejecutar click en el botón que abre el modal de criterios
            const btnPnc = document.getElementById('btn-detalle-pnc');
            if (btnPnc) btnPnc.click();
        }

        // Componentes: Si contiene "TODOS", marcar todos los checkboxes del BOM
        if (data.componentes_seleccionados && data.componentes_seleccionados.toUpperCase().includes("TODOS")) {
            setTimeout(() => {
                document.querySelectorAll('.bom-checkbox').forEach(chk => {
                    chk.checked = true;
                    triggerChange(chk);
                });
                console.log('✅ [Voz] BOM marcado completamente (TODOS).');
            }, 500); // Pequeño delay para asegurar renderizado final
        }

        console.log('🎤 [Voz] Formulario poblado con éxito.');
    },

    // --- LÓGICA DE GRABACIÓN DE VOZ (IA) ---
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,

    toggleGrabacionVoz: async function () {
        if (this.isRecording) {
            this.detenerGrabacion();
        } else {
            this.iniciarGrabacion();
        }
    },

    iniciarGrabacion: async function () {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

            const options = { mimeType: 'audio/webm;codecs=opus' };
            if (MediaRecorder.isTypeSupported(options.mimeType)) {
                this.mediaRecorder = new MediaRecorder(stream, options);
            } else {
                console.warn('[Ensamble] audio/webm;codecs=opus no soportado, usando fallback');
                this.mediaRecorder = new MediaRecorder(stream);
            }

            this.audioChunks = [];

            this.mediaRecorder.ondataavailable = e => {
                if (e.data.size > 0) this.audioChunks.push(e.data);
            };

            this.mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                await this.procesarAudioIA(audioBlob);
            };

            this.mediaRecorder.start();
            this.isRecording = true;

            // UI Feedback
            const btn = document.getElementById('btn-ia-voice-ensamble');
            if (btn) {
                // Remove animation class before triggering a reflow, then add back
                btn.classList.add('bg-danger');
                btn.style.background = ''; // Override gradient
                btn.innerHTML = '<i class="fas fa-stop-circle pulse"></i> Grabando...';
            }

            document.getElementById('btn-cancelar-voz-ensamble')?.classList.remove('d-none');
            document.getElementById('btn-forzar-relleno-ensamble')?.classList.add('d-none');

            mostrarNotificacion('Escuchando...', 'info');

        } catch (err) {
            console.error('[Ensamble] Error accediendo al micrófono:', err);
            Swal.fire('Error', 'No se pudo acceder al micrófono. Verifica los permisos de tu navegador.', 'error');
        }
    },

    cancelarGrabacionVoz: function () {
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            // Eliminar el onstop para evitar que se lance el fetch a la API de IA
            this.mediaRecorder.onstop = null;
            this.mediaRecorder.stop();

            // Liberar tracks
            this.mediaRecorder.stream.getTracks().forEach(t => t.stop());
            this.isRecording = false;
            this.audioChunks = [];

            const btn = document.getElementById('btn-ia-voice-ensamble');
            if (btn) {
                btn.classList.remove('bg-danger');
                btn.style.background = 'linear-gradient(135deg, #a855f7 0%, #7e22ce 100%)';
                btn.innerHTML = '<i class="fas fa-microphone"></i>';
            }

            document.getElementById('btn-cancelar-voz-ensamble')?.classList.add('d-none');
            mostrarNotificacion('Grabación cancelada', 'warning');
        }
    },

    detenerGrabacion: function () {
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
            // Free the stream
            this.mediaRecorder.stream.getTracks().forEach(t => t.stop());
            this.isRecording = false;

            const btn = document.getElementById('btn-ia-voice-ensamble');
            if (btn) {
                btn.classList.remove('bg-danger');
                btn.style.background = 'linear-gradient(135deg, #a855f7 0%, #7e22ce 100%)';
                btn.innerHTML = '<i class="fas fa-microphone"></i>';
            }
            document.getElementById('btn-cancelar-voz-ensamble')?.classList.add('d-none');
        }
    },

    procesarAudioIA: async function (audioBlob) {
        try {
            mostrarLoading(true, '🤖 Procesando audio con Gemini IA...');
            const formData = new FormData();
            formData.append('audio', audioBlob, 'ensamble_voz.webm');

            const res = await fetch('/api/ia/procesar-audio-ensamble', {
                method: 'POST',
                body: formData
            });
            const result = await res.json();
            mostrarLoading(false);

            if (result.success && result.data) {
                this.ultimoJsonVozEnsamble = result.data;
                document.getElementById('btn-forzar-relleno-ensamble')?.classList.remove('d-none');

                this.poblarFormularioDesdeVoz(result.data);
                mostrarNotificacion('IA: Formulario llenado exitosamente', 'success');
            } else {
                Swal.fire('Error de IA', result.error || 'No se pudo extraer información', 'error');
            }
        } catch (err) {
            mostrarLoading(false);
            console.error('[Ensamble] Error enviando audio:', err);
            Swal.fire('Error', 'Problema de conexión con el servidor IA.', 'error');
        }
    },

    // --- CAPTURA INFALIBLE: Método del Input Oculto ---
    intentarAutoSeleccionarResponsable: function () {
        const input = document.getElementById('responsable'); // ID REAL corregido
        const inputDisplay = document.getElementById('responsable-display'); // Cabecera reporte
        const hiddenInput = document.getElementById('current_user_fullname'); // Fuente de verdad

        if (!input || !hiddenInput) return;

        const nombreCompleto = hiddenInput.value;

        if (nombreCompleto && nombreCompleto.trim().length > 2) {
            // Asignar al formulario (SIN BLOQUEAR)
            input.value = nombreCompleto;
            input.readOnly = false;
            input.classList.remove('bg-light');

            // Asignar a la cabecera visual (si existe)
            if (inputDisplay) {
                inputDisplay.value = nombreCompleto;
            }

            console.log('✅ [Ensamble] Usuario capturado y asignado (editable):', nombreCompleto);
        }
    },

    validarFormulario: function () {
        const op = document.getElementById('reporte-op')?.value;
        const resp = document.getElementById('ensamble-responsable')?.value || document.getElementById('responsable')?.value;
        const qty = parseFloat(document.getElementById('reporte-cantidad')?.value || 0);
        const almacenDestino = document.getElementById('reporte-almacen-destino')?.value;
        const btn = document.getElementById('btn-reportar-avance');

        if (btn) {
            const esValido = op && resp && qty > 0 && almacenDestino;
            btn.disabled = !esValido;
            btn.style.opacity = esValido ? '1' : '0.6';
        }
    }
};

window.ModuloEnsamble = ModuloEnsamble;
window.initEnsamble = () => ModuloEnsamble.inicializar();
