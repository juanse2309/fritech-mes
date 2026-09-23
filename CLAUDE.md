# FRITECH — Instrucciones del proyecto

## Arquitectura de despliegue: varias empresas, una instancia por cliente

Este código corre como una instancia independiente por cliente (su propio
contenedor + su propia base de datos), no como un sistema multi-tenant
compartido. Las diferencias entre clientes viven en variables de entorno
(ver `backend/config/settings.py::Empresa`), no en ramas de código ni en
copias del repo.

Instancias activas hoy:
- **FRIPARTS** (`friparts.fribytes.com`) — instancia de referencia.
- **FRIMETALS** (`frimetals.fribytes.com`) — primer cliente piloto, separado.

## Flujo de despliegue: canary deployment

- **Solo FRIPARTS tiene auto-deploy activo** en Coolify — se despliega sola
  con cada push a `main`, porque es la que se usa a diario y cualquier
  regresión se nota de inmediato ahí.
- **Todas las demás instancias (FRIMETALS y cualquier cliente nuevo) tienen
  el auto-deploy desactivado a propósito.** Se actualizan con un Deploy
  manual en Coolify, solo después de confirmar que el cambio no rompió
  FRIPARTS.
- Un push a `main` **no** significa que todos los clientes ya están
  actualizados — hay que desplegarlos a mano, uno por uno, cuando se confía
  en el cambio.

## Qué señalar al terminar un cambio de código

Antes de dar un cambio por cerrado, indicar explícitamente:
- Si el comportamiento cambiado depende de configuración por cliente
  (`Empresa`), qué instancias necesitan una variable de entorno nueva o
  distinta para no romperse con el cambio.
- Qué instancias, aparte de FRIPARTS, van a necesitar un Deploy manual en
  Coolify una vez el cambio quede verificado ahí.

No asumir que "ya se hizo el push" equivale a "ya está desplegado en todos
los clientes" — son dos pasos distintos en este proyecto.

## Backups de base de datos

El backup diario (`pg_dump` -> gzip -> Google Drive) vive en
`backend/scripts/backup_db_drive.py` y corre como **Tarea Programada de
Coolify dentro del propio contenedor de cada instancia** (no como recurso
separado, no depende de ninguna PC ni de SSH). Sube a la carpeta de Drive
"fritech_backups", compartida hoy por todas las instancias, con el nombre
`<empresa>_YYYYMMDD_HHMMSS.sql.gz` (prefijo = `EMPRESA_NOMBRE` de esa
instancia, en minúsculas).

**Clave: una Tarea Programada de Coolify es de UN servicio, no se hereda
entre instancias** — el mismo patrón que el auto-deploy de la sección
anterior. Que FRIPARTS tenga sus backups corriendo no significa que
FRIMETALS (ni ningún cliente nuevo) los tenga; hay que configurarlo a mano,
por cliente, en Coolify.

### Checklist para que un cliente nuevo tenga backups

1. Confirmar que el `.env` de esa instancia ya define `DATABASE_URL` y
   `EMPRESA_NOMBRE` (ya son obligatorios por otras razones — ver
   `.env.example`). `EMPRESA_NOMBRE` determina el prefijo del archivo.
2. Definir `DRIVE_BACKUP_FOLDER_ID` en su `.env` — hoy se reusa el mismo
   valor que ya usan las demás instancias (misma carpeta compartida de
   Drive, se distinguen por prefijo de archivo). No hay que crear una
   carpeta nueva salvo que se decida separar por cliente (ver riesgo abajo).
3. Confirmar `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` /
   `GOOGLE_OAUTH_REFRESH_TOKEN` — hoy se reusa la misma cuenta de Drive
   (`friparts09@gmail.com`) que ya usan los PDF de Inyección.
4. En Coolify, en el servicio de ESA instancia (no en el de FriParts),
   crear una Tarea Programada nueva (Scheduled Tasks -> Add Task). Valores
   de la de FriParts, que vive solo en Coolify, no en este repo: Name
   `Backup DB a Drive`, Schedule `0 6 * * *` (06:00 UTC), Timeout `600`,
   Command `python3 -m backend.scripts.backup_db_drive`. FriParts tiene el
   campo Container con el uuid de su app; en Frimetals se dejó en blanco
   (contenedor principal) — confirmar con un "Execute Now" que resuelve.
5. Verificar al día siguiente que apareció en Drive un archivo
   `<empresa>_YYYYMMDD_HHMMSS.sql.gz` con el prefijo correcto y un tamaño
   razonable (no 0 bytes) — no basta con que la Tarea Programada aparezca
   como "creada" en Coolify.

