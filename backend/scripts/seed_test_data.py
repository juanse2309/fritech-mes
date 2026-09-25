# -*- coding: utf-8 -*-
"""
seed_test_data.py
==================
Genera datos sintéticos hiperrealistas (catálogo de productos, máquinas)
para que los flujos E2E (Playwright) y las pruebas de estrés manuales
tengan algo con qué trabajar en una base de datos de test recién creada
-- hoy no existe ningún script de seed/demo en el repo, y una DB nueva
tiene los catálogos vacíos.

Incluye casos borde a propósito (tildes/ñ, comillas, descripciones largas,
precio en cero) porque además sirve como la herramienta estándar de
"datos sintéticos para pruebas de estrés" que pide CLAUDE.md para
cambios de escritura crítica -- queda reutilizable para los próximos
bloques, no es exclusivo de Playwright.

USO:
    python -m backend.scripts.seed_test_data          # siembra (upsert)
    python -m backend.scripts.seed_test_data --cleanup  # borra lo sembrado

Nunca usar contra una base de producción -- respeta el mismo guard que
tests/conftest.py: se niega a correr si DATABASE_URL no contiene "test",
salvo que se exporte SEED_ALLOW_DB=1 explícitamente. No usa ninguna
credencial hardcodeada -- todo viene de variables de entorno ya
requeridas por la app (DATABASE_URL, FLASK_SECRET_KEY, JWT_PWA_SECRET).
"""
import os
import sys
import argparse
import logging

logger = logging.getLogger("seed_test_data")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

PREFIJO = "SEED-E2E-"


def _guard_db_de_test():
    url = os.environ.get("DATABASE_URL", "")
    permitido_explicito = os.environ.get("SEED_ALLOW_DB") == "1"
    if not permitido_explicito and "test" not in url.lower():
        logger.error(
            "DATABASE_URL no contiene 'test' -- me niego a sembrar/borrar datos "
            "en lo que podría ser una base real. Si de verdad quieres correr "
            "esto contra esa URL, exporta SEED_ALLOW_DB=1 explícitamente."
        )
        sys.exit(1)


def _productos_sinteticos():
    """
    Catálogo mínimo con casos borde reales: tildes/ñ, comillas, precio en
    cero, descripción larga. Todos con codigo_sistema/id_codigo prefijados
    para poder limpiarlos sin tocar nada más.
    """
    return [
        {
            "id_codigo": f"{PREFIJO}PROD-1",
            "codigo_sistema": f"{PREFIJO}PROD-1",
            "descripcion": 'Buje Ñoño Especial 3/4" - Edición "Premium" (áéíóú)',
            "precio": 15000,
            "p_terminado": 50,
            "stock_minimo": 10,
            "stock_maximo": 100,
            "punto_reorden": 5,
        },
        {
            "id_codigo": f"{PREFIJO}PROD-2",
            "codigo_sistema": f"{PREFIJO}PROD-2",
            "descripcion": "Repuesto genérico de prueba sin caracteres especiales",
            "precio": 0,  # borde: precio en cero
            "p_terminado": 0,  # borde: sin stock
            "stock_minimo": 10,
            "stock_maximo": 100,
            "punto_reorden": 5,
        },
        {
            "id_codigo": f"{PREFIJO}PROD-3",
            "codigo_sistema": f"{PREFIJO}PROD-3",
            "descripcion": "D" * 400,  # borde: descripción larga (cerca del límite de 500)
            "precio": 999999.99,
            "p_terminado": 9999,
            "stock_minimo": 10,
            "stock_maximo": 100,
            "punto_reorden": 5,
        },
    ]


def _maquinas_sinteticas():
    return [
        {"nombre": f"{PREFIJO}MAQ-1", "activa": True, "descripcion": "Máquina de prueba E2E"},
    ]


def _clientes_sinteticos():
    return [
        {
            "nombre": f'{PREFIJO}Cliente Ñandú & Cía. "Especial"',
            "identificacion": "900123456-1",
            "direccion": 'Cra 45 # 12-30 Local "B"',
            "ciudad": "Bogotá",
            "telefonos": "3001234567",
        },
    ]


