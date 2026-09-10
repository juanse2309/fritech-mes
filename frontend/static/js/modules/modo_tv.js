/**
 * modo_tv.js - Pantalla rotativa de planta ("Modo TV").
 *
 * Ciclo de 5 pantallas, cada una recarga sus datos al activarse (no hay
 * websockets en el proyecto -- se sigue el mismo patrón de polling que ya
 * usan Almacén/Panel de Supervisión de Pulido, ver auth.js/pulido.js):
 *   Ronda del día:
 *     1. Ranking por Referencia (Mix de Producción) de HOY, con meta diaria
 *        (2.000 pzs) y el efecto de "caballitos" de carrera (mismo que el
 *        Dashboard, ver posicionarIconosCarreraMix en dashboard.js). Mismo
 *        dato que alimenta el aviso por voz del líder (utils.js corre en
 *        cualquier página, incluida esta, sin código adicional acá).
 *     2. Pulido en vivo -- versión de solo lectura del Panel de Supervisión
 *        (pulido.js), con foto del producto igual que el panel de admin,
 *        para que las operarias mismas vean quién está trabajando, hace
 *        cuánto, y si alguien quedó pausada sin reanudar.
 *     3. Reporte de Máquinas de HOY (mismo dato que la Vista 2 de
 *        Inyección, mes_control.js).
 *   Ronda de acumulado semanal (pedido del usuario 2026-09-10: solo tiene
 *   sentido repetir Máquinas y el Ranking/Mix -- "Pulido en vivo" es
 *   inherentemente de ahora mismo, no tiene una versión "de la semana"):
 *     4. Mismo Ranking por Referencia pero con rango lunes->hoy, sin meta
 *        diaria (no aplica a una semana) y con los caballitos igual.
 *     5. Reporte de Máquinas -- acumulado semanal (mismo endpoint, campos
 *        'produccion_semana'/'lotes_semana' agregados en
 *        ProgramacionService.obtener_dashboard_mes).
 */
