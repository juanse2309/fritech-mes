/**
 * Modulo BI Dashboard Profesional
 */
let isInitialized = false;
let isFetching = false;
let searchListenerAttached = false;
let tableRowCache = {};

window.ModuloDashboard = (function () {
    let chartOperadores = null;
    let chartMaquinas = null;
    let chartTendencia = null;
    let chartPNC = null;
    let chartPNCPareto = null;
    let selectedOperators = []; // Para comparativa cara a cara

    let chartPulidoRankingInst = null;
    let chartPulidoMixRefInst = null;
    let cachePulidoProfundo = null; // Ultimo 'pulido_profundo' recibido (para re-render del mix sin refetch)
    let chartMensualInst = null;
    let chartRendimientoMensualNewInst = null;
    let inc_unidades_original = [];
    let inc_dinero_original = [];
    let chartTopMejoresInst = null;
    let chartTopPeoresInst = null;
    let lastJefaturaData = null;
    let inc_consolidado_original = [];
    let currentIncSortMode = localStorage.getItem('db_toggle_mode') || 'units'; // 'units' or 'money'
    let currentPncMode = localStorage.getItem('db_pnc_toggle_mode') || 'money'; // 'units' or 'money' — Desglose de Scrap/PNC (independiente del toggle de Jefatura). Default 'money': es la capacidad nueva que se busca destacar.
    let cacheAnalyticsPulido = null; // Cache para Analiticas de Pulido 
    window.ventasCache = {}; // Caché global de sesión para drill-down mensual (Juan Sebastian Request)
    
    // Cache de 1 minuto para KPIs de SQL
    let lastFetchTime = 0;
    let currentBackorderDetail = [];
    let cachedStats = null;
    let cachedJefatura = null;
    let lastCachedParams = null;
    const CACHE_DURATION = 60 * 1000; // 60 segundos

    // Helper: Debounce function for performance
    const debounce = (func, wait) => {
        let timeout;
        return function (...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    };

    const colores = {
        azul: ['rgba(59, 130, 246, 0.8)', 'rgba(96, 165, 250, 0.8)', 'rgba(147, 197, 253, 0.8)'],
        verde: ['#10b981', '#34d399', '#6ee7b7'],
        naranja: ['#f59e0b', '#fbbf24', '#fcd34d'],
        peligro: ['#ef4444', '#f87171', '#fca5a5']
    };

    let currentInsights = [];
    let insightIndex = 0;
    let insightInterval = null;

    // --- Loading overlay helper ---
    function showLoading(show) {
        let overlay = document.getElementById('db-loading-overlay');
        if (!overlay && show) {
            overlay = document.createElement('div');
            overlay.id = 'db-loading-overlay';
            overlay.innerHTML = `
                <div style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(255,255,255,0.85);
                    z-index:9999;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(5px);">
                    <div style="text-align:center;padding:2.5rem;background:white;border-radius:20px;box-shadow:0 25px 50px -12px rgba(0,0,0,0.15);max-width:450px;width:90%;">
                        <div class="mb-4">
                            <i class="fas fa-microchip fa-spin" style="font-size:3rem;color:#3b82f6;"></i>
                        </div>
                        <p style="margin-top:1rem;font-weight:700;color:#1e293b;font-size:1.1rem;">Analizando registros de producción y costos...</p>
                        <p style="font-size:0.9rem;color:#64748b;margin-bottom:1.5rem;">(Esto puede tomar unos segundos)</p>
                        <div class="progress" style="height:6px;border-radius:10px;background:#f1f5f9;">
                            <div class="progress-bar progress-bar-striped progress-bar-animated" style="width:100%;border-radius:10px;"></div>
                        </div>
                        <p style="font-size:0.75rem;color:#94a3b8;margin-top:1rem;font-style:italic;">Sincronizando bases de datos industriales en tiempo real</p>
                    </div>
                </div>`;
            document.body.appendChild(overlay);
        }
        if (overlay) overlay.style.display = show ? 'block' : 'none';
    }

    // --- Banner de error global: usado por cualquier fetch que falle con 401/403/500 ---
    function mostrarErrorBanner(message) {
        let errBanner = document.getElementById('dashboard-error-banner');
        if (!errBanner) {
            errBanner = document.createElement('div');
            errBanner.id = 'dashboard-error-banner';
            errBanner.className = 'alert alert-danger mx-3 mt-3 shadow-sm';
            const container = document.querySelector('.main-content .container-fluid') || document.body;
            container.prepend(errBanner);
        }
        errBanner.innerHTML = `<i class="fas fa-exclamation-triangle me-2"></i> <strong>Error crítico:</strong> ${message}`;
        errBanner.style.display = 'block';
    }

    // --- Overlay de error puntual sobre un <canvas> de Chart.js que falló al renderizar ---
    function mostrarErrorEnCanvas(canvasEl, mensaje = 'No se pudo renderizar el gráfico.') {
        if (!canvasEl || !canvasEl.parentElement) return;
        const parent = canvasEl.parentElement;
        let errDiv = parent.querySelector('.chart-error-overlay');
        if (!errDiv) {
            errDiv = document.createElement('div');
            errDiv.className = 'chart-error-overlay text-center text-danger small py-3';
            parent.appendChild(errDiv);
        }
        errDiv.innerHTML = `<i class="fas fa-triangle-exclamation me-1"></i>${mensaje}`;
    }

    function limpiarErrorEnCanvas(canvasEl) {
        if (!canvasEl || !canvasEl.parentElement) return;
        canvasEl.parentElement.querySelector('.chart-error-overlay')?.remove();
    }

    // --- e8 auditoría: aviso pasivo si algún agente WO (cartera/clientes/comercial)
    // lleva demasiado tiempo sin reportar una sincronización exitosa. Los agentes
    // corren desatendidos cada 15 min en la máquina de planta -- más de 24h sin
    // éxito es señal de un fallo persistente (SQL Server caído, token vencido, etc.)
    // que de otro modo nadie notaría hasta que alguien preguntara por qué los datos
    // están desactualizados. Mismo umbral que la advertencia de antigüedad del stock WO.
    const UMBRAL_SYNC_STALE_HORAS = 24;

    async function verificarEstadoSincronizacionesWO() {
        try {
            const res = await fetch('/api/wo/estado_sincronizaciones', {
                headers: construirAuthHeaders({ 'Accept': 'application/json' }),
                credentials: 'include'
            });
            if (!res.ok) return; // silencioso: sin permisos o error puntual, no es crítico mostrarlo
            const data = await res.json();
            if (!data.success) return;

            const NOMBRES = { cartera: 'Cartera', clientes: 'Clientes', comercial: 'Ventas/Pedidos' };
            const stale = Object.entries(NOMBRES)
                .filter(([clave]) => {
                    const info = data[clave];
                    return !info || info.antiguedad_horas === null || info.antiguedad_horas > UMBRAL_SYNC_STALE_HORAS;
                })
                .map(([clave, nombre]) => {
                    const horas = data[clave]?.antiguedad_horas;
                    return horas == null ? `${nombre} (sin registro)` : `${nombre} (hace ${Math.round(horas)}h)`;
                });

            let banner = document.getElementById('dashboard-sync-stale-banner');
            if (stale.length === 0) {
                if (banner) banner.remove();
                return;
            }

            if (!banner) {
                banner = document.createElement('div');
                banner.id = 'dashboard-sync-stale-banner';
                banner.className = 'alert alert-warning mx-3 mt-3 shadow-sm';
                const container = document.querySelector('.main-content .container-fluid') || document.body;
                container.prepend(banner);
            }
            banner.innerHTML = `<i class="fas fa-plug-circle-exclamation me-2"></i> <strong>Agentes de sincronización desactualizados:</strong> ${stale.join(', ')}. Verifica el agente en la máquina de planta.`;
        } catch (e) {
            console.warn('[Dashboard] No se pudo verificar el estado de sincronizaciones WO:', e);
        }
    }

    function construirAuthHeaders(extraHeaders = {}) {
        const pwaToken = localStorage.getItem('pwa_token');
        const headers = { ...extraHeaders };
        if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;
        return headers;
    }

    async function cargarDatos(nocache = false) {
        if (isFetching) {
            console.warn("⏳ Petición cargarDatos en curso, ignorando duplicada.");
            return;
        }

        try {
            isFetching = true;
            showLoading(true);
            console.log("📡 Cargando datos del dashboard...");
            const desde = document.getElementById('db-fecha-desde')?.value;
            const hasta = document.getElementById('db-fecha-hasta')?.value;
            const currentParams = `${desde}|${hasta}`;

            // Lógica de Cache (1 minuto)
            const now = Date.now();
            if (!nocache && cachedStats && lastCachedParams === currentParams && (now - lastFetchTime < CACHE_DURATION)) {
                console.log("♻️ Usando datos cacheados (Cache < 1min)");
                renderizarTodo(cachedStats, cachedJefatura);
                return;
            }

            let url = '/api/dashboard/stats';
            const params = new URLSearchParams();
            if (desde) params.append('desde', desde);
            if (hasta) params.append('hasta', hasta);
            if (nocache) params.append('nocache', '1');
            if (params.toString()) url += `?${params.toString()}`;

            const pwaToken = localStorage.getItem('pwa_token');
            const getAuthHeaders = (extraHeaders = {}) => {
                const headers = { ...extraHeaders };
                if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;
                return headers;
            };

            const responseStats = fetch(url, {
                headers: getAuthHeaders(),
                credentials: 'include'
            }).then(async res => {
                if (res.status === 403) {
                    console.warn('⚠️ Sin permisos (403) para /api/dashboard/stats. Se omite el panel operativo.');
                    return null;
                }
                if (!res.ok) throw new Error(`Error al obtener stats (HTTP ${res.status})`);
                return res.json();
            }).catch(err => {
                // fetch() rechaza con TypeError ante fallos de red (offline, DNS, CORS) --
                // se distingue así de un Error intencional lanzado arriba por un HTTP real (ej. 500),
                // que sí debe seguir propagándose para no ocultar fallos genuinos del backend.
                if (err instanceof TypeError) {
                    console.error('❌ Error de red obteniendo /api/dashboard/stats:', err);
                    return null;
                }
                throw err;
            });

            // RBAC: Solo cargar datos de Jefatura / Finanzas si es Admin, Gerencia o Comercial
            let isAdminOrManagement = false;
            let resultJefatura = null;

            if (window.AppState && window.AppState.user && window.AppState.user.rol) {
                const rol = window.AppState.user.rol.toUpperCase();
                isAdminOrManagement = ['ADMIN', 'ADMINISTRACION', 'ADMINISTRACIÓN', 'ADMINISTRADOR', 'GERENCIA', 'COMERCIAL'].includes(rol);
            }

            if (isAdminOrManagement) {
                verificarEstadoSincronizacionesWO(); // fire-and-forget: no bloquea el render principal del dashboard

                const adminParams = new URLSearchParams();
                if (desde) adminParams.append('start', desde);
                if (hasta) adminParams.append('end', hasta);
                if (nocache) adminParams.append('nocache', '1');

                // Timeout de 30s para evitar que la página se congele
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 30000);

                const jefUrl = `/api/admin/dashboard?${adminParams.toString()}`;
                console.log("📡 Consultando Jefatura:", jefUrl);

                try {
                    const reqJefatura = fetch(jefUrl, {
                        headers: getAuthHeaders({ 'Accept': 'application/json' }),
                        credentials: 'include',
                        signal: controller.signal
                    }).then(async res => {
                        if (res.status === 403) {
                            console.warn('⚠️ Sin permisos (403) para /api/admin/dashboard. Se omite el panel de Jefatura.');
                            return null;
                        }
                        if (!res.ok) {
                            const msg = res.status === 401 ? 'Sesión expirada. Por favor recarga la página e inicia sesión.'
                                      : `Error del servidor al obtener datos de Jefatura (${res.status})`;
                            throw new Error(msg);
                        }
                        return res.json();
                    }).catch(err => {
                        if (err instanceof TypeError) {
                            console.error('❌ Error de red obteniendo /api/admin/dashboard:', err);
                            return null;
                        }
                        throw err;
                    });

                    const ENABLE_CARTERA = false;
                    const reqCartera = ENABLE_CARTERA ? fetch(`/api/dashboard/cartera`, {
                        headers: getAuthHeaders({ 'Accept': 'application/json' }),
                        credentials: 'include',
                        signal: controller.signal
                    }).then(res => res.ok ? res.json() : null).catch(() => null) : Promise.resolve(null);

                    const [result, jefaturaData, carteraData] = await Promise.all([responseStats, reqJefatura, reqCartera]);
                    clearTimeout(timeoutId);
                    console.log("📊 Datos recibidos (Admin):", { result, jefaturaData, carteraData });

                    const statsOk = !!(result && result.status === 'success');
                    if (statsOk || jefaturaData) {
                        // Actualizar cache (stats y Jefatura se cachean de forma independiente)
                        if (statsOk) {
                            cachedStats = result.data;
                            lastFetchTime = Date.now();
                            lastCachedParams = currentParams;
                        }
                        cachedJefatura = jefaturaData;

                        // Paneles operativos (Inyección/Máquinas/Scrap) y Jefatura se pintan
                        // cada uno por separado: renderizarTodo tolera que cualquiera de los
                        // dos venga null (403/permiso o error de red) sin bloquear al otro.
                        renderizarTodo(statsOk ? result.data : null, jefaturaData, carteraData);
                    } else {
                        console.warn("⚠️ Ni stats ni Jefatura disponibles (403/permiso o error de red). Nada que renderizar.");
                    }
                } catch (fetchErr) {
                    clearTimeout(timeoutId);
                    if (fetchErr.name === 'AbortError') {
                        console.warn("⏰ Timeout en /api/admin/dashboard - mostrando stats sin datos de Jefatura");
                        const result = await responseStats;
                        if (result && result.status === 'success') {
                            renderizarTodo(result.data, null, null);
                        }
                    } else {
                        throw fetchErr;
                    }
                }
            } else {
                const result = await responseStats;
                if (result && result.status === 'success') {
                    // Actualizar cache
                    cachedStats = result.data;
                    cachedJefatura = null;
                    lastFetchTime = Date.now();
                    lastCachedParams = currentParams;

                    renderizarTodo(result.data, null, null);
                } else {
                    console.warn("⚠️ No hay datos operativos para renderizar (403/permiso o error de red en /api/dashboard/stats).");
                }
            }

            // Si llegamos aquí con datos de Jefatura (admin), también cacheamos
            if (isAdminOrManagement && cachedStats) {
                // El cache ya se actualizó en el flujo Promise.all anterior si entró por ahí
            }

            // Initialize Bootstrap tooltips if they exist. cargarDatos() puede correr
            // varias veces sobre los mismos nodos (refresco, cambio de filtro); reusar
            // la instancia existente evita el error "more than one instance per element".
            const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
            const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => bootstrap.Tooltip.getInstance(tooltipTriggerEl) || new bootstrap.Tooltip(tooltipTriggerEl));

        } catch (error) {
            console.error("Error en Dashboard BI / Jefatura:", error);
            mostrarErrorBanner(error.message);
        } finally {
            isFetching = false;
            showLoading(false);
        }
    }

    function renderizarTodo(data, jefaturaData, carteraData = null) {
        console.log("🚀 Iniciando renderizado de componentes...", { data, jefaturaData, carteraData });
        window.lastDashboardData = data;

        // Limpiar cache de filas al renderizar datos nuevos
        tableRowCache = {};

        // tieneDatosOperativos: gobierna KPIs/Inyección/Máquinas/Scrap/Pulido, todos
        // provenientes de /api/dashboard/stats. jefaturaData (financiero) es independiente:
        // cada bloque de abajo se pinta por su cuenta sin depender del éxito del otro.
        const tieneDatosOperativos = !!(data && data.kpis);
        if (!tieneDatosOperativos) {
            console.warn("⚠️ Sin datos operativos (data.kpis ausente): se omiten KPIs/Inyección/Máquinas/Scrap/Pulido.", data);
            if (!jefaturaData) {
                console.error("❌ Tampoco hay datos de Jefatura. Nada que renderizar.");
                return;
            }
        }

        const safeSetText = (id, text) => {
            try {
                const el = document.getElementById(id);
                if (el) el.textContent = text;
            } catch (e) {
                console.warn(`⚠️ Error asignando texto a #${id}:`, e);
            }
        };

        try {
            // 1. KPIs Principales (requieren datos operativos de /api/dashboard/stats)
            let formatCOP_PNC = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(0);
            if (tieneDatosOperativos) {
                safeSetText('total-iny-piezas', `${(data.kpis.inyeccion_ok || 0).toLocaleString()} Pz`);

                const valorPerdida = Number(data.kpis.perdida_calidad_dinero) || 0;
                formatCOP_PNC = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(valorPerdida);
                safeSetText('perdida-calidad-val', formatCOP_PNC);
                safeSetText('perdida-calidad-global', formatCOP_PNC);
                safeSetText('pnc-dinero-val', formatCOP_PNC);

                safeSetText('pnc-global-val', (data.kpis.scrap_total || 0).toLocaleString());
                safeSetText('pnc-porcentaje-val', (data.kpis.pct_pnc_total || 0).toFixed(2));
            }

            // 2. Insights IA Avanzados
            let smartInsights = [];

            // Insight Basico Fechas
            smartInsights.push(`Analizando datos clave de producción y ventas: <strong>${data?.rango?.desde || ''}</strong> al <strong>${data?.rango?.hasta || ''}</strong>.`);

            if (jefaturaData && jefaturaData.data) {
                const jd = jefaturaData.data;
                // Filtrar el peor cliente en incumplimiento
                if (jd.incumplimiento_dinero && jd.incumplimiento_dinero.length > 0) {
                    const topInc = [...jd.incumplimiento_dinero].sort((a, b) => b.dinero_perdido - a.dinero_perdido)[0];
                    if (topInc && topInc.dinero_perdido > 0) {
                        smartInsights.push(`<span class="text-danger"><i class="fas fa-exclamation-circle"></i> <strong>Atención Comercial (Histórico General):</strong></span> El mayor impacto global es con <b>${topInc.cliente}</b> perdiendo <b class="text-danger">${new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(topInc.dinero_perdido)}</b> por faltantes del producto <i>${topInc.producto}</i>.`);
                    }
                }

                // Productos Estrella vs Riesgo
                if (jd.top_productos_dinero && jd.top_productos_dinero.length > 0) {
                    const topVendido = jd.top_productos_dinero[0];
                    smartInsights.push(`<i class="fas fa-star text-warning"></i> <strong>Líder de Ventas (Histórico General):</strong> La referencia <b>${topVendido.producto}</b> lidera los ingresos con <b class="text-success">${new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(topVendido.ventas_dinero)}</b> facturados.`);
                }
            }

            // Insight Calidad (Scrap)
            if (tieneDatosOperativos && data.kpis.perdida_calidad_dinero > 0) {
                smartInsights.push(`<i class="fas fa-recycle text-secondary"></i> <strong>Alerta de Calidad:</strong> El acumulado de piezas rechazadas representa un costo hundido estimado en <b class="text-danger">${formatCOP_PNC}</b>. Revise los procesos de Pulido e Inyección.`);
            }

            // Avisos de Costos Faltantes
            if (tieneDatosOperativos && data.kpis.faltan_costos_pnc && data.kpis.faltan_costos_pnc.length > 0) {
                smartInsights.push(`<span class="text-warning"><i class="fas fa-exclamation-triangle"></i> <strong>Faltan Costos:</strong></span> Hay ${data.kpis.faltan_costos_pnc.length} referencias reportando scrap que no tienen costo asignado en DB_COSTOS. La Pérdida por Calidad mostrada es menor a la real.`);
            }

            // Darle formato visual ejecutivo al Reporte del Bot de Planta
            const botInsightsFormatted = (data?.insights_ia || []).map(item => {
                let tag = '';
                let text = item;
                if (typeof item === 'string' && item.includes('|')) {
                    const parts = item.split('|');
                    tag = parts[0];
                    text = parts[1];
                }

                if (tag === 'VOLUMEN_CONSOLIDADO' || text.includes('Volumen Consolidado')) {
                    return `<i class="fas fa-boxes text-primary me-2"></i> <strong class="text-primary">Volumen Consolidado:</strong> ${text.replace('Volumen Consolidado:', '')}`;
                }
                if (tag === 'DESVIACION' || text.includes('Brecha') || text.includes('Flujo')) {
                    return `<i class="fas fa-random text-warning me-2"></i> <strong class="text-warning">Desviaciones & Flujo:</strong> ${text.replace(/^(Brecha de Producción:|Flujo Inverso:|Flujo Balanceado:)/, '<b>$1</b>')}`;
                }
                if (tag === 'ALERTA_SCRAP' || text.includes('Impacto Financiero')) {
                    return `<i class="fas fa-exclamation-triangle text-danger me-2"></i> <strong class="text-danger">Mermas & Calidad:</strong> ${text.replace('Impacto Financiero de Mermas:', '')}`;
                }
                if (tag === 'ALERTA_STOCK' || text.includes('Alerta de Inventario')) {
                    return `<i class="fas fa-warehouse text-warning me-2"></i> <strong class="text-warning">Inventario Crítico:</strong> ${text.replace('Alerta de Inventario:', '')}`;
                }
                if (tag === 'LIDER_INY' || text.includes('Líder Inyección')) {
                    return `<i class="fas fa-industry text-info me-2"></i> <strong class="text-info">Top Inyección:</strong> ${text.replace('Líder Inyección:', '')}`;
                }
                if (tag === 'LIDER_PUL' || text.includes('Líder Pulido')) {
                    return `<i class="fas fa-gem text-success me-2"></i> <strong class="text-success">Top Pulido:</strong> ${text.replace('Líder Pulido:', '')}`;
                }
                return `<i class="fas fa-info-circle text-muted me-2"></i> ${text}`;
            });

            currentInsights = [
                ...smartInsights,
                ...botInsightsFormatted
            ];
            iniciarCarrouselBot();

            // 3. Gráficos de Producción (Inyección) -- requiere datos operativos
            try {
                if (tieneDatosOperativos && data.rankings?.inyeccion_ops) {
                    renderChartInyeccion(data.rankings.inyeccion_ops.slice(0, 10));
                    const btnVerTodosIny = document.getElementById('btn-ver-todos-iny');
                    if (btnVerTodosIny) {
                        btnVerTodosIny.onclick = () => mostrarModalTodosInyeccion(data.rankings.inyeccion_ops);
                    }
                }

                if (tieneDatosOperativos && data.maquinas) {
                    const maquinasArr = Array.isArray(data.maquinas) ? data.maquinas : Object.entries(data.maquinas || {}).map(([k, v]) => ({maquina: k, valor: v}));
                    if (maquinasArr.length > 0) renderChartMaquinas(maquinasArr);
                }
            } catch (errIny) {
                console.error("⚠️ Error defensivo renderizando Inyección:", errIny);
            }

            // 4. Scrap -- requiere datos operativos
            try {
                if (tieneDatosOperativos) {
                    if (data.tendencia && Array.isArray(data.tendencia) && data.tendencia.length > 0) renderChartTendencia(data.tendencia);
                    if (data.kpis?.scrap_detalle) renderChartPNC(data.kpis.scrap_detalle);
                    renderScrapAlmacenDetalle(data.kpis?.scrap_almacen_desglose || []);
                }
            } catch (errScrap) {
                console.error("⚠️ Error defensivo renderizando Scrap:", errScrap);
            }

            // 5. Tabla y Gráficos de Pulido -- requiere datos operativos
            try {
                if (tieneDatosOperativos) {
                    if (data.analytics_pulido) {
                        cacheAnalyticsPulido = data.analytics_pulido;
                        // Módulo dado de baja temporalmente (corrupción de datos backend): renderTablaEficienciaPulido deshabilitado.
                        // if (data.analytics_pulido.eficiencia_referencia) {
                        //     renderTablaEficienciaPulido(data.analytics_pulido.eficiencia_referencia);
                        // }
                    }
                    renderChartPulidoRanking(data.rankings?.pulido_profundo || {});
                    renderChartPulidoMixReferencias(data.rankings?.pulido_profundo || {});
                    renderTablaPulido(data.rankings?.pulido_profundo || {}, data.rankings?.pulido_evolucion || {});
                }
            } catch (errPulido) {
                console.error("⚠️ Error defensivo renderizando Pulido:", errPulido);
            }

            // 6. Datos de Jefatura
            if (jefaturaData && (jefaturaData.success || jefaturaData.status === 'success') && jefaturaData.data) {
                console.log("📈 Renderizando datos de Jefatura...");
                lastJefaturaData = jefaturaData.data;

                console.log("[DEBUG] lastJefaturaData:", lastJefaturaData);

                if (lastJefaturaData.mensual) {
                    renderChartMensual(lastJefaturaData.mensual, currentIncSortMode);
                    cargarRendimientoDedicado(currentIncSortMode);
                }
                
                // Iniciar Pre-fetching Seguro (Sin bloquear UI)
                try {
                    setTimeout(() => {
                        const yearAct = lastJefaturaData.year_actual || new Date().getFullYear();
                        const yearPrv = lastJefaturaData.year_prev || (yearAct - 1);
                        prefetchVentasDrilldown(lastJefaturaData.mensual, yearAct, yearPrv);
                    }, 2000);
                } catch (prefErr) {
                    console.warn("⚠️ Fallo no crítico en Pre-fetching:", prefErr);
                }

                if (lastJefaturaData.top_productos) {
                    renderChartTopMejores(lastJefaturaData.top_productos, currentIncSortMode);
                } else {
                    console.warn("⚠️ lastJefaturaData.top_productos es undefined");
                }

                if (lastJefaturaData.peores_productos) {
                    renderChartTopPeores(lastJefaturaData.peores_productos, currentIncSortMode);
                } else {
                    console.warn("⚠️ lastJefaturaData.peores_productos es undefined");
                }

                if (lastJefaturaData.incumplimiento_unidades) {
                    inc_unidades_original = lastJefaturaData.incumplimiento_unidades;
                }
                if (lastJefaturaData.incumplimiento_dinero) {
                    inc_dinero_original = lastJefaturaData.incumplimiento_dinero;
                }
                if (lastJefaturaData.incumplimiento_consolidado) {
                    inc_consolidado_original = lastJefaturaData.incumplimiento_consolidado;
                    renderTablaIncumplimientoConsolidada(inc_consolidado_original);
                }
            }

            // 7. Datos de Cartera
            if (carteraData && carteraData.success && carteraData.data) {
                renderCartera(carteraData.data);
            }

            // 7. Carga asíncrona non-blocking de Productos sin Movimiento (Rotación Cero)
            initBusquedaSinRotacion();
            cargarProductosSinRotacion();

            console.log("✅ Renderizado completado sin errores.");
        } catch (err) {
            console.error("❌ Error crítico durante el renderizado del dashboard:", err);
        }
    }

    function renderCartera(data) {
        if (!data || (typeof ENABLE_CARTERA !== 'undefined' && !ENABLE_CARTERA)) {
            return;
        }

        const formatCOP = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
        
        const total = data.total_cartera || 0;
        const vencida = data.total_vencida || 0;
        const pct = total > 0 ? ((vencida / total) * 100).toFixed(1) : 0;

        document.getElementById('cartera-total-val').textContent = formatCOP.format(total);
        document.getElementById('cartera-vencida-val').textContent = formatCOP.format(vencida);
        document.getElementById('cartera-pct-val').textContent = pct;
        
        const progressBar = document.getElementById('cartera-progress-bar');
        progressBar.style.width = `${pct}%`;

        // Renderizar tabla de clientes
        const tbody = document.getElementById('cartera-clientes-criticos');
        tbody.innerHTML = '';

        if (!data.clientes_criticos || data.clientes_criticos.length === 0) {
            tbody.innerHTML = `<tr><td colspan="2" class="text-center py-4 text-success"><i class="fas fa-check-circle me-2"></i>No hay cartera en mora.</td></tr>`;
            return;
        }

        data.clientes_criticos.forEach(cliente => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td class="fw-medium text-dark">
                    <i class="fas fa-user text-secondary me-2"></i>
                    ${cliente.nombre}
                </td>
                <td class="text-end fw-bold text-danger">
                    ${formatCOP.format(cliente.saldo_vencido)}
                </td>
            `;
            tbody.appendChild(tr);
        });

        // Habilitar botón de exportar solo para ADMIN o COMERCIAL
        if (window.AppState && window.AppState.user && window.AppState.user.rol) {
            const rol = window.AppState.user.rol.toUpperCase();
            if (['ADMIN', 'ADMINISTRACION', 'ADMINISTRACIÓN', 'ADMINISTRADOR', 'COMERCIAL'].includes(rol)) {
                const btnExportar = document.getElementById('btn-exportar-cartera');
                if (btnExportar) btnExportar.classList.remove('d-none');
            }
        }
    }

    async function exportarCartera() {
        const btn = document.getElementById('btn-exportar-cartera');
        const icon = btn.querySelector('i');
        const text = document.getElementById('text-exportar-cartera');
        
        if (!btn || btn.disabled) return;

        try {
            // Estado de carga
            btn.disabled = true;
            icon.className = 'fas fa-spinner fa-spin me-1';
            text.textContent = 'Procesando...';

            const pwaToken = localStorage.getItem('pwa_token');
            const headers = {};
            if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;

            const response = await fetch('/api/dashboard/cartera/exportar', {
                method: 'GET',
                headers: headers
            });

            if (!response.ok) {
                if (response.status === 401 || response.status === 403) {
                    throw new Error('No tienes permisos para exportar la cartera.');
                }
                throw new Error('Error al generar el archivo de exportación.');
            }

            // Manejo de Blob para forzar descarga
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = 'estado_cartera.csv';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            if (window.Swal) {
                Swal.fire({
                    toast: true,
                    position: 'top-end',
                    icon: 'success',
                    title: 'Exportación completada exitosamente',
                    showConfirmButton: false,
                    timer: 3000
                });
            }
        } catch (error) {
            console.error('Error exportando cartera:', error);
            if (window.Swal) {
                Swal.fire({
                    toast: true,
                    position: 'top-end',
                    icon: 'error',
                    title: error.message || 'Error al exportar la cartera',
                    showConfirmButton: false,
                    timer: 3000
                });
            }
        } finally {
            // Restaurar estado visual
            btn.disabled = false;
            icon.className = 'fas fa-file-excel me-1';
            text.textContent = 'Exportar';
        }
    }

    function iniciarCarrouselBot() {
        if (insightInterval) clearInterval(insightInterval);

        insightIndex = 0;
        mostrarSiguienteInsight();

        insightInterval = setInterval(mostrarSiguienteInsight, 6000); // Cambiar cada 6 segundos
    }

    function mostrarSiguienteInsight() {
        const container = document.getElementById('dashboard-bot-container');
        const textElement = document.getElementById('dashboard-bot-text');
        if (!container || !textElement || currentInsights.length === 0) return;

        // Efecto de desvanecimiento
        container.style.opacity = "0";

        setTimeout(() => {
            textElement.innerHTML = currentInsights[insightIndex];
            container.style.opacity = "1";

            // Avanzar índice
            insightIndex = (insightIndex + 1) % currentInsights.length;
        }, 500);
    }


    function renderChartInyeccion(ops) {
        const ctx = document.getElementById('chartInyeccionOperadores');
        if (!ctx) return;

        const labels = ops.map(o => o.nombre);
        const dataBuenas = ops.map(o => o.valor);

        // Crear gradientes para un look más "premium"
        const chartCtx = ctx.getContext('2d');
        const gradientGold = chartCtx.createLinearGradient(0, 0, 0, 400);
        gradientGold.addColorStop(0, 'rgba(251, 191, 36, 1)');
        gradientGold.addColorStop(1, 'rgba(217, 119, 6, 0.8)');

        const gradientBlue = chartCtx.createLinearGradient(0, 0, 0, 400);
        gradientBlue.addColorStop(0, 'rgba(59, 130, 246, 1)');
        gradientBlue.addColorStop(1, 'rgba(37, 99, 235, 0.8)');

        const bgColors = dataBuenas.map((val, idx) => idx === 0 ? gradientGold : gradientBlue);
        const borderColors = dataBuenas.map((val, idx) => idx === 0 ? 'rgba(217, 119, 6, 1)' : 'rgba(37, 99, 235, 1)');

        if (chartOperadores) chartOperadores.destroy();

        chartOperadores = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Buenas',
                    data: dataBuenas,
                    backgroundColor: bgColors,
                    borderColor: borderColors,
                    borderWidth: 0,
                    borderRadius: { topLeft: 8, topRight: 8, bottomLeft: 0, bottomRight: 0 },
                    barPercentage: 0.5,
                    categoryPercentage: 0.7
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 1500, easing: 'easeOutQuart' },
                layout: { padding: { top: 20 } },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(15, 23, 42, 0.95)',
                        titleFont: { size: 14, weight: 'bold' },
                        bodyFont: { size: 13 },
                        padding: 12,
                        cornerRadius: 8,
                        displayColors: false,
                        callbacks: {
                            label: (context) => `🎯 ${context.raw.toLocaleString()} Piezas Inyectadas`
                        }
                    }
                },
                onClick: (event, elements) => {
                    if (elements.length > 0) {
                        const index = elements[0].index;
                        const operador = ops[index].nombre;
                        ModuloDashboard.mostrarDetalleOperadorInyeccion(operador);
                    }
                },
                onHover: (event, chartElement) => {
                    event.native.target.style.cursor = chartElement[0] ? 'pointer' : 'default';
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(226, 232, 240, 0.5)', drawBorder: false },
                        ticks: { font: { weight: '600' }, color: '#64748b', padding: 10 }
                    },
                    x: {
                        grid: { display: false, drawBorder: false },
                        ticks: { font: { weight: 'bold', size: 11 }, color: '#334155', maxRotation: 25, minRotation: 0 }
                    }
                }
            }
        });
    }

    function renderChartMaquinas(maqs) {
        const ctx = document.getElementById('chartMaquinas');
        if (!ctx) return;
        if (chartMaquinas) chartMaquinas.destroy();

        // Blindaje: Asegurar que maqs sea siempre un array de objetos
        const maquinasArr = Array.isArray(maqs) ? maqs : Object.entries(maqs || {}).map(([k, v]) => ({maquina: k, valor: v, porcentaje: 0}));
        if (maquinasArr.length === 0) return;

        // Paleta de colores variados para máquinas
        const palette = [
            '#10b981', // Emerald
            '#6366f1', // Indigo
            '#f59e0b', // Amber
            '#f43f5e', // Rose
            '#8b5cf6', // Violet
            '#06b6d4'  // Cyan
        ];

        chartMaquinas = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: maquinasArr.map(m => {
                    const valStr = Number(m.valor || 0).toLocaleString();
                    const pctVal = (m.porcentaje !== undefined && m.porcentaje !== null) ? m.porcentaje : 0;
                    return `${m.maquina}: ${valStr} Pz (${pctVal}%)`;
                }),
                datasets: [{
                    data: maquinasArr.map(m => m.valor),
                    backgroundColor: palette,
                    borderWidth: 4,
                    borderColor: '#ffffff',
                    hoverOffset: 15
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '70%',
                plugins: {
                    legend: {
                        position: window.innerWidth < 768 ? 'bottom' : 'right',
                        labels: {
                            padding: window.innerWidth < 768 ? 10 : 20,
                            font: { size: window.innerWidth < 768 ? 10 : 12, weight: '600' },
                            color: '#475569'
                        }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(15, 23, 42, 0.9)',
                        padding: 12,
                        cornerRadius: 8,
                        callbacks: {
                            label: (context) => {
                                const item = maquinasArr[context.dataIndex];
                                const valStr = Number(item ? item.valor : context.raw || 0).toLocaleString();
                                const pctVal = (item && item.porcentaje !== undefined && item.porcentaje !== null) ? item.porcentaje : 0;
                                const maqName = item ? item.maquina : context.label;
                                return ` ⚙️ ${maqName}: ${valStr} Pz (${pctVal}%)`;
                            }
                        }
                    }
                }
            }
        });
    }

    function renderChartTendencia(tendencia) {
        const ctx = document.getElementById('chartTendencia');
        if (!ctx) return;
        if (!Array.isArray(tendencia) || tendencia.length === 0) return;
        if (chartTendencia) chartTendencia.destroy();
        ctx.style.cursor = 'pointer';
        chartTendencia = new Chart(ctx, {
            type: 'line',
            data: {
                labels: tendencia.map(t => t.fecha.split('-').reverse().join('/')),
                datasets: [
                    { label: 'Inyección', data: tendencia.map(t => t.iny), borderColor: '#3b82f6', tension: 0.3, fill: false },
                    { label: 'Pulido', data: tendencia.map(t => t.pul), borderColor: '#10b981', tension: 0.3, fill: false }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: (evt, elements) => {
                    if (elements && elements.length > 0) {
                        const index = elements[0].index;
                        const itemObj = tendencia[index];
                        if (itemObj && itemObj.fecha) {
                            abrirModalDrilldownInyeccion(itemObj.fecha);
                        }
                    }
                },
                scales: { y: { beginAtZero: true } }
            }
        });
    }

    async function abrirModalDrilldownInyeccion(fechaStr) {
        let modalEl = document.getElementById('modalDrilldownInyeccion');
        if (!modalEl) {
            const modalHtml = `
            <div class="modal fade" id="modalDrilldownInyeccion" tabindex="-1" aria-hidden="true">
              <div class="modal-dialog modal-xl modal-dialog-centered">
                <div class="modal-content text-dark border-0 shadow-lg" style="background-color: #ffffff;">
                  <div class="modal-header bg-dark text-white border-0 py-3">
                    <h5 class="modal-title fs-6 fw-bold text-white">
                      <i class="fas fa-search-plus text-info me-2"></i>Auditoría de Producción Inyección - <span id="modalDrilldownFecha" class="text-warning"></span>
                    </h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                  </div>
                  <div class="modal-body p-3" style="background-color: #ffffff;">
                    <div id="modalDrilldownLoading" class="text-center py-5">
                      <div class="spinner-border text-primary" role="status"></div>
                      <p class="mt-2 text-dark font-monospace small">Cargando registros atómicos de inyección...</p>
                    </div>
                    <div id="modalDrilldownContent" class="d-none">
                      <div class="table-responsive">
                        <table class="table table-hover table-bordered table-sm align-middle mb-0 text-dark responsive-mobile" style="font-size: 0.84rem; background-color: #ffffff; border-color: #cbd5e1;">
                          <thead class="table-dark text-uppercase small align-middle" style="background-color: #1e293b; color: #ffffff;">
                            <tr>
                              <th class="text-white fw-bold py-2">OP</th>
                              <th class="text-white fw-bold py-2">Máquina</th>
                              <th class="text-white fw-bold py-2">Referencia</th>
                              <th class="text-white fw-bold py-2">Descripción</th>
                              <th class="text-white fw-bold py-2">Operario</th>
                              <th class="text-white fw-bold text-end py-2">Buenas (Pz)</th>
                              <th class="text-white fw-bold text-end py-2">Malas / PNC</th>
                              <th class="text-white fw-bold text-center py-2">Cavidades</th>
                            </tr>
                          </thead>
                          <tbody id="modalDrilldownTbody" class="text-dark"></tbody>
                          <tfoot id="modalDrilldownTfoot" class="table-secondary fw-bold text-dark" style="background-color: #f1f5f9; color: #0f172a;"></tfoot>
                        </table>
                      </div>
                    </div>
                  </div>
                  <div class="modal-footer bg-light border-top p-2">
                    <button type="button" class="btn btn-secondary btn-sm px-4 fw-semibold" data-bs-dismiss="modal">Cerrar</button>
                  </div>
                </div>
              </div>
            </div>`;
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            modalEl = document.getElementById('modalDrilldownInyeccion');
        }

        const fechaSpan = document.getElementById('modalDrilldownFecha');
        const loadingEl = document.getElementById('modalDrilldownLoading');
        const contentEl = document.getElementById('modalDrilldownContent');
        const tbodyEl = document.getElementById('modalDrilldownTbody');
        const tfootEl = document.getElementById('modalDrilldownTfoot');

        if (fechaSpan) fechaSpan.textContent = fechaStr;
        if (loadingEl) loadingEl.classList.remove('d-none');
        if (contentEl) contentEl.classList.add('d-none');

        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();

        try {
            const resp = await fetch(`/api/dashboard/drilldown/inyeccion?fecha=${encodeURIComponent(fechaStr)}`);
            if (!resp.ok) {
                const textErr = await resp.text();
                throw new Error(`HTTP ${resp.status}: Servidor devolvió formato no válido o no encontrado.`);
            }
            const data = await resp.json();

            if (!data.success) {
                throw new Error(data.error || 'Error al obtener detalle');
            }

            const filas = data.detalle || [];
            let htmlTbody = '';
            let sumBuenas = 0;
            let sumMalas = 0;
            let sumCavidades = 0;

            if (filas.length === 0) {
                htmlTbody = `<tr><td colspan="8" class="text-center text-secondary font-monospace py-4 fw-semibold">No se encontraron registros de inyección para la fecha ${fechaStr}</td></tr>`;
            } else {
                filas.forEach(f => {
                    const cavidades = f.cavidades || 1;
                    sumBuenas += f.buenas;
                    sumMalas += f.malas;
                    sumCavidades += cavidades;

                    htmlTbody += `
                    <tr>
                      <td class="fw-bold text-dark font-monospace" data-label="OP">${f.orden_produccion}</td>
                      <td data-label="Máquina"><span class="badge bg-primary text-white px-2 py-1">${f.maquina}</span></td>
                      <td class="fw-bold text-primary font-monospace" data-label="Referencia">${f.id_codigo}</td>
                      <td class="text-dark fw-medium text-truncate" style="max-width: 220px;" title="${f.descripcion}" data-label="Descripción">${f.descripcion}</td>
                      <td class="text-dark fw-semibold" data-label="Operario">${f.operario}</td>
                      <td class="text-end fw-bold text-success fs-6" data-label="Buenas (Pz)">${f.buenas.toLocaleString()}</td>
                      <td class="text-end fw-bold ${f.malas > 0 ? 'text-danger fs-6' : 'text-secondary'}" data-label="Malas / PNC">${f.malas.toLocaleString()}</td>
                      <td class="text-center fw-bold text-dark" data-label="Cavidades">${cavidades}</td>
                    </tr>`;
                });
            }

            if (tbodyEl) tbodyEl.innerHTML = htmlTbody;
            if (tfootEl) {
                tfootEl.innerHTML = `
                <tr>
                  <td colspan="5" class="text-end text-uppercase fw-bold text-dark py-2">Total Consolidado:</td>
                  <td class="text-end text-success fw-bolder fs-6 py-2">${sumBuenas.toLocaleString()} Pz</td>
                  <td class="text-end ${sumMalas > 0 ? 'text-danger' : 'text-secondary'} fw-bolder fs-6 py-2">${sumMalas.toLocaleString()} Pz</td>
                  <td class="text-center text-dark fw-bold py-2">${sumCavidades}</td>
                </tr>`;
            }

            if (loadingEl) loadingEl.classList.add('d-none');
            if (contentEl) contentEl.classList.remove('d-none');

        } catch (err) {
            console.error('Error al cargar drilldown inyección:', err);
            if (tbodyEl) {
                tbodyEl.innerHTML = `<tr><td colspan="8" class="text-center text-danger py-4 font-monospace fw-bold">Error cargando datos: ${err.message}</td></tr>`;
            }
            if (loadingEl) loadingEl.classList.add('d-none');
            if (contentEl) contentEl.classList.remove('d-none');
        }
    }

    /**
     * Suscripción Nativa Directa al DOM del Canvas (Garantiza captura de clics)
     */
    function suscribirClickNativoCanvas(canvasId) {
        const canvas = typeof canvasId === 'string' ? document.getElementById(canvasId) : canvasId;
        if (canvas && !canvas.dataset.clickBound) {
            canvas.dataset.clickBound = 'true';
            canvas.style.cursor = 'pointer';

            canvas.addEventListener('click', (evt) => {
                console.log(`📌 Clic NATIVO en canvas #${canvas.id || 'desconocido'} detectado`);
                const chartInst = typeof Chart !== 'undefined' ? Chart.getChart(canvas) : null;
                if (!chartInst) {
                    console.error('❌ No se encontró instancia de Chart.js en el canvas');
                    return;
                }

                const points = chartInst.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, true);
                if (points && points.length > 0) {
                    const index = points[0].index;
                    const rawLabel = chartInst.data && chartInst.data.labels ? chartInst.data.labels[index] : null;
                    console.log('🎯 Etiqueta extraída del gráfico:', rawLabel);

                    if (rawLabel) {
                        const codigo = String(rawLabel).trim().split(' ')[0];
                        console.log('🎯 Código extraído correctamente:', codigo);

                        if (codigo && typeof window.abrirModalHistorial === 'function') {
                            window.abrirModalHistorial(codigo);
                        } else {
                            console.error('❌ Fallo al invocar window.abrirModalHistorial con código:', codigo);
                        }
                    }
                } else {
                    console.warn('⚠️ Clic en área fuera de barra.');
                }
            });
        }
    }

    async function renderChartPNC(legacyPnc, modo = currentPncMode) {
        const ctx = document.getElementById('chartPNCGlobal') || document.getElementById('chartPNC');
        if (!ctx) return;

        try {
            // Capturar las fechas globales del formulario de filtros
            const ini = document.getElementById('db-fecha-desde')?.value || '';
            const fin = document.getElementById('db-fecha-hasta')?.value || '';

            const response = await fetch(`/api/gerencia/metricas-pnc?fecha_inicio=${ini}&fecha_fin=${fin}`);
            const res = await response.json();
            if (!res.success) throw new Error(res.error || 'Unknown error');

            const totales = res.totales_area;
            const esModoDinero = modo === 'money';
            // Modos de Falla: Dinero usa el desglose costeado por criterio (modos_falla_dinero_area,
            // agregado en PncService); Unidades mantiene el desglose de cantidades de siempre.
            const modosFalla = esModoDinero
                ? (res.modos_falla_dinero_area || {})
                : (res.modos_falla_area || {});
            const pareto = res.pareto_referencias;

            // Actualizar el total global en el badge del card de calidad
            // Orden fijo: flujo secuencial de producción (Inyección → Pulido → Ensamble)
            const areaKeys = ['inyeccion', 'pulido', 'ensamble'].filter(k => totales[k] !== undefined);
            const globalPncSum = areaKeys.reduce((sum, key) => sum + (totales[key]?.pnc || 0), 0);
            const globalPncElement = document.getElementById('pnc-global-val');
            if (globalPncElement) {
                globalPncElement.textContent = globalPncSum.toLocaleString();
            }

            // Actualizar nuevos KPIs (FPY y % PNC Global)
            const fpyGlobal = res.fpy_global || 0;
            const pncGlobalPct = res.pnc_global_percentage || 0;

            const fpyElement = document.getElementById('fpy-global-val');
            if (fpyElement) fpyElement.textContent = fpyGlobal.toFixed(2);
            
            const fpyBar = document.getElementById('fpy-global-bar');
            if (fpyBar) {
                fpyBar.style.width = `${fpyGlobal}%`;
                fpyBar.setAttribute('aria-valuenow', fpyGlobal);
                if (fpyGlobal < 80) {
                    fpyBar.className = 'progress-bar bg-danger';
                } else if (fpyGlobal < 90) {
                    fpyBar.className = 'progress-bar bg-warning';
                } else {
                    fpyBar.className = 'progress-bar bg-success';
                }
            }

            const pctPncElement = document.getElementById('pnc-porcentaje-val');
            if (pctPncElement) pctPncElement.textContent = pncGlobalPct.toFixed(2);

            // 1. Gráfico de barras para totales por área
            if (chartPNC) chartPNC.destroy();
            
            const displayNames = {
                'inyeccion': 'Inyección',
                'pulido': 'Pulido',
                'ensamble': 'Ensamble'
            };
            
            // Calcular % NC en etiquetas del X-axis para visualización Lean rápida
            const areaLabels = areaKeys.map(k => {
                const name = displayNames[k] || k.charAt(0).toUpperCase() + k.slice(1);
                const dataArea = totales[k] || { pnc: 0, buenas: 0 };
                const pnc = dataArea.pnc || 0;
                const buenas = dataArea.buenas || 0;
                const total = pnc + buenas;
                const pct = total > 0 ? ((pnc / total) * 100).toFixed(1) : '0.0';
                return `${name} (${pct}%)`;
            });
            const areaValues = areaKeys.map(k => totales[k]?.pnc || 0);

            // Crear gradiente premium para barras de áreas
            const chartCtx = ctx.getContext('2d');
            const gradientAreas = chartCtx.createLinearGradient(0, 0, 0, 300);
            gradientAreas.addColorStop(0, 'rgba(59, 130, 246, 1)');   // Royal Blue
            gradientAreas.addColorStop(1, 'rgba(37, 99, 235, 0.8)'); // Deep Blue

            chartPNC = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: areaLabels,
                    datasets: [{
                        label: 'Piezas PNC',
                        data: areaValues,
                        backgroundColor: [
                            'rgba(59, 130, 246, 0.85)', // Inyección: Azul
                            'rgba(16, 185, 129, 0.85)', // Pulido: Verde
                            'rgba(245, 158, 11, 0.85)'   // Ensamble: Naranja
                        ],
                        borderColor: [
                            '#3b82f6',
                            '#10b981',
                            '#f59e0b'
                        ],
                        borderWidth: 1.5,
                        borderRadius: { topLeft: 8, topRight: 8, bottomLeft: 0, bottomRight: 0 },
                        barPercentage: 0.45,
                        categoryPercentage: 0.7
                    }]
                },
                options: {
                    events: ['mousemove', 'mouseout', 'click', 'touchstart', 'touchmove'],
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 1200, easing: 'easeOutQuart' },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: 'rgba(15, 23, 42, 0.95)',
                            titleFont: { size: 13, weight: 'bold' },
                            bodyFont: { size: 12 },
                            padding: 10,
                            cornerRadius: 8,
                            displayColors: true,
                            callbacks: {
                                label: (context) => {
                                    const index = context.dataIndex;
                                    const areaKey = areaKeys[index];
                                    const dataArea = totales[areaKey] || { pnc: 0, buenas: 0 };
                                    const pnc = dataArea.pnc || 0;
                                    const buenas = dataArea.buenas || 0;
                                    const total = pnc + buenas;
                                    const pct = total > 0 ? ((pnc / total) * 100).toFixed(2) : '0.00';
                                    return [
                                        `⚠️ Piezas PNC: ${pnc.toLocaleString()}`,
                                        `⚙️ Prod. Buenas: ${buenas.toLocaleString()}`,
                                        `📊 Merma (% NC): ${pct}%`
                                    ];
                                }
                            }
                        }
                    },
                    onClick: (evt, activeElements, chart) => {
                        console.log('📌 Clic detectado en canvas del gráfico chartPNC', evt);
                        const chartInst = chart || (evt && evt.chart);
                        const points = (chartInst && typeof chartInst.getElementsAtEventForMode === 'function')
                            ? chartInst.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, true)
                            : activeElements;

                        if (points && points.length > 0) {
                            const index = points[0].index;
                            const areaKey = areaKeys[index];
                            const areaLabel = areaLabels[index];
                            const modos = modosFalla[areaKey] || {};

                            if (Object.keys(modos).length === 0) {
                                Swal.fire({
                                    icon: 'info',
                                    title: `Modos de Falla: ${areaLabel}`,
                                    text: 'No se encontraron modos de falla registrados para esta área.',
                                    confirmButtonColor: '#3b82f6'
                                });
                                return;
                            }

                            // Render dynamic SweetAlert Modal with sub-bar-chart
                            let modalChartInstance = null;
                            Swal.fire({
                                title: `Modos de Falla — ${areaLabel}`,
                                html: `
                                    <div class="p-2">
                                        <p class="text-muted small text-start mb-3">Análisis detallado de rechazos para la estación de <strong>${areaLabel}</strong>.</p>
                                        <div style="height: 300px; width: 100%; position: relative;">
                                            <canvas id="chartModalPNC"></canvas>
                                        </div>
                                    </div>
                                `,
                                width: 650,
                                confirmButtonText: 'Cerrar Análisis',
                                confirmButtonColor: '#475569',
                                customClass: {
                                    popup: 'rounded-4'
                                },
                                didOpen: () => {
                                    const modalCtx = document.getElementById('chartModalPNC').getContext('2d');

                                    // Crear gradiente premium rojo para el modal de fallas
                                    const modalGrad = modalCtx.createLinearGradient(0, 0, 0, 300);
                                    modalGrad.addColorStop(0, 'rgba(239, 68, 68, 0.9)');
                                    modalGrad.addColorStop(1, 'rgba(185, 28, 28, 0.8)');

                                    // Formato condicionado al modo activo (Dinero vs Unidades)
                                    const formatCOP_Modal = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 0 });
                                    const formatearValorModal = (val) => esModoDinero ? formatCOP_Modal.format(val) : `${Math.round(val).toLocaleString()} Pz`;
                                    const formatearTickModal = (val) => {
                                        if (!esModoDinero) return val.toLocaleString();
                                        return val >= 1000000 ? `$${(val / 1000000).toFixed(1)}M` : formatCOP_Modal.format(val);
                                    };

                                    modalChartInstance = new Chart(modalCtx, {
                                        type: 'bar',
                                        data: {
                                            labels: Object.keys(modos),
                                            datasets: [{
                                                label: esModoDinero ? 'Costo del Defecto ($)' : 'Cantidad (Pz)',
                                                data: Object.values(modos),
                                                backgroundColor: modalGrad,
                                                borderColor: '#ef4444',
                                                borderWidth: 0,
                                                borderRadius: { topLeft: 6, topRight: 6, bottomLeft: 0, bottomRight: 0 },
                                                barPercentage: 0.5
                                            }]
                                        },
                                        options: {
                                            responsive: true,
                                            maintainAspectRatio: false,
                                            plugins: {
                                                legend: { display: false },
                                                tooltip: {
                                                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                                                    padding: 10,
                                                    cornerRadius: 8,
                                                    callbacks: {
                                                        label: (context) => ` ${formatearValorModal(context.raw)}`
                                                    }
                                                }
                                            },
                                            scales: {
                                                y: {
                                                    beginAtZero: true,
                                                    grid: { color: 'rgba(226, 232, 240, 0.5)' },
                                                    ticks: { callback: formatearTickModal }
                                                },
                                                x: {
                                                    grid: { display: false },
                                                    ticks: { font: { weight: 'bold', size: 11 }, color: '#334155' }
                                                }
                                            }
                                        }
                                    });
                                },
                                willClose: () => {
                                    // willClose corre ANTES de que Swal desmonte el <canvas> del DOM
                                    // (didClose corre después: para entonces Chart.getChart('chartModalPNC')
                                    // ya no encuentra el nodo y el destroy() nunca se ejecuta — verificado
                                    // empíricamente con una fuga de +1 instancia por apertura). Se usa la
                                    // referencia directa de la instancia en vez de una búsqueda por DOM.
                                    if (modalChartInstance) {
                                        modalChartInstance.destroy();
                                        modalChartInstance = null;
                                    }
                                }
                            });
                        }
                    },
                    onHover: (event, chartElement) => {
                        event.native.target.style.cursor = chartElement[0] ? 'pointer' : 'default';
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { color: 'rgba(226, 232, 240, 0.5)', drawBorder: false },
                            ticks: { font: { weight: '600' }, color: '#64748b', padding: 8 }
                        },
                        x: {
                            grid: { display: false, drawBorder: false },
                            ticks: { font: { weight: 'bold', size: 12 }, color: '#1e293b' }
                        }
                    }
                }
            });

            // 2. Gráfico de Pareto de Referencias (Barras Horizontales)
            const ctxPareto = document.getElementById('chartPNCPareto');
            if (ctxPareto) {
                if (chartPNCPareto) chartPNCPareto.destroy();

                const labelsPareto = pareto.map(p => p.referencia);
                const valuesPareto = pareto.map(p => p.cantidad);

                const paretoCtx = ctxPareto.getContext('2d');
                const gradientPareto = paretoCtx.createLinearGradient(0, 0, 400, 0);
                gradientPareto.addColorStop(0, 'rgba(244, 63, 94, 0.85)'); // Rose
                gradientPareto.addColorStop(1, 'rgba(225, 29, 72, 0.95)'); // Rose Dark

                chartPNCPareto = new Chart(ctxPareto, {
                    type: 'bar',
                    data: {
                        labels: labelsPareto,
                        datasets: [{
                            label: 'PNC Acumulado',
                            data: valuesPareto,
                            backgroundColor: gradientPareto,
                            borderColor: '#e11d48',
                            borderWidth: 0,
                            borderRadius: { topRight: 6, bottomRight: 6, topLeft: 0, bottomLeft: 0 },
                            barPercentage: 0.55
                        }]
                    },
                    options: {
                        events: ['mousemove', 'mouseout', 'click', 'touchstart', 'touchmove'],
                        indexAxis: 'y', // Convert to Horizontal Bar Chart!
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                backgroundColor: 'rgba(15, 23, 42, 0.95)',
                                padding: 10,
                                cornerRadius: 8,
                                callbacks: {
                                    label: (context) => `⚙️ Ref: ${context.label} — ${context.raw.toLocaleString()} PNC`
                                }
                            }
                        },
                        onClick: (evt, activeElements, chart) => {
                            console.log('📌 Clic detectado en canvas del gráfico chartPNCPareto', evt);
                            const chartInst = chart || (evt && evt.chart);
                            const points = (chartInst && typeof chartInst.getElementsAtEventForMode === 'function')
                                ? chartInst.getElementsAtEventForMode(evt, 'nearest', { intersect: true }, true)
                                : activeElements;

                            if (points && points.length > 0) {
                                const index = points[0].index;
                                const codigo = (chartInst && chartInst.data && chartInst.data.labels)
                                    ? chartInst.data.labels[index]
                                    : labelsPareto[index];
                                console.log('🎯 Código extraído del gráfico:', codigo);
                                if (codigo && typeof window.abrirModalHistorial === 'function') {
                                    window.abrirModalHistorial(codigo);
                                } else {
                                    console.error('❌ Fallo al invocar window.abrirModalHistorial con código:', codigo);
                                }
                            } else {
                                console.warn('⚠️ Clic en área vacía del canvas, no se detectó barra.');
                            }
                        },
                        onHover: (event, chartElement) => {
                            if (event && event.native && event.native.target) {
                                event.native.target.style.cursor = (chartElement && chartElement.length > 0) ? 'pointer' : 'default';
                            }
                        },
                        scales: {
                            x: {
                                beginAtZero: true,
                                grid: { color: 'rgba(226, 232, 240, 0.5)', drawBorder: false },
                                ticks: { font: { weight: '600' }, color: '#64748b' }
                            },
                            y: {
                                grid: { display: false, drawBorder: false },
                                ticks: { font: { weight: 'bold', size: 11 }, color: '#1e293b' }
                            }
                        }
                    }
                });
                // Suscripción Nativa DOM Directa al canvas del Pareto
                suscribirClickNativoCanvas('chartPNCPareto');
                suscribirClickNativoCanvas('chartPNC');
                suscribirClickNativoCanvas('chartPNCGlobal');
            }

        } catch (err) {
            console.error("❌ Error al renderizar métricas PNC con el nuevo API, usando fallback:", err);
            if (legacyPnc) {
                if (chartPNC) chartPNC.destroy();
                chartPNC = new Chart(ctx, {
                    type: 'polarArea',
                    data: {
                        labels: ['Inyección', 'Pulido', 'Ensamble'],
                        datasets: [{ data: [legacyPnc.inyeccion, legacyPnc.pulido, legacyPnc.ensamble], backgroundColor: ['#3b82f6cc', '#10b981cc', '#f59e0bcc'] }]
                    },
                    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
                });
            }
        }
    }

    // Toggle Dinero/Unidades del desglose de Scrap y PNC (independiente del
    // toggle global de Jefatura — no depende de lastJefaturaData porque la
    // tarjeta de PNC es visible para todos los roles).
    function setModoPnc(modo) {
        if (currentPncMode === modo) return;
        currentPncMode = modo;
        localStorage.setItem('db_pnc_toggle_mode', modo);

        const btnMoney = document.getElementById('toggle-pnc-money');
        const btnUnits = document.getElementById('toggle-pnc-units');
        if (btnMoney) btnMoney.classList.toggle('active', modo === 'money');
        if (btnUnits) btnUnits.classList.toggle('active', modo === 'units');

        renderChartPNC(null, modo);
    }

    function renderScrapAlmacenDetalle(desglose) {
        const container = document.getElementById('pnc-almacen-desglose');
        if (!container) return;

        container.innerHTML = '';

        if (!desglose || desglose.length === 0) {
            container.innerHTML = '<div class="list-group-item text-muted text-center py-3"><i class="fas fa-check-circle text-success mb-2 fs-4"></i><br>Sin Scrap reportado</div>';
            return;
        }

        // Tomar top 5
        const top5 = desglose.slice(0, 5);
        top5.forEach((item, index) => {
            const fCosto = item.costo > 0 ? new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(item.costo) : 'S/C';
            const html = `
                <div class="list-group-item d-flex justify-content-between align-items-center py-2 px-3 scrap-item-interactive" 
                     onclick="window.ModuloDashboard.abrirModalScrapDetalle('${item.producto}')"
                     style="border-left: 4px solid #ef4444; background-color: #fafafa; margin-bottom: 8px; border-radius: 8px; cursor: pointer; transition: all 0.2s ease;"
                     title="Haz clic para ver el desglose por fecha y máquina de ${item.producto}">
                    <div class="text-truncate me-3" style="max-width: 65%;">
                        <span class="text-muted me-2 fw-bold fs-6">${index + 1}.</span> 
                        <span class="fw-bold text-dark" style="font-size: 0.95rem;">${item.producto}</span>
                        <i class="fas fa-search-plus text-muted ms-2 fs-6"></i>
                    </div>
                    <div class="d-flex flex-column align-items-end justify-content-center">
                        <span class="fw-bold text-danger" style="font-size: 1.05rem; letter-spacing: -0.5px;">${fCosto}</span>
                        <span class="badge rounded-pill mt-1" style="background-color: #cbd5e1; color: #334155; font-size: 0.75rem;"><i class="fas fa-cubes me-1"></i>${item.cantidad.toLocaleString()} Pz</span>
                    </div>
                </div>
            `;
            container.insertAdjacentHTML('beforeend', html);
        });
    }

    async function abrirModalScrapDetalle(itemId) {
        if (!itemId) return;
        const modalEl = document.getElementById('modalScrapDetalle');
        if (!modalEl) return;

        const titleEl = document.getElementById('modal-scrap-ref-title');
        const loadingEl = document.getElementById('modal-scrap-loading');
        const contentEl = document.getElementById('modal-scrap-content');
        const tbodyEl = document.getElementById('modal-scrap-tbody');

        if (titleEl) titleEl.textContent = itemId;
        if (loadingEl) loadingEl.style.display = 'block';
        if (contentEl) contentEl.style.display = 'none';
        if (tbodyEl) tbodyEl.innerHTML = ''; // Limpia filas de una referencia/periodo anterior antes de pedir las nuevas

        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();

        try {
            const pwaToken = localStorage.getItem('pwa_token');
            const headers = {};
            if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;

            // Propaga el Filtro Global de Fechas del dashboard: sin esto, el modal mostraba
            // el scrap histórico completo de la referencia sin importar el período seleccionado.
            const params = new URLSearchParams({ item_id: itemId });
            const desde = document.getElementById('db-fecha-desde')?.value || '';
            const hasta = document.getElementById('db-fecha-hasta')?.value || '';
            if (desde) params.append('desde', desde);
            if (hasta) params.append('hasta', hasta);

            const res = await fetch(`/api/dashboard/scrap-detalle?${params.toString()}`, {
                headers,
                credentials: 'include'
            });
            const data = await res.json();
            
            if (loadingEl) loadingEl.style.display = 'none';
            if (contentEl) contentEl.style.display = 'block';
            
            if (data.success && Array.isArray(data.data) && data.data.length > 0) {
                tbodyEl.innerHTML = data.data.map(row => `
                    <tr>
                        <td><span class="fw-semibold text-dark">${row.fecha || 'Sin fecha'}</span></td>
                        <td><span class="badge bg-light text-dark border"><i class="fas fa-microchip me-1 text-primary"></i>${row.maquina || 'Inyección'}</span></td>
                        <td class="text-end fw-bold text-danger">${(row.cantidad || 0).toLocaleString()} Pz</td>
                    </tr>
                `).join('');
            } else {
                tbodyEl.innerHTML = `<tr><td colspan="3" class="text-center text-muted py-3">No hay registros detallados de scrap para la referencia ${itemId}</td></tr>`;
            }
        } catch (err) {
            console.error("Error cargando detalle de scrap:", err);
            if (loadingEl) loadingEl.style.display = 'none';
            if (contentEl) contentEl.style.display = 'block';
            if (tbodyEl) tbodyEl.innerHTML = `<tr><td colspan="3" class="text-center text-danger py-3">Error al obtener desglose de scrap</td></tr>`;
        }
    }

    let sinRotacionAbortController = null;

    async function cargarProductosSinRotacion(query = '', maxVentas = 0) {
        const container = document.getElementById('contenedor-sin-rotacion');
        const badgeTotal = document.getElementById('badge-total-sin-rotacion');
        if (!container) return;
        
        // Indicador visual de carga (Spinner activo)
        container.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm text-warning me-2" role="status"></div> Cargando productos de baja rotación...</div>';
        
        // Cancelar petición anterior si aún está pendiente para evitar condiciones de carrera
        if (sinRotacionAbortController) {
            sinRotacionAbortController.abort();
        }
        sinRotacionAbortController = new AbortController();

        try {
            let url = `/api/dashboard/sin-rotacion?max_ventas=${encodeURIComponent(maxVentas)}`;
            if (query && query.trim()) {
                url += `&q=${encodeURIComponent(query.trim())}`;
            }
            
            const pwaToken = localStorage.getItem('pwa_token');
            const headers = {};
            if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;
            
            const res = await fetch(url, { 
                headers, 
                credentials: 'include',
                signal: sinRotacionAbortController.signal
            });
            const data = await res.json();
            
            if (data.success && data.data && Array.isArray(data.data.productos)) {
                const prods = data.data.productos;
                if (badgeTotal) badgeTotal.textContent = data.data.total || prods.length;
                
                if (prods.length === 0) {
                    container.innerHTML = `<div class="text-center text-muted py-3"><i class="fas fa-check-circle text-success me-2"></i> ${query ? 'No se encontraron productos coincidentes' : 'Todos los productos superan el límite de ventas configurado'}</div>`;
                    return;
                }
                
                container.innerHTML = `
                    <div class="table-responsive">
                        <table class="table table-hover table-sm align-middle mb-0 responsive-mobile">
                            <thead class="table-light">
                                <tr>
                                    <th>Código</th>
                                    <th>Descripción</th>
                                    <th class="text-end">Ventas 12m</th>
                                    <th class="text-end">Stock P. Terminado (WO)</th>
                                    <th class="text-end">Estado Movimiento</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${prods.map(p => `
                                    <tr>
                                        <td data-label="Código"><span class="fw-bold text-dark">${p.codigo}</span></td>
                                        <td data-label="Descripción"><span class="text-muted small">${p.descripcion}</span></td>
                                        <td class="text-end fw-bold text-primary" data-label="Ventas 12m">${(p.ventas_periodo || 0).toLocaleString()} Pz</td>
                                        <td class="text-end fw-semibold text-dark" data-label="Stock P. Terminado (WO)">${(p.stock !== undefined && p.stock !== null ? p.stock : p.stock_terminado || 0).toLocaleString()} Pz</td>
                                        <td class="text-end" data-label="Estado Movimiento"><span class="badge ${p.ventas_periodo === 0 ? 'bg-danger' : 'bg-warning text-dark'}"><i class="fas fa-exclamation-triangle me-1"></i> ${p.ventas_periodo === 0 ? 'Sin Ventas (12m)' : `Baja Rotación (<= ${data.data.max_ventas_aplicado} Pz)`}</span></td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                `;
            } else {
                if (badgeTotal) badgeTotal.textContent = '0';
                container.innerHTML = '<div class="text-center text-muted py-3">No hay productos de baja rotación</div>';
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                console.log("ℹ️ [Baja Rotación] Petición previa cancelada por AbortController.");
                return;
            }
            console.error("Error cargando productos de baja rotación:", err);
            if (container) container.innerHTML = '<div class="text-center text-danger py-3">Error cargando información de rotación</div>';
        }
    }

    function initBusquedaSinRotacion() {
        const inputEl = document.getElementById('input-busqueda-sin-rotacion');
        const selectEl = document.getElementById('select-max-ventas-sin-rotacion');

        const triggerFetch = () => {
            const q = inputEl ? (inputEl.value || '').trim() : '';
            const maxV = selectEl ? (parseInt(selectEl.value, 10) || 0) : 0;
            // Eliminada la restricción de longitud: permite búsquedas de cualquier longitud (1 o más caracteres)
            console.log(`🔍 [Baja Rotación] Disparando fetch con q="${q}", max_ventas=${maxV}`);
            cargarProductosSinRotacion(q, maxV);
        };

        if (inputEl && inputEl.dataset.listenerAttached !== 'true') {
            inputEl.dataset.listenerAttached = 'true';
            const searchHandler = debounce(triggerFetch, 500);
            inputEl.addEventListener('input', searchHandler);
        }

        if (selectEl && selectEl.dataset.listenerAttached !== 'true') {
            selectEl.dataset.listenerAttached = 'true';
            selectEl.addEventListener('change', triggerFetch);
        }
    }

    // Paleta de colores para las operadoras
    const colorsList = [
        'rgba(59, 130, 246, 0.85)', 'rgba(16, 185, 129, 0.85)', 'rgba(245, 158, 11, 0.85)',
        'rgba(239, 68, 68, 0.85)', 'rgba(139, 92, 246, 0.85)', 'rgba(236, 72, 153, 0.85)',
        'rgba(14, 165, 233, 0.85)', 'rgba(20, 184, 166, 0.85)', 'rgba(217, 70, 239, 0.85)',
        'rgba(249, 115, 22, 0.85)'
    ];



    function renderChartPulidoRanking(profundo) {
        const ctx = document.getElementById('chartPulidoRanking');
        if (!ctx || !profundo) return;

        // Ranking volumétrico: ordena por piezas físicas buenas (NO por puntos ponderados)
        const ops = Object.keys(profundo)
            .map(k => ({ nombre: k, buenas: profundo[k].buenas || 0 }))
            .sort((a, b) => b.buenas - a.buenas)
            .slice(0, 10);

        const labels = ops.map(o => o.nombre);
        const data   = ops.map(o => o.buenas); // Unidades físicas, nunca puntos

        if (chartPulidoRankingInst) chartPulidoRankingInst.destroy();

        chartPulidoRankingInst = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Piezas Producidas (OK)',   // Métrica física — NO puntos
                    data: data,
                    backgroundColor: 'rgba(16, 185, 129, 0.8)',
                    borderRadius: 5,
                    barThickness: 20
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (c) => ` ${Math.round(c.raw).toLocaleString()} Pz OK`
                        }
                    }
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        grid: { color: 'rgba(0,0,0,0.05)' },
                        title: { display: true, text: 'Unidades físicas producidas', font: { size: 11 } }
                    },
                    y: { grid: { display: false }, ticks: { font: { weight: 'bold' } } }
                }
            },
            plugins: [{
                id: 'iconosCarreraPulido',
                afterDatasetsDraw: (chart) => posicionarIconosCarreraPulido(chart, ops.length)
            }]
        });
    }

    /**
     * Pedido del jefe de planta 2026-09-02: el ranking de operarias cambia de
     * líder en vivo durante el día ("la guerra diaria") a medida que van
     * reportando -- una corona en la punta de la barra que va primera y un
     * caballo en las demás, como si fuera una carrera, funciona como
     * motivación visual. Se dibuja como overlay HTML (no en el canvas) para
     * poder animarlo con CSS puro -- ver .pulido-race-icon-* en dashboard.css.
     * Las posiciones se recalculan en cada draw del chart (afterDatasetsDraw),
     * así siguen la animación de crecimiento inicial de las barras.
     */
    function posicionarIconosCarreraPulido(chart, totalOps) {
        const overlay = document.getElementById('pulido-ranking-icons');
        if (!overlay) return;

        const meta = chart.getDatasetMeta(0);
        const canvasRect = chart.canvas.getBoundingClientRect();
        const overlayRect = overlay.getBoundingClientRect();
        const offsetX = canvasRect.left - overlayRect.left;
        const offsetY = canvasRect.top - overlayRect.top;

        let html = '';
        for (let i = 0; i < totalOps; i++) {
            const el = meta.data[i];
            if (!el) continue;
            const x = el.x + offsetX + 4;
            const y = el.y + offsetY;
            const esLider = i === 0;
            const icono = esLider ? '👑' : '🐎';
            const clase = esLider ? 'pulido-race-icon-crown' : 'pulido-race-icon-horse';
            html += `<span class="pulido-race-icon ${clase}" style="left:${x}px; top:${y}px;">${icono}</span>`;
        }

        // Igual que en posicionarIconosCarreraMix: solo tocar el DOM si el
        // layout cambió de verdad, para no reiniciar la animación CSS en
        // cada redraw por hover/tooltip (eso se veía como "trabado").
        if (overlay.dataset.firma === html) return;
        overlay.dataset.firma = html;
        overlay.innerHTML = html;
    }




    // Paleta ampliada SOLO para el mix por referencia: cada referencia debe ser
    // distinguible dentro de una misma barra apilada, por eso son más tonos y más
    // separados en matiz que la paleta de operarias.
    const colorsRefs = [
        '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
        '#ec4899', '#0ea5e9', '#14b8a6', '#d946ef', '#f97316',
        '#84cc16', '#6366f1', '#06b6d4', '#eab308', '#f43f5e',
        '#22c55e', '#a855f7', '#fb7185', '#2dd4bf', '#c084fc'
    ];

    // Fuera de la paleta fija se generan tonos por ángulo áureo: nunca se repite
    // un color entre dos referencias vecinas de la misma barra.
    function colorParaReferencia(idx) {
        if (idx < colorsRefs.length) return colorsRefs[idx];
        return `hsl(${Math.round((idx * 137.508) % 360)}, 62%, 55%)`;
    }

    // Tope de referencias con color propio cuando el rango filtrado es largo
    // (ej. un mes); el resto se agrupa en "Otras referencias" para que la
    // leyenda siga siendo legible. Pedido del usuario 2026-09-01: en rangos
    // cortos (día/semana) las jefas quieren ver TODAS las referencias sin
    // agrupar, porque ahí sí son pocas.
    const MAX_REFS_MIX_LARGO = 12;
    const RANGO_CORTO_DIAS = 7;

    // Sin límite en rangos cortos (Infinity -> slice(0, Infinity) trae todo
    // y slice(Infinity) para "el resto" queda vacío); con límite en rangos
    // largos, mismo criterio de antes. Si no hay filtro de fecha activo se
    // asume rango largo (conservador, evita una leyenda gigante por defecto).
    function limiteReferenciasMix() {
        const desde = document.getElementById('db-fecha-desde')?.value;
        const hasta = document.getElementById('db-fecha-hasta')?.value;
        if (!desde || !hasta) return MAX_REFS_MIX_LARGO;
        const dDesde = new Date(`${desde}T00:00:00`);
        const dHasta = new Date(`${hasta}T00:00:00`);
        const dias = Math.round((dHasta - dDesde) / 86400000) + 1;
        return (dias > 0 && dias <= RANGO_CORTO_DIAS) ? Infinity : MAX_REFS_MIX_LARGO;
    }

    /**
     * Mismo ranking volumétrico que renderChartPulidoRanking (operaria vs piezas),
     * pero la barra se parte en tramos: uno por referencia, de largo proporcional
     * a las piezas pulidas de esa referencia y con color propio.
     *
     * Fuente: cacheAnalyticsPulido.operario_referencia (mismo cantidad_real que
     * alimenta 'buenas'), así el largo total de la barra coincide con el ranking.
     */
    function renderChartPulidoMixReferencias(profundo) {
        const ctx = document.getElementById('chartPulidoMixRef');
        if (!ctx) return;

        cachePulidoProfundo = profundo || cachePulidoProfundo || {};
        profundo = cachePulidoProfundo;
        limpiarErrorEnCanvas(ctx);
        initToggleMixPulido();

        const opRef = (cacheAnalyticsPulido && cacheAnalyticsPulido.operario_referencia) || {};
        const enPorcentaje = document.getElementById('switch-pulido-mix-pct')?.checked === true;

        // Mismo orden y mismo Top 10 que el ranking de piezas
        const ops = Object.keys(profundo)
            .map(k => ({ nombre: k, buenas: profundo[k].buenas || 0 }))
            .sort((a, b) => b.buenas - a.buenas)
            .slice(0, 10)
            .filter(o => opRef[o.nombre]);

        if (chartPulidoMixRefInst) chartPulidoMixRefInst.destroy();
        chartPulidoMixRefInst = null;

        if (ops.length === 0) {
            mostrarErrorEnCanvas(ctx, 'No hay desglose por referencia de Pulido en este rango.');
            return;
        }

        const labels = ops.map(o => o.nombre);

        // Referencias ordenadas por volumen total entre las operarias visibles
        const totalPorRef = {};
        ops.forEach(o => {
            const refs = opRef[o.nombre] || {};
            Object.keys(refs).forEach(r => {
                totalPorRef[r] = (totalPorRef[r] || 0) + (refs[r].cantidad_total || 0);
            });
        });
        const refsOrdenadas = Object.keys(totalPorRef).sort((a, b) => totalPorRef[b] - totalPorRef[a]);
        const limiteRefs = limiteReferenciasMix();
        const refsTop = refsOrdenadas.slice(0, limiteRefs);
        const refsResto = refsOrdenadas.slice(limiteRefs);

        // Total por operaria SEGÚN el mismo desglose: la base del % debe ser lo
        // que realmente se está pintando, no 'buenas', para que los tramos
        // sumen exactamente 100% en el modo porcentual.
        const totalPorOp = ops.map(o => {
            const refs = opRef[o.nombre] || {};
            return Object.keys(refs).reduce((acc, r) => acc + (refs[r].cantidad_total || 0), 0);
        });

        // Techo del eje X con margen extra a propósito ("hipódromo"): la meta
        // se dibuja en el borde derecho del área del gráfico, así que si el
        // máximo del eje coincidiera con la barra más larga, la meta quedaría
        // pegada a quien va ganando.
        // Meta mínima diaria pedida por planta 2026-09-02: 2000 piezas fijas
        // mientras nadie las alcance (la meta NO se achica solo porque el
        // rango filtrado tenga pocos datos); una vez el líder llega/supera
        // esa meta, ahí sí empieza a subir un 30% por encima del real -- el
        // comportamiento "casi alcanzable" que ya teníamos.
        const META_DIARIA_PIEZAS = 2000;
        const maxTotalOp = Math.max(1, ...totalPorOp);
        const axisMax = enPorcentaje
            ? 120
            : (maxTotalOp >= META_DIARIA_PIEZAS
                ? Math.ceil(maxTotalOp * 1.3 / 100) * 100
                : META_DIARIA_PIEZAS);

        const valorDe = (nombreOp, idxOp, refsDeLaSerie) => {
            const refs = opRef[nombreOp] || {};
            const qty = refsDeLaSerie.reduce((acc, r) => acc + ((refs[r] || {}).cantidad_total || 0), 0);
            if (!enPorcentaje) return qty;
            const tot = totalPorOp[idxOp];
            // Sin redondear: con 12 tramos, redondear cada uno a 1 decimal hace
            // que la barra sume 100.2% y el ultimo tramo se recorte contra max:100.
            return tot > 0 ? (qty / tot) * 100 : 0;
        };

        const datasets = refsTop.map((ref, i) => ({
            label: ref,
            data: ops.map((o, idxOp) => valorDe(o.nombre, idxOp, [ref])),
            backgroundColor: colorParaReferencia(i),
            borderWidth: 0,
            borderRadius: 3,
            barThickness: 22
        }));

        if (refsResto.length > 0) {
            datasets.push({
                label: `Otras referencias (${refsResto.length})`,
                data: ops.map((o, idxOp) => valorDe(o.nombre, idxOp, refsResto)),
                backgroundColor: 'rgba(148, 163, 184, 0.85)',
                borderWidth: 0,
                borderRadius: 3,
                barThickness: 22
            });
        }

        chartPulidoMixRefInst = new Chart(ctx, {
            type: 'bar',
            data: { labels: labels, datasets: datasets },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { boxWidth: 10, boxHeight: 10, font: { size: 10 }, padding: 8, usePointStyle: true, pointStyle: 'rectRounded' }
                    },
                    tooltip: {
                        callbacks: {
                            label: (c) => {
                                const idxOp = c.dataIndex;
                                const tot = totalPorOp[idxOp] || 0;
                                if (enPorcentaje) {
                                    const pct = c.raw || 0;
                                    const pz = Math.round((pct / 100) * tot);
                                    return ` ${c.dataset.label}: ${pct.toFixed(1)}% (${pz.toLocaleString()} pz)`;
                                }
                                const pz = c.raw || 0;
                                const pct = tot > 0 ? (pz / tot) * 100 : 0;
                                return ` ${c.dataset.label}: ${Math.round(pz).toLocaleString()} pz (${pct.toFixed(1)}%)`;
                            },
                            footer: (items) => {
                                const idxOp = items && items[0] ? items[0].dataIndex : undefined;
                                if (idxOp === undefined) return '';
                                return `Total operaria: ${(totalPorOp[idxOp] || 0).toLocaleString()} pz`;
                            }
                        },
                        itemSort: (a, b) => b.raw - a.raw
                    }
                },
                scales: {
                    x: {
                        stacked: true,
                        beginAtZero: true,
                        max: axisMax,
                        grid: { color: 'rgba(0,0,0,0.05)' },
                        ticks: { callback: (v) => enPorcentaje ? `${v}%` : v.toLocaleString() },
                        title: {
                            display: true,
                            text: enPorcentaje ? '% de la producción de cada operaria' : 'Unidades físicas producidas por referencia',
                            font: { size: 11 }
                        }
                    },
                    y: { stacked: true, grid: { display: false }, ticks: { font: { weight: 'bold' } } }
                }
            },
            plugins: [{
                id: 'iconosCarreraMix',
                afterDatasetsDraw: (chart) => posicionarIconosCarreraMix(chart, ops.length, totalPorOp, enPorcentaje)
            }]
        });
    }

    // Colores del "calor" de la carrera: rojo intenso para el 1er lugar,
    // enfriando hacia gris azulado para el último -- interpolación RGB simple.
    const COLOR_CALOR_TOP = [239, 68, 68];    // #ef4444
    const COLOR_CALOR_BOTTOM = [148, 163, 184]; // #94a3b8

    function colorCalorPorRango(idx, total) {
        const t = total > 1 ? idx / (total - 1) : 0;
        const [r1, g1, b1] = COLOR_CALOR_TOP;
        const [r2, g2, b2] = COLOR_CALOR_BOTTOM;
        const r = Math.round(r1 + (r2 - r1) * t);
        const g = Math.round(g1 + (g2 - g1) * t);
        const b = Math.round(b1 + (b2 - b1) * t);
        return `rgb(${r}, ${g}, ${b})`;
    }

    /**
     * Pedido del jefe de planta 2026-09-02 (boceto anexado): en la punta de
     * cada barra del Mix de Producción, un caballito corriendo con un efecto
     * de "efervescencia"/calor que varía de intensidad según el puesto en la
     * carrera -- más caliente (rojo, pulso rápido) para quien va primero, más
     * frío (gris azulado, pulso lento) para quien va más abajo. Igual que en
     * el ranking de piezas, se dibuja como overlay HTML sobre el canvas para
     * poder animarlo con CSS puro -- ver .pulido-mix-race-icon en dashboard.css.
     * La posición X se calcula con la escala X del chart sobre el TOTAL real
     * de cada operaria (totalPorOp), no leyendo el último segmento apilado:
     * con "Ver en %" activo, o cuando la referencia más pequeña de una
     * operaria vale 0, el último dataset no siempre queda posicionado en la
     * punta real de la barra -- la escala sí es exacta siempre.
     */
    function posicionarIconosCarreraMix(chart, totalOps, totalPorOp, enPorcentaje) {
        const overlay = document.getElementById('mix-race-icons');
        const xScale = chart.scales && chart.scales.x;
        if (!overlay || !xScale) return;

        const primerMeta = chart.getDatasetMeta(0);
        const canvasRect = chart.canvas.getBoundingClientRect();
        const overlayRect = overlay.getBoundingClientRect();
        const offsetX = canvasRect.left - overlayRect.left;
        const offsetY = canvasRect.top - overlayRect.top;

        // Pista de hipódromo: línea de salida (borde izquierdo del área de
        // dibujo) y meta con bandera (borde derecho) -- el techo del eje X
        // (ver axisMax en renderChartPulidoMixReferencias) ya deja aire
        // suficiente para que la meta nunca quede pegada a la barra líder.
        const area = chart.chartArea;
        const salidaX = area.left + offsetX;
        const metaX = area.right + offsetX;
        const pistaTop = area.top + offsetY;
        const pistaAlto = area.bottom - area.top;

        let html = `
            <div style="position:absolute; left:${salidaX}px; top:${pistaTop}px; width:0; height:${pistaAlto}px; border-left:2px dashed rgba(148,163,184,0.55);"></div>
            <div style="position:absolute; left:${metaX}px; top:${pistaTop}px; width:0; height:${pistaAlto}px; border-left:2px dashed rgba(100,116,139,0.6);"></div>
            <div class="mix-race-meta" style="left:${metaX - 9}px; top:${pistaTop - 22}px;">🏁<span>META</span></div>
        `;

        for (let i = 0; i < totalOps; i++) {
            const el = primerMeta.data[i];
            if (!el) continue;
            const valorTotal = enPorcentaje ? 100 : (totalPorOp[i] || 0);
            const x = xScale.getPixelForValue(valorTotal) + offsetX + 4;
            const y = el.y + offsetY;
            const color = colorCalorPorRango(i, totalOps);
            // Pedido del usuario 2026-09-02: el líder debe verse claramente
            // más rápido (0.45s, fijo), y de ahí en adelante cada puesto un
            // 10% más lento que el anterior (no un rango lineal fijo) -- así
            // se ajusta solo sin importar cuántas operarias haya. Con tope en
            // 1.35s para que ninguna se vea "congelada" aunque la lista sea
            // larga (probado hasta 10 operarias, ver slice(0,10) en ops).
            const duracion = i === 0
                ? '0.45'
                : Math.min(1.35, 0.9 * Math.pow(1.10, i - 1)).toFixed(2);
            html += `<span class="pulido-mix-race-icon" style="left:${x}px; top:${y}px; --heat-color:${color}; animation-duration:${duracion}s;">🐎</span>`;
        }

        // afterDatasetsDraw se dispara en CADA redraw del chart, incluyendo
        // los que Chart.js hace por simple hover/tooltip (con el mouse cerca
        // del gráfico puede ser varias veces por segundo). Reescribir el
        // innerHTML ahí destruye y recrea los <span>, reiniciando su
        // animación CSS desde el frame 0 cada vez -- eso es el "delay" o
        // trabado que reportó el usuario 2026-09-02, no su dispositivo.
        // Solución: solo tocar el DOM si el layout calculado cambió de
        // verdad (resize real, o cambio de datos), nunca en un redraw
        // idéntico por hover.
        if (overlay.dataset.firma === html) return;
        overlay.dataset.firma = html;
        overlay.innerHTML = html;
    }

    function initToggleMixPulido() {
        const sw = document.getElementById('switch-pulido-mix-pct');
        if (!sw || sw.dataset.listenerAttached === 'true') return;
        sw.dataset.listenerAttached = 'true';
        sw.addEventListener('change', () => renderChartPulidoMixReferencias(cachePulidoProfundo || {}));
    }


    function renderTablaPulido(profundo, evolucion) {
        const tbody = document.querySelector('#tabla-leaderboard-pulido tbody');
        if (!tbody) return;
        tbody.innerHTML = '';
        evolucion = evolucion || {};

        // Mostrar TODAS las operadoras
        const operadoras = Object.keys(profundo);
        if (operadoras.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-muted">No hay datos de pulido en este rango.</td></tr>';
            return;
        }

        // Ordenar por volumen físico (Unidades Totales) desc
        const totalDe = (op) => (profundo[op].buenas || 0) + (profundo[op].pnc || 0);
        const sortedOps = operadoras.sort((a, b) => totalDe(b) - totalDe(a));

        sortedOps.forEach((op, idx) => {
            const dataOp = profundo[op];
            const mix = dataOp.mix || [];
            const buenas = dataOp.buenas || 0;
            const pnc = dataOp.pnc || 0;
            const costoPnc = dataOp.costo_pnc || 0;

            const total = buenas + pnc;
            // Evitar el "100%" falso si hay scrap (PNC)
            const calidadRaw = total > 0 ? (buenas / total) * 100 : 100;
            const calidadYield = (buenas > 0 && pnc > 0 && calidadRaw > 99) ? calidadRaw.toFixed(2) : Math.round(calidadRaw);
            const ylColor = calidadRaw > 95 ? 'success' : (calidadRaw > 85 ? 'warning' : 'danger');

            // pulido_profundo no trae 'mix': la referencia top se saca del
            // desglose por referencia (misma fuente que el modal).
            const refsOp = cacheAnalyticsPulido?.operario_referencia?.[op] || null;
            let topProd = mix[0] ? mix[0].prod : "N/A";
            if (refsOp) {
                const topRef = Object.keys(refsOp)
                    .sort((a, b) => (refsOp[b].cantidad_total || 0) - (refsOp[a].cantidad_total || 0))[0];
                if (topRef) topProd = topRef;
            }

            // Preparar el detalle para el alert combinando analytics_pulido si existe
            let detalleMixStr = '';
            if (cacheAnalyticsPulido && cacheAnalyticsPulido.operario_referencia && cacheAnalyticsPulido.operario_referencia[op]) {
                const refs = cacheAnalyticsPulido.operario_referencia[op];
                // refs es un objeto { [ref]: { cantidad_total, costo_unidad,
                // hora_inicio, hora_fin, minutos, min_por_pieza, lotes } }
                const arr = Object.keys(refs).map(r => ({
                    ref: r,
                    cantidad: refs[r].cantidad_total || 0,
                    costo_u: refs[r].costo_unidad || 0,
                    ini: refs[r].hora_inicio || null,
                    fin: refs[r].hora_fin || null,
                    min: refs[r].minutos || 0,
                    // null (no 0) = la operaria no capturó tiempo en esa referencia.
                    // spz viene ya calculado en el backend desde 'minutos' crudo (no
                    // desde min_por_pieza, que está redondeado a 2 decimales) -- ver
                    // PulidoService.get_detalle_por_referencia.
                    spz: (refs[r].seg_por_pieza === null || refs[r].seg_por_pieza === undefined) ? null : refs[r].seg_por_pieza,
                    lotes: refs[r].lotes || 0
                })).sort((a, b) => b.cantidad - a.cantidad);
                detalleMixStr = encodeURIComponent(JSON.stringify(arr));
            } else {
                const arr = mix.slice(0, 5).map(p => ({
                    ref: p.prod,
                    cantidad: p.qty,
                    costo_u: 0
                }));
                detalleMixStr = encodeURIComponent(JSON.stringify(arr));
            }

            const isChecked = selectedOperators.some(s => s.nombre === op);

            // Evolución vs período anterior de igual duración (ver
            // PulidoService.get_evolucion_operarias). pct null = sin base de
            // comparación real (la operaria no tuvo actividad en el período
            // anterior) -- se muestra neutro, no como una caída a -100%.
            const evolOp = evolucion[op];
            const pctVol = evolOp?.pct_volumen;
            let evolHtml = '<span class="text-muted small">—</span>';
            if (pctVol !== null && pctVol !== undefined) {
                const subiendo = pctVol >= 0;
                const evolColor = subiendo ? 'success' : 'danger';
                const evolIcon = subiendo ? 'fa-arrow-up' : 'fa-arrow-down';
                evolHtml = `<span class="fw-bold text-${evolColor}"><i class="fas ${evolIcon} me-1"></i>${Math.abs(pctVol)}%</span>`;
            }

            const tr = document.createElement('tr');
            tr.style.cursor = 'pointer';
            tr.onclick = (e) => {
                // Si es clic en checkbox, lo maneja el onchange
                if (e.target.closest('.comparison-checkbox')) return;
                // Si es clic en botón (si hubiera), lo maneja el botón
                if (e.target.closest('button')) return;
                const insightText = dataOp.insight || "Sin insights disponibles para Pulido.";
                mostrarModalOperador(op, buenas, 'Pulido', insightText, detalleMixStr, dataOp.pnc_operario, dataOp.pnc_maquina);
            };
            tr.classList.add('hover-scale');
            tr.innerHTML = `
                <td class="ps-4">
                    ${idx === 0 ? '<i class="fas fa-medal text-warning fs-5" title="Líder en Producción"></i>' : `<span class="badge bg-light text-dark border">${idx + 1}</span>`}
                </td>
                <td class="text-center">
                    <input type="checkbox" class="comparison-checkbox form-check-input"
                        ${isChecked ? 'checked' : ''}
                        data-op="${op}"
                        onchange="window.ModuloDashboard.toggleOperatorSelection('${op}', ${buenas}, ${pnc}, ${costoPnc}, ${calidadYield}, ${dataOp.tiempo_estandar || 0}, '${detalleMixStr}')">
                </td>
                <td>
                    <div class="fw-bold">${op}</div>
                    <small class="text-muted">Expertiz: <span class="badge bg-info text-white">${topProd}</span></small>
                </td>
                <td class="text-center fw-bold text-dark">${total.toLocaleString()}</td>
                <td>
                    <div class="d-flex align-items-center gap-2">
                        <div class="progress flex-grow-1" style="height: 8px; border-radius: 10px; background-color: #e2e8f0;">
                            <div class="progress-bar bg-${ylColor}" style="width: ${calidadYield}%"></div>
                        </div>
                        <span class="fw-bold text-${ylColor}" style="min-width: 40px;">${calidadYield}%</span>
                    </div>
                </td>
                <td class="text-center">${evolHtml}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    let cacheEficienciaPulido = null;

    function renderTablaEficienciaPulido(eficienciaData) {
        const tbody = document.querySelector('#tabla-eficiencia-pulido-ref tbody');
        if (!tbody) return;

        cacheEficienciaPulido = eficienciaData || {};
        const referencias = Object.keys(cacheEficienciaPulido);

        if (referencias.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">No hay datos de velocidad por referencia en este rango.</td></tr>';
            return;
        }

        filtrarTablaEficienciaPulido('');
        initBusquedaEficienciaPulido();
    }

    function filtrarTablaEficienciaPulido(query) {
        const tbody = document.querySelector('#tabla-eficiencia-pulido-ref tbody');
        if (!tbody || !cacheEficienciaPulido) return;

        const q = (query || '').toLowerCase().trim();
        const refs = Object.keys(cacheEficienciaPulido).filter(r => {
            if (!q) return true;
            const item = cacheEficienciaPulido[r];
            const topOp = (item.operario_mas_rapido || '').toLowerCase();
            return r.toLowerCase().includes(q) || topOp.includes(q);
        });

        if (refs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">No se encontraron referencias que coincidan con la búsqueda.</td></tr>';
            return;
        }

        let html = '';
        refs.forEach(r => {
            const item = cacheEficienciaPulido[r];
            const topOp = item.operario_mas_rapido || 'S/R';
            const speed = (item.velocidad_pz_min || 0).toFixed(2);
            const timePz = (item.tiempo_min_pz || 0).toFixed(4);

            html += `
                <tr>
                    <td class="ps-4">
                        <span class="fw-bold text-dark">${r}</span>
                    </td>
                    <td>
                        <span class="badge bg-light text-dark border me-1 py-1 px-2"><i class="fas fa-running text-success me-1"></i>${topOp}</span>
                    </td>
                    <td class="text-center fw-bold text-success" style="font-family: 'JetBrains Mono', monospace;">
                        ${speed} pz/min
                    </td>
                    <td class="text-center pe-4 text-muted" style="font-family: 'JetBrains Mono', monospace;">
                        ${timePz} min/pz
                    </td>
                </tr>
            `;
        });

        tbody.innerHTML = html;
    }

    function initBusquedaEficienciaPulido() {
        const inputEl = document.getElementById('input-busqueda-eficiencia-pulido');
        if (inputEl && inputEl.dataset.listenerAttached !== 'true') {
            inputEl.dataset.listenerAttached = 'true';
            inputEl.addEventListener('input', debounce((e) => {
                filtrarTablaEficienciaPulido(e.target.value);
            }, 300));
        }
    }


    function aplicarPermisosVisuales() {
        console.log("🔒 Aplicando permisos visuales al Dashboard IA...");

        let rolUsuario = 'DESCONOCIDO';
        if (window.AppState && window.AppState.user && window.AppState.user.rol) {
            rolUsuario = window.AppState.user.rol.toUpperCase();
        } else if (window.AuthModule && window.AuthModule.currentUser && window.AuthModule.currentUser.rol) {
            rolUsuario = window.AuthModule.currentUser.rol.toUpperCase();
        }

        // Mapear roles antiguos/generales a los roles de acceso que configuramos en HTML
        let rolesEfectivos = [];
        if (['ADMIN', 'ADMINISTRACION', 'ADMINISTRACIÓN', 'ADMINISTRADOR', 'GERENCIA'].includes(rolUsuario)) {
            rolesEfectivos.push('ADMIN');
            rolesEfectivos.push('GERENCIA');
        }
        if (rolUsuario.includes('INYECCION')) rolesEfectivos.push('INYECCION');
        if (rolUsuario.includes('PULIDO')) rolesEfectivos.push('PULIDO');
        if (['COMERCIAL', 'VENTAS'].includes(rolUsuario)) rolesEfectivos.push('COMERCIAL');

        console.log(`Rol Usuario: ${rolUsuario} -> Evaluando como [${rolesEfectivos.join(', ')}]`);

        // Preparar flow-container para reordenar
        const container = document.querySelector('#dashboard-page .container-fluid');
        if (container) {
            container.classList.add('d-flex', 'flex-column');
        }

        const chartPulido = document.getElementById('dashboard-section-pulido-leaderboard');
        const tablaPulido = document.getElementById('dashboard-section-pulido-table');

        if (rolUsuario.includes('PULIDO')) {
            if (chartPulido) chartPulido.style.order = '-2';
            if (tablaPulido) tablaPulido.style.order = '-1';
        } else {
            if (chartPulido) chartPulido.style.order = '';
            if (tablaPulido) tablaPulido.style.order = '';
        }

        const secciones = document.querySelectorAll('[data-role-access]');
        secciones.forEach(seccion => {
            const accessStr = seccion.getAttribute('data-role-access');
            if (!accessStr) return;

            const rolesPermitidos = accessStr.split(',').map(r => r.trim().toUpperCase());

            // Ver si al menos uno de los roles efectivos está permitido en esta sección
            const accesoPermitido = rolesEfectivos.some(r => rolesPermitidos.includes(r));

            if (accesoPermitido) {
                // Mantener visibilidad original (block o flex, quitar d-none preventivo)
                seccion.style.display = '';
            } else {
                // Ocultar sección completa
                seccion.style.setProperty('display', 'none', 'important');
            }
        });
    }

    // Secciones saltables desde el índice rápido -- pedido del usuario
    // 2026-09-02: el Dashboard IA es larguísimo, y en vez de scrollear todo
    // quiere poder dar clic e ir directo a la parte que le interesa. IDs
    // tomados de los que ya existía cada fila (dashboard-section-*) o,
    // donde no había ninguno, uno nuevo agregado solo para esto
    // (dash-nav-*) -- ver frontend/templates/index.html.
    const SECCIONES_DASHBOARD_NAV = [
        { id: 'dashboard-section-metrics-admin', label: 'Resumen', icon: 'fa-bolt' },
        { id: 'dash-nav-comparativo-anual', label: 'Comparativo Anual', icon: 'fa-chart-area' },
        { id: 'dash-nav-rendimiento-mensual', label: 'Rendimiento Mensual', icon: 'fa-chart-bar' },
        { id: 'dashboard-section-incumplimiento', label: 'Backorder', icon: 'fa-history' },
        { id: 'dash-nav-top-productos', label: 'Top Productos', icon: 'fa-star' },
        { id: 'dashboard-section-sin-rotacion', label: 'Sin Rotación', icon: 'fa-boxes' },
        { id: 'dashboard-section-inyeccion', label: 'Inyección', icon: 'fa-industry' },
        { id: 'dashboard-section-tendencia', label: 'Tendencia', icon: 'fa-chart-line' },
        { id: 'dash-nav-scrap-pnc', label: 'Scrap y PNC', icon: 'fa-dumpster' },
        { id: 'dashboard-section-pulido-kpis', label: 'Ranking Pulido', icon: 'fa-medal' },
        { id: 'dashboard-section-pulido-mix', label: 'Mix de Producción', icon: 'fa-layer-group' },
        { id: 'dashboard-section-pulido-table', label: 'Tabla Pulido', icon: 'fa-table' }
    ];

    /**
     * "Pinea" el índice rápido arriba del viewport al scrollear más allá de
     * su posición natural -- a mano con JS y position:fixed, NO con
     * position:sticky (ver comentario largo en dashboard.css sobre por qué
     * #main-content rompe sticky aquí: tiene overflow:auto pero nunca
     * scrollea internamente, así que sticky se calcula contra un scroll que
     * no cambia y el pill se queda pegado a su ancestro en vez de quedar
     * fijo en pantalla).
     */
    function activarPinIndiceRapido(cont) {
        const placeholder = document.getElementById('dashboard-indice-rapido-placeholder');
        const main = document.getElementById('main-content');
        if (!placeholder || !main) return;

        let anclaNatural = null;

        function medirPosicionNatural() {
            if (cont.classList.contains('pinned')) return;
            anclaNatural = cont.getBoundingClientRect().top + window.scrollY;
        }
        medirPosicionNatural();

        function actualizarPin() {
            if (anclaNatural === null) return;
            const mainRect = main.getBoundingClientRect();
            if (window.scrollY >= anclaNatural) {
                if (!cont.classList.contains('pinned')) {
                    placeholder.style.height = `${cont.getBoundingClientRect().height}px`;
                    placeholder.classList.add('active');
                    cont.classList.add('pinned');
                }
                cont.style.left = `${mainRect.left}px`;
                cont.style.width = `${mainRect.width}px`;
            } else if (cont.classList.contains('pinned')) {
                cont.classList.remove('pinned');
                placeholder.classList.remove('active');
                cont.style.left = '';
                cont.style.width = '';
                medirPosicionNatural();
            }
        }

        window.addEventListener('scroll', actualizarPin, { passive: true });
        window.addEventListener('resize', () => { medirPosicionNatural(); actualizarPin(); });
        actualizarPin();
    }

    /**
     * Construye el índice rápido: solo incluye secciones que existen y que
     * aplicarPermisosVisuales() (ya corrida justo antes) dejó visibles --
     * así un pill nunca apunta a una sección oculta por rol. Se ejecuta una
     * sola vez (iniciar() está guardado por isInitialized), así que no hace
     * falta reconstruirlo en cada refresco de datos.
     */
    function inicializarIndiceRapidoDashboard() {
        const cont = document.getElementById('dashboard-indice-rapido');
        if (!cont) return;

        const disponibles = SECCIONES_DASHBOARD_NAV.filter(s => {
            const el = document.getElementById(s.id);
            return el && getComputedStyle(el).display !== 'none';
        });

        if (disponibles.length === 0) {
            cont.innerHTML = '';
            return;
        }

        cont.innerHTML = disponibles.map(s => `
            <button type="button" class="dashboard-quicknav-item" data-target="${s.id}">
                <i class="fas ${s.icon} me-1"></i>${s.label}
            </button>
        `).join('');

        cont.querySelectorAll('.dashboard-quicknav-item').forEach(btn => {
            btn.addEventListener('click', () => {
                const target = document.getElementById(btn.dataset.target);
                if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });

        activarPinIndiceRapido(cont);

        // Resalta el pill de la sección visible en pantalla mientras se hace
        // scroll -- puramente cosmético, si IntersectionObserver no existe
        // el índice sigue funcionando igual (solo sin el resaltado).
        if ('IntersectionObserver' in window) {
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (!entry.isIntersecting) return;
                    const btn = cont.querySelector(`[data-target="${entry.target.id}"]`);
                    if (!btn) return;
                    cont.querySelectorAll('.dashboard-quicknav-item.active').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                });
            }, { rootMargin: '-96px 0px -70% 0px', threshold: 0 });
            disponibles.forEach(s => {
                const el = document.getElementById(s.id);
                if (el) observer.observe(el);
            });
        }
    }


    // Sincroniza el filtro global de fechas del dashboard (inputs + localStorage +
    // recarga de datos) de forma programatica, sin depender de que el usuario haga
    // clic en "Actualizar". La necesita el boton "ver en el modulo" del Asistente
    // IA: si la respuesta del bot fue sobre un periodo distinto al filtro activo
    // (ej. Julio con el dashboard filtrado en Agosto), navegar sin esto dejaba al
    // usuario en la seccion correcta pero mirando datos del periodo viejo.
    async function establecerFiltroFechas(desde, hasta) {
        if (!desde || !hasta) return;
        const f_desde = document.getElementById('db-fecha-desde');
        const f_hasta = document.getElementById('db-fecha-hasta');
        if (f_desde) f_desde.value = desde;
        if (f_hasta) f_hasta.value = hasta;
        localStorage.setItem('db_filtro_desde', desde);
        localStorage.setItem('db_filtro_hasta', hasta);
        await cargarDatos(true);
        await fetchAndRenderMonthlyPerformance('chartMensual', desde, hasta);
    }

    function iniciar() {
        if (isInitialized) {
            console.log("🛡️ Dashboard ya inicializado. Abortando secuencia redundante.");
            return;
        }
        isInitialized = true;
        console.log("🚀 Iniciando BI Dashboard...");

        // Asistente NL -> datos: modulo independiente, no bloquea ni retrasa
        // la carga del resto del dashboard si falla o no esta presente.
        if (window.ModuloAsistente && typeof window.ModuloAsistente.inicializar === 'function') {
            try { window.ModuloAsistente.inicializar(); } catch (e) { console.error('[ModuloAsistente] Error al inicializar:', e); }
        }

        aplicarPermisosVisuales();
        inicializarIndiceRapidoDashboard();

        // Filtros de Incumplimiento (Interactive)
        const inputBusca = document.getElementById('buscador-incumplimiento');

        const filterIncumplimiento = debounce(() => {
            try {
                const term = inputBusca && inputBusca.value ? String(inputBusca.value).toLowerCase().trim() : "";
                const isFiltering = term !== "";

                console.log(`🔍 Filtrando por: "${term}"`);

                const activeTbodyId = 'incumplimiento-consolidado-body';

                // Filtrar Tablas vía Cache (Ultra performance)
                const filtrarTablaPorNodos = (tbodyId) => {
                    const tbody = document.getElementById(tbodyId);
                    if (!tbody) return;

                    requestAnimationFrame(() => {
                        const rows = tbody.querySelectorAll('tr');
                        let count = 0;

                        rows.forEach((row, index) => {
                            const searchStr = row.getAttribute('data-search') || "";
                            const matches = !term || searchStr.indexOf(term) !== -1;

                            // Determinamos el estado objetivo
                            const targetDisplay = matches ? (isFiltering ? '' : (index < 12 ? '' : 'none')) : 'none';

                            // SOLO escribimos al DOM si el estado cambió
                            if (row.style.display !== targetDisplay) {
                                row.style.display = targetDisplay;
                            }

                            if (matches) count++;
                        });

                        // Actualizar contador
                        if (tbodyId === activeTbodyId) {
                            const countSpan = document.getElementById('bo-count');
                            if (countSpan) {
                                countSpan.textContent = isFiltering ? count : inc_consolidado_original.length;
                            }
                        }
                    });
                };

                filtrarTablaPorNodos(activeTbodyId);

            } catch (err) {
                console.error("❌ Error en filterIncumplimiento:", err);
            }
        }, 600);

        if (inputBusca && !searchListenerAttached) {
            inputBusca.addEventListener('input', filterIncumplimiento);
            searchListenerAttached = true;
            console.log("✅ Event Listener del buscador registrado");
        }

        // ── PERSISTENCIA DE FILTROS (localStorage) ────────────────────────────
        const LS_DESDE = 'db_filtro_desde';
        const LS_HASTA = 'db_filtro_hasta';
        const LS_TOGGLE = 'db_toggle_mode';

        const f_desde = document.getElementById('db-fecha-desde');
        const f_hasta = document.getElementById('db-fecha-hasta');
        const btnAplicarFiltro = document.getElementById('btn-actualizar-dashboard');

        // Restaurar desde localStorage o usar valores por defecto (últimos 30 días)
        const hoy = new Date();
        const hace30 = new Date();
        hace30.setDate(hoy.getDate() - 30);

        // Los inputs de fecha ya NO disparan recarga por sí solos (anti-patrón: recargaba
        // el dashboard completo en cada 'change', bloqueando al usuario a mitad de elegir
        // el rango). Solo restauran/persisten su valor; la recarga se dispara
        // explícitamente con el botón #btn-actualizar-dashboard.
        if (f_desde) {
            f_desde.value = localStorage.getItem(LS_DESDE) || hoy.toISOString().split('T')[0]; // Default a HOY
        }
        if (f_hasta) {
            f_hasta.value = localStorage.getItem(LS_HASTA) || hoy.toISOString().split('T')[0]; // Default a HOY
        }

        const aplicarFiltroFechas = async () => {
            if (f_desde) localStorage.setItem(LS_DESDE, f_desde.value);
            if (f_hasta) localStorage.setItem(LS_HASTA, f_hasta.value);
            console.log("📅 Filtro de fechas aplicado por el usuario. Disparando recarga...");

            const btnHtmlOriginal = btnAplicarFiltro ? btnAplicarFiltro.innerHTML : null;
            try {
                if (btnAplicarFiltro) {
                    btnAplicarFiltro.disabled = true;
                    btnAplicarFiltro.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> <span>Analizando...</span>';
                }
                await cargarDatos(true);
                await fetchAndRenderMonthlyPerformance('chartMensual', f_desde?.value || '', f_hasta?.value || '');
            } finally {
                if (btnAplicarFiltro) {
                    btnAplicarFiltro.disabled = false;
                    btnAplicarFiltro.innerHTML = btnHtmlOriginal;
                }
            }
        };

        if (btnAplicarFiltro) {
            btnAplicarFiltro.addEventListener('click', aplicarFiltroFechas);
        }

        const savedToggle = localStorage.getItem(LS_TOGGLE) || 'money';
        window._db_toggleMode = savedToggle;

        cargarDatos();
    }

    function sortIncumplimiento(mode) {
        currentIncSortMode = mode;

        // Update Buttons UI
        document.getElementById('btn-sort-units')?.classList.toggle('active', mode === 'units');
        document.getElementById('btn-sort-money')?.classList.toggle('active', mode === 'money');

        if (!inc_consolidado_original || inc_consolidado_original.length === 0) return;

        let sorted = [...inc_consolidado_original];
        if (mode === 'units') {
            sorted.sort((a, b) => (b.unidades_fallidas || 0) - (a.unidades_fallidas || 0));
        } else {
            sorted.sort((a, b) => (b.dinero_perdido || 0) - (a.dinero_perdido || 0));
        }

        renderTablaIncumplimientoConsolidada(sorted);
    }

    /**
     * Renderiza tabla consolidada de Incumplimiento (Panel Gerencial)
     */
    function renderTablaIncumplimientoConsolidada(data) {
        try {
            const tbody = document.getElementById('incumplimiento-consolidado-body');
            const countSpan = document.getElementById('bo-count');

            // Nuevos IDs para KPIs Globales
            const kpiUnits = document.getElementById('bo-total-units');
            const kpiMoney = document.getElementById('bo-total-money');

            if (!tbody) return;

            if (!Array.isArray(data)) {
                tbody.innerHTML = `<tr><td colspan="3" class="text-center py-4 text-muted small">Error en formato de datos.</td></tr>`;
                return;
            }

            if (countSpan) countSpan.textContent = data.length;

            // Cálculos Globales (Grand Totals) para el Jefe
            let totalUnitsGlobal = 0;
            let totalMoneyGlobal = 0;

            let html = '';
            data.forEach((item, index) => {
                const cli = String(item.cliente || "S/N");
                const units = Number(item.unidades_fallidas) || 0;
                const money = Number(item.dinero_perdido) || 0;

                // Sumatoria
                totalUnitsGlobal += units;
                totalMoneyGlobal += money;

                const searchIndex = `${cli}`.toLowerCase().trim();
                const displayStyle = (index < 12) ? '' : 'none'; // Show top 12 by default
                const safeCli = cli.replace(/'/g, "\\'").replace(/"/g, "&quot;");

                html += `
                    <tr class="table-row-hover" style="display: ${displayStyle}; cursor: pointer; transition: background-color 0.2s;" data-search="${searchIndex}" onclick="window.ModuloDashboard.mostrarDetalleIncumplimiento('${safeCli}')">
                        <td class="ps-4 py-2" style="font-size: 0.85rem;">
                            <div class="fw-bold text-dark text-truncate" style="max-width: 300px;" title="${cli}">${cli}</div>
                        </td>
                        <td class="text-center text-danger fw-bold py-2" style="font-size: 0.95rem;">
                            ${formatNumber(units)}
                        </td>
                        <td class="text-center text-success fw-bold py-2" style="font-size: 0.95rem;">
                            ${formatCOP(money)}
                        </td>
                    </tr>
                `;
            });

            // Inyectar Totales Globales
            if (kpiUnits) kpiUnits.textContent = formatNumber(totalUnitsGlobal);
            if (kpiMoney) kpiMoney.textContent = formatCOP(totalMoneyGlobal);

            tbody.innerHTML = html || '<tr><td colspan="3" class="text-center py-4 text-muted">No hay registros de incumplimiento.</td></tr>';

        } catch (e) {
            console.error("❌ Error en renderTablaIncumplimientoConsolidada:", e);
        }
    }

    /**
     * Alterna la vista de las gráficas entre Dinero y Unidades
     */
    function mostrarModalOperador(nombre, totalOks, proceso, insightStr, detalleMix = null, pnc_propio = 0, pnc_maquina = 0) {

        let extraHtml = '';
        if (detalleMix) {
            let mixLines = '';
            try {
                const arr = JSON.parse(decodeURIComponent(detalleMix));

                // Solo Pulido envía tiempos; si el DTO no los trae (otros
                // procesos) se conserva la tabla corta de siempre.
                const conTiempos = arr.some(i => i.ini || i.fin || i.spz !== null && i.spz !== undefined);

                const fmtDuracion = (mins) => {
                    const m = Math.round(Number(mins) || 0);
                    if (m <= 0) return '—';
                    if (m < 60) return `${m} min`;
                    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
                };

                const filasHtml = arr.map(item => {
                    const celdasTiempo = conTiempos ? `
                            <td class="text-nowrap text-muted">${item.ini || '—'}</td>
                            <td class="text-nowrap text-muted">${item.fin || '—'}</td>
                            <td class="text-nowrap">${fmtDuracion(item.min)}</td>
                            <td class="text-nowrap fw-bold ${item.spz === null || item.spz === undefined ? 'text-muted' : 'text-primary'}" style="font-family: 'JetBrains Mono', monospace;">
                                ${item.spz === null || item.spz === undefined ? '—' : `${Number(item.spz).toFixed(1)} seg/pz`}
                            </td>
                    ` : '';
                    const badgeLotes = (conTiempos && (item.lotes || 0) > 1)
                        ? `<span class="badge bg-light text-secondary border ms-1" title="Suma de ${item.lotes} reportes: la hora de inicio es la del primero y la de fin la del último.">${item.lotes} lotes</span>`
                        : '';
                    return `
                        <tr class="modal-ref-item" data-search="${String(item.ref || '').toLowerCase()}">
                            <td class="text-start fw-bold fs-6"><i class="fas fa-cube text-muted me-1"></i> ${item.ref}${badgeLotes}</td>
                            <td><span class="badge bg-light text-dark border fs-6 px-3 py-2">${(item.cantidad || 0).toLocaleString()}</span></td>
                            ${celdasTiempo}
                        </tr>
                    `;
                }).join('');

                const encabezadosTiempo = conTiempos ? `
                                    <th class="text-nowrap">Hora inicio</th>
                                    <th class="text-nowrap">Hora fin</th>
                                    <th class="text-nowrap">Tiempo total</th>
                                    <th class="text-nowrap">Prom. por pieza</th>
                ` : '';

                mixLines = `
                    <div class="table-responsive mt-3">
                        <table class="table table-hover text-center align-middle" style="font-size: 1rem;">
                            <thead class="table-light text-secondary">
                                <tr style="font-size: 0.8rem;">
                                    <th class="text-start">Referencia</th>
                                    <th>Cantidad</th>
                                    ${encabezadosTiempo}
                                </tr>
                            </thead>
                            <tbody>
                                ${filasHtml}
                            </tbody>
                        </table>
                    </div>
                `;
            } catch (e) {
                console.error("Error parseando detalleMix JSON:", e);
                mixLines = `<p class="text-danger small">Error cargando desglose.</p>`;
            }

            extraHtml = `
                <div class="mt-4 text-start">
                    <h6 class="fw-bold text-uppercase text-secondary mb-3 d-flex flex-column flex-sm-row justify-content-between align-items-sm-center gap-2" style="font-size: 0.8rem; letter-spacing: 1px;">
                        <span><i class="fas fa-list-ol ms-1"></i> Referencias Producidas</span>
                        <div class="input-group input-group-sm rounded-pill overflow-hidden border" style="max-width: 200px;">
                            <span class="input-group-text bg-white border-0"><i class="fas fa-search text-muted"></i></span>
                            <input type="text" id="modal-search-ref" class="form-control border-0 px-1 shadow-none" placeholder="Buscar ref..." style="font-size: 0.8rem;">
                        </div>
                    </h6>
                    <div class="bg-white rounded-3 shadow-sm border p-2" style="max-height: 360px; overflow-y: auto;">
                        ${mixLines}
                        <div id="modal-ref-empty" class="text-center py-4 text-muted d-none">
                            <i class="fas fa-box-open fs-3 mb-2 opacity-50"></i>
                            <p class="mb-0 small">No se encontraron referencias</p>
                        </div>
                    </div>
                </div>
            `;
        }

        const themeColor = proceso.toUpperCase().includes('PULIDO') ? '#10b981' : '#3b82f6';
        const iconProceso = proceso.toUpperCase().includes('PULIDO') ? 'fa-gem' : 'fa-cogs';

        let pncBreakdownHtml = '';
        if (proceso.toUpperCase().includes('PULIDO')) {
            pncBreakdownHtml = `
                <div class="mb-4 text-center p-3 rounded-4 shadow-sm border-0" style="background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);">
                    <span class="text-secondary fw-bold text-uppercase small" style="letter-spacing: 1px;">Desglose de Auditoría PNC</span>
                    <div class="d-flex justify-content-center align-items-center gap-3 mt-2">
                        <div class="d-flex align-items-center">
                            <i class="fas fa-user-edit text-danger me-2"></i>
                            <span class="fw-bold" style="font-size: 0.95rem;">PNC Operación: <span class="text-danger">${(pnc_propio || 0).toLocaleString()}</span></span>
                        </div>
                        <div class="vr" style="height: 20px; opacity: 0.2;"></div>
                        <div class="d-flex align-items-center">
                            <i class="fas fa-tools text-warning me-2"></i>
                            <span class="fw-bold" style="font-size: 0.95rem;">PNC Máquina: <span class="text-warning">${(pnc_maquina || 0).toLocaleString()}</span></span>
                        </div>
                    </div>
                    <div class="mt-2 text-muted x-small" style="font-size: 0.7rem;">* El PNC Máquina no afecta el Yield de Calidad del operario.</div>
                </div>
            `;
        }

        Swal.fire({
            title: null,
            showCloseButton: true,
            html: `
                <div class="modal-body p-0">
                    <div class="p-4" style="background: linear-gradient(135deg, ${themeColor}15 0%, #ffffff 100%); border-radius: 24px 24px 0 0;">
                        <div class="d-flex align-items-center justify-content-between mb-4">
                            <div class="d-flex align-items-center">
                                <div class="icon-circle bg-white shadow-sm me-3" style="width: 54px; height: 54px; display: flex; align-items: center; justify-content: center; border-radius: 16px; color: ${themeColor};">
                                    <i class="fas ${iconProceso} fs-3"></i>
                                </div>
                                <div>
                                    <h4 class="fw-bold mb-0 text-dark">${nombre}</h4>
                                    <span class="badge rounded-pill px-3 py-1" style="background-color: ${themeColor}20; color: ${themeColor}; font-weight: 700; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px;">${proceso}</span>
                                </div>
                            </div>
                        </div>

                        ${pncBreakdownHtml}

                        <div class="row g-3">
                            <div class="col-12 col-md-4">
                                <div class="card shadow-none border rounded-4 p-3 h-100 text-center">
                                    <span class="text-muted small text-uppercase fw-bold mb-1" style="font-size: 0.65rem;">Piezas Buenas</span>
                                    <div class="fs-4 fw-bold text-dark">${totalOks.toLocaleString()}</div>
                                </div>
                            </div>
                            <div class="col-12 col-md-8">
                                <div class="card shadow-none border rounded-4 p-3 h-100 text-center bg-light">
                                    <span class="text-muted small text-uppercase fw-bold mb-1" style="font-size: 0.65rem;">Sugerencia IA</span>
                                    <div class="small fw-medium text-secondary" style="line-height: 1.2;">${insightStr}</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="p-4 bg-white">
                        ${extraHtml}
                    </div>
                </div>
            `,
            showConfirmButton: true,
            confirmButtonText: '<i class="fas fa-times me-1"></i> Cerrar Resumen',
            confirmButtonColor: '#334155',
            customClass: { confirmButton: 'btn rounded-pill px-4 shadow-sm' },
            width: window.innerWidth > 1200 ? '75em' : '95%',
            didOpen: () => {
                const searchInput = document.getElementById('modal-search-ref');
                if (searchInput) {
                    searchInput.addEventListener('input', (e) => {
                        const term = e.target.value.toLowerCase().trim();
                        const items = document.querySelectorAll('.modal-ref-item');
                        let count = 0;
                        items.forEach(item => {
                            const searchStr = item.getAttribute('data-search') || '';
                            if (searchStr.includes(term)) {
                                item.style.setProperty('display', '', 'important');
                                count++;
                            } else {
                                item.style.setProperty('display', 'none', 'important');
                            }
                        });
                        const emptyMsg = document.getElementById('modal-ref-empty');
                        if (emptyMsg) {
                            if (count === 0) emptyMsg.classList.remove('d-none');
                            else emptyMsg.classList.add('d-none');
                        }
                    });
                }
            }
        });
    }

    async function mostrarDetalleOperadorInyeccion(nombre) {
        const nombreSeguro = String(nombre || '').trim() || 'Operario';

        Swal.fire({
            title: `<i class="fas fa-industry text-primary mb-2"></i><br>Detalle de Inyección: ${nombreSeguro}`,
            html: `<div class="text-center py-4"><div class="spinner-border text-primary" role="status"></div><p class="mt-2 text-muted small">Cargando lotes de producción...</p></div>`,
            width: window.innerWidth > 768 ? '40em' : '95%',
            showConfirmButton: true,
            confirmButtonText: 'Cerrar',
            confirmButtonColor: '#3b82f6',
            didOpen: () => {
                Swal.showLoading();
            }
        });

        // Guardia defensiva: DTO vacío/mal formado nunca debe dejar celdas en blanco o números desalineados
        const sanitizarFila = (item) => ({
            responsable: (item && String(item.responsable || '').trim()) || 'S/R',
            id_codigo: (item && String(item.id_codigo || '').trim()) || 'S/C',
            unidades_buenas: Number(item && item.unidades_buenas) || 0,
            cavidades: Number(item && item.cavidades) || 1,
            duracion_minutos: Number(item && item.duracion_minutos) || 0,
            fecha: (item && String(item.fecha || '').trim()) || 'Sin fecha'
        });

        try {
            const params = new URLSearchParams({ responsable: nombreSeguro });
            const desde = document.getElementById('db-fecha-desde')?.value || '';
            const hasta = document.getElementById('db-fecha-hasta')?.value || '';
            if (desde) params.append('desde', desde);
            if (hasta) params.append('hasta', hasta);

            const res = await fetch(`/api/dashboard/drilldown/inyeccion/operador?${params.toString()}`, {
                headers: construirAuthHeaders({ 'Accept': 'application/json' }),
                credentials: 'include'
            });

            if (!res.ok) throw new Error(`HTTP ${res.status}: no fue posible obtener el detalle del operador.`);
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Error al obtener el detalle del operador.');

            const filas = (data.detalle || []).map(sanitizarFila);

            let totalBuenas = 0;
            let totalMinutos = 0;
            let filasHtml = `<tr><td colspan="4" class="text-center text-muted py-4"><i class="fas fa-box-open fs-3 mb-2 opacity-50 d-block"></i>No hay lotes de producción registrados</td></tr>`;

            if (filas.length > 0) {
                filasHtml = filas.map(f => {
                    totalBuenas += f.unidades_buenas;
                    totalMinutos += f.duracion_minutos;
                    return `
                    <tr>
                        <td class="text-start fw-medium" data-label="Referencia"><i class="fas fa-cube text-muted me-1"></i> ${f.id_codigo}</td>
                        <td class="text-center" data-label="Cavidades">${f.cavidades}</td>
                        <td class="text-center" data-label="Buenas"><span class="badge bg-success text-white">${f.unidades_buenas.toLocaleString()}</span></td>
                        <td class="text-end text-muted small" data-label="Fecha">${f.fecha}</td>
                    </tr>
                    `;
                }).join('');
            }

            Swal.update({
                html: `
                    <div class="row g-3 mb-4">
                        <div class="col-6">
                            <div class="card shadow-none border rounded-3 p-2 bg-light">
                                <span class="small text-muted fw-bold">Piezas Buenas</span>
                                <h4 class="text-success mb-0">${totalBuenas.toLocaleString()}</h4>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="card shadow-none border rounded-3 p-2 bg-light">
                                <span class="small text-muted fw-bold">Minutos Totales</span>
                                <h4 class="text-primary mb-0">${totalMinutos.toLocaleString()}</h4>
                            </div>
                        </div>
                    </div>
                    <div class="table-responsive" style="max-height: 320px;">
                        <table class="table table-hover align-middle small mb-0 responsive-mobile" style="width: 100% !important; min-width: 100% !important; table-layout: fixed;">
                            <thead class="bg-light sticky-top">
                                <tr>
                                    <th class="text-start">Referencia</th>
                                    <th class="text-center">Cavidades</th>
                                    <th class="text-center">Buenas</th>
                                    <th class="text-end">Fecha</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${filasHtml}
                            </tbody>
                        </table>
                    </div>
                `
            });
        } catch (err) {
            console.error('Error al cargar detalle de operador de inyección:', err);
            Swal.update({
                html: `<div class="text-center text-danger py-4"><i class="fas fa-triangle-exclamation fs-3 mb-2 d-block"></i>${err.message}</div>`
            });
        }
    }

    function mostrarModalTodosInyeccion(ops) {
        if (!ops || ops.length === 0) return Swal.fire("Aviso", "No hay operadores de inyección", "info");

        // Construir tabla
        let rowsHtml = ops.map((op, idx) => {
            const topProd = op.mix && op.mix[0] ? op.mix[0].prod : "N/A";
            const nombreEscaped = op.nombre.replace(/'/g, "\\'");

            return `
                <tr style="cursor: pointer" onclick="ModuloDashboard.mostrarDetalleOperadorInyeccion('${nombreEscaped}')">
                    <td class="ps-3"><span class="badge bg-light text-dark border">${idx + 1}</span></td>
                    <td class="text-start">
                        <div class="fw-bold">${op.nombre}</div>
                        <small class="text-muted">Expertiz: <span class="badge bg-info text-white">${topProd}</span></small>
                    </td>
                    <td class="text-center fw-bold text-success">${op.valor.toLocaleString()}</td>
                </tr>
                `;
        }).join('');

        Swal.fire({
            title: `<i class="fas fa-users text-primary mb-2"></i><br>Todos los Operadores (Inyección)`,
            html: `
                <div class="table-responsive" style="max-height: 400px;">
                    <table class="table table-hover align-middle small mb-0" style="width: 100% !important; min-width: 100% !important; table-layout: fixed;">
                        <thead class="bg-light sticky-top">
                            <tr>
                                <th style="width: 15%;">#</th>
                                <th class="text-start" style="width: 55%;">Operador</th>
                                <th class="text-center" style="width: 30%;">Piezas Buenas</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rowsHtml}
                        </tbody>
                    </table>
                </div>
                <div class="mt-2 text-muted small"><i class="fas fa-hand-pointer"></i> Haz clic en un operador para ver su detalle</div>
                `,
            width: window.innerWidth > 768 ? '36em' : '100%',
            showConfirmButton: true,
            confirmButtonText: 'Cerrar',
            confirmButtonColor: '#3b82f6'
        });
    }

    // --- Helpers de Formateo ---
    const formatCOP = (num) => new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }).format(num);
    const formatNumber = (num) => new Intl.NumberFormat('es-CO').format(num);

    /**
     * Pinta el nodo '#total-consolidado-anual' con el total de AMBOS años
     * (no solo el actual) más el % de crecimiento/decrecimiento entre ellos --
     * pedido del usuario 2026-09-02. Compartido por renderChartMensual y
     * renderChartMonthlyPerformance: ambos repintan el mismo <canvas
     * id="chartMensual"> y por lo tanto el mismo total.
     */
    function renderTotalConsolidadoAnual(yearActual, totalActual, yearPrev, totalPrev, fmt) {
        const totalNode = document.getElementById('total-consolidado-anual');
        if (!totalNode) return;

        let crecimientoHtml = '';
        if (totalPrev > 0) {
            const pct = ((totalActual - totalPrev) / totalPrev) * 100;
            const esCrecimiento = pct >= 0;
            const claseTrend = esCrecimiento ? 'kpi-trend-up' : 'kpi-trend-down';
            const icono = esCrecimiento ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down';
            crecimientoHtml = ` <span class="${claseTrend}"><i class="fas ${icono} me-1"></i>${Math.abs(pct).toFixed(1)}%</span>`;
        }

        totalNode.innerHTML = `Total ${yearActual}: ${fmt(totalActual)}` +
            `<span class="text-muted fw-normal mx-2">|</span>` +
            `Total ${yearPrev}: ${fmt(totalPrev)}` +
            crecimientoHtml;
    }

    function renderChartMensual(datosMensuales, mode = 'money') {
        const ctx = document.getElementById('chartMensual');
        if (!ctx || !Array.isArray(datosMensuales) || datosMensuales.length === 0) return;
        limpiarErrorEnCanvas(ctx);

        try {
            let datosFiltrados = datosMensuales;
            const hastaInput = document.getElementById('db-fecha-hasta');
            if (hastaInput?.value) {
                const parts = hastaInput.value.split('-');
                if (parts.length === 3) {
                    const limitMonth = parseInt(parts[1], 10);
                    if (!isNaN(limitMonth) && limitMonth > 0 && limitMonth <= 12 && datosFiltrados.length > limitMonth) {
                        datosFiltrados = datosFiltrados.slice(0, limitMonth);
                    }
                }
            }

            const labels = datosFiltrados.map(d => d.mes);
            // Número real de calendario (1-12) tal como lo calculó el backend
            // (DashboardRepository.get_rendimiento_mensual_sql: campo "mes_num"). Se guarda
            // posicionalmente junto a "labels" — NUNCA se reconstruye parseando el label.
            const mesesReales = datosFiltrados.map(d => Number(d.mes_num));
            const isMoney = mode === 'money';

            // Año desde los inputs reales del filtro
            const yearActual = hastaInput?.value
                ? new Date(hastaInput.value).getFullYear()
                : new Date().getFullYear();
            const yearPrev = yearActual - 1;

            // Datos
            const dataActualVentas = datosFiltrados.map(d => isMoney ? (d.actual_dinero || 0) : (d.actual_unidades || 0));
            const dataPrevVentas = datosFiltrados.map(d => isMoney ? (d.prev_dinero || 0) : (d.prev_unidades || 0));
            const dataActualPedidos = datosFiltrados.map(d => isMoney ? (d.actual_pedidos || 0) : (d.actual_pedidos_unidades || 0));
            const dataPrevPedidos = datosFiltrados.map(d => isMoney ? (d.prev_pedidos || 0) : (d.prev_pedidos_unidades || 0));

            // Ocultar líneas de Pedidos en Unidades si no hay datos reales
            const hayPedidosUnidades = !isMoney && dataActualPedidos.some(v => v > 0);

            // Suma consolidada del año actual (nodo DOM, cero texto estático embebido en el canvas)
            const totalConsolidadoActual = dataActualVentas.reduce((acc, v) => acc + v, 0);
            const totalConsolidadoPrev = dataPrevVentas.reduce((acc, v) => acc + v, 0);
            const fmt = isMoney ? formatCOP : (v => formatNumber(v) + ' unds');
            renderTotalConsolidadoAnual(yearActual, totalConsolidadoActual, yearPrev, totalConsolidadoPrev, fmt);

            // Destruir cualquier Chart.js atado a este canvas, sin importar qué función
            // lo haya creado (renderChartMensual o renderChartMonthlyPerformance comparten
            // el mismo <canvas id="chartMensual">; solo chartMensualInst no alcanza a detectar
            // instancias creadas por la otra función, causando "Canvas is already in use").
            const chartExistente = Chart.getChart(ctx);
            if (chartExistente) chartExistente.destroy();

            // Configuración Cromática Institucional (2025: Gris #6c757d, 2026: Verde #2dce89)
            const COLOR_2026 = '#2dce89'; // Verde Institucional para 2026
            const COLOR_2025 = '#6c757d'; // Gris para 2025

            chartMensualInst = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        // ── Barras agrupadas ──────────────────────────
                        {
                            label: isMoney ? `Ventas ${yearActual}` : `Ventas ${yearActual} (Unds)`,
                            data: dataActualVentas,
                            backgroundColor: COLOR_2026,
                            borderRadius: 4,
                            barPercentage: 0.8,
                            categoryPercentage: 0.7,
                            order: 3,
                            anio: yearActual, // Metadata nativa del dataset: fuente de verdad para el drill-down (no depende de parsear el label)
                            mesesReales
                        },
                        {
                            label: isMoney ? `Ventas ${yearPrev}` : `Ventas ${yearPrev} (Unds)`,
                            data: dataPrevVentas,
                            backgroundColor: COLOR_2025,
                            borderRadius: 4,
                            barPercentage: 0.8,
                            categoryPercentage: 0.7,
                            order: 4,
                            anio: yearPrev,
                            mesesReales
                        },

                        // ── Líneas de Pedidos ───────────────────────────
                        {
                            label: isMoney ? `Pedidos ${yearActual}` : `Pedidos ${yearActual} (Unds)`,
                            data: dataActualPedidos,
                            type: 'line',
                            showLine: true,
                            borderColor: COLOR_2026,
                            borderWidth: 2,
                            borderDash: [5, 5],
                            backgroundColor: 'transparent',
                            pointStyle: 'circle',
                            pointRadius: 5,
                            pointHoverRadius: 7,
                            pointBackgroundColor: COLOR_2026,
                            pointBorderColor: '#fff',
                            pointBorderWidth: 1.5,
                            tension: 0,
                            hidden: !isMoney && !hayPedidosUnidades,
                            order: 1,
                            anio: yearActual,
                            mesesReales
                        },
                        {
                            label: isMoney ? `Pedidos ${yearPrev}` : `Pedidos ${yearPrev} (Unds)`,
                            data: dataPrevPedidos,
                            type: 'line',
                            showLine: true,
                            borderColor: COLOR_2025,
                            borderWidth: 1.5,
                            borderDash: [3, 3],
                            backgroundColor: 'transparent',
                            pointStyle: 'rect',
                            pointRadius: 4,
                            pointHoverRadius: 6,
                            pointBackgroundColor: COLOR_2025,
                            pointBorderColor: '#fff',
                            pointBorderWidth: 1,
                            tension: 0,
                            hidden: !isMoney && !hayPedidosUnidades,
                            order: 2,
                            anio: yearPrev,
                            mesesReales
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 400, easing: 'easeOutCubic' },
                    interaction: { mode: 'nearest', intersect: true },
                    plugins: {
                        legend: {
                            labels: {
                                usePointStyle: true,
                                font: { size: 11 },
                                padding: 16,
                                filter: item => item.text !== ''
                            }
                        },
                        tooltip: {
                            callbacks: {
                                label: (c) => c.raw > 0
                                    ? ` ${c.dataset.label}: ${isMoney ? formatCOP(c.raw) : formatNumber(c.raw)}`
                                    : null
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { borderDash: [4, 4], color: 'rgba(0,0,0,0.04)' },
                            ticks: {
                                callback: v => {
                                    if (!isMoney) return formatNumber(v);
                                    if (v >= 1000000) return '$' + (v / 1000000).toFixed(1) + 'M';
                                    return '$' + formatNumber(v);
                                }
                            }
                        },
                        x: { grid: { display: false } }
                    },
                    onClick: (event, _elements, chart) => {
                        // Resolución explícita del elemento clickeado (independiente del modo de
                        // interacción usado para el hover/tooltip): un solo punto exacto, nunca
                        // "todos los datasets en ese índice".
                        const [hit] = chart.getElementsAtEventForMode(event, 'nearest', { intersect: true }, false);
                        if (!hit) return;

                        const { index, datasetIndex } = hit;
                        const dataset = chart.data.datasets[datasetIndex];
                        const label = chart.data.labels[index];
                        const valor = dataset.data[index];
                        const anioSeleccionado = dataset.anio; // Metadata nativa fijada al construir el dataset

                        // Número real de mes (1-12) provisto por el backend (campo "mes_num" de
                        // /api/dashboard/rendimiento), NUNCA reconstruido parseando el label del eje X.
                        const mesNum = dataset.mesesReales[index];
                        console.log('Mes enviado al backend:', mesNum);
                        mostrarDrillDownVentas(mesNum, anioSeleccionado, label, valor);
                    },
                    onHover: (event, chartElement) => {
                        event.native.target.style.cursor = chartElement[0] ? 'pointer' : 'default';
                    }
                }
            });
        } catch (e) {
            console.error("Error renderizando chartMensual:", e);
            mostrarErrorEnCanvas(ctx, 'No se pudo renderizar el gráfico mensual. Intenta recargar la página.');
        }
    }

    let rendimientoDataCompleto = [];
    let mesActualGlobalIdx = 0;
    let mesActivoIdx = 0; // Guarda el índice del mes seleccionado
    let tacometroChartInstance = null;
    let currentRendimientoMode = 'money'; // can be 'money' or 'units'

    async function cargarRendimientoDedicado(mode = 'money') {
        currentRendimientoMode = mode;
        try {
            const desde = document.getElementById('db-fecha-desde')?.value || '';
            const hasta = document.getElementById('db-fecha-hasta')?.value || '';
            const params = new URLSearchParams();
            if (desde) params.append('desde', desde);
            if (hasta) params.append('hasta', hasta);
            const url = '/api/dashboard/rendimiento' + (params.toString() ? `?${params.toString()}` : '');

            const res = await fetch(url, {
                headers: construirAuthHeaders({ 'Accept': 'application/json' }),
                credentials: 'include'
            });

            if (!res.ok) {
                const msg = res.status === 401 ? 'Sesión expirada. Recarga la página e inicia sesión de nuevo.'
                          : res.status === 403 ? 'Sin permisos para ver el rendimiento mensual.'
                          : `Error del servidor al obtener el rendimiento mensual (HTTP ${res.status}).`;
                throw new Error(msg);
            }

            const data = await res.json();
            if (data.success && data.data) {
                rendimientoDataCompleto = data.data.data;
                mesActualGlobalIdx = data.data.mes_actual_idx || 0;
                mesActivoIdx = mesActualGlobalIdx; // Por defecto es el actual

                // En el caso inicial, dibuja el chart y el tacómetro
                renderChartRendimientoMensualNew(rendimientoDataCompleto, mode, mesActivoIdx === mesActualGlobalIdx ? -1 : mesActivoIdx);
                renderTacometro(rendimientoDataCompleto[mesActivoIdx]);

                // Mostrar el botón de restaurar si el mes seleccionado no es el actual
                const btnRestaurar = document.getElementById('btn-ver-mes-actual');
                if (btnRestaurar) {
                    if (mesActivoIdx !== mesActualGlobalIdx) btnRestaurar.classList.remove('d-none');
                    else btnRestaurar.classList.add('d-none');
                }
            }
        } catch (e) {
            console.error("Error fetching rendimiento:", e);
            mostrarErrorBanner(e.message);
        }
    }

    function restaurarTacometroMesActual() {
        if (rendimientoDataCompleto.length > mesActualGlobalIdx) {
            mesActivoIdx = mesActualGlobalIdx;
            renderTacometro(rendimientoDataCompleto[mesActivoIdx]);
            const btnRestaurar = document.getElementById('btn-ver-mes-actual');
            if (btnRestaurar) btnRestaurar.classList.add('d-none');
            // Remove highlight from chart by rerendering
            renderChartRendimientoMensualNew(rendimientoDataCompleto, currentRendimientoMode);
        }
    }
    
    function reRenderizarVistaActiva() {
        // Mantiene el mes seleccionado al cambiar entre $ / Unidades
        if (rendimientoDataCompleto && rendimientoDataCompleto.length > 0) {
            mesActivoIdx = Math.max(0, Math.min(mesActivoIdx, rendimientoDataCompleto.length - 1));
            renderChartRendimientoMensualNew(rendimientoDataCompleto, currentRendimientoMode, mesActivoIdx === mesActualGlobalIdx ? -1 : mesActivoIdx);
            renderTacometro(rendimientoDataCompleto[mesActivoIdx]);
        }
    }

    // Export function so HTML can use it
    window.ModuloDashboard = window.ModuloDashboard || {};
    window.ModuloDashboard.restaurarTacometroMesActual = restaurarTacometroMesActual;
    window.ModuloDashboard.reRenderizarVistaActiva = reRenderizarVistaActiva;
    window.ModuloDashboard.setModoRendimiento = function(nuevoModo) {
        currentRendimientoMode = nuevoModo;
        // Actualizar clases activas/inactivas en los botones UI
        document.querySelectorAll('.btn-toggle-rendimiento, [data-mode-rendimiento]').forEach(b => {
            const m = b.dataset.modeRendimiento || b.dataset.mode;
            b.classList.toggle('active', m === nuevoModo);
        });
        window.ModuloDashboard.reRenderizarVistaActiva();
    };

    function renderTacometro(dataMes) {
        if (!dataMes) return;

        const isMoney = currentRendimientoMode === 'money';

        // Defensivo: '/api/dashboard/rendimiento' devuelve ventas_monto/pedidos_monto +
        // pct_cumplimiento_*, pero toggleChartView() puede repoblar rendimientoDataCompleto
        // con el shape de 'lastJefaturaData.mensual' (actual_dinero/actual_pedidos, sin pct
        // precalculado). Se normalizan ambos shapes aquí para no depender de un campo que
        // puede no existir y terminar siempre en 0%.
        const montoVentas = Number(dataMes.ventas_monto ?? dataMes.actual_dinero ?? dataMes.ventas_dinero ?? 0);
        const montoPedidos = Number(dataMes.pedidos_monto ?? dataMes.actual_pedidos ?? dataMes.pedidos_dinero ?? 0);
        const undsVentas = Number(dataMes.ventas_unidades ?? dataMes.actual_unidades ?? dataMes.ventas_qty ?? 0);
        const undsPedidos = Number(dataMes.pedidos_unidades ?? dataMes.actual_pedidos_unidades ?? dataMes.pedidos_qty ?? 0);

        const ventasVal = isMoney ? montoVentas : undsVentas;
        const pedidosVal = isMoney ? montoPedidos : undsPedidos;

        const pctPrecalculado = isMoney ? dataMes.pct_cumplimiento_monto : dataMes.pct_cumplimiento_unidades;
        const pct = (pctPrecalculado !== undefined && pctPrecalculado !== null)
            ? Number(pctPrecalculado)
            : (pedidosVal > 0 ? parseFloat(((ventasVal / pedidosVal) * 100).toFixed(1)) : 0);

        const ctx = document.getElementById('chartTacometroRendimiento');
        if (!ctx) return;

        if (tacometroChartInstance) {
            tacometroChartInstance.destroy();
        }

        const pctVal = Math.max(0, Number(pct) || 0);
        const chartColor = '#3b82f6'; // Azul Corporativo Estándar

        // Normalización > 100%: Escalar el Gauge
        const maxGaugeValue = Math.max(100, Math.ceil(pctVal / 10) * 10);
        const resto = Math.max(0, maxGaugeValue - pctVal);
        
        document.getElementById('titulo-tacometro').innerHTML = `<i class="fas fa-tachometer-alt me-2"></i>Cumplimiento: ${dataMes.mes}`;
        
        document.getElementById('detalle-tacometro').innerHTML = isMoney ? 
            `Ventas: $${montoVentas.toLocaleString('es-CO')} | Meta: $${montoPedidos.toLocaleString('es-CO')}` :
            `Ventas: ${undsVentas.toLocaleString('es-CO')} Unds | Meta: ${undsPedidos.toLocaleString('es-CO')} Unds`;

        const centerTextPlugin = {
            id: 'centerText',
            beforeDraw: function(chart) {
                const width = chart.width;
                const height = chart.height;
                const chartCtx = chart.ctx;
                
                chartCtx.restore();
                chartCtx.font = `bold 26px sans-serif`;
                chartCtx.textBaseline = "bottom";
                chartCtx.textAlign = "center";
                
                const text = `${pctVal}%`;
                const x = width / 2;
                // Since it's a half-doughnut, the center is at the bottom of the chart area
                const y = chart.chartArea.bottom;
                
                chartCtx.fillStyle = chartColor;
                chartCtx.fillText(text, x, y);
                chartCtx.save();
            }
        };

        const gaugeScaleTicks = {
            id: 'gaugeScaleTicks',
            afterDraw: function(chart) {
                const ctx = chart.ctx;
                const { chartArea } = chart;
                if (!chartArea) return;
                
                const width = chartArea.right - chartArea.left;
                const height = chartArea.bottom - chartArea.top;
                
                const outerRadius = Math.min(width, height) / 2;
                const centerX = chartArea.left + width / 2;
                const centerY = chartArea.bottom;
                
                ctx.restore();
                ctx.font = '10px sans-serif';
                ctx.fillStyle = '#8898aa';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                
                // UNICAMENTE 4 marcas de referencia
                const ticks = [...new Set([0, 50, 100, maxGaugeValue])];
                const tickRadius = outerRadius + 18;
                
                ticks.forEach(tick => {
                    const angle = Math.PI + (tick / maxGaugeValue) * Math.PI; // -180 to 0 degrees relative to maxGaugeValue
                    const x = centerX + Math.cos(angle) * tickRadius;
                    const y = centerY + Math.sin(angle) * tickRadius;
                    ctx.fillText(`${tick}%`, x, y);
                });
                
                ctx.save();
            }
        };

        tacometroChartInstance = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Fondo', 'Cumplimiento', 'Restante'],
                datasets: [
                    {
                        data: [maxGaugeValue],
                        backgroundColor: ['rgba(249, 115, 22, 0.25)'], // Pista Naranja
                        borderWidth: 0,
                        circumference: 180,
                        rotation: 270,
                        weight: 1
                    },
                    {
                        data: [pctVal, resto],
                        backgroundColor: ['#3b82f6', 'rgba(249, 115, 22, 0.85)'], // Avance (Azul Corporativo) + Restante (Naranja)
                        borderWidth: 0,
                        circumference: 180,
                        rotation: 270,
                        weight: 1
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '75%',
                layout: {
                    padding: { top: 25, bottom: 20, left: 30, right: 30 }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { 
                        enabled: true,
                        filter: (tooltipItem) => tooltipItem.datasetIndex === 1,
                        callbacks: {
                            label: function() {
                                return [
                                    'Cumplimiento: ' + pctVal.toFixed(1) + '%',
                                    'Ventas: $' + montoVentas.toLocaleString('es-CO') + ' (' + undsVentas.toLocaleString('es-CO') + ' Unds)',
                                    'Pedidos: $' + montoPedidos.toLocaleString('es-CO') + ' (' + undsPedidos.toLocaleString('es-CO') + ' Unds)'
                                ];
                            }
                        }
                    }
                }
            },
            plugins: [centerTextPlugin, gaugeScaleTicks]
        });
    }

    // === 3. Gráfico Nuevo: Ventas vs Pedidos Mensual ===
    function renderChartRendimientoMensualNew(datosMensuales, mode = 'money', highlightIndex = -1) {
        const ctx = document.getElementById('chartRendimientoMensualNew');
        if (!ctx || !Array.isArray(datosMensuales) || datosMensuales.length === 0) return;
        limpiarErrorEnCanvas(ctx);

        try {
            const labels = datosMensuales.map(d => d.mes);
            const isMoney = mode === 'money';
            currentRendimientoMode = mode;
            rendimientoDataCompleto = datosMensuales; // Update cache if called from elsewhere

            const hastaInput = document.getElementById('db-fecha-hasta');
            const yearActual = hastaInput?.value
                ? new Date(hastaInput.value).getFullYear()
                : new Date().getFullYear();

            // Datos del año actual (Ventas vs Pedidos)
            const dataVentas = datosMensuales.map(d => ({
                x: d.mes,
                y: isMoney ? (d.ventas_monto ?? d.actual_dinero ?? 0) : (d.ventas_unidades ?? d.actual_unidades ?? 0),
                monto: (d.ventas_monto ?? d.actual_dinero ?? 0),
                unidades: (d.ventas_unidades ?? d.actual_unidades ?? 0),
                pedidos_monto: (d.pedidos_monto ?? d.actual_pedidos ?? 0),
                pedidos_unidades: (d.pedidos_unidades ?? d.actual_pedidos_unidades ?? 0)
            }));
            const dataPedidos = datosMensuales.map(d => ({
                x: d.mes,
                y: isMoney ? (d.pedidos_monto ?? d.actual_pedidos ?? 0) : (d.pedidos_unidades ?? d.actual_pedidos_unidades ?? 0),
                monto: (d.pedidos_monto ?? d.actual_pedidos ?? 0),
                unidades: (d.pedidos_unidades ?? d.actual_pedidos_unidades ?? 0),
                pedidos_monto: (d.pedidos_monto ?? d.actual_pedidos ?? 0),
                pedidos_unidades: (d.pedidos_unidades ?? d.actual_pedidos_unidades ?? 0)
            }));

            if (chartRendimientoMensualNewInst) chartRendimientoMensualNewInst.destroy();

            // Colores Contrastantes: Ventas (Azul) vs Pedidos (Naranja)
            const COLOR_VENTAS = dataVentas.map((_, idx) => highlightIndex !== -1 && highlightIndex !== idx ? 'rgba(59, 130, 246, 0.3)' : 'rgba(59, 130, 246, 0.9)');
            const COLOR_PEDIDOS = dataPedidos.map((_, idx) => highlightIndex !== -1 && highlightIndex !== idx ? 'rgba(249, 115, 22, 0.3)' : 'rgba(249, 115, 22, 0.85)');

            chartRendimientoMensualNewInst = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {
                            label: isMoney ? `Ventas ${yearActual} ($)` : `Ventas ${yearActual} (Unds)`,
                            data: dataVentas,
                            backgroundColor: COLOR_VENTAS,
                            borderColor: 'rgba(37, 99, 235, 1)',
                            hoverBackgroundColor: 'rgba(37, 99, 235, 0.95)',
                            hoverBorderColor: 'rgba(29, 78, 216, 1)',
                            borderRadius: 4,
                            barPercentage: 0.8,
                            categoryPercentage: 0.8
                        },
                        {
                            label: isMoney ? `Pedidos ${yearActual} ($)` : `Pedidos ${yearActual} (Unds)`,
                            data: dataPedidos,
                            backgroundColor: COLOR_PEDIDOS,
                            borderColor: '#ea580c',
                            hoverBackgroundColor: 'rgba(249, 115, 22, 0.95)',
                            hoverBorderColor: '#ea580c',
                            borderRadius: 4,
                            barPercentage: 0.8,
                            categoryPercentage: 0.8
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false,
                    },
                    onClick: (event, elements) => {
                        if (elements.length > 0) {
                            const index = elements[0].index;
                            if (!rendimientoDataCompleto[index]) return;
                            mesActivoIdx = index;
                            renderTacometro(rendimientoDataCompleto[index]);
                            const btnRestaurar = document.getElementById('btn-ver-mes-actual');
                            if (btnRestaurar) {
                                if (mesActivoIdx !== mesActualGlobalIdx) btnRestaurar.classList.remove('d-none');
                                else btnRestaurar.classList.add('d-none');
                            }
                            
                            // Re-render chart to highlight selected index
                            renderChartRendimientoMensualNew(rendimientoDataCompleto, currentRendimientoMode, index);
                        }
                    },
                    onHover: (event, chartElement) => {
                        event.native.target.style.cursor = chartElement[0] ? 'pointer' : 'default';
                    },
                    plugins: {
                        legend: { position: 'top' },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    const raw = context.raw || {};
                                    const $monto = '$' + (raw.monto || 0).toLocaleString('es-CO');
                                    const $unds = (raw.unidades || 0).toLocaleString('es-CO') + ' Unds';
                                    const prefix = context.dataset.label.split(' ')[0];
                                    return `${prefix}: ${$monto} (${$unds})`;
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                callback: function(value) {
                                    let val = value || 0;
                                    if (!isMoney) return new Intl.NumberFormat('es-CO').format(val);
                                    if (val >= 1000000) return '$' + (val / 1000000).toFixed(1) + 'M';
                                    return '$' + new Intl.NumberFormat('es-CO').format(val);
                                }
                            }
                        },
                        x: {
                            grid: { display: false }
                        }
                    }
                }
            });
        } catch (e) {
            console.error("Error renderizando chartRendimientoMensualNew:", e);
            mostrarErrorEnCanvas(ctx, 'No se pudo renderizar el gráfico de rendimiento. Intenta recargar la página.');
        }
    }

    /**
     * DRILL-DOWN: Carga y muestra el desglose de ventas de un mes específico en un modal.
     * Refactorizado según requerimiento de Juan Sebastian (ventasCache y UI instantánea).
     */
    async function mostrarDrillDownVentas(mes, anio, mesNombre, valorClickeado) {
        const modalEl = document.getElementById('modalDesgloseVentas');
        if (!modalEl) return;

        const modal = new bootstrap.Modal(modalEl);
        const spinner = document.getElementById('modalDesgloseSpinner');
        const contenido = document.getElementById('modalDesgloseContenido');
        const titulo = document.getElementById('modalDesgloseTitulo');
        const subtitulo = document.getElementById('modalDesgloseSubtitulo');

        // El modo actual ('money' o 'units') define el orden y la lógica del drill-down
        const modo = currentIncSortMode || 'money';
        const cacheKey = `${mes}-${anio}-${modo}`;

        // Título/subtítulo instantáneos con el contexto exacto del elemento clickeado
        // (índice, etiqueta y valor), antes de que llegue la respuesta del servidor.
        if (titulo) titulo.innerText = `Análisis de Facturación: ${mesNombre} ${anio}`;
        if (subtitulo && typeof valorClickeado === 'number') {
            const valorFmt = modo === 'money' ? formatCOP(valorClickeado) : `${formatNumber(valorClickeado)} unds`;
            subtitulo.innerText = `Valor seleccionado en el gráfico: ${valorFmt}. Cargando desglose por referencia...`;
        }

        // Limpieza explícita ANTES del fetch/caché: sin esto, un mes con menos filas que el
        // anterior podía dejar residuos del render previo visibles un instante al reabrir
        // el modal (estado fantasma), incluso con el contenedor oculto por d-none.
        const tbodyProductos = document.getElementById('cuerpoTablaDesgloseVentas');
        const tbodyClientes = document.getElementById('cuerpoTablaDesgloseClientes');
        if (tbodyProductos) tbodyProductos.innerHTML = '';
        if (tbodyClientes) tbodyClientes.innerHTML = '';

        // Siempre reabrir en la pestaña "Productos", sin importar en cuál se quedó la última vez.
        const tabBtnProductos = document.getElementById('tab-btn-productos');
        if (tabBtnProductos && window.bootstrap?.Tab) {
            bootstrap.Tab.getOrCreateInstance(tabBtnProductos).show();
        }

        contenido.classList.add('d-none');
        spinner.classList.remove('d-none');
        modal.show();

        // 1. Verificar Caché Global (Velocidad Instantánea)
        if (window.ventasCache[cacheKey]) {
            console.log(`⚡ [Instant Load] Cargando ${cacheKey} desde ventasCache`);
            renderizarTablaDesglose(window.ventasCache[cacheKey], mes, anio, mesNombre);
            spinner.classList.add('d-none');
            contenido.classList.remove('d-none');
            return;
        }

        // 2. Fetch al Backend si no está en caché
        try {
            const url = `/api/dashboard/ventas/desglose-mensual?mes=${mes}&anio=${anio}&tipo_vista=${modo}`;
            const res = await fetch(url, {
                headers: construirAuthHeaders({ 'Accept': 'application/json' }),
                credentials: 'include'
            });

            if (!res.ok) {
                const msg = res.status === 401 ? 'Sesión expirada. Recarga la página e inicia sesión de nuevo.'
                          : res.status === 403 ? 'Sin permisos para ver el desglose de ventas.'
                          : `Error del servidor (HTTP ${res.status}) al obtener el desglose de ventas.`;
                throw new Error(msg);
            }

            const json = await res.json();
            if (json.success) {
                window.ventasCache[cacheKey] = json.data;
                renderizarTablaDesglose(json.data, mes, anio, mesNombre);
            } else {
                Swal.fire('Error', json.error || 'No se pudieron obtener los datos de ventas.', 'error');
            }
        } catch (error) {
            console.error('Error en drill-down:', error);
            Swal.fire('Error', error.message || 'Fallo de conexión con el servidor.', 'error');
        } finally {
            spinner.classList.add('d-none');
            contenido.classList.remove('d-none');
        }
    }

    /**
     * Helper para renderizar las dos pestañas del modal (Productos / Clientes) con diseño
     * profesional (Juan Sebastian vFinal). Optimizado para alto contraste (#000000).
     * `data` es el DTO de /ventas/desglose-mensual: {"productos": [...], "clientes": [...]}.
     */
    function renderizarTablaDesglose(data, mes, anio, mesNombre) {
        const titulo = document.getElementById('modalDesgloseTitulo');
        const subtitulo = document.getElementById('modalDesgloseSubtitulo');
        const cuerpoProductos = document.getElementById('cuerpoTablaDesgloseVentas');
        const cuerpoClientes = document.getElementById('cuerpoTablaDesgloseClientes');
        const totalFooter = document.getElementById('modalDesgloseTotal');
        const btnExportar = document.getElementById('btnExportarExcelVentas');

        const productos = Array.isArray(data?.productos) ? data.productos : [];
        const clientes = Array.isArray(data?.clientes) ? data.clientes : [];

        if (titulo) titulo.innerText = `Análisis de Facturación: ${mesNombre} ${anio}`;
        if (subtitulo) subtitulo.innerText = `Datos consolidados desde el núcleo ERP (Carga Instantánea).`;

        // --- Pestaña "Top Productos del Mes" ---
        let totalMes = 0;
        let htmlProductos = '';
        if (productos.length === 0) {
            htmlProductos = `<tr><td colspan="4" class="text-center py-5 text-muted">No se encontraron registros de ventas para este periodo.</td></tr>`;
        } else {
            productos.forEach(item => {
                const subtotal = parseFloat(item.total_ventas || 0);
                totalMes += subtotal;

                // Diseño de Máximo Contraste (#000000)
                const ref = item.id_codigo || 'N/A';
                const desc = item.descripcion || 'Descripción extraída del registro de venta';

                // Confirmado con Facturación: cantidad positiva + monto negativo = nota
                // crédito/devolución cargada desde el ERP. Se etiqueta para que no se lea
                // como un error de cálculo.
                const esDevolucion = subtotal < 0;
                const montoColor = esDevolucion ? '#dc2626' : '#000000';
                const badgeDevolucion = esDevolucion
                    ? '<span class="badge bg-danger-subtle text-danger border border-danger-subtle ms-2" style="font-size: 0.65rem; vertical-align: middle;">Devolución</span>'
                    : '';

                htmlProductos += `
                    <tr class="align-middle">
                        <td class="ps-4 py-3">
                            <span class="fw-bold text-dark font-monospace" style="font-size: 1rem; color: #000000 !important;">${ref}</span>
                        </td>
                        <td class="py-3">
                            <div class="fw-bold text-dark" style="font-size: 0.9rem; line-height: 1.2; color: #000000 !important; max-width: 550px;">${desc}</div>
                        </td>
                        <td class="text-center py-3">
                            <span class="fw-bold text-dark" style="font-size: 1rem; color: #000000 !important;">${formatNumber(item.unidades)}</span>
                        </td>
                        <td class="text-end pe-4 py-3">
                            <span class="fw-bold" style="font-size: 1.05rem; color: ${montoColor} !important;">${formatCOP(subtotal)}</span>${badgeDevolucion}
                        </td>
                    </tr>
                `;
            });
        }
        if (cuerpoProductos) cuerpoProductos.innerHTML = htmlProductos;

        // --- Pestaña "Top Clientes del Mes" ---
        let htmlClientes = '';
        if (clientes.length === 0) {
            htmlClientes = `<tr><td colspan="3" class="text-center py-5 text-muted">No se encontraron clientes con ventas en este periodo.</td></tr>`;
        } else {
            clientes.forEach(item => {
                const subtotal = parseFloat(item.total_ventas || 0);
                const nombre = item.nombre_cliente || 'Cliente Desconocido';

                const esDevolucion = subtotal < 0;
                const montoColor = esDevolucion ? '#dc2626' : '#000000';
                const badgeDevolucion = esDevolucion
                    ? '<span class="badge bg-danger-subtle text-danger border border-danger-subtle ms-2" style="font-size: 0.65rem; vertical-align: middle;">Devolución</span>'
                    : '';

                htmlClientes += `
                    <tr class="align-middle">
                        <td class="ps-4 py-3">
                            <span class="fw-bold text-dark" style="font-size: 0.95rem; color: #000000 !important;">${nombre}</span>
                        </td>
                        <td class="text-center py-3">
                            <span class="fw-bold text-dark" style="font-size: 1rem; color: #000000 !important;">${formatNumber(item.unidades)}</span>
                        </td>
                        <td class="text-end pe-4 py-3">
                            <span class="fw-bold" style="font-size: 1.05rem; color: ${montoColor} !important;">${formatCOP(subtotal)}</span>${badgeDevolucion}
                        </td>
                    </tr>
                `;
            });
        }
        if (cuerpoClientes) cuerpoClientes.innerHTML = htmlClientes;

        // El total del footer viene de "productos": misma CTE/periodo que "clientes" en el
        // backend, así que ambas dimensiones son matemáticamente equivalentes — no hay dos
        // fuentes de verdad que puedan divergir.
        if (totalFooter) {
            totalFooter.innerText = formatCOP(totalMes);
            totalFooter.style.color = "#000000";
        }

        if (btnExportar) {
            btnExportar.disabled = (productos.length === 0 && clientes.length === 0);
            btnExportar.onclick = async (e) => {
                e.preventDefault();
                const modo = currentIncSortMode || 'money';
                const iconoOriginal = btnExportar.innerHTML;
                btnExportar.disabled = true;
                btnExportar.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Generando...';

                try {
                    const url = `/api/dashboard/ventas/exportar-desglose?mes=${mes}&anio=${anio}&tipo_vista=${modo}`;
                    const res = await fetch(url, {
                        headers: construirAuthHeaders(),
                        credentials: 'include'
                    });

                    if (!res.ok) {
                        const msg = res.status === 401 ? 'Sesión expirada. Recarga la página e inicia sesión de nuevo.'
                                  : res.status === 403 ? 'Sin permisos para exportar este reporte.'
                                  : `Error del servidor (HTTP ${res.status}) al generar el reporte.`;
                        throw new Error(msg);
                    }

                    // Navegación directa perdía el header Authorization; el reporte ahora
                    // se descarga como Blob autenticado y se dispara localmente.
                    const blob = await res.blob();
                    const blobUrl = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = blobUrl;
                    a.download = `Reporte_Ventas_${mes}_${anio}.xlsx`;
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    window.URL.revokeObjectURL(blobUrl);
                } catch (error) {
                    console.error('Error exportando desglose de ventas:', error);
                    Swal.fire('Error', error.message || 'No fue posible generar el reporte Excel.', 'error');
                } finally {
                    btnExportar.disabled = (productos.length === 0 && clientes.length === 0);
                    btnExportar.innerHTML = iconoOriginal;
                }
            };
        }
    }

    /**
     * PRE-FETCHING PROACTIVO: Carga los últimos 3 meses en silencio para UX <500ms.
     * Ahora detecta el modo activo para cargar los datos ordenados correctamente.
     */
    async function prefetchVentasDrilldown(dataMensual, yearActual, yearPrev) {
        try {
            if (!Array.isArray(dataMensual) || dataMensual.length === 0) return;
            
            const modo = currentIncSortMode || 'money';
            console.log(`⚡ [Proactive Cache] Precargando meses estratégicos (Modo: ${modo})...`);
            
            // Seleccionar los últimos 3 meses con actividad
            const mesesEstrategicos = dataMensual
                .map((val, idx) => ({ mes: idx + 1, val }))
                .filter(o => o.val > 0)
                .slice(-3);

            for (const item of mesesEstrategicos) {
                const key = `${item.mes}-${yearActual}-${modo}`;
                if (!window.ventasCache[key]) {
                    try {
                        const res = await fetch(`/api/dashboard/ventas/desglose-mensual?mes=${item.mes}&anio=${yearActual}&tipo_vista=${modo}`, {
                            headers: construirAuthHeaders({ 'Accept': 'application/json' }),
                            credentials: 'include'
                        });
                        if (!res.ok) continue;
                        const json = await res.json();
                        if (json.success) {
                            window.ventasCache[key] = json.data;
                            console.log(`✅ [Cache Ready] ${key}`);
                        }
                    } catch(e) {
                        console.warn(`No se pudo precargar mes ${item.mes}`);
                    }
                    await new Promise(r => setTimeout(r, 400)); // Throttle más rápido
                }
            }
        } catch (globalPrefetchErr) {
            console.error("Error global en prefetch:", globalPrefetchErr);
        }
    }

    function renderChartTopMejores(arr, mode = 'money') {
        const ctx = document.getElementById('chartTopMejores');
        if (!ctx || !Array.isArray(arr) || arr.length === 0) return;
        limpiarErrorEnCanvas(ctx);

        try {
            const isMoney = mode === 'money';
            // Ordenar por el modo actual
            const datos = [...arr].sort((a, b) => isMoney ? (b.ventas_dinero - a.ventas_dinero) : (b.ventas_unidades - a.ventas_unidades));
            
            const labels = datos.slice(0, 10).map(d => {
                const name = d.producto || '';
                return name.substring(0, 20) + (name.length > 20 ? '...' : '');
            });
            const dataVals = datos.slice(0, 10).map(d => {
                const val = isMoney ? d.ventas_dinero : d.ventas_unidades;
                return val || 0;
            });

            if (chartTopMejoresInst) chartTopMejoresInst.destroy();

            chartTopMejoresInst = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: isMoney ? 'Ventas ($)' : 'Ventas (Unds)',
                        data: dataVals,
                        backgroundColor: 'rgba(245, 158, 11, 0.85)',
                        borderRadius: 4,
                        barPercentage: 0.7
                    }]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 300 },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                title: (context) => {
                                    const d = datos[context[0].dataIndex];
                                    return d.producto || '';
                                },
                                label: (context) => ` Ventas: ${isMoney ? formatCOP(context.raw) : formatNumber(context.raw)}`
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: { borderDash: [4, 4] },
                            ticks: {
                                callback: function (value) {
                                    if (!isMoney) return formatNumber(value);
                                    if (value >= 1000000) return '$' + (value / 1000000) + 'M';
                                    return '$' + formatNumber(value);
                                }
                            }
                        },
                        y: { grid: { display: false }, ticks: { font: { size: 10 } } }
                    }
                }
            });
            suscribirClickNativoCanvas('chartTopMejores');
        } catch (e) {
            console.error("Error renderizando chartTopMejores:", e);
            mostrarErrorEnCanvas(ctx, 'No se pudo renderizar el ranking de mejores productos.');
        }
    }
    function renderChartTopPeores(arr, mode = 'money') {
        const ctx = document.getElementById('chartTopPeores');
        if (!ctx || !Array.isArray(arr) || arr.length === 0) return;
        limpiarErrorEnCanvas(ctx);

        try {
            const isMoney = mode === 'money';
            // Ordenar por el modo actual (Peores = Menos ventas)
            const datos = [...arr].sort((a, b) => isMoney ? (a.ventas_dinero - b.ventas_dinero) : (a.ventas_unidades - b.ventas_unidades));
            
            const labels = datos.slice(0, 10).map(d => {
                const name = d.producto || '';
                return name.substring(0, 20) + (name.length > 20 ? '...' : '');
            });
            const dataVals = datos.slice(0, 10).map(d => {
                const val = isMoney ? d.ventas_dinero : d.ventas_unidades;
                return val || 0;
            });

            if (chartTopPeoresInst) chartTopPeoresInst.destroy();

            chartTopPeoresInst = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: isMoney ? 'Ventas ($)' : 'Ventas (Unds)',
                        data: dataVals,
                        backgroundColor: 'rgba(239, 68, 68, 0.75)',
                        borderRadius: 4,
                        barPercentage: 0.7
                    }]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                title: (context) => {
                                    const d = datos[context[0].dataIndex];
                                    return d.producto || '';
                                },
                                label: (context) => ` Ventas: ${isMoney ? formatCOP(context.raw) : formatNumber(context.raw)}`
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: { borderDash: [4, 4] },
                            ticks: {
                                callback: function (value) {
                                    if (!isMoney) return formatNumber(value);
                                    if (value >= 1000000) return '$' + (value / 1000000) + 'M';
                                    return '$' + formatNumber(value);
                                }
                            }
                        },
                        y: { grid: { display: false }, ticks: { font: { size: 10 } } }
                    }
                }
            });
            suscribirClickNativoCanvas('chartTopPeores');
        } catch (e) {
            console.error("Error renderizando chartTopPeores:", e);
            mostrarErrorEnCanvas(ctx, 'No se pudo renderizar el ranking de peores productos.');
        }
    }

    /**
     * Alterna la vista de las gráficas entre Dinero y Unidades
     */
    /**
     * Alterna la vista de las gráficas entre Dinero y Unidades (Sincronización Global vFinal)
     * Cuando se cambia el modo en una tarjeta, se sincroniza todo el Dashboard.
     */
    function toggleChartView(ignoreChartType, mode) {
        if (!lastJefaturaData) return;

        // 1. Evitar ejecuciones redundantes si ya estamos en ese modo
        if (currentIncSortMode === mode && ignoreChartType !== 'force') return;

        console.log(`🔄 Global Toggle: switching all charts to -> ${mode}`);

        // 2. Actualizar Estado Global y Persistencia
        currentIncSortMode = mode; 
        localStorage.setItem('db_toggle_mode', mode);

        // 3. Sincronizar UI de todos los botones Toggle en el DOM
        const allToggles = ['mensual', 'mejores', 'peores'];
        allToggles.forEach(type => {
            const btnMoney = document.getElementById(`toggle-${type}-money`);
            const btnUnits = document.getElementById(`toggle-${type}-units`);
            
            if (btnMoney) btnMoney.classList.toggle('active', mode === 'money');
            if (btnUnits) btnUnits.classList.toggle('active', mode === 'units');
        });

        // 4. Redibujar Todo el Dashboard con el nuevo modo (Ejecución Atómica)
        try {
            // Gráfico Principal
            const dataMensual = lastJefaturaData.mensual || lastJefaturaData.grafico_mensual || [];
            renderChartMensual(dataMensual, mode);
            renderChartRendimientoMensualNew(dataMensual, mode);
            
            // Rankings
            const arrMejores = lastJefaturaData.top_productos || lastJefaturaData.top_mejores || [];
            renderChartTopMejores(arrMejores, mode);
            
            const arrPeores = lastJefaturaData.peores_productos || [];
            renderChartTopPeores(arrPeores, mode);

            // 5. Actualizar Pre-fetch proactivo para el nuevo modo (Background)
            setTimeout(() => {
                const yAct = lastJefaturaData.year_actual || new Date().getFullYear();
                const yPrv = lastJefaturaData.year_prev || (yAct - 1);
                prefetchVentasDrilldown(dataMensual, yAct, yPrv);
            }, 500); // Pequeño delay para no competir con el renderizado principal

        } catch (err) {
            console.error("❌ Error en re-renderizado global:", err);
        }
    }

    async function mostrarDetalleIncumplimiento(cliente) {
        // Mostrar cargando con SweetAlert2
        Swal.fire({
            title: 'Analizando historial comercial...',
            text: 'Consultando registros detallados de pedidos y ventas.',
            allowOutsideClick: false,
            didOpen: () => {
                Swal.showLoading();
            }
        });

        try {
            const desde = document.getElementById('db-fecha-desde')?.value || "";
            const hasta = document.getElementById('db-fecha-hasta')?.value || "";
            const decodedCliente = cliente.replace(/&quot;/g, '"');
            
            const pwaToken = localStorage.getItem('pwa_token');
            const headers = { 'Accept': 'application/json' };
            if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;

            const url = `/api/admin/backorder/detalle?cliente=${encodeURIComponent(decodedCliente)}&desde=${desde}&hasta=${hasta}`;
            const response = await fetch(url, { headers, credentials: 'include' });
            if (!response.ok) {
                const errJson = await response.json().catch(() => ({}));
                throw new Error(errJson.message || errJson.error || `HTTP ${response.status}: Error consultando servicio de backorder`);
            }
            const res = await response.json();

            if (!res.success || !res.data) {
                Swal.fire("Error", res.message || "No se pudo obtener el detalle de backorder.", "error");
                return;
            }

            const listado = res.data;

            if (listado.length === 0) {
                Swal.fire("Información", `No hay detalle de faltantes para ${decodedCliente}`, "info");
                return;
            }

            let rowsHtml = '';
            let totP = 0, totV = 0, totF = 0, totM = 0;

            listado.forEach((item, idx) => {
                totP += item.pedidos;
                totV += item.ventas;
                totF += item.pendiente;
                totM += item.impacto;

                const rowBg = idx % 2 === 0 ? 'rgba(248, 250, 252, 0.8)' : '#ffffff';

                rowsHtml += `
                    <tr style="background-color: ${rowBg}; border-bottom: 1px solid #f1f5f9;">
                        <td class="text-start ps-3 py-3" style="font-size: 0.85rem; width: 42%;" data-label="Referencia / Producto">
                            <div class="fw-bold text-slate-800">${item.referencia || '-'}</div>
                            <div class="text-muted small" style="font-size: 0.7rem;">${item.descripcion || ''}</div>
                        </td>
                        <td class="text-end pe-3 py-3 text-muted" style="font-size: 0.9rem; font-family: 'JetBrains Mono', monospace;" data-label="Pedido">${formatNumber(item.pedidos)}</td>
                        <td class="text-end pe-3 py-3 text-primary" style="font-size: 0.9rem; font-family: 'JetBrains Mono', monospace;" data-label="Facturado">${formatNumber(item.ventas)}</td>
                        <td class="text-end pe-3 py-3 text-danger fw-bold" style="font-size: 0.95rem; font-family: 'JetBrains Mono', monospace; background-color: rgba(239, 68, 68, 0.04);" data-label="Faltante">${formatNumber(item.pendiente)}</td>
                        <td class="text-end pe-4 py-3 text-success fw-bold" style="font-size: 0.95rem; font-family: 'JetBrains Mono', monospace;" data-label="Impacto ($)">${formatCOP(item.impacto)}</td>
                    </tr>
                    `;
            });

            Swal.fire({
                title: `<div class="mb-1"><i class="fas fa-history text-danger fs-3"></i></div><div style="font-size:1.25rem; font-weight:800; color: #1e293b;">KPI: Análisis Comercial Detallado</div><div class="text-muted small fw-normal">${decodedCliente}</div>`,
                html: `
                    <div class="table-responsive border rounded-3" style="max-height: 520px; overflow-y: auto; position: relative;">
                        <table class="table table-sm table-hover align-middle mb-0 responsive-mobile" style="min-width: 900px; border-collapse: separate; border-spacing: 0;">
                            <thead style="position: sticky; top: 0; z-index: 20; background-color: #1e293b; color: #f8fafc;">
                                <tr>
                                    <th class="text-start ps-3 py-3" style="font-size:0.75rem; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: none;">REFERENCIA / PRODUCTO</th>
                                    <th class="text-end pe-3 py-3" style="font-size:0.75rem; text-transform: uppercase; border-bottom: none;">PEDIDO</th>
                                    <th class="text-end pe-3 py-3" style="font-size:0.75rem; text-transform: uppercase; border-bottom: none;">FACTURADO</th>
                                    <th class="text-end pe-3 py-3" style="font-size:0.75rem; text-transform: uppercase; border-bottom: none;">FALTANTE</th>
                                    <th class="text-end pe-4 py-3" style="font-size:0.75rem; text-transform: uppercase; border-bottom: none;">IMPACTO ($)</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${rowsHtml}
                            </tbody>
                            <tfoot style="position: sticky; bottom: 0; z-index: 20; background-color: #f8fafc; border-top: 2px solid #cbd5e1; box-shadow: 0 -4px 6px -1px rgb(0 0 0 / 0.1);">
                                <tr class="fw-bolder">
                                    <td class="ps-3 py-3 text-uppercase" style="font-size: 0.8rem; color: #475569;">Total para este cliente</td>
                                    <td class="text-end pe-3 py-3 text-dark" style="font-size: 1rem; font-family: 'JetBrains Mono', monospace;">${formatNumber(totP)}</td>
                                    <td class="text-end pe-3 py-3 text-primary" style="font-size: 1rem; font-family: 'JetBrains Mono', monospace;">${formatNumber(totV)}</td>
                                    <td class="text-end pe-3 py-3 text-danger" style="font-size: 1.1rem; font-family: 'JetBrains Mono', monospace; background-color: rgba(239, 68, 68, 0.06);">${formatNumber(totF)}</td>
                                    <td class="text-end pe-4 py-3 text-success" style="font-size: 1.1rem; font-family: 'JetBrains Mono', monospace;">${formatCOP(totM)}</td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                    <div class="mt-3 d-flex justify-content-end">
                        <button class="btn btn-outline-success btn-sm fw-bold rounded-pill px-4 shadow-sm" onclick="window.ModuloDashboard.exportarBackorderCliente('${decodedCliente.replace(/'/g, "\\'")}')">
                            <i class="fas fa-file-csv me-2"></i> Exportar Faltantes (CSV)
                        </button>
                    </div>
                `,
                width: window.innerWidth > 768 ? '65em' : '100%',
                confirmButtonText: '<i class="fas fa-times me-2"></i> Cerrar',
                confirmButtonColor: '#334155',
                customClass: {
                    confirmButton: 'btn rounded-pill px-5 py-2 fw-bold shadow-lg mt-2'
                }
            });

            currentBackorderDetail = listado;

        } catch (error) {
            console.error("Error cargando detalle incumplimiento:", error);
            Swal.fire("Error", "Ocurrió un problema al conectar con el servidor.", "error");
        }
    }

    function toggleOperatorSelection(nombre, buenas, pnc, costoPnc, eficiencia, tiempoEstandar, detalleMix = null) {
        const index = selectedOperators.findIndex(s => s.nombre === nombre);
        if (index > -1) {
            selectedOperators.splice(index, 1);
        } else {
            if (selectedOperators.length >= 3) {
                Swal.fire({
                    icon: 'warning',
                    title: 'Límite alcanzado',
                    text: 'Solo puedes comparar hasta 3 operarios a la vez.',
                    confirmButtonColor: '#3b82f6'
                });
                renderTablaPulido(lastJefaturaData?.profundo_pulido || {}); // Refrescar para desmarcar el checkbox
                return;
            }
            selectedOperators.push({ nombre, buenas, pnc, costoPnc, eficiencia, tiempoEstandar, detalleMix });
        }

        actualizarUIComparativa();
    }

    function actualizarUIComparativa() {
        const btn = document.getElementById('btn-comparar-pulido');
        const count = document.getElementById('count-comparar');

        if (btn && count) {
            count.textContent = selectedOperators.length;
            if (selectedOperators.length >= 2) {
                btn.classList.remove('d-none');
            } else {
                btn.classList.add('d-none');
            }
        }
    }

    function abrirModalComparativa() {
        console.log("📊 Abriendo comparativa cara a cara...", selectedOperators);
        const grid = document.getElementById('comparativa-grid');
        if (!grid) return;

        grid.innerHTML = '';

        const minCosto = Math.min(...selectedOperators.map(o => o.costoPnc));
        const maxEfi = Math.max(...selectedOperators.map(o => o.eficiencia));

        selectedOperators.forEach(op => {
            const fmtMoney = v => new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(v);
            const precisionEfi = op.buenas === (op.buenas + op.pnc) ? 100 : ((op.buenas / (op.buenas + op.pnc)) * 100).toFixed(2);

            const esGanadorCalidad = op.costoPnc === minCosto;

            // Procesar Mix para el Top 5
            let mixHtml = '<div class="text-muted small py-2">Sin datos de referencias</div>';
            if (op.detalleMix) {
                try {
                    const arr = JSON.parse(decodeURIComponent(op.detalleMix)).slice(0, 5); // Tomar solo los primeros 5
                    mixHtml = arr.map(item => {
                        const ref = String(item.ref || '').trim().substring(0, 25); // Truncar si es muy largo
                        const qty = item.cantidad || 0;
                        return `
                <div class="d-flex justify-content-between align-items-center mb-1 pb-1 border-bottom border-light">
                    <span style="font-size: 0.65rem; color: #475569;" class="text-truncate" title="${item.ref}">${ref}</span>
                    <div class="d-flex align-items-center gap-1">
                        <span class="badge bg-light text-dark" style="font-size: 0.6rem;">${qty.toLocaleString()} pz</span>
                    </div>
                </div>
                `;
                    }).join('');
                } catch (e) {
                    console.error("Error parseando mix comparativa", e);
                }
            }

            const html = `
                <div class="comparison-column">
                    <div class="comparison-header">
                        <div class="comparison-avatar">
                            <i class="fas fa-user-ninja"></i>
                        </div>
                        <h4 class="fw-bold text-dark mb-1" style="font-size: 1.1rem;">${op.nombre}</h4>
                        <span class="text-muted small">Experto en Pulido</span>
                    </div>

                    <div class="mb-3 px-3">
                        <div class="text-uppercase text-secondary fw-bold mb-2" style="font-size: 0.65rem; letter-spacing: 0.05em;">
                            <i class="fas fa-trophy text-info me-1"></i> Top 5 Referencias
                        </div>
                        <div class="bg-white rounded-2 p-2 shadow-sm border border-light">
                            ${mixHtml}
                        </div>
                    </div>

                    <div class="comparison-metric">
                        <div class="metric-row">
                            <div class="metric-label-group">
                                <div class="metric-icon"><i class="fas fa-hand-holding-usd text-danger"></i></div>
                                <span class="metric-label">Pérdida Calidad</span>
                            </div>
                            <span class="metric-value ${esGanadorCalidad ? 'metric-highlight text-success' : 'text-danger'}">${fmtMoney(op.costoPnc)}</span>
                        </div>
                    </div>

                    <div class="comparison-metric bg-light">
                        <div class="metric-row">
                            <div class="metric-label-group">
                                <div class="metric-icon"><i class="fas fa-chart-line text-success"></i></div>
                                <span class="metric-label">Efectividad</span>
                            </div>
                            <span class="metric-value ${op.eficiencia === maxEfi ? 'metric-highlight' : ''}">${precisionEfi}%</span>
                        </div>
                    </div>

                    <div class="comparison-metric">
                        <div class="metric-row">
                            <div class="metric-label-group">
                                <div class="metric-icon"><i class="fas fa-clock text-info"></i></div>
                                <span class="metric-label">Tiempo Est (min)</span>
                            </div>
                            <span class="metric-value" style="font-size: 1rem;">${(op.tiempoEstandar || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })}'</span>
                        </div>
                    </div>
                </div>
                `;
            grid.insertAdjacentHTML('beforeend', html);
        });

        const modal = new bootstrap.Modal(document.getElementById('modalComparativaPulido'));
        modal.show();
    }

    function exportarBackorderCliente(nombreCliente) {
        if (!currentBackorderDetail || currentBackorderDetail.length === 0) {
            Swal.fire("Error", "No hay datos para exportar", "error");
            return;
        }
        
        let csvContent = "\uFEFF"; // BOM para Excel
        csvContent += "Referencia;Descripcion;Pedido;Facturado;Faltante;Impacto\n";
        
        currentBackorderDetail.forEach(item => {
            const row = [
                `"${item.referencia || ''}"`,
                `"${(item.descripcion || '').replace(/"/g, '""')}"`,
                item.pedidos,
                item.ventas,
                item.pendiente,
                item.impacto
            ].join(";");
            csvContent += row + "\n";
        });
        
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", `Backorder_${nombreCliente.replace(/\s+/g, '_')}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    // ── GRÁFICO CENTRALIZADO: Análisis Comparativo Anual (Ventas vs Pedidos) ──────
    // Mapa de instancias keyed por containerId → evita memory leaks con múltiples canvas
    const _monthlyChartInstances = {};

    /**
     * Renderiza el gráfico 'Análisis Comparativo Anual' en el canvas indicado.
     * @param {Object} data  - Payload de /api/dashboard/performance/monthly
     * @param {string} containerId - id del elemento <canvas> destino
     */
    function renderChartMonthlyPerformance(data, containerId = 'chartMensual') {
        const ctx = document.getElementById(containerId);
        if (!ctx || !data || !Array.isArray(data.mensual) || data.mensual.length === 0) return;
        limpiarErrorEnCanvas(ctx);

        try {
            let mensualFiltrado = data.mensual;
            const hastaVal = document.getElementById('db-fecha-hasta')?.value;
            if (hastaVal) {
                const parts = hastaVal.split('-');
                if (parts.length === 3) {
                    const limitMonth = parseInt(parts[1], 10);
                    if (!isNaN(limitMonth) && limitMonth > 0 && limitMonth <= 12 && mensualFiltrado.length > limitMonth) {
                        mensualFiltrado = mensualFiltrado.slice(0, limitMonth);
                    }
                }
            }

            const yearActual = data.year_actual || new Date().getFullYear();
            const yearPrev   = data.year_prev   || (yearActual - 1);
            const labels     = mensualFiltrado.map(d => d.mes);
            // Número real de calendario (1-12) tal como lo calculó el backend
            // (DashboardRepository.get_rendimiento_mensual_sql: campo "mes_num"). Se guarda
            // posicionalmente junto a "labels" — NUNCA se reconstruye parseando el label.
            const mesesReales = mensualFiltrado.map(d => Number(d.mes_num));

            // Datos — garantizado 0 en meses sin registro
            const ventasAct  = mensualFiltrado.map(d => d.actual_dinero  || 0);
            const ventasPrev = mensualFiltrado.map(d => d.prev_dinero    || 0);
            const pedidosAct = mensualFiltrado.map(d => d.actual_pedidos || 0);
            const pedidosPrev= mensualFiltrado.map(d => d.prev_pedidos   || 0);

            // Destruir cualquier Chart.js atado a este canvas, sin importar qué función lo
            // haya creado (renderChartMensual y renderChartMonthlyPerformance comparten el
            // mismo <canvas>; _monthlyChartInstances por sí solo no detecta instancias creadas
            // por la otra función, causando "Canvas is already in use").
            const chartExistente = Chart.getChart(ctx);
            if (chartExistente) chartExistente.destroy();

            const COLOR_2026 = '#2dce89'; // Verde Institucional para 2026
            const COLOR_2025 = '#6c757d'; // Gris para 2025

            const _fmtCOP = v => new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }).format(v);
            const _fmtNum = v => new Intl.NumberFormat('es-CO').format(v);

            // Suma consolidada del año actual (mismo nodo DOM que renderChartMensual;
            // esta función es la que realmente repinta el canvas al cambiar el filtro
            // de fechas, así que el total debe actualizarse aquí también).
            const totalConsolidadoActual = ventasAct.reduce((acc, v) => acc + v, 0);
            const totalConsolidadoPrev = ventasPrev.reduce((acc, v) => acc + v, 0);
            renderTotalConsolidadoAnual(yearActual, totalConsolidadoActual, yearPrev, totalConsolidadoPrev, _fmtCOP);

            _monthlyChartInstances[containerId] = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {
                            label: `Ventas ${yearActual}`,
                            data: ventasAct,
                            backgroundColor: COLOR_2026,
                            borderRadius: 4,
                            barPercentage: 0.8,
                            categoryPercentage: 0.7,
                            order: 4,
                            anio: yearActual, // Metadata nativa del dataset: fuente de verdad para el drill-down (no depende de parsear el label)
                            mesesReales
                        },
                        {
                            label: `Ventas ${yearPrev}`,
                            data: ventasPrev,
                            backgroundColor: COLOR_2025,
                            borderRadius: 4,
                            barPercentage: 0.8,
                            categoryPercentage: 0.7,
                            order: 3,
                            anio: yearPrev,
                            mesesReales
                        },
                        {
                            label: `Pedidos ${yearActual}`,
                            data: pedidosAct,
                            type: 'line',
                            borderColor: COLOR_2026,
                            borderWidth: 2,
                            borderDash: [5, 5],
                            backgroundColor: 'transparent',
                            pointStyle: 'circle',
                            pointRadius: 5,
                            pointHoverRadius: 7,
                            pointBackgroundColor: COLOR_2026,
                            pointBorderColor: '#fff',
                            pointBorderWidth: 1.5,
                            tension: 0,
                            order: 1,
                            anio: yearActual,
                            mesesReales
                        },
                        {
                            label: `Pedidos ${yearPrev}`,
                            data: pedidosPrev,
                            type: 'line',
                            borderColor: COLOR_2025,
                            borderWidth: 1.5,
                            borderDash: [3, 3],
                            backgroundColor: 'transparent',
                            pointStyle: 'rect',
                            pointRadius: 4,
                            pointHoverRadius: 6,
                            pointBackgroundColor: COLOR_2025,
                            pointBorderColor: '#fff',
                            pointBorderWidth: 1,
                            tension: 0,
                            order: 2,
                            anio: yearPrev,
                            mesesReales
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 400, easing: 'easeOutCubic' },
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: {
                            labels: { usePointStyle: true, font: { size: 11 }, padding: 16 }
                        },
                        tooltip: {
                            callbacks: {
                                label: c => c.raw > 0
                                    ? ` ${c.dataset.label}: ${_fmtCOP(c.raw)}`
                                    : null
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { borderDash: [4, 4], color: 'rgba(0,0,0,0.04)' },
                            ticks: {
                                callback: v => {
                                    if (v >= 1000000) return '$' + (v / 1000000).toFixed(1) + 'M';
                                    return '$' + _fmtNum(v);
                                }
                            }
                        },
                        x: { grid: { display: false } }
                    },
                    onClick: (event, _elements, chart) => {
                        // Resolución explícita del elemento clickeado, desacoplada del
                        // interaction.mode:'index' usado para el tooltip (ese modo devuelve
                        // TODOS los datasets en el índice más cercano, no el que se clickeó,
                        // lo que hacía que el modal siempre resolviera el mismo dataset/año
                        // sin importar la barra o línea sobre la que se hizo clic).
                        const [hit] = chart.getElementsAtEventForMode(event, 'nearest', { intersect: true }, false);
                        if (!hit) return;

                        const { index, datasetIndex } = hit;
                        const ds = chart.data.datasets[datasetIndex];
                        const label = chart.data.labels[index];
                        const valor = ds.data[index];
                        const anio = ds.anio; // Metadata nativa fijada al construir el dataset
                        // Número real de mes (1-12) provisto por el backend (campo "mes_num" de
                        // /api/dashboard/performance/monthly), NUNCA reconstruido parseando el label.
                        const mesNum = ds.mesesReales[index];
                        console.log('Mes enviado al backend:', mesNum);
                        // Delegar al drill-down existente si está disponible
                        if (typeof mostrarDrillDownVentas === 'function') {
                            mostrarDrillDownVentas(mesNum, anio, label, valor);
                        }
                    },
                    onHover: (event, chartElement) => {
                        event.native.target.style.cursor = chartElement[0] ? 'pointer' : 'default';
                    }
                }
            });
        } catch (e) {
            console.error(`[renderChartMonthlyPerformance] Error en canvas '${containerId}':`, e);
            mostrarErrorEnCanvas(ctx, 'No se pudo renderizar el gráfico comparativo anual.');
        }
    }

    /**
     * Carga datos del endpoint /api/dashboard/performance/monthly y renderiza el gráfico.
     * @param {string} containerId - id del canvas destino
     * @param {string} [desde]     - Fecha inicio YYYY-MM-DD (opcional)
     * @param {string} [hasta]     - Fecha fin YYYY-MM-DD (opcional)
     */
    async function fetchAndRenderMonthlyPerformance(containerId = 'chartMensual', desde = '', hasta = '') {
        try {
            const params = new URLSearchParams();
            if (desde) params.append('desde', desde);
            if (hasta) params.append('hasta', hasta);
            const url = '/api/dashboard/performance/monthly' + (params.toString() ? '?' + params.toString() : '');

            const pwaToken = localStorage.getItem('pwa_token');
            const headers = { 'Accept': 'application/json' };
            if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;

            const res  = await fetch(url, { headers, credentials: 'include' });
            if (!res.ok) {
                throw new Error(`HTTP ${res.status} al cargar el gráfico comparativo mensual`);
            }
            if (res.status === 403) {
                console.warn('[fetchAndRenderMonthlyPerformance] Acceso restringido (403). Sin permiso para este gráfico.');
                return;
            }
            const json = await res.json();
            if (json.success && json.data) {
                renderChartMonthlyPerformance(json.data, containerId);
            }
        } catch (e) {
            console.error('[fetchAndRenderMonthlyPerformance] Error:', e);
        }
    }

    // ── INTERACTIVIDAD INDEPENDIENTE: Gráfico Rendimiento Mensual Nuevo ──
    // Escuchar clicks globales para los toggles de Rendimiento Mensual (Dinero/Unidades)
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.btn-toggle-rendimiento, [data-mode-rendimiento]');
        if (btn) {
            e.preventDefault();
            const mode = btn.dataset.modeRendimiento || btn.dataset.mode;
            if (mode && window.ModuloDashboard && typeof window.ModuloDashboard.setModoRendimiento === 'function') {
                window.ModuloDashboard.setModoRendimiento(mode);
            }
        }
    });

    // Attach to windows for HTML access
    window.ModuloDashboard = {
        inicializar: iniciar,
        refrescar: cargarDatos,
        establecerFiltroFechas: establecerFiltroFechas,
        refrescarDatos: async () => {
            try {
                const response = await fetch('/api/wo/solicitar_sync', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sync_pendiente: true })
                });
                
                if (response.ok) {
                    Swal.fire({
                        icon: 'info',
                        title: 'Sincronización Solicitada',
                        text: 'El proceso iniciará en un momento. No es necesario esperar.',
                        timer: 3000,
                        showConfirmButton: false
                    });
                } else {
                    Swal.fire({
                        icon: 'warning',
                        title: 'No se pudo solicitar la sincronización',
                        text: `El servidor respondió con un error (HTTP ${response.status}). Los datos mostrados no se actualizarán.`,
                        timer: 4000,
                        showConfirmButton: false
                    });
                }
            } catch (error) {
                console.error("Error al solicitar sincronización:", error);
                Swal.fire({
                    icon: 'error',
                    title: 'Fallo de conexión',
                    text: 'No se pudo contactar al servidor para solicitar la sincronización.',
                    timer: 4000,
                    showConfirmButton: false
                });
            }
            // Ejecutamos también la recarga visual del Dashboard
            cargarDatos(true);
        },   // botón 🔄: fuerza lectura fresca (nocache)
        mostrarDetalleOperadorInyeccion: mostrarDetalleOperadorInyeccion,
        mostrarModalOperador: mostrarModalOperador,
        toggleChartView: toggleChartView,
        mostrarDetalleIncumplimiento: mostrarDetalleIncumplimiento,
        exportarBackorderCliente: exportarBackorderCliente,
        exportarCartera: exportarCartera,
        sortIncumplimiento: sortIncumplimiento,
        toggleOperatorSelection,
        abrirModalComparativa,
        abrirModalScrapDetalle,
        cargarProductosSinRotacion,
        suscribirClickNativoCanvas,
        // ── API pública del gráfico centralizado ──
        renderChartMonthlyPerformance,
        fetchAndRenderMonthlyPerformance
    };
    
    // Add dynamically added methods back to the new ModuloDashboard object
    window.ModuloDashboard.restaurarTacometroMesActual = restaurarTacometroMesActual;
    window.ModuloDashboard.reRenderizarVistaActiva = reRenderizarVistaActiva;
    window.ModuloDashboard.setModoRendimiento = function(nuevoModo) {
        currentRendimientoMode = nuevoModo;
        // Actualizar clases activas/inactivas en los botones UI
        document.querySelectorAll('.btn-toggle-rendimiento, [data-mode-rendimiento]').forEach(b => {
            const m = b.dataset.modeRendimiento || b.dataset.mode;
            b.classList.toggle('active', m === nuevoModo);
        });
        window.ModuloDashboard.reRenderizarVistaActiva();
    };
    window.ModuloDashboard.setModoPnc = setModoPnc;

    return window.ModuloDashboard;
})();
