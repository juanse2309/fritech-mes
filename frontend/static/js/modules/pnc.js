// ============================================
// pnc.js - Lógica de PNC (Producto No Conforme) - NAMESPACED
// ============================================

const ModuloPNC = {
    /**
     * Cargar datos de PNC
     */
    cargarDatos: async function () {
        try {
            console.log('📦 [PNC] Cargando datos...');
            mostrarLoading(true);

            // Fecha actual
            const hoy = new Date().toISOString().split('T')[0];
            const fechaInput = document.getElementById('pnc-manual-fecha');
            if (fechaInput) fechaInput.value = hoy;

            await this.cargarResponsables();

            console.log('✅ [PNC] Datos cargados');
            mostrarLoading(false);
        } catch (error) {
            console.error('Error [PNC] cargarDatos:', error);
            mostrarLoading(false);
        }
    },

    /**
     * Catálogo de responsables para el selector obligatorio del registro
     * manual de PNC. Sin esto la merma quedaba anónima (sin persona
     * atribuible), rompiendo la trazabilidad que exige el área de calidad.
     */
    cargarResponsables: async function () {
        try {
            const select = document.getElementById('pnc-manual-responsable');
            if (!select) return;

            const usuarioActual = (window.AuthModule && AuthModule.getUsuarioActual()) || '';
            const responsables = await fetchData('/api/obtener_responsables');
            const nombres = Array.isArray(responsables) ? responsables.map(r => typeof r === 'object' ? r.nombre : r) : [];

            // Defensivo: si el catálogo no trae al usuario activo (fallo de red,
            // catálogo vacío, etc.) se agrega igual, para que el select nunca
            // quede sin una opción válida que bloquee el registro del PNC.
            if (usuarioActual && !nombres.includes(usuarioActual)) {
                nombres.unshift(usuarioActual);
            }

            select.innerHTML = '<option value="">Selecciona...</option>';
            nombres.forEach(nombre => {
                const opt = document.createElement('option');
                opt.value = nombre;
                opt.textContent = nombre;
                select.appendChild(opt);
            });

            if (usuarioActual && nombres.includes(usuarioActual)) {
                select.value = usuarioActual;
            }
        } catch (error) {
            console.error('Error [PNC] cargarResponsables:', error);
        }
    },

    /**
     * Autocomplete de Producto (Aislado)
     */
    initAutocompleteProducto: function () {
        const input = document.getElementById('pnc-manual-producto');
        const suggestionsDiv = document.getElementById('pnc-manual-producto-suggestions');

        console.log('🔍 [PNC] initAutocomplete - Context:', { input: !!input, suggestions: !!suggestionsDiv });

        if (!input || !suggestionsDiv) return;

        let debounceTimer;

        input.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            const query = e.target.value.trim().toLowerCase();

            if (query.length < 2) {
                suggestionsDiv.classList.remove('active');
                suggestionsDiv.style.display = 'none';
                return;
            }

            debounceTimer = setTimeout(() => {
                let products = [];
                if (window.AppState && window.AppState.sharedData && Array.isArray(window.AppState.sharedData.productos)) {
                    products = window.AppState.sharedData.productos;
                }

                console.log(`🔍 [PNC] Buscando "${query}" en ${products.length} productos`);

                const terms = query.split(/\s+/).filter(t => t.length > 0);
                const resultados = products.filter(prod => {
                    const cod = String(prod.codigo_sistema || prod.codigo || '').toLowerCase();
                    const desc = String(prod.descripcion || '').toLowerCase();
                    return terms.every(term => 
                        cod.includes(term) || 
                        desc.includes(term) ||
                        cod.replace(/[-\s]/g, '').includes(term.replace(/[-\s]/g, ''))
                    );
                }).slice(0, 15);

                this.renderSuggestions(suggestionsDiv, resultados, (item) => {
                    input.value = item.codigo_sistema || item.codigo || '';
                    suggestionsDiv.classList.remove('active');
                    suggestionsDiv.style.display = 'none';
                    console.log('✅ [PNC] Seleccionado:', input.value);
                });
            }, 300);
        });

        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
                suggestionsDiv.style.display = 'none';
            }
        });
    },

    /**
     * Renderizador de sugerencias local
     */
    renderSuggestions: function (container, items, onSelect) {
        renderProductSuggestions(container, items, onSelect);
    },

    /**
     * Registrar PNC
     */
    registrar: async function () {
        try {
            mostrarLoading(true);

            const datos = {
                fecha: document.getElementById('pnc-manual-fecha')?.value || '',
                codigo_producto: document.getElementById('pnc-manual-producto')?.value || '',
                cantidad: document.getElementById('pnc-manual-cantidad')?.value || '0',
                criterio: document.getElementById('pnc-manual-criterio')?.value || '',
                notas: document.getElementById('pnc-manual-ensamble')?.value || '',
                responsable: document.getElementById('pnc-manual-responsable')?.value || (window.AuthModule ? AuthModule.getUsuarioActual() : '')
            };

            console.log('📤 [PNC] ENVIANDO:', datos);

            if (!datos.codigo_producto?.trim()) {
                mostrarNotificacion('⚠️ Ingresa código del producto', 'error');
                mostrarLoading(false);
                return;
            }

            if (!datos.responsable?.trim()) {
                mostrarNotificacion('⚠️ Selecciona la persona responsable', 'error');
                mostrarLoading(false);
                return;
            }

            const response = await fetch('/api/pnc', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(datos)
            });

            const resultado = await response.json();

            if (response.ok && resultado.success) {
                mostrarNotificacion(`✅ ${resultado.mensaje || 'PNC registrado'}`, 'success');
                document.getElementById('form-manual-pnc')?.reset();
                this.cargarDatos();
            } else {
                mostrarNotificacion(`❌ ${resultado.error || 'Error'}`, 'error');
            }
        } catch (error) {
            console.error('Error [PNC] registrar:', error);
            mostrarNotificacion(`Error: ${error.message}`, 'error');
        } finally {
            mostrarLoading(false);
        }
    },

    /**
     * Inicialización del módulo
     */
    inicializar: function () {
        console.log('🔧 [PNC] Inicializando...');
        this.cargarDatos();
        // initAutocompleteProducto liga addEventListener sobre el input y
        // sobre `document` -- inicializar() corre en cada visita a la
        // página, así que sin este guard cada visita apila otro listener
        // (cada tecla dispara N búsquedas y cada click en la app corre N
        // checks de "cerrar sugerencias").
        if (!this._autocompleteListo) {
            this._autocompleteListo = true;
            this.initAutocompleteProducto();
        }

        const form = document.getElementById('form-manual-pnc');
        if (form) {
            form.onsubmit = (e) => {
                e.preventDefault();
                this.registrar();
            };
        }

        // Configurar Smart Enter
        if (window.ModuloUX && window.ModuloUX.setupSmartEnter) {
            window.ModuloUX.setupSmartEnter({
                inputIds: [
                    'pnc-manual-producto', 'pnc-manual-cantidad', 'pnc-manual-motivo', 'pnc-manual-maquina', 'pnc-manual-ensamble'
                ],
                actionBtnId: 'btn-submit-pnc',
                autocomplete: {
                    inputId: 'pnc-manual-producto',
                    suggestionsId: 'pnc-manual-producto-suggestions'
                }
            });
        }

        console.log('✅ [PNC] Módulo inicializado');
    }
};

// Exportación global
window.ModuloPNC = ModuloPNC;
window.initPnc = () => ModuloPNC.inicializar();
