"""
Reglas de negocio y mapas de configuración estáticos, extraídos del antiguo
God Service `repository_service.py`.
"""
import os
import re


def _lista_desde_env(nombre_env: str, valor_defecto: list) -> list:
    """Lee una lista separada por comas de una variable de entorno; si no
    está definida, usa el valor por defecto (el catálogo actual de FRIPARTS)."""
    valor = os.getenv(nombre_env)
    if not valor:
        return valor_defecto
    return [v.strip() for v in valor.split(',') if v.strip()]

# Validador de identificador propio de este módulo (deliberadamente separado
# de `_validar_identificador` de BaseRepository): esa whitelist es para el
# CRUD genérico (nombres de tabla/columna sueltos) y es más estricta que lo
# que necesita `get_sql_exclusion_clause`, que recibe columnas calificadas
# por alias (ej. 'p.codigo_sistema'). Mantenerlos separados evita tener que
# aflojar el validador del CRUD — la superficie más sensible a inyección —
# solo para acomodar este caso de uso distinto, y también evita cualquier
# dependencia circular entre este archivo y base_repository.py.
_IDENTIFICADOR_CALIFICADO_VALIDO = re.compile(r'^[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)?$')


def _validar_identificador_calificado(nombre, tipo="identificador"):
    """Valida un identificador SQL simple o calificado por alias (alias.columna)."""
    if not nombre or not isinstance(nombre, str) or not _IDENTIFICADOR_CALIFICADO_VALIDO.match(nombre):
        raise ValueError(f"Nombre de {tipo} inválido o potencialmente inseguro: {nombre!r}")
    return nombre


# ─────────────────────────────────────────────────────────────
# MAPA DE TABLAS  (nombre de hoja Sheets → nombre tabla SQL)
# ─────────────────────────────────────────────────────────────
SHEET_TO_TABLE = {
    'INYECCION':            'db_inyeccion',
    'PULIDO':               'db_pulido',
    'CONTROL_ASISTENCIA':   'db_asistencia',
    'PRODUCTOS':            'db_productos',
    'DB_Clientes':          'db_clientes',
    'CLIENTES':             'db_clientes',
    'RESPONSABLES':         'usuarios_raw',
    'PEDIDOS':              'db_pedidos',
    'ENSAMBLES':            'db_ensambles',
    'PNC':                  'db_pnc',
    'RAW_VENTAS':           'db_ventas',
    'db_costos':            'db_costos',
    'METALS_PRODUCTOS':     'metals_productos',
    # Fallbacks / Alias para evitar errores de transicion
    'produccion_inyeccion': 'db_inyeccion',
    'programacion_inyeccion': 'db_programacion',
}


class CatalogExclusionConfig:
    """
    Estructura paramétrica centralizada para la abstracción de filtros de catálogo y prefijos comerciales.
    Elimina el acoplamiento rígido de cadenas fijas en la lógica de negocio.
    """
    REFERENCIAS_EXCLUIDAS_TOP = _lista_desde_env('CATALOGO_REFERENCIAS_EXCLUIDAS_TOP', ['VEHICULO-03'])
    PREFIJOS_EXCLUIDOS_MENOS_VENDIDOS = _lista_desde_env(
        'CATALOGO_PREFIJOS_EXCLUIDOS_MENOS_VENDIDOS', ['PS', 'BDF', 'BSL', 'BL', 'VEHICULO-03']
    )
    PREFIJOS_EXCLUIDOS_SIN_ROTACION = _lista_desde_env('CATALOGO_PREFIJOS_EXCLUIDOS_SIN_ROTACION', ['IM'])

    @classmethod
    def get_sql_exclusion_clause(cls, column_name: str, mode: str = 'menos_vendidos') -> tuple:
        """
        Genera una cláusula de exclusión SQL segura y sus parámetros.

        `column_name` se valida como identificador puro (whitelist de forma) antes
        de interpolarse en el texto del SQL — no hay otra forma de referenciar una
        columna dinámicamente con SQLAlchemy `text()`. Los VALORES a excluir nunca
        se interpolan: siempre van parametrizados vía `:placeholder`, aunque hoy
        provengan de constantes hardcodeadas de esta clase (defensa en profundidad:
        si mañana estas listas se vuelven configurables, el patrón ya es seguro).

        Devuelve (clausula_sql, params_dict). El caller debe fusionar params_dict
        en el dict que le pasa a `db.session.execute(text(sql), params)`.
        Si mode es inválido o no hay items que excluir, devuelve ("", {}).
        """
        _validar_identificador_calificado(column_name, "columna")

        if mode == 'top':
            items = cls.REFERENCIAS_EXCLUIDAS_TOP
            patrones = {f"excl_{mode}_{i}": f"%{item}%" for i, item in enumerate(items)}
        elif mode == 'menos_vendidos':
            items = cls.PREFIJOS_EXCLUIDOS_MENOS_VENDIDOS
            patrones = {
                f"excl_{mode}_{i}": (f"%{item}%" if item == 'VEHICULO-03' else f"{item}%")
                for i, item in enumerate(items)
            }
        elif mode == 'sin_rotacion':
            items = cls.PREFIJOS_EXCLUIDOS_SIN_ROTACION
            patrones = {f"excl_{mode}_{i}": f"{item}%" for i, item in enumerate(items)}
        else:
            return "", {}

        if not patrones:
            return "", {}

        # Citar cada segmento por separado ('p.codigo_sistema' -> "p"."codigo_sistema"):
        # citar el identificador completo como una sola cadena (p.ej. "p.codigo_sistema")
        # lo convierte en UN identificador literal con un punto en el nombre, que
        # Postgres no resuelve contra el alias "p" de la consulta (UndefinedColumn).
        columna_citada = '.'.join(f'"{segmento}"' for segmento in column_name.split('.'))
        conds = [f'{columna_citada} NOT ILIKE :{key}' for key in patrones]
        clausula = " AND " + " AND ".join(conds)
        return clausula, patrones


