/**
 * Empaque Tour — explica el módulo nuevo (reunión 2026-08-25): nadie
 * programa, se reporta lo armado; la OP del día sale sola con el primer
 * reporte; y antes de confirmar, siempre se ve qué se va a descontar. Ver
 * tour_engine.js para el motor compartido.
 */
window.EmpaqueTour = (function () {
    'use strict';

    const STEPS = [
        {
            selector: '#empaque-buscador',
            title: '1/2 · Busca y arma',
            content: 'Aquí no se programa nada -- se reporta lo que ya se armó. Busca la referencia y pon la cantidad: apenas la elijas, del lado derecho aparece la vista previa de qué materiales se van a descontar, antes de confirmar nada. La Orden de Producción del día se crea sola con el primer reporte -- no hay que pedirla ni teclearla.',
            placement: 'right'
        },
        {
            selector: '#btn-empaque-reportar',
            title: '2/2 · Si falta material',
            content: 'Si no alcanza el stock de algún componente (ni en P. Terminado ni en Por Pulir), el sistema no deja registrar y dice exactamente cuánto falta de cuál referencia -- nunca descuenta a medias.',
            placement: 'top'
        }
    ];

    return window.TourEngine.crear({
        storageKey: 'frt_tour_empaque_v1',
        steps: STEPS,
        pageElementId: 'empaque-page'
    });
})();
