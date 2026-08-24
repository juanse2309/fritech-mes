from backend.utils.auth_middleware import require_role, ROL_ADMINS, ROL_COMERCIALES, ROL_JEFES, _obtener_usuario_activo
from flask import Blueprint, request, make_response, current_app
from backend.core.responses import api_success, api_error
from backend.models.sql_models import db, Pedido, MetalsPedido, DespachoPedido
from backend.services.audit_service import AuditService, OwnershipMismatchException
from backend.config.constants import FALLBACK_OPERARIO
from sqlalchemy import text
from backend.core.tenant import get_tenant_from_request
from backend.utils.time_utils import get_colombia_time
from backend.utils.formatters import normalizar_codigo_sin_prefijo, sql_expr_codigo_sin_prefijo_fr, preservar_o_normalizar_prefijo
from datetime import datetime
import logging
import json


pedidos_bp = Blueprint('pedidos', __name__)
logger = logging.getLogger(__name__)

# Superset de roles con acceso a las páginas "pedidos"/"almacen" en el frontend
# (ver frontend/static/js/modules/auth.js) — usado para los endpoints de
# consulta/gestión interna de pedidos que no tienen matiz de ownership por cliente.
ROLES_PEDIDOS_INTERNOS = ROL_ADMINS + ROL_COMERCIALES + ROL_JEFES + ['ALISTAMIENTO']


def _resolve_tenant():
    """Resuelve el tenant desde la sesión."""
    tenant = get_tenant_from_request()
    logger.debug(f"🏢 [Tenant] Resuelto: {tenant}")
    return tenant

def _buscar_producto_inteligente(codigo_buscado, registros):
    """
    Búsqueda inteligente en registros de PRODUCTOS.
    Jerarquía: Exacto > Prefijo (solo para CAR, INT, ENS, CB).
    Retorna (fila_real, datos_dict) o (None, None).
    """
    target = str(codigo_buscado).strip().upper()
    if not target:
        return None, None

    PREFIJOS_FLEX = ("CAR", "INT", "ENS", "CB")
    es_comp = target.startswith(PREFIJOS_FLEX)

    for idx, r in enumerate(registros):
        v_sis = str(r.get("CODIGO SISTEMA", "")).strip().upper()
        v_id = str(r.get("ID CODIGO", "")).strip().upper()

        # 1. Coincidencia Exacta
        if target == v_sis or target == v_id:
            return idx + 2, r

        # 2. Coincidencia por Prefijo (Solo componentes)
        if es_comp:
            if v_sis.startswith(target) or v_id.startswith(target):
                logger.debug(f"   🔍 [Prefix Match] '{target}' hallado en '{v_sis or v_id}'")
                return idx + 2, r

    return None, None

def _validar_ownership_cliente(nit_pedido):
    """
    Guard de acceso para endpoints de consulta de pedidos consumidos tanto por
    staff interno como por el portal B2B de clientes:
    - Exige sesión/JWT válido (bloquea acceso anónimo).
    - Si el rol activo es CLIENTE, exige que el NIT del pedido consultado
      coincida con el NIT de su propia empresa (evita IDOR: un cliente
      autenticado no puede enumerar/consultar pedidos de otro NIT).
    - Cualquier otro rol interno autenticado conserva el acceso sin restricción
      de NIT (comportamiento previo para el módulo de staff).

    Retorna None si el acceso es válido, o (response, status) si debe rechazarse.
    """
    from backend.utils.auth_middleware import obtener_identidad_segura
    from backend.models.sql_models import Usuario

    user, role = obtener_identidad_segura(request)
    if not user:
        return api_error("No autorizado", status_code=401)

    if str(role or '').strip().upper() == 'CLIENTE':
        cuenta = Usuario.query.filter_by(username=user, rol='cliente').first()
        nit_propio = str(getattr(cuenta, 'nit_empresa', '') or '').strip()
        if not nit_propio or nit_propio != str(nit_pedido or '').strip():
            return api_error("No autorizado para consultar este pedido", status_code=403)

    return None

