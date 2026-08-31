"""
wo_templates.py
================
Mapeo del archivo plano de importación de Órdenes de Producción a World
Office. Único archivo que hay que tocar si WO cambia el layout.

Derivado del archivo real que exporta WO
(`OrdenProduccionEncabezadosMovimientoInventarioWO.xls`) más el ejemplo
diligenciado por FriParts, ambos verificados 2026-08-25. Son 40 columnas en
orden fijo: el importador de WO las lee por POSICIÓN además de por nombre,
así que ni el orden ni la cantidad se pueden alterar.

Estructura del documento: varias filas comparten el mismo encabezado
(`Encab: *` repetido idéntico) y cada una aporta una línea de `Detalle: *`.
Así es como una OP agrupa varias referencias.
"""

# Orden EXACTO del archivo de WO. No reordenar ni agregar/quitar columnas.
# 'Encab: Tipo Documento ' lleva un espacio al final -- verificado byte a
# byte contra el archivo real exportado por WO. No es un error de tipeo:
# quitarlo es lo que rompería la coincidencia con el nombre que WO exporta.
COLUMNAS_OP = [
    'Encab: Empresa',
    'Encab: Tipo Documento ',
    'Encab: Prefijo',
    'Encab: Documento Número',
    'Encab: Fecha',
    'Encab: Tercero Interno',
    'Encab: Tercero Externo',
    'Encab: Fecha Inicial',
    'Encab: Fecha Final',
    'Encab: Nota',
    'Encab: Abierta/Cerrada',
    'Encab: Modo Distribución',
    'Encab:Lista Precios Modo Distribución',
    *[f'Encab:Personalizado {i}' for i in range(1, 16)],
    'Detalle:Producto',
    'Detalle:Bodega',
    'Detalle:Unidad Medida',
    'Detalle:Cantidad',
    'Detalle:Cantidad Recibida',
    'Detalle:Nota',
    'Detalle:Porcentaje de Distribución',
    'Detalle:Talla',
    'Detalle:Color',
    'Detalle:Valor Unitario',
    'Detalle:Iva',
    'Detalle:Vencimiento',
]

# Valores constantes confirmados contra el archivo real de FriParts y contra
# el migrador real de WO (carga de prueba 2026-08-26).
# 'Encab: Tipo Documento' es SIEMPRE 'OP': el área (INY/ENS/EMP) va en
# 'Encab: Prefijo', que es un campo aparte -- así funciona WO (un tipo de
# documento con varios prefijos configurados).
FIJOS_OP = {
    # SIN puntos: 'FRIPARTS S.A.S' fue rechazado por el migrador real de WO
    # ("El documento pasa FRIPARTS SAS y FRIPARTS S.A.S no pasa" -- prueba
    # 2026-08-26). El nombre exacto registrado en WO es 'FRIPARTS SAS'.
    'Encab: Empresa': 'FRIPARTS SAS',
    'Encab: Tipo Documento ': 'OP',
    # Encab: Tercero Interno NO va aquí -- varía por máquina/ámbito (ver
    # RESPONSABLE_POR_MAQUINA / RESPONSABLE_ENSAMBLE / RESPONSABLE_EMPAQUE
    # más abajo), se calcula por línea en wo_export_service.
    # -1 = Abierta (no 0, no 1). La plantilla documentaba "0 = Abierta /
    # -1 = Cerrada" pero está invertida frente a la realidad -- probado
    # directo en WO 2026-08-26: 0 -> Cerrada, 1 -> seguía sin quedar
    # Abierta, -1 -> Abierta (confirmado editando el campo en la orden ya
    # creada y viendo el resultado). Con evidencia real, no con el manual.
    'Encab: Abierta/Cerrada': -1,
    'Encab: Modo Distribución': 'Cantidad a Producir',
    'Encab:Lista Precios Modo Distribución': '',
    'Detalle:Bodega': 'Principal',
    'Detalle:Unidad Medida': 'Und.',
    # Detalle:Valor Unitario y Detalle:Iva: una OP es un movimiento interno
    # de producción, no una venta -- no hay precio de venta ni hecho
    # generador de IVA que registrar aquí. 0, no '': el migrador real de WO
    # rechazó el campo vacío ("Este valor es requerido"), así que tiene que
    # llevar un número -- confirmado con costos/contabilidad (2026-08-26)
    # que el valor correcto para ambos es 0, no el precio de venta del
    # catálogo. Talla/Color son campos de confección que no aplican al
    # negocio de piezas. Vacíos en el ejemplo real de FriParts.
    'Detalle:Valor Unitario': 0,
    'Detalle:Iva': 0,
    'Detalle:Talla': '',
    'Detalle:Color': '',
}

