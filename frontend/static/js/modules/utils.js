// ============================================
// utils.js - Funciones compartidas
// ============================================

/**
 * Placeholder SVG para productos sin imagen
 */
const PLACEHOLDER_SVG_PRODUCTO = `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0%25' y1='0%25' x2='100%25' y2='100%25'%3E%3Cstop offset='0%25' style='stop-color:%23f8fafc;stop-opacity:1' /%3E%3Cstop offset='100%25' style='stop-color:%23e2e8f0;stop-opacity:1' /%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='100' height='100' fill='url(%23g)' rx='12'/%3E%3Cg opacity='0.4' transform='translate(0, -5)'%3E%3Cpath d='M30 40c0-2.2 1.8-4 4-4h32c2.2 0 4 1.8 4 4v25c0 2.2-1.8 4-4 4H34c-2.2 0-4-1.8-4-4V40z' fill='%2364748b'/%3E%3Ccircle cx='50' cy='52.5' r='7' fill='%23f1f5f9'/%3E%3Cpath d='M46 32h8l2 4h-12z' fill='%2364748b'/%3E%3C/g%3E%3Ctext x='50' y='82' text-anchor='middle' font-family='sans-serif' font-size='7' fill='%2394a3b8' font-weight='bold'%3EFriTech%3C/text%3E%3C/svg%3E`;

/**
 * Función centralizada para limpiar códigos de prefijos (Regex)
 * Réplica exacta de la lógica del backend.
 */
function limpiarCodigoJS(codigo) {
    if (!codigo) return '';
    return String(codigo).trim().toUpperCase().replace(/^[A-Z]+-/, '');
}

/**
 * Renderiza sugerencias de productos con imagen (Estandarizado)
 * @param {HTMLElement} container - El div de sugerencias
 * @param {Array} items - Lista de productos filtrados
 * @param {Function} onSelect - Callback al seleccionar un item
 */
function renderProductSuggestions(container, items, onSelect) {
    if (!container) return;

    if (items.length === 0) {
        container.innerHTML = '<div class="suggestion-item">No se encontraron productos</div>';
        container.classList.add('active');
        return;
    }

    container.innerHTML = items.map(prod => {
        const codigoOriginal = String(prod.codigo_sistema || prod.codigo || 'N/A').trim();
        const codigoLimpio = limpiarCodigoJS(codigoOriginal);
        const descripcion = prod.descripcion || 'Sin descripción';
        const precio = prod.precio || 0;
        const stock = prod.stock_disponible ?? prod.stock_total ?? prod.stock ?? 0;

        // Lógica de imágenes Súper Radar v3.0 (Estandarizado)
        const localImgOriginal = `/static/img/productos/${codigoOriginal}.jpg`;
        const localImgLimpio = `/static/img/productos/${codigoLimpio}.jpg`;
        const localImgPng = `/static/img/productos/${codigoLimpio}.png`;
        const fallbackUrl = prod.imagen || '';

        return `
            <div class="suggestion-item product-suggestion-pro" 
                 style="display: flex; align-items: center; gap: 12px; padding: 10px; cursor: pointer; border-bottom: 1px solid #f0f0f0;"
                 data-codigo="${codigoOriginal}">
                
                <img src="${localImgOriginal}" 
                     data-limpio-src="${localImgLimpio}"
                     data-png-src="${localImgPng}"
                     data-url-src="${fallbackUrl}"
                     data-placeholder="${PLACEHOLDER_SVG_PRODUCTO}"
                     data-attempt="0"
                     onerror="
                        const attempt = parseInt(this.dataset.attempt || '0');
                        this.dataset.attempt = (attempt + 1).toString();
                        
                        if (attempt === 0) {
                            // Intento 1: Original -> Código Limpio (.jpg)
                            this.src = this.dataset.limpioSrc;
                        } else if (attempt === 1) {
                            // Intento 2: Limpio (.jpg) -> Limpio (.png)
                            this.src = this.dataset.pngSrc;
                        } else if (attempt === 2 && this.dataset.urlSrc) {
                            // Intento 3: .png -> Cloud URL (Google Drive)
                            this.src = this.dataset.urlSrc;
                        } else if (attempt === 3) {
                            // Intento 4: Placeholder SVG
                            this.src = this.dataset.placeholder;
                        } else {
                            // Final: Imagen por defecto no-photo.png
                            this.src = '/static/img/no-photo.png';
                            this.onerror = null;
                        }
                     "
                     style="width: 42px; height: 42px; object-fit: cover; border-radius: 8px; background: #f8fafc; border: 1px solid #e2e8f0;">
                
                <div style="flex: 1; min-width: 0;">
                    <div style="display: flex; justify-content: space-between; align-items: baseline; gap: 8px;">
                        <strong style="color: #1e293b; font-size: 0.95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${codigoOriginal}</strong>
                        <span style="color: #64748b; font-size: 0.75rem; font-weight: 600;">Stock: ${stock}</span>
                    </div>
                    <div style="color: #475569; font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${descripcion}</div>
                    ${precio > 0 ? `<div style="color: #0891b2; font-size: 0.8rem; font-weight: 600; margin-top: 2px;">$ ${formatNumber(precio)}</div>` : ''}
                </div>
            </div>
        `;
    }).join('');

    // Event listeners para selección
    container.querySelectorAll('.suggestion-item').forEach((item, index) => {
        item.addEventListener('click', () => {
            onSelect(items[index]);
            container.classList.remove('active');
            container.style.display = 'none'; // Asegurar que se oculte
        });
    });

    container.classList.add('active');
    container.style.display = 'block';
}

