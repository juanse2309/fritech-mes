# 🏭 FriTech MES - Sistema de Gestión de Producción e Inventario ![v1.8.45](https://img.shields.io/badge/versión-1.8.45--estable-green)

FriTech MES (Manufacturing Execution System) es una plataforma full-stack diseñada específicamente para el control y automatización de procesos de producción, gestión de inventarios y sincronización con el ERP World Office de la planta de fabricación de bujes de FriParts.

El sistema utiliza una **arquitectura 100% SQL-First**, empleando **PostgreSQL** en la nube como base de datos transaccional única. La dependencia histórica de Google Sheets ha sido completamente removida, conservando únicamente la API de Google Drive de manera opcional para el almacenamiento de reportes PDF generados.

## ✨ Novedades Versión 1.8.45 (Estable)
- **Empaque — stock insuficiente ya no bloquea el registro**: si al armar falta material, la operaria ahora puede confirmar "Registrar de todas formas" y el reporte se guarda igual (el armado físico ya ocurrió), dejando el almacén correspondiente en negativo con advertencia visible en vez de rechazar el reporte por completo.

## ✨ Novedades Versión 1.8.44 (Estable)
- **Lanzamiento 2026-08-31**: numeración automática de OP (INY/ENS/EMP) con bloque dedicado, nuevo módulo de **Empaque** (descuenta componentes vía BOM con prelación P. TERMINADO → POR PULIR y acredita el muñeco armado) y **Panel de Supervisión** de Pulido para Administración con tarjetas en vivo por operaria.
- **Pulido — confiabilidad de pausar/reanudar**: las llamadas a pausar/reanudar ciclo ahora reintentan (3 intentos con backoff) y, si aun así fallan, resincronizan contra el estado real del servidor antes de mostrar error -- eliminó falsos "No se pudo reanudar" cuando la petición sí había llegado.
- **Pulido — cola de autorización**: un reporte bloqueado por fecha/OP ya no se pierde si no hay un ADMIN físicamente presente -- queda pendiente y cualquier Administración puede autorizarlo o rechazarlo de forma remota desde el Panel de Supervisión.
- **Pulido — Panel de Supervisión**: rediseño visual con imagen real del producto por tarjeta, indicador automático de "Break" durante las ventanas de desayuno/almuerzo (solo visual, no toca el descuento real de tiempos), y "Corregir" ahora edita Referencia/OP/Lote (mientras la sesión sigue activa) en vez de las cantidades ya reportadas.
- **Pulido — corrección de datos**: la cantidad de "Bujes Revueltos" no recalculaba el total producido bruto, generando un falso "Error de Consistencia" que bloqueaba reportes reales en planta.
- **Gestión de Pedidos — Ver Empacado**: vista rápida y desplegable (colapsada por pedido) de cuánto lleva empacado/alistado cada pedido pendiente, referencia por referencia, sin tener que entrar uno por uno.
- **Dashboard — Mix de Producción por Referencia**: nuevo gráfico en el ranking de Pulido que parte cada barra de operaria por referencia (con vista en % opcional); el detalle por operaria ahora también muestra hora de inicio/fin, tiempo total y promedio de minutos por pieza cuando el dato viene de Pulido.