# ─────────────────────────────────────────────────────────────
# TRADUCTOR LEGACY  (columna SQL → nombre original en Sheets)
# Front-end espera exactamente estos nombres.
# ─────────────────────────────────────────────────────────────
COLUMNS_TO_LEGACY = {
    # Productos
    'codigo_sistema':       'CODIGO SISTEMA',
    'id_codigo':            'ID CODIGO',
    'descripcion':          'DESCRIPCION',
    'precio':               'PRECIO',
    'por_pulir':            'POR PULIR',
    'p_terminado':          'P. TERMINADO',
    'comprometido':         'COMPROMETIDO',
    'producto_ensamblado':  'PRODUCTO ENSAMBLADO',
    'imagen':               'IMAGEN',
    'stock_minimo':         'STOCK MINIMO',
    'stock_maximo':         'STOCK MAXIMO',
    'punto_reorden':        'PUNTO REORDEN',
    'oem':                  'OEM',
    'dolares':              'DOLARES',
    'medida':               'MEDIDA',
    'ubicacion':            'UBICACION',
    'categoria':            'CATEGORIA',

    # Producción – genérico
    'cantidad_real':        'CANTIDAD REAL',
    'pnc':                  'PNC',
    'estado':               'ESTADO',
    'responsable':          'RESPONSABLE',
    'maquina':              'MAQUINA',
    'fecha':                'FECHA',
    'turno':                'TURNO',

    # Inyección
    'id_inyeccion':         'ID INYECCION',

    # Pulido
    'id_pulido':            'ID PULIDO',
    'hora_inicio':          'HORA INICIO',
    'hora_fin':              'HORA FIN',
    'cantidad_real':        'CANTIDAD REAL',

    # Ventas / Pedidos
    'documento':            'ID PEDIDO',
    'id_pedido':             'ID PEDIDO',
    'nro_pedido':           'Nro Pedido',
    'pedido_id':            'ID PEDIDO',
    'cliente':              'Cliente',
    'productos':            'PRODUCTOS',
    'cantidad':             'CANTIDAD',
    'total_ingresos':       'TOTAL VENTA',
    'precio_promedio':      'PRECIO PROMEDIO',
    'clasificacion':        'CLASIFICACION',
    'precio_unitario':      'PRECIO UNITARIO',
    'total':                'TOTAL',
    'nit':                  'NIT',
    'observaciones':        'OBSERVACIONES',

    # Clientes
    'nombre':               'NOMBRE',
    'identificacion':       'NIT',
    'nit':                  'NIT',
    'direccion':            'DIRECCION',
    'telefonos':            'TELEFONOS',
    'ciudad':               'CIUDAD',
    'email':                'EMAIL',
    'contacto':             'CONTACTO',


    # Asistencia
    'colaborador':          'COLABORADOR',
    'ingreso_real':         'INGRESO REAL',
    'salida_real':          'SALIDA REAL',
    'horas_ordinarias':     'HORAS ORDINARIAS',
    'horas_extras':         'HORAS EXTRAS',
    'registrado_por':       'REGISTRADO POR',
    'motivo':               'MOTIVO',
    'comentarios':          'COMENTARIOS',

    # Costos
    'referencia':           'REFERENCIA',
    'costo_total':          'COSTO TOTAL',
    'tiempo_minutos':       'TIEMPO MINUTOS',

    # PNC
    'proceso':              'PROCESO',
    'accion':               'ACCION',
}
