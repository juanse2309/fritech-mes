"""
limpiar_datos_prueba_migracion.py
===================================
Script de un solo uso: borra los 2 registros de prueba que quedaron en
friparts-db durante la verificación de la migración Render -> DigitalOcean
(2026-09-13), y anula sus OP asociadas para que no queden sueltas en la
lista de exportables a World Office.

- Lote de Inyección id_inyeccion='INY-20827CD5' (OP INY-304235, FR-9304):
  se probó Programar -> Iniciar -> Finalizar, nunca se validó.
- Meta de Ensamble id_prog=172 (OP ENS-304236, FR-9739 x5): se creó para
  probar el flujo de Programación, nunca se reportó ningún avance.

Ninguna de las dos llegó a tocar inventario real (ver conversación de
migración) -- por eso es seguro borrarlas en vez de solo anularlas.

Se ejecuta UNA VEZ como Tarea Programada de Coolify (Execute Now) contra
el contenedor de la app, y luego se borra tanto la tarea como este archivo.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.app import app
from backend.core.sql_database import db
from backend.models.sql_models import ChecklistEnsamble, ProduccionInyeccion, ProgramacionEnsamble
from backend.services.op_numerador_service import OpNumeradorService

ID_INYECCION_PRUEBA = 'INY-20827CD5'
ID_PROG_ENSAMBLE_PRUEBA = 172
OPS_A_ANULAR = ('INY-304235', 'ENS-304236')
MOTIVO = "Dato de prueba de la verificacion post-migracion Render->DigitalOcean, nunca se produjo de verdad"

with app.app_context():
    # Paso 1: confirmar QUÉ se va a borrar antes de borrarlo.
    filas_iny = ProduccionInyeccion.query.filter_by(id_inyeccion=ID_INYECCION_PRUEBA).all()
    fila_prog = ProgramacionEnsamble.query.filter_by(id_prog=ID_PROG_ENSAMBLE_PRUEBA).first()
    print(f"[CHECK] db_inyeccion filas encontradas: {len(filas_iny)}")
    for f in filas_iny:
        print(f"         id={f.id} id_codigo={f.id_codigo} estado={f.estado} cantidad_real={f.cantidad_real}")
    print(f"[CHECK] db_programacion_ensamble id_prog={ID_PROG_ENSAMBLE_PRUEBA}: "
          f"{'encontrada, id_codigo=' + fila_prog.id_codigo if fila_prog else 'NO encontrada'}")

    try:
        borrados_iny = ProduccionInyeccion.query.filter_by(id_inyeccion=ID_INYECCION_PRUEBA).delete()
        borrados_checklist = ChecklistEnsamble.query.filter_by(id_prog=ID_PROG_ENSAMBLE_PRUEBA).delete()
        borrados_prog = ProgramacionEnsamble.query.filter_by(id_prog=ID_PROG_ENSAMBLE_PRUEBA).delete()
        db.session.commit()
        print(f"[OK] Borrados -- db_inyeccion={borrados_iny}, "
              f"db_checklist_ensamble={borrados_checklist}, db_programacion_ensamble={borrados_prog}")
    except Exception as e:
        db.session.rollback()
        print(f"[ERROR] Falló el borrado: {e}")
        raise

    for numero_op in OPS_A_ANULAR:
        try:
            op = OpNumeradorService.anular(numero_op, MOTIVO, "sistema")
            db.session.commit()
            print(f"[OK] OP {numero_op} anulada, estado={op.estado}")
        except Exception as e:
            db.session.rollback()
            print(f"[ERROR] Falló anulando {numero_op}: {e}")
