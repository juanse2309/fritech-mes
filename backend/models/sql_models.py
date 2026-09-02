"""
sql_models.py — Modelos SQLAlchemy 100% SQL-First
Tablas planas, sin relationships, sin ForeignKey.
extend_existing=True previene errores de Mapper al recargar el módulo.
"""
from datetime import datetime
import uuid
import time
import random
from sqlalchemy.orm import validates
from backend.core.sql_database import db
from backend.utils.formatters import normalizar_codigo_sin_prefijo, preservar_o_normalizar_prefijo
from backend.utils.time_utils import get_colombia_time


class Producto(db.Model):
    __tablename__ = 'db_productos'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # unique=True: soporta INSERT ... ON CONFLICT(codigo_sistema) para el UPSERT
    # masivo de World Office (ver ProductoRepository.upsert_productos_wo). La
    # constraint real en Postgres (uq_productos_codigo_sistema) la crea
    # scratch/migrar_productos_unique.py -- este flag solo refleja el estado
    # esperado del esquema, no la aplica por si solo.
    codigo_sistema  = db.Column(db.String(50),  unique=True, index=True, nullable=True)
    id_codigo       = db.Column(db.String(50),  index=True, nullable=True)
    descripcion     = db.Column(db.String(500), nullable=True)
    precio          = db.Column(db.Numeric(18, 2), default=0)
    por_pulir       = db.Column(db.Numeric(18, 2), default=0)
    p_terminado     = db.Column(db.Numeric(18, 2), default=0)
    comprometido    = db.Column(db.Numeric(18, 2), default=0)
    producto_ensamblado = db.Column(db.Numeric(18, 2), default=0)
    stock_minimo    = db.Column(db.Numeric(18, 2), default=10)
    stock_maximo    = db.Column(db.Numeric(18, 2), default=100)
    punto_reorden   = db.Column(db.Numeric(18, 2), default=20)
    imagen          = db.Column(db.String(500),  nullable=True)
    oem             = db.Column(db.String(200),  nullable=True)
    dolares         = db.Column(db.Numeric(18, 2), default=0)
    stock_bodega    = db.Column(db.Numeric(18, 2), default=0)
    diametro_interno = db.Column(db.Numeric(10, 2), nullable=True)
    diametro_externo = db.Column(db.Numeric(10, 2), nullable=True)
    altura          = db.Column(db.Numeric(10, 2), nullable=True)
    pared           = db.Column(db.Numeric(10, 2), nullable=True)


class ProduccionInyeccion(db.Model):
    __tablename__ = 'db_inyeccion'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_inyeccion    = db.Column(db.String(80),  index=True, nullable=True)
    fecha_inicia    = db.Column(db.DateTime,    index=True, nullable=True)
    fecha_fin       = db.Column(db.DateTime,    nullable=True)
    id_codigo       = db.Column(db.String(50),  index=True, nullable=True)
    responsable     = db.Column(db.String(150), nullable=True)
    maquina         = db.Column(db.String(80),  nullable=True)
    cantidad_real   = db.Column(db.BigInteger,  default=0)
    estado          = db.Column(db.String(50),  nullable=True)
    # VARCHAR, no Integer (migrado 2026-08-28, ver migrate_molde_texto.py):
    # el código real de molde no es numérico -- '5002A', '9304 moneda', etc.,
    # verificado contra rel_producto_molde (314 códigos distintos reales).
    molde           = db.Column(db.String(50),  nullable=True)
    cavidades       = db.Column(db.Integer,     default=1) # Columna principal (int4)
    
    # --- Audit Trail 3 Firmas ---
    programado_por  = db.Column(db.String(150), nullable=True)
    iniciado_por    = db.Column(db.String(150), nullable=True)
    finalizado_por  = db.Column(db.String(150), nullable=True)
    validado_por    = db.Column(db.String(150), nullable=True)
    
    # --- Columnas Operativas ---
    hora_llegada         = db.Column(db.String(20),  nullable=True)
    hora_inicio          = db.Column(db.String(20),  nullable=True)
    hora_termina         = db.Column(db.String(20),  nullable=True)
    cant_contador        = db.Column(db.BigInteger,  default=0) # Sincronizado con BigInt real
    almacen_destino      = db.Column(db.String(100), default='POR PULIR')
    codigo_ensamble      = db.Column(db.String(100), nullable=True)
    orden_produccion     = db.Column(db.String(100), nullable=True)
    observaciones        = db.Column(db.Text,        nullable=True)
    produccion_teorica   = db.Column(db.Numeric(12, 2), default=0)
    peso_bujes           = db.Column(db.Numeric(12, 4), default=0)
    pnc_total            = db.Column(db.BigInteger,  default=0)
    pnc_detalle          = db.Column(db.Text,        nullable=True)
    peso_lote            = db.Column(db.Numeric(18, 4), default=0)
    entrada              = db.Column(db.Numeric(18, 2), default=0)
    salida               = db.Column(db.Numeric(18, 2), default=0)
    
    # --- Métricas ---
    duracion_segundos    = db.Column(db.Integer, default=0)
    tiempo_total_minutos = db.Column(db.Numeric(10, 2), default=0)
    segundos_por_unidad  = db.Column(db.Integer, default=0)
    departamento         = db.Column(db.String(100), default='Inyeccion')

    @validates('id_codigo')
    def _sanitizar_id_codigo(self, key, value):
        """Blindaje obligatorio: db_inyeccion persiste la referencia TAL CUAL la
        reportó la planta —preservando 'MT-', 'CAR-', 'CB-' o la ausencia de
        prefijo— sin reetiquetar números puros como 'FR-'. La unificación entre
        'FR-9843' y '9843' se resuelve en las consultas (sql_normalizar_codigo_fr /
        sql_expr_codigo_sin_prefijo_fr), nunca alterando el dato al escribirlo."""
        return preservar_o_normalizar_prefijo(value) if value else value


class PncInyeccion(db.Model):
    __tablename__ = 'db_pnc_inyeccion'
    __table_args__ = {'extend_existing': True}

    id_row           = db.Column(db.Integer, primary_key=True, default=lambda: int(time.time() % 100000000) + random.randint(100000000, 900000000))
    id_pnc_inyeccion = db.Column(db.String(80), index=True, default=lambda: uuid.uuid4().hex[:8])
    id_inyeccion     = db.Column(db.String(80), index=True)
    id_codigo        = db.Column(db.String(50), index=True)
    cantidad         = db.Column(db.Numeric(18, 2), default=0)
    criterio         = db.Column(db.String(200), nullable=True)
    codigo_ensamble  = db.Column(db.String(50), nullable=True)

    # --- Trazabilidad de personas (migrate_pnc_responsable_validado_por.py) ---
    # responsable: operario de INYECCIÓN dueño del lote (db_inyeccion.responsable).
    # validado_por: quien audita la merma. Se llena EXCLUSIVAMENTE desde la
    # identidad autenticada (JWT) en InyeccionService.validar_lote — nunca desde
    # el payload, que es autorreportado y puede falsificar la firma.
    responsable      = db.Column(db.String(150), nullable=True)
    validado_por     = db.Column(db.String(150), nullable=True)

    # --- Desglose de Defectos ---
    quemado_manchado         = db.Column(db.Numeric(18, 2), default=0)
    incompleto_falta_llenado = db.Column(db.Numeric(18, 2), default=0)
    rebaba_excesiva          = db.Column(db.Numeric(18, 2), default=0)
    burbuja_porosidad        = db.Column(db.Numeric(18, 2), default=0)
    deformacion_rechupado    = db.Column(db.Numeric(18, 2), default=0)

    @validates('id_codigo')
    def _sanitizar_id_codigo(self, key, value):
        """Blindaje obligatorio: ningún registro puede persistirse con prefijo 'FR-' colgando."""
        return normalizar_codigo_sin_prefijo(value) if value else value


