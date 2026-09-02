// ============================================
// inventario.js - LÃ³gica de Inventario con PaginaciÃ³n
// ============================================

// Configuración de paginación
const getItemsPerPage = () => window.innerWidth < 992 ? 20 : 50;
let paginaActual = 1;

// Placeholder premium de FriTech (SVG en base64 para evitar peticiones extra)
const PLACEHOLDER_SVG = `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0%25' y1='0%25' x2='100%25' y2='100%25'%3E%3Cstop offset='0%25' style='stop-color:%23f8fafc;stop-opacity:1' /%3E%3Cstop offset='100%25' style='stop-color:%23e2e8f0;stop-opacity:1' /%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='100' height='100' fill='url(%23g)' rx='12'/%3E%3Cg opacity='0.4' transform='translate(0, -5)'%3E%3Cpath d='M30 40c0-2.2 1.8-4 4-4h32c2.2 0 4 1.8 4 4v25c0 2.2-1.8 4-4 4H34c-2.2 0-4-1.8-4-4V40z' fill='%2364748b'/%3E%3Ccircle cx='50' cy='52.5' r='7' fill='%23f1f5f9'/%3E%3Cpath d='M46 32h8l2 4h-12z' fill='%2364748b'/%3E%3C/g%3E%3Ctext x='50' y='82' text-anchor='middle' font-family='sans-serif' font-size='7' fill='%2394a3b8' font-weight='bold'%3EFriTech%3C/text%3E%3C/svg%3E`;

/**
 * Genera el HTML de la imagen con lógica de fallback multinivel (Súper Radar v3.0)
 * Orden: Imagen Pre-validada -> Local Original (.jpg) -> Local Limpio (.jpg) -> Local Limpio (.png) -> no-image.svg
 */
function obtenerHtmlImagen(p, esMovil = false) {
    const codigoOriginal = String(p.codigo || p.id_codigo || '').trim();
    const codigoLimpio = typeof limpiarCodigoJS === 'function' ? limpiarCodigoJS(codigoOriginal) : codigoOriginal;
    
    // Rutas de fallback (evitar .jpg o .png roto si el código está vacío)
    const tieneCodigo = codigoOriginal.length > 0;
    const localImgOriginal = tieneCodigo ? `/static/img/productos/${codigoOriginal}.jpg` : PLACEHOLDER_SVG;
    const localImgLimpio = tieneCodigo ? `/static/img/productos/${codigoLimpio}.jpg` : PLACEHOLDER_SVG;
    const localImgPng = tieneCodigo ? `/static/img/productos/${codigoLimpio}.png` : PLACEHOLDER_SVG;
    const cloudImg = (p.imagen && typeof p.imagen === 'string' && p.imagen.trim() !== '') ? p.imagen : '';
    
    // Si el backend ya validó una ruta, la usamos como punto de partida, si no, empezamos el radar
    const srcInicial = cloudImg || (p.imagen_valida ? p.imagen_valida : (tieneCodigo ? localImgOriginal : PLACEHOLDER_SVG));
    
    // Estilos según vista
    const estilo = esMovil 
        ? 'width: 100%; height: 100%; object-fit: cover;' 
        : 'width: 40px; height: 40px; object-fit: cover; border-radius: 4px; cursor: pointer; background: white; border: 1px solid #eee;';
    
    const extraAttr = esMovil ? 'class="card-img"' : 'onclick="window.open(this.src, \'_blank\')" title="Click para ampliar"';

    return `
        <img src="${srcInicial}" 
             data-limpio-src="${localImgLimpio}"
             data-png-src="${localImgPng}"
             data-cloud-src="${cloudImg}"
             data-placeholder="${PLACEHOLDER_SVG}"
             data-attempt="0"
             style="${estilo}" 
             ${extraAttr} 
             onerror="
                const attempt = parseInt(this.dataset.attempt || '0');
                this.dataset.attempt = (attempt + 1).toString();
                
                if (attempt === 0) {
                    this.src = this.dataset.limpioSrc;
                } else if (attempt === 1) {
                    this.src = this.dataset.pngSrc;
                } else if (attempt === 2 && this.dataset.cloudSrc && this.dataset.cloudSrc.length > 10) {
                    this.src = this.dataset.cloudSrc;
                } else {
                    this.src = this.dataset.placeholder;
                    this.onerror = null;
                }
             ">
    `;
}

/**
 * Cargar productos para inventario
 */
async function cargarProductos(forceRefresh = false) {
    if (!forceRefresh && window.AppState.productosRaw && window.AppState.productosRaw.length > 0) {
        console.log('📦 Reutilizando productos de cache global (Instantáneo)...');
        procesarYRenderizarProductos(window.AppState.productosRaw);
        return;
    }

    try {
        console.log('📦 Cargando productos desde el servidor...');
        mostrarLoading(true);

        const isMetals = window.AppState.user?.division === 'FRIMETALS';
        let url = isMetals ? '/api/metals/productos/listar' : '/api/productos/listar';

        if (forceRefresh && !isMetals) {
            url += '?refresh=true';
        }
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        console.log('Datos recibidos:', data);

        let listaFinal = [];
        if (data.items && Array.isArray(data.items)) {
            listaFinal = data.items;
        } else if (data.productos && Array.isArray(data.productos)) {
            listaFinal = data.productos;
        } else if (Array.isArray(data)) {
            listaFinal = data;
        }

        if (listaFinal.length > 0) {
            // Guardar en cache para futuros usos
            window.AppState.productosRaw = listaFinal;
            procesarYRenderizarProductos(listaFinal);
        } else {
            mostrarNotificacion('No hay productos para mostrar', 'warning');
        }

        mostrarLoading(false);
    } catch (error) {
        console.error('Error cargando productos:', error);
        mostrarNotificacion('No se pudo cargar el inventario. Verifica tu conexión e intenta de nuevo.', 'error');
        mostrarLoading(false);
    }
}

/**
 * Procesa la lista raw de productos y actualiza la UI
 */