/**
 * Mostrar notificación visual
 */
function mostrarNotificacion(mensaje, tipo = 'info', undoData = null) {
    // 1. Reproducir sonido (Gamificación v1.3)
    if (window.ModuloUX && window.ModuloUX.playSound) {
        window.ModuloUX.playSound(tipo);
    }

    // Definir colores de borde según tipo
    const colors = {
        'success': '#10b981', // Green
        'error': '#ef4444',   // Red
        'warning': '#f59e0b', // Amber
        'info': '#3b82f6'     // Blue
    };

    const iconColor = colors[tipo] || colors['info'];

    const icon = {
        'success': `<i class="fas fa-check-circle" style="color: ${iconColor}; font-size: 1.2rem;"></i>`,
        'error': `<i class="fas fa-times-circle" style="color: ${iconColor}; font-size: 1.2rem;"></i>`,
        'warning': `<i class="fas fa-exclamation-triangle" style="color: ${iconColor}; font-size: 1.2rem;"></i>`,
        'info': `<i class="fas fa-info-circle" style="color: ${iconColor}; font-size: 1.2rem;"></i>`
    }[tipo] || '';

    const notificationDiv = document.createElement('div');

    // Estilos profesionales
    notificationDiv.style.cssText = `
        position: fixed;
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
        background-color: #ffffff;
        color: #333333;
        padding: 16px 24px;
        border-radius: 12px;
        font-weight: 500;
        font-size: 0.95rem;
        z-index: 10005;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.15), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
        display: flex;
        align-items: center;
        gap: 15px;
        max-width: 90%;
        width: git 420px;
        border-left: 6px solid ${iconColor};
        animation: slideInDown 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    `;

    // Botón Undo (si hay datos)
    let undoHtml = '';
    if (undoData && undoData.hoja && undoData.fila) {
        undoHtml = `
            <button id="btn-undo-action" class="btn btn-sm btn-outline-danger ms-auto" 
                style="border-radius: 20px; padding: 2px 10px; font-size: 0.8rem; white-space: nowrap;">
                <i class="fas fa-undo"></i> DESHACER
            </button>
        `;
    }

    notificationDiv.innerHTML = `
        <div style="flex-shrink: 0;">${icon}</div>
        <span style="flex-grow: 1; line-height: 1.4;">${mensaje}</span>
        ${undoHtml}
        <i class="fas fa-times" style="color: #9ca3af; cursor: pointer; font-size: 0.9rem; margin-left: 10px;" onclick="this.parentElement.remove()"></i>
    `;

    document.body.appendChild(notificationDiv);

    // Handler para Undo
    if (undoData) {
        const btnUndo = notificationDiv.querySelector('#btn-undo-action');
        if (btnUndo) {
            btnUndo.addEventListener('click', async () => {
                // Feedback inmediato
                btnUndo.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ...';
                btnUndo.disabled = true;

                try {
                    const res = await fetch('/api/undo', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(undoData)
                    });
                    const data = await res.json();

                    if (data.success) {
                        notificationDiv.remove();
                        mostrarNotificacion('Acción deshecha correctamente. Verifica el stock si es necesario.', 'warning');

                        // Callback de restauración de datos
                        if (undoData && typeof undoData.restoreCallback === 'function') {
                            undoData.restoreCallback();
                        }

                        // Opcional: Recargar módulo actual
                        const pagina = window.AppState.paginaActual;
                        if (window[`Modulo${pagina.charAt(0).toUpperCase() + pagina.slice(1)}`]?.cargarDatos) {
                            window[`Modulo${pagina.charAt(0).toUpperCase() + pagina.slice(1)}`].cargarDatos();
                        }
                    } else {
                        mostrarNotificacion('No se pudo deshacer: ' + data.error, 'error');
                    }
                } catch (e) {
                    mostrarNotificacion('Error de conexión al deshacer', 'error');
                }
            });
        }
    }

    // Animaciones
    if (!document.querySelector('style#notification-animations')) {
        const style = document.createElement('style');
        style.id = 'notification-animations';
        style.textContent = `
            @keyframes slideInDown {
                from { transform: translate(-50%, -100%); opacity: 0; }
                to { transform: translate(-50%, 0); opacity: 1; }
            }
            @keyframes fadeOutUp {
                from { opacity: 1; transform: translate(-50%, 0); }
                to { opacity: 0; transform: translate(-50%, -20px); }
            }
        `;
        document.head.appendChild(style);
    }

    // Auto eliminar (5s para dar tiempo al Undo)
    setTimeout(() => {
        if (notificationDiv.parentNode) {
            notificationDiv.style.animation = 'fadeOutUp 0.5s forwards';
            setTimeout(() => {
                if (notificationDiv.parentNode) notificationDiv.remove();
            }, 500);
        }
    }, 5000);
}

