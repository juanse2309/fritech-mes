
// auth.js - Manejo de Autenticación, Permisos y Portal B2B

const AuthModule = {
    currentUser: null,
    authorizedPages: [],
    currentStaffType: 'FRIPARTS', // 'FRIPARTS' o 'FRIMETALS'

    // Normalización robusta de roles (quita tildes y pasa a MAYUSCULAS)
    normalizeRole: function (role) {
        if (!role) return 'INVITADO';
        let r = role.toString()
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toUpperCase()
            .trim();
        
        // REGLA DE RESCATE: Cualquier variante de ADMIN es TRATADA IGUAL
        if (r === 'ADMINISTRACION' || r === 'ADMINISTRADOR' || r === 'ADMIN') {
            return 'ADMIN';
        }
        return r;
    },

    // Obtener permisos para un rol (soporta coincidencias parciales)
    getPagesForRole: function (roleName) {
        const normalizedRole = this.normalizeRole(roleName);

        // 1. Coincidencia exacta de Rol
        if (this.permissions[normalizedRole]) return [...this.permissions[normalizedRole]];

        // 2. Coincidencia parcial (ej: "ADMINISTRADOR" en "JEFE ADMINISTRADOR")
        for (const key in this.permissions) {
            if (key !== 'INVITADO' && normalizedRole.includes(key)) {
                console.log(`🔍 Coincidencia parcial de rol de "${normalizedRole}" con "${key}"`);
                return [...this.permissions[key]];
            }
        }

        return [...(this.permissions['INVITADO'] || [])];
    },

    // Verifica si el usuario actual tiene acceso a una página
    isPageAllowed: function (pageName) {
        if (!this.currentUser) return false;
        if (this.authorizedPages && this.authorizedPages.length > 0) {
            return this.authorizedPages.includes(pageName);
        }

        // Fallback si no se ha inicializado sidebar aún (ej: carga ultra-rápida app.js)
        const role = this.normalizeRole(this.currentUser.rol || this.currentUser.role);
        const allowedBase = this.getPagesForRole(role, this.currentUser.nombre || this.currentUser.name || '');
        return allowedBase.includes(pageName);
    },

    // Nombre del usuario activo, para pre-poblar campos de responsable/operario
    getUsuarioActual: function () {
        if (!this.currentUser) return '';
        return this.currentUser.nombre || this.currentUser.username || '';
    },

    // Matriz de Permisos por Rol (Modelo RBAC Estricto basado en Departamentos)
    permissions: {
        'ADMINISTRACION': ['dashboard', 'inventario', 'inyeccion', 'simulador', 'pulido', 'ensamble', 'pnc', 'facturacion', 'mezcla', 'historial', 'reportes', 'pedidos', 'almacen', 'admin-clientes', 'asistencia', 'nomina', 'gerencia', 'notificaciones', 'comercial-historico', 'cartera', 'auditoria-op', 'metals-dashboard', 'metals-produccion', 'metals-pedidos', 'metals-torno', 'metals-laser', 'metals-soldadura', 'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura', 'metals-zincado', 'metals-horno', 'metals-pulido-m'],
        'ADMINISTRADOR': ['dashboard', 'inventario', 'inyeccion', 'simulador', 'pulido', 'ensamble', 'pnc', 'facturacion', 'mezcla', 'historial', 'reportes', 'pedidos', 'almacen', 'admin-clientes', 'asistencia', 'nomina', 'gerencia', 'notificaciones', 'comercial-historico', 'cartera', 'auditoria-op', 'metals-dashboard', 'metals-produccion', 'metals-pedidos', 'metals-torno', 'metals-laser', 'metals-soldadura', 'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura', 'metals-zincado', 'metals-horno', 'metals-pulido-m'],
        'ADMIN': ['dashboard', 'inventario', 'inyeccion', 'simulador', 'pulido', 'ensamble', 'pnc', 'facturacion', 'mezcla', 'historial', 'reportes', 'pedidos', 'almacen', 'admin-clientes', 'asistencia', 'nomina', 'gerencia', 'notificaciones', 'comercial-historico', 'cartera', 'auditoria-op', 'metals-dashboard', 'metals-produccion', 'metals-pedidos', 'metals-torno', 'metals-laser', 'metals-soldadura', 'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura', 'metals-zincado', 'metals-horno', 'metals-pulido-m'],
        'GERENCIA': ['dashboard', 'inventario', 'inyeccion', 'simulador', 'pulido', 'ensamble', 'pnc', 'facturacion', 'mezcla', 'historial', 'reportes', 'pedidos', 'almacen', 'admin-clientes', 'asistencia', 'nomina', 'gerencia', 'notificaciones', 'comercial-historico', 'cartera', 'auditoria-op', 'metals-dashboard', 'metals-produccion', 'metals-pedidos'],
        // === FRIMETALS ROLES (Phase 2 Multi-Tenant) ===
        'STAFF FRIMETALS': ['metals-dashboard', 'metals-produccion', 'metals-pedidos', 'metals-torno', 'metals-laser', 'metals-soldadura', 'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura', 'metals-zincado', 'metals-horno', 'metals-pulido-m', 'pedidos', 'almacen', 'asistencia'],
        'JEFE DE PLANTA': ['metals-dashboard', 'metals-produccion', 'metals-pedidos', 'metals-torno', 'metals-laser', 'metals-soldadura', 'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura', 'metals-zincado', 'metals-horno', 'metals-pulido-m', 'pedidos', 'almacen', 'asistencia', 'historial', 'inventario'],
        'COMERCIAL FRIMETALS': ['metals-dashboard', 'metals-pedidos', 'pedidos', 'almacen', 'comercial-historico'],
        'COMERCIAL': ['almacen', 'pedidos', 'comercial-historico', 'cartera'],
        'JEFE ALMACEN': ['inventario', 'inyeccion', 'facturacion', 'almacen', 'pedidos', 'asistencia'],
        'JEFE ALISTAMIENTO': ['inventario', 'inyeccion', 'ensamble', 'facturacion', 'almacen', 'pedidos', 'asistencia'],
        'AUXILIAR INVENTARIO': ['inventario', 'inyeccion', 'pulido', 'ensamble', 'pnc', 'historial', 'asistencia'],
        'JEFE INYECCION': ['dashboard', 'inyeccion', 'simulador', 'mezcla', 'asistencia'],
        'INYECCION': ['dashboard', 'inyeccion', 'mezcla', 'asistencia'],
        'JEFE PULIDO': ['dashboard', 'pulido', 'historial', 'asistencia'],
        'PULIDO': ['dashboard', 'pulido', 'historial', 'asistencia'],
        'ALISTAMIENTO': ['almacen', 'asistencia'],
        'ENSAMBLE': ['inyeccion', 'ensamble', 'asistencia'],
        'CLIENTE': ['portal-cliente'],
        'METALS_PROD': ['metals-dashboard', 'metals-produccion', 'metals-torno', 'metals-laser', 'metals-soldadura', 'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura', 'metals-zincado', 'metals-horno', 'metals-pulido-m'],
        'METALS_ADMIN': ['metals-dashboard', 'metals-produccion', 'metals-pedidos', 'metals-torno', 'metals-laser', 'metals-soldadura', 'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura', 'metals-zincado', 'metals-horno', 'metals-pulido-m', 'inventario', 'historial'],
        'INVITADO': []
    },

    // Notificación Visual
    mostrarNotificacion: function (msg, tipo = 'info') {
        const div = document.createElement('div');
        div.className = 'custom-toast';
        div.innerHTML = `<i class="fas ${tipo === 'success' ? 'fa-check-circle' : 'fa-exclamation-circle'} me-2"></i> ${msg}`;

        let bgColor = '#3b82f6'; // Info
        if (tipo === 'success') bgColor = '#10b981';
        if (tipo === 'error') bgColor = '#ef4444';

        div.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 16px 24px;
            background-color: ${bgColor};
            color: white;
            border-radius: 12px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            z-index: 30000;
            font-weight: 500;
            display: flex;
            align-items: center;
            animation: slideInRight 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            font-size: 0.95rem;
        `;

        // Add animation keyframes if not exists
        if (!document.getElementById('toast-style')) {
            const style = document.createElement('style');
            style.id = 'toast-style';
            style.textContent = `@keyframes slideInRight { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }`;
            document.head.appendChild(style);
        }

        document.body.appendChild(div);

        setTimeout(() => {
            div.style.transition = 'all 0.4s ease';
            div.style.transform = 'translateX(100%)';
            div.style.opacity = '0';
            setTimeout(() => div.remove(), 400);
        }, 3000);
    },

    init: async function () {
        console.log("🔐 Inicializando Módulo de Autenticación Avanzado...");

        // Juan Sebastian: Control estricto de visibilidad del sidebar en el portal
        const user = sessionStorage.getItem('user');
        const landingVisible = document.getElementById('landing-screen') && document.getElementById('landing-screen').style.display !== 'none';
        const sidebar = document.querySelector('.sidebar');
        
        if (!user || landingVisible) {
            document.body.classList.remove('logged-in');
            if (sidebar) sidebar.style.setProperty('display', 'none', 'important');
        } else {
            document.body.classList.add('logged-in');
            if (sidebar) sidebar.style.setProperty('display', 'flex', 'important');
        }

        // 1. Bloquear interfaz o mostrar Landing - INMEDIATO para evitar race conditions
        this.checkSession();

        // 2. Listeners Staff Logic
        const loginForm = document.getElementById('login-form');
        if (loginForm) {
            loginForm.addEventListener('submit', (e) => this.handleLogin(e));
        }

        const clientLoginForm = document.getElementById('client-login-form');
        if (clientLoginForm) {
            clientLoginForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.handleClientLogin();
            });
        }

        const clientRegisterForm = document.getElementById('client-register-form');
        if (clientRegisterForm) {
            clientRegisterForm.addEventListener('submit', (e) => this.handleClientRegister(e));
        }

        const passwordInput = document.getElementById('login-password');
        if (passwordInput) {
            passwordInput.addEventListener('input', function (e) {
                this.value = this.value.replace(/[^0-9]/g, '');
            });
        }
    },

    // =================================================================
    // GESTIÓN DE PANTALLAS (Landing / Modales)
    // =================================================================

    showLandingScreen: function () {
        const landing = document.getElementById('landing-screen');
        if (landing) {
            landing.style.display = 'flex';
            document.body.style.overflow = 'hidden';
            // Juan: Ocultar sidebar en el portal
            document.body.classList.remove('logged-in');
        }
        // Cerrar otros modales por si acaso
        this.closeLoginModal();
        this.closeClientAuth();
    },

    hideLandingScreen: function (isLogin = false, skipRedirect = true) {
        console.log(`🔓 Ocultando pantalla de entrada (isLogin=${isLogin}, skipRedirect=${skipRedirect})...`);
        const screen = document.getElementById('landing-screen');
        if (screen) {
            screen.style.display = 'none';
            document.body.style.overflow = 'auto';
        }

        // Juan: Activar visibilidad del sidebar tras login
        document.body.classList.add('logged-in');
        const sidebar = document.querySelector('.sidebar');
        if (sidebar) {
            sidebar.style.setProperty('display', 'flex', 'important');
        }

        // Ejecutar permisos y redirección
        this.applyPermissions(isLogin, skipRedirect);
    },
    openStaffLogin: function (type = 'FRIPARTS') {
        this.currentStaffType = type;
        const modal = document.getElementById('login-modal');
        if (modal) {
            // Personalizar título del modal según el tipo
            const title = modal.querySelector('h2');
            const sub = modal.querySelector('p');
            if (title) title.textContent = type === 'FRIMETALS' ? 'FriMetals' : 'FriTech';
            if (sub) sub.textContent = type === 'FRIMETALS' ? 'Módulo Metalmecánica' : 'Sistema de Producción';

            modal.style.display = 'flex';
            document.body.style.overflow = 'hidden';

            // Juan: Cargar los responsables correspondientes (Forzar recarga por división)
            this.loadResponsables(true);
        }
    },

    closeLoginModal: function () {
        const modal = document.getElementById('login-modal');
        if (modal) {
            modal.style.display = 'none';
        }
    },

    openClientAuth: function () {
        const modal = document.getElementById('client-auth-modal');
        if (modal) {
            modal.style.display = 'flex';
            document.body.style.overflow = 'hidden';
            // Reset forms
            document.getElementById('client-login-form').style.display = 'block';
            document.getElementById('client-register-form').style.display = 'none';
            document.getElementById('auth-title').textContent = 'Bienvenido Cliente';
        }
    },

    closeClientAuth: function () {
        const modal = document.getElementById('client-auth-modal');
        if (modal) modal.style.display = 'none';
    },

    toggleClientForms: function () {
        const loginForm = document.getElementById('client-login-form');
        const registerForm = document.getElementById('client-register-form');
        const authTitle = document.getElementById('auth-title');

        if (loginForm.style.display === 'none') {
            loginForm.style.display = 'block';
            registerForm.style.display = 'none';
            if (authTitle) authTitle.textContent = 'Bienvenido Cliente';
        } else {
            loginForm.style.display = 'none';
            registerForm.style.display = 'block';
            if (authTitle) authTitle.textContent = 'Registro de Cliente';
        }
    },

    handleClientRegister: async function (e) {
        e.preventDefault();
        
        const nombre_empresa = document.getElementById('register-nombre').value;
        const nit = document.getElementById('register-nit').value;
        const email = document.getElementById('register-email').value;
        const password = document.getElementById('register-password').value;
        const confirm_password = document.getElementById('register-confirm-password').value;

        if (password !== confirm_password) {
            this.mostrarNotificacion("Las contraseñas no coinciden", 'error');
            return;
        }

        const btn = document.querySelector('#client-register-form button');
        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Registrando...';

        try {
            const response = await fetch('/api/auth/client/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ nombre_empresa, nit, email, password })
            });

            const data = await response.json();

            if (data.success) {
                this.mostrarNotificacion("Registro exitoso. Tu cuenta está en revisión por un administrador", 'success');
                // Limpiar formulario
                document.getElementById('client-register-form').reset();
                // Volver al login
                this.toggleClientForms();
            } else {
                this.mostrarNotificacion(data.message || "Error al registrar", 'error');
            }
        } catch (error) {
            console.error(error);
            this.mostrarNotificacion("Error de conexión", 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    },

    // =================================================================
    // LÓGICA DE CLIENTE (Auth)
    // =================================================================

    // =================================================================
    // LÓGICA DE CLIENTE (Auth)
    // =================================================================

    handleClientLogin: async function () {
        const email = document.getElementById('client-email').value;
        const password = document.getElementById('client-password').value;
        const btn = document.querySelector('#client-login-form button');

        if (!email || !password) {
            Swal.fire({ icon: 'warning', title: 'Campos incompletos', text: 'Complete los campos.' });
            return;
        }

        btn.disabled = true;
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Conectando...';

        try {
            const response = await fetch('/api/auth/client/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });

            const data = await response.json();

            if (data.success) {
                if (data.pwa_token) {
                    localStorage.setItem('pwa_token', data.pwa_token);
                }
                
                if (data.requires_password_change) {
                    this.closeClientAuth();
                    this.showChangePasswordModal(email, password); // Pass current creds
                } else {
                    this.setCurrentUser(data.user);
                    this.closeClientAuth();
                    this.hideLandingScreen();
                }
            } else {
                this.mostrarNotificacion(data.message || "Error de inicio de sesión", 'error');
            }
        } catch (e) {
            console.error(e);
            this.mostrarNotificacion("Error de conexión", 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    },

    showChangePasswordModal: function (email, oldPassword) {
        let modal = document.getElementById('change-pass-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'change-pass-modal';
            modal.style.cssText = `
                position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(0,0,0,0.85); z-index: 20000;
                display: flex; align-items: center; justify-content: center;
                backdrop-filter: blur(5px);
            `;
            modal.innerHTML = `
                <div class="bg-white rounded-3 shadow-lg p-4" style="width: 100%; max-width: 400px; animation: slideIn 0.3s ease;">
                    <div class="text-center mb-4">
                        <i class="fas fa-key fa-3x text-warning mb-3"></i>
                        <h4 class="fw-bold">Cambio de Contraseña</h4>
                        <p class="text-muted small">Por seguridad, debes actualizar tu contraseña temporal.</p>
                    </div>
                    <form id="change-pass-form">
                        <div class="mb-3">
                            <label class="form-label fw-bold small">Nueva Contraseña</label>
                            <input type="password" id="new-pass-1" class="form-control" required minlength="6">
                        </div>
                        <div class="mb-3">
                            <label class="form-label fw-bold small">Confirmar Contraseña</label>
                            <input type="password" id="new-pass-2" class="form-control" required minlength="6">
                        </div>
                        <div class="d-grid gap-2">
                            <button type="submit" class="btn btn-primary fw-bold">Actualizar Contraseña</button>
                        </div>
                    </form>
                </div>
            `;
            document.body.appendChild(modal);
        }

        // Reset inputs
        if (document.getElementById('new-pass-1')) document.getElementById('new-pass-1').value = '';
        if (document.getElementById('new-pass-2')) document.getElementById('new-pass-2').value = '';

        modal.style.display = 'flex';

        const form = document.getElementById('change-pass-form');
        form.onsubmit = async (e) => {
            e.preventDefault();
            const p1 = document.getElementById('new-pass-1').value;
            const p2 = document.getElementById('new-pass-2').value;

            if (p1 !== p2) {
                Swal.fire({ icon: 'warning', title: 'Contraseñas distintas', text: 'Las contraseñas no coinciden.' });
                return;
            }
            if (p1.length < 6) {
                Swal.fire({ icon: 'warning', title: 'Contraseña muy corta', text: 'La contraseña debe tener al menos 6 caracteres.' });
                return;
            }

            // Call API
            try {
                const btn = form.querySelector('button');
                const orig = btn.innerHTML;
                btn.disabled = true;
                btn.innerHTML = 'Actualizando...';

                const res = await fetch('/api/auth/client/change-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: email,
                        old_password: oldPassword,
                        new_password: p1
                    })
                });

                const d = await res.json();
                if (d.success) {
                    modal.style.display = 'none';

                    // Auto-login with new password
                    this.setCurrentUser(d.user); // Backend should return user info
                    this.closeClientAuth();
                    this.hideLandingScreen();

                    // Show success message
                    this.mostrarNotificacion('Contraseña actualizada correctamente', 'success');

                    // Initialize portal
                    if (window.ModuloPortal && typeof window.ModuloPortal.init === 'function') {
                        await window.ModuloPortal.init();
                    }
                } else {
                    Swal.fire({ icon: 'error', title: 'Error', text: d.message || 'Error al actualizar la contraseña.' });
                }
                btn.disabled = false;
                btn.innerHTML = orig;

            } catch (err) {
                console.error("Error changing password:", err);
                Swal.fire({ icon: 'error', title: 'Error de conexión', text: 'No se pudo conectar con el servidor. Intenta de nuevo.' });
            }
        };
    },

    // handleClientRegister REMOVED - Public registration disabled

    // =================================================================
    // LÓGICA DE STAFF (Responsables)
    // =================================================================

    loadResponsables: async function (force = false) {
        const select = document.getElementById('login-usuario');
        if (!select) return;

        // Si ya tiene opciones y no forzamos, no sobrescribir 
        if (!force && select.options.length > 1 && !select.innerHTML.includes('Cargando')) {
            console.log("ℹ️ Usando usuarios en caché.");
            return;
        }

        // Limpiar y mostrar carga
        select.innerHTML = '<option value="">Cargando operarios...</option>';

        try {
            const type = this.currentStaffType;
            const endpoint = type === 'FRIMETALS'
                ? `/api/auth/metals/responsables?division=${type.toLowerCase()}`
                : `/api/auth/responsables?division=${type.toLowerCase()}`;

            const response = await fetch(endpoint);
            const result = await response.json();
            const users = result.data || result; 

            // Si el tipo cambió mientras cargábamos, ignorar esta respuesta
            if (this.currentStaffType !== type) return;

            if (result.status === 'error' || result.error) {
                select.innerHTML = `<option value="">Error: ${result.message || result.error}</option>`;
                return;
            }

            const usuariosActivos = Array.isArray(users) ? users.filter(user => user.nombre && user.nombre.trim() !== '') : [];

            select.innerHTML = '<option value="">Seleccione su nombre...</option>';
            usuariosActivos.forEach(user => {
                const option = document.createElement('option');
                option.value = user.nombre;
                option.dataset.dept = user.departamento;
                option.textContent = user.nombre;
                select.appendChild(option);
            });

        } catch (e) {
            console.error("Error fetching responsables:", e);
            select.innerHTML = '<option value="">Error de conexión</option>';
        }
    },

    handleLogin: async function (e) {
        e.preventDefault();

        const usuarioSelect = document.getElementById('login-usuario');
        const passwordInput = document.getElementById('login-password');
        const submitBtn = document.getElementById('btn-login-submit');
        const errorMsg = document.getElementById('login-error-msg');

        const nombre = usuarioSelect.value;
        const password = passwordInput.value;

        if (!nombre || !password) {
            this.showError("Por favor complete todos los campos.");
            return;
        }

        submitBtn.disabled = true;
        submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Verificando...';
        errorMsg.style.display = 'none';

        try {
            const endpoint = this.currentStaffType === 'FRIMETALS'
                ? '/api/auth/metals/login'
                : '/api/auth/login';

            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ responsable: nombre, password: password })
            });

            const data = await response.json();

            if (data.success) {
                // Guardar token PWA si viene en la respuesta (Fallback Offline/Standalone)
                if (data.pwa_token) {
                    localStorage.setItem('pwa_token', data.pwa_token);
                }

                // Agregar división actual al objeto de usuario para persistencia
                data.user.division = this.currentStaffType;

                this.setCurrentUser(data.user);
                this.closeLoginModal();
                this.hideLandingScreen(true, false); // isLogin=true, skipRedirect=false -> Forzar redirección
                passwordInput.value = '';
            } else {
                this.showError(data.message);
            }

        } catch (error) {
            console.error("Login error:", error);
            this.showError("Error de conexión con el servidor.");
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Ingresar al Sistema';
        }
    },

    showError: function (msg) {
        const el = document.getElementById('login-error-msg');
        if (el) {
            el.textContent = msg;
            el.style.display = 'block';
        } else {
            Swal.fire({ icon: 'error', title: 'Error', text: msg });
        }
    },

    // =================================================================
    // GESTIÓN DE SESIÓN
    // =================================================================

    setCurrentUser: function (user) {
        this.currentUser = user;
        sessionStorage.setItem('friparts_user', JSON.stringify(user));

        if (!window.AppState) window.AppState = {};
        window.AppState.user = {
            name: user.nombre,
            nombre: user.nombre,
            rol: user.rol,
            tipo: user.tipo || 'STAFF',
            division: user.division || 'FRIPARTS', // Persistir división
            nit: user.nit || null,
            email: user.email || null,
            direccion: user.direccion || null,
            ciudad: user.ciudad || null,
            fullData: { ...user, division: user.division || 'FRIPARTS' } // Asegurar división en fullData
        };

        console.log(`👤 Usuario Logueado: ${user.nombre} (${user.rol})`);

        // DISPATCH EVENT: User Ready (Solo uno para evitar duplicidad)
        document.dispatchEvent(new CustomEvent('user-ready', { detail: user }));

        // REDIRECCIÓN INMEDIATA para clientes
        if (user.rol === 'Cliente') {
            console.log('🔄 Cliente detectado - Redirigiendo a portal-cliente...');
            this.navigateTo('portal-cliente');
            return;
        }

        this.updateProfileUI();
        this.applyPermissions(true); // isLogin=true: permite redirección al landing solo en primer ingreso

        if (user.rol !== 'Cliente') {
            this.showWelcomeMessage(user);
            this.autoFillForms();
        }
    },

    showWelcomeMessage: function (user) {
        const roleUpper = this.normalizeRole(user.rol);
        const mensajes = {
            'INYECCION': `¡Hola ${user.nombre}! Listo para la producción.`,
            'PULIDO': `¡Hola ${user.nombre}! Lista para pulir.`,
            'ADMINISTRACION': `Bienvenido al Centro de Control.`,
            'ADMINISTRADOR': `Bienvenido al Centro de Control.`,
            'COMERCIAL': `Bienvenido al Módulo de Pedidos.`,
            'STAFF FRIMETALS': `¡Hola ${user.nombre}! Bienvenido a FriMetals.`,
            'COMERCIAL FRIMETALS': `Bienvenido al Módulo Comercial FriMetals.`,
            'CLIENTE': `Bienvenido a su Portal FriParts.`
        };
        const mensaje = mensajes[roleUpper] || `¡Bienvenido ${user.nombre}!`;
        this.mostrarNotificacion(mensaje, 'success');
    },

    checkSession: function () {
        const stored = sessionStorage.getItem('friparts_user');
        if (stored) {
            try {
                const user = JSON.parse(stored);
                this.currentUser = user;
                if (!window.AppState) window.AppState = {};
                window.AppState.user = {
                    name: user.nombre,
                    nombre: user.nombre,
                    rol: user.rol,
                    tipo: user.tipo || 'STAFF',
                    division: user.division || 'FRIPARTS', // RECUPERAR DIVISION
                    nit: user.nit,
                    direccion: user.direccion || null,
                    ciudad: user.ciudad || null,
                    fullData: user
                };

                this.hideLandingScreen(false, true); // Ocultar landing (Restauración, no redirigir)
                this.closeLoginModal();
                this.updateProfileUI();

                // CRÍTICO: No redirigir inmediatamente en el boot inicial
                // El control de la primera página lo tendrá app.js
                this.applyPermissions(false, true); // isLogin=false, skipRedirect=true

                if (user.tipo === 'STAFF') {
                    this.autoFillForms();
                }

                // Notificar a módulos que esperan login (e.g. Almacén)
                window.dispatchEvent(new Event('user-ready'));
                document.dispatchEvent(new Event('user-ready'));

            } catch (e) {
                console.error("Error parsing session:", e);
                this.logout();
            }
        } else {
            // MOSTRAR LANDING SCREEN
            this.showLandingScreen();
        }
    },

    logout: function () {
        console.log("🔐 Cerrando sesión y limpiando rastro de producción...");
        this.currentUser = null;
        sessionStorage.removeItem('friparts_user');
        
        // Limpiar clase de visibilidad
        document.body.classList.remove('logged-in');
        
        // LIMPIEZA TOTAL DE ESTADOS DE PRODUCCIÓN (Juan Sebastian request)
        // 1. Limpiar llaves estáticas conocidas
        const legacyKeys = ['pulido_state', 'inyeccion_state', 'ensamble_state', 'mezcla_state', 'mes_maquina_ref', 'ensamble_responsable', 'pwa_token'];
        legacyKeys.forEach(k => localStorage.removeItem(k));

        // 2. Limpiar llaves namespaced (ej: pulido_state::JUAN, pulido_last_responsable::JUAN)
        const productionPrefixes = ['pulido_', 'inyeccion_', 'ensamble_', 'mezcla_', 'mes_'];
        const keys = Object.keys(localStorage);
        keys.forEach(key => {
            if (productionPrefixes.some(pref => key.startsWith(pref))) {
                localStorage.removeItem(key);
            }
        });
        
        // Mostrar Landing
        this.showLandingScreen();
        // Recargar para limpiar estados volátiles en memoria de módulos
        setTimeout(() => window.location.reload(), 200);
    },

    updateProfileUI: function () {
        if (!this.currentUser) return;
        const userNameEl = document.querySelector('.user-name');
        const userRoleEl = document.querySelector('.user-role');
        if (userNameEl) userNameEl.textContent = this.currentUser.nombre;
        if (userRoleEl) userRoleEl.textContent = this.currentUser.rol;

        // Juan Sebastian: Personalizar Branding del Sidebar según división
        const division = this.currentUser.division || 'FRIPARTS';
        const brandNameEl = document.getElementById('sidebar-company-name');
        const brandSubEl = document.getElementById('sidebar-company-subtitle');

        if (brandNameEl && brandSubEl) {
            if (division === 'FRIMETALS') {
                brandNameEl.textContent = 'FriMetals';
                brandSubEl.textContent = 'Metalmecánica';
            } else {
                brandNameEl.textContent = 'FriTech';
                brandSubEl.textContent = 'Sistema de Producción';
            }
        }
    },

    applySidebarVisibility: function () {
        if (!this.currentUser) return;
        const role = this.normalizeRole(this.currentUser.rol || this.currentUser.role);
        const division = this.currentUser.division || window.AppState.user?.division || 'FRIPARTS';
        
        let allowedPages = [];
        if (this.currentUser.tipo === 'METALS_STAFF') {
            const metalsPages = [
                'metals-dashboard', 'metals-produccion', 'metals-torno', 'metals-laser', 'metals-soldadura',
                'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura',
                'metals-zincado', 'metals-horno', 'metals-pulido-m',
                'asistencia', 'pedidos'
            ];
            if (this.permissions[role]) {
                allowedPages = [...this.permissions[role]];
            } else {
                allowedPages = [...metalsPages];
            }
            if (role.toUpperCase().includes('ADMIN') || role.toUpperCase().includes('GERENCIA')) {
                if (!allowedPages.includes('inventario')) allowedPages.push('inventario');
                if (!allowedPages.includes('historial')) allowedPages.push('historial');
            }
        } else {
            allowedPages = this.getPagesForRole(role);
        }

        // Filtro estricto de división
        if (division === 'FRIPARTS') {
            allowedPages = allowedPages.filter(p => !p.startsWith('metals-'));
        } else if (division === 'FRIMETALS') {
            let forbiddenInMetals = [
                'dashboard', 'inyeccion', 'pulido', 'ensamble', 'pnc',
                'facturacion', 'mezcla', 'reportes', 'auditoria-op',
                'admin-clientes', 'portal-cliente'
            ];
            if (role.includes('ADMIN') || role.includes('GERENCIA')) {
                forbiddenInMetals = forbiddenInMetals.filter(p => !['admin-clientes'].includes(p));
            }
            allowedPages = allowedPages.filter(p => !forbiddenInMetals.includes(p));
            if (!allowedPages.includes('metals-dashboard')) {
                allowedPages.push('metals-dashboard');
            }
        }

        const sidebarEntries = document.querySelectorAll('.sidebar-menu .menu-item, .sidebar-menu .sidebar-divider');
        
        if (sidebarEntries.length === 0) {
            console.warn("⚠️ Elementos del sidebar no detectados en el DOM aún. Configurando MutationObserver y retry...");
            // Reintento rápido por si acaso
            setTimeout(() => this.applySidebarVisibility(), 100);
            
            // MutationObserver para re-evaluar si se dibuja dinámicamente más tarde
            if (!this._sidebarObserver) {
                this._sidebarObserver = new MutationObserver((mutations, observer) => {
                    if (document.querySelector('.sidebar-menu .menu-item')) {
                        this.applySidebarVisibility();
                        observer.disconnect();
                        this._sidebarObserver = null;
                    }
                });
                this._sidebarObserver.observe(document.body, { childList: true, subtree: true });
            }
            return;
        }

        sidebarEntries.forEach(item => {
            if (item.classList.contains('sidebar-divider')) {
                const isMetalsDivider = item.textContent.toUpperCase().includes('METALS');
                if (division === 'FRIPARTS' && isMetalsDivider) {
                    item.style.setProperty('display', 'none', 'important');
                } else if (division === 'FRIMETALS' && !isMetalsDivider) {
                    item.style.setProperty('display', 'none', 'important');
                } else {
                    item.style.display = 'block';
                }
                return;
            }

            const pageName = item.getAttribute('data-page');
            if (allowedPages.includes(pageName)) {
                item.style.display = 'block';
            } else {
                item.style.setProperty('display', 'none', 'important');
            }
        });

        // Garantía absoluta de visualización para el rol ADMIN en notificaciones push
        if (role === 'ADMIN') {
            const navNotif = document.getElementById('nav-notificaciones') || document.querySelector('[data-page="notificaciones"]');
            if (navNotif) {
                console.log("🚀 Módulo de Notificaciones Push forzado como inmutable (Visible) para ADMIN.");
                navNotif.style.setProperty('display', 'block', 'important');
            }
        }
    },

    applyPermissions: function (isLogin = false, skipRedirect = false) {
        if (!this.currentUser) return;

        const role = this.normalizeRole(this.currentUser.rol || this.currentUser.role);
        let allowedPages = [];

        // Determinar permisos base
        if (this.currentUser.tipo === 'METALS_STAFF') {
            // Si es de metales, damos acceso a todo lo de metales por defecto
            const metalsPages = [
                'metals-dashboard', 'metals-produccion', 'metals-torno', 'metals-laser', 'metals-soldadura',
                'metals-marcadora', 'metals-taladro', 'metals-dobladora', 'metals-pintura',
                'metals-zincado', 'metals-horno', 'metals-pulido-m',
                'asistencia', 'pedidos'
            ];

            // Si el rol existe en la matriz, usar sus permisos
            if (this.permissions[role]) {
                allowedPages = [...this.permissions[role]];
            } else {
                allowedPages = [...metalsPages];
            }

            // Asegurar inventario e historial para Admin/Gerencia de Metales
            if (role.toUpperCase().includes('ADMIN') || role.toUpperCase().includes('GERENCIA')) {
                if (!allowedPages.includes('inventario')) allowedPages.push('inventario');
                if (!allowedPages.includes('historial')) allowedPages.push('historial');
            }
        } else {
            allowedPages = this.getPagesForRole(role, this.currentUser.nombre || '');
        }

        // =============================================================
        // FILTRADO ESTRICTO POR DIVISIÓN (Juan Sebastian request)
        // =============================================================
        const division = this.currentUser.division || window.AppState.user?.division || 'FRIPARTS';
        console.log(`🛡️ Aplicando permisos. Rol normalizado: "${role}", División: ${division}`);

        if (division === 'FRIPARTS') {
            // Eliminar CUALQUIER módulo de Metales si entramos por FriParts
            allowedPages = allowedPages.filter(p => !p.startsWith('metals-'));
        } else if (division === 'FRIMETALS') {
            // Eliminar módulos exclusivos de FriParts si entramos por Metales
            // Incluimos TODO lo que no sea compartido o exclusivo de Metales
            // pedidos y almacen son COMPARTIDOS (los usan STAFF/COMERCIAL FRIMETALS)
            let forbiddenInMetals = [
                'dashboard', 'inyeccion', 'pulido', 'ensamble', 'pnc',
                'facturacion', 'mezcla', 'reportes', 'auditoria-op',
                'admin-clientes', 'portal-cliente'
            ];

            // EXCEPCIÓN: Admins y Gerencia siempre pueden ver Clientes
            if (role.includes('ADMIN') || role.includes('GERENCIA')) {
                forbiddenInMetals = forbiddenInMetals.filter(p => !['admin-clientes'].includes(p));
            }

            console.log("🚫 Filtrando módulos de FriParts en sesión de Metales...");
            allowedPages = allowedPages.filter(p => !forbiddenInMetals.includes(p));

            // Forzar que el dashboard sea el de metales
            if (!allowedPages.includes('metals-dashboard')) {
                allowedPages.push('metals-dashboard');
            }
        }

        console.log(`✅ Páginas autorizadas para ${role}:`, allowedPages);

        // LOGICA EXCEPCIONES STAFF FRIPARTS (Solo si no es METALS_STAFF)
        let firstAllowed = allowedPages[0] || null;

        // 1. Mostrar/Ocultar Menú Sidebar (Items y Divisores) con lógica tolerante a race conditions
        this.applySidebarVisibility();

        // 1.5 Colapso Forzado del Sidebar para Metales (Solo en login fresco para no molestar)
        if (division === 'FRIMETALS' && isLogin) {
            const sidebar = document.querySelector('.sidebar');
            const main = document.querySelector('.main-content');
            if (sidebar && main && !sidebar.classList.contains('collapsed')) {
                console.log("🛡️ Sidebar colapsado automáticamente para FRIMETALS (Login)");
                sidebar.classList.add('collapsed');
                main.classList.add('expanded');
            }
        }

        // 2. Redirección Forzada
        const activePage = document.querySelector('.page.active');
        
        // --- Lógica de Destino (Source of Truth) ---
        // Juan Sebastian: Prioridad ABSOLUTA a la División seleccionada
        let targetPage = 'inyeccion'; // Default general
        
        if (role === 'CLIENTE') {
            targetPage = 'portal-cliente';
        } else if (division === 'FRIMETALS') {
            // Si es METALS, SIEMPRE va a producción (Formularios) sin importar si es Admin
            targetPage = 'metals-produccion';
        } else if (role === 'ADMIN') {
            targetPage = 'dashboard';
        } else if (role === 'PULIDO') {
            targetPage = 'pulido';
        } else if (role.includes('COMERCIAL')) {
            targetPage = 'pedidos';
        } else if (role === 'ALISTAMIENTO' || role === 'JEFE ALISTAMIENTO') {
            targetPage = 'almacen';
        } else {
            targetPage = firstAllowed || allowedPages[0] || 'inyeccion';
        }

        console.log(`🎯 [Auth] Target Page Final: ${targetPage} (Rol: ${role}, Div: ${division})`);

        if (activePage) {
            const pageId = activePage.id.replace('-page', '');

            // Si la página actual no es permitida, redirigir
            if (!this.isPageAllowed(pageId)) {
                console.warn(`🛑 ACCESO DENEGADO a ${pageId}. Redirigiendo a ${targetPage}...`);
                if (!skipRedirect) this.navigateTo(targetPage);
            }
        } else if (!skipRedirect) {
            // En login fresco, SIEMPRE redirigir al landing del rol
            if (isLogin) {
                console.log(`🎯 Login fresco. Redirigiendo a landing: ${targetPage}`);
                this.navigateTo(targetPage);
            } else {
                // En restauración de sesión, respetar hash si existe
                const hashPage = window.location.hash.replace('#', '');
                if (hashPage && document.getElementById(`${hashPage}-page`) && allowedPages.includes(hashPage)) {
                    console.log(`🔗 Hash detectado: #${hashPage}. Cargando vía app.js.`);
                    if (typeof window.cargarPagina === 'function') window.cargarPagina(hashPage);
                } else {
                    console.log(`🎯 No hay página activa válida. Redirigiendo a: ${targetPage}`);
                    this.navigateTo(targetPage);
                }
            }
        }

        this.authorizedPages = allowedPages;
        return allowedPages; // Retornar para uso externo
    },

    navigateTo: function (pageName) {
        // Lógica de navegación. 
        // Si es Portal Cliente (que no está en sidebar), hacerlo manualmente.

        if (pageName === 'portal-cliente') {
            // Ocultar todas las paginas
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.menu-item').forEach(m => m.classList.remove('active'));

            // Mostrar Portal
            const portalPage = document.getElementById('portal-cliente-page');
            if (portalPage) portalPage.classList.add('active');

            // Sidebar: Desactivar todo o ocultar sidebar entero?
            // Mejor ocultar sidebar para Clientes para dar look de App
            if (this.currentUser.rol === 'Cliente') {
                const sidebar = document.querySelector('.sidebar');
                if (sidebar) sidebar.style.display = 'none';
                const main = document.querySelector('.main-content');
                if (main) main.style.marginLeft = '0'; // Full width
            }

            // Cargar datos iniciales del portal si existe Modulo
            if (window.ModuloPortal && typeof window.ModuloPortal.init === 'function') {
                window.ModuloPortal.init();
            }

        } else {
            // Navegación standard via clic en sidebar
            const link = document.querySelector(`.menu-item[data-page="${pageName}"] a`);
            if (link) {
                link.click();
            } else {
                console.error("No navigation link found for", pageName);
            }
        }
    },

    autoFillForms: function () {
        if (!this.currentUser) return;

        // Fuente de verdad para módulos que leen el nombre completo del
        // operario desde este hidden input (Ensamble, Pintura, Rayada,
        // Hornos, Mes Control) en vez de depender de AppState directamente.
        // Antes quedaba SIEMPRE vacío: el template lo renderizaba desde
        // session['user_name'], una clave que ningún login del backend
        // llega a escribir (todos usan session['user'], que además guarda
        // el username, no el nombre completo).
        const hiddenFullname = document.getElementById('current_user_fullname');
        if (hiddenFullname) hiddenFullname.value = this.currentUser.nombre || '';

        // Bloquear selects para que nadie registre a nombre de otros
        const selects = document.querySelectorAll('select[id^="responsable-"]');
        selects.forEach(select => {
            // Asegurar opcion
            let exists = false;
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].value === this.currentUser.nombre) {
                    exists = true;
                    select.selectedIndex = i;
                    break;
                }
            }
            if (!exists) {
                const opt = document.createElement('option');
                opt.value = this.currentUser.nombre;
                opt.textContent = this.currentUser.nombre;
                select.appendChild(opt);
                select.value = this.currentUser.nombre;
            }
            // Bloquear (opcional, usuario pidio "persistencia", no explícitamente "bloqueo", pero es mas seguro)
            // select.disabled = true; 
        });

        const inputs = document.querySelectorAll('input[id^="responsable-"]');
        inputs.forEach(input => {
            if (input.type === 'text' || input.type === 'search') {
                input.value = this.currentUser.nombre;
            }
        });
    }
};

window.AuthModule = AuthModule;

document.addEventListener('DOMContentLoaded', () => {
    AuthModule.init();
    // Ejecutar re-evaluación diferida por si acaso
    setTimeout(() => {
        if (AuthModule.currentUser) {
            AuthModule.applySidebarVisibility();
        }
    }, 150);
});

// Listener global para el evento de usuario listo
document.addEventListener('user-ready', () => {
    console.log("👤 Re-evaluando sidebar en evento user-ready (document)");
    AuthModule.applySidebarVisibility();
});

window.addEventListener('user-ready', () => {
    console.log("👤 Re-evaluando sidebar en evento user-ready (window)");
    AuthModule.applySidebarVisibility();
});