function procesarYRenderizarProductos(listaFinal) {
    // Normalizar claves SQL → Frontend (Mapeo SQL-First v2.0)
    window.AppState.productosData = listaFinal.map(p => {
        // Campos exactos de la respuesta del JSON SQL
        const codigoSistema = p.codigo_sistema || p.id_codigo || p.codigo || '';
        const nombreProducto = p.nombre_producto || p.descripcion || '';
        const pTerminado = parseFloat(p.p_terminado) || parseFloat(p.stock_terminado) || 0;
        const stockBodega = parseFloat(p.stock_bodega) || 0;

        const comp = parseFloat(p.comprometido) || parseFloat(p.stock_comprometido) || 0;
        const porPulir = parseFloat(p.por_pulir) || 0;
        const min = parseFloat(p.stock_minimo) || 10;
        const disp = pTerminado - comp;

        // Lógica de 'Agotados' corregida (SQL-First)
        let semaforo = { color: 'green', estado: 'STOCK OK' };

        // Si no hay terminado ni hay en bodega (MP), está AGOTADO
        if (pTerminado <= 0 && stockBodega <= 0) {
            semaforo = { color: 'red', estado: 'AGOTADO' };
        } else if (pTerminado <= 0 && stockBodega > 0) {
            semaforo = { color: 'yellow', estado: 'POR ENSAMBLAR' };
        } else if (disp < min) {
            semaforo = { color: 'yellow', estado: 'POR PEDIR' };
        } else {
            semaforo = { color: 'green', estado: 'DISPONIBLE' };
        }

        // Imagen: usar campo SQL directo, no construir rutas a ciegas
        const imagenSQL = (p.imagen && typeof p.imagen === 'string' && p.imagen.trim() !== '') ? p.imagen : '';

        return {
            codigo: codigoSistema,
            id_codigo: p.id_codigo || '',
            descripcion: nombreProducto,
            precio: parseFloat(p.precio) || 0,
            stock_disponible: disp,
            stock_terminado: pTerminado,
            stock_comprometido: comp,
            stock_bodega: stockBodega,
            por_pulir: porPulir,
            en_zincado: parseFloat(p.en_zincado) || 0,
            en_granallado: parseFloat(p.en_granallado) || 0,
            stock_minimo: min,
            semaforo: semaforo,
            imagen: imagenSQL,
            imagen_valida: imagenSQL || null
        };
    });
    paginaActual = 1; // Resetear a página 1
    renderizarTablaProductos(window.AppState.productosData);
    actualizarEstadisticasInventario(window.AppState.productosData);
    console.log('✅ Productos SQL cargados y normalizados:', window.AppState.productosData.length);
}


function ordenarProductos(lista) {
    return lista.sort((a, b) => {
        const codeA = (a.codigo || a.codigo_sistema || "").toUpperCase();
        const codeB = (b.codigo || b.codigo_sistema || "").toUpperCase();
        
        // 1. Prioridad Máxima y Absoluta para FR-
        const isFR_A = codeA.startsWith('FR-');
        const isFR_B = codeB.startsWith('FR-');
        if (isFR_A && !isFR_B) return -1;
        if (!isFR_A && isFR_B) return 1;
        
        // 2. Segunda prioridad: Cualquier otra letra (AL-, CAR-, MT-) frente a números
        const isLetraA = /^[A-Z]/.test(codeA);
        const isLetraB = /^[A-Z]/.test(codeB);
        if (isLetraA && !isLetraB) return -1;
        if (!isLetraA && isLetraB) return 1;
        
        // 3. Desempate alfabético y numérico natural
        return codeA.localeCompare(codeB, undefined, { numeric: true, sensitivity: 'base' });
    });
}

/**
 * Renderizar tabla de productos con paginaciÃ³n
 */
