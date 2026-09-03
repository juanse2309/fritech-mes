"""Utilidades de formateo y normalización de datos."""

def to_int(valor, default=0):
    """Convierte un valor a entero de forma segura."""
    try:
        if valor is None:
            return default
        if isinstance(valor, (int, float)):
            return int(valor)
        valor = str(valor).strip().replace(',', '')
        if valor == '':
            return default
        return int(float(valor))
    except:
        return default


def to_float(valor, default=0.0):
    """Convierte un valor a float de forma segura."""
    try:
        if valor is None:
            return default
        if isinstance(valor, (int, float)):
            return float(valor)
        valor = str(valor).strip().replace(',', '')
        if valor == '':
            return default
        return float(valor)
    except:
        return default


def calcular_metricas_inyeccion(duracion_segundos, cantidad_real):
    """
    Calcula defensivamente las métricas derivadas de inyección:
    - tiempo_total_minutos = round(duracion_segundos / 60.0, 2)
    - segundos_por_unidad = round(duracion_segundos / cantidad_real, 2) si cantidad_real > 0 else 0.0
    """
    dur_seg = to_int(duracion_segundos, 0)
    cant = to_float(cantidad_real, 0.0)
    
    tiempo_minutos = round(dur_seg / 60.0, 2) if dur_seg > 0 else 0.0
    seg_unidad = round(dur_seg / cant, 2) if (dur_seg > 0 and cant > 0) else 0.0
    
    return tiempo_minutos, seg_unidad


import re

def normalizar_codigo(codigo: str) -> str:
    """
    Normaliza un código de producto SIN prefijo.
    Quita cualquier prefijo (FR-, MT-, CAR-, etc.) y devuelve la parte restante.
    Uso: consultas contra db_programacion y db_distribucion_op_pedidos
    donde los códigos se almacenan como '9890'.
    """
    if codigo is None:
        return ""
    cod = str(codigo).strip().upper()
    return re.sub(r'^[A-Z]+-', '', cod).strip()


def sql_normalizar_codigo_fr(expresion_sql: str) -> str:
    """
    Fragmento SQL reutilizable (no ejecuta nada, solo arma el texto de la query).
    Unifica un código con y sin prefijo 'FR-' al estándar 'FR-XXXX' para que
    GROUP BY y JOIN contra db_costos.referencia no fragmenten '9304' / 'FR-9304'
    en filas o resultados de join distintos.
    """
    base = f"UPPER(TRIM({expresion_sql}::text))"
    return f"CASE WHEN {base} ~ '^[0-9]+$' THEN 'FR-' || {base} ELSE {base} END"


def normalizar_codigo_sin_prefijo(codigo) -> str:
    """
    Sanitiza un código para las tablas de PNC (db_pnc_inyeccion, db_pnc_pulido,
    db_pnc_ensamble, db_pnc): quita EXCLUSIVAMENTE el prefijo 'FR-' (case-insensitive)
    para unificar 'FR-1005' y '1005' bajo una sola clave de agrupación.
    A diferencia de normalizar_codigo(), preserva intacto cualquier otro prefijo
    (MT-, CAR-, CB-...) para no pisar códigos de otras divisiones/tablas.
    """
    if codigo is None:
        return ""
    cod = str(codigo).strip().upper()
    if cod.startswith('FR-'):
        return cod[3:].strip()
    return cod