let gLoaderInterval = null;
let gLoaderCurrentStep = 1;
let gLoaderFinished = false;

// Variables for micro-tips
let gTipsInterval = null;
const microTips = {
  1: ["Conectando con Servidor...", "Abriendo sockets...", "Pidiendo autorización..."],
  2: ["Verificando Permisos...", "Autenticando token...", "Cargando perfiles..."],
  3: ["Sincronizando Catálogo...", "Leyendo códigos...", "Validando existencias...", "Calculando Stock..."],
  4: ["Preparando Interfaces...", "Cargando Dashboards...", "Activando módulos..."]
};

window.mostrarLoaderGlobal = function() {
    const overlay = document.getElementById('global-loader');
    if (!overlay) return;
    
    // Limpieza de estados anteriores
    if (gLoaderInterval) clearInterval(gLoaderInterval);
    if (gTipsInterval) clearInterval(gTipsInterval);

    gLoaderCurrentStep = 1;
    gLoaderFinished = false;
    overlay.style.display = 'flex';
    // Forzar redibujo para reiniciar animación de fade
    void overlay.offsetWidth; 
    overlay.style.opacity = '1';
    
    for (let i = 1; i <= 4; i++) {
        const div = document.getElementById(`g-etapa-${i}`);
        if (!div) continue;
        div.className = i === 1 ? 'loader-etapa etapa-active' : 'loader-etapa etapa-pending';
        const icon = div.querySelector('.l-icon');
        if (icon) icon.textContent = '⏳';
        
        const textSpan = div.querySelector('.l-texto-dinamico');
        if (textSpan && microTips[i]) {
            textSpan.textContent = microTips[i][0];
        }
    }
    
    let tipIndex = 1;
    gTipsInterval = setInterval(() => {
        if (gLoaderCurrentStep <= 4) {
            const activeDiv = document.getElementById(`g-etapa-${gLoaderCurrentStep}`);
            if (activeDiv) {
                const textSpan = activeDiv.querySelector('.l-texto-dinamico');
                if (textSpan && microTips[gLoaderCurrentStep]) {
                    const tipsArr = microTips[gLoaderCurrentStep];
                    textSpan.textContent = tipsArr[tipIndex % tipsArr.length];
                    tipIndex++;
                }
            }
        }
    }, 800);

    gLoaderInterval = setInterval(() => {
        if (gLoaderCurrentStep > 4) {
            clearInterval(gLoaderInterval);
            if (gTipsInterval) clearInterval(gTipsInterval);
            return;
        }

        const prevDiv = document.getElementById(`g-etapa-${gLoaderCurrentStep}`);
        if (prevDiv) {
            prevDiv.className = 'loader-etapa etapa-complete';
            const icon = prevDiv.querySelector('.l-icon');
            if (icon) icon.textContent = '✅';
        }

        gLoaderCurrentStep++;
        tipIndex = 1; 

        if (gLoaderCurrentStep > 4 && gLoaderFinished) {
            cerrarLoaderGlobalUI();
            return;
        }

        if (gLoaderCurrentStep <= 4) {
            const nextDiv = document.getElementById(`g-etapa-${gLoaderCurrentStep}`);
            if (nextDiv) {
                nextDiv.className = 'loader-etapa etapa-active';
                const textSpan = nextDiv.querySelector('.l-texto-dinamico');
                if (textSpan && microTips[gLoaderCurrentStep]) {
                    textSpan.textContent = microTips[gLoaderCurrentStep][0];
                }
            }
        }
    }, 600);
};