function renderizarTablaProductos(productos, resetearPagina = false) {
    console.error("🚨 [AUDITORÍA] 'renderizarTablaProductos' se está ejecutando en vivo!");
    const tbody = document.getElementById('tabla-productos-body');
    if (!tbody) {
        console.error('No se encontrÃ³ tabla-productos-body');
        return;
    }

    if (!productos || productos.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 20px;">No hay productos</td></tr>';
        const paginationDiv = document.getElementById('pagination-container');
        if (paginationDiv) paginationDiv.innerHTML = '';
        return;
    }

    // APLICAR ORDENAMIENTO PRIORITARIO
    productos = ordenarProductos(productos);

    // Resetear pÃ¡gina si es necesario (por filtros)
    if (resetearPagina) paginaActual = 1;

    // Calcular Ã­ndices de paginaciÃ³n
    const itemsPerPage = getItemsPerPage();
    const totalProductos = productos.length;
    const totalPaginas = Math.ceil(totalProductos / itemsPerPage);
    const inicio = (paginaActual - 1) * itemsPerPage;
    const fin = Math.min(inicio + itemsPerPage, totalProductos);
    const productosPagina = productos.slice(inicio, fin);

    // Usar DocumentFragment para renderizado eficiente
    const fragment = document.createDocumentFragment();

    // Detectar modo móvil
    const esMovil = window.innerWidth < 992;

    // Si es móvil, limpiar estilos de tabla para usar grid/flex si es necesario, 
    // pero aquí mantendremos el tbody y usaremos celdas block o cambiaremos el contenedor.
    // ESTRATEGIA: Si es móvil, no inyectamos TRs, inyectamos un solo TR con un TD que contiene el Grid de Cards.
    // O mejor, manipulamos el DOM para ocultar la tabla y mostrar un div de cards.
    // SIMPLIFICACION: Generar HTML de cards dentro del tbody (un tr por card con display block) o reemplazar contenido.

    // MEJOR OPCION: Detectar y renderizar Cards
    // const fragment = document.createDocumentFragment(); // YA DECLARADO ARRIBA

    if (esMovil) {
        // MODO MÓVIL: CARDS MODERNAS (PWA Style)
        productosPagina.forEach(p => {
            const tr = document.createElement('tr');
            tr.className = 'mobile-product-card-row'; // Usar clase en lugar de estilos inline pesados

            const semaforoColor = p.semaforo?.color || 'gray';
            const imagenUrl = p.imagen || '';
            const localImageJpg = `/static/img/productos/${(p.codigo || '').trim()}.jpg`;
            const localImagePng = `/static/img/productos/${(p.codigo || '').trim()}.png`;

            tr.innerHTML = `
                <td class="mobile-card-cell">
                    <div class="mobile-product-card">
                        <div class="card-image-wrapper">
                            ${obtenerHtmlImagen(p, true)}
                            <span class="mobile-status-badge" style="background: ${getSemaforoColor(semaforoColor)}"></span>
                        </div>
                        
                        <div class="card-content">
                            <div class="card-header-flex">
                                <span class="card-code producto-trazabilidad-link" data-codigo="${p.codigo}" style="text-decoration: underline; cursor: pointer; color: #0d6efd;" title="Ver trazabilidad completa">${p.codigo}</span>
                                <span class="card-status-text" style="color: ${getSemaforoColor(semaforoColor)}">${p.semaforo?.estado || ''}</span>
                            </div>
                            
                            <h6 class="card-title">${p.descripcion || 'Sin descripción'}</h6>
                            
                            <div class="card-stats-grid">
                                <div class="stat-item" title="Material en espera de pulido">
                                    <span class="stat-label">POR PULIR</span>
                                    <span class="stat-value" style="color: #f97316;">${formatNumber(p.por_pulir || 0)}</span>
                                </div>
                                <div class="stat-item" title="Producto Terminado">
                                    <span class="stat-label">TERMINADO</span>
                                    <span class="stat-value success">${formatNumber(p.stock_terminado || 0)}</span>
                                </div>
                                <div class="stat-item" title="Unidades ya asignadas a pedidos. Toca para ver el detalle.">
                                    <span class="stat-label">COMPROM.</span>
                                    <span class="stat-value danger comprometido-link" data-codigo="${p.codigo}" style="color: #ef4444; text-decoration: underline; cursor: pointer;">${formatNumber(p.stock_comprometido || 0)}</span>
                                </div>
                                <div class="stat-item" title="Calculado: TERMINADO - COMPROMETIDO">
                                    <span class="stat-label">DISPONIBLE</span>
                                    <span class="stat-value" style="color: ${(p.stock_terminado - p.stock_comprometido) < p.stock_minimo ? '#dc2626' : '#2563eb'}; font-weight: 800;">
                                        ${(p.stock_terminado - p.stock_comprometido) < p.stock_minimo ? '⚠️ ' : ''}
                                        ${formatNumber((p.stock_terminado || 0) - (p.stock_comprometido || 0))}
                                    </span>
                                </div>
                                <div class="stat-item" title="Materia Prima en Bodega">
                                    <span class="stat-label">BODEGA (MP)</span>
                                    <span class="stat-value" style="color: #f59e0b;">${formatNumber(p.stock_bodega || 0)}</span>
                                </div>
                                <div class="stat-item" title="Procesos Externos (Zincado/Granallado)">
                                    <span class="stat-label">TRÁNSITO</span>
                                    <span class="stat-value" style="color: #8b5cf6;">${formatNumber((p.en_zincado || 0) + (p.en_granallado || 0))}</span>
                                </div>
                                <div class="stat-item" title="Mínimo Requerido">
                                    <span class="stat-label">MIN</span>
                                    <span class="stat-value secondary">${formatNumber(p.stock_minimo || 0)}</span>
                                </div>
                            </div>
                        </div>
                        <div class="card-arrow">
                            <i class="fas fa-chevron-right"></i>
                        </div>
                    </div>
                </td>
            `;
            fragment.appendChild(tr);
        });

    } else {
        // MODO DESKTOP: TABLA NORMAL
        productosPagina.forEach((p, idx) => {
            const tr = document.createElement('tr');
            tr.className = `animate-on-scroll delay-${(idx % 4) + 1}`; // Efecto cascada continuo
            tr.style.borderBottom = '1px solid #f0f0f0';

            // Obtener semáforo
            const semaforoColor = p.semaforo?.color || 'gray';
            const semaforoEstado = p.semaforo?.estado || '';

            // Cálculo de Disponible
            const stockTerminado = p.stock_terminado || 0;
            const stockComprometido = p.stock_comprometido || 0;
            const stockMinimo = p.stock_minimo || 0;
            const disponible = stockTerminado - stockComprometido;
            const bajoMinimo = disponible < stockMinimo;

            // ESTADO DE AUDITORÍA
            const tieneDiscrepancia = p.estado_auditoria === 'DISCREPANCIA';
            if (tieneDiscrepancia) {
                tr.style.backgroundColor = 'rgba(239, 68, 68, 0.1)'; // Naranja/Rojo muy claro
                tr.style.borderLeft = '4px solid #ef4444';
            }

            tr.innerHTML = `
                <td style="padding: 10px; text-align: center;">${obtenerHtmlImagen(p, false)}</td>
                <td style="padding: 10px;"><a href="#" class="producto-trazabilidad-link text-primary fw-bold text-decoration-underline" data-codigo="${p.codigo}" style="cursor: pointer;" title="Ver trazabilidad completa">${p.codigo || '-'}</a></td>
                <td style="padding: 10px; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${p.descripcion || '-'}
                    ${tieneDiscrepancia ? '<br><span class="badge bg-danger" style="font-size: 10px;">DISCREPANCIA DETECTADA</span>' : ''}
                </td>
                <td style="padding: 10px; text-align: right; color: #f97316; font-weight: 600; background: rgba(249, 115, 22, 0.05);">${formatNumber(p.por_pulir || 0)}</td>
                <td style="padding: 10px; text-align: right; color: #64748b;">${formatNumber(stockTerminado)}</td>
                <td style="padding: 10px; text-align: right; color: #ef4444; text-decoration: underline; cursor: pointer;" class="comprometido-link" data-codigo="${p.codigo}" title="Ver pedidos que componen este comprometido">${formatNumber(stockComprometido)}</td>
                <td style="padding: 10px; text-align: right; font-weight: ${bajoMinimo ? 'bold' : '600'}; color: ${bajoMinimo ? '#dc2626' : '#2563eb'};">
                    ${bajoMinimo ? '<i class="fas fa-exclamation-triangle" title="Bajo el Mínimo!"></i> ' : ''}
                    ${formatNumber(disponible)}
                </td>
                <td style="padding: 10px; text-align: right; color: #f59e0b; font-weight: 500;">${formatNumber(p.stock_bodega || 0)}</td>
                <td style="padding: 10px; text-align: right; color: #8b5cf6; font-weight: 500;" title="Zincado: ${formatNumber(p.en_zincado || 0)} | Granallado: ${formatNumber(p.en_granallado || 0)}">${formatNumber((p.en_zincado || 0) + (p.en_granallado || 0))}</td>
                <td style="padding: 10px; text-align: center;">
                    ${tieneDiscrepancia 
                        ? `<button class="btn btn-danger btn-sm w-100 fw-bold" onclick="window.ModuloInventario.abrirModalConteo('${p.codigo}')" style="font-size: 11px;">
                             <i class="fas fa-gavel"></i> CONTEO 3 (ADMIN)
                           </button>`
                        : `<span style="background: ${getSemaforoColor(semaforoColor)}; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 500;">
                             ${semaforoEstado}
                           </span>`
                    }
                </td>
            `;
            fragment.appendChild(tr);
        });
    }

    // Limpiar y agregar todo de una vez
    tbody.innerHTML = '';
    tbody.appendChild(fragment);

    // Renderizar controles de paginaciÃ³n
    renderizarPaginacion(totalProductos, totalPaginas, productos);

    console.log(`âœ… PÃ¡gina ${paginaActual}/${totalPaginas}: Mostrando ${productosPagina.length} de ${totalProductos} productos`);

    // --- ENCAPSULACIÓN ESTRUCTURAL FORZADA POR ID ---
    try {
        const tablaReal = document.querySelector('#tabla-inventario') || document.querySelector('.table');
        
        if (tablaReal) {
            // 1. Verificar si nuestro contenedor único ya existe, si no, crearlo en caliente
            let cajaScroll = document.querySelector('#contenedor-scroll-bujes');
            
            if (!cajaScroll) {
                cajaScroll = document.createElement('div');
                cajaScroll.id = 'contenedor-scroll-bujes';
                
                // Inyectar estilos inline atómicos indestructibles
                cajaScroll.style.cssText = `
                    max-height: 60vh !important;
                    overflow-y: auto !important;
                    overflow-x: auto !important;
                    position: relative !important;
                    display: block !important;
                    margin-bottom: 1rem !important;
                    border: 1px solid #dee2e6 !important;
                `;
                
                // Mover la tabla adentro del nuevo contenedor exclusivo
                tablaReal.parentNode.insertBefore(cajaScroll, tablaReal);
                cajaScroll.appendChild(tablaReal);
            }
            
            // 2. Forzar que la tabla separe sus bordes para permitir el anclaje nativo
            tablaReal.style.setProperty('border-collapse', 'separate', 'important');
            tablaReal.style.setProperty('border-spacing', '0', 'important');
            
            // 3. Clavar de forma nativa los th al techo de la nueva cajaScroll
            const headers = tablaReal.querySelectorAll('thead th');
            headers.forEach(th => {
                th.style.cssText = `
                    position: sticky !important;
                    top: 0 !important;
                    background-color: #ffffff !important;
                    z-index: 1050 !important;
                    box-shadow: inset 0 -1px 0 #dee2e6, 0 2px 4px rgba(0,0,0,0.08) !important;
                    transform: none !important;
                `;
            });
            
            console.log("🎯 [EXITO] Tabla encapsulada con éxito en #contenedor-scroll-bujes nativo");
        }
    } catch (error) {
        console.error("❌ Error aplicando la encapsulación forzada:", error);
    }
}