## ✨ Novedades Versión 1.7.0 (Estable)
- **Nuevo módulo — Cartera**: saldos, edades de cartera (30-60-90) y detalle de factura por cliente sincronizados desde World Office, con búsqueda por número de documento sin prefijo.
- **Nuevo módulo — Asistente IA**: chat de datos en lenguaje natural sobre ventas, cartera, producción y procura, con síntesis multi-herramienta, markdown enriquecido, gráficas y botón "ver en el módulo" que navega directo al dashboard real.
- **Nuevo módulo — Analítica Comercial (YoY)**: clasificación de clientes por crecimiento interanual (nuevo/reactivado/activo) por vendedor y zona, con corte YTD dinámico y exportación a Excel.
- **Nuevo módulo — Simulador de Programación**: sandbox de asignación de moldes/portamoldes/máquinas para planeación "qué pasaría si" sin afectar el MES real.
- **PWA**: se reemplazó el auto-reload silencioso por un banner de actualización con confirmación explícita; el service worker fuerza recarga cuando el frontend queda desactualizado contra el backend, sin bloquear CDNs externos (íconos/estilos) y esperando a que el nuevo SW tome control antes de recargar.
- **Rendimiento**: el dashboard administrativo bajó de ~14.9s a menos de 1s; se eliminaron queries de PNC duplicadas y joins fan-out que inflaban el conteo de ventas; Comercial Histórico ahora pagina server-side con debounce.
- **Seguridad**: RBAC exacto para Auxiliar de Inventario, validación server-side de identidad (JWT/sesión) en el cierre de nómina, el Ownership Guard normaliza tildes/espacios al comparar propietario, y se retiraron credenciales hardcodeadas del módulo de productos.
- **Arquitectura**: `app.py` monolítico se dividió en repositorios/servicios/rutas de dominio; se alineó el esquema físico de los modelos y se purgaron 11 clases duplicadas; los controladores de World Office y Pedidos ahora son "thin" (sin lógica de negocio en las rutas).
- **Correcciones de datos**: duplicidad de PNC por colisión de IDs en Historial, pérdida de signo/escala en helpers numéricos del dashboard, alias faltante en el `UPDATE` de nómina, y agotamiento de memoria (OOM) al exportar el Historial Global a Excel.

## ✨ Novedades Versión 1.6.4 (Estable)
- **Nómina — Sábado/Domingo**: se corrige el cálculo de jornada para que un formato de hora no estándar ya no caiga silenciosamente en 0 horas; se centraliza la normalización en `nomina_service.py` con logging explícito, y se ejecutó un backfill histórico de los registros afectados en periodo abierto.
- **Analítica**: ratios de eficiencia de Inyección/Pulido corregidos (numerador y denominador ya comparten la misma población de lotes), e Índice de Desperdicio (% PNC Total) ahora es matemáticamente consistente con el FPY Global.
- **Seguridad — Ownership**: pedidos, despachos y pulido ya no dependen exclusivamente del campo `responsable`/`vendedor` enviado por el cliente; se resuelve con fallback a la identidad autenticada (JWT/sesión) y se rechaza la operación si no hay identidad válida.
- **PWA**: el service worker ahora purga cachés obsoletas al activarse, evitando que usuarios con la app instalada queden atascados en una versión vieja tras un deploy.

## ✨ Novedades Versión 1.5.0 (Estable)
- **Arquitectura SQL-First**: Consolidación definitiva de base de datos transaccional PostgreSQL como origen único de persistencia y control de inventarios, eliminando por completo Google Sheets.
- **Decomisión de Módulos Obsoletos**: Purga completa de los dashboards de gerencia y métricas PNC antiguas en favor de consultas SQL directas y óptimas.
- **Estructuración del Proyecto**: Organización formal de scripts temporales en `scratch/` y pruebas en `tests/` para mantener la raíz limpia y segura.
- **Seguridad en Integraciones**: Robustecimiento de la comunicación HTTPS con World Office mediante handshakes seguros autenticados con tokens dinámicos.

---

## 🧭 Módulos Principales del Sistema

### 🏭 Producción y Planta

