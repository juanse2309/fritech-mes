"""
Migración: crea db_pulido_pendientes_autorizacion -- cola de reportes de
Pulido bloqueados (fecha distinta a hoy o cantidad que excede lo inyectado)
esperando que un ADMIN los autorice (plan 2026-09-01, ver
PulidoPendienteAutorizacion en sql_models.py). Antes, un reporte bloqueado
para una operaria normal simplemente se perdía -- ahora queda guardado aquí
con el payload completo para poder re-enviarlo si un ADMIN lo autoriza desde
el Panel de Supervisión, sin tener que estar en la tablet de la operaria.

No destructiva: solo CREATE TABLE IF NOT EXISTS + índices IF NOT EXISTS.
Correr dos veces es un no-op seguro.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS db_pulido_pendientes_autorizacion (
                id                  SERIAL PRIMARY KEY,
                id_pulido           VARCHAR(100) NOT NULL,
                responsable         VARCHAR(200),
                codigo              VARCHAR(100),
                orden_produccion    VARCHAR(100),
                lote                VARCHAR(100),
                cantidad_real       NUMERIC(12, 2),
                fecha_trabajo       VARCHAR(20),
                tipo_bloqueo        VARCHAR(50)  NOT NULL,
                motivo_bloqueo      TEXT,
                payload_json        TEXT         NOT NULL,
                estado              VARCHAR(20)  NOT NULL DEFAULT 'PENDIENTE',
                resuelto_por        VARCHAR(150),
                motivo_resolucion   TEXT,
                creado_en           TIMESTAMP    NOT NULL DEFAULT NOW(),
                resuelto_en         TIMESTAMP
            );
        """))
        print("Tabla 'db_pulido_pendientes_autorizacion' verificada/creada.")

        # Ensancha tipo_bloqueo si la tabla ya existia con la version corta
        # (VARCHAR(20)) de una corrida anterior de este script -- los codigos
        # reales que llegan aqui (PULIDO_FECHA_BLOQUEADA, etc.) no caben ahi.
        db.session.execute(text("ALTER TABLE db_pulido_pendientes_autorizacion ALTER COLUMN tipo_bloqueo TYPE VARCHAR(50);"))
        print("Columna 'tipo_bloqueo' verificada en VARCHAR(50).")

        for nombre, columna in [
            ('ix_pulido_pend_auth_id_pulido', 'id_pulido'),
            ('ix_pulido_pend_auth_estado', 'estado'),
            ('ix_pulido_pend_auth_creado_en', 'creado_en'),
        ]:
            db.session.execute(text(f"CREATE INDEX IF NOT EXISTS {nombre} ON db_pulido_pendientes_autorizacion ({columna});"))
        print("Índices verificados/creados.")

        db.session.commit()
        print("Migración exitosa.")

    except Exception as e:
        db.session.rollback()
        print(f"Error en la migración, rollback aplicado: {e}")
        raise