/**
 * Renderizar controles de paginaciÃ³n
 */
function renderizarPaginacion(totalProductos, totalPaginas, productos) {
    const paginationDiv = document.getElementById('pagination-container');
    if (!paginationDiv) return;

    if (totalPaginas <= 1) {
        paginationDiv.innerHTML = '';
        return;
    }

    const itemsPerPage = getItemsPerPage();
    const inicio = (paginaActual - 1) * itemsPerPage + 1;
    const fin = Math.min(paginaActual * itemsPerPage, totalProductos);

    let html = `
        <div class="pagination-container" style="display: flex; justify-content: space-between; align-items: center; padding: 15px 0;">
            <div class="pagination-info" style="color: #666; font-size: 14px;">
                Mostrando <strong>${inicio}-${fin}</strong> de <strong>${totalProductos}</strong> productos
            </div>
            <div class="pagination-buttons" style="display: flex; gap: 5px;">
    `;

    // BotÃ³n anterior
    html += `
        <button 
            onclick="window.ModuloInventario.cambiarPagina(${paginaActual - 1})" 
            ${paginaActual === 1 ? 'disabled' : ''}
            class="pagination-btn"
            style="padding: 8px 12px; border: 1px solid #ddd; background: ${paginaActual === 1 ? '#f5f5f5' : 'white'}; border-radius: 4px; cursor: ${paginaActual === 1 ? 'not-allowed' : 'pointer'}; color: ${paginaActual === 1 ? '#ccc' : '#333'};"
        >
            <i class="fas fa-chevron-left"></i> <span class="btn-text">Anterior</span>
        </button>
    `;

    // NÃºmeros de pÃ¡gina (mÃ¡ximo 7 botones)
    const maxBotones = 7;
    let inicioPaginas = Math.max(1, paginaActual - Math.floor(maxBotones / 2));
    let finPaginas = Math.min(totalPaginas, inicioPaginas + maxBotones - 1);

    if (finPaginas - inicioPaginas < maxBotones - 1) {
        inicioPaginas = Math.max(1, finPaginas - maxBotones + 1);
    }

    if (inicioPaginas > 1) {
        html += `<button onclick="window.ModuloInventario.cambiarPagina(1)" style="padding: 8px 12px; border: 1px solid #ddd; background: white; border-radius: 4px; cursor: pointer;">1</button>`;
        if (inicioPaginas > 2) html += `<span style="padding: 8px;">...</span>`;
    }

    for (let i = inicioPaginas; i <= finPaginas; i++) {
        const esActiva = i === paginaActual;
        html += `
            <button 
                onclick="window.ModuloInventario.cambiarPagina(${i})" 
                class="pagination-btn"
                style="padding: 8px 12px; border: 1px solid ${esActiva ? '#007bff' : '#ddd'}; background: ${esActiva ? '#007bff' : 'white'}; color: ${esActiva ? 'white' : '#333'}; border-radius: 4px; cursor: pointer; font-weight: ${esActiva ? 'bold' : 'normal'};"
            >
                ${i}
            </button>
        `;
    }

    if (finPaginas < totalPaginas) {
        if (finPaginas < totalPaginas - 1) html += `<span style="padding: 8px;">...</span>`;
        html += `<button onclick="window.ModuloInventario.cambiarPagina(${totalPaginas})" style="padding: 8px 12px; border: 1px solid #ddd; background: white; border-radius: 4px; cursor: pointer;">${totalPaginas}</button>`;
    }

    // BotÃ³n siguiente
    html += `
        <button 
            onclick="window.ModuloInventario.cambiarPagina(${paginaActual + 1})" 
            ${paginaActual === totalPaginas ? 'disabled' : ''}
            class="pagination-btn"
            style="padding: 8px 12px; border: 1px solid #ddd; background: ${paginaActual === totalPaginas ? '#f5f5f5' : 'white'}; border-radius: 4px; cursor: ${paginaActual === totalPaginas ? 'not-allowed' : 'pointer'}; color: ${paginaActual === totalPaginas ? '#ccc' : '#333'};"
        >
            <span class="btn-text">Siguiente</span> <i class="fas fa-chevron-right"></i>
        </button>
    `;

    html += `</div></div>`;
    paginationDiv.innerHTML = html;
}

/**
 * Cambiar pÃ¡gina
 */
function cambiarPagina(nuevaPagina) {
    const itemsPerPage = getItemsPerPage();
    const productosActuales = window.AppState.productosFiltrados || window.AppState.productosData || [];
    const totalPaginas = Math.ceil(productosActuales.length / itemsPerPage);

    if (nuevaPagina < 1 || nuevaPagina > totalPaginas) return;

    paginaActual = nuevaPagina;
    renderizarTablaProductos(productosActuales, false);

    // Scroll hacia arriba
    const tableContainer = document.querySelector('.table-container');
    if (tableContainer) tableContainer.scrollTop = 0;
}

/**
 * Obtener color de semÃ¡foro
 */
function getSemaforoColor(color) {
    const colores = {
        'green': '#28a745',
        'yellow': '#ffc107',
        'red': '#dc3545',
        'dark': '#6c757d',
        'gray': '#6c757d'
    };
    return colores[color] || '#6c757d';
}

/**
 * Actualizar estadÃ­sticas de inventario
 */
function actualizarEstadisticasInventario(productos) {
    if (!productos || productos.length === 0) return;

    const totalProductos = productos.length;

    // Contar productos por estado de semáforo según la nueva lógica
    const stockOK = productos.filter(p => p.stock_terminado > 0 || p.stock_bodega > 0).length; // Disponible si hay Terminado o Bodega
    const porPedir = productos.filter(p => p.semaforo?.color === 'yellow').length; 
    // Agotados: Solo si Terminado y Bodega son 0
    const agotados = productos.filter(p => p.stock_terminado <= 0 && p.stock_bodega <= 0).length;

    // Actualizar elementos del HTML (Soporta IDs viejos y nuevos de Premium UI)
    const el_total = document.getElementById('val-total-prod') || document.getElementById('total-productos');
    const el_stockOk = document.getElementById('val-stock-ok') || document.getElementById('productos-stock-ok');
    const el_bajoStock = document.getElementById('val-bajo-stock') || document.getElementById('productos-bajo-stock');
    const el_agotados = document.getElementById('val-agotados') || document.getElementById('productos-agotados');

    if (el_total) el_total.textContent = formatNumber(totalProductos);
    if (el_stockOk) el_stockOk.textContent = formatNumber(stockOK);

    if (el_bajoStock) {
        el_bajoStock.textContent = formatNumber(porPedir || 0);
    }

    if (el_agotados) el_agotados.textContent = formatNumber(agotados);

    console.log(`📊 Estadísticas: Total=${totalProductos}, OK=${stockOK}, PorPedir=${porPedir}, Agotados=${agotados}`);
}

/**
 * Inicializar módulo de inventario
 */
