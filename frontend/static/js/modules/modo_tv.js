/**
 * modo_tv.js - Pantalla rotativa de planta ("Modo TV").
 *
 * Ciclo de 6 pantallas, cada una recarga sus datos al activarse (no hay
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
 *     3. Metas de Ensamble (2026-09-24): barra de avance y checklist de
 *        procesos por meta (GET /ensamble/modo_tv). Solo lo reportado -- no hay
 *        "en vivo" porque la UI de Ensamble no registra el inicio en la BD.
 *     4. Reporte de Máquinas de HOY (mismo dato que la Vista 2 de
 *        Inyección, mes_control.js).
 *   Ronda de acumulado semanal (pedido del usuario 2026-09-10: solo tiene
 *   sentido repetir Máquinas y el Ranking/Mix -- "Pulido en vivo" es
 *   inherentemente de ahora mismo, no tiene una versión "de la semana"):
 *     5. Mismo Ranking por Referencia pero con rango lunes->hoy, sin meta
 *        diaria (no aplica a una semana) y con los caballitos igual.
 *     6. Reporte de Máquinas -- acumulado semanal (mismo endpoint, campos
 *        'produccion_semana'/'lotes_semana' agregados en
 *        ProgramacionService.obtener_dashboard_mes).
 */
window.ModuloTV = (function () {

    // Duraciones subidas ~30% (pedido del usuario 2026-09-22: "salta" de una
    // vista a otra) y la última del ciclo (maquinas-semana) queda con más
    // aire todavía antes de volver a saltar a la primera, para que el corte
    // del loop no se sienta tan abrupto.
    const SLIDES = [
        {
            id: 'ranking',
            duracion: 30000,
            titulo: 'Ranking por Referencia',
            subtitulo: 'Mix de Producción — Hoy · Meta diaria 2.000 pzs',
            cargar: cargarSlideRanking
        },
        {
            id: 'pulido',
            duracion: 30000,
            titulo: 'Pulido en Vivo',
            subtitulo: 'Quién está trabajando ahora mismo',
            cargar: cargarSlidePulido
        },
        {
            id: 'ensamble',
            duracion: 30000,
            titulo: 'Metas de Ensamble',
            subtitulo: 'Avance reportado y procesos hechos',
            cargar: cargarSlideEnsamble
        },
        {
            id: 'maquinas-hoy',
            duracion: 26000,
            titulo: 'Reporte de Máquinas',
            subtitulo: 'Avance de hoy',
            cargar: cargarSlideMaquinasHoy
        },
        {
            id: 'ranking-semana',
            duracion: 28000,
            titulo: 'Ranking por Referencia',
            subtitulo: 'Mix de Producción — Acumulado de la semana',
            cargar: cargarSlideRankingSemana
        },
        {
            id: 'maquinas-semana',
            duracion: 30000,
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
            // Con el diálogo de "último accidente" abierto, Esc solo lo cierra a él.
            if (e.key === 'Escape' && activo && !document.querySelector('.swal2-container')) salir();
        };
        setTimeout(() => { if (activo) window.addEventListener('keydown', escHandler); }, 400);

        try {
            if (document.documentElement.requestFullscreen) {
                document.documentElement.requestFullscreen().catch(() => {});
            }
        } catch (e) { /* Fullscreen no soportado/bloqueado: se sigue viendo bien en ventana normal */ }

        const btnEditarSeguridad = document.getElementById('tv-seguridad-editar');
        if (btnEditarSeguridad) btnEditarSeguridad.onclick = editarFechaAccidente;

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
        pintarSeguridad();
    }

    // ── Días sin accidentes (encabezado) ────────────────────────────────
    // La fecha la guarda y valida el backend (GET/POST /seguridad/ultimo_accidente,
    // solo ADMIN edita). Sin fecha registrada NO se muestra nada: un "0" o un
    // número inventado en la TV de planta sería peor que ningún dato.

    let seguridad = { fecha: null, puedeEditar: false };

    function diasDesdeFecha(fechaISO) {
        // Aritmética en UTC sobre fechas calendario (sin hora): inmune a zonas y horarios.
        const [y, m, d] = fechaISO.split('-').map(Number);
        const [Y, M, D] = fechaHoyColombia().split('-').map(Number);
        return Math.round((Date.UTC(Y, M - 1, D) - Date.UTC(y, m - 1, d)) / 86400000);
    }

    function pintarSeguridad() {
        const caja = document.getElementById('tv-seguridad');
        const texto = document.getElementById('tv-seguridad-texto');
        const btn = document.getElementById('tv-seguridad-editar');
        if (!caja || !texto) return;

        let dias = seguridad.fecha ? diasDesdeFecha(seguridad.fecha) : null;
        if (dias !== null && dias < 0) dias = null; // fecha futura: se trata como "sin dato"

        const visible = dias !== null || seguridad.puedeEditar;
        caja.style.display = visible ? 'flex' : 'none';
        if (btn) btn.style.display = seguridad.puedeEditar ? '' : 'none';
        if (!visible) return;

        caja.classList.toggle('tv-seguridad-cero', dias === 0);
        caja.classList.toggle('tv-seguridad-sin-dato', dias === null);
        const html = dias === null
            ? 'Sin fecha del<br>último accidente'
            : `<strong>${dias.toLocaleString('es-CO')}</strong><span>${dias === 1 ? 'día' : 'días'} sin<br>accidentes</span>`;
        if (texto.dataset.pintado !== html) {
            texto.innerHTML = html; // solo números y texto fijo, ningún dato del usuario
            texto.dataset.pintado = html;
        }
    }

    async function cargarSeguridad() {
        try {
            const res = await window.apiClient.get('/seguridad/ultimo_accidente');
            seguridad = { fecha: res?.data?.fecha || null, puedeEditar: !!res?.data?.puede_editar };
            pintarSeguridad();
        } catch (e) {
            // Sin red o sin permiso: se conserva lo último que se mostró.
            console.error('[ModuloTV] Error cargando días sin accidentes:', e);
        }
    }

    async function editarFechaAccidente() {
        const hoy = fechaHoyColombia();
        const { value: fecha } = await Swal.fire({
            title: 'Fecha del último accidente',
            text: 'El contador cuenta los días desde esta fecha. Si hubo un accidente hoy, elige la fecha de hoy.',
            input: 'date',
            inputValue: seguridad.fecha || hoy,
            inputAttributes: { max: hoy, min: '2000-01-01' },
            showCancelButton: true,
            confirmButtonText: 'Guardar',
            cancelButtonText: 'Cancelar',
            customClass: { container: 'tv-swal-z' },
            inputValidator: (v) => !v ? 'Elige una fecha' : (v > hoy ? 'La fecha no puede ser futura' : undefined)
        });
        if (!fecha) return;
        try {
            await window.apiClient.post('/seguridad/ultimo_accidente', { fecha });
            await cargarSeguridad();
        } catch (e) {
            Swal.fire({
                icon: 'error', title: 'No se guardó',
                text: e?.body?.error || 'No se pudo guardar la fecha.',
                customClass: { container: 'tv-swal-z' }
            });
        }
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

        // Una vez por ciclo (al volver a la primera pantalla) se refresca el
        // contador de días sin accidentes; entre refrescos el número se
        // recalcula solo cada segundo (ver actualizarReloj), así cambia a
        // medianoche sin depender de la red.
        if (indiceActual === 0) cargarSeguridad();

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

    const IDS_GRIDS_TV = ['tv-pulido-grid', 'tv-ensamble-grid', 'tv-maquinas-hoy-grid', 'tv-maquinas-semana-grid'];

    /**
     * Auto-scroll de los grids de tarjetas (Pulido en vivo, Máquinas) --
     * pedido del usuario 2026-09-10 tras probar en la TV real: con las
     * tarjetas agrandadas para leerse a distancia, en la pantalla física de
     * la TV solo entran 1-2 filas, y el resto queda cortado sin que nadie
     * pueda hacer scroll manual.
     *
     * CUARTO INTENTO. 1) scrollTop por JS: nada en la TV real. 2) animación
     * CSS con @keyframes sobre 'transform': tampoco se movió. 3) transition
     * sobre 'margin-top' (mismo patrón que la barra de progreso, que sí
     * corre ahí): esta vez SÍ se movió, pero a muy pocos FPS -- 'margin-top'
     * es una propiedad de layout, y animarla obliga al navegador a
     * recalcular el layout de toda la tarjeta en cada cuadro, carísimo
     * para el hardware limitado de una TV. Acá se prueba el combo de las
     * dos lecciones: 'transition' (que sí corre) pero sobre 'transform'
     * (que no toca layout, solo composición de capas -- mucho más barato).
     * Si en la TV real se sigue viendo entrecortado, hay que volver a
     * margin-top (funciona, aunque feo) en vez de esto.
     */
    function iniciarAutoScrollGrid(gridId, duracionMs) {
        const grid = document.getElementById(gridId);
        const viewport = grid?.parentElement;
        if (!grid || !viewport) return;

        grid.style.transition = 'none';
        grid.style.transform = 'translateY(0)';
        void grid.offsetHeight; // forzar reflow, mismo truco que animarBarraProgreso

        // Pequeña espera a que el layout esté asentado (tarjetas ya
        // pintadas con su alto real) antes de medir.
        const t = setTimeout(() => {
            const distancia = grid.scrollHeight - viewport.clientHeight;
            if (distancia <= 4) return; // todo cabe en una pantalla, no hace falta animar

            grid.style.transition = `transform ${duracionMs}ms linear`;
            grid.style.transform = `translateY(-${Math.round(distancia)}px)`;
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
            grid.style.transform = 'translateY(0)';
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
            // nocache=1: /dashboard/stats cachea 10 minutos en el servidor
            // (@cached_route). Sin esto, cuando alguien se pone de líder a
            // mitad de esos 10 min, utils.js SÍ anuncia el cambio por voz
            // de inmediato (esa consulta no tiene caché), pero el gráfico
            // de esta pantalla seguía mostrando datos viejos hasta que el
            // caché expirara por su cuenta -- pedido del usuario 2026-09-11
            // tras confirmar justo ese desfase en planta.
            const res = await window.apiClient.get(`/dashboard/stats?desde=${desde}&hasta=${hasta}&nocache=1`);
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
                borderRadius: 4
            }));
            if (refsResto.length > 0) {
                datasets.push({
                    label: `Otras (${refsResto.length})`,
                    data: ops.map(o => refsResto.reduce((acc, r) => acc + ((operarioRef[o.nombre]?.[r]?.cantidad_total) || 0), 0)),
                    // Gris oscuro y apagado (slate-700-ish) a propósito: contra el
                    // fondo azul-marino del TV, el slate-400 anterior quedaba MÁS
                    // claro que varios colores de la paleta y "saltaba" como si
                    // fuera una referencia más en vez de leerse como "el resto,
                    // menos relevante" -- pedido del usuario 2026-09-22.
                    backgroundColor: 'rgba(51, 65, 85, 0.75)',
                    borderWidth: 0,
                    borderRadius: 4
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
                    // Grosor de barra RELATIVO a la fila (antes barThickness fijo en
                    // px: 34, y 40 para "Otras"). En la TV real de planta
                    // (Tizen/WebOS, viewport ~960x540) cada fila mide ~33px, así
                    // que las barras se montaban unas sobre otras y Chart.js
                    // saltaba la mitad de los nombres (autoSkip) porque el texto
                    // de 26px ya no cabía -- reproducido 2026-09-24. A 1080p hay
                    // ~108px por fila y se veía bien, por eso solo fallaba en la TV.
                    // maxBarThickness evita barras gigantes cuando sobra espacio.
                    datasets: { bar: { categoryPercentage: 0.9, barPercentage: 0.85, maxBarThickness: 44 } },
                    scales: {
                        x: {
                            stacked: true, max: axisMax,
                            ticks: { color: '#cbd5e1', font: { size: 20 } },
                            grid: { color: 'rgba(255,255,255,0.06)' }
                        },
                        y: {
                            stacked: true,
                            ticks: {
                                color: '#f1f5f9',
                                // Nunca omitir nombres: si hay poco alto, el texto se
                                // achica (26px máx. -> 11px mín.) en vez de esconder
                                // operarias. Se calcula con el alto real por fila.
                                autoSkip: false,
                                font: (c) => {
                                    const area = c.chart.chartArea;
                                    const altoFila = area ? (area.bottom - area.top) / ops.length : 60;
                                    return { size: Math.max(11, Math.min(26, Math.floor(altoFila * 0.6))), weight: '700' };
                                }
                            },
                            grid: { display: false }
                        }
                    },
                    plugins: {
                        legend: {
                            position: 'bottom',
                            // boxWidth/boxHeight más grandes + más padding entre items:
                            // con 11 referencias en una sola fila se veían "montadas"
                            // (el swatch de color casi ilegible a distancia de TV) --
                            // pedido del usuario 2026-09-22.
                            labels: { color: '#e2e8f0', boxWidth: 24, boxHeight: 16, font: { size: 17, weight: '600' }, padding: 18 }
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
    const TEMA_BREAK = { acento: '#71717a', badge: '#a1a1aa' };

    // Pausas programadas (Desayuno/Almuerzo): las entrega el backend junto a
    // las sesiones ('pausas_programadas', ver PausasService.obtener_ventanas),
    // son las mismas horas que descuenta al cerrar el reporte -- no se copian
    // acá. Todo lo de abajo es SOLO visual: no cambia el estado real de
    // ninguna sesión ni escribe nada. Vacío (sin badge, sin descuento) hasta
    // que llegue el primer fetch: ante la duda, el cronómetro se comporta
    // como antes.
    let ventanasPausa = [];

    function aMinutosDelDia(hhmm) {
        const [h, m] = String(hhmm).split(':');
        return parseInt(h, 10) * 60 + parseInt(m, 10);
    }

    function ventanaBreakActual() {
        const partes = new Intl.DateTimeFormat('es-CO', {
            timeZone: 'America/Bogota', hour: '2-digit', minute: '2-digit', hour12: false
        }).formatToParts(new Date());
        const h = parseInt(partes.find(p => p.type === 'hour')?.value || '0', 10);
        const m = parseInt(partes.find(p => p.type === 'minute')?.value || '0', 10);
        const min = h * 60 + m;
        return ventanasPausa.find(v => min >= aMinutosDelDia(v.inicio) && min < aMinutosDelDia(v.fin)) || null;
    }

    /**
     * Milisegundos de [inicio, fin] que caen dentro de las ventanas de
     * pausa programada -- misma regla que PausasService.calcular_descuento_
     * pausas_programadas: solape de intervalos, y si el tramo cruza de día
     * no se descuenta nada. 'inicio' y 'fin' se interpretan igual que ya
     * hacía el cronómetro (Date local), y las ventanas se arman sobre la
     * fecha de 'inicio' con el mismo constructor, así el solape es
     * consistente sin depender de la zona horaria del navegador.
     */
    function calcularDescuentoBreakMs(inicio, fin, ventanas = ventanasPausa) {
        if (!(inicio instanceof Date) || !(fin instanceof Date)) return 0;
        if (isNaN(inicio) || isNaN(fin) || fin <= inicio) return 0;
        if (inicio.toDateString() !== fin.toDateString()) return 0;

        const pad = (n) => String(n).padStart(2, '0');
        const ymd = `${inicio.getFullYear()}-${pad(inicio.getMonth() + 1)}-${pad(inicio.getDate())}`;
        return ventanas.reduce((acc, v) => {
            const vIni = new Date(`${ymd}T${v.inicio}:00`);
            const vFin = new Date(`${ymd}T${v.fin}:00`);
            const solape = Math.min(fin, vFin) - Math.max(inicio, vIni);
            return solape > 0 ? acc + solape : acc;
        }, 0);
    }

    async function cargarSlidePulido() {
        const grid = document.getElementById('tv-pulido-grid');
        if (!grid) return;
        try {
            const [res, productos] = await Promise.all([
                window.apiClient.get('/pulido/admin/sesiones'),
                obtenerCatalogoProductos()
            ]);
            const sesiones = res?.data?.sesiones || [];
            ventanasPausa = res?.data?.pausas_programadas || [];

            if (sesiones.length === 0) {
                grid.innerHTML = '<div class="tv-vacio-slide">No hay nadie trabajando ni pausado ahora mismo.</div>';
                return;
            }

            grid.innerHTML = sesiones.map(s => {
                const tema = TEMA_ESTADO_PULIDO[s.estado] || TEMA_ESTADO_PULIDO.PAUSADO_COLA;
                const imagenUrl = obtenerImagenProducto(s.codigo, productos);
                const imagenHtml = imagenUrl
                    ? `<img src="${escapeHtml(imagenUrl)}" alt="${escapeHtml(s.codigo || '')}" onerror="this.parentElement.innerHTML='<i class=\\'fas fa-cog\\'></i>';">`
                    : `<i class="fas fa-cog"></i>`;

                return `
                <div class="tv-card" style="border-top-color:${tema.acento}"
                     data-tv-pulido-card="${escapeHtml(s.id_pulido)}"
                     data-estado="${escapeHtml(s.estado)}"
                     data-hora-inicio="${escapeHtml(s.hora_inicio_dt || '')}"
                     data-hora-pausa="${escapeHtml(s.hora_pausa_dt || '')}"
                     data-pausa-acumulada="${s.tiempo_pausa_acumulado || 0}">
                    <div class="tv-card-header">
                        <span class="tv-card-nombre">${escapeHtml(s.responsable || '—')}</span>
                        <span class="tv-card-badge" data-tv-pulido-badge style="background:${tema.badge}22;color:${tema.badge}">${escapeHtml(s.estado)}</span>
                    </div>
                    <div class="tv-card-row-imagen" style="background:${tema.acento}18;">
                        <div class="tv-card-imagen">${imagenHtml}</div>
                        <div class="tv-card-info">
                            <div class="tv-card-timer" data-tv-pulido-timer>--:--:--</div>
                            <div class="tv-card-meta">${escapeHtml(s.codigo || '—')} · Lote ${escapeHtml(s.lote || '—')} · OP ${escapeHtml(s.orden_produccion || 'SIN OP')}</div>
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

    function formatearCronometro(ms) {
        const safe = Math.max(0, ms);
        const hrs = String(Math.floor(safe / 3600000)).padStart(2, '0');
        const mins = String(Math.floor((safe % 3600000) / 60000)).padStart(2, '0');
        const secs = String(Math.floor((safe % 60000) / 1000)).padStart(2, '0');
        return `${hrs}:${mins}:${secs}`;
    }

    function tickTimersPulido() {
        const breakActual = ventanaBreakActual();

        document.querySelectorAll('[data-tv-pulido-card]').forEach(card => {
            const timerEl = card.querySelector('[data-tv-pulido-timer]');
            const alertaEl = card.querySelector('[data-tv-pulido-alerta]');
            const badgeEl = card.querySelector('[data-tv-pulido-badge]');
            if (!timerEl) return;

            const estado = card.dataset.estado;
            const horaInicio = card.dataset.horaInicio ? new Date(card.dataset.horaInicio) : null;
            const pausaAcumuladaMs = (parseInt(card.dataset.pausaAcumulada, 10) || 0) * 1000;
            const enPausa = estado === 'PAUSADO' || estado === 'PAUSADO_COLA';

            if (!horaInicio || isNaN(horaInicio.getTime())) {
                timerEl.textContent = '--:--:--';
                return;
            }

            // Se resta el solape con Desayuno/Almuerzo (igual que hace el
            // backend al cerrar el reporte) para que el cronómetro de la TV
            // coincida con el tiempo que finalmente se guarda -- solo
            // pantalla, no toca el estado real de la sesión.
            let diffMs;
            let minutosPausado = 0;
            if (enPausa) {
                const horaPausa = card.dataset.horaPausa ? new Date(card.dataset.horaPausa) : new Date();
                diffMs = horaPausa - horaInicio - pausaAcumuladaMs - calcularDescuentoBreakMs(horaInicio, horaPausa);
                minutosPausado = (new Date() - horaPausa) / 60000;
            } else {
                const ahora = new Date();
                diffMs = ahora - horaInicio - pausaAcumuladaMs - calcularDescuentoBreakMs(horaInicio, ahora);
            }

            timerEl.textContent = formatearCronometro(diffMs);

            // Badge de break: solo si la sesión sigue TRABAJANDO -- una pausa
            // real (PAUSADO) sigue siendo la información que importa mostrar.
            if (badgeEl) {
                const enTrabajo = estado === 'TRABAJANDO' || estado === 'EN_PROCESO';
                const tema = (enTrabajo && breakActual)
                    ? TEMA_BREAK
                    : (TEMA_ESTADO_PULIDO[estado] || TEMA_ESTADO_PULIDO.PAUSADO_COLA);
                badgeEl.textContent = (enTrabajo && breakActual) ? `☕ BREAK · ${breakActual.nombre}` : estado;
                badgeEl.style.background = `${tema.badge}22`;
                badgeEl.style.color = tema.badge;
                card.style.borderTopColor = tema.acento;
            }

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

    // ── Slide 2b: Metas de Ensamble ─────────────────────────────────────
    // Una tarjeta por meta con su avance y el checklist de procesos. Es lo único
    // REAL que se puede mostrar de Ensamble: la UI no registra un "inicio" en la
    // BD (solo escribe al pausar/reanudar o al finalizar un reporte), así que NO
    // hay forma de saber quién está ensamblando en este instante -- mostrar
    // "Nadie ensamblando" sería falso. cantidad_realizada es la suma de los
    // reportes FINALIZADOS de la meta (ver EnsambleService.reportar_multi), o
    // sea, el avance se mueve cada vez que alguien envía un reporte, no de
    // forma continua.

    const ETIQUETAS_CHECKLIST_ENSAMBLE = {
        ensamble_crudo: 'Crudo', rayada_carcaza: 'Rayada carcaza', rayada_interno: 'Rayada interno',
        pintura: 'Pintura', horno1: 'Horno 1', ensamble: 'Ensamble', cerrada: 'Cerrada', horno2: 'Horno 2'
    };
    const TEMA_META_ENSAMBLE = {
        COMPLETADO: '#16a34a',
        EN_PROCESO: '#2563eb',
        PENDIENTE: '#64748b'
    };

    function htmlOpRecuadro(op, chico) {
        const clases = `tv-maq-op${chico ? ' tv-maq-op-sm' : ''}${op ? '' : ' tv-maq-op-vacio'}`;
        return `<div class="${clases}">${op ? `OP ${escapeHtml(op)}` : 'SIN OP'}</div>`;
    }

    function htmlTarjetaMetaEnsamble(m, hoyISO) {
        const objetivo = Number(m.cantidad_objetivo) || 0;
        const realizada = Number(m.cantidad_realizada) || 0;
        const pct = objetivo > 0 ? Math.round((realizada * 100) / objetivo) : 0;
        const atrasada = m.estado !== 'COMPLETADO' && m.fecha_programada && m.fecha_programada < hoyISO;
        const color = atrasada ? '#d97706' : (TEMA_META_ENSAMBLE[m.estado] || TEMA_META_ENSAMBLE.PENDIENTE);

        // Solo los procesos que aplican: NO_APLICA no se dibuja (menos ruido y menos alto).
        const chips = Object.keys(ETIQUETAS_CHECKLIST_ENSAMBLE)
            .filter(k => (m.checklist || {})[k] && m.checklist[k] !== 'NO_APLICA')
            .map(k => m.checklist[k] === 'HECHO'
                ? `<span class="tv-ens-chip tv-ens-chip-hecho">✓ ${ETIQUETAS_CHECKLIST_ENSAMBLE[k]}</span>`
                : `<span class="tv-ens-chip">${ETIQUETAS_CHECKLIST_ENSAMBLE[k]}</span>`)
            .join('');

        let avisoAtraso = '';
        if (atrasada) {
            const fechaFmt = new Date(`${m.fecha_programada}T00:00:00`)
                .toLocaleDateString('es-CO', { day: 'numeric', month: 'short' });
            avisoAtraso = `<div class="tv-ens-atraso">Pendiente desde el ${escapeHtml(fechaFmt)}</div>`;
        }

        return `
        <div class="tv-card" style="border-top-color:${color}">
            <div class="tv-card-header">
                <span class="tv-card-nombre">${escapeHtml(m.id_codigo || '—')}</span>
                <span class="tv-card-badge" style="background:${color}22;color:${color}">${escapeHtml(m.estado)}</span>
            </div>
            ${htmlOpRecuadro(m.op_numero, true)}
            <div class="tv-ens-barra"><div class="tv-ens-barra-fill" style="width:${Math.min(100, pct)}%;background:${color}"></div></div>
            <div class="tv-ens-avance">
                <span>${realizada.toLocaleString('es-CO')} de ${objetivo.toLocaleString('es-CO')}</span>
                <span>${pct}%</span>
            </div>
            <div class="tv-ens-chips">${chips}</div>
            ${avisoAtraso}
        </div>`;
    }

    async function cargarSlideEnsamble() {
        const grid = document.getElementById('tv-ensamble-grid');
        if (!grid) return;
        try {
            const res = await window.apiClient.get('/ensamble/modo_tv');
            const metas = res?.data?.metas || [];

            if (metas.length === 0) {
                grid.innerHTML = '<div class="tv-vacio-slide">No hay metas de ensamble pendientes.</div>';
                return;
            }

            const hoyISO = fechaHoyColombia();
            grid.innerHTML = metas.map(m => htmlTarjetaMetaEnsamble(m, hoyISO)).join('');
            iniciarAutoScrollGrid('tv-ensamble-grid', duracionDeSlide('ensamble'));
        } catch (e) {
            console.error('[ModuloTV] Error cargando Metas de Ensamble:', e);
            grid.innerHTML = '<div class="tv-vacio-slide">No se pudo cargar las metas de Ensamble.</div>';
        }
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
                            <span class="tv-card-nombre">${escapeHtml(m.nombre)}</span>
                            <span class="tv-card-badge" style="background:${color}22;color:${color}">${escapeHtml(m.estado)}</span>
                        </div>
                        <div class="tv-card-meta" style="margin-top:20px;">Sin trabajo activo</div>
                    </div>`;
                }

                const productosHTML = (activo.productos_activos || []).map(p => `
                    <div class="tv-maq-producto"><span>${escapeHtml(p.codigo_sistema || '-')}</span><span>${escapeHtml(p.cavidades)} cav.</span></div>
                `).join('');

                const lecturas = activo.lecturas_parciales_hoy || [];
                const lecturasHTML = lecturas.length > 0
                    ? lecturas.map(l => `<div class="tv-maq-producto"><span>🕐 ${escapeHtml(l.hora)}</span><span>${Number(l.cierres).toLocaleString('es-CO')} cierres</span></div>`).join('')
                    : '<div class="tv-card-alerta" style="display:block;">Sin reporte de avance hoy todavía</div>';

                // El OP es lo que la gente de Pulido usa para identificar el trabajo,
                // por eso va grande y en su propio recuadro (pedido del usuario
                // 2026-09-24); el molde pasa a la línea secundaria. Sin OP se marca
                // en ámbar, para que se note de lejos.
                const op = activo.orden_produccion;
                return `
                <div class="tv-card" style="border-top-color:${color}">
                    <div class="tv-card-header">
                        <span class="tv-card-nombre">${escapeHtml(m.nombre)}</span>
                        <span class="tv-card-badge" style="background:${color}22;color:${color}">${escapeHtml(m.estado)}</span>
                    </div>
                    <div class="tv-maq-op${op ? '' : ' tv-maq-op-vacio'}">${op ? `OP ${escapeHtml(op)}` : 'SIN OP'}</div>
                    <div class="tv-card-meta" style="margin-bottom:8px;">Molde ${escapeHtml(activo.molde || 'N/A')} · Inicio ${escapeHtml(activo.hora_inicio || '—')}</div>
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

            // 'lotes_en_proceso_semana': lotes que ya arrancaron esta semana pero
            // siguen EN_PROCESO -- su producción todavía no suma a produccion_semana
            // (solo se cuenta al cerrar el lote). Se avisa como pendiente en vez de
            // inventar un número de piezas sin auditar (ver programacion_service.py).
            grid.innerHTML = ordenadas.map(m => {
                const enCurso = m.lotes_en_proceso_semana || 0;
                const avisoEnCurso = enCurso > 0
                    ? `<div class="tv-maq-pendiente">+ ${enCurso} lote${enCurso > 1 ? 's' : ''} en curso esta semana, aún sin cerrar</div>`
                    : '';
                return `
                <div class="tv-card" style="border-top-color:#38bdf8">
                    <div class="tv-card-header">
                        <span class="tv-card-nombre">${m.nombre}</span>
                        <span class="tv-card-badge" style="background:#38bdf822;color:#38bdf8">${m.lotes_semana || 0} lotes cerrados</span>
                    </div>
                    <div class="tv-maq-total-label">Piezas esta semana (validadas)</div>
                    <div class="tv-maq-total">${Math.round(m.produccion_semana || 0).toLocaleString('es-CO')}</div>
                    ${avisoEnCurso}
                </div>`;
            }).join('');
            iniciarAutoScrollGrid('tv-maquinas-semana-grid', duracionDeSlide('maquinas-semana'));
        } catch (e) {
            console.error('[ModuloTV] Error cargando Reporte de Máquinas (semana):', e);
            grid.innerHTML = '<div class="tv-vacio-slide">No se pudo cargar el acumulado semanal.</div>';
        }
    }

    return {
        inicializar,
        desactivar,
        salir,
        // Expuestas solo para poder probarlas con datos sintéticos.
        calcularDescuentoBreakMs,
        diasDesdeFecha
    };
})();
