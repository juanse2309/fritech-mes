// ============================================
// pulido.js - Módulo de Pulido (Versión DUAL FINAL - Satélite vs Planta)
// ============================================

const ModuloPulido = {
    productosData: [],
    responsablesData: [],
    selectedProduct: null,
    
    // Pro Mode State
    sesionActiva: false,
    enPausa: false,
    pausaTime: null,
    totalPausaMs: 0,
    tiempoAcumuladoMs: 0, // NUEVO: Tiempo de segmentos anteriores
    timerInterval: null,
    sessionId: null,

    // PNC Dynamic State
    pncRows: [],
    revueltosRows: [],
    catalogosPnc: {
        'INYECCION': ["Rechupe", "Quemado", "Retención", "Incompleto/Escaso", "Contaminado", "Mancha", "Deformado", "Otros"],
        'PULIDO': ["Rayado", "Porosidad", "Exceso de Rebaba", "Medida Incorrecta", "Mal Acabado", "Otros"],
        'ENSAMBLE': ["Falta de Componente", "Mal Ajuste", "Inserto Defectuoso", "Daño Físico", "Otros"]
    },

    // Helper de Normalización
    normalizarCodigo: function(c) {
        if (!c) return "";
        return String(c).toUpperCase().replace(/FR-/gi, "").trim();
    },

    // ==========================================================
    // STORAGE NAMESPACING (evita colisiones en tablet compartida)
    // ==========================================================
    getOperarioActual: function () {
        // Prioridad: sesión autenticada -> input -> fallback
        const u = window.AuthModule?.currentUser?.nombre
            || window.AppState?.user?.nombre
            || window.AppState?.user?.name;
        const input = document.getElementById('responsable-pulido-input')?.value;
        const raw = (u || input || '').toString().trim();
        return raw;
    },

    storageKey: function (baseKey) {
        const operario = (this.getOperarioActual() || 'ANON').toString().trim().toUpperCase();
        return `${baseKey}::${operario}`;
    },

    getLastResponsableKey: function () {
        return this.storageKey('pulido_last_responsable');
    },

    getStateKey: function () {
        return this.storageKey('pulido_state');
    },

    // Limpia posibles keys antiguas globales (migración suave)
    limpiarLegacyStorageKeys: function () {
        try {
            if (localStorage.getItem('pulido_state') && !localStorage.getItem(this.getStateKey())) {
                // No migramos: solo evitamos que afecte a otro operario
                localStorage.removeItem('pulido_state');
            }
            if (localStorage.getItem('pulido_last_responsable') && !localStorage.getItem(this.getLastResponsableKey())) {
                localStorage.removeItem('pulido_last_responsable');
            }
        } catch (e) {
            console.warn('[Pulido] No se pudo limpiar legacy keys:', e);
        }
    },

    inicializar: async function () {
        console.log('🔧 [Pulido] Inicializando módulo DUAL FINAL...');
        this.configurarUI();
        await this.cargarDatosMaestros();
        this.initAutocompletes();
        this.limpiarLegacyStorageKeys();
        
        // --- LIMPIEZA POR CAMBIO DE VERSIÓN (v4.5 - Fix orden_produccion) ---
        const PULIDO_VERSION = '4.5';
        if (localStorage.getItem('pulido_app_version') !== PULIDO_VERSION) {
            console.log("🚀 [Pulido] Nueva versión detectada (v4.5). Limpiando caché para sincronización...");
            // Limpiar solo estados de sesión para no borrar preferencias de usuario
            Object.keys(localStorage).forEach(key => {
                if (key.includes('pulido_state')) localStorage.removeItem(key);
            });
            localStorage.setItem('pulido_app_version', PULIDO_VERSION);
        }

        this.cargarCacheUI();
        await this.verificarTrabajoActivo(); // Rehidratar desde SQL
        this.cargarEstadoLocal(); // Fallback/Sync local
        
        // Verificación de reportes pendientes por fallo de red previo
        this.verificarReportesPendientes();

        // Cargar el último registro guardado (banner satélite)
        this.actualizarBannerUltimoRegistro();

        // Keep-Alive: Ping al servidor cada 5 min para evitar que Render se duerma
        this.iniciarPingServidor();

        // Sync default mode state based on switch
        const switchEl = document.getElementById('toggle-pulido-mode');
        if (switchEl) {
            this.cambiarModo(switchEl.checked);
        }

        // Si cambia el usuario en el mismo navegador (tablet compartida),
        // cortar intervalos y cargar estado del nuevo operario.
        const onUserReady = () => {
            if (this.timerInterval) clearInterval(this.timerInterval);
            this.timerInterval = null;
            this.cargarCacheUI();
            this.verificarTrabajoActivo().then(() => this.cargarEstadoLocal());
        };
        document.addEventListener('user-ready', onUserReady);
    },

    iniciarPingServidor: function() {
        console.log("📡 [Pulido] Iniciando Keep-Alive (ping cada 5 min)...");
        setInterval(async () => {
            try {
                // Ping silencioso al endpoint de sesión para mantener el servidor despierto
                await fetch('/api/pulido/session_active?ping=true');
            } catch (e) {
                console.warn("[Keep-Alive] Fallo de ping:", e);
            }
        }, 5 * 60 * 1000); // 5 minutos
    },

    // ──────────────────────────────────────────────────────────────────
    // COLA DE REPORTES FALLIDOS (localStorage)
    // Se guarda como LISTA (no una sola llave) para que fallos consecutivos
    // no se sobrescriban entre sí y se pierdan reportes silenciosamente.
    // ──────────────────────────────────────────────────────────────────
    _FAILED_REPORTS_KEY: 'pulido_failed_reports_queue',

    _obtenerReportesFallidos: function() {
        try {
            const raw = localStorage.getItem(this._FAILED_REPORTS_KEY);
            const lista = raw ? JSON.parse(raw) : [];
            return Array.isArray(lista) ? lista : [];
        } catch (e) {
            console.warn('[Pulido] Cola de reportes fallidos corrupta, se reinicia:', e);
            return [];
        }
    },

    _guardarReportesFallidos: function(lista) {
        localStorage.setItem(this._FAILED_REPORTS_KEY, JSON.stringify(lista));
    },

    _agregarOActualizarReporteFallido: function(data) {
        const lista = this._obtenerReportesFallidos();
        const idx = lista.findIndex(r => r._localId === data._localId);
        if (idx >= 0) {
            lista[idx] = data;
        } else {
            lista.push(data);
        }
        this._guardarReportesFallidos(lista);
    },

    _removerReporteFallido: function(localId) {
        const lista = this._obtenerReportesFallidos().filter(r => r._localId !== localId);
        this._guardarReportesFallidos(lista);
    },

    verificarReportesPendientes: function() {
        const pendientes = this._obtenerReportesFallidos();
        if (pendientes.length === 0) return;

        const siguiente = pendientes[0];
        const texto = pendientes.length > 1
            ? `Hay ${pendientes.length} reportes que no se pudieron enviar anteriormente por fallo de red. Se procesarán uno a uno. ¿Reintentar el primero ahora?`
            : 'Se detectó un reporte que no se pudo enviar anteriormente por fallo de red. ¿Deseas intentar enviarlo de nuevo?';

        Swal.fire({
            title: pendientes.length > 1 ? `${pendientes.length} Reportes Pendientes` : 'Reporte Pendiente',
            text: texto,
            icon: 'info',
            showCancelButton: true,
            confirmButtonText: 'Sí, reintentar envío',
            cancelButtonText: 'Descartar este',
            confirmButtonColor: '#3b82f6'
        }).then(async (result) => {
            if (result.isConfirmed) {
                await this.enviarAServidor(siguiente);
            } else if (result.dismiss === Swal.DismissReason.cancel) {
                this._removerReporteFallido(siguiente._localId);
                this.verificarReportesPendientes(); // Seguir con el siguiente pendiente, si hay
            }
        });
    },

    guardarEstadoLocal: function() {
        const estado = {
            sesionActiva: this.sesionActiva,
            sessionId: this.sessionId,
            startTime: this.startTime ? this.startTime.getTime() : null,
            totalPausaMs: this.totalPausaMs,
            tiempoAcumuladoMs: this.tiempoAcumuladoMs,
            enPausa: this.enPausa,
            pausaTime: this.pausaTime ? this.pausaTime.getTime() : null,
            sesionesEnPausa: this.sesionesEnPausa,
            // Guardar quién es el operario para validar al rehidratar
            responsable: document.getElementById('responsable-pulido-input')?.value || '',
            formData: {
                resp: document.getElementById('responsable-pulido-input')?.value,
                prod: document.getElementById('buscador-productos')?.value,
                op: document.getElementById('orden-produccion-pulido')?.value,
                lote: document.getElementById('lote-pulido')?.value
            }
        };
        localStorage.setItem(this.getStateKey(), JSON.stringify(estado));
    },

    cargarEstadoLocal: function() {
        const raw = localStorage.getItem(this.getStateKey());
        if (!raw) return;
        try {
            const estado = JSON.parse(raw);
            if (estado.sesionActiva) {
                // BLINDAJE OPERARIO: Solo rehidratar si el operario guardado
                // coincide con el operario actualmente logueado
                const operarioActual = document.getElementById('responsable-pulido-input')?.value?.trim()
                    || localStorage.getItem(this.getLastResponsableKey()) || '';
                const operarioGuardado = (estado.responsable || estado.formData?.resp || '').trim();

                if (operarioActual && operarioGuardado && operarioActual.toUpperCase() !== operarioGuardado.toUpperCase()) {
                    console.log(`🚫 [Pulido] Estado local pertenece a '${operarioGuardado}' pero el operario actual es '${operarioActual}' — ignorando.`);
                    localStorage.removeItem(this.getStateKey());
                    return;
                }

                // BLINDAJE hora_inicio: No arrancar cronómetro con startTime nulo
                if (!estado.startTime) {
                    console.log('🚫 [Pulido] startTime nulo en estado local — descartando sesión corrupta.');
                    localStorage.removeItem(this.getStateKey());
                    return;
                }

                console.log("♻️ Rehidratando sesión activa de Pulido...");
                this.sesionActiva = true;
                this.sessionId = estado.sessionId;
                this.startTime = new Date(estado.startTime);
                this.totalPausaMs = estado.totalPausaMs;
                this.tiempoAcumuladoMs = estado.tiempoAcumuladoMs || 0;
                this.enPausa = estado.enPausa;
                if (estado.pausaTime) this.pausaTime = new Date(estado.pausaTime);

                // Restaurar formulario
                if (estado.formData) {
                    const r = document.getElementById('responsable-pulido-input');
                    const p = document.getElementById('buscador-productos');
                    const o = document.getElementById('orden-produccion-pulido');
                    const l = document.getElementById('lote-pulido');
                    if(r) r.value = estado.formData.resp || '';
                    if(p) p.value = estado.formData.prod || '';
                    if(o) o.value = estado.formData.op || '';
                    if(l) l.value = estado.formData.lote || '';
                }

                // UI
                document.getElementById('pulido-idle-msg').style.display = 'none';
                document.getElementById('pulido-active-msg').style.display = 'block';
                document.getElementById('pulido-session-id-display').innerText = this.sessionId;
                
                ['fecha-pulido', 'responsable-pulido-input', 'buscador-productos', 'orden-produccion-pulido', 'lote-pulido'].forEach(id => {
                    const el = document.getElementById(id);
                    if(el) el.disabled = true;
                });

                document.getElementById('btn-iniciar-pulido').disabled = true;
                document.getElementById('btn-pausar-pulido').disabled = false;
                document.getElementById('btn-terminar-pulido').disabled = false;
                document.getElementById('btn-cambiar-ref-pulido').style.display = 'block';

                if (this.enPausa) {
                    const btn = document.getElementById('btn-pausar-pulido');
                    btn.innerHTML = '<i class="fas fa-play me-2"></i> Reanudar';
                    btn.className = 'btn btn-info btn-lg p-3 shadow';
                    document.getElementById('pulido-pausa-msg').style.display = 'block';
                }
                this.timerInterval = setInterval(() => this.actualizarTimer(), 1000);
            }
            if (estado.sesionesEnPausa && estado.sesionesEnPausa.length > 0) {
                this.sesionesEnPausa = estado.sesionesEnPausa;
                this.renderCola();
            }
        } catch (e) {
            console.error("Error rehidratando:", e);
            localStorage.removeItem(this.getStateKey());
        }
    },

    sesionesEnPausa: [],

    configurarUI: function () {
        // Set fecha de hoy por defecto y sincronizar Lote
        const fechaInput = document.getElementById('fecha-pulido');
        const loteInput = document.getElementById('lote-pulido');

        if (fechaInput) {
            fechaInput.value = new Date().toISOString().split('T')[0];
        }
        if (loteInput && !loteInput.value) {
            loteInput.value = new Date().toISOString().split('T')[0];
        }
        
        // Reset manual display
        this.actualizarCalculoManual();

        // Sincronizar encabezado "Trabajando en" en tiempo real
        const actualizarHeader = () => {
            const prodRaw = document.getElementById('buscador-productos')?.value || '---';
            const prod = this.normalizarCodigo(prodRaw) || '---';
            const lote = document.getElementById('lote-pulido')?.value || '---';
            const display = document.getElementById('current-pulido-job');
            if (display && this.sesionActiva) {
                display.innerText = `${prod} | Lote: ${lote}`;
            }
        };

        ['responsable-pulido-input', 'buscador-productos', 'lote-pulido'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('input', () => {
                    actualizarHeader();
                    this.validarBotonInicioPro();
                });
                el.addEventListener('change', () => {
                    actualizarHeader();
                    this.validarBotonInicioPro();
                });
            }
        });

        this.validarBotonInicioPro();
        this.renderCola();
    },

    validarBotonInicioPro: function() {
        const resp = document.getElementById('responsable-pulido-input')?.value?.trim();
        const prod = document.getElementById('buscador-productos')?.value?.trim();
        const lote = document.getElementById('lote-pulido')?.value?.trim();
        const btn = document.getElementById('btn-iniciar-pulido');
        
        if (btn) {
            if (resp && prod && lote && !this.sesionActiva) {
                btn.disabled = false;
            } else {
                btn.disabled = true;
            }
        }
    },

    cambiarModo: function (isPro) {
        console.log("🔄 Cambiando a Modo:", isPro ? "PRO (Planta)" : "MANUAL (Satélite)");
        const panelManual = document.getElementById('panel-pulido-manual');
        const panelPro = document.getElementById('panel-pulido-pro');
        const panelLotes = document.getElementById('panel-pulido-lotes');
        const btnVoz = document.getElementById('btn-dictar-voz');

        // Ocultar Panel C cuando se usa el toggle legacy
        if (panelLotes) panelLotes.style.display = 'none';

        if (isPro) {
            panelManual.style.display = 'none';
            panelPro.style.display = 'block';
            if(btnVoz) btnVoz.style.display = 'none';
        } else {
            panelManual.style.display = 'block';
            panelPro.style.display = 'none';
            if(btnVoz) btnVoz.style.display = 'inline-flex';
        }
    },




    verificarTrabajoActivo: async function(idEspecifico = null) {
        const resp = document.getElementById('responsable-pulido-input')?.value || localStorage.getItem(this.getLastResponsableKey());
        if (!resp) return;

        try {
            console.log(`📡 [Pulido] Validando estado de sesión en servidor para: ${resp}...`);
            let url = `/api/pulido/session_active?responsable=${encodeURIComponent(resp)}`;
            if (idEspecifico) url += `&id_pulido=${idEspecifico}`;
            
            const res = await fetch(url);
            const data = await res.json();
            const session = data.data?.session;

            if (data.success && session) {
                // BLINDAJE hora_inicio nula: No arrancar cronómetro sin hora válida
                if (!session.hora_inicio_dt) {
                    console.log('🚫 [Pulido] Sesión activa sin hora_inicio — ignorando.');
                    return;
                }

                console.log("✅ [Pulido] Sesión activa confirmada en SQL:", session);
                this.sesionActiva = true;
                this.sessionId = session.id_pulido;
                this.startTime = new Date(session.hora_inicio_dt);
                // Convertir acumulado de segundos a ms para el timer local
                this.totalPausaMs = (session.tiempo_pausa_acumulado || 0) * 1000;
                this.enPausa = (session.estado === 'PAUSADO');

                // Poblar UI
                const rInput = document.getElementById('responsable-pulido-input');
                if (rInput) rInput.value = resp;
                const p = document.getElementById('buscador-productos');
                const o = document.getElementById('orden-produccion-pulido');
                const l = document.getElementById('lote-pulido');
                if(p) p.value = session.codigo;
                if(o) o.value = session.orden_produccion;
                if(l) l.value = session.lote;

                this.continuarUIActiva();

                // --- ACTUALIZAR BANNER Y UI ---
                const prod = session.codigo || '---';
                const lote = session.lote || '---';
                const display = document.getElementById('current-pulido-job');
                if (display) display.innerText = `${prod} | Lote: ${lote}`;

                this.renderCola(); // Refrescar cola (ahora filtrará la activa)

                // --- ACTUALIZAR IMAGEN (Blindaje contra TypeError) ---
                try {
                    if (session.codigo) {
                        this.cargarImagenProducto(session.codigo);
                    }
                } catch (imgErr) {
                    console.warn("[Pulido] No se pudo cargar la imagen del producto:", imgErr);
                }

                if (this.timerInterval) clearInterval(this.timerInterval);
                this.timerInterval = setInterval(() => this.actualizarTimer(), 1000);
            } else {
                console.log(`🧹 [Pulido] No hay trabajos activos específicos para '${resp}' en DB.`);
                if (!idEspecifico) this.limpiarGhostState(resp);
            }
        } catch (e) {
            console.error("Error recuperando sesión SQL:", e);
        }
    },

    renderCola: async function() {
        const container = document.getElementById('pulido-queue-container');
        const list = document.getElementById('pulido-queue-list');
        const responsable = document.getElementById('responsable-pulido-input')?.value;

        if (!responsable) {
            if (container) container.style.display = 'none';
            return;
        }

        try {
            const res = await fetch(`/api/pulido/tareas_pendientes?responsable=${encodeURIComponent(responsable)}`);
            const data = await res.json();

            if (data.success && data.data?.tareas.length > 0) {
                container.style.display = 'block';

                // FILTRAR: Excluir la que ya está trabajando
                const tareasFiltradas = data.data.tareas.filter(t => t.id_pulido !== this.sessionId);
                
                if (tareasFiltradas.length === 0) {
                    container.style.display = 'none';
                    return;
                }

                // Estilo compacto con scroll (Restaurado)
                list.style.maxHeight = '250px';
                list.style.overflowY = 'auto';
                list.style.paddingRight = '5px';

                list.innerHTML = tareasFiltradas.map(t => {
                    const isPausada = t.estado === 'PAUSADO_COLA';
                    return `
                        <div class="card mb-2 border-start border-4 ${isPausada ? 'border-warning shadow-sm' : 'border-secondary'}" 
                             style="background: #f8f9fa;">
                            <div class="card-body p-2 d-flex justify-content-between align-items-center">
                                <div style="flex: 1;">
                                    <span class="fw-bold d-block text-dark" style="font-size: 0.8rem;">${t.codigo}</span>
                                    <small class="text-muted" style="font-size: 0.65rem;">
                                        OP: ${t.orden_produccion || 'N/A'} | ${isPausada ? '<b class="text-warning">PAUSADA</b>' : 'PENDIENTE'}
                                    </small>
                                </div>
                                <button class="btn btn-sm ${isPausada ? 'btn-warning' : 'btn-outline-primary'} py-1 px-2" 
                                        style="font-size: 0.7rem;"
                                        onclick="ModuloPulido.seleccionarTareaRecuperada('${t.id_pulido}')">
                                    <i class="fas ${isPausada ? 'fa-play' : 'fa-hand-pointer'}"></i> Retomar
                                </button>
                            </div>
                        </div>
                    `;
                }).join('');
            } else {
                if (container) container.style.display = 'none';
            }
        } catch (e) {
            console.error("[Pulido] Error al cargar cola:", e);
        }
    },

    seleccionarTareaRecuperada: async function(idPulido) {
        const responsable = document.getElementById('responsable-pulido-input')?.value;
        
        // EXTRACCIÓN FORZADA: Garantizar que enviamos solo el String del ID
        let idReal = idPulido;
        if (typeof idPulido === 'object' && idPulido !== null) {
            idReal = idPulido.id_pulido;
        }

        if (!idReal || idReal === "[object Object]") {
            console.error("🚫 [Pulido] ID inválido detectado en Swap:", idPulido);
            return;
        }

        mostrarLoading(true, 'Cambiando de tarea...');
        try {
            // Ejecutar el SWAP (Pausa automática de lo actual y activación de lo nuevo)
            const res = await fetch('/api/pulido/swap_task', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ responsable, id_pulido: idReal })
            });
            const data = await res.json();

            if (data.success) {
                // Rehidratar UI con la nueva tarea activa de forma instantánea
                // Pasamos el ID real como String para evitar [object Object]
                this.verificarTrabajoActivo(idReal);
            } else {
                Swal.fire('No se pudo cambiar de tarea', data.error || 'El servidor rechazó el cambio.', 'error');
            }
        } catch (e) {
            console.error("[Pulido] Error en el intercambio de tareas:", e);
            Swal.fire('Error de conexión', 'No se pudo comunicar con el servidor para cambiar de tarea.', 'error');
        } finally {
            mostrarLoading(false);
        }
    },

    cargarImagenProducto: function(idCodigo) {
        const imgElement = document.getElementById('pulido-product-image');
        const container = document.getElementById('pulido-product-image-container');
        if (!imgElement || !container) return;

        try {
            // Normalizar código para la ruta de la imagen
            const codigoLimpio = idCodigo.split(' ')[0].replace(/\//g, '-');
            const imgPath = `/static/img/productos/imagenes/${codigoLimpio}.jpg`;
            
            imgElement.src = imgPath;
            imgElement.onerror = () => {
                imgElement.src = '/static/img/no-image.svg';
                console.log(`[Pulido] Imagen no encontrada para: ${idCodigo}`);
            };
            container.style.display = 'block';
        } catch (e) {
            console.error("[Pulido] Error al gestionar imagen:", e);
            container.style.display = 'none';
        }
    },

    limpiarGhostState: function(operario) {
        const key = this.getStateKey();
        if (localStorage.getItem(key)) {
            console.warn(`[Pulido] Eliminando Ghost State detectado para: ${operario}`);
            localStorage.removeItem(key);
            // Si la UI estaba activa por un error de flujo previo, resetearla
            if (this.sesionActiva) {
                this.limpiarSesionLocal();
            }
        }
    },

    continuarUIActiva: function() {
        document.getElementById('pulido-idle-msg').style.display = 'none';
        document.getElementById('pulido-active-msg').style.display = 'block';
        document.getElementById('pulido-session-id-display').innerText = this.sessionId;
        
        ['fecha-pulido', 'responsable-pulido-input', 'buscador-productos', 'orden-produccion-pulido', 'lote-pulido'].forEach(id => {
            const el = document.getElementById(id);
            if(el) el.disabled = true;
        });

        document.getElementById('btn-iniciar-pulido').disabled = true;
        document.getElementById('btn-pausar-pulido').disabled = false;
        document.getElementById('btn-terminar-pulido').disabled = false;
        document.getElementById('btn-cambiar-ref-pulido').style.display = 'block';

        // Sincronizar botón de pausa según estado actual
        const btnPausa = document.getElementById('btn-pausar-pulido');
        if (this.enPausa) {
            btnPausa.innerHTML = '<i class="fas fa-play me-2"></i> Reanudar';
            btnPausa.className = 'btn btn-info btn-lg p-3 shadow';
            document.getElementById('pulido-pausa-msg').style.display = 'block';
        } else {
            btnPausa.innerHTML = '<i class="fas fa-pause me-2"></i> Pausar';
            btnPausa.className = 'btn btn-warning btn-lg p-3 shadow';
            document.getElementById('pulido-pausa-msg').style.display = 'none';
        }

        if (this.timerInterval) clearInterval(this.timerInterval);
        this.timerInterval = setInterval(() => this.actualizarTimer(), 1000);
        this.guardarEstadoLocal();
        this.mostrarFotoProducto();
    },

    // ==========================================
    // LÓGICA MODO PRO (PLANTA)
    // ==========================================
    
    iniciarCiclo: function () {
        const respInput = document.getElementById('responsable-pulido-input');
        const prodInput = document.getElementById('buscador-productos');
        const loteInput = document.getElementById('lote-pulido');
        
        const resp = respInput?.value?.trim();
        const prodRaw = prodInput?.value?.trim();
        const prod = this.normalizarCodigo(prodRaw);
        const lote = loteInput?.value?.trim();
        
        if (!resp || !prod || !lote) {
            Swal.fire({
                title: 'Campos Incompletos',
                text: 'Por favor, selecciona Responsable, Referencia y Lote antes de iniciar el cronómetro de planta.',
                icon: 'warning',
                confirmButtonColor: '#3b82f6'
            });
            return;
        }

        // Bloquear campos compartidos
        ['fecha-pulido', 'responsable-pulido-input', 'buscador-productos', 'orden-produccion-pulido', 'lote-pulido'].forEach(id => {
            const el = document.getElementById(id);
            if(el) el.disabled = true;
        });

        this.sesionActiva = true;
        this.enPausa = false;
        
        // Si no hay startTime, es una sesión nueva
        if (!this.startTime) {
            this.startTime = new Date();
            this.totalPausaMs = 0;
            this.tiempoAcumuladoMs = 0;
        }

        // Mostrar Foto (NUEVO)
        this.mostrarFotoProducto();
        
        if (!this.sessionId) this.sessionId = 'PUL-' + Math.random().toString(36).substr(2, 9).toUpperCase();
        
        document.getElementById('pulido-idle-msg').style.display = 'none';
        document.getElementById('pulido-active-msg').style.display = 'block';
        document.getElementById('current-pulido-job').innerText = `${prod} | Lote: ${lote}`;
        document.getElementById('pulido-session-id-display').innerText = this.sessionId;
        
        document.getElementById('btn-iniciar-pulido').disabled = true;
        document.getElementById('btn-pausar-pulido').disabled = false;
        document.getElementById('btn-terminar-pulido').disabled = false;
        
        const btnUrgencia = document.getElementById('btn-cambiar-ref-pulido');
        if (btnUrgencia) btnUrgencia.style.display = 'block';

        if (this.timerInterval) clearInterval(this.timerInterval);
        this.timerInterval = setInterval(() => this.actualizarTimer(), 1000);
        
        // PERSISTENCIA INMEDIATA EN SQL
        this.persistirInicioSQL(resp, prod, lote, document.getElementById('orden-produccion-pulido')?.value);
        
        this.guardarEstadoLocal();
    },

    persistirInicioSQL: async function(resp, prod, lote, op) {
        const horaInicioStr = this.startTime.toLocaleTimeString('es-CO', { timeZone: 'America/Bogota', hour12: false, hour: '2-digit', minute: '2-digit' });
        const fechaInicioStr = this.startTime.toLocaleDateString('sv-SE', { timeZone: 'America/Bogota' });

        const data = {
            id_pulido: this.sessionId,
            responsable: resp,
            codigo_producto: prod,
            lote: lote,
            orden_produccion: op || 'SIN OP',
            estado: 'TRABAJANDO',
            hora_inicio: horaInicioStr,
            fecha_inicio: fechaInicioStr
        };

        try {
            await fetch('/api/pulido', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            console.log("✅ [Pulido] Inicio persistido en SQL inmediatamente.");
        } catch (e) {
            console.error("Error persistencia inmediata:", e);
        }
    },

    actualizarTimer: function () {
        if (this.enPausa) return;

        // BLINDAJE: Si startTime es nulo/inválido, mostrar 00:00:00 estático
        if (!this.startTime || isNaN(this.startTime.getTime())) {
            document.getElementById('pulido-main-timer').innerText = '00:00:00';
            return;
        }
        
        const now = new Date();
        const diffMs = (now - this.startTime - (this.totalPausaMs || 0)) + (this.tiempoAcumuladoMs || 0);
        
        // Protección contra valores negativos (por drift de reloj)
        const safeDiff = Math.max(0, diffMs);
        const hrs = String(Math.floor(safeDiff / 3600000)).padStart(2, '0');
        const mins = String(Math.floor((safeDiff % 3600000) / 60000)).padStart(2, '0');
        const secs = String(Math.floor((safeDiff % 60000) / 1000)).padStart(2, '0');
        
        document.getElementById('pulido-main-timer').innerText = `${hrs}:${mins}:${secs}`;
    },

    pausarCiclo: async function () {
        const btn = document.getElementById('btn-pausar-pulido');
        const horaPausa = new Date().toLocaleTimeString('es-CO', {
            timeZone: 'America/Bogota',
            hour12: false,
            hour: '2-digit',
            minute: '2-digit'
        });
        const estabaEnPausa = this.enPausa;

        mostrarLoading(true, estabaEnPausa ? 'Reanudando...' : 'Pausando...');
        try {
            if (!this.enPausa) {
                console.log(`⏸️ [Pulido] Pausando a las ${horaPausa}...`);

                // 2. Ejecutar la pausa en el servidor
                const res = await fetch('/api/pulido/pausar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id_pulido: this.sessionId,
                        hora_pausa: horaPausa
                    })
                });
                if (res.ok) {
                    this.enPausa = true;
                    btn.innerHTML = '<i class="fas fa-play me-2"></i> Reanudar';
                    btn.className = 'btn btn-info btn-lg p-3 shadow';
                    document.getElementById('pulido-pausa-msg').style.display = 'block';
                } else {
                    throw new Error(`El servidor respondió ${res.status}`);
                }
            } else {
                console.log(`▶️ [Pulido] Reanudando a las ${horaPausa}...`);
                const res = await fetch('/api/pulido/reanudar', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id_pulido: this.sessionId,
                        hora_reanudar: horaPausa
                    })
                });
                const data = await res.json();
                if (data.success) {
                    this.enPausa = false;
                    this.totalPausaMs = (data.data?.acumulado || 0) * 1000;
                    btn.innerHTML = '<i class="fas fa-pause me-2"></i> Pausar';
                    btn.className = 'btn btn-warning btn-lg p-3 shadow';
                    document.getElementById('pulido-pausa-msg').style.display = 'none';
                } else {
                    throw new Error(data.error || `El servidor respondió ${res.status}`);
                }
            }
            this.guardarEstadoLocal();
        } catch (error) {
            console.error('❌ [Pulido] Error en pausarCiclo:', error);
            Swal.fire({
                icon: 'error',
                title: estabaEnPausa ? 'No se pudo reanudar' : 'No se pudo pausar',
                text: 'No se pudo comunicar con el servidor. El botón sigue mostrando el estado anterior porque el cambio no quedó confirmado -- verifica tu conexión e intenta de nuevo.'
            });
        } finally {
            mostrarLoading(false);
        }
    },

    /**
     * Muestra el modal obligatorio de PNC para Pulido.
     * Retorna un objeto con los defectos { criterio: cantidad } o null si canceló.
     */
    _mostrarModalPncPulido: async function(titulo) {
        return {};
    },

    habilitarCambioReferencia: function() {
        Swal.fire({
            title: 'Multitarea / Urgencia',
            text: '¿Qué deseas hacer con el trabajo actual?',
            icon: 'question',
            showCancelButton: true,
            showDenyButton: true,
            confirmButtonColor: '#10b981',
            denyButtonColor: '#3b82f6',
            cancelButtonColor: '#6b7280',
            confirmButtonText: 'Reportar y Terminar',
            denyButtonText: 'Pausar y Enviar a Cola',
            cancelButtonText: 'Cancelar'
        }).then((result) => {
            if (result.isConfirmed) {
                this.prepararReporteFinal();
            } else if (result.isDenied) {
                this.enviarACola();
            }
        });
    },

    enviarACola: async function() {
        if (this.timerInterval) clearInterval(this.timerInterval);
        
        const now = new Date();
        const segmentTime = now - this.startTime - this.totalPausaMs;
        const totalElapsedMs = this.tiempoAcumuladoMs + segmentTime;

        const prodRaw = document.getElementById('buscador-productos').value;
        const prod = this.normalizarCodigo(prodRaw);
        const op = document.getElementById('orden-produccion-pulido').value;
        const lote = document.getElementById('lote-pulido').value;
        const resp = document.getElementById('responsable-pulido-input').value;

        const sessionToPause = {
            sessionId: this.sessionId,
            prod,
            op,
            lote,
            resp,
            tiempoAcumuladoMs: totalElapsedMs
        };

        // Guardado preventivo en DB
        const horaInicioStr = this.startTime.toLocaleTimeString('es-CO', { timeZone: 'America/Bogota', hour12: false, hour: '2-digit', minute: '2-digit' });
        const dataPreventiva = {
            id_pulido: this.sessionId,
            codigo_producto: prod,
            responsable: resp,
            orden_produccion: op,
            lote: lote,
            estado: 'PAUSADO_COLA',
            cantidad_real: 0,
            hora_inicio: horaInicioStr
        };

        mostrarLoading(true, 'Enviando trabajo a la cola...');
        await this.enviarAServidor(dataPreventiva);
        mostrarLoading(false);

        this.sesionesEnPausa.push(sessionToPause);

        // Reset local para nueva urgencia
        this.limpiarSesionLocal();
        this.renderCola();
        this.guardarEstadoLocal();

        Swal.fire({
            title: 'Trabajo en Cola',
            text: `El trabajo ${prod} se ha pausado. Ahora puedes iniciar la urgencia.`,
            icon: 'info',
            timer: 3000,
            toast: true,
            position: 'top-end',
            showConfirmButton: false
        });
    },

    renderCola: async function() {
        const container = document.getElementById('pulido-queue-container');
        const list = document.getElementById('pulido-queue-list');
        const responsable = document.getElementById('responsable-pulido-input')?.value;

        if (!responsable) {
            if (container) container.style.display = 'none';
            return;
        }

        try {
            const res = await fetch(`/api/pulido/tareas_pendientes?responsable=${encodeURIComponent(responsable)}`);
            const data = await res.json();

            if (data.success && data.data?.tareas.length > 0) {
                container.style.display = 'block';
                list.innerHTML = data.data.tareas.map(t => {
                    const isPausada = t.estado === 'PAUSADO_COLA';
                    return `
                        <div class="card mb-2 border-start border-4 ${isPausada ? 'border-warning shadow-sm' : 'border-secondary'}">
                            <div class="card-body p-2 d-flex justify-content-between align-items-center">
                                <div>
                                    <span class="fw-bold d-block" style="font-size: 0.85rem;">${t.codigo}</span>
                                    <small class="text-muted" style="font-size: 0.7rem;">
                                        OP: ${t.orden_produccion || 'N/A'} | ${isPausada ? '<b class="text-warning">PAUSADA</b>' : 'PENDIENTE'}
                                    </small>
                                </div>
                                <button class="btn btn-sm ${isPausada ? 'btn-warning' : 'btn-outline-primary'}" 
                                        onclick="ModuloPulido.seleccionarTareaRecuperada(${JSON.stringify(t).replace(/"/g, '&quot;')})">
                                    <i class="fas ${isPausada ? 'fa-play' : 'fa-hand-pointer'}"></i> Retomar
                                </button>
                            </div>
                        </div>
                    `;
                }).join('');
            } else {
                if (container) container.style.display = 'none';
            }
        } catch (e) {
            console.error("[Pulido] Error al cargar cola:", e);
        }
    },

    // Función eliminada (unificada arriba)

    retomarSesion: async function(index) {
        const s = this.sesionesEnPausa[index];
        const responsable = document.getElementById('responsable-pulido-input')?.value;

        mostrarLoading(true, 'Retomando trabajo...');
        try {
            // Ejecutar el SWAP (Pausa automática de lo actual y activación de lo nuevo)
            const res = await fetch('/api/pulido/swap_task', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ responsable, id_pulido: s.sessionId })
            });
            const data = await res.json();

            if (data.success) {
                // Quitar de la cola local y rehidratar UI
                this.sesionesEnPausa.splice(index, 1);
                this.verificarTrabajoActivo(s.sessionId);
            } else {
                Swal.fire('No se pudo retomar', data.error || 'El servidor rechazó el cambio.', 'error');
            }
        } catch (e) {
            console.error("[Pulido] Error en el intercambio de tareas:", e);
            Swal.fire('Error de conexión', 'No se pudo comunicar con el servidor para retomar el trabajo.', 'error');
        } finally {
            mostrarLoading(false);
        }
    },

    limpiarSesionLocal: function() {
        clearInterval(this.timerInterval);
        this.sesionActiva = false;
        this.sessionId = null;
        this.startTime = null;
        this.totalPausaMs = 0;
        this.tiempoAcumuladoMs = 0;
        this.enPausa = false;

        document.getElementById('pulido-idle-msg').style.display = 'block';
        document.getElementById('pulido-active-msg').style.display = 'none';
        document.getElementById('pulido-main-timer').innerText = '00:00:00';
        document.getElementById('btn-iniciar-pulido').disabled = true;
        
        // Limpiar campos para nueva entrada
        document.getElementById('buscador-productos').value = '';
        document.getElementById('orden-produccion-pulido').value = '';
        
        // Desbloquear campos (incluye fecha-pulido, omitida antes — quedaba
        // deshabilitada permanentemente tras enviarACola() o al descartar un
        // reporte fallido, ya que iniciarCiclo()/continuarUIActiva() sí la bloquean)
        ['fecha-pulido', 'responsable-pulido-input', 'buscador-productos', 'orden-produccion-pulido', 'lote-pulido'].forEach(id => {
            const el = document.getElementById(id);
            if(el) el.disabled = false;
        });
    },



    prepararReporteFinal: function () {
        // DETENER CRONÓMETRO INMEDIATAMENTE PARA EXACTITUD (Bug Fix)
        if (this.sesionActiva && !this.enPausa) {
            console.log("⏱️ [Pulido] Deteniendo cronómetro para reporte final...");
            this.pausarCiclo();
        }

        const now = new Date();
        
        // El tiempo total es la suma de lo acumulado (sesiones previas) + el segmento actual.
        // El descuento por pausas programadas (Desayuno/Almuerzo) lo calcula y aplica
        // exclusivamente el backend (PulidoService) a partir de hora_inicio/hora_fin crudas.
        const msSegmentoActual = this.startTime ? (now - this.startTime - this.totalPausaMs) : 0;
        const msTotales = this.tiempoAcumuladoMs + msSegmentoActual;

        const totalMin = Math.floor(msTotales / 60000);
        const totalSec = Math.floor(msTotales / 1000);

        // Validación flexible: 30 segundos para urgencias/retomados, 1 min para nuevos
        const umbralSegundos = (this.tiempoAcumuladoMs > 0) ? 10 : 30; 

        if (totalSec < umbralSegundos) {
            Swal.fire({
                title: 'Tiempo Insuficiente',
                text: `La sesión debe durar al menos ${umbralSegundos} segundos. Tiempo actual: ${totalSec}s`,
                icon: 'error',
                confirmButtonColor: '#d33'
            });
            return;
        }

        document.getElementById('modal-tiempo-total').innerText = totalMin + ' min ' + (totalSec % 60) + 's';
        // El tiempo efectivo (descontadas las pausas programadas) lo calcula el
        // backend al persistir el reporte — aquí solo se muestra el tiempo bruto.
        document.getElementById('modal-tiempo-efectivo').innerText = totalMin + ' min ' + (totalSec % 60) + 's';

        // Reset inputs modal
        document.getElementById('cantidad-recibida-pro').value = 0;
        document.getElementById('resultado-buenas-pro').innerText = '0';
        
        document.getElementById('modal-reporte-final').style.display = 'flex';
    },

    // ==========================================
    // GESTIÓN DE PNC DINÁMICO
    // ==========================================

    agregarFilaPnc: function() {
        // Validar que la última fila tenga datos antes de añadir otra (opcional, pero ayuda a la limpieza)
        if (this.pncRows.length > 0) {
            const lastRow = this.pncRows[this.pncRows.length - 1];
            if (!lastRow.cantidad || lastRow.cantidad <= 0 || !lastRow.criterio) {
                Swal.fire({
                    title: 'Fila incompleta',
                    text: 'Por favor complete la información de la fila de PNC actual antes de añadir una nueva.',
                    icon: 'warning',
                    toast: true,
                    position: 'top-end',
                    timer: 3000,
                    showConfirmButton: false
                });
                return;
            }
        }

        this.pncRows.push({
            proceso: 'PULIDO',
            cantidad: 0,
            criterio: ''
        });
        this.renderPncRows();
    },

    eliminarFilaPnc: function(index) {
        this.pncRows.splice(index, 1);
        this.renderPncRows();
        this.actualizarCalculoPro();
    },

    renderPncRows: function() {
        const container = document.getElementById('pnc-dynamic-container');
        if (!container) return;

        if (this.pncRows.length === 0) {
            container.innerHTML = '<div class="text-center text-muted py-2 small" id="pnc-empty-msg">No hay PNC reportado</div>';
            return;
        }

        container.innerHTML = this.pncRows.map((row, index) => `
            <div class="d-flex gap-1 align-items-center p-2 border-bottom bg-white rounded shadow-sm animate__animated animate__fadeInIn">
                <select class="form-select form-select-sm" style="width: 30%;" onchange="ModuloPulido.updateRow(${index}, 'proceso', this.value)">
                    <option value="PULIDO" ${row.proceso === 'PULIDO' ? 'selected' : ''}>Pulido</option>
                    <option value="INYECCION" ${row.proceso === 'INYECCION' ? 'selected' : ''}>Inyección</option>
                </select>
                <input type="number" class="form-control form-control-sm text-center fw-bold" style="width: 20%;" 
                    value="${row.cantidad}" min="1" placeholder="Cant"
                    oninput="ModuloPulido.updateRow(${index}, 'cantidad', this.value)">
                <select class="form-select form-select-sm" style="width: 40%;" onchange="ModuloPulido.updateRow(${index}, 'criterio', this.value)">
                    <option value="">- Motivo -</option>
                    ${(this.catalogosPnc[row.proceso] || []).map(c => `
                        <option value="${c}" ${row.criterio === c ? 'selected' : ''}>${c}</option>
                    `).join('')}
                </select>
                <button type="button" class="btn btn-sm btn-link text-danger p-0" style="width: 10%;" onclick="ModuloPulido.eliminarFilaPnc(${index})">
                    <i class="fas fa-times-circle"></i>
                </button>
            </div>
        `).join('');
    },

    updateRow: function(index, field, value) {
        if (field === 'cantidad') {
            const val = parseInt(value, 10);
            if (val < 0) {
                Swal.fire('Cantidad inválida', 'No se permiten cantidades negativas en PNC', 'error');
                this.pncRows[index].cantidad = 0;
            } else {
                this.pncRows[index].cantidad = val || 0;
            }
        } else {
            this.pncRows[index][field] = value;
        }
        
        // Si cambió el proceso, resetear el criterio para que coincida con el nuevo catálogo
        if (field === 'proceso') {
            this.pncRows[index].criterio = '';
            this.renderPncRows();
        }
        
        this.actualizarCalculoPro();
    },

    // ==========================================
    // CÁLCULOS EN TIEMPO REAL (REQUISITO)
    // ==========================================
    
    actualizarCalculoManual: function() {
        const brutoInput = document.getElementById('cantidad-recibida-pulido');
        const pncInyInput = document.getElementById('manual-pnc-iny');
        const pncPulInput = document.getElementById('manual-pnc-pul');
        const display = document.getElementById('manual-bujes-buenos');
        
        if (!brutoInput) return;
        
        const recibida = parseInt(brutoInput.value, 10) || 0;
        const pncIny = parseInt(pncInyInput?.value, 10) || 0;
        const pncPul = parseInt(pncPulInput?.value, 10) || 0;
        
        const buenas = Math.max(0, recibida - pncIny - pncPul);
        
        console.log(`[Pulido Manual] Bruto: ${recibida}, PNC_Iny: ${pncIny}, PNC_Pul: ${pncPul} -> Total Buenos: ${buenas}`);
        
        if (display) display.innerText = buenas;
    },

    actualizarCalculoPro: function() {
        const display = document.getElementById('resultado-buenas-pro');
        const buenosInput = document.getElementById('cantidad-recibida-pro');
        if (!buenosInput) return;
        
        const buenos = parseFloat(buenosInput.value) || 0;
        
        // Sincronizar pncRows desde el DOM y calcular total
        let totalPnc = 0;
        this.pncRows.forEach(row => {
            const input = document.getElementById(`pnc-cant-${row.id}`);
            if (input) {
                row.cantidad = parseFloat(input.value) || 0;
                totalPnc += row.cantidad;
            }
        });

        // Sincronizar revueltosRows desde el DOM
        let totalRevueltos = 0;
        this.revueltosRows.forEach(row => {
            const input = document.getElementById(`rev-cant-${row.id}`);
            if (input) {
                row.cantidad = parseFloat(input.value) || 0;
                totalRevueltos += row.cantidad;
            }
        });
        
        const totalBruto = buenos + totalPnc + totalRevueltos;
        
        if (display) display.innerText = totalBruto;
    },

    // ==========================================
    // GUARDADO DE DATOS
    // ==========================================

    guardarReportePro: async function () {
        // Guard clause: sin sessionId activo no hay lote/sesión que reportar
        if (!this.sessionId) {
            console.warn('[Pulido] guardarReportePro llamado sin sessionId activo — abortando.');
            Swal.fire({ title: 'Sin Sesión Activa', text: 'No hay un ciclo de pulido activo para reportar. Inicia una sesión primero.', icon: 'warning' });
            return;
        }

        // Agrupar PNC por proceso para compatibilidad con DB
        const pncData = this.pncRows.map(row => ({
            proceso: document.getElementById(`pnc-proc-${row.id}`)?.value,
            cantidad: parseFloat(document.getElementById(`pnc-cant-${row.id}`)?.value || 0),
            criterio: document.getElementById(`pnc-crit-${row.id}`)?.value
        })).filter(p => p.cantidad > 0);

        // Bujes Revueltos (NUEVO)
        const revueltosData = this.revueltosRows.map(row => ({
            id_codigo: document.getElementById(`rev-cod-${row.id}`)?.value,
            cantidad: parseFloat(document.getElementById(`rev-cant-${row.id}`)?.value || 0)
        })).filter(r => r.cantidad > 0 && r.id_codigo);

        const data = {
            id_pulido: this.sessionId,
            fecha_inicio: document.getElementById('fecha-pulido')?.value || new Date().toISOString().split('T')[0],
            hora_inicio: this.startTime ? (this.startTime.getHours() + ':' + String(this.startTime.getMinutes()).padStart(2, '0')) : '00:00',
            hora_fin: new Date().getHours() + ':' + String(new Date().getMinutes()).padStart(2, '0'),
            responsable: document.getElementById('responsable-pulido-input')?.value || '',
            codigo_producto: this.normalizarCodigo(document.getElementById('buscador-productos')?.value || ''),
            
            // NUEVA LÓGICA: cantidad_real son las buenas, cantidad_recibida es el total (bruto reportado)
            cantidad_real: parseFloat(document.getElementById('cantidad-recibida-pro')?.value || 0),
            cantidad_recibida: parseFloat(document.getElementById('resultado-buenas-pro')?.innerText || 0),
            
            pnc_inyeccion: pncData.filter(p => p.proceso === 'INYECCION').reduce((a, b) => a + b.cantidad, 0),
            pnc_pulido: pncData.filter(p => p.proceso === 'PULIDO').reduce((a, b) => a + b.cantidad, 0),
            criterio_pnc_inyeccion: pncData.filter(p => p.proceso === 'INYECCION').map(p => `${p.criterio} (${p.cantidad})`).join(', '),
            criterio_pnc_pulido: pncData.filter(p => p.proceso === 'PULIDO').map(p => `${p.criterio} (${p.cantidad})`).join(', '),
            
            observaciones: (document.getElementById('observaciones-pro')?.value || ''),
            
            orden_produccion: document.getElementById('orden-produccion-pulido')?.value || '',
            lote: document.getElementById('lote-pulido')?.value || '',
            departamento: 'PULIDO',
            almacen_destino: 'P. TERMINADO',
            modo: 'PRO',
            tiempo_acumulado_ms: this.tiempoAcumuladoMs,
            pnc_detail: pncData,
            revueltos: revueltosData
        };

        // Validación de filas completas
        if (pncData.some(r => !r.cantidad || r.cantidad <= 0 || !r.criterio)) {
            Swal.fire('PNC Incompleto', 'Todas las filas de PNC deben tener cantidad y motivo seleccionado.', 'warning');
            return;
        }

        // Validación de consistencia
        const totalPnc = pncData.reduce((s, r) => s + r.cantidad, 0);
        const totalRevueltos = revueltosData.reduce((s, r) => s + r.cantidad, 0);
        const totalCalculado = data.cantidad_real + totalPnc + totalRevueltos;
        
        if (totalCalculado !== data.cantidad_recibida) {
            Swal.fire('Error de Consistencia', 'La suma de piezas buenas, PNC y revueltos no coincide con el total (' + totalCalculado + ' vs ' + data.cantidad_recibida + ').', 'error');
            return;
        }

        if (!data.responsable || !data.codigo_producto || data.cantidad_real < 0) {
            Swal.fire('Atención', 'Faltan campos obligatorios o hay valores negativos', 'warning');
            return;
        }

        await this.enviarAServidor(data);
    },

    registrarPulidoTradicional: async function () {
        console.log('🔘 Botón presionado, iniciando envío (Modo Satélite)...');
        try {
            const form = document.getElementById('form-pulido');
            console.log('Formulario #form-pulido:', form ? 'Detectado' : 'No encontrado (NULL/UNDEFINED)');
            
            const horaInicio = document.getElementById('hora-inicio-pulido')?.value || '00:00';
            const horaFin = document.getElementById('hora-fin-pulido')?.value || '00:00';

            if (horaFin <= horaInicio) {
                Swal.fire({
                    title: 'Error de Tiempos',
                    text: 'La Hora de Fin debe ser estrictamente posterior a la Hora de Inicio.',
                    icon: 'error',
                    confirmButtonColor: '#d33'
                });
                return;
            }

            // Blindaje AM/PM: en celulares con formato 12h, el picker nativo
            // de hora puede precargar AM/PM según la hora del día en que se
            // ABRE el campo (no según el turno real que se está reportando).
            // Esto produce horas fuera de la jornada de planta o duraciones
            // absurdas. Se avisa (sin bloquear del todo) para que la
            // operaria confirme o corrija antes de guardar.
            //
            // Ventana válida (confirmada con planta): jornada 07:00-17:00, y
            // las extras llegan como máximo de 06:00 a 18:00. Por eso 06:00 y
            // 18:00 EXACTOS son válidos y no deben disparar el aviso.
            const MIN_JORNADA = 6 * 60;    // 06:00
            const MAX_JORNADA = 18 * 60;   // 18:00
            const [hiH, hiM] = horaInicio.split(':').map(Number);
            const [hfH, hfM] = horaFin.split(':').map(Number);
            const iniMin = hiH * 60 + hiM;
            const finMin = hfH * 60 + hfM;
            const duracionMin = finMin - iniMin;
            const avisosHorario = [];

            if (iniMin < MIN_JORNADA || iniMin > MAX_JORNADA) {
                avisosHorario.push(`La Hora de Inicio (${horaInicio}) está fuera de la jornada de planta (máximo 6:00 a.m. - 6:00 p.m. contando extras).`);
            }
            if (finMin < MIN_JORNADA || finMin > MAX_JORNADA) {
                avisosHorario.push(`La Hora de Fin (${horaFin}) está fuera de la jornada de planta (máximo 6:00 a.m. - 6:00 p.m. contando extras).`);
            }
            if (duracionMin > 480) {
                avisosHorario.push(`El turno dura ${Math.floor(duracionMin / 60)}h ${duracionMin % 60}min, más de las 8 horas de una jornada normal.`);
            }

            if (avisosHorario.length > 0) {
                const listaAvisos = avisosHorario.map(a => `<li style="text-align:left; margin-bottom:6px;">${a}</li>`).join('');
                const confirmacionHorario = await Swal.fire({
                    title: '⚠️ Verifica la Hora',
                    html: `
                        <ul style="padding-left:20px; margin-bottom:10px;">${listaAvisos}</ul>
                        <p style="text-align:left; font-size:0.85em; color:#666;">
                            Si tu celular marcó AM/PM automáticamente al abrir el campo de hora, revísalo antes de continuar.
                        </p>
                    `,
                    icon: 'warning',
                    showCancelButton: true,
                    confirmButtonText: 'Los datos son correctos, continuar',
                    cancelButtonText: 'Corregir hora',
                    confirmButtonColor: '#f59e0b'
                });
                if (!confirmacionHorario.isConfirmed) {
                    return;
                }
            }

            // Validar existencia de inputs críticos
            const responsableInput = document.getElementById('responsable-pulido-input');
            const buscadorProd = document.getElementById('buscador-productos');
            const cantRecibidaInput = document.getElementById('cantidad-recibida-pulido');
            const manualBuenosSpan = document.getElementById('manual-bujes-buenos');
            
            console.log('Referencias de inputs cargadas:', {
                responsableInput: responsableInput ? 'OK' : 'NULL',
                buscadorProd: buscadorProd ? 'OK' : 'NULL',
                cantRecibidaInput: cantRecibidaInput ? 'OK' : 'NULL',
                manualBuenosSpan: manualBuenosSpan ? 'OK' : 'NULL'
            });

            const data = {
                fecha_inicio: document.getElementById('fecha-pulido')?.value || new Date().toISOString().split('T')[0],
                responsable: responsableInput?.value || '',
                hora_inicio: horaInicio,
                hora_fin: horaFin,
                codigo_producto: this.normalizarCodigo(buscadorProd?.value || ''),
                orden_produccion: document.getElementById('orden-produccion-pulido')?.value || '',
                lote: document.getElementById('lote-pulido')?.value || '',
                departamento: 'PULIDO',
                almacen_destino: 'P. TERMINADO',
                cantidad_recibida: parseInt(cantRecibidaInput?.value, 10) || 0,
                pnc_inyeccion: 0,
                pnc_pulido: 0,
                criterio_pnc_inyeccion: '',
                criterio_pnc_pulido: '',
                cantidad_real: parseInt(manualBuenosSpan?.innerText, 10) || 0,
                observaciones: document.getElementById('observaciones-pulido')?.value || '',
                modo: 'MANUAL'
            };

            console.log('📦 Payload generado para Modo Satélite:', JSON.stringify(data, null, 2));

            if (!data.responsable || !data.codigo_producto || !data.cantidad_recibida || data.cantidad_recibida <= 0) {
                Swal.fire('Atención', 'Faltan campos obligatorios (Responsable, Referencia o Cantidad mayor a 0)', 'warning');
                return;
            }

            await this.enviarAServidor(data);
        } catch (error) {
            console.error('❌ Error capturado en registrarPulidoTradicional:', error);
            Swal.fire('Error de Ejecución', 'Se produjo un error al procesar el envío: ' + error.message, 'error');
        }
    },

    // ──────────────────────────────────────────────────────────────────
    // BANNER: Último registro guardado (Modo Satélite)
    // Responsabilidad única: fetch → pintar. Cero lógica de negocio en DOM.
    // ──────────────────────────────────────────────────────────────────
    actualizarBannerUltimoRegistro: async function () {
        const responsable = this.getOperarioActual();
        const banner = document.getElementById('banner-ultimo-registro');
        if (!banner) return; // El panel Satélite no está en el DOM actual

        if (!responsable) {
            banner.style.display = 'none';
            return;
        }

        try {
            const res = await fetch(`/api/pulido/ultimo_registro?responsable=${encodeURIComponent(responsable)}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            if (!data.success || !data.data?.registro) {
                // Sin registros previos: ocultar banner silenciosamente
                banner.style.display = 'none';
                return;
            }

            // Pintar — con programación defensiva
            const registro = data.data.registro || {};
            const codigo_producto = registro.codigo_producto || '—';
            const cantidad = registro.cantidad !== undefined && registro.cantidad !== null ? registro.cantidad : (registro.piezas || registro.cantidad_aprobada || 0);
            const fecha_hora = registro.fecha_hora || '—';

            document.getElementById('banner-ur-codigo').textContent = codigo_producto;
            document.getElementById('banner-ur-cantidad').textContent = cantidad;
            document.getElementById('banner-ur-fecha').textContent = fecha_hora;
            banner.style.display = 'flex';
        } catch (e) {
            console.warn('[Pulido] Banner último registro: no se pudo cargar.', e);
            if (banner) banner.style.display = 'none';
        }
    },

    enviarAServidor: async function (data) {
        // Identificador local estable: permite ubicar y remover ESTE reporte
        // específico de la cola de fallidos sin afectar a los demás.
        if (!data._localId) {
            data._localId = 'LOCAL-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6);
        }

        try {
            if (!navigator.onLine) {
                throw new Error("No hay conexión a internet (Modo Offline)");
            }

            Swal.showLoading();
            // Logger del payload exacto justo antes del post
            console.log('📡 Enviando POST a /api/pulido con payload:', JSON.stringify(data, null, 2));
            // No enviar el marcador interno de la cola local al backend
            const { _localId, ...payload } = data;
            // Usar el apiClient robusto que ya implementa 3 reintentos
            const result = await window.apiClient.post('/pulido', payload);

            if (result.success) {
                Swal.fire('¡Éxito!', 'Producción guardada correctamente.', 'success');
                this._removerReporteFallido(_localId); // Limpiar SOLO este reporte de la cola

                this.terminarCiclo();
                this.limpiarSesionLocal();

                const modal = document.getElementById('modal-reporte-final');
                if (modal) modal.style.display = 'none';
                this.limpiarFormulario();

                // Actualizar banner con el registro recién guardado (HTTP 200 confirmado)
                this.actualizarBannerUltimoRegistro();

                // Recargar productos reactivamente para actualizar stock
                if (window.DataReloadHelpers && window.DataReloadHelpers.recargarProductos) {
                    window.DataReloadHelpers.recargarProductos().catch(err => console.error("[Pulido] Error actualizando stock:", err));
                }

                // Si quedan más reportes pendientes en la cola, seguir procesándolos
                if (this._obtenerReportesFallidos().length > 0) {
                    this.verificarReportesPendientes();
                }
            } else {
                console.warn("Servidor rechazó el reporte:", result.error);
                throw new Error(result.error || 'Error desconocido del servidor');
            }
        } catch (error) {
            console.error("Error crítico al guardar:", error);

            // Persistencia Local (LocalStorage) ante fallos — se AGREGA a la cola,
            // nunca sobrescribe reportes previos que sigan pendientes de envío.
            this._agregarOActualizarReporteFallido(data);

            const isServerError = typeof error.status === 'number' || error.message.includes('HTTP');
            // error.body.error trae el motivo real que mandó el backend (ej. duración de
            // turno inválida) -- sin esto solo se veía "HTTP 400" y no daba pista de qué corregir.
            const errorMsg = isServerError ? (error.body?.error || error.message) : 'No se pudo contactar al servidor tras varios intentos.';

            Swal.fire({
                title: isServerError ? 'Error del Servidor' : 'Fallo de Conexión',
                text: `${errorMsg} El reporte se guardó LOCALMENTE en la tablet. Puedes reintentar ahora o cerrar para intentarlo más tarde.`,
                icon: 'error',
                showCancelButton: true,
                confirmButtonText: 'Reintentar Ahora',
                cancelButtonText: 'Guardar y Cerrar',
                confirmButtonColor: '#3b82f6'
            }).then((result) => {
                if (result.isConfirmed) {
                    this.enviarAServidor(data);
                } else {
                    // Si deciden cerrar, limpiamos la UI pero el reporte queda en la cola local pendiente
                    this.limpiarSesionLocal();
                    const modal = document.getElementById('modal-reporte-final');
                    if (modal) modal.style.display = 'none';
                    location.reload();
                }
            });
        }
    },

    terminarCiclo: function() {
        if (this.timerInterval) clearInterval(this.timerInterval);
        this.sesionActiva = false;
        this.sessionId = null;
        this.startTime = null;
        this.totalPausaMs = 0;
        this.tiempoAcumuladoMs = 0;
        this.enPausa = false;

        document.getElementById('pulido-active-msg').style.display = 'none';
        document.getElementById('pulido-idle-msg').style.display = 'block';
        document.getElementById('btn-iniciar-pulido').disabled = false;
        document.getElementById('btn-pausar-pulido').disabled = true;
        document.getElementById('btn-terminar-pulido').disabled = true;
        document.getElementById('pulido-main-timer').innerText = '00:00:00';
        
        // Desbloquear campos compartidos
        ['fecha-pulido', 'responsable-pulido-input', 'buscador-productos', 'orden-produccion-pulido', 'lote-pulido'].forEach(id => {
            const el = document.getElementById(id);
            if(el) el.disabled = false;
        });

        const btnUrgencia = document.getElementById('btn-cambiar-ref-pulido');
        if (btnUrgencia) btnUrgencia.style.display = 'none';

        this.validarBotonInicioPro();
        this.guardarEstadoLocal();

        // Ocultar Foto (NUEVO)
        const photoContainer = document.getElementById('pulido-product-photo-container');
        if (photoContainer) photoContainer.style.display = 'none';

        // Preguntar por trabajos en cola
        if (this.sesionesEnPausa.length > 0) {
            const proxima = this.sesionesEnPausa[0];
            Swal.fire({
                title: '¿Retomar pendiente?',
                text: `Tienes un trabajo en cola: ${proxima.prod}. ¿Deseas retomarlo ahora?`,
                icon: 'question',
                showCancelButton: true,
                confirmButtonText: 'Sí, retomar',
                cancelButtonText: 'No, después'
            }).then(r => {
                if (r.isConfirmed) {
                    this.retomarSesion(0);
                }
            });
        }
    },

    // ==========================================
    // HELPERS
    // ==========================================

    cargarDatosMaestros: async function () {
        try {
            // Responsables
            const resp = await fetch('/api/obtener_responsables').then(r => r.json());
            this.responsablesData = resp || [];
            
            // Productos (Usa AppState si existe, sino fetch)
            if (window.AppState?.sharedData?.productos?.length > 0) {
                this.productosData = window.AppState.sharedData.productos;
            } else {
                const prods = await fetch('/api/productos/listar').then(r => r.json());
                this.productosData = prods?.items || prods?.productos || [];
            }
        } catch (e) { console.error("Error maestros:", e); }
    },

    initAutocompletes: function () {
        const inputResp = document.getElementById('responsable-pulido-input');
        const suggestionsResp = document.getElementById('pulido-responsable-suggestions');
        
        if (inputResp && suggestionsResp) {
            inputResp.addEventListener('input', (e) => {
                const query = e.target.value.toLowerCase();
                if (!query) { suggestionsResp.classList.remove('active'); return; }
                const resultados = this.responsablesData.filter(resp => 
                    (typeof resp === 'string' ? resp : (resp.nombre || '')).toLowerCase().includes(query)
                ).slice(0, 5);
                this.renderSuggestions(suggestionsResp, resultados, (item) => {
                    const val = typeof item === 'object' ? item.nombre : item;
                    inputResp.value = val;
                    localStorage.setItem(this.getLastResponsableKey(), val);
                    suggestionsResp.classList.remove('active');
                    inputResp.dispatchEvent(new Event('input'));
                });
            });
        }

        const inputProd = document.getElementById('buscador-productos');
        const suggestionsProd = document.getElementById('pulido-producto-suggestions');
        if (inputProd && suggestionsProd) {
            inputProd.addEventListener('input', (e) => {
                const query = e.target.value.trim().toLowerCase();
                if (query.length < 2) { suggestionsProd.classList.remove('active'); return; }
                const resultados = this.productosData.filter(p => 
                    String(p.codigo_sistema || '').toLowerCase().includes(query) || 
                    String(p.descripcion || '').toLowerCase().includes(query)
                ).slice(0, 10);
                this.renderSuggestions(suggestionsProd, resultados, (p) => {
                    inputProd.value = p.codigo_sistema;
                    this.selectedProduct = p; // Guardar para el cronómetro
                    suggestionsProd.classList.remove('active');
                    inputProd.dispatchEvent(new Event('input'));
                    
                    // Si ya está en modo PRO y la sesión no ha iniciado, 
                    // tal vez quiera ver la foto antes de empezar (opcional)
                });
            });
        }

        // Click outside suggestions
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.autocomplete-suggestions')) {
                document.querySelectorAll('.autocomplete-suggestions').forEach(el => el.classList.remove('active'));
            }
        });
    },

    renderSuggestions: function (container, items, onSelect) {
        if (items.length === 0) { container.classList.remove('active'); return; }
        container.innerHTML = items.map(item => {
            const isProd = typeof item === 'object' && item.codigo_sistema;
            const val = isProd ? item.codigo_sistema : (item.nombre || item);
            const desc = item.descripcion ? `<br><small class="text-muted" style="font-size: 0.75rem;">${item.descripcion}</small>` : '';
            
            if (isProd) {
                let imgSrc = item.imagen || '';
                if (imgSrc && !imgSrc.startsWith('/') && !imgSrc.startsWith('http') && !imgSrc.startsWith('data:')) {
                    imgSrc = `/static/img/productos/${imgSrc}`;
                }
                const img = imgSrc ? `<img src="${imgSrc}" style="width: 45px; height: 45px; object-fit: contain; margin-right: 12px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;" onerror="this.src='/static/img/no-image.svg'">` : '';
                return `
                    <div class="suggestion-item p-2 border-bottom d-flex align-items-center" style="cursor:pointer; transition: background 0.2s;">
                        ${img}
                        <div style="line-height: 1.2;">
                            <span class="fw-bold text-dark">${val}</span>
                            ${desc}
                        </div>
                    </div>`;
            }
            
            return `<div class="suggestion-item p-2 border-bottom" style="cursor:pointer;">${val}${desc}</div>`;
        }).join('');
        
        container.querySelectorAll('.suggestion-item').forEach((div, idx) => {
            div.addEventListener('click', () => onSelect(items[idx]));
        });
        container.classList.add('active');
    },

    cargarCacheUI: function () {
        const lastResp = localStorage.getItem(this.getLastResponsableKey());
        if (lastResp) {
            const input = document.getElementById('responsable-pulido-input');
            if (input) input.value = lastResp;
        }
    },

    limpiarFormulario: function() {
        document.getElementById('form-pulido')?.reset();
        this.selectedProduct = null;
        this.pncRows = [];
        this.revueltosRows = [];
        this.renderFilasPnc();
        this.renderFilasRevuelto();
        this.actualizarCalculoManual();
        this.actualizarCalculoPro();
    },

    // ==========================================
    // GESTIÓN DE FILAS DINÁMICAS (PNC Y REVUELTOS)
    // ==========================================
    
    agregarFilaPnc: function() {
        const id = Date.now();
        this.pncRows.push({ id, proceso: 'PULIDO', cantidad: 0, criterio: '' });
        this.renderFilasPnc();
    },

    eliminarFilaPnc: function(id) {
        this.pncRows = this.pncRows.filter(r => r.id !== id);
        this.renderFilasPnc();
        this.actualizarCalculoPro();
    },

    renderFilasPnc: function() {
        const container = document.getElementById('pnc-dynamic-container');
        const emptyMsg = document.getElementById('pnc-empty-msg');
        if (!container) return;

        if (this.pncRows.length === 0) {
            container.innerHTML = '<div class="text-center text-muted py-2 small" id="pnc-empty-msg">No hay PNC reportado</div>';
            return;
        }

        container.innerHTML = this.pncRows.map(row => `
            <div class="pnc-row d-flex gap-2 align-items-center bg-white p-2 rounded border shadow-sm">
                <select id="pnc-proc-${row.id}" class="form-select form-select-sm" style="width: 110px;">
                    <option value="PULIDO" ${row.proceso === 'PULIDO' ? 'selected' : ''}>PULIDO</option>
                    <option value="INYECCION" ${row.proceso === 'INYECCION' ? 'selected' : ''}>INYECCIÓN</option>
                    <option value="ENSAMBLE" ${row.proceso === 'ENSAMBLE' ? 'selected' : ''}>ENSAMBLE</option>
                </select>
                <input type="number" id="pnc-cant-${row.id}" class="form-control form-control-sm" placeholder="Cant" style="width: 70px;" value="${row.cantidad}" oninput="ModuloPulido.actualizarCalculoPro()">
                <select id="pnc-crit-${row.id}" class="form-select form-select-sm flex-grow-1">
                    <option value="">Seleccionar motivo...</option>
                    ${(this.catalogosPnc[row.proceso] || []).map(c => `<option value="${c}" ${row.criterio === c ? 'selected' : ''}>${c}</option>`).join('')}
                    <option value="OTRO">OTRO</option>
                </select>
                <button type="button" class="btn btn-sm btn-outline-danger border-0" onclick="ModuloPulido.eliminarFilaPnc(${row.id})">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `).join('');

        // Re-vincular eventos de cambio de proceso para actualizar criterios
        this.pncRows.forEach(row => {
            const selectProc = document.getElementById(`pnc-proc-${row.id}`);
            if (selectProc) {
                selectProc.addEventListener('change', (e) => {
                    const newProc = e.target.value;
                    const rowIdx = this.pncRows.findIndex(r => r.id === row.id);
                    if (rowIdx !== -1) {
                        this.pncRows[rowIdx].proceso = newProc;
                        this.renderFilasPnc();
                    }
                });
            }
        });
    },

    // --- NUEVA SECCIÓN: BUJES REVUELTOS ---
    
    agregarFilaRevuelto: function() {
        const id = Date.now();
        this.revueltosRows.push({ id, id_codigo: '', cantidad: 0 });
        this.renderFilasRevuelto();
        this.initRevueltosAutocomplete(id);
    },

    eliminarFilaRevuelto: function(id) {
        this.revueltosRows = this.revueltosRows.filter(r => r.id !== id);
        this.renderFilasRevuelto();
    },

    renderFilasRevuelto: function() {
        const container = document.getElementById('revueltos-dynamic-container');
        if (!container) return;

        if (this.revueltosRows.length === 0) {
            container.innerHTML = '<div class="text-center text-muted py-2 small" id="revueltos-empty-msg">No hay bujes revueltos</div>';
            return;
        }

        container.innerHTML = this.revueltosRows.map(row => `
            <div class="revueltos-row d-flex gap-2 align-items-center bg-white p-2 rounded border shadow-sm position-relative">
                <div class="flex-grow-1 position-relative">
                    <input type="text" id="rev-cod-${row.id}" class="form-control form-control-sm" placeholder="Referencia..." value="${row.id_codigo}" autocomplete="off" oninput="ModuloPulido.updateRevState(${row.id})">
                    <div id="rev-sugg-${row.id}" class="autocomplete-suggestions" style="top: 100%; left: 0; width: 100%; z-index: 1000;"></div>
                </div>
                <input type="number" id="rev-cant-${row.id}" class="form-control form-control-sm" placeholder="Cant" style="width: 80px;" value="${row.cantidad}" oninput="ModuloPulido.updateRevState(${row.id})">
                <button type="button" class="btn btn-sm btn-outline-danger border-0" onclick="ModuloPulido.eliminarFilaRevuelto(${row.id})">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `).join('');

        this.revueltosRows.forEach(row => {
            this.initRevueltosAutocomplete(row.id);
        });
    },

    updateRevState: function(id) {
        const row = this.revueltosRows.find(r => r.id === id);
        if (row) {
            row.id_codigo = document.getElementById(`rev-cod-${id}`)?.value || '';
            row.cantidad = parseFloat(document.getElementById(`rev-cant-${id}`)?.value) || 0;
        }
    },

    initRevueltosAutocomplete: function(rowId) {
        const input = document.getElementById(`rev-cod-${rowId}`);
        const suggestions = document.getElementById(`rev-sugg-${rowId}`);
        if (!input || !suggestions) return;

        input.addEventListener('input', (e) => {
            const query = e.target.value.trim().toUpperCase();
            if (query.length < 2) {
                suggestions.classList.remove('active');
                return;
            }

            const resultados = this.productosData.filter(p => 
                (p.codigo_sistema || '').toUpperCase().includes(query) || 
                (p.descripcion || '').toUpperCase().includes(query)
            ).slice(0, 5);

            this.renderSuggestions(suggestions, resultados, (p) => {
                input.value = p.codigo_sistema;
                const rowIdx = this.revueltosRows.findIndex(r => r.id === rowId);
                if (rowIdx !== -1) this.revueltosRows[rowIdx].id_codigo = p.codigo_sistema;
                suggestions.classList.remove('active');
            });
        });
    },

    mostrarFotoProducto: function() {
        const container = document.getElementById('pulido-product-photo-container');
        const img = document.getElementById('pulido-product-photo');
        if (!container || !img) return;

        let url = "";
        if (this.selectedProduct && this.selectedProduct.imagen) {
            url = this.selectedProduct.imagen;
        } else {
            // Si no tenemos selectedProduct (ej. tras recargar), buscamos en la data
            const codigo = this.normalizarCodigo(document.getElementById('buscador-productos')?.value);
            const prod = this.productosData.find(p => this.normalizarCodigo(p.codigo_sistema) === codigo);
            if (prod) {
                url = prod.imagen;
            }
        }

        if (url) {
            // FIX: Si la URL es relativa (ej: 'No-disponible.jpg'), apuntar a la carpeta de productos
            if (!url.startsWith('/') && !url.startsWith('http') && !url.startsWith('data:')) {
                url = `/static/img/productos/${url}`;
            }
            img.src = url;
            container.style.display = 'block';
        } else {
            container.style.display = 'none';
        }
    },

    // ==========================================
    // REPORTE MASIVO POR VOZ (MODO LEGADO)
    // ==========================================
    loteVoz: [],
    transcripcionCompleta: '',
    recognitionMasivo: null,
    isEscuchandoMasivo: false,

    // ==========================================
    // MODO LOTES EN VIVO (MODO 3 — MES)
    // Estado del lote seleccionado táctilmente.
    // Voz solo dicta cantidades numéricas.
    // ==========================================
    lotesActivosData: [],          // Caché de lotes devueltos por GET /api/pulido/lotes_activos
    loteSeleccionado: null,        // { id_lote, id_codigo, orden_produccion } del lote tocado en pantalla
    recognitionLote: null,         // Instancia SpeechRecognition del Modo Lotes
    isEscuchandoLote: false,       // Flag para controlar el toggle de voz del Modo Lotes

    abrirDictadoMasivo: function() {
        const resp = this.getOperarioActual();
        if (!resp) {
            Swal.fire({
                title: 'Operario Requerido',
                text: 'Por favor ingrese o busque un Responsable en el campo principal antes de abrir el dictado masivo.',
                icon: 'warning',
                confirmButtonColor: '#3b82f6'
            });
            return;
        }

        this.loteVoz = [];
        this.transcripcionCompleta = '';
        const modal = document.getElementById('modal-reporte-masivo-voz');
        if (modal) {
            modal.style.setProperty('display', 'flex', 'important');
        }
        this.renderTablaMasivo();
        
        const hi = document.getElementById('hora-inicio-pulido')?.value;
        const hf = document.getElementById('hora-fin-pulido')?.value;
        if (hi) document.getElementById('hora-inicio-global-masivo').value = hi;
        if (hf) document.getElementById('hora-fin-global-masivo').value = hf;
    },

    // ==============================================================
    // MODO LOTES EN VIVO — FUNCIONES PRINCIPALES
    // ==============================================================
    gruposLotesActivos: [],
    grupoLoteSeleccionado: null,

    cargarLotesActivos: async function() {
        const contenedor = document.getElementById('lista-lotes-activos');
        const spinnerLotes = document.getElementById('spinner-lotes-activos');
        if (!contenedor) return;

        if (spinnerLotes) spinnerLotes.style.display = 'flex';
        contenedor.innerHTML = '';

        try {
            const res = await fetch('/api/pulido/lotes_activos');
            const data = await res.json();
            
            console.log("Lotes recibidos del servidor:", data);

            if (spinnerLotes) spinnerLotes.style.display = 'none';

            if (!data.success || data.data?.lotes.length === 0) {
                contenedor.innerHTML = `
                    <div class="text-center text-muted py-5">
                        <i class="fas fa-layer-group mb-3 d-block" style="font-size:2.5rem;opacity:0.35"></i>
                        <span class="fw-bold d-block">No hay lotes abiertos en producción</span>
                        <small>El Jefe de Máquinas debe iniciar el turno primero.</small>
                    </div>`;
                this.lotesActivosData = [];
                this.gruposLotesActivos = [];
                return;
            }

            this.lotesActivosData = data.data.lotes;

            const grupos = [];
            data.data.lotes.forEach(l => {
                if (!l || !l.id_lote) {
                    console.warn('[Pulido] Entrada de lote nula/incompleta recibida del servidor — ignorada:', l);
                    return;
                }
                const maq = l.maquina || 'Sin Máquina';
                const op = l.orden_produccion || 'Sin OP';
                let g = grupos.find(x => x.maquina === maq && x.orden_produccion === op);
                if (!g) {
                    g = {
                        maquina: maq,
                        orden_produccion: op,
                        fecha_creacion: l.fecha_creacion || 'Sin Fecha',
                        referencias: []
                    };
                    grupos.push(g);
                }
                g.referencias.push(l);
            });

            this.gruposLotesActivos = grupos;

            contenedor.innerHTML = grupos.map((g, index) => {
                const codigosHTML = g.referencias.map(l => `<span class="badge bg-light text-dark border me-1">${l.id_codigo}</span>`).join('');
                
                return `
                    <div class="card lote-card-activo mb-2 border-start border-4 border-success shadow-sm"
                         id="lote-grupo-card-${index}"
                         onclick="ModuloPulido.seleccionarGrupoLote(${index})"
                         style="cursor:pointer; transition: all .15s; user-select:none;">
                        <div class="card-body py-2 px-3">
                            <div class="d-flex justify-content-between align-items-start">
                                <div style="flex:1;">
                                    <span class="fw-bold text-dark d-block mb-1" style="font-size:1.1rem">MÁQUINA: ${g.maquina}</span>
                                    <div class="mb-1"><small class="text-muted">OP: <strong>${g.orden_produccion}</strong></small></div>
                                    <div class="mb-2"><span class="badge bg-secondary"><i class="fas fa-clock me-1"></i> Inyectado: ${g.fecha_creacion}</span></div>
                                    <div class="d-flex flex-wrap mt-1">${codigosHTML}</div>
                                </div>
                                <div class="text-end d-flex flex-column align-items-end gap-2 ms-2">
                                    <span class="badge bg-success-subtle text-success">${g.referencias.length} Ref(s)</span>
                                    <button type="button"
                                        class="btn btn-danger btn-sm text-white fw-bold"
                                        data-grupo-index="${index}"
                                        data-accion="liquidar"
                                        title="Cerrar canastilla: pone por_pulir en 0 y envía a Validación">
                                        <i class="fas fa-fire-alt me-1"></i>Liquidar
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');

            // Event delegation: botones Liquidar sin onclick inline (evita SyntaxError)
            contenedor.querySelectorAll('[data-accion="liquidar"]').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const idx = parseInt(btn.getAttribute('data-grupo-index'), 10);
                    const grupo = this.gruposLotesActivos[idx];
                    if (!grupo || !Array.isArray(grupo.referencias)) return;
                    const idLotes = grupo.referencias.map(l => l?.id_lote).filter(Boolean);
                    this.liquidarLote(idLotes, grupo.maquina);
                });
            });

        } catch (err) {
            if (spinnerLotes) spinnerLotes.style.display = 'none';
            console.error('[Modo Lotes] Error:', err);
            contenedor.innerHTML = '<p class="text-danger text-center mt-3">Error al cargar lotes.</p>';
        }
    },

    seleccionarGrupoLote: function(index) {
        const grupo = this.gruposLotesActivos[index];
        if (!grupo || !Array.isArray(grupo.referencias) || grupo.referencias.length === 0) {
            console.warn('[Pulido] Grupo de lote inválido o sin referencias — abortando selección:', grupo);
            return;
        }

        // Sanitizar: descartar referencias nulas o sin id_lote antes de renderizar
        const referenciasValidas = grupo.referencias.filter(l => l && l.id_lote);
        if (referenciasValidas.length === 0) {
            Swal.fire({
                title: 'Lote Inválido',
                text: 'La canastilla seleccionada no tiene referencias válidas. Actualiza la lista e inténtalo de nuevo.',
                icon: 'error'
            });
            return;
        }

        this.grupoLoteSeleccionado = grupo;
        this.loteSeleccionado = referenciasValidas[0];

        document.querySelectorAll('.lote-card-activo').forEach(c => {
            c.style.background = '';
            c.style.boxShadow = '';
        });
        const card = document.getElementById(`lote-grupo-card-${index}`);
        if (card) {
            card.style.background = '#d1fae5';
            card.style.boxShadow = '0 0 0 3px #10b981';
        }

        const panelCant = document.getElementById('panel-lote-cantidades');
        if (panelCant) panelCant.style.display = 'block';

        const opEl = document.getElementById('lote-modo-op');
        const maqEl = document.getElementById('lote-modo-maquina');
        if (opEl) opEl.value = grupo.orden_produccion;
        if (maqEl) maqEl.value = grupo.maquina;

        const container = document.getElementById('lote-modo-referencias-container');
        if (container) {
            let html = '';
            referenciasValidas.forEach(lote => {
                html += `
                <div class="card p-3 mb-3 border rounded-3 bg-white shadow-sm reference-row-block" 
                     data-lote-id="${lote.id_lote}" data-codigo="${lote.id_codigo}">
                    <div class="row g-3 align-items-center">
                        <div class="col-md-6 col-12">
                            <label class="form-label fw-bold text-muted small text-uppercase mb-1">Referencia</label>
                            <input type="text" class="form-control fw-bold text-dark bg-light" readonly value="${lote.id_codigo}" style="border-radius:8px;">
                        </div>
                        <div class="col-md-6 col-12">
                            <label class="form-label fw-bold text-success small text-uppercase mb-1">
                                <i class="fas fa-check-circle me-1"></i>Bujes Buenos (OK)
                            </label>
                            <input type="number" class="form-control text-center fw-bold lote-buenos-input" 
                                   min="0" value="0" data-lote-id="${lote.id_lote}"
                                   style="color:#16a34a; border:2px solid #86efac; border-radius:8px;">
                        </div>
                    </div>

                    
                    <div class="defectos-container mt-3 pt-2 border-top" id="defects-container-${lote.id_lote.replace(/[^a-zA-Z0-9]/g, '_')}">
                        <!-- Sub-filas de defectos -->
                    </div>
                    
                    <div class="revueltos-container mt-3 pt-2 border-top" id="revueltos-container-${lote.id_lote.replace(/[^a-zA-Z0-9]/g, '_')}">
                        <!-- Sub-filas de revueltos -->
                    </div>
                    
                    <div class="mt-2 text-end d-flex justify-content-end gap-2">
                        <button type="button" class="btn btn-sm btn-outline-secondary fw-bold rounded-pill px-3"
                                onclick="ModuloPulido.agregarRevueltoFilaMasiva('${lote.id_lote}')">
                            <i class="fas fa-layer-group me-1"></i>+ Añadir Revuelto
                        </button>
                    </div>
                </div>
                `;
            });
            container.innerHTML = html;
        }

        const statusEl = document.getElementById('status-voz-lote');
        if (statusEl) statusEl.textContent = '';
    },

    seleccionarLote: function(idLote) {
        if (!this.gruposLotesActivos) return;
        const index = this.gruposLotesActivos.findIndex(g => g.referencias.some(l => l.id_lote === idLote));
        if (index !== -1) {
            this.seleccionarGrupoLote(index);
        }
    },

    liquidarLote: async function(idLotes, maquinaNombre) {
        // idLotes es un array con todos los id_lote del grupo
        idLotes = Array.isArray(idLotes) ? idLotes.filter(Boolean) : [];
        if (idLotes.length === 0) {
            Swal.fire({ title: 'Sin Lotes Válidos', text: 'No se encontraron lotes válidos para liquidar.', icon: 'warning' });
            return;
        }

        const operario = this.getOperarioActual() || '';
        const codigosTexto = (this.gruposLotesActivos || []).find(g => g.maquina === maquinaNombre)?.referencias?.map(l => l.id_codigo).join(', ') || '';

        const { value: formValues } = await Swal.fire({
            title: '⚡ Liquidar Canastilla',
            icon: 'warning',
            html: `
                <div class="alert alert-warning border-0 text-start py-2 px-3 mb-3" style="background:#fef9c3;border-radius:10px;">
                    <strong>Máquina:</strong> ${maquinaNombre || '-'}<br>
                    <strong>Referencias:</strong> ${codigosTexto || idLotes.join(', ')}<br>
                    <small class="text-muted">Esto pondrá <code>por_pulir = 0</code> en cada lote del grupo y los enviará a <b>Validación</b>.</small>
                </div>
                <div class="text-start">
                    <label class="form-label fw-bold small text-uppercase text-muted mb-1">Responsable que liquida</label>
                    <input type="text" id="swal-liq-responsable" class="form-control"
                           value="${operario}" placeholder="Nombre del supervisor">
                </div>
            `,
            showCancelButton: true,
            confirmButtonText: '<i class="fas fa-fire-alt me-1"></i> Sí, Liquidar Todo',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#dc2626',
            focusConfirm: false,
            preConfirm: () => {
                const resp = document.getElementById('swal-liq-responsable').value.trim();
                if (!resp) {
                    Swal.showValidationMessage('El responsable es obligatorio');
                    return false;
                }
                return resp;
            }
        });

        if (!formValues) return;
        const responsable = formValues;

        if (typeof window.mostrarLoading === 'function') window.mostrarLoading(true, 'Liquidando lotes...');

        let errores = [];
        for (const id_lote of idLotes) {
            try {
                const res = await fetch('/api/pulido/liquidar_lote', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id_lote, responsable })
                });
                const data = await res.json();
                if (!data.success) errores.push(`${id_lote}: ${data.error}`);
            } catch (e) {
                errores.push(`${id_lote}: Error de red`);
            }
        }

        if (typeof window.mostrarLoading === 'function') window.mostrarLoading(false);

        if (errores.length === 0) {
            await Swal.fire({
                icon: 'success',
                title: '¡Lotes Liquidados!',
                text: `${idLotes.length} lote(s) cerrados y enviados a Validación.`,
                timer: 2500,
                showConfirmButton: false
            });
        } else {
            await Swal.fire('Error Parcial', errores.join('\n'), 'error');
        }

        // Refrescar lista y limpiar selección
        this.grupoLoteSeleccionado = null;
        const panelCant = document.getElementById('panel-lote-cantidades');
        if (panelCant) panelCant.style.display = 'none';
        await this.cargarLotesActivos();
    },

    agregarRevueltoFilaMasiva: function(idLote) {
        if (!idLote) {
            console.warn('[Pulido] agregarRevueltoFilaMasiva llamado sin idLote — abortando.');
            return;
        }
        const containerId = `revueltos-container-${idLote.replace(/[^a-zA-Z0-9]/g, '_')}`;
        const container = document.getElementById(containerId);
        if (!container) return;

        const rowId = Math.random().toString(36).substring(2, 9);
        const subRowId = 'revuelto-row-' + rowId;
        const subRow = document.createElement('div');
        subRow.className = 'row g-2 mb-2 align-items-center revuelto-sub-row';
        subRow.id = subRowId;

        subRow.innerHTML = `
            <div class="col-6 col-md-7 mb-2 position-relative">
                <input type="text" class="form-control form-control-sm rev-codigo" 
                       id="rev-cod-masivo-${rowId}"
                       placeholder="Buscar referencia revuelta..." 
                       autocomplete="off" style="border-radius:8px;">
                <div id="rev-sugg-masivo-${rowId}" class="autocomplete-suggestions" style="top: 100%; left: 0; width: 100%; z-index: 1000;"></div>
            </div>
            <div class="col-4 col-md-3">
                <input type="number" class="form-control form-control-sm text-center fw-bold rev-cantidad" 
                       min="1" placeholder="Cant" value="1" 
                       style="color:#0284c7; border:1px solid #bae6fd; border-radius:8px;">
            </div>
            <div class="col-2 col-md-2 text-end">
                <button type="button" class="btn btn-sm btn-link text-danger p-0" 
                        onclick="document.getElementById('${subRowId}').remove()">
                    <i class="fas fa-trash-alt"></i>
                </button>
            </div>
        `;
        container.appendChild(subRow);

        this.initAutocompleteRevueltoMasivo(rowId);
    },

    initAutocompleteRevueltoMasivo: function(rowId) {
        const input = document.getElementById(`rev-cod-masivo-${rowId}`);
        const suggestions = document.getElementById(`rev-sugg-masivo-${rowId}`);
        if (!input || !suggestions) return;

        input.addEventListener('input', (e) => {
            const query = e.target.value.trim().toLowerCase();
            if (query.length < 2) {
                suggestions.classList.remove('active');
                return;
            }

            const resultados = this.productosData.filter(p => 
                String(p.codigo_sistema || '').toLowerCase().includes(query) || 
                String(p.descripcion || '').toLowerCase().includes(query)
            ).slice(0, 10);

            this.renderSuggestions(suggestions, resultados, (p) => {
                input.value = p.codigo_sistema;
                suggestions.classList.remove('active');
                input.dispatchEvent(new Event('input'));
            });
        });
    },



    enviarReporteLote: async function() {
        if (!this.grupoLoteSeleccionado) {
            Swal.fire({ title: 'Sin Selección', text: 'Selecciona un lote de la lista primero.', icon: 'warning' }); return;
        }
        const responsable = this.getOperarioActual();
        if (!responsable) {
            Swal.fire({ title: 'Sin Operario', text: 'Inicia sesión o ingresa tu nombre.', icon: 'warning' }); return;
        }

        const blocks = document.querySelectorAll('.reference-row-block');
        const items = [];

        blocks.forEach(block => {
            try {
            const idLote = block.getAttribute('data-lote-id');
            const referencia = block.getAttribute('data-codigo');
            const op = this.grupoLoteSeleccionado?.orden_produccion || '';

            // Guard clause: sin id_lote no hay forma de identificar el registro en el backend
            if (!idLote) {
                console.warn('[Pulido] Bloque de lote sin data-lote-id — ignorado:', block);
                return;
            }

            const buenos = parseFloat(block.querySelector('.lote-buenos-input')?.value) || 0;

            // PNC de defectos: sección eliminada del DOM (solo Buenas reportadas)
            let totalMalos = 0;
            const pnc_detail = [];
            // Los .defect-sub-row ya no existen en el HTML — no iterar para evitar null references

            if (buenos === 0 && totalMalos === 0) {
                // Si no hay nada, pasamos al siguiente (solo si no hay revueltos tampoco)
            }

            const revueltoRows = block.querySelectorAll('.revuelto-sub-row');
            const revueltos = [];
            let totalRevueltos = 0;
            revueltoRows.forEach(row => {
                const cod = row.querySelector('.rev-codigo')?.value || '';
                const cant = parseFloat(row.querySelector('.rev-cantidad')?.value) || 0;
                if (cod && cant > 0) {
                    totalRevueltos += cant;
                    revueltos.push({
                        id_codigo: cod,
                        cantidad: cant
                    });
                }
            });

            const item_hora_inicio = block.querySelector('.item-hora-inicio')?.value || '';
            const item_hora_fin = block.querySelector('.item-hora-fin')?.value || '';

            // Anti-Basura: Si todo es cero, se ignora por completo
            if (buenos === 0 && totalMalos === 0 && totalRevueltos === 0) {
                return;
            }

            items.push({
                referencia: referencia,
                op: op,
                lote: idLote,
                id_lote: idLote,
                buenos: buenos,
                malos: totalMalos,
                pnc_detail: pnc_detail,
                revueltos: revueltos,
                hora_inicio: item_hora_inicio,
                hora_fin: item_hora_fin
            });
            } catch (blockErr) {
                console.error('[Pulido] Error procesando bloque de lote (ignorado):', blockErr);
            }
        });

        if (items.length === 0) {
            Swal.fire({ title: 'Sin Movimiento', text: 'No hay piezas para reportar', icon: 'warning' });
            return;
        }

        const hi = document.getElementById('hora-inicio-pulido')?.value || '';
        const hf = document.getElementById('hora-fin-pulido')?.value   || '';

        const payload = {
            responsable,
            hora_inicio: hi,
            hora_fin   : hf,
            items: items
        };

        if (typeof window.mostrarLoading === 'function') window.mostrarLoading(true, 'Registrando...');

        try {
            const res = await fetch('/api/pulido/reporte_masivo', {
                method : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body   : JSON.stringify(payload)
            });

            // Intentar parsear el cuerpo aunque la respuesta sea un error HTTP
            // (400/409 del Ownership Guard) — el backend normalmente devuelve
            // JSON incluso en fallos, pero no lo asumimos.
            let data = null;
            try {
                data = await res.json();
            } catch (parseErr) {
                data = null;
            }

            if (typeof window.mostrarLoading === 'function') window.mostrarLoading(false);

            if (!res.ok || !data || !data.success) {
                const mensaje = data?.error || `Error del servidor (HTTP ${res.status}).`;
                Swal.fire({ title: 'Error', text: mensaje, icon: 'error' });
                return;
            }

            const totalBuenos = items.reduce((sum, item) => sum + item.buenos, 0);
            const totalMalos = items.reduce((sum, item) => sum + item.malos, 0);

            Swal.fire({
                title: '¡Registrado!',
                html: `Reporte registrado con éxito:<br><b>${totalBuenos}</b> buenos y <b>${totalMalos}</b> malos para la máquina <code>${this.grupoLoteSeleccionado?.maquina || '-'}</code>`,
                icon: 'success', confirmButtonColor: '#10b981'
            });

            this.grupoLoteSeleccionado = null;
            this.loteSeleccionado = null;
            const panelCant = document.getElementById('panel-lote-cantidades');
            if (panelCant) panelCant.style.display = 'none';
            await this.cargarLotesActivos();
        } catch (err) {
            if (typeof window.mostrarLoading === 'function') window.mostrarLoading(false);
            console.error('[Pulido] Error de red en enviarReporteLote:', err);
            Swal.fire({ title: 'Fallo de Conexión', text: 'No se pudo conectar.', icon: 'error' });
        }
    },



    renderTablaMasivo: function() {
        const tbody = document.getElementById('tabla-masivo-voz-body');
        const countLbl = document.getElementById('count-items-masivo');
        
        if (!tbody) return;

        if (this.loteVoz.length === 0) {
            tbody.innerHTML = `
                <tr id="row-sin-items-masivo">
                    <td colspan="6" class="text-center text-muted py-5" style="color: #94a3b8 !important;">
                        <i class="fas fa-microphone-slash mb-3 d-block" style="font-size: 2.5rem; opacity: 0.4;"></i>
                        <span class="fw-bold">No se han dictado ni agregado referencias</span>
                        <small class="d-block mt-1">Presiona "Iniciar Grabación Continua" y dicta de forma natural: <br><em>"referencia MT-504, OP 905, lote primero de junio, 350 buenos, 12 malos"</em></small>
                    </td>
                </tr>
            `;
            if (countLbl) countLbl.innerText = '0';
            return;
        }

        tbody.innerHTML = this.loteVoz.map((item, index) => `
            <tr class="item-voz-row" data-index="${index}">
                <td class="position-relative">
                    <input type="text" class="form-control form-control-sm ref-masivo-input fw-bold" id="masivo-ref-${index}" value="${item.referencia}" placeholder="MT-XXX" oninput="ModuloPulido.updateLoteVozState(${index})">
                    <div id="masivo-ref-sugg-${index}" class="autocomplete-suggestions" style="top: 100%; left: 0; width: 100%; z-index: 1000;"></div>
                </td>
                <td>
                    <input type="text" class="form-control form-control-sm text-center" id="masivo-op-${index}" value="${item.op}" oninput="ModuloPulido.updateLoteVozState(${index})">
                </td>
                <td>
                    <input type="text" class="form-control form-control-sm text-center" id="masivo-lote-${index}" value="${item.lote}" oninput="ModuloPulido.updateLoteVozState(${index})">
                </td>
                <td>
                    <input type="number" class="form-control form-control-sm text-center text-success fw-bold" id="masivo-buenos-${index}" value="${item.buenos}" min="0" oninput="ModuloPulido.updateLoteVozState(${index})">
                </td>
                <td>
                    <input type="number" class="form-control form-control-sm text-center text-danger fw-bold" id="masivo-malos-${index}" value="${item.malos}" min="0" oninput="ModuloPulido.updateLoteVozState(${index})">
                </td>
                <td class="text-center">
                    <button type="button" class="btn btn-sm btn-outline-danger border-0" onclick="ModuloPulido.eliminarFilaMasivo(${index})">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                </td>
            </tr>
        `).join('');

        if (countLbl) {
            countLbl.innerText = this.loteVoz.length;
        }

        this.loteVoz.forEach((item, index) => {
            this.initMasivoRowAutocomplete(index);
        });
    },

    updateLoteVozState: function(index) {
        const item = this.loteVoz[index];
        if (item) {
            item.referencia = document.getElementById(`masivo-ref-${index}`)?.value || '';
            item.op = document.getElementById(`masivo-op-${index}`)?.value || '';
            item.lote = document.getElementById(`masivo-lote-${index}`)?.value || '';
            item.buenos = parseFloat(document.getElementById(`masivo-buenos-${index}`)?.value) || 0;
            item.malos = parseFloat(document.getElementById(`masivo-malos-${index}`)?.value) || 0;
        }
    },

    initMasivoRowAutocomplete: function(index) {
        const input = document.getElementById(`masivo-ref-${index}`);
        const suggestions = document.getElementById(`masivo-ref-sugg-${index}`);
        if (!input || !suggestions) return;

        input.addEventListener('input', (e) => {
            const query = e.target.value.trim().toUpperCase();
            if (query.length < 2) {
                suggestions.classList.remove('active');
                return;
            }

            const resultados = this.productosData.filter(p => 
                (p.codigo_sistema || '').toUpperCase().includes(query) || 
                (p.descripcion || '').toUpperCase().includes(query)
            ).slice(0, 5);

            this.renderSuggestions(suggestions, resultados, (p) => {
                input.value = p.codigo_sistema;
                this.loteVoz[index].referencia = p.codigo_sistema;
                suggestions.classList.remove('active');
            });
        });
    },

    agregarFilaManualMasivo: function() {
        if (this.loteVoz.length >= 8) {
            Swal.fire({
                title: 'Límite alcanzado',
                text: 'El reporte masivo permite registrar un máximo de 8 referencias por lote.',
                icon: 'warning'
            });
            return;
        }
        
        this.loteVoz.push({
            referencia: '',
            op: 'SIN OP',
            lote: new Date().toISOString().split('T')[0],
            buenos: 0,
            malos: 0
        });

        this.renderTablaMasivo();
    },

    eliminarFilaMasivo: function(index) {
        this.loteVoz.splice(index, 1);
        this.renderTablaMasivo();
    },

    enviarLoteMasivo: async function() {
        const responsable = this.getOperarioActual();
        if (!responsable) {
            Swal.fire({
                title: 'Falta Responsable',
                text: 'No se detecta operario responsable asignado.',
                icon: 'error'
            });
            return;
        }

        if (this.loteVoz.length === 0) {
            Swal.fire({
                title: 'Lote Vacío',
                text: 'No hay referencias en la tabla para registrar.',
                icon: 'warning'
            });
            return;
        }

        for (let i = 0; i < this.loteVoz.length; i++) {
            const item = this.loteVoz[i];
            if (!item.referencia.trim()) {
                Swal.fire({
                    title: 'Falta Referencia',
                    text: `La fila #${i + 1} no tiene una referencia válida.`,
                    icon: 'warning'
                });
                return;
            }
            if (item.buenos <= 0 && item.malos <= 0) {
                Swal.fire({
                    title: 'Cantidades en Cero',
                    text: `La fila #${i + 1} (${item.referencia}) debe tener al menos una pieza buena o mala.`,
                    icon: 'warning'
                });
                return;
            }
        }

        const horaInicio = document.getElementById('hora-inicio-global-masivo').value;
        const horaFin = document.getElementById('hora-fin-global-masivo').value;

        const payload = {
            responsable: responsable,
            hora_inicio: horaInicio,
            hora_fin: horaFin,
            items: this.loteVoz
        };

        if (typeof window.mostrarLoading === 'function') {
            window.mostrarLoading(true, 'Registrando lote transaccional masivo...');
        }

        try {
            const res = await fetch('/api/pulido/reporte_masivo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (typeof window.mostrarLoading === 'function') {
                window.mostrarLoading(false);
            }

            if (data.success) {
                Swal.fire({
                    title: '¡Registro Exitoso!',
                    text: data.data?.message || 'Se registraron con éxito los reportes del lote.',
                    icon: 'success',
                    confirmButtonColor: '#10b981'
                });

                this.loteVoz = [];
                this.cerrarDictadoMasivo();
                
                if (typeof window.cargarHistorialCompleto === 'function') {
                    window.cargarHistorialCompleto();
                } else if (typeof ModuloPulido.renderCola === 'function') {
                    ModuloPulido.renderCola();
                }
            } else {
                Swal.fire({
                    title: 'Error de Servidor',
                    text: data.error || 'Ocurrió un error inesperado al procesar el lote.',
                    icon: 'error'
                });
            }
        } catch (err) {
            if (typeof window.mostrarLoading === 'function') {
                window.mostrarLoading(false);
            }
            console.error("Error al enviar lote masivo:", err);
            Swal.fire({
                title: 'Fallo de Conexión',
                text: 'No se pudo conectar con el servidor. Por favor intente más tarde.',
                icon: 'error'
            });
        }
    }
};

// Vinculación global
window.ModuloPulido = ModuloPulido;
window.initPulido = () => ModuloPulido.inicializar();