function inicializarInventario() {
    console.log('🔧 Inicializando módulo de Inventario...');
    configurarEventosInventario();
    cargarProductos();

    // Re-renderizar al redimensionar (Debounce)
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (window.AppState.productosData) {
                const prods = window.AppState.productosFiltrados || window.AppState.productosData;
                renderizarTablaProductos(prods, false);
            }
        }, 200);
    });

    console.log('✅ Módulo de Inventario inicializado');
}

/**
 * Configurar eventos de inventario
 */
function configurarEventosInventario() {
    // Buscar y filtrar productos
    const searchInput = document.getElementById('buscar-producto');
    if (searchInput) {
        let debounceTimerBusqueda;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(debounceTimerBusqueda);
            const query = e.target.value.toLowerCase();

            debounceTimerBusqueda = setTimeout(() => {
            if (!window.AppState.productosData) return;

            const filtrados = window.AppState.productosData.filter(p =>
                String(p.codigo || '').toLowerCase().includes(query) ||
                String(p.descripcion || '').toLowerCase().includes(query)
            );
            window.AppState.productosFiltrados = filtrados;
            renderizarTablaProductos(filtrados, true);
            console.log(`ðŸ” BÃºsqueda: "${query}" â†’ ${filtrados.length} resultados`);
            }, 200);
        });
    }

    // Botones de filtro por estado de semÃ¡foro
    const botonesFiltro = document.querySelectorAll('#filtros-inventario button');
    botonesFiltro.forEach((btn, index) => {
        btn.addEventListener('click', () => {
            // Quitar 'active' de todos los botones
            botonesFiltro.forEach(b => b.classList.remove('active'));
            // Marcar este botÃ³n como activo
            btn.classList.add('active');

            if (!window.AppState.productosData) return;

            let productosFiltrados = [];
            const textoBtn = btn.textContent.trim().toLowerCase();

            // Filtrar segÃºn el botÃ³n clicado
            // Filtrar SEGÚN SEMÁFORO (CORREGIDO Juan Sebastian)
            if (textoBtn.includes('todos')) {
                productosFiltrados = window.AppState.productosData;
            } else if (textoBtn.includes('por pedir') || textoBtn.includes('pedir')) {
                // AMARILLO: Stock <= Reorden y > 0
                productosFiltrados = window.AppState.productosData.filter(p => p.semaforo?.color === 'yellow');
            } else if (textoBtn.includes('stock ok')) {
                // VERDE: Stock > Reorden
                productosFiltrados = window.AppState.productosData.filter(p => p.semaforo?.color === 'green');
            } else if (textoBtn.includes('agotados')) {
                // ROJO: Stock <= 0
                productosFiltrados = window.AppState.productosData.filter(p =>
                    p.semaforo?.estado === 'AGOTADO' || p.semaforo?.color === 'red' || p.semaforo?.color === 'dark'
                );
            }

            window.AppState.productosFiltrados = productosFiltrados;
            renderizarTablaProductos(productosFiltrados, true);
            console.log(`ðŸ”˜ Filtro: "${textoBtn}" â†’ ${productosFiltrados.length} productos`);
        });
    });

    // Botón actualizar
    const btnActualizar = document.getElementById('btn-actualizar-productos');
    if (btnActualizar) {
        btnActualizar.addEventListener('click', () => {
            console.log('🔄 Recargando productos (Forzando actualización)...');
            mostrarNotificacion('Actualizando inventario desde la nube...', 'info');
            cargarProductos(true);
        });
    }

    // Botón Conteo / Auditoría
    const btnConteo = document.getElementById('btn-conteo-inventario');
    if (btnConteo) {
        btnConteo.addEventListener('click', () => {
            abrirModalConteo();
        });
    }

    // Delegación de eventos para la Trazabilidad 360 (Evita inline onclick)
    const tbody = document.getElementById('tabla-productos-body');
    if (tbody && !tbody.dataset.trazabilidadBound) {
        tbody.dataset.trazabilidadBound = 'true';
        tbody.addEventListener('click', (e) => {
            const target = e.target.closest('.producto-trazabilidad-link');
            if (target) {
                e.preventDefault();
                const codigo = target.getAttribute('data-codigo');
                if (codigo && typeof window.abrirModalHistorial === 'function') {
                    window.abrirModalHistorial(codigo);
                }
                return;
            }

            const targetComprometido = e.target.closest('.comprometido-link');
            if (targetComprometido) {
                e.preventDefault();
                const codigo = targetComprometido.getAttribute('data-codigo');
                if (codigo) abrirModalComprometidos(codigo);
            }
        });
    }

    // Formulario de Conteo
    const formConteo = document.getElementById('form-conteo-inventario');
    if (formConteo) {
        formConteo.addEventListener('submit', (e) => {
            e.preventDefault();
            const inputAutocomplete = document.getElementById('conteo-producto-autocomplete');
            const hiddenCodigo = document.getElementById('conteo-producto-codigo');
            
            // Si el hidden no tiene valor, intentar parsear del input visual
            let codigoVal = hiddenCodigo.value;
            if (!codigoVal && inputAutocomplete.value.includes(' - ')) {
                codigoVal = inputAutocomplete.value.split(' - ')[0].trim();
            } else if (!codigoVal) {
                codigoVal = inputAutocomplete.value.trim();
            }

            if (!codigoVal) {
                Swal.fire('Error', 'Debe seleccionar un producto válido', 'warning');
                return;
            }

            const data = {
                codigo: codigoVal,
                cantidad: parseInt(document.getElementById('conteo-cantidad').value),
                tipo_stock: document.querySelector('input[name="tipo_stock"]:checked')?.value || 'principal',
                responsable: document.getElementById('conteo-responsable').value,
                observaciones: document.getElementById('conteo-observaciones').value
            };
            registrarConteo(data);
        });
    }

    // Inicializar Autocomplete para Auditoría
    inicializarAutocompleteAuditoria();
}

/**
 * Autocomplete avanzado para Auditoría
 */