class PncPulido(db.Model):
    __tablename__ = 'db_pnc_pulido'
    __table_args__ = {'extend_existing': True}

    id_row           = db.Column(db.Integer, primary_key=True, default=lambda: int(time.time() % 100000000) + random.randint(100000000, 900000000))
    id_pnc_pulido    = db.Column(db.String(80), index=True, default=lambda: uuid.uuid4().hex[:8])
    id_pulido        = db.Column(db.Text, index=True) # OID 25
    codigo           = db.Column(db.String(50), index=True)
    cantidad         = db.Column(db.Numeric(18, 2), default=0)
    criterio         = db.Column(db.String(200), nullable=True)
    codigo_ensamble  = db.Column(db.String(50), nullable=True)

    # --- Trazabilidad de personas (migrate_pnc_responsable_validado_por.py) ---
    # responsable: operaria de PULIDO que produjo la merma. Llega en el DTO de
    # validación (items[].operaria_pulido); es obligatoria si hay PNC de pulido.
    # validado_por: quien audita, desde el JWT. Ver nota en PncInyeccion.
    responsable      = db.Column(db.String(150), nullable=True)
    validado_por     = db.Column(db.String(150), nullable=True)

    @validates('codigo')
    def _sanitizar_codigo(self, key, value):
        """Blindaje obligatorio: ningún registro puede persistirse con prefijo 'FR-' colgando."""
        return normalizar_codigo_sin_prefijo(value) if value else value


class PncEnsamble(db.Model):
    __tablename__ = 'db_pnc_ensamble'
    __table_args__ = {'extend_existing': True}

    id_row           = db.Column(db.Integer, primary_key=True, default=lambda: int(time.time() % 100000000) + random.randint(100000000, 900000000))
    id_pnc_ensamble  = db.Column(db.String(80), index=True, default=lambda: uuid.uuid4().hex[:8])
    id_ensamble      = db.Column(db.String(80), index=True)
    id_codigo        = db.Column(db.String(50), index=True)
    cantidad         = db.Column(db.Numeric(18, 2), default=0)
    criterio         = db.Column(db.String(200), nullable=True)
    codigo_ensamble  = db.Column(db.String(50), nullable=True)

    @validates('id_codigo')
    def _sanitizar_id_codigo(self, key, value):
        """Blindaje obligatorio: ningún registro puede persistirse con prefijo 'FR-' colgando."""
        return normalizar_codigo_sin_prefijo(value) if value else value


class ProduccionPulido(db.Model):
    __tablename__ = 'db_pulido'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_pulido       = db.Column(db.String(100), index=True, nullable=True) # VARCHAR
    fecha           = db.Column(db.DateTime, index=True, nullable=True)
    # default=get_colombia_time: fallback Python-side por si algún insert no pasa por
    # PulidoService (que ya fija fecha_registro explícitamente con la misma fuente de
    # verdad). server_default=func.now() queda solo como red de seguridad a nivel DB
    # (nunca se dispara mientras SQLAlchemy siga enviando el default de Python).
    fecha_registro  = db.Column(db.DateTime, index=True, default=get_colombia_time, server_default=db.func.now())
    codigo          = db.Column(db.String(100), index=True, nullable=True) # VARCHAR
    responsable     = db.Column(db.String(200), nullable=True) # VARCHAR
    cantidad_real   = db.Column(db.Integer,     default=0) # int4 en DB
    pnc_inyeccion   = db.Column(db.Integer,     default=0)
    pnc_pulido      = db.Column(db.Integer,     default=0)
    hora_inicio     = db.Column(db.DateTime,    nullable=True)
    hora_fin        = db.Column(db.DateTime,    nullable=True)
    estado          = db.Column(db.String(50),  default='FINALIZADO')
    tiempo_total_minutos = db.Column(db.Numeric(10, 2), default=0)
    duracion_segundos    = db.Column(db.Integer, default=0)
    segundos_por_unidad  = db.Column(db.Numeric(10, 2), default=0)
    orden_produccion     = db.Column(db.String(100), nullable=True) # VARCHAR
    observaciones        = db.Column(db.Text,        nullable=True) # TEXT
    criterio_pnc_inyeccion = db.Column(db.Text, nullable=True)
    criterio_pnc_pulido    = db.Column(db.Text, nullable=True)
    departamento           = db.Column(db.String(100), default='PULIDO')
    lote                   = db.Column(db.String(100), nullable=True) # VARCHAR
    cantidad_recibida      = db.Column(db.Numeric(18, 2), default=0)
    almacen_destino        = db.Column(db.String(100), default='P. TERMINADO')
    hora_pausa             = db.Column(db.DateTime,    nullable=True)
    tiempo_pausa_acumulado = db.Column(db.Integer,     default=0)


class PausasPulido(db.Model):
    __tablename__ = 'db_pausas_pulido'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_pulido       = db.Column(db.Text,     index=True, nullable=True) # OID 25
    motivo          = db.Column(db.String(200), nullable=True)
    hora_inicio     = db.Column(db.DateTime,    nullable=True)
    hora_fin        = db.Column(db.DateTime,    nullable=True)


class PulidoOverride(db.Model):
    """
    Bitácora de bloqueos duros de Pulido (fecha distinta a hoy, o cantidad
    que excede lo inyectado) saltados por un ADMIN -- plan 2026-08-28. No
    reemplaza el log del servidor, es la fuente para el reporte que la
    jefa pidió explícitamente para restar puntos por no llevar la app al
    día: "avisaran a uno como admin para dejarlas subir y dejar un reporte
    para restarles puntos".
    """
    __tablename__ = 'db_pulido_overrides'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_pulido       = db.Column(db.String(100), index=True, nullable=True)
    tipo            = db.Column(db.String(20),  nullable=False)  # FECHA / CANTIDAD
    operaria        = db.Column(db.String(200), nullable=True)
    autorizado_por  = db.Column(db.String(150), nullable=True)
    motivo          = db.Column(db.Text,        nullable=True)
    detalle         = db.Column(db.Text,        nullable=True)
    creado_en       = db.Column(db.DateTime,    default=get_colombia_time)


class PulidoPendienteAutorizacion(db.Model):
    """
    Cola de reportes de Pulido bloqueados (fecha distinta a hoy o cantidad
    que excede lo inyectado) esperando que un ADMIN los autorice -- plan
    2026-09-01. Antes, un reporte bloqueado para una operaria normal
    simplemente se perdía (no quedaba guardado en ningún lado): si no había
    un ADMIN físicamente en su tablet para autorizarlo en el momento, tocaba
    que se acordara y avisara. Ahora el intento se guarda aquí con el
    payload completo, y el ADMIN lo autoriza o rechaza desde el Panel de
    Supervisión, desde su propio usuario, sin tocar la sesión de la operaria.
    """
    __tablename__ = 'db_pulido_pendientes_autorizacion'
    __table_args__ = {'extend_existing': True}

    id                  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_pulido           = db.Column(db.String(100), index=True, nullable=False)
    responsable         = db.Column(db.String(200), nullable=True)
    codigo              = db.Column(db.String(100), nullable=True)
    orden_produccion    = db.Column(db.String(100), nullable=True)
    lote                = db.Column(db.String(100), nullable=True)
    cantidad_real       = db.Column(db.Numeric(12, 2), nullable=True)
    fecha_trabajo       = db.Column(db.String(20),  nullable=True)  # fecha que puso la operaria en el formulario
    tipo_bloqueo        = db.Column(db.String(50),  nullable=False)  # ej. PULIDO_FECHA_BLOQUEADA / PULIDO_CANTIDAD_EXCEDE_INYECTADO
    motivo_bloqueo      = db.Column(db.Text,        nullable=True)  # mensaje real que devolvio el backend
    payload_json        = db.Column(db.Text,        nullable=False)  # payload completo, para poder re-enviarlo tal cual si se autoriza
    estado              = db.Column(db.String(20),  default='PENDIENTE')  # PENDIENTE / AUTORIZADO / RECHAZADO
    resuelto_por        = db.Column(db.String(150), nullable=True)
    motivo_resolucion   = db.Column(db.Text,        nullable=True)
    creado_en           = db.Column(db.DateTime,    default=get_colombia_time)
    resuelto_en         = db.Column(db.DateTime,    nullable=True)


