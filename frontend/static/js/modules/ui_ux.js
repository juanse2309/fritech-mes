/**
 * Módulo de UX/UI - Personalización y Gamificación
 * v1.0.0
 */

const ModuloUX = (() => {

    // Configuración de Sonidos (Web Audio API para no depender de archivos)
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();

    const playTone = (freq, type, duration) => {
        try {
            const osc = audioContext.createOscillator();
            const gainNode = audioContext.createGain();

            osc.type = type;
            osc.frequency.setValueAtTime(freq, audioContext.currentTime);

            gainNode.gain.setValueAtTime(0.1, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + duration);

            osc.connect(gainNode);
            gainNode.connect(audioContext.destination);

            osc.start();
            osc.stop(audioContext.currentTime + duration);
        } catch (e) {
            console.warn("Audio blocked or failed", e);
        }
    };

    /**
     * Reproducir sonido según el tema configurado
     * @param {string} type 'success' | 'error' | 'new_order'
     */
    const playSound = (type) => {
        if (type === 'success') {
            playTone(523.25, 'sine', 0.1); // C5
            setTimeout(() => playTone(659.25, 'sine', 0.1), 100); // E5
        } else if (type === 'error') {
            playTone(150, 'sawtooth', 0.4);
            playTone(110, 'square', 0.4);
        } else if (type === 'new_order') {
            const theme = localStorage.getItem('friparts_sound_theme') || 'marimba';
            switch (theme) {
                case 'siren':
                    // Sirena Industrial (Llamativa y potente)
                    [800, 600, 800, 600].forEach((f, i) => {
                        setTimeout(() => playTone(f, 'sawtooth', 0.2), i * 200);
                    });
                    break;
                case 'mega_siren':
                    // Mega Sirena (Largo - 8 ciclos)
                    for (let i = 0; i < 8; i++) {
                        setTimeout(() => playTone(800, 'sawtooth', 0.2), i * 400);
                        setTimeout(() => playTone(600, 'sawtooth', 0.2), i * 400 + 200);
                    }
                    break;
                case 'urgent':
                    // Alarma Urgente (Rápida y penetrante)
                    for (let i = 0; i < 12; i++) {
                        setTimeout(() => playTone(1200, 'square', 0.05), i * 100);
                    }
                    break;
                case 'sonar':
                    // Eco de Sonar (Pulsos profundos)
                    [400, 400, 400].forEach((f, i) => {
                        setTimeout(() => {
                            playTone(f, 'sine', 0.8);
                            playTone(f * 1.5, 'sine', 0.4);
                        }, i * 1000);
                    });
                    break;
                case 'melody':
                    // Secuencia Melódica (Elegante y larga)
                    [523, 659, 783, 1046, 783, 659, 523].forEach((f, i) => {
                        setTimeout(() => playTone(f, 'sine', 0.3), i * 200);
                    });
                    break;
                case 'techno':
                    // Techno Beat (Rítmico y persistente)
                    for (let i = 0; i < 6; i++) {
                        setTimeout(() => playTone(150, 'square', 0.1), i * 400); // Kick
                        setTimeout(() => playTone(1000, 'sine', 0.05), i * 400 + 200); // Snare
                    }
                    break;
                case 'emergency':
                    // Emergencia (Patrulla Digital)
                    for (let i = 0; i < 10; i++) {
                        setTimeout(() => playTone(900, 'sawtooth', 0.15), i * 300);
                        setTimeout(() => playTone(600, 'sawtooth', 0.15), i * 300 + 150);
                    }
                    break;
                case 'space':
                    // Space Odyssey (Barridos espaciales)
                    [1, 2, 3].forEach((cycle, i) => {
                        setTimeout(() => {
                            const now = audioContext.currentTime;
                            const osc = audioContext.createOscillator();
                            const gain = audioContext.createGain();
                            osc.type = 'sine';
                            osc.frequency.setValueAtTime(200, now);
                            osc.frequency.exponentialRampToValueAtTime(1500, now + 0.8);
                            gain.gain.setValueAtTime(0.1, now);
                            gain.gain.exponentialRampToValueAtTime(0.01, now + 0.8);
                            osc.connect(gain);
                            gain.connect(audioContext.destination);
                            osc.start();
                            osc.stop(now + 0.8);
                        }, i * 1000);
                    });
                    break;
                case 'marimba':
                    // Marimba Rhythm (Alegre)
                    [440, 523, 659, 440, 523, 659, 880].forEach((f, i) => {
                        setTimeout(() => playTone(f, 'sine', 0.2), i * 150);
                    });
                    break;
                case 'pulse':
                    // Pulso Moderno (Corto y penetrante)
                    playTone(1000, 'square', 0.05);
                    setTimeout(() => playTone(1000, 'square', 0.05), 100);
                    setTimeout(() => playTone(1200, 'square', 0.1), 200);
                    break;
                case 'arpeggio':
                    // Arpeggio Melódico
                    [440, 554, 659, 880].forEach((f, i) => {
                        setTimeout(() => playTone(f, 'sine', 0.15), i * 100);
                    });
                    break;
                case 'uber':
                    // Uber Style (Dos tonos rápidos y urgentes)
                    playTone(659.25, 'sine', 0.1); // E5
                    setTimeout(() => playTone(783.99, 'sine', 0.2), 150); // G5
                    break;
                case 'mario':
                    // Level Up (Estilo Mario)
                    [523.25, 659.25, 783.99, 1046.50].forEach((f, i) => {
                        setTimeout(() => playTone(f, 'sine', 0.15), i * 100);
                    });
                    break;
                case 'magic':
                    // Varita Mágica (Brillo descendente)
                    for (let i = 0; i < 15; i++) {
                        setTimeout(() => playTone(2000 - (i * 100), 'sine', 0.05), i * 30);
                    }
                    break;
                case 'tada':
                    // Tada! (Fanfarria triunfal)
                    playTone(392.00, 'sawtooth', 0.1); // G4
                    setTimeout(() => playTone(523.25, 'sawtooth', 0.4), 100); // C5
                    break;
                case 'zen':
                    // Campana Zen (Relajante y profunda)
                    playTone(196.00, 'sine', 1.5); // G3
                    playTone(392.00, 'sine', 1.0); // G4
                    break;
                case 'bird':
                    // Pájaro Digital (Chirp)
                    const now = audioContext.currentTime;
                    const osc = audioContext.createOscillator();
                    const gain = audioContext.createGain();
                    osc.type = 'sine';
                    osc.frequency.setValueAtTime(1500, now);
                    osc.frequency.exponentialRampToValueAtTime(3000, now + 0.1);
                    gain.gain.setValueAtTime(0.1, now);
                    gain.gain.exponentialRampToValueAtTime(0.01, now + 0.1);
                    osc.connect(gain);
                    gain.connect(audioContext.destination);
                    osc.start();
                    osc.stop(now + 0.1);
                    break;
                case 'classic':
                default:
                    // Campana Clásica (Mejorada)
                    playTone(600, 'sine', 0.1);
                    setTimeout(() => playTone(800, 'sine', 0.4), 150);
                    break;
            }
        }
    };

    const getSoundTheme = () => localStorage.getItem('friparts_sound_theme') || 'marimba';
    const setSoundTheme = (theme) => localStorage.setItem('friparts_sound_theme', theme);

    /**
     * Generar Avatar e Iniciales
     */
    const actualizarPerfil = () => {
        try {
            const user = window.AppState?.user;
            if (!user) return;

            const nombre = user.nombre || user.name || 'Usuario';

            // 1. Saludo según hora
            const hora = new Date().getHours();
            let saludo = 'Hola';
            let icono = '👋';

            if (hora < 12) { saludo = 'Buenos días'; icono = '☀️'; }
            else if (hora < 19) { saludo = 'Buenas tardes'; icono = '🌤️'; }
            else { saludo = 'Buenas noches'; icono = '🌙'; }

            // 2. Renderizar Saludo
            const greetingEl = document.getElementById('user-greeting');
            const nameDisplayEl = document.getElementById('user-name-display');

            if (greetingEl) greetingEl.innerHTML = `${saludo}, ${icono}`;
            if (nameDisplayEl) nameDisplayEl.textContent = nombre.split(' ')[0]; // Primer nombre

            // 3. Avatar con iniciales
            const avatarEl = document.getElementById('user-avatar');
            if (avatarEl) {
                const iniciales = nombre.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
                avatarEl.textContent = iniciales;

                // Color de fondo consistente basado en el nombre (Hash simple)
                const hash = nombre.split('').reduce((acc, char) => char.charCodeAt(0) + ((acc << 5) - acc), 0);
                const hue = Math.abs(hash) % 360;
                avatarEl.style.background = `linear-gradient(135deg, hsl(${hue}, 70%, 50%), hsl(${hue + 40}, 70%, 40%))`;
            }

        } catch (e) {
            console.error("Error actualizando perfil UX", e);
        }
    };

    return {
        init: () => {
            console.log("🎨 Modulo UX Inicializado");

            // 1. Monkey Patching para Swal (Interceptor de Sonido Global)
            if (typeof Swal !== 'undefined') {
                const originalSwalFire = Swal.fire;
                Swal.fire = function (...args) {
                    const config = args[0];

                    // Determinar si es éxito o error analizando los argumentos
                    const isSuccess = (typeof config === 'object' && config.icon === 'success') || (typeof args[1] === 'string' && args[1] === 'success');
                    const isError = (typeof config === 'object' && (config.icon === 'error' || config.icon === 'warning')) || (typeof args[1] === 'string' && (args[1] === 'error' || args[1] === 'warning'));

                    if (isSuccess && typeof ModuloUX !== 'undefined') {
                        ModuloUX.reproducirExito();
                    } else if (isError && typeof ModuloUX !== 'undefined') {
                        ModuloUX.reproducirError();
                    }

                    return originalSwalFire.apply(this, args);
                };
            }

            // 2. Esperar a que el usuario cargue en AppState
            const checkUser = setInterval(() => {
                if (window.AppState && window.AppState.user) {
                    actualizarPerfil();
                    clearInterval(checkUser);
                }
            }, 500);
        },
        getSoundTheme,
        setSoundTheme,
        playSound,
        reproducirExito: () => playSound('success'),
        reproducirError: () => playSound('error'),
        reproducirNotificacionPedido: () => playSound('new_order'),

        /**
         * Configura el comportamiento de la tecla Enter en un formulario
         * @param {Object} config - { inputIds: [], actionBtnId: '', autocomplete: { inputId: '', suggestionsId: '' } }
         */
        setupSmartEnter: (config) => {
            const { inputIds, actionBtnId, autocomplete } = config;

            inputIds.forEach((id, idx) => {
                const input = document.getElementById(id);
                if (!input) return;

                // setupSmartEnter() se llama desde inicializar() de varios
                // módulos (pnc, inventario, mes_control...), y esas funciones
                // vuelven a correr cada vez que se navega a la página, no
                // solo la primera vez. Sin este guard, cada visita apilaba
                // otro par de listeners keydown/input sobre el mismo campo
                // -- y el de Enter hace `btn.click()` en el botón de acción,
                // así que un solo Enter real terminaba disparando N clicks
                // programáticos (doble/triple confirmación al guardar).
                if (input.dataset.smartEnterBound) return;
                input.dataset.smartEnterBound = 'true';

                let selectedIndex = -1;

                // Resetear selección cuando el usuario escribe
                input.addEventListener('input', () => {
                    selectedIndex = -1;
                });

                input.addEventListener('keydown', (e) => {
                    const isAutocomplete = autocomplete && id === autocomplete.inputId;
                    const suggestionsDiv = isAutocomplete ? document.getElementById(autocomplete.suggestionsId) : null;
                    const isVisible = suggestionsDiv && (suggestionsDiv.classList.contains('active') || suggestionsDiv.style.display === 'block');

                    // 1. Manejo de Flechas (Navegación)
                    if (isVisible && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                        e.preventDefault();
                        const items = suggestionsDiv.querySelectorAll('.suggestion-item');
                        if (items.length === 0) return;

                        // Quitar clase previa
                        if (selectedIndex >= 0 && selectedIndex < items.length) {
                            items[selectedIndex].classList.remove('selected');
                        }

                        if (e.key === 'ArrowDown') {
                            selectedIndex = (selectedIndex + 1) % items.length;
                        } else {
                            selectedIndex = (selectedIndex - 1 + items.length) % items.length;
                        }

                        // Añadir nueva clase y scroll
                        const activeItem = items[selectedIndex];
                        activeItem.classList.add('selected');
                        activeItem.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                        return;
                    }

                    // 2. Manejo de Enter
                    if (e.key === 'Enter') {
                        e.preventDefault();

                        if (isVisible) {
                            const items = suggestionsDiv.querySelectorAll('.suggestion-item');
                            const itemToClick = selectedIndex >= 0 ? items[selectedIndex] : items[0];

                            if (itemToClick) {
                                itemToClick.click();
                                selectedIndex = -1;
                                return;
                            }
                        }

                        // Saltar al siguiente campo o ejecutar acción
                        const nextId = inputIds[idx + 1];
                        if (nextId) {
                            document.getElementById(nextId)?.focus();
                        } else if (actionBtnId) {
                            const btn = document.getElementById(actionBtnId);
                            if (btn) {
                                btn.click();
                                const firstInput = document.getElementById(inputIds[0]);
                                if (firstInput) firstInput.focus();
                            }
                        }
                    }
                });
            });
        }
    };

})();

// Exponer globalmente
window.ModuloUX = ModuloUX;

// Auto-init si el DOM ya está listo, o esperar
document.addEventListener('DOMContentLoaded', ModuloUX.init);