def preservar_o_normalizar_prefijo(codigo: str, prefijo_defecto: str = None) -> str:
    """
    Sanea un código de referencia SIN inventarle división.

    Regla 1 — Si el código ya trae un prefijo con guion (FR-, MT-, CAR-, CB-...)
    se retorna INTACTO, respetando sus mayúsculas tal como llegó.
    Regla 2 — Un número puro ('7011') NO recibe prefijo alguno: se retorna
    '7011'. Esta función antes convertía todo número huérfano en 'FR-7011',
    reetiquetando como FriParts referencias de otras divisiones (motos,
    carrocería) que solo se distinguen por el contexto de la orden de
    producción. Esa inyección automática queda ELIMINADA: la división es un
    dato del negocio, no algo que un formateador pueda deducir de que la
    cadena sea numérica.

    `prefijo_defecto` sobrevive únicamente como OPT-IN explícito, para el caso
    en que el contexto operativo SÍ conoce la división —p.ej. una búsqueda
    de solo lectura contra db_productos, donde la referencia FriParts vive con
    'FR-'—. Sin ese argumento la función jamás antepone nada.

    El cruce histórico entre '9843' y 'FR-9843' se resuelve en las CONSULTAS
    (sql_normalizar_codigo_fr / sql_expr_codigo_sin_prefijo_fr), no mutando la
    referencia al persistirla.
    """
    if codigo is None:
        return ""
    cod = str(codigo).strip()
    if not cod:
        return ""

    # Único camino que antepone prefijo: el llamador lo pidió explícitamente
    # y el código es un número puro y huérfano (ej. "7008" -> "FR-7008").
    if prefijo_defecto and cod.isdigit():
        return f"{prefijo_defecto}{cod}"

    # Cualquier otro caso —prefijo propio, alfanumérico o número puro— intacto.
    return cod


def sql_expr_codigo_sin_prefijo_fr(columna):
    """
    Expresión SQLAlchemy (no texto crudo) que normaliza una columna de código a
    UPPER + TRIM y sin el prefijo 'FR-', para comparaciones de igualdad en
    filtros ORM. Complementa a sql_normalizar_codigo_fr(), que hace lo inverso
    (unificar hacia 'FR-XXXX') para las queries en texto plano.

    Necesaria porque desde que preservar_o_normalizar_prefijo() dejó de inyectar
    'FR-', conviven en la misma tabla filas históricas 'FR-9843' y filas nuevas
    '9843'; un filter_by(id_codigo=...) crudo las trataría como SKUs distintos
    y duplicaría el registro en vez de actualizarlo.

    Uso: filter(sql_expr_codigo_sin_prefijo_fr(Modelo.id_codigo) ==
                normalizar_codigo_sin_prefijo(codigo))
    """
    from sqlalchemy import func
    return func.replace(func.upper(func.trim(columna)), 'FR-', '')


def limpiar_cadena(texto: str) -> str:
    """Limpia una cadena de texto eliminando espacios extras."""
    if not texto:
        return ""
    return ' '.join(str(texto).strip().split())


def to_int_seguro(valor, default=0):
    """
    Convierte un valor a entero de forma segura, tratando tanto '.' como ','
    como separadores de miles (formato numérico usado en Dashboard/Reportes).
    Distinto de to_int(): ese solo limpia comas y preserva el punto decimal.
    """
    try:
        if valor is None:
            return default
        if isinstance(valor, (int, float)):
            return int(valor)
        s = str(valor).strip().replace('.', '').replace(',', '')
        if s == '' or s.lower() == 'none':
            return default
        return int(float(s))
    except:
        return default


def clean_currency(val):
    """Convierte un string de moneda formato colombiano ('$1.500,50') a float."""
    if not val:
        return 0
    try:
        s = str(val).replace('$', '').replace('.', '').replace(',', '.').strip()
        return float(s)
    except ValueError:
        return 0


def parsear_fecha_dashboard(fecha_str):
    """Parsea DD/MM/YYYY o YYYY-MM-DD o un objeto date/datetime ya construido."""
    import datetime
    if not fecha_str:
        return None
    if isinstance(fecha_str, (datetime.date, datetime.datetime)):
        return fecha_str.date() if isinstance(fecha_str, datetime.datetime) else fecha_str
    if not isinstance(fecha_str, str):
        return None
    try:
        if '-' in fecha_str:
            return datetime.datetime.strptime(fecha_str.split(' ')[0], '%Y-%m-%d').date()
        return datetime.datetime.strptime(fecha_str.split(' ')[0], '%d/%m/%Y').date()
    except:
        return None


