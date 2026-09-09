"""
Migración: crea db_inyeccion_lecturas_parciales.

Reporte parcial de avance de Inyección (pedido del usuario 2026-09-04):
además de Iniciar/Finalizar, el operario puede dejar una lectura del
contador a media jornada (normalmente 11am y 3pm) sin cerrar el lote.
Es solo una bitácora de auditoría -- NO toca db_inyeccion (cant_contador,
cantidad_real, estado), por lo que no afecta en nada a Validación ni al
export a WO, que siguen dependiendo exclusivamente del cierre real
(InyeccionService.reportar_trabajo -> validar_lote).

No destructiva: CREATE TABLE IF NOT EXISTS, no toca ninguna tabla existente.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS db_inyeccion_lecturas_parciales (
                id_lectura        SERIAL PRIMARY KEY,
                id_inyeccion      VARCHAR(80) NOT NULL,
                maquina           VARCHAR(80),
                molde             VARCHAR(50),
                orden_produccion  VARCHAR(100),
                cierres_lectura   BIGINT NOT NULL,
                responsable       VARCHAR(150),
                creado_por        VARCHAR(150),
                fecha_hora        TIMESTAMP NOT NULL
            );
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_inyeccion_lecturas_parciales_id_inyeccion
                ON db_inyeccion_lecturas_parciales (id_inyeccion);
        """))

        db.session.commit()
        print("Migración exitosa: 'db_inyeccion_lecturas_parciales' creada.")
    except Exception as e:
        db.session.rollback()
        print("Error en migración:", e)
