/**
 * Exportación a WO Tour ("vista de Zoe") — explica la Fase 6 del plan
 * OP->WO (2026-08-25): selección por casillas, vista previa sin efectos
 * secundarios, y descarga separada por área. Ver tour_engine.js para el
 * motor compartido.
 */
window.ExportacionWoTour = (function () {
    'use strict';

    const STEPS = [
        {
            selector: '#expwo-listado',
            title: '1/3 · Selecciona por casillas',
            content: 'Marca las Órdenes de Producción que quieras exportar. El estado de cada una te dice en qué punto va: <b>RESERVADA</b> (existe pero el día no ha cerrado), <b>LISTA_EXPORTAR</b> (ya se puede bajar), <b>EXPORTADA</b> (ya se descargó al menos una vez).',
            placement: 'top'
        },
        {
            selector: '#btn-expwo-preview',
            title: '2/3 · Vista previa es solo mirar',
            content: 'Revisa el contenido exacto del archivo antes de bajarlo. No marca nada como exportado ni cambia ningún estado -- puedes usarla las veces que quieras.',
            placement: 'bottom'
        },
        {
            selector: '#btn-expwo-descargar',
            title: '3/3 · Descarga separada por área',
            content: 'Si seleccionas OP de una sola área, baja un archivo directo. Si mezclas Inyección, Ensamble y Empaque en la misma selección, baja un .zip con un archivo POR ÁREA adentro -- nunca se combinan en uno solo, así un error en un archivo no bloquea los otros dos.',
            placement: 'bottom'
        }
    ];

    return window.TourEngine.crear({
        storageKey: 'frt_tour_exportacion_wo_v1',
        steps: STEPS,
        pageElementId: 'exportacion-wo-page'
    });
})();