def resolver_operario(payload_name: str) -> str:
    """
    Resuelve el operario responsable aplicando la jerarquía universal:
    1. Si payload_name no está vacío/nulo/undefined, se limpia y se retorna.
    2. Fallback: Se busca en session['user'] (o 'user_name').
    3. Si ambos fallan, retorna 'SISTEMA'.
    """
    from flask import session
    if payload_name is not None:
        val = str(payload_name).strip()
        if val and val.lower() not in ('null', 'undefined', 'none'):
            return val
    
    session_user = session.get('user') or session.get('user_name')
    if session_user is not None:
        val_sess = str(session_user).strip()
        if val_sess and val_sess.lower() not in ('null', 'undefined', 'none'):
            return val_sess
            
    return 'SISTEMA'

# Tipos de documento que WO antepone a la identificación en
# Vista_Tabla_Direcciones. El '.' cubre las tildes, que llegan como mojibake en
# filas históricas ('Identificaci?n', 'extranjer?a').
_TIPOS_DOCUMENTO_WO = (
    r'documento\s+de\s+identificaci.n\s+extranjer[oa](?:\s+persona\s+(?:jur.dica|natural))?',
    r'nit\s+de\s+otro\s+pa.s',
    r'c.dula\s+de\s+ciudadan.a',
    r'c.dula\s+de\s+extranjer.a',
    r'tarjeta\s+de\s+extranjer.a',
    r'tarjeta\s+de\s+identidad',
    r'registro\s+civil',
    r'pasaporte',
)


def limpiar_identificacion_tercero(valor) -> str:
    """
    Normaliza una identificación de tercero al valor EXACTO con el que World
    Office la tiene registrada, para la columna 'Encab: Tercero Externo' del
    archivo de importación.

    db_clientes.identificacion se sincroniza tal cual desde
    Vista_Tabla_Direcciones de WO (ver backend/integration/agente_wo_clientes.py),
    y ahí conviven tres formatos:
      'NIT 830102900 3'                                    -> '830102900'
      'CC 52306854'                                        -> '52306854'
      'Documento de identificación extranjero 13167415 1'  -> '13167415'
      'Documento de Identificación extranjero Persona
       Jurídica J-31284787 5'                              -> 'J-31284787'
      'J-31284787'                                         -> 'J-31284787'
      'X0028B9MR7'                                         -> 'X0028B9MR7'

    Lo que se quita es SOLO el envoltorio que WO antepone (tipo de documento)
    y el dígito de verificación suelto al final. El identificador en sí se
    devuelve intacto, incluidas sus letras: extraer "el primer grupo de
    dígitos" convertía el RIF venezolano 'J-31284787' en '31284787' y WO
    rechazaba la carga porque el tercero no concordaba.

    Nota: la tilde de 'Identificación' llega corrupta en filas históricas
    (mojibake), por eso el patrón usa '.' en esa posición.
    """
    if valor is None:
        return ""
    ident = str(valor).strip()
    if not ident:
        return ""

    # 1. Nombre completo del tipo de documento tal como lo escribe WO.
    ident = re.sub(r'(?i)^(?:' + '|'.join(_TIPOS_DOCUMENTO_WO) + r')\s+', '', ident).strip()

    # 2. Prefijo corto de tipo de documento. Exige separador después del prefijo
    #    para no morder identificaciones que empiezan con esas letras
    #    (p.ej. 'CCQ211015S64', de un tercero mexicano).
    ident = re.sub(r'(?i)^(nit|cc|ce|ti|nuip|rut|c\.c\.|c\.e\.|t\.i\.)[\s\.\-:]+', '', ident).strip()

    # 3. Dígito de verificación suelto al final ('900315300 3' -> '900315300').
    #    Solo separado por espacio: un guion final ('12345678-9') puede ser
    #    parte del identificador extranjero.
    ident = re.sub(r'\s+\d$', '', ident).strip()

    # 4. Separadores de miles en identificaciones puramente numéricas.
    if re.fullmatch(r'[\d\s\.]+', ident):
        ident = re.sub(r'[\s\.]', '', ident)

    # 5. Basura de separación que quedó suelta en los extremos. Devolver '' es
    #    preferible a devolver un resto sin sentido: WO reporta el tercero
    #    vacío en vez de cargarlo contra un tercero equivocado.
    ident = ident.strip(' |,;:-')
    if not any(ch.isalnum() for ch in ident):
        return ""

    return ident
