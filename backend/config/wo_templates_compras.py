"""
wo_templates_compras.py
========================
Mapeo del archivo plano de importación de Órdenes de Compra (proveedores
externos) a World Office. Único archivo que hay que tocar si WO cambia el
layout -- mismo criterio que wo_templates.py para Órdenes de Producción,
pero es un archivo SEPARADO a propósito: es un tipo de documento distinto
(compra a un proveedor real, no un movimiento interno de producción) y
ningún valor fijo confirmado para OP aplica aquí sin verificar de nuevo.

Columnas verificadas byte a byte (2026-09-15) contra el archivo real que
el usuario proporcionó: DocumentosComprasEncabezadosMovimientoInventarioWO.xls,
hoja "Encab +Movimi. Inventa". 58 columnas, orden fijo -- el importador de
WO las lee por POSICIÓN además de por nombre, así que ni el orden ni la
cantidad se pueden alterar.

Estructura del documento: igual que OP, varias filas comparten el mismo
encabezado (`Encab: *` repetido idéntico) y cada una aporta una línea de
`Detalle: *`. Así un solo archivo puede agrupar varias Órdenes de Compra.
"""

COLUMNAS_OC = [
    'Encab: Empresa',
    'Encab: Tipo Documento',
    'Encab: Prefijo',
    'Encab: Documento Número',
    'Encab: Fecha',
    'Encab: Tercero Interno',
    'Encab: Tercero Externo',
    'Encab: Pref Dto Ext',
    'Encab: No. Dto Ext',
    'Encab: Nota',
    'Encab: FormaPago',
    'Encab: Verificado',
    'Encab: Anulado',
    'Encab: Fecha Emision',
    *[f'Encab: Personalizado {i}' for i in range(1, 16)],
    'Encab: Importacion',
    'Encab: Sucursal',
    'Encab: Clasificación',
    'Detalle: Producto',
    'Detalle: Bodega',
    'Detalle: UnidadDeMedida',
    'Detalle: Cantidad',
    'Detalle: IVA',
    'Detalle: Valor Unitario',
    'Detalle: Descuento',
    'Detalle: Vencimiento',
    'Detalle: Nota',
    'Detalle: Centro costos',
    *[f'Detalle: Personalizado{i}' for i in range(1, 16)],
    'Detalle : Código Centro Costos',
]

# Valores confirmados con una CARGA DE PRUEBA REAL en el importador de
# World Office (2026-09-15) -- no solo leyendo un documento existente.
# El archivo de ejemplo original traía 'Encab: Tipo Documento'='FC' con
# prefijos FVG/FVD: confirmado que es data de DEMO genérica de World
# Office, no aplica -- el tipo real es 'OC'.
FIJOS_OC = {
    'Encab: Empresa': 'FRIPARTS SAS',
    'Encab: Tipo Documento': 'OC',
    # Confirmado: una OC en WO no lleva prefijo (a diferencia de OP, que
    # usa INY/ENS/EMP) -- el documento real leído trae 'prefijo': None.
    'Encab: Prefijo': '',
    # 'Principal', NO 'Uno' -- corregido tras la carga de prueba real
    # (2026-09-15): la lectura pasiva de un documento existente traía
    # 'Uno' como bodega, pero al subir el archivo de prueba al
    # importador de WO hubo que cambiarlo a 'Principal' para que
    # funcionara. Mismo valor que ya usa OP.
    'Detalle: Bodega': 'Principal',
    'Encab: Verificado': 0,
    'Encab: Anulado': 0,
    # Cédula de Diego (confirmada contra Vista_Tabla_Terceros de WO,
    # IdTercero=3147: Diego Alejandro Isaza Cortés) -- es quien hoy crea
    # las OC en WO, mismo criterio que RESPONSABLE_ENSAMBLE en
    # wo_templates.py. Si en el futuro otro ADMIN también crea OC, esto
    # necesita resolverse por usuario en vez de quedar fijo.
    'Encab: Tercero Interno': '1007781947',
    # 'Credito' -- decisión de negocio confirmada (las OC siempre se
    # manejan a crédito) Y confirmado que el importador de WO acepta
    # literalmente ese string, sin tilde, en la carga de prueba real.
    'Encab: FormaPago': 'Credito',
    # Confirmado en la carga de prueba real: el documento SÍ lleva IVA
    # (0.19 = 19%, la tarifa general en Colombia) -- a diferencia de OP,
    # que siempre va en 0 por ser un movimiento interno sin hecho
    # generador de IVA. Se aplica como default a toda línea; si algún
    # insumo comprado es exento, esto habrá que resolverlo por
    # línea/producto más adelante, no es el caso hoy.
    'Detalle: IVA': 0.19,
}

DELIMITADOR_TXT = '\t'
ENCODING_TXT = 'utf-8-sig'   # BOM: Excel/WO en Windows lo esperan para acentos
FORMATO_DEFECTO = 'xlsx'


def nota_oc(numero_oc, fecha):
    """Texto de 'Encab: Nota' y 'Detalle: Nota' -- mismo formato que ya usa
    FriParts para OP (nota_op en wo_templates.py)."""
    return f"Orden de compra {numero_oc} día {fecha.strftime('%d-%m-%Y')}"