### Riesgo abierto, sin resolver

Todas las instancias comparten hoy la misma carpeta y la misma cuenta
personal de Drive para los backups — a pesar de que la arquitectura exige
base de datos separada por cliente. Es aceptable mientras los backups sean
un asunto interno de FRITECH y nadie prometa aislamiento de backups a un
cliente, pero si eso cambia (más clientes, exigencia contractual de
aislamiento de datos) hay que separar por carpeta o por cuenta de Drive
antes de que se vuelva un problema, no después.

## Reglas de arquitectura de código (innegociables)

1. **Separación estricta de capas.** Prohibido escribir lógica de negocio,
   cálculos o consultas SQL en los archivos de rutas (`*_routes.py`). Un
   controlador solo recibe el request, delega a un servicio (`*_service.py`
   o un repositorio) y traduce el resultado a JSON. Si una tarea implica
   meter lógica o SQL en una ruta, hay que negarse y corregir el enfoque, no
   hacerlo igual porque se pidió así. Esto aplica también a código ya
   existente: si al tocar un archivo de rutas por otra razón se nota una
   violación preexistente (lógica/SQL ya viviendo ahí), señalarla
   explícitamente aunque nadie haya preguntado — no basta con no agregar más
   violaciones nuevas.
2. **Código defensivo.** Toda ejecución contra la base de datos (en el
   servicio, que es donde debe vivir) va envuelta en `try/except` con
   `db.session.rollback()` en el except — nunca dejar una sesión de
   PostgreSQL colgada a medio commit.
3. **Cero suposiciones sobre el código.** Antes de asumir cómo se comporta
   algo, leerlo directamente del repo (con las herramientas de archivo/
   búsqueda disponibles) en vez de adivinar nombres de variables, funciones
   o rutas. Si hace falta información que no está en el repo ni se ha dado
   en la conversación, preguntar antes de inventar.
4. **Datos sintéticos para pruebas de estrés.** Cuando un cambio toque
   lógica de escritura crítica (inventario, cierres, facturación, lo que
   afecte dinero o trazabilidad), preparar y correr un caso de prueba con
   datos borde (caracteres especiales, nulos, duplicados) antes de darlo
   por listo para producción — no basta con que compile o con un caso feliz
   único.
