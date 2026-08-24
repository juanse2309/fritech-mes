"""
Migración: crea db_checklist_ensamble.

Checklist de procesos de planta (Ensamble, Rayada Carcaza, Rayada Interno,
Pintura, Horno 1, Cerrada, Horno 2) por producto programado. Una fila por
id_prog (FK lógica hacia db_programacion_ensamble.id_prog, sin constraint
formal -- mismo patrón que id_prog en db_ensambles). Cada columna de
proceso vale PENDIENTE, HECHO o NO_APLICA; todas nacen en PENDIENTE.

No destructiva: CREATE TABLE IF NOT EXISTS. La fila de un id_prog se crea
de forma perezosa (upsert) la primera vez que se guarda un reporte contra
esa meta, no al programarla -- si nunca se le llega a reportar, no acumula
filas huérfanas.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS db_checklist_ensamble (
                id_checklist            SERIAL PRIMARY KEY,
                id_prog                 INTEGER NOT NULL,
                ensamble_estado         VARCHAR(20) DEFAULT 'PENDIENTE',
                rayada_carcaza_estado   VARCHAR(20) DEFAULT 'PENDIENTE',
                rayada_interno_estado   VARCHAR(20) DEFAULT 'PENDIENTE',
                pintura_estado          VARCHAR(20) DEFAULT 'PENDIENTE',
                horno1_estado           VARCHAR(20) DEFAULT 'PENDIENTE',
                cerrada_estado          VARCHAR(20) DEFAULT 'PENDIENTE',
                horno2_estado           VARCHAR(20) DEFAULT 'PENDIENTE',
                actualizado_en          TIMESTAMP,
                actualizado_por         TEXT
            );
        """))
        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_checklist_ensamble_id_prog
                ON db_checklist_ensamble (id_prog);
        """))

        db.session.commit()
        print("Migración exitosa: 'db_checklist_ensamble' creada.")
    except Exception as e:
        db.session.rollback()
        print("Error en migración:", e)
