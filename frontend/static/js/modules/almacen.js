/**
 * Módulo de Gestión de Almacén (Alistamiento)
 * Desarrollado para FriParts por Antigravity
 * Soporta alistamiento parcial por cantidad.
 */

const AlmacenModule = {
    pedidosPendientes: [],
    pedidoActual: null,
    tvInterval: null,
    autoRefreshInterval: null,
    scrollInterval: null,
    scrollTimeout: null, // Timeout para el delay del scroll
    isTVMode: false,

    /**
     * Redirigir al módulo de Pedidos para editar un pedido existente
     * @param {string} id_pedido 
     */
    editarPedido: function (id_pedido) {
        if (!id_pedido) return;

        console.log(`🚀 [Almacen] Redirigiendo a Pedidos para editar: ${id_pedido}`);

        // Guardar ID en localStorage para que Pedidos lo capture al cargar
        localStorage.setItem('pending_edit_id', id_pedido);

        // Cambiar hash y navegar (esto disparará el cambio de módulo en app.js)
        if (window.AuthModule && typeof window.AuthModule.navigateTo === 'function') {
            window.AuthModule.navigateTo('pedidos');
        } else {
            window.location.hash = '#pedidos';
        }

        // Feedback visual opcional
        if (window.AuthModule) {
            window.AuthModule.mostrarNotificacion(`Cargando pedido ${id_pedido}...`, 'info');
        }
    },
    /**
     * Copiar ID al portapapeles con feedback visual
     */
    copiarID: function (id, element) {
        if (!id) return;

        // Fallback para navegadores sin navigator.clipboard
        const copyToClipboard = (text) => {
            if (navigator.clipboard && window.isSecureContext) {
                return navigator.clipboard.writeText(text);
            } else {
                const textArea = document.createElement("textarea");
                textArea.value = text;
                textArea.style.position = "fixed";
                textArea.style.left = "-9999px";
                textArea.style.top = "0";
                document.body.appendChild(textArea);
                textArea.focus();
                textArea.select();
                return new Promise((res, rej) => {
                    document.execCommand('copy') ? res() : rej();
                    textArea.remove();
                });
            }
        };

        copyToClipboard(id).then(() => {
            const icon = element.querySelector('i');
            const originalClass = icon.className;
            icon.className = 'fas fa-check text-success';

            // Feedback háptico si está disponible
            if (window.HapticFeedback) window.HapticFeedback.light();

            setTimeout(() => {
                icon.className = originalClass;
            }, 2000);
        }).catch(err => {
            console.error('Error al copiar ID:', err);
            mostrarNotificacion('Error al copiar el ID', 'error');
        });
    },

    /**
     * Inicializar módulo
     */
    inicializar: function () {
        if (this._inicializado) {
            console.log('⚠️ [Almacen] Módulo ya estaba inicializado, recargando datos...');
            this.cargarPedidos();
            return;
        }

        console.log('🔧 [Almacen] Inicializando módulo...');

        // Adaptación visual para Frimetals
        const division = window.AppState?.user?.division;
        if (division === 'FRIMETALS') {
            const h1 = document.querySelector('#almacen-page .page-title h1');
            if (h1) h1.innerHTML = '<i class="fas fa-warehouse"></i> Gestión Pedidos';

            const subtitle = document.querySelector('#almacen-page .page-title .subtitle');
            if (subtitle) subtitle.textContent = 'Alistamiento y preparación de pedidos pendientes';

            // También el menú para ser consistentes
            const menuLink = document.querySelector('.menu-item[data-page="almacen"] span');
            if (menuLink) menuLink.textContent = 'Gestión Pedidos';
        }

        // Marcar como inicializado
        this._inicializado = true;

        // Si el usuario ya está logueado, cargar pedidos inmediatamente
        // Verificamos tanto .name como .nombre por variaciones en AppState
        const user = window.AppState?.user;
        if (user && (user.name || user.nombre)) {
            console.log('✅ [Almacen] Usuario ya presente, cargando pedidos...');
            this.cargarPedidos();
        } else {
            // Escuchar evento de login y cargar cuando esté listo
            console.log('⏳ [Almacen] Esperando login de usuario para cargar pedidos...');

            const onUserReady = () => {
                console.log('✅ [Almacen] Evento user-ready detectado, cargando pedidos...');
                this.cargarPedidos();
                document.removeEventListener('user-ready', onUserReady);
                window.removeEventListener('user-ready', onUserReady);
            };

            document.addEventListener('user-ready', onUserReady);
            window.addEventListener('user-ready', onUserReady);

            // Timeout de seguridad: si en 4s no hay login, intentar de todos modos con lo que haya en session
            setTimeout(() => {
                document.removeEventListener('user-ready', onUserReady);
                window.removeEventListener('user-ready', onUserReady);
                if (this.pedidosPendientes.length === 0) {
                    console.log('⏰ [Almacen] Timeout, intentando carga forzada...');
                    this.cargarPedidos();
                }
            }, 4000);
        }

        this.iniciarAutoRefresco();

        // Evitar múltiples listeners en el menú
        const menuBtn = document.querySelector('[data-page="almacen"]');
        if (menuBtn && !menuBtn._hasAlmacenListener) {
            menuBtn.addEventListener('click', () => {
                console.log('🔧 [Almacen] Actualización por click en menú');
                this.cargarPedidos();
            });
            menuBtn._hasAlmacenListener = true;
        }

        console.log('✅ [Almacen] Inicialización completa');
    },

    /**
     * Cargar pedidos pendientes desde la API
     */
    cargarPedidos: async function (showLoading = true) {
        console.log('📦 [Almacen] cargarPedidos() iniciado, showLoading:', showLoading);
        try {
            if (showLoading) mostrarLoading(true);

            // CRÍTICO: Si window.AppState.user no está listo, intentar recuperarlo de AuthModule o SessionStorage
            let user = window.AppState?.user;
            console.log('📦 [Almacen] Usuario inicial:', user);
            if (!user || (!user.name && !user.nombre)) {
                const sessionUser = sessionStorage.getItem('friparts_user');
                console.log('📦 [Almacen] Recuperando usuario de sesión:', sessionUser);
                if (sessionUser) {
                    const parsed = JSON.parse(sessionUser);
                    user = {
                        name: parsed.nombre,
                        nombre: parsed.nombre,
                        rol: parsed.rol
                    };
                    console.log('📦 [Almacen] Usuario recuperado de sesión:', user.name);
                }
            }

            const url = new URL('/api/pedidos/pendientes', window.location.origin);
            url.searchParams.set('_t', Date.now()); // Cache-buster
            if (user) {
                const role = AuthModule.normalizeRole(user.rol || user.role);
                const isAdmin = ['ADMINISTRACION', 'ADMINISTRADOR', 'GERENCIA', 'JEFE ALMACEN', 'JEFE ALISTAMIENTO', 'COMERCIAL'].includes(role);

                // Si es admin, pasamos el rol Administración para que el backend devuelva todo
                url.searchParams.append('usuario', user.name || user.nombre || 'N/A');
                url.searchParams.append('rol', isAdmin ? 'ADMINISTRACION' : role);
            }

            // CRÍTICO: Cache buster para evitar que el navegador guarde la respuesta
            url.searchParams.append('_t', Date.now());

            console.log('📦 [Almacen] Haciendo fetch a:', url.toString());
            const response = await fetch(url, {
                cache: 'no-store', // Forzar al navegador a no usar cache
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            });
            console.log('📦 [Almacen] Response status:', response.status);

            const data = await response.json();

            // LOG DE DEPURACIÓN CRÍTICO PARA OBSERVACIONES
            if (data.success && data.data?.pedidos && data.data.pedidos.length > 0) {
                const sample = data.data.pedidos[0];
                console.log('📦 [Almacen] ESTRUCTURA DE PEDIDO RECIBIDA:', {
                    id: sample.id_pedido,
                    cliente: sample.cliente,
                    observaciones: sample.observaciones,
                    keys: Object.keys(sample)
                });
            }

            console.log('📦 [Almacen] Datos recibidos:', {
                success: data.success,
                pedidosCount: data.data?.pedidos?.length || 0
            });

            if (data.success) {
                // DETECTAR NUEVOS PEDIDOS (Notificación auditiva global para planta)
                if (this.pedidosPendientes && this.pedidosPendientes.length > 0) {
                    const nuevosCount = data.data.pedidos.length;
                    const anterioresCount = this.pedidosPendientes.length;

                    if (nuevosCount > anterioresCount) {
                        try {
                            if (window.ModuloUX && typeof window.ModuloUX.reproducirNotificacionPedido === 'function') {
                                window.ModuloUX.reproducirNotificacionPedido();
                            } else if (window.ModuloUX && window.ModuloUX.playSound) {
                                window.ModuloUX.playSound('new_order');
                            }
                        } catch (e) {
                            console.warn('Error reproduciendo sonido de nuevo pedido', e);
                        }
                    }
                }

                this.pedidosPendientes = data.data.pedidos;
                console.log('📦 [Almacen] Pedidos asignados, llamando renderizarTarjetas()...');
                this.renderizarTarjetas();
                console.log('✅ [Almacen] renderizarTarjetas() completado');
            } else {
                console.error('Error al cargar pedidos:', data.error);
                if (showLoading) mostrarNotificacion('Error al cargar pedidos pendientes', 'error');
            }
        } catch (error) {
            console.error('❌ [Almacen] Error fetch pedidos:', error);
            if (showLoading) mostrarNotificacion('Error de conexión con el servidor', 'error');
        } finally {
            if (showLoading) mostrarLoading(false);
            console.log('📦 [Almacen] cargarPedidos() finalizado');

            // Si está en modo TV, solo iniciar scroll si no hay uno activo
            // (para no interrumpir el ciclo cada vez que se refrescan datos)
            if (this.isTVMode && !this.scrollTimeout) {
                this.iniciarAutoScroll();
            }
        }
    },

    /**
     * Renderizar tarjetas de pedidos en el contenedor
     */
    renderizarTarjetas: function () {
        const container = document.getElementById('almacen-container');
        if (!container) return;

        if (this.pedidosPendientes.length === 0) {
            container.innerHTML = `
                <div class="text-center py-5 text-muted empty-state-almacen">
                    <i class="fas fa-clipboard-check fa-3x mb-3" style="opacity: 0.2; color: #10b981;"></i>
                    <p>¡Buen trabajo! No hay pedidos pendientes</p>
                </div>
            `;
            return;
        }

        const isMetals = (window.AppState?.user?.division === 'FRIMETALS');
        let html = '<div class="row g-3">';
        // Filtrar pedidos completados (100% Alistado y 100% Enviado)
        const pendientesReales = this.pedidosPendientes.filter(p => {
            // Limpiar % y manejar nulos de forma robusta
            const alistadoStr = String(p.progreso || '0').replace('%', '').trim();
            const enviadoStr = String(p.progreso_despacho || '0').replace('%', '').trim();

            const alistado = parseInt(alistadoStr) || 0;
            const enviado = parseInt(enviadoStr) || 0;

            // Un pedido se oculta SOLO si ambos procesos están al 100%
            const isCompletado = (alistado >= 100 && enviado >= 100);

            if (this.isTVMode) {
                console.log(`🔍 [Almacen TV] Filtrando Pedido ${p.id_pedido}: Alistado=${alistado}%, Enviado=${enviado}% -> Mostrar: ${!isCompletado}`);
            }

            return !isCompletado;
        });

        console.log(`📦 [Almacen] Pedidos tras filtrado: ${pendientesReales.length} de ${this.pedidosPendientes.length}`);

        // Set exacto de pedidos que realmente se van a pintar como tarjetas (mismo
        // criterio de validez que el forEach de abajo). El modal de cartera usa esta
        // lista, no this.pedidosPendientes, para no mostrar clientes que no están
        // visibles en pantalla en este momento.
        this.pedidosVisibles = pendientesReales.filter(p => p && p.id_pedido && p.cliente && p.cliente !== 'undefined');

        if (pendientesReales.length === 0) {
            container.innerHTML = `
                <div class="text-center py-5 text-muted empty-state-almacen">
                     <i class="fas fa-check-circle fa-3x mb-3" style="opacity: 0.2; color: #10b981;"></i>
                    <p>¡Todo al día! No hay pedidos pendientes de gestión.</p>
                </div>
            `;
            return;
        }

        pendientesReales.forEach(pedido => {
            // Validación robusta: Omitir pedidos "fantasma" sin datos mínimos
            if (!pedido || !pedido.id_pedido || !pedido.cliente || pedido.cliente === 'undefined') {
                console.warn('⚠️ [Almacen] Omitiendo pedido inválido/vacío:', pedido);
                return;
            }

            const progresoAlisado = parseInt(pedido.progreso) || 0;
            const progresoEnviado = parseInt(pedido.progreso_despacho) || 0;

            // Colores sugeridos por el usuario
            const colorStatus = this.getColorPorEstadoProporcional(progresoAlisado, progresoEnviado);

            const currentUser = window.AppState?.user;
            const currentRole = AuthModule.normalizeRole(currentUser?.rol || currentUser?.role);

            // 1. Detección robusta de la división según los requerimientos
            const isFrimetals = (window.AppState?.user?.division === 'FRIMETALS');

            // RBAC: Solo Administración, Jefe Almacén y Comercial pueden DELEGAR
            const puedeDelegar = ['ADMINISTRACION', 'ADMINISTRADOR', 'GERENCIA', 'JEFE ALMACEN', 'JEFE ALISTAMIENTO', 'COMERCIAL'].includes(currentRole);
            const esParaMi = (currentUser?.nombre || currentUser?.name) === pedido.delegado_a;

            html += `
                <div class="col-md-6 col-lg-4">
                    <div class="card h-100 shadow-sm border-0 almacen-card-pro"
                        style="transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1); border-radius: 12px; overflow: hidden; border-left: 5px solid ${colorStatus} !important; background: #fff; min-height: 280px; display: flex; flex-direction: column;">
                        <div style="background: #f8fafc; padding: 12px 15px; border-bottom: 1px solid #edf2f7; cursor: pointer;" onclick="AlmacenModule.abrirModal('${pedido.id_pedido}')">
                            <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
                                <div class="d-flex align-items-center gap-2 cursor-pointer edit-order-btn" 
                                     onclick="event.stopPropagation(); AlmacenModule.editarPedido('${pedido.id_pedido}')"
                                     title="Editar Pedido (Cargar en módulo Pedidos)">
                                    <span class="fw-bold text-primary order-id-ref" style="font-size: 1.4rem; white-space: nowrap; letter-spacing: -0.3px; font-weight: 800;">${pedido.id_pedido}</span>
                                    <i class="fas fa-edit text-warning" style="font-size: 0.9rem; opacity: 0.8;"></i>
                                </div>
                                <div class="d-flex flex-wrap gap-1 align-items-center justify-content-end" style="flex: 1;">
                                    ${esParaMi && !isFrimetals ? '<span class="badge bg-info" style="font-size: 0.6rem; padding: 4px 6px; font-weight: 700; border-radius: 4px;"><i class="fas fa-user-check me-1"></i>MÍO</span>' : ''}
                                    <span class="badge" style="background: ${colorStatus}; font-size: 0.6rem; padding: 4px 6px; text-transform: uppercase; font-weight: 700; border-radius: 4px;">${pedido.estado}</span>
                                </div>
                            </div>
                        </div>
                        <div class="card-body" style="padding: 15px; flex: 1; display: flex; flex-direction: column;">
                            <h6 class="card-title fw-bold mb-1 d-flex align-items-center flex-wrap" style="color: #1e293b; cursor: pointer; font-size: 0.85rem; line-height: 1.3; margin-bottom: 4px !important; gap: 6px;" onclick="AlmacenModule.abrirModal('${pedido.id_pedido}')">
                                <span>${pedido.cliente}</span>
                                ${(parseFloat(pedido.saldo_vencido_total) || 0) > 0 ? `
                                <span class="badge" style="background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; font-size: 0.6rem; padding: 3px 7px; border-radius: 6px; font-weight: 700; text-transform: none;" title="Cartera vencida del cliente (solo informativo, no bloquea el despacho)">
                                    <i class="fas fa-exclamation-circle me-1"></i>Mora: ${new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }).format(pedido.saldo_vencido_total)}
                                </span>` : ''}
                            </h6>
                            <p class="text-muted mb-1" style="font-size: 0.70rem; margin-bottom: 4px !important;"><i class="fas fa-map-marker-alt me-1"></i> ${pedido.direccion || 'S/D'} - ${pedido.ciudad || 'S/C'}</p>
                            <p class="text-muted mb-3" style="font-size: 0.70rem; margin-bottom: 12px !important;">
                                <i class="fas fa-calendar-alt me-1"></i> ${pedido.fecha} 
                                ${pedido.hora ? `<span class="ms-2 text-primary" style="font-weight: 600;"><i class="fas fa-clock me-1"></i> ${pedido.hora}</span>` : ''} 
                                | <i class="fas fa-user me-1"></i> ${pedido.vendedor}
                            </p>
                            
                            ${(() => {
                    const obs = (pedido.observaciones || pedido.observacion || pedido.notas || '').toString().trim();
                    if (obs && obs !== 'null' && obs !== 'undefined') {
                        return `
                                    <div class="alert alert-warning p-2 mb-3 nota-alistamiento-card" style="font-size: 0.75rem; border-radius: 8px; border-left: 4px solid #f59e0b; background-color: #fefce8; color: #92400e; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                        <i class="fas fa-exclamation-triangle me-1"></i> <strong>Nota:</strong> ${obs.length > 150 ? obs.substring(0, 150) + '...' : obs}
                                    </div>
                                    `;
                    }
                    return '';
                })()}
                            
                            <!-- Barra Doble de Progreso -->
                            <div class="mt-auto" style="cursor: pointer; margin-bottom: 10px;" onclick="AlmacenModule.abrirModal('${pedido.id_pedido}')">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <!-- 2. Reemplazo dinámico "En Reparto" -> "Alistado" para Frimetals -->
                                    <span class="small fw-bold text-muted" style="font-size: 0.65rem;"><i class="fas fa-box me-1"></i> ${isFrimetals ? 'Alistado' : 'En Reparto'}</span>
                                    <span class="small fw-bold" style="color: #6366f1; font-size: 0.7rem;">${progresoAlisado}%</span>
                                </div>
                                <div class="progress progress-modern" style="height: 6px; border-radius: 10px; background: #f1f5f9; margin-bottom: 10px;">
                                    <div class="progress-bar progress-bar-striped progress-bar-animated progress-bar-shimmer ${progresoAlisado > 0 ? 'progress-glow-indigo' : ''}" role="progressbar" style="width: ${progresoAlisado}%; background: linear-gradient(90deg, #6366f1, #818cf8);" 
                                        aria-valuenow="${progresoAlisado}" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>

                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <span class="small fw-bold text-muted" style="font-size: 0.65rem;"><i class="fas fa-truck me-1"></i> Despachado</span>
                                    <span class="small fw-bold" style="color: #10b981; font-size: 0.7rem;">${progresoEnviado}%</span>
                                </div>
                                <div class="progress progress-modern" style="height: 6px; border-radius: 10px; background: #f1f5f9;">
                                    <div class="progress-bar progress-bar-shimmer ${progresoEnviado > 0 ? 'progress-glow-emerald' : ''}" role="progressbar" style="width: ${progresoEnviado}%; background: linear-gradient(90deg, #10b981, #34d399);" 
                                        aria-valuenow="${progresoEnviado}" aria-valuemin="0" aria-valuemax="100"></div>
                                </div>
                            </div>

                            <!-- 3. Condición para renderizar completamente o NO renderizar la delegación -->
                            ${!isFrimetals ? `
                            <div class="mt-3 pt-3 border-top delegation-section" style="border-top: 1px dashed #e2e8f0 !important; ${pedido.delegado_a || puedeDelegar ? '' : 'display:none;'}">
                                <label class="small fw-bold text-muted mb-2 d-block text-uppercase delegate-label" style="letter-spacing: 0.5px; font-size: 0.6rem; opacity: 0.7;">
                                    ${pedido.delegado_a ? 'Alistador Asignado' : 'Delegar Alistamiento'}
                                </label>
                                
                                <div class="delegation-controls">
                                    ${puedeDelegar ? `
                                    <div class="input-group input-group-sm">
                                        <select class="form-select select-delegar" id="select-delegar-${pedido.id_pedido}" style="border-radius: 6px 0 0 6px; font-size: 0.75rem; border-color: #e2e8f0; background-color: #f8fafc;">
                                            <option value="">Sin asignar</option>
                                            ${(window.AppState.sharedData.responsables || [])
                            .filter(r => {
                                if (!r) return false;
                                const nome = typeof r === 'object' ? (r.nombre || '') : r;
                                const depto = (typeof r === 'object' ? (r.departamento || '') : '').toUpperCase();
                                const esUsuarioActual = nome === (currentUser?.nombre || currentUser?.name);
                                return depto.includes('ALISTAMIENTO') || esUsuarioActual;
                            })
                            .map(r => {
                                const nome = typeof r === 'object' ? (r.nombre || '') : r;
                                return `<option value="${nome}" ${pedido.delegado_a === nome ? 'selected' : ''}>${nome}</option>`;
                            }).join('')}
                                        </select>
                                        <button class="btn btn-primary" onclick="AlmacenModule.delegarPedido('${pedido.id_pedido}')" style="border-radius: 0 6px 6px 0; padding: 0 12px; background: #4f46e5; border: none;">
                                            <i class="fas fa-user-plus" style="font-size: 0.8rem;"></i>
                                        </button>
                                    </div>
                                    ` : `
                                    <div class="text-muted" style="font-size: 0.75rem;">
                                        <i class="fas fa-user-circle me-1"></i> ${pedido.delegado_a || 'Sin asignar'}
                                    </div>
                                    `}
                                </div >
                            </div >
    ` : ''}
                            
                            <!-- Mensaje Solo Lectura para Comercial -->
                            ${currentUser?.rol === 'Comercial' ? '<div class="mt-2 text-center"><span class="badge bg-secondary text-white small" style="opacity:0.8"><i class="fas fa-eye"></i> Solo Lectura</span></div>' : ''}
                        </div>
                    </div>
                </div>
    `;
        });
        html += '</div>';
        container.innerHTML = html;

        // Diagnóstico avanzado Fase 5
        if (this.isTVMode) {
            const cardsCount = container.querySelectorAll('.almacen-card-pro').length;
            const containerStyles = window.getComputedStyle(container);
            console.log(`📊 [Almacen TV Diagnostic] DOM Actualizado: ${cardsCount} tarjetas inyectadas.`);
            console.log(`📊 [Almacen TV Diagnostic] Container Style: display=${containerStyles.display}, opacity=${containerStyles.opacity}, visibility=${containerStyles.visibility}`);

            if (cardsCount > 0 && (containerStyles.display === 'none' || containerStyles.opacity === '0')) {
                console.error('❌ [Almacen TV Diagnostic] ¡ALERTA! Las tarjetas existen pero el contenedor está oculto por CSS.');
            }
        }
    },

    /**
     * Lógica de colores intuitivos según solicitud
     */
    getColorPorEstadoProporcional: function (alisado, enviado) {
        if (enviado === 100) return '#10b981'; // Verde: Todo enviado
        if (enviado > 0) return '#facc15';     // Amarillo: Envío parcial
        if (alisado === 100) return '#6366f1'; // Azul: Listo para enviar
        return '#f97316';                      // Naranja: Pendiente/Faltante
    },

    /**
     * Determina el color del badge según el estado
     */
    getColorEstado: function (estado) {
        if (!estado) return '#94a3b8';
        switch (estado.toUpperCase()) {
            case 'PENDIENTE': return '#64748b';
            case 'EN ALISTAMIENTO': return '#6366f1';
            case 'COMPLETADO': return '#10b981';
            default: return '#94a3b8';
        }
    },

    /**
    * Abrir modal de checklist para un pedido específico
    */
    abrirModal: function (id_pedido) {
        const pedido = this.pedidosPendientes.find(p => p.id_pedido === id_pedido);
        if (!pedido) return;

        this.pedidoActual = JSON.parse(JSON.stringify(pedido)); // Clonar para no afectar el original
        // Reiniciar estado de visualización de ocultos
        this.mostrarOcultos = false;
        const toggle = document.getElementById('toggle-ver-ocultos');
        if (toggle) toggle.checked = false;

        const isMetals = (window.AppState?.user?.division === 'FRIMETALS');
        document.getElementById('modal-alistamiento-titulo').innerText = `${isMetals ? 'Pedido' : 'Alistamiento'}: ${pedido.id_pedido}`;

        // --- Info del pedido: cliente + hora + contador de referencias ---
        const totalRefs = pedido.productos.length;
        const completadas = pedido.productos.filter(p => (parseInt(p.cant_lista) || 0) >= (parseInt(p.cantidad) || 1) || p.no_disponible).length;
        const horaStr = pedido.hora ? ` | 🕐 ${pedido.hora}` : '';

        const clienteEl = document.getElementById('modal-alistamiento-cliente');
        clienteEl.innerHTML = `
        <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
            <span><i class="fas fa-user me-1"></i> ${pedido.cliente}${horaStr}</span>
            <span class="badge ${completadas === totalRefs ? 'bg-success' : 'bg-primary'}" style="font-size: 0.85rem; padding: 6px 12px;">
                <i class="fas fa-cube me-1"></i> ${completadas}/${totalRefs} ${isMetals ? 'items' : 'referencias'}
            </span>
        </div>
    `;

        const observacionesContainer = document.getElementById('modal-alistamiento-observaciones');
        if (observacionesContainer) {
            const obsText = pedido.observaciones || pedido.observacion || '';
            if (obsText && String(obsText).trim()) {
                observacionesContainer.innerHTML = `
                    <div class="alert alert-warning mb-3" style="border-left: 5px solid #f59e0b; background-color: #fffbeb;">
                        <h6 class="fw-bold mb-1" style="color: #92400e;"><i class="fas fa-comment-dots me-2"></i>Observaciones del Vendedor:</h6>
                        <p class="mb-0 text-dark" style="font-size: 0.95rem;">${obsText}</p>
                    </div>
                `;
                observacionesContainer.style.display = 'block';
            } else {
                observacionesContainer.style.display = 'none';
            }
        }

        this.renderizarProductosChecklist();
        this.actualizarProgresoVisual();

        document.getElementById('modalAlistamiento').style.display = 'flex';

        // MODO LECTURA: Si es Comercial, deshabilitar TODO dentro del modal
        const user = window.AppState?.user;
        const isReadOnly = user?.rol === 'Comercial';

        const modalContainer = document.getElementById('modalAlistamiento');
        const inputs = modalContainer.querySelectorAll('input, button:not(.btn-close):not(#btn-cerrar-modal)');

        if (isReadOnly) {
            // Deshabilitar controles de edición
            inputs.forEach(el => {
                // Permitir cerrar y escrolear, bloquear acciones
                if (!el.classList.contains('close') && el.id !== 'modal-cancelar') {
                    el.disabled = true;
                    el.style.opacity = '0.6';
                    el.style.pointerEvents = 'none';
                }
            });
            // Ocultar botón Guardar si existe y mostrar aviso
            const btnGuardar = document.getElementById('btn-guardar-alistamiento');
            if (btnGuardar) btnGuardar.style.display = 'none';
        } else {
            // Restaurar si se reutiliza el modal
            const btnGuardar = document.getElementById('btn-guardar-alistamiento');
            if (btnGuardar) btnGuardar.style.display = 'block';
        }
    },

    /**
     * Renderizar lista de productos con inputs de cantidad
     */
    renderizarProductosChecklist: function () {
        const container = document.getElementById('lista-productos-alistamiento');
        if (!container) return;

        const isMetals = (window.AppState?.user?.division === 'FRIMETALS');
        let html = '';
        let itemsVisibles = 0;

        if (!this.pedidoActual || !this.pedidoActual.productos) {
            container.innerHTML = '<div class="text-center py-4 text-muted">No hay productos en este pedido</div>';
            return;
        }

        this.pedidoActual.productos.forEach((prod, index) => {
            if (prod.cant_lista === undefined) prod.cant_lista = 0;
            // Asegurar booleano
            if (prod.despachado === undefined) prod.despachado = false;
            if (prod.no_disponible === undefined) prod.no_disponible = false;

            // Lógica de Filtrado:
            // - Normal: Ocultar si está 100% alistado y despachado, O si está marcado no_disponible.
            // - Recuperación (mostrarOcultos): Mostrar todo
            const estaCompletamenteDespachado = (prod.cant_lista >= prod.cantidad && prod.despachado) || prod.no_disponible;

            if (estaCompletamenteDespachado && !this.mostrarOcultos) {
                return; // Ocultar en modo normal
            }

            itemsVisibles++;
            const isCompletoAlisado = prod.cant_lista >= prod.cantidad;

            // Lógica de Estado Visual (Quad-estado: pendiente, listo, despachado, no disponible)
            const isReadyToDispatch = isCompletoAlisado && !prod.despachado && !prod.no_disponible;

            // Colores Dinámicos
            let bgClass = '#f8fafc'; // Gris (Default/Disabled)
            let borderClass = '#e2e8f0';
            let iconHtml = '<i class="far fa-circle"></i> PENDIENTE';
            let labelStyle = 'color: #94a3b8;'; // Gray 400

            if (prod.no_disponible) {
                bgClass = '#fef2f2'; // Red 50
                borderClass = '#fca5a5'; // Red 300
                iconHtml = '<i class="fas fa-ban"></i> NO DISPONIBLE';
                labelStyle = 'color: #dc2626;'; // Red 600
            } else if (prod.despachado) {
                bgClass = '#dcfce7'; // Green 100
                borderClass = '#22c55e'; // Green 500
                iconHtml = '<i class="fas fa-check-circle"></i> DESPACHADO';
                labelStyle = 'color: #15803d;'; // Green 700
            } else if (isReadyToDispatch) {
                bgClass = '#fff7ed'; // Orange 50
                borderClass = '#fdba74'; // Orange 300
                iconHtml = '<i class="far fa-clock"></i> LISTO PARA ENVIAR';
                labelStyle = 'color: #ea580c;'; // Orange 600
            }

            // ID único para el checkbox
            const checkId = `check-despacho-${index}`;

            // Progress width for visual feedback within the card background or border
            const progress = prod.no_disponible ? 100 : (prod.cant_lista / prod.cantidad) * 100;
            const progressColor = prod.no_disponible ? '#ef4444' : (progress >= 100 ? '#10b981' : '#6366f1');

            // RBAC: Solo Administración y Jefe Almacén pueden ELIMINAR productos
            const currentUser = window.AppState?.user;
            const currentRole = AuthModule.normalizeRole(currentUser?.rol || currentUser?.role);
            const puedeEliminar = ['ADMINISTRACION', 'ADMINISTRADOR', 'GERENCIA', 'JEFE ALMACEN', 'JEFE ALISTAMIENTO'].includes(currentRole);

            // Estilo de producto no disponible (tachado y gris)
            const ndOverlayStyle = prod.no_disponible ? 'opacity: 0.5; text-decoration: line-through;' : '';
            const ndControlsDisabled = prod.no_disponible ? 'pointer-events: none; opacity: 0.3;' : '';

            html += `
            <div class="product-row-item p-4 mb-4 bg-white shadow-sm border-0 rounded-4 position-relative overflow-hidden" id="row-prod-${index}" 
                 style="transition: all 0.4s ease; transform: scale(1);">
                 
                <!-- Visual Progress Bar at Bottom -->
                <div style="position: absolute; bottom: 0; left: 0; height: 6px; width: ${progress}%; background: ${progressColor}; transition: width 0.3s ease;"></div>

                <div class="d-flex justify-content-between align-items-start mb-3" style="${ndOverlayStyle}">
                    <div style="max-width: 60%;">
                        <span class="badge bg-light text-dark border fw-bold mb-1" style="font-size: 1.6rem; letter-spacing: 1px; padding: 6px 12px;">CÓDIGO: ${prod.codigo}</span>
                        <h6 class="text-muted mb-0 fw-normal" style="font-size: 0.7rem; line-height: 1.2;">${prod.descripcion}</h6>
                    </div>
                    <div class="d-flex align-items-start gap-2">
                        <div class="text-end">
                            <small class="text-uppercase text-muted fw-bold" style="font-size: 0.7rem; letter-spacing: 1px;">Solicitado</small>
                            <div class="fw-bold text-primary" style="font-size: 2.5rem; line-height: 1; letter-spacing: -1px;">${prod.cantidad}</div>
                        </div>
                        ${puedeEliminar && !prod.despachado ? `
                        <button class="btn btn-sm btn-outline-danger rounded-circle btn-eliminar-producto" 
                                onclick="event.stopPropagation(); AlmacenModule.eliminarProducto(${index})"
                                title="Eliminar producto del pedido"
                                style="width: 36px; height: 36px; padding: 0; display: flex; align-items: center; justify-content: center; z-index: 10; position: relative; background: white;">
                            <i class="fas fa-trash" style="font-size: 0.9rem;"></i>
                        </button>
                        ` : ''}
                    </div>
                </div>

                <!-- Big Input Control Area -->
                <div class="d-flex align-items-center justify-content-between bg-light rounded-4 p-2 mb-4 border inner-shadow" style="${ndControlsDisabled}">
                    <button class="btn btn-white shadow-sm rounded-circle d-flex align-items-center justify-content-center border-0" 
                        onclick="AlmacenModule.ajustarCantidad(${index}, -1, 'cant_lista')"
                        style="width: 60px; height: 60px; font-size: 1.5rem; color: #ef4444; transition: transform 0.1s;"
                        onmousedown="this.style.transform='scale(0.95)'" onmouseup="this.style.transform='scale(1)'">
                        <i class="fas fa-minus"></i>
                    </button>
                    
                    <div class="flex-grow-1 px-3 text-center">
                        <small class="text-uppercase text-muted fw-bold d-block mb-1" style="font-size: 0.65rem; letter-spacing: 0.5px;">${isMetals ? 'Alistado' : 'Empacado'}</small>
                        <input type="number" class="form-control border-0 bg-transparent text-center fw-bold p-0" 
                            value="${prod.cant_lista}" 
                            max="${prod.cantidad}"
                            oninput="if(this.value > ${prod.cantidad}) this.value = ${prod.cantidad};"
                            onchange="AlmacenModule.cambiarCantidad(${index}, this.value, 'cant_lista')"
                            style="font-size: 2.5rem; color: #1e293b; height: auto; box-shadow: none;">
                    </div>

                    <button class="btn btn-white shadow-sm rounded-circle d-flex align-items-center justify-content-center border-0" 
                         onclick="AlmacenModule.ajustarCantidad(${index}, 1, 'cant_lista')"
                         style="width: 60px; height: 60px; font-size: 1.5rem; color: #10b981; transition: transform 0.1s;"
                         onmousedown="this.style.transform='scale(0.95)'" onmouseup="this.style.transform='scale(1)'">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>

                <!-- Status Toggle Actions -->
                <div class="d-flex align-items-center justify-content-between gap-2 border-top pt-3">
                    <!-- NO DISPONIBLE button (always enabled) -->
                    <button class="btn btn-sm ${prod.no_disponible ? 'btn-danger' : 'btn-outline-danger'}" 
                            onclick="AlmacenModule.toggleNoDisponible(${index})"
                            style="font-size: 0.7rem; padding: 6px 10px; border-radius: 8px; font-weight: 700; transition: all 0.2s;">
                        <i class="fas fa-ban me-1"></i> ${prod.no_disponible ? 'REVERTIR' : 'NO DISPONIBLE'}
                    </button>

                    <div class="d-flex align-items-center gap-2">
                        <span class="text-muted small fw-bold" style="transition: color 0.3s; ${labelStyle}">
                             ${iconHtml}
                        </span>
                        
                        <div class="form-check form-switch custom-switch-lg">
                            <input class="form-check-input" type="checkbox" role="switch" id="${checkId}"
                                ${prod.despachado ? 'checked' : ''}
                                ${(!isCompletoAlisado || prod.no_disponible) ? 'disabled' : ''}
                                onchange="AlmacenModule.toggleDespacho(${index}, this.checked)"
                                style="width: 3.5rem; height: 2rem; cursor: pointer;">
                        </div>
                    </div>
                </div>
            </div>`;
        });

        if (itemsVisibles === 0) {
            html = `
                <div class="text-center py-5">
                    <i class="fas fa-check-double fa-3x text-success mb-3"></i>
                    <h5 class="text-muted">¡Todo alistado y despachado!</h5>
                    <p class="small text-muted">Este pedido se cerrará automáticamente.</p>
                </div>
            `;
        }

        container.innerHTML = html;
    },

    /**
     * Alternar estado de despacho y guardar automáticamente
     * Si está completo (alistado=cantidad) y se despacha, iniciar fade-out
     */
    toggleDespacho: function (index, checked) {
        this.pedidoActual.productos[index].despachado = checked;

        // Efecto Sonoro/Háptico
        if (window.HapticFeedback) window.HapticFeedback.light();

        this.actualizarProgresoVisual();
        this.actualizarContadorReferencias();

        // Si se marca como despachado y estaba completamente alistado...
        const prod = this.pedidoActual.productos[index];
        if (checked && prod.cant_lista >= prod.cantidad) {
            // Si estamos viendo ocultos, NO ocultar (para permitir gestión), solo actualizar estado visual
            if (this.mostrarOcultos) {
                this.renderizarProductosChecklist();
            } else {
                // Modo normal: fade-out y desaparecer
                setTimeout(() => {
                    const row = document.getElementById(`row-prod-${index}`);
                    if (row) {
                        row.style.transform = 'translateX(50px)';
                        row.style.opacity = '0';
                        setTimeout(() => {
                            this.renderizarProductosChecklist();
                        }, 500);
                    } else {
                        this.renderizarProductosChecklist();
                    }
                }, 500);
            }
        } else {
            this.renderizarProductosChecklist();
        }
    },

    toggleOcultos: function (checked) {
        this.mostrarOcultos = checked;
        this.renderizarProductosChecklist();
    },

    /**
     * Alternar estado NO DISPONIBLE de un producto (reversible)
     */
    toggleNoDisponible: function (index) {
        const prod = this.pedidoActual.productos[index];
        prod.no_disponible = !prod.no_disponible;

        // Si marcamos como no disponible, limpiar despacho
        if (prod.no_disponible) {
            prod.despachado = false;
        }

        if (window.HapticFeedback) window.HapticFeedback.medium();

        this.actualizarProgresoVisual();
        this.actualizarContadorReferencias();

        // Fade-out si se marca no disponible y no estamos en modo ocultos
        if (prod.no_disponible && !this.mostrarOcultos) {
            setTimeout(() => {
                const row = document.getElementById(`row-prod-${index}`);
                if (row) {
                    row.style.transform = 'translateX(-50px)';
                    row.style.opacity = '0';
                    setTimeout(() => {
                        this.renderizarProductosChecklist();
                    }, 500);
                } else {
                    this.renderizarProductosChecklist();
                }
            }, 300);
        } else {
            this.renderizarProductosChecklist();
        }
    },

    /**
     * Actualizar el contador de referencias en el header del modal
     */
    actualizarContadorReferencias: function () {
        if (!this.pedidoActual) return;
        const totalRefs = this.pedidoActual.productos.length;
        const completadas = this.pedidoActual.productos.filter(p => (parseInt(p.cant_lista) || 0) >= (parseInt(p.cantidad) || 1) || p.no_disponible).length;
        const badge = document.querySelector('#modal-alistamiento-cliente .badge');
        if (badge) {
            badge.className = `badge ${completadas === totalRefs ? 'bg-success' : 'bg-primary'}`;
            badge.innerHTML = `<i class="fas fa-cube me-1"></i> ${completadas}/${totalRefs} referencias`;
        }
    },

    /**
     * Ajustar cantidad con botones +/- genérico
     */
    ajustarCantidad: function (index, delta, campo) {
        if (campo === 'cant_enviada') return; // Deprecado, usar toggleDespacho

        let val = parseInt(this.pedidoActual.productos[index][campo]) || 0;
        let max = this.pedidoActual.productos[index].cantidad;

        val = Math.max(0, Math.min(max, val + delta));
        this.cambiarCantidad(index, val, campo);
    },

    /**
     * Cambiar cantidad manualmente genérico
     */
    cambiarCantidad: function (index, valor, campo) {
        if (campo === 'cant_enviada') return; // Deprecado

        let max = this.pedidoActual.productos[index].cantidad;
        let num = parseInt(valor) || 0;

        if (num < 0) num = 0;
        if (num > max) num = max;

        this.pedidoActual.productos[index][campo] = num;

        this.renderizarProductosChecklist();
        this.actualizarProgresoVisual();
        this.actualizarContadorReferencias();

        if (window.HapticFeedback) window.HapticFeedback.light();
    },

    /**
     * Calcular y actualizar barra de progreso visual en el modal
     */
    actualizarProgresoVisual: function () {
        let totalRequerido = 0;
        let totalListo = 0;
        let totalUnidadesDespachadas = 0;

        this.pedidoActual.productos.forEach(p => {
            const cantidad = parseFloat(p.cantidad) || 0;

            // Productos NO DISPONIBLE se tratan como resueltos (100% alistado + despachado)
            if (p.no_disponible) {
                totalRequerido += cantidad;
                totalListo += cantidad;  // Contar como completamente alistado
                totalUnidadesDespachadas += cantidad;  // Contar como despachado
                return;
            }

            totalRequerido += cantidad;
            totalListo += parseInt(p.cant_lista) || 0;

            if (p.despachado) {
                totalUnidadesDespachadas += cantidad;
            }
        });

        const pctAlisado = totalRequerido > 0 ? Math.round((totalListo / totalRequerido) * 100) : 0;
        // AHORA: El progreso enviado se basa en VOLUMEN (unidades) igual que el alistado
        const pctEnviado = totalRequerido > 0 ? Math.round((totalUnidadesDespachadas / totalRequerido) * 100) : 0;

        // Actualizar barras en el modal
        const barAlisado = document.getElementById('modal-alisado-progress');
        const barEnviado = document.getElementById('modal-enviado-progress');

        const isMetals = (window.AppState?.user?.division === 'FRIMETALS');
        if (barAlisado) {
            barAlisado.style.width = `${pctAlisado}% `;
            barAlisado.innerText = `${isMetals ? 'Alistado' : 'En Reparto'}: ${pctAlisado}% `;
            // ... efectos ...
            barAlisado.parentElement.classList.add('progress-modern');
            barAlisado.classList.add('progress-bar-shimmer');
            if (pctAlisado > 0) barAlisado.classList.add('progress-glow-indigo');
            else barAlisado.classList.remove('progress-glow-indigo');
        }
        if (barEnviado) {
            barEnviado.style.width = `${pctEnviado}% `;
            barEnviado.innerText = `Despachado: ${pctEnviado}% `;
            // ... efectos ...
            barEnviado.parentElement.classList.add('progress-modern');
            barEnviado.classList.add('progress-bar-shimmer');
            if (pctEnviado > 0) barEnviado.classList.add('progress-glow-emerald');
            else barEnviado.classList.remove('progress-glow-emerald');
        }
    },

    /**
     * Cerrar modal
     */
    cerrarModal: function () {
        document.getElementById('modalAlistamiento').style.display = 'none';
        this.pedidoActual = null;
    },

    /**
     * Guardar progreso en el backend y persistir en Sheets
     */
    guardarAlistamiento: async function () {
        if (!this.pedidoActual) return;

        let totalReq = 0;
        let totalLis = 0;
        let totalUnidadesDesp = 0;

        this.pedidoActual.productos.forEach(p => {
            const cantidad = parseFloat(p.cantidad) || 0;

            // Productos NO DISPONIBLE cuentan como resueltos
            if (p.no_disponible) {
                totalReq += cantidad;
                totalLis += cantidad;
                totalUnidadesDesp += cantidad;
                return;
            }

            totalReq += cantidad;
            totalLis += parseInt(p.cant_lista) || 0;

            if (p.despachado) {
                totalUnidadesDesp += cantidad;
            }
        });

        const pctLis = totalReq > 0 ? Math.round((totalLis / totalReq) * 100) : 0;
        // Calculo basado en volumen
        const pctEnv = totalReq > 0 ? Math.round((totalUnidadesDesp / totalReq) * 100) : 0;

        // Determinar estado final para la UI
        let estado = 'EN ALISTAMIENTO';
        if (pctEnv === 100 && pctLis === 100) estado = 'DESPACHADO'; // Todo alistado y todo check despachado
        else if (pctEnv > 0) estado = 'DESPACHO PARCIAL';
        else if (pctLis === 100) estado = 'EN REPARTO';
        else if (pctLis === 0) estado = 'PENDIENTE';

        const detalles = this.pedidoActual.productos.map(p => ({
            codigo: p.codigo,
            cant_lista: p.cant_lista,
            despachado: p.despachado,
            no_disponible: p.no_disponible || false
        }));

        try {
            mostrarLoading(true);
            const response = await fetch('/api/pedidos/actualizar-alistamiento', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id_pedido: this.pedidoActual.id_pedido,
                    progreso: `${pctLis}% `,
                    progreso_despacho: `${pctEnv}% `,
                    estado: estado,
                    detalles: detalles
                })
            });

            const data = await response.json();
            if (data.success) {
                // --- INICIO INYECCIÓN: REGISTRO HISTÓRICO EN db_despachos_pedido ---
                const itemsDespachados = this.pedidoActual.productos
                    .filter(p => p.despachado) // Solo ítems que el operario marcó explícitamente como salida/despachados
                    .map(p => ({
                        id_codigo: p.codigo,
                        cantidad_enviada: parseFloat(p.cantidad) || 0 // Usamos la cantidad total del ítem
                    }))
                    .filter(item => item.cantidad_enviada > 0);

                if (itemsDespachados.length > 0) {
                    try {
                        const usuarioLocal = window.AppState?.user?.name || window.AppState?.user?.nombre || sessionStorage.getItem('friparts_user_name') || 'Almacén';
                        
                        // Fuego y olvido (no bloquea el flujo principal si falla)
                        fetch('/api/pedidos/despacho', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                id_pedido: this.pedidoActual.id_pedido,
                                responsable: usuarioLocal,
                                transportadora: "Gestión Interna",
                                guia: "N/A",
                                items: itemsDespachados
                            })
                        }).then(res => res.json())
                          .then(resData => {
                              if(resData.success) {
                                  console.log("✅ Auditoría relacional guardada en db_despachos_pedido.");
                              } else {
                                  console.warn("⚠️ Auditoría de despacho rechazada:", resData.error);
                              }
                          }).catch(err => console.error("❌ Error en fetch de auditoría:", err));
                    } catch (e) {
                        console.error("❌ Error guardando el histórico en db_despachos_pedido", e);
                    }
                }
                // --- FIN INYECCIÓN ---

                mostrarNotificacion('¡Seguimiento actualizado!', 'success');
                this.cerrarModal();
                this.cargarPedidos();
            } else {
                mostrarNotificacion(data.error || 'Error al actualizar', 'error');
            }
        } catch (error) {
            console.error('Error guardando:', error);
            mostrarNotificacion('Error de conexión', 'error');
        } finally {
            mostrarLoading(false);
        }
    },

    /**
     * Eliminar un producto del pedido
     */
    eliminarProducto: async function (index) {
        console.log(`🗑️ Intentando eliminar producto en índice: ${index}`);
        if (!this.pedidoActual) {
            console.error('❌ No hay pedido actual');
            return;
        }

        const producto = this.pedidoActual.productos[index];
        if (!producto) {
            console.error('❌ Producto no encontrado en índice:', index);
            return;
        }

        // Verificar si ya fue despachado
        if (producto.despachado) {
            mostrarNotificacion('No se puede eliminar un producto que ya fue despachado', 'error');
            return;
        }

        // Confirmación
        const confirmar = await this.mostrarConfirmacion(
            'Eliminar Producto',
            `¿Estás seguro de eliminar <b>${producto.codigo} - ${producto.descripcion}</b> del pedido?<br><br>` +
            `<small class="text-muted">La cantidad (${producto.cantidad}) se devolverá automáticamente al inventario.</small>`
        );

        if (!confirmar) return;

        try {
            mostrarLoading(true);
            const response = await fetch('/api/pedidos/eliminar-producto', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id_pedido: this.pedidoActual.id_pedido,
                    codigo: producto.codigo
                })
            });

            const data = await response.json();

            if (data.success) {
                mostrarNotificacion(data.data?.message, 'success');

                // Si el pedido quedó vacío, cerrar modal y recargar
                if (data.data?.pedido_vacio) {
                    this.cerrarModal();
                    this.cargarPedidos();
                } else {
                    // Eliminar del array local y re-renderizar
                    this.pedidoActual.productos.splice(index, 1);
                    this.renderizarProductosChecklist();
                    this.actualizarProgresoVisual();
                }
            } else {
                mostrarNotificacion(data.error || 'Error al eliminar producto', 'error');
            }
        } catch (error) {
            console.error('Error eliminando producto:', error);
            mostrarNotificacion('Error de conexión', 'error');
        } finally {
            mostrarLoading(false);
        }
    },

    /**
     * Alternar el Modo TV (Pantalla completa, Fuentes grandes, Auto-refresco)
     */
    toggleModoTV: function () {
        this.isTVMode = !this.isTVMode;

        if (this.isTVMode) {
            console.log('📺 [Almacen] Activando Modo TV...');

            // Forzar cierre de cualquier overlay de carga (Fase 5 fix)
            if (window.mostrarLoading) window.mostrarLoading(false);
            const overlay = document.getElementById('loading-overlay');
            if (overlay) overlay.style.display = 'none';

            document.body.classList.add('tv-mode');

            // Forzar visibilidad absoluta de la página de Almacén
            const p = document.getElementById('almacen-page');
            if (p) {
                p.classList.add('active');
                p.style.setProperty('display', 'block', 'important');
                p.style.setProperty('z-index', '5000', 'important');
            }

            // Forzar re-renderizado para aplicar estilos de TV y asegurar visibilidad
            this.renderizarTarjetas();

            // Salir con ESCAPE (Protección contra propagación)
            this._escHandler = (e) => {
                if (e.key === 'Escape' && this.isTVMode) {
                    console.log('📺 [Almacen] Tecla ESC detectada, saliendo de TV...');
                    this.toggleModoTV();
                }
            };

            // Timeout pequeño para evitar que el Enter/Click inicial dispare el handler
            setTimeout(() => {
                if (this.isTVMode) window.addEventListener('keydown', this._escHandler);
            }, 500);

            // Intentar poner en pantalla completa si el navegador lo permite
            try {
                if (document.documentElement.requestFullscreen) {
                    document.documentElement.requestFullscreen().catch(e => {
                        console.warn('Pantalla completa rechazada:', e);
                    });
                }
            } catch (e) {
                console.warn('Pantalla completa no soportada o bloqueada:', e);
            }

            // Agregar botón de salida flotante si no existe
            if (!document.querySelector('.btn-exit-tv')) {
                const btnExit = document.createElement('button');
                btnExit.className = 'btn-exit-tv';
                btnExit.innerHTML = '<i class="fas fa-times"></i> Salir de Modo TV';
                btnExit.onclick = () => this.toggleModoTV();
                document.body.appendChild(btnExit);
            }

            // Iniciar intervalo de refresco automático (cada 30 segundos)
            this.tvInterval = setInterval(() => {
                // Solo refrescar si NO hay un modal abierto (para evitar interrumpir al usuario)
                const modal = document.getElementById('modalAlistamiento');
                if (modal && modal.style.display !== 'flex' && modal.style.display !== 'block') {
                    console.log('📺 [Almacen] Refresco automático de Modo TV...');
                    this.cargarPedidos(false);
                }
            }, 30000);

            mostrarNotificacion('Modo TV Activado: Auto-refresco cada 30s', 'info');

            // Iniciar auto-scroll después de un delay de seguridad (10s) para asegurar carga visual
            setTimeout(() => {
                if (this.isTVMode) {
                    console.log('📜 [Almacen] Iniciando Auto-Scroll diferido...');
                    this.iniciarAutoScroll();
                }
            }, 10000);
        } else {
            console.log('📺 [Almacen] Desactivando Modo TV...');
            document.body.classList.remove('tv-mode');

            // CRÍTICO: Limpiar estilos inline !important que se pusieron al activar TV mode.
            // Sin esto, almacen-page queda con display:block !important bloqueando la navegación.
            const pTV = document.getElementById('almacen-page');
            if (pTV) {
                pTV.style.removeProperty('display');
                pTV.style.removeProperty('z-index');
            }

            // Re-renderizar para volver a modo normal (colores, fondos, etc)
            this.renderizarTarjetas();

            // Remover listener de ESC
            if (this._escHandler) {
                window.removeEventListener('keydown', this._escHandler);
            }

            // Salir de pantalla completa
            try {
                if (document.fullscreenElement) {
                    document.exitFullscreen();
                }
            } catch (e) { }

            // Limpiar botón de salida
            document.querySelector('.btn-exit-tv')?.remove();

            // Detener el auto-refresco
            if (this.tvInterval) {
                clearInterval(this.tvInterval);
                this.tvInterval = null;
            }

            // Detener auto-scroll
            this.detenerAutoScroll();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    },

    /**
     * Lógica de Auto-Scroll para Modo TV - Paginación por viewport
     * Scroll simple basado en la altura del viewport, garantiza mostrar todo el contenido
     */
    iniciarAutoScroll: function () {
        this.detenerAutoScroll();
        if (!this.isTVMode) return;

        console.log('📜 [Almacen] Iniciando Auto-Scroll inteligente (Alineado a tarjetas)...');

        const pauseDuration = 8000; // 8 segundos por página (más tiempo para leer)

        const getNextScrollPosition = () => {
            const tvContainer = document.getElementById('almacen-page');
            if (!tvContainer) return 0;
            const viewportHeight = tvContainer.clientHeight || window.innerHeight;
            const currentScrollY = tvContainer.scrollTop;
            const docHeight = tvContainer.scrollHeight;
            const maxScroll = docHeight - viewportHeight;

            if (maxScroll <= 10) return 0;

            // Buscar todas las tarjetas visibles
            const cards = Array.from(document.querySelectorAll('.almacen-card-pro'));
            if (cards.length === 0) return (currentScrollY + viewportHeight * 0.8);

            // Encontrar la primera tarjeta cuyo fondo (bottom) esté fuera del viewport actual
            let nextCard = cards.find(c => {
                const rect = c.getBoundingClientRect();
                // Aumentamos el margen para asegurar que detectamos una fila nueva
                return rect.top > 100; // Si el top de la tarjeta está debajo del header invisible
            });

            if (!nextCard) return maxScroll;

            // Queremos scrollear al TOP de esa tarjeta (ajustando a scroll absoluto del contenedor)
            const nextScrollY = (nextCard.getBoundingClientRect().top + currentScrollY) - 10;

            return Math.min(nextScrollY, maxScroll);
        };

        const scrollToNext = () => {
            if (!this.isTVMode) return;

            // Si hay modal, reintentar pronto
            if (document.getElementById('modalAlistamiento')?.style.display === 'flex') {
                this.scrollTimeout = setTimeout(scrollToNext, 2000);
                return;
            }

            const tvContainer = document.getElementById('almacen-page');
            if (!tvContainer) return;

            const currentY = tvContainer.scrollTop;
            const viewportHeight = tvContainer.clientHeight || window.innerHeight;
            const docHeight = tvContainer.scrollHeight;

            // Si ya estamos muy cerca del final, volver arriba
            // Aumentamos threshold de 50 a 150 para evitar rebotes prematuros
            if (currentY + viewportHeight >= docHeight - 150) {
                console.log('📜 [Almacen] Fin alcanzado, volviendo al inicio...');
                this.scrollTimeout = setTimeout(() => {
                    tvContainer.scrollTo({ top: 0, behavior: 'smooth' });
                    // Aprovechar el reset para recargar datos
                    this.cargarPedidos(false);
                    this.scrollTimeout = setTimeout(scrollToNext, pauseDuration);
                }, pauseDuration);
                return;
            }

            const nextY = getNextScrollPosition();

            if (nextY <= currentY + 10) {
                // Si no avanzamos (ej: no hay más tarjetas), forzar scroll o volver arriba
                console.log('📜 [Almacen] No hay más contenido claro, volviendo arriba.');
                this.scrollTimeout = setTimeout(() => {
                    tvContainer.scrollTo({ top: 0, behavior: 'smooth' });
                    this.scrollTimeout = setTimeout(scrollToNext, pauseDuration);
                }, pauseDuration);
                return;
            }

            console.log(`📜 [Almacen] Navengando a: ${nextY}px`);
            tvContainer.scrollTo({ top: nextY, behavior: 'smooth' });
            this.scrollTimeout = setTimeout(scrollToNext, pauseDuration);
        };

        // Iniciar ciclo
        this.scrollTimeout = setTimeout(scrollToNext, pauseDuration);
    },

    /**
     * Detener el auto-scroll
     */
    detenerAutoScroll: function () {
        if (this.scrollInterval) {
            clearInterval(this.scrollInterval);
            this.scrollInterval = null;
        }
        if (this.scrollTimeout) {
            clearTimeout(this.scrollTimeout);
            this.scrollTimeout = null;
        }
    },

    /**
     * Desactivar procesos del módulo al salir de la página
     */
    desactivar: function () {
        console.log('🔌 [Almacen] Desactivando procesos de fondo...');
        if (this.isTVMode) {
            this.toggleModoTV(); // Apaga el modo TV limpiamente
        }
        this.detenerAutoRefresco();
        this.detenerAutoScroll();
    },

    /**
     * Delegar un pedido a una colaboradora
     */
    delegarPedido: async function (id_pedido) {
        const select = document.getElementById(`select-delegar-${id_pedido}`);
        if (!select) return;

        const colaboradora = select.value;
        const title = colaboradora ? 'Confirmar Asignación' : 'Quitar Asignación';
        const confirmMsg = colaboradora
            ? `¿Deseas asignar el pedido <b>${id_pedido}</b> a <b>${colaboradora}</b>?`
            : `¿Deseas quitar la asignación del pedido <b>${id_pedido}</b>?`;

        const confirmar = await this.mostrarConfirmacion(title, confirmMsg);
        if (!confirmar) return;

        try {
            mostrarLoading(true);
            const response = await fetch('/api/pedidos/delegar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id_pedido: id_pedido,
                    colaboradora: colaboradora
                })
            });

            const data = await response.json();
            if (data.success) {
                mostrarNotificacion(`Pedido ${id_pedido} actualizado`, 'success');
                this.cargarPedidos();
            } else {
                mostrarNotificacion(data.error || 'Error al delegar', 'error');
            }
        } catch (error) {
            console.error('Error delegando:', error);
            mostrarNotificacion('Error de conexión', 'error');
        } finally {
            mostrarLoading(false);
        }
    },

    /**
     * Mostrar confirmación personalizada estilo Pedidos
     */
    mostrarConfirmacion: function (titulo, mensaje) {
        return new Promise((resolve) => {
            const modal = document.createElement('div');
            modal.className = 'modal-overlay';
            modal.style.zIndex = '10001';
            modal.innerHTML = `
    <div class="modal-content confirmation-modal-pro" style = "max-width: 420px; border: none; overflow: hidden; background-color: #ffffff; border-radius: 12px; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1);">
                    <div class="modal-header" style="background: white; border-bottom: 1px solid #e5e7eb; padding: 20px 25px;">
                        <h3 style="color: #111827; margin: 0; font-size: 1.15rem; font-weight: 700;">
                            <i class="fas fa-question-circle" style="color: #6366f1; margin-right: 12px;"></i> ${titulo}
                        </h3>
                    </div>
                    <div class="modal-body" style="padding: 25px; color: #4b5563; font-size: 0.95rem; line-height: 1.5; background-color: #ffffff;">
                        <p style="margin: 0;">${mensaje}</p>
                    </div>
                    <div class="modal-footer" style="background: #f9fafb; padding: 15px 25px; border-top: 1px solid #e5e7eb; display: flex; gap: 10px; justify-content: flex-end;">
                        <button class="btn btn-sm btn-secondary" id="modal-cancelar" style="background: white; border: 1px solid #d1d5db; color: #4b5563; padding: 7px 15px; font-weight: 600; border-radius: 8px;">
                            Cancelar
                        </button>
                        <button class="btn btn-sm btn-primary" id="modal-confirmar" style="background: #4f46e5; color: white; border: none; padding: 7px 20px; font-weight: 600; border-radius: 8px; box-shadow: 0 4px 6px -1px rgba(79, 70, 229, 0.4);">
                            Confirmar
                        </button>
                    </div>
                </div>
    `;

            document.body.appendChild(modal);

            document.getElementById('modal-confirmar').addEventListener('click', () => {
                document.body.removeChild(modal);
                resolve(true);
            });

            document.getElementById('modal-cancelar').addEventListener('click', () => {
                document.body.removeChild(modal);
                resolve(false);
            });

            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    document.body.removeChild(modal);
                    resolve(false);
                }
            });
        });
    },

    /**
     * Iniciar refresco automático cada 60 segundos si la pestaña está activa
     */
    iniciarAutoRefresco: function () {
        if (this.autoRefreshInterval) return;

        console.log('⏱️ [Almacen] Iniciando poll background (15s)...');
        this.autoRefreshInterval = setInterval(() => {
            const paginaActual = window.AppState?.paginaActual;
            const modalAbierto = document.getElementById('modalAlistamiento')?.style.display === 'flex';

            // Verificación robusta: página actual O existencia del contenedor
            // CRÍTICO: Solo usar paginaActual. getElementById siempre devuelve true porque el div
            // existe en el HTML aunque no esté visible, causando recargas en otros módulos.
            const esPaginaAlmacen = paginaActual === 'almacen';

            if (esPaginaAlmacen && !this.isTVMode && !modalAbierto) {
                console.log('🔄 [Almacen] Auto-refresco de fondo...');
                this.cargarPedidos(false);
            }
        }, 15000); // 15s poll
    },

    /**
     * Detener el refresco automático
     */
    detenerAutoRefresco: function () {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
            this.autoRefreshInterval = null;
        }
    },

    /**
     * Abrir modal de configuración de sonido
     */
    abrirConfigSonido: function () {
        const modal = document.getElementById('modalSoundConfig');
        if (!modal) return;

        // Marcar la opción actual
        const currentSound = window.ModuloUX?.getSoundTheme() || 'marimba';
        const options = modal.querySelectorAll('#sound-options button');
        options.forEach(opt => {
            if (opt.dataset.sound === currentSound) {
                opt.classList.add('active', 'bg-primary', 'text-white');
            } else {
                opt.classList.remove('active', 'bg-primary', 'text-white');
            }
        });

        modal.style.display = 'flex';
    },

    /**
     * Probar y seleccionar un sonido
     */
    seleccionarSonido: function (theme) {
        if (window.ModuloUX) {
            window.ModuloUX.setSoundTheme(theme);
            window.ModuloUX.playSound('new_order'); // Probar sonido

            // Actualizar UI del modal
            this.abrirConfigSonido();
        }
    },

    /**
     * Normaliza un NIT/CC al mismo formato base que usa el backend
     * (backend/services/cartera_service.py::_normalizar_identificacion). Distintos
     * pedidos del mismo cliente pueden traer el nit como "NIT 901653115 9" o ya
     * limpio "901653115" segun el flujo que los creo -- sin esto se agrupan como
     * si fueran clientes distintos.
     */
    _normalizarNit: function (valor) {
        if (!valor) return '';
        let texto = String(valor).trim().toUpperCase();
        texto = texto.replace(/^(NIT|CC)\s+/, '');
        texto = texto.replace(/\s+\d+$/, '');
        return texto.trim();
    },

    /**
     * Modal informativo de cartera: solo clientes con pedidos activos visibles
     * en este momento y saldo vencido. No bloquea ni condiciona el despacho.
     */
    abrirModalCartera: function () {
        const modal = document.getElementById('modalCarteraPedidos');
        const body = document.getElementById('cartera-modal-body');
        if (!modal || !body) return;

        const clientesMap = {};
        (this.pedidosVisibles || []).forEach(p => {
            const saldo = parseFloat(p.saldo_vencido_total) || 0;
            const clave = this._normalizarNit(p.nit);
            if (saldo > 0 && clave && clave !== 'S/N') {
                const existente = clientesMap[clave];
                if (!existente || saldo > existente.saldo_vencido_total) {
                    clientesMap[clave] = { nit: p.nit, cliente: p.cliente, saldo_vencido_total: saldo };
                }
            }
        });
        const clientes = Object.values(clientesMap).sort((a, b) => b.saldo_vencido_total - a.saldo_vencido_total);

        if (clientes.length === 0) {
            body.innerHTML = `
                <div class="text-center py-4 text-muted">
                    <i class="fas fa-check-circle fa-2x mb-2 text-success"></i>
                    <p class="mb-0">Ningún cliente con pedidos activos tiene cartera vencida.</p>
                </div>
            `;
        } else {
            const fmt = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
            body.innerHTML = clientes.map((c, idx) => `
                <div class="cartera-cliente-item border rounded-3 mb-2">
                    <div class="d-flex justify-content-between align-items-center p-3" style="cursor:pointer;" onclick="AlmacenModule.toggleDetalleCartera('${c.nit}', ${idx})">
                        <div>
                            <div class="fw-bold" style="color:#1e293b;">${c.cliente}</div>
                            <small class="text-muted">${c.nit}</small>
                        </div>
                        <div class="d-flex align-items-center gap-2">
                            <span class="fw-bold text-danger">${fmt.format(c.saldo_vencido_total)}</span>
                            <i class="fas fa-chevron-down" id="chevron-cartera-${idx}"></i>
                        </div>
                    </div>
                    <div id="detalle-cartera-${idx}" class="px-3 pb-3" style="display:none;"></div>
                </div>
            `).join('');
        }

        modal.style.display = 'flex';
    },

    /**
     * Colapsa/expande el detalle de un pedido dentro del modal "Ver
     * Empacado" (plan 2026-09-01, 2da correccion): todos los pedidos
     * arrancan colapsados para que el scroll recorra solo encabezados y no
     * el detalle completo de cada uno.
     */
    toggleDetalleEmpacado: function (idPedido) {
        const detalle = document.getElementById(`empacado-detalle-${idPedido}`);
        const chevron = document.getElementById(`empacado-chevron-${idPedido}`);
        if (!detalle) return;
        const abierto = detalle.style.display !== 'none';
        detalle.style.display = abierto ? 'none' : 'block';
        if (chevron) chevron.style.transform = abierto ? 'rotate(0deg)' : 'rotate(90deg)';
    },

    /**
     * Vista rápida de lo ya empacado/alistado (pedido de almacén 2026-09-02,
     * corregido el mismo día): NO tiene nada que ver con el módulo de
     * producción de Empaque -- usa el mismo dato que ya se ve dentro del
     * checklist de cada pedido (pedido.productos[].cant_lista, la cantidad
     * que ya se marcó como "Empacado"/"Alistado" ahí), pero de TODOS los
     * pedidos pendientes a la vez, para no tener que entrar pedido por
     * pedido a revisarlo. Reutiliza this.pedidosVisibles (ya calculado por
     * renderizarTarjetas con el mismo filtro de "no completados" que se ve
     * en pantalla) -- no pega a ningún endpoint nuevo. Cada pedido arranca
     * colapsado (ver toggleDetalleEmpacado) para minimizar el scroll.
     */
    abrirModalEmpacado: async function () {
        const modal = document.getElementById('modalEmpacadoAlmacen');
        const body = document.getElementById('empacado-almacen-modal-body');
        if (!modal || !body) return;

        modal.style.display = 'flex';
        body.innerHTML = '<div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin fa-2x"></i></div>';

        try {
            await this.cargarPedidos(false);

            const isMetals = (window.AppState?.user?.division === 'FRIMETALS');
            const etiqueta = isMetals ? 'Alistado' : 'Empacado';
            const pedidos = (this.pedidosVisibles && this.pedidosVisibles.length)
                ? this.pedidosVisibles
                : (this.pedidosPendientes || []);

            if (!pedidos.length) {
                body.innerHTML = `
                    <div class="text-center py-4 text-muted">
                        <i class="fas fa-box-open fa-2x mb-2" style="opacity:0.3;"></i>
                        <p class="mb-0">No hay pedidos pendientes.</p>
                    </div>`;
                return;
            }

            const bloques = pedidos.map(pedido => {
                const productos = pedido.productos || [];
                if (!productos.length) return '';

                const filas = productos.map(p => {
                    const solicitado = parseInt(p.cantidad) || 0;
                    const empacado = parseInt(p.cant_lista) || 0;
                    const pct = solicitado > 0 ? Math.min(100, Math.round((empacado / solicitado) * 100)) : 0;
                    const completo = p.no_disponible || empacado >= solicitado;
                    const desc = (p.descripcion || '').slice(0, 32);
                    return `
                        <tr>
                            <td class="small" style="overflow: hidden;">
                                <span class="fw-bold d-block text-truncate">${p.codigo}</span>
                                <span class="text-muted text-truncate d-block" style="font-size: 0.68rem;">${desc}</span>
                            </td>
                            <td class="small text-center text-muted">${solicitado}</td>
                            <td class="small text-center"><span class="badge ${completo ? 'bg-success' : 'bg-secondary'}">${p.no_disponible ? 'N/D' : empacado}</span></td>
                            <td class="small text-center text-muted">${p.no_disponible ? '-' : pct + '%'}</td>
                        </tr>`;
                }).join('');

                const bodyId = `empacado-detalle-${pedido.id_pedido}`;
                const chevronId = `empacado-chevron-${pedido.id_pedido}`;
                return `
                    <div class="mb-2 border rounded-3 overflow-hidden">
                        <div class="d-flex justify-content-between align-items-center px-3 py-2" style="background:#f8fafc; cursor: pointer;"
                             onclick="AlmacenModule.toggleDetalleEmpacado('${pedido.id_pedido}')">
                            <span class="fw-bold small">
                                <i class="fas fa-chevron-right me-2 text-muted" id="${chevronId}" style="transition: transform 0.15s; font-size: 0.7rem;"></i>
                                <i class="fas fa-hashtag me-1 text-muted"></i>${pedido.id_pedido} <span class="text-muted fw-normal">- ${pedido.cliente}</span>
                            </span>
                            <span class="badge bg-primary">${parseInt(pedido.progreso) || 0}%</span>
                        </div>
                        <div id="${bodyId}" style="display: none;">
                            <table class="table table-compact table-sm table-hover align-middle mb-0" style="table-layout: fixed; width: 100%;">
                                <colgroup>
                                    <col style="width: 52%;">
                                    <col style="width: 16%;">
                                    <col style="width: 16%;">
                                    <col style="width: 16%;">
                                </colgroup>
                                <thead class="table-light">
                                    <tr><th class="small">Referencia</th><th class="small text-center">Sol.</th><th class="small text-center">${etiqueta}</th><th class="small text-center">%</th></tr>
                                </thead>
                                <tbody>${filas}</tbody>
                            </table>
                        </div>
                    </div>`;
            }).join('');

            body.innerHTML = bloques || `
                <div class="text-center py-4 text-muted">
                    <i class="fas fa-box-open fa-2x mb-2" style="opacity:0.3;"></i>
                    <p class="mb-0">No hay pedidos pendientes.</p>
                </div>`;
        } catch (e) {
            console.error('[Almacen] Error cargando empacado:', e);
            body.innerHTML = '<div class="text-center text-danger py-4">No se pudo cargar la información de empacado.</div>';
        }
    },

    toggleDetalleCartera: async function (nit, idx) {
        const detalleDiv = document.getElementById(`detalle-cartera-${idx}`);
        const chevron = document.getElementById(`chevron-cartera-${idx}`);
        if (!detalleDiv) return;

        const abierto = detalleDiv.style.display !== 'none';
        if (abierto) {
            detalleDiv.style.display = 'none';
            if (chevron) chevron.className = 'fas fa-chevron-down';
            return;
        }

        detalleDiv.style.display = 'block';
        if (chevron) chevron.className = 'fas fa-chevron-up';

        if (detalleDiv.dataset.loaded === 'true') return;

        detalleDiv.innerHTML = '<div class="text-center py-2 text-muted"><i class="fas fa-spinner fa-spin"></i> Cargando facturas...</div>';

        try {
            const res = await fetch(`/api/cartera/cliente/${encodeURIComponent(nit)}`);
            const data = await res.json();

            if (!data.success || !data.facturas || data.facturas.length === 0) {
                detalleDiv.innerHTML = '<div class="text-muted small">No se encontraron facturas para este cliente.</div>';
                return;
            }

            const fmt = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
            const celda = 'padding: 10px 14px; white-space: nowrap;';
            const celdaNum = celda + ' border-left: 1px solid #edf2f7;';
            const filas = data.facturas.map(f => `
                <tr>
                    <td style="${celda}">${f.documento}</td>
                    <td style="${celda}">${f.fecha_emision || 'N/A'}</td>
                    <td style="${celda}">${f.fecha_vencimiento || 'N/A'}</td>
                    <td class="text-center" style="${celdaNum}">${f.dias_mora ?? 'N/A'}</td>
                    <td class="text-end" style="${celdaNum}">${fmt.format(f.saldo)}</td>
                </tr>
            `).join('');

            detalleDiv.innerHTML = `
                <div class="table-responsive">
                    <table class="table table-sm table-striped mb-0" style="table-layout: auto; width: auto;">
                        <thead>
                            <tr>
                                <th style="${celda}">Documento</th>
                                <th style="${celda}">Emisión</th>
                                <th style="${celda}">Vence</th>
                                <th class="text-center" style="${celdaNum}">Días Mora</th>
                                <th class="text-end" style="${celdaNum}">Saldo</th>
                            </tr>
                        </thead>
                        <tbody>${filas}</tbody>
                    </table>
                </div>
            `;
            detalleDiv.dataset.loaded = 'true';
        } catch (error) {
            console.error('❌ Error cargando detalle de cartera:', error);
            detalleDiv.innerHTML = '<div class="text-danger small">Error al cargar el detalle.</div>';
        }
    },

    irACarteraCompleta: function () {
        const modal = document.getElementById('modalCarteraPedidos');
        if (modal) modal.style.display = 'none';
        if (window.cargarPagina) window.cargarPagina('cartera');
    }
};

// Exportación global para app.js
window.AlmacenModule = AlmacenModule;
window.initAlmacen = () => AlmacenModule.inicializar();
