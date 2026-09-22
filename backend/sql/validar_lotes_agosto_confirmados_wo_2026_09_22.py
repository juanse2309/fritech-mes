"""
Script de un solo uso (2026-09-22): valida 8 lotes de Inyeccion de agosto
(304179, 304175, 304173, 304176, 304174, 304171, 304170, 304162) que
llevaban semanas en "Pendientes de Validacion" sin que nadie les diera clic.

Confirmados contra World Office ANTES de correr esto (Auditoria de OP,
AuditoriaService.obtener_conciliacion_ops): las 8 OP ya existen alla, asi
que el problema no era que faltara subir nada a WO -- era puramente que
nadie habia hecho el paso de Validacion en la app.

OP 304176 (lote INY-D72A8821): el codigo 9736 reporta 188 buenas contra
184 teoricas (184 disparos x 1 cavidad) -- 4 piezas de mas que el contador
no explica. Se le pregunto al usuario antes de incluirlo: confirmo que es
normal para esta referencia, se incluye tal cual viene reportado.

Los lotes de septiembre (17/21-Sep) NO se incluyen -- no se auditaron
contra WO todavia.

No se trae ningun dato desde db_op_wo_staging (el espejo de WO): se
confirmo que ninguno de estos 7 lotes tiene un campo faltante en la app
(todos ya traen Cantidad Real capturada por el operario en el reporte de
maquina) -- WO solo sirvio para confirmar que la OP es real, no aporta
PNC ni desglose de buenas/defectuosas, que WO no registra.

Usa exactamente los datos YA presentes en cada reporte (cantidad_real,
pnc_total, cant_contador, cavidades) -- no corrige ni inventa nada. El
validador queda marcado como script automatico, NO como una persona, para
que quede trazable en validado_por que esto no paso por una revision de
Calidad real.

Se corre una sola vez desde la terminal del contenedor en Coolify:
    python -m backend.sql.validar_lotes_agosto_confirmados_wo_2026_09_22
"""
from backend.app import app
from backend.services.inyeccion_service import InyeccionService

USUARIO_VALIDACION = "Auditoria OP (script 2026-09-22, sin revision de Calidad)"

LOTES = {
    "INY-E5FE41E1": [  # OP 304179
        {"codigo": "9988", "pnc_inyeccion": 0, "disparos": 30, "no_cavidades": 2, "buenas": 60},
    ],
    "INY-D4CDA2D6": [  # OP 304175
        {"codigo": "9001", "pnc_inyeccion": 0, "disparos": 100, "no_cavidades": 1, "buenas": 102},
    ],
    "INY-13876D83": [  # OP 304173
        {"codigo": "9319", "pnc_inyeccion": 0, "disparos": 200, "no_cavidades": 1, "buenas": 200},
        {"codigo": "9865", "pnc_inyeccion": 0, "disparos": 200, "no_cavidades": 1, "buenas": 198},
        {"codigo": "9828", "pnc_inyeccion": 0, "disparos": 200, "no_cavidades": 1, "buenas": 200},
        {"codigo": "9888", "pnc_inyeccion": 0, "disparos": 200, "no_cavidades": 1, "buenas": 199},
        {"codigo": "9708", "pnc_inyeccion": 0, "disparos": 200, "no_cavidades": 1, "buenas": 199},
        {"codigo": "9725", "pnc_inyeccion": 0, "disparos": 200, "no_cavidades": 1, "buenas": 200},
    ],
    "INY-D72A8821": [  # OP 304176
        {"codigo": "9863", "pnc_inyeccion": 0, "disparos": 184, "no_cavidades": 1, "buenas": 183},
        {"codigo": "9736", "pnc_inyeccion": 0, "disparos": 184, "no_cavidades": 1, "buenas": 188},
        {"codigo": "9308", "pnc_inyeccion": 0, "disparos": 184, "no_cavidades": 1, "buenas": 176},
        {"codigo": "9757", "pnc_inyeccion": 9, "disparos": 184, "no_cavidades": 1, "buenas": 184},
        {"codigo": "9631", "pnc_inyeccion": 0, "disparos": 184, "no_cavidades": 1, "buenas": 185},
        {"codigo": "9311", "pnc_inyeccion": 0, "disparos": 184, "no_cavidades": 1, "buenas": 181},
        {"codigo": "9701", "pnc_inyeccion": 0, "disparos": 184, "no_cavidades": 1, "buenas": 180},
        {"codigo": "9962", "pnc_inyeccion": 0, "disparos": 184, "no_cavidades": 1, "buenas": 184},
    ],
    "INY-C3AB78C2": [  # OP 304174
        {"codigo": "9765", "pnc_inyeccion": 0, "disparos": 120, "no_cavidades": 2, "buenas": 240},
    ],
    "INY-735D4146": [  # OP 304171
        {"codigo": "9001", "pnc_inyeccion": 0, "disparos": 19, "no_cavidades": 1, "buenas": 21},
    ],
    "INY-7A8E8702": [  # OP 304170
        {"codigo": "9765", "pnc_inyeccion": 0, "disparos": 77, "no_cavidades": 2, "buenas": 159},
    ],
    "INY-4A61EF4C": [  # OP 304162
        {"codigo": "9742", "pnc_inyeccion": 0, "disparos": 134, "no_cavidades": 2, "buenas": 134},
    ],
}

with app.app_context():
    for id_inyeccion, items in LOTES.items():
        try:
            resultado = InyeccionService.validar_lote(
                id_inyeccion, {"items": items}, USUARIO_VALIDACION
            )
            print(f"OK: {id_inyeccion} -> {resultado.get('message')}")
        except Exception as e:
            print(f"ERROR: {id_inyeccion} -> {e}")

print("Listo.")
