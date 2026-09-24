"""
pausas_service.py
==================
Única fuente de verdad para el descuento de pausas programadas (Desayuno /
Almuerzo) no remuneradas. Compartida por Pulido e Inyección — el frontend
solo envía hora_inicio/hora_fin crudas (Zero Trust); esta clase asume la
autoridad matemática sobre cuánto tiempo trabajado se descuenta.
"""


class PausasService:

    _VENTANAS_PAUSAS_PROGRAMADAS = (
        ("DESAYUNO", "09:00", "09:20"),
        ("ALMUERZO", "13:00", "13:40"),
    )

    @staticmethod
    def obtener_ventanas() -> list:
        """
        Ventanas de pausa programada en formato serializable para el frontend
        (Panel de Supervisión y Modo TV de Pulido): así las horas viven solo
        acá y no se copian a mano en JS, donde un cambio de horario quedaría
        desincronizado con el descuento real que aplica el backend.
        """
        return [
            {"tipo": tipo, "nombre": tipo.capitalize(), "inicio": ini, "fin": fin}
            for tipo, ini, fin in PausasService._VENTANAS_PAUSAS_PROGRAMADAS
        ]

    @staticmethod
    def calcular_descuento_pausas_programadas(hora_inicio, hora_fin) -> dict:
        """
        Calcula el descuento por intersección entre el intervalo trabajado
        [hora_inicio, hora_fin] y las ventanas fijas de Desayuno/Almuerzo.

        Retorna:
        {
            "segundos_descuento": int,
            "detalle": [ {"tipo", "inicio", "fin", "minutos"}, ... ]
        }
        """
        if not hora_inicio or not hora_fin or hora_fin <= hora_inicio:
            return {"segundos_descuento": 0, "detalle": []}

        # Cruce de medianoche: fuera de alcance para las ventanas fijas del turno.
        if hora_inicio.date() != hora_fin.date():
            return {"segundos_descuento": 0, "detalle": []}

        total_segundos = 0
        detalle = []
        for nombre, ini_str, fin_str in PausasService._VENTANAS_PAUSAS_PROGRAMADAS:
            h_i, m_i = (int(x) for x in ini_str.split(':'))
            h_f, m_f = (int(x) for x in fin_str.split(':'))
            ventana_ini = hora_inicio.replace(hour=h_i, minute=m_i, second=0, microsecond=0)
            ventana_fin = hora_inicio.replace(hour=h_f, minute=m_f, second=0, microsecond=0)

            solape_ini = max(hora_inicio, ventana_ini)
            solape_fin = min(hora_fin, ventana_fin)
            solape_seg = int((solape_fin - solape_ini).total_seconds())

            if solape_seg > 0:
                total_segundos += solape_seg
                detalle.append({
                    "tipo": nombre,
                    "inicio": ini_str,
                    "fin": fin_str,
                    "minutos": round(solape_seg / 60.0, 2)
                })

        return {"segundos_descuento": total_segundos, "detalle": detalle}
