"""
Migración: crea db_pulido_overrides -- bitácora de bloqueos duros de
Pulido (fecha distinta a hoy, o cantidad que excede lo inyectado) saltados
por un ADMIN (plan 2026-08-28, ver PulidoOverride en sql_models.py). Fuente
del reporte que la jefa pidió para restar puntos por no llevar la app al día.

No destructiva: solo CREATE TABLE IF NOT EXISTS + índices IF NOT EXISTS.
Correr dos veces es un no-op seguro.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS db_pulido_overrides (
                id              SERIAL PRIMARY KEY,
                id_pulido       VARCHAR(100),
                tipo            VARCHAR(20)  NOT NULL,
                operaria        VARCHAR(200),
                autorizado_por  VARCHAR(150),
                motivo          TEXT,
                detalle         TEXT,
                creado_en       TIMESTAMP    NOT NULL DEFAULT NOW()
            );
        """))
        print("Tabla 'db_pulido_overrides' verificada/creada.")

        for nombre, columna in [
            ('ix_pulido_overrides_id_pulido', 'id_pulido'),
            ('ix_pulido_overrides_creado_en', 'creado_en'),
        ]:
            db.session.execute(text(f"CREATE INDEX IF NOT EXISTS {nombre} ON db_pulido_overrides ({columna});"))
        print("Índices verificados/creados.")

        db.session.commit()
        print("Migración exitosa.")

    except Exception as e:
        db.session.rollback()
        print(f"Error en la migración, rollback aplicado: {e}")
        raise
