/**
 * Tour Engine — motor genérico de recorridos guiados, extraído de
 * DashboardTour (Fase 10 del plan OP->WO, 2026-08-27) para no reimplementar
 * la misma máquina de estados 5 veces (Dashboard, Inyección, Ensamble,
 * Empaque, Exportar a WO).
 *
 * Reglas de arquitectura (heredadas de DashboardTour, ya aprobadas):
 * 1. Cero acoplamiento: cada tour solo LEE el DOM que su página ya pinta.
 *    El motor nunca llama funciones internas de ningún ModuloX.
 * 2. Ligereza: usa exclusivamente bootstrap.Popover. Cero librerías nuevas.
 * 3. Persistencia local: "tutorial visto" vive 100% en localStorage, una
 *    clave por tour -- el backend nunca se entera.
 *
 * Uso: window.TourEngine.crear({ storageKey, steps, pageElementId, listoParaTour }).
 * Devuelve { iniciar, cerrar, autoIniciarSiNoVisto }, igual que exponía
 * DashboardTour directamente antes de este refactor.
 *
 * Cada step admite: { selector, title, content, placement, onBeforeShow }.
 * onBeforeShow(el) corre ANTES de comprobar visibilidad -- pensado para
 * revelar contenido detrás de un tab (ver ensamble_tour.js: hace clic en la
 * pestaña "Reporte" antes de señalar "Cerrar Jornada", que vive ahí oculto).
 */
