const apiClient = {
    baseURL: '/api',
    
    async get(endpoint) {
        try {
            const url = endpoint.startsWith(this.baseURL) ? endpoint : `${this.baseURL}${endpoint}`;
            const headers = {};
            const token = localStorage.getItem('pwa_token');
            if (token) headers['Authorization'] = `Bearer ${token}`;

            const response = await fetch(url, { headers });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } catch (error) {
            console.error(`[API GET] ${endpoint}:`, error);
            throw error;
        }
    },
    
    async post(endpoint, data) {
        try {
            const url = endpoint.startsWith(this.baseURL) ? endpoint : `${this.baseURL}${endpoint}`;
            const headers = { 'Content-Type': 'application/json' };
            const token = localStorage.getItem('pwa_token');
            if (token) headers['Authorization'] = `Bearer ${token}`;

            const response = await fetch(url, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(data)
            });
            if (!response.ok) {
                // El body de error (code, mensaje, campos extra de api_error) se
                // adjunta al Error -- sin esto, un caller no puede distinguir un
                // bloqueo de negocio (ej. PULIDO_FECHA_BLOQUEADA) de un 500 genérico,
                // ni mostrar el mensaje real que mandó el backend.
                const err = new Error(`HTTP ${response.status}`);
                err.status = response.status;
                err.body = await response.json().catch(() => null);
                throw err;
            }
            return await response.json();
        } catch (error) {
            console.error(`[API POST] ${endpoint}:`, error);
            throw error;
        }
    }
};
window.apiClient = apiClient;