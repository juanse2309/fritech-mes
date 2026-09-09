import os
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
import logging
from backend.config.settings import Empresa

logger = logging.getLogger(__name__)

class PDFGenerator:
    """Servicio para generar reportes PDF de registros de producción."""
    
    @staticmethod
    def _safe_int(val, default=0):
        try:
            if val is None or str(val).strip() == "": return default
            return int(float(val))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_float(val, default=0.0):
        try:
            if val is None or str(val).strip() == "": return default
            return float(val)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def generar_reporte_inyeccion(datos_fila, filepath, pnc=0, producto_nombre=""):
        """
        Genera un PDF profesional basado en los 22 campos de inyección.
        datos_fila: List con los valores de las 22 columnas.
        filepath: Ruta donde se guardará el archivo temporalmente.
        pnc: Cantidad de piezas no conformes.
        producto_nombre: Descripción completa del producto.
        """
        try:
            doc = SimpleDocTemplate(filepath, pagesize=letter)
            styles = getSampleStyleSheet()
            elements = []

            # Título y Cabecera Profesional
            titulo_style = ParagraphStyle(
                'TituloStyle',
                parent=styles['Heading1'],
                fontSize=20,
                alignment=1, # Center
                textColor=colors.darkblue,
                spaceAfter=10
            )
            op_val = str(datos_fila[18]).upper()
            title_text = f"REPORTE DE PRODUCCIÓN - {op_val}" if op_val.startswith("OP") else f"REPORTE DE PRODUCCIÓN - OP {op_val}"
            elements.append(Paragraph(title_text, titulo_style))
            
            subtitulo_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, alignment=1, textColor=colors.grey, spaceAfter=20)
            elements.append(Paragraph(f"Trazabilidad de Proceso {Empresa.NOMBRE}", subtitulo_style))
            elements.append(Spacer(1, 0.1 * inch))

            # Información General (Layout de dos columnas)
            producto_display = f"{datos_fila[6]}"
            if producto_nombre and producto_nombre != datos_fila[6]:
                producto_display = f"{datos_fila[6]} - {producto_nombre}"

            data_info = [
                ["ID REGISTRO:", datos_fila[0], "FECHA:", datos_fila[1]],
                ["RESPONSABLE:", str(datos_fila[5]).upper(), "MÁQUINA:", datos_fila[4]],
                ["PRODUCTO:", Paragraph(producto_display, styles['Normal']), "ORDEN PROD:", datos_fila[18]],
                ["ALMACÉN:", datos_fila[16], "PROCESO:", "INYECCIÓN"],
            ]

            t1 = Table(data_info, colWidths=[1.3*inch, 2.3*inch, 1.2*inch, 1.7*inch])
            t1.setStyle(TableStyle([
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TEXTCOLOR', (0,0), (0,-1), colors.darkblue),
                ('TEXTCOLOR', (2,0), (2,-1), colors.darkblue),
                ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ]))
            elements.append(t1)
            elements.append(Spacer(1, 0.3 * inch))

            # Sección de Tiempos
            elements.append(Paragraph("<b>DETALLE DE TIEMPOS</b>", styles['Heading4']))
            data_tiempos = [
                ["Llegada", "Inicio", "Término", "No. Cavidades"],
                [datos_fila[8], datos_fila[9], datos_fila[10], datos_fila[7]]
            ]
            t2 = Table(data_tiempos, colWidths=[1.625*inch]*4)
            t2.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            elements.append(t2)
            elements.append(Spacer(1, 0.3 * inch))

            # Sección de Producción y Calidad
            elements.append(Paragraph("<b>PRODUCCIÓN Y CALIDAD</b>", styles['Heading4']))
            
            # Cálculo de Proyección Proyectada (Teórica)
            cierres = PDFGenerator._safe_int(datos_fila[11])
            cavidades = PDFGenerator._safe_int(datos_fila[7], default=1)
            proyeccion_teorica = cierres * cavidades
            
            cant_inyectada = PDFGenerator._safe_int(datos_fila[15])
            pnc_val = PDFGenerator._safe_int(pnc)
            buenas_val = cant_inyectada - pnc_val
            eficiencia = (cant_inyectada / proyeccion_teorica * 100) if proyeccion_teorica > 0 else 0
            
            data_prod = [
                ["CONCEPTO", "DATO", "RESULTADO"],
                ["Cierres de Máquina", f"{cierres}", "CONTADOR"],
                ["Cantidad Proyectada", f"{proyeccion_teorica} pz", f"{cavidades} cav"],
                ["Total Inyectado (Bruto)", f"{cant_inyectada} pz", f"EFIC: {eficiencia:.1f}%"],
                ["Piezas No Conformes (PNC)", f"{pnc_val} pz", "RECHAZO"],
                ["Cantidad Real (Neto OK)", f"{buenas_val} pz", "NETO"],
                ["Peso Consolidado", f"{datos_fila[21]} kg", "Bujes"],
                ["Peso Vela Máquina", f"{datos_fila[20]} kg", "Material"],
            ]
            t3 = Table(data_prod, colWidths=[2.5*inch, 2.5*inch, 1.5*inch])
            t3.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,0), 'CENTER'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('TEXTCOLOR', (1,4), (1,4), colors.red if pnc_val > 0 else colors.black),
                ('FONTNAME', (0,5), (2,5), 'Helvetica-Bold'), # Resaltar Buenas
                ('BACKGROUND', (0,5), (2,5), colors.lightgreen),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                # Alertar si la eficiencia es baja
                ('TEXTCOLOR', (2,3), (2,3), colors.red if eficiencia < 90 else colors.darkgreen),
            ]))
            elements.append(t3)
            elements.append(Spacer(1, 0.3 * inch))

            # Observaciones / Novedades
            elements.append(Paragraph("<b>OBSERVACIONES / NOVEDADES</b>", styles['Heading4']))
            obs_text = str(datos_fila[19]) if datos_fila[19] else "Ninguna"
            elements.append(Paragraph(str(obs_text), styles['Normal']))

            # Pie de página
            footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=7, textColor=colors.grey, alignment=1)
            elements.append(Spacer(1, 0.6 * inch))
            elements.append(Paragraph("-" * 150, footer_style))
            responsable = str(datos_fila[5]).upper()
            footer_text = f"Documento de Control Interno {Empresa.NOMBRE} | Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Validado en sistema por: {responsable}"
            elements.append(Paragraph(footer_text, footer_style))

            doc.build(elements)
            logger.info(f"PDF generado exitosamente en: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error generando PDF: {str(e)}")
            return False

    @staticmethod
    def generar_reporte_inyeccion_lote(turno, items, filepath):
        """
        Genera un PDF para un lote Multi-SKU (Molde de Familia).
        turno: Dict con datos de cabecera.
        items: List[Dict] con datos de cada producto.
        """
        try:
            doc = SimpleDocTemplate(filepath, pagesize=letter)
            styles = getSampleStyleSheet()
            elements = []

            # Título y Cabecera Profesional
            titulo_style = ParagraphStyle(
                'TituloStyle',
                parent=styles['Heading1'],
                fontSize=20,
                alignment=1, # Center
                textColor=colors.darkblue,
                spaceAfter=5
            )
            op_val = str(turno.get('orden_produccion', 'S/OP')).upper()
            title_text = f"REPORTE DE PRODUCCIÓN - {op_val}" if op_val.startswith("OP") else f"REPORTE DE PRODUCCIÓN - OP {op_val}"
            elements.append(Paragraph(title_text, titulo_style))
            
            subtitulo_style = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10, alignment=1, textColor=colors.grey, spaceAfter=20)
            elements.append(Paragraph(f"Gestión Multi-SKU {Empresa.NOMBRE}", subtitulo_style))
            elements.append(Spacer(1, 0.1 * inch))

            obs_text_turno = str(turno.get('observaciones') or turno.get('observaciones_generales') or "").strip()
            if (not obs_text_turno or obs_text_turno == 'None') and items:
                obs_text_turno = str(items[0].get('observaciones') or "").strip()
            if not obs_text_turno or obs_text_turno == 'None':
                obs_text_turno = "Ninguna"

            # Información de Turno (Ajustada a requerimiento de trazabilidad)
            data_turno = [
                ["FECHA VALIDACIÓN:", turno.get('fecha_inicio', ''), "RESPONSABLE:", str(turno.get('responsable', '')).upper()],
                ["MÁQUINA:", turno.get('maquina', ''), "ORDEN PROD (OP):", str(turno.get('orden_produccion', '')).upper()],
                ["ENTRADA:", f"{turno.get('entrada_manual', 0)} kg", "SALIDA:", f"{turno.get('salida_manual', 0)} kg"],
                ["HORA INICIO:", turno.get('hora_inicio', ''), "HORA TERMINA:", turno.get('hora_termina', '')],
                ["PESO VELA MÁQ:", f"{turno.get('peso_vela_maquina', 0)} kg", "OBSERVACIONES:", Paragraph(obs_text_turno, styles['Normal'])]
            ]
            t_turno = Table(data_turno, colWidths=[1.5*inch, 2.0*inch, 1.5*inch, 2.5*inch])
            t_turno.setStyle(TableStyle([
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TEXTCOLOR', (0,0), (0,-1), colors.darkblue),
                ('TEXTCOLOR', (2,0), (2,-1), colors.darkblue),
                ('GRID', (0,0), (-1,-1), 0.5, colors.whitesmoke),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            elements.append(t_turno)
            elements.append(Spacer(1, 0.25 * inch))

            # Tabla de Productos
            elements.append(Paragraph("<b>DETALLE DE PRODUCTOS PROCESADOS</b>", styles['Heading4']))
            
            headers = ["PRODUCTO", "PESO BUJE (kg)", "CAV", "CONTADOR", "CANT. PROYECTADA", "CANTIDAD REAL", "PNC", "EFICIENCIA (%)"]
            data_items = [headers]
            
            total_proyectado = 0
            total_buenas = 0
            total_pnc = 0
            total_peso = 0
            
            for it in items:
                cav = PDFGenerator._safe_int(it.get('no_cavidades'), default=1)
                disp = PDFGenerator._safe_int(it.get('disparos'))
                proyectado = disp * cav
                
                # Cantidad Real reportada (bruto o neto según el caso, lo normalizamos a buenas después)
                real_reportado = PDFGenerator._safe_int(it.get('cantidad_real')) or proyectado
                pnc = PDFGenerator._safe_int(it.get('pnc'))
                
                # Lógica de buenas (Neto OK) para la columna CANTIDAD REAL
                manual_buenas = it.get('manual_buenas')
                if manual_buenas is not None and str(manual_buenas).strip() != "":
                    buenas = PDFGenerator._safe_int(manual_buenas)
                else:
                    buenas = real_reportado - pnc
                
                peso = PDFGenerator._safe_float(it.get('peso_bujes'))
                item_efic = (buenas / proyectado * 100) if proyectado > 0 else 0
                
                total_proyectado += proyectado
                total_buenas += buenas
                total_pnc += pnc
                total_peso += peso
                
                data_items.append([
                    Paragraph(str(it.get('codigo_producto') or 'S/C'), styles['Normal']),
                    f"{peso:.3f}",
                    cav,
                    disp,
                    proyectado,
                    buenas,
                    pnc,
                    f"{item_efic:.1f}%"
                ])

            # Fila de Totales
            total_efic_promedio = (total_buenas / total_proyectado * 100) if total_proyectado > 0 else 0
            data_items.append(["TOTALES", "", "", "", f"{total_proyectado}", f"{total_buenas}", f"{total_pnc}", f"{total_efic_promedio:.1f}%"])

            t_prod = Table(data_items, colWidths=[1.4*inch, 1.0*inch, 0.4*inch, 0.6*inch, 1.1*inch, 1.0*inch, 0.5*inch, 1.0*inch])
            t_prod.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.darkblue),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,0), 'CENTER'),
                ('ALIGN', (0,1), (0,-1), 'LEFT'),
                ('ALIGN', (1,1), (-1,-1), 'CENTER'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
                ('BACKGROUND', (0,-1), (-1,-1), colors.lightgrey),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                # Colorear en rojo si hay diferencia significativa entre proyectado y real
                ('TEXTCOLOR', (4,1), (4,-2), colors.darkred),
            ]))
            elements.append(t_prod)
            elements.append(Spacer(1, 0.4 * inch))

            # Resumen Ejecutivo
            elements.append(Paragraph("<b>RESUMEN DE CUANTIFICACIÓN</b>", styles['Heading4']))
            eficiencia_lote = (total_buenas / total_proyectado * 100) if total_proyectado > 0 else 0
            resumen_data = [
                ["Eficiencia Promedio OP", f"{eficiencia_lote:.1f}%", "Estado: REGISTRADO"],
                ["Total Cantidad Real (Neto)", f"{total_buenas} pz", "Total Descarte (PNC): " + f"{total_pnc} pz"],
                ["Total Peso Consolidado", f"{total_peso:.3f} kg", "Almacén Destino: " + str(turno.get('almacen_destino', ''))]
            ]
            t_res = Table(resumen_data, colWidths=[2.1*inch, 2.1*inch, 2.3*inch])
            t_res.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BACKGROUND', (0,0), (0,-1), colors.whitesmoke),
            ]))
            elements.append(t_res)

            # Observaciones / Novedades
            elements.append(Paragraph("<b>OBSERVACIONES / NOVEDADES</b>", styles['Heading4']))
            # Buscar observaciones en el turno o en el primer item (usualmente se envían en el payload principal)
            obs_text = str(turno.get('observaciones') or turno.get('observaciones_generales') or "").strip()
            if (not obs_text or obs_text == 'None') and items:
                obs_text = str(items[0].get('observaciones') or "").strip()
            if not obs_text or obs_text == 'None':
                obs_text = "Ninguna"
            elements.append(Paragraph(str(obs_text), styles['Normal']))

            # Pie de página
            footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=7, textColor=colors.grey, alignment=1)
            elements.append(Spacer(1, 0.6 * inch))
            elements.append(Paragraph("-" * 150, footer_style))
            responsable = str(turno.get('responsable', '')).upper()
            footer_text = f"Documento de Control Interno {Empresa.NOMBRE} | Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Validado en sistema por: {responsable}"
            elements.append(Paragraph(footer_text, footer_style))

            doc.build(elements)
            return True
        except Exception as e:
            logger.error(f"Error generando PDF Batch: {str(e)}")
            return False
