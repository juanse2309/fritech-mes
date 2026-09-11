-- db_precio_venta no está modelada en el ORM (sql_models.py), por lo que
-- db.create_all() nunca la crea -- es una tabla legado creada a mano en
-- Render/FriParts, igual que cartera_wo. Una instancia nueva (ej. FRIMETALS
-- standalone) necesita este CREATE manual una sola vez.
--
-- Nada en el código escribe en esta tabla -- solo se lee en
-- InventarioService.buscar_catalogo_con_precio() para normalizar precio por
-- prefijo. El JOIN ya hace COALESCE(pv1.precio, pv2.precio, p.precio, 0),
-- así que puede quedar vacía: simplemente cae al precio de db_productos.
CREATE TABLE IF NOT EXISTS db_precio_venta (
    codigo VARCHAR PRIMARY KEY,
    precio NUMERIC
);
