"""
Servicio de Explosión de Materiales (BOM – Bill of Materials) SQL-Native.

Calcula los descuentos de inventario para un ensamble/kit a partir
de la ficha técnica definida en la tabla nueva_ficha_maestra de PostgreSQL.
"""
import re
import logging
from typing import List, Dict, Optional
from backend.models.sql_models import FichaMaestra
from backend.core.sql_database import db

logger = logging.getLogger(__name__)

from backend.utils.formatters import normalizar_codigo

# ──────────────────────────────────────────────
#  Traducción de código de componente
# ──────────────────────────────────────────────
def traducir_codigo_componente(codigo_raw: str) -> str:
    """
    Aplica la regla de traducción de códigos de la ficha técnica
    al código real de inventario (SQL-Native).

    BUG CORREGIDO 2026-08-25 (detectado al probar la ficha real de FR-9408):
    la versión anterior truncaba al texto ANTES del primer guion salvo que el
    prefijo estuviera en una lista blanca de materias primas
    ('MP','CH','BP','TR','PL','WASA','ARC','BSL','BL','LM','AL'). Como 'FR'
    no estaba en esa lista, un componente de ficha 'FR-9302' se traducía al
    código de inventario 'FR' -- que no existe en db_productos, así que el
    componente se descartaba silenciosamente y NUNCA se le descontaba stock.
    Afectaba a 66 componentes distintos de nueva_ficha_maestra, entre ellos
    los más usados (FR-9632 aparece en 20 fichas, FR-9301 en 12). Como
    EnsambleService ya usa esta función en producción, esos descuentos
    llevaban tiempo perdiéndose sin generar ningún error visible.

    La lista blanca era el enfoque equivocado: cualquier prefijo nuevo que
    alguien agregara a una ficha quedaba roto por defecto. La regla correcta
    es estructural -- truncar SOLO cuando el patrón es genuinamente un RANGO
    de referencias ('CAR9722-9723': izquierda con letras+números, derecha
    solo números), que era el caso que el truncado quería resolver. Un
    prefijo de división ('FR-9302') o un código con guion interno
    ('003-TUERCA', 'CH-PEGANTE-213') se conservan completos.

    Devuelve el código COMPLETO, con su prefijo intacto ('FR-9302', 'SP-15').
    NO le aplica normalizar_codigo(), que pelaría el prefijo: verificado
    contra datos reales que db_productos guarda los separadores/tornillos
    con el prefijo pegado ('SP-15', 'TR-3/8*10', 'BSL-026') en AMBAS columnas,
    así que pelarlos rompe el cruce (243 componentes cruzan pelando vs 343
    conservando). Quien resuelve la ambigüedad 'FR-9302' vs '9302' es
    _resolver_codigo_inventario(), preguntándole al catálogo en vez de
    adivinar -- misma filosofía que el resto del proyecto: la división es un
    dato del negocio, no algo deducible de la forma de la cadena.
    """
    if not codigo_raw: return ""

    # Caso especial legacy: 'C-123' es notación vieja de carrocería. Va
    # primero porque su salida ('CAR123') no pasa por las reglas de abajo.
    if str(codigo_raw).upper().startswith("C-"):
        numero = str(codigo_raw)[2:].strip()
        return f"CAR{numero}"

    # 1. Quitar espacios, mayúsculas y guiones colgantes ('ARC002-' -> 'ARC002',
    #    dato sucio real presente en la ficha).
    cod = str(codigo_raw).strip().upper().strip('-')

    # 2. Truncar SOLO rangos reales de referencias (ej. CAR9722-9723 -> CAR9722):
    #    izquierda = letras seguidas de números, derecha = solo números.
    #    Todo lo demás conserva el código completo -- ver docstring.
    if '-' in cod:
        izq, der = cod.split('-', 1)
        if re.match(r'^[A-Z]+[0-9]+$', izq) and re.match(r'^[0-9]+$', der):
            cod = izq

    return cod


def _resolver_codigo_inventario(codigo: str) -> str:
    """
    Traduce el código de la ficha al código con el que ese producto existe
    REALMENTE en db_productos, consultando el catálogo en vez de asumir un
    formato.

    Necesario porque el catálogo no es uniforme -- verificado 2026-08-25:
      - los bujes viven como codigo_sistema='FR-9302' / id_codigo='9302',
        así que ambas formas cruzan;
      - los separadores/tornillos/barras viven como 'SP-15' / 'TR-3/8*10' /
        'BSL-026' en AMBAS columnas: solo cruza la forma CON prefijo;
      - algún caso suelto ('PL-6000') solo existe pelado ('6000').
    Una regla fija de "siempre pelar" o "nunca pelar" falla en ~100 de los
    357 componentes; preguntarle al catálogo acierta en ambos sentidos.

    Devuelve el código completo si ninguna forma existe, para que el fallo
    quede registrado con el código real de la ficha y no con una versión
    mutilada que no le dice nada a quien lea el log.
    """
    from backend.models.sql_models import Producto

    if not codigo:
        return ""

    candidatos = [codigo]
    pelado = normalizar_codigo(codigo)
    if pelado and pelado != codigo:
        candidatos.append(pelado)

    for cand in candidatos:
        if Producto.query.filter(
            (Producto.codigo_sistema == cand) | (Producto.id_codigo == cand)
        ).first():
            return cand

    return codigo

