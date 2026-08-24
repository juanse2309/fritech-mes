"""
Migración: reemplaza el índice único de db_programacion_ensamble por uno
parcial que excluye las filas COMPLETADO.

Bug que corrige: EnsambleService.crear_o_actualizar_programacion hace un
UPSERT sobre (fecha_programada, id_codigo, COALESCE(op_numero, '')). Con el
índice único original (sin condición), programar el mismo producto sin OP
el mismo día en que ya existe una meta COMPLETADA con esa clave reabre esa
fila: el UPSERT solo actualiza cantidad_objetivo y estado, nunca
cantidad_realizada, que sigue arrastrando la producción de la meta anterior
(ej. objetivo=1 / realizado=50 -> "5000%").

Resetear cantidad_realizada en el UPSERT no alcanza: reportar_multi
recalcula esa columna sumando db_ensambles.cantidad filtrado por id_prog
(ver migrate_ensamble_add_id_prog.py). Como el UPSERT reabre la MISMA fila
(mismo id_prog), el próximo reporte contra ella vuelve a sumar la
producción histórica de la meta anterior. La única forma de mantener el
historial de producción separado por meta es que una meta COMPLETADA nunca
sea candidata al UPSERT -- debe crearse una fila nueva, con su propio
id_prog.

Este índice parcial logra eso sin tocar la lógica de negocio: al excluir
estado='COMPLETADO' de la unicidad, un INSERT ON CONFLICT que solo choque
contra una fila completada no encuentra conflicto y crea una fila nueva en
su lugar (ver el index_where agregado en
EnsambleService.crear_o_actualizar_programacion). Sí sigue previniendo
duplicados entre metas activas (PENDIENTE/EN_PROCESO) del mismo
producto/fecha/OP, que es el caso que el índice original protegía.

No destructiva más allá de reemplazar el índice: no borra ni modifica filas
de datos.

NOTA: esta migración ya se ejecutó contra la base compartida (fritech_db)
en una sesión paralela -- el índice `uq_programacion_ensamble_activa` ya
existe. Este archivo se agrega igual para que el historial de migraciones
de este árbol de trabajo quede consistente con el de esa rama; volver a
correrlo es un no-op seguro (CREATE UNIQUE INDEX IF NOT EXISTS).
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        # Descubre el/los índices únicos existentes sobre esa combinación de
        # columnas (el nombre en el código de referencia es
        # 'uq_programacion_ensamble', pero se busca dinámicamente por si el
        # nombre real en esta base difiere).
        rows = db.session.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'db_programacion_ensamble'
              AND indexdef ILIKE '%UNIQUE%'
              AND indexdef ILIKE '%fecha_programada%'
              AND indexdef ILIKE '%id_codigo%'
              AND indexdef ILIKE '%op_numero%'
        """)).fetchall()

        for (indexname,) in rows:
            db.session.execute(text(f'DROP INDEX IF EXISTS "{indexname}"'))
            print(f"Índice único eliminado: {indexname}")

        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_programacion_ensamble_activa
                ON db_programacion_ensamble (fecha_programada, id_codigo, COALESCE(op_numero, ''))
                WHERE estado <> 'COMPLETADO';
        """))

        db.session.commit()
        print("Migración exitosa: índice único parcial 'uq_programacion_ensamble_activa' creado.")
    except Exception as e:
        db.session.rollback()
        print("Error en migración:", e)
