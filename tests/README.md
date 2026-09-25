# Cómo correr la suite de pruebas localmente

Esta suite corre contra una base de datos **Postgres real** -- no hay mocks
ni SQLite. **Nunca la apuntes a `DATABASE_URL` de producción**: importar
`backend.app` ya ejecuta `db.create_all()` + varios `ALTER TABLE`, y
`tests/conftest.py` corta la ejecución si `DATABASE_URL` no contiene la
palabra `test` (salvo que exportes `PYTEST_ALLOW_DB=1` a propósito).

## 1. Levantar Postgres y crear la base de test

```bash
# Cualquier Postgres 15+ sirve. Localmente (Debian/Ubuntu):
sudo service postgresql start
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres createdb fritech_test
```

## 2. Variables de entorno (dummies, nunca las reales)

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/fritech_test
export FLASK_SECRET_KEY=algo-cualquiera-de-desarrollo
export JWT_PWA_SECRET=otro-valor-distinto-al-anterior
export PORT=5005
```

## 3. Instalar dependencias

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements-dev.txt` es nuevo y **no** se instala en la imagen Docker de
producción (el `Dockerfile` solo instala `requirements.txt`).

## 4. Tests unitarios / de servicio (sin necesidad de la app corriendo)

```bash
python -m pytest tests/ --ignore=tests/test_asistencia_api.py --ignore=tests/e2e -v
```

`tests/test_asistencia_api.py` es un script manual (urllib contra un server
ya corriendo en `127.0.0.1:5005`), no un test de pytest -- por eso se
ignora igual que en CI.

## 5. E2E con Playwright (necesitan la app corriendo + datos sembrados)

```bash
# Instalar el browser de Playwright (una sola vez; en este sandbox de
# desarrollo Chromium ya viene preinstalado y tests/e2e/conftest.py lo usa
# automáticamente si lo encuentra en /opt/pw-browsers/chromium):
python -m playwright install --with-deps chromium

# Sembrar catálogo mínimo (productos, cliente, máquina) que los E2E usan --
# ver backend/scripts/seed_test_data.py. Reusable con --cleanup para borrar.
python -m backend.scripts.seed_test_data

# Levantar el servidor de desarrollo en otra terminal (o en background):
python -m backend.app

# Correr los E2E (en otra terminal, con el server arriba):
python -m pytest tests/e2e/ -v
```

Cada E2E crea su propio usuario de staff sintético (`TEST-E2E-...`, rol
`ADMINISTRACION`, password numérica generada al vuelo) y limpia después de
sí mismo (usuario + cualquier Pedido/sesión de Pulido que haya creado) --
ver el fixture `e2e_staff_user` en `tests/e2e/conftest.py`. Correr la suite
dos veces seguidas no debería dejar basura en la base.

## 6. Todo junto (como en CI, con el server ya arriba)

```bash
python -m pytest tests/ --ignore=tests/test_asistencia_api.py -v
```

## Hallazgos encontrados escribiendo estas pruebas (no corregidos acá)

Este PR es solo de tests/CI -- lo siguiente quedó documentado para el
próximo bloque (backend), no se tocó código de producción para no mezclar
alcance:

- **`pedido_id_seq` nunca se crea en una instancia nueva de verdad.** El
  bloque de arranque de `backend/app.py` (líneas ~157-203) corre varias
  sentencias DDL en un único `try/except` que aborta TODO el bloque ante el
  primer error -- en una base sin la tabla legacy `cartera_wo` (que ningún
  modelo ORM crea), el `ALTER TABLE cartera_wo` de la línea 162 revienta
  antes de llegar a crear la secuencia `pedido_id_seq` de la línea ~181, así
  que el primer pedido que alguien registre en un cliente nuevo falla con
  `relation "pedido_id_seq" does not exist`. `backend/scripts/seed_test_data.py`
  crea la secuencia a mano como workaround para que el E2E de Pedidos
  pueda correr, pero el arreglo real (separar cada `ALTER`/`CREATE` en su
  propio `try/except`, como ya se hizo para el bloque de líneas ~214-219)
  queda pendiente.
- **El checkbox de selección en Facturación tiene un doble-toggle.**
  `input.check-pedido-wo` hace `onchange="togglePedido(id);
  event.stopPropagation()"`, pero eso solo detiene la propagación del
  evento `change` -- el `click` sigue burbujeando hasta el `onclick` de la
  `<tr>`, que hace su propio `checkbox.checked = !checkbox.checked` +
  `togglePedido(id)`. Un clic directo sobre el checkbox termina
  alternando el estado dos veces (se cancela a sí mismo): la fila nunca
  queda marcada como seleccionada. `tests/e2e/test_facturacion.py` lo
  esquiva haciendo clic en otra celda de la fila en vez del checkbox.
