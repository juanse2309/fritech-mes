FROM python:3.14-slim

# libpq-dev/build-essential quedan solo por si algún paquete no trae wheel
# precompilado para esta versión de Python y pip cae a compilar desde fuente
# (psycopg2-binary normalmente no lo necesita, pero es la red de seguridad
# estándar para no romper el build por un solo paquete sin wheel).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# pg_dump para backend/scripts/backup_db_drive.py (Tarea Programada de
# Coolify, backup diario a Drive). El postgresql-client de Debian trixie
# trae la v17, pero friparts-db real corre Postgres 18 -- pg_dump se niega
# a respaldar un servidor MÁS NUEVO que él mismo ("aborting because of
# server version mismatch", confirmado en producción 2026-09-16). Por eso
# se agrega el repo oficial de PostgreSQL para instalar postgresql-client-18
# en vez del genérico de Debian.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/
COPY gunicorn.conf.py .

# No correr como root: si algún día aparece otro bug (RCE en una dependencia,
# un upload mal validado, etc.) que le de a un atacante ejecución dentro del
# contenedor, un usuario sin privilegios acota el daño (sin acceso de
# escritura fuera de /app, sin poder instalar/alterar nada a nivel sistema).
# chown DESPUES de copiar todo: este usuario es dueño de /app completo,
# incluyendo logs/, temp_reports/ y demás carpetas que la app cree en
# runtime (ver LOG_FILE en .env.example).
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1
ENV PORT=10000
EXPOSE 10000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "backend.app:app"]
