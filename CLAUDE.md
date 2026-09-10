# FRITECH — Instrucciones del proyecto

## Arquitectura de despliegue: varias empresas, una instancia por cliente

Este código corre como una instancia independiente por cliente (su propio
contenedor + su propia base de datos), no como un sistema multi-tenant
compartido. Las diferencias entre clientes viven en variables de entorno
(ver `backend/config/settings.py::Empresa`), no en ramas de código ni en
copias del repo.

Instancias activas hoy:
- **FRIPARTS** (`friparts.fribytes.com`) — instancia de referencia.
- **FRIMETALS** (`frimetals.fribytes.com`) — primer cliente piloto, separado.

## Flujo de despliegue: canary deployment

- **Solo FRIPARTS tiene auto-deploy activo** en Coolify — se despliega sola
  con cada push a `main`, porque es la que se usa a diario y cualquier
  regresión se nota de inmediato ahí.
- **Todas las demás instancias (FRIMETALS y cualquier cliente nuevo) tienen
  el auto-deploy desactivado a propósito.** Se actualizan con un Deploy
  manual en Coolify, solo después de confirmar que el cambio no rompió
  FRIPARTS.
- Un push a `main` **no** significa que todos los clientes ya están
  actualizados — hay que desplegarlos a mano, uno por uno, cuando se confía
  en el cambio.

## Qué señalar al terminar un cambio de código

Antes de dar un cambio por cerrado, indicar explícitamente:
- Si el comportamiento cambiado depende de configuración por cliente
  (`Empresa`), qué instancias necesitan una variable de entorno nueva o
  distinta para no romperse con el cambio.
- Qué instancias, aparte de FRIPARTS, van a necesitar un Deploy manual en
  Coolify una vez el cambio quede verificado ahí.

No asumir que "ya se hizo el push" equivale a "ya está desplegado en todos
los clientes" — son dos pasos distintos en este proyecto.

## Reglas de arquitectura de código (innegociables)

1. **Separación estricta de capas.** Prohibido escribir lógica de negocio,
   cálculos o consultas SQL en los archivos de rutas (`*_routes.py`). Un
   controlador solo recibe el request, delega a un servicio (`*_service.py`
   o un repositorio) y traduce el resultado a JSON. Si una tarea implica
   meter lógica o SQL en una ruta, hay que negarse y corregir el enfoque, no
   hacerlo igual porque se pidió así.
2. **Código defensivo.** Toda ejecución contra la base de datos (en el
   servicio, que es donde debe vivir) va envuelta en `try/except` con
   `db.session.rollback()` en el except — nunca dejar una sesión de
   PostgreSQL colgada a medio commit.
3. **Cero suposiciones sobre el código.** Antes de asumir cómo se comporta
   algo, leerlo directamente del repo (con las herramientas de archivo/
   búsqueda disponibles) en vez de adivinar nombres de variables, funciones
   o rutas. Si hace falta información que no está en el repo ni se ha dado
   en la conversación, preguntar antes de inventar.
4. **Datos sintéticos para pruebas de estrés.** Cuando un cambio toque
   lógica de escritura crítica (inventario, cierres, facturación, lo que
   afecte dinero o trazabilidad), preparar y correr un caso de prueba con
   datos borde (caracteres especiales, nulos, duplicados) antes de darlo
   por listo para producción — no basta con que compile o con un caso feliz
   único.

## Postura esperada: nada de complacencia

No asumir que "el usuario lo pidió así" es suficiente para proceder sin
más. En cada tarea, señalar activamente riesgos, efectos secundarios o
supuestos débiles que se noten — aunque no se haya preguntado por ellos
explícitamente — en vez de simplemente ejecutar y quedar bien. Si algo
huele a riesgo para producción, para clientes reales, o para datos, decirlo
primero y proponer la alternativa más segura, no callarlo por seguir el
hilo. Verificar contra datos/comportamiento real (no solo que el código
compile) siempre que sea posible antes de dar un cambio por terminado.