5. **No confiar en el tipo de columna que dice el modelo de SQLAlchemy.**
   Cuando un cambio empieza a persistir de verdad un campo que antes era un
   no-op silencioso (el backend lo aceptaba del payload pero nunca lo
   guardaba), verificar el tipo real de esa columna en producción
   (`information_schema.columns`) antes de asumir que coincide con
   `db.Column(...)`. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` no valida
   tipo si la columna ya existe — si alguna vez se creó mal (incluso por
   fuera de este repo, a mano), la migración se queda callada y el ORM
   sigue leyendo/escribiendo como si todo estuviera bien. Ver incidente
   2026-09-16: `db_pedidos.no_disponible` era `TEXT` en vez de `BOOLEAN` en
   FriParts; `bool("false")` da `True` en Python (cualquier string no vacío
   es truthy), así que cada pedido nuevo se leía con sus líneas marcadas
   "no disponible" sin que nadie las tocara.

## Reglas de seguridad (curadas en la auditoría 2026-09-23)

Salieron de una auditoría de seguridad completa del repo. Aplican a
código nuevo Y a código tocado por otra razón (mismo criterio que la regla
de capas: si se nota una violación preexistente al pasar por ahí,
señalarla/corregirla, no ignorarla).

1. **Todo endpoint nuevo lleva `@require_login` o `@require_role(...)`
   desde el día uno**, salvo que sea deliberadamente público (ej. login,
   registro de cliente) o máquina-a-máquina con su propio secreto
   compartido (mismo patrón que ya usan las rutas WO y
   `ensamble_routes.cerrar_jornada_auto`: `X-Sync-Token`/`X-API-Key` contra
   una env var, no sesión de usuario). Nunca lo dejes sin protección "para
   agregarlo después" — la auditoría encontró varios endpoints de
   producción (inyección, pulido) expuestos así por meses.
2. **Nunca insertar datos de BD/usuario en `innerHTML` sin escapar.** Usa
   `escapeHtml(...)` (global, definida una sola vez en
   `frontend/static/js/modules/utils.js` — no dupliques esta función en
   otro módulo). Esto incluye datos que van DENTRO de un atributo HTML
   (`data-*`, `title`, etc.) — ahí además hay que evitar interpolar el dato
   directo en un `onclick="...('${variable}')"` inline, porque una comilla
   en el dato rompe el atributo aunque el texto esté "escapado" a medias.
   Patrón correcto: `data-*` attributes + un solo listener delegado
   (`addEventListener` en el contenedor padre, revisa `dataset.action` /
   `dataset.email` etc.) — ver `admin_clientes.js` como referencia.
3. **Rate limiting de login es específico, no solo el límite global de
   IP.** El límite global (`60 per minute` en `backend/app.py`) no frena
   fuerza bruta contra UNA cuenta si el atacante rota de IP. Los tres
   endpoints de login llevan un límite propio keyed por la identidad del
   body (`responsable`/`email`, no por IP) aplicado desde `backend/app.py`
   vía `app.view_functions['auth.xxx'] = limiter.limit(...)(...)` — **no**
   importar el objeto `limiter` dentro de `backend/routes/*.py` (vive en
   `app.py`; importarlo desde una ruta arriesga un import circular, por
   eso el patrón existente engancha los límites de rutas específicas desde
   `app.py`, después de `register_blueprint`, no con un decorador en el
   archivo de la ruta).
4. **Validación de payloads con Pydantic, no `data.get(...)` sueltos** —
   nuevo estándar del proyecto (antes no había librería de validación).
   Los schemas viven en `backend/schemas/<dominio>_schemas.py` (ver
   `facturacion_schemas.py` como referencia/plantilla). Aplica por lo menos
   a endpoints nuevos o tocados que sean de dinero/trazabilidad (regla 4
   de arquitectura, arriba) — no hace falta retrofitear TODO el backend de
   una vez, pero cualquier endpoint de ese tipo que se toque de ahora en
   adelante se valida así. **Cuidado con los defaults reales del
   frontend**: un `<input>` vacío manda `''` (string vacío), no `None` —
   si el schema espera `Optional[int]`, hay que mapear `''` a `None` con un
   `field_validator(..., mode='before')` o vas a rechazar el caso más común
   en producción (pasó en el primer schema escrito, `ExportarWorldOfficeSchema`,
   detectado con un test antes de shippearlo).
5. **`requirements.txt` con versiones fijadas (`==`), no sueltas.** Antes
   de fijar una versión, correr `pip-audit -r requirements.txt` (en un
   venv de prueba, no el de desarrollo) — fijar sin auditar puede congelar
   una versión con CVE conocido que el build sin pin habría recogido ya
   parchada. Si el fix de un CVE choca con el pin de OTRA dependencia
   (pasó con `click`, bloqueado en `<8.2` por `gTTS==2.5.4`, la última
   versión publicada), documentarlo como riesgo residual aceptado con el
   razonamiento de por qué no es explotable en este contexto — no dejarlo
   silencioso ni fingir que se arregló.
6. **El contenedor corre con un usuario no-root** (`Dockerfile`, `USER
   appuser`) — cualquier `RUN` o `COPY` nueva que necesite escribir en
   `/app` debe ir ANTES del `chown -R appuser:appuser /app`, o ese archivo
   quedará sin permiso de escritura para el proceso de la app.
7. **`MAX_CONTENT_LENGTH` está fijado en 20MB** (`backend/app.py`) — si
   algún día se necesita subir un archivo legítimo más grande que eso
   (ej. un catálogo de precios enorme), subir el límite explícitamente ahí
   en vez de quitarlo.
8. **Nunca hardcodear una credencial real** (password de BD, API key) en
   ningún script, ni siquiera en `scratch/` (está en `.gitignore`, pero
   este repo vive dentro de OneDrive — un archivo ignorado por git igual
   se sincroniza a la nube). Usar siempre `os.getenv(...)` incluso en
   scripts de un solo uso.

## Postura esperada: nada de complacencia

No asumir que "el usuario lo pidió así" es suficiente para proceder sin
más. En cada tarea, señalar activamente riesgos, efectos secundarios o
supuestos débiles que se noten — aunque no se haya preguntado por ellos
explícitamente — en vez de simplemente ejecutar y quedar bien. Si algo
huele a riesgo para producción, para clientes reales, o para datos, decirlo
primero y proponer la alternativa más segura, no callarlo por seguir el
hilo. Verificar contra datos/comportamiento real (no solo que el código
compile) siempre que sea posible antes de dar un cambio por terminado.
