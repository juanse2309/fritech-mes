/**
 * Dashboard Tour — guía interactiva desacoplada del Dashboard IA.
 *
 * Solo define los pasos y la condición de "pantalla lista"; el motor de
 * recorrido (overlay, popovers, navegación, persistencia) vive en
 * tour_engine.js (Fase 10, 2026-08-27) y es compartido con Inyección,
 * Ensamble, Empaque y Exportar a WO -- ver ese archivo para las reglas de
 * arquitectura (cero acoplamiento, cero librerías nuevas, persistencia
 * 100% local).
 */
window.DashboardTour = (function () {
    'use strict';

    const STEPS = [
        {
            selector: '#dashboard-icono-ayuda-ejemplo',
            title: '1/5 · Íconos de Ayuda',
            content: 'Cada tarjeta del dashboard tiene un ícono ⓘ junto a su título. Pasa el mouse por encima para ver la explicación directa de esa tarjeta específica.',
            placement: 'bottom'
        },
        {
            selector: '#dashboard-filtros-globales',
            title: '2/5 · Filtros Globales',
            content: 'Filtra la información de todo el tablero por rango de fechas.',
            placement: 'bottom'
        },
        {
            selector: '#dashboard-btn-refrescar',
            title: '3/5 · Actualizar Datos',
            content: 'Fuerza una lectura fresca de los datos en vivo sin perder el rango de fechas seleccionado.',
            placement: 'bottom'
        },
        {
            selector: '#dashboard-bot-container',
            title: '4/5 · Análisis Inteligente',
            content: 'El Bot de Planta cruza producción, calidad y ventas para generar insights ejecutivos automáticamente.',
            placement: 'bottom'
        },
        {
            selector: '#dashboard-toggle-unidades-dinero',
            title: '5/5 · Unidades vs. Dinero',
            content: 'Alterna la visualización de las tarjetas entre Unidades y Dinero para comparar el rendimiento desde ambas perspectivas.',
            placement: 'left'
        }
    ];

    // Señal puramente de DOM (sin llamar funciones internas de ModuloDashboard):
    // ni el loader de pantalla completa puede seguir visible, ni el bot puede seguir
    // mostrando su placeholder estático — ambos indican que los datos aún no llegaron.
    function pantallaListaParaTour() {
        const loader = document.getElementById('global-loader');
        if (loader && loader.offsetParent !== null) return false;

        const bot = document.getElementById('dashboard-bot-text');
        if (bot && bot.textContent.trim().startsWith('Analizando datos de la planta en tiempo real')) return false;

        return true;
    }

    return window.TourEngine.crear({
        storageKey: 'frt_tour_dashboard_v1',
        steps: STEPS,
        pageElementId: 'dashboard-page',
        listoParaTour: pantallaListaParaTour
    });
})();
