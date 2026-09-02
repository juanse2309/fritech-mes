"""
Migración: crea db_programacion_pulido -- cola diaria de Programación de
Pulido (plan 2026-09-02, ver ProgramacionPulido en sql_models.py). El ADMIN
arma para cada operaria qué pulir hoy y en qué orden (OP + referencia +
cantidad objetivo + prioridad); la operaria lo ve como tarjetas en "Modo
Programado". `id_pulido` vincula la fila con db_pulido una vez que la
operaria le da "Iniciar" a la tarjeta.

No destructiva: solo CREATE TABLE IF NOT EXISTS + índices IF NOT EXISTS.
Correr dos veces es un no-op seguro.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS db_programacion_pulido (
                id                  SERIAL PRIMARY KEY,
                fecha               DATE         NOT NULL,
                orden_produccion    VARCHAR(100) NOT NULL,
                codigo              VARCHAR(100) NOT NULL,
                lote                VARCHAR(100),
                cantidad_objetivo   NUMERIC(18, 2) DEFAULT 0,
                operaria            VARCHAR(200) NOT NULL,
                orden_prioridad     INTEGER      DEFAULT 1,
                estado              VARCHAR(30)  NOT NULL DEFAULT 'PROGRAMADO',
                responsable_planta  VARCHAR(150),
                observaciones       TEXT,
                id_pulido           VARCHAR(100),
                creado_en           TIMESTAMP    NOT NULL DEFAULT NOW()
            );
        """))
        print("Tabla 'db_programacion_pulido' verificada/creada.")

        # ALTER idempotente para bases donde la tabla ya existía sin 'lote'
        # (columna agregada después, plan 2026-09-02 v2: la operaria copia
        # referencia/OP/lote directo de la bolsa física).
        db.session.execute(text("ALTER TABLE db_programacion_pulido ADD COLUMN IF NOT EXISTS lote VARCHAR(100);"))
        print("Columna 'lote' verificada.")

        for nombre, columna in [
            ('ix_prog_pulido_fecha', 'fecha'),
            ('ix_prog_pulido_orden_produccion', 'orden_produccion'),
            ('ix_prog_pulido_codigo', 'codigo'),
            ('ix_prog_pulido_operaria', 'operaria'),
            ('ix_prog_pulido_id_pulido', 'id_pulido'),
        ]:
            db.session.execute(text(f"CREATE INDEX IF NOT EXISTS {nombre} ON db_programacion_pulido ({columna});"))
        print("Índices verificados/creados.")

        db.session.commit()
        print("Migración exitosa.")

    except Exception as e:
        db.session.rollback()
        print(f"Error en la migración, rollback aplicado: {e}")
        raise