window.ocultarLoaderGlobal = function() {
    gLoaderFinished = true;
    if (gLoaderCurrentStep <= 4) {
        if (gLoaderInterval) clearInterval(gLoaderInterval);
        if (gTipsInterval) clearInterval(gTipsInterval); 
        
        gLoaderInterval = setInterval(() => {
            const divInfo = document.getElementById(`g-etapa-${gLoaderCurrentStep}`);
            if (divInfo) {
                divInfo.className = 'loader-etapa etapa-complete';
                const icon = divInfo.querySelector('.l-icon');
                if(icon) icon.textContent = '✅';
                
                const textSpan = divInfo.querySelector('.l-texto-dinamico');
                if (textSpan && microTips[gLoaderCurrentStep]) {
                    textSpan.textContent = microTips[gLoaderCurrentStep][0];
                }
            }
            gLoaderCurrentStep++;
            if (gLoaderCurrentStep > 4) {
                clearInterval(gLoaderInterval);
                cerrarLoaderGlobalUI();
            }
        }, 50); 
    } else {
        cerrarLoaderGlobalUI();
    }
};

function cerrarLoaderGlobalUI() {
    if (gLoaderInterval) clearInterval(gLoaderInterval);
    if (gTipsInterval) clearInterval(gTipsInterval);
    
    // Prevenir time-outs atascados
    gLoaderInterval = null;
    gTipsInterval = null;

    const overlay = document.getElementById('global-loader');
    if (overlay) {
        overlay.style.transition = 'opacity 0.4s ease';
        overlay.style.opacity = '0';
        setTimeout(() => {
            overlay.style.display = 'none';
        }, 400);
    }
}

/**
 * Mostrar/ocultar loading unificado (Híbrido)
 */
function mostrarLoading(mostrar) {
    if (mostrar) {
        window.mostrarLoaderGlobal();
    } else {
        window.ocultarLoaderGlobal();
    }
}

/**
 * Fetch con manejo de errores
 */
async function fetchData(url, options = {}) {
    // silent=true: el caller quiere manejar él mismo un error de negocio
    // esperado (p.ej. STOCK_INSUFICIENTE con su `code`) en vez de que se
    // muestre el toast genérico y se pierda el body -- se le devuelve el
    // JSON de error tal cual en vez de null. Solo aplica a errores HTTP con
    // body JSON válido; fallas de red siguen el camino normal de abajo.
    const silent = options.silent === true;
    try {
        // Inyectar Token de Autenticación
        const token = localStorage.getItem('pwa_token');
        options.headers = options.headers || {};
        if (token && !options.headers['Authorization']) {
            options.headers['Authorization'] = `Bearer ${token}`;
        }

        console.log(`📡 [Fetching]: ${url}`);
        const response = await fetch(url, options);

        if (!response.ok) {
            let errorMsg = `HTTP ${response.status}: ${response.statusText}`;
            let errorBody = null;
            try {
                // Intentar extraer el detalle del error JSON (traceback)
                errorBody = await response.json();
                if (errorBody.traceback) {
                    console.group(`❌ [Backend Error Detail] ${url}`);
                    console.error('Message:', errorBody.message || errorBody.error);
                    console.error('Traceback:\n', errorBody.traceback);
                    console.groupEnd();
                }
                errorMsg = errorBody.message || errorBody.error || errorMsg;
            } catch (e) {
                // Si no es JSON, fallar silenciosamente y usar el error original
            }
            if (silent && errorBody) {
                return errorBody;
            }
            throw new Error(errorMsg);
        }

        const data = await response.json();
        console.log(`✅ [API Success] ${url}`);
        return data;
    } catch (error) {
        console.error(`❌ [API Error] ${url}:`, error);
        mostrarNotificacion(`Error en la solicitud: ${error.message}`, 'error');
        return null;
    }
}