function inicializarAutocompleteAuditoria() {
    const input = document.getElementById('conteo-producto-autocomplete');
    const suggestionsDiv = document.getElementById('conteo-producto-suggestions');
    const hiddenCodigo = document.getElementById('conteo-producto-codigo');

    if (!input || !suggestionsDiv) return;

    let debounceTimer;

    input.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        const query = e.target.value.trim();
        hiddenCodigo.value = ''; // Resetear al escribir

        if (query.length < 2) {
            suggestionsDiv.style.display = 'none';
            return;
        }

        debounceTimer = setTimeout(() => {
            const productos = window.AppState.productosData || [];
            const queryNorm = query.toLowerCase();
            
            const filtrados = productos.filter(p => {
                const code = String(p.codigo || '').toLowerCase();
                const desc = String(p.descripcion || '').toLowerCase();
                return code.includes(queryNorm) || desc.includes(queryNorm);
            });

            if (filtrados.length === 0) {
                suggestionsDiv.innerHTML = '<div class="suggestion-item text-muted">No se encontraron productos</div>';
                suggestionsDiv.style.display = 'block';
                return;
            }

            // Usar la utilidad global de renderizado si existe, o implementarla localmente
            if (typeof window.renderProductSuggestions === 'function') {
                window.renderProductSuggestions(suggestionsDiv, filtrados.slice(0, 15), (item) => {
                    input.value = `${item.codigo} - ${item.descripcion}`;
                    hiddenCodigo.value = item.codigo;
                    suggestionsDiv.style.display = 'none';
                    // Saltar a cantidad
                    document.getElementById('conteo-cantidad')?.focus();
                });
                suggestionsDiv.style.display = 'block';
            } else {
                // Fallback local
                suggestionsDiv.innerHTML = filtrados.slice(0, 15).map(p => `
                    <div class="suggestion-item p-2 border-bottom" style="cursor: pointer;" data-code="${p.codigo}">
                        <strong>${p.codigo}</strong> - ${p.descripcion}
                    </div>
                `).join('');
                suggestionsDiv.style.display = 'block';
                
                suggestionsDiv.querySelectorAll('.suggestion-item').forEach(el => {
                    el.onclick = () => {
                        const code = el.getAttribute('data-code');
                        const p = filtrados.find(x => x.codigo === code);
                        input.value = `${p.codigo} - ${p.descripcion}`;
                        hiddenCodigo.value = p.codigo;
                        suggestionsDiv.style.display = 'none';
                        document.getElementById('conteo-cantidad')?.focus();
                    };
                });
            }
        }, 300);
    });

    // Cerrar al click fuera
    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
            suggestionsDiv.style.display = 'none';
        }
    });

    // Configurar SmartEnter
    if (window.ModuloUX && window.ModuloUX.setupSmartEnter) {
        window.ModuloUX.setupSmartEnter({
            inputIds: ['conteo-producto-autocomplete', 'conteo-cantidad', 'conteo-responsable', 'conteo-observaciones'],
            actionBtnId: 'btn-registrar-conteo-submit', // Asegúrate de que el botón tenga este ID o usa el submit del form
            autocomplete: {
                inputId: 'conteo-producto-autocomplete',
                suggestionsId: 'conteo-producto-suggestions'
            }
        });
    }
}

/**
 * Lógica de Auditoría / Conteo
 */
function abrirModalConteo(codigoDefecto = null) {
    const modal = document.getElementById('modalConteoInventario');
    const inputAutocomplete = document.getElementById('conteo-producto-autocomplete');
    const hiddenCodigo = document.getElementById('conteo-producto-codigo');
    const selectResp = document.getElementById('conteo-responsable');

    if (!modal) return;

    // VALIDACIÓN DE PERMISOS PARA CONTEO 3 (DISCREPANCIAS)
    if (codigoDefecto) {
        const productos = window.AppState.productosData || [];
        const prod = productos.find(p => p.codigo === codigoDefecto);
        
        if (prod && prod.estado_auditoria === 'DISCREPANCIA') {
            const userRole = (window.AppState.user?.rol || '').toLowerCase();
            const esAutorizado = userRole.includes('admin') || userRole.includes('supervisor');
            
            if (!esAutorizado) {
                Swal.fire({
                    icon: 'lock',
                    title: 'Acceso Restringido',
                    text: 'Solo un Administrador o Supervisor puede resolver una discrepancia de inventario (Conteo 3).',
                    confirmButtonColor: '#ef4444'
                });
                return;
            }
        }
    }

    // Resetear campos
    if (inputAutocomplete) inputAutocomplete.value = '';
    if (hiddenCodigo) hiddenCodigo.value = '';
    
    // Resetear radio buttons a 'principal'
    const radioPrincipal = document.getElementById('tipo-stock-principal');
    if (radioPrincipal) radioPrincipal.checked = true;

    // Si viene un código por defecto (ej: desde el botón de la tabla), seleccionarlo
    if (codigoDefecto && inputAutocomplete) {
        const prod = (window.AppState.productosData || []).find(p => p.codigo === codigoDefecto);
        if (prod) {
            inputAutocomplete.value = `${prod.codigo} - ${prod.descripcion}`;
            hiddenCodigo.value = prod.codigo;
        } else {
            inputAutocomplete.value = codigoDefecto;
            hiddenCodigo.value = codigoDefecto;
        }
    }

    // Limpiar cantidad previa
    const inputCantidad = document.getElementById('conteo-cantidad');
    if (inputCantidad) inputCantidad.value = '';

    // Poblar responsables (Corregido: r es un objeto {nombre, departamento})
    if (selectResp && selectResp.options.length <= 1) {
        const responsables = window.AppState.sharedData?.responsables || [];
        console.log('👥 Poblando responsables en modal:', responsables);

        responsables.forEach(r => {
            const nombre = typeof r === 'object' ? r.nombre : r;
            const opt = document.createElement('option');
            opt.value = nombre;
            opt.textContent = nombre;
            selectResp.appendChild(opt);
        });
    }

    modal.style.display = 'flex';
    document.getElementById('form-conteo-inventario')?.reset();
}

function cerrarModalConteo() {
    const modal = document.getElementById('modalConteoInventario');
    if (modal) modal.style.display = 'none';
}

