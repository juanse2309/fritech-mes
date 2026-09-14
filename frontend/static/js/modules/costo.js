// costo.js - Módulo de Costo y Rentabilidad por Pedido (Solo Administración)
// Costo de fabricar la pieza (db_costos) vs precio de venta cobrado en el
// pedido (db_pedidos.precio_unitario). Deliberadamente NO incluye costos
// operativos -- eso lo calcula World Office.

const ModuloCosto = {
    pedidos: [],
    ordenCampo: 'fecha',
    ordenAsc: false,
    _initDone: false,

    fmt: new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }),

    inicializar: function () {
        if (!this._initDone) {
            this._initDone = true;
            console.log('💵 Inicializando Módulo Costo...');
        }
        this.cargar();
    },

    cargar: async function () {
        const tbody = document.getElementById('costo-modulo-tbody');
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center py-5 text-muted"><i class="fas fa-spinner fa-spin fa-2x"></i></td></tr>';
        }

        try {
            const pwaToken = localStorage.getItem('pwa_token');
            const headers = { 'Accept': 'application/json' };
            if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;

            const res = await fetch('/api/costos/listar', { headers, credentials: 'include' });
            const data = await res.json();

            if (!data.success) {
                throw new Error(data.error || 'Error al obtener el costo de los pedidos');
            }

            this.pedidos = data.pedidos || [];
            this.renderizarResumen();
            this.renderizarTabla();
        } catch (error) {
            console.error('❌ Error cargando costo de pedidos:', error);
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="7" class="text-center py-5 text-danger">Error al cargar el costo de los pedidos: ${error.message}</td></tr>`;
            }
        }
    },

    renderizarResumen: function () {
        // Solo pedidos con costeo completo entran a la sumatoria -- mezclar un
        // pedido con costo parcial (0 en las líneas sin match) inflaría el margen.
        const completos = this.pedidos.filter(p => p.costeo_completo);
        const ventaTotal = completos.reduce((acc, p) => acc + (p.venta_total || 0), 0);
        const costoTotal = completos.reduce((acc, p) => acc + (p.costo_total || 0), 0);
        const margenTotal = ventaTotal - costoTotal;
        const margenPct = ventaTotal > 0 ? Math.round((margenTotal / ventaTotal) * 100) : 0;
        const incompletos = this.pedidos.length - completos.length;

        const ventaEl = document.getElementById('costo-modulo-venta-val');
        const costoEl = document.getElementById('costo-modulo-costo-val');
        const margenEl = document.getElementById('costo-modulo-margen-val');
        const avisoEl = document.getElementById('costo-modulo-aviso-incompletos');

        if (ventaEl) ventaEl.textContent = this.fmt.format(ventaTotal);
        if (costoEl) costoEl.textContent = this.fmt.format(costoTotal);
        if (margenEl) margenEl.textContent = `${this.fmt.format(margenTotal)} (${margenPct}%)`;
        if (avisoEl) {
            if (incompletos > 0) {
                avisoEl.textContent = `${incompletos} pedido(s) con referencias sin costo cargado -- no entran en este resumen.`;
                avisoEl.classList.remove('d-none');
            } else {
                avisoEl.classList.add('d-none');
            }
        }
    },

    filtrar: function () {
        this.renderizarTabla();
    },

    ordenarPor: function (campo) {
        if (this.ordenCampo === campo) {
            this.ordenAsc = !this.ordenAsc;
        } else {
            this.ordenCampo = campo;
            this.ordenAsc = false;
        }
        this.renderizarTabla();
    },

    renderizarTabla: function () {
        const tbody = document.getElementById('costo-modulo-tbody');
        if (!tbody) return;

        const buscador = document.getElementById('costo-modulo-buscador');
        const query = (buscador?.value || '').trim().toUpperCase();

        let filtrados = this.pedidos;
        if (query) {
            filtrados = filtrados.filter(p =>
                (p.id_pedido || '').toString().toUpperCase().includes(query) ||
                (p.cliente || '').toUpperCase().includes(query)
            );
        }

        const campo = this.ordenCampo;
        const asc = this.ordenAsc;
        filtrados = [...filtrados].sort((a, b) => {
            const esTexto = campo === 'cliente' || campo === 'id_pedido' || campo === 'fecha';
            const va = esTexto ? (a[campo] || '') : (parseFloat(a[campo]) || 0);
            const vb = esTexto ? (b[campo] || '') : (parseFloat(b[campo]) || 0);
            if (va < vb) return asc ? -1 : 1;
            if (va > vb) return asc ? 1 : -1;
            return 0;
        });

        if (filtrados.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center py-5 text-muted">No se encontraron pedidos.</td></tr>';
            return;
        }

        tbody.innerHTML = filtrados.map((p, idx) => {
            const sinCosteoCompleto = !p.costeo_completo;
            const margenTxt = p.margen !== null && p.margen !== undefined
                ? `${this.fmt.format(p.margen)} <span class="text-muted small">(${p.margen_pct ?? 0}%)</span>`
                : '<span class="text-muted small">N/D</span>';
            const margenClase = p.margen !== null && p.margen !== undefined
                ? (p.margen >= 0 ? 'text-success' : 'text-danger')
                : '';

            return `
            <tr style="cursor:pointer;" onclick="ModuloCosto.toggleDetalle('${p.id_pedido}', ${idx})">
                <td class="fw-bold" data-label="Pedido" style="color:#1e293b;"><i class="fas fa-chevron-right me-2 text-muted" id="costo-chevron-${idx}" style="font-size:0.7rem;"></i>${p.id_pedido}
                    ${sinCosteoCompleto ? '<span class="badge bg-warning text-dark ms-1" style="font-size:0.6rem;" title="Hay referencias de este pedido sin costo cargado en db_costos">Costeo incompleto</span>' : ''}
                </td>
                <td data-label="Cliente">${p.cliente || 'N/A'}</td>
                <td data-label="Fecha">${p.fecha || 'N/A'}</td>
                <td data-label="Estado"><span class="badge bg-secondary">${p.estado || 'N/A'}</span></td>
                <td class="text-end" data-label="Venta">${this.fmt.format(p.venta_total)}</td>
                <td class="text-end" data-label="Costo">${p.costo_total !== null && p.costo_total !== undefined ? this.fmt.format(p.costo_total) : '<span class="text-muted small">N/D</span>'}</td>
                <td class="text-end fw-bold ${margenClase}" data-label="Margen">${margenTxt}</td>
            </tr>
            <tr id="costo-detalle-fila-${idx}" style="display:none;">
                <td colspan="7" class="p-0 td-detalle-anidado">
                    <div id="costo-detalle-${idx}" class="p-3 bg-light"></div>
                </td>
            </tr>
        `;
        }).join('');
    },

    toggleDetalle: async function (idPedido, idx) {
        const fila = document.getElementById(`costo-detalle-fila-${idx}`);
        const contenedor = document.getElementById(`costo-detalle-${idx}`);
        const chevron = document.getElementById(`costo-chevron-${idx}`);
        if (!fila || !contenedor) return;

        const abierto = fila.style.display !== 'none';
        if (abierto) {
            fila.style.display = 'none';
            if (chevron) chevron.className = 'fas fa-chevron-right me-2 text-muted';
            return;
        }

        fila.style.display = 'table-row';
        if (chevron) chevron.className = 'fas fa-chevron-down me-2 text-muted';

        if (contenedor.dataset.loaded === 'true') return;

        contenedor.innerHTML = '<div class="text-center py-2 text-muted"><i class="fas fa-spinner fa-spin"></i> Cargando líneas...</div>';

        try {
            const pwaToken = localStorage.getItem('pwa_token');
            const headers = { 'Accept': 'application/json' };
            if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;

            const res = await fetch(`/api/costos/pedido/${encodeURIComponent(idPedido)}`, { headers, credentials: 'include' });
            const data = await res.json();

            if (!data.success || !data.items || data.items.length === 0) {
                contenedor.innerHTML = '<div class="text-muted small">No se encontraron líneas para este pedido.</div>';
                return;
            }

            const filasItems = data.items.map(it => {
                const sinCosto = it.costo_unitario === null || it.costo_unitario === undefined;
                const margenTxt = sinCosto
                    ? '<span class="text-muted">Sin costo cargado</span>'
                    : `${this.fmt.format(it.margen_linea)} <span class="text-muted small">(${it.margen_pct ?? 0}%)</span>`;
                const margenClase = !sinCosto && it.margen_linea < 0 ? 'text-danger' : (!sinCosto ? 'text-success' : '');
                return `
                <tr>
                    <td>${it.codigo || ''}</td>
                    <td>${it.descripcion || ''}</td>
                    <td class="text-end">${it.cantidad}</td>
                    <td class="text-end">${this.fmt.format(it.precio_unitario)}</td>
                    <td class="text-end">${sinCosto ? '<span class="text-muted">N/D</span>' : this.fmt.format(it.costo_unitario)}</td>
                    <td class="text-end fw-bold ${margenClase}">${margenTxt}</td>
                </tr>
            `;
            }).join('');

            contenedor.innerHTML = `
                <table class="table table-sm table-bordered bg-white mb-0">
                    <thead>
                        <tr><th>Código</th><th>Descripción</th><th class="text-end">Cantidad</th><th class="text-end">Precio Venta</th><th class="text-end">Costo Pieza</th><th class="text-end">Margen</th></tr>
                    </thead>
                    <tbody>${filasItems}</tbody>
                </table>
            `;
            contenedor.dataset.loaded = 'true';
        } catch (error) {
            console.error('❌ Error cargando detalle de costo del pedido:', error);
            contenedor.innerHTML = '<div class="text-danger small">Error al cargar el detalle.</div>';
        }
    }
};

window.ModuloCosto = ModuloCosto;
