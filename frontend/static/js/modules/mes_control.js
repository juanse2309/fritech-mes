// ============================================
// mes_control.js - Módulo de Control de Producción (MES)
// ============================================

window.ModuloMes = {
    maquinas: [],
    productos: [],
    programacionesActivas: [],
    maquinaSeleccionada: localStorage.getItem('mes_maquina_ref') || '',
    trabajoActivo: null,
    tempProductList: [], // Lista para multi-producto (un montaje, varios códigos)

    canOperarMaquina: function () {
        if (typeof AuthModule === 'undefined' || !AuthModule.currentUser) return false;
        const userRole = AuthModule.normalizeRole(AuthModule.currentUser.rol || AuthModule.currentUser.role || '');
        return ['INYECCION', 'ENSAMBLE', 'ADMIN'].some(rolePart => userRole.includes(rolePart));
    },

    // Botón "editar OP" del modal de iniciar trabajo (reunión 2026-08-25):
    // la OP ya viene automática desde la programación, pero un admin debe
    // poder corregirla (programación vieja sin OP, OP creada a mano en WO).
    esAdmin: function () {
        if (typeof AuthModule === 'undefined' || !AuthModule.currentUser) return false;
        const userRole = (AuthModule.currentUser.rol || AuthModule.currentUser.role || '').toUpperCase();
        return userRole.includes('ADMIN') || userRole.includes('GERENCIA');
    },

    /**
     * Cajita visible con la OP asignada (pedido del usuario 2026-08-28: la OP
     * ya se generaba sola pero no se veía en ningún lado). Se muestra igual en
     * el montaje en cola y en el lote activo.
     *
     * 'SIN_OP' es el placeholder que devuelve el backend para programaciones
     * viejas anteriores al numerador automático -- se trata como "sin OP" y no
     * se pinta la cajita, para no mostrar una etiqueta que no significa nada.
     */
    badgeOP: function (op) {
        const valor = String(op || '').trim();
        if (!valor || valor === 'SIN_OP' || valor === 'SIN OP') return '';
        return `<div class="mb-2">
            <span class="badge d-inline-flex align-items-center gap-1"
                  style="background:#eef2ff;color:#4338ca;border:1px solid #c7d2fe;font-size:.7rem;font-weight:700;">
                <i class="fas fa-file-invoice"></i> OP ${valor}
            </span>
        </div>`;
    },

    // Catálogo real de códigos de molde para el datalist del campo "Molde"
    // (pedido del usuario 2026-08-28, ver GET /api/programacion/moldes).
    // Se carga una sola vez al iniciar el módulo -- 314 códigos, no cambia
    // mientras la página está abierta.
    cargarMoldesDisponibles: async function () {
        const datalist = document.getElementById('mes-prog-moldes-datalist');
        if (!datalist) return;
        try {
            const res = await fetchData('/api/programacion/moldes');
            const moldes = res?.moldes || [];
            datalist.innerHTML = moldes.map(m => `<option value="${m}">`).join('');
        } catch (e) {
            console.warn('[MES] No se pudo cargar el catálogo de moldes:', e);
        }
    },

    init: async function () {
        console.log('🚀 [MES] Inicializando Módulo de Control de Producción...');

        // Limpiar cola local/caché antes de cargar
        this.programacionesActivas = [];

        // Registrar persistencia Juan Sebastian Request
        if (window.FormHelpers) {
            window.FormHelpers.registrarPersistencia('form-mes-programar');
        }

        this.configurarEventos();
        await this.cargarDatos();
        this.initAutocomplete();
        this.cargarMoldesDisponibles();

        // Inicializar fecha de programación (visual)
        const fechaProg = document.getElementById('mes-prog-fecha');
        if (fechaProg) {
            fechaProg.value = new Date().toISOString().split('T')[0];
        }


        // Cargar máquina desde localStorage si existe
        if (this.maquinaSeleccionada) {
            const select = document.getElementById('mes-op-maquina-sel');
            if (select) {
                select.value = this.maquinaSeleccionada;
                this.cambiarMaquina(this.maquinaSeleccionada);
            }
        }

        // Aplicar Reglas Granulares de RBAC (Bloqueo de Pestañas)
        if (typeof window.applyRBACRules === 'function') {
            window.applyRBACRules();
        }
    },

    inicializar: async function () {
        return await this.init();
    },

    cargarDatos: async function () {
        try {
            // 1. Cargar Máquinas
            const maqData = await fetchData('/api/obtener_maquinas');
            this.maquinas = maqData || [];
            this.actualizarSelect('mes-prog-maquina', this.maquinas);

            // 2. Cargar Productos (desde cache o API)
            if (window.AppState && window.AppState.sharedData && window.AppState.sharedData.productos) {
                this.productos = window.AppState.sharedData.productos;
            } else {
                const res = await fetchData('/api/productos/listar');
                this.productos = res || [];
            }

            // 3. Cargar Responsables para autocompletado
            try {
                const respData = await fetchData('/api/obtener_responsables');
                this.responsables = Array.isArray(respData) ? respData : (respData?.responsables || []);
            } catch (e) {
                console.warn('[MES] No se pudieron cargar responsables:', e);
                this.responsables = ['Richard Lobo', 'Oscar Prieto'];
            }

            // 4. Cargar estado del dashboard de máquinas
            await this.cargarDashboard();

            // 5. Cargar la cola de programación (Vista 1)
            await this.actualizarColaProgramacion();

        } catch (error) {
            console.error('[MES] Error cargando datos:', error);
        }
    },

    /** Fecha de trabajo compartida (barra sobre las pestañas) -- gobierna tanto
     * la Cola de Trabajo como el Reporte de Máquina. Vacía = hoy, tanto aquí
     * como en el backend (ProgramacionService._parse_fecha_o_hoy). */
    obtenerFechaVista: function () {
        return document.getElementById('mes-prog-fecha')?.value || '';
    },

    /**
     * Carga el dashboard de 4 máquinas desde /api/mes/dashboard
     * y renderiza las tarjetas en la Vista 2.
     */
    cargarDashboard: async function () {
        try {
            const fecha = this.obtenerFechaVista();
            const data = await fetchData(`/api/mes/dashboard${fecha ? `?fecha=${fecha}` : ''}`);
            if (data && data.maquinas) {
                this.dashboardData = data.maquinas;
                this.renderDashboardMaquinas(data.maquinas);
            }
        } catch (error) {
            console.error('[MES] Error cargando dashboard:', error);
        }
    },

    getColorEstadoMaquina: function (estado) {
        if (estado === 'EN_PROCESO') return { header: 'bg-primary text-white', badge: 'bg-white text-primary' };
        if (estado === 'PROGRAMADO') return { header: 'bg-warning text-dark', badge: 'bg-dark text-white' };
        return { header: 'bg-light text-muted', badge: 'bg-secondary text-white' };
    },

    renderDashboardMaquinas: function (maquinas) {
        const grid = document.getElementById('mes-dashboard-grid');
        if (!grid) return;

        if (!maquinas || maquinas.length === 0) {
            grid.innerHTML = `<div class="col-12 text-center py-5 opacity-50">
                <i class="fas fa-industry fa-3x mb-3"></i>
                <p>No hay m\u00e1quinas configuradas.</p>
            </div>`;
            return;
        }

        // ── Ordenar siempre por n\u00famero de m\u00e1quina ──────────────────
        const sorted = [...maquinas].sort((a, b) => {
            const num = s => parseInt((s.nombre || '').replace(/\D/g, '')) || 0;
            return num(a) - num(b);
        });

        // Paleta por estado
        const palette = {
            EN_PROCESO: { border: '#2563eb', bg: '#eff6ff', badge: '#2563eb', label: '\u25B6 EN PROCESO', btnCls: 'btn-warning' },
            PROGRAMADO: { border: '#16a34a', bg: '#f0fdf4', badge: '#16a34a', label: '\u23F3 PROGRAMADO', btnCls: 'btn-success' },
            LIBRE: { border: '#cbd5e1', bg: '#f8fafc', badge: '#94a3b8', label: '\u2713 LIBRE', btnCls: '' },
        };

        grid.innerHTML = sorted.map(m => {
            try {
            const pal = palette[m.estado] || palette.LIBRE;
            const activo = m.trabajo_activo;
            const cola = m.cola || [];

            // ── Card LIBRE ─────────────────────────────────────────────
            if (m.estado === 'LIBRE') {
                return `
                <div class="col-md-6 col-xl-3">
                    <div class="card border-0 h-100" style="border-radius:16px;border-left:4px solid ${pal.border} !important;
                        box-shadow:0 2px 10px rgba(0,0,0,.07);background:${pal.bg}">
                        <div class="card-body d-flex flex-column align-items-center justify-content-center text-center p-4">
                            <div class="fw-bold text-muted" style="font-size:.7rem;letter-spacing:.1em;text-transform:uppercase">${m.nombre}</div>
                            <div class="mt-2" style="color:${pal.badge};font-size:.75rem;font-weight:600">${pal.label}</div>
                            <div class="text-muted mt-1" style="font-size:.75rem">Sin trabajos en cola</div>
                        </div>
                    </div>
                </div>`;
            }

            // ── Datos clave del trabajo (SQL Native) ────────────────────
            const item = activo || (cola[0]) || {};
            const capacidadMolde = item.molde || 'N/A';
            const horaInicio = (m.estado === 'EN_PROCESO' && activo?.hora_inicio)
                ? `<small class="text-muted"><i class="fas fa-clock me-1"></i>Inicio: ${activo.hora_inicio}</small>` : '';

            // Productos activos HTML (Agrupados por id_inyeccion de lote)
            let productosActivosHTML = '';
            if (activo && activo.productos_activos && activo.productos_activos.length > 0) {
                const skuList = activo.productos_activos.map(p => `
                    <div class="d-flex justify-content-between align-items-center py-1" style="font-size:.75rem">
                        <span><i class="fas fa-cog fa-spin me-1" style="color:#2563eb"></i> ${p.codigo_sistema || '-'}</span>
                        <span class="badge" style="background:#eff6ff;color:#2563eb;font-size:.65rem">${p.cavidades} cav.</span>
                    </div>
                `).join('');

                const canOperate = this.canOperarMaquina();
                productosActivosHTML = `
                    <div class="mb-3 p-2" style="border: 1px solid #93c5fd; border-radius: 8px; background: #eff6ff;">
                        <div class="fw-bold mb-2 text-primary" style="font-size:.8rem">Lote Activo (Molde: ${activo.molde || capacidadMolde})</div>
                        ${this.badgeOP(activo.orden_produccion)}
                        ${skuList}
                        ${this.avanceParcialHTML(activo.lecturas_parciales_hoy || [])}
                        <button class="btn btn-outline-primary btn-sm fw-bold w-100 mt-2"
                            ${canOperate ? '' : 'disabled title="Sin permisos para operar"'}
                            onclick="ModuloMes.clickReportarParcialDesdeCard('${activo.id_inyeccion}', '${activo.molde || capacidadMolde}', '${m.nombre}')">
                            <i class="fas fa-clipboard-check me-1"></i> Reportar Avance (11am/3pm)
                        </button>
                        <button class="btn btn-warning btn-sm fw-bold w-100 mt-2"
                            ${canOperate ? '' : 'disabled title="Sin permisos para operar"'}
                            onclick="ModuloMes.clickFinalizarDesdeCard('${activo.id_inyeccion}', ${activo.cavidades}, '${activo.molde || capacidadMolde}', '${activo.producto || 'LOTE MÚLTIPLE'}', '${activo.hora_inicio || '06:00'}', '${m.nombre}')">
                            <i class="fas fa-stop-circle me-1"></i> Pausar/Finalizar Montaje
                        </button>
                    </div>
                `;
            }

            // Productos en cola HTML (Agrupados por Molde/Montaje)
            let productosColaHTML = '';
            if (cola && cola.length > 0) {
                // Agrupamos por Molde + OP: dos montajes distintos pueden
                // compartir letra de molde en la misma máquina/fecha (ej. uno
                // al inicio de jornada y otro a las 12) y no deben fusionarse
                // en una sola tarjeta con un solo botón "Iniciar".
                const colaAgrupada = {};
                cola.forEach(c => {
                    const groupKey = `${c.molde || 'N/A'}|${c.orden_produccion || 'SIN_OP'}`;
                    if (!colaAgrupada[groupKey]) colaAgrupada[groupKey] = [];
                    colaAgrupada[groupKey].push(c);
                });

                const canOperate = this.canOperarMaquina();
                for (const itemsMolde of Object.values(colaAgrupada)) {
                    // Tomamos el id de la primera programación para iniciar todo el bloque
                    const primerId = itemsMolde[0].id_programacion;
                    const moldeLabel = itemsMolde[0].molde || 'N/A';

                    const skuList = itemsMolde.map(c => `
                        <div class="d-flex justify-content-between align-items-center py-1" style="font-size:.75rem">
                            <span><i class="fas fa-caret-right me-1 text-muted"></i> ${c.codigo_sistema || '-'}</span>
                            <span class="badge" style="background:#f0fdf4;color:#16a34a;font-size:.65rem">${c.cavidades} cav.</span>
                        </div>
                    `).join('');

                    const opMontaje = itemsMolde[0].orden_produccion || '';
                    productosColaHTML += `
                        <div class="mb-3 p-2" style="border: 1px dashed #cbd5e1; border-radius: 8px; background: #f8fafc;">
                            <div class="fw-bold mb-2 text-dark" style="font-size:.8rem">Montaje (Molde ${moldeLabel})</div>
                            ${this.badgeOP(opMontaje)}
                            ${skuList}
                            <button class="btn btn-success btn-sm fw-bold w-100 mt-2"
                                ${canOperate ? '' : 'disabled title="Sin permisos para operar"'}
                                onclick="ModuloMes.clickIniciarDesdeCard('${primerId}', '${m.nombre}', '${opMontaje}')">
                                <i class="fas fa-play me-1"></i> Iniciar Montaje
                            </button>
                        </div>
                    `;
                }
            }

            const skuList = (productosActivosHTML + productosColaHTML) || `<div class="text-muted" style="font-size:.75rem">Sin productos.</div>`;
            const btn = '';

            // Botón liberar (solo PROGRAMADO)
            const canOperateLiberar = this.canOperarMaquina();
            const btnLiberar = (m.estado === 'PROGRAMADO' && cola && cola.length > 0)
                ? `<button class="btn btn-outline-danger btn-sm w-100 mt-2"
                       ${canOperateLiberar ? '' : 'disabled title="Sin permisos para operar"'}
                       onclick="ModuloMes.cancelarBatch('${m.nombre}')">
                       <i class="fas fa-ban me-1"></i> Liberar M\u00e1quina
                   </button>` : '';

            return `
            <div class="col-md-6 col-xl-3">
                <div class="card border-0 h-100" style="border-radius:16px;overflow:hidden;
                    border-left:4px solid ${pal.border} !important;
                    box-shadow:0 4px 16px rgba(0,0,0,.09);">

                    <!-- Cabecera: Máquina + Estado -->
                    <div style="background:${pal.bg};padding:12px 16px 8px;border-bottom:1px solid #f1f5f9">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span style="font-size:.62rem;font-weight:700;letter-spacing:.1em;
                                text-transform:uppercase;color:#64748b">${m.nombre}</span>
                            <span style="font-size:.62rem;font-weight:700;color:${pal.badge};
                                background:${pal.badge}18;padding:2px 8px;border-radius:20px">${pal.label}</span>
                        </div>
                        <!-- MOLDE como Héroe -->
                        <div style="font-size:1.35rem;font-weight:900;color:#0f172a;line-height:1.1">
                             Molde ${capacidadMolde}
                        </div>
                        ${horaInicio}
                    </div>

                    <!-- Lista de SKUs del molde -->
                    <div style="padding:10px 14px;flex-grow:1">
                        <div style="font-size:.6rem;font-weight:700;text-transform:uppercase;color:#94a3b8;margin-bottom:6px">
                            <i class="fas fa-boxes me-1"></i> Productos del Montaje
                        </div>
                        ${skuList}
                    </div>

                    <!-- Acciones -->
                    <div style="padding:10px 14px 14px">
                        ${btn}
                        ${btnLiberar}
                    </div>
                </div>
            </div>`;
            } catch (cardErr) {
                console.error(`[MES] Error renderizando tarjeta máquina ${m?.nombre}:`, cardErr);
                return `<div class="col-md-6 col-xl-3"><div class="card border-danger h-100 p-3 text-center text-danger small"><i class="fas fa-exclamation-triangle"></i> Error cargando ${m?.nombre || 'máquina'}</div></div>`;
            }
        }).join('');
    },


    /**
     * Iniciar trabajo desde el botón de la tarjeta de máquina.
     */
    clickIniciarDesdeCard: async function (idProg, maquinaNombre, opHeredada) {
        if (!this.canOperarMaquina()) {
            Swal.fire('Acceso Denegado', 'No tienes permisos para iniciar producción.', 'error');
            return;
        }
        if (!idProg) return;

        // OP automática (reunión 2026-08-25): llega ya asignada desde la
        // programación de la tarde anterior. Si es una programación vieja
        // (creada antes de este cambio) opHeredada viene vacía y se sigue
        // pidiendo a mano, igual que antes.
        const opAutomatica = (opHeredada || '').trim();
        const puedeEditarOP = this.esAdmin();

        // Lógica de operario por máquina
        const _getDefaultResp = (maq) => {
            if (!maq) return document.getElementById('current_user_fullname')?.value || '';
            const n = maq.replace(/\D/g, ''); // extrae dígitos
            if (n === '1' || n === '2') return 'Richard Lobo';
            if (n === '3' || n === '4') return 'Oscar Prieto';
            return document.getElementById('current_user_fullname')?.value || '';
        };
        const defaultResp = _getDefaultResp(maquinaNombre);

        // Construir datalist — la API devuelve [{nombre, departamento, username}] o strings
        const responsables = this.responsables || [];
        const datalistOpts = responsables
            .map(r => typeof r === 'object' ? (r.nombre || r.username || '') : String(r))
            .filter(Boolean)
            .map(n => `<option value="${n}">`)
            .join('');
        // Fallback si no hay datos cargados
        const datalistOptsDefault = datalistOpts || '<option value="Richard Lobo"><option value="Oscar Prieto">';

        const { value: formValues } = await Swal.fire({
            title: 'Iniciar Trabajo (Turno Mañana)',
            html: `
                <datalist id="swal-resp-list-iniciar">${datalistOptsDefault}</datalist>
                <div id="swal-prueba-alert" class="badge bg-warning text-dark w-100 mb-3 py-2 fs-6 border border-warning" style="display: none; background-color: #ffc107!important;"><i class="fas fa-vial me-1"></i> MODO DE PRUEBA - NO AFECTA INVENTARIO</div>
                <div class="mb-3 text-start">
                    <label for="swal-op-wo" class="form-label fw-bold small text-uppercase text-muted">Orden de Producción (OP) de World Office</label>
                    <input type="text" id="swal-op-wo" class="form-control ${opAutomatica ? 'bg-light' : ''}"
                           value="${opAutomatica}"
                           ${opAutomatica && !puedeEditarOP ? 'readonly' : ''}
                           placeholder="${opAutomatica ? '' : 'Ej: OP-1025'}">
                    ${opAutomatica
                        ? `<small class="text-muted"><i class="fas fa-check-circle text-success me-1"></i>Asignada automáticamente al programar.${puedeEditarOP ? ' Como administrador puedes corregirla si hace falta.' : ''}</small>`
                        : `<small class="text-muted">Esta programación no tiene OP automática (creada antes del cambio) -- indícala manualmente.</small>`
                    }
                </div>
                <div class="mb-3 text-start">
                    <label for="swal-responsable-iniciar" class="form-label fw-bold small text-uppercase text-muted">Operario / Responsable</label>
                    <input type="text" id="swal-responsable-iniciar" class="form-control"
                           list="swal-resp-list-iniciar"
                           value="${defaultResp}" autocomplete="off"
                           placeholder="Escribe para buscar...">
                </div>
            `,
            icon: 'info',
            showCancelButton: true,
            confirmButtonText: '<i class="fas fa-play me-1"></i> Iniciar Lote',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#0284c7',
            focusConfirm: false,
            didOpen: () => {
                const opInput = document.getElementById('swal-op-wo');
                const alertDiv = document.getElementById('swal-prueba-alert');
                const checkPrueba = (val) => {
                    alertDiv.style.display = val.toUpperCase().includes('9999') || val.toUpperCase().includes('PRUEBA')
                        ? 'block' : 'none';
                };
                opInput.addEventListener('input', (e) => checkPrueba(e.target.value));
                // Chequeo inicial: el valor puede llegar pre-cargado (OP
                // automática) sin disparar el evento 'input'.
                checkPrueba(opInput.value);
            },
            preConfirm: () => {
                const op = document.getElementById('swal-op-wo').value;
                const resp = document.getElementById('swal-responsable-iniciar').value;
                if (!op || !op.trim()) {
                    Swal.showValidationMessage('El número de OP es estrictamente obligatorio');
                    return false;
                }
                return { op_world_office: op.trim(), responsable: resp };
            }
        });

        if (formValues) {
            try {
                mostrarLoading(true);
                const res = await fetchData('/api/mes/iniciar_trabajo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        id_programacion: idProg, 
                        op_world_office: formValues.op_world_office,
                        responsable: formValues.responsable
                    })
                });
                mostrarLoading(false);
                if (res?.success) {
                    Swal.fire({
                        icon: 'success',
                        title: '¡Trabajo Iniciado!',
                        text: res.data?.message || 'El lote está activo y las cubetas de pedidos han sido despertadas.',
                        timer: 2500,
                        showConfirmButton: false
                    });
                    await this.cargarDashboard(); // Refrescar tarjetas
                    await this.actualizarColaProgramacion(); // Refrescar cola
                } else {
                    Swal.fire('Error', res?.error || 'No se pudo iniciar el trabajo', 'error');
                }
            } catch (e) {
                mostrarLoading(false);
                console.error('[MES] Error iniciando trabajo:', e);
                Swal.fire('Error', 'Error de red al intentar iniciar el trabajo', 'error');
            }
        }
    },

    /**
     * Finalizar turno desde el botón de la tarjeta de máquina.
     * Finalizar turno desde el botón de la tarjeta de máquina.
     */
    clickFinalizarDesdeCard: async function (idInyeccion, cavidades, molde, codigo, horaInicio, maquinaNombre) {
        if (!this.canOperarMaquina()) {
            Swal.fire('Acceso Denegado', 'No tienes permisos para pausar o finalizar producción.', 'error');
            return;
        }
        if (!idInyeccion) return;

        // Obtener la hora actual en formato HH:MM para sugerir como Hora Fin
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, '0');
        const mm = String(now.getMinutes()).padStart(2, '0');
        const horaSugerida = `${hh}:${mm}`;

        // Extraer los códigos de producto individuales
        const productos = (codigo || '').split(',').map(p => p.trim()).filter(Boolean);
        let pncHtml = '';
        if (productos.length > 0) {
            pncHtml = `
                <div class="mt-4 border-top pt-3 text-start">
                    <h6 class="fw-bold text-danger mb-3"><i class="fas fa-exclamation-triangle me-1"></i> Reportar Defectos (PNC) Obligatorio</h6>
            `;
            productos.forEach((prod, index) => {
                pncHtml += `
                    <div class="card p-3 mb-3 border-0 shadow-sm" style="border-radius: 12px; background: #fffafb; border: 1px solid #fee2e2 !important;">
                        <div class="fw-bold text-danger mb-2" style="font-size: 0.9rem;"><i class="fas fa-cog me-1"></i> Producto: ${prod}</div>
                        <div class="row g-2">
                            <div class="col-4">
                                <label class="form-label small fw-bold text-muted mb-0">Quemado</label>
                                <input type="number" id="swal-pnc-quemado-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-4">
                                <label class="form-label small fw-bold text-muted mb-0">Falta Llenado</label>
                                <input type="number" id="swal-pnc-incompleto-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-4">
                                <label class="form-label small fw-bold text-muted mb-0">Rebaba</label>
                                <input type="number" id="swal-pnc-rebaba-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-6 mt-2">
                                <label class="form-label small fw-bold text-muted mb-0">Burbujas/Porosidad</label>
                                <input type="number" id="swal-pnc-burbuja-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-6 mt-2">
                                <label class="form-label small fw-bold text-muted mb-0">Deformación</label>
                                <input type="number" id="swal-pnc-deformacion-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                        </div>
                    </div>
                `;
            });
            pncHtml += `</div>`;
        }

        const maquinaData = (this.dashboardData || []).find(m => (m.nombre || '').toUpperCase() === (maquinaNombre || '').toUpperCase());
        const activeResp = maquinaData?.trabajo_activo?.responsable;

        // Lógica de operario por máquina (Máq 1-2 → Richard, Máq 3-4 → Oscar)
        const _getRespFin = (maq) => {
            const n = (maq || '').replace(/\D/g, '');
            if (n === '1' || n === '2') return 'Richard Lobo';
            if (n === '3' || n === '4') return 'Oscar Prieto';
            return document.getElementById('current_user_fullname')?.value || '';
        };
        const defaultResp = activeResp || _getRespFin(maquinaNombre);

        // Datalist de responsables — extraer .nombre del objeto si la API devuelve objetos
        const responsables = this.responsables || [];
        const datalistOptsFin = responsables
            .map(r => typeof r === 'object' ? (r.nombre || r.username || '') : String(r))
            .filter(Boolean)
            .map(n => `<option value="${n}">`)
            .join('') || '<option value="Richard Lobo"><option value="Oscar Prieto">';

        const opActiva = maquinaData?.trabajo_activo?.orden_produccion || '';
        const idInyActivo = maquinaData?.trabajo_activo?.id_inyeccion || '';
        const esPrueba = opActiva.toUpperCase().includes('9999') || opActiva.toUpperCase().includes('PRUEBA') || idInyActivo.toUpperCase().includes('9999') || idInyActivo.toUpperCase().includes('PRUEBA');
        const badgePrueba = esPrueba ? `<div class="badge bg-warning text-dark w-100 mb-3 py-2 fs-6 border border-warning" style="background-color: #ffc107!important;"><i class="fas fa-vial me-1"></i> MODO DE PRUEBA - NO AFECTA INVENTARIO</div>` : '';

        const { value: formValues } = await Swal.fire({
            title: '\u00bfFinalizar Turno?',
            html: `
                ${badgePrueba}
                <div class="alert alert-info py-2 px-3 mb-3 border-0 text-start" style="background:#e0f2fe;color:#0369a1;border-radius:12px">
                    <div class="row g-2">
                        <div class="col-6"><small class="d-block fw-bold opacity-75">MOLDE</small> <strong>${molde}</strong></div>
                        <div class="col-6"><small class="d-block fw-bold opacity-75">CAVIDADES</small> <strong>${cavidades}</strong></div>
                        <div class="col-12 mt-1"><small class="d-block fw-bold opacity-75">PRODUCTO(S)</small> <strong>${codigo}</strong></div>
                        <div class="col-12 mt-1"><small class="d-block fw-bold opacity-75">OP WORLD OFFICE</small> <strong>${opActiva || '-'}</strong></div>
                    </div>
                </div>

                <datalist id="swal-resp-list-fin">${datalistOptsFin}</datalist>
                <div class="mb-3 text-start px-2">
                    <label class="form-label fw-bold small text-uppercase text-muted mb-1">Operario / Responsable</label>
                    <input type="text" id="swal-responsable-fin" class="form-control"
                           list="swal-resp-list-fin"
                           value="${defaultResp}" autocomplete="off"
                           placeholder="Escribe para buscar...">
                </div>

                <div class="mb-3 text-start px-2">
                    <label class="form-label fw-bold small text-uppercase text-muted mb-1">Cierres del Contador</label>
                    <input type="number" id="swal-cierres" class="form-control form-control-lg text-center fw-bold" placeholder="0" min="1">
                </div>
                
                <div class="row text-start px-2 g-3">
                    <div class="col-6">
                        <label class="form-label fw-bold small text-uppercase text-muted mb-1">Hora Inicio Real</label>
                        <input type="time" id="swal-hora-inicio" class="form-control" value="${horaInicio}">
                    </div>
                    <div class="col-6">
                        <label class="form-label fw-bold small text-uppercase text-muted mb-1">Hora Fin Real</label>
                        <input type="time" id="swal-hora-fin" class="form-control" value="${horaSugerida}">
                    </div>
                </div>
                ${pncHtml}
            `,
            focusConfirm: false,
            showCancelButton: true,
            confirmButtonText: '<i class="fas fa-check-circle me-1"></i> Reportar y Finalizar',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#16a34a',
            preConfirm: () => {
                const cierres = document.getElementById('swal-cierres').value;
                const hi = document.getElementById('swal-hora-inicio').value;
                const hf = document.getElementById('swal-hora-fin').value;
                const resp = document.getElementById('swal-responsable-fin').value;

                if (!cierres || parseInt(cierres) <= 0) {
                    Swal.showValidationMessage('Ingresa un n\u00famero v\u00e1lido de cierres');
                    return false;
                }
                if (!hi || !hf) {
                    Swal.showValidationMessage('Ambas horas son obligatorias');
                    return false;
                }

                // Extraer PNC para cada producto
                const pncList = [];
                productos.forEach((prod, index) => {
                    const q = parseInt(document.getElementById(`swal-pnc-quemado-${index}`).value) || 0;
                    const inc = parseInt(document.getElementById(`swal-pnc-incompleto-${index}`).value) || 0;
                    const r = parseInt(document.getElementById(`swal-pnc-rebaba-${index}`).value) || 0;
                    const b = parseInt(document.getElementById(`swal-pnc-burbuja-${index}`).value) || 0;
                    const d = parseInt(document.getElementById(`swal-pnc-deformacion-${index}`).value) || 0;

                    if (q > 0 || inc > 0 || r > 0 || b > 0 || d > 0) {
                        pncList.push({
                            codigo: prod,
                            defectos: {
                                "Quemado / Manchado": q,
                                "Falta de Llenado": inc,
                                "Rebaba": r,
                                "Burbujas": b,
                                "Deformación": d
                            }
                        });
                    }
                });

                return { cierres: parseInt(cierres), hora_inicio: hi, hora_fin: hf, responsable: resp, pncList: pncList };
            }
        });

        if (formValues) {
            try {
                mostrarLoading(true);
                const res = await fetchData('/api/mes/reportar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id_inyeccion: idInyeccion,
                        cierres: formValues.cierres,
                        hora_inicio: formValues.hora_inicio,
                        hora_fin: formValues.hora_fin,
                        responsable: formValues.responsable
                    })
                });

                if (res?.success) {
                    // Registrar o limpiar PNC para cada producto en db_pnc_inyeccion
                    for (const prod of productos) {
                        const foundPnc = formValues.pncList.find(p => p.codigo === prod);
                        const defectsPayload = foundPnc ? foundPnc.defectos : {};
                        try {
                            await fetchData('/api/pnc/registrar_inyeccion', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    id_inyeccion: idInyeccion,
                                    id_codigo: prod,
                                    defectos: defectsPayload
                                })
                            });
                        } catch (errPnc) {
                            console.error(`[MES] Error registrando PNC para ${prod}:`, errPnc);
                        }
                    }

                    mostrarLoading(false);
                    Swal.fire({
                        icon: 'success',
                        title: 'Turno Reportado',
                        text: `Producci\u00f3n te\u00f3rica: ${res.data?.teorica?.toLocaleString()} piezas. Pasa a Control de Calidad.`,
                        timer: 3500, showConfirmButton: false
                    });
                    await this.cargarDashboard();
                } else {
                    mostrarLoading(false);
                    Swal.fire('Error', res?.error || 'No se pudo reportar', 'error');
                }
            } catch (e) {
                mostrarLoading(false);
                console.error('[MES] Error finalizando:', e);
                Swal.fire('Error', 'Error de red al intentar reportar', 'error');
            }
        }
    },

    /**
     * Resumen visual del "Reporte de Avance" (pedido del usuario 2026-09-04):
     * muestra la última lectura del contador hecha hoy y avisa en ámbar si ya
     * pasó la hora de una franja (11am / 3pm) y todavía no hay ninguna
     * lectura registrada después de esa hora. Una lectura posterior a las
     * 15:00 también cubre la franja de las 11:00 -- no se pide una lectura
     * por cada franja exacta, solo que haya habido *algún* check-in después.
     */
    avanceParcialHTML: function (lecturas) {
        const ahora = new Date();
        const horaActual = ahora.getHours() * 60 + ahora.getMinutes();
        const pasoDe = (hhmm) => {
            const [h, m] = hhmm.split(':').map(Number);
            return horaActual >= (h * 60 + m);
        };
        const hayLecturaDesde = (hhmm) => lecturas.some(l => l.hora && l.hora >= hhmm);

        const faltantes = [];
        if (pasoDe('11:00') && !hayLecturaDesde('11:00')) faltantes.push('11:00 am');
        if (pasoDe('15:00') && !hayLecturaDesde('15:00')) faltantes.push('3:00 pm');

        const ultima = lecturas.length > 0 ? lecturas[lecturas.length - 1] : null;
        const ultimaHTML = ultima
            ? `<div class="d-flex justify-content-between align-items-center" style="font-size:.72rem;color:#334155">
                   <span><i class="fas fa-history me-1"></i>Última lectura: ${ultima.hora}</span>
                   <span class="fw-bold">${Number(ultima.cierres).toLocaleString()} cierres</span>
               </div>`
            : `<div style="font-size:.72rem;color:#94a3b8"><i class="fas fa-info-circle me-1"></i>Sin reportes de avance hoy</div>`;

        const avisoHTML = faltantes.length > 0
            ? `<div class="mt-1" style="font-size:.68rem;font-weight:700;color:#b45309;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:3px 6px">
                   <i class="fas fa-exclamation-triangle me-1"></i>Falta reportar avance de las ${faltantes.join(' y ')}
               </div>`
            : '';

        return `<div class="mt-2 pt-2" style="border-top:1px dashed #bfdbfe">${ultimaHTML}${avisoHTML}</div>`;
    },

    /**
     * Reporte parcial de avance (pedido del usuario 2026-09-04): a diferencia
     * de "Pausar/Finalizar Montaje", NO cierra el lote -- solo deja una
     * lectura del contador para que el supervisor vea el ritmo del día a las
     * 11am y 3pm. Modal deliberadamente mínimo (solo Cierres + Responsable):
     * ni PNC ni horas, porque el lote sigue vivo y esos datos se piden en el
     * cierre real.
     */
    clickReportarParcialDesdeCard: async function (idInyeccion, molde, maquinaNombre) {
        if (!this.canOperarMaquina()) {
            Swal.fire('Acceso Denegado', 'No tienes permisos para reportar avance de producción.', 'error');
            return;
        }
        if (!idInyeccion) return;

        const maquinaData = (this.dashboardData || []).find(m => (m.nombre || '').toUpperCase() === (maquinaNombre || '').toUpperCase());
        const activeResp = maquinaData?.trabajo_activo?.responsable;
        const _getResp = (maq) => {
            const n = (maq || '').replace(/\D/g, '');
            if (n === '1' || n === '2') return 'Richard Lobo';
            if (n === '3' || n === '4') return 'Oscar Prieto';
            return document.getElementById('current_user_fullname')?.value || '';
        };
        const defaultResp = activeResp || _getResp(maquinaNombre);

        const responsables = this.responsables || [];
        const datalistOpts = responsables
            .map(r => typeof r === 'object' ? (r.nombre || r.username || '') : String(r))
            .filter(Boolean)
            .map(n => `<option value="${n}">`)
            .join('') || '<option value="Richard Lobo"><option value="Oscar Prieto">';

        const { value: formValues } = await Swal.fire({
            title: 'Reportar Avance',
            html: `
                <div class="alert alert-info py-2 px-3 mb-3 border-0 text-start" style="background:#e0f2fe;color:#0369a1;border-radius:12px">
                    <small class="d-block fw-bold opacity-75">MOLDE</small> <strong>${molde}</strong>
                </div>
                <datalist id="swal-resp-list-parcial">${datalistOpts}</datalist>
                <div class="mb-3 text-start px-2">
                    <label class="form-label fw-bold small text-uppercase text-muted mb-1">Operario / Responsable</label>
                    <input type="text" id="swal-responsable-parcial" class="form-control"
                           list="swal-resp-list-parcial"
                           value="${defaultResp}" autocomplete="off"
                           placeholder="Escribe para buscar...">
                </div>
                <div class="mb-1 text-start px-2">
                    <label class="form-label fw-bold small text-uppercase text-muted mb-1">Cierres del Contador (ahora)</label>
                    <input type="number" id="swal-cierres-parcial" class="form-control form-control-lg text-center fw-bold" placeholder="0" min="1">
                </div>
                <small class="text-muted px-2">Esto NO finaliza el lote -- solo deja un registro de avance.</small>
            `,
            focusConfirm: false,
            showCancelButton: true,
            confirmButtonText: '<i class="fas fa-clipboard-check me-1"></i> Guardar Avance',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#0284c7',
            preConfirm: () => {
                const cierres = document.getElementById('swal-cierres-parcial').value;
                const resp = document.getElementById('swal-responsable-parcial').value;
                if (!cierres || parseInt(cierres) <= 0) {
                    Swal.showValidationMessage('Ingresa un número válido de cierres');
                    return false;
                }
                return { cierres: parseInt(cierres), responsable: resp };
            }
        });

        if (formValues) {
            try {
                mostrarLoading(true);
                const res = await fetchData('/api/mes/reportar_parcial', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id_inyeccion: idInyeccion,
                        cierres: formValues.cierres,
                        responsable: formValues.responsable
                    })
                });
                mostrarLoading(false);
                if (res?.success) {
                    Swal.fire({
                        icon: 'success',
                        title: 'Avance registrado',
                        text: `${formValues.cierres.toLocaleString()} cierres a las ${res.data?.hora || ''}.`,
                        timer: 2000,
                        showConfirmButton: false
                    });
                    await this.cargarDashboard();
                } else {
                    Swal.fire('Error', res?.error || 'No se pudo registrar el avance', 'error');
                }
            } catch (e) {
                mostrarLoading(false);
                console.error('[MES] Error registrando avance parcial:', e);
                Swal.fire('Error', 'Error de red al intentar registrar el avance', 'error');
            }
        }
    },

    configurarEventos: function () {
        // Tab Events - Refresh data on tab change
        const tabs = document.querySelectorAll('#mes-tabs button');
        tabs.forEach(tab => {
            tab.addEventListener('shown.bs.tab', (e) => {
                const targetId = e.target.getAttribute('data-bs-target');
                if (targetId === '#panel-programacion') this.actualizarColaProgramacion();
                if (targetId === '#panel-calidad') this.actualizarPendientesCalidad();
                if (targetId === '#panel-operacion') this.cargarDashboard(); // <-- Llama dashboard unificado
                if (targetId === '#panel-legacy') {
                    if (window.ModuloInyeccion) {
                        window.ModuloInyeccion.currentModule = 'validation';
                        if (typeof window.ModuloInyeccion.init === 'function') {
                            window.ModuloInyeccion.init();
                        }
                    }
                }
                if (targetId === '#panel-operacion') {
                    if (window.ModuloInyeccion) {
                        window.ModuloInyeccion.currentModule = 'operator';
                    }
                    this.cargarDashboard();
                }
            });
        });

        // Fecha de trabajo compartida: al cambiarla, refrescar Cola de Trabajo
        // y Reporte de Máquina para que ambas pestañas queden mirando el mismo día.
        const fechaVista = document.getElementById('mes-prog-fecha');
        if (fechaVista) {
            fechaVista.addEventListener('change', () => {
                this.actualizarColaProgramacion();
                this.cargarDashboard();
            });
        }

        // Form Programar
        const formProg = document.getElementById('form-mes-programar');
        if (formProg) {
            formProg.addEventListener('submit', (e) => {
                e.preventDefault();
                this.crearProgramacion();
            });
        }

        // Selección de Máquina (Operario)
        const selectMaq = document.getElementById('mes-op-maquina-sel');
        if (selectMaq) {
            selectMaq.addEventListener('change', (e) => {
                this.cambiarMaquina(e.target.value);
            });
        }

        // Botones de Acción Operario
        const btnIniciar = document.getElementById('btn-mes-iniciar-trabajo');
        if (btnIniciar) {
            btnIniciar.addEventListener('click', () => this.iniciarTrabajo());
        }

        const btnFinalizar = document.getElementById('btn-mes-finalizar-trabajo');
        if (btnFinalizar) {
            btnFinalizar.addEventListener('click', () => this.finalizarTrabajo());
        }

        // Refresh buttons
        document.getElementById('btn-refresh-prog')?.addEventListener('click', () => this.actualizarColaProgramacion());
        document.getElementById('btn-refresh-operacion')?.addEventListener('click', () => this.cargarDashboard());
        document.getElementById('btn-retomar-general')?.addEventListener('click', () => this.retomarGeneral());


        // --- MEJORA: Búsqueda automática y Autocompletado ---
        const productInput = document.getElementById('mes-prog-producto');
        const btnAddProd = document.getElementById('btn-mes-add-prod-list');

        if (productInput) {
            // Autocompletado mientras escribe
            productInput.addEventListener('input', (e) => this.filtrarProductos(e.target.value));

            productInput.addEventListener('blur', () => {
                // Pequeño delay para permitir click en sugerencias
                setTimeout(() => {
                    const suggestions = document.getElementById('mes-prog-prod-suggestions');
                    if (suggestions) suggestions.classList.remove('active');
                }, 200);
            });

            // Re-escuchar cambio/blur para detalles técnicos
            productInput.addEventListener('blur', () => this.buscarDetallesProducto(productInput.value));
            productInput.addEventListener('change', () => this.buscarDetallesProducto(productInput.value));

            // Permitir 'Enter' para añadir a la lista
            productInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.agregarProductoATemp();
                }
            });
        }

        if (btnAddProd) {
            btnAddProd.addEventListener('click', () => this.agregarProductoATemp());
        }

        // El "+" y la lista de "Productos en este Montaje" quedan ocultos
        // hasta completar Molde y Cavidades (pedido del usuario 2026-08-27):
        // no tiene sentido armar la lista antes de saber con qué molde va a
        // correr el montaje. Ver actualizarVisibilidadAgregar.
        const moldeInput = document.getElementById('mes-prog-molde');
        const cavInputListener = document.getElementById('mes-prog-cavidades');
        if (moldeInput) moldeInput.addEventListener('input', () => this.actualizarVisibilidadAgregar());
        if (cavInputListener) cavInputListener.addEventListener('input', () => this.actualizarVisibilidadAgregar());
        this.actualizarVisibilidadAgregar();
    },

    actualizarVisibilidadAgregar: function () {
        const molde = document.getElementById('mes-prog-molde');
        const cav = document.getElementById('mes-prog-cavidades');
        const btnAdd = document.getElementById('btn-mes-add-prod-list');
        const listaMontaje = document.getElementById('mes-prog-montaje-lista');

        // Molde ya no es una capacidad numérica (ver quitarProductoATemp/Fase
        // molde-texto, 2026-08-28): es un código real de catálogo, y no todos
        // empiezan con dígito (ej. "D" -- confirmado en rel_producto_molde).
        // parseFloat(molde.value) > 0 dejaba el "+" oculto para siempre en esos
        // casos porque parseFloat("D") es NaN -- solo hace falta que no esté vacío.
        const camposListos = !!(molde && molde.value.trim())
            && !!(cav && cav.value && parseFloat(cav.value) > 0);
        // La lista, una vez que ya tiene productos, no se vuelve a esconder
        // aunque el usuario borre Molde/Cavidades por accidente -- perdería
        // de vista lo que ya añadió, no solo la posibilidad de añadir más.
        const yaHayProductos = (this.tempProductList || []).length > 0;

        if (btnAdd) btnAdd.style.display = camposListos ? '' : 'none';
        if (listaMontaje) listaMontaje.style.display = (camposListos || yaHayProductos) ? '' : 'none';
    },

    filtrarProductos: function (query) {
        const suggestions = document.getElementById('mes-prog-prod-suggestions');
        if (!suggestions) return;

        if (!query || query.length < 2) {
            suggestions.classList.remove('active');
            return;
        }

        const q = query.toLowerCase();
        // Filtrar del cache de productos — buscar en TODOS los campos de código
        const filtrados = (this.productos || []).filter(p =>
            (p.codigo && p.codigo.toLowerCase().includes(q)) ||
            (p.codigo_sistema && p.codigo_sistema.toLowerCase().includes(q)) ||
            (p.descripcion && p.descripcion.toLowerCase().includes(q))
        ).slice(0, 8);

        if (filtrados.length > 0) {
            suggestions.innerHTML = filtrados.map(p => {
                const codigoDisplay = p.codigo_sistema || p.codigo;
                const badgePed = (p.pedidos_pendientes && p.pedidos_pendientes > 0) ? `<span class="badge" style="background:#fee2e2;color:#b91c1c;margin-left:8px;font-size:0.7rem;">⚠️ ${p.pedidos_pendientes} en Pedido</span>` : '';
                return `
                <div class="suggestion-item p-2 border-bottom pointer" onclick="ModuloMes.seleccionarProducto('${codigoDisplay}')">
                    <div class="fw-bold d-flex align-items-center">${codigoDisplay} ${badgePed}</div>
                    <div class="text-xs text-muted text-truncate">${p.descripcion}</div>
                </div>`;
            }).join('');
            suggestions.classList.add('active');
        } else {
            suggestions.classList.remove('active');
        }
    },

    seleccionarProducto: function (codigo) {
        const input = document.getElementById('mes-prog-producto');
        if (input) {
            input.value = codigo;
            this.filtrarProductos(''); // Cerrar
            this.buscarDetallesProducto(codigo);
        }
    },

    /**
     * Añade el producto actual del input a la lista temporal del molde
     */
    agregarProductoATemp: function () {
        const input = document.getElementById('mes-prog-producto');
        const moldeInput = document.getElementById('mes-prog-molde');
        const cavInput = document.getElementById('mes-prog-cavidades');

        const codigo = input.value.trim();
        const cavidades = parseInt(cavInput.value) || 1;

        if (!codigo) return;

        // Verificar si ya está en la lista
        if (this.tempProductList.some(p => p.codigo === codigo)) {
            Swal.fire('Atención', 'Este producto ya está en la lista', 'warning');
            return;
        }

        this.tempProductList.push({
            codigo: codigo,
            cavidades: cavidades,
            molde: moldeInput.value.trim()
        });

        // Limpiar para el siguiente
        input.value = '';
        input.focus();
        this.filtrarProductos(''); // Limpiar sugerencias

        this.renderTempList();
        this.actualizarVisibilidadAgregar();
        console.log('➕ [MES] Producto añadido a lote:', codigo);

        // Feedback visual en el input
        input.style.borderColor = '#10b981';
        setTimeout(() => input.style.borderColor = '', 500);
    },

    quitarProductoATemp: function (codigo) {
        this.tempProductList = this.tempProductList.filter(p => p.codigo !== codigo);
        this.renderTempList();
        this.actualizarVisibilidadAgregar();
    },

    renderTempList: function () {
        const container = document.getElementById('mes-prog-temp-list');
        const totalCavBadge = document.getElementById('mes-prog-total-cav');

        if (!container) return;

        if (this.tempProductList.length === 0) {
            container.innerHTML = '<tr><td class="text-center text-muted py-2 small">Añade productos para empezar</td></tr>';
            if (totalCavBadge) totalCavBadge.innerText = '0 Cav';
            return;
        }

        let totalCav = 0;
        container.innerHTML = this.tempProductList.map(p => {
            totalCav += p.cavidades;
            return `
                <tr>
                    <td class="fw-bold">${p.codigo}</td>
                    <td class="text-center">${p.cavidades}</td>
                    <td class="text-end">
                        <button type="button" class="btn btn-sm btn-link text-danger p-0" onclick="ModuloMes.quitarProductoATemp('${p.codigo}')">
                            <i class="fas fa-times"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');

        if (totalCavBadge) totalCavBadge.innerText = `${totalCav} Cav`;
    },

    /**
     * Busca los detalles técnicos de un producto (molde/cavidades) para autocompletar el form.
     */
    buscarDetallesProducto: async function (codigo) {
        if (!codigo || codigo.length < 3) {
            const preview = document.getElementById('preview-producto');
            if (preview) preview.innerHTML = '';
            return;
        }

        try {
            console.log(`🔍 [MES] Buscando detalles técnicos para: ${codigo}`);

            const preview = document.getElementById('preview-producto');
            if (preview) {
                preview.innerHTML = `<div class="text-muted small"><i class="fas fa-spinner fa-spin"></i> Buscando producto...</div>`;
            }

            const res = await fetchData(`/api/productos/detalle/${codigo}`);

            if (res && res.status === 'success' && res.producto) {
                const p = res.producto;
                console.log('✅ [MES] Detalles encontrados:', p);

                // NUEVO: Consultar pedidos pendientes para la tarde (cubetas)
                if (p.codigo_sistema) {
                    this.buscarPedidosPendientes(p.codigo_sistema);
                } else if (p.codigo) {
                    this.buscarPedidosPendientes(p.codigo);
                }

                // NUEVO: Consultar cruce de demanda B2B vs stock
                let alertDemandHTML = '';
                try {
                    const checkRes = await fetchData(`/api/produccion/verificar_demanda/${p.codigo_sistema || p.id_codigo || codigo}`);
                    if (checkRes && checkRes.success) {
                        const { unidades_pedidas_b2b, stock_terminado, stock_por_pulir, empacado_hoy, alistado_pendiente_despacho } = checkRes.data;
                        // P. Terminado, Por Pulir y Empacado Hoy por separado (pedido
                        // de la jefa 2026-08-31): antes solo se veía P. Terminado.
                        // Disponible sigue siendo SOLO sobre P. Terminado (lo que ya
                        // se puede facturar hoy) -- Por Pulir y Empacado Hoy son
                        // contexto de lo que viene en camino / ya se armó, no se
                        // suman porque Por Pulir todavía puede salir con PNC.
                        const disponible_calc = stock_terminado - unidades_pedidas_b2b;

                        const stat = (label, valor, conBorde) => `
                            <div class="col-6 col-md ${conBorde ? 'border-end' : ''}">
                                <div class="text-muted fw-bold mb-1" style="font-size: 0.65rem; text-transform: uppercase;">${label}</div>
                                <div class="fs-5 fw-bold text-dark">${valor}</div>
                            </div>`;

                        alertDemandHTML = `
                            <div class="mt-3 p-3 shadow-sm" style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;">
                                <div class="row text-center g-2" style="font-size: 0.8rem;">
                                    ${stat('P. Terminado', stock_terminado, true)}
                                    ${stat('Por Pulir', stock_por_pulir, true)}
                                    ${stat('Empacado Hoy', empacado_hoy, true)}
                                    ${stat('Alistado (s/despacho)', alistado_pendiente_despacho, true)}
                                    ${stat('Demanda Activa', unidades_pedidas_b2b, true)}
                                    ${stat('Disponible', disponible_calc, false)}
                                </div>
                            </div>
                        `;
                    }
                } catch (errCheck) {
                    console.error('[MES] Error al verificar demanda:', errCheck);
                }

                if (preview) {
                    preview.innerHTML = `
                        <div class="alert alert-success d-flex align-items-center mb-0 p-2 border-0" style="background-color: #d1fae5; color: #065f46; border-radius: 8px;">
                            <i class="fas fa-check-circle me-2 fs-5"></i>
                            <div>
                                <strong class="d-block" style="font-size: 0.85rem;">Producto Válido</strong>
                                <span style="font-size: 0.75rem;">${p.descripcion || p.codigo_sistema}</span>
                            </div>
                        </div>
                        ${alertDemandHTML}
                    `;
                }

                const moldeInput = document.getElementById('mes-prog-molde');
                const cavInput = document.getElementById('mes-prog-cavidades');

                if (moldeInput && p.moldes) {
                    moldeInput.value = p.moldes;
                    moldeInput.classList.add('is-valid');
                    setTimeout(() => moldeInput.classList.remove('is-valid'), 2000);
                }

                if (cavInput && p.cavidades) {
                    // FIX BUG CAVIDADES: Solo sobreescribir si está vacío o en el default de 1
                    if (cavInput.value === '1' || cavInput.value === '') {
                        cavInput.value = p.cavidades;
                        cavInput.classList.add('is-valid');
                        setTimeout(() => cavInput.classList.remove('is-valid'), 2000);
                    }
                }

                // Obligatorio tras autocompletar: asignar .value por código NO
                // dispara el evento 'input', así que los listeners de
                // configurarEventos no se enteran y el botón "+" se quedaría
                // oculto para siempre aunque Molde/Cavidades ya estén llenos.
                this.actualizarVisibilidadAgregar();
            } else {
                throw new Error("Producto no encontrado");
            }
        } catch (error) {
            console.warn('[MES] No se pudieron obtener detalles para auto-completar:', error);

            const preview = document.getElementById('preview-producto');
            if (preview) preview.innerHTML = '';

            const productInput = document.getElementById('mes-prog-producto');
            if (productInput) productInput.value = '';

            Swal.fire({
                icon: 'error',
                title: 'Producto no encontrado',
                text: `El código "${codigo}" no existe en el catálogo. Verifica e intenta nuevamente.`
            });
        }
    },

    initAutocomplete: function () {
        if (window.ModuloUX && window.ModuloUX.setupSmartEnter) {
            window.ModuloUX.setupSmartEnter({
                inputIds: ['mes-prog-producto', 'mes-prog-molde', 'mes-prog-cavidades'],
                actionBtnId: 'btn-mes-add-prod-list',
                autocomplete: {
                    inputId: 'mes-prog-producto',
                    suggestionsId: 'mes-prog-prod-suggestions'
                }
            });
        }
    },

    // --- LÓGICA DE PROGRAMACIÓN (Fase 1) ---

    actualizarColaProgramacion: async function () {
        try {
            console.log('🔄 [MES] Actualizando cola de programación...');
            const fecha = this.obtenerFechaVista();
            const data = await fetchData(`/api/mes/programaciones/TODAS${fecha ? `?fecha=${fecha}` : ''}`);
            this.programacionesActivas = data || [];
            this.renderCardsProgramacion();
        } catch (error) {
            console.error('[MES] Error actualizando cola:', error);
        }
    },

    renderCardsProgramacion: function () {
        const container = document.getElementById('mes-cards-container');
        if (!container) return;

        if (!this.programacionesActivas || this.programacionesActivas.length === 0) {
            container.innerHTML = `
                <div class="col-12 text-center py-5 opacity-50">
                    <i class="fas fa-calendar-check fa-3x mb-3"></i>
                    <p>No hay nada programado para esta fecha.</p>
                </div>`;
            return;
        }

        const porBloque = {};
        
        this.programacionesActivas.forEach(p => {
            const maq = (p.maquina || '').toUpperCase();
            const op = p.orden_produccion || 'SIN_OP';
            const molde = p.molde || '0';
            const fecha = p.fecha || 'N/A';
            // Clave única por máquina, OP, molde y fecha (representa un bloque)
            const bKey = `${maq}|${op}|${molde}|${fecha}`;
            if (!porBloque[bKey]) porBloque[bKey] = [];
            porBloque[bKey].push(p);
        });

        // Asegurar que las máquinas vacías se muestren como disponibles
        this.maquinas.forEach(m => {
            const mKey = (typeof m === 'string' ? m : String(m)).toUpperCase();
            const keysDeMaquina = Object.keys(porBloque).filter(k => k.startsWith(mKey + '|'));
            if (keysDeMaquina.length === 0) {
                porBloque[`${mKey}|VACIA|0|N/A`] = [];
            }
        });

        const todasClaves = Object.keys(porBloque);

        // Priorizar la fecha seleccionada (no necesariamente "hoy" real) y
        // ordenar máquinas vacías al final. El servidor ya filtra la cola a
        // esta misma fecha exacta (ver obtenerFechaVista/
        // obtener_programaciones_activas), así que en la práctica todo
        // termina con la misma fecha -- esto solo ordena qué máquina va
        // primero.
        const tzOffset = new Date().getTimezoneOffset() * 60000;
        const todayStr = this.obtenerFechaVista() || new Date(Date.now() - tzOffset).toISOString().split('T')[0];

        todasClaves.sort((a, b) => {
            const aParts = a.split('|');
            const bParts = b.split('|');
            const aOp = aParts[1];
            const bOp = bParts[1];
            const aDate = aParts[3] || '';
            const bDate = bParts[3] || '';
            
            const aIsToday = (aDate === todayStr);
            const bIsToday = (bDate === todayStr);
            
            const aIsEmpty = (aOp === 'VACIA');
            const bIsEmpty = (bOp === 'VACIA');
            
            if (aIsEmpty && !bIsEmpty) return 1;
            if (!aIsEmpty && bIsEmpty) return -1;
            
            if (aIsToday && !bIsToday) return -1;
            if (!aIsToday && bIsToday) return 1;
            
            if (aDate < bDate) return 1;
            if (aDate > bDate) return -1;
            return 0;
        });

        // Paleta de colores...
        const paletas = [
            { grad: 'linear-gradient(135deg,#1d4ed8,#3b82f6)', light: '#eff6ff', accent: '#1d4ed8' },
            { grad: 'linear-gradient(135deg,#6d28d9,#8b5cf6)', light: '#f5f3ff', accent: '#6d28d9' },
            { grad: 'linear-gradient(135deg,#0f766e,#14b8a6)', light: '#f0fdfa', accent: '#0f766e' },
            { grad: 'linear-gradient(135deg,#c2410c,#f97316)', light: '#fff7ed', accent: '#c2410c' },
        ];

        container.innerHTML = todasClaves.map((bKey, idx) => {
            const items = porBloque[bKey] || [];
            const m = bKey.split('|')[0]; // Extraer nombre real de máquina
            const op = bKey.split('|')[1];
            const tieneTrabajo = items.length > 0;
            const pal = paletas[idx % paletas.length];

            // Determinar si hay algo en proceso
            const esEnProceso = items.some(i => i.estado === 'EN_PROCESO');
            let statusLabel = 'PROGRAMADA';
            if (esEnProceso) {
                statusLabel = `EN USO - Molde ${items[0].molde}`;
            } else if (op !== 'SIN_OP' && op !== 'VACIA') {
                statusLabel = `OP: ${op}`;
            }

            if (!tieneTrabajo) {
                return `
                <div class="col-md-6 col-xl-3 mb-3">
                    <div class="card border-0 h-100" style="border-radius:18px;background:#f8fafc;box-shadow:0 2px 8px rgba(0,0,0,.06)">
                        <div class="card-body d-flex flex-column align-items-center justify-content-center text-center p-4" style="min-height:170px">
                            <div class="rounded-circle d-flex align-items-center justify-content-center mb-3"
                                style="width:52px;height:52px;background:#e2e8f0">
                                <i class="fas fa-microchip text-muted" style="font-size:1.4rem"></i>
                            </div>
                            <div class="fw-bold text-muted" style="font-size:.7rem;letter-spacing:.1em;text-transform:uppercase">${m}</div>
                            <div class="text-muted mt-1 mb-2" style="font-size:.78rem">Disponible</div>
                            <button onclick="ModuloMes.retomarMaquina('${m}')"
                                style="padding:5px 12px;border:1px solid #cbd5e1;border-radius:20px;
                                background:#fff;color:#334155;font-size:.7rem;font-weight:600;cursor:pointer"
                                title="Repite en esta máquina la última programación conocida (mismo molde/referencia)">
                                <i class="fas fa-history me-1"></i> Retomar
                            </button>
                        </div>
                    </div>
                </div>`;
            }

            // El molde principal (capacidad)
            const moldeCapacidad = items[0].molde || 'N/A';
            const totalCav = items.reduce((sum, p) => sum + (parseInt(p.cavidades) || 0), 0);

            return `
            <div class="col-md-6 col-xl-3 mb-3">
                <div class="card border-0 h-100" style="border-radius:18px;overflow:hidden;box-shadow:0 6px 24px rgba(0,0,0,.13)">
                    <div style="background:${pal.grad};padding:18px 20px 16px">
                        <div class="d-flex justify-content-between align-items-start">
                            <div style="max-width:65%">
                                <div style="color:rgba(255,255,255,.6);font-size:.6rem;letter-spacing:.12em;text-transform:uppercase;font-weight:700">${m}</div>
                                <div style="color:#fff;font-size:1.1rem;font-weight:800;line-height:1.25;margin-top:4px;word-break:break-word">Molde ${moldeCapacidad}</div>
                            </div>
                            <div class="text-center" style="min-width:52px">
                                <div style="color:#fff;font-size:2.4rem;font-weight:900;line-height:1">${totalCav}</div>
                                <div style="color:rgba(255,255,255,.55);font-size:.6rem;letter-spacing:.06em">CAV.</div>
                            </div>
                        </div>
                        <div class="d-flex align-items-center gap-2 mt-3">
                            <span style="width:8px;height:8px;border-radius:50%;background:#bef264;display:inline-block;
                                animation:mes-pulse 1.8s ease-in-out infinite"></span>
                            <span style="color:#fff;font-size:.68rem;font-weight:700">
                                ${statusLabel} &middot; ${items.length} SKU${items.length !== 1 ? 'S' : ''}
                            </span>
                        </div>
                    </div>
                    <!-- Body: lista de SKUs -->
                    <div style="background:#fff;padding:14px 16px 16px">
                        <div style="margin-bottom:12px">
                            ${items.map((p, i) => `
                                <div style="display:flex;justify-content:space-between;align-items:center;
                                    padding:9px 12px;
                                    background:${i % 2 === 0 ? pal.light : '#fff'};
                                    border-radius:8px;margin-bottom:3px">
                                    <span style="font-size:1.15rem;font-weight:900;color:${pal.accent}">
                                        ${p.codigo_sistema || '-'}
                                    </span>
                                    <span style="font-size:1.15rem;font-weight:700;color:#374151">
                                        x${p.cavidades || 0}
                                    </span>
                                </div>`).join('')}
                        </div>
                        <button onclick="ModuloMes.cancelarBatch('${m}')"
                            style="width:100%;padding:7px;border:none;border-radius:10px;
                            background:#fee2e2;color:#b91c1c;font-size:.78rem;font-weight:600;cursor:pointer">
                            <i class="fas fa-times-circle me-1"></i> Liberar Máquina
                        </button>
                    </div>
                </div>
            </div>`;
        }).join('');
    },


    crearProgramacion: async function () {
        const maquina = document.getElementById('mes-prog-maquina').value;
        const observaciones = document.getElementById('mes-prog-obs').value;

        if (!maquina) {
            Swal.fire('Error', 'Debes seleccionar una máquina', 'error');
            return;
        }

        const productosParaEnviar = this.tempProductList;

        // NUEVO: Bloqueo estricto para productos no existentes Juan SEBASTIAN feedback
        for (const p of productosParaEnviar) {
            const pCodeNorm = p.codigo.replace(/[^0-9a-zA-Z]/g, '').toUpperCase();

            let existe = (this.productos || []).some(prod => {
                const prodCodeNorm = (prod.codigo || prod.codigo_sistema || '').replace(/[^0-9a-zA-Z]/g, '').toUpperCase();
                return prodCodeNorm === pCodeNorm || prodCodeNorm.includes(pCodeNorm) || pCodeNorm.includes(prodCodeNorm);
            });

            // Fallback: Si no está en el caché local de la vista, consultar la API real
            if (!existe) {
                try {
                    const res = await fetchData(`/api/productos/detalle/${p.codigo}`);
                    if (res && res.status === 'success' && res.producto) {
                        existe = true;
                    }
                } catch (e) {
                    console.warn('[MES] Fallback de validación falló:', e);
                }
            }

            if (!existe) {
                Swal.fire({
                    icon: 'error',
                    title: 'Producto no existe',
                    text: `El código "${p.codigo}" no está en el catálogo. Por favor verifícalo.`
                });
                return;
            }
        }

        // REGLA RELAJADA: Se permite el encolamiento de múltiples OPs para la misma máquina.
        // La validación de "Máquina Ocupada" ya no bloquea la creación (programación)
        // de la orden. Se bloqueará únicamente al "Iniciar Turno" operativo.

        if (productosParaEnviar.length === 0) {
            Swal.fire('Error', 'Añade al menos un producto a la lista', 'error');
            return;
        }

        const fecha = document.getElementById('mes-prog-fecha').value;
        // Código real del molde físico (ej. '5002A'), no una capacidad que
        // deba coincidir con la suma de cavidades -- esa regla se eliminó
        // 2026-08-28: ahora la tercera persona del equipo de programación
        // elige el molde según disponibilidad real (arreglo, cavidad dañada,
        // etc.), no según que la aritmética cuadre. Ver selector con
        // datalist en el HTML y cargarMoldesDisponibles().
        const molde = document.getElementById('mes-prog-molde').value.trim();

        // --- NUEVO: Crucial Check de Demanda B2B (Fase 2) ---
        // Verificar demanda de cada producto para lanzar Alerta de Desperdicio si tienen stock y 0 pedidos
        const warnings = [];
        for (const p of productosParaEnviar) {
            try {
                const checkRes = await fetchData(`/api/produccion/verificar_demanda/${p.codigo}`);
                if (checkRes && checkRes.success) {
                    const { unidades_pedidas_b2b, stock_actual_disponible } = checkRes.data;
                    if (stock_actual_disponible > 0 && unidades_pedidas_b2b === 0) {
                        warnings.push(`La referencia <strong>${p.codigo}</strong> tiene suficiente stock (${stock_actual_disponible} piezas) y <strong>0 pedidos B2B activos</strong>.`);
                    }
                }
            } catch (errCheck) {
                console.warn('[MES Check] Error al verificar demanda de pre-programación:', errCheck);
            }
        }

        if (warnings.length > 0) {
            const warningHtml = `
                <div class="text-start">
                    <p class="mb-2">Se detectó stock suficiente para referencias sin pedidos activos:</p>
                    <ul class="mb-0 text-danger fw-bold" style="font-size: 0.9rem;">
                        ${warnings.map(w => `<li class="mb-1">${w}</li>`).join('')}
                    </ul>
                    <p class="mt-3 mb-0 text-muted small"><i class="fas fa-info-circle me-1"></i> Se recomienda priorizar referencias con backorder para evitar desperdicio y optimizar la capacidad de las máquinas.</p>
                </div>
            `;

            const { isConfirmed } = await Swal.fire({
                title: 'Alerta de Desperdicio de Capacidad',
                html: warningHtml,
                icon: 'warning',
                showCancelButton: true,
                confirmButtonColor: '#eab308',
                confirmButtonText: 'Sí, programar de todas formas',
                cancelButtonText: 'Cancelar y revisar'
            });

            if (!isConfirmed) {
                return; // Detener la programación
            }
        }

        // RECOPILAR PEDIDOS ASIGNADOS (TRAZABILIDAD VESPERTINA):
        const pedidosAsignados = [];
        const inputsCubetas = document.querySelectorAll('.input-cant-asignada');
        inputsCubetas.forEach(input => {
            const cant = parseInt(input.value) || 0;
            if (cant > 0) {
                pedidosAsignados.push({
                    id_pedido: input.getAttribute('data-id-pedido'),
                    cant_requerida: cant,
                    codigo_producto: input.getAttribute('data-codigo-producto') || ''
                });
            }
        });

        const usarNuevoEndpoint = pedidosAsignados.length > 0;
        const urlEndpoint = usarNuevoEndpoint ? '/api/programacion/guardar' : '/api/mes/programar';

        let payload;
        if (usarNuevoEndpoint) {
            payload = {
                maquina: maquina,
                productos: productosParaEnviar.map(p => {
                    // Calcular cantidad específica para este producto sumando sus cubetas
                    const totalSKU = pedidosAsignados
                        .filter(pa => pa.codigo_producto === p.codigo)
                        .reduce((sum, pa) => sum + pa.cant_requerida, 0);

                    return {
                        codigo_sistema: p.codigo,
                        cavidades: p.cavidades,
                        cantidad: totalSKU
                    };
                }),
                molde: molde,
                fecha: fecha,
                responsable_planta: window.AuthModule?.currentUser?.nombre || 'ADMIN',
                observaciones: observaciones,
                pedidos_asignados: pedidosAsignados
            };
        } else {
            payload = {
                maquina: maquina,
                fecha: fecha,
                molde: molde,
                productos: productosParaEnviar,
                observaciones: observaciones,
                responsable_planta: window.AuthModule?.currentUser?.nombre || 'ADMIN'
            };
        }

        try {
            mostrarLoading(true);
            const res = await fetchData(urlEndpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            mostrarLoading(false);

            if (res && res.success) {
                // '/api/mes/programar' (endpoint legacy, sin refactorizar) sigue
                // devolviendo 'message' en la raíz; '/api/programacion/guardar' ya
                // lo anida en 'data'. Se prueban ambas rutas sin tocar el legacy.
                // TODO: Remover fallback cuando el backend migre 100%.
                // orden_produccion: la OP que OpNumeradorService ya asignó sola al
                // programar (pedido del usuario 2026-08-27: mostrar cuál quedó).
                const opAsignada = res.data?.orden_produccion || res.orden_produccion || '';
                const mensajeBase = res.data?.message || res.message || 'Programación diaria guardada correctamente.';
                Swal.fire({
                    icon: 'success',
                    title: '¡Programado!',
                    html: opAsignada
                        ? `${mensajeBase}<div class="mt-3"><span class="badge bg-primary fs-6">OP asignada: ${opAsignada}</span></div>`
                        : mensajeBase
                });

                this.tempProductList = [];
                this.renderTempList();
                document.getElementById('form-mes-programar').reset();
                this.actualizarVisibilidadAgregar();
                if (window.FormHelpers) window.FormHelpers.limpiarPersistencia('form-mes-programar');
                // Refrescar tanto la tabla de cola como las tarjetas de máquinas
                await this.actualizarColaProgramacion();
                await this.cargarDashboard();   // ← actualiza las cards a estado PROGRAMADO
            } else {
                Swal.fire('Error', res?.error || 'No se pudo programar', 'error');
            }
        } catch (error) {
            mostrarLoading(false);
            console.error('[MES] Error al programar:', error);
            Swal.fire('Error', 'Error de red al intentar programar', 'error');
        }
    },

    cancelarBatch: async function (maquina) {
        if (!this.canOperarMaquina()) {
            Swal.fire('Acceso Denegado', 'No tienes permisos para liberar máquinas.', 'error');
            return;
        }
        const result = await Swal.fire({
            title: '¿Liberar Máquina?',
            text: `Se cancelarán todas las programaciones pendientes para ${maquina}.`,
            icon: 'warning',
            showCancelButton: true,
            confirmButtonColor: '#ef4444',
            confirmButtonText: 'Sí, liberar',
            cancelButtonText: 'Cancelar'
        });

        if (result.isConfirmed) {
            try {
                mostrarLoading(true);

                // Leer la cola de la máquina — Búsqueda insensible a mayúsculas
                const maquinaData = (this.dashboardData || []).find(m => 
                    (m.nombre || '').toUpperCase() === (maquina || '').toUpperCase()
                );
                const cola = maquinaData?.cola || [];

                if (cola.length === 0) {
                    mostrarLoading(false);
                    Swal.fire('Aviso', 'No hay programaciones activas para esta máquina.', 'info');
                    return;
                }

                // Obtener IDs de la cola (Programaciones)
                const idsProg = [...new Set(cola.map(p => p.id_programacion || p.id).filter(Boolean))];
                
                // Obtener ID del trabajo activo (Producción) si aplica
                const idActivo = maquinaData.trabajo_activo?.id_inyeccion || maquinaData.trabajo_activo?.id;

                const todosLosIds = [...idsProg];
                if (idActivo) todosLosIds.push(idActivo);

                if (todosLosIds.length === 0) {
                    mostrarLoading(false);
                    Swal.fire('Aviso', 'No hay trabajos para cancelar en esta máquina.', 'info');
                    return;
                }

                for (const id of todosLosIds) {
                    await fetchData(`/api/mes/cancelar/${id}`, { method: 'POST' });
                }

                mostrarLoading(false);
                await this.cargarDashboard();
                await this.actualizarColaProgramacion();
                Swal.fire('Liberada', `La máquina ${maquina} ya no tiene trabajos pendientes.`, 'success');
            } catch (error) {
                mostrarLoading(false);
                console.error('[MES] Error liberando máquina:', error);
                Swal.fire('Error', 'No se pudieron cancelar todos los trabajos', 'error');
            }
        }
    },

    /**
     * Repite en una sola máquina la última programación conocida (mismo
     * molde/referencia/cavidades) para la fecha seleccionada en el formulario
     * -- atajo para cuando la máquina sigue con el mismo montaje del día
     * anterior y no hay nada que cambiar.
     */
    retomarMaquina: async function (maquina) {
        if (!this.canOperarMaquina()) {
            Swal.fire('Acceso Denegado', 'No tienes permisos para programar máquinas.', 'error');
            return;
        }
        const fecha = document.getElementById('mes-prog-fecha')?.value || '';
        try {
            mostrarLoading(true);
            const res = await fetchData('/api/mes/retomar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ maquinas: [maquina], fecha })
            });
            mostrarLoading(false);

            if (res && res.success && res.retomadas?.length > 0) {
                const r = res.retomadas[0];
                Swal.fire('Retomada', `${maquina} quedó programada con el molde ${r.molde} (${r.productos} referencia${r.productos !== 1 ? 's' : ''}), igual que su última programación.`, 'success');
            } else {
                const motivo = res?.omitidas?.[0]?.motivo || res?.error || 'No se pudo retomar la máquina.';
                Swal.fire('Sin cambios', motivo, 'info');
            }
            await this.cargarDashboard();
            await this.actualizarColaProgramacion();
        } catch (error) {
            mostrarLoading(false);
            console.error('[MES] Error al retomar máquina:', error);
            Swal.fire('Error', 'Error de red al intentar retomar la máquina', 'error');
        }
    },

    /**
     * "Retomar General": repite la última programación conocida en TODAS las
     * máquinas que aún sigan sin programar para la fecha seleccionada. Las
     * que ya tienen algo cargado (o producción activa) se dejan intactas.
     */
    retomarGeneral: async function () {
        if (!this.canOperarMaquina()) {
            Swal.fire('Acceso Denegado', 'No tienes permisos para programar máquinas.', 'error');
            return;
        }
        const fecha = document.getElementById('mes-prog-fecha')?.value || '';
        const confirm = await Swal.fire({
            title: '¿Retomar todas las máquinas?',
            text: 'Las máquinas que ya tengan programación o producción activa para esta fecha se dejarán intactas. El resto retomará su último molde/referencia conocido.',
            icon: 'question',
            showCancelButton: true,
            confirmButtonColor: '#0d6efd',
            confirmButtonText: 'Sí, retomar todas',
            cancelButtonText: 'Cancelar'
        });
        if (!confirm.isConfirmed) return;

        try {
            mostrarLoading(true);
            const res = await fetchData('/api/mes/retomar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ maquinas: [], fecha })
            });
            mostrarLoading(false);

            if (res && res.success) {
                const retomadas = res.retomadas || [];
                const omitidas = res.omitidas || [];
                const detalleOmitidas = omitidas.length > 0
                    ? `<br><br><small class="text-muted">Sin cambios: ${omitidas.map(o => `${o.maquina} (${o.motivo})`).join(', ')}</small>`
                    : '';
                Swal.fire({
                    icon: retomadas.length > 0 ? 'success' : 'info',
                    title: retomadas.length > 0 ? '¡Máquinas Retomadas!' : 'Sin máquinas para retomar',
                    html: `${retomadas.length} máquina${retomadas.length !== 1 ? 's' : ''} quedaron programadas igual que su última jornada.${detalleOmitidas}`
                });
            } else {
                Swal.fire('Error', res?.error || 'No se pudo retomar la programación', 'error');
            }
            await this.cargarDashboard();
            await this.actualizarColaProgramacion();
        } catch (error) {
            mostrarLoading(false);
            console.error('[MES] Error al retomar general:', error);
            Swal.fire('Error', 'Error de red al intentar retomar la programación', 'error');
        }
    },

    // --- LÓGICA DE OPERACIÓN (Fase 2) ---

    cambiarMaquina: function (idMaquina) {
        this.maquinaSeleccionada = idMaquina;
        localStorage.setItem('mes_maquina_ref', idMaquina);
        this.actualizarEstadoMaquina();
    },

    actualizarEstadoMaquina: async function () {
        if (!this.maquinaSeleccionada) return;

        try {
            // Usamos el nuevo endpoint de status
            const data = await fetchData(`/api/mes/status/${this.maquinaSeleccionada}`);
            this.trabajoActivo = (data && data.estado !== 'LIBRE') ? data : null;
            this.renderOperacion();
        } catch (error) {
            console.error('[MES] Error cargando estado máquina:', error);
        }
    },

    renderOperacion: function () {
        const card = document.getElementById('mes-operacion-card');
        const empty = document.getElementById('mes-empty-operacion');
        const statusBadge = document.getElementById('mes-status-maquina');

        if (!this.trabajoActivo) {
            if (card) card.style.display = 'none';
            if (empty) empty.style.display = 'block';
            if (statusBadge) {
                statusBadge.className = 'badge bg-secondary p-3 fs-6 rounded-pill';
                statusBadge.innerText = 'Máquina Sin Programación';
            }
            return;
        }

        if (card) card.style.display = 'block';
        if (empty) empty.style.display = 'none';

        // Info Superior
        const infoDiv = document.getElementById('mes-info-trabajo');
        if (infoDiv) {
            infoDiv.innerHTML = `
                <div class="d-flex justify-content-between">
                    <div>
                        <h4 class="fw-bold mb-1">${this.trabajoActivo.producto}</h4>
                        <span class="text-muted small">Estado: ${this.trabajoActivo.estado}</span>
                    </div>
                    <div class="text-end">
                        <div class="fw-bold">Molde: ${this.trabajoActivo.molde || 'N/A'}</div>
                        <div class="text-muted small">${this.trabajoActivo.cavidades} Cavidades</div>
                    </div>
                </div>
            `;
        }

        // Indicadores Laterales
        const txtTeorica = document.getElementById('mes-txt-teorica');
        if (txtTeorica) txtTeorica.innerText = this.trabajoActivo.teorica || '--';

        const txtMolde = document.getElementById('mes-txt-molde');
        if (txtMolde) txtMolde.innerText = this.trabajoActivo.molde || 'N/A';

        const txtCav = document.getElementById('mes-txt-cavidades');
        if (txtCav) txtCav.innerText = `${this.trabajoActivo.cavidades} cavidades`;

        const txtInicio = document.getElementById('mes-txt-hora-inicio');
        if (txtInicio) txtInicio.innerText = this.trabajoActivo.inicio || '--:--';

        // Switch de pasos
        const stepIniciar = document.getElementById('mes-step-iniciar');
        const stepReportar = document.getElementById('mes-step-reportar');

        if (this.trabajoActivo.estado === 'PROGRAMADO') {
            if (statusBadge) {
                statusBadge.className = 'badge bg-primary p-3 fs-6 rounded-pill animate__animated animate__pulse animate__infinite';
                statusBadge.innerText = 'TRABAJO PENDIENTE';
            }
            if (stepIniciar) stepIniciar.style.display = 'block';
            if (stepReportar) stepReportar.style.display = 'none';
        } else if (this.trabajoActivo.estado === 'EN_PROCESO') {
            if (statusBadge) {
                statusBadge.className = 'badge bg-success p-3 fs-6 rounded-pill animate__animated animate__flash animate__slow animate__infinite';
                statusBadge.innerText = '▶ TRABAJANDO...';
            }
            if (stepIniciar) stepIniciar.style.display = 'none';
            if (stepReportar) stepReportar.style.display = 'block';
        }
    },

    iniciarTrabajo: async function () {
        if (!this.trabajoActivo) return;

        const maquinaNombre = this.maquinaSeleccionada || '';
        let defaultResp = '';
        if (maquinaNombre.includes('1') || maquinaNombre.includes('2')) {
            defaultResp = 'Richard Lobo';
        } else {
            defaultResp = document.getElementById('current_user_fullname')?.value || '';
        }

        const { value: formValues } = await Swal.fire({
            title: '¿Confirmar Inicio?',
            html: `
                <div class="mb-3 text-start">
                    <p class="mb-2 text-muted">Vas a iniciar la producción de <b>${this.trabajoActivo.producto}</b> en la <b>${maquinaNombre}</b>.</p>
                </div>
                <div class="mb-3 text-start">
                    <label for="swal-responsable-iniciar" class="form-label fw-bold small text-uppercase text-muted">Operario / Responsable</label>
                    <input type="text" id="swal-responsable-iniciar" class="form-control" value="${defaultResp}">
                </div>
            `,
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: 'Sí, iniciar',
            focusConfirm: false,
            preConfirm: () => {
                const resp = document.getElementById('swal-responsable-iniciar').value;
                return { responsable: resp };
            }
        });

        if (formValues) {
            try {
                mostrarLoading(true);
                const res = await fetchData('/api/mes/iniciar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id_programacion: this.trabajoActivo.id_programacion,
                        responsable: formValues.responsable
                    })
                });
                mostrarLoading(false);
                if (res && res.success) {
                    this.actualizarEstadoMaquina();
                } else {
                    Swal.fire('Error', res?.error || 'No se pudo iniciar', 'error');
                }
            } catch (error) {
                mostrarLoading(false);
                console.error('[MES] Error iniciando:', error);
            }
        }
    },

    finalizarTrabajo: async function () {
        const cierres = parseInt(document.getElementById('mes-op-cierres').value);
        if (!cierres || cierres <= 0) {
            Swal.fire('Atenci\u00f3n', 'Debe reportar el n\u00famero de cierres del contador', 'warning');
            return;
        }

        // Obtener la hora actual en formato HH:MM para sugerir como Hora Fin
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, '0');
        const mm = String(now.getMinutes()).padStart(2, '0');
        const horaSugerida = `${hh}:${mm}`;

        // Extraer los códigos de producto individuales de la máquina activa
        const codigo = this.trabajoActivo.producto || '';
        const idInyeccion = this.trabajoActivo.id_inyeccion;
        const horaInicio = this.trabajoActivo.inicio || '06:00';
        const productos = codigo.split(',').map(p => p.trim()).filter(Boolean);
        const maquinaNombre = this.maquinaSeleccionada || '';
        
        let defaultResp = this.trabajoActivo.responsable || '';
        if (!defaultResp) {
            if (maquinaNombre.includes('1') || maquinaNombre.includes('2')) {
                defaultResp = 'Richard Lobo';
            } else {
                defaultResp = document.getElementById('current_user_fullname')?.value || '';
            }
        }
        
        let pncHtml = '';
        if (productos.length > 0) {
            pncHtml = `
                <div class="mt-4 border-top pt-3 text-start">
                    <h6 class="fw-bold text-danger mb-3"><i class="fas fa-exclamation-triangle me-1"></i> Reportar Defectos (PNC) Obligatorio</h6>
            `;
            productos.forEach((prod, index) => {
                pncHtml += `
                    <div class="card p-3 mb-3 border-0 shadow-sm" style="border-radius: 12px; background: #fffafb; border: 1px solid #fee2e2 !important;">
                        <div class="fw-bold text-danger mb-2" style="font-size: 0.9rem;"><i class="fas fa-cog me-1"></i> Producto: ${prod}</div>
                        <div class="row g-2">
                            <div class="col-4">
                                <label class="form-label small fw-bold text-muted mb-0">Quemado</label>
                                <input type="number" id="swal-pnc-quemado-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-4">
                                <label class="form-label small fw-bold text-muted mb-0">Falta Llenado</label>
                                <input type="number" id="swal-pnc-incompleto-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-4">
                                <label class="form-label small fw-bold text-muted mb-0">Rebaba</label>
                                <input type="number" id="swal-pnc-rebaba-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-6 mt-2">
                                <label class="form-label small fw-bold text-muted mb-0">Burbujas/Porosidad</label>
                                <input type="number" id="swal-pnc-burbuja-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                            <div class="col-6 mt-2">
                                <label class="form-label small fw-bold text-muted mb-0">Deformación</label>
                                <input type="number" id="swal-pnc-deformacion-${index}" class="form-control form-control-sm text-center fw-bold" min="0" value="0">
                            </div>
                        </div>
                    </div>
                `;
            });
            pncHtml += `</div>`;
        }

        const { value: formValues } = await Swal.fire({
            title: '\u00bfFinalizar Turno?',
            html: `
                <div class="text-start fs-6 mb-3 text-muted">Se reportar\u00e1n <b>${cierres} cierres</b> de molde. Revisa los tiempos del turno:</div>
                
                <div class="mb-3 text-start px-2">
                    <label class="form-label fw-bold small text-uppercase text-muted mb-1">Operario / Responsable</label>
                    <input type="text" id="swal-responsable-fin" class="form-control" value="${defaultResp}">
                </div>

                <div class="row text-start p-2 g-3">
                    <div class="col-6">
                        <label class="form-label fw-bold small text-uppercase text-muted mb-1">Hora Inicio Real</label>
                        <input type="time" id="swal-hora-inicio" class="form-control" value="${horaInicio}">
                    </div>
                    <div class="col-6">
                        <label class="form-label fw-bold small text-uppercase text-muted mb-1">Hora Fin Real</label>
                        <input type="time" id="swal-hora-fin" class="form-control" value="${horaSugerida}">
                    </div>
                </div>
                <div class="mt-2 small text-muted text-start ps-2 mb-3"><i class="fas fa-info-circle me-1"></i>La hora de inicio sugerida es la hora en que inici\u00f3 el lote.</div>
                ${pncHtml}
            `,
            focusConfirm: false,
            showCancelButton: true,
            confirmButtonText: '<i class="fas fa-check-circle me-1"></i> Finalizar y Reportar',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#16a34a',
            preConfirm: () => {
                const hi = document.getElementById('swal-hora-inicio').value;
                const hf = document.getElementById('swal-hora-fin').value;
                const resp = document.getElementById('swal-responsable-fin').value;
                if (!hi || !hf) {
                    Swal.showValidationMessage('Ambas horas son obligatorias');
                    return false;
                }

                // Extraer PNC para cada producto
                const pncList = [];
                productos.forEach((prod, index) => {
                    const q = parseInt(document.getElementById(`swal-pnc-quemado-${index}`).value) || 0;
                    const inc = parseInt(document.getElementById(`swal-pnc-incompleto-${index}`).value) || 0;
                    const r = parseInt(document.getElementById(`swal-pnc-rebaba-${index}`).value) || 0;
                    const b = parseInt(document.getElementById(`swal-pnc-burbuja-${index}`).value) || 0;
                    const d = parseInt(document.getElementById(`swal-pnc-deformacion-${index}`).value) || 0;

                    if (q > 0 || inc > 0 || r > 0 || b > 0 || d > 0) {
                        pncList.push({
                            codigo: prod,
                            defectos: {
                                "Quemado / Manchado": q,
                                "Falta de Llenado": inc,
                                "Rebaba": r,
                                "Burbujas": b,
                                "Deformación": d
                            }
                        });
                    }
                });

                return { hora_inicio: hi, hora_fin: hf, responsable: resp, pncList: pncList };
            }
        });

        if (formValues) {
            try {
                mostrarLoading(true);
                const res = await fetchData('/api/mes/reportar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id_inyeccion: idInyeccion,
                        cierres: cierres,
                        hora_inicio: formValues.hora_inicio,
                        hora_fin: formValues.hora_fin,
                        responsable: formValues.responsable
                    })
                });

                if (res && res.success) {
                    // Registrar o limpiar PNC para cada producto en db_pnc_inyeccion
                    for (const prod of productos) {
                        const foundPnc = formValues.pncList.find(p => p.codigo === prod);
                        const defectsPayload = foundPnc ? foundPnc.defectos : {};
                        try {
                            await fetchData('/api/pnc/registrar_inyeccion', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    id_inyeccion: idInyeccion,
                                    id_codigo: prod,
                                    defectos: defectsPayload
                                })
                            });
                        } catch (errPnc) {
                            console.error(`[MES] Error registrando PNC para ${prod}:`, errPnc);
                        }
                    }

                    mostrarLoading(false);
                    Swal.fire('Reportado', 'El trabajo ha finalizado y pasado a Validaci\u00f3n de Paola', 'success');
                    document.getElementById('mes-op-cierres').value = '';
                    await this.actualizarEstadoMaquina();
                    await this.cargarDashboard(); // Refrescar tarjetas de la izquierda
                } else {
                    mostrarLoading(false);
                    Swal.fire('Error', res?.error || 'No se pudo reportar', 'error');
                }
            } catch (error) {
                mostrarLoading(false);
                console.error('[MES] Error reportando:', error);
                Swal.fire('Error', 'Error de red al reportar', 'error');
            }
        }
    },





    // --- UTILS ---

    getColorEstado: function (estado) {
        switch (estado) {
            case 'PROGRAMADO': return 'bg-info text-white';
            case 'EN_PROCESO': return 'bg-success text-white';
            case 'PENDIENTE_CALIDAD': return 'bg-warning text-dark';
            case 'FINALIZADO': return 'bg-secondary text-white';
            default: return 'bg-light text-dark';
        }
    },

    actualizarSelect: function (id, datos) {
        const select = document.getElementById(id);
        if (!select) return;
        select.innerHTML = '<option value="">-- Seleccionar --</option>';
        if (datos && Array.isArray(datos)) {
            datos.forEach(item => {
                const opt = document.createElement('option');
                opt.value = item;
                opt.textContent = item;
                select.appendChild(opt);
            });
        }
    },

    // ====================================================================
    // TRAZABILIDAD VESPERTINA: MÓDULO DE CUBETAS
    // ====================================================================

    /**
     * Busca pedidos pendientes para un código de producto desde la API
     */
    buscarPedidosPendientes: async function (codigo) {
        console.log("🔥 Ejecutando buscarPedidosPendientes para:", codigo); // Rastreador 1
        const contenedor = document.getElementById('contenedor-pedidos-pendientes');
        console.log("📦 ¿Existe el contenedor en el HTML?", contenedor); // Rastreador 2
        
        if (!contenedor) {
            console.warn("⚠️ ALERTA: No se encontró el contenedor-pedidos-pendientes en el HTML");
            return;
        }

        try {
            contenedor.innerHTML = `
                <div class="text-center py-3 text-muted small">
                    <i class="fas fa-spinner fa-spin me-1"></i> Consultando pedidos pendientes de World Office...
                </div>`;

            const res = await fetchData(`/api/pedidos/pendientes/${codigo}`);

            if (res && res.success && res.data?.pedidos && res.data.pedidos.length > 0) {
                this.renderizarTablaPedidos(res.data.pedidos, codigo);
            } else {
                // Eliminado a petición de UX: No mostrar alert-info cuando la demanda es 0
                contenedor.innerHTML = '';
            }
        } catch (error) {
            console.error('[TRAZABILIDAD] Error al buscar pedidos:', error);
            contenedor.innerHTML = `
                <div class="alert alert-danger py-2 px-3 border-0 small text-center mt-3" style="border-radius:12px;">
                    <i class="fas fa-exclamation-triangle me-1"></i> Error al conectar con el servidor de pedidos.
                </div>`;
        }
    },

    /**
     * Renderiza dinámicamente la tabla de cubetas para asignación de pedidos
     */
    renderizarTablaPedidos: function (pedidos, codigoProducto = '') {
        const contenedor = document.getElementById('contenedor-pedidos-pendientes');
        if (!contenedor) return;

        const filas = pedidos.map(p => `
            <tr class="align-middle">
                <td class="fw-bold text-primary small" data-label="Pedido">#${p.id_pedido}</td>
                <td class="small text-truncate" style="max-width: 140px;" title="${p.cliente}" data-label="Cliente">${p.cliente}</td>
                <td class="text-center small" data-label="Solicitado">${p.cantidad_solicitada}</td>
                <td class="text-center small fw-bold text-secondary" data-label="Pendiente">${p.cantidad_pendiente}</td>
                <td style="width: 100px;" data-label="Asignar">
                    <input type="number" 
                           class="form-control form-control-sm text-center fw-bold input-cant-asignada border-0 bg-light"
                           style="border-radius: 8px;"
                           min="0" 
                           max="${p.cantidad_pendiente}" 
                           value="${p.cantidad_pendiente}"
                           placeholder="0"
                           data-id-pedido="${p.id_pedido}"
                           data-pendiente="${p.cantidad_pendiente}"
                           data-codigo-producto="${codigoProducto}"
                           oninput="ModuloMes.calcularTotalAsignado()">
                </td>
            </tr>
        `).join('');

        contenedor.innerHTML = `
            <div class="card border-0 shadow-sm mt-3" style="border-radius: 16px; background: #ffffff;">
                <div class="card-header bg-white border-0 pt-3 pb-0">
                    <h6 class="fw-bold mb-0 text-dark">
                        <i class="fas fa-cubes text-primary me-2"></i>Distribución a Cubetas de Pedidos
                    </h6>
                    <small class="text-muted text-xs">Asigna la cantidad a inyectar para cada pedido pendiente</small>
                </div>
                <div class="card-body px-3 py-2">
                    <div class="table-responsive" style="max-height: 250px; overflow-y: auto;">
                        <table class="table table-sm table-hover border-0 mb-2 responsive-mobile" style="font-size: 0.85rem;">
                            <thead>
                                <tr class="text-muted" style="font-size: 0.75rem; border-bottom: 2px solid #f1f5f9;">
                                    <th>Pedido</th>
                                    <th>Cliente</th>
                                    <th class="text-center">Solicitado</th>
                                    <th class="text-center">Pendiente</th>
                                    <th class="text-center" style="width: 100px;">Asignar</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${filas}
                            </tbody>
                        </table>
                    </div>
                    <div class="d-flex justify-content-between align-items-center pt-2 border-top" style="border-color: #f1f5f9 !important;">
                        <span class="small fw-bold text-muted">Total Distribuido:</span>
                        <span id="badge-total-asignado" class="badge bg-primary fs-7" style="border-radius: 8px; padding: 6px 12px;">0 piezas</span>
                    </div>
                </div>
            </div>
        `;

        // Actualizar el total distribuido inmediatamente al renderizar la tabla
        this.calcularTotalAsignado();
    },

    /**
     * Calculates and updates the dynamic counter for assigned pieces
     */
    calcularTotalAsignado: function () {
        let total = 0;
        const inputs = document.querySelectorAll('.input-cant-asignada');
        inputs.forEach(input => {
            const val = parseInt(input.value) || 0;
            total += val;
        });

        const badge = document.getElementById('badge-total-asignado');
        if (badge) {
            badge.innerText = `${total.toLocaleString()} piezas`;
        }
        return total;
    }
};

// Roles con acceso a /api/mes/* en el backend (ver ROLES_PLANTA en
// backend/routes/programacion_routes.py = ROL_ADMINS + ROL_JEFES + ROL_OPERARIOS).
// '#inyeccion-page' SIEMPRE existe en el DOM de este SPA (oculto, no removido)
// sin importar el rol, así que ese chequeo por sí solo no evitaba que roles
// como COMERCIAL dispararan fetch a estos endpoints y recibieran 403 en cada carga.
const ROLES_MES = [
    'ADMIN', 'ADMINISTRACION', 'ADMINISTRADOR', 'GERENCIA',
    'JEFE ALMACEN', 'JEFE INYECCION', 'JEFE PULIDO', 'JEFE DE PLANTA', 'JEFE ALISTAMIENTO',
    'INYECCION', 'PULIDO', 'ALISTAMIENTO', 'ENSAMBLE', 'AUXILIAR INVENTARIO'
];

// Auto-inicializar cuando el DOM esté listo
document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('inyeccion-page')) return;

    let rolActual = '';
    try {
        const stored = sessionStorage.getItem('friparts_user');
        rolActual = stored ? (JSON.parse(stored).rol || '').toString().trim().toUpperCase() : '';
    } catch (e) {
        rolActual = '';
    }

    if (ROLES_MES.includes(rolActual)) {
        ModuloMes.init();
    }
});