| Módulo | Descripción Técnica | Componentes Clave |
| :--- | :--- | :--- |
| **🏭 Inyección** | Control del proceso primario de inyección de plástico. Soporta configuraciones de "Molde de Familia" (múltiples SKUs por ciclo), control de cavidades y control de tiempos y contadores por máquina. | `inyeccion_routes.py`<br>`inyeccion.js`<br>`PDFGenerator` (ReportLab) |
| **✨ Pulido** | Monitoreo del acabado y calidad de piezas. Incluye el flujo de **Liquidación de Lote**, cálculo automático de diferencias e inventario en tránsito desde satélites externos. | `pulido_routes.py`<br>`pulido.js`<br>`trazabilidad_lotes` (SQL) |
| **🔩 Ensamble** | Mapeo y ensamble final de bujes con base en una ficha maestra (recetas de componentes). Realiza deducciones automáticas de stock del almacén de materias primas al ensamblar un SKU. | `ensamble_routes.py`<br>`ensamble.js`<br>`bom_service.py` |
| **🧪 Mezcla y Molido** | Registro de mezclas de materia prima y pesajes de material molido (recuperado/contaminado) en planta. | `materia_prima_routes.py`<br>`mezcla.js` |
| **⚠️ PNC** | Control de **Producto No Conforme**. Registro, clasificación y búsqueda inteligente de rechazos de calidad por tipo de defecto, con descuento automático de inventario. | `pnc_routes.py`<br>`pnc.js` |
| **🗓️ Control MES / Programación** | Catálogo de máquinas y programación general de producción en planta. | `programacion_routes.py`<br>`mes_control.js` |
| **🧮 Simulador de Programación** | Sandbox de asignación de máquinas/moldes/portamoldes para planear "qué pasaría si" sin afectar el MES real. | `simulador_routes.py`<br>`simulador.js` |
| **⚙️ Frimetals** | Línea de negocio metalmecánica independiente: registro de producción, dashboard y catálogo propio. | `metals_routes.py`<br>`metals.js` |
| **📦 Inventario** | Entradas, salidas, conteos físicos y gestión de fichas/moldes. | `inventario_routes.py`<br>`inventario.js` |
| **🚚 Almacén / Alistamiento** | Flujo logístico interno con **Doble Check**: Alistamiento de mercancía (**Box** 📦) y confirmación de Despacho de camiones (**Truck** 🚚), con soporte para despachos parciales. | `pedidos_routes.py`<br>`almacen.js` |

### 🛒 Comercial y Ventas

| Módulo | Descripción Técnica | Componentes Clave |
| :--- | :--- | :--- |
| **🛒 Pedidos** | Gestión de órdenes de compra comerciales y solicitudes de clientes. Visualización en tiempo real optimizada para visualizadores en planta (Modo TV) con alertas sonoras integradas. | `pedidos_routes.py`<br>`pedidos.js` |
| **🌐 Portal de Cliente (B2B)** | Catálogo, carrito y seguimiento de pedidos para clientes externos autenticados. | `cliente_routes.py`<br>`portal_client.js` |
| **🧾 Facturación / World Office** | Exporta pedidos al formato de World Office para facturación, con auto-sanado de precios. | `facturacion_routes.py`<br>`facturacion.js` |
| **💰 Cartera** | Saldos, edades de cartera (30-60-90) y detalle de facturas por cliente, sincronizados desde World Office. | `cartera_routes.py`<br>`cartera.js` |
| **📈 Analítica Comercial Histórica** | Series históricas de ventas agregadas (2024 en adelante) para consulta gerencial. | `comercial_routes.py`<br>`comercial_historico.js` |
| **📊 Crecimiento de Clientes (YoY)** | Clasificación de clientes por crecimiento interanual: nuevo, reactivado o activo, por vendedor/zona. | `comercial_routes.py`<br>`analitica_comercial.js` |
| **👤 Administración de Clientes B2B** | Alta, reseteo de clave y activación de cuentas de clientes del portal. | `admin_routes.py`<br>`admin_clientes.js` |
| **📣 Marketing / Notificaciones Push** | Envío masivo de campañas push a clientes y personal. | `pwa_routes.py`<br>`marketing.js` |

### 📊 Dashboard e Inteligencia

| Módulo | Descripción Técnica | Componentes Clave |
| :--- | :--- | :--- |
| **📊 Dashboard BI** | Panel principal de indicadores: ventas, producción, cartera y PNC, incluida la métrica consolidada Lean de calidad. | `dashboard_routes.py`<br>`gerencia_routes.py`<br>`dashboard.js` |
| **🤖 Asistente IA** | Preguntas en lenguaje natural sobre los datos reales del negocio (ventas, cartera, producción), con gráficas y síntesis en el chat. | `asistente_routes.py`<br>`asistente.js` |
| **🎙️ IA de Voz para Ensamble** | Transcribe audio a datos estructurados para reportar producción de Ensamble por voz (Gemini). | `ia_routes.py` |
| **🕓 Historial Global** | Consulta y edición de movimientos históricos consolidados de Inyección, Pulido, Ensamble y PNC. | `historial_routes.py`<br>`historial.js` |