class ProgramacionPulido(db.Model):
    """
    Programación diaria de Pulido (plan 2026-09-02): el ADMIN arma, por
    operaria, la cola de qué pulir hoy y en qué orden (OP + referencia +
    cantidad objetivo + prioridad). Separada de ProduccionPulido (la
    ejecución real, con horas/cantidad real/PNC) -- mismo patrón ya
    probado en Inyección (ver ProgramacionInyeccion / ProduccionInyeccion):
    la fila de programación es el plan, `id_pulido` la vincula con la fila
    de ejecución real una vez que la operaria le da "Iniciar".
    """
    __tablename__ = 'db_programacion_pulido'
    __table_args__ = {'extend_existing': True}

    id                  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha               = db.Column(db.Date, index=True, nullable=False)
    orden_produccion    = db.Column(db.String(100), index=True, nullable=False)
    codigo              = db.Column(db.String(100), index=True, nullable=False)
    lote                = db.Column(db.String(100), nullable=True)  # lo que diga la bolsa física, junto a OP/referencia
    cantidad_objetivo   = db.Column(db.Numeric(18, 2), default=0)
    operaria            = db.Column(db.String(200), index=True, nullable=False)
    orden_prioridad     = db.Column(db.Integer, default=1)
    estado              = db.Column(db.String(30), default='PROGRAMADO')  # PROGRAMADO / EN_PROCESO / FINALIZADO
    responsable_planta  = db.Column(db.String(150), nullable=True)  # ADMIN que armó la cola
    observaciones       = db.Column(db.Text, nullable=True)
    id_pulido           = db.Column(db.String(100), index=True, nullable=True)  # vínculo con db_pulido tras iniciar
    creado_en           = db.Column(db.DateTime, default=get_colombia_time)


class RawVentas(db.Model):
    __tablename__ = 'db_ventas'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha           = db.Column(db.Date,        index=True, nullable=True)
    documento       = db.Column(db.String(80),  index=True, nullable=True)
    nombres         = db.Column(db.String(200), index=True, nullable=True)
    productos       = db.Column(db.String(100), index=True, nullable=True)
    cantidad        = db.Column(db.Numeric(18, 2), default=0)
    total_ingresos  = db.Column(db.Numeric(18, 2), default=0)
    precio_promedio = db.Column(db.Numeric(18, 2), default=0)
    clasificacion   = db.Column(db.String(80),  nullable=True)
    vendedor        = db.Column(db.String(150), index=True, nullable=True)
    zona            = db.Column(db.String(100), index=True, nullable=True)
    # estado no existe en la DB real
    # Detalle de factura WO -- nullable: solo se llenan si agente_wo_comercial.py
    # logro detectar la columna correspondiente en la vista de WO (ver ese
    # archivo). Filas sincronizadas antes de esta ampliacion quedan en NULL.
    descripcion_producto  = db.Column(db.String(255), nullable=True)
    iva                    = db.Column(db.Numeric(18, 2), nullable=True)
    identificacion_cliente = db.Column(db.String(50), nullable=True)


class DbClientes(db.Model):
    __tablename__ = 'db_clientes'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nombre          = db.Column(db.String(200), index=True, nullable=True)
    # NO unique: un mismo NIT puede tener varias direcciones/sucursales
    # (confirmado contra Vista_Tabla_Direcciones de World Office, ej. NIT
    # 830008309 tiene sede en Bogota y en Funza). La llave real de UPSERT
    # por fila es id_direccion_wo (IdTerceroDireccion de WO), no identificacion.
    identificacion  = db.Column(db.String(50),  index=True, nullable=True)
    direccion       = db.Column(db.String(300), nullable=True)
    telefonos       = db.Column(db.String(100), nullable=True)
    ciudad          = db.Column(db.String(100), nullable=True)
    # id_direccion_wo: IdTerceroDireccion de Vista_Tabla_Direcciones (WO).
    # Llave natural de UPSERT por sucursal/direccion. Nullable porque las filas
    # capturadas manualmente antes de esta sincronizacion no tienen este id.
    id_direccion_wo = db.Column(db.Integer, unique=True, nullable=True, index=True)



class RegistroAsistencia(db.Model):
    __tablename__ = 'db_asistencia'
    __table_args__ = {'extend_existing': True}

    id                  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha               = db.Column(db.Date,        index=True, nullable=True)
    colaborador         = db.Column(db.String(150), index=True, nullable=True)
    ingreso_real        = db.Column(db.String(20),  nullable=True)
    salida_real         = db.Column(db.String(20),  nullable=True)
    horas_ordinarias    = db.Column(db.Numeric(10, 2), default=0)
    horas_extras        = db.Column(db.Numeric(10, 2), default=0)
    # El campo en la BD real se llama 'jefe'

    estado              = db.Column(db.String(50),  nullable=True)
    estado_pago         = db.Column(db.String(50),  default='PENDIENTE')
    motivo              = db.Column(db.String(255), nullable=True)
    comentarios         = db.Column(db.Text,        nullable=True)
    
    # --- Auditoría de Creación y Edición ---
    registrado_por      = db.Column(db.String(150), nullable=True)
    editado_por         = db.Column(db.String(150), nullable=True)
    fecha_edicion       = db.Column(db.DateTime,    nullable=True)
    motivo_edicion      = db.Column(db.String(255), nullable=True)


class Pedido(db.Model):
    __tablename__ = 'db_pedidos'
    __table_args__ = {'extend_existing': True}

    id_sql          = db.Column("id", db.Integer, primary_key=True, autoincrement=True)
    fecha           = db.Column(db.Date,        index=True, nullable=True)
    hora            = db.Column(db.String(20),  nullable=True)
    id_pedido       = db.Column(db.String(80),  index=True, nullable=True) # ID PEDIDO
    vendedor        = db.Column(db.String(150), nullable=True)
    cliente         = db.Column(db.String(200), index=True, nullable=True)
    nit             = db.Column(db.String(50),  nullable=True)
    direccion       = db.Column(db.String(255), nullable=True)
    ciudad          = db.Column(db.String(100), nullable=True)
    forma_de_pago   = db.Column(db.String(100), nullable=True)
    descuento       = db.Column(db.String(50),  nullable=True)
    wo_consecutivo  = db.Column(db.String(50),  nullable=True)
    id_codigo       = db.Column(db.String(100), index=True, nullable=True) # ID CODIGO
    descripcion     = db.Column(db.String(500), nullable=True)
    cantidad        = db.Column(db.Numeric(18, 2), default=0)
    precio_unitario = db.Column(db.Numeric(18, 2), default=0)
    # Trazabilidad de conversión USD->COP para pedidos de exportación (botón
    # "Consultar TRM" del frontend). NULL cuando el item se cotizó directo en
    # COP -- ver backend/services/trm_service.py.
    precio_usd      = db.Column(db.Numeric(18, 2), nullable=True)
    trm_aplicada    = db.Column(db.Numeric(18, 4), nullable=True)
    total           = db.Column(db.Numeric(18, 2), default=0)
    estado          = db.Column(db.String(50),  nullable=True) # PENDIENTE, ALISTADO, etc.
    progreso        = db.Column(db.String(10),  default='0%')
    cant_alistada   = db.Column(db.String(50),  default='0')
    progreso_despacho = db.Column(db.String(10), default='0%')
    delegado_a      = db.Column(db.String(150), nullable=True)
    observaciones   = db.Column(db.Text,        nullable=True)


class DbClienteEquivalencias(db.Model):
    __tablename__ = 'db_cliente_equivalencias'
    __table_args__ = {'extend_existing': True}

    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)
    alias            = db.Column(db.String(255), unique=True, nullable=False, index=True)
    nombre_canonical = db.Column(db.String(255), nullable=False)
    created_at       = db.Column(db.DateTime, server_default=db.func.now())


