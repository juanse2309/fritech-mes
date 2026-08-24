-- Script para la tabla de programación de ensamble
CREATE TABLE IF NOT EXISTS db_programacion_ensamble (
    id_prog SERIAL PRIMARY KEY,
    id_codigo VARCHAR(50) NOT NULL,
    cantidad_objetivo INTEGER NOT NULL,
    cantidad_realizada INTEGER DEFAULT 0,
    fecha_programada DATE NOT NULL,
    estado VARCHAR(20) DEFAULT 'PENDIENTE' -- PENDIENTE, EN_PROCESO, COMPLETADO
);

-- Index para búsquedas rápidas
CREATE INDEX IF NOT EXISTS idx_prog_ensamble_codigo ON db_programacion_ensamble(id_codigo);
CREATE INDEX IF NOT EXISTS idx_prog_ensamble_estado ON db_programacion_ensamble(estado);

-- Único parcial: evita duplicar metas activas (PENDIENTE/EN_PROCESO) para el
-- mismo producto/fecha/OP, pero excluye las COMPLETADO para que una meta ya
-- completada no pueda reabrirse por UPSERT arrastrando su cantidad_realizada
-- vieja -- ver backend/sql/migrate_programacion_ensamble_unique_activa.py.
CREATE UNIQUE INDEX IF NOT EXISTS uq_programacion_ensamble_activa
    ON db_programacion_ensamble (fecha_programada, id_codigo, COALESCE(op_numero, ''))
    WHERE estado <> 'COMPLETADO';
