// ============================================
// exportacion_wo.js - Exportar OP a World Office ("vista de Zoe")
// Reunión 2026-08-25: descarga del archivo plano que reemplaza la
// digitación manual de las OP en WO. Un archivo POR ÁREA (INY/ENS/EMP);
// si se seleccionan varias áreas a la vez, bajan juntas en un ZIP.
// ============================================

window.ModuloExportacionWO = {
    ops: [],
    habilitado: false,
    _pollTimer: null,

    inicializar: async function () {
        console.log('📤 [ExportWO] Inicializando módulo...');
        this.configurarEventos();
        this.fijarRangoPorDefecto();
        await this.cargar();
    },

    // Por defecto se muestran los últimos 7 días: Zoe descarga lo del día
    // anterior, pero si un día no alcanzó, tiene que poder ver hacia atrás.
    fijarRangoPorDefecto: function () {
        const hoy = new Date();
        const hace7 = new Date(hoy.getTime() - 7 * 24 * 60 * 60 * 1000);
        const fmt = (d) => d.toISOString().slice(0, 10);
        const desde = document.getElementById('expwo-desde');
        const hasta = document.getElementById('expwo-hasta');
        if (desde && !desde.value) desde.value = fmt(hace7);
        if (hasta && !hasta.value) hasta.value = fmt(hoy);
    },

    configurarEventos: function () {
        document.getElementById('btn-expwo-refrescar')?.addEventListener('click', () => this.cargar());
        document.getElementById('btn-expwo-preview')?.addEventListener('click', () => this.previsualizar());
        document.getElementById('btn-expwo-descargar')?.addEventListener('click', () => this.descargar());
        document.getElementById('btn-expwo-cerrar-preview')?.addEventListener('click', () => {
            document.getElementById('expwo-preview-card').style.display = 'none';
        });
        ['expwo-desde', 'expwo-hasta', 'expwo-ambito'].forEach(id => {
            document.getElementById(id)?.addEventListener('change', () => this.cargar());
        });
    },

    cargar: async function () {
        const cont = document.getElementById('expwo-listado');
        if (!cont) return;
        cont.innerHTML = '<div class="text-muted small">Cargando…</div>';

        const params = new URLSearchParams();
        const desde = document.getElementById('expwo-desde')?.value;
        const hasta = document.getElementById('expwo-hasta')?.value;
        const ambito = document.getElementById('expwo-ambito')?.value;
        if (desde) params.set('fecha_desde', desde);
        if (hasta) params.set('fecha_hasta', hasta);
        if (ambito) params.set('ambito', ambito);

        const res = await fetchData(`/api/wo/op/exportables?${params.toString()}`);
        if (!res?.success) {
            cont.innerHTML = '<div class="text-danger small">No se pudo cargar el listado.</div>';
            return;
        }

        this.ops = res.data.ops || [];
        this.habilitado = !!res.data.exportacion_habilitada;

        const aviso = document.getElementById('expwo-aviso-deshabilitado');
        if (aviso) aviso.style.setProperty('display', this.habilitado ? 'none' : 'flex', 'important');
        const btnDescargar = document.getElementById('btn-expwo-descargar');
        if (btnDescargar) {
            btnDescargar.disabled = !this.habilitado;
            btnDescargar.title = this.habilitado ? '' : 'Descarga deshabilitada hasta confirmar la plantilla con WO';
        }

        this.render();
    },

    etiquetaAmbito: function (a) {
        return { INYECCION: 'Inyección', ENSAMBLE: 'Ensamble', EMPAQUE: 'Empaque' }[a] || a;
    },

    badgeEstado: function (estado) {
        const map = {
            RESERVADA: 'bg-secondary', LISTA_EXPORTAR: 'bg-warning text-dark',
            EXPORTADA: 'bg-success', CONFIRMADA_WO: 'bg-primary', CONFLICTO: 'bg-danger',
        };
        return `<span class="badge ${map[estado] || 'bg-light text-dark'}">${estado}</span>`;
    },

    render: function () {
        const cont = document.getElementById('expwo-listado');
        if (!cont) return;

        if (!this.ops.length) {
            cont.innerHTML = '<div class="p-4 text-muted small">No hay órdenes de producción en este rango de fechas.</div>';
            return;
        }

        cont.innerHTML = `
            <div class="table-responsive">
                <table class="table table-hover align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th style="width:40px"><input type="checkbox" id="expwo-check-todos" class="form-check-input"></th>
                            <th class="small">Fecha</th><th class="small">Área</th><th class="small">Máquina</th>
                            <th class="small">OP</th><th class="small text-center">Líneas</th>
                            <th class="small text-center">Unidades</th><th class="small">Estado</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${this.ops.map(op => `
                            <tr class="${op.lineas === 0 ? 'opacity-50' : ''}">
                                <td><input type="checkbox" class="form-check-input expwo-check" value="${op.numero_op}"
                                           ${op.lineas === 0 ? 'disabled title="Esta OP no tiene líneas para exportar"' : ''}></td>
                                <td class="small">${op.fecha_produccion}</td>
                                <td class="small">${this.etiquetaAmbito(op.ambito)}</td>
                                <td class="small text-muted">${op.maquina || '—'}</td>
                                <td class="small"><code>${op.numero_op}</code></td>
                                <td class="small text-center">${op.lineas}</td>
                                <td class="small text-center">${op.total_unidades}</td>
                                <td class="small">${this.badgeEstado(op.estado)}
                                    ${op.exportada_en ? `<div class="text-muted" style="font-size:.7rem">${op.exportada_en}</div>` : ''}</td>
                            </tr>`).join('')}
                    </tbody>
                </table>
            </div>`;

        document.getElementById('expwo-check-todos')?.addEventListener('change', (e) => {
            document.querySelectorAll('.expwo-check:not([disabled])').forEach(c => { c.checked = e.target.checked; });
        });
    },

    seleccionadas: function () {
        return [...document.querySelectorAll('.expwo-check:checked')].map(c => c.value);
    },

    previsualizar: async function () {
        const numeros = this.seleccionadas();
        if (!numeros.length) {
            return Swal.fire('Sin selección', 'Marca al menos una OP para ver su contenido.', 'info');
        }

        const res = await fetchData('/api/wo/op/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ numeros_op: numeros })
        });
        if (!res?.success) return;   // fetchData ya notificó el error

        const d = res.data;
        // Solo las columnas con contenido: la plantilla tiene 40 y 15 de
        // ellas ('Personalizado N') van siempre vacías -- mostrarlas todas
        // haría la tabla ilegible sin aportar nada.
        const colsUtiles = d.columnas.filter(c => d.filas.some(f => String(f[c] ?? '').trim() !== ''));

        document.getElementById('expwo-preview-contenido').innerHTML = `
            <div class="alert alert-info py-2 small mb-3">
                <i class="fas fa-circle-info me-1"></i>
                ${d.total_filas} línea(s) en total. Se muestran las primeras ${d.filas.length},
                y solo las ${colsUtiles.length} columnas con contenido (la plantilla completa tiene ${d.columnas.length}).
            </div>
            <div class="table-responsive" style="max-height:420px; overflow:auto;">
                <table class="table table-sm table-bordered mb-0" style="font-size:.75rem; white-space:nowrap;">
                    <thead class="table-light" style="position:sticky; top:0;">
                        <tr>${colsUtiles.map(c => `<th>${c}</th>`).join('')}</tr>
                    </thead>
                    <tbody>
                        ${d.filas.map(f => `<tr>${colsUtiles.map(c => `<td>${f[c] ?? ''}</td>`).join('')}</tr>`).join('')}
                    </tbody>
                </table>
            </div>`;
        document.getElementById('expwo-preview-card').style.display = 'block';
    },

    descargar: async function () {
        const numeros = this.seleccionadas();
        if (!numeros.length) {
            return Swal.fire('Sin selección', 'Marca al menos una OP para descargar.', 'info');
        }

        const areas = [...new Set(this.ops.filter(o => numeros.includes(o.numero_op)).map(o => o.ambito))];
        const confirmacion = await Swal.fire({
            title: '¿Generar el archivo?',
            html: `${numeros.length} OP de ${areas.length} área(s).` +
                  (areas.length > 1 ? '<br><small class="text-muted">Como hay varias áreas, se descargará un ZIP con un archivo por cada una.</small>' : ''),
            icon: 'question', showCancelButton: true,
            confirmButtonText: 'Generar y descargar', cancelButtonText: 'Cancelar', confirmButtonColor: '#0284c7'
        });
        if (!confirmacion.isConfirmed) return;

        const res = await fetchData('/api/wo/op/exportar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ numeros_op: numeros, formato: document.getElementById('expwo-formato')?.value })
        });
        if (!res?.success || !res.data?.task_id) return;

        Swal.fire({ title: 'Generando el archivo…', allowOutsideClick: false, didOpen: () => Swal.showLoading() });
        this.esperarTarea(res.data.task_id);
    },

    // El archivo se genera en background; se consulta el estado hasta que
    // termine. Límite de intentos para no dejar el spinner girando para
    // siempre si el servidor se cae a mitad de camino.
    esperarTarea: function (taskId, intento = 0) {
        clearTimeout(this._pollTimer);
        if (intento > 60) {
            Swal.fire('Está tardando demasiado', 'El archivo no terminó de generarse. Intenta de nuevo.', 'warning');
            return;
        }

        this._pollTimer = setTimeout(async () => {
            const res = await fetchData(`/api/tasks/status/${taskId}`);
            const estado = res?.data?.status;

            if (estado === 'COMPLETED') {
                Swal.close();
                window.location.href = res.data.download_url;
                await this.cargar();   // refrescar estados (pasan a EXPORTADA)
            } else if (estado === 'FAILED') {
                Swal.fire('No se pudo generar', res?.data?.error || 'Error desconocido', 'error');
            } else {
                this.esperarTarea(taskId, intento + 1);
            }
        }, 1000);
    },
};