# 'Encab: Tercero Interno' = "Responsable Producción" en la pantalla de WO
# (confirmado con prueba real 2026-08-26: con el NIT fijo de FriParts el
# campo queda en blanco -- WO espera la cédula de una PERSONA, no de la
# empresa). Decisión del usuario 2026-08-27: fijo por máquina/ámbito, no
# resuelto por nombre -- ninguno de los tres operarios tiene cédula en
# db_usuarios ni en ninguna otra tabla de FRITECH, así que no hay de dónde
# traerla en automático.
#
# Cédulas confirmadas contra Vista_Tabla_Terceros de WO (2026-08-27) --
# búsqueda directa en la base real de WO, con autorización explícita del
# usuario para lectura, y confirmadas una a una por el usuario.
RESPONSABLE_POR_MAQUINA = {
    'MAQUINA No. 1': '4153340',    # Richard Miguel Lobo Moreno (dos IDs en WO, el usuario confirmó que sirve este)
    'MAQUINA No. 2': '4153340',    # Richard Miguel Lobo Moreno
    'MAQUINA No. 3': '1003579456', # Oscar Camilo Prieto Novoa -- confirmado
    'MAQUINA No. 4': '1003579456', # Oscar Camilo Prieto Novoa -- confirmado
}
RESPONSABLE_ENSAMBLE = '1071632236'  # Albeiro Antonio Gutiérrez Rodríguez -- confirmado
RESPONSABLE_EMPAQUE = '1019603152'  # Juan Sebastián Novoa Cepeda (jefa/admin) -- confirmado

# Respaldo cuando RESPONSABLE_POR_MAQUINA/ENSAMBLE no tiene la cédula real
# todavía (ver arriba): NIT de FriParts, igual que se venía usando antes de
# este cambio. No bloquea la carga -- el campo "Responsable Producción"
# queda en blanco en WO, como ya se vio en la prueba real, hasta que se
# cargue la cédula de la persona correspondiente.
TERCERO_INTERNO_DEFECTO = 900315300

# 'Encab: Tercero Externo' = SIEMPRE el NIT de FriParts (decisión del
# usuario 2026-08-28). Ya no se resuelve por la cédula de quien registró:
# una OP es un documento interno de la empresa consigo misma, así que el
# tercero externo es la empresa, no la persona. La persona va en
# 'Encab: Tercero Interno' (= "Responsable Producción" en WO, ver arriba).
# Verificado contra Vista_Tabla_Terceros de WO: 900315300 = FRIPARTS SAS.
#
# STR, no int: en el archivo real de WO esta columna es numérica, pero se
# mantiene como texto para que toda la columna tenga un solo tipo y se
# castea a entero recién al escribir el archivo (ver
# WoExportService._normalizar_tipos). Sobreescribible en
# AppConfig['wo_export.tercero_externo_defecto'].
TERCERO_EXTERNO_DEFECTO = '900315300'

# Prefijo de WO por ámbito. El área NO va en 'Tipo Documento' (siempre 'OP').
PREFIJO_POR_AMBITO = {
    'INYECCION': 'INY',
    'ENSAMBLE': 'ENS',
    'EMPAQUE': 'EMP',
}

NOMBRE_AREA = {
    'INYECCION': 'inyección',
    'ENSAMBLE': 'ensamble',
    'EMPAQUE': 'empaque',
}

# Formato de salida. El usuario pidió "texto plano"; el importador que ya usa
# la empresa consume .xlsx (el propio archivo de ejemplo lo es). Como el
# DataFrame es el mismo, el formato se elige por parámetro sin duplicar nada.
DELIMITADOR_TXT = '\t'
ENCODING_TXT = 'utf-8-sig'   # BOM: Excel/WO en Windows lo esperan para acentos
FORMATO_DEFECTO = 'xlsx'


def nota_op(ambito, fecha):
    """Texto de 'Encab: Nota' y 'Detalle:Nota', con el mismo formato que usó
    FriParts en el archivo real: 'Orden de producción inyección día 24-08-2026'."""
    area = NOMBRE_AREA.get(ambito, str(ambito).lower())
    return f"Orden de producción {area} día {fecha.strftime('%d-%m-%Y')}"
