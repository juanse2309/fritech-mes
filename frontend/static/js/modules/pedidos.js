
// pedidos.js - Módulo de Gestión de Pedidos

const ModuloPedidos = {
    listaProductos: [], // Array de productos: [{codigo, descripcion, cantidad, precio_unitario, stock_disponible}]
    clientesData: [],  // Cache de clientes
    productosData: [], // Cache de productos
    clienteSeleccionado: null, // {nombre, nit}
    ultimoIdRegistrado: null, // ID generado por el servidor
    idPedidoEdicion: null,   // ID del pedido que se está editando
    usdConversionPendiente: null, // {precio_usd, trm_aplicada} tras "Consultar TRM", hasta que se añada el item

    _productosLoading: false,
    _productosReady: false,

    _emitProductosReady: function () {
        this._productosLoading = false;
        this._productosReady = true;
        document.dispatchEvent(new CustomEvent('pedidos-productos-ready'));
    },

    _tryHydrateProductosFromAppState: function () {
        try {
            const isFrimetals = (window.AppState?.user?.division === 'FRIMETALS');
            const shared = window.AppState?.sharedData?.productos;
            if (!isFrimetals && !this._productosReady && Array.isArray(shared) && shared.length > 0) {
                this.productosData = shared;
                this._emitProductosReady();
                console.log("✅ Hidratando productos desde AppState en Pedidos:", this.productosData.length);
                return true;
            }
        } catch (e) {
            console.warn("⚠️ No se pudo hidratar productos desde AppState:", e);
        }
        return false;
    },


    init: function () {
        if (this._initDone) return;
        this._initDone = true;

        console.log("🛒 Inicializando Módulo Pedidos (Listeners)...");

        // Registrar persistencia Juan Sebastian Request
        if (window.FormHelpers) {
            window.FormHelpers.registrarPersistencia('form-pedidos');
        }

        // 1. Inicializar autocomplete
        this.inicializarAutocompleteCliente();
        this.inicializarAutocompleteProducto();

        // 2. Botón añadir item
        const btnAgregar = document.getElementById('btn-agregar-item');
        if (btnAgregar) {
            btnAgregar.addEventListener('click', () => this.agregarItemAlCarrito());
        }

        // 2.a Enter en Producto/Cantidad/Precio: sin esto, el Enter nativo del
        // navegador dispara el submit del <form> (hay un button[type=submit]
        // más abajo, "Registrar Pedido") en vez de solo añadir el item a la
        // lista -- abre el modal de "¿Confirmar Registro?" a medio llenar un
        // producto, o directamente registra el pedido sin el item que se
        // estaba tipeando todavía. Enter aquí debe comportarse como
        // "Añadir al Pedido", nunca como enviar el formulario completo.
        const inputProducto = document.getElementById('ped-producto');
        const inputCantidad = document.getElementById('ped-cantidad');
        const inputPrecioItem = document.getElementById('ped-precio');
        const onEnterAgregarItem = (focusSiguiente) => (e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            if (focusSiguiente) {
                focusSiguiente.focus();
                if (focusSiguiente.select) focusSiguiente.select();
            } else {
                this.agregarItemAlCarrito();
            }
        };
        if (inputProducto && inputCantidad) {
            inputProducto.addEventListener('keydown', onEnterAgregarItem(inputCantidad));
        }
        if (inputCantidad && inputPrecioItem) {
            inputCantidad.addEventListener('keydown', onEnterAgregarItem(inputPrecioItem));
        }
        if (inputPrecioItem) {
            inputPrecioItem.addEventListener('keydown', onEnterAgregarItem(null));
        }

        // 2.b Checkbox "Exportación (USD)" y botón "Consultar TRM"
        const chkUsd = document.getElementById('ped-es-usd');
        if (chkUsd) {
            chkUsd.addEventListener('change', () => this.toggleModoUsd());
        }
        const btnTrm = document.getElementById('btn-consultar-trm');
        if (btnTrm) {
            btnTrm.addEventListener('click', () => this.consultarTRM());
        }

        // 3. Submit del formulario
        const form = document.getElementById('form-pedidos');
        if (form) {
            form.addEventListener('submit', (e) => this.registrarPedido(e));
        }

        // 4. Listener para descuento global
        const descuentoGlobal = document.getElementById('ped-descuento-global');
        if (descuentoGlobal) {
            descuentoGlobal.addEventListener('input', () => this.calcularTotalPedido());
        }

        // 5. Inicializar fecha
        const inputFecha = document.getElementById('ped-fecha');
        if (inputFecha) {
            inputFecha.valueAsDate = new Date();
        }

        // Si AppState.sharedData se hidrata después (app.js), tomar productos y refrescar autocomplete
        if (!this._sharedDataReadyListenerAdded) {
            this._sharedDataReadyListenerAdded = true;
            document.addEventListener('shared-data-ready', () => {
                if (this._tryHydrateProductosFromAppState()) {
                    // Si el usuario está escribiendo, refrescar sugerencias
                    const inputProd = document.getElementById('ped-producto');
                    const suggestionsDiv = document.getElementById('ped-producto-suggestions');
                    const q = inputProd?.value?.trim?.() || '';
                    if (suggestionsDiv && q.length >= 2) {
                        this.buscarProductos(q, suggestionsDiv);
                    }
                }
            }, { once: true });
        }

        // 6. Configurar gestión de Enter (Smart Enter)
        if (window.ModuloUX && window.ModuloUX.setupSmartEnter) {
            window.ModuloUX.setupSmartEnter({
                inputIds: [
                    'ped-fecha', 'ped-cliente', 'ped-pago', 'ped-descuento-global',
                    'ped-producto', 'ped-cantidad'
                ],
                actionBtnId: 'btn-agregar-item',
                autocomplete: {
                    inputId: 'ped-producto',
                    suggestionsId: 'ped-producto-suggestions'
                }
            });

            // Autocomplete para cliente
            window.ModuloUX.setupSmartEnter({
                inputIds: ['ped-cliente'],
                autocomplete: {
                    inputId: 'ped-cliente',
                    suggestionsId: 'ped-cliente-suggestions'
                }
            });
        }
    },

    cargarDatosIniciales: async function () {
        console.log("📦 Cargando datos para Pedidos...");

        // Multi-Tenant: Frimetals users MUST fetch fresh data from tenant-aware API
        // (AppState.sharedData may contain cached Friparts data)
        const isFrimetals = (window.AppState?.user?.division === 'FRIMETALS');
        if (isFrimetals) {
            console.log("🏢 [Tenant] División FRIMETALS detectada → Forzando fetch desde API tenant-aware");
        }

        // Cargar Clientes
        try {
            if (!isFrimetals && window.AppState && window.AppState.sharedData.clientes.length > 0) {
                this.clientesData = window.AppState.sharedData.clientes;
            } else {
                const response = await fetch('/api/obtener_clientes');
                this.clientesData = await response.json();
            }
            console.log("✅ Clientes cargados:", this.clientesData.length);
        } catch (e) {
            console.error("Error cargando clientes:", e);
        }

        // Cargar Productos - Para Frimetals, siempre ir al API (tenant-aware por sesión)
        try {
            if (!isFrimetals && window.AppState && window.AppState.sharedData.productos.length > 0) {
                this.productosData = window.AppState.sharedData.productos;
                console.log("✅ Usando productos desde AppState en Pedidos:", this.productosData.length);
                this._emitProductosReady();
            } else {
                console.log("🔄 Solicitando productos al servidor (tenant-aware)...");
                this._productosLoading = true;
                this._productosReady = false;
                
                // LINEA CLAVE: Pasar división a la API para activar el switch de tabla
                const division = window.AppState?.user?.division || (isFrimetals ? 'FRIMETALS' : 'FRIPARTS');
                console.log(`📡 [Pedidos] Cargando catálogo para división: ${division}`);
                const respProd = await fetch(`/api/productos/listar?division=${division.toLowerCase()}`);
                
                const productosResp = await respProd.json();
                const rawList = Array.isArray(productosResp) ? productosResp : (productosResp.items || []);
                this.productosData = rawList.map(p => {
                    const pRaw = p.precio !== undefined ? p.precio : (p.PRECIO || 0);
                    let precioFinal = 0;
                    
                    if (isFrimetals) {
                        // Ya viene como INTEGER limpio desde el backend
                        precioFinal = parseInt(pRaw) || 0;
                    } else {
                        const pClean = String(pRaw).replace(/[^0-9.]/g, '');
                        precioFinal = parseFloat(pClean) || 0;
                    }
                    
                    return {
                        codigo_sistema: p.codigo || p.codigo_sistema || '',
                        codigo: p.codigo || p.codigo_sistema || '',
                        descripcion: p.descripcion || p.nombre_producto || '',
                        imagen: p.imagen || '',
                        precio: precioFinal,
                        stock_por_pulir: p.stock_por_pulir || 0,
                        stock_terminado: p.stock_terminado || 0,
                        stock_disponible: p.stock_disponible !== undefined ? p.stock_disponible : (p.stock || 0),
                        stock_total: p.existencias_totales || p.stock_total || 0,
                    };
                });
                this._emitProductosReady();
            }
            const conPrecio = this.productosData.filter(p => p.precio > 0).length;
            console.log(`✅ Productos listos en Pedidos: ${this.productosData.length} total, ${conPrecio} con precio`);
        } catch (e) {
            console.error("Error cargando productos para Pedidos:", e);
            this._productosLoading = false;
            this._productosReady = false;
        }

        // Pre-fill vendedor
        this.actualizarVendedor();
    },

    inicializarAutocompleteCliente: function () {
        const input = document.getElementById('ped-cliente');
        const suggestionsDiv = document.getElementById('ped-cliente-suggestions');

        if (!input || !suggestionsDiv) return;

        let debounceTimer;

        input.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            const query = e.target.value.trim();

            if (query.length < 2) {
                suggestionsDiv.classList.remove('active');
                return;
            }

            debounceTimer = setTimeout(() => {
                this.buscarClientes(query, suggestionsDiv);
            }, 300);
        });

        // Cerrar sugerencias al hacer clic fuera
        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });
    },

    // Helper para normalizar texto (quitar tildes y minusculas)
    normalizeString: function (str) {
        return String(str || '').normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    },

    buscarClientes: function (query, suggestionsDiv) {
        console.log(`🔍 [DEBUG-CLIENTES] Buscando: "${query}" | Clientes en memoria: ${this.clientesData?.length}`);
        if (this.clientesData?.length > 0) {
            console.log('🔍 [DEBUG-CLIENTES] Muestra 1er cliente:', this.clientesData[0]);
        }

        const queryNorm = this.normalizeString(query);

        const resultados = this.clientesData.filter(cliente => {
            const nombreNorm = this.normalizeString(cliente.nombre);
            const matches = nombreNorm.includes(queryNorm) ||
                (cliente.nit && String(cliente.nit).includes(query));
            return matches;
        });

        console.log(`🔍 [DEBUG-CLIENTES] Resultados encontrados: ${resultados.length}`);

        if (resultados.length === 0) {
            suggestionsDiv.innerHTML = '<div class="suggestion-item">No se encontraron clientes</div>';
            suggestionsDiv.classList.add('active');
            return;
        }

        suggestionsDiv.innerHTML = resultados.map(cliente => `
            <div class="suggestion-item" 
                data-nombre="${cliente.nombre}" 
                data-nit="${cliente.nit || ''}"
                data-direccion="${cliente.direccion || ''}"
                data-telefonos="${cliente.telefonos || ''}"
                data-ciudad="${cliente.ciudad || ''}">
                <strong>${cliente.nombre}</strong>
                ${cliente.nit ? `<br><small>NIT: ${cliente.nit}</small>` : ''}
                <br><small style="color: #6b7280;"><i class="fas fa-map-marker-alt"></i> ${cliente.direccion || 'Sin dirección'} - ${cliente.ciudad || 'Sin ciudad'}</small>
            </div>
        `).join('');

        // Event listeners para selección
        suggestionsDiv.querySelectorAll('.suggestion-item').forEach(item => {
            item.addEventListener('click', () => {
                this.seleccionarCliente({
                    nombre: item.dataset.nombre,
                    nit: item.dataset.nit,
                    direccion: item.dataset.direccion,
                    telefonos: item.dataset.telefonos,
                    ciudad: item.dataset.ciudad
                });
                suggestionsDiv.classList.remove('active');
            });
        });

        suggestionsDiv.classList.add('active');
    },

    seleccionarCliente: function (cliente) {
        this.clienteSeleccionado = cliente;
        document.getElementById('ped-cliente').value = cliente.nombre;
        document.getElementById('ped-nit').value = cliente.nit || '';
        document.getElementById('ped-direccion').value = cliente.direccion || '';
        document.getElementById('ped-ciudad').value = cliente.ciudad || '';
        console.log("🔄 Cliente seleccionado con sede:", cliente);
    },

    inicializarAutocompleteProducto: function () {
        const input = document.getElementById('ped-producto');
        const suggestionsDiv = document.getElementById('ped-producto-suggestions');

        if (!input || !suggestionsDiv) return;

        let debounceTimer;

        input.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            const query = e.target.value.trim();

            if (query.length < 2) {
                suggestionsDiv.classList.remove('active');
                return;
            }

            debounceTimer = setTimeout(() => {
                this.buscarProductos(query, suggestionsDiv);
            }, 300);
        });

        // Cerrar sugerencias al hacer clic fuera
        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });
    },

    buscarProductos: function (query, suggestionsDiv) {
        // UX: mientras la data está cargando, no mostrar "no se encontraron"
        if ((this._productosLoading || !this._productosReady) && (!this.productosData || this.productosData.length === 0)) {
            suggestionsDiv.innerHTML = `
                <div class="suggestion-item text-muted">
                    <i class="fas fa-spinner fa-spin me-2"></i> Cargando productos...
                </div>
            `;
            suggestionsDiv.classList.add('active');

            // Refrescar una sola vez cuando termine la carga (sin apilar listeners)
            if (!this._productosAutocompleteRefreshQueued) {
                this._productosAutocompleteRefreshQueued = true;
                document.addEventListener('pedidos-productos-ready', () => {
                    this._productosAutocompleteRefreshQueued = false;
                    const inputProd = document.getElementById('ped-producto');
                    const div = document.getElementById('ped-producto-suggestions');
                    const q = inputProd?.value?.trim?.() || '';
                    if (div && q.length >= 2) this.buscarProductos(q, div);
                }, { once: true });
            }
            return;
        }

        const queryNorm = this.normalizeString(query);
        const resultados = this.productosData.filter(prod => {
            const codigoNorm = this.normalizeString(prod.codigo_sistema || prod.codigo || '');
            const descNorm = this.normalizeString(prod.descripcion);
            return codigoNorm.includes(queryNorm) || descNorm.includes(queryNorm);
        });

        if (resultados.length === 0) {
            suggestionsDiv.innerHTML = `
                <div class="suggestion-item text-muted">No se encontraron productos</div>
                <div class="suggestion-item text-primary fw-bold border-top mt-1" 
                     onclick="ModuloPedidos.abrirModalCrearProducto('${query.toUpperCase()}')">
                    <i class="fas fa-plus-circle me-2"></i> Crear "${query.toUpperCase()}" como nuevo
                </div>
            `;
            suggestionsDiv.classList.add('active');
            return;
        }

        renderProductSuggestions(suggestionsDiv, resultados.slice(0, 10), (item) => {
            const codigoDisplay = item.codigo_sistema || item.codigo || '';
            document.getElementById('ped-producto').value = `${codigoDisplay} - ${item.descripcion}`;

            // Juan Sebastian: Automatización de carga de precios simplificada
            let precioUnitario = 0;
            if (window.AppState?.user?.division === 'FRIMETALS') {
                precioUnitario = parseInt(item.precio) || 0;
            } else {
                const pClean = String(item.precio || 0).replace(/[^0-9.]/g, '');
                precioUnitario = parseFloat(pClean) || 0;
            }

            const inputPrecio = document.getElementById('ped-precio');
            if (inputPrecio) {
                inputPrecio.value = precioUnitario;
                console.log(`💰 Precio automático: ${precioUnitario}`);
            }

            this.productoSeleccionado = item;
            if (precioUnitario === 0) {
                console.warn("⚠️ Este producto no tiene precio en la tabla de METALES.");
            }
            // Saltar automáticamente al campo cantidad
            const campoCantidad = document.getElementById('ped-cantidad');
            if (campoCantidad) {
                campoCantidad.focus();
                campoCantidad.select();
            }
        });
    },

    actualizarVendedor: function () {
        const inputVendedor = document.getElementById('ped-vendedor');
        if (!inputVendedor) {
            console.warn('⚠️  Campo vendedor no encontrado en el DOM');
            return;
        }

        let nombreVendedor = null;

        if (window.AppState && window.AppState.user && window.AppState.user.name) {
            nombreVendedor = window.AppState.user.name;
        } else if (window.AuthModule && window.AuthModule.currentUser && window.AuthModule.currentUser.nombre) {
            nombreVendedor = window.AuthModule.currentUser.nombre;
        } else {
            try {
                const storedUser = sessionStorage.getItem('friparts_user');
                if (storedUser) {
                    const userData = JSON.parse(storedUser);
                    nombreVendedor = userData.nombre;
                }
            } catch (e) {
                console.error('Error parseando sessionStorage:', e);
            }
        }

        if (nombreVendedor) {
            const wasDifferent = (inputVendedor.value !== nombreVendedor);
            inputVendedor.value = nombreVendedor;
            inputVendedor.readOnly = true;
            if (wasDifferent || this._lastVendedorAsignado !== nombreVendedor) {
                console.log(`✅ Vendedor asignado: ${nombreVendedor}`);
            }
            this._lastVendedorAsignado = nombreVendedor;
        } else {
            if (!this._vendedorPendienteLogged) {
                console.log('ℹ️  Vendedor pendiente: esperando login de usuario');
                this._vendedorPendienteLogged = true;
            }

            // Fallback: reintentar cuando el usuario esté listo (no apilar listeners)
            if (!this._userReadyListenerAdded) {
                this._userReadyListenerAdded = true;
                document.addEventListener('user-ready', () => {
                    console.log("👤 Evento user-ready recibido en Pedidos");
                    this.actualizarVendedor();
                }, { once: true });
            }

            // Polling fallback (max 5 seconds) - una sola vez por ciclo de vida del módulo
            if (!this._vendedorPollingStarted) {
                this._vendedorPollingStarted = true;
                let attempts = 0;
                const interval = setInterval(() => {
                    attempts++;
                    if (window.AppState?.user?.name) {
                        this.actualizarVendedor();
                        clearInterval(interval);
                    }
                    if (attempts > 10) clearInterval(interval);
                }, 500);
            }
        }
    },



    /**
     * Cargar un pedido por su ID para edición (Proactivo)
     * @param {string} idParam - Opcional, ID del pedido a cargar
     */
    cargarPedidoPorId: async function (idParam = null) {
        const inputId = document.getElementById('ped-load-id');
        const id = idParam || (inputId ? inputId.value.trim().toUpperCase() : null);

        if (!id) {
            if (!idParam) mostrarNotificacion('Ingrese un ID de pedido', 'warning');
            return;
        }

        try {
            console.log(`🔍 [Pedidos] Cargando pedido: ${id}...`);
            // Mostrar loading local si es posible
            if (window.mostrarLoading) window.mostrarLoading(true);

            const response = await fetch(`/api/pedidos/detalle/${id}`, {
                method: 'GET',
                cache: 'no-store',
                headers: {
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                    'Expires': '0'
                }
            });
            const result = await response.json();

            if (result.success) {
                this.poblarFormularioConPedido(result.data.pedido);
                if (inputId) inputId.value = '';

                // Si fue proactivo (desde Almacen), dar bienvenida visual
                const msg = idParam ? `Pedido ${id} cargado proactivamente desde Almacén` : `Pedido ${id} cargado correctamente`;
                mostrarNotificacion(msg, 'success');
            } else {
                mostrarNotificacion(result.error || 'Pedido no encontrado', 'error');
            }
        } catch (error) {
            console.error('Error cargando pedido:', error);
            mostrarNotificacion('Error de conexión al cargar pedido', 'error');
        } finally {
            if (window.mostrarLoading) window.mostrarLoading(false);
        }
    },

    poblarFormularioConPedido: function (pedido) {
        console.log("📄 Poblando formulario con:", pedido);

        // 1. Limpiar estado actual
        this.listaProductos = [];
        this.idPedidoEdicion = pedido.id_pedido;

        // 2. Poblar cabecera
        document.getElementById('ped-fecha').value = pedido.fecha;
        document.getElementById('ped-vendedor').value = pedido.vendedor;
        document.getElementById('ped-cliente').value = pedido.cliente;
        document.getElementById('ped-nit').value = pedido.nit || '';
        document.getElementById('ped-direccion').value = pedido.direccion || '';
        document.getElementById('ped-ciudad').value = pedido.ciudad || '';
        document.getElementById('ped-pago').value = pedido.forma_pago || 'Contado';
        document.getElementById('ped-descuento-global').value = pedido.descuento_global || 0;
        document.getElementById('ped-observaciones').value = pedido.observaciones || '';
        const chkExport = document.getElementById('ped-es-exportacion');
        if (chkExport) chkExport.checked = !!pedido.es_exportacion;

        // Establecer cliente seleccionado para las validaciones
        this.clienteSeleccionado = {
            nombre: pedido.cliente,
            nit: pedido.nit,
            direccion: pedido.direccion,
            ciudad: pedido.ciudad
        };

        // 3. Cargar productos (conviertiendo de la estructura del backend)
        this.listaProductos = pedido.productos.map(p => ({
            id_sql: p.id_sql, // Persistir ID único de BD
            codigo: p.codigo,
            descripcion: p.descripcion,
            cantidad: p.cantidad,
            precio_unitario: p.precio_unitario,
            precio_usd: p.precio_usd || null,
            trm_aplicada: p.trm_aplicada || null,
            stock_disponible: 0
        }));

        // 4. Actualizar UI
        this.renderizarTablaItems();
        this.calcularTotalPedido();

        // 5. Mostrar indicador de edición
        const indicator = document.getElementById('edit-mode-indicator');
        const spanId = document.getElementById('active-edit-id');
        if (indicator && spanId) {
            indicator.style.display = 'block';
            spanId.textContent = pedido.id_pedido;
        }

        // 6. Cambiar texto del botón de registro
        const btnSubmit = document.querySelector('#form-pedidos button[type="submit"]');
        if (btnSubmit) {
            btnSubmit.innerHTML = '<i class="fas fa-save"></i> Actualizar Pedido';
            btnSubmit.style.background = 'linear-gradient(135deg, #f59e0b 0%, #d97706 100%)';
        }

        window.scrollTo({ top: 0, behavior: 'smooth' });

        // 7. Cargar y renderizar historial de despachos
        this.cargarHistorialDespachos(pedido.id_pedido);
    },

    cargarHistorialDespachos: async function (idPedido) {
        try {
            const response = await fetch(`/api/pedidos/${idPedido}/despachos`);
            const data = await response.json();
            
            // Buscar contenedor de la tabla de items o el formulario para inyectar después de él
            let contenedorPadre = document.getElementById('historial-despachos-container');
            
            // Si no existe, crearlo dinámicamente y colocarlo al final del formulario
            if (!contenedorPadre) {
                const formPedidos = document.getElementById('form-pedidos');
                if (formPedidos) {
                    contenedorPadre = document.createElement('div');
                    contenedorPadre.id = 'historial-despachos-container';
                    contenedorPadre.className = 'mt-4 w-100';
                    formPedidos.appendChild(contenedorPadre);
                } else {
                    return; // No se encontró dónde inyectar
                }
            }

            if (!data.success || !data.data?.despachos || data.data.despachos.length === 0) {
                contenedorPadre.innerHTML = `
                    <div class="card bg-light mt-3 border-0 shadow-sm" id="seccion-historial-despachos">
                        <div class="card-body text-center text-muted">
                            <i class="fas fa-box-open fa-2x mb-2 text-secondary"></i>
                            <h6 class="mb-0">📦 Aún no se registran despachos físicos para este pedido.</h6>
                        </div>
                    </div>
                `;
                return;
            }

            const filasTabla = data.data.despachos.map(d => `
                <tr>
                    <td><span class="badge bg-secondary">${d.fecha}</span></td>
                    <td class="fw-bold">${d.id_codigo}</td>
                    <td class="text-end fw-bold text-success">+${d.cantidad_enviada}</td>
                    <td>${d.transportadora || 'N/A'} ${d.guia ? `<br><small class="text-muted">Guía: ${d.guia}</small>` : ''}</td>
                    <td>${d.responsable || 'Sistema'}</td>
                </tr>
            `).join('');

            contenedorPadre.innerHTML = `
                <div class="card mt-4 border-0 shadow-sm" id="seccion-historial-despachos">
                    <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center">
                        <h6 class="mb-0"><i class="fas fa-truck-loading me-2"></i>Historial de Despachos</h6>
                        <span class="badge bg-primary rounded-pill">${data.data.despachos.length} envíos</span>
                    </div>
                    <div class="card-body p-0">
                        <div class="table-responsive">
                            <table class="table table-sm table-striped table-hover mb-0">
                                <thead class="table-light">
                                    <tr>
                                        <th>Fecha</th>
                                        <th>Código Producto</th>
                                        <th class="text-end">Cantidad</th>
                                        <th>Transportadora</th>
                                        <th>Responsable</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${filasTabla}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
        } catch (error) {
            console.error("Error cargando historial de despachos:", error);
            // El catch previene que falle el flujo de renderizado principal del pedido
        }
    },

    cancelarEdicion: function () {
        this.idPedidoEdicion = null;
        limpiarFormulario('form-pedidos');
        this.listaProductos = [];
        this.clienteSeleccionado = null;
        this.renderizarTablaItems();
        this.calcularTotalPedido();

        document.getElementById('edit-mode-indicator').style.display = 'none';

        const btnSubmit = document.querySelector('#form-pedidos button[type="submit"]');
        if (btnSubmit) {
            btnSubmit.innerHTML = '<i class="fas fa-paper-plane"></i> Registrar Pedido';
            btnSubmit.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';
        }

        document.getElementById('ped-total').textContent = '$ 0.00';
        this.actualizarVendedor();
        mostrarNotificacion('Edición cancelada', 'info');
    },


    toggleModoUsd: function () {
        const esUsd = document.getElementById('ped-es-usd')?.checked;
        const btnTrm = document.getElementById('btn-consultar-trm');
        const infoEl = document.getElementById('ped-trm-info');

        // Cambiar de modo invalida cualquier conversión ya calculada
        this.usdConversionPendiente = null;
        if (infoEl) infoEl.style.display = 'none';
        if (btnTrm) btnTrm.style.display = esUsd ? 'inline-block' : 'none';
    },

    consultarTRM: async function () {
        const precioInput = document.getElementById('ped-precio');
        const usdValue = parseFloat(precioInput.value);
        if (!usdValue || usdValue <= 0) {
            mostrarNotificacion('Ingrese el precio en dólares antes de consultar la TRM', 'warning');
            return;
        }

        const btn = document.getElementById('btn-consultar-trm');
        const infoEl = document.getElementById('ped-trm-info');
        const textoOriginal = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Consultando...';

        try {
            const response = await fetch('/api/pedidos/trm_actual');
            const result = await response.json();
            if (!response.ok || !result.success) {
                throw new Error(result.message || 'No fue posible consultar la TRM');
            }

            const trm = parseFloat(result.data.trm);
            const precioCop = Math.round(usdValue * trm * 100) / 100;

            this.usdConversionPendiente = { precio_usd: usdValue, trm_aplicada: trm };
            precioInput.value = precioCop.toFixed(2);

            if (infoEl) {
                infoEl.style.display = 'block';
                infoEl.textContent = `USD $${usdValue.toFixed(2)} × TRM $${trm.toFixed(2)} = $${precioCop.toLocaleString('es-CO')} COP`;
            }
            mostrarNotificacion(`TRM aplicada: $${trm.toFixed(2)}`, 'success');
        } catch (e) {
            console.error('Error consultando TRM:', e);
            mostrarNotificacion(e.message || 'Error consultando la TRM', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = textoOriginal;
        }
    },

    agregarItemAlCarrito: function () {
        console.log('🛒 agregarItemAlCarrito llamado');

        const productoInput = document.getElementById('ped-producto').value;
        const cantidad = parseInt(document.getElementById('ped-cantidad').value);
        const precioUnitario = parseFloat(document.getElementById('ped-precio').value);
        const esUsd = document.getElementById('ped-es-usd')?.checked;

        console.log('📦 Datos del formulario:', { productoInput, cantidad, precioUnitario, esUsd });

        // Validaciones básicas SOLO de campos vacíos
        if (!productoInput || !cantidad || !precioUnitario) {
            console.warn('⚠️ Campos incompletos');
            mostrarNotificacion('Complete todos los campos del producto', 'warning');
            return;
        }

        if (cantidad <= 0 || precioUnitario <= 0) {
            console.warn('⚠️ Valores inválidos');
            mostrarNotificacion('Cantidad y precio deben ser mayores a 0', 'warning');
            return;
        }

        if (esUsd && !this.usdConversionPendiente) {
            mostrarNotificacion('Consulte la TRM antes de añadir un producto en dólares', 'warning');
            return;
        }

        // Extraer código y descripción del input
        let codigo = productoInput;
        let descripcion = '';

        if (productoInput.includes(' - ')) {
            const partes = productoInput.split(' - ');
            codigo = partes[0].trim();
            descripcion = partes.slice(1).join(' - ').trim();
        }

        console.log('✅ Producto a agregar:', { codigo, descripcion, cantidad, precioUnitario });

        // ========================================
        // AGREGAR DIRECTAMENTE SIN VALIDACIONES
        // ========================================

        this.listaProductos.push({
            codigo: codigo,
            descripcion: descripcion || 'Sin descripción',
            cantidad: cantidad,
            precio_unitario: precioUnitario,
            precio_usd: esUsd ? this.usdConversionPendiente.precio_usd : null,
            trm_aplicada: esUsd ? this.usdConversionPendiente.trm_aplicada : null,
            stock_disponible: (this.productoSeleccionado && this.productoSeleccionado.codigo_sistema === codigo)
                ? this.productoSeleccionado.stock_disponible
                : 0
        });

        console.log(`✅ Producto ${codigo} agregado a la lista. Total items: ${this.listaProductos.length}`);
        console.log('📋 Lista completa:', this.listaProductos);

        // Limpiar campos de producto
        document.getElementById('ped-producto').value = '';
        document.getElementById('ped-cantidad').value = '';
        document.getElementById('ped-precio').value = '';
        document.getElementById('ped-es-usd').checked = false;
        this.toggleModoUsd();

        // Renderizar tabla y calcular total
        this.renderizarTablaItems();
        this.calcularTotalPedido();

        mostrarNotificacion(`✓ ${codigo} agregado (${cantidad} unidades)`, 'success');

        // Devolver el foco a la cajita de producto para seguir ingresando el
        // siguiente item sin tener que hacer scroll de vuelta hasta acá.
        document.getElementById('ped-producto').focus();
    },


    eliminarItemDelCarrito: function (index) {
        this.listaProductos.splice(index, 1);
        this.renderizarTablaItems();
        this.calcularTotalPedido();
        this.actualizarEstadoBotonPDF();
    },

    renderizarTablaItems: function () {
        const tbody = document.getElementById('items-pedido-body');

        if (this.listaProductos.length === 0) {
            tbody.innerHTML = `
                <tr class="empty-state">
                    <td colspan="6" style="text-align: center; color: #999; padding: 20px; border: 1px solid #dee2e6;">
                        No hay productos agregados
                    </td>
                </tr>
            `;
            this.actualizarEstadoBotonPDF();
            return;
        }

        tbody.innerHTML = this.listaProductos.map((item, index) => {
            const subtotal = item.cantidad * item.precio_unitario;
            return `
                <tr>
                    <td data-label="Código" style="padding: 10px; border: 1px solid #dee2e6;">${item.codigo}</td>
                    <td data-label="Descripción" style="padding: 10px; border: 1px solid #dee2e6;">${item.descripcion}</td>
                    <td data-label="Cantidad" style="padding: 10px; border: 1px solid #dee2e6; text-align: right;">${item.cantidad}</td>
                    <td data-label="Precio Unit." style="padding: 10px; border: 1px solid #dee2e6; text-align: right;">
                        ${formatearMoneda(item.precio_unitario)}
                        ${item.precio_usd ? `<br><small style="color:#0ea5e9;">USD $${parseFloat(item.precio_usd).toFixed(2)} @ TRM ${parseFloat(item.trm_aplicada).toFixed(2)}</small>` : ''}
                    </td>
                    <td data-label="Subtotal" style="padding: 10px; border: 1px solid #dee2e6; text-align: right;">${formatearMoneda(subtotal)}</td>
                    <td data-label="Acciones" style="padding: 10px; border: 1px solid #dee2e6; text-align: center;">
                        <div class="d-flex justify-content-center gap-1">
                            <button type="button" class="btn btn-sm btn-outline-primary" style="padding: 4px 8px; font-size: 0.8rem;" title="Editar Cantidad" onclick="ModuloPedidos.editarItemDelCarrito(${index})">
                                <i class="fas fa-edit"></i>
                            </button>
                            <button type="button" class="btn btn-sm btn-outline-danger" style="padding: 4px 8px; font-size: 0.8rem;" title="Eliminar" onclick="ModuloPedidos.eliminarItemDelCarrito(${index})">
                                <i class="fas fa-trash-alt"></i>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
        this.actualizarEstadoBotonPDF();
    },

    editarItemDelCarrito: function (index) {
        const item = this.listaProductos[index];
        if (!item) return;

        const modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:20000;';
        modal.innerHTML = `
            <div style="background:#fff; border-radius:16px; max-width:450px; width:95%; overflow:hidden; box-shadow:0 20px 60px rgba(0,0,0,0.3); animation: slideInRight 0.3s ease;">
                <div style="background:linear-gradient(135deg,#6366f1,#4f46e5); padding:18px 24px; color:#fff;">
                    <h4 style="margin:0; font-size:1.1rem; font-weight:600;"><i class="fas fa-edit me-2"></i>Editar Item del Pedido</h4>
                </div>
                <div style="padding:24px;">
                    <div style="margin-bottom: 20px;">
                        <label style="font-size:0.8rem; color:#64748b; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; display: block; margin-bottom: 8px;">Producto (Búsqueda)</label>
                        <div style="position: relative;">
                            <input type="text" id="modal-edit-producto" value="${item.codigo} - ${item.descripcion}" 
                                   style="width:100%; padding:10px 14px; border:2px solid #e2e8f0; border-radius:10px; font-size:0.95rem; font-weight:500; outline:none;"
                                   autocomplete="off">
                            <div id="modal-edit-producto-suggestions" class="autocomplete-suggestions" style="top: 100%; width: 100%;"></div>
                        </div>
                    </div>

                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                        <div>
                            <label style="font-size:0.8rem; color:#64748b; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; display: block; margin-bottom: 8px;">Cantidad</label>
                            <input type="number" id="modal-edit-cantidad" value="${item.cantidad}" min="1" 
                                   style="width:100%; padding:10px 14px; font-size:1.2rem; font-weight:700; text-align:center; border:2px solid #e2e8f0; border-radius:10px; outline:none;">
                        </div>
                        <div>
                            <label style="font-size:0.8rem; color:#64748b; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; display: block; margin-bottom: 8px;">Precio Unit.</label>
                            <input type="number" id="modal-edit-precio" value="${item.precio_unitario}" min="0" step="0.01"
                                   style="width:100%; padding:10px 14px; font-size:1.2rem; font-weight:700; text-align:center; border:2px solid #e2e8f0; border-radius:10px; outline:none;">
                        </div>
                    </div>
                </div>
                <div style="padding:16px 24px; background:#f8fafc; border-top:1px solid #e5e7eb; display:flex; gap:12px; justify-content:flex-end;">
                    <button id="modal-edit-cancelar" style="padding:10px 20px; border:1px solid #d1d5db; background:#fff; color:#374151; border-radius:10px; font-weight:500; cursor:pointer; font-size:0.9rem;">
                        Cancelar
                    </button>
                    <button id="modal-edit-confirmar" style="padding:10px 24px; background:linear-gradient(135deg,#6366f1,#4f46e5); color:#fff; border:none; border-radius:10px; font-weight:600; cursor:pointer; font-size:0.9rem;">
                        <i class="fas fa-check me-1"></i> Guardar Cambios
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        const inputProd = document.getElementById('modal-edit-producto');
        const suggestionsDiv = document.getElementById('modal-edit-producto-suggestions');
        const inputCant = document.getElementById('modal-edit-cantidad');
        const inputPrecio = document.getElementById('modal-edit-precio');

        // Inicializar logic de busqueda para el modal
        let debounceTimer;
        inputProd.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            const query = e.target.value.trim();
            if (query.length < 2) {
                suggestionsDiv.classList.remove('active');
                return;
            }
            debounceTimer = setTimeout(() => {
                const queryNorm = this.normalizeString(query);
                const resultados = this.productosData.filter(prod => {
                    const codigoNorm = this.normalizeString(prod.codigo_sistema || prod.codigo || '');
                    const descNorm = this.normalizeString(prod.descripcion);
                    return codigoNorm.includes(queryNorm) || descNorm.includes(queryNorm);
                });

                if (resultados.length === 0) {
                    suggestionsDiv.innerHTML = '<div class="suggestion-item">No se encontraron productos</div>';
                    suggestionsDiv.classList.add('active');
                    return;
                }

                renderProductSuggestions(suggestionsDiv, resultados.slice(0, 10), (res) => {
                    const codigoDisplay = res.codigo_sistema || res.codigo || '';
                    inputProd.dataset.selectedCodigo = codigoDisplay;
                    inputProd.dataset.selectedDesc = res.descripcion;
                    inputProd.value = `${codigoDisplay} - ${res.descripcion}`;
                    
                    // Asignación automática de precio en modal
                    inputPrecio.value = parseFloat(res.precio) || 0;
                    
                    suggestionsDiv.classList.remove('active');
                    inputCant.focus();
                });
                suggestionsDiv.classList.add('active');
            }, 300);
        });

        inputCant.focus();
        inputCant.select();

        // Cerrar sugerencias al hacer clic fuera
        modal.addEventListener('click', (e) => {
            if (!inputProd.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });

        // Enter key support
        const handleEnter = (e) => {
            if (e.key === 'Enter') document.getElementById('modal-edit-confirmar').click();
            if (e.key === 'Escape') document.getElementById('modal-edit-cancelar').click();
        };
        inputCant.addEventListener('keydown', handleEnter);
        inputPrecio.addEventListener('keydown', handleEnter);

        document.getElementById('modal-edit-confirmar').addEventListener('click', () => {
            const cantNum = parseInt(inputCant.value);
            const precioNum = parseFloat(inputPrecio.value);
            const prodText = inputProd.value;

            if (isNaN(cantNum) || cantNum <= 0) {
                mostrarNotificacion('La cantidad debe ser un número mayor a 0', 'warning');
                return;
            }
            if (isNaN(precioNum) || precioNum < 0) {
                mostrarNotificacion('El precio no puede ser negativo', 'warning');
                return;
            }

            // Actualizar datos del producto
            let finalCodigo = item.codigo;
            let finalDesc = item.descripcion;

            if (prodText.includes(' - ')) {
                const partes = prodText.split(' - ');
                finalCodigo = partes[0].trim();
                finalDesc = partes.slice(1).join(' - ').trim();
            } else {
                finalCodigo = prodText.trim();
                finalDesc = 'Sin descripción';
            }

            item.codigo = finalCodigo;
            item.descripcion = finalDesc;
            item.cantidad = cantNum;

            // Si el precio cambió, la conversión USD/TRM que traía (si tenía)
            // ya no corresponde al nuevo precio en COP -- se descarta para no
            // guardar una trazabilidad falsa (ej. "USD $10 @ TRM X" pegada a
            // un precio que en realidad se editó manualmente después).
            if (precioNum !== item.precio_unitario) {
                item.precio_usd = null;
                item.trm_aplicada = null;
            }
            item.precio_unitario = precioNum;

            this.renderizarTablaItems();
            this.calcularTotalPedido();
            document.body.removeChild(modal);
            mostrarNotificacion('Item actualizado correctamente', 'success');
        });

        document.getElementById('modal-edit-cancelar').addEventListener('click', () => {
            document.body.removeChild(modal);
        });

        modal.addEventListener('click', (e) => {
            if (e.target === modal) document.body.removeChild(modal);
        });
    },

    actualizarEstadoBotonPDF: function () {
        const btnPdf = document.getElementById('btn-pdf-pedido');
        if (btnPdf) {
            btnPdf.disabled = this.listaProductos.length === 0;
        }
    },

    calcularTotalPedido: function () {
        const descuentoGlobal = parseFloat(document.getElementById('ped-descuento-global')?.value || 0);

        const subtotalBruto = this.listaProductos.reduce((sum, item) => {
            return sum + (item.cantidad * item.precio_unitario);
        }, 0);

        const valorDescuento = subtotalBruto * (descuentoGlobal / 100);
        const subtotalNeto = subtotalBruto - valorDescuento;
        const iva = subtotalNeto * 0.19;
        const total = subtotalNeto + iva;

        // Actualizar UI
        const pedSubtotal = document.getElementById('ped-subtotal');
        const pedIva = document.getElementById('ped-iva');
        const pedTotal = document.getElementById('ped-total');

        if (pedSubtotal) pedSubtotal.textContent = formatearMoneda(subtotalBruto);
        if (pedIva) pedIva.textContent = formatearMoneda(iva);
        if (pedTotal) pedTotal.textContent = formatearMoneda(total);

        // Habilitar botón PDF si hay items
        const btnPdf = document.getElementById('btn-pdf-pedido');
        if (btnPdf) btnPdf.disabled = this.listaProductos.length === 0;
    },

    registrarPedido: async function (e) {
        e.preventDefault();

        // Validar que haya productos en la lista
        if (this.listaProductos.length === 0) {
            mostrarNotificacion('Debe agregar al menos un producto al pedido', 'warning');
            return;
        }

        if (!this.clienteSeleccionado) {
            mostrarNotificacion('Debe seleccionar un cliente', 'warning');
            return;
        }

        // Mostrar modal de confirmación en lugar de confirm()
        const confirmar = await this.mostrarConfirmacion(
            '¿Confirmar Registro?',
            `Se registrará el pedido con ${this.listaProductos.length} producto(s)`
        );

        if (!confirmar) return;

        const btn = e.target.querySelector('button[type="submit"]');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Guardando...';

        try {
            const totalStr = document.getElementById('ped-total').textContent;
            const descuentoGlobal = parseFloat(document.getElementById('ped-descuento-global')?.value || 0);

            const pedidoData = {
                id_pedido: this.idPedidoEdicion, // Incluir si estamos editando
                fecha: document.getElementById('ped-fecha').value || new Date().toISOString().split('T')[0],
                vendedor: document.getElementById('ped-vendedor').value,
                cliente: this.clienteSeleccionado.nombre,
                nit: this.clienteSeleccionado.nit || '',
                direccion: this.clienteSeleccionado.direccion || '',
                ciudad: this.clienteSeleccionado.ciudad || '',
                forma_pago: document.getElementById('ped-pago').value,
                es_exportacion: document.getElementById('ped-es-exportacion')?.checked || false,
                descuento_global: descuentoGlobal,
                observaciones: document.getElementById('ped-observaciones').value || '',
                productos: this.listaProductos.map(item => ({
                    id_sql: item.id_sql || null, // Enviar si existe para UPSERT
                    codigo: item.codigo,
                    descripcion: item.descripcion,
                    cantidad: item.cantidad,
                    precio_unitario: item.precio_unitario,
                    precio_usd: item.precio_usd || null,
                    trm_aplicada: item.trm_aplicada || null
                }))
            };

            // LOG DE AUDITORÍA
            console.log("📦 ===== DATOS DEL PEDIDO =====");
            console.table(pedidoData);
            console.log("Total de productos:", pedidoData.productos.length);
            console.log("Descuento global:", descuentoGlobal + "%");

            const response = await fetch('/api/pedidos/registrar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(pedidoData)
            });

            const result = await response.json();

            // LOG DE RESPUESTA
            console.log("🚀 ===== RESPUESTA DEL SERVIDOR =====");
            console.log("Status HTTP:", response.status);
            console.log("Datos de respuesta:", result);

            if (result.success) {
                console.log("✅ Pedido registrado exitosamente:", result.data.id_pedido);
                this.ultimoIdRegistrado = result.data.id_pedido;
                mostrarNotificacion(`✓ Pedido ${result.data.id_pedido} registrado con ${result.data.total_productos} productos`, 'success');

                // Limpiar formulario y lista SOLO después de éxito
                limpiarFormulario('form-pedidos');
                if (window.FormHelpers) window.FormHelpers.limpiarPersistencia('form-pedidos');
                this.listaProductos = [];
                this.clienteSeleccionado = null;
                this.idPedidoEdicion = null;
                if (document.getElementById('edit-mode-indicator')) {
                    document.getElementById('edit-mode-indicator').style.display = 'none';
                }
                const btnSubmit = document.querySelector('#form-pedidos button[type="submit"]');
                if (btnSubmit) {
                    btnSubmit.innerHTML = '<i class="fas fa-paper-plane"></i> Registrar Pedido';
                    btnSubmit.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';
                }
                this.renderizarTablaItems();
                this.calcularTotalPedido();
                document.getElementById('ped-total').textContent = '$ 0.00';
                if (document.getElementById('ped-descuento-global')) {
                    document.getElementById('ped-descuento-global').value = '0';
                }
                this.actualizarVendedor();

                // --- Preguntar por PDF tras registro ---
                // Capturamos datos antes de la confirmación porque es async y el estado podría cambiar
                const dataParaPDF = {
                    id_pedido: result.data.id_pedido,
                    fecha: pedidoData.fecha,
                    vendedor: pedidoData.vendedor,
                    cliente: {
                        nombre: pedidoData.cliente,
                        nit: pedidoData.nit,
                        direccion: pedidoData.direccion || '',
                        ciudad: pedidoData.ciudad || '',
                        telefonos: ''
                    },
                    productos: JSON.parse(JSON.stringify(pedidoData.productos)),
                    total: totalStr,
                    forma_pago: pedidoData.forma_pago,
                    descuento_global: descuentoGlobal,
                    observaciones: pedidoData.observaciones
                };

                setTimeout(async () => {
                    const descargar = await this.mostrarConfirmacion(
                        '¡Pedido Registrado!',
                        '¿Desea descargar el comprobante PDF del pedido ahora?'
                    );
                    if (descargar) {
                        this.generarPDF(dataParaPDF);
                    }
                }, 500);

            } else {
                console.error("❌ Error del servidor:", result.error);
                mostrarNotificacion(result.error || "Error desconocido", 'error');
            }

        } catch (error) {
            console.error("❌ ERROR DE CONEXIÓN:", error);
            mostrarNotificacion("Error de conexión con el servidor", 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    },

    generarPDF: function (datosManuales = null) {
        try {
            const { jsPDF } = window.jspdf;
            const doc = new jsPDF();

            // Si vienen datos manuales (ej: justo después de registrar), usarlos. 
            // Si no, leer del DOM (flujo normal).
            const fechaRaw = datosManuales ? datosManuales.fecha : document.getElementById('ped-fecha').value;
            const fecha = fechaRaw || new Date().toLocaleDateString();
            const vendedor = datosManuales ? datosManuales.vendedor : document.getElementById('ped-vendedor').value;
            const cliente = datosManuales ? datosManuales.cliente : (this.clienteSeleccionado || {
                nombre: document.getElementById('ped-cliente').value,
                nit: document.getElementById('ped-nit').value,
                direccion: '',
                telefonos: '',
                ciudad: ''
            });
            const totalStr = datosManuales ? datosManuales.total : document.getElementById('ped-total').textContent;
            const formaPago = datosManuales ? datosManuales.forma_pago : document.getElementById('ped-pago').value;
            const listaProductos = datosManuales ? datosManuales.productos : this.listaProductos;
            const idMostrar = datosManuales ? datosManuales.id_pedido : (this.ultimoIdRegistrado || `Ped-TEMP`);
            const observaciones = datosManuales ? (datosManuales.observaciones || '') : (document.getElementById('ped-observaciones')?.value || '');
            const descuentoGlobal = datosManuales ? (parseFloat(datosManuales.descuento_global) || 0) : (parseFloat(document.getElementById('ped-descuento-global')?.value) || 0);

            // --- 1. ENCABEZADO Y LOGO ---
            // Adaptación para Frimetals
            const division = window.AppState?.user?.division;
            const isMetals = division === 'FRIMETALS';
            const imgPath = isMetals ? '/static/img/logo_frimetals.png' : '/static/img/logo_friparts_nuevo.jpg';
            const companyName = isMetals ? 'FRIMETALS' : 'FRIPARTS S.A.S';
            const docTitle = isMetals ? 'Orden de Pedido - Frimetals' : 'Comprobante de Pedido';

            // Función interna para dibujar el contenido (con o sin logo)
            const dibujarContenido = (imgData = null) => {
                if (imgData) {
                    // Ajustamos dimensiones según el logo
                    if (isMetals) {
                        // El logo de Frimetals es ovalado/alargado
                        doc.addImage(imgData, 'PNG', 14, 10, 80, 25);
                    } else {
                        try {
                            doc.addImage(imgData, 'JPEG', 14, 10, 60, 28);
                        } catch (e) {
                            doc.addImage(imgData, 'PNG', 14, 10, 60, 28);
                        }
                    }
                }

                // Nombre de la empresa DEBAJO del logo (alineado a la izquierda)
                doc.setFontSize(22);
                doc.setTextColor(isMetals ? 50 : 30, isMetals ? 50 : 58, isMetals ? 50 : 138); // Gris oscuro para Metals, Azul para Parts
                doc.setFont(undefined, 'bold');
                doc.text(companyName, 14, 45);

                doc.setFontSize(10);
                doc.setTextColor(100);
                doc.setFont(undefined, 'normal');

                // Título del documento
                doc.setFontSize(14);
                doc.text(docTitle, 196, 14, { align: 'right' });

                doc.setFontSize(10);
                doc.text(`Comprobante No: ${idMostrar}`, 196, 22, { align: 'right' });
                doc.text(`Fecha: ${fecha}`, 196, 28, { align: 'right' });

                // --- 2. BLOQUE DE INFORMACIÓN (Grid de 2 columnas) ---
                doc.setDrawColor(220);
                doc.setLineWidth(0.5);
                doc.line(14, 55, 196, 55);

                // Columna Izquierda: Cliente (Ancho máx 100)
                doc.setFontSize(11);
                doc.setTextColor(isMetals ? 50 : 30, isMetals ? 50 : 58, isMetals ? 50 : 138);
                doc.setFont(undefined, 'bold');
                doc.text("CLIENTE:", 14, 65);

                doc.setFontSize(10);
                doc.setTextColor(40);
                doc.setFont(undefined, 'normal');
                const splitNombre = doc.splitTextToSize(`Nombre: ${cliente.nombre}`, 110);
                doc.text(splitNombre, 14, 72);

                const nextY = 72 + (splitNombre.length * 5);
                doc.text(`NIT/ID: ${cliente.nit || 'N/A'}`, 14, nextY);
                doc.text(`Dirección: ${cliente.direccion || 'N/A'}`, 14, nextY + 7);
                doc.text(`Ciudad: ${cliente.ciudad || 'N/A'}`, 14, nextY + 14);
                doc.text(`Teléfonos: ${cliente.telefonos || 'N/A'}`, 14, nextY + 21);

                // Columna Derecha: Venta (Movida más a la derecha para evitar choques)
                const rightX = 135;
                doc.setFontSize(11);
                doc.setTextColor(isMetals ? 50 : 30, isMetals ? 50 : 58, isMetals ? 50 : 138);
                doc.setFont(undefined, 'bold');
                doc.text("DETALLES DE VENTA:", rightX, 65);

                doc.setFontSize(10);
                doc.setTextColor(40);
                doc.setFont(undefined, 'normal');
                doc.text(`Vendedor: ${vendedor}`, rightX, 72);
                doc.text(`Forma de Pago: ${formaPago}`, rightX, 79);
                doc.text(`Estado: REGISTRADO`, rightX, 86);

                // --- 3. TABLA DE PRODUCTOS ---
                const tablaData = listaProductos.map(item => [
                    item.codigo,
                    item.descripcion,
                    item.cantidad,
                    formatearMoneda(item.precio_unitario),
                    formatearMoneda(item.cantidad * item.precio_unitario)
                ]);

                doc.autoTable({
                    startY: 115,
                    head: [['Código', 'Descripción', 'Cant.', 'Unitario', 'Subtotal']],
                    body: tablaData,
                    theme: 'grid',
                    headStyles: { fillColor: isMetals ? [50, 50, 50] : [30, 58, 138], textColor: 255, halign: 'center' },
                    styles: { fontSize: 9, cellPadding: 3 },
                    columnStyles: {
                        0: { cellWidth: 30 },
                        2: { halign: 'center' },
                        3: { halign: 'right' },
                        4: { halign: 'right' }
                    }
                });

                // --- 4. TOTALES ---
                let currentY = doc.lastAutoTable.finalY + 15;
                const totalX = 196;
                const labelX = totalX - 65; // Más espacio para etiquetas

                // Cálculos para el PDF
                const subtotalBruto = listaProductos.reduce((acc, p) => acc + (p.cantidad * p.precio_unitario), 0);
                const valorDescuento = subtotalBruto * (descuentoGlobal / 100);
                const subtotalNeto = subtotalBruto - valorDescuento;
                const iva = subtotalNeto * 0.19;
                const totalFinal = subtotalNeto + iva;

                // Verificar espacio antes del pie de página
                const pageHeight = doc.internal.pageSize.height || doc.internal.pageSize.getHeight();
                if (currentY > pageHeight - 60) {
                    doc.addPage();
                    currentY = 25;
                }

                doc.setFontSize(10);
                doc.setTextColor(80);
                doc.setFont(undefined, 'normal');

                // Subtotal
                doc.text(`Subtotal:`, labelX, currentY);
                doc.text(formatearMoneda(subtotalBruto), totalX, currentY, { align: 'right' });

                // Descuento (si existe)
                if (descuentoGlobal > 0) {
                    currentY += 7;
                    doc.text(`Descuento (${descuentoGlobal}%):`, labelX, currentY);
                    doc.setTextColor(220, 38, 38); // Rojo para descuento
                    doc.text(`- ${formatearMoneda(valorDescuento)}`, totalX, currentY, { align: 'right' });
                    doc.setTextColor(80);
                }

                // IVA
                currentY += 7;
                doc.text(`IVA (19%):`, labelX, currentY);
                doc.text(formatearMoneda(iva), totalX, currentY, { align: 'right' });

                // Línea de total
                currentY += 5;
                doc.setDrawColor(isMetals ? 50 : 30, isMetals ? 50 : 58, isMetals ? 50 : 138);
                doc.setLineWidth(0.8);
                doc.line(labelX, currentY, totalX, currentY);

                // Total Final (Repartido a lo ancho para máximo impacto)
                currentY += 12;
                doc.setFontSize(16);
                doc.setTextColor(isMetals ? 50 : 30, isMetals ? 50 : 58, isMetals ? 50 : 138);
                doc.setFont(undefined, 'bold');
                doc.text(`TOTAL A PAGAR:`, 14, currentY); // A la izquierda
                doc.text(formatearMoneda(totalFinal), totalX, currentY, { align: 'right' }); // A la derecha

                // --- 5. FIRMAS Y PIE DE PÁGINA (Siempre al final del documento) ---
                const pageHeightFinal = doc.internal.pageSize.height || doc.internal.pageSize.getHeight();
                const footerY = pageHeightFinal - 25;

                // Línea separadora final
                doc.setDrawColor(220);
                doc.setLineWidth(0.3);
                doc.line(14, footerY - 8, 196, footerY - 8);

                doc.setFontSize(8.5);
                doc.setTextColor(120);
                doc.setFont(undefined, 'italic');
                doc.text("Este documento es un comprobante interno de pedido y no constituye factura legal.", 105, footerY, { align: 'center' });

                doc.setFontSize(9);
                doc.setTextColor(60);
                doc.setFont(undefined, 'bold');
                const footerCompanyInfo = isMetals
                    ? "FRIMETALS - Metalmecánica de Precisión - www.frimetals.com"
                    : "FRIPARTS S.A.S - Carrera 29 #78-40 - www.friparts.com";
                doc.text(footerCompanyInfo, 105, footerY + 6, { align: 'center' });

                if (!isMetals) {
                    doc.setFontSize(9);
                    doc.setTextColor(30, 58, 138);
                    doc.setFont(undefined, 'normal');
                    doc.text("Instagram: @friparts_bujes", 105, footerY + 12, { align: 'center' });
                }

                // Guardar
                const fileName = `Pedido_${cliente.nombre.replace(/\s+/g, '_')}_${fecha}.pdf`;
                doc.save(fileName);
                mostrarNotificacion("PDF generado correctamente", "success");
            };

            // Intentar cargar logo antes de dibujar
            const img = new Image();
            img.onload = function () {
                try {
                    dibujarContenido(this);
                } catch (err) {
                    console.error("Error en dibujarContenido (onload):", err);
                    mostrarNotificacion("Error finalizando el PDF", "error");
                }
            };
            img.onerror = function () {
                try {
                    console.warn("Logo no encontrado o error de carga - Generando sin logo");
                    dibujarContenido(null);
                } catch (err) {
                    console.error("Error en dibujarContenido (onerror):", err);
                    mostrarNotificacion("Error finalizando el PDF (sin logo)", "error");
                }
            };
            img.src = imgPath;

        } catch (error) {
            console.error("Error generando PDF:", error);
            mostrarNotificacion("No se pudo generar el PDF", "error");
        }
    },

    // Función para mostrar confirmación personalizada
    mostrarConfirmacion: function (titulo, mensaje) {
        return new Promise((resolve) => {
            // Crear modal de confirmación
            const modal = document.createElement('div');
            modal.className = 'modal-overlay';
            modal.innerHTML = `
                <div class="modal-content confirmation-modal-pro" style="max-width: 420px; border: none; overflow: hidden; background-color: #ffffff;">
                    <div class="modal-header" style="background: white; border-bottom: 1px solid #e5e7eb; padding: 20px 25px;">
                        <h3 style="color: #111827; margin: 0; font-size: 1.25rem; font-weight: 600;"><i class="fas fa-question-circle" style="color: #3b82f6; margin-right: 12px;"></i> ${titulo}</h3>
                    </div>
                    <div class="modal-body" style="padding: 30px 25px; color: #374151; font-size: 1.05rem; line-height: 1.6; background-color: #ffffff;">
                        <p>${mensaje}</p>
                    </div>
                    <div class="modal-footer" style="background: #f9fafb; padding: 15px 25px; border-top: 1px solid #e5e7eb; display: flex; gap: 12px; justify-content: flex-end;">
                        <button class="btn btn-secondary" id="modal-cancelar" style="background: white; border: 1px solid #d1d5db; color: #374151; padding: 8px 16px; font-weight: 500; border-radius: 6px;">
                            Cancelar
                        </button>
                        <button class="btn btn-primary" id="modal-confirmar" style="background: #2563eb; color: white; border: 1px solid #2563eb; padding: 8px 20px; font-weight: 500; border-radius: 6px;">
                            Confirmar
                        </button>
                    </div>
                </div>
            `;

            document.body.appendChild(modal);

            // Event listeners
            document.getElementById('modal-confirmar').addEventListener('click', () => {
                document.body.removeChild(modal);
                resolve(true);
            });

            document.getElementById('modal-cancelar').addEventListener('click', () => {
                document.body.removeChild(modal);
                resolve(false);
            });

            // Cerrar al hacer clic fuera
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    document.body.removeChild(modal);
                    resolve(false);
                }
            });
        });
    },

    // Nueva función Toast Profesional
    showToast: function (mensaje, tipo = 'info') {
        // Colores de barra lateral según tipo
        const colores = {
            'success': '#10b981', // Verde
            'error': '#ef4444',   // Rojo
            'warning': '#f59e0b', // Naranja
            'info': '#3b82f6'     // Azul
        };

        const color = colores[tipo] || colores['info'];
        const icono = tipo === 'success' ? 'fa-check-circle' :
            tipo === 'error' ? 'fa-exclamation-triangle' :
                'fa-info-circle';

        const toast = document.createElement('div');
        toast.className = 'toast-notification';
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background-color: #ffffff;
            color: #333;
            padding: 16px 24px;
            border-radius: 12px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.15);
            display: flex;
            align-items: center;
            gap: 15px;
            z-index: 10000;
            min-width: 320px;
            border-left: 6px solid ${color};
            font-family: 'Segoe UI', system-ui, sans-serif;
            font-weight: 500;
            opacity: 0;
            transition: all 0.4s cubic-bezier(0.68, -0.55, 0.27, 1.55);
        `;

        toast.innerHTML = `
            <i class="fas ${icono}" style="color: ${color}; font-size: 1.2rem;"></i>
            <span style="flex: 1;">${mensaje}</span>
        `;

        document.body.appendChild(toast);

        // Animación de entrada
        requestAnimationFrame(() => {
            toast.style.top = '40px';
            toast.style.opacity = '1';
        });

        // Auto-eliminar
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.top = '0px';
            setTimeout(() => toast.remove(), 400);
        }, 3000);
    },

    // --- INTEGRACIÓN Y LÓGICA PROACTIVA ---

    /**
     * Inicializar el módulo (llamado desde app.js)
     */
    inicializar: function () {
        console.log("🔧 Inicializando módulo Pedidos (desde app.js)");

        // 1. Asegurar setup de UI y listeners
        this.init();
        // Set vendedor de inmediato (sin esperar fetch de datos)
        this.actualizarVendedor();

        // Fallback: si el usuario se hidrata después, reintentar una vez
        if (!this._userReadyListenerAdded) {
            this._userReadyListenerAdded = true;
            document.addEventListener('user-ready', () => this.actualizarVendedor(), { once: true });
        }

        // Evitar múltiples cargas simultáneas
        if (this._cargando) {
            console.log("⏭️ Pedidos ya está cargando, omitiendo...");
            return;
        }
        this._cargando = true;

        this.cargarDatosIniciales().finally(() => {
            this._cargando = false;
            this.actualizarVendedor();

            // --- LÓGICA PROACTIVA: Verificar si venimos desde Almacén para editar ---
            const pendingEditId = localStorage.getItem('pending_edit_id');
            if (pendingEditId) {
                console.log(`✨ [Pedidos] Detectado pedido pendiente de edición: ${pendingEditId}`);
                localStorage.removeItem('pending_edit_id'); // Limpiar inmediatamente

                // Esperar a que el DOM esté listo
                setTimeout(() => {
                    this.cargarPedidoPorId(pendingEditId);
                }, 300);
            }

            // Juan Sebastian: Si estamos en la página de historial de metales, cargar datos de inmediato
            const page = window.AppState?.paginaActual;
            if (page === 'metals-pedidos') {
                setTimeout(() => this.cargarHistorialMetals(), 500);
            }
        });
    },

    /**
     * Abrir modal para creación dual de producto (Master + Inventario)
     */
    abrirModalCrearProducto: function (codigoSugerido = '') {
        console.log(`🆕 Abriendo modal de creación para: ${codigoSugerido}`);

        // Cerrar sugerencias si están abiertas
        const suggestionsDiv = document.getElementById('ped-producto-suggestions');
        if (suggestionsDiv) suggestionsDiv.classList.remove('active');

        const modalHtml = `
            <div class="modal-overlay" id="modal-crear-producto" style="z-index: 10001; background: rgba(0,0,0,0.5); position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;">
                <div class="modal-content" style="background: white; width: 95%; max-width: 500px; border-radius: 16px; overflow: hidden; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25); animation: zoomIn 0.3s ease;">
                    <div class="modal-header" style="padding: 20px 25px; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: center; background: #f9fafb;">
                        <h5 style="margin: 0; font-weight: 700; color: #111827;"><i class="fas fa-plus-circle text-primary me-2"></i> Registro de Nuevo Producto</h5>
                        <button type="button" onclick="document.getElementById('modal-crear-producto').remove()" style="border: none; background: none; font-size: 1.2rem; color: #9ca3af; cursor: pointer;"><i class="fas fa-times"></i></button>
                    </div>
                    <div class="modal-body" style="padding: 25px;">
                        <form id="form-crear-dual">
                            <div class="mb-3">
                                <label class="form-label small fw-bold text-muted text-uppercase">ID Código (Maestro)</label>
                                <input type="text" id="new-id-codigo" class="form-control fw-bold" value="${codigoSugerido}" placeholder="Ej: 9304" required style="border-radius: 8px;">
                            </div>
                            <div class="mb-3">
                                <label class="form-label small fw-bold text-muted text-uppercase">Código Sistema (Opcional)</label>
                                <input type="text" id="new-sis-codigo" class="form-control" placeholder="Ej: BUJE-01" style="border-radius: 8px;">
                            </div>
                            <div class="mb-3">
                                <label class="form-label small fw-bold text-muted text-uppercase">Descripción / Nombre</label>
                                <input type="text" id="new-descripcion" class="form-control" placeholder="Nombre completo del producto" required style="border-radius: 8px;">
                            </div>
                            <div class="row">
                                <div class="col-6 mb-3">
                                    <label class="form-label small fw-bold text-muted text-uppercase">Precio Unitario ($)</label>
                                    <input type="number" id="new-precio" class="form-control" value="0" step="0.01" style="border-radius: 8px;">
                                </div>
                                <div class="col-6 mb-3">
                                    <label class="form-label small fw-bold text-muted text-uppercase">Stock Inicial</label>
                                    <input type="number" id="new-stock" class="form-control" value="0" style="border-radius: 8px;">
                                </div>
                            </div>
                            <div class="mt-4">
                                <button type="submit" class="btn btn-primary w-100 fw-bold" style="padding: 12px; border-radius: 10px; background: #2563eb; border: none; box-shadow: 0 4px 6px -1px rgba(37, 99, 235, 0.4);">
                                    <i class="fas fa-save me-2"></i> Crear y Seleccionar
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Focus en descripción si ya hay código
        setTimeout(() => {
            const focusTarget = codigoSugerido ? 'new-descripcion' : 'new-id-codigo';
            const input = document.getElementById(focusTarget);
            if (input) input.focus();
        }, 300);

        // Submit Logic
        document.getElementById('form-crear-dual').onsubmit = async (e) => {
            e.preventDefault();
            const btn = e.target.querySelector('button[type="submit"]');
            const originalText = btn.innerHTML;

            try {
                btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i> Creando...';
                btn.disabled = true;

                const payload = {
                    id_codigo: document.getElementById('new-id-codigo').value.trim().toUpperCase(),
                    codigo_sistema: document.getElementById('new-sis-codigo').value.trim().toUpperCase(),
                    descripcion: document.getElementById('new-descripcion').value.trim(),
                    precio: parseFloat(document.getElementById('new-precio').value) || 0,
                    stock_inicial: parseInt(document.getElementById('new-stock').value) || 0
                };

                const response = await fetch('/api/productos/crear_dual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                const result = await response.json();

                if (result.success) {
                    this.showToast(result.message, 'success');

                    // 1. Crear el objeto producto para la sesión actual
                    const nuevoProd = {
                        codigo: payload.id_codigo,
                        id_codigo: payload.id_codigo,
                        codigo_sistema: payload.codigo_sistema || payload.id_codigo,
                        descripcion: payload.descripcion,
                        precio: payload.precio,
                        stock_disponible: payload.stock_inicial
                    };

                    // 2. Insertarlo en el cache local
                    this.productosData.unshift(nuevoProd);

                    // 3. Seleccionarlo automáticamente
                    document.getElementById('ped-producto').value = `${nuevoProd.id_codigo} - ${nuevoProd.descripcion}`;
                    document.getElementById('ped-precio').value = nuevoProd.precio;
                    this.productoSeleccionado = nuevoProd;

                    // 4. Cerrar modal y saltar a cantidad
                    document.getElementById('modal-crear-producto').remove();
                    const campoCant = document.getElementById('ped-cantidad');
                    if (campoCant) campoCant.focus();

                } else {
                    this.showToast(result.error || 'Error al crear producto', 'error');
                }
            } catch (error) {
                console.error('Error in crear_dual:', error);
                this.showToast('Error de conexión', 'error');
            } finally {
                btn.innerHTML = originalText;
                btn.disabled = false;
            }
        };
    },

    // --- LÓGICA EXPORTACIÓN WORLD OFFICE ---

    abrirPreviewWO: function () {
        const modal = document.getElementById('modal-preview-wo');
        if (modal) {
            modal.style.display = 'block';
            this.cargarPreviewWO();
        }
    },

    cerrarPreviewWO: function () {
        const modal = document.getElementById('modal-preview-wo');
        if (modal) {
            modal.style.display = 'none';
        }
    },

    cargarPreviewWO: async function () {
        const tbody = document.querySelector('#tabla-preview-wo tbody');
        const thead = document.querySelector('#tabla-preview-wo thead');

        if (!tbody || !thead) return;

        tbody.innerHTML = '<tr><td colspan="10" class="text-center"><i class="fas fa-spinner fa-spin"></i> Cargando vista previa...</td></tr>';

        try {
            const response = await fetch('/api/exportar/world-office/preview');
            const result = await response.json();

            if (result.success && result.data.length > 0) {
                // Render Headers (Excluyendo columnas auxiliares de auditoría)
                const firstRow = result.data[0];
                const columns = Object.keys(firstRow).filter(col => col !== 'precio_historico' && col !== 'precio_maestro');
                thead.innerHTML = '<tr>' + columns.map(col => `<th>${col}</th>`).join('') + '</tr>';

                // Render Rows
                tbody.innerHTML = result.data.map(row => {
                    const precioHist = row.precio_historico !== undefined && row.precio_historico !== null ? parseFloat(row.precio_historico) : null;
                    const precioMaest = row.precio_maestro !== undefined && row.precio_maestro !== null ? parseFloat(row.precio_maestro) : null;
                    const hasPriceDiff = precioHist !== null && precioMaest !== null && Math.abs(precioHist - precioMaest) > 0.01;

                    const rowStyle = hasPriceDiff 
                        ? 'style="background-color: #fff3cd !important; border-left: 4px solid #fd7e14;" class="table-warning"' 
                        : '';

                    return `<tr ${rowStyle}>` + columns.map(col => {
                        let cellVal = row[col] !== null ? row[col] : '';
                        
                        if (col === 'Detalle: Valor Unitario' && hasPriceDiff) {
                            const tooltip = `Desfase detectado: El cliente pidió a $${precioHist.toLocaleString('es-CO')}, pero el precio maestro actual es $${precioMaest.toLocaleString('es-CO')}`;
                            let displayVal = cellVal;
                            try {
                                const parsedVal = parseFloat(cellVal);
                                if (!isNaN(parsedVal)) {
                                    displayVal = `$ ${parsedVal.toLocaleString('es-CO')}`;
                                }
                            } catch (e) {}
                            cellVal = `<span class="text-danger fw-bold d-inline-flex align-items-center gap-1" title="${tooltip}" style="cursor: help;">
                                ${displayVal} <span style="font-size: 1.1rem;">⚠️</span>
                            </span>`;
                        }
                        
                        return `<td>${cellVal}</td>`;
                    }).join('') + '</tr>';
                }).join('');

            } else {
                tbody.innerHTML = '<tr><td colspan="10" class="text-center text-warning"><i class="fas fa-exclamation-triangle"></i> No hay pedidos pendientes para exportar.</td></tr>';
            }
        } catch (error) {
            console.error("Error cargando preview:", error);
            tbody.innerHTML = `<tr><td colspan="10" class="text-center text-danger">Error: ${error.message}</td></tr>`;
        }
    },

    descargarExcelWO: async function () {
        const btn = document.getElementById('btn-confirmar-exportar-wo');
        if (!btn) return;

        const originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generando...';
        btn.disabled = true;

        try {
            const res = await fetchData('/api/exportar/world-office', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!res) return; // fetchData ya notificó el error de red/HTTP
            if (!res.success || !res.data?.task_id) {
                throw new Error(res.error || 'No se pudo iniciar la exportación');
            }

            this.showToast('Generando Excel de World Office... esto puede tardar unos segundos.', 'info');
            const downloadUrl = await this._sondearExportacionWO(res.data.task_id);

            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = downloadUrl;
            document.body.appendChild(a);
            a.click();
            a.remove();

            this.showToast('✅ Archivo generado correctamente.', 'success');
            this.cerrarPreviewWO();
        } catch (error) {
            console.error("Error descarga WO:", error);
            this.showToast(`Error: ${error.message}`, 'error');
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    },

    /**
     * Sondea /api/tasks/status/<task_id> hasta COMPLETED/FAILED. Devuelve la
     * download_url o lanza si falla/expira.
     */
    _sondearExportacionWO: async function (taskId, intentos = 0) {
        const POLL_MS = 2000;
        const MAX_INTENTOS = 150; // ~5 min de margen

        if (intentos >= MAX_INTENTOS) {
            throw new Error('La exportación está tardando demasiado. Intenta de nuevo más tarde.');
        }

        const estado = await fetchData(`/api/tasks/status/${taskId}`);
        if (!estado) throw new Error('Error de conexión consultando el estado de la exportación');
        if (!estado.success) throw new Error(estado.error || 'Error consultando el estado de la exportación');

        const { status, download_url, error } = estado.data;
        if (status === 'COMPLETED') return download_url;
        if (status === 'FAILED') throw new Error(error || 'Error generando el Excel de World Office');

        // PENDING/RUNNING: seguir esperando
        await new Promise(resolve => setTimeout(resolve, POLL_MS));
        return this._sondearExportacionWO(taskId, intentos + 1);
    },

    // Estado de la pestaña actual para metales
    metalsTabActiva: 'proceso',

    cargarHistorialMetals: async function () {
        const container = document.getElementById('metals-historial-container');
        if (!container) return;

        const search = document.getElementById('busqueda-pedidos-metals')?.value || '';

        try {
            container.innerHTML = `
                <div class="text-center py-5 text-muted" style="grid-column: 1 / -1;">
                    <i class="fas fa-spinner fa-spin fa-2x mb-3"></i>
                    <p>Consultando pedidos de Metales...</p>
                </div>
            `;

            const res = await fetch(`/api/pedidos/listar?division=frimetals&search=${encodeURIComponent(search)}`);
            const data = await res.json();

            if (data.success) {
                this.pedidosMetalsCache = data.data.pedidos || [];
                this.renderizarPedidosMetals(this.pedidosMetalsCache);
            } else {
                throw new Error(data.error || 'Error desconocido');
            }

        } catch (error) {
            console.error('❌ Error cargando historial metales:', error);
            container.innerHTML = `<div class="alert alert-danger" style="grid-column: 1 / -1;">Error al cargar datos: ${error.message}</div>`;
        }
    },

    setMetalsTab: function(tabName) {
        this.metalsTabActiva = tabName;
        if (this.pedidosMetalsCache) {
            this.renderizarPedidosMetals(this.pedidosMetalsCache);
        }
    },

    renderizarPedidosMetals: function (pedidos) {
        console.log('📦 Pedidos recibidos:', pedidos);
        const container = document.getElementById('metals-historial-container');
        if (!container) return;

        container.innerHTML = ''; 

        // 1. Renderizar Nav-Pills
        const navHtml = `
            <div class="d-flex justify-content-center w-100 mb-4" style="grid-column: 1 / -1;">
                <ul class="nav nav-pills p-1 bg-light rounded-pill shadow-sm" style="border: 1px solid #e2e8f0; display: inline-flex;">
                    <li class="nav-item">
                        <button class="nav-link rounded-pill fw-bold px-4 py-2 ${this.metalsTabActiva === 'proceso' ? 'active shadow' : 'text-muted'}" 
                                onclick="ModuloPedidos.setMetalsTab('proceso')"
                                style="transition: all 0.3s; ${this.metalsTabActiva === 'proceso' ? 'background: #4f46e5; color: white;' : ''}">
                            <i class="fas fa-box me-2"></i>Pedidos en Proceso
                        </button>
                    </li>
                    <li class="nav-item">
                        <button class="nav-link rounded-pill fw-bold px-4 py-2 ${this.metalsTabActiva === 'historial' ? 'active shadow' : 'text-muted'}" 
                                onclick="ModuloPedidos.setMetalsTab('historial')"
                                style="transition: all 0.3s; ${this.metalsTabActiva === 'historial' ? 'background: #10b981; color: white;' : ''}">
                            <i class="fas fa-truck me-2"></i>Pedidos Enviados / Historial
                        </button>
                    </li>
                </ul>
            </div>
        `;

        let html = navHtml;

        // 2. Filtrar pedidos según pestaña
        const estadosHistorial = ['DESPACHADO', 'DESPACHADO PARCIAL', 'ENTREGADO'];
        let pedidosFiltrados = [];
        
        if (this.metalsTabActiva === 'historial') {
            pedidosFiltrados = pedidos.filter(p => estadosHistorial.includes(p.estado?.toUpperCase()));
        } else {
            // Proceso (PENDIENTE, PRODUCCION, FINALIZADO, etc.)
            pedidosFiltrados = pedidos.filter(p => !estadosHistorial.includes(p.estado?.toUpperCase()));
        }

        if (!pedidosFiltrados || pedidosFiltrados.length === 0) {
            html += `
                <div class="text-center py-5 text-muted w-100" style="grid-column: 1 / -1;">
                    <i class="fas ${this.metalsTabActiva === 'historial' ? 'fa-truck-loading' : 'fa-folder-open'} fa-3x mb-3 opacity-20"></i>
                    <p class="fs-5">No se encontraron pedidos en esta categoría</p>
                </div>
            `;
            container.innerHTML = html;
            return;
        }

        // 3. Renderizar Tarjetas
        if (this.metalsTabActiva === 'historial') {
            // Tarjetas simplificadas para historial
            pedidosFiltrados.forEach(ped => {
                const totalFormatted = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }).format(ped.total || 0);
                const fechaEnvio = ped.fecha_despacho || ped.fecha || 'N/A';
                
                let desgloseItems = '';
                if (ped.productos && ped.productos.length > 0) {
                    desgloseItems = ped.productos.slice(0, 3).map(p => `<span class="badge bg-light text-dark border me-1 mb-1">${p.cantidad}x ${p.codigo || p.id_codigo || p.descripcion}</span>`).join('');
                    if (ped.productos.length > 3) desgloseItems += `<span class="badge bg-secondary mb-1">+${ped.productos.length - 3} más</span>`;
                }

                html += `
                    <div class="pedido-card-metals p-3 bg-white rounded-4 shadow-sm border-start border-4 border-success" 
                         style="transition: all 0.3s ease; display: flex; flex-direction: column;">
                        <div class="d-flex justify-content-between align-items-start mb-2">
                            <div>
                                <h5 class="mb-0 fw-bold text-success" style="font-size: 1.1rem;">${ped.id_pedido}</h5>
                                <small class="text-muted"><i class="fas fa-truck-loading me-1"></i> Enviado: ${fechaEnvio}</small>
                            </div>
                            <span class="badge bg-success text-uppercase shadow-sm" style="font-size: 0.65rem; padding: 5px 10px; border-radius: 20px;"><i class="fas fa-check-circle me-1"></i>${ped.estado}</span>
                        </div>
                        
                        <div class="mb-3 mt-2 flex-grow-1" onclick="ModuloPedidos.verDetalleMetals('${ped.id_pedido}', ${JSON.stringify(ped.productos).replace(/"/g, '&quot;')})" style="cursor: pointer;">
                            <div class="small fw-bold text-muted text-uppercase mb-1" style="font-size: 0.6rem; letter-spacing: 0.5px;">Cliente Destino</div>
                            <div class="text-truncate fw-bold" style="color: #1e293b; font-size: 0.95rem;" title="${ped.cliente}">${ped.cliente}</div>
                        </div>

                        <div class="mb-3 bg-light p-2 rounded-3 border" style="font-size: 0.8rem;">
                            <div class="text-muted mb-1" style="font-size: 0.65rem; font-weight: 600;">RESUMEN DE PRODUCTOS</div>
                            <div>${desgloseItems || '<span class="text-muted">Sin detalle de items</span>'}</div>
                        </div>

                        <div class="d-flex justify-content-between align-items-center pt-2 border-top">
                            <div class="d-flex flex-column">
                                <span class="text-muted small" style="font-size: 0.65rem; font-weight: 600;">VALOR ENVIADO</span>
                                <span class="fw-bold text-dark" style="font-size: 1rem;">${totalFormatted}</span>
                            </div>
                            <button class="btn btn-sm btn-outline-success rounded-pill px-3 fw-bold" style="font-size: 0.75rem;" onclick="ModuloPedidos.verDetalleMetals('${ped.id_pedido}', ${JSON.stringify(ped.productos).replace(/"/g, '&quot;')})">
                                <i class="fas fa-eye me-1"></i> AUDITAR
                            </button>
                        </div>
                    </div>
                `;
            });
        } else {
            // Tarjetas normales (En Proceso)
            pedidosFiltrados.forEach(ped => {
                const totalFormatted = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }).format(ped.total || 0);
                const progresoNum = parseInt(String(ped.progreso || 0).replace('%', '')) || 0;
                
                let barColor = 'bg-danger';
                if (progresoNum >= 70) barColor = 'bg-success';
                else if (progresoNum >= 30) barColor = 'bg-warning';

                html += `
                    <div class="pedido-card-metals p-3 bg-white rounded-4 shadow-sm border-start border-4 ${ped.estado === 'PENDIENTE' ? 'border-warning' : 'border-primary'}" 
                         style="transition: all 0.3s ease;">
                        <div class="d-flex justify-content-between align-items-start mb-3">
                            <div>
                                <h5 class="mb-0 fw-bold text-primary" style="font-size: 1.1rem;">${ped.id_pedido}</h5>
                                <small class="text-muted"><i class="fas fa-calendar-alt me-1"></i> ${ped.fecha}</small>
                            </div>
                            <span class="badge ${ped.estado === 'PENDIENTE' ? 'bg-warning text-dark' : 'bg-primary'} text-uppercase shadow-sm" style="font-size: 0.65rem; padding: 5px 10px; border-radius: 20px;">${ped.estado}</span>
                        </div>
                        
                        <div class="mb-3" onclick="ModuloPedidos.verDetalleMetals('${ped.id_pedido}', ${JSON.stringify(ped.productos).replace(/"/g, '&quot;')})" style="cursor: pointer;">
                            <div class="small fw-bold text-muted text-uppercase mb-1" style="font-size: 0.6rem; letter-spacing: 0.5px;">Cliente</div>
                            <div class="text-truncate fw-bold" style="color: #1e293b; font-size: 1rem;" title="${ped.cliente}">${ped.cliente}</div>
                        </div>

                        <!-- Barra de Progreso -->
                        <div class="progress-section mb-4">
                            <div class="d-flex justify-content-between mb-2 align-items-center">
                                <span class="fw-bold text-muted" style="font-size: 0.7rem;">PROGRESO DE PRODUCCIÓN</span>
                                <span class="badge bg-light text-dark border fw-bold" style="font-size: 0.8rem;">${progresoNum}%</span>
                            </div>
                            <div class="progress shadow-sm" style="height: 12px; background-color: #f1f5f9; border-radius: 10px; overflow: hidden;">
                                <div class="progress-bar ${barColor} progress-bar-striped progress-bar-animated" role="progressbar" style="width: ${progresoNum}%; border-radius: 10px;"></div>
                            </div>
                        </div>

                        <div class="d-flex justify-content-between align-items-center mt-3 pt-3 border-top">
                            <div class="d-flex flex-column">
                                <span class="text-muted small" style="font-size: 0.65rem; font-weight: 600;">TOTAL</span>
                                <span class="fw-bold text-dark" style="font-size: 1.1rem;">${totalFormatted}</span>
                            </div>
                            <div class="d-flex gap-2">
                                <button class="btn btn-sm btn-primary rounded-pill px-3 fw-bold" style="font-size: 0.75rem;" onclick="ModuloPedidos.abrirGestionProgreso('${ped.id_pedido}', ${progresoNum})">
                                    <i class="fas fa-tasks me-1"></i> GESTIONAR
                                </button>
                            </div>
                        </div>
                    </div>
                `;
            });
        }

        container.innerHTML = html;
    },

    abrirGestionProgreso: function (idPedido, progresoActual) {
        // Encontrar el pedido para tener el estado actual
        // (Podríamos pasarlo por parámetro, pero lo simplificamos aquí)
        Swal.fire({
            title: `Gestionar Pedido ${idPedido}`,
            html: `
                <div class="text-start p-2">
                    <label class="form-label fw-bold small">Nivel de Progreso:</label>
                    <select id="swal-progreso" class="form-select mb-3">
                        <option value="0" ${progresoActual == 0 ? 'selected' : ''}>0% - Pendiente</option>
                        <option value="25" ${progresoActual == 25 ? 'selected' : ''}>25% - Iniciado</option>
                        <option value="50" ${progresoActual == 50 ? 'selected' : ''}>50% - En Proceso</option>
                        <option value="75" ${progresoActual == 75 ? 'selected' : ''}>75% - Casi Listo</option>
                        <option value="100" ${progresoActual == 100 ? 'selected' : ''}>100% - Completado</option>
                    </select>

                    <label class="form-label fw-bold small">Estado del Pedido:</label>
                    <select id="swal-estado" class="form-select">
                        <option value="PENDIENTE">PENDIENTE</option>
                        <option value="PRODUCCION">EN PRODUCCIÓN</option>
                        <option value="FINALIZADO">FINALIZADO</option>
                    </select>
                </div>
            `,
            showCancelButton: true,
            confirmButtonText: 'Guardar Cambios',
            cancelButtonText: 'Cancelar',
            preConfirm: () => {
                return {
                    progreso: document.getElementById('swal-progreso').value,
                    estado: document.getElementById('swal-estado').value
                }
            }
        }).then((result) => {
            if (result.isConfirmed) {
                this.actualizarEstadoMetals(idPedido, result.value.progreso, result.value.estado);
            }
        });
    },

    actualizarEstadoMetals: async function (idPedido, progreso, estado) {
        try {
            const res = await fetch('/api/pedidos/actualizar-progreso', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id_pedido: idPedido, progreso, estado })
            });
            const data = await res.json();
            if (data.success) {
                Swal.fire('¡Actualizado!', 'El progreso del pedido se ha guardado.', 'success');
                this.cargarHistorialMetals(); // Recargar lista
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            Swal.fire('Error', 'No se pudo actualizar: ' + error.message, 'error');
        }
    },

    verDetalleMetals: function (idPedido, productos) {
        let itemsHtml = productos.map(p => `
            <div class="d-flex justify-content-between align-items-center p-2 border-bottom">
                <div style="flex: 1;">
                    <div class="fw-bold text-dark" style="font-size: 0.9rem;">${p.id_codigo || p.codigo}</div>
                    <div class="small text-muted" style="font-size: 0.8rem;">${p.descripcion}</div>
                </div>
                <div class="text-end" style="min-width: 80px;">
                    <div class="fw-bold">x${p.cantidad}</div>
                    <div class="small text-primary fw-bold">$${new Intl.NumberFormat('es-CO').format(p.precio_unitario || p.precio || 0)}</div>
                </div>
            </div>
        `).join('');

        Swal.fire({
            title: `<div class="text-primary fw-bold">Pedido ${idPedido}</div>`,
            html: `
                <div class="text-start mt-3" style="max-height: 400px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 8px;">
                    ${itemsHtml}
                </div>
            `,
            confirmButtonText: 'Cerrar',
            confirmButtonColor: '#4361ee',
            width: '500px',
            customClass: {
                popup: 'rounded-4 shadow-lg'
            }
        });
    }
};

// Export global
window.ModuloPedidos = ModuloPedidos;

// Hook para compatibilidad (ya cubierto por inicializar pero por seguridad)
if (!window.initPedidos) {
    window.initPedidos = () => ModuloPedidos.inicializar();
}

// Hook para compatibilidad (ya cubierto por inicializar pero por seguridad)
if (!window.initPedidos) {
    window.initPedidos = () => ModuloPedidos.inicializar();
}

// --- INYECCIÓN IMPERATIVA: BOTÓN DE HISTORIAL DE ENVIADOS (GESTIÓN DE PEDIDOS) ---
// DESHABILITADO (Política Cero Tolerancia a Código Zombi): la fuente de "fecha_despacho"
// que consume esta vista (GET /api/pedidos/listar) cae a fecha_creacion cuando no existe
// despacho real en db_despachos_pedido, mostrando "Mismo día" para pedidos nunca despachados.
// Feature incompleta retirada del frontend hasta que se corrija esa fuente de datos.
/*
(function() {
    // 1. ESTADO DE VISTA: Inicializar la propiedad global en el módulo de Pedidos
    ModuloPedidos.vistaActual = "activos";

    const inyectarBotonHistorial = () => {
        // Localizamos la cabecera de la vista Gestión de Pedidos (almacen-page)
        const topControls = document.querySelector('#almacen-page .top-controls');
        if (!topControls) return;

        // 2. BLOQUEO DE AUTO-REFRESH / AUTO-UPDATE:
        // Interceptamos la función cargarPedidos de AlmacenModule
        if (window.AlmacenModule && typeof window.AlmacenModule.cargarPedidos === 'function' && !window.AlmacenModule._cargarPedidosParcheado) {
            const originalCargarPedidos = window.AlmacenModule.cargarPedidos;
            window.AlmacenModule.cargarPedidos = async function(showLoading = true) {
                if (ModuloPedidos.vistaActual === 'historial') { 
                    console.log("⏳ [Auto-Update] Congelado porque el usuario está auditando el historial.");
                    return; 
                }
                return originalCargarPedidos.call(this, showLoading);
            };
            window.AlmacenModule._cargarPedidosParcheado = true;
        }

        const btnActualizar = document.getElementById('btn-refrescar-almacen');
        if (btnActualizar && !document.getElementById('btn-historial-enviados')) {
            // Crear el botón nuevo
            const btnHistorial = document.createElement('button');
            btnHistorial.id = 'btn-historial-enviados';
            btnHistorial.className = 'btn btn-outline-success me-2';
            btnHistorial.innerHTML = '<i class="fas fa-history me-1"></i> Ver Historial Enviados';

            // 3. CONTROL DEL BOTÓN "Ver Historial Enviados":
            btnHistorial.addEventListener('click', async () => {
                const container = document.getElementById('almacen-container');

                if (ModuloPedidos.vistaActual === 'activos') {
                    // Pasar a Historial
                    ModuloPedidos.vistaActual = "historial";
                    btnHistorial.className = 'btn btn-outline-primary me-2';
                    btnHistorial.innerHTML = '<i class="fas fa-box-open me-1"></i> Ver Pedidos Activos';
                    
                    if (container) {
                        container.innerHTML = `
                            <div class="text-center py-5 text-muted">
                                <i class="fas fa-spinner fa-spin fa-3x mb-3 text-primary"></i>
                                <p class="fs-5">Cargando historial de pedidos despachados...</p>
                            </div>
                        `;

                        try {
                            const division = (window.AppState && window.AppState.user && window.AppState.user.division) || 'friparts';
                            const res = await fetch(`/api/pedidos/listar?division=${encodeURIComponent(division)}`);
                            const data = await res.json();
                            
                            if (!data.success) {
                                throw new Error(data.error || 'Error al obtener historial');
                            }

                            const pedidosCargados = data.data.pedidos || [];

                            const enviados = pedidosCargados.filter(p => {
                                const estadoNormalizado = (p.estado || '').toUpperCase();
                                const progreso = String(p.progreso_despacho || '').trim();

                                return estadoNormalizado === 'DESPACHADO' || 
                                       estadoNormalizado === 'ENTREGADO' || 
                                       estadoNormalizado === 'DESPACHADO PARCIAL' ||
                                       progreso === '100%' || 
                                       progreso === '100';
                            });

                            if (enviados.length === 0) {
                                container.innerHTML = `
                                    <div class="text-center py-5 text-muted empty-state-almacen">
                                        <i class="fas fa-truck-loading fa-3x mb-3" style="opacity: 0.2; color: #10b981;"></i>
                                        <p class="fs-5">Aún no hay despachos registrados</p>
                                    </div>
                                `;
                                return;
                            }

                            // Renderizar Tabla Moderna
                            let tablaHtml = `
                                <div class="table-responsive bg-white rounded-4 shadow-sm border p-3 mt-3">
                                    <table class="table table-hover align-middle mb-0">
                                        <thead>
                                            <tr style="background-color: #212529 !important;">
                                                <th style="color: #ffffff !important; background-color: #212529 !important; padding: 10px; font-size: 0.85rem; text-transform: uppercase;">Pedido</th>
                                                <th style="color: #ffffff !important; background-color: #212529 !important; padding: 10px; font-size: 0.85rem; text-transform: uppercase;">Cliente</th>
                                                <th style="color: #ffffff !important; background-color: #212529 !important; padding: 10px; font-size: 0.85rem; text-transform: uppercase;"><i class="fas fa-calendar-alt me-1"></i> F. Ingreso</th>
                                                <th style="color: #ffffff !important; background-color: #212529 !important; padding: 10px; font-size: 0.85rem; text-transform: uppercase;"><i class="fas fa-shipping-fast me-1"></i> F. Despacho</th>
                                                <th style="color: #ffffff !important; background-color: #212529 !important; padding: 10px; font-size: 0.85rem; text-transform: uppercase;" class="text-center"><i class="fas fa-hourglass-half me-1"></i> Días en Proceso</th>
                                                <th style="color: #ffffff !important; background-color: #212529 !important; padding: 10px; font-size: 0.85rem; text-transform: uppercase;" class="text-center">Acciones</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                            `;

                            // Limpieza segura de fechas para evitar 'Invalid Date' por sufijos como 'p.m.'
                            const cleanDateStr = (dateStr) => {
                                if (!dateStr) return null;
                                return typeof dateStr === 'string' ? dateStr.split(' ')[0] : dateStr;
                            };

                            enviados.forEach(p => {
                                const rawIngreso = p.fecha_creacion || p.fecha || null;
                                const fIngresoDate = rawIngreso ? new Date(cleanDateStr(rawIngreso)) : null;
                                
                                const rawDespacho = p.fecha_despacho || p.fecha_modificacion || null;
                                const fDespachoDate = rawDespacho ? new Date(cleanDateStr(rawDespacho)) : null;
                                
                                let diasProceso = 0;
                                let diasTexto = "N/A";
                                let badgeClass = 'bg-light text-secondary border border-secondary';

                                if (fIngresoDate && fDespachoDate && !isNaN(fIngresoDate.getTime()) && !isNaN(fDespachoDate.getTime())) {
                                    const diferenciaTiempo = Math.abs(fDespachoDate - fIngresoDate);
                                    diasProceso = Math.ceil(diferenciaTiempo / (1000 * 60 * 60 * 24)) - 1;
                                    diasTexto = diasProceso <= 0 ? "Mismo día" : `${diasProceso} ${diasProceso === 1 ? 'día' : 'días'}`;
                                    
                                    badgeClass = 'bg-light text-success border border-success border-opacity-25';
                                    if (diasProceso >= 5) {
                                        badgeClass = 'bg-warning text-dark border border-warning';
                                    } else if (diasProceso >= 3) {
                                        badgeClass = 'bg-light text-secondary border border-secondary';
                                    }
                                }

                                const formatFecha = (d) => d && !isNaN(d.getTime()) ? d.toISOString().split('T')[0] : 'N/A';
                                const fechaIngresoStr = typeof rawIngreso === 'string' ? rawIngreso : (rawIngreso ? formatFecha(fIngresoDate) : 'N/A');
                                const fechaDespachoStr = typeof rawDespacho === 'string' ? rawDespacho : (rawDespacho ? formatFecha(fDespachoDate) : 'N/A');

                                tablaHtml += `
                                    <tr>
                                        <td class="fw-bold text-primary">${p.id_pedido}</td>
                                        <td>
                                            <div class="fw-bold" style="color: #1e293b;">${p.cliente}</div>
                                        </td>
                                        <td class="text-muted" style="white-space: nowrap;"><i class="far fa-calendar-plus me-1"></i>${fechaIngresoStr}</td>
                                        <td class="text-muted" style="white-space: nowrap;"><i class="fas fa-truck-loading me-1"></i>${fechaDespachoStr}</td>
                                        <td class="text-center">
                                            <span class="badge ${badgeClass} shadow-sm" style="padding: 6px 10px; border-radius: 20px;">
                                                <i class="far fa-clock me-1"></i>${diasTexto}
                                            </span>
                                        </td>
                                        <td class="text-end">
                                            <button class="btn btn-sm btn-outline-secondary rounded-pill px-3 fw-bold" onclick="ModuloPedidos.verDetalleMetals('${p.id_pedido}', ${JSON.stringify(p.productos || []).replace(/"/g, '&quot;')})">
                                                <i class="fas fa-search me-1"></i> Auditar
                                            </button>
                                        </td>
                                    </tr>
                                `;
                            });

                            tablaHtml += `
                                        </tbody>
                                    </table>
                                </div>
                            `;
                            container.innerHTML = tablaHtml;

                        } catch (error) {
                            console.error('❌ Error al cargar historial:', error);
                            container.innerHTML = `
                                <div class="alert alert-danger mt-3">
                                    <i class="fas fa-exclamation-triangle me-2"></i> Error al cargar el historial: ${error.message}
                                </div>
                            `;
                        }
                    }
                } else {
                    // Pasar a Activos
                    ModuloPedidos.vistaActual = "activos";
                    btnHistorial.className = 'btn btn-outline-success me-2';
                    btnHistorial.innerHTML = '<i class="fas fa-history me-1"></i> Ver Historial Enviados';
                    
                    if (window.AlmacenModule && typeof window.AlmacenModule.cargarPedidos === 'function') {
                        if (container) container.innerHTML = '<div class="text-center py-5 text-muted"><i class="fas fa-spinner fa-spin fa-3x mb-3"></i><p>Restaurando tarjetas de pedidos activos...</p></div>';
                        window.AlmacenModule.cargarPedidos();
                    }
                }
            });

            // Inyectar justo a la izquierda del botón 'Actualizar'
            topControls.insertBefore(btnHistorial, btnActualizar);
            console.log("✅ Botón de Historial de Enviados inyectado correctamente en Gestión de Pedidos.");
        }
    };

    // Asegurar inyección sin importar cuándo cargue este script
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(inyectarBotonHistorial, 100));
    } else {
        setTimeout(inyectarBotonHistorial, 500);
    }
})();
*/
