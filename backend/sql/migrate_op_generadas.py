"""
Migración: crea db_op_generadas -- el numerador de OP (reunión 2026-08-25,
corte 31-ago-2026).

Contexto: FRITECH pasa a asignar el número de OP en vez de que un humano lo
teclee tras crearla a mano en World Office. Esta tabla es el registro local
de "qué número le tocó a cada OP que FRITECH generó" -- necesaria porque
db_op_wo_staging se sobreescribe completo cada ~15 min (truncate+insert
desde agente_wo_comercial.py) y no sirve como registro persistente de lo
que FRITECH generó entre una sincronización y la siguiente.

El índice único parcial uq_op_generada_dia_ambito es lo que hace la reserva
IDEMPOTENTE por (fecha_produccion, ambito, máquina): dos llamados para la
misma combinación devuelven la MISMA fila en vez de crear una segunda. Esto
es necesario porque:
  - hay dos rutas de programación de inyección (guardar_programacion nueva
    y crear_programacion legacy) escribiendo en la misma db_programacion,
    y ambas deben converger en la misma OP sin coordinarse entre sí.
  - empaque reserva "la OP del día" con el primer reporte del día (reserva
    perezosa) y los siguientes reportes del mismo día deben caer en la
    misma OP.

COALESCE(maquina,'') en el índice: máquina es NULL en ensamble y empaque
(no aplica), y NULL no participa en unicidad por defecto en Postgres --
sin el COALESCE, dos OP de ENSAMBLE el mismo día no chocarían entre sí
porque ambas máquina=NULL se tratarían como "distintas". El WHERE estado
<> 'ANULADA' permite reabrir la clave si una OP se anula.

No destructiva: solo CREATE TABLE / CREATE INDEX, ambos IF NOT EXISTS.
Correr dos veces es un no-op seguro.
"""
from backend.core.sql_database import db
from backend.app import app
from sqlalchemy import text

with app.app_context():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS db_op_generadas (
                id                SERIAL PRIMARY KEY,
                prefijo           VARCHAR(20)  NOT NULL,
                consecutivo       BIGINT       NOT NULL,
                numero_op         VARCHAR(50)  NOT NULL UNIQUE,
                ambito            VARCHAR(20)  NOT NULL,
                maquina           VARCHAR(80),
                fecha_produccion  DATE         NOT NULL,
                estado            VARCHAR(20)  NOT NULL DEFAULT 'RESERVADA',
                creado_por        VARCHAR(150),
                creado_en         TIMESTAMP    NOT NULL DEFAULT NOW(),
                exportada_por     VARCHAR(150),
                exportada_en      TIMESTAMP,
                confirmada_en     TIMESTAMP,
                anulada_motivo    TEXT
            );
        """))
        print("Tabla 'db_op_generadas' verificada/creada.")

        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_op_generadas_prefijo
                ON db_op_generadas (prefijo);
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_op_generadas_ambito
                ON db_op_generadas (ambito);
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_op_generadas_fecha_produccion
                ON db_op_generadas (fecha_produccion);
        """))
        print("Índices simples verificados/creados.")

        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_op_generada_dia_ambito
                ON db_op_generadas (fecha_produccion, ambito, COALESCE(maquina, ''))
                WHERE estado <> 'ANULADA';
        """))
        print("Índice único parcial 'uq_op_generada_dia_ambito' verificado/creado.")

        db.session.commit()
        print("Migración exitosa.")

    except Exception as e:
        db.session.rollback()
        print(f"Error en la migración, rollback aplicado: {e}")
        raise
