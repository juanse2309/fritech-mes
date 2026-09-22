"""
Auditoría de la columna `comprometido` en db_productos (2026-09-22): compara
el valor persistido contra el que arroja PedidosService.obtener_desglose_comprometido
-- la misma función que ya es la única fuente de verdad para esta regla
(ver su docstring y el hallazgo del backfill 2026-09-08 en pedidos_service.py).

No inventa un cálculo nuevo: si hoy hay códigos donde COMPROMETIDO no coincide
con "lo que hay realmente pendiente en db_pedidos", es porque algún camino de
escritura (edición manual, script puntual, import, o un estado que cambió sin
pasar por PedidosService.recalcular_comprometido) dejó ese código desincronizado.
Este script solo lo hace visible.

Modo por defecto: SOLO LECTURA. Imprime cada código donde el valor persistido
difiere del recalculado, sin tocar la base de datos.

Con --fix: para los códigos que sí divergen y que tienen fila en db_productos,
llama a PedidosService.recalcular_comprometido (mismo camino que usan las rutas
de pedidos) y hace commit. No usa UPDATE masivo ni SQL propio -- reutiliza la
función ya validada para no introducir una segunda implementación de la regla.

Se corre desde la terminal del contenedor en Coolify, una instancia a la vez
(cada cliente tiene su propia base de datos):
    python -m backend.sql.auditar_comprometido_2026_09_22           # solo auditoría
    python -m backend.sql.auditar_comprometido_2026_09_22 --fix     # aplica la corrección
"""
import sys

from backend.core.sql_database import db
from backend.app import app
from backend.models.sql_models import Producto, Pedido
from backend.services.pedidos_service import PedidosService
from backend.utils.formatters import normalizar_codigo_sin_prefijo, sql_expr_codigo_sin_prefijo_fr

TOLERANCIA = 0.01


def _codigos_a_auditar(session):
    codigos = set()

    for (codigo_sistema,) in session.query(Producto.codigo_sistema).filter(
        Producto.comprometido.isnot(None), Producto.comprometido != 0
    ).all():
        norm = normalizar_codigo_sin_prefijo(codigo_sistema)
        if norm:
            codigos.add(norm)

    for (id_codigo,) in session.query(Pedido.id_codigo).distinct().all():
        norm = normalizar_codigo_sin_prefijo(id_codigo)
        if norm:
            codigos.add(norm)

    return sorted(codigos)


def auditar(aplicar_fix=False):
    with app.app_context():
        session = db.session
        codigos = _codigos_a_auditar(session)
        print(f"Auditando {len(codigos)} códigos distintos (db_productos.comprometido != 0 + codigos en db_pedidos)...\n")

        divergentes = []
        for codigo_norm in codigos:
            try:
                _, real = PedidosService.obtener_desglose_comprometido(codigo_norm, session)
            except Exception as e:
                print(f"ERROR calculando '{codigo_norm}': {e}")
                continue

            producto = session.query(Producto).filter(
                sql_expr_codigo_sin_prefijo_fr(Producto.codigo_sistema) == codigo_norm
            ).first()

            persistido = float(producto.comprometido or 0) if producto else None
            comparar_contra = persistido if persistido is not None else 0.0

            if abs(comparar_contra - real) > TOLERANCIA:
                divergentes.append((codigo_norm, producto, persistido, real))

        if not divergentes:
            print("Sin divergencias: COMPROMETIDO en db_productos ya coincide con lo pendiente real en db_pedidos.")
            return

        divergentes.sort(key=lambda t: abs((t[2] or 0) - t[3]), reverse=True)

        print(f"{'CODIGO':<20}{'PERSISTIDO':>15}{'REAL (pedidos)':>18}{'DIFERENCIA':>15}   NOTA")
        for codigo_norm, producto, persistido, real in divergentes:
            nota = "" if producto else "NO existe en db_productos"
            persistido_fmt = "-" if persistido is None else f"{persistido:,.2f}"
            diff = (persistido or 0.0) - real
            print(f"{codigo_norm:<20}{persistido_fmt:>15}{real:>18,.2f}{diff:>15,.2f}   {nota}")

        print(f"\nTotal divergentes: {len(divergentes)}")

        if not aplicar_fix:
            print("\nSolo lectura -- no se modificó nada. Correr con --fix para aplicar la corrección.")
            return

        aplicables = [c for c, p, _, _ in divergentes if p is not None]
        omitidos = [c for c, p, _, _ in divergentes if p is None]
        if omitidos:
            print(f"\nOMITIDOS del fix (no tienen fila en db_productos): {', '.join(omitidos)}")

        if not aplicables:
            print("Nada para corregir: ningún código divergente tiene fila en db_productos.")
            return

        try:
            PedidosService.recalcular_comprometido(aplicables, session)
            session.commit()
            print(f"\nOK: {len(aplicables)} código(s) corregidos y confirmados.")
        except Exception as e:
            session.rollback()
            print(f"\nERROR aplicando el fix, se hizo rollback: {e}")
            raise


if __name__ == '__main__':
    auditar(aplicar_fix='--fix' in sys.argv)