### 👥 Personas

| Módulo | Descripción Técnica | Componentes Clave |
| :--- | :--- | :--- |
| **🕘 Asistencia** | Registro de asistencia diaria/masiva, ausencias y horas por colaborador. | `asistencia_routes.py`<br>`asistencia.js` |
| **💵 Nómina** | Consolidado de horas y ejecución del cierre/corte de nómina. | `asistencia_routes.py`<br>`nomina_service.py`<br>`nomina.js` |

Además de lo anterior, un conjunto de rutas de infraestructura compartida (`productos_routes.py` como catálogo maestro de SKUs, `imagenes_routes.py` como proxy con caché de imágenes de producto en Google Drive, `auth_routes.py` para sesión/roles y `pwa_routes.py` para push) da soporte transversal a todos los módulos de arriba.

---

## 🛠️ Stack Tecnológico

*   **Backend:** Python 3.9+ con **Flask** (Estructura de Blueprints modulares).
*   **Base de Datos:** **PostgreSQL** (100% transaccional principal vía *Flask-SQLAlchemy*). El soporte de lectura/escritura de Google Sheets está completamente deprecado e inactivo.
*   **Frontend:** HTML5 semántico, **Vanilla CSS3** (layouts responsivos para pantallas de operador y celulares) y **JavaScript (ES6+)** con arquitectura modular.
*   **Reportes y PDF:** **ReportLab** para la generación local y carga automática en **Google Drive** de tiquetes de producción y fichas técnicas.
*   **Infraestructura:** Despliegue automatizado mediante CI/CD en **Render**.

---

## ⚙️ Configuración del Entorno

### Requisitos Previos
*   Python 3.9 o superior.
*   Instalación de PostgreSQL local o en la nube.
*   Credenciales de Google Cloud Platform (Service Account habilitado para la API de Google Drive para almacenamiento de reportes).

### Archivo de Variables de Entorno (`.env`)
Configura un archivo `.env` en la raíz del proyecto basándote en la siguiente plantilla:

```ini
# ============================================
# CONFIGURACIÓN GOOGLE DRIVE (REPORTES PDF)
# ============================================
DRIVE_REPORTS_FOLDER_ID=id_de_la_carpeta_de_drive_para_reportes

# ============================================
# CONFIGURACIÓN DE CACHÉ Y SEGURIDAD FLASK
# ============================================
FLASK_ENV=development # development | production
FLASK_DEBUG=true
PORT=5005
# Requerida (Fail-Fast): backend/app.py aborta el arranque si falta. Firma las
# cookies de sesión Flask. Genera un valor aleatorio largo, distinto de JWT_PWA_SECRET.
FLASK_SECRET_KEY=clave_secreta_para_sesiones_flask
# Requerida (Fail-Fast): backend/app.py aborta el arranque si falta. Firma/valida
# los JWT Bearer usados por la PWA y el portal de clientes (ver auth_middleware.py).
# Deliberadamente distinta de FLASK_SECRET_KEY -- no se debe reutilizar el mismo
# secreto entre ambos canales de autenticación. Mínimo 32 caracteres aleatorios.
JWT_PWA_SECRET=tu_clave_secreta_pwa_aqui_minimo_32_caracteres
CACHE_TTL=120
CACHE_ENABLED=true

# ============================================
# CONFIGURACIÓN DE BASE DE DATOS TRANSACCIONAL
# ============================================
# Utilizado por Flask-SQLAlchemy para persistencia de producción
DATABASE_URL=postgresql://usuario:password@host:port/database_name

# ============================================
# INTEGRACIÓN ERP WORLD OFFICE (WO)
# ============================================
# Conexión local del agente a la BD SQL Server de World Office
WO_SERVER=SERVERWO\WORLDOFFICEXXXX
WO_DB=WO_DB
WO_USER=cliente
WO_PASSWORD=contraseñaxxxx

# Handshake seguro de API de Sincronización
WO_SYNC_API_KEY=token_seguro_de_comunicacion_wo
API_RENDER_URL=https://tu-app-en-render.com/api/wo/recibir_datos

# ============================================
# CONFIGURACIÓN DE INTELIGENCIA ARTIFICIAL
# ============================================
GOOGLE_API_KEY=api_key_para_google_ai_studio
```

