/**
 * metals.js - Módulos de Producción FRIMETALS
 * Selector de procesos + formularios específicos por máquina
 */

const ModuloMetals = {
    productosData: [],
    procesoActual: null,
    graficoTiempos: null,
    graficoDefectos: null,
    graficoOk: null,
    graficoTendencia: null,
    graficoOperarios: null,

    // ----------------------------------------------------------------
    // Mapa de procesos → máquinas + campos específicos
    // ----------------------------------------------------------------
    PAGE_MAP: {
        'metals-torno': 'TORNO',
        'metals-laser': 'CORTADORA_LASER',
        'metals-soldadura': 'SOLDADURA',
        'metals-marcadora': 'MARCADORA_LASER',
        'metals-taladro': 'TALADRO',
        'metals-dobladora': 'DOBLADORA',
        'metals-pintura': 'PINTURA',
        'metals-zincado': 'ZINCADO',
        'metals-horno': 'HORNO',
        'metals-pulido-m': 'PULIDO'
    },

    PROCESOS: {
        'TORNO': {
            label: 'Torno',
            icon: 'fa-circle-notch',
            color: '#3b82f6',
            maquinas: ['TORNO-1', 'TORNO-2', 'TORNO-3', 'TORNO-4'],
            extraFields: ''
        },
        'CORTADORA_LASER': {
            label: 'Cortadora Laser',
            icon: 'fa-bolt',
            color: '#ef4444',
            maquinas: ['CORTADORA LASER'],
            extraFields: ''
        },
        'SOLDADURA': {
            label: 'Soldadura',
            icon: 'fa-fire',
            color: '#f59e0b',
            maquinas: ['SOLDADORA-1', 'SOLDADORA-2'],
            extraFields: ''
        },
        'MARCADORA_LASER': {
            label: 'Marcadora Laser',
            icon: 'fa-crosshairs',
            color: '#8b5cf6',
            maquinas: ['MARCADORA LASER'],
            extraFields: ''
        },
        'TALADRO': {
            label: 'Taladro',
            icon: 'fa-circle',
            color: '#10b981',
            maquinas: ['TALADRO-1', 'TALADRO-2'],
            extraFields: ''
        },
        'DOBLADORA': {
            label: 'Dobladora',
            icon: 'fa-angle-left',
            color: '#06b6d4',
            maquinas: ['DOBLADORA'],
            extraFields: ''
        },
        'PINTURA': {
            label: 'Pintura',
            icon: 'fa-paint-roller',
            color: '#ec4899',
            maquinas: ['CABINA PINTURA'],
            extraFields: ''
        },
        'ZINCADO': {
            label: 'Zincado',
            icon: 'fa-shield-alt',
            color: '#64748b',
            maquinas: ['CUBA ZINCADO'],
            extraFields: `
                <div class="form-group-metals">
                    <label class="label-metals"><i class="fas fa-truck"></i> Estado de Zincado</label>
                    <select id="extra-estado-zincado" class="input-metals">
                        <option value="ENVIADO_A_PROVEEDOR">Enviado a Proveedor</option>
                        <option value="RECIBIDO_DE_PROVEEDOR">Recibido de Proveedor</option>
                    </select>
                </div>
                <div class="form-group-metals">
                    <label class="label-metals"><i class="fas fa-file-invoice"></i> N° Remisión / Guía</label>
                    <input type="text" id="extra-remision" class="input-metals" placeholder="Ej: RM-0012">
                </div>`
        },
        'HORNO': {
            label: 'Horno',
            icon: 'fa-fire-alt',
            color: '#dc2626',
            maquinas: ['HORNO-1'],
            extraFields: ''
        },
        'PULIDO': {
            label: 'Pulido',
            icon: 'fa-certificate',
            color: '#7c3aed',
            maquinas: ['PULIDORA-1'],
            extraFields: ''
        }
    },

    // ----------------------------------------------------------------
    // Inicializar
    // ----------------------------------------------------------------
    inicializar: async function () {
        console.log('🏭 [Metals] Inicializando módulo de producción...');

        // 1. Cargar productos si no están en AppState
        if (!window.AppState.sharedData.productosMetals) {
            await this.cargarProductos();
        } else {
            this.productosData = window.AppState.sharedData.productosMetals;
        }

        // 2. Detectar qué proceso mostrar según la página actual
        const paginaActual = window.AppState.paginaActual;
        console.log(`🏭 [Metals] Página actual: ${paginaActual}`);

        if (paginaActual === 'metals-dashboard') {
            // Fix: Asegurar que el historial esté oculto al entrar al dashboard
            const historyPage = document.getElementById('metals-pedidos-page');
            if (historyPage) historyPage.style.display = 'none';
            
            this.cargarDashboard();
            return;
        }

        if (paginaActual === 'metals-produccion') {
            this.mostrarSelectorProcesos();
            return;
        }

        const procesoKey = this.PAGE_MAP[paginaActual];
        if (procesoKey) {
            this.abrirFormulario(procesoKey);
        } else {
            console.warn(`⚠️ [Metals] No hay proceso mapeado para la página: ${paginaActual}`);
            this.mostrarSelectorProcesos(); // Fallback
        }
    },

    desactivar: function () {
        // Resetear estado al salir
        this.procesoActual = null;
    },

    // ----------------------------------------------------------------
    // Selector de procesos (vista principal)
    // ----------------------------------------------------------------
    mostrarSelectorProcesos: function () {
        const contenedor = document.getElementById('metals-contenido');
        if (!contenedor) return;

        this.procesoActual = null;

        const tarjetas = Object.entries(this.PROCESOS).map(([key, proc]) => `
            <div class="metals-process-card" onclick="ModuloMetals.abrirFormulario('${key}')"
                style="border-top: 4px solid ${proc.color};">
                <div class="metals-card-icon" style="background: ${proc.color}20; color: ${proc.color};">
                    <i class="fas ${proc.icon} fa-2x"></i>
                </div>
                <div class="metals-card-label">${proc.label}</div>
                <div class="metals-card-machines">${proc.maquinas.join(' · ')}</div>
            </div>
        `).join('');

        contenedor.innerHTML = `
            <div class="metals-selector-header">
                <h2><i class="fas fa-hammer me-2"></i>Registro de Producción</h2>
                <p class="text-muted">Selecciona el proceso que realizaste</p>
            </div>
            <div class="metals-process-grid">
                ${tarjetas}
            </div>
        `;
    },

    // ----------------------------------------------------------------
    // Formulario por proceso
    // ----------------------------------------------------------------
    abrirFormulario: function (procesoKey) {
        const proc = this.PROCESOS[procesoKey];
        if (!proc) return;

        this.procesoActual = procesoKey;
        const contenedor = document.getElementById('metals-contenido');
        if (!contenedor) return;

        const maquinasOptions = proc.maquinas.map(m => `<option value="${m}">${m}</option>`).join('');
        const hoy = new Date().toISOString().split('T')[0];
        const responsable = window.AppState?.user?.name || '';

        contenedor.innerHTML = `
            <div class="metals-form-header" style="border-left: 5px solid ${proc.color}; padding-left: 16px; margin-bottom: 24px;">
                <button class="btn-back-metals" onclick="ModuloMetals.mostrarSelectorProcesos()">
                    <i class="fas fa-arrow-left me-2"></i>Volver
                </button>
                <h3 style="color: ${proc.color}; margin: 8px 0 4px;">
                    <i class="fas ${proc.icon} me-2"></i>${proc.label}
                </h3>
                <p class="text-muted small mb-0">Registrar actividad de producción</p>
            </div>

            <form id="form-metals" class="metals-form">
                <!-- Fila 1: Fecha + Responsable -->
                <div class="metals-form-grid">
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-calendar"></i> Fecha</label>
                        <input type="date" id="metals-fecha" class="input-metals" value="${hoy}" required>
                    </div>
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-user"></i> Operario</label>
                        <input type="text" id="metals-responsable" class="input-metals" value="${responsable}" readonly
                            style="background: #f1f5f9; color: #64748b; font-weight: 700;">
                    </div>
                </div>

                <!-- Fila 2: Máquina + Producto -->
                <div class="metals-form-grid">
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-cog"></i> Máquina</label>
                        <select id="metals-maquina" class="input-metals" required>
                            <option value="">-- Seleccionar --</option>
                            ${maquinasOptions}
                        </select>
                    </div>
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-hashtag"></i> ID Pedido (Opcional)</label>
                        <input type="text" id="metals-id-pedido" class="input-metals" placeholder="Ej: PED9644" list="lista-pedidos-sugeridos">
                        <datalist id="lista-pedidos-sugeridos"></datalist>
                    </div>
                </div>

                <!-- Fila 3: Producto -->
                <div class="metals-form-grid">
                    <div class="form-group-metals" style="position: relative; grid-column: 1 / -1;">
                        <label class="label-metals"><i class="fas fa-box"></i> Producto (Código / Descripción)</label>
                        <input type="text" id="metals-producto" class="input-metals" autocomplete="off"
                            placeholder="Buscar código o descripción..." required>
                        <div id="metals-sugerencias" class="autocomplete-suggestions"></div>
                    </div>
                </div>

                <!-- Fila 3: Horas -->
                <div class="metals-form-grid">
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-clock"></i> Hora Inicio</label>
                        <div style="display: flex; gap: 8px;">
                            <input type="time" id="metals-hora-inicio" class="input-metals" style="flex: 1;">
                            <button type="button" class="btn-hora-metals" onclick="ModuloMetals.marcarHora('inicio')" title="Marcar ahora">
                                <i class="fas fa-stopwatch"></i>
                            </button>
                        </div>
                    </div>
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-clock"></i> Hora Fin</label>
                        <div style="display: flex; gap: 8px;">
                            <input type="time" id="metals-hora-fin" class="input-metals" style="flex: 1;">
                            <button type="button" class="btn-hora-metals" onclick="ModuloMetals.marcarHora('fin')" title="Marcar ahora">
                                <i class="fas fa-flag-checkered"></i>
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Fila 4: Cantidad OK + PNC -->
                <div class="metals-form-grid">
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-check-circle" style="color: #10b981;"></i> Cantidad OK</label>
                        <input type="number" id="metals-cant-ok" class="input-metals" min="0" placeholder="0" required>
                    </div>
                    <div class="form-group-metals">
                        <label class="label-metals"><i class="fas fa-times-circle" style="color: #ef4444;"></i> PNC / Defectos</label>
                        <input type="number" id="metals-pnc" class="input-metals" min="0" placeholder="0" value="0">
                    </div>
                </div>

                <!-- Campos específicos del proceso -->
                <div class="metals-form-grid" id="metals-campos-extra">
                    ${proc.extraFields}
                </div>

                <!-- Observaciones -->
                <div class="form-group-metals" style="grid-column: 1 / -1;">
                    <label class="label-metals"><i class="fas fa-comment"></i> Observaciones</label>
                    <textarea id="metals-observaciones" class="input-metals" rows="2"
                        placeholder="Notas adicionales del proceso..."></textarea>
                </div>

                <!-- Botones -->
                <div class="metals-form-actions">
                    <button type="submit" class="btn-guardar-metals"
                        style="background: ${proc.color};">
                        <i class="fas fa-save me-2"></i>Guardar Registro
                    </button>
                    <button type="button" class="btn-limpiar-metals"
                        onclick="document.getElementById('form-metals').reset(); ModuloMetals.abrirFormulario('${procesoKey}')">
                        <i class="fas fa-redo me-1"></i>Limpiar
                    </button>
                </div>
            </form>
        `;

        // Inicializar autocomplete y eventos
        this.initAutocompleteProducto();
        document.getElementById('form-metals').addEventListener('submit', (e) => this.handleSubmit(e));

        // Auto-seleccionar máquina si solo hay una
        if (proc.maquinas.length === 1) {
            document.getElementById('metals-maquina').value = proc.maquinas[0];
        }
    },

    // ----------------------------------------------------------------
    // Autocomplete de productos
    // ----------------------------------------------------------------
    initAutocompleteProducto: function () {
        const input = document.getElementById('metals-producto');
        const suggestionsDiv = document.getElementById('metals-sugerencias');
        if (!input || !suggestionsDiv) return;

        // abrirFormulario() reconstruye #form-metals (y por tanto este input)
        // cada vez que se abre/limpia el formulario, así que el listener de
        // 'input' de aquí abajo queda automáticamente huérfano y reemplazado
        // -- sin fugas. Pero el listener de click en `document` de más abajo
        // NO se recrea junto con el form: si se liga aquí dentro, cada
        // apertura del formulario (cada visita a la página o cada "Limpiar")
        // apila un listener global más sobre `document`, permanente por el
        // resto de la sesión. Se liga UNA sola vez y busca los elementos
        // vigentes por id en cada click, no por closure.
        input.addEventListener('input', () => {
            const query = input.value.trim().toLowerCase();
            if (query.length < 2) {
                suggestionsDiv.classList.remove('active');
                return;
            }
            const terms = query.split(/\s+/).filter(t => t.length > 0);
            const resultados = this.productosData.filter(p => {
                const cod = String(p.codigo || '').toLowerCase();
                const desc = String(p.descripcion || '').toLowerCase();
                return terms.every(term => 
                    cod.includes(term) || 
                    desc.includes(term) ||
                    cod.replace(/[-\s]/g, '').includes(term.replace(/[-\s]/g, ''))
                );
            }).slice(0, 10);

            if (resultados.length === 0) {
                suggestionsDiv.innerHTML = '<div class="suggestion-item text-muted">Sin resultados</div>';
            } else {
                suggestionsDiv.innerHTML = resultados.map(p => {
                    const cod = p.codigo || 'S/C';
                    const desc = p.descripcion || 'Sin descripción';
                    return `
                        <div class="suggestion-item" data-cod="${cod}" data-desc="${desc}">
                            <strong>${cod}</strong> — ${desc}
                        </div>
                    `;
                }).join('');
                suggestionsDiv.querySelectorAll('.suggestion-item').forEach(div => {
                    div.addEventListener('click', () => {
                        input.value = `${div.dataset.cod} — ${div.dataset.desc}`;
                        input.dataset.codigo = div.dataset.cod;
                        input.dataset.descripcion = div.dataset.desc;
                        suggestionsDiv.classList.remove('active');
                    });
                });
            }
            suggestionsDiv.classList.add('active');
        });

        if (!this._outsideClickBound) {
            this._outsideClickBound = true;
            document.addEventListener('click', (e) => {
                const inputActual = document.getElementById('metals-producto');
                const sugerenciasActual = document.getElementById('metals-sugerencias');
                if (inputActual && sugerenciasActual &&
                    !inputActual.contains(e.target) && !sugerenciasActual.contains(e.target)) {
                    sugerenciasActual.classList.remove('active');
                }
            });
        }
    },

    // ----------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------
    marcarHora: function (tipo) {
        const ahora = new Date();
        const hh = String(ahora.getHours()).padStart(2, '0');
        const mm = String(ahora.getMinutes()).padStart(2, '0');
        const idInput = tipo === 'inicio' ? 'metals-hora-inicio' : 'metals-hora-fin';
        const el = document.getElementById(idInput);
        if (el) el.value = `${hh}:${mm}`;
    },

    cargarProductos: async function () {
        try {
            console.log("🔄 [Metals] Cargando lista maestra de productos...");
            // Aseguramos que use el endpoint específico que ya maneja la tabla metals_productos
            const res = await fetch('/api/metals/productos/listar');
            const data = await res.json();
            
            // Mapeo defensivo para asegurar llaves codigo y descripcion
            const raw = data.productos || data.items || [];
            this.productosData = raw.map(p => ({
                codigo: p.codigo || p.ID || '',
                descripcion: p.descripcion || p.nombre_producto || p.DESCRIPCION || 'Sin descripción',
                precio: parseFloat(p.precio) || 0
            }));
            
            window.AppState.sharedData.productosMetals = this.productosData; 
            console.log(`✅ [Metals] ${this.productosData.length} productos cargados satisfactoriamente.`);
        } catch (e) {
            console.error('❌ Error cargando productos metals:', e);
        }
    },

    cargarDashboard: async function () {
        console.log('📊 [Metals] Cargando Dashboard Dinámico...');
        try {
            const res = await fetch('/api/metals/dashboard/stats');
            const data = await res.json();
            
            // Debug Log solicitado: Ver en consola (F12) lo que llega del servidor
            console.log('📦 [Metals] Datos recibidos del Dashboard:', data);

            if (data.success) {
                const statsHoy = document.getElementById('metals-kpi-hoy');
                const statsActivos = document.getElementById('metals-kpi-activos');
                const statsPnc = document.getElementById('metals-kpi-pnc');
                const statsTop = document.getElementById('metals-kpi-procesos');
                const listaActividad = document.getElementById('metals-dashboard-history');

                if (statsHoy) statsHoy.textContent = data.piezas_hoy || 0;
                if (statsActivos) statsActivos.textContent = data.pedidos_activos || 0;
                if (statsPnc) statsPnc.textContent = data.pnc_hoy || 0;
                
                if (statsTop && data.proceso_top) {
                    statsTop.innerHTML = `
                        <div style="font-size: 0.85rem; line-height: 1.1;">
                            ${data.proceso_top.nombre}
                            <div class="small opacity-75" style="font-size: 0.65rem;">(${data.proceso_top.cantidad} pz)</div>
                        </div>
                    `;
                }

                // --- TABLA DE ACTIVIDAD RECIENTE (Mapeo Quirúrgico) ---
                if (listaActividad && data.actividad_reciente) {
                    listaActividad.innerHTML = data.actividad_reciente.map(a => `
                        <tr>
                            <td><span class="badge bg-light text-dark" style="font-size: 0.7rem;">#${a.id}</span></td>
                            <td class="small">${a.fecha}</td>
                            <td class="text-muted small">${a.maquina}</td>
                            <td class="fw-bold" style="font-size: 0.8rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${a.producto}">${a.producto}</td>
                            <td><span class="badge bg-primary-subtle text-primary border border-primary-subtle" style="font-size: 0.7rem;">${a.proceso}</span></td>
                            <td class="small"><i class="fas fa-user-circle me-1 text-muted"></i>${a.responsable}</td>
                            <td class="text-center fw-bold text-success">${a.cantidad_ok}</td>
                            <td class="text-center text-muted small">${a.tiempo}</td>
                            <td class="text-center text-danger fw-bold">${a.pnc}</td>
                        </tr>
                    `).join('') || '<tr><td colspan="9" class="text-center text-muted p-4">Sin actividad reciente</td></tr>';
                }

                // --- INTEGRACIÓN DE GRÁFICAS (Chart.js) ---
                if (data.graficas) {
                    this.renderizarGraficasMetals(data.graficas);
                }
            }
        } catch (e) {
            console.error('Error dashboard stats:', e);
        }
    },

    renderizarGraficasMetals: function(datos) {
        if (!window.Chart) return;

        // 1. Gráfica Producción OK por Máquina
        const ctxOk = document.getElementById('metals-chart-ok');
        if (ctxOk) {
            if (this.chartOk) this.chartOk.destroy();
            const labels = Object.keys(datos.produccion_maquina || {});
            const values = Object.values(datos.produccion_maquina || {});
            
            this.chartOk = new Chart(ctxOk, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Piezas OK',
                        data: values,
                        backgroundColor: '#0d6efd',
                        borderRadius: 5
                    }]
                },
                options: { responsive: true, maintainAspectRatio: false }
            });
        }

        // 2. Gráfica PNC por Proceso
        const ctxPnc = document.getElementById('metals-chart-defectos');
        if (ctxPnc) {
            if (this.chartPnc) this.chartPnc.destroy();
            const labels = Object.keys(datos.pnc_proceso || {});
            const values = Object.values(datos.pnc_proceso || {});

            this.chartPnc = new Chart(ctxPnc, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{
                        data: values,
                        backgroundColor: ['#dc3545', '#fd7e14', '#ffc107', '#20c997']
                    }]
                },
                options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
            });
        }

        // 3. Gráfica Tiempos por Máquina
        const ctxTiempos = document.getElementById('metals-chart-tiempos');
        if (ctxTiempos) {
            if (this.chartTiempos) this.chartTiempos.destroy();
            const labels = Object.keys(datos.tiempos_maquina || {});
            const values = Object.values(datos.tiempos_maquina || {});

            this.chartTiempos = new Chart(ctxTiempos, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Minutos Operativos',
                        data: values,
                        backgroundColor: '#6610f2',
                        borderRadius: 5
                    }]
                },
                options: { 
                    indexAxis: 'y',
                    responsive: true, 
                    maintainAspectRatio: false 
                }
            });
        }

        // 4. Participación por Operario
        const ctxOp = document.getElementById('metals-chart-operarios');
        if (ctxOp) {
            if (this.chartOp) this.chartOp.destroy();
            const labels = Object.keys(datos.participacion_operarios || {});
            const values = Object.values(datos.participacion_operarios || {});

            this.chartOp = new Chart(ctxOp, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{
                        data: values,
                        backgroundColor: ['#fd7e14', '#0d6efd', '#20c997', '#ffc107', '#6610f2']
                    }]
                },
                options: { 
                    responsive: true, 
                    maintainAspectRatio: false,
                    plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } }
                }
            });
        }

        // 5. Gráfica Rendimiento de Producción (Tendencia 7 días)
        const ctxTendencia = document.getElementById('metals-chart-tendencia');
        if (ctxTendencia) {
            if (this.chartTendencia) this.chartTendencia.destroy();
            
            // Ordenar fechas cronológicamente para la gráfica
            const fechas = Object.keys(datos.rendimiento_diario || {}).sort((a, b) => {
                const [da, ma, ya] = a.split('/').map(Number);
                const [db, mb, yb] = b.split('/').map(Number);
                return new Date(ya, ma - 1, da) - new Date(yb, mb - 1, db);
            });
            const values = fechas.map(f => datos.rendimiento_diario[f]);

            this.chartTendencia = new Chart(ctxTendencia, {
                type: 'line',
                data: {
                    labels: fechas,
                    datasets: [{
                        label: 'Piezas OK',
                        data: values,
                        borderColor: '#0dcaf0',
                        backgroundColor: 'rgba(13, 202, 240, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointBackgroundColor: '#0dcaf0',
                        pointRadius: 4
                    }]
                },
                options: { 
                    responsive: true, 
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { beginAtZero: true, ticks: { precision: 0 } }
                    }
                }
            });
        }
    },

    // ----------------------------------------------------------------
    // Recolectar campos extra del proceso actual
    // ----------------------------------------------------------------
    getExtraData: function () {
        const extras = {};
        const extraEl = document.getElementById('metals-campos-extra');
        if (!extraEl) return extras;

        extraEl.querySelectorAll('input, select').forEach(el => {
            if (el.id && el.value) {
                const key = el.id.replace('extra-', '').replace(/-/g, '_');
                extras[key] = el.value;
            }
        });
        return extras;
    },

    // ----------------------------------------------------------------
    // Submit
    // ----------------------------------------------------------------
    handleSubmit: async function (e) {
        e.preventDefault();

        const productoInput = document.getElementById('metals-producto');
        const codigo = productoInput?.dataset?.codigo;
        const descripcion = productoInput?.dataset?.descripcion || productoInput?.value || '';

        if (!codigo) {
            mostrarNotificacion('⚠️ Selecciona un producto del buscador', 'warning');
            productoInput.focus();
            return;
        }

        const horaInicio = document.getElementById('metals-hora-inicio')?.value;
        const horaFin = document.getElementById('metals-hora-fin')?.value;

        // Calcular tiempo en minutos
        let tiempoMin = null;
        if (horaInicio && horaFin) {
            const [h1, m1] = horaInicio.split(':').map(Number);
            const [h2, m2] = horaFin.split(':').map(Number);
            tiempoMin = (h2 * 60 + m2) - (h1 * 60 + m1);
            if (tiempoMin < 0) tiempoMin += 1440; // cruce de medianoche
        }

        const proc = this.PROCESOS[this.procesoActual];

        const payload = {
            proceso: this.procesoActual,
            proceso_label: proc?.label || this.procesoActual,
            maquina: document.getElementById('metals-maquina')?.value,
            fecha: document.getElementById('metals-fecha')?.value,
            responsable: document.getElementById('metals-responsable')?.value,
            codigo_producto: codigo,
            descripcion_producto: descripcion,
            hora_inicio: horaInicio,
            hora_fin: horaFin,
            tiempo_min: tiempoMin,
            cantidad_ok: document.getElementById('metals-cant-ok')?.value,
            pnc: document.getElementById('metals-pnc')?.value || '0',
            id_pedido: document.getElementById('metals-id-pedido')?.value || '',
            observaciones: document.getElementById('metals-observaciones')?.value || '',
            campos_extra: this.getExtraData()
        };

        try {
            mostrarLoading(true);
            const response = await fetch('/api/metals/produccion/registrar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const res = await response.json();
            mostrarLoading(false);

            if (res.success) {
                mostrarNotificacion(`✅ Registro guardado — ${proc?.label}`, 'success');
                
                // Refresco silencioso del dashboard en segundo plano
                this.cargarDashboard();
                
                // Volver al selector o limpiar
                setTimeout(() => this.inicializar(), 1200);
            } else {
                mostrarNotificacion(`❌ Error: ${res.message}`, 'error');
            }
        } catch (err) {
            mostrarLoading(false);
            console.error('Error submit metals:', err);
            mostrarNotificacion('Error de conexión', 'error');
        }
    },

    // ----------------------------------------------------------------
    // Gráficos y Análisis
    // ----------------------------------------------------------------
    parseTiempoAMinutos: function (tiempoStr) {
        if (!tiempoStr) return 0;
        let mins = 0;
        const hMatch = tiempoStr.match(/(\d+)h/);
        if (hMatch) mins += parseInt(hMatch[1]) * 60;
        const mMatch = tiempoStr.match(/(\d+)m/);
        if (mMatch) mins += parseInt(mMatch[1]);
        return mins;
    },

    renderizarGraficos: function (tiempos, defectos, ok, tendencia, operarios) {
        // Tiempos por Máquina (Doughnut)
        const ctxTiempos = document.getElementById('metals-chart-tiempos');
        if (ctxTiempos && window.Chart) {
            if (this.graficoTiempos) this.graficoTiempos.destroy();
            const bgColorsTpl = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#64748b'];

            const labelsTiempos = Object.keys(tiempos).length > 0 ? Object.keys(tiempos) : ['Sin datos'];
            const dataTiempos = Object.keys(tiempos).length > 0 ? Object.values(tiempos) : [1];
            const colorsT = Object.keys(tiempos).length > 0 ? bgColorsTpl : ['#e2e8f0'];

            this.graficoTiempos = new Chart(ctxTiempos, {
                type: 'doughnut',
                data: {
                    labels: labelsTiempos.map(l => l.length > 15 ? l.substring(0, 15) + '...' : l),
                    datasets: [{
                        data: dataTiempos,
                        backgroundColor: colorsT,
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'right', labels: { boxWidth: 12, font: { size: 10 } } },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => {
                                    if (Object.keys(tiempos).length === 0) return ' Sin registros';
                                    return ` ${ctx.label}: ${Math.floor(ctx.raw / 60)}h ${ctx.raw % 60}m`;
                                }
                            }
                        }
                    },
                    cutout: '65%'
                }
            });
        }

        // Defectos por Proceso (Bar)
        const ctxDefectos = document.getElementById('metals-chart-defectos');
        if (ctxDefectos && window.Chart) {
            if (this.graficoDefectos) this.graficoDefectos.destroy();

            const labelsDefectos = Object.keys(defectos).length > 0 ? Object.keys(defectos) : ['Sin defectos reportados'];
            const dataDefectos = Object.keys(defectos).length > 0 ? Object.values(defectos) : [0];

            this.graficoDefectos = new Chart(ctxDefectos, {
                type: 'bar',
                data: {
                    labels: labelsDefectos.map(l => l.length > 15 ? l.substring(0, 15) + '...' : l),
                    datasets: [{
                        label: 'Unidades Defectuosas (PNC)',
                        data: dataDefectos,
                        backgroundColor: '#ef4444',
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: { precision: 0 },
                            max: Object.keys(defectos).length > 0 ? undefined : 5
                        }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }

        // Producción OK por Máquina (Pie)
        const ctxOk = document.getElementById('metals-chart-ok');
        if (ctxOk && window.Chart) {
            if (this.graficoOk) this.graficoOk.destroy();
            const bgColorsTpl = ['#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899', '#64748b', '#ef4444'];

            const labelsOk = Object.keys(ok).length > 0 ? Object.keys(ok) : ['Sin datos'];
            const dataOk = Object.keys(ok).length > 0 ? Object.values(ok) : [1];
            const colorsO = Object.keys(ok).length > 0 ? bgColorsTpl : ['#e2e8f0'];

            this.graficoOk = new Chart(ctxOk, {
                type: 'pie',
                data: {
                    labels: labelsOk.map(l => l.length > 15 ? l.substring(0, 15) + '...' : l),
                    datasets: [{
                        data: dataOk,
                        backgroundColor: colorsO,
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'right', labels: { boxWidth: 12, font: { size: 10 } } },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => {
                                    if (Object.keys(ok).length === 0) return ' Sin registros';
                                    return ` ${ctx.label}: ${ctx.raw} unidades OK`;
                                }
                            }
                        }
                    }
                }
            });
        }

        // Rendimiento Diario (Line)
        const ctxTendencia = document.getElementById('metals-chart-tendencia');
        if (ctxTendencia && window.Chart) {
            if (this.graficoTendencia) this.graficoTendencia.destroy();

            // Ordenar fechas cronológicamente
            const fechas = Object.keys(tendencia).sort((a, b) => {
                const partsA = a.split('/');
                const partsB = b.split('/');
                return new Date(partsA[2], partsA[1] - 1, partsA[0]) - new Date(partsB[2], partsB[1] - 1, partsB[0]);
            });

            // Tomar los últimos 7 días como máximo
            const fechasRecientes = fechas.slice(-7);
            const dataTendenciaStr = fechasRecientes.map(f => tendencia[f]);

            const labelsTendencia = fechasRecientes.length > 0 ? fechasRecientes : ['Sin datos'];
            const dataTendencia = fechasRecientes.length > 0 ? dataTendenciaStr : [0];

            this.graficoTendencia = new Chart(ctxTendencia, {
                type: 'line',
                data: {
                    labels: labelsTendencia,
                    datasets: [{
                        label: 'Unidades OK Producidas',
                        data: dataTendencia,
                        borderColor: '#0dcaf0',
                        backgroundColor: 'rgba(13, 202, 240, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.3,
                        pointBackgroundColor: '#0dcaf0',
                        pointRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: { precision: 0 },
                            max: fechasRecientes.length > 0 ? undefined : 10
                        }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }

        // Trabajos por Operario (Horizontal Bar)
        const ctxOperarios = document.getElementById('metals-chart-operarios');
        if (ctxOperarios && window.Chart) {
            if (this.graficoOperarios) this.graficoOperarios.destroy();

            // Ordenar operarios de mayor a menor cantidad de trabajos
            const operariosOrdenados = Object.keys(operarios).sort((a, b) => operarios[b] - operarios[a]);

            const labelsOperarios = operariosOrdenados.length > 0 ? operariosOrdenados : ['Sin datos'];
            const dataOperarios = operariosOrdenados.length > 0 ? operariosOrdenados.map(o => operarios[o]) : [0];

            this.graficoOperarios = new Chart(ctxOperarios, {
                type: 'bar',
                data: {
                    labels: labelsOperarios.map(l => l.length > 20 ? l.substring(0, 20) + '...' : l),
                    datasets: [{
                        label: 'Número de Trabajos Realizados',
                        data: dataOperarios,
                        backgroundColor: '#f59e0b',
                        borderRadius: 4
                    }]
                },
                options: {
                    indexAxis: 'y', // Hace que las barras sean horizontales
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            beginAtZero: true,
                            ticks: { precision: 0 },
                            max: operariosOrdenados.length > 0 ? undefined : 5
                        }
                    },
                    plugins: { legend: { display: false } }
                }
            });
        }
    }
};

window.ModuloMetals = ModuloMetals;