class Ensamble(db.Model):
    __tablename__ = 'db_ensambles'
    __table_args__ = {'extend_existing': True}

    id             = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_ensamble    = db.Column(db.String(80),  nullable=True, default=lambda: uuid.uuid4().hex[:8]) # varchar
    id_codigo      = db.Column(db.Text,  index=True, nullable=True) # TEXT
    # FK lógica (sin constraint formal, mismo patrón que buje_ensamble/id_ensamble
    # en esta tabla) hacia programacion_ensamble.id_prog. Vincula este registro de
    # producción con la meta específica que se reportó, para que el recálculo de
    # cantidad_realizada en EnsambleService.reportar_multi no mezcle avance entre
    # metas distintas del mismo producto (ver migrate_ensamble_add_id_prog.py).
    id_prog        = db.Column(db.Integer, index=True, nullable=True)
    responsable    = db.Column(db.Text, nullable=True) # TEXT
    cantidad       = db.Column(db.Integer,     default=0)
    hora_inicio    = db.Column(db.DateTime,    nullable=True)
    hora_fin       = db.Column(db.DateTime,    nullable=True)
    fecha          = db.Column(db.DateTime,    index=True, nullable=True)
    observaciones  = db.Column(db.Text,        nullable=True) # TEXT
    # Campos de trazabilidad (Asegurar que coincidan con la DB real)
    op_numero      = db.Column(db.Text, nullable=True) # TEXT
    almacen_para_descargar = db.Column(db.String(100), nullable=True)
    almacen_destino        = db.Column(db.String(100), nullable=True)
    qty                    = db.Column(db.Numeric(18, 4), default=1)
    buje_ensamble  = db.Column(db.Text, nullable=True) # TEXT
    buje_origen    = db.Column(db.String(100), nullable=True)
    consumo_total  = db.Column(db.Numeric(18, 4), default=0)
    # Métricas Globales
    duracion_segundos    = db.Column(db.Integer, default=0)
    tiempo_total_minutos = db.Column(db.Numeric(10, 2), default=0)
    segundos_por_unidad  = db.Column(db.Numeric(10, 2), default=0)
    departamento         = db.Column(db.String(100), default='Ensamble')
    estado               = db.Column(db.String(50),  default='FINALIZADO') # EN_PROCESO, FINALIZADO
    hora_pausa           = db.Column(db.DateTime,    nullable=True)
    tiempo_pausa_acumulado = db.Column(db.Integer,     default=0)


class ProduccionPintura(db.Model):
    """Subproceso de Ensamble: registra insumo (ml) consumido y rendimiento
    por unidad. Sin FK -- se ancla al padre solo por id_ensamble (texto),
    mismo patrón que PncEnsamble."""
    __tablename__ = 'db_pintura'
    __table_args__ = {'extend_existing': True}

    id                     = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_pintura             = db.Column(db.String(80), index=True, default=lambda: uuid.uuid4().hex[:8])
    id_ensamble            = db.Column(db.Text, index=True, nullable=True)
    id_codigo              = db.Column(db.Text, index=True, nullable=True)
    responsable            = db.Column(db.Text, nullable=True)
    insumo_pintura         = db.Column(db.String(100), nullable=True)  # color/tipo de insumo
    cantidad               = db.Column(db.Integer, default=0)          # unidades pintadas
    ml_insumo_utilizado    = db.Column(db.Numeric(18, 2), default=0)
    rendimiento_ml_unidad  = db.Column(db.Numeric(10, 4), default=0)   # calculado en PinturaService.finalizar
    op_numero              = db.Column(db.Text, nullable=True)
    fecha                  = db.Column(db.DateTime, index=True, nullable=True)
    hora_inicio            = db.Column(db.DateTime, nullable=True)
    hora_fin               = db.Column(db.DateTime, nullable=True)
    hora_pausa             = db.Column(db.DateTime, nullable=True)
    tiempo_pausa_acumulado = db.Column(db.Integer, default=0)
    duracion_segundos      = db.Column(db.Integer, default=0)
    tiempo_total_minutos   = db.Column(db.Numeric(10, 2), default=0)
    segundos_por_unidad    = db.Column(db.Numeric(10, 2), default=0)
    pnc_cantidad           = db.Column(db.Integer, default=0)
    observaciones          = db.Column(db.Text, nullable=True)
    estado                 = db.Column(db.String(50), default='EN_PROCESO')  # EN_PROCESO, PAUSADO, FINALIZADO
    departamento           = db.Column(db.String(100), default='Pintura')

    @validates('id_codigo')
    def _sanitizar_id_codigo(self, key, value):
        return preservar_o_normalizar_prefijo(value) if value else value


class ProduccionRayada(db.Model):
    """Subproceso de Ensamble: control de tiempos por referencia de carcaza."""
    __tablename__ = 'db_rayada'
    __table_args__ = {'extend_existing': True}

    id                     = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_rayada              = db.Column(db.String(80), index=True, default=lambda: uuid.uuid4().hex[:8])
    id_ensamble            = db.Column(db.Text, index=True, nullable=True)
    id_codigo              = db.Column(db.Text, index=True, nullable=True)  # referencia de carcaza (CAR-)
    responsable            = db.Column(db.Text, nullable=True)
    cantidad               = db.Column(db.Integer, default=0)
    op_numero              = db.Column(db.Text, nullable=True)
    fecha                  = db.Column(db.DateTime, index=True, nullable=True)
    hora_inicio            = db.Column(db.DateTime, nullable=True)
    hora_fin               = db.Column(db.DateTime, nullable=True)
    hora_pausa             = db.Column(db.DateTime, nullable=True)
    tiempo_pausa_acumulado = db.Column(db.Integer, default=0)
    duracion_segundos      = db.Column(db.Integer, default=0)
    tiempo_total_minutos   = db.Column(db.Numeric(10, 2), default=0)
    segundos_por_unidad    = db.Column(db.Numeric(10, 2), default=0)
    pnc_cantidad           = db.Column(db.Integer, default=0)
    observaciones          = db.Column(db.Text, nullable=True)
    estado                 = db.Column(db.String(50), default='EN_PROCESO')  # EN_PROCESO, PAUSADO, FINALIZADO
    departamento           = db.Column(db.String(100), default='Rayada')

    @validates('id_codigo')
    def _sanitizar_id_codigo(self, key, value):
        return preservar_o_normalizar_prefijo(value) if value else value


class ProduccionHorno(db.Model):
    """Subproceso de Ensamble: registro de temperatura de ingreso/salida y
    tiempo de curado por lote en horno."""
    __tablename__ = 'db_hornos'
    __table_args__ = {'extend_existing': True}

    id                     = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_horno_registro      = db.Column(db.String(80), index=True, default=lambda: uuid.uuid4().hex[:8])
    id_ensamble            = db.Column(db.Text, index=True, nullable=True)
    id_codigo              = db.Column(db.Text, index=True, nullable=True)
    horno_numero           = db.Column(db.String(50), nullable=True)  # identificador del horno físico
    responsable            = db.Column(db.Text, nullable=True)
    cantidad               = db.Column(db.Integer, default=0)
    temperatura_ingreso_c  = db.Column(db.Numeric(6, 2), nullable=True)
    temperatura_salida_c   = db.Column(db.Numeric(6, 2), nullable=True)
    op_numero              = db.Column(db.Text, nullable=True)
    fecha                  = db.Column(db.DateTime, index=True, nullable=True)
    hora_inicio            = db.Column(db.DateTime, nullable=True)
    hora_fin               = db.Column(db.DateTime, nullable=True)
    duracion_segundos      = db.Column(db.Integer, default=0)
    tiempo_total_minutos   = db.Column(db.Numeric(10, 2), default=0)
    pnc_cantidad           = db.Column(db.Integer, default=0)
    observaciones          = db.Column(db.Text, nullable=True)
    estado                 = db.Column(db.String(50), default='EN_HORNO')  # EN_HORNO, FINALIZADO
    departamento           = db.Column(db.String(100), default='Hornos')

    @validates('id_codigo')
    def _sanitizar_id_codigo(self, key, value):
        return preservar_o_normalizar_prefijo(value) if value else value


