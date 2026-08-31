/**
 * Ensamble Tour — explica la Fase 2 del plan OP->WO (2026-08-25): OP
 * automática al programar, y "Cerrar Jornada" como la señal explícita de
 * fin de día para que la OP quede lista para exportar. Ver tour_engine.js
 * para el motor compartido.
 *
 * El paso de "Cerrar Jornada" vive en la pestaña "Reporte de Tareas", que
 * arranca oculta -- por eso usa onBeforeShow para activarla primero,
 * disparando el mismo click que usaría un usuario real (ver
 * ensamble.js:209, cambiarTab).
 */
window.EnsambleTour = (function () {
    'use strict';

    function activarTabReporte() {
        const btnTab = document.querySelector('#ensamble-tabs .nav-link[data-tab="reporte"]');
        if (btnTab) btnTab.click();
    }

    const STEPS = [
        {
            selector: '#ensamble-tabs',
            title: '1/2 · Dos roles, dos pestañas',
            content: 'Aquí se programan las metas del día (quién ensambla qué), y en "Reporte de Tareas" quien está en planta va marcando el avance real.',
            placement: 'bottom'
        },
        {
            selector: '#btn-cerrar-jornada-ensamble',
            title: '2/2 · Cerrar Jornada',
            content: 'Al terminar el día, este botón marca la Orden de Producción como lista para que se exporte a World Office al día siguiente. Si alguna meta del día tiene el checklist de procesos incompleto, no deja cerrar -- hay que completarlo o justificar por qué no aplica.',
            placement: 'top',
            onBeforeShow: activarTabReporte
        }
    ];

    return window.TourEngine.crear({
        storageKey: 'frt_tour_ensamble_v1',
        steps: STEPS,
        pageElementId: 'ensamble-page'
    });
})();
