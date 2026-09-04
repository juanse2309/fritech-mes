// ============================================
// inyeccion.js - Lógica de Inyección (SMART SEARCH) - NAMESPACED
// ============================================

// ============================================================
// ESTADO REACTIVO DE LA TABLA DE ITEMS (renderTablaItems)
// ------------------------------------------------------------
// Antes: this.items se mutaba en ~9 sitios distintos (agregarItem,
// removerItem, editarItem, calculos, _sincronizarItemsConRespuestaServidor,
// _restaurarItemsDraft, limpiarFormularioValidacion, confirmarRegistroFinal,
// seleccionarLoteValidacion) y cada uno tenia que acordarse de llamar
// this.renderTablaItems() a mano -- el origen tipico de "estado zombi"
// (se muta pero no se repinta a tiempo, o se repinta antes de que la
// mutacion termine).
//
// Ahora this.items es un getter/setter (ver ModuloInyeccion.items mas
// abajo) que delega en TablaInyeccionState.items, un array envuelto en
// Proxy: cualquier mutacion (incluidos push/splice, que internamente
// son sets sobre indices y length) programa un render en el siguiente
// microtask. Varias mutaciones sincronas seguidas (ej. splice, que
// dispara varios sets) colapsan en un unico render.
// ============================================================
let _renderTablaProgramado = false;
function _programarRenderTablaInyeccion() {
    if (_renderTablaProgramado) return;
    _renderTablaProgramado = true;
    queueMicrotask(() => {
        _renderTablaProgramado = false;
        ModuloInyeccion.renderUI();
    });
}

function _envolverArrayReactivo(arr) {
    return new Proxy(arr, {
        set(target, prop, value) {
            target[prop] = value;
            _programarRenderTablaInyeccion();
            return true;
        },
        deleteProperty(target, prop) {
            delete target[prop];
            _programarRenderTablaInyeccion();
            return true;
        }
    });
}

// status: 'ready' | 'loading'. El unico gap de carga real de esta tabla
// es el await de responsablesPulido dentro de seleccionarLoteValidacion
// (ver mas abajo) -- ahi es donde se usa 'loading'.
const TablaInyeccionState = new Proxy(
    { items: _envolverArrayReactivo([]), status: 'ready' },
    {
        set(target, prop, value) {
            target[prop] = (prop === 'items' && Array.isArray(value)) ? _envolverArrayReactivo(value) : value;
            _programarRenderTablaInyeccion();
            return true;
        }
    }
);

