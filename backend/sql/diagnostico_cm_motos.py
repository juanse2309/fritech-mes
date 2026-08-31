"""
Diagnóstico READ-ONLY: alcance real de los códigos 'CM-' antes de migrarlos a 'MT-'.

Contexto (reunión 2026-08-25): se decidió que CM desaparece y todo queda MT.
La limpieza del catálogo se hace en World Office; este script mide, ANTES de
tocar nada, qué tan grande es el problema del lado de FRITECH.

Por qué un diagnóstico y no un UPDATE directo: un
`REPLACE(codigo, 'CM-', 'MT-')` a ciegas es destructivo cuando el gemelo
'MT-<núcleo>' YA EXISTE en db_productos. Ahí el rename no es un rename sino
un MERGE: hay dos filas de inventario (p_terminado, por_pulir, stock_bodega)
que deben SUMARSE. Renombrar sin sumar hace que una de las dos filas quede
huérfana o que el UPDATE falle por la constraint única de codigo_sistema --
en el mejor caso revienta, en el peor desaparece stock sin que nadie lo note.

Este script NO escribe absolutamente nada. Solo SELECT + print.

Salida:
  1. Conteo de filas con 'CM' por tabla/columna (con guion, sin guion, minúsculas).
  2. Clasificación de cada código CM- distinto en tres buckets:
       BUCKET 1 (MERGE)    -> ya existe el gemelo MT-. Requiere sumar stocks a mano.
       BUCKET 2 (RENAME)   -> no existe gemelo. Rename directo y seguro.
       BUCKET 3 (REVISAR)  -> existe gemelo pero con descripción distinta.
  3. Conteo de referencias "peladas" (núcleo sin prefijo) como señal de riesgo
     aparte: validar_lote busca Producto.codigo_sistema == normalizar_codigo_sin_prefijo(...)
     que PRESERVA 'MT-'. Si el catálogo queda en 'MT-7016' pero planta reportó
     '7016', el por_pulir no se acredita y solo queda un logger.warning.

Uso:
    python -m backend.sql.diagnostico_cm_motos
"""
import re
from collections import defaultdict

from backend.app import app
from backend.core.sql_database import db
from sqlalchemy import text


# (tabla, columna) a auditar. Se verifica la existencia de cada una antes de
# consultarla: el esquema varía entre entornos y una tabla ausente no debe
# tumbar el diagnóstico completo.
OBJETIVOS = [
    ('db_productos',                'codigo_sistema'),
    ('db_productos',                'id_codigo'),
    ('db_op_wo_staging',            'codigo_producto'),
    ('db_inyeccion',                'id_codigo'),
    ('db_inyeccion',                'codigo_ensamble'),
    ('db_programacion',             'codigo_sistema'),
    ('db_pulido',                   'codigo'),
    ('db_distribucion_op_pedidos',  'codigo_producto'),
    ('db_ensambles',                'id_codigo'),
    ('db_trazabilidad_lotes',       'id_codigo'),
    ('db_pnc_inyeccion',            'id_codigo'),
    ('db_pnc_pulido',               'codigo'),
    ('db_pedidos',                  'id_codigo'),
    ('db_ventas',                   'id_codigo'),
    ('nueva_ficha_maestra',         'producto'),
    ('nueva_ficha_maestra',         'subproducto'),
]