class Pnc(db.Model):
    __tablename__ = 'db_pnc'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha           = db.Column(db.DateTime,    index=True, nullable=True)
    id_pnc          = db.Column(db.String(80),  nullable=True)
    id_codigo       = db.Column(db.String(50),  index=True, nullable=True)
    cantidad        = db.Column(db.Numeric(18, 2), default=0)
    criterio        = db.Column(db.String(255), nullable=True)
    codigo_ensamble = db.Column(db.String(50),  nullable=True)
    responsable     = db.Column(db.String(150), nullable=True)

    @validates('id_codigo')
    def _sanitizar_id_codigo(self, key, value):
        """Blindaje obligatorio: ningún registro puede persistirse con prefijo 'FR-' colgando."""
        return normalizar_codigo_sin_prefijo(value) if value else value


class BujeRevuelto(db.Model):
    __tablename__ = 'db_bujes_revueltos'
    __table_args__ = {'extend_existing': True}

    id_bujes_revueltos = db.Column(db.String(80), primary_key=True, default=lambda: uuid.uuid4().hex[:8])
    id_pulido        = db.Column(db.String(100), index=True) # VARCHAR
    id_codigo        = db.Column(db.String(50), index=True) 
    cantidad         = db.Column(db.Numeric(18, 2), default=0)
    codigo_ensamble  = db.Column(db.String(50), nullable=True)
    responsable      = db.Column(db.String(150), nullable=True)


class DbCostos(db.Model):
    __tablename__ = 'db_costos'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    referencia      = db.Column(db.String(80),  index=True, nullable=True)
    costo_total     = db.Column(db.Numeric(18, 2), default=0)
    precio_de_venta = db.Column(db.String(50),  nullable=True) # Juan Sebastian: Puede contener '$' y puntos
    puntos_pieza    = db.Column(db.Numeric(10, 2), default=1)
    tiempo_minutos  = db.Column(db.Numeric(10, 2), default=0)


class Usuario(db.Model):
    __tablename__ = 'db_usuarios'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username        = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password_hash   = db.Column(db.String(255), nullable=False)
    nombre_completo = db.Column(db.String(150), nullable=True)
    alias_vendedor_wo = db.Column(db.String(150), nullable=True) # Nombre exacto tal como llega en db_ventas.vendedor (World Office), para match estricto sin depender del nombre de login
    rol             = db.Column(db.String(50),  default='operario')
    cedula          = db.Column(db.String(20),  nullable=True) # Identificación oficial del usuario
    nit_empresa     = db.Column(db.String(50),  nullable=True, index=True) # Identificación empresarial para clientes B2B
    departamento    = db.Column(db.String(100), nullable=True) # Area asignada (ej: INYECCION, PULIDO)
    hora_entrada    = db.Column(db.String(20),  nullable=True) # Horario oficial
    hora_salida     = db.Column(db.String(20),  nullable=True) # Horario oficial
    activo          = db.Column(db.Boolean,     default=True)
    ultimo_acceso   = db.Column(db.DateTime,    default=datetime.utcnow)
class CorteNomina(db.Model):
    __tablename__ = 'db_cortes_nomina'
    __table_args__ = {'extend_existing': True}

    id_corte          = db.Column(db.String(50),  primary_key=True)
    fecha_corte       = db.Column(db.DateTime,    default=datetime.utcnow)
    usuario_que_corta = db.Column(db.String(150), nullable=False)
    periodo_inicio    = db.Column(db.Date,        nullable=True)
    periodo_fin       = db.Column(db.Date,        nullable=True)
    total_registros   = db.Column(db.Integer,     nullable=True)
    usuario_autoriza  = db.Column(db.String(150), nullable=True)
    estado            = db.Column(db.String(50),  nullable=True)
    division          = db.Column(db.String(50),  nullable=True)


class Maquina(db.Model):
    __tablename__ = 'db_maquinas'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nombre          = db.Column(db.String(100), unique=True, nullable=False)
    activa          = db.Column(db.Boolean, default=True)
    descripcion     = db.Column(db.String(255), nullable=True)


class ProgramacionInyeccion(db.Model):
    __tablename__ = 'db_programacion'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha           = db.Column(db.Date, index=True)
    codigo_sistema  = db.Column(db.String(50), index=True)
    maquina         = db.Column(db.String(80))
    cantidad        = db.Column(db.Numeric(18, 2), default=0)
    estado          = db.Column(db.String(50), default='PENDIENTE')
    # VARCHAR, no Integer -- ver mismo comentario en ProduccionInyeccion.molde.
    molde           = db.Column(db.String(50), nullable=True)
    cavidades       = db.Column(db.Integer, default=1)
    responsable_planta = db.Column(db.String(150), nullable=True)
    observaciones   = db.Column(db.Text, nullable=True)
    op_world_office = db.Column(db.String(100), index=True, nullable=True)



class Mezcla(db.Model):
    __tablename__ = 'db_mezcla'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha           = db.Column(db.Date, index=True)
    hora            = db.Column(db.String(20))
    responsable     = db.Column(db.String(150))
    maquina         = db.Column(db.String(80))
    virgen_kg       = db.Column(db.Numeric(10, 2), default=0)
    molido_kg       = db.Column(db.Numeric(10, 2), default=0)
    pigmento_kg     = db.Column(db.Numeric(10, 2), default=0)
    lote_interno    = db.Column(db.String(50), index=True)
    observaciones   = db.Column(db.Text)

class Molido(db.Model):
    __tablename__ = 'db_molido'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha_registro  = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    responsable     = db.Column(db.String(150))
    peso_kg         = db.Column(db.Numeric(10, 2), default=0)
    tipo_material   = db.Column(db.String(50)) # 'Recuperado', 'Contaminado'
    observaciones   = db.Column(db.Text)


class FichaMaestra(db.Model):
    __tablename__ = 'nueva_ficha_maestra'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    producto        = db.Column(db.String(50), index=True) # Codigo Padre
    subproducto     = db.Column(db.String(50), index=True) # Codigo Componente
    cantidad        = db.Column(db.Numeric(18, 2), default=0)


class Molde(db.Model):
    """Modelo para la tabla db_moldes — validación de cavidades max por molde."""
    __tablename__ = 'db_moldes'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nombre          = db.Column(db.String(100), unique=True, nullable=False, index=True)
    cavidades_max   = db.Column(db.Integer, default=1)
    activo          = db.Column(db.Boolean, default=True)
    descripcion     = db.Column(db.String(255), nullable=True)


class RelProductoMolde(db.Model):
    """Modelo para rel_producto_molde — qué molde produce cada referencia Friparts
    y con cuántas cavidades. Un molde puede tener varias filas (combo/moneda
    alternativa); tipo_vinculo distingue CAVIDAD_FIJA de MONEDA_ALTERNATIVA."""
    __tablename__ = 'rel_producto_molde'
    __table_args__ = {'extend_existing': True}

    id                  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    codigo_molde        = db.Column(db.String(50), index=True, nullable=False)
    codigo_referencia   = db.Column(db.String(50), index=True, nullable=False)
    cavidades           = db.Column(db.Integer, default=1)
    tipo_vinculo        = db.Column(db.String(30), default='CAVIDAD_FIJA')
    activo              = db.Column(db.Boolean, default=True)


class Portamolde(db.Model):
    """Modelo para db_portamoldes — catálogo de portamoldes físicos (A-P, Ñ).
    Cada código es una sola pieza física: cantidad_fisica=1 salvo que planta
    confirme copias."""
    __tablename__ = 'db_portamoldes'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    codigo          = db.Column(db.String(10), unique=True, nullable=False, index=True)
    cantidad_fisica = db.Column(db.Integer, default=1)
    activo          = db.Column(db.Boolean, default=True)


class RelMoldePortamolde(db.Model):
    """Modelo para rel_molde_portamoldes — qué portamolde(s) necesita cada
    molde para montarse en una máquina."""
    __tablename__ = 'rel_molde_portamoldes'
    __table_args__ = {'extend_existing': True}

    id                  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    codigo_molde        = db.Column(db.String(50), index=True, nullable=False)
    codigo_portamolde   = db.Column(db.String(10), index=True, nullable=False)