window.TourEngine = (function () {
    'use strict';

    function crear(config) {
        const STORAGE_KEY = config.storageKey;
        const STEPS = config.steps || [];
        const pageElementId = config.pageElementId;
        const listoParaTour = typeof config.listoParaTour === 'function' ? config.listoParaTour : () => true;

        // --- Estado interno del tour activo ---
        let activeSteps = [];
        let currentIndex = -1;
        let currentPopover = null;
        let currentTargetEl = null;
        let overlayEl = null;
        let resizeHandler = null;
        let keydownHandler = null;
        let clickHandler = null;
        let pendingTimeoutId = null;

        function esVisible(el) {
            return !!el && el.offsetParent !== null;
        }

        function crearOverlay() {
            if (overlayEl) return overlayEl;
            overlayEl = document.createElement('div');
            overlayEl.className = 'dtour-overlay';
            document.body.appendChild(overlayEl);
            return overlayEl;
        }

        function destruirOverlay() {
            if (overlayEl) {
                overlayEl.remove();
                overlayEl = null;
            }
        }

        // Mitigación de memory leaks: SIEMPRE se destruye la instancia previa de
        // Popover (bootstrap.Popover crea listeners/Popper internos que no se
        // liberan solos si solo se oculta el elemento; hay que llamar dispose()).
        function limpiarPasoActual() {
            if (pendingTimeoutId) {
                clearTimeout(pendingTimeoutId);
                pendingTimeoutId = null;
            }
            if (currentPopover) {
                currentPopover.dispose();
                currentPopover = null;
            }
            if (currentTargetEl) {
                currentTargetEl.classList.remove('dtour-highlight');
                currentTargetEl = null;
            }
        }

        function construirContenidoHTML(index, total) {
            const esUltimo = index === total - 1;
            const botonAtras = index > 0
                ? '<button type="button" class="dtour-btn dtour-btn-secondary" data-dtour-action="anterior">Atrás</button>'
                : '';
            return `
                <div class="dtour-progress">Paso ${index + 1} de ${total}</div>
                <div class="dtour-nav">
                    <button type="button" class="dtour-btn dtour-btn-link" data-dtour-action="cerrar">Saltar tour</button>
                    <div class="d-flex gap-2">
                        ${botonAtras}
                        <button type="button" class="dtour-btn dtour-btn-primary" data-dtour-action="siguiente">${esUltimo ? 'Finalizar' : 'Siguiente'}</button>
                    </div>
                </div>
            `;
        }

        function mostrarPaso(index) {
            limpiarPasoActual();

            if (index < 0 || index >= activeSteps.length) {
                cerrar();
                return;
            }

            const step = activeSteps[index];

            if (typeof step.onBeforeShow === 'function') {
                try { step.onBeforeShow(); } catch (e) { console.warn('TourEngine: onBeforeShow falló', e); }
            }

            const el = document.querySelector(step.selector);

            if (!esVisible(el)) {
                // Selector inexistente u oculto (RBAC, tab no activo pese al
                // onBeforeShow, etc.): saltar al siguiente paso.
                mostrarPaso(index + 1);
                return;
            }

            currentIndex = index;
            currentTargetEl = el;
            el.classList.add('dtour-highlight');
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });

            // Pequeño margen para que el scroll (y el onBeforeShow, si animó
            // algo) termine antes de posicionar el popover.
            pendingTimeoutId = setTimeout(() => {
                pendingTimeoutId = null;
                if (currentTargetEl !== el) return;

                currentPopover = new bootstrap.Popover(el, {
                    container: 'body',
                    trigger: 'manual',
                    html: true,
                    sanitize: false,
                    placement: step.placement || 'auto',
                    fallbackPlacements: ['top', 'bottom', 'right', 'left'],
                    customClass: 'dtour-popover',
                    title: `<div class="dtour-popover-title">${step.title}</div>`,
                    content: `<div class="dtour-body-text">${step.content}</div>${construirContenidoHTML(index, activeSteps.length)}`
                });
                currentPopover.show();
            }, 300);
        }

        function siguiente() {
            mostrarPaso(currentIndex + 1);
        }

        function anterior() {
            mostrarPaso(currentIndex - 1);
        }

        function marcarComoVisto() {
            try {
                localStorage.setItem(STORAGE_KEY, '1');
            } catch (e) {
                console.warn('TourEngine: no se pudo escribir en localStorage', e);
            }
        }

        function cerrar() {
            limpiarPasoActual();
            destruirOverlay();

            if (resizeHandler) {
                window.removeEventListener('resize', resizeHandler);
                resizeHandler = null;
            }
            if (keydownHandler) {
                document.removeEventListener('keydown', keydownHandler);
                keydownHandler = null;
            }
            if (clickHandler) {
                document.removeEventListener('click', clickHandler);
                clickHandler = null;
            }

            const habiaTourActivo = currentIndex !== -1;
            currentIndex = -1;
            activeSteps = [];

            if (habiaTourActivo) marcarComoVisto();
            document.dispatchEvent(new CustomEvent('tour:end', { detail: { storageKey: STORAGE_KEY } }));
        }

        function iniciar() {
            // Evitar doble arranque si ya hay un tour en curso
            if (currentIndex !== -1) return;

            // Los steps con onBeforeShow (revelan contenido detrás de un tab)
            // se incluyen siempre -- su visibilidad real se resuelve en
            // mostrarPaso(), después de ejecutar onBeforeShow.
            activeSteps = STEPS.filter(step =>
                typeof step.onBeforeShow === 'function' || esVisible(document.querySelector(step.selector))
            );
            if (activeSteps.length === 0) {
                console.warn(`TourEngine[${STORAGE_KEY}]: no hay pasos visibles para este rol/vista, tour omitido.`);
                return;
            }

            crearOverlay();

            clickHandler = (e) => {
                const btn = e.target.closest('[data-dtour-action]');
                if (!btn) return;
                const accion = btn.getAttribute('data-dtour-action');
                if (accion === 'siguiente') siguiente();
                else if (accion === 'anterior') anterior();
                else if (accion === 'cerrar') cerrar();
            };
            document.addEventListener('click', clickHandler);

            keydownHandler = (e) => {
                if (e.key === 'Escape') cerrar();
            };
            document.addEventListener('keydown', keydownHandler);

            resizeHandler = () => {
                if (currentPopover) currentPopover.update();
            };
            window.addEventListener('resize', resizeHandler);

            mostrarPaso(0);
        }

        function autoIniciarSiNoVisto() {
            let visto;
            try {
                visto = localStorage.getItem(STORAGE_KEY);
            } catch (e) {
                console.warn('TourEngine: localStorage no disponible', e);
                return;
            }
            if (visto !== null) return;

            const ESPERA_MAX_MS = 8000;
            const POLL_MS = 300;
            const inicioEspera = Date.now();

            const esperarCargaYArrancar = () => {
                if (listoParaTour() || Date.now() - inicioEspera >= ESPERA_MAX_MS) {
                    setTimeout(iniciar, 300);
                    return;
                }
                setTimeout(esperarCargaYArrancar, POLL_MS);
            };

            setTimeout(esperarCargaYArrancar, 600);
        }

        // --- Auto-arranque desacoplado ---
        // Observa la clase 'active' de #<pageElementId> (mecanismo genérico ya
        // usado por app.js para mostrar/ocultar páginas) en vez de engancharse
        // a la inicialización de ningún ModuloX.
        (function initAutoWatch() {
            if (!pageElementId) return;
            const pageEl = document.getElementById(pageElementId);
            if (!pageEl || typeof MutationObserver === 'undefined') return;

            let estabaActiva = pageEl.classList.contains('active');
            if (estabaActiva) autoIniciarSiNoVisto();

            const observer = new MutationObserver(() => {
                const activaAhora = pageEl.classList.contains('active');
                if (activaAhora && !estabaActiva) autoIniciarSiNoVisto();
                estabaActiva = activaAhora;
            });
            observer.observe(pageEl, { attributes: true, attributeFilter: ['class'] });
        })();

        return { iniciar, cerrar, autoIniciarSiNoVisto };
    }

    return { crear };
})();