const ModuloInyeccion = {
    productosData: [],
    responsablesData: [],
    responsablesPulido: [], // Personal de PULIDO (filtrado server-side vía ?rol=PULIDO), para el selector de Operaria Pulido
    _responsablesPulidoPromise: null, // Memoiza el fetch para que cualquier caller pueda hacer 'await' sin duplicar la petición
    criteriosPnc: null,      // Catálogo canónico servido por el backend (/api/pnc/criterios)
    // Getter/setter: this.items sigue comportandose como un array normal
    // para todo el resto del archivo (push/splice/reasignacion completa
    // this.items = [...] funcionan igual), pero delega en
    // TablaInyeccionState.items para que quede reactivo. Ver el bloque
    // de estado reactivo arriba de esta declaracion.
    get items() { return TablaInyeccionState.items; },
    set items(v) { TablaInyeccionState.items = v; },
    isInitialized: false,
    isFetching: false,
    pncRows: [], // Lista de PNC dinámicos para el cierre
    currentModule: 'operator', // 'operator' (Reporte Máquina) o 'validation' (Validación Paola)

    
    normalizarCodigo: function(c) {
        if (!c) return "";
        return String(c).toUpperCase().replace(/FR-/gi, "").trim();
    },

    // =========================================================
    // PERSISTENCIA DE LA LISTA EN VIVO (localStorage)
    // ---------------------------------------------------------
    // this.items (la "Lista de Producción Agregada") no es estado de
    // formulario: se pinta a mano en <tbody> y se muta vía onchange
    // handlers, por lo que FormAutoSave (que solo serializa
    // input/select/textarea) nunca la ve. Una recarga accidental del
    // navegador en planta perdía en silencio todo lo tecleado. Se
    // persiste solo el sub-módulo de Reporte de Máquina (operario
    // llenando a mano): en modo Validación this.items se reconstruye
    // siempre desde pendientesData (servidor) al elegir el lote, así
    // que "recordar" un borrador ahí arriesgaría mezclar una auditoría
    // vieja con datos ya cerrados en la base.
    // =========================================================
    _ITEMS_DRAFT_TTL_MS: 12 * 60 * 60 * 1000, // 12h, igual que FormAutoSave
    _draftSaveTimer: null,

    _getCurrentUsernameSafe: function () {
        const user = window.AppState?.user || (typeof AuthModule !== 'undefined' ? AuthModule.currentUser : null);
        return user?.name || user?.nombre || '';
    },

    _getItemsDraftKey: function () {
        const username = (this._getCurrentUsernameSafe() || 'anonymous').toLowerCase().trim().replace(/\s+/g, '_');
        return `inyeccion_items_draft_${encodeURIComponent(username)}`;
    },

    _guardarItemsDraft: function () {
        if (this.esValidacionMode) return;

        clearTimeout(this._draftSaveTimer);
        this._draftSaveTimer = setTimeout(() => {
            try {
                const key = this._getItemsDraftKey();
                if (!this.items || this.items.length === 0) {
                    localStorage.removeItem(key);
                    return;
                }
                localStorage.setItem(key, JSON.stringify({
                    timestamp: Date.now(),
                    items: this.items
                }));
            } catch (e) {
                console.error('[Inyeccion] Error guardando borrador de lista:', e);
            }
        }, 400);
    },

    _limpiarItemsDraft: function () {
        try {
            localStorage.removeItem(this._getItemsDraftKey());
        } catch (e) {
            console.error('[Inyeccion] Error limpiando borrador de lista:', e);
        }
    },

    _restaurarItemsDraft: function () {
        if (this.esValidacionMode || (this.items && this.items.length > 0)) return;

        try {
            const key = this._getItemsDraftKey();
            const raw = localStorage.getItem(key);
            if (!raw) return;

            const payload = JSON.parse(raw);
            if (!payload || !Array.isArray(payload.items) || payload.items.length === 0) return;

            if (Date.now() - (payload.timestamp || 0) > this._ITEMS_DRAFT_TTL_MS) {
                localStorage.removeItem(key);
                return;
            }

            this.items = payload.items;
            this.renderTablaItems();

            Swal.fire({
                title: 'Lista recuperada',
                text: `Se restauraron ${this.items.length} producto(s) que no se habían guardado antes de la recarga.`,
                icon: 'info',
                toast: true,
                position: 'top-end',
                showConfirmButton: false,
                timer: 4500
            });
        } catch (e) {
            console.error('[Inyeccion] Error restaurando borrador de lista:', e);
        }
    },

    // Espera a que la identidad del usuario esté resuelta antes de restaurar:
    // sin esto, en el instante en que carga la página la clave caería en
    // "anonymous" y podría mostrarle a un operario el borrador de otro.
    _restaurarItemsDraftCuandoListo: function () {
        if (this._getCurrentUsernameSafe()) {
            this._restaurarItemsDraft();
            return;
        }

        let resuelto = false;
        const intentarResolver = () => {
            if (resuelto || !this._getCurrentUsernameSafe()) return;
            resuelto = true;
            this._restaurarItemsDraft();
            window.removeEventListener('user-ready', handler);
            document.removeEventListener('user-ready', handler);
            clearInterval(interval);
        };
        const handler = () => intentarResolver();
        window.addEventListener('user-ready', handler);
        document.addEventListener('user-ready', handler);

        // Respaldo por sondeo: si el evento 'user-ready' ya se disparó antes de
        // que este listener quedara registrado (init() tarda varios fetches en
        // llegar hasta aquí), el listener por sí solo nunca vería el evento y el
        // borrador quedaría sin restaurar. Mismo patrón que
        // intentarAutoSeleccionarResponsable.
        let attempts = 0;
        const interval = setInterval(() => {
            attempts++;
            intentarResolver();
            if (resuelto || attempts > 10) clearInterval(interval);
        }, 500);
    },

    init: async function () {
        if (this.isInitialized) return;
        console.log('🔧 [Inyeccion] Inicializando...');
        await this.cargarDatos();
        this.configurarEventos();
        this.initAutocompleteProducto();
        this.initAutocompleteResponsable();
        this.intentarAutoSeleccionarResponsable();

        // Inicializar fecha
        const fechaHoy = new Date().toISOString().split('T')[0];
        const fechaInput = document.getElementById('fecha-inyeccion');
        if (fechaInput && !fechaInput.value) fechaInput.value = fechaHoy;

        // Configurar Smart Enter
        if (window.ModuloUX && window.ModuloUX.setupSmartEnter) {
            window.ModuloUX.setupSmartEnter({
                inputIds: [
                    'fecha-inyeccion', 'maquina-inyeccion', 
                    'hora-llegada-inyeccion', 'hora-inicio-inyeccion', 'hora-termina-inyeccion',
                    'peso-vela-inyeccion', 'orden-produccion-inyeccion',
                    'codigo-producto-inyeccion', 'cavidades-inyeccion', 'cantidad-inyeccion',
                    'cantidad-real-inyeccion', 'pnc-inyeccion', 'peso-bujes-inyeccion', 'observaciones-inyeccion'
                ],
                actionBtnId: 'btn-agregar-inyeccion',
                autocomplete: {
                    inputId: 'codigo-producto-inyeccion',
                    suggestionsId: 'inyeccion-producto-suggestions'
                }
            });
        }

        // Registrar persistencia de formulario Juan Sebastian Request
        if (window.FormHelpers) {
            window.FormHelpers.registrarPersistencia('form-inyeccion');
        }

        // Persistencia de la Lista de Producción Agregada (this.items): a
        // diferencia de los campos del <form>, que FormAutoSave ya cubre,
        // estas filas viven solo en memoria y una recarga accidental en
        // planta las borraba sin aviso. Ver _restaurarItemsDraftCuandoListo.
        this._restaurarItemsDraftCuandoListo();

        this.isInitialized = true;
    },

    _idTurnoActivo: null, // ID para persistencia (Flujo directo)


    cargarDatos: async function () {
        if (this.isFetching) return;
        try {
            this.isFetching = true;
            if (typeof window.mostrarLoading === 'function') window.mostrarLoading(true);

            console.log('📦 [Inyeccion] Cargando datos...');

            // 1. Cargar Responsables
            const responsables = await fetchData('/api/obtener_responsables');
            if (responsables) {
                // Mapear de objetos a strings para mantener compatibilidad con el buscador
                this.responsablesData = responsables.map(r => typeof r === 'object' ? r.nombre : r);
            }

            // 1.1 Operarias de Pulido: filtrado SERVER-SIDE (?rol=PULIDO) para el
            // selector de Operaria Pulido. El endpoint ya excluye admin/sistema.
            // El catálogo general (this.responsablesData) no debe usarse ahí:
            // mostraba comerciales y administradores porque no estaba acotado.
            await this._cargarResponsablesPulido();

            // 1.2 Catálogo canónico de criterios PNC (fuente única en el backend)
            await this.cargarCriteriosPnc();

            // 1.5 Cargar pendientes de validacion
            await this.cargarPendientesValidacion();

            // 2. Cargar Productos (Cache Compartido)
            if (window.AppState.sharedData.productos && window.AppState.sharedData.productos.length > 0) {
                this.productosData = window.AppState.sharedData.productos;
                console.log('✅ [Inyeccion] Usando productos del cache compartido:', this.productosData.length);
            } else {
                console.warn('⚠️ [Inyeccion] Cache vacío, intentando fetch...');
                const prods = await fetchData('/api/productos/listar');
                this.productosData = prods?.productos || prods?.items || (Array.isArray(prods) ? prods : []);
            }

            // 3. Cargar Máquinas
            const maquinas = await fetchData('/api/obtener_maquinas');
            this.actualizarSelect('maquina-inyeccion', maquinas);

            if (typeof window.mostrarLoading === 'function') window.mostrarLoading(false);
        } catch (error) {
            console.error('Error [Inyeccion] cargarDatos:', error);
            if (typeof window.mostrarLoading === 'function') window.mostrarLoading(false);
        } finally {
            this.isFetching = false;
        }
    },

    // Carga this.responsablesPulido y memoiza la promesa: `seleccionarLoteValidacion`
    // se dispara desde un onchange inline en el HTML (index.html), interactivo
    // desde que el DOM parsea — independiente de si ModuloInyeccion.init()/
    // cargarDatos() ya terminó. Sin este guard, seleccionar un lote a validar
    // muy rápido tras cargar la página podía ganarle la carrera al fetch y
    // renderTablaItems() construía el <select> de Operaria Pulido sin opciones.
    // Cualquier caller que necesite el catálogo antes de renderizar debe hacer
    // `await this._cargarResponsablesPulido()` primero.
    _cargarResponsablesPulido: function () {
        if (this._responsablesPulidoPromise) return this._responsablesPulidoPromise;

        this._responsablesPulidoPromise = (async () => {
            try {
                const datos = await fetchData('/api/obtener_responsables?rol=PULIDO');
                this.responsablesPulido = (datos || []).map(r => typeof r === 'object' ? r.nombre : r);
            } catch (err) {
                console.error('Error [Inyeccion] cargando responsablesPulido:', err);
                this.responsablesPulido = [];
                this._responsablesPulidoPromise = null; // permitir reintentar en la próxima llamada
            }
            return this.responsablesPulido;
        })();

        return this._responsablesPulidoPromise;
    },

    cargarCriteriosPnc: async function () {
        // Deliberadamente SIN fallback a una lista local: los catálogos locales
        // divergían de los del backend y ese era justamente el defecto. Si el
        // endpoint falla, los modales avisan en vez de ofrecer criterios que el
        // normalizador no reconoce y terminarían agregados como "Otros".
        try {
            const res = await fetchData('/api/pnc/criterios');
            if (res && res.success && res.criterios) {
                this.criteriosPnc = res.criterios;
                console.log('✅ [Inyeccion] Catálogo canónico de criterios PNC cargado:', this.criteriosPnc);
            } else {
                this.criteriosPnc = null;
            }
        } catch (err) {
            console.error('Error [Inyeccion] cargando criterios PNC:', err);
            this.criteriosPnc = null;
        }
    },

    pendientesData: [], // Store the raw data from backend

    formatearFechaParaInput: function (fechaStr) {
        if (!fechaStr) return '';
        // DD/MM/YYYY -> YYYY-MM-DD
        const parts = fechaStr.split('/');
        if (parts.length === 3) {
            return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
        }
        return fechaStr;
    },

    formatearHoraParaInput: function (horaStr) {
        if (!horaStr) return '';
        // H:mm -> HH:mm
        const parts = horaStr.split(':');
        if (parts.length === 2) {
            return `${parts[0].padStart(2, '0')}:${parts[1].padStart(2, '0')}`;
        }
        return horaStr;
    },

    cargarPendientesValidacion: async function () {
        try {
            const select = document.getElementById('select-validar-lote');
            if (!select) return;

            select.innerHTML = '<option value="">Cargando...</option>';
            const res = await fetchData('/api/mes/pendientes_validacion');

            select.innerHTML = '<option value="">-- Seleccionar lote a validar (o dejar vacío para registro manual) --</option>';

            if (res && res.success && res.data) {
                this.pendientesData = res.data;
                console.log(`📋 [Inyeccion] ${res.data.length} pendientes cargados:`, res.data);

                // Agrupar por id_inyeccion para mostrar un solo lote por cada montaje mixto
                const grupos = {};
                res.data.forEach(lote => {
                    const id = lote.id_inyeccion;
                    if (!grupos[id]) {
                        grupos[id] = {
                            id: id,
                            maquina: lote.maquina || 'S/M',
                            ordenProduccion: lote.orden_produccion || 'SIN OP',
                            fechaDisplay: lote.fecha_display || '',
                            codigos: [],
                            cantidadTotal: 0
                        };
                    }
                    if (lote.id_codigo) grupos[id].codigos.push(lote.id_codigo);
                    grupos[id].cantidadTotal += parseFloat(lote.cantidad_real) || 0;
                });

                Object.values(grupos).forEach(g => {
                    const opt = document.createElement('option');
                    opt.value = g.id;
                    const codigosTexto = [...new Set(g.codigos)].join(', ');
                    const fechaTexto = g.fechaDisplay ? ` - ${g.fechaDisplay}` : '';
                    opt.textContent = `Lote: ${g.id} - OP: ${g.ordenProduccion} - Maq: ${g.maquina}${fechaTexto} - Códigos: ${codigosTexto}`;
                    select.appendChild(opt);
                });
            }
        } catch (err) {
            console.error("Error cargando pendientes validacion:", err);
            const select = document.getElementById('select-validar-lote');
            if (select) select.innerHTML = '<option value="">Error cargando pendientes</option>';
        }
    },

    seleccionarLoteValidacion: async function () {
        const select = document.getElementById('select-validar-lote');
        const idValidacion = select ? select.value : '';
        console.log(`🎯 [Inyeccion] Lote seleccionado ID: "${idValidacion}"`);

        if (!idValidacion) {
            console.log('🧹 [Inyeccion] No hay ID, limpiando formulario.');
            this.limpiarFormularioValidacion();
            return;
        }

        // Este handler cuelga de un onchange inline en el HTML: puede dispararse
        // antes de que ModuloInyeccion.init()/cargarDatos() termine. Esperar aquí
        // garantiza que this.responsablesPulido ya esté poblado antes de que
        // renderTablaItems() construya los <select> de Operaria Pulido más abajo.
        TablaInyeccionState.status = 'loading';
        await this._cargarResponsablesPulido();
        TablaInyeccionState.status = 'ready';

        const registrosDelLote = this.pendientesData.filter(l => l.id_inyeccion === idValidacion);
        if (registrosDelLote.length === 0) {
            console.error('❌ [Inyeccion] No se encontraron registros para este ID.');
            return;
        }

        const lotePrincipal = registrosDelLote[0];

        const container = document.getElementById('contenedor-agregar-producto-inyeccion');
        if (container) container.classList.add('d-none');

        this.limpiarFormularioValidacion(false);
        this.esValidacionMode = true; // Activar modo validación inmediatamente
        this._idTurnoActivo = idValidacion; // Preservar ID de lote seleccionado
        this._idSqlActivo = lotePrincipal.id_sql; // Capturar ID SQL del registro principal

        if (document.getElementById('fecha-inyeccion')) {
            document.getElementById('fecha-inyeccion').value = (lotePrincipal.fecha || '').split('T')[0];
        }
        if (document.getElementById('maquina-inyeccion')) document.getElementById('maquina-inyeccion').value = lotePrincipal.maquina || '';
        if (document.getElementById('responsable-inyeccion')) document.getElementById('responsable-inyeccion').value = lotePrincipal.responsable || '';

        // Tarea 2: Llenar horas automáticamente
        if (document.getElementById('hora-inicio-inyeccion')) {
            document.getElementById('hora-inicio-inyeccion').value = lotePrincipal.hora_inicio || '';
        }
        if (document.getElementById('hora-termina-inyeccion')) {
            document.getElementById('hora-termina-inyeccion').value = lotePrincipal.hora_fin || '';
        }
        if (document.getElementById('hora-llegada-inyeccion')) {
            document.getElementById('hora-llegada-inyeccion').value = '06:00';
        }
        if (document.getElementById('orden-produccion-inyeccion')) {
            document.getElementById('orden-produccion-inyeccion').value = lotePrincipal.orden_produccion || '';
        }

        // 2. Poblar los Items desde los registros del lote
        registrosDelLote.forEach(reg => {
            const cavs = reg.cavidades || 1;
            const cantReal = parseFloat(String(reg.cantidad_real || 0).replace(/[^0-9.]/g, '')) || 0;
            const pncVal = parseFloat(String(reg.pnc || 0).replace(/[^0-9.]/g, '')) || 0;
            
            // Tarea 1: Calcular disparos reales
            const dispCalc = Math.ceil(cantReal / cavs);
            
            // Ajuste de Bruto vs Buenas Juan Sebastian Request
            // Inyectadas = Buenas + PNC + Revueltos + WIP
            const brutoReal = cantReal + pncVal + (reg.revueltos || 0) + (reg.wip || 0);

            const nuevoItem = {
                id_item: Date.now().toString() + Math.random().toString(36).substr(2, 5),
                codigo_producto: reg.id_codigo || reg.codigo_sistema,
                no_cavidades: cavs,
                disparos: dispCalc,
                cantidad_real: brutoReal, // TOTAL (Buenas + PNC)
                manual_buenas: cantReal,   // BUENAS
                pnc: pncVal,
                piezasBuenas: cantReal,
                observaciones: reg.molde || '',
                id_inyeccion: reg.id_inyeccion,
                id_sql: reg.id_sql, // <--- CAPTURAR ID PARA EVITAR SOBRESCRITURA
                wip: reg.wip || 0,
                revueltos: reg.revueltos || 0
            };
            
            this.items.push(nuevoItem);
        });

        this.renderTablaItems();

        // Marcar explícitamente que estamos en modo validación
        this.esValidacionMode = true; 

        // Mostrar botón de VALIDACIÓN RÁPIDA en el header del form
        this.mostrarBotonValidacionRapida(idValidacion);
    },

    mostrarBotonValidacionRapida: function(idInyeccion) {
        const actions = document.querySelector('.form-actions');
        if (!actions) return;

        // Eliminar si ya existe
        const existing = document.getElementById('btn-validar-directo');
        if (existing) existing.remove();

        const btn = document.createElement('button');
        btn.id = 'btn-validar-directo';
        btn.type = 'button';
        btn.className = 'btn btn-success btn-lg mb-2';
        btn.style.width = '100%';
        btn.innerHTML = `<i class="fas fa-check-double me-2"></i> Validar y Registrar Lote`;
        // Único camino de validación. Antes apuntaba a confirmarRegistroFinal()
        // (POST /api/inyeccion/lote), que era una segunda ruta de cierre con
        // reglas distintas: escribía la merma de pulido en db_pnc_inyeccion y
        // firmaba validado_por con el operario del formulario.
        btn.onclick = () => this.validarRegistro(idInyeccion);

        actions.prepend(btn);

        // Ocultar botón submit original para evitar confusiones en planta
        const btnSubmitOriginal = document.querySelector('#form-inyeccion button[type="submit"]');
        if (btnSubmitOriginal) btnSubmitOriginal.classList.add('d-none');
    },

    validarRegistro: async function (idInyeccion) {
        if (!idInyeccion) return;

        // Guard: si hay merma de pulido que atribuir pero el catálogo de
        // operarias nunca cargó, el <select> no tiene opciones y el guard de
        // abajo bloquearía sin que el usuario pueda resolverlo. Avisar explícito.
        const requierePulido = this.items.some(i => (i.pnc_pulido || 0) > 0);
        if (requierePulido && (!this.responsablesPulido || this.responsablesPulido.length === 0)) {
            Swal.fire('Error', 'No se pudo cargar el catálogo de operarias de pulido. Contacte a soporte o recargue la página.', 'error');
            return;
        }

        // Guard: toda merma de pulido debe quedar atribuida a una persona.
        const sinOperaria = this.items.find(i => (i.pnc_pulido || 0) > 0 && !i.operaria_pulido);
        if (sinOperaria) {
            Swal.fire(
                'Falta la operaria de pulido',
                `Selecciona la operaria responsable de la merma de pulido de ${sinOperaria.codigo_producto}.`,
                'warning'
            );
            return;
        }

        const result = await Swal.fire({
            title: '¿Validar Lote?',
            text: `Se marcarán todos los registros del lote ${idInyeccion} como VALIDADOS.`,
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: 'Sí, Validar',
            cancelButtonText: 'Cancelar'
        });

        if (result.isConfirmed) {
            try {
                mostrarLoading(true, 'Validando lote...');

                // Payload unificado de validación. No lleva identidad del
                // validador: la firma la resuelve el backend desde el JWT.
                // Incluye Contador/Cav./Buenas editados en esta pantalla --
                // antes solo viajaba el PNC y el backend recalculaba todo
                // desde el registro original, así que cualquier corrección
                // hecha aquí (necesaria para que lo exportado a WO cuadre)
                // se perdía en silencio.
                const payload = {
                    items: this.items.map(i => ({
                        codigo: i.codigo_producto,
                        pnc_inyeccion: i.pnc || 0,
                        pnc_pulido: i.pnc_pulido || 0,
                        operaria_pulido: i.operaria_pulido || '',
                        pnc_list: i.pnc_list || [],
                        pnc_pulido_list: i.pnc_pulido_list || [],
                        disparos: i.disparos || 0,
                        no_cavidades: i.no_cavidades || 1,
                        buenas: (i.piezasBuenas !== undefined && i.piezasBuenas !== null)
                            ? i.piezasBuenas
                            : (i.manual_buenas != null ? i.manual_buenas : 0)
                    }))
                };

                const res = await fetchData(`/api/inyeccion/validar/${idInyeccion}`, {
                    method: 'POST',
                    body: payload
                });
                mostrarLoading(false);

                if (res && res.success) {
                    Swal.fire('¡Validado!', res.data?.message, 'success');
                    if (window.FormHelpers) window.FormHelpers.limpiarPersistencia('form-inyeccion');
                    this.limpiarFormularioValidacion(true);
                    if (window.ModuloHistorial && typeof window.ModuloHistorial.cargarHistorial === 'function') {
                        window.ModuloHistorial.cargarHistorial();
                    }
                } else {
                    Swal.fire('Error', res?.error || 'No se pudo validar', 'error');
                }
            } catch (err) {
                mostrarLoading(false);
                console.error("Error validando:", err);
                Swal.fire('Error', 'Error de conexión', 'error');
            }
        }
    },

    limpiarFormularioValidacion: function (limpiarSelect = true) {
        this.esValidacionMode = false; // Resetear modo validación
        if (limpiarSelect) {
            const select = document.getElementById('select-validar-lote');
            if (select) select.value = '';

            const container = document.getElementById('contenedor-agregar-producto-inyeccion');
            if (container) container.classList.remove('d-none');
        }

        if (document.getElementById('legacy-id-inyeccion')) {
            document.getElementById('legacy-id-inyeccion').value = '';
        }
        if (document.getElementById('legacy-id-programacion')) {
            document.getElementById('legacy-id-programacion').value = '';
        }

        // Remover botón temporal
        const existing = document.getElementById('btn-validar-directo');
        if (existing) existing.remove();

        // Mostrar botón submit original
        const btnSubmitOriginal = document.querySelector('#form-inyeccion button[type="submit"]');
        if (btnSubmitOriginal) btnSubmitOriginal.classList.remove('d-none');

        this.items = [];
        this.renderTablaItems();
        this.limpiarFormularioProducto();
        document.getElementById('hora-llegada-inyeccion').value = '';
        document.getElementById('hora-inicio-inyeccion').value = '';
        document.getElementById('hora-termina-inyeccion').value = '';
        document.getElementById('orden-produccion-inyeccion').value = '';
        document.getElementById('peso-vela-inyeccion').value = 0;
        this.intentarAutoSeleccionarResponsable();
    },

    actualizarSelect: function (id, datos) {
        const select = document.getElementById(id);
        if (!select) return;
        select.innerHTML = '<option value="">-- Seleccionar --</option>';
        if (datos) {
            datos.forEach(item => {
                const opt = document.createElement('option');
                opt.value = item;
                opt.textContent = item;
                select.appendChild(opt);
            });
        }
    },

    intentarAutoSeleccionarResponsable: function () {
        const input = document.getElementById('responsable-inyeccion');
        if (!input) return;

        let nombreUsuario = null;

        if (window.AppState?.user?.name) {
            nombreUsuario = window.AppState.user.name;
        } else if (window.AuthModule?.currentUser?.nombre) {
            nombreUsuario = window.AuthModule.currentUser.nombre;
        }

        if (nombreUsuario) {
            input.value = nombreUsuario;
            console.log(`✅ [Inyeccion] Responsable auto-asignado: ${nombreUsuario}`);
        } else {
            console.log('⏳ [Inyeccion] Esperando usuario para auto-asignación...');
            const handler = () => {
                this.intentarAutoSeleccionarResponsable();
                window.removeEventListener('user-ready', handler);
            };
            window.addEventListener('user-ready', handler);

            // Polling fallback
            let attempts = 0;
            const interval = setInterval(() => {
                attempts++;
                if (window.AppState?.user?.name) {
                    this.intentarAutoSeleccionarResponsable();
                    clearInterval(interval);
                    window.removeEventListener('user-ready', handler);
                }
                if (attempts > 10) clearInterval(interval);
            }, 500);
        }
    },

    initAutocompleteProducto: function () {
        const input = document.getElementById('codigo-producto-inyeccion');
        const suggestionsDiv = document.getElementById('inyeccion-producto-suggestions');

        if (!input || !suggestionsDiv) return;

        let debounceTimer;

        input.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            const query = e.target.value.trim().toLowerCase();

            if (query.length < 2) {
                suggestionsDiv.classList.remove('active');
                return;
            }

            debounceTimer = setTimeout(() => {
                const terms = query.split(/\s+/).filter(t => t.length > 0);
                const resultados = this.productosData.filter(prod => {
                    const codigo = String(prod.codigo_sistema || '').toLowerCase();
                    const descripcion = String(prod.descripcion || '').toLowerCase();
                    return terms.every(term => 
                        codigo.includes(term) || 
                        descripcion.includes(term) ||
                        codigo.replace(/[-\s]/g, '').includes(term.replace(/[-\s]/g, ''))
                    );
                }).slice(0, 15);

                this.renderSuggestions(suggestionsDiv, resultados, (item) => {
                    input.value = item.codigo_sistema || item.codigo;
                    this.autocompletarCodigoEnsamble(input.value);
                    suggestionsDiv.classList.remove('active');
                }, true);
            }, 300);
        });

        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });
    },

    initAutocompleteResponsable: function () {
        const input = document.getElementById('responsable-inyeccion');
        const suggestionsDiv = document.getElementById('inyeccion-responsable-suggestions');

        if (!input || !suggestionsDiv) return;

        input.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            if (query.length < 1) {
                suggestionsDiv.classList.remove('active');
                return;
            }

            const resultados = this.responsablesData.filter(resp =>
                resp.toLowerCase().includes(query)
            );

            this.renderSuggestions(suggestionsDiv, resultados, (item) => {
                input.value = item;
                suggestionsDiv.classList.remove('active');
            }, false);
        });

        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });
    },

    renderSuggestions: function (container, items, onSelect, isProduct) {
        if (isProduct) {
            renderProductSuggestions(container, items, onSelect);
        } else {
            if (items.length === 0) {
                container.innerHTML = '<div class="suggestion-item">No se encontraron resultados</div>';
                container.classList.add('active');
                return;
            }

            container.innerHTML = items.map(item => `<div class="suggestion-item" data-val="${item}">${item}</div>`).join('');

            container.querySelectorAll('.suggestion-item').forEach((div, index) => {
                div.addEventListener('click', () => {
                    onSelect(items[index]);
                });
            });

            container.classList.add('active');
        }
    },

    autocompletarCodigoEnsamble: async function (codigoProducto) {
        const codigoEnsambleField = document.getElementById('codigo-ensamble-inyeccion');
        if (!codigoProducto || !codigoEnsambleField) return;

        try {
            codigoEnsambleField.value = 'Buscando...';
            codigoEnsambleField.classList.add('loading');

            const response = await fetch(`/api/inyeccion/ensamble_desde_producto?codigo=${encodeURIComponent(codigoProducto)}`);
            const data = await response.json();

            if (data.success && data.data?.codigo_ensamble) {
                codigoEnsambleField.value = data.data.codigo_ensamble;
            } else {
                codigoEnsambleField.value = '';
            }
        } catch (error) {
            console.error('Error [Inyeccion] buscando ensamble:', error);
            codigoEnsambleField.value = codigoProducto;
        } finally {
            codigoEnsambleField.classList.remove('loading');
        }
    },

    configurarEventos: function () {
        const form = document.getElementById('form-inyeccion');
        if (form) {
            form.onsubmit = (e) => {
                e.preventDefault();
                this.registrar();
            };
        }

        const btnAgregar = document.getElementById('btn-agregar-inyeccion');
        if (btnAgregar) {
            btnAgregar.onclick = () => this.agregarItem();
        }

        const btnDefectos = document.getElementById('btn-defectos-inyeccion');
        if (btnDefectos) {
            btnDefectos.replaceWith(btnDefectos.cloneNode(true));
            const newBtn = document.getElementById('btn-defectos-inyeccion');
            newBtn.onclick = () => {
                if (typeof window.abrirModalInyeccion === 'function') {
                    window.abrirModalInyeccion();
                }
            };
        }

        // Limpiar peso vela si cambia la maquina
        const selectMaquina = document.getElementById('maquina-inyeccion');
        if (selectMaquina) {
            selectMaquina.addEventListener('change', () => {
                const pesoVela = document.getElementById('peso-vela-inyeccion');
                if (pesoVela && this.items.length === 0) {
                    pesoVela.value = 0;
                }
            });
        }

        ['cantidad-inyeccion', 'cavidades-inyeccion', 'pnc-inyeccion', 'cantidad-real-inyeccion', 'inyeccion-entrada', 'inyeccion-salida'].forEach(id => {
            document.getElementById(id)?.addEventListener('input', (e) => this.calculos(e.target.id));
        });

        // Delegación única de eventos para la tabla de items: un solo
        // listener en el <tbody>, adjuntado una vez, en vez de
        // onchange/onclick inline recreados en cada fila/render. Usa
        // data-item-id (id_item, estable) en vez de índice posicional,
        // así que borrar una fila del medio no desincroniza los
        // handlers de las filas siguientes.
        const tbodyItems = document.getElementById('lista-inyeccion-body');
        if (tbodyItems) {
            tbodyItems.addEventListener('change', (e) => {
                const tr = e.target.closest('tr[data-item-id]');
                const campo = e.target.dataset.campo;
                if (!tr || !campo) return;
                const index = this._indexPorItemId(tr.dataset.itemId);
                if (index === -1) return;

                if (campo === 'operaria_pulido') {
                    this.setOperariaPulido(index, e.target.value);
                } else {
                    this.editarItem(index, campo, e.target.value);
                }
            });

            tbodyItems.addEventListener('click', (e) => {
                const tr = e.target.closest('tr[data-item-id]');
                if (!tr) return;
                const index = this._indexPorItemId(tr.dataset.itemId);
                if (index === -1) return;

                const accion = e.target.closest('[data-accion]')?.dataset.accion;
                if (accion === 'eliminar') this.removerItem(index);
                else if (accion === 'pnc-inyeccion') this.editarPNCLista(index);
                else if (accion === 'pnc-pulido') this.editarPNCPulidoLista(index);
            });
        }
    },

    calculos: function (triggerId) {
        const inputEntrada = document.getElementById('inyeccion-entrada');
        const inputSalida = document.getElementById('inyeccion-salida');
        const inputDisparos = document.getElementById('cantidad-inyeccion');
        const inputReal = document.getElementById('cantidad-real-inyeccion');
        const inputCavidades = document.getElementById('cavidades-inyeccion');
        const inputPnc = document.getElementById('pnc-inyeccion');

        const entrada = parseInt(inputEntrada?.value) || 0;
        const salida = parseInt(inputSalida?.value) || 0;
        const cavidades = parseInt(inputCavidades?.value) || 1;
        const pnc = parseInt(inputPnc?.value) || 0;
        
        let disparos = parseInt(inputDisparos?.value);
        
        // 1. Sincronización de Disparos por Entrada/Salida
        // SOLO ocurre si el trigger fue entrada o salida para garantizar independencia total
        if ((triggerId === 'inyeccion-entrada' || triggerId === 'inyeccion-salida') && salida > 0 && salida >= entrada) {
            const calcDisparos = salida - entrada;
            disparos = calcDisparos;
            if (inputDisparos) inputDisparos.value = disparos;
        } 

        // Si disparos es NaN, lo tomamos como 0 para los cálculos pero NO sobrescribimos el input
        const disparosCalculo = isNaN(disparos) ? 0 : disparos;

        // Sincronizar items si estamos en modo validación
        if (this.esValidacionMode && this.items && this.items.length > 0 && disparosCalculo > 0) {
            let reRender = false;
            this.items.forEach(item => {
                if (item.disparos !== disparosCalculo) {
                    item.disparos = disparosCalculo;
                    const produccionTeorica = item.disparos * item.no_cavidades;
                    item.cantidad_real = produccionTeorica;
                    item.piezasBuenas = Math.max(0, produccionTeorica - item.pnc);
                    item.manual_buenas = null;
                    reRender = true;
                }
            });
            if (reRender) this.renderTablaItems();
        }

        const manualBuenas = parseInt(inputReal?.value) || 0;
        const isManual = inputReal?.value !== '';

        const produccionTeorica = disparosCalculo * cavidades;
        const piezasBuenas = isManual ? manualBuenas : Math.max(0, produccionTeorica - pnc);
        const produccionBrutaReal = isManual ? (manualBuenas + pnc) : produccionTeorica;

        const displayProduccion = document.getElementById('produccion-calculada');
        const displayFormula = document.getElementById('formula-calc');
        const displayBuenas = document.getElementById('piezas-buenas');

        // Alerta visual de proyección
        const projectionAlert = document.getElementById('inyeccion-proyeccion-alert');
        if (projectionAlert) {
            const diff = piezasBuenas - produccionTeorica;
            const sign = diff >= 0 ? '+' : '';
            const color = diff >= 0 ? '#10b981' : '#ef4444';
            const bgColor = diff >= 0 ? '#f0fdf4' : '#fef2f2';

            projectionAlert.style.display = 'block';
            projectionAlert.style.background = bgColor;
            projectionAlert.style.border = `1px solid ${color}`;
            projectionAlert.style.padding = '10px';
            projectionAlert.style.borderRadius = '8px';
            projectionAlert.style.marginTop = '10px';
            projectionAlert.style.fontWeight = 'bold';
            projectionAlert.style.color = color;
            projectionAlert.innerHTML = `
                <i class="fas ${diff >= 0 ? 'fa-check-circle' : 'fa-exclamation-triangle'}"></i> 
                Proyectado: ${produccionTeorica} | Diferencia: <span style="font-size: 1.1rem">${sign}${diff}</span>
            `;
        }

        if (displayProduccion) displayProduccion.textContent = piezasBuenas.toLocaleString();

        if (displayFormula) {
            if (isManual) {
                const diff = produccionBrutaReal - produccionTeorica;
                const sign = diff >= 0 ? '+' : '';
                const color = diff >= 0 ? '#4ade80' : '#f87171';
                displayFormula.innerHTML = `Teórica: <b>${produccionTeorica}</b> | Bruto (Buenas+PNC): <b>${produccionBrutaReal}</b> <span style="color: ${color}; font-weight: bold; margin-left:8px;">(${sign}${diff})</span>`;
            } else {
                displayFormula.textContent = `Disparos: ${disparosCalculo} × Cavidades: ${cavidades} = ${produccionTeorica} (Teórica)`;
            }
        }

        if (displayBuenas) displayBuenas.textContent = `Real: ${piezasBuenas} | PNC: ${pnc}`;
    },

    agregarItem: function () {
        const rawCode = document.getElementById('codigo-producto-inyeccion')?.value || '';
        const codigo_producto = this.normalizarCodigo(rawCode);
        const cavidades = parseInt(document.getElementById('cavidades-inyeccion')?.value) || 1;
        const disparos = parseInt(document.getElementById('cantidad-inyeccion')?.value) || 0;
        const inputReal = document.getElementById('cantidad-real-inyeccion');
        const manualBuenas = parseInt(inputReal?.value) || 0;
        const isManual = inputReal?.value !== '';

        if (!codigo_producto) {
            Swal.fire('Atención', 'Ingresa un código de producto', 'warning');
            return;
        }
        if (disparos <= 0) {
            Swal.fire('Atención', 'Los disparos deben ser mayor a 0', 'warning');
            return;
        }

        const pnc = parseInt(document.getElementById('pnc-inyeccion')?.value) || 0;
        const criterio_pnc = document.getElementById('criterio-pnc-hidden-inyeccion')?.value || '';

        const codigo_ensamble = document.getElementById('codigo-ensamble-inyeccion')?.value || '';
        const peso_bujes = parseFloat(document.getElementById('peso-bujes-inyeccion')?.value) || 0;
        const observaciones = document.getElementById('observaciones-inyeccion')?.value || '';

        const produccionTeorica = disparos * cavidades;
        const piezasBuenas = isManual ? manualBuenas : Math.max(0, produccionTeorica - pnc);
        const cantidadRealBruta = isManual ? (manualBuenas + pnc) : produccionTeorica;

        const legacyId = document.getElementById('legacy-id-inyeccion')?.value || '';

        const nuevoItem = {
            id_item: Date.now().toString() + Math.random().toString(36).substr(2, 5),
            codigo_producto,
            no_cavidades: cavidades,
            disparos,
            cantidad_real: cantidadRealBruta,
            manual_buenas: isManual ? manualBuenas : null,
            pnc,
            criterio_pnc,
            codigo_ensamble,
            peso_bujes,
            observaciones,
            piezasBuenas, // Solo para visualizacion
            id_inyeccion: legacyId ? legacyId : undefined // <-- INYECTAR ID AQUÍ
        };

        this.items.push(nuevoItem);
        this.renderTablaItems();
        this.limpiarFormularioProducto();

        // Limpiar el legacy ID luego de agregarlo para evitar duplicados en el mismo turno manual
        document.getElementById('legacy-id-inyeccion').value = '';

        Swal.fire({
            title: 'Agregado',
            text: 'Producto agregado a la lista',
            icon: 'success',
            toast: true,
            position: 'top-end',
            showConfirmButton: false,
            timer: 2000
        });
    },

    removerItem: function (index) {
        Swal.fire({
            title: '¿Eliminar producto?',
            text: "Se quitará de la lista actual.",
            icon: 'warning',
            showCancelButton: true,
            confirmButtonColor: '#d33',
            cancelButtonColor: '#3085d6',
            confirmButtonText: 'Sí, eliminar',
            cancelButtonText: 'Cancelar'
        }).then((result) => {
            if (result.isConfirmed) {
                this.items.splice(index, 1);
                this.renderTablaItems();
            }
        });
    },

    editarItem: function (index, campo, valor) {
        const item = this.items[index];
        if (!item) return;

        let val = parseFloat(valor);
        if (isNaN(val) || val < 0) val = 0;

        if (campo === 'manual_buenas') {
            item.manual_buenas = valor === '' ? null : val;
        } else if (campo === 'no_cavidades') {
            item.no_cavidades = val;
        } else {
            item[campo] = val;
        }

        const produccionTeorica = item.disparos * item.no_cavidades;

        if (item.manual_buenas !== null) {
            item.piezasBuenas = item.manual_buenas;
            item.cantidad_real = item.manual_buenas + item.pnc;
        } else {
            item.cantidad_real = produccionTeorica;
            item.piezasBuenas = Math.max(0, produccionTeorica - item.pnc);
        }

        this.renderTablaItems();
    },

    // Modal único de captura de criterios. Antes existían dos copias del mismo
    // formulario, cada una con su catálogo hardcodeado; ahora ambas áreas leen
    // el catálogo canónico del backend y comparten esta implementación.
    _abrirModalCriteriosPnc: async function (index, area) {
        const item = this.items[index];
        if (!item) return;

        const criterios = this.criteriosPnc ? this.criteriosPnc[area] : null;
        if (!criterios || criterios.length === 0) {
            Swal.fire(
                'Catálogo no disponible',
                'No se pudo cargar el catálogo de criterios PNC desde el servidor. Recarga la página antes de reportar mermas.',
                'error'
            );
            return;
        }

        const esPulido = area === 'pulido';
        const campoMapa = esPulido ? 'defectos_pnc_pulido' : 'defectos_pnc';
        const campoLista = esPulido ? 'pnc_pulido_list' : 'pnc_list';
        const campoTotal = esPulido ? 'pnc_pulido' : 'pnc';
        const claseInput = esPulido ? 'swal-pnc-pulido-input' : 'swal-pnc-input';
        const defectosActuales = item[campoMapa] || {};

        let htmlContent = `
            <div style="text-align: left; margin-bottom: 15px;">
                <span class="badge bg-secondary mb-1">Producto: ${item.codigo_producto}</span>
                <p class="text-muted small mb-0">Ingresa la cantidad de piezas defectuosas por cada criterio aplicable${esPulido ? ' (Pulido)' : ''}:</p>
            </div>
            <div class="pnc-list-modal-body" style="max-height: 320px; overflow-y: auto; padding: 10px; border: 1px solid #e2e8f0; border-radius: 8px; background: #f8fafc;">
        `;

        criterios.forEach(crit => {
            const val = defectosActuales[crit] || 0;
            htmlContent += `
                <div class="row align-items-center mb-2 pb-2 border-bottom" style="border-color: #f1f5f9 !important;">
                    <div class="col-7 text-start fw-bold text-dark small">${crit}</div>
                    <div class="col-5">
                        <input type="number" min="0" class="form-control form-control-sm text-center ${claseInput} fw-bold" data-criterio="${crit}" value="${val}" style="border-radius: 6px;">
                    </div>
                </div>
            `;
        });
        htmlContent += `</div>`;

        const { value: formValues } = await Swal.fire({
            title: esPulido ? 'Reportar Criterios PNC Pulido' : 'Reportar Criterios PNC',
            html: htmlContent,
            focusConfirm: false,
            showCancelButton: true,
            confirmButtonText: '<i class="fas fa-save me-1"></i> Guardar PNC',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#e11d48',
            preConfirm: () => {
                const resultados = {};
                document.querySelectorAll(`.${claseInput}`).forEach(input => {
                    const crit = input.getAttribute('data-criterio');
                    const qty = parseInt(input.value) || 0;
                    if (qty > 0) resultados[crit] = qty;
                });
                return resultados;
            }
        });

        if (formValues) {
            item[campoMapa] = formValues; // Guardar mapa para reapertura

            // Array detallado que consume el backend
            item[campoLista] = Object.entries(formValues).map(([criterio, cantidad]) => ({
                criterio,
                cantidad
            }));

            const totalPNC = Object.values(formValues).reduce((sum, val) => sum + val, 0);

            // Actualizar la celda principal y recalcular buenas/bruto
            this.editarItem(index, campoTotal, totalPNC);
        }
    },

    editarPNCLista: function (index) {
        return this._abrirModalCriteriosPnc(index, 'inyeccion');
    },

    editarPNCPulidoLista: function (index) {
        return this._abrirModalCriteriosPnc(index, 'pulido');
    },

    setOperariaPulido: function (index, valor) {
        // Setter dedicado: `editarItem` hace parseFloat sobre el valor y
        // convertiría el nombre de la operaria en 0.
        const item = this.items[index];
        if (!item) return;
        item.operaria_pulido = valor || '';
        this.renderTablaItems(); // Refresca el resaltado de campo obligatorio
    },

    limpiarFormularioProducto: function () {
        document.getElementById('codigo-producto-inyeccion').value = '';
        document.getElementById('cavidades-inyeccion').value = 1;
        // document.getElementById('cantidad-inyeccion').value = ''; // No limpiar, se mantiene por máquina
        document.getElementById('cantidad-real-inyeccion').value = '';
        document.getElementById('pnc-inyeccion').value = 0;
        document.getElementById('criterio-pnc-hidden-inyeccion').value = '';
        document.getElementById('codigo-ensamble-inyeccion').value = '';
        document.getElementById('peso-bujes-inyeccion').value = 0;
        document.getElementById('observaciones-inyeccion').value = '';
        this.calculos();
        document.getElementById('codigo-producto-inyeccion').focus();
    },

    // Actualiza el <select> de operaria de pulido dentro de una fila ya
    // existente (this.responsablesPulido: catálogo filtrado server-side
    // vía ?rol=PULIDO en /api/obtener_responsables, cargado con
    // _cargarResponsablesPulido para evitar la condición de carrera con
    // seleccionarLoteValidacion). El catálogo general (this.responsablesData)
    // no debe usarse aquí: exponía comerciales y administradores en un
    // selector que debe ser solo de personal de pulido. Se reconstruye
    // completo (no es un input de texto, no hay cursor/estado de edición
    // que preservar).
    _actualizarSelectOperariaPulido: function (select, item) {
        if (!select) return;
        const seleccionada = item.operaria_pulido || '';
        const requiere = (item.pnc_pulido || 0) > 0;

        const opciones = (this.responsablesPulido || []).map(nombre =>
            `<option value="${nombre}" ${nombre === seleccionada ? 'selected' : ''}>${nombre}</option>`
        ).join('');

        select.innerHTML = `<option value="">-- Seleccionar --</option>${opciones}`;
        select.classList.toggle('border-danger', requiere && !seleccionada);
    },

    _sincronizarItemsConRespuestaServidor: function (items_resultado) {
        // Fusiona la respuesta enriquecida del servidor (que trae id_sql real
        // y otros valores definitivos post-persistencia) con this.items para
        // evitar desincronizaciones. El servidor devuelve en MISMO ORDEN en que
        // se enviaron, así que se empareja por índice.
        if (!items_resultado || !Array.isArray(items_resultado)) return;

        // Emparejar por código normalizado para ser robusto ante reordenes
        // de lista o cambios de cursor.
        const mapServerPorCodigo = {};
        items_resultado.forEach(item => {
            const clave = this.normalizarCodigo(item.codigo_producto);
            if (!mapServerPorCodigo[clave]) {
                mapServerPorCodigo[clave] = [];
            }
            mapServerPorCodigo[clave].push(item);
        });

        const contadores = {};
        this.items.forEach(item => {
            const clave = this.normalizarCodigo(item.codigo_producto);
            const idx = contadores[clave] || 0;

            if (mapServerPorCodigo[clave] && mapServerPorCodigo[clave][idx]) {
                const serverItem = mapServerPorCodigo[clave][idx];
                // Copiar campos definitivos devueltos por el servidor
                item.id_sql = serverItem.id_sql;
                item.id_inyeccion = serverItem.id_inyeccion;
                item.responsable = serverItem.responsable;
                item.cant_contador = serverItem.cant_contador;
                item.pnc_total = serverItem.pnc_total;
                item.pnc_detalle = serverItem.pnc_detalle;
                item._guardado_en_bd = true; // Bandera visual para renderTablaItems
                contadores[clave] = idx + 1;
            }
        });

        console.log('[Inyeccion] Items sincronizados con respuesta del servidor:', this.items);
    },

    // Alias público retro-compatible: los ~9 call-sites existentes que
    // llaman this.renderTablaItems() tras mutar this.items siguen
    // funcionando (ya no es estrictamente necesario -- el Proxy de
    // TablaInyeccionState programa el render solo ante push/splice/
    // reasignación completa -- pero esta llamada extra queda coalesada
    // en el mismo microtask por _programarRenderTablaInyeccion, así que
    // no hace falta tocar esos sitios).
    renderTablaItems: function () {
        _programarRenderTablaInyeccion();
    },

    // Función pura del estado: decide qué rama visual pintar. Solo la
    // dispara el scheduler de TablaInyeccionState (_programarRenderTablaInyeccion),
    // nunca se llama directamente desde otro punto del módulo.
    renderUI: function () {
        const tbody = document.getElementById('lista-inyeccion-body');
        const tfoot = document.getElementById('inyeccion-tfoot');
        if (!tbody) return;

        if (TablaInyeccionState.status === 'loading') {
            this._pintarEstadoCarga(tbody, tfoot);
            return;
        }
        if (TablaInyeccionState.items.length === 0) {
            this._pintarEstadoVacio(tbody, tfoot);
            return;
        }
        this.pintarFilas(tbody, tfoot, TablaInyeccionState.items);
    },

    _pintarEstadoCarga: function (tbody, tfoot) {
        tbody.innerHTML = `
            <tr>
                <td colspan="11" class="text-center text-muted py-5">
                    <i class="fas fa-spinner fa-spin mb-3" style="font-size: 2rem; color: #cbd5e1;"></i>
                    <p class="mb-0">Cargando lote...</p>
                </td>
            </tr>
        `;
        if (tfoot) tfoot.style.display = 'none';
    },

    _pintarEstadoVacio: function (tbody, tfoot) {
        tbody.innerHTML = `
            <tr>
                <td colspan="11" class="text-center text-muted py-5">
                    <i class="fas fa-box-open mb-3" style="font-size: 2rem; color: #cbd5e1;"></i>
                    <p class="mb-0">No hay productos en la lista.</p>
                    <small>Llena la sección de Agregar Producto y presiona "+ Agregar Producto".</small>
                </td>
            </tr>
        `;
        if (tfoot) tfoot.style.display = 'none';
        this._guardarItemsDraft();
    },

    // Reconciliación por clave (id_item): actualiza in-place las filas
    // que ya existen en el DOM y solo crea/borra las que cambiaron de
    // presencia. Reemplaza el innerHTML completo de antes -- que
    // destruía y recreaba cada <input>/<select> en CADA edición de
    // CUALQUIER celda, obligando al hack de guardar/restaurar foco
    // (activeEl/focoAGuardar/restaurarFoco) que existía solo para
    // compensar esa pérdida de foco/cursor. Ese hack ya no existe: al
    // no destruir los nodos, el foco nunca se pierde.
    pintarFilas: function (tbody, tfoot, items) {
        try {
            const vivos = new Set(items.map(i => i.id_item));
            Array.from(tbody.children).forEach(tr => {
                if (!vivos.has(tr.dataset.itemId)) tr.remove();
            });

            let anterior = null;
            items.forEach(item => {
                let tr = tbody.querySelector(`tr[data-item-id="${item.id_item}"]`);
                if (!tr) {
                    tr = this._crearFilaVacia(item.id_item);
                    tbody.insertBefore(tr, anterior ? anterior.nextSibling : tbody.firstChild);
                } else if (anterior && tr.previousElementSibling !== anterior) {
                    tbody.insertBefore(tr, anterior.nextSibling);
                }
                this.actualizarCeldasFila(tr, item);
                anterior = tr;
            });

            this._actualizarTotales(tfoot, items);
        } catch (error) {
            console.error("Error en pintarFilas:", error);
            tbody.innerHTML = `<tr><td colspan="11" class="text-center text-danger py-3">Error al renderizar los datos. Verifica la consola.</td></tr>`;
        }

        this._guardarItemsDraft();
    },

    _actualizarTotales: function (tfoot, items) {
        if (!tfoot) return;

        let totalBuenas = 0, totalPNC = 0, totalPNCPulido = 0, totalBruto = 0, totalPeso = 0;
        items.forEach(item => {
            totalBuenas += item.piezasBuenas;
            totalPNC += item.pnc;
            totalPNCPulido += (item.pnc_pulido || 0);
            totalBruto += item.cantidad_real;
            totalPeso += (parseFloat(item.peso_bujes) || 0);
        });

        tfoot.style.display = 'table-footer-group';
        document.getElementById('inyeccion-total-buenas').textContent = totalBuenas.toLocaleString();
        document.getElementById('inyeccion-total-pnc').textContent = totalPNC.toLocaleString();
        // El id real del tfoot es 'inyeccion-total-pnc-pul' (index.html); el JS
        // buscaba 'inyeccion-total-pnc-pulido' y el total nunca se pintaba.
        if (document.getElementById('inyeccion-total-pnc-pul')) {
            document.getElementById('inyeccion-total-pnc-pul').textContent = totalPNCPulido.toLocaleString();
        }

        const pesoFooter = document.getElementById('inyeccion-total-peso');
        if (pesoFooter) pesoFooter.textContent = totalPeso.toFixed(2);

        const brutoFooter = document.getElementById('inyeccion-total-bruto');
        if (brutoFooter) brutoFooter.textContent = totalBruto.toLocaleString();
    },

    // Esqueleto estático de una fila: se crea UNA sola vez por id_item y
    // sobrevive a los repintados siguientes (actualizarCeldasFila solo
    // le escribe valores encima). Los onchange/onclick inline de antes
    // se reemplazan por data-campo/data-accion + la delegación única en
    // el <tbody> (ver configurarEventos).
    _crearFilaVacia: function (idItem) {
        const tr = document.createElement('tr');
        tr.dataset.itemId = idItem;
        tr.innerHTML = `
            <td class="text-center align-middle js-icono-guardado" style="min-width: 40px;"></td>
            <td class="fw-bold align-middle js-codigo-producto"></td>
            <td class="text-center align-middle">
                <input id="iny-cav-${idItem}" type="number" min="1" class="form-control form-control-sm text-center mx-auto" style="width: 60px;" data-campo="no_cavidades">
            </td>
            <td class="text-center align-middle">
                <input id="iny-disparos-${idItem}" type="number" min="1" class="form-control form-control-sm text-center mx-auto" style="width: 70px;" data-campo="disparos">
            </td>
            <td class="text-center align-middle">
                <div class="d-flex flex-column align-items-center">
                    <small class="text-muted" style="font-size: 0.65rem;">Cant. Real</small>
                    <input id="iny-buenas-${idItem}" type="number" min="0" class="form-control form-control-sm text-center mx-auto" style="width: 85px; color: #000000 !important; background-color: #ffffff !important; font-weight: bold !important; font-size: 1.1rem !important; border: 1px solid #6c757d;" data-campo="manual_buenas">
                </div>
            </td>
            <td class="text-center align-middle">
                <div class="d-flex flex-column align-items-center">
                    <small class="text-muted" style="font-size: 0.65rem;">PNC Iny</small>
                    <div class="d-flex justify-content-center align-items-center gap-1">
                        <input id="iny-pnc-${idItem}" type="number" min="0" class="form-control form-control-sm text-center" style="width: 65px; color: #000000 !important; background-color: #ffffff !important; font-weight: bold !important; font-size: 1.1rem !important; border: 1px solid #6c757d;" data-campo="pnc">
                        <button type="button" class="btn btn-sm btn-outline-danger px-2 py-1" data-accion="pnc-inyeccion"><i class="fas fa-list-ul"></i></button>
                    </div>
                </div>
            </td>
            <td class="text-center align-middle">
                <div class="d-flex flex-column align-items-center">
                    <small class="text-muted" style="font-size: 0.65rem;">PNC Pul</small>
                    <div class="d-flex justify-content-center align-items-center gap-1">
                        <input id="iny-pncpul-${idItem}" type="number" min="0" class="form-control form-control-sm text-center border-warning" style="width: 65px; color: #000000 !important; background-color: #ffffff !important; font-weight: bold !important; font-size: 1.1rem !important;" data-campo="pnc_pulido">
                        <button type="button" class="btn btn-sm btn-outline-warning px-2 py-1" data-accion="pnc-pulido"><i class="fas fa-list-ul"></i></button>
                    </div>
                </div>
            </td>
            <td class="text-center align-middle">
                <div class="d-flex flex-column align-items-center">
                    <small class="text-muted" style="font-size: 0.65rem;">Operaria Pul.</small>
                    <select id="iny-operaria-${idItem}" class="form-select form-select-sm select-operaria-pulido" style="width: 150px;" data-campo="operaria_pulido"></select>
                </div>
            </td>
            <td class="text-center align-middle">
                <div class="d-flex flex-column align-items-center">
                    <small class="text-muted" style="font-size: 0.65rem;">Peso (kg)</small>
                    <input id="iny-peso-${idItem}" type="number" step="0.01" min="0" class="form-control form-control-sm text-center mx-auto border-primary" style="width: 80px;" data-campo="peso_bujes">
                </div>
            </td>
            <td class="text-center align-middle">
                <span class="badge bg-secondary fs-6 js-cantidad-real"></span>
            </td>
            <td class="text-center align-middle">
                <button type="button" class="btn btn-sm btn-outline-danger border-0" data-accion="eliminar" title="Eliminar">
                    <i class="fas fa-trash"></i>
                </button>
            </td>
        `;
        return tr;
    },

    // Único punto que escribe valores dentro de una fila ya existente.
    // Solo toca .value/.textContent si cambió respecto al DOM actual --
    // eso es lo que preserva el foco y la posición del cursor sin
    // necesidad de guardarlos/restaurarlos a mano.
    actualizarCeldasFila: function (tr, item) {
        tr.style.backgroundColor = item._guardado_en_bd ? '#f0fff4' : '';

        const icono = tr.querySelector('.js-icono-guardado');
        icono.innerHTML = item._guardado_en_bd
            ? '<i class="fas fa-check-circle" style="color: #28a745; font-size: 1.2rem;" title="Guardado en BD"></i>'
            : '';

        const codigo = tr.querySelector('.js-codigo-producto');
        if (codigo.textContent !== item.codigo_producto) codigo.textContent = item.codigo_producto;

        this._asignarSiCambio(tr, '[data-campo="no_cavidades"]', item.no_cavidades);
        this._asignarSiCambio(tr, '[data-campo="disparos"]', item.disparos);
        this._asignarSiCambio(tr, '[data-campo="manual_buenas"]', item.manual_buenas !== null ? item.manual_buenas : item.piezasBuenas);
        this._asignarSiCambio(tr, '[data-campo="pnc"]', item.pnc);
        this._asignarSiCambio(tr, '[data-campo="pnc_pulido"]', item.pnc_pulido || 0);
        this._asignarSiCambio(tr, '[data-campo="peso_bujes"]', item.peso_bujes || 0);

        this._actualizarSelectOperariaPulido(tr.querySelector('[data-campo="operaria_pulido"]'), item);

        const badge = tr.querySelector('.js-cantidad-real');
        const textoBadge = (item.cantidad_real || 0).toLocaleString();
        if (badge.textContent !== textoBadge) badge.textContent = textoBadge;
    },

    _asignarSiCambio: function (tr, selector, valor) {
        const el = tr.querySelector(selector);
        if (!el) return;
        const nuevo = String(valor);
        if (el.value !== nuevo) el.value = nuevo;
    },

    _indexPorItemId: function (idItem) {
        return this.items.findIndex(i => i.id_item === idItem);
    },

    registrar: async function () {
        if (this.items.length === 0) {
            Swal.fire('Advertencia', 'Agrega al menos un producto a la lista', 'warning');
            return;
        }

        // --- SUB-MÓDULO 3: VALIDACIÓN (Paola) ---
        if (this.currentModule === 'validation' || this.esValidacionMode) {
            const idIny = document.getElementById('select-validar-lote')?.value;

            if (idIny) {
                this.validarRegistro(idIny);
                return;
            }

            // "Nuevo Manual": sin lote pendiente que auditar (ej. producto recién
            // creado en el catálogo que no alcanzó a programarse a tiempo).
            // /api/inyeccion/lote SIEMPRE deja el registro en PENDIENTE -- igual
            // que Reporte de Máquina -- así que esto no salta la firma de
            // auditoría (eso vive exclusivamente en /api/inyeccion/validar/<id>,
            // ver InyeccionService.validar_lote). Es la vía que
            // ROLES_INYECCION_ESCRITURA (calidad/inventario incluidos) tiene para
            // dejar el registro cuando Producción no completó el ciclo normal.
            const confirmManual = await Swal.fire({
                title: 'Registrar producción nueva',
                text: 'No seleccionaste un lote pendiente, así que esto se guardará como un nuevo registro de producción (quedará PENDIENTE de validación, igual que si viniera de Reporte de Máquina).',
                icon: 'question',
                showCancelButton: true,
                confirmButtonText: 'Sí, registrar',
                cancelButtonText: 'Volver'
            });

            if (confirmManual.isConfirmed) {
                this.esValidacionMode = true;
                this.confirmarRegistroFinal();
            }
            return;
        }

        // --- SUB-MÓDULO 2: REPORTE DE MÁQUINA (Operario) ---
        const horaInicio = document.getElementById('hora-inicio-inyeccion')?.value || '00:00';
        const horaFin = document.getElementById('hora-termina-inyeccion')?.value || '00:00';
        
        if (horaFin <= horaInicio) {
            Swal.fire({
                title: 'Error de Tiempos',
                text: 'La Hora de Fin (Termina) debe ser estrictamente posterior a la Hora de Inicio.',
                icon: 'error',
                confirmButtonColor: '#d33'
            });
            return;
        }

        // REQUERIMIENTO: SÍ debe salir el modal de 'Finalizar Reporte' (PNC, Disparos, etc.)
        this.abrirModalCierre();
    },

    abrirModalCierre: function () {
        const modal = document.getElementById('modal-cierre-inyeccion');
        if (!modal) return;

        // Reset de PNCs del modal
        this.pncRows = [];
        this.renderFilasPnc();

        // Calcular totales para el resumen
        let totalBuenas = 0;
        this.items.forEach(item => {
            totalBuenas += (item.piezasBuenas || 0);
        });

        document.getElementById('resumen-buenas-inyeccion').textContent = totalBuenas.toLocaleString();
        document.getElementById('resumen-pnc-inyeccion').textContent = "0";

        modal.style.display = 'flex';
    },

    agregarFilaPnc: function () {
        const id = Date.now();
        this.pncRows.push({
            id,
            codigo: '',
            motivo: '',
            cantidad: 0
        });
        this.renderFilasPnc();
    },

    eliminarFilaPnc: function (id) {
        this.pncRows = this.pncRows.filter(r => r.id !== id);
        this.renderFilasPnc();
        this.actualizarTotalesResumen();
    },

    actualizarDatoPnc: function (id, campo, valor) {
        const row = this.pncRows.find(r => r.id === id);
        if (row) {
            if (campo === 'cantidad') {
                row[campo] = parseInt(valor) || 0;
                this.actualizarTotalesResumen();
            } else {
                row[campo] = valor;
            }
        }
    },

    manejarCambioMotivo: function(id, valor) {
        const row = this.pncRows.find(r => r.id === id);
        if (row) {
            const inputOtro = document.getElementById(`otro-motivo-${id}`);
            if (valor === 'Otro...') {
                row.esOtro = true;
                row.motivo = '';
                if (inputOtro) {
                    inputOtro.style.display = 'block';
                    inputOtro.value = '';
                    inputOtro.focus();
                }
            } else {
                row.esOtro = false;
                row.motivo = valor;
                if (inputOtro) inputOtro.style.display = 'none';
            }
        }
    },

    actualizarTotalesResumen: function () {
        const totalPnc = this.pncRows.reduce((sum, row) => sum + row.cantidad, 0);
        const display = document.getElementById('resumen-pnc-inyeccion');
        if (display) display.textContent = totalPnc.toLocaleString();
    },

    renderFilasPnc: function () {
        const container = document.getElementById('inyeccion-pnc-container');
        const emptyMsg = document.getElementById('inyeccion-pnc-empty-msg');
        if (!container) return;

        if (this.pncRows.length === 0) {
            if (emptyMsg) emptyMsg.style.display = 'block';
            container.querySelectorAll('.pnc-row').forEach(el => el.remove());
            return;
        }

        if (emptyMsg) emptyMsg.style.display = 'none';

        // Mantener solo las filas que existen en el estado
        const existingIds = this.pncRows.map(r => `pnc-row-${r.id}`);
        container.querySelectorAll('.pnc-row').forEach(el => {
            if (!existingIds.includes(el.id)) el.remove();
        });

        this.pncRows.forEach(row => {
            let rowEl = document.getElementById(`pnc-row-${row.id}`);
            if (!rowEl) {
                rowEl = document.createElement('div');
                rowEl.id = `pnc-row-${row.id}`;
                rowEl.className = 'pnc-row d-flex gap-2 align-items-start p-2 border rounded-3 bg-white shadow-sm';
                rowEl.innerHTML = `
                    <div class="flex-grow-1 position-relative">
                        <input type="text" class="form-control form-control-sm pnc-codigo-input" placeholder="Referencia..." value="${row.codigo}" oninput="ModuloInyeccion.actualizarDatoPnc(${row.id}, 'codigo', this.value)">
                        <div class="autocomplete-suggestions pnc-suggestions" id="suggestions-${row.id}"></div>
                    </div>
                    <div style="width: 160px;" class="d-flex flex-column gap-1">
                        <select class="form-select form-select-sm" onchange="ModuloInyeccion.manejarCambioMotivo(${row.id}, this.value)">
                            <option value="">Motivo...</option>
                            ${["Incompleto", "Chupado", "Manchas", "Deformado", "Burbujas", "Quemado", "Hundido", "Líneas de Flujo", "Rebaba", "Material Contaminado", "Otro..."].map(c => `
                                <option value="${c}" ${(row.motivo === c && !row.esOtro) || (row.esOtro && c === 'Otro...') ? 'selected' : ''}>${c}</option>
                            `).join('')}
                        </select>
                        <input type="text" id="otro-motivo-${row.id}" class="form-control form-control-sm" placeholder="Especifique..." 
                               value="${row.esOtro ? row.motivo : ''}" 
                               style="display: ${row.esOtro ? 'block' : 'none'};" 
                               oninput="ModuloInyeccion.actualizarDatoPnc(${row.id}, 'motivo', this.value)">
                    </div>
                    <div style="width: 80px;">
                        <input type="number" class="form-control form-control-sm text-center" placeholder="Cant" value="${row.cantidad}" oninput="ModuloInyeccion.actualizarDatoPnc(${row.id}, 'cantidad', this.value)">
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-danger border-0" onclick="ModuloInyeccion.eliminarFilaPnc(${row.id})">
                        <i class="fas fa-times"></i>
                    </button>
                `;
                container.appendChild(rowEl);
                this.initAutocompletePncRow(row.id);
            }
        });
    },

    initAutocompletePncRow: function (rowId) {
        const rowEl = document.getElementById(`pnc-row-${rowId}`);
        if (!rowEl) return;
        const input = rowEl.querySelector('.pnc-codigo-input');
        const suggestionsDiv = rowEl.querySelector('.pnc-suggestions');

        let debounceTimer;
        input.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            const query = e.target.value.trim().toLowerCase();
            if (query.length < 2) {
                suggestionsDiv.classList.remove('active');
                return;
            }

            debounceTimer = setTimeout(() => {
                const resultados = this.productosData.filter(prod =>
                    String(prod.codigo_sistema || '').toLowerCase().includes(query) ||
                    String(prod.descripcion || '').toLowerCase().includes(query)
                ).slice(0, 10);

                if (window.renderProductSuggestions) {
                    window.renderProductSuggestions(suggestionsDiv, resultados, (item) => {
                        input.value = item.codigo_sistema || item.codigo;
                        this.actualizarDatoPnc(rowId, 'codigo', input.value);
                        suggestionsDiv.classList.remove('active');
                    });
                }
            }, 300);
        });

        document.addEventListener('click', (e) => {
            if (!input.contains(e.target) && !suggestionsDiv.contains(e.target)) {
                suggestionsDiv.classList.remove('active');
            }
        });
    },

    confirmarRegistroFinal: async function () {
        const btn = document.getElementById('btn-finalizar-inyeccion');
        const btnSubmitOriginal = document.querySelector('#form-inyeccion button[type="submit"]');

        try {
            if (btn) btn.disabled = true;
            mostrarLoading(true, 'Guardando reporte e inyectando PNC...');

            // Captura robusta del operario que reporta la producción. Ya no se
            // deriva de aquí ninguna identidad de validador: /api/inyeccion/lote
            // no valida, y la firma de auditoría sale del JWT en el endpoint de
            // validación.
            const responsableVal = document.getElementById('responsable-inyeccion')?.value ||
                                   window.AppState?.user?.nombre ||
                                   window.AppState?.user?.user ||
                                   localStorage.getItem('user_name') ||
                                   localStorage.getItem('user') || '';

            // Datos comunes de turno
            const datosTurno = {
                fecha_inicio: document.getElementById('fecha-inyeccion')?.value || '',
                maquina: document.getElementById('maquina-inyeccion')?.value || '',
                responsable: responsableVal,
                hora_llegada: document.getElementById('hora-llegada-inyeccion')?.value || '',
                hora_inicio: document.getElementById('hora-inicio-inyeccion')?.value || '',
                hora_termina: document.getElementById('hora-termina-inyeccion')?.value || '',
                orden_produccion: document.getElementById('orden-produccion-inyeccion')?.value || '',
                entrada_manual: parseFloat(String(document.getElementById('inyeccion-entrada')?.value || '0').replace(/[^0-9.]/g, '')) || 0,
                salida_manual: parseFloat(String(document.getElementById('inyeccion-salida')?.value || '0').replace(/[^0-9.]/g, '')) || 0,
                peso_vela_maquina: parseFloat(String(document.getElementById('peso-vela-inyeccion')?.value || '0').replace(/[^0-9.]/g, '')) || 0,
                id_programacion: document.getElementById('legacy-id-programacion')?.value || '',
                almacen_destino: 'POR PULIR',
                id_inyeccion: this._idTurnoActivo || undefined,
                id_sql: this._idSqlActivo || undefined
            };

            // Consolidar los criterios detallados de PNC de INYECCIÓN de cada item.
            // La merma de pulido no se envía por esta ruta: el backend la
            // clasificaba contra las columnas tipadas de inyección (criterios
            // como "Rayado" caían en deformacion_rechupado) y la contabilizaba
            // como defecto de máquina. Se reporta solo en la validación, que la
            // escribe en db_pnc_pulido con su operaria responsable.
            const pncListConsolidada = [];
            this.items.forEach(item => {
                if (item.pnc_list && item.pnc_list.length > 0) {
                    item.pnc_list.forEach(p => {
                        pncListConsolidada.push({
                            codigo: item.codigo_producto,
                            criterio: p.criterio,
                            cantidad: p.cantidad
                        });
                    });
                }
            });

            // Sumar también las PNC de la lista general del modal de cierre (si las hay)
            this.pncRows.forEach(r => {
                if (r.codigo && r.cantidad > 0) {
                    pncListConsolidada.push({
                        codigo: r.codigo,
                        criterio: r.motivo || 'SIN ESPECIFICAR',
                        cantidad: r.cantidad
                    });
                }
            });

            const payload = {
                turno: datosTurno,
                items: this.items,
                pnc_list: pncListConsolidada,
                responsable: responsableVal
            };

            console.log('📤 [Inyeccion] ENVIANDO REPORTE DE PRODUCCIÓN CON PNC:', payload);

            // Inyección de token JWT en cabeceras de autorización
            const token = localStorage.getItem('pwa_token') || localStorage.getItem('token') || sessionStorage.getItem('token') || '';
            const headers = {
                'Content-Type': 'application/json'
            };
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }

            const response = await fetch('/api/inyeccion/lote', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload)
            });

            const resultado = await response.json();

            if (response.ok && resultado.success) {
                // 1-2. El DTO enriquecido de InyeccionService.registrar_lote trae,
                // por item y en el MISMO ORDEN en que se enviaron (el backend itera
                // data['items'] tal cual), el id_sql definitivo que Postgres le
                // asignó. Se sincroniza sobre this.items ANTES de tocar nada más:
                // si el operario reabre esta fila en vez de resetear, cualquier
                // edición posterior debe apuntar (UPDATE) al registro real ya
                // creado, no crear uno nuevo por no conocer su id_sql.
                this._sincronizarItemsConRespuestaServidor(resultado.data.items);

                // Ocultar el loading YA (la confirmación visual que sigue no debe
                // quedar detrás del overlay de carga).
                mostrarLoading(false);

                // Cerrar modal de cierre si estaba abierto
                const modal = document.getElementById('modal-cierre-inyeccion');
                if (modal) modal.style.display = 'none';

                // 3. Confirmación visual por fila: se pinta el check de "Guardado"
                // (usando los id_sql recién sincronizados) ANTES del diálogo de
                // éxito, para que el operario vea qué filas quedaron confirmadas
                // en el DOM y no solo un mensaje genérico.
                this.renderTablaItems();

                await Swal.fire('¡Registrado!', 'El reporte de producción se ha guardado correctamente.', 'success');

                // Reiniciar todo. renderTablaItems() con items=[] también borra
                // el borrador de localStorage (_guardarItemsDraft trata la lista
                // vacía como "sin nada que recuperar"), así que el lote recién
                // confirmado no reaparece si el operario recarga después.
                this.items = [];
                this.pncRows = [];
                this.renderTablaItems();
                document.getElementById('form-inyeccion')?.reset();
                if (window.FormHelpers) window.FormHelpers.limpiarPersistencia('form-inyeccion');
                this._idTurnoActivo = null;
                this._idSqlActivo = null; // Limpiar ID SQL
                this.intentarAutoSeleccionarResponsable();
                this.limpiarFormularioValidacion(true);

                // Recargar productos reactivamente para actualizar stock
                if (window.DataReloadHelpers && window.DataReloadHelpers.recargarProductos) {
                    window.DataReloadHelpers.recargarProductos().catch(err => console.error("[Inyeccion] Error actualizando stock:", err));
                }
            } else {
                // require_role (403) responde {status, message}, no {success, error} —
                // sin este fallback el mensaje real de permisos se perdía y quedaba
                // el genérico "Error procesando reporte".
                Swal.fire('Error', resultado.error || resultado.message || 'Error procesando reporte', 'error');
            }

        } catch (e) {
            console.error('Error [Inyeccion] confirmar registro:', e);
            let msg = e.message;
            if (msg.toLowerCase().includes('json') || msg.includes('Unexpected token')) {
                msg = "Ocurrió un error en el servidor (500). Verifique los datos ingresados o contacte a sistemas.";
            }
            Swal.fire({
                title: 'Error del Sistema',
                text: msg,
                icon: 'error',
                confirmButtonText: 'Entendido'
            });
        } finally {
            mostrarLoading(false);
            if (btn) btn.disabled = false;
        }
    },

    // Alias para compatibilidad
    inicializar: function () {
        return this.init();
    }
};

// Exportación global
window.ModuloInyeccion = ModuloInyeccion;
window.initInyeccion = () => ModuloInyeccion.init();
