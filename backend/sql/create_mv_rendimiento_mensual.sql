-- Vista materializada: pre-agrega db_ventas por (año, mes) para el comparativo
-- mensual de Ventas vs Pedidos (Rendimiento / Análisis Comparativo Anual) del
-- panel de Jefatura, evitando el SUM(CASE WHEN clasificacion ILIKE ...) sobre
-- 100k+ filas de db_ventas en cada carga del dashboard sin filtro de fecha.
--
-- Se agrega para TODOS los años presentes en db_ventas (no solo el año actual),
-- así el repositorio decide en Python qué año_actual/año_previo mostrar sin
-- tener que recrear la vista cada cambio de año.
--
-- Se refresca con REFRESH MATERIALIZED VIEW CONCURRENTLY al terminar cada
-- sincronización exitosa de World Office — ver backend/routes/wo_routes.py
-- (_refrescar_mv_dashboard_ventas).
--
-- Cuando el usuario filtra el panel de Jefatura por rango de fechas específico,
-- el repositorio (dashboard_repository.py) NO usa esta vista (no tiene
-- granularidad diaria) y cae al camino en vivo sobre db_ventas para no romper
-- el filtro parcial de mes.
-- NOT ILIKE FRIPARTS: mismo criterio de exclusion que mv_dashboard_ventas_analitica
-- (fuente de Backorder/Top/Peores). Sin esto, pedidos/ventas facturados bajo un
-- nombre que contiene 'FRIPARTS' se contaban en el comparativo Pedidos vs Ventas
-- pero quedaban invisibles para el Backorder, generando una brecha entre ambos
-- paneles que nunca podia reconciliar.
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_rendimiento_mensual AS
SELECT
    EXTRACT(YEAR FROM fecha)::INTEGER AS ano,
    EXTRACT(MONTH FROM fecha)::INTEGER AS mes,
    SUM(CASE WHEN clasificacion ILIKE '%venta%'  THEN COALESCE(total_ingresos, 0) ELSE 0 END) AS ventas_dinero,
    SUM(CASE WHEN clasificacion ILIKE '%pedido%' THEN COALESCE(total_ingresos, 0) ELSE 0 END) AS pedidos_dinero,
    SUM(CASE WHEN clasificacion ILIKE '%venta%'  THEN COALESCE(cantidad, 0) ELSE 0 END) AS ventas_unidades,
    SUM(CASE WHEN clasificacion ILIKE '%pedido%' THEN COALESCE(cantidad, 0) ELSE 0 END) AS pedidos_unidades
FROM db_ventas
WHERE fecha IS NOT NULL
  AND UPPER(TRIM(nombres)) NOT ILIKE '%FRIPARTS%'
GROUP BY 1, 2;

-- Obligatorio para poder usar REFRESH MATERIALIZED VIEW CONCURRENTLY.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_rendimiento_mensual_pk
    ON mv_rendimiento_mensual (ano, mes);