class RelMaquinaPortamolde(db.Model):
    """Modelo para rel_maquina_portamolde — qué portamoldes acepta cada
    máquina según su capacidad (grandes: M,N,Ñ,O,P / chicas: A-M)."""
    __tablename__ = 'rel_maquina_portamolde'
    __table_args__ = {'extend_existing': True}

    id                  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    maquina             = db.Column(db.String(80), index=True, nullable=False)
    codigo_portamolde   = db.Column(db.String(10), index=True, nullable=False)


class Macho(db.Model):
    """Modelo para db_machos — inventario de machos independientes (no tienen
    SKU Friparts propio). diametro_interno_mm se usa con tolerancia +/-1mm
    para calzar contra el diámetro que pide una referencia; cantidad_fisica_disponible
    limita cuántas cavidades de esa medida se pueden montar simultáneamente."""
    __tablename__ = 'db_machos'
    __table_args__ = {'extend_existing': True}

    id                          = db.Column(db.Integer, primary_key=True, autoincrement=True)
    codigo_macho                = db.Column(db.String(50), unique=True, nullable=False, index=True)
    diametro_interno_mm         = db.Column(db.Numeric(10, 2), nullable=True)
    cantidad_fisica_disponible  = db.Column(db.Integer, default=0)
    activo                      = db.Column(db.Boolean, default=True)


class SimuladorAsignacion(db.Model):
    """Modelo para simulador_asignaciones — estado propio y aislado del
    simulador de programación (2026-08-06). NO se relaciona con
    db_programacion/db_inyeccion; es un sandbox separado del MES real.
    codigo_macho es nullable (no todos los moldes usan macho); la
    compatibilidad molde<->macho se calcula por diámetro, no se guarda aquí."""
    __tablename__ = 'simulador_asignaciones'
    __table_args__ = {'extend_existing': True}

    id                  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    maquina             = db.Column(db.String(80), index=True, nullable=False)
    codigo_portamolde   = db.Column(db.String(10), nullable=False)
    codigo_molde        = db.Column(db.String(50), nullable=False)
    codigo_referencia   = db.Column(db.String(50), nullable=False)
    codigo_macho        = db.Column(db.String(50), nullable=True)
    cavidades           = db.Column(db.Integer, default=1)
    origen              = db.Column(db.String(30), nullable=False)
    estado              = db.Column(db.String(20), default='ACTIVA', index=True)
    responsable         = db.Column(db.String(150), nullable=True)
    creado_en           = db.Column(db.DateTime, default=datetime.utcnow)
    liberado_en         = db.Column(db.DateTime, nullable=True)


class OperacionLog(db.Model):
    """Modelo para registro de auditoría de operaciones en el sistema."""
    __tablename__ = 'db_logs'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha           = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    modulo          = db.Column(db.String(50), index=True)
    operario        = db.Column(db.String(150))
    accion          = db.Column(db.String(255))
    detalles        = db.Column(db.Text, nullable=True)


class MetalsProduccion(db.Model):
    __tablename__ = 'metals_produccion'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fecha           = db.Column(db.String(50))
    responsable     = db.Column(db.String(150), index=True)
    departamento    = db.Column(db.String(100))
    proceso         = db.Column(db.String(150))
    maquina         = db.Column(db.String(100))
    id_pedido       = db.Column(db.String(50), index=True)
    codigo          = db.Column(db.String(50), index=True)
    descripcion     = db.Column(db.String(500))
    cantidad_ok     = db.Column(db.Numeric(18, 2), default=0)
    pnc             = db.Column(db.Numeric(18, 2), default=0)
    hora_inicio     = db.Column(db.String(50))
    hora_fin        = db.Column(db.String(50))
    tiempo          = db.Column(db.String(50))
    observaciones   = db.Column(db.Text)
    campos_extra    = db.Column(db.Text)


class MetalsPersonal(db.Model):
    __tablename__ = 'metals_personal'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    responsable     = db.Column(db.String(150), index=True)
    departamento    = db.Column(db.String(100))
    documento       = db.Column(db.String(50))
    activo          = db.Column(db.String(10), default='SI')
    # Alineacion 2026-08-10: la tabla real en Postgres SI tiene esta columna
    # (confirmado via information_schema.columns) pero el modelo no la
    # declaraba, dejandola invisible para el ORM.
    telefono        = db.Column(db.String(100))


class MetalsProducto(db.Model):
    __tablename__ = 'metals_productos'
    __table_args__ = {'extend_existing': True}

    codigo          = db.Column(db.String(50), primary_key=True)
    descripcion     = db.Column(db.String(500))
    precio          = db.Column(db.Integer, default=0)


# VERTICAL RETIRADA (2026-08-20): DbProveedor y OrdenCompra respaldaban el
# modulo Procura (backend/routes/procura_routes.py + frontend/.../procura.js
# + rotacion.js), retirado por completo -- no tenia uso real en planta y
# recibir_ingreso() mutaba stock/ordenes de compra sin autenticacion alguna.
# Los modelos se conservan (no se borran) porque las tablas siguen en
# Postgres con historico real; nada en el codigo activo las referencia hoy.
class DbProveedor(db.Model):
    __tablename__ = 'db_proveedores'
    __table_args__ = {'extend_existing': True}

    proveedores     = db.Column(db.String(200), index=True)
    nit             = db.Column(db.String(50), primary_key=True)
    direccion       = db.Column(db.String(300))
    persona_de_contacto = db.Column(db.String(150))
    telefono        = db.Column(db.String(100))
    correo          = db.Column(db.String(150))
    proceso         = db.Column(db.String(100))
    forma_de_pago   = db.Column(db.String(100))
    ultima_evaluacion = db.Column(db.String(50))


class OrdenCompra(db.Model):
    __tablename__ = 'ordenes_de_compra'
    __table_args__ = {'extend_existing': True}

    # Alineacion 2026-08-10: la tabla real no tenia columna `id` ni PK alguna
    # (confirmado via information_schema) -- el modelo la declaraba fantasma.
    # scratch/fix_ordenes_compra_metals_clientes_pk.py agrego id SERIAL +
    # PRIMARY KEY real antes de este cambio, asi que ahora es honesto.
    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # Los dos db.Column("col_fisica", ...) siguientes preservan el nombre de
    # atributo Python (fecha_solicitud/cantidad_fact) porque
    # backend/routes/procura_routes.py los usa como kwargs al construir
    # OrdenCompra(...) -- solo cambia el nombre de columna FISICA, que en
    # Postgres es 'fecha_de_solicitud'/'cantidad_facturada', no lo que el
    # modelo asumia.
    fecha_solicitud = db.Column('fecha_de_solicitud', db.String(50))
    n_oc            = db.Column(db.String(50), index=True)
    proveedor       = db.Column(db.String(200))
    producto        = db.Column(db.String(50), index=True)
    # NOTA DE TIPO (hallazgo de la auditoria, no pedido explicito): TODAS las
    # columnas de ordenes_de_compra son `text` en Postgres, incluidas estas
    # "numericas" -- WO/la carga original las exporto como texto. Declararlas
    # Numeric hacia que SQLAlchemy fallara al LEER con
    # "InvalidRequestError: Unknown PG numeric type: 25" (25 = OID de text),
    # rompiendo cualquier OrdenCompra.query existente. Mismo patron ya usado
    # en este archivo para DbCostos.precio_de_venta ("Puede contener '$' y
    # puntos"). El codigo que escribe estos campos (procura_routes.py) ya les
    # pasa float() -- eso no cambia con este fix, solo la lectura.
    cantidad        = db.Column(db.String(50))
    fecha_factura   = db.Column(db.String(50))
    n_factura       = db.Column(db.String(80))
    cantidad_fact   = db.Column('cantidad_facturada', db.String(50))
    fecha_llegada   = db.Column(db.String(50))
    cantidad_recibida = db.Column(db.String(50))
    diferencia      = db.Column(db.String(50))
    observaciones   = db.Column(db.Text)
    estado_proceso  = db.Column(db.String(100))
    # Columna real que existia en Postgres pero nunca se habia declarado en
    # el modelo (hallazgo de la auditoria de esquema, no pedido explicito).
    cantidad_total_enviada = db.Column(db.String(50))