window.ModuloTV = (function () {

    const SLIDES = [
        {
            id: 'ranking',
            duracion: 24000,
            titulo: 'Ranking por Referencia',
            subtitulo: 'Mix de Producción — Hoy · Meta diaria 2.000 pzs',
            cargar: cargarSlideRanking
        },
        {
            id: 'pulido',
            duracion: 24000,
            titulo: 'Pulido en Vivo',
            subtitulo: 'Quién está trabajando ahora mismo',
            cargar: cargarSlidePulido
        },
        {
            id: 'maquinas-hoy',
            duracion: 20000,
            titulo: 'Reporte de Máquinas',
            subtitulo: 'Avance de hoy',
            cargar: cargarSlideMaquinasHoy
        },
        {
            id: 'ranking-semana',
            duracion: 22000,
            titulo: 'Ranking por Referencia',
            subtitulo: 'Mix de Producción — Acumulado de la semana',
            cargar: cargarSlideRankingSemana
        },
        {
            id: 'maquinas-semana',
            duracion: 20000,
            titulo: 'Reporte de Máquinas',
            subtitulo: 'Acumulado de la semana',
            cargar: cargarSlideMaquinasSemana
        }
    ];

    function duracionDeSlide(id) {
        return SLIDES.find(s => s.id === id)?.duracion || 20000;
    }

    let indiceActual = 0;
    let timeoutRotacion = null;
    let intervalReloj = null;
    let intervalTimersPulido = null;
    let activo = false;
    let escHandler = null;
    let timeoutsAutoScroll = [];

    // Un chart de Chart.js por canvas (día y semana son canvases distintos,
    // ver HTML) -- se destruyen y recrean cada vez que su slide vuelve a
    // activarse, igual que el patrón que ya usa dashboard.js.
    const charts = { dia: null, semana: null };

    const PALETA_REFERENCIAS = [
        '#38bdf8', '#a78bfa', '#34d399', '#fbbf24', '#f472b6',
        '#fb923c', '#4ade80', '#60a5fa', '#f87171', '#facc15',
        '#2dd4bf', '#c084fc'
    ];

    // ── Ciclo de vida ──────────────────────────────────────────────────

    function inicializar() {
        if (activo) return;
        activo = true;
        indiceActual = 0;

        document.body.classList.add('tv-produccion-mode');

        escHandler = (e) => {
            if (e.key === 'Escape' && activo) salir();
        };
        setTimeout(() => { if (activo) window.addEventListener('keydown', escHandler); }, 400);

        try {
            if (document.documentElement.requestFullscreen) {
                document.documentElement.requestFullscreen().catch(() => {});
            }
        } catch (e) { /* Fullscreen no soportado/bloqueado: se sigue viendo bien en ventana normal */ }

        actualizarReloj();
        intervalReloj = setInterval(actualizarReloj, 1000);

        activarSlide(0);
    }

    function desactivar() {
        activo = false;
        document.body.classList.remove('tv-produccion-mode');

        if (timeoutRotacion) clearTimeout(timeoutRotacion);
        if (intervalReloj) clearInterval(intervalReloj);
        if (intervalTimersPulido) clearInterval(intervalTimersPulido);
        limpiarAutoScrollGrid();
        timeoutRotacion = null;
        intervalReloj = null;
        intervalTimersPulido = null;

        if (escHandler) window.removeEventListener('keydown', escHandler);
        escHandler = null;

        try {
            if (document.fullscreenElement) document.exitFullscreen();
        } catch (e) { /* noop */ }

        if (charts.dia) { charts.dia.destroy(); charts.dia = null; }
        if (charts.semana) { charts.semana.destroy(); charts.semana = null; }
    }

    function salir() {
        if (window.cargarPagina) window.cargarPagina('dashboard');
    }

    // ── Reloj ──────────────────────────────────────────────────────────

    function actualizarReloj() {
        const ahora = new Date();
        const horaCO = new Intl.DateTimeFormat('es-CO', {
            timeZone: 'America/Bogota', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
        }).format(ahora);
        const fechaCO = new Intl.DateTimeFormat('es-CO', {
            timeZone: 'America/Bogota', weekday: 'long', day: 'numeric', month: 'long'
        }).format(ahora);
        const elReloj = document.getElementById('tv-reloj');
        const elFecha = document.getElementById('tv-fecha');
        if (elReloj) elReloj.textContent = horaCO;
        if (elFecha) elFecha.textContent = fechaCO;
    }

    // ── Fechas (hora Colombia) ───────────────────────────────────────────

    function fechaHoyColombia() {
        // 'sv' (sueco) da directo el formato YYYY-MM-DD -- mismo truco que ya
        // usan otros módulos para no reconstruir la fecha a mano.
        return new Intl.DateTimeFormat('sv', { timeZone: 'America/Bogota' }).format(new Date());
    }

    function fechaLunesColombia() {
        // Mismo criterio que el backend (ProgramacionService.obtener_dashboard_mes):
        // lunes de la semana en curso, calculado sobre la fecha de HOY en
        // Colombia -- Colombia no tiene horario de verano, así que la
        // aritmética de días sobre una fecha local es segura.
        const hoy = new Date(`${fechaHoyColombia()}T00:00:00`);
        const diaSemana = hoy.getDay(); // 0=domingo, 1=lunes, ... 6=sábado
        const offsetDesdeElLunes = diaSemana === 0 ? 6 : diaSemana - 1;
        hoy.setDate(hoy.getDate() - offsetDesdeElLunes);
        return new Intl.DateTimeFormat('sv').format(hoy);
    }

    // ── Rotación ───────────────────────────────────────────────────────

    function activarSlide(idx) {
        if (!activo) return;
        indiceActual = idx % SLIDES.length;
        const slide = SLIDES[indiceActual];

        document.querySelectorAll('.tv-slide').forEach(el => {
            el.classList.toggle('active', el.dataset.tvSlide === slide.id);
        });
        document.querySelectorAll('.tv-dot').forEach(el => {
            el.classList.toggle('active', el.dataset.tvDot === slide.id);
        });

        const elTitulo = document.getElementById('tv-slide-titulo');
        const elSubtitulo = document.getElementById('tv-slide-subtitulo');
        if (elTitulo) elTitulo.textContent = slide.titulo;
        if (elSubtitulo) elSubtitulo.textContent = slide.subtitulo;

        // Al salir de la pantalla de Pulido en vivo, detener su cronómetro
        // por segundo -- no tiene sentido seguir calculándolo mientras la
        // tarjeta no es visible.
        if (intervalTimersPulido) { clearInterval(intervalTimersPulido); intervalTimersPulido = null; }
        limpiarAutoScrollGrid();

        animarBarraProgreso(slide.duracion);

        try {
            slide.cargar();
        } catch (e) {
            console.error(`[ModuloTV] Error cargando slide '${slide.id}':`, e);
        }

        if (timeoutRotacion) clearTimeout(timeoutRotacion);
        timeoutRotacion = setTimeout(() => activarSlide(indiceActual + 1), slide.duracion);
    }

    function animarBarraProgreso(duracionMs) {
        const fill = document.getElementById('tv-progreso-fill');
        if (!fill) return;
        fill.style.transition = 'none';
        fill.style.width = '0%';
        // Forzar reflow antes de animar, si no el navegador funde los dos estilos en uno.
        void fill.offsetWidth;
        fill.style.transition = `width ${duracionMs}ms linear`;
        fill.style.width = '100%';
    }

    const IDS_GRIDS_TV = ['tv-pulido-grid', 'tv-maquinas-hoy-grid', 'tv-maquinas-semana-grid'];

    /**
     * Auto-scroll de los grids de tarjetas (Pulido en vivo, Máquinas) --
     * pedido del usuario 2026-09-10 tras probar en la TV real: con las
     * tarjetas agrandadas para leerse a distancia, en la pantalla física de
     * la TV solo entran 1-2 filas, y el resto queda cortado sin que nadie
     * pueda hacer scroll manual.
     *
     * TERCER INTENTO -- el primero movía scrollTop con setTimeout: perfecto
     * en escritorio, nada en la TV real. El segundo cambió a una animación
     * CSS con @keyframes sobre 'transform' (traducción por compositor):
     * tampoco se movió en la TV. Lo único confirmado corriendo ahí es la
     * barra de progreso de abajo (animarBarraProgreso), que anima 'width'
     * -- una propiedad de layout, no de compositor. Así que acá se copia
     * ese mismo patrón EXACTO (transition + cambiar la propiedad, forzando
     * reflow antes de armar la transición) pero sobre 'margin-top' en vez
     * de 'width': layout puro, sin transform ni compositor de por medio,
     * para no depender de una capacidad que esa TV podría no tener.
     */
    function iniciarAutoScrollGrid(gridId, duracionMs) {
        const grid = document.getElementById(gridId);
        const viewport = grid?.parentElement;
        if (!grid || !viewport) return;

        grid.style.transition = 'none';
        grid.style.marginTop = '0px';
        void grid.offsetHeight; // forzar reflow, mismo truco que animarBarraProgreso

        // Pequeña espera a que el layout esté asentado (tarjetas ya
        // pintadas con su alto real) antes de medir.
        const t = setTimeout(() => {
            const distancia = grid.scrollHeight - viewport.clientHeight;
            if (distancia <= 4) return; // todo cabe en una pantalla, no hace falta animar

            grid.style.transition = `margin-top ${duracionMs}ms linear`;
            grid.style.marginTop = `-${Math.round(distancia)}px`;
        }, 50);
        timeoutsAutoScroll.push(t);
    }

    function limpiarAutoScrollGrid() {
        timeoutsAutoScroll.forEach(t => clearTimeout(t));
        timeoutsAutoScroll = [];
        IDS_GRIDS_TV.forEach(id => {
            const grid = document.getElementById(id);
            if (!grid) return;
            grid.style.transition = 'none';
            grid.style.marginTop = '0px';
        });
    }

    // ── Slides 1 y 4: Ranking por Referencia (Mix de Producción) ────────
    // Función compartida entre la ronda de HOY (con meta diaria) y la de
    // ACUMULADO SEMANAL (mismo endpoint, solo cambia el rango de fechas):
    // pedido del usuario 2026-09-10, mismo "caballito" de carrera en las
    // dos, pero la meta de 2.000 pzs/día no aplica a la vista semanal.

    async function renderRankingChart({ desde, hasta, canvasId, overlayId, vacioId, mostrarMeta, chartKey }) {
        const elVacio = document.getElementById(vacioId);
        const overlay = document.getElementById(overlayId);
        try {
            const res = await window.apiClient.get(`/dashboard/stats?desde=${desde}&hasta=${hasta}`);
            const data = res?.data || {};
            const profundo = data.rankings?.pulido_profundo || {};
            const operarioRef = data.analytics_pulido?.operario_referencia || {};

            const ops = Object.keys(profundo)
                .map(k => ({ nombre: k, buenas: profundo[k].buenas || 0 }))
                .sort((a, b) => b.buenas - a.buenas)
                .slice(0, 8)
                .filter(o => operarioRef[o.nombre]);

            if (charts[chartKey]) { charts[chartKey].destroy(); charts[chartKey] = null; }
            if (overlay) { overlay.innerHTML = ''; overlay.dataset.firma = ''; }

            if (ops.length === 0) {
                if (elVacio) elVacio.style.display = 'flex';
                return;
            }
            if (elVacio) elVacio.style.display = 'none';

            const totalPorRef = {};
            ops.forEach(o => {
                const refs = operarioRef[o.nombre] || {};
                Object.keys(refs).forEach(r => {
                    totalPorRef[r] = (totalPorRef[r] || 0) + (refs[r].cantidad_total || 0);
                });
            });
            const refsOrdenadas = Object.keys(totalPorRef).sort((a, b) => totalPorRef[b] - totalPorRef[a]);
            const refsTop = refsOrdenadas.slice(0, 10);
            const refsResto = refsOrdenadas.slice(10);

            // El líder (índice 0, ya viene ordenado por 'buenas' desc) se marca con
            // una corona -- mismo evento que dispara el aviso por voz de utils.js
            // (chequearLiderPulido), corriendo en segundo plano en esta misma página.
            const labels = ops.map((o, i) => i === 0 ? `🏆 ${o.nombre}` : o.nombre);

            const datasets = refsTop.map((ref, i) => ({
                label: ref,
                data: ops.map(o => (operarioRef[o.nombre]?.[ref]?.cantidad_total) || 0),
                backgroundColor: PALETA_REFERENCIAS[i % PALETA_REFERENCIAS.length],
                borderWidth: 0,
                borderRadius: 4,
                barThickness: 34
            }));
            if (refsResto.length > 0) {
                datasets.push({
                    label: `Otras (${refsResto.length})`,
                    data: ops.map(o => refsResto.reduce((acc, r) => acc + ((operarioRef[o.nombre]?.[r]?.cantidad_total) || 0), 0)),
                    backgroundColor: 'rgba(148, 163, 184, 0.6)',
                    borderWidth: 0,
                    borderRadius: 4,
                    barThickness: 40
                });
            }

            const totalPorOp = ops.map(o => o.buenas);
            const maxTotal = Math.max(1, ...totalPorOp);
            const META_DIARIA_PIEZAS = 2000;
            const axisMax = mostrarMeta
                ? Math.max(META_DIARIA_PIEZAS, Math.ceil(maxTotal * 1.25 / 100) * 100)
                : Math.ceil(maxTotal * 1.25 / 100) * 100;

            // Tamaños de letra pensados para leerse desde varios metros (TV de
            // planta 55"), no desde un monitor -- mismo criterio que el resto
            // de modo_tv.css.
            const ctx = document.getElementById(canvasId);
            charts[chartKey] = new Chart(ctx, {
                type: 'bar',
                data: { labels, datasets },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 600 },
                    scales: {
                        x: {
                            stacked: true, max: axisMax,
                            ticks: { color: '#94a3b8', font: { size: 18 } },
                            grid: { color: 'rgba(255,255,255,0.06)' }
                        },
                        y: {
                            stacked: true,
                            ticks: { color: '#f1f5f9', font: { size: 24, weight: '700' } },
                            grid: { display: false }
                        }
                    },
                    plugins: {
                        legend: {
                            position: 'bottom',
                            labels: { color: '#cbd5e1', boxWidth: 18, font: { size: 16 }, padding: 14 }
                        },
                        tooltip: { enabled: false }
                    }
                },
                plugins: [{
                    id: `tvRaceIcons_${chartKey}`,
                    afterDatasetsDraw: (chart) => posicionarCaballosCarrera(chart, overlayId, ops.length, totalPorOp, mostrarMeta)
                }]
            });
        } catch (e) {
            console.error(`[ModuloTV] Error cargando ranking (${chartKey}):`, e);
            if (elVacio) { elVacio.style.display = 'flex'; elVacio.textContent = 'No se pudo cargar el ranking.'; }
        }
    }

    async function cargarSlideRanking() {
        const hoy = fechaHoyColombia();
        await renderRankingChart({
            desde: hoy, hasta: hoy,
            canvasId: 'tv-chart-mix', overlayId: 'tv-mix-race-icons', vacioId: 'tv-mix-vacio',
            mostrarMeta: true, chartKey: 'dia'
        });
    }

    async function cargarSlideRankingSemana() {
        const lunes = fechaLunesColombia();
        const hoy = fechaHoyColombia();
        await renderRankingChart({
            desde: lunes, hasta: hoy,
            canvasId: 'tv-chart-mix-semana', overlayId: 'tv-mix-race-icons-semana', vacioId: 'tv-mix-vacio-semana',
            mostrarMeta: false, chartKey: 'semana'
        });
    }

    // Colores del "calor" de la carrera: rojo intenso para el 1er lugar,
    // enfriando hacia gris azulado para el último -- mismo criterio que
    // colorCalorPorRango en dashboard.js (interpolación RGB simple).
    const COLOR_CALOR_TOP = [239, 68, 68];
    const COLOR_CALOR_BOTTOM = [148, 163, 184];

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
     * Mismo "caballito" de carrera que el Dashboard (ver
     * posicionarIconosCarreraMix en dashboard.js): un 🐎 en la punta de
     * cada barra, con color/velocidad de pulso según el puesto -- las
     * clases .pulido-mix-race-icon/.mix-race-meta y sus @keyframes ya
     * están cargadas globalmente vía dashboard.css, así que se reutilizan
     * tal cual, sin duplicar CSS. Con mostrarMeta=false (ronda semanal,
     * pedido del usuario 2026-09-10: "no vale la pena poner la meta, solo
     * el caballito") se omiten las líneas de pista y la bandera de meta.
     */
    function posicionarCaballosCarrera(chart, overlayId, totalOps, totalPorOp, mostrarMeta) {
        const overlay = document.getElementById(overlayId);
        const xScale = chart.scales && chart.scales.x;
        if (!overlay || !xScale) return;

        const primerMeta = chart.getDatasetMeta(0);
        const canvasRect = chart.canvas.getBoundingClientRect();
        const overlayRect = overlay.getBoundingClientRect();
        const offsetX = canvasRect.left - overlayRect.left;
        const offsetY = canvasRect.top - overlayRect.top;
        const area = chart.chartArea;

        let html = '';
        if (mostrarMeta) {
            const salidaX = area.left + offsetX;
            const metaX = area.right + offsetX;
            const pistaTop = area.top + offsetY;
            const pistaAlto = area.bottom - area.top;
            html += `
                <div style="position:absolute; left:${salidaX}px; top:${pistaTop}px; width:0; height:${pistaAlto}px; border-left:2px dashed rgba(148,163,184,0.55);"></div>
                <div style="position:absolute; left:${metaX}px; top:${pistaTop}px; width:0; height:${pistaAlto}px; border-left:2px dashed rgba(100,116,139,0.6);"></div>
                <div class="mix-race-meta" style="left:${metaX - 9}px; top:${pistaTop - 22}px;">🏁<span>META</span></div>
            `;
        }

        for (let i = 0; i < totalOps; i++) {
            const el = primerMeta.data[i];
            if (!el) continue;
            const valorTotal = totalPorOp[i] || 0;
            const x = xScale.getPixelForValue(valorTotal) + offsetX + 4;
            const y = el.y + offsetY;
            const color = colorCalorPorRango(i, totalOps);
            const duracion = i === 0
                ? '0.45'
                : Math.min(1.35, 0.9 * Math.pow(1.10, i - 1)).toFixed(2);
            html += `<span class="pulido-mix-race-icon" style="left:${x}px; top:${y}px; --heat-color:${color}; animation-duration:${duracion}s;">🐎</span>`;
        }

        // Igual que en dashboard.js: afterDatasetsDraw dispara en cada redraw
        // (incluyendo hover); solo tocar el DOM si el layout cambió de
        // verdad evita reiniciar la animación CSS desde cero todo el tiempo.
        if (overlay.dataset.firma === html) return;
        overlay.dataset.firma = html;
        overlay.innerHTML = html;
    }

    // ── Catálogo de productos (para la foto del buje en Pulido en vivo) ──
    // Mismo criterio que pulido.js (_obtenerImagenProducto/normalizarCodigo):
    // se carga una sola vez y se reutiliza mientras la pestaña siga abierta.

    let productosCache = null;
    let productosCachePromise = null;

    async function obtenerCatalogoProductos() {
        if (productosCache) return productosCache;
        if (!productosCachePromise) {
            productosCachePromise = fetch('/api/productos/listar')
                .then(r => r.json())
                .then(data => {
                    productosCache = data?.items || data?.productos || [];
                    return productosCache;
                })
                .catch(() => { productosCache = []; return productosCache; });
        }
        return productosCachePromise;
    }

    function normalizarCodigoProducto(c) {
        if (!c) return '';
        return String(c).toUpperCase().replace(/FR-/gi, '').trim();
    }

    function obtenerImagenProducto(codigo, productos) {
        if (!codigo || !productos) return null;
        const codigoNorm = normalizarCodigoProducto(codigo);
        const prod = productos.find(p => normalizarCodigoProducto(p.codigo_sistema) === codigoNorm);
        if (!prod || !prod.imagen) return null;
        let url = prod.imagen;
        if (!url.startsWith('/') && !url.startsWith('http') && !url.startsWith('data:')) {
            url = `/static/img/productos/${url}`;
        }
        return url;
    }

    // ── Slide 2: Pulido en vivo (solo lectura) ──────────────────────────

    const TEMA_ESTADO_PULIDO = {
        TRABAJANDO: { acento: '#16a34a', badge: '#16a34a' },
        EN_PROCESO: { acento: '#16a34a', badge: '#16a34a' },
        PAUSADO: { acento: '#d97706', badge: '#d97706' },
        PAUSADO_COLA: { acento: '#64748b', badge: '#64748b' }
    };
    const MINUTOS_ALERTA_PAUSA = 12;

    async function cargarSlidePulido() {
        const grid = document.getElementById('tv-pulido-grid');
        if (!grid) return;
        try {
            const [res, productos] = await Promise.all([
                window.apiClient.get('/pulido/admin/sesiones'),
                obtenerCatalogoProductos()
            ]);
            const sesiones = res?.data?.sesiones || [];

            if (sesiones.length === 0) {
                grid.innerHTML = '<div class="tv-vacio-slide">No hay nadie trabajando ni pausado ahora mismo.</div>';
                return;
            }

            grid.innerHTML = sesiones.map(s => {
                const tema = TEMA_ESTADO_PULIDO[s.estado] || TEMA_ESTADO_PULIDO.PAUSADO_COLA;
                const imagenUrl = obtenerImagenProducto(s.codigo, productos);
                const imagenHtml = imagenUrl
                    ? `<img src="${imagenUrl}" alt="${s.codigo || ''}" onerror="this.parentElement.innerHTML='<i class=\\'fas fa-cog\\'></i>';">`
                    : `<i class="fas fa-cog"></i>`;

                return `
                <div class="tv-card" style="border-top-color:${tema.acento}"
                     data-tv-pulido-card="${s.id_pulido}"
                     data-estado="${s.estado}"
                     data-hora-inicio="${s.hora_inicio_dt || ''}"
                     data-hora-pausa="${s.hora_pausa_dt || ''}"
                     data-pausa-acumulada="${s.tiempo_pausa_acumulado || 0}">
                    <div class="tv-card-header">
                        <span class="tv-card-nombre">${s.responsable || '—'}</span>
                        <span class="tv-card-badge" style="background:${tema.badge}22;color:${tema.badge}">${s.estado}</span>
                    </div>
                    <div class="tv-card-row-imagen" style="background:${tema.acento}18;">
                        <div class="tv-card-imagen">${imagenHtml}</div>
                        <div class="tv-card-info">
                            <div class="tv-card-timer" data-tv-pulido-timer>--:--:--</div>
                            <div class="tv-card-meta">${s.codigo || '—'} · Lote ${s.lote || '—'} · OP ${s.orden_produccion || 'SIN OP'}</div>
                        </div>
                    </div>
                    <div class="tv-card-alerta" data-tv-pulido-alerta style="display:none;"></div>
                </div>`;
            }).join('');

            tickTimersPulido();
            if (intervalTimersPulido) clearInterval(intervalTimersPulido);
            intervalTimersPulido = setInterval(tickTimersPulido, 1000);
            iniciarAutoScrollGrid('tv-pulido-grid', duracionDeSlide('pulido'));
        } catch (e) {
            console.error('[ModuloTV] Error cargando Pulido en vivo:', e);
            grid.innerHTML = '<div class="tv-vacio-slide">No se pudo cargar Pulido en vivo.</div>';
        }
    }

    function tickTimersPulido() {
        document.querySelectorAll('[data-tv-pulido-card]').forEach(card => {
            const timerEl = card.querySelector('[data-tv-pulido-timer]');
            const alertaEl = card.querySelector('[data-tv-pulido-alerta]');
            if (!timerEl) return;

            const estado = card.dataset.estado;
            const horaInicio = card.dataset.horaInicio ? new Date(card.dataset.horaInicio) : null;
            const pausaAcumuladaMs = (parseInt(card.dataset.pausaAcumulada, 10) || 0) * 1000;
            const enPausa = estado === 'PAUSADO' || estado === 'PAUSADO_COLA';

            if (!horaInicio || isNaN(horaInicio.getTime())) {
                timerEl.textContent = '--:--:--';
                return;
            }

            let diffMs;
            let minutosPausado = 0;
            if (enPausa) {
                const horaPausa = card.dataset.horaPausa ? new Date(card.dataset.horaPausa) : new Date();
                diffMs = horaPausa - horaInicio - pausaAcumuladaMs;
                minutosPausado = (new Date() - horaPausa) / 60000;
            } else {
                diffMs = new Date() - horaInicio - pausaAcumuladaMs;
            }

            const safeDiff = Math.max(0, diffMs);
            const hrs = String(Math.floor(safeDiff / 3600000)).padStart(2, '0');
            const mins = String(Math.floor((safeDiff % 3600000) / 60000)).padStart(2, '0');
            const secs = String(Math.floor((safeDiff % 60000) / 1000)).padStart(2, '0');
            timerEl.textContent = `${hrs}:${mins}:${secs}`;

            if (alertaEl) {
                if (enPausa && minutosPausado >= MINUTOS_ALERTA_PAUSA) {
                    alertaEl.style.display = 'block';
                    alertaEl.textContent = `⚠️ Pausada hace ${Math.floor(minutosPausado)} min sin reanudar`;
                } else {
                    alertaEl.style.display = 'none';
                }
            }
        });
    }

    // ── Slides 3 y 5: Reporte de Máquinas (hoy / semana) ────────────────

    const TEMA_ESTADO_MAQUINA = {
        EN_PROCESO: '#2563eb',
        PROGRAMADO: '#16a34a',
        LIBRE: '#64748b'
    };

    async function obtenerMaquinasDashboard() {
        const res = await window.apiClient.get('/mes/dashboard');
        return res?.maquinas || [];
    }

    async function cargarSlideMaquinasHoy() {
        const grid = document.getElementById('tv-maquinas-hoy-grid');
        if (!grid) return;
        try {
            const maquinas = await obtenerMaquinasDashboard();
            if (maquinas.length === 0) {
                grid.innerHTML = '<div class="tv-vacio-slide">No hay máquinas configuradas.</div>';
                return;
            }

            const ordenadas = [...maquinas].sort((a, b) => {
                const num = s => parseInt((s.nombre || '').replace(/\D/g, '')) || 0;
                return num(a) - num(b);
            });

            grid.innerHTML = ordenadas.map(m => {
                const color = TEMA_ESTADO_MAQUINA[m.estado] || TEMA_ESTADO_MAQUINA.LIBRE;
                const activo = m.trabajo_activo;

                if (!activo) {
                    return `
                    <div class="tv-card" style="border-top-color:${color}">
                        <div class="tv-card-header">
                            <span class="tv-card-nombre">${m.nombre}</span>
                            <span class="tv-card-badge" style="background:${color}22;color:${color}">${m.estado}</span>
                        </div>
                        <div class="tv-card-meta" style="margin-top:20px;">Sin trabajo activo</div>
                    </div>`;
                }

                const productosHTML = (activo.productos_activos || []).map(p => `
                    <div class="tv-maq-producto"><span>${p.codigo_sistema || '-'}</span><span>${p.cavidades} cav.</span></div>
                `).join('');

                const lecturas = activo.lecturas_parciales_hoy || [];
                const lecturasHTML = lecturas.length > 0
                    ? lecturas.map(l => `<div class="tv-maq-producto"><span>🕐 ${l.hora}</span><span>${Number(l.cierres).toLocaleString('es-CO')} cierres</span></div>`).join('')
                    : '<div class="tv-card-alerta" style="display:block;">Sin reporte de avance hoy todavía</div>';

                return `
                <div class="tv-card" style="border-top-color:${color}">
                    <div class="tv-card-header">
                        <span class="tv-card-nombre">${m.nombre}</span>
                        <span class="tv-card-badge" style="background:${color}22;color:${color}">${m.estado}</span>
                    </div>
                    <div class="tv-maq-molde">Molde ${activo.molde || 'N/A'}</div>
                    <div class="tv-card-meta" style="margin-bottom:8px;">OP ${activo.orden_produccion || 'SIN OP'} · Inicio ${activo.hora_inicio || '—'}</div>
                    ${productosHTML}
                    <div style="margin-top:8px;">${lecturasHTML}</div>
                </div>`;
            }).join('');
            iniciarAutoScrollGrid('tv-maquinas-hoy-grid', duracionDeSlide('maquinas-hoy'));
        } catch (e) {
            console.error('[ModuloTV] Error cargando Reporte de Máquinas (hoy):', e);
            grid.innerHTML = '<div class="tv-vacio-slide">No se pudo cargar el reporte de máquinas.</div>';
        }
    }

    async function cargarSlideMaquinasSemana() {
        const grid = document.getElementById('tv-maquinas-semana-grid');
        const sub = document.getElementById('tv-maquinas-semana-sub');
        if (!grid) return;
        try {
            const maquinas = await obtenerMaquinasDashboard();
            if (maquinas.length === 0) {
                grid.innerHTML = '<div class="tv-vacio-slide">No hay máquinas configuradas.</div>';
                if (sub) sub.textContent = '';
                return;
            }

            const totalSemana = maquinas.reduce((acc, m) => acc + (m.produccion_semana || 0), 0);
            const desde = maquinas[0]?.semana_desde;
            if (sub) {
                const desdeFmt = desde ? new Date(`${desde}T00:00:00`).toLocaleDateString('es-CO', { day: 'numeric', month: 'long' }) : '';
                sub.textContent = `Total planta desde el lunes ${desdeFmt}: ${Math.round(totalSemana).toLocaleString('es-CO')} piezas`;
            }

            const ordenadas = [...maquinas].sort((a, b) => (b.produccion_semana || 0) - (a.produccion_semana || 0));

            grid.innerHTML = ordenadas.map(m => `
                <div class="tv-card" style="border-top-color:#38bdf8">
                    <div class="tv-card-header">
                        <span class="tv-card-nombre">${m.nombre}</span>
                        <span class="tv-card-badge" style="background:#38bdf822;color:#38bdf8">${m.lotes_semana || 0} lotes</span>
                    </div>
                    <div class="tv-maq-total-label">Piezas esta semana</div>
                    <div class="tv-maq-total">${Math.round(m.produccion_semana || 0).toLocaleString('es-CO')}</div>
                </div>`).join('');
            iniciarAutoScrollGrid('tv-maquinas-semana-grid', duracionDeSlide('maquinas-semana'));
        } catch (e) {
            console.error('[ModuloTV] Error cargando Reporte de Máquinas (semana):', e);
            grid.innerHTML = '<div class="tv-vacio-slide">No se pudo cargar el acumulado semanal.</div>';
        }
    }

    return {
        inicializar,
        desactivar,
        salir
    };
})();