/**
 * Formatear números
 */
function formatNumber(num) {
    if (num === null || num === undefined || isNaN(num)) return '0';
    return parseFloat(num).toLocaleString('es-CO');
}

/**
 * Formatear moneda con 2 decimales fijos
 */
function formatearMoneda(num) {
    if (num === null || num === undefined || isNaN(num)) return '$ 0,00';
    const val = parseFloat(num);
    return '$ ' + val.toLocaleString('es-CO', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

/**
 * Formatear fecha corta
 */
function formatDateShort(fecha) {
    if (!fecha) return '';
    const date = new Date(fecha);
    return date.toLocaleDateString('es-CO');
}

/**
 * Formatear hora
 */
function formatTime(hora) {
    if (!hora) return '00:00';
    if (typeof hora === 'string' && hora.includes('T')) {
        return hora.split('T')[1].substring(0, 5);
    }
    return hora;
}

/**
 * Validar email
 */
function validarEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

/**
 * Obtener estado de stock
 */
function getEstadoStock(stockActual, stockMinimo) {
    stockActual = parseFloat(stockActual || 0);
    stockMinimo = parseFloat(stockMinimo || 0);

    if (stockActual <= 0) {
        return { estado: 'AGOTADO', clase: 'bg-danger' };
    } else if (stockActual < stockMinimo) {
        return { estado: 'BAJO STOCK', clase: 'bg-warning' };
    } else {
        return { estado: 'STOCK OK', clase: 'bg-success' };
    }
}

/**
 * Limpiar formulario
 */
function limpiarFormulario(formId) {
    const form = document.getElementById(formId);
    if (form) {
        form.reset();
        // Restaurar usuario logueado
        if (window.AuthModule) {
            window.AuthModule.autoFillForms();
        }
    }
}

// ============================================================
// GESTIÓN CENTRALIZADA DE IMÁGENES — validarImagen()
// ============================================================

/** URL de fallback única y canónica para toda la app. */
const IMG_FALLBACK = '/static/img/no-image.svg';

/**
 * Aplica el handler de fallback a un elemento <img>.
 *
 * Estrategia de cascada:
 *   1. Intenta cargar la src original del servidor.
 *   2. Si falla (404/CORS/red), muestra IMG_FALLBACK.
 *   3. Marca el elemento con data-img-handled="true" para no
 *      registrar el listener más de una vez.
 *
 * @param {HTMLImageElement} imgElement - El elemento <img> a proteger.
 * @param {string} [sourceUrl]         - URL explícita a asignar (opcional).
 *                                       Si se omite, se usa la src actual.
 */
function validarImagen(imgElement, sourceUrl) {
    if (!(imgElement instanceof HTMLImageElement)) return;
    // Evitar registrar el handler duplicado
    if (imgElement.dataset.imgHandled === 'true') return;
    imgElement.dataset.imgHandled = 'true';

    // Asignar nueva src si se provee
    if (sourceUrl) imgElement.src = sourceUrl;

    imgElement.addEventListener('error', function onImgError() {
        // Evitar bucle infinito si el propio fallback falla
        if (this.src === window.location.origin + IMG_FALLBACK ||
            this.src === IMG_FALLBACK) {
            this.removeEventListener('error', onImgError);
            return;
        }
        this.removeEventListener('error', onImgError);
        this.src = IMG_FALLBACK;
    }, { once: false });
}

/**
 * Aplica validarImagen() a todas las <img> dentro de un nodo raíz.
 * @param {Element|Document} root
 */
function aplicarFallbackImagenes(root) {
    (root === document ? document.querySelectorAll('img') : root.querySelectorAll('img'))
        .forEach(img => validarImagen(img));
}

/**
 * Observer global: protege automáticamente cualquier <img> nueva
 * inyectada por JS (tablas de inventario, sugerencias, tarjetas, etc.)
 */
(function initImageFallbackObserver() {
    // Aplicar a las imágenes ya presentes en el DOM
    document.addEventListener('DOMContentLoaded', () => aplicarFallbackImagenes(document));

    // Observar mutaciones futuras (innerHTML dinámico)
    const observer = new MutationObserver(mutations => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType !== Node.ELEMENT_NODE) continue;
                // Si el nodo mismo es una img
                if (node.tagName === 'IMG') {
                    validarImagen(node);
                }
                // Si el nodo contiene imgs anidadas
                const imgs = node.querySelectorAll ? node.querySelectorAll('img') : [];
                imgs.forEach(img => validarImagen(img));
            }
        }
    });

    observer.observe(document.body || document.documentElement, {
        childList: true,
        subtree: true
    });
})();