def _columna_existe(tabla, columna):
    """True si tabla.columna existe en el esquema actual."""
    fila = db.session.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
        LIMIT 1
    """), {'t': tabla, 'c': columna}).fetchone()
    return fila is not None


def _nucleo(codigo):
    """Quita el prefijo de división y devuelve el núcleo. 'CM-7016' -> '7016'."""
    return re.sub(r'^[A-Za-z]+-?', '', str(codigo or '').strip().upper()).strip()


def contar_por_tabla():
    """Sección 1: cuántas filas con CM hay en cada tabla/columna."""
    print("=" * 78)
    print("1. CONTEO DE CÓDIGOS 'CM' POR TABLA/COLUMNA")
    print("=" * 78)

    total_general = 0
    omitidas = []

    for tabla, columna in OBJETIVOS:
        if not _columna_existe(tabla, columna):
            omitidas.append(f"{tabla}.{columna}")
            continue

        # 'CM-%' es el caso canónico; 'CM%' sin guion detecta variantes sucias
        # ('CM7016'). ILIKE ya cubre minúsculas, pero se separan los conteos
        # para saber si hay basura de digitación además del caso normal.
        fila = db.session.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE "{columna}" ILIKE 'CM-%')                       AS con_guion,
                COUNT(*) FILTER (WHERE "{columna}" ILIKE 'CM%' AND "{columna}" NOT ILIKE 'CM-%') AS sin_guion,
                COUNT(DISTINCT UPPER(TRIM("{columna}"))) FILTER (WHERE "{columna}" ILIKE 'CM%')  AS distintos
            FROM {tabla}
        """)).mappings().first()

        con_guion = fila['con_guion'] or 0
        sin_guion = fila['sin_guion'] or 0
        distintos = fila['distintos'] or 0
        subtotal = con_guion + sin_guion
        total_general += subtotal

        if subtotal:
            print(f"  {tabla}.{columna:<18} CM-xxxx: {con_guion:>6}   CMxxxx: {sin_guion:>5}   "
                  f"códigos distintos: {distintos:>4}")
        else:
            print(f"  {tabla}.{columna:<18} (limpio)")

    if omitidas:
        print(f"\n  [omitidas por no existir en este esquema] {', '.join(omitidas)}")

    print(f"\n  TOTAL de filas con 'CM' en todo el sistema: {total_general}")
    return total_general


def clasificar_codigos():
    """Sección 2: los tres buckets. Es la parte que decide la estrategia."""
    print()
    print("=" * 78)
    print("2. CLASIFICACIÓN DE CADA CÓDIGO 'CM-' (buckets de migración)")
    print("=" * 78)

    if not _columna_existe('db_productos', 'codigo_sistema'):
        print("  db_productos.codigo_sistema no existe -- no se puede clasificar.")
        return

    cm_rows = db.session.execute(text("""
        SELECT codigo_sistema, descripcion,
               COALESCE(p_terminado, 0)  AS p_terminado,
               COALESCE(por_pulir, 0)    AS por_pulir,
               COALESCE(stock_bodega, 0) AS stock_bodega
        FROM db_productos
        WHERE codigo_sistema ILIKE 'CM%'
        ORDER BY codigo_sistema
    """)).mappings().all()

    if not cm_rows:
        print("  No hay códigos 'CM' en db_productos. Nada que migrar en el catálogo.")
        return

    # Índice de los MT- existentes por núcleo, para detectar gemelos en memoria
    # (una sola query en vez de N).
    mt_rows = db.session.execute(text("""
        SELECT codigo_sistema, descripcion,
               COALESCE(p_terminado, 0)  AS p_terminado,
               COALESCE(por_pulir, 0)    AS por_pulir,
               COALESCE(stock_bodega, 0) AS stock_bodega
        FROM db_productos
        WHERE codigo_sistema ILIKE 'MT%'
    """)).mappings().all()

    mt_por_nucleo = {}
    for r in mt_rows:
        mt_por_nucleo[_nucleo(r['codigo_sistema'])] = r

    buckets = defaultdict(list)

    for cm in cm_rows:
        nucleo = _nucleo(cm['codigo_sistema'])
        gemelo = mt_por_nucleo.get(nucleo)

        if gemelo is None:
            buckets['RENAME'].append((cm, None))
            continue

        desc_cm = (cm['descripcion'] or '').strip().upper()
        desc_mt = (gemelo['descripcion'] or '').strip().upper()

        if desc_cm and desc_mt and desc_cm != desc_mt:
            buckets['REVISAR'].append((cm, gemelo))
        else:
            buckets['MERGE'].append((cm, gemelo))

    # --- BUCKET 1: MERGE (el peligroso) ---
    print(f"\n  [BUCKET 1 - MERGE] {len(buckets['MERGE'])} códigos con gemelo MT- ya existente")
    print("  *** NO renombrar: hay que SUMAR los stocks de las dos filas. ***")
    if buckets['MERGE']:
        print(f"  {'CM':<14} {'-> MT':<14} {'stock CM (term/pulir/bod)':<28} {'stock MT (term/pulir/bod)'}")
        for cm, mt in buckets['MERGE']:
            s_cm = f"{cm['p_terminado']}/{cm['por_pulir']}/{cm['stock_bodega']}"
            s_mt = f"{mt['p_terminado']}/{mt['por_pulir']}/{mt['stock_bodega']}"
            print(f"  {cm['codigo_sistema']:<14} {mt['codigo_sistema']:<14} {s_cm:<28} {s_mt}")

    # --- BUCKET 2: RENAME (el seguro) ---
    print(f"\n  [BUCKET 2 - RENAME] {len(buckets['RENAME'])} códigos SIN gemelo -- rename directo y seguro")
    for cm, _ in buckets['RENAME']:
        sugerido = f"MT-{_nucleo(cm['codigo_sistema'])}"
        print(f"  {cm['codigo_sistema']:<14} -> {sugerido:<14} {(cm['descripcion'] or '')[:44]}")

    # --- BUCKET 3: REVISAR (decisión humana) ---
    print(f"\n  [BUCKET 3 - REVISAR] {len(buckets['REVISAR'])} códigos con gemelo de DESCRIPCIÓN DISTINTA")
    print("  *** Revisión manual: puede que no sean la misma pieza. ***")
    for cm, mt in buckets['REVISAR']:
        print(f"  {cm['codigo_sistema']:<14} '{(cm['descripcion'] or '')[:34]}'")
        print(f"  {'':14} vs {mt['codigo_sistema']:<12} '{(mt['descripcion'] or '')[:34]}'")

    print(f"\n  RESUMEN: {len(buckets['MERGE'])} merge / {len(buckets['RENAME'])} rename / "
          f"{len(buckets['REVISAR'])} revisar")


