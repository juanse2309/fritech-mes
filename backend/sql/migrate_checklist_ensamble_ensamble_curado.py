"""
Migración: separa el proceso "Ensamble" del checklist en dos.

Hasta ahora db_checklist_ensamble.ensamble_estado representaba un único
proceso "Ensamble" (el único que siempre aplica, para productos simples).
En planta en realidad hay dos armados distintos:
  - "Ensamble Crudo": el de siempre, con la pieza sin curar.
  - "Ensamble" (a secas): un segundo armado que se hace DESPUÉS de Horno 1,
    con la pieza ya curada.

Esta migración:
  1. Renombra la columna existente `ensamble_estado` -> `ensamble_crudo_estado`
     (preserva el histórico ya marcado -- lo que se venía llamando "Ensamble"
     era, en efecto, el crudo).
  2. Agrega una columna `ensamble_estado` nueva, en PENDIENTE, para el
     segundo armado post-Horno 1.

No destructiva: no borra filas ni datos, solo renombra/agrega columnas.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        # Defensivo: si ya se corrió esta migración (ensamble_crudo_estado ya
        # existe), no reintenta el RENAME -- fallaría porque la columna vieja
        # ensamble_estado ya no existe con ese nombre.
        ya_migrado = db.session.execute(text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'db_checklist_ensamble' AND column_name = 'ensamble_crudo_estado'
        """)).fetchone()

        if ya_migrado:
            print("Migración ya aplicada: 'ensamble_crudo_estado' ya existe. No-op.")
        else:
            db.session.execute(text("""
                ALTER TABLE db_checklist_ensamble
                    RENAME COLUMN ensamble_estado TO ensamble_crudo_estado;
            """))
            print("Columna renombrada: ensamble_estado -> ensamble_crudo_estado.")

        db.session.execute(text("""
            ALTER TABLE db_checklist_ensamble
                ADD COLUMN IF NOT EXISTS ensamble_estado VARCHAR(20) DEFAULT 'PENDIENTE';
        """))

        db.session.commit()
        print("Migración exitosa.")
    except Exception as e:
        db.session.rollback()
        print("Error en migración:", e)