class MetalsCliente(db.Model):
    """Modelo sin usos en el codebase (verificado 2026-08-10: ningun
    routes/services referencia MetalsCliente) -- se pudo alinear renombrando
    los atributos Python directamente a los nombres reales de columna, sin
    necesidad de db.Column('nombre_fisico', ...) para preservar compatibilidad."""
    __tablename__ = 'metals_clientes'
    __table_args__ = {'extend_existing': True}

    # Alineacion 2026-08-10: la tabla real no tenia columna `id` ni PK alguna
    # (confirmado via information_schema) -- el modelo la declaraba fantasma.
    # scratch/fix_ordenes_compra_metals_clientes_pk.py agrego id SERIAL +
    # PRIMARY KEY real antes de este cambio, asi que ahora es honesto.
    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nombre          = db.Column(db.String(200), index=True)
    # 'nit' -> 'identificacion': la columna real en Postgres se llama
    # identificacion, no nit.
    identificacion  = db.Column(db.String(50))
    direccion       = db.Column(db.String(300))
    ciudad          = db.Column(db.String(100))
    # 'telefono' -> 'telefonos': la columna real en Postgres es plural.
    telefonos       = db.Column(db.String(100))


class MetalsPedido(db.Model):
    __tablename__ = 'metals_pedidos'
    __table_args__ = {'extend_existing': True}

    id_pedido       = db.Column(db.String(50), primary_key=True)
    fecha           = db.Column(db.String(50))
    hora            = db.Column(db.String(50))
    id_codigo       = db.Column(db.String(50), primary_key=True)
    descripcion     = db.Column(db.Text)
    vendedor        = db.Column(db.String(100))
    cliente         = db.Column(db.String(200))
    nit             = db.Column(db.String(50))
    direccion       = db.Column(db.String(300))
    ciudad          = db.Column(db.String(100))
    cantidad        = db.Column(db.Integer, default=0)
    precio_unitario = db.Column(db.Integer, default=0)
    total           = db.Column(db.Integer, default=0)
    estado          = db.Column(db.String(50), default='PENDIENTE')
    progreso        = db.Column(db.Integer, default=0)
    observaciones   = db.Column(db.Text)


class ProgramacionEnsamble(db.Model):
    __tablename__ = 'db_programacion_ensamble'
    __table_args__ = {'extend_existing': True}

    id_prog            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_codigo          = db.Column(db.String(50), index=True, nullable=True)
    op_numero          = db.Column(db.String(100), nullable=True)
    cantidad_objetivo  = db.Column(db.Integer, nullable=False)
    cantidad_realizada = db.Column(db.Integer, default=0)
    fecha_programada   = db.Column(db.Date, nullable=False)
    estado             = db.Column(db.String(20), default='PENDIENTE') # PENDIENTE, EN_PROCESO, COMPLETADO


class ChecklistEnsamble(db.Model):
    """
    Checklist de procesos por producto programado (1 fila por id_prog).
    Cada columna es independiente de cantidad_realizada/estado de
    ProgramacionEnsamble -- ese eje mide unidades, este mide qué procesos
    de planta se le hicieron al producto (o si no le correspondían).
    Estados válidos por columna: PENDIENTE, HECHO, NO_APLICA.
    """
    __tablename__ = 'db_checklist_ensamble'
    __table_args__ = {'extend_existing': True}

    id_checklist            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # FK lógica (sin constraint formal, mismo patrón que id_prog en Ensamble)
    # hacia programacion_ensamble.id_prog. Única: 1 checklist por meta.
    id_prog                 = db.Column(db.Integer, index=True, unique=True, nullable=False)
    # "Crudo": único proceso que SIEMPRE aplica (productos simples que no
    # llevan nada más). Antes se llamaba solo "ensamble" -- renombrada por
    # migrate_checklist_ensamble_ensamble_curado.py.
    ensamble_crudo_estado   = db.Column(db.String(20), default='PENDIENTE')
    rayada_carcaza_estado   = db.Column(db.String(20), default='PENDIENTE')
    rayada_interno_estado   = db.Column(db.String(20), default='PENDIENTE')
    pintura_estado          = db.Column(db.String(20), default='PENDIENTE')
    horno1_estado           = db.Column(db.String(20), default='PENDIENTE')
    # Segundo armado, con la pieza ya curada (después de Horno 1) -- distinto
    # del crudo de arriba.
    ensamble_estado         = db.Column(db.String(20), default='PENDIENTE')
    cerrada_estado          = db.Column(db.String(20), default='PENDIENTE')
    horno2_estado           = db.Column(db.String(20), default='PENDIENTE')
    actualizado_en          = db.Column(db.DateTime, nullable=True)
    actualizado_por         = db.Column(db.Text, nullable=True)


class DistribucionOpPedidos(db.Model):
    """
    Sistema de cubetas para la Vista Gerencial. 
    Cruza el progreso de todas las etapas productivas basadas en OP y Pedido.
    """
    __tablename__ = 'db_distribucion_op_pedidos'
    __table_args__ = {'extend_existing': True}

    id_distribucion  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    op_world_office  = db.Column(db.String(100), index=True, nullable=True) # Nullable para planificación de la tarde anterior
    id_pedido        = db.Column(db.String(80), index=True, nullable=False)
    codigo_producto  = db.Column(db.String(100), index=True, nullable=False)
    cant_requerida   = db.Column(db.Integer, nullable=False, default=0)
    cant_inyectada   = db.Column(db.Integer, default=0)
    cant_pulida      = db.Column(db.Integer, default=0)
    cant_ensamblada  = db.Column(db.Integer, default=0)
    cant_alistada    = db.Column(db.Integer, default=0)

class DespachoPedido(db.Model):
    """
    Modelo para registrar el historial de envíos/despachos parciales o totales de un pedido.
    """
    __tablename__ = 'db_despachos_pedido'
    __table_args__ = {'extend_existing': True}

    id_despacho      = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_pedido        = db.Column(db.String(80), index=True, nullable=False)
    id_codigo        = db.Column(db.String(100), index=True, nullable=False)
    cantidad_enviada = db.Column(db.Integer, nullable=False, default=0)
    fecha            = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    transportadora   = db.Column(db.String(100), nullable=True)
    guia             = db.Column(db.String(100), nullable=True)
    responsable      = db.Column(db.String(150), nullable=True)

class TrazabilidadLote(db.Model):
    """
    Tabla pivote de estado del lote (Cabecera MES).
    Creada por Inyeccion al iniciar turno. Pulido la consume en Modo Lotes en Vivo.
    Validacion es el unico punto de escritura hacia db_productos (Paso 3).
    Sin FK -- patron SQL-First del proyecto.

    Ciclo de vida de estado_actual:
      ABIERTO_PRODUCCION -> EN_PULIDO -> PENDIENTE_VALIDACION -> APROBADO_CERRADO
    """
    __tablename__ = 'db_trazabilidad_lotes'
    __table_args__ = {'extend_existing': True}

    # PK textual -- formato: YYYYMMDD-Maquina-OP (ej: 20260602-MAQ1-OP12345)
    # Permite lookup natural desde Pulido sin JOIN.
    id_lote            = db.Column(db.String(120), primary_key=True)

    # Datos de la Orden de Produccion
    orden_produccion   = db.Column(db.String(100), index=True, nullable=True)
    id_codigo          = db.Column(db.String(50),  index=True, nullable=False)
    maquina            = db.Column(db.String(80),  nullable=True)

    # Referencia al registro padre en db_inyeccion (sin FK declarado)
    id_inyeccion       = db.Column(db.String(80),  index=True, nullable=True)

    # Estado del ciclo de vida del lote
    estado_actual      = db.Column(db.String(30),  nullable=False, default='ABIERTO_PRODUCCION')

    # Auditoria
    fecha_creacion     = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    responsable        = db.Column(db.String(150), nullable=True)

    # Cantidad total inyectada -- actualizada al cierre del turno por mes_reportar.
    # Validacion la usa para calcular WIP: WIP = cantidad_inyectada - SUM(db_pulido.cantidad_real)
    cantidad_inyectada = db.Column(db.Integer, default=0)
    por_pulir          = db.Column(db.Integer, default=0)