async function registrarConteo(data) {
    try {
        console.log('📤 Enviando conteo:', data);
        mostrarLoading(true);

        const response = await fetch('/api/conteo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const contentType = response.headers.get("content-type");
        if (!contentType || !contentType.includes("application/json")) {
            throw new Error('Respuesta no válida del servidor');
        }

        const result = await response.json();
        
        // CERRAR LOADER ANTES DE MOSTRAR ALERTAS (Crucial para evitar bloqueo)
        mostrarLoading(false);

        // --- SOLUCIÓN UX: CERRAR Y LIMPIAR MODAL DE INMEDIATO ---
        cerrarModalConteo();
        document.getElementById('form-conteo-inventario')?.reset(); 

        // Nueva lógica alineada con el Backend estandarizado
        const msg = result.mensaje || result.message || "Operación completada";

        if (result.status === 'discrepancy') {
            await Swal.fire({
                icon: 'warning',
                title: '¡ALERTA DE DISCREPANCIA!',
                text: msg,
                confirmButtonText: 'Entendido, llamar a Supervisor',
                confirmButtonColor: '#d33', // Rojo vibrante solicitado
                allowOutsideClick: false,
                allowEscapeKey: false
            });
        } else if (result.status === 'first_count') {
            await Swal.fire({
                icon: 'info',
                title: 'Primer Conteo Registrado',
                text: msg,
                timer: 2500,
                showConfirmButton: false
            });
        } else if (result.status === 'match' || result.success) {
            await Swal.fire({
                icon: 'success',
                title: 'Auditoría Exitosa',
                text: msg,
                timer: 2000,
                showConfirmButton: false
            });
        } else {
            Swal.fire('Error', result.error || msg || 'No se pudo guardar el conteo', 'error');
        }

        if (typeof cargarProductos === 'function') {
            cargarProductos(true); // Refrescar para ver estado de discrepancia en tabla
        }
    } catch (error) {
        mostrarLoading(false);
        console.error('Error:', error);
        Swal.fire('Error de Conexión', 'No se pudo comunicar con el servidor o la respuesta no es válida.', 'error');
    } finally {
        // Doble aseguramiento
        mostrarLoading(false);
    }
}

// ============================================
// EXPORTAR MÃ“DULO
// ============================================
window.ModuloInventario = {
    inicializar: inicializarInventario,
    cambiarPagina: cambiarPagina,
    cerrarModalConteo: cerrarModalConteo,
    abrirModalConteo: abrirModalConteo,

    // ── Sincronización de Precios WO ────────────────────────────────────

    _archivoPreciosWO: null,

    abrirModalSincronizarPrecios: function () {
        const modal = document.getElementById('modalSincronizarPreciosWO');
        if (!modal) { console.error('[WO] Modal no encontrado'); return; }
        // Resetear estado
        this._archivoPreciosWO = null;
        const fileNameEl = document.getElementById('wo-file-name');
        if (fileNameEl) fileNameEl.textContent = '';
        const fileInput = document.getElementById('wo-file-input');
        if (fileInput) fileInput.value = '';
        const btnConfirmar = document.getElementById('btn-confirmar-sync-wo');
        if (btnConfirmar) btnConfirmar.disabled = true;
        const progressEl = document.getElementById('wo-progress-container');
        if (progressEl) progressEl.classList.add('d-none');
        modal.style.display = 'flex';
    },

    cerrarModalSincronizarPrecios: function () {
        const modal = document.getElementById('modalSincronizarPreciosWO');
        if (modal) modal.style.display = 'none';
        this._archivoPreciosWO = null;
    },

    manejarArchivoWO: function (file) {
        if (!file) return;
        const ext = file.name.toLowerCase().split('.').pop();
        if (!['csv', 'xlsx', 'xls'].includes(ext)) {
            Swal.fire('Formato Inválido', 'Por favor selecciona un archivo .csv, .xlsx o .xls exportado de World Office.', 'warning');
            return;
        }
        this._archivoPreciosWO = file;
        const fileNameEl = document.getElementById('wo-file-name');
        if (fileNameEl) fileNameEl.textContent = `✅ ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        const btnConfirmar = document.getElementById('btn-confirmar-sync-wo');
        if (btnConfirmar) btnConfirmar.disabled = false;
    },

    ejecutarSincronizarPrecios: async function () {
        if (!this._archivoPreciosWO) {
            Swal.fire('Sin Archivo', 'Primero selecciona un archivo válido de World Office.', 'warning');
            return;
        }

        const progressEl = document.getElementById('wo-progress-container');
        const btnConfirmar = document.getElementById('btn-confirmar-sync-wo');
        if (progressEl) progressEl.classList.remove('d-none');
        if (btnConfirmar) btnConfirmar.disabled = true;

        try {
            const formData = new FormData();
            formData.append('archivo', this._archivoPreciosWO);

            const response = await fetch('/api/productos/sincronizar_precios', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (progressEl) progressEl.classList.add('d-none');
            this.cerrarModalSincronizarPrecios();

            if (result.success) {
                // Almacenar el reporte detallado en memoria global para su descarga
                window._ultimoReporteWO = result;

                await Swal.fire({
                    icon: 'success',
                    title: '¡Sincronización Completada!',
                    html: `
                        <p class="mb-2">El archivo se ha procesado con los siguientes resultados:</p>
                        <div class="d-flex justify-content-center gap-2 mt-2 mb-3 flex-wrap">
                            <span class="badge bg-success px-3 py-2">✅ ${result.actualizados_count} actualizados</span>
                            <span class="badge bg-secondary px-3 py-2">⏭ ${result.omitidos_count} no encontrados</span>
                            ${result.errores_count > 0 ? `<span class="badge bg-danger px-3 py-2">⚠️ ${result.errores_count} errores</span>` : ''}
                        </div>
                        <button type="button" class="btn btn-outline-primary btn-sm w-100 py-2 border-dashed"
                            style="border-style: dashed;"
                            onclick="ModuloInventario.descargarReporteWO()">
                            <i class="fas fa-download me-1"></i> Descargar Reporte Detallado (.txt)
                        </button>
                    `,
                    confirmButtonText: 'Entendido',
                    confirmButtonColor: '#6366f1'
                });
                // Refrescar tabla de inventario
                if (typeof cargarProductos === 'function') cargarProductos(true);
            } else {
                Swal.fire('Error en la Sincronización', result.error || 'No se pudo procesar el archivo.', 'error');
                if (btnConfirmar) btnConfirmar.disabled = false;
            }
        } catch (e) {
            if (progressEl) progressEl.classList.add('d-none');
            if (btnConfirmar) btnConfirmar.disabled = false;
            console.error('[WO Sync] Error de red:', e);
            Swal.fire('Error de Conexión', 'No se pudo comunicar con el servidor. Revisa tu conexión.', 'error');
        }
    },

    descargarReporteWO: function () {
        const report = window._ultimoReporteWO;
        if (!report || !report.detalles) {
            Swal.fire('Atención', 'No hay datos de reporte disponibles para descargar.', 'warning');
            return;
        }

        let txt = "=========================================================\n";
        txt += "     REPORTE DE SINCRONIZACIÓN DE PRECIOS WORLD OFFICE\n";
        txt += "=========================================================\n";
        txt += `Fecha y Hora:   ${new Date().toLocaleString()}\n`;
        txt += `Archivo:        ${this._archivoPreciosWO ? this._archivoPreciosWO.name : 'Desconocido'}\n`;
        txt += `Actualizados:   ${report.actualizados_count}\n`;
        txt += `No encontrados: ${report.omitidos_count}\n`;
        txt += `Errores:        ${report.errores_count}\n`;
        txt += "=========================================================\n\n";

        const actualizados = report.detalles.filter(d => d.status === 'Actualizado');
        const noEncontrados = report.detalles.filter(d => d.status.includes('No encontrado'));
        const errores = report.detalles.filter(d => d.status === 'Error');

        txt += "---------------------------------------------------------\n";
        txt += `1. PRECIOS ACTUALIZADOS CON ÉXITO (${actualizados.length})\n`;
        txt += "---------------------------------------------------------\n";
        if (actualizados.length === 0) {
            txt += "(Ninguno)\n";
        } else {
            actualizados.forEach(d => {
                txt += `- Código: ${d.codigo} | Nuevo Precio: $${d.precio_archivo}\n`;
            });
        }
        txt += "\n";

        txt += "---------------------------------------------------------\n";
        txt += `2. NO ENCONTRADOS EN BASE DE DATOS (${noEncontrados.length})\n`;
        txt += "---------------------------------------------------------\n";
        if (noEncontrados.length === 0) {
            txt += "(Ninguno)\n";
        } else {
            noEncontrados.forEach(d => {
                txt += `- Código: ${d.codigo} | Precio en Archivo: $${d.precio_archivo || 'N/A'} | Motivo: ${d.status}\n`;
            });
        }
        txt += "\n";

        txt += "---------------------------------------------------------\n";
        txt += `3. REGISTROS CON ERROR EN LA OPERACIÓN (${errores.length})\n`;
        txt += "---------------------------------------------------------\n";
        if (errores.length === 0) {
            txt += "(Ninguno)\n";
        } else {
            errores.forEach(d => {
                txt += `- Código: ${d.codigo} | Motivo: ${d.motivo || 'Error desconocido'}\n`;
            });
        }

        const blob = new Blob([txt], { type: 'text/plain;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `reporte_sincronizacion_precios_wo_${new Date().toISOString().slice(0, 10)}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },

    // Umbral a partir del cual se considera que los datos recibidos del
    // agente local (agente_wo.py) están desactualizados y hay que advertir
    // al usuario antes de aplicarlos sobre db_productos.
    UMBRAL_ANTIGUEDAD_WO_HORAS: 24,

    formatearAntiguedadWO: function (antiguedadHoras) {
        if (antiguedadHoras === null || antiguedadHoras === undefined) {
            return 'Nunca se ha recibido una sincronización del agente WO.';
        }
        if (antiguedadHoras < 1) {
            return `Datos extraídos hace ${Math.round(antiguedadHoras * 60)} minuto(s).`;
        }
        if (antiguedadHoras < 24) {
            return `Datos extraídos hace ${antiguedadHoras} hora(s).`;
        }
        return `Datos extraídos hace ${Math.round(antiguedadHoras / 24)} día(s) — verifica que el agente WO haya corrido recientemente.`;
    },

    sincronizarStockWO: async function () {
        let antiguedadHoras = null;
        let registrosDisponibles = 0;
        try {
            const estadoResp = await fetch('/api/wo/inventario/estado');
            const estado = await estadoResp.json();
            if (estado.success) {
                antiguedadHoras = estado.antiguedad_horas;
                registrosDisponibles = estado.registros || 0;
            }
        } catch (e) {
            console.warn('[WO Stock Sync] No se pudo consultar la antigüedad de los datos:', e);
        }

        const esDesactualizado = registrosDisponibles === 0 ||
            antiguedadHoras === null ||
            antiguedadHoras > this.UMBRAL_ANTIGUEDAD_WO_HORAS;

        const confirmResult = await Swal.fire({
            title: '¿Sincronizar Stock con World Office?',
            html: 'Esta acción sobrescribirá la columna de Producto Terminado en FriTech con el stock recibido de World Office.' +
                `<br><br><strong>${this.formatearAntiguedadWO(antiguedadHoras)}</strong>` +
                (esDesactualizado ? '<br><span style="color:#dc3545">Los datos pueden no coincidir con World Office ahora mismo. Si necesitas el stock actual, pide que corran primero el agente WO.</span>' : ''),
            icon: esDesactualizado ? 'warning' : 'question',
            showCancelButton: true,
            confirmButtonText: 'Sí, unificar stock',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#10b981',
            cancelButtonColor: '#6c757d'
        });

        if (!confirmResult.isConfirmed) return;

        // Mostrar alerta de carga
        Swal.fire({
            title: 'Sincronizando Inventario...',
            text: 'Por favor espera mientras unificamos las cantidades de stock.',
            allowOutsideClick: false,
            allowEscapeKey: false,
            allowEnterKey: false,
            showConfirmButton: false,
            didOpen: () => {
                Swal.showLoading();
            }
        });

        try {
            const response = await fetch('/api/wo/unificar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            const result = await response.json();

            if (result.success) {
                await Swal.fire({
                    icon: 'success',
                    title: '¡Stock Unificado!',
                    html: `Se actualizaron con éxito ${result.actualizados} productos en el inventario real.` +
                        `<br><small>${this.formatearAntiguedadWO(result.antiguedad_horas)}</small>`,
                    confirmButtonText: 'Excelente',
                    confirmButtonColor: '#10b981'
                });
                // Refrescar tabla de inventario
                if (typeof cargarProductos === 'function') cargarProductos(true);
            } else {
                Swal.fire('Error de Unificación', result.message || result.error || 'No se pudo sincronizar el stock.', 'error');
            }
        } catch (e) {
            console.error('[WO Stock Sync] Error:', e);
            Swal.fire('Error de Conexión', 'No se pudo comunicar con el servidor para la unificación.', 'error');
        }
    }
};

/**
 * Modal de detalle de "Comprometido": lista los pedidos activos que suman
 * el valor mostrado en la columna COMPROMETIDO de un producto.
 */
async function abrirModalComprometidos(codigo) {
    const modal = document.getElementById('modalComprometidosProducto');
    const body = document.getElementById('comprometidos-modal-body');
    const subtitulo = document.getElementById('comprometidos-modal-subtitulo');
    if (!modal || !body) return;

    const producto = (window.AppState.productosData || []).find(p => p.codigo === codigo);
    subtitulo.textContent = producto
        ? `${codigo} — ${producto.descripcion || ''}`
        : codigo;

    body.innerHTML = '<div class="text-center py-4 text-muted"><i class="fas fa-spinner fa-spin fa-2x"></i></div>';
    modal.style.display = 'flex';

    try {
        const response = await fetch(`/api/productos/comprometidos/${encodeURIComponent(codigo)}`);
        const data = await response.json();

        if (!data.success || !Array.isArray(data.items) || data.items.length === 0) {
            body.innerHTML = '<div class="text-center py-4 text-muted">No hay pedidos activos que expliquen este comprometido.</div>';
            return;
        }

        const filas = data.items.map(it => `
            <tr>
                <td>${it.id_pedido || '-'}</td>
                <td>${it.cliente || '-'}</td>
                <td>${it.fecha || '-'}</td>
                <td><span class="badge bg-secondary">${it.estado || '-'}</span></td>
                <td class="text-end">${formatNumber(it.cantidad)}</td>
                <td class="text-end">${formatNumber(it.cant_alistada)}</td>
                <td class="text-end fw-bold text-danger">${formatNumber(it.pendiente)}</td>
            </tr>
        `).join('');

        body.innerHTML = `
            <table class="table table-sm table-hover align-middle mb-0">
                <thead>
                    <tr style="font-size: 11px; text-transform: uppercase; color: #64748b;">
                        <th>Pedido</th>
                        <th>Cliente</th>
                        <th>Fecha</th>
                        <th>Estado</th>
                        <th class="text-end">Cant.</th>
                        <th class="text-end">Alistada</th>
                        <th class="text-end">Pendiente</th>
                    </tr>
                </thead>
                <tbody>${filas}</tbody>
                <tfoot>
                    <tr class="fw-bold">
                        <td colspan="6" class="text-end">Total Comprometido:</td>
                        <td class="text-end text-danger">${formatNumber(data.total_comprometido)}</td>
                    </tr>
                </tfoot>
            </table>
        `;
    } catch (error) {
        console.error('Error cargando comprometidos:', error);
        body.innerHTML = '<div class="text-center py-4 text-danger">No se pudo cargar el detalle de pedidos.</div>';
    }
}
window.abrirModalComprometidos = abrirModalComprometidos;

/**
 * Función puente global para abrir el Modal de Historial.
 * Se invoca desde in-place en la tabla y llama al módulo Historial sin recargar.
 */
window.abrirModalHistorial = function(codigo) {
    if (window.ModuloHistorial && typeof window.ModuloHistorial.irAProducto === 'function') {
        window.ModuloHistorial.irAProducto(codigo);
    } else {
        console.error("Módulo de Trazabilidad/Historial no se encuentra disponible.");
        if (typeof Swal !== 'undefined') {
            Swal.fire('Atención', 'El módulo de trazabilidad no está disponible en esta vista.', 'info');
        } else {
            alert('El módulo de trazabilidad no está disponible.');
        }
    }
};
