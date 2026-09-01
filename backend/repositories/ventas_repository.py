"""
Repositorio de Pedidos/Ventas 100% SQL-First.
Centraliza el acceso a db_pedidos, db_ventas, db_costos (costeo) y cartera_wo.
"""
import logging
import calendar
from sqlalchemy import text
from backend.core.sql_database import db, rollback_seguro
from backend.utils.numeric_helpers import _num

logger = logging.getLogger(__name__)


class VentasRepository:
    """Repositorio para operaciones de Pedidos/Ventas vía SQL crudo."""

    @staticmethod
    def get_pedidos_pendientes():
        """
        Obtiene pedidos pendientes para Almacén con mapeo robusto.
        """
        try:
            # LEFT JOIN informativo con cartera_wo: no bloquea ni condiciona el listado,
            # solo anexa el saldo vencido del cliente (por NIT) para que Sofía/Andrés
            # lo vean en la tarjeta del pedido antes de despachar.
            sql = """
                SELECT
                    p.id, p.id_pedido, p.fecha, p.hora, p.estado, p.delegado_a, p.vendedor,
                    p.nit, p.cliente, p.direccion, p.ciudad, p.id_codigo, p.descripcion,
                    p.cantidad, p.precio_unitario, p.total, p.observaciones, p.progreso,
                    p.progreso_despacho, p.cant_alistada,
                    cw.saldo_vencido_total
                FROM db_pedidos p
                LEFT JOIN (
                    SELECT identificacion, SUM(saldo_documento) AS saldo_vencido_total
                    FROM cartera_wo
                    WHERE fecha_vencimiento < CURRENT_DATE AND saldo_documento > 0
                    GROUP BY identificacion
                ) cw ON cw.identificacion = regexp_replace(
                    regexp_replace(upper(trim(p.nit)), '^(NIT|CC)\\s+', ''),
                    '\\s+\\d+$', ''
                )
                WHERE p.estado NOT IN ('COMPLETADO', 'DESPACHADO', 'ENTREGADO', 'FACTURADO', 'CANCELADO')
                  AND p.estado IS NOT NULL
                ORDER BY p.fecha ASC, p.id_pedido ASC
            """
            rows = db.session.execute(text(sql)).mappings().all()

            agrupados = {}
            for r in rows:
                nro_pedido = str(r['id_pedido'] or '').strip()
                if not nro_pedido: continue

                if nro_pedido not in agrupados:
                    # Formateo de dirección/ciudad para el frontend
                    dir_raw = str(r['direccion'] or 'S/D').strip()
                    ciu_raw = str(r['ciudad'] or 'S/C').strip()
                    dir_final = f"{dir_raw} - {ciu_raw}" if ciu_raw and ciu_raw.upper() != 'S/C' else dir_raw

                    agrupados[nro_pedido] = {
                        "id": nro_pedido,
                        "id_pedido": nro_pedido,
                        "nro_pedido": nro_pedido,
                        "fecha_creacion": f"{str(r['fecha'])[:10]} {str(r['hora']).strip()}" if r['fecha'] else '',
                        "fecha": str(r['fecha'])[:10] if r['fecha'] else '',
                        "hora": str(r['hora'] or '').strip(),
                        "cliente": str(r['cliente'] or 'Cliente Desconocido').strip(),
                        "nit": str(r['nit'] or 'S/N').strip(),
                        "direccion": dir_final,
                        "ciudad": ciu_raw,
                        "vendedor": str(r['vendedor'] or '').strip(),
                        "estado": str(r['estado'] or 'PENDIENTE').strip().upper(),
                        "delegado_a": str(r['delegado_a'] or '').strip(),
                        "observaciones": str(r['observaciones'] or '').strip(),
                        "progreso": str(r['progreso'] or '0').replace('%', ''),
                        "progreso_despacho": str(r['progreso_despacho'] or '0').replace('%', ''),
                        "saldo_vencido_total": float(r['saldo_vencido_total'] or 0),
                        "productos": []
                    }

                # Mapeo de producto individual (ID CODIGO)
                codigo = str(r['id_codigo'] or '').strip().upper()

                # Limpieza de cantidades (manejo de strings o nulls)
                def _parse_num(v):
                    if not v: return 0.0
                    try: return float(str(v).replace('$', '').replace(',', '').strip())
                    except: return 0.0

                agrupados[nro_pedido]["productos"].append({
                    "id_sql": r['id'],
                    "codigo": codigo,
                    "id_codigo": codigo,
                    "descripcion": str(r['descripcion'] or '').strip(),
                    "cantidad": _parse_num(r['cantidad']),
                    "precio_unitario": _parse_num(r['precio_unitario']),
                    "total": _parse_num(r['total']),
                    "cant_alistada": str(r['cant_alistada'] or '0'),
                    "cant_lista": str(r['cant_alistada'] or '0')
                })

            return list(agrupados.values())
        except Exception as e:
            rollback_seguro()
            logger.error(f"[VentasRepository.get_pedidos_pendientes] ERROR: {e}")
            return []

    @staticmethod
    def get_desglose_mensual_ventas(mes, anio, tipo_vista='money'):
        """
        Obtiene el desglose de ventas de un mes y año exactos en dos dimensiones:
        - "productos": agrupado por referencia (comportamiento histórico).
        - "clientes": agrupado por nombre de cliente, para seguimiento comercial de metas.
        Ambas comparten el mismo periodo (misma CTE VentasPeriodo, mismos params), así que
        sus totales son consistentes entre sí — no son dos fuentes que puedan divergir.

        Devuelve: {"productos": [...], "clientes": [...]}

        Fail-closed: 'mes'/'anio' inválidos lanzan ValueError (el controlador lo traduce a
        HTTP 400) ANTES de tocar la base de datos. El WHERE fecha >= :start_date AND
        fecha <= :end_date de la consulta es literal en el SQL, nunca condicional — pero
        un 'mes' corrupto/fuera de rango que pasara sin validar produciría igualmente un
        rango de fechas válido (via calendar.monthrange) sobre un mes equivocado, sin que
        la consulta fallara ni delatara el error.
        """
        try:
            mes_int = int(mes)
            anio_int = int(anio)
        except (TypeError, ValueError):
            raise ValueError(f"'mes'/'anio' deben ser enteros. Recibido: mes={mes!r}, anio={anio!r}")

        if not (1 <= mes_int <= 12):
            raise ValueError(f"'mes' fuera de rango (1-12): {mes_int}")
        if not (2000 <= anio_int <= 2100):
            raise ValueError(f"'anio' fuera de rango válido: {anio_int}")

        try:
            def _sql_cast_num(col):
                # Columna ya NUMERIC nativo: COALESCE directo evita el bug de perder
                # el signo '-' de devoluciones/NC que tenia el cast via texto+regex.
                return f"COALESCE({col}, 0)"

            # Optimización de fechas para uso de índices
            last_day = calendar.monthrange(anio_int, mes_int)[1]
            start_date = f"{anio_int}-{mes_int:02d}-01"
            end_date = f"{anio_int}-{mes_int:02d}-{last_day}"
            params = {'start_date': start_date, 'end_date': end_date}

            # Determinar columna de ordenamiento
            order_col = 'total_ventas' if tipo_vista == 'money' else 'unidades'

            # CTE compartida por ambas dimensiones — mismo período, misma fuente. Se repite
            # en cada consulta (SQLAlchemy `text()` no permite reutilizar una CTE ya
            # preparada entre dos executes), pero el WHERE de fecha es idéntico en ambas,
            # así que "productos" y "clientes" nunca pueden reportar totales inconsistentes.
            cte_ventas_periodo = """
                WITH VentasPeriodo AS (
                    SELECT
                        productos as raw_string,
                        nombres,
                        CASE
                            WHEN productos LIKE '%|%' THEN TRIM(SPLIT_PART(productos, '|', 1))
                            WHEN productos LIKE '% %' THEN TRIM(SPLIT_PART(productos, ' ', 1))
                            ELSE TRIM(productos)
                        END as raw_codigo,

                        CASE
                            WHEN productos LIKE '%|%' THEN TRIM(SPLIT_PART(productos, '|', 2))
                            WHEN productos LIKE '% %' THEN TRIM(SUBSTRING(productos FROM POSITION(' ' IN productos) + 1))
                            ELSE ''
                        END as desc_raw,

                        cantidad,
                        total_ingresos
                    FROM db_ventas
                    WHERE CAST(fecha AS DATE) >= :start_date AND CAST(fecha AS DATE) <= :end_date
                      AND (clasificacion ILIKE '%venta%' OR clasificacion IS NULL)
                )
            """

            # BUGFIX: el LEFT JOIN anterior (`v.raw_codigo = p.id_codigo OR v.raw_codigo =
            # p.codigo_sistema`) podia matchear MAS DE UNA fila de db_productos para el mismo
            # raw_codigo (ej. un codigo que coincide con id_codigo de un producto Y con
            # codigo_sistema de otro). Eso duplicaba las filas de VentasPeriodo ANTES del
            # SUM (fan-out de join), inflando total_ventas/unidades de ese codigo tantas veces
            # como productos matcheara -- confirmado en produccion con 'FR-5002B' (2 matches),
            # que inflaba el total de julio 2026 en +$27,500. Se resuelve agregando primero por
            # raw_codigo (sin ningun join, imposible de duplicar) y solo despues enriqueciendo
            # la descripcion con un LEFT JOIN a una subconsulta deduplicada (DISTINCT ON) que
            # garantiza como maximo 1 fila por codigo.
            sql_productos = text(cte_ventas_periodo + f"""
                , VentasPorCodigo AS (
                    SELECT
                        v.raw_codigo as id_codigo,
                        MAX(NULLIF(v.desc_raw, '')) as desc_raw,
                        MAX(v.raw_string) as raw_string,
                        SUM({_sql_cast_num('v.cantidad')}) as unidades,
                        SUM({_sql_cast_num('v.total_ingresos')}) as total_ventas
                    FROM VentasPeriodo v
                    GROUP BY v.raw_codigo
                    HAVING SUM({_sql_cast_num('v.cantidad')}) > 0
                ),
                ProductosDedup AS (
                    SELECT DISTINCT ON (codigo) codigo, descripcion
                    FROM (
                        SELECT id_codigo as codigo, descripcion FROM db_productos WHERE id_codigo IS NOT NULL
                        UNION ALL
                        SELECT codigo_sistema as codigo, descripcion FROM db_productos WHERE codigo_sistema IS NOT NULL
                    ) candidatos
                    ORDER BY codigo
                )
                SELECT
                    vc.id_codigo,
                    COALESCE(
                        pd.descripcion,
                        NULLIF(vc.desc_raw, ''),
                        vc.raw_string
                    ) as descripcion,
                    vc.unidades,
                    vc.total_ventas
                FROM VentasPorCodigo vc
                LEFT JOIN ProductosDedup pd ON vc.id_codigo = pd.codigo
                ORDER BY {order_col} DESC
            """)
            rows_productos = db.session.execute(sql_productos, params).mappings().all()

            # Mismo alias hardcoded que _get_admin_dashboard_metrics_sql_impl (Backorder):
            # 'DISTRIBUJES Y CAUCHOS FC SAS' factura bajo variantes de nombre distintas al
            # cliente real. Sin este CASE, "Top Clientes" partiría sus compras en dos filas.
            sql_clientes = text(cte_ventas_periodo + f"""
                SELECT
                    CASE WHEN UPPER(TRIM(v.nombres)) ILIKE '%DISTRIBUJES%' THEN 'FELIPE DUARTE MORENO'
                         ELSE COALESCE(NULLIF(TRIM(v.nombres), ''), 'Cliente Desconocido')
                    END as nombre_cliente,
                    SUM({_sql_cast_num('v.cantidad')}) as unidades,
                    SUM({_sql_cast_num('v.total_ingresos')}) as total_ventas
                FROM VentasPeriodo v
                GROUP BY 1
                HAVING SUM({_sql_cast_num('v.cantidad')}) > 0
                ORDER BY {order_col} DESC
            """)
            rows_clientes = db.session.execute(sql_clientes, params).mappings().all()

            return {
                "productos": [dict(r) for r in rows_productos],
                "clientes": [dict(r) for r in rows_clientes]
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"[VentasRepository.get_desglose_mensual_ventas] {e}")
            return {"productos": [], "clientes": []}

    @staticmethod
    def get_backorder_detalle_por_cliente(cliente_nombre, start_date=None, end_date=None, exact=False):
        """
        Retorna el detalle de productos pendientes para un cliente específico con impacto financiero.

        exact=False (default): match difuso (ILIKE '%cliente%' + resolución de alias
        bidireccional). Lo usa el asistente de IA (asistente_tools._tool_backorder_cliente),
        donde el usuario puede escribir un nombre parcial o mal escrito y se necesita
        tolerancia para encontrar algo.

        exact=True: match EXACTO contra la MISMA expresión de resolución de nombre que
        usa el listado de Incumplimiento del dashboard
        (dashboard_repository._get_admin_dashboard_metrics_sql_impl: alias resuelto por
        igualdad + hardcode de DISTRIBUJES). La usa el modal del dashboard
        (/api/admin/backorder/detalle), que siempre recibe el nombre YA resuelto que el
        listado mostró en pantalla.
        BUGFIX: antes ambos caminos (listado y modal) usaban ILIKE '%cliente%' de forma
        independiente; para un nombre corto/genérico (ej. 'CHOHO') el modal podía
        capturar y sumar OTRO cliente real cuyo nombre o alias simplemente contenía esa
        subcadena, mostrando un total distinto al de la fila en la que el usuario hizo
        click. Con exact=True, modal y listado quedan garantizados a coincidir.

        BUGFIX 'impacto': antes este método valoraba el dinero pendiente con el costo
        de producción (MAX(costo_total) de db_costos), mientras que el listado
        (dashboard_repository) lo valora con el precio promedio de venta
        (MAX(precio_promedio) de db_ventas, que viene directo de World Office). Para
        el mismo cliente y las mismas unidades pendientes daban cifras completamente
        distintas (confirmado con CHOHO COLOMBIA SAS: $24.5M vs $3.1M). Decisión de
        negocio: db_ventas/WO es la fuente primordial, así que el modal ahora usa la
        MISMA fórmula que el listado -- (pedidos - ventas) * precio_promedio -- para
        que ambos paneles siempre muestren el mismo número de dinero.
        """

        def _sql_cast_num(col):
            # Columna ya NUMERIC nativo: COALESCE directo evita el bug de perder
            # el signo '-' de devoluciones/NC que tenia el cast via texto+regex.
            return f"COALESCE({col}, 0)"

        filt = ""
        if exact:
            params = {"cliente": str(cliente_nombre or "").strip().upper()}
        else:
            params = {"cliente": f"%{cliente_nombre}%"}
        if start_date and end_date:
            filt = "AND CAST(fecha AS DATE) BETWEEN :sd AND :ed"
            params["sd"] = start_date
            params["ed"] = end_date

        if exact:
            join_clause = """
                LEFT JOIN db_cliente_equivalencias e
                    ON UPPER(TRIM(b.nombres)) = UPPER(TRIM(e.alias))
            """
            where_cliente = """
                WHERE UPPER(TRIM(
                    CASE WHEN UPPER(TRIM(b.nombres)) ILIKE '%DISTRIBUJES%'
                         THEN 'FELIPE DUARTE MORENO' ELSE COALESCE(e.nombre_canonical, b.nombres) END
                )) = :cliente
            """
        else:
            join_clause = """
                -- BUGFIX defensivo: el OR/ILIKE fuzzy de abajo podia matchear MAS DE UN
                -- alias para el mismo b.nombres (fan-out de join, mismo patron confirmado
                -- en get_desglose_mensual_ventas -- ver nota ahi), duplicando filas de
                -- db_ventas ANTES del SUM e inflando p_qty/v_qty. Hoy no hay ningun alias
                -- real que dispare el fan-out (verificado en produccion), pero el LATERAL +
                -- LIMIT 1 lo hace estructuralmente imposible en vez de confiar en que los
                -- datos nunca cambien. Prioriza el match exacto sobre el fuzzy.
                LEFT JOIN LATERAL (
                    SELECT e.nombre_canonical
                    FROM db_cliente_equivalencias e
                    WHERE UPPER(TRIM(b.nombres)) = UPPER(TRIM(e.alias))
                       OR UPPER(TRIM(b.nombres)) ILIKE '%' || UPPER(TRIM(e.alias)) || '%'
                    ORDER BY (UPPER(TRIM(b.nombres)) = UPPER(TRIM(e.alias))) DESC, LENGTH(e.alias) DESC
                    LIMIT 1
                ) e ON true
            """
            where_cliente = """
                WHERE (
                    b.nombres ILIKE :cliente
                    OR (CASE WHEN UPPER(TRIM(b.nombres)) ILIKE '%DISTRIBUJES%'
                        THEN 'FELIPE DUARTE MORENO' ELSE COALESCE(e.nombre_canonical, b.nombres) END) ILIKE :cliente
                )
            """

        sql = text(f"""
            WITH totals AS (
                SELECT
                    b.productos as full_desc,
                    TRIM(split_part(REPLACE(b.productos::TEXT, 'FR-', ''), ' ', 1)) as ref_final,
                    SUM(CASE WHEN b.clasificacion ILIKE '%pedido%' THEN {_sql_cast_num('b.cantidad')} ELSE 0 END) as p_qty,
                    SUM(CASE WHEN b.clasificacion ILIKE '%venta%' THEN {_sql_cast_num('b.cantidad')} ELSE 0 END) as v_qty,
                    MAX({_sql_cast_num('b.precio_promedio')}) as avg_price
                FROM db_ventas b
                {join_clause}
                {where_cliente}
                {filt.replace('fecha', 'b.fecha')}
                AND UPPER(TRIM(b.nombres)) NOT ILIKE '%FRIPARTS%'
                GROUP BY b.productos
            )
            SELECT
                t.full_desc,
                t.ref_final,
                t.p_qty,
                t.v_qty,
                (t.p_qty - t.v_qty) as diff_qty,
                COALESCE(t.avg_price, 0) as avg_price
            FROM totals t
            WHERE (t.p_qty - t.v_qty) > 0
            ORDER BY (t.p_qty - t.v_qty) DESC
        """)

        results = []
        try:
            results = db.session.execute(sql, params).fetchall()
        except Exception as e_join:
            logger.warning(f"[VentasRepository.get_backorder_detalle_por_cliente JOIN fallback]: {e_join}")
            rollback_seguro()
            if exact:
                where_cliente_fb = """
                    WHERE UPPER(TRIM(CASE WHEN UPPER(TRIM(nombres)) ILIKE '%DISTRIBUJES%'
                        THEN 'FELIPE DUARTE MORENO' ELSE nombres END)) = :cliente
                """
            else:
                where_cliente_fb = """
                    WHERE (CASE WHEN UPPER(TRIM(nombres)) ILIKE '%DISTRIBUJES%'
                        THEN 'FELIPE DUARTE MORENO' ELSE nombres END) ILIKE :cliente
                """
            sql_fallback = text(f"""
                WITH totals AS (
                    SELECT
                        productos as full_desc,
                        TRIM(split_part(REPLACE(productos::TEXT, 'FR-', ''), ' ', 1)) as ref_final,
                        SUM(CASE WHEN clasificacion ILIKE '%pedido%' THEN {_sql_cast_num('cantidad')} ELSE 0 END) as p_qty,
                        SUM(CASE WHEN clasificacion ILIKE '%venta%' THEN {_sql_cast_num('cantidad')} ELSE 0 END) as v_qty,
                        MAX({_sql_cast_num('precio_promedio')}) as avg_price
                    FROM db_ventas
                    {where_cliente_fb} {filt}
                        AND UPPER(TRIM(nombres)) NOT ILIKE '%FRIPARTS%'
                    GROUP BY productos
                )
                SELECT
                    t.full_desc,
                    t.ref_final,
                    t.p_qty,
                    t.v_qty,
                    (t.p_qty - t.v_qty) as diff_qty,
                    COALESCE(t.avg_price, 0) as avg_price
                FROM totals t
                WHERE (t.p_qty - t.v_qty) > 0
                ORDER BY (t.p_qty - t.v_qty) DESC
            """)
            try:
                results = db.session.execute(sql_fallback, params).fetchall()
            except Exception as e_fb:
                rollback_seguro()
                logger.error(f"[VentasRepository.get_backorder_detalle_por_cliente fallback error]: {e_fb}")

        try:
            detalle = []
            for r in results:
                diff = _num(r[4])
                avg_price = _num(r[5])
                detalle.append({
                    "descripcion": r[0],
                    "referencia": r[1],
                    "pedidos": _num(r[2]),
                    "ventas": _num(r[3]),
                    "pendiente": diff,
                    # Misma fórmula que el listado (dashboard_repository): precio
                    # promedio de venta de db_ventas/WO, no costo de producción.
                    "impacto": diff * avg_price
                })
            return detalle
        except Exception as e:
            logger.error(f"[VentasRepository.get_backorder_detalle_por_cliente] {e}")
            return []

    @staticmethod
    def upsert_cartera_wo(datos_cartera):
        """
        Realiza un UPSERT masivo y eficiente en la tabla cartera_wo.
        Procesa los datos usando Decimal para la precisión monetaria.
        """
        if not datos_cartera:
            return 0

        try:
            sql = text("""
                INSERT INTO cartera_wo (documento, identificacion, nombre, vendedor, moneda, empresa, fecha_emision, fecha_vencimiento, saldo_documento, ultima_actualizacion)
                VALUES (:documento, :identificacion, :nombre, :vendedor, :moneda, :empresa, :fecha_emision, :fecha_vencimiento, :saldo_documento, CURRENT_TIMESTAMP)
                ON CONFLICT (documento) DO UPDATE SET
                    identificacion = EXCLUDED.identificacion,
                    nombre = EXCLUDED.nombre,
                    vendedor = EXCLUDED.vendedor,
                    moneda = EXCLUDED.moneda,
                    empresa = EXCLUDED.empresa,
                    fecha_emision = EXCLUDED.fecha_emision,
                    fecha_vencimiento = EXCLUDED.fecha_vencimiento,
                    saldo_documento = EXCLUDED.saldo_documento,
                    ultima_actualizacion = CURRENT_TIMESTAMP
            """)
            db.session.execute(sql, datos_cartera)
            db.session.commit()
            return len(datos_cartera)
        except Exception as e:
            rollback_seguro()
            logger.error(f"[VentasRepository.upsert_cartera_wo] Error en el UPSERT: {e}")
            raise e

    @staticmethod
    def eliminar_cartera_wo_obsoleta(documentos_vigentes):
        """
        Borra de cartera_wo los documentos que ya no vienen en la extraccion de WO
        (Vista_CuentasPorCobrar_Detallada filtra Saldo > 0). El upsert nunca toca
        una fila que deja de llegar, asi que una factura que se paga/cruza en WO
        se queda huerfana con su saldo viejo para siempre si no se borra aqui.
        Se llama solo despues de pasar el circuit breaker en la ruta, con el set
        completo de documentos de la extraccion actual.
        """
        if not documentos_vigentes:
            return 0

        try:
            # Se usa unnest(array) en lugar de NOT IN (:lista...) con expanding=True.
            # Con expanding, SQLAlchemy genera un parámetro por documento; con 1500+
            # documentos eso produce un query gigantesco que agota la RAM del worker
            # en Render (OOM kill silencioso). unnest pasa UN solo array como parámetro.
            sql = text("""
                DELETE FROM cartera_wo
                WHERE documento NOT IN (
                    SELECT unnest(cast(:documentos AS text[]))
                )
            """)
            resultado = db.session.execute(sql, {"documentos": documentos_vigentes})
            db.session.commit()
            return resultado.rowcount
        except Exception as e:
            rollback_seguro()
            logger.error(f"[VentasRepository.eliminar_cartera_wo_obsoleta] Error en el DELETE: {e}")
            raise e
