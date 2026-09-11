-- db_ventas_staging no está modelada en el ORM (sql_models.py) y no tiene
-- CREATE propio en ningún lado -- solo se le hace TRUNCATE/INSERT en
-- WoSyncService (sincronización comercial con World Office) y se le agregan
-- columnas por ALTER TABLE en app.py (líneas 163-165), asumiendo que ya
-- existe. Una instancia nueva (ej. FRIMETALS standalone) necesita este
-- CREATE manual una sola vez.
--
-- Mismo esquema que db_ventas (sin id ni índices -- es tabla de paso, se
-- trunca en cada sincronización comercial), ver DbVentas en sql_models.py.
--
-- IMPORTANTE: mientras esta tabla no exista, el ALTER TABLE de app.py sobre
-- ella sigue fallando en cada arranque y aborta el bloque completo de DDL de
-- arranque ANTES de llegar al bootstrap de la secuencia 'pedido_id_seq'
-- (backend/app.py líneas 171-183) -- eso bloquea generar_siguiente_id_pedido
-- (registrar pedidos nuevos falla con "relation pedido_id_seq does not
-- exist"). Correr este script junto con create_cartera_wo.sql es requisito
-- para que Pedidos funcione de punta a punta en una instancia nueva.
CREATE TABLE IF NOT EXISTS db_ventas_staging (
    fecha                   DATE,
    documento               VARCHAR(80),
    nombres                 VARCHAR(200),
    productos               VARCHAR(100),
    cantidad                NUMERIC(18, 2) DEFAULT 0,
    total_ingresos          NUMERIC(18, 2) DEFAULT 0,
    precio_promedio         NUMERIC(18, 2) DEFAULT 0,
    clasificacion           VARCHAR(80),
    vendedor                VARCHAR(150),
    zona                    VARCHAR(100),
    descripcion_producto    VARCHAR(255),
    iva                     NUMERIC(18, 2),
    identificacion_cliente  VARCHAR(50)
);
