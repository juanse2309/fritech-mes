FROM python:3.14-slim

# libpq-dev/build-essential quedan solo por si algún paquete no trae wheel
# precompilado para esta versión de Python y pip cae a compilar desde fuente
# (psycopg2-binary normalmente no lo necesita, pero es la red de seguridad
# estándar para no romper el build por un solo paquete sin wheel).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/
COPY gunicorn.conf.py .

ENV PYTHONUNBUFFERED=1
ENV PORT=10000
EXPOSE 10000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "backend.app:app"]
