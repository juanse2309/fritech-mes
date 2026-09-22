"""
Script de un solo uso (2026-09-22): pone la linea base real de materia
prima, ahora que llegaron 10 toneladas de TPU Desmopan y es buen momento
para arrancar limpio en vez de intentar reconstruir meses de consumo mal
registrado (nunca existio una pantalla de "entrada de materia prima" --
stock_bodega solo se ha restado via BOM al validar Inyeccion, nunca sumado).

- 6 codigos que estaban en negativo y de los que no hay conteo real
  confiable: se dejan en 0 (arranque limpio, no un conteo fisico).
- MP-029 (Desmopan 385SX TPU Covestro Dureza 85): el unico que SI se
  conoce con certeza -- 10 toneladas (10.000 kg) que llegaron ayer,
  empiezan a descontarse desde ayer.

Nota: el usuario senalo que MP-002 ("TPU Dureza 85") y MP-029 ("Desmopan
385SX ... Dureza 85") podrian ser el mismo material con dos codigos
distintos -- no se toco esa posible duplicacion aqui, queda pendiente de
confirmar con quien maneja el catalogo/BOM.

Se corre una sola vez desde la terminal del contenedor en Coolify:
    python -m backend.sql.baseline_materia_prima_2026_09_22
"""
from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import Producto

# codigo_sistema -> nuevo valor absoluto de stock_bodega
BASELINE = {
    "004-TUERCA": 0,
    "MP-001": 0,
    "MP-002": 0,
    "MP-003": 0,
    "MP-006": 0,
    "MP-009": 0,
    "PL-TOPE": 0,
    "MP-029": 10000,  # Desmopan 385SX -- 10 toneladas, llegaron ayer
}

with app.app_context():
    for codigo, valor_nuevo in BASELINE.items():
        producto = db.session.query(Producto).filter_by(codigo_sistema=codigo).first()
        if not producto:
            print(f"ERROR: {codigo} -> no se encontro en db_productos")
            continue

        antes = float(producto.stock_bodega or 0)
        producto.stock_bodega = valor_nuevo
        db.session.commit()
        print(f"OK: {codigo} -> stock_bodega {antes} -> {valor_nuevo}")

print("Listo.")
