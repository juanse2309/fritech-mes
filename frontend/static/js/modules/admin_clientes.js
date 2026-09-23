// admin_clientes.js - Módulo de Administración de Clientes B2B

const ModuloAdminClientes = {
    clientes: [],
    clientesMaster: [], // Lista de CLIENTES (NIT autorizados)
    filtroActual: '',

    // Alias para compatibilidad con app.js
    inicializar: async function () {
        await this.init();
    },

    init: async function () {
        console.log("👥 Inicializando Módulo Admin Clientes...");
        await this.cargarClientesMaster();
        await this.cargarClientes();
        this.setupEventListeners();
    },

    setupEventListeners: function () {
        const searchInput = document.getElementById('admin-clientes-search');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                this.filtroActual = e.target.value.toLowerCase();
                this.renderizarTabla();
            });
        }

        // Delegado una sola vez sobre el contenedor (sobrevive a los
        // re-renders de renderizarTabla) -- reemplaza los onclick="...('${x}')"
        // inline, que rompían el atributo si el dato tenía una comilla.
        const container = document.getElementById('admin-clientes-container');
        if (container) {
            container.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-action]');
                if (!btn) return;
                const email = btn.dataset.email || '';
                if (btn.dataset.action === 'reset-password') {
                    this.resetearPassword(email);
                } else if (btn.dataset.action === 'toggle-estado') {
                    this.toggleEstado(email, btn.dataset.estado || '');
                }
            });
        }
    },

    cargarClientesMaster: async function () {
        try {
            const res = await fetch('/api/clientes');
            const data = await res.json();
            if (data.status === 'success') {
                this.clientesMaster = data.clientes || [];
                console.log(`✅ ${this.clientesMaster.length} clientes autorizados cargados`);
            }
        } catch (e) {
            console.error("Error cargando clientes master:", e);
        }
    },

    cargarClientes: async function () {
        const container = document.getElementById('admin-clientes-container');
        if (!container) return;

        container.innerHTML = '<div class="text-center py-5"><i class="fas fa-spinner fa-spin fa-2x text-primary"></i><p class="mt-3 text-muted">Cargando usuarios...</p></div>';

        try {
            const res = await fetch('/api/admin/clientes/listar');
            const data = await res.json();

            if (data.success) {
                this.clientes = data.clientes || [];
                this.renderizarTabla();
            } else {
                container.innerHTML = `<div class="alert alert-danger">Error: ${data.message}</div>`;
            }
        } catch (e) {
            console.error(e);
            container.innerHTML = '<div class="alert alert-danger">Error de conexión</div>';
        }
    },

    renderizarTabla: function () {
        const container = document.getElementById('admin-clientes-container');
        if (!container) return;

        let clientesFiltrados = this.clientes;

        if (this.filtroActual) {
            clientesFiltrados = this.clientes.filter(c =>
                String(c.nombre_empresa || '').toLowerCase().includes(this.filtroActual) ||
                String(c.nit || '').toLowerCase().includes(this.filtroActual) ||
                String(c.email || '').toLowerCase().includes(this.filtroActual)
            );
        }

        if (clientesFiltrados.length === 0) {
            container.innerHTML = '<div class="text-center py-5 text-muted">No se encontraron clientes</div>';
            return;
        }

        const html = `
            <div class="table-responsive">
                <table class="table table-hover align-middle">
                    <thead class="table-light">
                        <tr>
                            <th>NIT</th>
                            <th>Empresa</th>
                            <th>Email</th>
                            <th>Contacto</th>
                            <th>Estado</th>
                            <th>Registro</th>
                            <th class="text-center">Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${clientesFiltrados.map(c => this.renderizarFila(c)).join('')}
                    </tbody>
                </table>
            </div>
        `;

        container.innerHTML = html;
    },

    renderizarFila: function (cliente) {
        const estadoBadge = cliente.estado === 'ACTIVO'
            ? '<span class="badge bg-success">Activo</span>'
            : '<span class="badge bg-secondary">Inactivo</span>';

        const cambiarClave = cliente.cambiar_clave === 'TRUE' || cliente.cambiar_clave === true
            ? '<i class="fas fa-exclamation-triangle text-warning ms-2" title="Debe cambiar contraseña"></i>'
            : '';
        const email = cliente.email || '';

        return `
            <tr>
                <td class="fw-bold">${escapeHtml(cliente.nit || 'N/A')}</td>
                <td>${escapeHtml(cliente.nombre_empresa || 'N/A')}</td>
                <td><small>${escapeHtml(email || 'N/A')}</small></td>
                <td>${escapeHtml(cliente.nombre_contacto || 'N/A')}</td>
                <td>${estadoBadge}${cambiarClave}</td>
                <td><small class="text-muted">${escapeHtml(cliente.fecha_registro || 'N/A')}</small></td>
                <td class="text-center">
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-primary" data-action="reset-password" data-email="${escapeHtml(email)}" title="Resetear Contraseña">
                            <i class="fas fa-key"></i>
                        </button>
                        <button class="btn btn-outline-${cliente.estado === 'ACTIVO' ? 'warning' : 'success'}"
                                data-action="toggle-estado" data-email="${escapeHtml(email)}" data-estado="${escapeHtml(cliente.estado || '')}"
                                title="${cliente.estado === 'ACTIVO' ? 'Desactivar' : 'Activar'}">
                            <i class="fas fa-${cliente.estado === 'ACTIVO' ? 'ban' : 'check'}"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    },

    mostrarModalCrear: function () {
        const modal = document.getElementById('modal-crear-cliente');
        if (!modal) {
            this.crearModalCrear();
        }

        // Reset form
        document.getElementById('form-crear-cliente').reset();
        document.getElementById('crear-nit').value = '';
        document.getElementById('crear-nombre-empresa').value = '';

        const modalInstance = new bootstrap.Modal(document.getElementById('modal-crear-cliente'));
        modalInstance.show();
    },

    crearModalCrear: function () {
        const modalHTML = `
            <div class="modal fade" id="modal-crear-cliente" tabindex="-1">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header bg-primary text-white">
                            <h5 class="modal-title"><i class="fas fa-user-plus me-2"></i>Crear Cuenta de Cliente</h5>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <form id="form-crear-cliente">
                                <div class="row">
                                    <div class="col-md-6 mb-3">
                                        <label class="form-label fw-bold">NIT <span class="text-danger">*</span></label>
                                        <input type="text" id="crear-nit" class="form-control" required 
                                               placeholder="Buscar NIT..." list="nit-list">
                                        <datalist id="nit-list">
                                            ${this.clientesMaster.map(c => `<option value="${c}">`).join('')}
                                        </datalist>
                                        <small class="text-muted">Debe estar en la lista de clientes autorizados</small>
                                    </div>
                                    <div class="col-md-6 mb-3">
                                        <label class="form-label fw-bold">Nombre Empresa <span class="text-danger">*</span></label>
                                        <input type="text" id="crear-nombre-empresa" class="form-control" required>
                                    </div>
                                </div>
                                <div class="row">
                                    <div class="col-md-6 mb-3">
                                        <label class="form-label fw-bold">Email <span class="text-danger">*</span></label>
                                        <input type="email" id="crear-email" class="form-control" required>
                                    </div>
                                    <div class="col-md-6 mb-3">
                                        <label class="form-label fw-bold">Nombre Contacto</label>
                                        <input type="text" id="crear-contacto" class="form-control">
                                    </div>
                                </div>
                                <div class="row">
                                    <div class="col-md-4 mb-3">
                                        <label class="form-label fw-bold">Teléfono</label>
                                        <input type="tel" id="crear-telefono" class="form-control">
                                    </div>
                                    <div class="col-md-4 mb-3">
                                        <label class="form-label fw-bold">Ciudad</label>
                                        <input type="text" id="crear-ciudad" class="form-control">
                                    </div>
                                    <div class="col-md-4 mb-3">
                                        <label class="form-label fw-bold">Dirección</label>
                                        <input type="text" id="crear-direccion" class="form-control">
                                    </div>
                                </div>
                                <div class="alert alert-info mb-0">
                                    <i class="fas fa-info-circle me-2"></i>
                                    Se generará una contraseña temporal: <strong>NIT-2026</strong>
                                </div>
                            </form>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
                            <button type="button" class="btn btn-primary" onclick="ModuloAdminClientes.crearCuenta()">
                                <i class="fas fa-save me-2"></i>Crear Cuenta
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        document.body.insertAdjacentHTML('beforeend', modalHTML);
    },

    crearCuenta: async function () {
        if (window.FormValidator && !FormValidator.validateForm('form-crear-cliente')) {
            return;
        }

        const nit = document.getElementById('crear-nit').value.trim();
        const nombreEmpresa = document.getElementById('crear-nombre-empresa').value.trim();
        const email = document.getElementById('crear-email').value.trim();
        const nombreContacto = document.getElementById('crear-contacto').value.trim();
        const telefono = document.getElementById('crear-telefono').value.trim();
        const ciudad = document.getElementById('crear-ciudad').value.trim();
        const direccion = document.getElementById('crear-direccion').value.trim();

        if (!nit || !nombreEmpresa || !email) {
            Swal.fire({ icon: 'warning', title: 'Campos incompletos', text: 'Por favor complete los campos obligatorios (NIT, Nombre Empresa, Email).' });
            return;
        }

        try {
            const res = await fetch('/api/admin/clientes/crear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    nit,
                    nombre_empresa: nombreEmpresa,
                    email,
                    nombre_contacto: nombreContacto,
                    telefono,
                    ciudad,
                    direccion
                })
            });

            const data = await res.json();

            if (data.success) {
                // Mostrar credenciales
                this.mostrarCredenciales(data.credenciales);

                // Cerrar modal
                bootstrap.Modal.getInstance(document.getElementById('modal-crear-cliente')).hide();

                // Recargar lista
                await this.cargarClientes();

                if (window.AuthModule) {
                    window.AuthModule.mostrarNotificacion('Cuenta creada exitosamente', 'success');
                }
            } else {
                Swal.fire({ icon: 'error', title: 'Error', text: data.message });
            }
        } catch (e) {
            console.error(e);
            Swal.fire({ icon: 'error', title: 'Error de conexión', text: 'No se pudo conectar con el servidor. Intenta de nuevo.' });
        }
    },

    mostrarCredenciales: function (creds) {
        const html = `
            <div class="modal fade" id="modal-credenciales" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header bg-success text-white">
                            <h5 class="modal-title"><i class="fas fa-check-circle me-2"></i>Cuenta Creada</h5>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="alert alert-success">
                                <h6 class="fw-bold mb-3">Credenciales Generadas:</h6>
                                <div class="mb-2">
                                    <strong>NIT:</strong> <code>${creds.nit}</code>
                                </div>
                                <div class="mb-2">
                                    <strong>Email:</strong> <code>${creds.email}</code>
                                </div>
                                <div class="mb-2">
                                    <strong>Contraseña Temporal:</strong> <code id="pass-temp">${creds.password_temporal}</code>
                                    <button class="btn btn-sm btn-outline-primary ms-2" onclick="navigator.clipboard.writeText('${creds.password_temporal}')">
                                        <i class="fas fa-copy"></i>
                                    </button>
                                </div>
                            </div>
                            <p class="text-muted small mb-0">
                                <i class="fas fa-info-circle me-1"></i>
                                El cliente deberá cambiar su contraseña en el primer inicio de sesión.
                            </p>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-primary" data-bs-dismiss="modal">Entendido</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Remove old modal if exists
        const oldModal = document.getElementById('modal-credenciales');
        if (oldModal) oldModal.remove();

        document.body.insertAdjacentHTML('beforeend', html);
        const modalInstance = new bootstrap.Modal(document.getElementById('modal-credenciales'));
        modalInstance.show();
    },

    resetearPassword: async function (email) {
        const confirmacion = await Swal.fire({
            title: '¿Resetear contraseña?',
            html: `Se generará una nueva contraseña temporal para <strong>${email}</strong>.`,
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: 'Sí, resetear',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#dc3545',
            cancelButtonColor: '#6c757d'
        });
        if (!confirmacion.isConfirmed) {
            return;
        }

        try {
            const res = await fetch('/api/admin/clientes/reset-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email })
            });

            const data = await res.json();

            if (data.success) {
                this.mostrarCredenciales({
                    nit: data.nit || 'N/A',
                    email: email,
                    password_temporal: data.password_temporal
                });

                await this.cargarClientes();

                if (window.AuthModule) {
                    window.AuthModule.mostrarNotificacion('Contraseña reseteada', 'success');
                }
            } else {
                Swal.fire({ icon: 'error', title: 'Error', text: data.message });
            }
        } catch (e) {
            console.error(e);
            Swal.fire({ icon: 'error', title: 'Error de conexión', text: 'No se pudo conectar con el servidor. Intenta de nuevo.' });
        }
    },

    toggleEstado: async function (email, estadoActual) {
        const nuevoEstado = estadoActual === 'ACTIVO' ? 'INACTIVO' : 'ACTIVO';
        const accion = nuevoEstado === 'ACTIVO' ? 'activar' : 'desactivar';

        const confirmacion = await Swal.fire({
            title: `¿${accion.charAt(0).toUpperCase() + accion.slice(1)} cuenta?`,
            html: `¿Confirma ${accion} la cuenta de <strong>${email}</strong>?`,
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: `Sí, ${accion}`,
            cancelButtonText: 'Cancelar',
            confirmButtonColor: nuevoEstado === 'ACTIVO' ? '#10b981' : '#dc3545',
            cancelButtonColor: '#6c757d'
        });
        if (!confirmacion.isConfirmed) {
            return;
        }

        try {
            const res = await fetch('/api/admin/clientes/toggle-estado', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, estado: nuevoEstado })
            });

            const data = await res.json();

            if (data.success) {
                await this.cargarClientes();

                if (window.AuthModule) {
                    window.AuthModule.mostrarNotificacion(`Cuenta ${accion}da`, 'success');
                }
            } else {
                Swal.fire({ icon: 'error', title: 'Error', text: data.message });
            }
        } catch (e) {
            console.error(e);
            Swal.fire({ icon: 'error', title: 'Error de conexión', text: 'No se pudo conectar con el servidor. Intenta de nuevo.' });
        }
    }
};

window.ModuloAdminClientes = ModuloAdminClientes;
