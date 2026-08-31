/**
 * Inyección Tour — explica el cambio más importante de la Fase 1 del plan
 * OP->WO (2026-08-25): la Orden de Producción ya no se teclea a mano, sale
 * asignada sola al programar. Un paso por cada submódulo real (1.
 * Programación / 2. Reporte de Máquina / 3. Validación), cambiando de
 * pestaña automáticamente antes de cada uno -- mismo patrón que
 * ensamble_tour.js. Ver tour_engine.js para el motor compartido.
 */
window.InyeccionTour = (function () {
    'use strict';

    function irA(idBotonTab) {
        return () => {
            const btn = document.getElementById(idBotonTab);
            if (btn) btn.click();
        };
    }

    const STEPS = [
        {
            selector: '#form-mes-programar',
            title: '1/3 · Programación: la OP ya no se teclea',
            content: 'Al programar una máquina aquí para el día siguiente, FRITECH le asigna la Orden de Producción automáticamente -- ya no hay que crearla a mano en World Office ni escribirla de nuevo acá.',
            placement: 'right',
            onBeforeShow: irA('tab-programacion')
        },
        {
            selector: '#mes-dashboard-grid',
            title: '2/3 · Reporte de Máquina: ahí se inicia el trabajo',
            content: 'Cuando le des "Iniciar Trabajo" a una máquina desde aquí, el campo de OP ya viene lleno y bloqueado -- es la misma que se asignó al programar. Solo un administrador puede corregirla si hiciera falta.',
            placement: 'top',
            onBeforeShow: irA('tab-operacion')
        },
        {
            selector: '#select-validar-lote',
            title: '3/3 · Validación: el último filtro antes de WO',
            content: 'Aquí se audita cada lote reportado (cantidad buena vs. PNC) antes de cerrarlo. Solo un lote ya validado queda disponible para exportarse a World Office.',
            placement: 'bottom',
            onBeforeShow: irA('tab-legacy')
        }
    ];

    return window.TourEngine.crear({
        storageKey: 'frt_tour_inyeccion_v2',
        steps: STEPS,
        pageElementId: 'inyeccion-page'
    });
})();
