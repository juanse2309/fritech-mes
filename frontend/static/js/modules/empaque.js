// ============================================
// empaque.js - Reporte de Empaque (reunión 2026-08-25)
// Nadie programa: la operaria arma según el pedido y solo reporta
// referencia + cantidad. La OP EMP del día se asigna sola en el backend
// (reserva perezosa: la crea el primer reporte, los siguientes la reutilizan).
// ============================================

window.ModuloEmpaque = {
    productosData: [],
    referenciaSeleccionada: null,
    _debouncePreview: null,

    inicializar: async function () {
        console.log('📦 [Empaque] Inicializando módulo...');
        await this.cargarProductos();
        this.configurarEventos();
        await this.cargarListado();
    },

    cargarProductos: async function () {
        try {
            const res = await fetchData('/api/productos/listar');
            // /api/productos/listar devuelve {items: [...]} -- no {data: [...]}
            // ni el arreglo suelto. Con la forma anterior, res.data era
            // undefined y el fallback dejaba productosData = el objeto
            // {items:[...]} completo (no un arreglo), así que cada búsqueda
            // tronaba en silencio: el buscador de referencia nunca mostraba
            // nada. Bug real, no una limitación del catálogo.
            this.productosData = res?.items || res?.data || (Array.isArray(res) ? res : []);
        } catch (e) {
            console.warn('[Empaque] No se pudo cargar el catálogo de productos:', e);
            this.productosData = [];
        }
    },

    configurarEventos: function () {
        const input = document.getElementById('empaque-buscador');
        const sugerencias = document.getElementById('empaque-sugerencias');

        input?.addEventListener('input', (e) => {
            const query = String(e.target.value || '').toLowerCase().trim();
            this.referenciaSeleccionada = null;
            this.ocultarPreview();

            if (query.length < 1) {
                sugerencias.style.display = 'none';
                return;
            }

            const terms = query.split(/\s+/).filter(Boolean);
            const resultados = this.productosData.filter(p => {
                const codigo = String(p.codigo_sistema || '').toLowerCase();
                const desc = String(p.descripcion || '').toLowerCase();
                return terms.every(t =>
                    codigo.includes(t) || desc.includes(t) ||
                    codigo.replace(/[-\s]/g, '').includes(t.replace(/[-\s]/g, ''))
                );
            }).slice(0, 12);

            if (!resultados.length) {
                sugerencias.style.display = 'none';
                return;
            }

            sugerencias.innerHTML = resultados.map(p => `
                <button type="button" class="list-group-item list-group-item-action py-2"
                        data-codigo="${p.codigo_sistema}">
                    <span class="fw-bold">${p.codigo_sistema}</span>
                    <small class="text-muted d-block text-truncate">${p.descripcion || ''}</small>
                </button>
            `).join('');
            sugerencias.style.display = 'block';

            sugerencias.querySelectorAll('button').forEach(btn => {
                btn.addEventListener('click', () => {
                    input.value = btn.dataset.codigo;
                    this.referenciaSeleccionada = btn.dataset.codigo;
                    sugerencias.style.display = 'none';
                    this.previsualizar();
                });
            });
        });

        document.addEventListener('click', (e) => {
            if (input && sugerencias && !input.contains(e.target) && !sugerencias.contains(e.target)) {
                sugerencias.style.display = 'none';
            }
        });

        // La vista previa depende de la cantidad, así que se re-consulta al
        // cambiarla -- con debounce para no disparar una petición por tecla.
        document.getElementById('empaque-cantidad')?.addEventListener('input', () => {
            clearTimeout(this._debouncePreview);
            this._debouncePreview = setTimeout(() => this.previsualizar(), 400);
        });

        document.getElementById('btn-empaque-reportar')?.addEventListener('click', () => this.reportar());
        document.getElementById('btn-empaque-refrescar')?.addEventListener('click', () => this.cargarListado());
    },

    ocultarPreview: function () {
        const card = document.getElementById('empaque-preview-card');
        if (card) card.style.display = 'none';
    },

    previsualizar: async function () {
        const codigo = this.referenciaSeleccionada || document.getElementById('empaque-buscador')?.value?.trim();
        const cantidad = parseInt(document.getElementById('empaque-cantidad')?.value || '1', 10);
        if (!codigo || !cantidad || cantidad <= 0) return this.ocultarPreview();

        const res = await fetchData(`/api/empaque/ficha/${encodeURIComponent(codigo)}?cantidad=${cantidad}`);
        const card = document.getElementById('empaque-preview-card');
        const cont = document.getElementById('empaque-preview-contenido');
        if (!card || !cont) return;

        if (!res?.success) {
            // fetchData ya notificó el error; aquí solo se deja el panel oculto.
            return this.ocultarPreview();
        }

        const d = res.data;
        const filas = d.componentes.map(c => {
            const falta = c.faltante > 0;
            const detalle = falta
                ? `<span class="badge bg-danger">Faltan ${c.faltante}</span>`
                : `<span class="badge bg-success">${c.de_terminado > 0 ? `${c.de_terminado} de terminado` : ''}${c.de_pulir > 0 ? ` · ${c.de_pulir} de por pulir` : ''}</span>`;
            return `<div class="d-flex justify-content-between align-items-center py-1 border-bottom">
                        <span class="small"><i class="fas fa-cube me-1 text-muted"></i>${c.codigo}</span>${detalle}
                    </div>`;
        }).join('');

        cont.innerHTML = `
            ${d.stock_suficiente
                ? '<div class="alert alert-success py-2 mb-3 small"><i class="fas fa-check-circle me-1"></i> Hay material suficiente.</div>'
                : '<div class="alert alert-danger py-2 mb-3 small"><i class="fas fa-triangle-exclamation me-1"></i> No hay material suficiente — no se podrá registrar.</div>'}
            ${filas}`;
        card.style.display = 'block';
    },

    reportar: async function (forzar = false) {
        const codigo = this.referenciaSeleccionada || document.getElementById('empaque-buscador')?.value?.trim();
        const cantidad = parseInt(document.getElementById('empaque-cantidad')?.value || '0', 10);
        const observaciones = document.getElementById('empaque-observaciones')?.value?.trim();

        if (!codigo) return Swal.fire('Falta la referencia', 'Escribe o selecciona la referencia que armaste.', 'warning');
        if (!cantidad || cantidad <= 0) return Swal.fire('Cantidad inválida', 'La cantidad debe ser mayor a 0.', 'warning');

        if (!forzar) {
            const confirmacion = await Swal.fire({
                title: '¿Registrar empaque?',
                html: `<b>${cantidad}</b> x <b>${codigo}</b><br><small class="text-muted">Se descontará el material de inventario.</small>`,
                icon: 'question',
                showCancelButton: true,
                confirmButtonText: 'Sí, registrar',
                cancelButtonText: 'Cancelar',
                confirmButtonColor: '#0284c7'
            });
            if (!confirmacion.isConfirmed) return;
        }

        // silent: si falta stock queremos leer el `code` del error y
        // ofrecer forzar, no el toast genérico de fetchData.
        const res = await fetchData('/api/empaque/reportar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id_codigo: codigo, cantidad, observaciones, forzar }),
            silent: true
        });

        if (res?.success) {
            Swal.fire({
                icon: res.data.forzado ? 'warning' : 'success',
                title: res.data.forzado ? 'Empaque registrado (stock quedó negativo)' : 'Empaque registrado',
                html: `${cantidad} x ${codigo}<div class="mt-2"><span class="badge bg-primary fs-6">OP asignada: ${res.data.op_numero}</span></div>`,
                timer: res.data.forzado ? undefined : 3600,
                showConfirmButton: !!res.data.forzado
            });
            document.getElementById('empaque-buscador').value = '';
            document.getElementById('empaque-cantidad').value = 1;
            document.getElementById('empaque-observaciones').value = '';
            this.referenciaSeleccionada = null;
            this.ocultarPreview();
            await this.cargarListado();
            return;
        }

        if (res?.code === 'STOCK_INSUFICIENTE' && !forzar) {
            const forzarConfirm = await Swal.fire({
                icon: 'warning',
                title: 'Stock insuficiente',
                html: `${res.error}<br><br>El material ya se armó y solo falta el registro -- se puede guardar igual y el inventario quedará en negativo hasta que se reponga.`,
                showCancelButton: true,
                confirmButtonText: 'Registrar de todas formas',
                cancelButtonText: 'Cancelar',
                confirmButtonColor: '#dc3545'
            });
            if (forzarConfirm.isConfirmed) {
                return this.reportar(true);
            }
            return;
        }

        // Cualquier otro error (ficha inexistente, producto no encontrado
        // en inventario, fallo de red, etc.) -- se muestra tal como antes
        // hacía fetchData automáticamente.
        mostrarNotificacion(`Error en la solicitud: ${res?.error || 'No se pudo registrar el empaque.'}`, 'error');
    },

    cargarListado: async function () {
        const cont = document.getElementById('empaque-listado');
        if (!cont) return;

        const res = await fetchData('/api/empaque/reportes');
        const filas = res?.data || [];

        if (!filas.length) {
            cont.innerHTML = '<div class="text-muted small">Sin registros todavía.</div>';
            return;
        }

        cont.innerHTML = `
            <div class="table-responsive">
                <table class="table table-sm table-hover align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th class="small">Hora</th><th class="small">Referencia</th>
                            <th class="small text-center">Cant.</th><th class="small">OP</th><th class="small">Responsable</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${filas.map(r => `
                            <tr>
                                <td class="small text-muted">${(r.fecha_registro || '').slice(11)}</td>
                                <td class="small fw-bold">${r.id_codigo}</td>
                                <td class="small text-center"><span class="badge bg-primary">${r.cantidad}</span></td>
                                <td class="small"><code>${r.op_numero || '-'}</code></td>
                                <td class="small text-muted">${r.responsable || '-'}</td>
                            </tr>`).join('')}
                    </tbody>
                </table>
            </div>`;
    },
};
