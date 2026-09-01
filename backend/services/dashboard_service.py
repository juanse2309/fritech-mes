import io
import logging
from backend.core.sql_database import db, rollback_seguro
from sqlalchemy import text
import pandas as pd

logger = logging.getLogger(__name__)

class DashboardService:
    @staticmethod
    def calcular_fulfillment_rate(inyeccion_ok, total_solicitado):
        """
        KPI de negocio: % de piezas inyectadas OK frente a lo solicitado en pedidos.
        Con prevención de ZeroDivisionError cuando no hay pedidos en el período.
        """
        total = total_solicitado or 1
        return round((float(inyeccion_ok or 0) / total) * 100, 1)

    @staticmethod
    def generar_excel_desglose(data, mes, anio):
        """
        Genera el reporte Excel del desglose mensual de ventas, con una hoja por dimensión:
        'Productos' y 'Clientes'. `data` es el DTO de VentasRepository.get_desglose_mensual_ventas:
        {"productos": [...], "clientes": [...]}.
        Retorna un BytesIO listo para send_file(); no toca el objeto Response ni Flask.
        """
        def _hoja_productos(writer):
            df = pd.DataFrame(data.get('productos') or [])
            columnas_esperadas = ['id_codigo', 'descripcion', 'unidades', 'total_ventas']
            for col in columnas_esperadas:
                if col not in df.columns:
                    df[col] = 0 if ('total' in col or 'unidades' in col) else ''
            df = df[columnas_esperadas]
            df.columns = ['Referencia', 'Descripción', 'Cantidad', 'Total (COP)']
            df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce').fillna(0)
            df['Total (COP)'] = pd.to_numeric(df['Total (COP)'], errors='coerce').fillna(0)
            df.to_excel(writer, index=False, sheet_name='Productos')
            return df

        def _hoja_clientes(writer):
            df = pd.DataFrame(data.get('clientes') or [])
            columnas_esperadas = ['nombre_cliente', 'unidades', 'total_ventas']
            for col in columnas_esperadas:
                if col not in df.columns:
                    df[col] = 0 if ('total' in col or 'unidades' in col) else ''
            df = df[columnas_esperadas]
            df.columns = ['Cliente', 'Cantidad', 'Total (COP)']
            df['Cantidad'] = pd.to_numeric(df['Cantidad'], errors='coerce').fillna(0)
            df['Total (COP)'] = pd.to_numeric(df['Total (COP)'], errors='coerce').fillna(0)
            df.to_excel(writer, index=False, sheet_name='Clientes')
            return df

        def _autoajustar_columnas(worksheet, df):
            for idx, col in enumerate(df.columns):
                val_max_len = df[col].astype(str).map(len).max() if not df.empty else 0
                max_len = max(val_max_len, len(col)) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 60)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_productos = _hoja_productos(writer)
            _autoajustar_columnas(writer.sheets['Productos'], df_productos)

            df_clientes = _hoja_clientes(writer)
            _autoajustar_columnas(writer.sheets['Clientes'], df_clientes)

        output.seek(0)
        return output

    @staticmethod
    def generar_insights_bot_planta(kpis, stock_critico, pulido_profundo, ranking_iny_ops):
        """
        Genera el informe ejecutivo de IA del Bot de Planta con:
        1. Volumen Consolidado de Producción
        2. Desviaciones de Producción (Brechas de flujo entre Inyección y Pulido)
        3. Alertas Operativas, Financieras y de Mermas (Scrap)
        4. Top Productividad y Liderazgos Operativos
        """
        insights = []

        iny_ok = float(kpis.get('inyeccion_ok', 0) or 0)
        pul_ok = float(kpis.get('pulido_ok', 0) or 0)
        ens_ok = float(kpis.get('ensambles_ok', 0) or 0)
        total_ok = iny_ok + pul_ok + ens_ok

        # 1. Volumen Consolidado
        insights.append(
            f"VOLUMEN_CONSOLIDADO|Volumen Consolidado: Inyección {iny_ok:,.0f} Pz | Pulido {pul_ok:,.0f} Pz | Ensamble {ens_ok:,.0f} Pz. Total OK: {total_ok:,.0f} Pz."
        )

        # 2. Desviaciones de Producción (Flujo de planta)
        brecha_iny_pul = iny_ok - pul_ok
        if brecha_iny_pul > 0:
            insights.append(
                f"DESVIACION|Brecha de Producción: Inyección superó a Pulido por {brecha_iny_pul:,.0f} Pz acumuladas por pulir."
            )
        elif brecha_iny_pul < 0:
            insights.append(
                f"DESVIACION|Flujo Inverso: Pulido procesó {abs(brecha_iny_pul):,.0f} Pz provenientes de lotes en inventario WIP."
            )
        else:
            insights.append(
                f"DESVIACION|Flujo Balanceado: Balance 1:1 perfecto entre piezas inyectadas y pulidas."
            )

        # 3. Alertas Operativas y de Mermas
        costo_scrap = float(kpis.get('perdida_calidad_dinero', 0) or 0)
        total_scrap = float(kpis.get('scrap_total', 0) or 0)
        fpy = float(kpis.get('fpy_global', 99.2) or 99.2)

        if costo_scrap > 0:
            insights.append(
                f"ALERTA_SCRAP|Impacto Financiero de Mermas: ${costo_scrap:,.0f} COP perdidos por {total_scrap:,.0f} piezas de scrap (FPY: {fpy:.1f}%)."
            )

        n_critico = len(stock_critico) if isinstance(stock_critico, list) else 0
        if n_critico > 0:
            insights.append(
                f"ALERTA_STOCK|Alerta de Inventario: {n_critico} referencias están en nivel crítico por debajo del mínimo."
            )

        # 4. Líderes de Productividad
        if ranking_iny_ops and len(ranking_iny_ops) > 0:
            top_iny = ranking_iny_ops[0]
            insights.append(
                f"LIDER_INY|Líder Inyección: {top_iny.get('nombre', 'Operario')} con {float(top_iny.get('valor', 0)):,.0f} Pz OK producidas."
            )

        if pulido_profundo and isinstance(pulido_profundo, dict):
            top_pul = sorted(pulido_profundo.items(), key=lambda x: x[1].get('buenas', 0), reverse=True)
            if top_pul:
                top_pul_nombre, top_pul_data = top_pul[0]
                insights.append(
                    f"LIDER_PUL|Líder Pulido: {top_pul_nombre} con {top_pul_data.get('buenas', 0):,.0f} Pz OK ({top_pul_data.get('yield_calidad', 100)}% Yield)."
                )

        return insights

    @staticmethod
    def mapear_fechas_despacho_backorder(backorder):
        """
        Normaliza `ultima_fecha_despacho` del DTO de Backorder al contrato del cliente:
        timestamp válido -> 'YYYY-MM-DD'; sin despachos registrados -> None (null JSON).
        El rótulo visible de la ausencia lo decide la vista, no esta capa.
        """
        for item in backorder or []:
            valor = item.get("ultima_fecha_despacho")
            if hasattr(valor, 'strftime'):
                item["ultima_fecha_despacho"] = valor.strftime('%Y-%m-%d')
            elif valor:
                item["ultima_fecha_despacho"] = str(valor)[:10]
            else:
                item["ultima_fecha_despacho"] = None
        return backorder

    @staticmethod
    def get_cartera_wo_stats():
        """
        Obtiene los indicadores principales de la tabla cartera_wo (World Office)
        Maneja fallos de conexión o excepciones devolviendo diccionarios vacíos por defecto.
        """
        try:
            # Totales calculados dinámicamente sobre el detalle (Zona Horaria Colombia)
            query_totales = text("""
                SELECT 
                    COALESCE(SUM(saldo_documento), 0), 
                    COALESCE(SUM(CASE WHEN fecha_vencimiento < (CURRENT_TIMESTAMP AT TIME ZONE 'America/Bogota')::date THEN saldo_documento ELSE 0 END), 0) 
                FROM cartera_wo
            """)
            totales = db.session.execute(query_totales).fetchone()
            
            total_cartera = float(totales[0]) if totales else 0.0
            total_vencida = float(totales[1]) if totales else 0.0
            
            # Clientes críticos: Agrupando las facturas vencidas por cliente
            query_top = text("""
                SELECT nombre, SUM(CASE WHEN fecha_vencimiento < (CURRENT_TIMESTAMP AT TIME ZONE 'America/Bogota')::date THEN saldo_documento ELSE 0 END) as saldo_vencido 
                FROM cartera_wo 
                GROUP BY nombre 
                HAVING SUM(CASE WHEN fecha_vencimiento < (CURRENT_TIMESTAMP AT TIME ZONE 'America/Bogota')::date THEN saldo_documento ELSE 0 END) > 0 
                ORDER BY saldo_vencido DESC 
                LIMIT 5
            """)
            top_clientes = [{"nombre": row[0], "saldo_vencido": float(row[1])} for row in db.session.execute(query_top).fetchall()]
            
            return {
                "total_cartera": total_cartera,
                "total_vencida": total_vencida,
                "clientes_criticos": top_clientes
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"Error consultando métricas de cartera: {e}")
            # Retorno seguro (default values) en caso de fallo
            return {
                "total_cartera": 0.0,
                "total_vencida": 0.0,
                "clientes_criticos": []
            }

    @staticmethod
    def normalizar_cliente_alias(nombre: str) -> str:
        """
        Resuelve dinámicamente el alias del cliente consultando el mapeo relacional en DB (db_cliente_equivalencias / db_clientes).
        Elimina el acoplamiento rígido de diccionarios estáticos en código.
        """
        if not nombre:
            return ""
        clean_name = str(nombre).strip()
        upper_name = clean_name.upper()
        if 'DISTRIBUJES' in upper_name and ('FC' in upper_name or 'CAUCHOS' in upper_name):
            return 'FELIPE DUARTE MORENO'

        try:
            sql = text("""
                SELECT COALESCE(
                    (SELECT nombre_canonical FROM db_cliente_equivalencias WHERE UPPER(TRIM(alias)) = UPPER(TRIM(:nombre)) OR UPPER(TRIM(:nombre)) ILIKE '%' || UPPER(TRIM(alias)) || '%' OR UPPER(TRIM(alias)) ILIKE '%' || UPPER(TRIM(:nombre)) || '%' LIMIT 1),
                    (SELECT razon_social FROM db_clientes WHERE UPPER(TRIM(alias)) = UPPER(TRIM(:nombre)) OR UPPER(TRIM(nombre)) = UPPER(TRIM(:nombre)) LIMIT 1),
                    :nombre
                )
            """)
            row = db.session.execute(sql, {"nombre": clean_name}).fetchone()
            if row and row[0]:
                return str(row[0]).strip()
        except Exception as e:
            logger.debug(f"[normalizar_cliente_alias DB lookup]: {e}")
            rollback_seguro()

        return clean_name

    @staticmethod
    def get_rendimiento(desde=None, hasta=None):
        """
        Devuelve el desglose de rendimiento de los últimos 12 meses con el índice del mes actual.
        Soporta filtrado reactivo por fechas desde/hasta.
        """
        try:
            from backend.repositories.dashboard_repository import DashboardRepository
            import datetime
            now = datetime.datetime.now()
            current_month = now.month
            current_year = now.year

            start = str(desde) if desde else None
            end = str(hasta) if hasta else None
            mensual_data = DashboardRepository.get_rendimiento_mensual_sql(start, end)
            
            rendimiento = []
            mes_actual_idx = 0

            # mensual_data puede venir truncada (filtro desde/hasta), por lo que el idx
            # de iteración ya NO equivale al número de mes calendario: se resuelve el mes
            # real a partir de la abreviatura ('ene'..'dic') para no desalinear mes_actual_idx.
            meses_abrev = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

            # mensual_data contains data mapping from 1 to 12 (o subconjunto truncado)
            for idx, item in enumerate(mensual_data):
                p_monto = float(item.get('actual_pedidos') or 0)
                v_monto = float(item.get('actual_dinero') or 0)
                p_und = float(item.get('actual_pedidos_unidades') or 0)
                v_und = float(item.get('actual_unidades') or 0)

                pct_monto = (v_monto / p_monto * 100) if p_monto > 0 else 0.0
                pct_unidades = (v_und / p_und * 100) if p_und > 0 else 0.0

                rendimiento.append({
                    "mes": item.get('mes', str(idx+1)),
                    "anio": current_year,
                    "ventas_monto": v_monto,
                    "pedidos_monto": p_monto,
                    "ventas_unidades": v_und,
                    "pedidos_unidades": p_und,
                    "pct_cumplimiento_monto": round(float(pct_monto), 2),
                    "pct_cumplimiento_unidades": round(float(pct_unidades), 2)
                })

                mes_abrev = item.get('mes')
                mes_num = (meses_abrev.index(mes_abrev) + 1) if mes_abrev in meses_abrev else (idx + 1)
                if mes_num == current_month:
                    mes_actual_idx = idx

            return {
                "mes_actual_idx": mes_actual_idx,
                "data": rendimiento
            }
            
        except Exception as e:
            rollback_seguro()
            logger.error(f"Error consultando rendimiento: {e}")
            return {"mes_actual_idx": 0, "data": []}

    @staticmethod
    def calcular_porcentajes_maquinas(ranking_maquinas):
        """
        Calcula el % de participación de cada máquina con prevención de ZeroDivisionError.
        Si la suma total de piezas es 0 o vacía, asigna 0.0% a todas las máquinas.
        """
        if not ranking_maquinas or not isinstance(ranking_maquinas, list):
            return []

        total_piezas = sum(float(m.get('valor', 0) or 0) for m in ranking_maquinas)
        
        resultado = []
        for m in ranking_maquinas:
            maq_name = str(m.get('maquina', '?')).strip()
            valor_num = float(m.get('valor', 0) or 0)
            
            if total_piezas <= 0:
                pct = 0.0
            else:
                pct = round((valor_num / total_piezas) * 100.0, 2)

            resultado.append({
                "maquina": maq_name,
                "valor": valor_num,
                "porcentaje": pct
            })

        return resultado

    @staticmethod
    def get_scrap_detalle(item_id, desde=None, hasta=None):
        """
        Obtiene el desglose detallado del scrap/mermas para una referencia específica:
        Fecha, Máquina de origen y Cantidad.
        Respeta el Filtro Global de Fechas del dashboard cuando se provee desde/hasta;
        sin él, mostraba el histórico completo de la referencia sin importar el período
        que el usuario tenía seleccionado en el resto del panel.
        """
        if not item_id:
            return []

        try:
            from backend.utils.formatters import normalizar_codigo
            cod_norm = normalizar_codigo(str(item_id).strip())

            params = {"cod_norm": cod_norm}
            filt_iny = ""
            filt_pul = ""
            if desde and hasta:
                filt_iny = " AND CAST(i.fecha_inicia AS DATE) BETWEEN :desde AND :hasta"
                filt_pul = " AND CAST(d.fecha AS DATE) BETWEEN :desde AND :hasta"
                params["desde"] = desde
                params["hasta"] = hasta

            sql = text(f"""
                SELECT
                    COALESCE(to_char(i.fecha_inicia, 'YYYY-MM-DD HH24:MI'), 'Sin fecha') as fecha,
                    COALESCE(NULLIF(TRIM(i.maquina::text), ''), 'Inyección') as maquina,
                    SUM(COALESCE(p.cantidad, 0))::numeric as cantidad
                FROM db_pnc_inyeccion p
                LEFT JOIN (
                    SELECT DISTINCT ON (id_inyeccion) id_inyeccion, fecha_inicia, maquina
                    FROM db_inyeccion
                    WHERE fecha_inicia IS NOT NULL
                    ORDER BY id_inyeccion, fecha_inicia DESC
                ) i ON p.id_inyeccion = i.id_inyeccion
                WHERE TRIM(UPPER(REPLACE(p.id_codigo::text, 'FR-', ''))) = :cod_norm
                  AND COALESCE(p.cantidad, 0) > 0
                  {filt_iny}
                GROUP BY COALESCE(to_char(i.fecha_inicia, 'YYYY-MM-DD HH24:MI'), 'Sin fecha'), COALESCE(NULLIF(TRIM(i.maquina::text), ''), 'Inyección')

                UNION ALL

                SELECT
                    COALESCE(to_char(d.fecha, 'YYYY-MM-DD'), 'Sin fecha') as fecha,
                    'Pulido' as maquina,
                    SUM(COALESCE(p.cantidad, 0))::numeric as cantidad
                FROM db_pnc_pulido p
                LEFT JOIN db_pulido d ON p.id_pulido = d.id_pulido
                WHERE TRIM(UPPER(REPLACE(p.codigo::text, 'FR-', ''))) = :cod_norm
                  AND COALESCE(p.cantidad, 0) > 0
                  {filt_pul}
                GROUP BY COALESCE(to_char(d.fecha, 'YYYY-MM-DD'), 'Sin fecha')

                ORDER BY fecha DESC
                LIMIT 50
            """)

            rows = db.session.execute(sql, params).mappings().all()

            return [{
                "fecha": r['fecha'],
                "maquina": r['maquina'],
                "cantidad": float(r['cantidad'] or 0)
            } for r in rows]

        except Exception as e:
            rollback_seguro()
            logger.error(f"Error consultando detalle de scrap para {item_id}: {e}")
            return []

    @staticmethod
    def get_productos_sin_rotacion(q=None, max_ventas=0, limit=50):
        """
        Obtiene productos de baja rotación en los últimos 12 meses.
        - max_ventas: Configurable dinámicamente de 0 a 50 unidades vendidas.
        - Stock P. Terminado: Mapeado 1:1 directo desde la sincronización oficial de World Office (inventario_wo / db_productos).
        """
        try:
            max_ventas_val = max(0, min(50, int(max_ventas or 0)))

            from backend.config.business_rules import CatalogExclusionConfig
            excl_sin_rot, excl_sin_rot_params = CatalogExclusionConfig.get_sql_exclusion_clause('p.codigo_sistema', mode='sin_rotacion')

            # FROM/JOIN/WHERE compartido entre el COUNT (sin LIMIT, el total real) y el
            # SELECT (con LIMIT, solo para no sobrecargar la respuesta) -- BUGFIX: antes
            # 'total' se calculaba como len(resultado) DESPUES de aplicar LIMIT :limit
            # (50 por defecto) directo en el SQL, asi que nunca podia superar el limite
            # aunque hubiera muchos mas productos calificando. Confirmado en produccion:
            # con max_ventas=0 hay 521 productos sin rotacion reales, pero 'total' devolvia
            # 50 -- afecta tanto el badge del dashboard (badge-total-sin-rotacion) como
            # esta misma tool del asistente.
            from_join_where = f"""
                FROM db_productos p
                LEFT JOIN (
                    SELECT DISTINCT ON (codigo_producto) codigo_producto, stock_wo
                    FROM inventario_wo
                    WHERE codigo_producto IS NOT NULL
                    ORDER BY codigo_producto
                ) w ON TRIM(UPPER(REPLACE(w.codigo_producto::text, 'FR-', ''))) = TRIM(UPPER(REPLACE(p.codigo_sistema::text, 'FR-', '')))
                LEFT JOIN (
                    SELECT
                        TRIM(UPPER(REPLACE(productos::text, 'FR-', ''))) as ref,
                        SUM(COALESCE(cantidad, 0)) as total_ventas
                    FROM db_ventas
                    WHERE fecha >= (CURRENT_DATE - INTERVAL '12 months')
                    GROUP BY 1
                ) v_tot ON v_tot.ref = TRIM(UPPER(REPLACE(p.codigo_sistema::text, 'FR-', '')))
                WHERE p.codigo_sistema IS NOT NULL AND p.codigo_sistema != ''
                  AND COALESCE(v_tot.total_ventas, 0) <= :max_ventas
                  {excl_sin_rot}
                  AND (
                      :q_raw != '%%' OR (
                          p.codigo_sistema ILIKE 'FR-%' OR p.codigo_sistema ILIKE 'MT-%'
                          OR p.codigo_sistema ILIKE 'DE-%'
                          OR p.id_codigo ILIKE 'FR-%' OR p.id_codigo ILIKE 'MT-%'
                      )
                  )
            """
            params = {
                'max_ventas': max_ventas_val,
                'q_raw': '%%',
                'q_norm': '%%',
                **excl_sin_rot_params,
            }

            if q and str(q).strip():
                q_clean = str(q).strip()
                q_norm = q_clean.upper().replace('FR-', '').strip()
                from_join_where += """ AND (
                    p.codigo_sistema ILIKE :q_raw
                    OR p.id_codigo ILIKE :q_raw
                    OR p.descripcion ILIKE :q_raw
                    OR TRIM(UPPER(REPLACE(p.codigo_sistema::text, 'FR-', ''))) ILIKE :q_norm
                )"""
                params['q_raw'] = f"%{q_clean}%"
                params['q_norm'] = f"%{q_norm}%"

            total_real = db.session.execute(
                text(f"SELECT COUNT(*) {from_join_where}"), params
            ).scalar()

            sql = f"""
                SELECT
                    p.id,
                    COALESCE(NULLIF(TRIM(p.codigo_sistema), ''), p.id_codigo, 'S/C') as codigo,
                    COALESCE(NULLIF(TRIM(p.descripcion), ''), 'Sin descripción') as descripcion,
                    COALESCE(w.stock_wo, p.p_terminado, 0) as stock_terminado,
                    COALESCE(p.precio, 0) as precio,
                    COALESCE(v_tot.total_ventas, 0) as ventas_periodo
                {from_join_where}
                ORDER BY stock_terminado DESC, p.id ASC LIMIT :limit
            """
            params['limit'] = limit

            logger.info(f"🔍 [get_productos_sin_rotacion] Consultando catálogo 100% con max_ventas={max_ventas_val}, q='{q or ''}'")
            rows = db.session.execute(text(sql), params).mappings().all()
            logger.info(f"✅ [get_productos_sin_rotacion] Total real={total_real}, filas devueltas={len(rows)}")

            resultado = [{
                "id": r['id'],
                "codigo": r['codigo'],
                "descripcion": r['descripcion'],
                "stock": float(r['stock_terminado'] or 0),
                "stock_terminado": float(r['stock_terminado'] or 0),
                "precio": float(r['precio'] or 0),
                "ventas_periodo": float(r['ventas_periodo'] or 0)
            } for r in rows]

            return {
                "total": total_real,
                "max_ventas_aplicado": max_ventas_val,
                "productos": resultado
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"Error consultando productos sin/baja rotación: {e}")
            return {"total": 0, "productos": []}

    @staticmethod
    def get_drilldown_inyeccion_por_fecha(fecha_str):
        """
        Consulta los registros atómicos de inyección correspondientes a una fecha específica (YYYY-MM-DD),
        agrupando OP, máquina, referencia, operario y calculando cantidades buenas y malas (PNC).
        Optimizado con rango sargable para aprovechamiento de índices PostgreSQL.
        """
        try:
            from datetime import datetime, timedelta
            target_date = datetime.strptime(str(fecha_str).strip(), '%Y-%m-%d').date()
            start_dt = datetime.combine(target_date, datetime.min.time())
            end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

            sql = text("""
                SELECT 
                    COALESCE(NULLIF(TRIM(i.orden_produccion), ''), 'SIN OP')          AS orden_produccion,
                    COALESCE(NULLIF(TRIM(i.maquina), ''), 'S/M')                     AS maquina,
                    COALESCE(NULLIF(TRIM(i.id_codigo), ''), 'S/C')                    AS id_codigo,
                    COALESCE(NULLIF(TRIM(p.descripcion), ''), 'Sin Descripción')     AS descripcion,
                    COALESCE(NULLIF(TRIM(i.responsable), ''), 'S/R')                 AS operario,
                    ROUND(SUM(COALESCE(i.cantidad_real, 0))::NUMERIC, 2)              AS buenas,
                    ROUND(SUM(COALESCE(i.pnc_total, 0))::NUMERIC, 2)                 AS malas,
                    COUNT(i.id)::INTEGER                                              AS total_registros,
                    COALESCE(MAX(i.cavidades), 1)::INTEGER                            AS cavidades
                FROM db_inyeccion i
                LEFT JOIN db_productos p ON TRIM(UPPER(REPLACE(p.codigo_sistema::text, 'FR-', ''))) = TRIM(UPPER(REPLACE(i.id_codigo::text, 'FR-', '')))
                WHERE i.fecha_inicia >= :start_dt AND i.fecha_inicia < :end_dt
                GROUP BY 
                    COALESCE(NULLIF(TRIM(i.orden_produccion), ''), 'SIN OP'),
                    COALESCE(NULLIF(TRIM(i.maquina), ''), 'S/M'),
                    COALESCE(NULLIF(TRIM(i.id_codigo), ''), 'S/C'),
                    COALESCE(NULLIF(TRIM(p.descripcion), ''), 'Sin Descripción'),
                    COALESCE(NULLIF(TRIM(i.responsable), ''), 'S/R')
                ORDER BY buenas DESC, malas DESC;
            """)

            rows = db.session.execute(sql, {'start_dt': start_dt, 'end_dt': end_dt}).mappings().all()

            resultado = [{
                'orden_produccion': r['orden_produccion'],
                'maquina': r['maquina'],
                'id_codigo': r['id_codigo'],
                'descripcion': r['descripcion'],
                'operario': r['operario'],
                'buenas': float(r['buenas'] or 0),
                'malas': float(r['malas'] or 0),
                'total_registros': int(r['total_registros'] or 0),
                'cavidades': int(r['cavidades'] or 1)
            } for r in rows]

            return {
                'success': True,
                'fecha': fecha_str,
                'total_filas': len(resultado),
                'detalle': resultado
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"[get_drilldown_inyeccion_por_fecha] Error consultando fecha {fecha_str}: {e}")
            return {
                'success': False,
                'fecha': fecha_str,
                'error': str(e),
                'detalle': []
            }

    @staticmethod
    def get_detalle_operador_inyeccion(responsable, desde=None, hasta=None):
        """
        Detalle atómico por lote de producción de un operador de Inyección
        (Modal 'Top Rendimiento Inyección'). Devuelve un DTO tipado y consistente:
        responsable, id_codigo, unidades_buenas, cavidades, duracion_minutos, fecha.
        """
        if not responsable or not str(responsable).strip():
            return {'success': False, 'responsable': responsable, 'error': 'Responsable no especificado', 'detalle': []}

        try:
            filt = ""
            params = {'responsable': str(responsable).strip().upper()}
            if desde and hasta:
                filt = " AND CAST(i.fecha_inicia AS DATE) BETWEEN :desde AND :hasta"
                params['desde'] = desde
                params['hasta'] = hasta

            sql = text(f"""
                SELECT
                    COALESCE(NULLIF(TRIM(i.responsable), ''), 'S/R')              AS responsable,
                    COALESCE(NULLIF(TRIM(i.id_codigo), ''), 'S/C')                AS id_codigo,
                    COALESCE(i.cantidad_real, 0)::NUMERIC                          AS unidades_buenas,
                    COALESCE(i.cavidades, 1)::INTEGER                              AS cavidades,
                    COALESCE(i.tiempo_total_minutos, 0)::NUMERIC                   AS duracion_minutos,
                    to_char(i.fecha_inicia, 'YYYY-MM-DD HH24:MI')                  AS fecha
                FROM db_inyeccion i
                WHERE UPPER(TRIM(i.responsable)) = :responsable {filt}
                ORDER BY i.fecha_inicia DESC
                LIMIT 200
            """)

            rows = db.session.execute(sql, params).mappings().all()

            resultado = [{
                'responsable': r['responsable'],
                'id_codigo': r['id_codigo'],
                'unidades_buenas': float(r['unidades_buenas'] or 0),
                'cavidades': int(r['cavidades'] or 1),
                'duracion_minutos': float(r['duracion_minutos'] or 0),
                'fecha': r['fecha'] or 'Sin fecha'
            } for r in rows]

            return {
                'success': True,
                'responsable': responsable,
                'total_filas': len(resultado),
                'detalle': resultado
            }
        except Exception as e:
            rollback_seguro()
            logger.error(f"[get_detalle_operador_inyeccion] Error consultando responsable {responsable}: {e}")
            return {
                'success': False,
                'responsable': responsable,
                'error': str(e),
                'detalle': []
            }

    @staticmethod
    def calcular_eficiencia_pulido_por_referencia(desde=None, hasta=None):
        """
        Cálculo optimizado que relaciona tiempo por lote y cantidad pulida para determinar
        de forma fidedigna qué operario procesa más rápido cada referencia específica.
        Excluye operarias ignoradas ('NOHEMY', 'LAURA JIMENEZ').
        """
        # Módulo dado de baja temporalmente por corrupción de datos (orden del Arquitecto de Software).
        # TODO: Refactorizar cálculo por datos erróneos
        return {}
        try:
            filt = " AND p.estado IN ('FINALIZADO', 'APROBADO') AND p.cantidad_real > 0 AND p.tiempo_total_minutos > 0"
            params = {}
            if desde and hasta:
                filt += " AND CAST(p.fecha AS DATE) BETWEEN :desde AND :hasta"
                params['desde'] = desde
                params['hasta'] = hasta

            from backend.utils.formatters import sql_normalizar_codigo_fr
            ref_norm = sql_normalizar_codigo_fr('p.codigo')

            sql = text(f"""
                SELECT
                    {ref_norm}                                         AS referencia,
                    UPPER(TRIM(p.responsable::text))                  AS operario,
                    SUM(COALESCE(p.cantidad_real, 0))                 AS total_piezas,
                    SUM(COALESCE(p.tiempo_total_minutos, 0))         AS total_minutos
                FROM db_pulido p
                WHERE 1=1 {filt}
                GROUP BY 1, 2
                HAVING SUM(COALESCE(p.cantidad_real, 0)) > 0 AND SUM(COALESCE(p.tiempo_total_minutos, 0)) > 0
                ORDER BY 1, total_piezas DESC
            """)

            rows = db.session.execute(sql, params).mappings().all()

            from backend.services.pulido_service import PulidoService
            ref_map = {}

            for r in rows:
                op = r['operario']
                if PulidoService._es_responsable_ignorado(op):
                    continue

                ref = r['referencia']
                piezas = float(r['total_piezas'] or 0)
                minutos = float(r['total_minutos'] or 0)

                pz_min = round(piezas / minutos, 2) if minutos > 0 else 0.0
                min_pz = round(minutos / piezas, 4) if piezas > 0 else 0.0

                if ref not in ref_map:
                    ref_map[ref] = []

                ref_map[ref].append({
                    "operario": op,
                    "piezas": piezas,
                    "minutos": minutos,
                    "pz_por_minuto": pz_min,
                    "minutos_por_pieza": min_pz
                })

            resumen_eficiencia = {}
            for ref, ops in ref_map.items():
                ops_sorted = sorted(ops, key=lambda x: x['pz_por_minuto'], reverse=True)
                resumen_eficiencia[ref] = {
                    "operario_mas_rapido": ops_sorted[0]['operario'],
                    "velocidad_pz_min": ops_sorted[0]['pz_por_minuto'],
                    "tiempo_min_pz": ops_sorted[0]['minutos_por_pieza'],
                    "ranking_operarios": ops_sorted
                }

            return resumen_eficiencia
        except Exception as e:
            rollback_seguro()
            logger.error(f"[calcular_eficiencia_pulido_por_referencia] {e}")
            return {}