# ──────────────────────────────────────────────
#  Función principal: calcular_descuentos_ensamble (SQL)
# ──────────────────────────────────────────────
def calcular_descuentos_ensamble(
    codigo_kit: str,
    cantidad_armada: int
) -> Dict:
    """
    Explota la BOM de un ensamble (kit) usando PostgreSQL.
    Cruce inteligente via normalizar_codigo().
    """
    resultado = {
        "success": False,
        "kit": None,
        "cantidad_armada": cantidad_armada,
        "componentes": [],
        "error": None,
    }

    if not codigo_kit:
        resultado["error"] = "Código de kit no proporcionado"
        return resultado

    # 1. Normalizar código del kit
    codigo_norm = normalizar_codigo(codigo_kit)
    resultado["kit"] = codigo_norm
    
    try:
        # 2. Consultar Ficha Maestra en SQL
        codigo_limpio = re.sub(r'^FR-?', '', str(codigo_norm), flags=re.IGNORECASE).strip()
        
        # Intento 1: Exacto con prefijo FR- (ej. "FR-9380", "FR-9380 ")
        query = FichaMaestra.query.filter(
            FichaMaestra.producto.ilike(f"FR-{codigo_limpio}%")
        ).all()
        
        # Intento 2: Exacto sin prefijo (ej. "9380")
        if not query:
            query = FichaMaestra.query.filter(
                FichaMaestra.producto == codigo_limpio
            ).all()
        
        # Intento 3: Coincidencia exacta con código normalizado original
        if not query:
            query = FichaMaestra.query.filter(
                (FichaMaestra.producto == codigo_kit) |
                (FichaMaestra.producto == codigo_norm)
            ).all()
            
        # Intento 4: Empieza con el código normalizado (ej: CAR9609%, INT9722%)
        if not query:
            query = FichaMaestra.query.filter(
                FichaMaestra.producto.ilike(f"{codigo_norm}%")
            ).all()

        # Intento 5: Empieza con el código limpio (ej: 9609%, 9722%)
        if not query:
            query = FichaMaestra.query.filter(
                FichaMaestra.producto.ilike(f"{codigo_limpio}%")
            ).all()

        # Intento 6: Búsqueda inteligente por sub-partes de códigos compuestos (ej: CAR9723 -> CAR9722-9723)
        if not query:
            potential_query = FichaMaestra.query.filter(
                FichaMaestra.producto.ilike(f"%{codigo_limpio}%")
            ).all()
            
            query = []
            for row in potential_query:
                prod_name = str(row.producto).strip()
                first_word = prod_name.split(' ')[0]
                if codigo_limpio in first_word or codigo_norm in first_word:
                    query.append(row)
        
        if not query:
            logger.warning(f" [BOM SQL] No se encontró ficha estricta para {codigo_kit}")
            resultado["error"] = f"Ficha técnica no encontrada para {codigo_kit}"
            return resultado
        
        # Filtrar sub-recetas: excluir filas donde el PRODUCTO empiece con CB
        query = [row for row in query if not str(row.producto).strip().upper().startswith('CB')]
        if not query:
            resultado["error"] = f"Solo se encontraron sub-recetas (CB) para {codigo_kit}, no un ensamble"
            return resultado

        componentes_ficha = []
        for row in query:
            subpro_raw = str(row.subproducto).strip()
            
            # Tarea 1: Normalización de Códigos para el Cruce (Extraer primera palabra)
            codigo_limpio_receta = subpro_raw.split(' ')[0]
            subpro_cod = traducir_codigo_componente(codigo_limpio_receta)

            # Evitar auto-referencia. Se compara PELADO en ambos lados:
            # subpro_cod ahora conserva el prefijo ('FR-9408') mientras que
            # codigo_norm ya viene pelado ('9408'), así que compararlos crudos
            # dejaría pasar la auto-referencia sin detectarla.
            if normalizar_codigo(subpro_cod) == normalizar_codigo(codigo_norm):
                continue

            qty_por_kit = float(row.cantidad or 0)
            if qty_por_kit <= 0: continue

            # Resolver contra el catálogo real: 'FR-9302' puede vivir como
            # 'FR-9302' o como '9302' según el producto -- ver docstring de
            # _resolver_codigo_inventario.
            subpro_norm = _resolver_codigo_inventario(subpro_cod)

            componentes_ficha.append({
                "codigo_ficha": subpro_raw,
                "codigo_inventario": subpro_norm, # Este debe cruzar con Producto.codigo_sistema
                "cantidad_por_kit": qty_por_kit,
                "cantidad_total_descontar": qty_por_kit * cantidad_armada
            })

        if not componentes_ficha:
            resultado["error"] = f"La ficha de {codigo_kit} no tiene componentes válidos"
            return resultado

        resultado["success"] = True
        resultado["componentes"] = componentes_ficha
        logger.info(f" [BOM SQL] Explosión exitosa para {codigo_kit}: {len(componentes_ficha)} items")
        return resultado

    except Exception as e:
        logger.error(f" [BOM SQL] Error crítico: {e}")
        resultado["error"] = str(e)
        return resultado
