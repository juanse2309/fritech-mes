// metals_comercial.js - Dashboard comercial de Frimetals (ventas y cartera de World Office)
//
// Solo pinta: toda la agregación vive en el backend
// (GET /api/comercial/dashboard, ver ComercialDashboardService). La cartera es
// una foto del momento (no depende del filtro de fechas) y sale de los
// endpoints que ya usa el módulo Cartera.

const ModuloMetalsComercial = {
    _initDone: false,
    _charts: {},
    _reqId: 0,

    fmt: new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }),
    fmtNum: new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 }),

    MESES: ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'],

    inicializar: function () {
        if (!this._initDone) {
            this._initDone = true;
            console.log('📈 Inicializando Dashboard Comercial (Frimetals)...');
            this._aplicarPreset('anio', false);
        }
        this.cargar();
    },

    desactivar: function () {
        this._destruirGraficas();
    },

    // ------------------------------------------------------------------
    // Filtros
    // ------------------------------------------------------------------
    _iso: function (d) {
        // Fecha LOCAL como YYYY-MM-DD (toISOString() la correría por UTC).
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    },

    _aplicarPreset: function (preset, recargar = true) {
        const hoy = new Date();
        let desde, hasta = hoy;
        if (preset === 'anio') {
            desde = new Date(hoy.getFullYear(), 0, 1);
        } else if (preset === 'anio_pasado') {
            desde = new Date(hoy.getFullYear() - 1, 0, 1);
            hasta = new Date(hoy.getFullYear() - 1, 11, 31);
        } else if (preset === '12m') {
            desde = new Date(hoy.getFullYear() - 1, hoy.getMonth(), hoy.getDate() + 1);
        } else if (preset === 'mes') {
            desde = new Date(hoy.getFullYear(), hoy.getMonth(), 1);
        } else {
            return;
        }
        const elDesde = document.getElementById('mc-fecha-desde');
        const elHasta = document.getElementById('mc-fecha-hasta');
        if (elDesde) elDesde.value = this._iso(desde);
        if (elHasta) elHasta.value = this._iso(hasta);
        document.querySelectorAll('#metals-comercial-page [data-mc-preset]').forEach(b => {
            b.classList.toggle('active', b.dataset.mcPreset === preset);
        });
        if (recargar) this.cargar();
    },

    preset: function (nombre) {
        this._aplicarPreset(nombre, true);
    },

    aplicarFiltro: function () {
        // Fechas escritas a mano: ningún preset sigue "activo".
        document.querySelectorAll('#metals-comercial-page [data-mc-preset]').forEach(b => b.classList.remove('active'));
        this.cargar();
    },

    // ------------------------------------------------------------------
    // Carga
    // ------------------------------------------------------------------
    _headers: function () {
        const headers = { 'Accept': 'application/json' };
        const pwaToken = localStorage.getItem('pwa_token');
        if (pwaToken) headers['Authorization'] = `Bearer ${pwaToken}`;
        return headers;
    },

    cargar: async function () {
        const reqId = ++this._reqId; // descarta respuestas viejas si el usuario cambia el filtro rápido
        const desde = document.getElementById('mc-fecha-desde')?.value || '';
        const hasta = document.getElementById('mc-fecha-hasta')?.value || '';

        this._estado('cargando');

        const qs = new URLSearchParams();
        if (desde) qs.set('desde', desde);
        if (hasta) qs.set('hasta', hasta);

        const headers = this._headers();
        const [ventasRes, resumenRes, carteraRes] = await Promise.allSettled([
            fetch(`/api/comercial/dashboard?${qs.toString()}`, { headers, credentials: 'include' }).then(r => r.json()),
            fetch('/api/dashboard/cartera', { headers, credentials: 'include' }).then(r => r.json()),
            fetch('/api/cartera/listar', { headers, credentials: 'include' }).then(r => r.json())
        ]);
        if (reqId !== this._reqId) return;

        const ventas = ventasRes.status === 'fulfilled' ? ventasRes.value : null;
        if (!ventas || !ventas.success) {
            const msg = ventas?.detalles?.join(' · ') || ventas?.error || 'No fue posible cargar las ventas.';
            this._estado('error', msg);
            return;
        }

        // La cartera es complementaria: si falla, el resto del dashboard igual se pinta.
        const resumen = resumenRes.status === 'fulfilled' && resumenRes.value?.success ? resumenRes.value.data : null;
        const cartera = carteraRes.status === 'fulfilled' && carteraRes.value?.success ? (carteraRes.value.clientes || []) : null;

        this._estado('listo');
        this._periodo = ventas.data.periodo;
        this._renderPeriodo(ventas.data.periodo);
        this._renderKpis(ventas.data.kpis, resumen);
        this._renderKpis2(ventas.data);
        this._renderMensual(ventas.data.mensual);
        this._renderCumplimiento(ventas.data.tabla_mensual.filas);
        this._renderTablaMensual(ventas.data.tabla_mensual);
        this._renderVendedores(ventas.data.vendedores);
        this._renderProductos(ventas.data.productos_por_ventas, ventas.data.productos_por_unidades);
        this._renderClientes(ventas.data.clientes);
        this._renderZonas(ventas.data.zonas);
        this._renderPedidosPlanta(ventas.data.pedidos_planta);
        this._renderClientesNuevos(ventas.data.clientes_nuevos);
        this._renderCartera(resumen, cartera);
    },

    _estado: function (estado, mensaje) {
        const cont = document.getElementById('mc-contenido');
        const aviso = document.getElementById('mc-aviso');
        if (!cont || !aviso) return;
        if (estado === 'listo') {
            aviso.style.display = 'none';
            cont.style.opacity = '1';
            return;
        }
        aviso.style.display = 'block';
        if (estado === 'cargando') {
            aviso.className = 'alert alert-light border text-center';
            aviso.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Cargando ventas desde World Office...';
            cont.style.opacity = '0.5';
        } else {
            aviso.className = 'alert alert-danger';
            aviso.textContent = mensaje || 'Error al cargar el dashboard.';
            cont.style.opacity = '0.5';
        }
    },

    // ------------------------------------------------------------------
    // Formato
    // ------------------------------------------------------------------
    _millones: function (v) {
        // $563,4 M -- legible en una tarjeta; el valor exacto va en el title.
        const m = (v || 0) / 1e6;
        return `$${m.toLocaleString('es-CO', { maximumFractionDigits: 1 })} M`;
    },

    _pct: function (n) {
        return (n || 0).toLocaleString('es-CO', { maximumFractionDigits: 1 });
    },

    _fechaCorta: function (iso) {
        const [a, m, d] = String(iso).split('-');
        return `${d}/${m}/${a}`;
    },

    _variacion: function (pct) {
        if (pct === null || pct === undefined) return '<span class="text-muted">sin base previa</span>';
        const cls = pct >= 0 ? 'text-success' : 'text-danger';
        const flecha = pct >= 0 ? '▲' : '▼';
        return `<span class="${cls} fw-bold">${flecha} ${Math.abs(pct).toLocaleString('es-CO', { maximumFractionDigits: 1 })}%</span> vs periodo previo`;
    },

    _etiquetaMes: function (ym) {
        const [a, m] = String(ym).split('-');
        return `${this.MESES[parseInt(m, 10) - 1]} ${a.slice(2)}`;
    },

    _set: function (id, html) {
        const el = document.getElementById(id);
        if (el) el.innerHTML = html;
    },

    // ------------------------------------------------------------------
    // Render
    // ------------------------------------------------------------------
    _renderPeriodo: function (p) {
        this._set('mc-periodo-txt',
            `Comparado con ${escapeHtml(this._fechaCorta(p.desde_previo))} – ${escapeHtml(this._fechaCorta(p.hasta_previo))}`);
    },

    _tarjeta: function (etiqueta, valor, sub, titulo, cols) {
        return `
            <div class="${cols || 'col-6 col-lg-4 col-xl-2'}">
                <div class="card shadow-sm border-0 h-100" style="border-radius: 14px;">
                    <div class="card-body py-3">
                        <div class="text-muted text-uppercase fw-bold" style="font-size:0.68rem; letter-spacing:.04em;">${etiqueta}</div>
                        <div class="fw-bold mt-1" style="font-size:1.45rem; color:#1e293b;" title="${escapeHtml(titulo || '')}">${valor}</div>
                        <div class="small mt-1 text-muted">${sub}</div>
                    </div>
                </div>
            </div>`;
    },

    _renderKpis: function (k, resumenCartera) {
        const a = k.actual, v = k.variacion_pct;
        const total = resumenCartera?.total_cartera ?? null;
        const vencida = resumenCartera?.total_vencida ?? null;
        const pctVencido = total ? Math.round((vencida / total) * 100) : null;

        this._set('mc-kpis', [
            this._tarjeta('Ventas', this._millones(a.ventas), this._variacion(v.ventas), this.fmt.format(a.ventas)),
            this._tarjeta('Unidades', this.fmtNum.format(a.unidades), this._variacion(v.unidades)),
            this._tarjeta('Ticket promedio', this._millones(a.ticket_promedio),
                `${this.fmtNum.format(a.documentos)} facturas`, this.fmt.format(a.ticket_promedio)),
            this._tarjeta('Clientes activos', this.fmtNum.format(a.clientes),
                `${this.fmtNum.format(k.previo.clientes)} en el periodo previo`),
            this._tarjeta('Cartera total', total === null ? 's/d' : this._millones(total),
                'foto de hoy', total === null ? '' : this.fmt.format(total)),
            this._tarjeta('Cartera vencida', vencida === null ? 's/d' : this._millones(vencida),
                pctVencido === null ? '' : `<span class="text-danger fw-bold">${pctVencido}%</span> del total`,
                vencida === null ? '' : this.fmt.format(vencida))
        ].join(''));
    },

    _destruirGraficas: function () {
        Object.values(this._charts).forEach(c => { try { c.destroy(); } catch (e) { /* ya destruida */ } });
        this._charts = {};
    },

    _crearGrafica: function (clave, canvasId, config) {
        if (this._charts[clave]) { this._charts[clave].destroy(); delete this._charts[clave]; }
        const canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') return;
        this._charts[clave] = new Chart(canvas, config);
    },

    _ejes: function () {
        return {
            x: { grid: { display: false }, ticks: { font: { size: 11 } } },
            y: { beginAtZero: true, ticks: { font: { size: 11 } }, grid: { color: 'rgba(148,163,184,.2)' } }
        };
    },

    _renderMensual: function (mensual) {
        const etiquetas = mensual.map(m => this._etiquetaMes(m.mes));
        this._crearGrafica('mensual', 'mc-chart-mensual', {
            type: 'bar',
            data: {
                labels: etiquetas,
                datasets: [
                    { label: 'Periodo actual', data: mensual.map(m => m.ventas / 1e6), backgroundColor: '#3b82f6', borderRadius: 4, order: 2 },
                    { label: 'Mismo mes año previo', data: mensual.map(m => m.ventas_previo / 1e6), type: 'line',
                      borderColor: '#94a3b8', backgroundColor: '#94a3b8', borderWidth: 2, pointRadius: 3, tension: 0.25, order: 1 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
                    tooltip: { callbacks: { label: c => `${c.dataset.label}: $${c.parsed.y.toLocaleString('es-CO', { maximumFractionDigits: 1 })} M` } }
                },
                scales: this._ejes()
            }
        });
    },

    _renderCumplimiento: function (cumplimiento) {
        // 'cumplimiento' = filas de tabla_mensual (mes, facturado, pedidos, cumplimiento_pct)
        const etiquetas = cumplimiento.map(c => this._etiquetaMes(c.mes));
        const valores = cumplimiento.map(c => c.cumplimiento_pct);
        const color = v => v === null ? '#cbd5e1' : v >= 100 ? '#10b981' : v >= 85 ? '#f59e0b' : '#ef4444';
        this._crearGrafica('cumplimiento', 'mc-chart-cumplimiento', {
            type: 'bar',
            data: { labels: etiquetas, datasets: [{ data: valores.map(v => v === null ? 0 : v), backgroundColor: valores.map(color), borderRadius: 4 }] },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: c => {
                                const f = cumplimiento[c.dataIndex];
                                if (f.cumplimiento_pct === null) return 'Sin pedidos en el mes';
                                return `${this._pct(f.cumplimiento_pct)}% · facturado ${this._millones(f.facturado)} / pedidos ${this._millones(f.pedidos)}`;
                            }
                        }
                    }
                },
                scales: this._ejes()
            }
        });
    },

    _filaBarra: function (nombre, valor, maximo, derecha, sub, color, insignia) {
        const pct = maximo > 0 ? Math.max(2, Math.round((valor / maximo) * 100)) : 0;
        return `
            <div class="mb-3">
                <div class="d-flex justify-content-between align-items-baseline gap-2">
                    <span class="small fw-bold text-truncate" style="color:#1e293b; min-width:0;" title="${escapeHtml(nombre)}">${escapeHtml(nombre)}${insignia || ''}</span>
                    <span class="small fw-bold text-nowrap" style="font-variant-numeric:tabular-nums;">${derecha}</span>
                </div>
                <div class="progress" style="height:7px; background:#e2e8f0;">
                    <div class="progress-bar" style="width:${pct}%; background:${color};"></div>
                </div>
                <div class="text-muted" style="font-size:0.72rem;">${sub}</div>
            </div>`;
    },

    _lista: function (id, filas, vacio) {
        this._set(id, filas.length ? filas.join('') : `<div class="text-muted small py-3">${vacio}</div>`);
    },

    _renderVendedores: function (vendedores) {
        const max = Math.max(0, ...vendedores.map(v => v.total_ventas));
        this._lista('mc-vendedores', vendedores.map(v => this._filaBarra(
            v.nombre, v.total_ventas, max, this._millones(v.total_ventas),
            `${this._pct(v.participacion_pct)}% · ${this.fmtNum.format(v.total_unidades)} unid. · ${this.fmtNum.format(v.clientes || 0)} clientes`,
            '#6366f1')), 'Sin ventas en el periodo.');
    },

    _renderProductos: function (porVentas, porUnidades) {
        const fila = (p, valorClave, maximo, textoDerecha, color) => this._filaBarra(
            p.codigo, p[valorClave], maximo, textoDerecha,
            p.descripcion ? escapeHtml(p.descripcion) : '<span class="fst-italic">sin descripción</span>', color);

        const maxV = Math.max(0, ...porVentas.map(p => p.total_ventas));
        const maxU = Math.max(0, ...porUnidades.map(p => p.total_unidades));
        this._lista('mc-prod-ventas', porVentas.map(p => fila(p, 'total_ventas', maxV, this._millones(p.total_ventas), '#0ea5e9')),
            'Sin ventas en el periodo.');
        this._lista('mc-prod-unidades', porUnidades.map(p => fila(p, 'total_unidades', maxU, `${this.fmtNum.format(p.total_unidades)} unid.`, '#14b8a6')),
            'Sin ventas en el periodo.');
    },

    _renderClientes: function (clientes) {
        const max = Math.max(0, ...clientes.map(c => c.total_ventas));
        this._lista('mc-clientes', clientes.map(c => this._filaBarra(
            c.nombre, c.total_ventas, max, this._millones(c.total_ventas),
            `${this._pct(c.participacion_pct)}% del total · ${this.fmtNum.format(c.total_unidades)} unid.`,
            c.entre_empresas ? '#f59e0b' : '#3b82f6',
            c.entre_empresas ? ' <span class="badge bg-warning text-dark ms-1" style="font-size:0.62rem;">entre empresas</span>' : '')),
            'Sin ventas en el periodo.');
    },

    _renderZonas: function (zonas) {
        const max = Math.max(0, ...zonas.map(z => z.total_ventas));
        this._lista('mc-zonas', zonas.map(z => this._filaBarra(
            z.nombre, z.total_ventas, max, this._millones(z.total_ventas),
            `${this._pct(z.participacion_pct)}% del total`, '#8b5cf6')), 'Sin ventas en el periodo.');
    },

    // ------------------------------------------------------------------
    // Bloques nuevos: KPIs 2, tabla mensual, pedidos en planta, clientes nuevos
    // ------------------------------------------------------------------
    _renderKpis2: function (d) {
        const cols = 'col-6 col-lg-3';
        const dc = d.dias_cartera;
        const conc = d.concentracion;
        const cn = d.clientes_nuevos;
        const pp = d.pedidos_planta;

        const diasCartera = this._tarjeta('Días de cartera',
            dc && dc.dias !== null ? `${this.fmtNum.format(dc.dias)} días` : 's/d',
            dc && dc.dias !== null
                ? `cartera ${this._millones(dc.cartera)} ÷ venta diaria ${this._millones(dc.venta_diaria)}`
                : 'sin ventas en el rango o cartera no disponible', '', cols);

        let subConc = 'sin ventas en el rango';
        if (conc && conc.top5_pct !== null) {
            const grupo = conc.entre_empresas_clientes || [];
            subConc = conc.entre_empresas_pct > 0
                ? `${escapeHtml(grupo.length === 1 ? grupo[0] : 'Entre empresas')}: ${this._pct(conc.entre_empresas_pct)}%`
                : 'sin ventas entre empresas';
        }
        const concentracion = this._tarjeta('Concentración top 5',
            conc && conc.top5_pct !== null ? `${this._pct(conc.top5_pct)}%` : 's/d', subConc, '', cols);

        let valNuevos = 's/d', subNuevos = 'el rango empieza donde empieza la historia de WO';
        if (cn && cn.disponible) {
            valNuevos = this.fmtNum.format(cn.nuevos.cantidad);
            subNuevos = `${this._millones(cn.nuevos.total)} · ${this.fmtNum.format(cn.recuperados.cantidad)} recuperados`;
        }
        const nuevos = this._tarjeta('Clientes nuevos', valNuevos, subNuevos, '', cols);

        let valPlanta = 's/d', subPlanta = 'no se pudo leer';
        if (pp) {
            const n = pp.reduce((a, e) => a + e.pedidos, 0);
            const v = pp.reduce((a, e) => a + e.valor, 0);
            valPlanta = this._millones(v);
            subPlanta = `${this.fmtNum.format(n)} pedidos en el rango`;
        }
        const planta = this._tarjeta('Pedidos en planta', valPlanta, subPlanta, '', cols);

        this._set('mc-kpis2', diasCartera + concentracion + nuevos + planta);
    },

    MESES_LARGOS: ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'],

    _m1: function (v) {
        // millones con 1 decimal y signo real (−) para negativos: "−24,3".
        const m = (v || 0) / 1e6;
        const txt = Math.abs(m).toLocaleString('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
        return m < 0 ? `−${txt}` : txt;
    },

    _pct1: function (n) {
        // un decimal fijo: en la tabla, "+114,0%" junto a "+189,1%" se lee parejo
        return (n || 0).toLocaleString('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    },

    _pillCumplimiento: function (pct) {
        if (pct === null || pct === undefined) {
            return '<span class="badge" style="background:#e2e8f0; color:#64748b; min-width:48px;">—</span>';
        }
        const [bg, fg] = pct >= 100 ? ['#d1fae5', '#047857'] : pct >= 85 ? ['#fef3c7', '#b45309'] : ['#fee2e2', '#b91c1c'];
        return `<span class="badge" style="background:${bg}; color:${fg}; min-width:48px;">${this._pct1(pct)}%</span>`;
    },

    _etiquetaFilaMes: function (ym) {
        // Marca los meses parciales: el mes del "hasta" si no está completo, y el del "desde" si no arranca el día 1.
        const [anio, mes] = ym.split('-').map(Number);
        const p = this._periodo;
        const variosAnios = p && p.desde.slice(0, 4) !== p.hasta.slice(0, 4);
        let nombre = `${this.MESES_LARGOS[mes - 1]}${variosAnios ? ' ' + anio : ''}`;
        if (p) {
            const [hA, hM, hD] = p.hasta.split('-').map(Number);
            const [dA, dM, dD] = p.desde.split('-').map(Number);
            const ultimoDia = new Date(hA, hM, 0).getDate();
            if (anio === hA && mes === hM && hD < ultimoDia) {
                nombre += ` <span class="badge bg-warning text-dark ms-1" style="font-size:0.6rem;">al ${hD}</span>`;
            } else if (anio === dA && mes === dM && dD > 1) {
                nombre += ` <span class="badge bg-warning text-dark ms-1" style="font-size:0.6rem;">desde ${dD}</span>`;
            }
        }
        return nombre;
    },

    _renderTablaMensual: function (tabla) {
        const varTxt = v => v === null || v === undefined
            ? '<span class="text-muted">—</span>'
            : `<span class="${v >= 0 ? 'text-success' : 'text-danger'}">${v >= 0 ? '+' : '−'}${this._pct1(Math.abs(v))}%</span>`;
        const dif = v => `<span class="${v > 0 ? '' : 'text-muted'}">${this._m1(v)}</span>`;

        const filas = tabla.filas.map(f => `
            <tr>
                <td class="text-start">${this._etiquetaFilaMes(f.mes)}</td>
                <td>${this._m1(f.pedidos)}</td>
                <td class="fw-bold">${this._m1(f.facturado)}</td>
                <td>${dif(f.diferencia)}</td>
                <td>${this._pillCumplimiento(f.cumplimiento_pct)}</td>
                <td class="text-muted">${this._m1(f.facturado_previo)}</td>
                <td>${varTxt(f.variacion_pct)}</td>
                <td>${this._m1(f.acumulado)}</td>
            </tr>`).join('');

        const t = tabla.total;
        const total = `
            <tr class="fw-bold" style="border-top:2px solid #cbd5e1;">
                <td class="text-start">Total</td>
                <td>${this._m1(t.pedidos)}</td>
                <td>${this._m1(t.facturado)}</td>
                <td>${dif(t.diferencia)}</td>
                <td>${this._pillCumplimiento(t.cumplimiento_pct)}</td>
                <td>${this._m1(t.facturado_previo)}</td>
                <td>${varTxt(t.variacion_pct)}</td>
                <td>${this._m1(t.facturado)}</td>
            </tr>`;

        this._set('mc-tabla-mensual', `
            <table class="table table-sm align-middle mb-0 text-end" style="font-variant-numeric:tabular-nums; font-size:0.85rem;">
                <thead class="text-muted" style="font-size:0.68rem; text-transform:uppercase; letter-spacing:.04em;">
                    <tr>
                        <th class="text-start">Mes</th><th>Pedidos</th><th>Facturado</th><th>Diferencia</th>
                        <th>Cumpl.</th><th>Año previo</th><th>Var.</th><th>Acumulado</th>
                    </tr>
                </thead>
                <tbody>${filas}${total}</tbody>
            </table>`);
    },

    _colorEstado: function (estado) {
        const e = String(estado || '').toUpperCase();
        if (e.includes('DESPACHADO')) return '#10b981';
        if (e.includes('EXPORTADO')) return '#3b82f6';
        if (e.includes('LISTO')) return '#f59e0b';
        if (e.includes('ALISTAMIENTO')) return '#f97316';
        return '#94a3b8';
    },

    _renderPedidosPlanta: function (pp) {
        if (!pp) {
            this._set('mc-pedidos-planta', '<div class="text-muted small py-3">No se pudieron leer los pedidos de la app.</div>');
            return;
        }
        const max = Math.max(0, ...pp.map(e => e.pedidos));
        this._lista('mc-pedidos-planta', pp.map(e => {
            const texto = String(e.estado).toLowerCase().replace(/_/g, ' ');
            return this._filaBarra((texto.charAt(0).toUpperCase() + texto.slice(1)).replace(/\bwo\b/i, 'WO'), e.pedidos, max,
                this.fmtNum.format(e.pedidos), `${this._millones(e.valor)} en pedidos`, this._colorEstado(e.estado));
        }), 'No hay pedidos creados en la app dentro del rango.');
    },

    _renderClientesNuevos: function (cn) {
        if (!cn || !cn.disponible) {
            this._set('mc-clientes-nuevos',
                '<div class="text-muted small py-3">No se puede calcular: el rango arranca en (o antes de) la primera venta registrada en WO, ' +
                'y todos los clientes parecerían nuevos. Elige un rango que empiece después.</div>');
            return;
        }
        const max = Math.max(0, ...cn.nuevos.top.map(c => c.total_ventas));
        const lista = cn.nuevos.top.map(c => this._filaBarra(
            c.nombre, c.total_ventas, max, this._millones(c.total_ventas), 'primera compra en este periodo', '#7c3aed'));
        const extra = cn.nuevos.cantidad > cn.nuevos.top.length
            ? ` Se muestran los ${cn.nuevos.top.length} mayores de ${this.fmtNum.format(cn.nuevos.cantidad)} nuevos.` : '';
        const pie = `<div class="text-muted small border-top pt-2 mt-1">
            <i class="fas fa-redo me-1"></i><b>Recuperados:</b> ${this.fmtNum.format(cn.recuperados.cantidad)} clientes
            (${this._millones(cn.recuperados.total)}) que volvieron tras más de ${this.fmtNum.format(cn.dias_inactividad)} días sin comprar.${extra}
        </div>`;
        this._set('mc-clientes-nuevos',
            (lista.length ? lista.join('') : '<div class="text-muted small py-3">No hubo clientes nuevos en el periodo.</div>') + pie);
    },

    _renderCartera: function (resumen, cartera) {
        if (!cartera) {
            this._destruirGrafica('edades');
            this._set('mc-morosos', '<div class="text-muted small py-3">No se pudo cargar la cartera.</div>');
            return;
        }

        // Suma de las columnas de edades que ya calcula el backend por cliente.
        const edades = cartera.reduce((acc, c) => {
            acc.corriente += c.corriente || 0; acc.d1 += c.d1_30 || 0; acc.d2 += c.d31_60 || 0;
            acc.d3 += c.d61_90 || 0; acc.d4 += c.mas_90 || 0;
            return acc;
        }, { corriente: 0, d1: 0, d2: 0, d3: 0, d4: 0 });

        this._crearGrafica('edades', 'mc-chart-edades', {
            type: 'bar',
            data: {
                labels: ['Corriente', '1-30 d', '31-60 d', '61-90 d', '+90 d'],
                datasets: [{
                    data: [edades.corriente, edades.d1, edades.d2, edades.d3, edades.d4].map(v => v / 1e6),
                    backgroundColor: ['#10b981', '#f59e0b', '#f59e0b', '#f97316', '#ef4444'], borderRadius: 4
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: c => `$${c.parsed.y.toLocaleString('es-CO', { maximumFractionDigits: 1 })} M` } }
                },
                scales: this._ejes()
            }
        });

        // Mayores saldos vencidos = todo lo que no es "corriente".
        const vencidos = cartera
            .map(c => ({ nombre: c.nombre, vencido: (c.saldo_total || 0) - (c.corriente || 0), total: c.saldo_total || 0 }))
            .filter(c => c.vencido > 0)
            .sort((a, b) => b.vencido - a.vencido)
            .slice(0, 8);
        const max = Math.max(0, ...vencidos.map(c => c.vencido));
        this._lista('mc-morosos', vencidos.map(c => this._filaBarra(
            c.nombre, c.vencido, max, this._millones(c.vencido),
            `de ${this._millones(c.total)} en cartera`, '#ef4444')), 'No hay cartera vencida.');
    },

    _destruirGrafica: function (clave) {
        if (this._charts[clave]) { this._charts[clave].destroy(); delete this._charts[clave]; }
    }
};

window.ModuloMetalsComercial = ModuloMetalsComercial;