// Exponer al scope global para uso explícito en otros módulos
window.validarImagen = validarImagen;
window.aplicarFallbackImagenes = aplicarFallbackImagenes;

/**
 * Convierte 'dd/mm HH:MM' (formato de PulidoService._fmt_hora) en un entero
 * ordenable cronológicamente dentro del mismo año. null si no matchea.
 */
function parseHoraCorta(str) {
    const m = /^(\d{2})\/(\d{2})\s+(\d{2}):(\d{2})$/.exec(str || '');
    if (!m) return null;
    const [, dd, mm, HH, MM] = m.map(Number);
    return ((mm * 31 + dd) * 24 + HH) * 60 + MM;
}
window.parseHoraCorta = parseHoraCorta;

/**
 * Vuelve ordenable por columnas cualquier tabla: cada <th data-sort-key="x">
 * se vuelve clicable y reordena las filas de su <tbody> según el atributo
 * data-sort-x de cada <tr> (numérico si es parseable, alfabético si no).
 * Alterna asc/desc en el mismo click y marca la columna activa con una
 * flechita. Reutilizable en cualquier tabla de la app -- no depende de cómo
 * se generó el HTML de la tabla, solo de esos atributos data-*.
 */
function hacerTablaOrdenable(tabla) {
    if (!tabla || tabla.dataset.ordenableInit === 'true') return;
    tabla.dataset.ordenableInit = 'true';

    const encabezados = Array.from(tabla.querySelectorAll('thead th[data-sort-key]'));
    encabezados.forEach(th => {
        th.style.cursor = 'pointer';
        th.style.userSelect = 'none';
        if (!th.querySelector('.sort-arrow')) {
            th.insertAdjacentHTML('beforeend', ' <span class="sort-arrow" style="opacity:.35;font-size:.7em;display:inline-block;width:.9em;"></span>');
        }
        th.addEventListener('click', () => {
            const key = th.getAttribute('data-sort-key');
            const dirNueva = th.getAttribute('data-sort-dir') === 'asc' ? 'desc' : 'asc';

            encabezados.forEach(h => {
                h.removeAttribute('data-sort-dir');
                const arrow = h.querySelector('.sort-arrow');
                if (arrow) { arrow.textContent = ''; arrow.style.opacity = '.35'; }
            });
            th.setAttribute('data-sort-dir', dirNueva);
            const arrow = th.querySelector('.sort-arrow');
            if (arrow) { arrow.textContent = dirNueva === 'asc' ? '▲' : '▼'; arrow.style.opacity = '1'; }

            const tbody = tabla.querySelector('tbody');
            if (!tbody) return;
            const attr = `data-sort-${key}`;
            const filas = Array.from(tbody.querySelectorAll('tr'));
            filas.sort((a, b) => {
                const va = a.getAttribute(attr);
                const vb = b.getAttribute(attr);
                const aVacio = va === null || va === '';
                const bVacio = vb === null || vb === '';
                // Filas sin dato en esta columna siempre al final, sea cual sea la dirección.
                if (aVacio && bVacio) return 0;
                if (aVacio) return 1;
                if (bVacio) return -1;
                const na = Number(va), nb = Number(vb);
                const cmp = (!Number.isNaN(na) && !Number.isNaN(nb))
                    ? na - nb
                    : String(va).localeCompare(String(vb), 'es', { numeric: true, sensitivity: 'base' });
                return dirNueva === 'asc' ? cmp : -cmp;
            });
            filas.forEach(fila => tbody.appendChild(fila));
        });
    });
}
window.hacerTablaOrdenable = hacerTablaOrdenable;
