"""
Migración: crea db_empaque -- reporte de Empaque (reunión 2026-08-25).

Contexto: hoy Empaque se anota en hojas de papel y alguien lo transcribe
después a World Office. db_empaque es el registro digital: referencia +
cantidad armada, sin meta ni programación (nadie programa Empaque, el
trabajo lo dicta el pedido -- ver EmpaqueService.reportar).

No destructiva: solo CREATE TABLE IF NOT EXISTS + índices IF NOT EXISTS.
Correr dos veces es un no-op seguro.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS db_empaque (
                id              SERIAL PRIMARY KEY,
                id_empaque      VARCHAR(100) NOT NULL UNIQUE,
                fecha           DATE         NOT NULL,
                fecha_registro  TIMESTAMP    NOT NULL DEFAULT NOW(),
                id_codigo       VARCHAR(50)  NOT NULL,
                cantidad        INTEGER      NOT NULL,
                responsable     VARCHAR(150),
                op_numero       VARCHAR(100),
                observaciones   TEXT
            );
        """))
        print("Tabla 'db_empaque' verificada/creada.")

        for nombre, columna in [
            ('ix_empaque_fecha', 'fecha'),
            ('ix_empaque_id_codigo', 'id_codigo'),
            ('ix_empaque_op_numero', 'op_numero'),
        ]:
            db.session.execute(text(f"CREATE INDEX IF NOT EXISTS {nombre} ON db_empaque ({columna});"))
        print("Índices verificados/creados.")

        db.session.commit()
        print("Migración exitosa.")

    except Exception as e:
        db.session.rollback()
        print(f"Error en la migración, rollback aplicado: {e}")
        raise