def _bootstrap_objetos_fragiles():
    """
    HALLAZGO (no se corrige acá, este PR es solo de tests/CI): el bloque de
    arranque de backend/app.py (líneas ~157-203) ejecuta varias sentencias
    DDL en un único try/except que hace rollback COMPLETO ante el primer
    error. En una base nueva sin la tabla legacy `cartera_wo` (que ningún
    modelo ORM crea -- vive fuera de db.create_all()), el
    `ALTER TABLE cartera_wo ...` de la línea 162 revienta ANTES de llegar a
    crear la secuencia `pedido_id_seq` (línea ~181), así que
    PedidosService.generar_siguiente_id_pedido() falla con
    "relation pedido_id_seq does not exist" en el primer pedido que se
    intente registrar -- confirmado corriendo este seed contra una DB de
    test recién creada. Esto afecta a cualquier instancia nueva de verdad
    (ver checklist de "cliente nuevo" en CLAUDE.md), no solo a este sandbox.
    Reportado como hallazgo para el Bloque 2 (backend); acá solo se crea la
    secuencia a mano para que el E2E de Pedidos pueda correr.
    """
    from sqlalchemy import text
    from backend.core.sql_database import db

    db.session.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE sequencename = 'pedido_id_seq') THEN
                CREATE SEQUENCE pedido_id_seq START WITH 1001;
            END IF;
        END $$;
    """))
    db.session.commit()


def sembrar():
    from backend.core.sql_database import db
    from backend.models.sql_models import Producto, Maquina, DbClientes

    _bootstrap_objetos_fragiles()

    creados = {"productos": 0, "maquinas": 0, "clientes": 0}

    for datos in _productos_sinteticos():
        existente = Producto.query.filter_by(codigo_sistema=datos["codigo_sistema"]).first()
        if existente:
            for campo, valor in datos.items():
                setattr(existente, campo, valor)
        else:
            db.session.add(Producto(**datos))
            creados["productos"] += 1

    for datos in _maquinas_sinteticas():
        existente = Maquina.query.filter_by(nombre=datos["nombre"]).first()
        if existente:
            existente.activa = datos["activa"]
            existente.descripcion = datos["descripcion"]
        else:
            db.session.add(Maquina(**datos))
            creados["maquinas"] += 1

    for datos in _clientes_sinteticos():
        existente = DbClientes.query.filter_by(nombre=datos["nombre"]).first()
        if existente:
            for campo, valor in datos.items():
                setattr(existente, campo, valor)
        else:
            db.session.add(DbClientes(**datos))
            creados["clientes"] += 1

    db.session.commit()
    logger.info(f"Sembrado completo: {creados}")
    return creados


def limpiar():
    from backend.core.sql_database import db
    from backend.models.sql_models import Producto, Maquina, DbClientes

    borrados_productos = Producto.query.filter(
        Producto.codigo_sistema.like(f"{PREFIJO}%")
    ).delete(synchronize_session=False)
    borrados_maquinas = Maquina.query.filter(
        Maquina.nombre.like(f"{PREFIJO}%")
    ).delete(synchronize_session=False)
    borrados_clientes = DbClientes.query.filter(
        DbClientes.nombre.like(f"{PREFIJO}%")
    ).delete(synchronize_session=False)
    db.session.commit()
    logger.info(
        f"Limpieza completa: productos={borrados_productos} maquinas={borrados_maquinas} "
        f"clientes={borrados_clientes}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true", help="Borra los datos sembrados en vez de crearlos")
    args = parser.parse_args()

    _guard_db_de_test()

    # Importar backend.app ejecuta db.create_all() -- necesario para que las
    # tablas existan en una DB de test recién creada, antes de sembrar nada.
    from backend.app import app

    with app.app_context():
        if args.cleanup:
            limpiar()
        else:
            sembrar()


if __name__ == "__main__":
    main()
