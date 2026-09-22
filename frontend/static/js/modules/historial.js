// ============================================
// historial.js - Lógica de Historial Global
// ============================================

(function () {
    console.log('✅ Historial.js cargando...');

    // Variables de estado privadas al módulo
    let h_datos = [];
    let h_paginaActual = 1;
    const getHRegistrosPorPagina = () => window.innerWidth < 992 ? 10 : 20;

    /**
     * Formatear hora a HH:MM (recorta segundos: 13:40:00 → 13:40)
     */
    function formatHorario(horaStr) {
        if (!horaStr || horaStr === '' || horaStr === 'None' || horaStr === 'undefined') return '';
        const parts = horaStr.toString().trim().split(':');
        if (parts.length >= 2) return `${parts[0].padStart(2, '0')}:${parts[1].padStart(2, '0')}`;
        return horaStr.trim();
    }

    /**
     * Formatear métrica numérica defensiva sin romper render por null/undefined/NaN.
     */
    function formatMetricaNum(val, decimals = 2, fallback = 'N/A') {
        if (val === null || val === undefined || val === '' || val === 'None' || val === 'null' || val === 'undefined') {
            return fallback;
        }
        const num = parseFloat(val);
        if (isNaN(num)) return fallback;
        return decimals !== null ? num.toFixed(decimals) : num.toString();
    }

    /**
     * Formatear la columna Detalle (Parsea JSON y tags especiales)
     */
    function formatearDetalle(detalle) {
        if (!detalle || detalle === '-' || detalle === 'None') return '<span class="text-muted">-</span>';
        
        let html = detalle;
        
        // 1. Procesar [PNC_DETAIL] con JSON
        const pncRegex = /\[PNC_DETAIL\]\s*(\{.*?\})/g;
        html = html.replace(pncRegex, (match, jsonStr) => {
            try {
                const data = JSON.parse(jsonStr);
                let badges = '';
                for (const [motivo, cant] of Object.entries(data)) {
                    const label = motivo.charAt(0).toUpperCase() + motivo.slice(1);
                    badges += `<span class="badge bg-danger bg-opacity-10 text-danger border border-danger-subtle me-1" style="font-size: 0.7rem; font-weight: 600;">
                                <i class="fas fa-exclamation-triangle me-1"></i>${label}: ${cant}
                               </span>`;
                }
                return badges;
            } catch (e) {
                return `<span class="badge bg-warning text-dark me-1" style="font-size: 0.7rem;">PNC: ${jsonStr}</span>`;
            }
        });

        // 2. Procesar [AUTO_BREAK] (Recesos automáticos)
        html = html.replace(/\[AUTO_BREAK\]/g, '<span class="badge bg-info bg-opacity-10 text-info border border-info-subtle me-1" style="font-size: 0.7rem; font-weight: 600;"><i class="fas fa-clock me-1"></i>RECESO</span>');

        // 3. Limpieza de separadores feos
        html = html.replace(/ \| /g, ' <span class="text-muted mx-1">|</span> ');

        return `<div class="detalle-formateado">${html}</div>`;
    }

    /**
     * Limpiar texto para exportación (Sin HTML y con JSON a texto plano)
     */
    function limpiarTextoParaExcel(detalle) {
        if (!detalle || detalle === '-' || detalle === 'None') return '';
        
        let text = detalle;
        
        // 1. Detectar y parsear JSON directamente (si el campo es un JSON string puro o contiene [PNC_DETAIL])
        const pncRegex = /\[PNC_DETAIL\]\s*(\{.*?\})/g;
        if (pncRegex.test(text)) {
            text = text.replace(pncRegex, (match, jsonStr) => {
                try {
                    const data = JSON.parse(jsonStr);
                    return "PNC: " + Object.entries(data).map(([k, v]) => `${k.toUpperCase()}: ${v}`).join(', ');
                } catch (e) { return jsonStr; }
            });
        } else if (text.trim().startsWith('{') && text.trim().endsWith('}')) {
            try {
                const data = JSON.parse(text);
                text = Object.entries(data).map(([k, v]) => `${k.toUpperCase()}: ${v}`).join(' | ');
            } catch (e) { /* No es JSON válido, dejar como está */ }
        }

        // 2. Limpieza de tags y caracteres especiales
        text = text.replace(/\[AUTO_BREAK\]/g, 'RECESO');
        text = text.replace(/<br\s*\/?>/gi, ' | ');
        text = text.replace(/&nbsp;/g, ' ');
        
        return text.trim();
    }


    /**
     * Cargar datos desde la API
     */
    async function cargarHistorial() {
        try {
            console.log('📜 Cargando historial v5.0 (SQL-Limpio)...');
            if (typeof mostrarLoading === 'function') mostrarLoading(true);

            const proceso = document.getElementById('tipoProceso')?.value || '';
            const desde = document.getElementById('fechaDesde')?.value || '';
            const hasta = document.getElementById('fechaHasta')?.value || '';
            const operario = document.getElementById('filtroOperario')?.value || '';
            const codigo = document.getElementById('filtroCodigo')?.value || '';

            let url = `/api/historial-global?tipo=${proceso}&desde=${desde}&hasta=${hasta}`;
            
            // Si el proceso es PULIDO, usamos el endpoint especializado con filtros dinámicos
            if (proceso === 'PULIDO') {
                url = `/api/pulido/historial?fecha_inicio=${desde}&fecha_fin=${hasta}&operario=${encodeURIComponent(operario)}&id_codigo=${encodeURIComponent(codigo)}`;
            }

            if (typeof fetchData !== 'function') {
                console.error('❌ Error: fetchData no está definida.');
                return;
            }

            const res = await fetchData(url);

            // LOG DE AUDITORÍA (Juan Sebastian Request)
            console.log('📡 [Historial] Respuesta recibida:', res);

            if (res && res.success && Array.isArray(res.data)) {
                let rawData = res.data;

                // Filtrado estricto por división
                const division = window.AppState.user?.division || 'FRIPARTS';
                if (division === 'FRIPARTS') {
                    h_datos = rawData.filter(r => r.Tipo !== 'METALS');
                } else {
                    h_datos = rawData.filter(r => r.Tipo === 'METALS');
                }

                // BUGFIX 2026-09-22: /api/historial-global ni siquiera recibe
                // 'operario'/'codigo' como parámetros (solo el camino especial de
                // PULIDO -> /api/pulido/historial los usaba), así que escribir en
                // esos campos con "Todos los Movimientos" u otro proceso distinto
                // a Pulido no filtraba nada. construir_movimientos_historial ya
                // normaliza TODOS los tipos (Inyección/Pulido/Ensamble/Ventas/PNC/
                // Metals) a las mismas llaves 'Responsable' y 'Producto' -- se
                // filtra acá en memoria (los datos ya están cargados completos)
                // en vez de tocar cada rama del backend por separado.
                const operarioFiltro = operario.trim().toUpperCase();
                const codigoFiltro = codigo.trim().toUpperCase();
                if (operarioFiltro) {
                    h_datos = h_datos.filter(r => String(r.Responsable || '').toUpperCase().includes(operarioFiltro));
                }
                if (codigoFiltro) {
                    h_datos = h_datos.filter(r => String(r.Producto || '').toUpperCase().includes(codigoFiltro));
                }

                h_paginaActual = 1;

                if (h_datos.length > 0) {
                    console.log('✅ Historial - Datos procesados exitosamente. Muestra:', h_datos[0]);
                } else {
                    console.log('⚠️ Historial - Filtro devolvió 0 registros');
                }

                try {
                    renderizarTablaHistorial();
                } catch (renderErr) {
                    console.error('❌ Error renderizando tabla historial:', renderErr);
                    const container = document.getElementById('historial-container');
                    if (container) container.innerHTML = '<div class="text-center py-5 text-danger"><i class="fas fa-exclamation-triangle mb-2"></i> Error al dibujar la tabla. Revisa la consola.</div>';
                }

                const totalSpan = document.getElementById('total-registros-historial');
                if (totalSpan) totalSpan.textContent = h_datos.length;
            } else {
                console.error('❌ La API no devolvió un Array válido:', res);
                if (typeof mostrarNotificacion === 'function') {
                    mostrarNotificacion('Error en formato de datos del historial', 'error');
                }
            }

        } catch (error) {
            console.error('❌ Error en cargarHistorial:', error);
        } finally {
            if (typeof mostrarLoading === 'function') mostrarLoading(false);
        }
    }

    /**
     * Renderizar la tabla con los datos actuales y paginación
     */
    function renderizarTablaHistorial() {
        const container = document.getElementById('historial-container');
        if (!container) return;

        if (!h_datos || h_datos.length === 0) {
            container.innerHTML = '<div class="text-center py-5 text-muted"><i class="fas fa-info-circle mb-2"></i> No se encontraron registros</div>';
            return;
        }

        // Calcular paginación
        const h_registrosPorPagina = getHRegistrosPorPagina();
        const inicio = (h_paginaActual - 1) * h_registrosPorPagina;
        const fin = inicio + h_registrosPorPagina;
        const registrosVisibles = h_datos.slice(inicio, fin);
        const totalPaginas = Math.ceil(h_datos.length / h_registrosPorPagina);

        // RBAC: Validación centralizada de permisos
        const rolActual = (window.AppState?.user?.rol || window.AppState?.user?.departamento || '').toUpperCase().trim();
        console.log('DEBUG Permisos - Rol/Depto actual:', rolActual);
        const terminosPermitidos = ['ADMIN', 'ADMINISTRADOR', 'ADMINISTRACION', 'AUXILIAR'];
        const puedeEditar = terminosPermitidos.some(termino => rolActual.includes(termino));

        // Detectar modo vista (Móvil vs Escritorio)
        const esMovil = window.innerWidth < 992;

        let html = '';

        if (esMovil) {
            // VISTA DE TARJETAS (MOBILE)
            html += '<div class="d-flex flex-column gap-3 pb-5">';

            registrosVisibles.forEach(r => {
                const badgeClass = obtenerBadgeClass(r.Tipo);

                // Normalización de datos
                let responsable = r.Responsable;
                let cantidad = r.Cant;
                let orden = r.Orden;
                let maquina = r.maquina || 'N/A';

                if (r.Tipo === 'INYECCION') {
                    responsable = r.RESPONSABLE || r.Responsable || r.OPERARIO || r.Usuario || '-';
                    cantidad = r['CANTIDAD REAL'] !== undefined ? r['CANTIDAD REAL'] : r.Cant;
                    orden = r['ORDEN PRODUCCION'] || r.Orden;
                    maquina = r.maquina || 'N/A';
                } else if (r.Tipo === 'PULIDO') {
                    responsable = r.RESPONSABLE || r.Responsable || r.OPERARIO || r.Usuario || '-';
                    // USAR cantidad_real según instrucción del usuario
                    cantidad = r.cantidad_real !== undefined ? r.cantidad_real : r.Cant;
                    orden = r['ORDEN PRODUCCION'] || r.Orden;
                    maquina = r.maquina || 'N/A';
                } else if (r.Tipo === 'METALS') {
                    responsable = r.Responsable || r.RESPONSABLE || '-';
                    cantidad = r.Cant || r.CANTIDAD_OK || '0';
                    orden = r.Orden || '-';
                    maquina = r.maquina || 'N/A';
                }

                html += `
                    <div class="card shadow-sm border-0" style="border-radius: 12px; overflow: hidden;">
                        <div class="card-body p-3">
                            <div class="d-flex justify-content-between align-items-center mb-2">
                                <span class="badge ${badgeClass}">${r.Tipo}</span>
                                <div class="d-flex align-items-center gap-2">
                                    <small class="text-muted fw-bold">${r.Fecha}</small>
                                    ${puedeEditar ?
                        `<button class="btn btn-sm btn-outline-primary p-1" onclick="window.ModuloHistorial.editarRegistro(${h_datos.indexOf(r)})" style="line-height: 1;">
                                            <i class="fas fa-pencil-alt" style="font-size: 0.8rem;"></i>
                                        </button>` : ''}
                                </div>
                            </div>
                            <h6 class="mb-1 fw-bold text-dark td-producto">
                                ${r.Producto ? `<a href="#" onclick="event.preventDefault(); window.ModuloHistorial.irAProducto('${r.Producto}');" class="text-primary text-decoration-underline">${r.Producto}</a>` : 'Sin Producto'}
                            </h6>
                            <div class="text-muted small mb-1">
                                <i class="fas fa-user me-1"></i> ${responsable}
                            </div>
                            <div class="mb-2">
                                ${formatearDetalle(r.Detalle)}
                            </div>
                            ${r.Tipo === 'PULIDO' && (r.HORA_INICIO || r.HORA_FIN) && formatHorario(r.HORA_INICIO) ? `<div class="horario-movimiento mb-2"><i class="far fa-clock me-1"></i>${formatHorario(r.HORA_INICIO)} - ${formatHorario(r.HORA_FIN) || '?'}</div>` : ''}
                            ${r.Tipo === 'INYECCION' && r.tiempo_total_minutos != null ? 
                                `<div class="metrica-inyeccion small text-muted mb-2">
                                    <i class="fas fa-stopwatch me-1"></i> Duración: <strong>${formatMetricaNum(r.tiempo_total_minutos, 2)}</strong> min | Seg/U: <strong>${formatMetricaNum(r.segundos_por_unidad, 2)}</strong>s | Cav: <strong>${formatMetricaNum(r.cavidades, 0)}</strong>
                                </div>` : ''}

                            
                            <div class="d-flex justify-content-between align-items-center bg-light p-2 rounded">
                                <div class="text-center px-2">
                                    <small class="d-block text-muted" style="font-size: 10px;">CANTIDAD</small>
                                    <span class="fw-bold fs-5 text-primary">${cantidad ?? '-'}</span>
                                </div>
                                <div class="text-center px-2 border-start">
                                    <small class="d-block text-muted" style="font-size: 10px;">ORDEN</small>
                                    <span class="fw-medium">${orden || '-'}</span>
                                </div>
                                <div class="text-center px-2 border-start">
                                    <small class="d-block text-muted" style="font-size: 10px;">MAQ</small>
                                    <span class="fw-medium">${maquina || '-'}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            });

            html += '</div>';

        } else {
            // VISTA DE TABLA (DESKTOP)
            html += `
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0" style="min-width: 1000px;">
                        <thead class="ps-4 bg-light">
                            <tr>
                                <th class="ps-4" style="width: 100px;">Fecha</th>
                                <th style="width: 120px;">Tipo</th>
                                <th style="width: 150px;">Responsable</th>
                                <th style="width: 120px;">Producto</th>
                                <th style="width: 120px;">Orden Prod.</th>
                                <th style="width: 100px;">Máquina</th>
                                <th>Detalle</th>
                                <th class="text-center" style="width: 80px;">Cant.</th>
                                ${puedeEditar ? '<th class="text-center" style="width: 50px;"></th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
            `;

            registrosVisibles.forEach(r => {
                const badgeClass = obtenerBadgeClass(r.Tipo);

                // Valores por defecto (normalizados)
                let responsable = r.Responsable;
                let cantidad = r.Cant;
                let orden = r.Orden;
                let maquina = r.maquina || 'N/A';

                // Manejo especial por proceso según requerimiento Juan Sebastian
                if (r.Tipo === 'INYECCION') {
                    responsable = r.RESPONSABLE || r.Responsable || r.OPERARIO || r.Usuario || '-';
                    cantidad = r['CANTIDAD REAL'] !== undefined ? r['CANTIDAD REAL'] : r.Cant;
                    orden = r['ORDEN PRODUCCION'] || r.Orden;
                    maquina = r.maquina || 'N/A';
                } else if (r.Tipo === 'PULIDO') {
                    responsable = r.RESPONSABLE || r.Responsable || r.OPERARIO || r.Usuario || '-';
                    // USAR cantidad_real según instrucción del usuario
                    cantidad = r.cantidad_real !== undefined ? r.cantidad_real : r.Cant;
                    orden = r['ORDEN PRODUCCION'] || r.Orden;
                    maquina = r.maquina || 'N/A';
                } else if (r.Tipo === 'METALS') {
                    responsable = r.Responsable || r.RESPONSABLE || '-';
                    cantidad = r.Cant || r.CANTIDAD_OK || '0';
                    orden = r.Orden || '-';
                    maquina = r.maquina || 'N/A';
                }

                html += `
                    <tr>
                        <td class="ps-4">${r.Fecha || '-'}</td>
                        <td><span class="badge ${badgeClass}">${r.Tipo || 'N/A'}</span></td>
                        <td>${responsable || '-'}</td>
                        <td class="td-producto">
                            <strong>
                                ${r.Producto ? `<a href="#" onclick="event.preventDefault(); window.ModuloHistorial.irAProducto('${r.Producto}');" class="text-primary text-decoration-underline">${r.Producto}</a>` : '-'}
                            </strong>
                        </td>
                        <td><span class="text-primary fw-medium">${orden || '-'}</span></td>
                        <td><small>${maquina || '-'}</small></td>
                        <td>
                            ${formatearDetalle(r.Detalle)}
                            ${r.Tipo === 'PULIDO' && (r.HORA_INICIO || r.HORA_FIN) && formatHorario(r.HORA_INICIO) ? `<div class="mt-1"><span class="horario-movimiento"><i class="far fa-clock me-1"></i>${formatHorario(r.HORA_INICIO)} - ${formatHorario(r.HORA_FIN) || '?'}</span></div>` : ''}
                            ${r.Tipo === 'INYECCION' && r.tiempo_total_minutos != null ? `<div class="mt-1"><span class="badge bg-light text-dark border me-1" title="Tiempo total min"><i class="fas fa-stopwatch me-1 text-primary"></i>${formatMetricaNum(r.tiempo_total_minutos, 2)} min</span><span class="badge bg-light text-dark border" title="Peso Bujes"><i class="fas fa-weight-hanging me-1 text-secondary"></i>${formatMetricaNum(r.peso_bujes, 4)}g</span></div>` : ''}
                        </td>

                        <td class="text-center fw-bold">${cantidad ?? '-'}</td>
                        ${puedeEditar ?
                        `<td class="text-center">
                                ${r.Tipo === 'INYECCION' && r.Estado === 'PENDIENTE' ? `
                                <button class="btn btn-sm btn-link text-success p-0 me-2" onclick="if(window.ModuloInyeccion) window.ModuloInyeccion.validarRegistro('${r.id}')" title="Validar Lote">
                                    <i class="fas fa-check-double"></i>
                                </button>` : ''}
                                <button class="btn btn-sm btn-link text-primary p-0" onclick="window.ModuloHistorial.editarRegistro(${h_datos.indexOf(r)})">
                                    <i class="fas fa-pencil-alt"></i>
                                </button>
                            </td>` : ''}
                    </tr>
                `;
            });

            html += `</tbody></table></div>`;
        }

        // Controles de Paginación
        if (totalPaginas > 1) {
            const h_registrosPorPagina = getHRegistrosPorPagina();
            const inicio_real = (h_paginaActual - 1) * h_registrosPorPagina + 1;
            const fin_real = Math.min(h_paginaActual * h_registrosPorPagina, h_datos.length);

            html += `
                <div class="pagination-container d-flex justify-content-between align-items-center p-3 bg-light border-top">
                    <div class="pagination-info text-muted small">
                        Mostrando ${inicio_real} a ${fin_real} de ${h_datos.length} registros
                    </div>
                    <nav>
                        <ul class="pagination-buttons pagination pagination-sm mb-0" style="display: flex; gap: 5px; list-style: none; padding: 0;">
                            <li class="page-item ${h_paginaActual === 1 ? 'disabled' : ''}">
                                <button class="pagination-btn page-link" onclick="window.ModuloHistorial.cambiarPagina(${h_paginaActual - 1})" ${h_paginaActual === 1 ? 'disabled' : ''}>
                                    <i class="fas fa-chevron-left"></i> <span class="btn-text">Anterior</span>
                                </button>
                            </li>
                            <li class="page-item disabled">
                                <span class="page-link text-dark">Página ${h_paginaActual} de ${totalPaginas}</span>
                            </li>
                            <li class="page-item ${h_paginaActual === totalPaginas ? 'disabled' : ''}">
                                <button class="pagination-btn page-link" onclick="window.ModuloHistorial.cambiarPagina(${h_paginaActual + 1})" ${h_paginaActual === totalPaginas ? 'disabled' : ''}>
                                    <span class="btn-text">Siguiente</span> <i class="fas fa-chevron-right"></i>
                                </button>
                            </li>
                        </ul>
                    </nav>
                </div>
            `;
        }

        container.innerHTML = html;
    }

    /**
     * Cambiar de página
     */
    function cambiarPagina(nuevaPagina) {
        const h_registrosPorPagina = getHRegistrosPorPagina();
        const totalPaginas = Math.ceil(h_datos.length / h_registrosPorPagina);
        if (nuevaPagina < 1 || nuevaPagina > totalPaginas) return;
        h_paginaActual = nuevaPagina;
        renderizarTablaHistorial();
        const container = document.getElementById('historial-container');
        if (container) container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    /**
     * Obtener clase de badge según tipo
     */
    function obtenerBadgeClass(tipo) {
        switch (tipo) {
            case 'INYECCION': return 'bg-primary';
            case 'PULIDO': return 'bg-success';
            case 'ENSAMBLE': return 'badge-ensamble'; // Indigo 600 con contraste garantizado
            case 'VENTA': return 'bg-warning text-dark fw-bold';
            case 'PNC': return 'bg-danger';
            case 'METALS': return 'bg-dark';
            default: return 'bg-secondary';
        }
    }

    /**
     * Inicializar módulo
     */
    function initHistorial() {
        console.log('🔧 Inicializando módulo de historial...');
        try {
            const hInput = document.getElementById('fechaHasta');
            const dInput = document.getElementById('fechaDesde');
            if (hInput && !hInput.value) hInput.value = new Date().toISOString().split('T')[0];
            if (dInput && !dInput.value) {
                const d = new Date(); d.setDate(d.getDate() - 7);
                dInput.value = d.toISOString().split('T')[0];
            }
            
            // Vincular botón de exportación
            const btnExport = document.getElementById('btn-exportar-historial');
            if (btnExport) {
                btnExport.onclick = exportarHistorialExcel;
            }

            // BUGFIX 2026-09-22: antes cada input (fecha/proceso/operario/codigo)
            // traía su propio listener que disparaba cargarHistorial() solo con
            // cambiar la fecha o teclear -- el usuario pedía que la consulta
            // SOLO se dispare al hacer clic en "Filtrar" (el botón ya llama a
            // cargarHistorial() directo vía onclick en el HTML). Tampoco se
            // precarga al abrir la página: el placeholder de historial-container
            // ya invita a elegir el rango y filtrar.

            // Re-renderizar al cambiar tamaño de ventana (Debounce simple)
            let resizeTimer;
            window.addEventListener('resize', () => {
                clearTimeout(resizeTimer);
                resizeTimer = setTimeout(() => {
                    renderizarTablaHistorial();
                }, 200);
            });

        } catch (e) {
            console.error('❌ Error en initHistorial:', e);
        }
    }

    // Registro Global indispensable para el onclick en HTML
    /**
     * Abrir modal de edición (Carga asíncrona de datos de base de datos)
     */
    async function editarRegistro(index) {
        const r = h_datos[index];
        if (!r || !r.hoja || !r.fila) {
            mostrarNotificacion('No se puede editar este registro (Faltan metadatos)', 'error');
            return;
        }

        const modal = document.getElementById('modalEditarHistorial');
        const container = document.getElementById('campos-edicion-dinamicos');
        document.getElementById('edit-hoja').value = r.hoja;
        document.getElementById('edit-fila').value = r.fila;

        // Mostrar spinner de carga
        container.innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border text-primary" role="status" style="width: 3rem; height: 3rem;">
                    <span class="visually-hidden">Cargando...</span>
                </div>
                <p class="mt-3 text-muted fw-medium">Cargando datos completos del registro...</p>
            </div>
        `;
        modal.style.display = 'flex';

        try {
            const res = await fetch(`/api/historial/detalle?hoja=${encodeURIComponent(r.hoja)}&fila=${encodeURIComponent(r.fila)}`);
            const data = await res.json();
            
            if (!data.success) {
                mostrarNotificacion(data.error || 'Error al obtener detalles del registro', 'error');
                modal.style.display = 'none';
                return;
            }

            const det = data.data;
            let html = '';

            // Helpers de formato seguros
            const formatVal = (val) => val !== null && val !== undefined ? val : '';
            const formatDateVal = (val) => val ? val.split('T')[0] : '';
            const formatTimeVal = (val) => {
                if (!val) return '';
                if (val.includes('T')) {
                    const timePart = val.split('T')[1];
                    return timePart.substring(0, 5); // HH:MM
                }
                return val.substring(0, 5);
            };

            if (r.hoja === 'db_inyeccion') {
                html += `
                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-info-circle me-2"></i> Información General</h6>
                        <div class="row g-3">
                            <div class="col-md-6">${crearCampoEdicion('Responsable', formatVal(det.responsable), 'text', 'RESPONSABLE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Departamento', formatVal(det.departamento), 'text', 'DEPARTAMENTO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Máquina', formatVal(det.maquina), 'text', 'MAQUINA')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Orden Producción', formatVal(det.orden_produccion), 'text', 'ORDEN PRODUCCION')}</div>
                            <div class="col-md-6">${crearCampoEdicion('ID Código', formatVal(det.id_codigo), 'text', 'ID CODIGO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Cod. Ensamble', formatVal(det.codigo_ensamble), 'text', 'CODIGO ENSAMBLE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Estado', formatVal(det.estado), 'text', 'ESTADO')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-clock me-2"></i> Tiempos y Fechas</h6>
                        <div class="row g-3">
                            <div class="col-md-12">${crearCampoEdicion('Fecha Inicia', formatDateVal(det.fecha_inicia), 'date', 'FECHA INICIA')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Hora Llegada', formatTimeVal(det.hora_llegada), 'time', 'HORA LLEGADA')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Hora Inicio', formatTimeVal(det.hora_inicio), 'time', 'HORA INICIO')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Hora Termina', formatTimeVal(det.hora_termina), 'time', 'HORA TERMINA')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-microchip me-2"></i> Producción y Contadores</h6>
                        <div class="row g-3">
                            <div class="col-md-4">${crearCampoEdicion('No. Cavidades', formatVal(det.cavidades), 'number', 'No. CAVIDADES')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Contador Maq.', formatVal(det.cant_contador), 'number', 'CONTADOR MAQ.')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Cant. Contador', formatVal(det.cant_contador), 'number', 'CANT. CONTADOR')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Tomados Proceso', formatVal(det.tomados_proceso), 'number', 'TOMADOS EN PROCESO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Cantidad REAL', formatVal(det.cantidad_real), 'number', 'CANTIDAD REAL')}</div>
                            <div class="col-md-12">${crearCampoEdicion('Almacén Destino', formatVal(det.almacen_destino), 'text', 'ALMACEN DESTINO')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-weight-hanging me-2"></i> Pesos (g)</h6>
                        <div class="row g-3">
                            <div class="col-md-6">${crearCampoEdicion('Peso Tomadas (g)', formatVal(det.peso_tomados_proceso), 'number', 'PESO TOMADAS EN PROCESO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Peso Vela (g)', formatVal(det.peso_vela), 'number', 'PESO VELA MAQUINA')}</div>
                            <div class="col-md-12">${crearCampoEdicion('Peso Bujes (g)', formatVal(det.peso_bujes), 'number', 'PESO BUJES')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-3">
                        <h6 class="section-title"><i class="fas fa-comment-alt me-2"></i> Observaciones del Registro</h6>
                        <textarea class="form-control edit-input" data-col="OBSERVACIONES" aria-label="Observaciones del registro" rows="2"
                            style="border-radius: 10px; border: 1px solid #e2e8f0; padding: 10px; font-size: 0.9rem;">${formatVal(det.observaciones)}</textarea>
                    </div>
                `;
            } else if (r.hoja === 'db_pulido') {
                html += `
                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-id-badge me-2"></i> Datos de Pulido</h6>
                        <div class="row g-3">
                            <div class="col-md-6">${crearCampoEdicion('Responsable', formatVal(det.responsable), 'text', 'RESPONSABLE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Producto/Código', formatVal(det.codigo), 'text', 'ID CODIGO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Orden Producción', formatVal(det.orden_produccion), 'text', 'ORDEN PRODUCCION')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Lote', formatVal(det.lote), 'text', 'LOTE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Estado', formatVal(det.estado), 'text', 'ESTADO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Almacén Destino', formatVal(det.almacen_destino), 'text', 'ALMACEN DESTINO')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-clock me-2"></i> Tiempos y Fechas</h6>
                        <div class="row g-3">
                            <div class="col-md-4">${crearCampoEdicion('Fecha', formatDateVal(det.fecha), 'date', 'FECHA')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Hora Inicio', formatTimeVal(det.hora_inicio), 'time', 'HORA INICIO')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Hora Fin', formatTimeVal(det.hora_fin), 'time', 'HORA FIN')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-calculator me-2"></i> Cantidades y Calidad</h6>
                        <div class="row g-3">
                            <div class="col-md-4">${crearCampoEdicion('Recibidos', formatVal(det.cantidad_recibida), 'number', 'CANTIDAD RECIBIDA')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Buenos', formatVal(det.cantidad_real), 'number', 'BUJES BUENOS')}</div>
                            <div class="col-md-4">${crearCampoEdicion('PNC Pulido', formatVal(det.pnc_pulido), 'number', 'PNC')}</div>
                            <div class="col-md-12">${crearCampoEdicion('PNC Inyección', formatVal(det.pnc_inyeccion), 'number', 'PNC_INYECCION')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-3">
                        <h6 class="section-title"><i class="fas fa-comment-alt me-2"></i> Observaciones del Registro</h6>
                        <textarea class="form-control edit-input" data-col="OBSERVACIONES" aria-label="Observaciones del registro" rows="2"
                            style="border-radius: 10px; border: 1px solid #e2e8f0; padding: 10px; font-size: 0.9rem;">${formatVal(det.observaciones)}</textarea>
                    </div>
                `;
            } else if (r.hoja === 'db_ensambles') {
                html += `
                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-puzzle-piece me-2"></i> Datos de Ensamble</h6>
                        <div class="row g-3">
                            <div class="col-md-6">${crearCampoEdicion('Responsable', formatVal(det.responsable), 'text', 'RESPONSABLE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('ID Código Final', formatVal(det.id_codigo), 'text', 'ID CODIGO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Buje Ensamble', formatVal(det.buje_ensamble), 'text', 'BUJE ENSAMBLE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('OP Número', formatVal(det.op_numero), 'text', 'OP NUMERO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('ID Ensamble', formatVal(det.id_ensamble), 'text', 'ID ENSAMBLE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Estado', formatVal(det.estado), 'text', 'ESTADO')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-clock me-2"></i> Tiempos y Fechas</h6>
                        <div class="row g-3">
                            <div class="col-md-4">${crearCampoEdicion('Fecha', formatDateVal(det.fecha), 'date', 'FECHA')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Hora Inicio', formatTimeVal(det.hora_inicio), 'time', 'HORA INICIO')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Hora Fin', formatTimeVal(det.hora_fin), 'time', 'HORA FIN')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-warehouse me-2"></i> Cantidades y Almacenes</h6>
                        <div class="row g-3">
                            <div class="col-md-4">${crearCampoEdicion('Cantidad', formatVal(det.cantidad), 'number', 'CANTIDAD')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Qty Unitaria', formatVal(det.qty), 'number', 'QTY (Unitaria)')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Consumo Total', formatVal(det.consumo_total), 'number', 'CONSUMO_TOTAL')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Almacén Origen', formatVal(det.almacen_para_descargar), 'text', 'ALMACEN ORIGEN')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Almacén Destino', formatVal(det.almacen_destino), 'text', 'ALMACEN DESTINO')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-3">
                        <h6 class="section-title"><i class="fas fa-comment-alt me-2"></i> Observaciones del Registro</h6>
                        <textarea class="form-control edit-input" data-col="OBSERVACIONES" aria-label="Observaciones del registro" rows="2"
                            style="border-radius: 10px; border: 1px solid #e2e8f0; padding: 10px; font-size: 0.9rem;">${formatVal(det.observaciones)}</textarea>
                    </div>
                `;
            } else if (r.hoja === 'db_mezcla') {
                html += `
                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-blender me-2"></i> Datos de Mezcla</h6>
                        <div class="row g-3">
                            <div class="col-md-6">${crearCampoEdicion('Responsable', formatVal(det.responsable), 'text', 'RESPONSABLE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Máquina', formatVal(det.maquina), 'text', 'MAQUINA')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Lote Interno', formatVal(det.lote_interno), 'text', 'LOTE_INTERNO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Fecha', formatDateVal(det.fecha), 'date', 'FECHA')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Hora', formatTimeVal(det.hora), 'time', 'HORA')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-weight me-2"></i> Materiales (Kg)</h6>
                        <div class="row g-3">
                            <div class="col-md-4">${crearCampoEdicion('Virgen (Kg)', formatVal(det.virgen_kg), 'number', 'VIRGEN (Kg)')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Molido (Kg)', formatVal(det.molido_kg), 'number', 'MOLIDO (Kg)')}</div>
                            <div class="col-md-4">${crearCampoEdicion('Pigmento (Kg)', formatVal(det.pigmento_kg), 'number', 'PIGMENTO (Kg)')}</div>
                        </div>
                    </div>

                    <div class="edit-section mb-3">
                        <h6 class="section-title"><i class="fas fa-comment-alt me-2"></i> Observaciones del Registro</h6>
                        <textarea class="form-control edit-input" data-col="OBSERVACIONES" aria-label="Observaciones del registro" rows="2"
                            style="border-radius: 10px; border: 1px solid #e2e8f0; padding: 10px; font-size: 0.9rem;">${formatVal(det.observaciones)}</textarea>
                    </div>
                `;
            } else if (r.hoja === 'db_ventas') {
                html += `
                    <div class="edit-section mb-4">
                        <h6 class="section-title"><i class="fas fa-shopping-cart me-2"></i> Datos de Ventas</h6>
                        <div class="row g-3">
                            <div class="col-md-6">${crearCampoEdicion('Cliente', formatVal(det.nombres), 'text', 'CLIENTE')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Documento/Orden', formatVal(det.documento), 'text', 'DOCUMENTO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Producto', formatVal(det.productos), 'text', 'PRODUCTO')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Cantidad', formatVal(det.cantidad), 'number', 'CANTIDAD')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Fecha', formatDateVal(det.fecha), 'date', 'FECHA')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Clasificación', formatVal(det.clasificacion), 'text', 'CLASIFICACION')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Total Ingresos', formatVal(det.total_ingresos), 'number', 'TOTAL_INGRESOS')}</div>
                            <div class="col-md-6">${crearCampoEdicion('Precio Promedio', formatVal(det.precio_promedio), 'number', 'PRECIO_PROMEDIO')}</div>
                        </div>
                    </div>
                `;
            }

            // Siempre permitir editar observaciones (motivo)
            html += `
                <div class="edit-section mb-3">
                    <h6 class="section-title text-primary"><i class="fas fa-comment-dots me-2"></i> Motivo de la Corrección</h6>
                    <textarea class="form-control" id="edit-motivo" rows="3" aria-label="Motivo de la Corrección"
                        placeholder="Describa brevemente por qué realiza este cambio..."
                        style="border-radius: 12px; border: 1px solid #cbd5e1; padding: 12px; font-size: 0.95rem;"></textarea>
                </div>`;

            container.innerHTML = html;
        } catch (error) {
            console.error('❌ Error cargando detalles del registro:', error);
            mostrarNotificacion('Error al obtener los detalles del registro', 'error');
            modal.style.display = 'none';
        }
    }

    function crearCampoEdicion(label, valor, tipo, colName) {
        const inputId = 'edit-campo-' + colName.replace(/[^a-zA-Z0-9]+/g, '-').toLowerCase();
        return `
            <div class="form-group mb-1">
                <label for="${inputId}" class="small fw-bold text-secondary text-uppercase mb-1 d-block" style="font-size: 0.7rem; letter-spacing: 0.5px;">${label}</label>
                <input type="${tipo}" id="${inputId}" class="form-control edit-input" data-col="${colName}" value="${valor || ''}"
                    style="border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px; font-size: 0.95rem; background: #ffffff; transition: all 0.2s ease;">
            </div>
        `;
    }

    /**
     * Guardar cambios en el backend
     */
    async function guardarCambios() {
        const hoja = document.getElementById('edit-hoja').value;
        const fila = document.getElementById('edit-fila').value;
        const motivo = document.getElementById('edit-motivo').value;

        const inputs = document.querySelectorAll('.edit-input');
        const datos = {};
        inputs.forEach(input => {
            datos[input.dataset.col] = input.value;
        });

        const horaInicio = datos['HORA INICIO'];
        const horaFin = datos['HORA FIN'] || datos['HORA TERMINA'];
        if (horaInicio && horaFin && horaFin <= horaInicio) {
            mostrarNotificacion('La Hora Fin/Termina debe ser estrictamente posterior a la Hora Inicio', 'error');
            return;
        }

        // Añadir motivo a las observaciones si se proporcionó (Concatenar si ya existe en datos)
        if (motivo) {
            if (datos['OBSERVACIONES']) {
                datos['OBSERVACIONES'] = datos['OBSERVACIONES'] + " | Motivo: " + motivo;
            } else {
                datos['OBSERVACIONES'] = motivo;
            }
        }

        try {
            mostrarLoading(true);
            const res = await fetch('/api/historial/actualizar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    hoja: hoja,
                    fila: fila,
                    datos: datos,
                    usuario: window.AppState.user.nombre
                })
            });

            const data = await res.json();
            if (data.success) {
                mostrarNotificacion('Registro corregido correctamente', 'success');
                cerrarModalEdicion();
                cargarHistorial(); // Refrescar lista
            } else {
                mostrarNotificacion(data.error || 'Error al actualizar', 'error');
            }
        } catch (error) {
            console.error('Error guardando edicion:', error);
            mostrarNotificacion('Error de conexión', 'error');
        } finally {
            mostrarLoading(false);
        }
    }

    function cerrarModalEdicion() {
        document.getElementById('modalEditarHistorial').style.display = 'none';
    }

    /**
     * Exportar los datos actuales a Excel (vía Backend)
     */
    let exportandoHistorial = false;

    function exportarHistorialExcel() {
        if (!h_datos || h_datos.length === 0) {
            mostrarNotificacion('No hay datos para exportar', 'warning');
            return;
        }

        // Si estamos en vista PULIDO, usar el endpoint específico de Pulido
        // (sigue siendo descarga síncrona -- fuera de alcance de este cambio)
        const proceso = document.getElementById('tipoProceso')?.value || '';
        if (proceso === 'PULIDO') {
            return descargarExcelPulido();
        }

        if (exportandoHistorial) {
            mostrarNotificacion('Ya hay una exportación en curso, espera a que termine.', 'warning');
            return;
        }

        const desde = document.getElementById('fechaDesde')?.value || '';
        const hasta = document.getElementById('fechaHasta')?.value || '';
        const tipo_filtro = proceso;

        const url = `/api/exportar-historial-global?desde=${desde}&hasta=${hasta}&tipo=${tipo_filtro}`;
        console.log('📊 [Excel Historial] Solicitando generación en background:', url);
        iniciarExportacionAsincrona(url);
    }

    /**
     * Dispara una exportación en background (task_id + polling) y descarga el
     * archivo cuando el backend termina de generarlo.
     *
     * Reemplaza la descarga directa por window.location.href: armar el Excel
     * completo (consulta + Workbook) bloqueaba el único worker gunicorn de la
     * app (ver gunicorn.conf.py) hasta terminar, tumbando el resto de
     * requests concurrentes en rangos de fecha grandes.
     */
    async function iniciarExportacionAsincrona(url) {
        exportandoHistorial = true;
        try {
            const res = await fetchData(url);
            if (!res) return; // fetchData ya notificó el error de red/HTTP

            if (!res.success || !res.data?.task_id) {
                mostrarNotificacion(res.error || 'No se pudo iniciar la exportación', 'error');
                return;
            }

            mostrarNotificacion('Generando Excel del Historial... esto puede tardar unos segundos.', 'info');
            await sondearEstadoExportacion(res.data.task_id);
        } catch (error) {
            console.error('❌ Error en exportación asíncrona del Historial:', error);
            mostrarNotificacion('Error de conexión al exportar', 'error');
        } finally {
            exportandoHistorial = false;
        }
    }

    const EXPORT_POLL_MS = 2000;
    const EXPORT_MAX_INTENTOS = 150; // ~5 min de margen

    async function sondearEstadoExportacion(taskId, intentos = 0) {
        if (intentos >= EXPORT_MAX_INTENTOS) {
            mostrarNotificacion('La exportación está tardando demasiado. Intenta de nuevo más tarde.', 'error');
            return;
        }

        const estado = await fetchData(`/api/tasks/status/${taskId}`);
        if (!estado) return; // fetchData ya notificó el error
        if (!estado.success) {
            mostrarNotificacion(estado.error || 'Error consultando el estado de la exportación', 'error');
            return;
        }

        const { status, download_url, error } = estado.data;
        if (status === 'COMPLETED') {
            mostrarNotificacion('¡Excel listo! Descargando...', 'success');
            window.location.href = download_url;
            return;
        }
        if (status === 'FAILED') {
            mostrarNotificacion(error || 'Error generando el Excel', 'error');
            return;
        }

        // PENDING/RUNNING: seguir esperando
        await new Promise(resolve => setTimeout(resolve, EXPORT_POLL_MS));
        await sondearEstadoExportacion(taskId, intentos + 1);
    }

    /**
     * Descarga Excel profesional de Pulido desde el backend (openpyxl).
     * Reusa el mismo flujo task_id + polling que exportarHistorialExcel --
     * ambos comparten el guard exportandoHistorial a propósito, para no
     * disparar dos exportaciones pesadas en paralelo desde la misma pestaña.
     */
    function descargarExcelPulido() {
        if (exportandoHistorial) {
            mostrarNotificacion('Ya hay una exportación en curso, espera a que termine.', 'warning');
            return;
        }

        const desde = document.getElementById('fechaDesde')?.value || '';
        const hasta = document.getElementById('fechaHasta')?.value || '';
        const operario = document.getElementById('filtroOperario')?.value || '';
        const codigo = document.getElementById('filtroCodigo')?.value || '';

        const url = `/api/pulido/exportar_excel?fecha_inicio=${desde}&fecha_fin=${hasta}&operario=${encodeURIComponent(operario)}&id_codigo=${encodeURIComponent(codigo)}`;
        console.log('📊 [Excel Pulido] Solicitando generación en background:', url);
        iniciarExportacionAsincrona(url);
    }

    let swalTimelineState = {
        page: 1, limit: 100, codigo: null, isLoading: false, hasMore: true,
        gruposInfo: {
            'INYECCION': { icon: '🏭', title: 'Inyección', bg: 'primary', color: '#0d6efd', border: 'border-primary' },
            'PULIDO': { icon: '✨', title: 'Pulido', bg: 'success', color: '#198754', border: 'border-success' },
            'ENSAMBLE': { icon: '🛠️', title: 'Ensamble', bg: 'indigo', color: '#4f46e5', border: 'border-indigo-subtle', textClass: 'text-indigo' },
            'COMERCIAL': { icon: '📦', title: 'Comercial (Pedidos/Ventas)', bg: 'dark', color: '#343a40', border: 'border-dark' }
        }
    };

    function renderAccordionShell(kpis) {
        const container = document.getElementById('accordionTimeline');
        if (!container) return;
        
        let html = '';
        const totales = Object.values(kpis).reduce((a, b) => a + b, 0);
        if (totales === 0) {
            container.innerHTML = `<div class="text-center text-muted py-5"><i class="fas fa-archive mb-2 fs-2 text-black-50"></i><br>Sin registros productivos o comerciales</div>`;
            return;
        }

        Object.keys(swalTimelineState.gruposInfo).forEach(key => {
            const kpiSum = parseInt(kpis[key] || 0);
            if (kpiSum === 0) return;

            const grupo = swalTimelineState.gruposInfo[key];
            const textClass = grupo.textClass || `text-${grupo.bg}`;
            const opacityClass = grupo.bgSolid ? 'bg-opacity-100' : 'bg-opacity-10';
            const borderClass = grupo.bgSolid ? 'border-0' : `border-${grupo.bg}-subtle`;
            const customStyles = grupo.bgSolid ? `background-color: ${grupo.color} !important; color: white !important;` : '';
            
            html += `
            <div class="accordion-item border-0 mb-3 bg-transparent swal-accordion-group">
                <h2 class="accordion-header" id="heading-${key}">
                    <button class="accordion-button collapsed rounded fw-bold ${textClass} bg-${grupo.bg} ${opacityClass} border ${borderClass} shadow-sm" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-${key}" style="padding: 12px 20px; ${customStyles}">
                        <div class="d-flex justify-content-between align-items-center w-100 me-3">
                            <span><span class="me-2 fs-5" style="${grupo.bgSolid ? 'color: white !important;' : ''}">${grupo.icon}</span> <span style="letter-spacing: 0.5px;">${grupo.title}</span></span>
                            <span class="badge ${grupo.bgSolid ? 'bg-white text-dark' : 'bg-' + grupo.bg} rounded-pill fs-6 shadow-sm"><i class="fas fa-chart-line me-1"></i> ${kpiSum.toLocaleString()} uds</span>
                        </div>
                    </button>
                </h2>
                <div id="collapse-${key}" class="accordion-collapse collapse">
                    <div class="accordion-body px-1 py-3 pt-4" id="body-${key}">
                    </div>
                </div>
            </div>
            `;
        });
        
        container.innerHTML = html;
    }

    function appendTimelineItems(movimientos) {
        movimientos.forEach((m) => {
            let key = m.tipo;
            if (m.tipo === 'PEDIDO' || m.tipo === 'VENTA') key = 'COMERCIAL';
            
            const bodyParent = document.getElementById(`body-${key}`);
            if (!bodyParent) return; 
            
            const grupo = swalTimelineState.gruposInfo[key];
            let innerEmoji = '📌';
            if (m.tipo === 'PEDIDO') innerEmoji = '🛒';
            else if (m.tipo === 'VENTA') innerEmoji = '📦';
            else innerEmoji = grupo.icon;

            // Debug para verificar si llegan las horas
            if (m.tipo === 'PULIDO') {
                console.log("DEBUG PULIDO ITEM:", {
                    fecha: m.fecha,
                    inicio: m.hora_inicio,
                    fin: m.hora_fin,
                    formatted: formatHorario(m.hora_inicio)
                });
            }

            const searchStr = `${m.tipo} ${m.fecha} ${m.cant || 0} ${m.responsable} ${m.detalle}`.toLowerCase();
            
            let detalleFormatted = m.detalle || '';
            detalleFormatted = detalleFormatted.replace(/Estado:\s*([A-Za-z0-9_]+)/gi, (match, p1) => {
                let badgeColor = 'bg-secondary';
                const p1Upper = p1.toUpperCase();
                if (['PENDIENTE', 'ABIERTO'].includes(p1Upper)) badgeColor = 'bg-warning text-dark';
                else if (['EXPORTADO_WO', 'CERRADO'].includes(p1Upper)) badgeColor = 'bg-success';
                else if (['ALISTADO', 'ALISTAMIENTO'].includes(p1Upper)) badgeColor = 'bg-info text-dark';
                else if (p1Upper === 'CANCELADO') badgeColor = 'bg-danger';
                return `Estado: <span class="badge ${badgeColor}" style="font-size: 0.7rem; padding: 0.25em 0.6em; vertical-align: baseline;">${p1Upper}</span>`;
            });
            detalleFormatted = detalleFormatted.replace(/ \| /g, ' <span class="text-muted mx-1">|</span> ');
            
            const itemHtml = `
                <div class="timeline-item d-flex mb-3 align-items-start swal-tl-item bg-${grupo.bg} bg-opacity-10 border border-2 border-top-0 border-bottom-0 border-end-0 border-${grupo.bg}-subtle p-3 rounded" data-search="${searchStr}" style="margin-left:10px;">
                    <div class="timeline-icon me-3 mt-1" style="font-size: 1.3rem; min-width: 30px; text-align: center;">
                        ${innerEmoji}
                    </div>
                    <div class="timeline-content pb-1 flex-grow-1">
                        <div class="d-flex justify-content-between align-items-center">
                            <strong style="color: ${grupo.color}; font-size: 0.9rem;">${m.tipo}</strong>
                            <span class="text-muted fw-semibold" style="font-size: 0.75rem;"><i class="far fa-calendar-alt me-1"></i>${m.fecha}</span>
                        </div>
                        <div class="mt-2 d-flex align-items-center">
                            <span class="badge rounded-pill shadow-sm text-white" style="background-color: ${grupo.color}; font-size: 0.75rem;">${m.cant || 0} uds</span>
                            <span class="ms-2 fw-bold text-dark text-truncate d-inline-block" style="max-width: 200px; font-size: 0.85rem;">${m.responsable || '-'}</span>
                        </div>
                        <div class="small text-secondary mt-2 lh-sm" style="font-size: 0.85rem;">${detalleFormatted}</div>
                    </div>
                </div>
            `;
            bodyParent.insertAdjacentHTML('beforeend', itemHtml);
        });
    }

    async function loadTimelinePage(isFirstLoad = false) {
        if (swalTimelineState.isLoading || (!swalTimelineState.hasMore && !isFirstLoad)) return;
        swalTimelineState.isLoading = true;
        
        const container = document.getElementById('swal-timeline-list');
        const spinnerId = 'swal-timeline-spinner';
        
        if (container && !document.getElementById(spinnerId) && !isFirstLoad) {
            container.insertAdjacentHTML('beforeend', `<div id="${spinnerId}" class="text-center py-3"><div class="spinner-border text-primary spinner-border-sm" role="status"></div></div>`);
        }

        try {
            const res = await fetch(`/api/productos/historial/${encodeURIComponent(swalTimelineState.codigo)}?page=${swalTimelineState.page}&limit=${swalTimelineState.limit}`);
            if (res.ok) {
                const data = await res.json();
                if (data.status === 'success') {
                    swalTimelineState.hasMore = data.has_more;
                    
                    if (isFirstLoad) {
                        renderAccordionShell(data.kpis || {});
                    }
                    
                    appendTimelineItems(data.resultados || []);
                    swalTimelineState.page++;

                    // Disparar redibujado de filtros si hay caja de búsqueda sucia
                    const searchInput = document.getElementById('swal-timeline-search');
                    if (searchInput && searchInput.value.trim().length > 0) {
                        searchInput.dispatchEvent(new Event('input'));
                    }
                }
            }
        } catch (e) {
            console.error('Error paginando trazabilidad:', e);
        } finally {
            if (document.getElementById(spinnerId)) document.getElementById(spinnerId).remove();
            swalTimelineState.isLoading = false;
        }
    }

    /**
     * Vista Rápida del Producto (Quick View Modal) con PANDAS LATENCY CERO
     */
    async function irAProducto(codigo) {
        if (!codigo) return;
        
        swalTimelineState = {
            page: 1, limit: 500, codigo: codigo, isLoading: false, hasMore: true,
            gruposInfo: swalTimelineState.gruposInfo
        };
        
        const customStyle = `
            <style>
                .timeline-container::-webkit-scrollbar { width: 6px; }
                .timeline-container::-webkit-scrollbar-track { background: #f8f9fa; border-radius: 4px; }
                .timeline-container::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
                .timeline-container::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
            </style>
        `;
        
        const skeletonHeader = `
            <div id="swal-header-contents">
                <div class="d-flex align-items-center mb-4 p-3 bg-light rounded-4 shadow-sm" style="border-left: 4px solid #0d6efd;">
                    <div class="placeholder-glow me-3"><div class="placeholder bg-secondary rounded" style="width: 70px; height: 70px;"></div></div>
                    <div style="text-align: left; width: 100%;">
                        <h4 class="mb-1 placeholder-glow"><span class="placeholder col-6"></span></h4>
                        <div class="small mb-2 placeholder-glow"><span class="placeholder col-8"></span></div>
                        <div class="d-flex gap-2 mt-1">
                            <span class="placeholder-glow"><span class="placeholder col-4" style="border-radius: 6px; width: 100px; height: 25px;"></span></span>
                            <span class="placeholder-glow"><span class="placeholder col-4" style="border-radius: 6px; width: 100px; height: 25px;"></span></span>
                        </div>
                    </div>
                </div>
            </div>
            <div class="input-group mb-3 pb-2 border-bottom">
                <span class="input-group-text bg-light border-end-0 text-muted"><i class="fas fa-search"></i></span>
                <input type="text" id="swal-timeline-search" aria-label="Filtrar por operario, proceso, orden" class="form-control border-start-0 bg-light" placeholder="Filtrar por operario, proceso, orden..." style="font-size: 0.85rem; box-shadow: none;" disabled>
            </div>
        `;
        
        const skeletonTimeline = `
            <div id="swal-timeline-list" class="timeline-container px-2 pb-2" style="text-align: left; max-height: 400px; overflow-y: auto; overflow-x: hidden;">
                <div class="accordion" id="accordionTimeline">
                    <div class="accordion-item border-0 mb-3 bg-transparent"><div class="placeholder-glow"><div class="placeholder rounded w-100" style="height: 52px; opacity: 0.3;"></div></div></div>
                    <div class="accordion-item border-0 mb-3 bg-transparent"><div class="placeholder-glow"><div class="placeholder rounded w-100" style="height: 52px; opacity: 0.25;"></div></div></div>
                    <div class="accordion-item border-0 mb-3 bg-transparent"><div class="placeholder-glow"><div class="placeholder rounded w-100" style="height: 52px; opacity: 0.2;"></div></div></div>
                    <div class="accordion-item border-0 mb-3 bg-transparent"><div class="placeholder-glow"><div class="placeholder rounded w-100" style="height: 52px; opacity: 0.15;"></div></div></div>
                </div>
            </div>
        `;

        Swal.fire({
            title: false,
            html: customStyle + skeletonHeader + skeletonTimeline,
            width: 650,
            padding: '1.5rem',
            showCloseButton: true,
            showConfirmButton: false,
            customClass: { popup: 'rounded-4 shadow-lg' },
            didOpen: async () => {
                try {
                    // Peticiones al backend simultaneas tras abrir
                    const resDetalleP = fetch(`/api/productos/detalle/${encodeURIComponent(codigo)}`);
                    const resDetalle = await resDetalleP;
                    
                    let prodDetalle = null;
                    if (resDetalle && resDetalle.ok) {
                        const dataDetalle = await resDetalle.json();
                        if (dataDetalle.status === 'success') {
                            prodDetalle = dataDetalle.producto;
                        }
                    }
                    
                    const desc = prodDetalle ? (prodDetalle.descripcion_larga || prodDetalle.descripcion || 'Sin descripción') : 'Sin descripción';
                    let srcValida = prodDetalle && prodDetalle.imagen_valida ? prodDetalle.imagen_valida : '/static/img/no-image.svg';
                    if (srcValida === '') srcValida = '/static/img/no-image.svg';
                    
                    const stockDisp = prodDetalle ? (prodDetalle.stock_disponible || 0) : '0';
                    const stockTerm = prodDetalle ? (prodDetalle.stock_terminado || 0) : '0';
                    
                    const headerContainer = document.getElementById('swal-header-contents');
                    if(headerContainer) {
                        headerContainer.innerHTML = `
                            <div class="d-flex align-items-center mb-4 p-3 bg-light rounded-4 shadow-sm" style="border-left: 4px solid #0d6efd;">
                                <img src="${srcValida}" style="width: 70px; height: 70px; object-fit: cover; border-radius: 8px; border: 1px solid #dee2e6; background: white;" class="me-3 shadow-sm" onerror="this.onerror=null; this.src='/static/img/no-image.svg';">
                                <div style="text-align: left;">
                                    <h4 class="mb-1 fw-bold text-dark" style="font-size: 1.1rem; letter-spacing: -0.5px;">${codigo}</h4>
                                    <div class="text-muted small mb-2" style="font-size: 0.85rem; line-height: 1.2;">${desc}</div>
                                    <div class="d-flex gap-2 mt-1">
                                        <span class="badge px-3 py-2 fw-bold shadow-sm" style="background-color: #1cc88a; color: white !important; font-size: 0.8rem; border-radius: 6px;"><i class="fas fa-check-circle me-1"></i> Disponible: <span style="font-size: 0.95rem;">${stockDisp}</span></span>
                                    </div>
                                </div>
                            </div>
                        `;
                    }
                    
                    const searchInput = document.getElementById('swal-timeline-search');
                    if (searchInput) {
                        searchInput.disabled = false;
                        searchInput.focus();
                        searchInput.addEventListener('input', (e) => {
                            const val = e.target.value.toLowerCase().trim();
                            const terms = val.split(' ').filter(t => t.length > 0); 
                            const groups = document.querySelectorAll('.swal-accordion-group');
                            groups.forEach(group => {
                                const items = group.querySelectorAll('.swal-tl-item');
                                let hasVisibleItems = false;
                                items.forEach(item => {
                                    const docStr = item.getAttribute('data-search') || '';
                                    const matches = terms.length === 0 || terms.every(t => docStr.includes(t));
                                    if (matches) {
                                        item.classList.remove('d-none');
                                        item.classList.add('d-flex');
                                        item.style.setProperty('display', 'flex', 'important');
                                        hasVisibleItems = true;
                                    } else {
                                        item.classList.remove('d-flex');
                                        item.classList.add('d-none');
                                        item.style.setProperty('display', 'none', 'important');
                                    }
                                });
                                if (!hasVisibleItems && terms.length > 0) {
                                    group.style.setProperty('display', 'none', 'important');
                                } else {
                                    group.style.setProperty('display', 'block', 'important');
                                    if (terms.length > 0) {
                                        const collapse = group.querySelector('.accordion-collapse');
                                        const btn = group.querySelector('.accordion-button');
                                        if (collapse && !collapse.classList.contains('show')) {
                                            collapse.classList.add('show');
                                            btn.classList.remove('collapsed');
                                        }
                                    }
                                }
                            });
                        });
                    }

                    // Lanzar carga timeline (Sustituirá al Skeleton)
                    await loadTimelinePage(true);

                    // Enganchar Scroll Infinito
                    const container = document.getElementById('swal-timeline-list');
                    if (container) {
                        container.addEventListener('scroll', async () => {
                            if (container.scrollTop + container.clientHeight >= container.scrollHeight - 30) {
                                await loadTimelinePage(false);
                            }
                        });
                    }
                } catch (e) {
                    console.error('Error Quick View Flow:', e);
                }
            }
        });
    }

    window.filtrarHistorial = cargarHistorial;
    window.ModuloHistorial = {
        inicializar: initHistorial,
        filtrar: cargarHistorial,
        // BUGFIX 2026-09-22: el boton "Filtrar" (index.html) y una llamada
        // cruzada desde inyeccion.js ya invocaban .cargarHistorial(), no
        // .filtrar() -- esa clave nunca existio aqui. Antes pasaba
        // desapercibido porque el auto-filtrado en cada cambio de fecha
        // disparaba la busqueda por una referencia directa a la funcion, sin
        // pasar por window.ModuloHistorial. Al quitar el auto-filtrado (ver
        // commit del mismo dia "Historial Global solo filtra al hacer clic
        // en Filtrar"), el boton quedo como el unico camino para buscar, y
        // resulto que nunca habia funcionado.
        cargarHistorial: cargarHistorial,
        cambiarPagina: cambiarPagina,
        editarRegistro: editarRegistro,
        guardarCambios: guardarCambios,
        cerrarModalEdicion: cerrarModalEdicion,
        irAProducto: irAProducto
    };

    console.log('🚀 Módulo Historial registrado y listo');
})();