class InventarioWO(db.Model):
    __tablename__ = 'inventario_wo'
    __table_args__ = {'extend_existing': True}

    codigo_producto      = db.Column(db.String(50), primary_key=True)
    descripcion          = db.Column(db.String(500), nullable=True)
    stock_wo             = db.Column(db.Numeric(18, 2), default=0)
    precio_wo            = db.Column(db.Numeric(18, 2), default=0)
    codigo_alterno       = db.Column(db.String(100), nullable=True)
    referencia           = db.Column(db.String(100), nullable=True)
    fecha_sincronizacion = db.Column(db.DateTime, nullable=True)


class OpGenerada(db.Model):
    """
    Numerador de OP: FRITECH asigna el consecutivo (antes lo hacia un humano
    tecleando en World Office). Reunion 2026-08-25 -- corte 31-ago-2026.

    Grano: una fila por documento OP que se va a subir a WO -- una por
    maquina/dia en inyeccion, una por dia en ensamble y en empaque (reserva
    perezosa: nace con el primer reporte del dia, no con la programacion).

    El indice unico parcial uq_op_generada_dia_ambito es lo que hace la
    reserva IDEMPOTENTE: dos llamados para la misma (fecha, ambito, maquina)
    devuelven la MISMA fila en vez de crear una segunda. Eso es necesario
    porque hay dos rutas de programacion de inyeccion escribiendo en la
    misma tabla (guardar_programacion nueva y crear_programacion legacy), y
    porque empaque puede reportar varias veces el mismo dia.

    numero_op se compone como f"{prefijo}-{consecutivo}" -- MISMO formato
    exacto que agente_wo_comercial.py usa al extraer OP reales de WO, o la
    conciliacion (auditoria_service.py) no cruza.

    La serie de consecutivos es GLOBAL entre los tres ambitos (verificado
    contra datos reales: EMP-303425, ENS-303787, OP-303904 conviven en el
    mismo rango sin un solo numero repetido entre prefijos) -- por eso el
    piso se calcula con un solo MAX, no uno por prefijo.
    """
    __tablename__ = 'db_op_generadas'
    __table_args__ = {'extend_existing': True}

    id                = db.Column(db.Integer, primary_key=True, autoincrement=True)
    prefijo           = db.Column(db.String(20),  nullable=False, index=True)  # INY / ENS / EMP
    consecutivo       = db.Column(db.BigInteger,  nullable=False)
    numero_op         = db.Column(db.String(50),  nullable=False, unique=True, index=True)
    ambito            = db.Column(db.String(20),  nullable=False, index=True)  # INYECCION / ENSAMBLE / EMPAQUE
    maquina           = db.Column(db.String(80),  nullable=True)               # NULL salvo INYECCION
    fecha_produccion  = db.Column(db.Date,        nullable=False, index=True)
    estado            = db.Column(db.String(20),  nullable=False, default='RESERVADA')
    # RESERVADA -> LISTA_EXPORTAR -> EXPORTADA -> CONFIRMADA_WO ; lateral: ANULADA, CONFLICTO

    creado_por        = db.Column(db.String(150), nullable=True)
    creado_en         = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)
    exportada_por     = db.Column(db.String(150), nullable=True)
    exportada_en      = db.Column(db.DateTime,    nullable=True)
    confirmada_en     = db.Column(db.DateTime,    nullable=True)
    anulada_motivo    = db.Column(db.Text,        nullable=True)


class ProduccionEmpaque(db.Model):
    """
    Reporte de Empaque (reunion 2026-08-25): registro simple de "arme este
    muneco/kit, esta cantidad". A diferencia de Ensamble, aqui NADIE programa
    -- el trabajo lo dicta el pedido (la operaria ya sabe que hacer viendo
    gestion de pedidos), asi que deliberadamente no hay cantidad_objetivo ni
    estado PENDIENTE/COMPLETADO: no hay meta que cumplir, solo un hecho que
    se registra una vez.

    op_numero se llena con reserva PEREZOSA (ver EmpaqueService.reportar):
    el primer reporte del dia pide la OP de (hoy, EMPAQUE) y la crea; los
    siguientes reportes del mismo dia piden lo mismo y reciben la MISMA OP
    gracias al indice unico parcial de OpGenerada -- asi, al final del dia,
    todos los reportes quedan bajo una sola OP EMP multi-linea.
    """
    __tablename__ = 'db_empaque'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id_empaque      = db.Column(db.String(100), unique=True, index=True, nullable=False)
    fecha           = db.Column(db.Date,        nullable=False, index=True)
    fecha_registro  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)
    id_codigo       = db.Column(db.String(50),  index=True, nullable=False)  # referencia del muñeco/kit
    cantidad        = db.Column(db.Integer,     nullable=False)
    responsable     = db.Column(db.String(150), nullable=True)
    op_numero       = db.Column(db.String(100), index=True, nullable=True)
    observaciones   = db.Column(db.Text,        nullable=True)


class OpWoStaging(db.Model):
    """
    Staging de Ordenes de Produccion extraidas de World Office
    (Tipo_de_Documento='OP' en Vista_Tabla_Encabezados). Fase 2 del plan de
    conciliacion OP: tabla interna, poblada exclusivamente por
    agente_wo_comercial.py via truncate+bulk insert directo (sin pasar por
    ninguna ruta web -- no tiene dato financiero ni de cliente).

    Grano: (numero_op, codigo_producto) -- una OP es una maquina/un dia y
    agrupa varias referencias, nunca una sola.
    """
    __tablename__ = 'db_op_wo_staging'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    numero_op       = db.Column(db.String(50),  index=True, nullable=True)
    codigo_producto = db.Column(db.String(50),  index=True, nullable=True)
    cantidad        = db.Column(db.Numeric(18, 2), default=0)
    fecha           = db.Column(db.DateTime, nullable=True)
    anulado         = db.Column(db.Boolean, default=False)
    verificado      = db.Column(db.Boolean, default=False)
    bodega          = db.Column(db.String(100), nullable=True)

    # --- Señal EPT (Entrada de Producto Terminado) ---
    # En World Office cada OP genera una EPT que ingresa a inventario lo
    # realmente producido; su encabezado la referencia en la nota
    # ("EPT GENERADA POR OP No 304048" -- formato verificado, 100% parseable).
    # Comparar la cantidad de la OP contra la de su EPT es lo que expone el
    # descuadre de inventario que reporta planta: la OP se hace pero la
    # entrada no se genera, o se genera por una cantidad distinta.
    # cantidad_ept queda en NULL cuando la OP no tiene EPT asociada.
    cantidad_ept    = db.Column(db.Numeric(18, 2), nullable=True)
    # Numero de documento de la EPT en si (prefijo+consecutivo de WO), para
    # que planta pueda ubicarla directamente en World Office sin adivinar.
    numero_ept      = db.Column(db.String(50), nullable=True)


class AppConfig(db.Model):
    """
    Config clave/valor de propósito general. Primer uso: reemplaza el flag
    de sincronización comercial (antes en data/sync_comercial_flag.json,
    filesystem efímero en Render -- se perdía en cada redeploy). No pensada
    para datos de negocio, solo banderas/config operativa liviana.
    """
    __tablename__ = 'app_config'
    __table_args__ = {'extend_existing': True}

    clave           = db.Column(db.String(100), primary_key=True)
    valor           = db.Column(db.Text, nullable=True)
    actualizado_en  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SuscripcionesPush(db.Model):
    """
    Entidad plana (SQL-First) para almacenar los endpoints de Web Push de cada dispositivo/usuario.
    """
    __tablename__ = 'db_push_subscriptions'
    __table_args__ = {'extend_existing': True}

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id         = db.Column(db.String(100), db.ForeignKey('db_usuarios.username', ondelete='CASCADE'), index=True, nullable=False)
    endpoint        = db.Column(db.Text, nullable=False, unique=True)
    p256dh          = db.Column(db.Text, nullable=False)
    auth            = db.Column(db.Text, nullable=False)
    fecha_creacion  = db.Column(db.DateTime, default=datetime.utcnow)