---

## 🔄 Integraciones y Flujos Críticos

### 1. Sincronización con World Office ERP
El sistema mantiene una comunicación fluida con la base de datos comercial y de inventario de World Office mediante un agente automatizado (`agente_wo_comercial.py` / `agente_wo.py`):
1.  **Agente Local**: Lee de manera segura la base de datos del ERP en SQL Server.
2.  **Handshake Seguro**: Empaqueta los datos y los envía a la API en Render usando cabeceras de autorización firmadas con `X-Sync-Token` (asociado a `WO_SYNC_API_KEY`).
3.  **Procesamiento**: Los endpoints en `backend/routes/wo_routes.py` reciben y actualizan los saldos de inventario comprometido y de ventas acumuladas en PostgreSQL.

### 2. Flujo de Satélite / Pulido
*   El material inyectado se clasifica como "Por Pulir".
*   Al enviarse a satélites de pulido, se crea un **Lote de Pulido** en estado "ACTIVO" en la base de datos.
*   El frontend en `pulido.js` implementa campos "pegajosos" (Sticky Inputs) y notificaciones dinámicas basadas en caché de persistencia de sesión para acelerar el registro del operario.
*   **Liquidación de Lote**: Cuando el lote retorna, el supervisor cierra el lote a través de la acción "Liquidar Lote", lo que transfiere automáticamente el stock pulido a "Producto Terminado" o "Producto Ensamblado" y registra diferencias de producción.

---

## 🔧 Reglas de Mantenimiento y Estructura de Limpieza

Para mantener el repositorio limpio y el código en producción libre de archivos basura, se han establecido reglas estrictas de segregación de carpetas:

```
📂 proyecto_friparts/
├── 📂 backend/           # Lógica del servidor Python/Flask, modelos y rutas
├── 📂 frontend/          # Interfaz de usuario (HTML, CSS, módulos JS)
├── 📂 scratch/           # Carpeta exclusiva para scripts de desarrollo
└── 📂 tests/             # Carpeta para pruebas automáticas y de integración
```

*   **🚫 Cero Scripts en la Raíz**: Queda estrictamente prohibido crear scripts de prueba rápida o utilitarios sueltos en el directorio raíz o dentro de `backend/`.
*   **📁 Carpeta `scratch/`**: Todos los scripts de migración de datos (`migrate.py`), pruebas de query rápidas (`test_query_cot.py`) o diagnósticos temporales deben guardarse en `scratch/`. Esta carpeta está diseñada para no interferir con las ejecuciones en producción.
*   **📁 Carpeta `tests/`**: Los archivos que verifiquen el comportamiento de la aplicación de manera automatizada (e.g., pruebas unitarias o de integración como `test_wo_sync.py`) deben residir en esta sección.

---

## 🚀 Puesta en Marcha (Instalación Local)

1.  **Clonar e ingresar al directorio del proyecto**:
    ```bash
    git clone https://github.com/juanse2309/fritech-mes.git
    cd fritech-mes
    ```

2.  **Crear el entorno virtual y activar**:
    *   **En Windows:**
        ```bash
        python -m venv .venv
        .venv\Scripts\activate
        ```
    *   **En macOS/Linux:**
        ```bash
        python3 -m venv .venv
        source .venv/bin/activate
        ```

3.  **Instalar dependencias**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configurar credenciales de acceso**:
    *   Duplicar `.env.example`, renombrarlo a `.env` y configurar las credenciales correctas.
    *   Ubicar el archivo de cuenta de servicio de Google Cloud (`credentials_apps.json`) en la raíz del proyecto (este archivo se encuentra en el `.gitignore` por seguridad).

5.  **Ejecutar la aplicación**:
    ```bash
    python -m backend.app
    ```
    La aplicación se iniciará en `http://localhost:5005` (o en el puerto definido en tus variables de entorno).

---
*Hecho con ❤️ por Juanxe Novoa.*
