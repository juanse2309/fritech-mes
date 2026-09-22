"""
Migración de un solo uso (2026-09-22): marca 3 pedidos que ya tienen su
documento real en World Office (confirmado por el usuario directamente
contra WO / contra la sincronización comercial en db_ventas) pero cuyo
wo_consecutivo quedó vacío en FRITECH porque nunca pasaron por
FacturacionService.procesar_datos_wo -- llegaron a WO por otro camino
(entrada manual, o exportación con plantilla distinta).

No toca 'estado' (sigue describiendo únicamente el picking de Almacén,
sin cambios): solo llena wo_consecutivo, que es la señal que
FacturacionService.listar_pedidos_exportables ya usa para excluir un
pedido de "Pedidos Pendientes" -- ver commit del fix 2026-09-22
("Pedidos Pendientes ya no vuelve a mostrar pedidos ya exportados a WO").

Casos (confirmados por el usuario 2026-09-22):
  - id_pedido '104453' (JIMMY MAURICIO FUERTES GOMEZ) -> WO: documento
    104453 PED, visto directamente en el reporte de World Office.
  - id_pedido '10402' (TECNI-GRAPAS LTDA, FR-9002 x50) -> se deja el mismo
    número 10402 a pedido explícito del usuario. ADVERTENCIA: el documento
    real 'PED-10402' que trae la sincronización comercial (db_ventas) es de
    otros productos (FR-9721/FR-9318/FR-9628) para el mismo cliente -- son
    casi con toda seguridad dos pedidos distintos que coinciden en número
    por un error de digitación al crear este pedido interno. El usuario
    decidió marcarlo como resuelto igual, aceptando ese riesgo. Si más
    adelante se nota que el pedido de FR-9002 x50 nunca llegó de verdad a
    WO, revisar este caso primero.
  - id_pedido 'PED-638369' (MEDINA SUPPLAY C.A, plantilla de Exportación)
    -> WO: documento 10302, confirmado por fecha (17/07/2026) y cantidad de
    líneas (27) coincidiendo exacto contra la sincronización comercial.

Se corre una sola vez desde la terminal del contenedor en Coolify:
    python -m backend.sql.migrate_marcar_pedidos_ya_en_wo_2026_09_22
"""
from backend.core.sql_database import db
from backend.app import app
from backend.models.sql_models import Pedido

CASOS = [
    {"id_pedido": "104453", "wo_consecutivo": "104453"},
    {"id_pedido": "10402", "wo_consecutivo": "10402"},
    {"id_pedido": "PED-638369", "wo_consecutivo": "10302"},
]

with app.app_context():
    for caso in CASOS:
        id_pedido = caso["id_pedido"]
        nuevo_consecutivo = caso["wo_consecutivo"]

        filas = db.session.query(Pedido).filter_by(id_pedido=id_pedido).all()
        if not filas:
            print(f"OMITIDO: no se encontró ningún pedido con id_pedido='{id_pedido}'")
            continue

        ya_marcadas = [f for f in filas if (f.wo_consecutivo or '').strip()]
        if ya_marcadas:
            print(
                f"OMITIDO: '{id_pedido}' ya tiene wo_consecutivo="
                f"'{ya_marcadas[0].wo_consecutivo}' en {len(ya_marcadas)}/{len(filas)} "
                f"líneas -- no se sobreescribe, revisar a mano."
            )
            continue

        actualizadas = db.session.query(Pedido).filter_by(id_pedido=id_pedido).update(
            {"wo_consecutivo": nuevo_consecutivo}
        )
        db.session.commit()
        print(f"OK: '{id_pedido}' -> wo_consecutivo='{nuevo_consecutivo}' ({actualizadas} líneas actualizadas)")

print("Listo.")