@pedidos_bp.route('/api/pedidos/registrar', methods=['POST'])
@require_role(ROL_ADMINS + ROL_COMERCIALES + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def registrar_pedido():
    """
    Registra o actualiza un pedido en PostgreSQL (SQL-Native).
    Implementa lógica de UPSERT basada en id_sql e id_pedido.
    """
    from backend.models.sql_models import Pedido
    from backend.core.sql_database import db
    from backend.services.pedidos_service import generar_siguiente_id_pedido, GeneracionIdPedidoError

    try:
        # Asegurar transacción limpia
        db.session.rollback()

        logger.debug("🛒 ===== INICIO REGISTRO DE PEDIDO (UPSERT-MODE) =====")
        data = request.json
        if not data:
            return api_error("No data provided", status_code=400)

        # 1. Extracción y Validación
        fecha_str = data.get('fecha')
        vendedor = data.get('vendedor')
        cliente = data.get('cliente')
        nit = data.get('nit', '')
        direccion = data.get('direccion', '')
        ciudad = data.get('ciudad', '')
        forma_pago = data.get('forma_pago', 'Contado')
        descuento_global = str(data.get('descuento_global', '0'))
        observaciones = data.get('observaciones', '')
        productos = data.get('productos', [])
        
        if not all([fecha_str, vendedor, cliente]):
            return api_error("Faltan campos obligatorios: fecha, vendedor, cliente", status_code=400)
            
        # 2. Determinar ID de Pedido (Nuevo o Existente)
        id_pedido_final = data.get('id_pedido')
        es_edicion = False
        if not id_pedido_final:
            try:
                id_pedido_final = generar_siguiente_id_pedido(db.session)
            except GeneracionIdPedidoError as e:
                logger.error(f"❌ Fallo generando id_pedido: {e}")
                return api_error(
                    "No fue posible generar un identificador de pedido único. Intente nuevamente.",
                    status_code=409, code="ID_PEDIDO_CONFLICT"
                )
            logger.debug(f"🆔 Nuevo ID Pedido Generado: {id_pedido_final}")
        else:
            id_pedido_final = str(id_pedido_final).strip().upper()
            es_edicion = True
            logger.debug(f"🔄 Actualizando Pedido Existente: {id_pedido_final}")

        # Guard de ownership centralizado con AuditService e identificación desacoplada HTTP
        registro_previo = Pedido.query.filter_by(id_pedido=id_pedido_final).first() if es_edicion else None
        usuario_activo = _obtener_usuario_activo()
        candidato_vendedor = vendedor or usuario_activo

        if not candidato_vendedor or str(candidato_vendedor).strip().upper() in ['', 'SISTEMA']:
            return api_error(
                "Se requiere la identidad del vendedor o usuario activo para crear/editar el pedido",
                status_code=400, code="RESPONSABLE_REQUERIDO"
            )

        try:
            vendedor = AuditService.resolver_y_validar_propietario(registro_previo, candidato_vendedor)
        except OwnershipMismatchException as e:
            return api_error(
                e.message, status_code=409, code="PEDIDOS_SESSION_OWNERSHIP_MISMATCH",
                responsable_db=e.responsable_db, responsable_in=e.responsable_in
            )

        # Convertir fecha
        try:
            fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        except:
            fecha_dt = datetime.now().date()

        # Capturar hora actual
        try:
            hora_actual = get_colombia_time().strftime('%I:%M %p')
        except:
            hora_actual = datetime.now().strftime('%I:%M %p')

        # 3. Guardar cada ítem con Lógica UPSERT
        if not productos:
             return api_error("Debe incluir al menos un producto", status_code=400)

        # Sincronización de eliminaciones: Si es edición, borrar lo que ya no viene en el payload
        if es_edicion:
            ids_enviados = [p.get('id_sql') for p in productos if p.get('id_sql')]
            if ids_enviados:
                try:
                    db.session.execute(
                        text("DELETE FROM db_pedidos WHERE id_pedido = :id_p AND id NOT IN :ids"),
                        {"id_p": id_pedido_final, "ids": tuple(ids_enviados)}
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Error limpiando items eliminados: {e}")

        items_procesados = 0
        for prod in productos:
            id_sql = prod.get('id_sql')
            # db_pedidos (Pedido) es EXCLUSIVAMENTE FriParts -- Frimetals vive en
            # metals_pedidos, una tabla distinta que esta ruta nunca escribe. Por
            # eso, a diferencia de Inyeccion/Pulido (donde la division reportada
            # es ambigua y NO debe inferirse), aqui es seguro y necesario anteponer
            # 'FR-' a un codigo numerico huerfano vía opt-in explicito: sin el
            # prefijo, procesar_datos_wo() (facturacion_routes.py) escribe la
            # referencia incompleta en el archivo plano y World Office rechaza la
            # carga.
            codigo = preservar_o_normalizar_prefijo(prod.get('codigo', ''), prefijo_defecto='FR-').upper()
            cantidad = float(prod.get('cantidad', 0))
            precio = float(prod.get('precio_unitario', 0))
            descripcion = prod.get('descripcion', '')
            total_item = cantidad * precio

            # Trazabilidad de conversión USD->COP (pedidos de exportación,
            # ver botón "Consultar TRM" en el frontend). Ambos quedan en
            # NULL si el item se cotizó directamente en COP.
            precio_usd_raw = prod.get('precio_usd')
            trm_aplicada_raw = prod.get('trm_aplicada')
            precio_usd = float(precio_usd_raw) if precio_usd_raw not in (None, '', 0) else None
            trm_aplicada = float(trm_aplicada_raw) if trm_aplicada_raw not in (None, '', 0) else None
            
            if not codigo or cantidad <= 0:
                continue

            if precio <= 0:
                logger.warning(
                    f"⚠️ [Fuga de Precios] Item guardado con precio $0 o nulo. "
                    f"Pedido: {id_pedido_final}, Producto: {codigo}, Cantidad: {cantidad}, "
                    f"Vendedor: {vendedor}, Cliente: {cliente}"
                )

            # Buscar registro existente para UPSERT
            registro_existente = None
            if id_sql:
                registro_existente = Pedido.query.get(id_sql)
            else:
                # Fallback: Buscar por id_pedido + id_codigo, tolerante al prefijo.
                # Las líneas guardadas antes de retirar la inyección automática
                # viven como 'FR-9304' y el payload nuevo trae '9304': comparar en
                # crudo insertaría una línea duplicada en vez de actualizarla.
                registro_existente = Pedido.query.filter(
                    Pedido.id_pedido == id_pedido_final,
                    sql_expr_codigo_sin_prefijo_fr(Pedido.id_codigo) == normalizar_codigo_sin_prefijo(codigo)
                ).first()

            if registro_existente:
                # LÓGICA DE SUMA ATÓMICA (Juan Sebastian Request)
                # Si viene id_sql, es una edición directa de esa fila -> Sobrecribimos.
                # Si NO viene id_sql pero se halló por id_pedido + id_codigo -> Sumamos.
                if id_sql:
                    registro_existente.cantidad = cantidad
                    logger.debug(f"📝 Sobreescribiendo fila {id_sql} (Edición directa)")
                else:
                    nueva_cantidad = float(registro_existente.cantidad or 0) + cantidad
                    registro_existente.cantidad = nueva_cantidad
                    logger.debug(f"➕ Sumando cantidad a producto existente {codigo}: {cantidad} -> Total: {nueva_cantidad}")

                registro_existente.fecha = fecha_dt
                registro_existente.cliente = cliente
                registro_existente.nit = nit
                registro_existente.direccion = direccion
                registro_existente.ciudad = ciudad
                registro_existente.id_codigo = codigo
                registro_existente.descripcion = descripcion
                registro_existente.precio_unitario = precio
                registro_existente.precio_usd = precio_usd
                registro_existente.trm_aplicada = trm_aplicada
                registro_existente.total = float(registro_existente.cantidad) * precio
                registro_existente.observaciones = observaciones
                registro_existente.forma_de_pago = forma_pago
                registro_existente.descuento = descuento_global
            else:
                # INSERT
                nuevo_registro = Pedido(
                    id_pedido=id_pedido_final,
                    fecha=fecha_dt,
                    hora=hora_actual,
                    cliente=cliente,
                    nit=nit,
                    direccion=direccion,
                    ciudad=ciudad,
                    vendedor=vendedor,
                    id_codigo=codigo,
                    descripcion=descripcion,
                    cantidad=cantidad,
                    precio_unitario=precio,
                    precio_usd=precio_usd,
                    trm_aplicada=trm_aplicada,
                    total=total_item,
                    estado='PENDIENTE',
                    observaciones=observaciones,
                    forma_de_pago=forma_pago,
                    descuento=descuento_global
                )
                db.session.add(nuevo_registro)
            
            items_procesados += 1
            
        # 4. Transacción Final con Commit y Error Handling
        try:
            db.session.commit()
            logger.info(f"✅ Pedido {id_pedido_final} procesado exitosamente. Items: {items_procesados}")
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error en commit de Pedidos: {e}")
            return api_error(f"Error de persistencia: {str(e)}", status_code=500)

        # Invalidad caché
        try:
            from backend.app import invalidar_cache_pedidos
            invalidar_cache_pedidos()
        except: pass

        return api_success(
            data={"id_pedido": id_pedido_final},
            message=f"Pedido {id_pedido_final} procesado correctamente"
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"💥 Error crítico en registrar_pedido: {e}")
        import traceback
        traceback.print_exc()
        return api_error(f"Error interno: {str(e)}", status_code=500)

        # Invalidad caché de pedidos
        try:
            from backend.app import invalidar_cache_pedidos
            invalidar_cache_pedidos()
        except:
            pass

        return api_success(
            data={
                "id_pedido": id_pedido,
                "total_productos": len(productos),
                "productos_guardados": items_agregados
            },
            message=f"Pedido {id_pedido} registrado exitosamente",
            status_code=201
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ ERROR registrando pedido SQL: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return api_error(str(e), status_code=500)

@pedidos_bp.route('/api/pedidos/trm_actual', methods=['GET'])
@require_role(ROL_ADMINS + ROL_COMERCIALES + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def obtener_trm_actual():
    """Consulta la TRM oficial vigente (datos.gov.co), usada para convertir a COP los precios que un vendedor ingresa en USD."""
    from backend.services.trm_service import obtener_trm_oficial, TrmNoDisponibleError
    try:
        trm_info = obtener_trm_oficial()
        return api_success(data=trm_info)
    except TrmNoDisponibleError as e:
        return api_error(str(e), status_code=502)
    except Exception as e:
        logger.error(f"❌ Error obteniendo TRM: {e}")
        return api_error("Error interno consultando la TRM", status_code=500)


@pedidos_bp.route('/api/pedidos/detalle/<id_pedido>', methods=['GET'])
def obtener_detalle_pedido(id_pedido):
    """
    Obtiene el detalle completo de un pedido desde PostgreSQL (SQL-Native).
    Útil para la funcionalidad de 'Retomar Pedido' y edición.
    """
    from backend.models.sql_models import Pedido
    try:
        if not id_pedido:
             return api_error("ID Pedido requerido", status_code=400)

        id_pedido_buscado = str(id_pedido).strip().upper()
        logger.debug(f"🔍 [SQL-DETALLE] Consultando pedido: {id_pedido_buscado}")

        # 1. Buscar todos los items en SQL
        items_sql = Pedido.query.filter_by(id_pedido=id_pedido_buscado).all()

        if not items_sql:
            return api_error(f"Pedido {id_pedido} no encontrado en SQL", status_code=404)
            
        # 2. Construir cabecera (usando el primer item)
        cab = items_sql[0]

        bloqueo = _validar_ownership_cliente(cab.nit)
        if bloqueo:
            return bloqueo

        # Formatear fecha para el input date del frontend (YYYY-MM-DD)
        fecha_str = cab.fecha.strftime('%Y-%m-%d') if cab.fecha else ""

        pedido = {
            "id_pedido": cab.id_pedido,
            "fecha": fecha_str,
            "hora": cab.hora or "",
            "vendedor": cab.vendedor,
            "cliente": cab.cliente,
            "nit": cab.nit or "",
            "direccion": getattr(cab, 'direccion', '') or "",
            "ciudad": getattr(cab, 'ciudad', '') or "",
            "forma_pago": getattr(cab, 'forma_de_pago', 'Contado') or "Contado",
            "descuento_global": getattr(cab, 'descuento', '0') or "0",
            "estado": cab.estado,
            "observaciones": cab.observaciones or "",
            "productos": []
        }
        
        def _clean_numeric(val):
            if not val or str(val).strip() in ['', 'None']: return 0
            # Limpieza matemática: remover símbolos pero NO el punto decimal
            s = str(val).replace('$', '').replace(',', '').strip()
            try:
                # Primero convertimos a float (entiende el punto) y luego a int si necesario
                return int(float(s))
            except: return 0

        # 3. Mapear productos (Ya curados por el script)
        for item in items_sql:
            cant_item = _clean_numeric(item.cantidad)
            ali_item = _clean_numeric(item.cant_alistada)
            precio_item = _clean_numeric(item.precio_unitario)
            
            pedido["productos"].append({
                "id_sql": item.id_sql, # CRÍTICO PARA UPSERT
                "codigo": str(item.id_codigo or '').strip().upper(),
                "descripcion": item.descripcion or "Sin descripción",
                "cantidad": cant_item,
                "precio_unitario": precio_item,
                "precio_usd": float(item.precio_usd) if item.precio_usd else None,
                "trm_aplicada": float(item.trm_aplicada) if item.trm_aplicada else None,
                "cant_alistada": ali_item,
                "cant_lista": ali_item,
                "progreso": item.progreso or "0%"
            })
        
        response = make_response(api_success(data={"pedido": pedido}))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    except Exception as e:
        logger.error(f"❌ ERROR obteniendo detalle pedido SQL: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return api_error(str(e), status_code=500)

@pedidos_bp.route('/api/pedidos/pendientes', methods=['GET'])
@require_role(ROLES_PEDIDOS_INTERNOS)
def obtener_pedidos_pendientes():
    """
    Retorna la lista de pedidos que no están completados.
    Ahora utiliza SQL-First con JOIN de clientes para evitar valores null.
    """
    try:
        from backend.repositories.ventas_repository import VentasRepository
        from backend.models.sql_models import Usuario
        from backend.utils.auth_middleware import obtener_identidad_segura

        # 1. Obtener datos desde SQL (JOIN incluido)
        pedidos = VentasRepository.get_pedidos_pendientes()

        # 2. Determinar tenant para filtrado
        tenant = get_tenant_from_request()

        # 3. Filtrar por usuario y rol (Lógica RBAC Estricta)
        # Identidad extraída EXCLUSIVAMENTE de obtener_identidad_segura (JWT o
        # sesión Flask ya validados por @require_role). El fallback previo a
        # request.args.get('rol'/'usuario') permitía que cualquier usuario
        # autenticado con rol bajo (p.ej. ALISTAMIENTO) anexara ?rol=admin a
        # la URL y viera los pedidos de todos los demás usuarios, saltándose
        # el filtro por delegado_a -- un query param nunca es una fuente
        # válida para una decisión de autorización.
        user_raw, rol_identidad = obtener_identidad_segura(request)
        rol_session = str(rol_identidad or '').lower().strip()

        nombre_completo_user = ""
        username_user = ""
        
        if user_raw:
            user_raw_str = str(user_raw).strip()
            # Buscar usuario en BD para normalizar su correspondencia entre username y nombre completo
            usuario_db = Usuario.query.filter(
                (Usuario.username.ilike(user_raw_str)) |
                (Usuario.nombre_completo.ilike(user_raw_str))
            ).first()
            if usuario_db:
                nombre_completo_user = str(usuario_db.nombre_completo or '').strip().upper()
                username_user = str(usuario_db.username or '').strip().upper()
            else:
                username_user = user_raw_str.upper()

        # Roles con visibilidad global (Excluyendo 'alistador', 'alistamiento' y 'auxiliar almacen' que son operarias de planta)
        es_admin = any(x in rol_session for x in [
            "admin", "administracion", "administrador", "gerencia", 
            "jefe almacen", "jefe alistamiento", "jefe de planta", "comercial", "metals_staff", "metals_admin"
        ])

        filtrados = []
        for p in pedidos:
            if es_admin or tenant == "frimetals":
                filtrados.append(p)
            else:
                # Si no es admin y es Friparts, solo ve lo que tiene asignado
                delegado = str(p.get("delegado_a", "")).strip().upper()
                if delegado and (delegado == username_user or (nombre_completo_user and delegado == nombre_completo_user)):
                    filtrados.append(p)

        # 4. DEBUG EN TERMINAL (Solicitado por el usuario)
        logger.debug(f"DEBUG ALMACEN: Rol detectado: {rol_session}, Usuario normalizado: {username_user} ({nombre_completo_user}), Pedidos totales SQL: {len(pedidos)}, Pedidos filtrados: {len(filtrados)}")

        response = make_response(api_success(data={"pedidos": filtrados}))
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as e:
        logger.error(f"Error cargando pedidos pendientes (SQL): {e}")
        return api_error(str(e), status_code=500)


@pedidos_bp.route('/api/pedidos/delegar', methods=['POST'])
@require_role(ROL_ADMINS + ROL_COMERCIALES + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def delegar_pedido():
    """Asigna un pedido a una colaboradora en SQL."""
    try:
        data = request.json
        id_p = data.get("id_pedido")
        colab = data.get("colaboradora")
        
        if not id_p: return api_error("ID requerido", status_code=400)

        Pedido.query.filter_by(id_pedido=id_p).update({"delegado_a": colab})
        db.session.commit()
        return api_success(message=f"Pedido {id_p} delegado a {colab}")
    except Exception as e:
        db.session.rollback()
        return api_error(str(e), status_code=500)

@pedidos_bp.route('/api/pedidos/<id_pedido>/reasignar_cliente', methods=['PUT'])
@require_role(ROL_ADMINS)
def reasignar_cliente_pedido_route(id_pedido):
    """Reasigna la cabecera comercial (cliente/nit/direccion/ciudad) de un pedido existente.
    Restringido a Admin/Gerencia. Requiere motivo explícito; queda auditado en db_logs.

    Si el pedido ya fue exportado a World Office (estado EXPORTADO_WO), exige
    confirmar_desacople_wo=true explícito en el payload: la corrección solo
    actualiza el MES (Postgres), nunca el documento ya emitido en World Office.
    """
    from backend.services.pedidos_service import (
        reasignar_cliente_pedido,
        ValidacionReasignacionError,
        PedidoNoEncontradoError,
        PedidoEstadoInmutableError,
        DesacopleWoNoAutorizadoError,
    )

    try:
        data = request.json or {}

        nuevo_cliente = data.get('nuevo_cliente')
        nuevo_nit = data.get('nuevo_nit')
        nueva_direccion = data.get('nueva_direccion')
        nueva_ciudad = data.get('nueva_ciudad')
        motivo = data.get('motivo')
        confirmar_desacople_wo = bool(data.get('confirmar_desacople_wo', False))

        if not all([nuevo_cliente, nuevo_nit, motivo]):
            return api_error(
                "Faltan campos obligatorios: nuevo_cliente, nuevo_nit, motivo",
                status_code=400, code="CAMPOS_REQUERIDOS"
            )

        usuario_activo = _obtener_usuario_activo()
        if not usuario_activo:
            return api_error("No fue posible identificar al usuario autenticado", status_code=401)

        try:
            estado_anterior, estado_nuevo, id_pedido_real = reasignar_cliente_pedido(
                id_pedido=id_pedido,
                nuevo_cliente=nuevo_cliente,
                nuevo_nit=nuevo_nit,
                nueva_direccion=nueva_direccion,
                nueva_ciudad=nueva_ciudad,
                usuario=usuario_activo,
                motivo=motivo,
                db_session=db.session,
                permitir_desacople_wo=confirmar_desacople_wo
            )
        except ValidacionReasignacionError as e:
            db.session.rollback()
            return api_error(str(e), status_code=400, code="VALIDACION_INVALIDA")
        except PedidoNoEncontradoError as e:
            db.session.rollback()
            return api_error(str(e), status_code=404, code="PEDIDO_NO_ENCONTRADO")
        except DesacopleWoNoAutorizadoError as e:
            db.session.rollback()
            return api_error(str(e), status_code=403, code="DESACOPLE_WO_NO_AUTORIZADO")
        except PedidoEstadoInmutableError as e:
            db.session.rollback()
            return api_error(str(e), status_code=409, code="ESTADO_INMUTABLE")

        db.session.commit()
        logger.info(f"✅ [REASIGNACION_CLIENTE] Pedido {id_pedido_real} reasignado por {usuario_activo}")

        try:
            from backend.app import invalidar_cache_pedidos
            invalidar_cache_pedidos()
        except: pass

        return api_success(data={
            "id_pedido": id_pedido_real,
            "estado_anterior": estado_anterior,
            "estado_nuevo": estado_nuevo
        }, message=f"Pedido {id_pedido_real} reasignado correctamente")

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error reasignando cliente del pedido {id_pedido}: {e}")
        return api_error(str(e), status_code=500)


@pedidos_bp.route('/api/pedidos/eliminar-producto', methods=['POST'])
@require_role(ROL_ADMINS + ['JEFE ALMACEN', 'JEFE ALISTAMIENTO'])
def eliminar_producto_pedido():
    """Elimina un producto de un pedido en SQL y restaura stock."""
    try:
        data = request.json
        id_p = data.get("id_pedido")
        cod = data.get("codigo")

        item = Pedido.query.filter_by(id_pedido=id_p, id_codigo=cod).first()
        if not item: return api_error("No hallado", status_code=404)
        
        # Restaurar stock
        from backend.services.stock_service import StockService
        # FIX (ticket task_10a6a645): la llamada original pasaba 5 argumentos
        # posicionales contra una firma de 4 (codigo, cantidad, almacen,
        # operacion) y además mandaba "ENTRADA" donde se esperaba 'sumar'/
        # 'restar' — lanzaba TypeError siempre, capturado por el except con
        # rollback, así que la eliminación del producto nunca se confirmaba.
        # `registrar_entrada` es el helper de 3 args que ya encapsula
        # operacion='sumar' para este caso (restaurar stock eliminado).
        logger.info(f"Restaurando stock por eliminación de producto {cod} del pedido {id_p}")
        StockService.registrar_entrada(cod, float(item.cantidad or 0), "STOCK_BODEGA")
        
        db.session.delete(item)
        db.session.commit()
        return api_success()
    except Exception as e:
        db.session.rollback()
        return api_error(str(e), status_code=500)

@pedidos_bp.route('/api/pedidos/actualizar-alistamiento', methods=['POST'])
@require_role(ROLES_PEDIDOS_INTERNOS)
def actualizar_alistamiento():
    """
    Actualiza el progreso y estado de alistamiento de bodega de un pedido.
    Thin controller: parsea el payload, delega en
    PedidosService.actualizar_alistamiento (algoritmo FIFO de cubetas,
    cálculo de progreso, promoción de estado) y responde jsonify. Rollback
    atómico ante cualquier fallo -- ningún cambio parcial queda persistido.
    """
    from backend.core.sql_database import db
    from backend.services.pedidos_service import PedidosService, PedidoNoEncontradoError

    try:
        data = request.json or {}
        id_pedido = data.get("id_pedido")
        estado_general = data.get("estado")  # ej: "EN ALISTAMIENTO"
        progreso_pedido = data.get("progreso")  # ej: "40%"
        progreso_despacho_pedido = data.get("progreso_despacho")  # ej: "10%"
        detalles = data.get("detalles", [])  # [{codigo, cant_lista, despachado, no_disponible}, ...]

        if not id_pedido:
            return api_error("ID Pedido requerido", status_code=400)

        logger.debug(f"📦 [SQL-ALISTAMIENTO] Actualizando pedido: {id_pedido}")

        resultado = PedidosService.actualizar_alistamiento(
            id_pedido=id_pedido,
            estado_general=estado_general,
            progreso_pedido=progreso_pedido,
            progreso_despacho_pedido=progreso_despacho_pedido,
            detalles=detalles,
            db_session=db.session,
        )

        db.session.commit()

        # Opcional: Invalidar caché
        try:
            from backend.app import invalidar_cache_pedidos
            invalidar_cache_pedidos()
        except: pass

        return api_success(
            data={"movimientos_inventario": resultado['movimientos_inventario']},
            message=f"Progreso actualizado en SQL para {resultado['items_actualizados']} productos"
        )

    except PedidoNoEncontradoError as e:
        db.session.rollback()
        return api_error(str(e), status_code=404)
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error alistamiento SQL: {e}")
        return api_error(str(e), status_code=500)


@pedidos_bp.route('/api/pedidos/cliente', methods=['GET'])
def obtener_pedidos_cliente():
    """Historial de pedidos por NIT desde SQL (db_pedidos)."""
    try:
        nit = request.args.get('nit')
        if not nit: return api_error("NIT requerido", status_code=400)

        bloqueo = _validar_ownership_cliente(nit)
        if bloqueo:
            return bloqueo

        items = Pedido.query.filter_by(nit=str(nit)).all()
        # Agrupar por id_pedido
        ped_map = {}
        for r in items:
            id_p = r.id_pedido
            if id_p not in ped_map:
                fecha_str = r.fecha.strftime('%Y-%m-%d') if r.fecha else ""
                hora_str = str(r.hora or "").strip()
                
                ped_map[id_p] = {
                    "id": id_p,
                    "fecha_creacion": f"{fecha_str} {hora_str}".strip() if fecha_str else "",
                    "fecha": fecha_str,
                    "hora": hora_str,
                    "estado": r.estado,
                    "items": 0,
                    "total": 0.0
                }
            else:
                if not ped_map[id_p]["hora"] and r.hora:
                    ped_map[id_p]["hora"] = str(r.hora).strip()
            
            ped_map[id_p]["items"] += float(r.cantidad or 0)
            ped_map[id_p]["total"] += float(r.total or 0)
            
        final = sorted(list(ped_map.values()), key=lambda x: x['id'], reverse=True)
        return api_success(data={"pedidos": final})
    except Exception as e:
        return api_error(str(e), status_code=500)

@pedidos_bp.route('/api/pedidos/listar', methods=['GET'])
@require_role(ROLES_PEDIDOS_INTERNOS)
def listar_pedidos():
    """Listado general de pedidos con soporte estricto para FriMetals."""
    try:
        division = request.args.get('division', 'friparts').lower()
        search = request.args.get('search', '').strip().upper()
        
        logger.debug(f"🔍 [API] Listando pedidos - División: {division}, Búsqueda: {search}")

        if division == 'frimetals':
            # Consulta a la tabla metals_pedidos (mapeada en sql_models.py)
            query = MetalsPedido.query
            if search:
                query = query.filter(
                    (MetalsPedido.id_pedido.ilike(f'%{search}%')) |
                    (MetalsPedido.cliente.ilike(f'%{search}%'))
                )
            
            # Orden descendente por fecha e id_pedido
            rows = query.order_by(MetalsPedido.fecha.desc(), MetalsPedido.id_pedido.desc()).limit(200).all()
            
            # Agrupación por id_pedido para soportar múltiples ítems por orden
            ped_map = {}
            for r in rows:
                id_p = r.id_pedido
                if id_p not in ped_map:
                    # Extraer fecha_creacion real (original) para no usar la fecha_actualizacion masiva de MetalsPedido
                    # Pedido ya está importado globalmente en la línea 3 — NO re-importar aquí (causa UnboundLocalError)
                    original = Pedido.query.filter_by(id_pedido=id_p).first()
                    fecha_orig = original.fecha.strftime('%Y-%m-%d') if original and original.fecha else str(r.fecha)

                    fecha_str_metal = str(r.fecha) if r.fecha else ""
                    hora_str_metal = str(r.hora or "").strip()
                    fecha_mod_metal = f"{fecha_str_metal} {hora_str_metal}".strip() if fecha_str_metal else ""

                    ped_map[id_p] = {
                        "id_pedido": id_p,
                        "fecha_creacion": fecha_orig,
                        "fecha": fecha_str_metal,
                        "fecha_despacho": fecha_mod_metal,
                        "cliente": r.cliente,
                        "vendedor": r.vendedor,
                        "estado": r.estado or "REGISTRADO",
                        "progreso": r.progreso or 0,
                        "items_count": 0,
                        "total": 0,
                        "productos": []
                    }
                
                ped_map[id_p]["items_count"] += 1
                ped_map[id_p]["total"] += (r.total or 0)
                ped_map[id_p]["productos"].append({
                    "id_codigo": r.id_codigo,
                    "descripcion": r.descripcion,
                    "cantidad": r.cantidad or 0,
                    "precio": r.precio_unitario or 0,
                    "total": r.total or 0
                })
            
            # Devolver lista de pedidos agrupados
            return api_success(data={
                "pedidos": sorted(list(ped_map.values()), key=lambda x: x['id_pedido'], reverse=True)
            })

        else:
            # Lógica estándar FriParts (db_pedidos)
            if search:
                query = Pedido.query.filter(
                    (Pedido.id_pedido.ilike(f'%{search}%')) |
                    (Pedido.cliente.ilike(f'%{search}%'))
                )
                rows = query.order_by(Pedido.id_pedido.desc()).limit(500).all()
            else:
                # 1. Obtener los últimos 100 id_pedido únicos
                ids_result = db.session.query(Pedido.id_pedido).group_by(Pedido.id_pedido).order_by(Pedido.id_pedido.desc()).limit(100).all()
                lista_de_ids = [r[0] for r in ids_result if r[0]]
                
                # 2. Consultar todas las filas (ítems) para esos IDs
                if lista_de_ids:
                    rows = Pedido.query.filter(Pedido.id_pedido.in_(lista_de_ids)).order_by(Pedido.id_pedido.desc()).all()
                else:
                    rows = []
            
            # Obtener todos los id_pedido únicos
            id_pedidos = list(set([r.id_pedido for r in rows if r.id_pedido]))
            
            # Consultar todos los despachos para estos id_pedido
            despachos = []
            if id_pedidos:
                despachos = DespachoPedido.query.filter(DespachoPedido.id_pedido.in_(id_pedidos)).all()
            
            # Mapear el último despacho por id_pedido
            despachos_map = {}
            for d in despachos:
                id_p = d.id_pedido
                if id_p not in despachos_map or d.fecha > despachos_map[id_p].fecha:
                    despachos_map[id_p] = d

            ped_map = {}
            for r in rows:
                id_p = r.id_pedido
                if not id_p:
                    continue
                if id_p not in ped_map:
                    fecha_str = r.fecha.strftime('%Y-%m-%d') if r.fecha else ""
                    hora_str = str(r.hora or "").strip()
                    # Mapear fecha_creacion única e independiente para evitar fallback a Date() en JS
                    fecha_creacion_final = f"{fecha_str} {hora_str}".strip() if fecha_str else ""
                    
                    d_obj = despachos_map.get(id_p)
                    # Formato seguro %Y-%m-%d %H:%M:%S
                    fecha_despacho_final = d_obj.fecha.strftime('%Y-%m-%d %H:%M:%S') if d_obj and d_obj.fecha else ""
                    
                    # Fallback si no hay registro de despacho (usa la fecha/hora de creación)
                    if not fecha_despacho_final:
                        fecha_despacho_final = fecha_creacion_final

                    ped_map[id_p] = {
                        "id_pedido": id_p,
                        "fecha_creacion": fecha_creacion_final or fecha_str,
                        "fecha": fecha_str,
                        "fecha_despacho": fecha_despacho_final,
                        "cliente": r.cliente,
                        "vendedor": r.vendedor,
                        "estado": r.estado,
                        "total": 0,
                        "items_count": 0,
                        "productos": []
                    }
                ped_map[id_p]["items_count"] += 1
                ped_map[id_p]["total"] += float(r.total or 0)
                ped_map[id_p]["productos"].append({
                    "id_codigo": r.id_codigo,
                    "descripcion": r.descripcion,
                    "cantidad": float(r.cantidad or 0),
                    "precio_unitario": float(r.precio_unitario or 0),
                    "total": float(r.total or 0)
                })
            
            return api_success(data={
                "pedidos": sorted(list(ped_map.values()), key=lambda x: x['id_pedido'], reverse=True)
            })

    except Exception as e:
        logger.error(f"❌ Error crítico en listar_pedidos: {e}")
        return api_error("Error interno del servidor", status_code=500, detail=str(e))

@pedidos_bp.route('/api/pedidos/actualizar-progreso', methods=['POST'])
@require_role(ROLES_PEDIDOS_INTERNOS)
def actualizar_progreso_pedido():
    """Actualiza el porcentaje de progreso de un pedido de Metales."""
    try:
        data = request.json
        id_pedido = data.get('id_pedido')
        nuevo_progreso = data.get('progreso')
        nuevo_estado = data.get('estado')

        if not id_pedido:
            return api_error("ID de pedido faltante", status_code=400)

        # Actualizar todas las filas que compartan el mismo id_pedido
        pedidos = MetalsPedido.query.filter_by(id_pedido=id_pedido).all()
        for p in pedidos:
            if nuevo_progreso is not None:
                p.progreso = int(nuevo_progreso)
            if nuevo_estado:
                p.estado = nuevo_estado
        
        db.session.commit()
        return api_success(message=f"Pedido {id_pedido} actualizado correctamente")

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error actualizando progreso: {e}")
        return api_error(str(e), status_code=500)

@pedidos_bp.route('/api/pedidos/despacho', methods=['POST'])
@require_role(ROLES_PEDIDOS_INTERNOS)
def registrar_despacho():
    """Registra un envío/despacho parcial o total de un pedido y descuenta stock."""
    from backend.models.sql_models import db, Pedido, DespachoPedido
    from backend.services.stock_service import StockService
    try:
        data = request.json
        if not data:
            return api_error("No se recibieron datos", status_code=400)

        id_pedido = data.get('id_pedido')
        # Guard de ownership centralizado con AuditService e identificación desacoplada HTTP
        usuario_activo = _obtener_usuario_activo()
        candidato_responsable = data.get('responsable') or usuario_activo

        if not candidato_responsable or str(candidato_responsable).strip().upper() in ['', 'SISTEMA']:
            return api_error(
                "Se requiere la identidad del usuario o responsable para registrar el despacho",
                status_code=400, code="RESPONSABLE_REQUERIDO"
            )

        try:
            responsable = AuditService.resolver_y_validar_propietario(None, candidato_responsable)
        except OwnershipMismatchException as e:
            return api_error(
                e.message, status_code=409, code="PEDIDOS_SESSION_OWNERSHIP_MISMATCH",
                responsable_db=e.responsable_db, responsable_in=e.responsable_in
            )
        transportadora = data.get('transportadora')
        guia = data.get('guia')
        items = data.get('items', [])

        if not id_pedido or not items:
            return api_error("ID de pedido e items son requeridos", status_code=400)

        despachos_creados = 0
        fecha_actual = get_colombia_time()

        for item in items:
            codigo = item.get('id_codigo')
            cant = item.get('cantidad_enviada')

            if not codigo:
                continue

            try:
                cant_enviada = int(cant)
            except (ValueError, TypeError):
                continue

            if cant_enviada > 0:
                # 1. Registrar despacho en base de datos
                nuevo_despacho = DespachoPedido(
                    id_pedido=id_pedido,
                    id_codigo=codigo,
                    cantidad_enviada=cant_enviada,
                    fecha=fecha_actual,
                    transportadora=transportadora,
                    guia=guia,
                    responsable=responsable
                )
                db.session.add(nuevo_despacho)

                # 2. Descontar stock de p_terminado (único punto de entrada/salida)
                res_salida = StockService.registrar_salida(codigo, cant_enviada, 'P. TERMINADO')
                if isinstance(res_salida, dict) and "error" in res_salida:
                    raise Exception(f"No se pudo actualizar stock de p_terminado para {codigo}: {res_salida['error']}")

                despachos_creados += 1

        if despachos_creados == 0:
            return api_error("No hay cantidades válidas para despachar", status_code=400)

        # Opcional: Actualizar el progreso/estado del Pedido
        pedidos_filas = Pedido.query.filter_by(id_pedido=id_pedido).all()
        if pedidos_filas:
            # Calcular despachos totales históricos para este pedido (incluidos los recién agregados)
            from sqlalchemy import func
            despachos_totales = db.session.query(
                DespachoPedido.id_codigo,
                func.sum(DespachoPedido.cantidad_enviada).label('total_enviado')
            ).filter(DespachoPedido.id_pedido == id_pedido).group_by(DespachoPedido.id_codigo).all()
            
            despachos_map = {d.id_codigo: float(d.total_enviado or 0) for d in despachos_totales}
            
            todos_despachados = True
            for fila in pedidos_filas:
                cant_pedida = float(fila.cantidad or 0)
                cant_enviada_total = despachos_map.get(fila.id_codigo, 0.0)
                
                # Si algún ítem no ha sido enviado completamente, entonces no está 100% despachado
                if cant_enviada_total < cant_pedida:
                    todos_despachados = False
            
            nuevo_estado = 'DESPACHADO' if todos_despachados else 'DESPACHADO PARCIAL'
            for fila in pedidos_filas:
                fila.estado = nuevo_estado
        
        db.session.commit()
        logger.info(f"🚚 [DESPACHO] Se registraron {despachos_creados} items despachados para el pedido {id_pedido}")
        
        # Invalidar caché de pedidos para refrescar vistas comerciales/bodega
        try:
            from backend.app import invalidar_cache_pedidos
            invalidar_cache_pedidos()
        except:
            pass

        return api_success(
            data={"id_pedido": id_pedido},
            message=f"Se registraron {despachos_creados} despachos exitosamente",
            status_code=201
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error registrando despacho: {e}")
        return api_error(str(e), status_code=500)


@pedidos_bp.route('/api/pedidos/<id_pedido>/despachos', methods=['GET'])
@require_role(ROLES_PEDIDOS_INTERNOS)
def obtener_despachos_pedido(id_pedido):
    """Consulta el historial de despachos de un pedido específico."""
    from backend.models.sql_models import DespachoPedido
    try:
        despachos = DespachoPedido.query.filter_by(id_pedido=id_pedido).order_by(DespachoPedido.fecha.desc()).all()
        
        resultado = []
        for d in despachos:
            resultado.append({
                "id_despacho": d.id_despacho,
                "id_pedido": d.id_pedido,
                "id_codigo": d.id_codigo,
                "cantidad_enviada": d.cantidad_enviada,
                "fecha": d.fecha.strftime('%Y-%m-%d %H:%M') if d.fecha else '',
                "transportadora": d.transportadora or '',
                "guia": d.guia or '',
                "responsable": d.responsable or ''
            })

        return api_success(data={"despachos": resultado})

    except Exception as e:
        logger.error(f"❌ Error obteniendo despachos del pedido {id_pedido}: {e}")
        return api_error(str(e), status_code=500)
