-- Esquema real, tomado del UPSERT que la escribe --
-- VentasRepository.upsert_cartera_wo (backend/repositories/ventas_repository.py):
--
--   INSERT INTO cartera_wo (documento, identificacion, nombre, vendedor,
--       moneda, empresa, fecha_emision, fecha_vencimiento, saldo_documento,
--       ultima_actualizacion)
--   ...
--   ON CONFLICT (documento) DO UPDATE SET ...
--
-- La versión anterior de este script (identificacion como PRIMARY KEY, sin
-- documento/vendedor/fecha_vencimiento/saldo_documento) estaba incompleta y
-- con la llave primaria equivocada -- un mismo cliente (identificacion)
-- tiene varios documentos (facturas) pendientes, así que la llave natural es
-- 'documento', no 'identificacion'. Con el esquema viejo, cualquier consulta
-- que hiciera JOIN sobre saldo_documento/fecha_vencimiento (ventas_repository.
-- get_pedidos_pendientes, cartera_service.py) fallaba con UndefinedColumn --
-- atrapado en silencio por el try/except de esas funciones, así que el
-- síntoma era una lista vacía sin ningún error visible, no un 500.
CREATE TABLE IF NOT EXISTS cartera_wo (
    documento           VARCHAR PRIMARY KEY,
    identificacion      VARCHAR,
    nombre              VARCHAR,
    vendedor            VARCHAR,
    moneda              VARCHAR,
    empresa             VARCHAR,
    fecha_emision       DATE,
    fecha_vencimiento   DATE,
    saldo_documento     NUMERIC,
    ultima_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
