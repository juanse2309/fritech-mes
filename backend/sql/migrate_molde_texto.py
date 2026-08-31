"""
Migración: db_programacion.molde y db_inyeccion.molde de INTEGER a VARCHAR(50).

Contexto (2026-08-28): el catálogo real de moldes (`rel_producto_molde`,
314 códigos distintos verificados) NO es numérico -- incluye códigos como
'5002A' o '9304 moneda'. El campo "Molde" del formulario de Programación
dejó de ser una capacidad que se compara contra la suma de cavidades del
montaje (esa regla se elimina en el mismo cambio) y pasa a ser el código
real del molde físico que la tercera persona del equipo de programación
elige del catálogo.

Sin esta migración, cualquier código con letras se guardaría corrompido:
las conversiones actuales (`to_int()`, `int(float(...))`) devuelven 0 en
silencio ante un valor no numérico -- no truncan con error, lo pierden sin
avisar. Ver inyeccion_service.py y programacion_service.py (mismo commit)
para el resto del cambio.

USING molde::text conserva los valores enteros existentes como su
representación en texto -- no se pierde ningún dato ya guardado.

No destructiva: ALTER COLUMN es reversible (texto -> entero solo fallaría
si ya se guardó un código con letras, que es justo el caso que se está
habilitando). Solo corre si el tipo actual todavía es integer -- correr dos
veces es un no-op seguro.

Descubierto al correrla la primera vez: db_programacion tiene el índice
único `uq_programacion_diaria` sobre (fecha, maquina, codigo_sistema,
COALESCE(molde, 0)) -- el 0 literal es entero y bloquea el ALTER (Postgres
no puede convertir esa expresión sola). Hay que tumbar el índice antes del
ALTER y recrearlo con COALESCE(molde, '') después, sobre la MISMA
combinación de columnas -- es la unicidad que usa el UPSERT de
crear_programacion/guardar_programacion para no duplicar la fila del día.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

TABLAS = [
    ('db_programacion', 'molde'),
    ('db_inyeccion', 'molde'),
]

INDICE_PROGRAMACION = 'uq_programacion_diaria'

with app.app_context():
    try:
        indice_existia = db.session.execute(text("""
            SELECT 1 FROM pg_indexes WHERE indexname = :nombre
        """), {'nombre': INDICE_PROGRAMACION}).scalar() is not None

        if indice_existia:
            db.session.execute(text(f"DROP INDEX {INDICE_PROGRAMACION}"))
            print(f"Índice {INDICE_PROGRAMACION} eliminado temporalmente (depende de molde como entero).")

        for tabla, columna in TABLAS:
            tipo_actual = db.session.execute(text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = :tabla AND column_name = :columna
            """), {'tabla': tabla, 'columna': columna}).scalar()

            if tipo_actual is None:
                print(f"{tabla}.{columna} no existe -- se omite (¿tabla nueva sin esa columna todavía?).")
                continue

            if tipo_actual == 'character varying':
                print(f"{tabla}.{columna} ya es VARCHAR -- no-op.")
                continue

            db.session.execute(text(f"""
                ALTER TABLE {tabla}
                ALTER COLUMN {columna} TYPE VARCHAR(50)
                USING {columna}::text
            """))
            print(f"{tabla}.{columna}: {tipo_actual} -> VARCHAR(50) migrado.")

        if indice_existia:
            db.session.execute(text(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {INDICE_PROGRAMACION}
                ON db_programacion (fecha, maquina, codigo_sistema, COALESCE(molde, ''))
            """))
            print(f"Índice {INDICE_PROGRAMACION} recreado con COALESCE(molde, '').")

        db.session.commit()
        print("Migración exitosa.")

    except Exception as e:
        db.session.rollback()
        print(f"Error en la migración, rollback aplicado: {e}")
        raise