def contar_pelados():
    """Sección 3: referencias sin prefijo, riesgo aparte de acreditación de inventario."""
    print()
    print("=" * 78)
    print("3. REFERENCIAS 'PELADAS' (núcleo sin prefijo) -- riesgo de inventario")
    print("=" * 78)
    print("  Si el catálogo queda en 'MT-7016' pero planta reportó '7016',")
    print("  validar_lote no encuentra el producto y el por_pulir NO se acredita.")
    print()

    for tabla, columna in [('db_inyeccion', 'id_codigo'),
                           ('db_programacion', 'codigo_sistema'),
                           ('db_pulido', 'codigo')]:
        if not _columna_existe(tabla, columna):
            continue

        fila = db.session.execute(text(f"""
            SELECT COUNT(*) AS n,
                   COUNT(DISTINCT TRIM("{columna}")) AS distintos
            FROM {tabla}
            WHERE TRIM("{columna}") ~ '^[0-9]+$'
        """)).mappings().first()

        print(f"  {tabla}.{columna:<18} filas peladas: {fila['n']:>7}   "
              f"códigos distintos: {fila['distintos']:>5}")


def main():
    with app.app_context():
        try:
            print()
            print("#" * 78)
            print("#  DIAGNÓSTICO CM -> MT  (READ-ONLY -- este script no escribe nada)")
            print("#" * 78)

            total = contar_por_tabla()
            if total:
                clasificar_codigos()
            contar_pelados()

            print()
            print("=" * 78)
            print("FIN. No se modificó ningún dato.")
            print("Siguiente paso: limpiar el catálogo en World Office, resincronizar")
            print("db_productos, y recién ahí construir migrate_cm_a_mt.py con el")
            print("mapeo literal derivado de los buckets de arriba.")
            print("=" * 78)

        except Exception as e:
            # rollback por si alguna query dejó la sesión en estado abortado;
            # no hay nada que deshacer porque no se escribió nada.
            db.session.rollback()
            print(f"\n[ERROR] El diagnóstico falló: {e}")
            raise


if __name__ == '__main__':
    main()
