# MARTES

MARTES es un asistente personal local con interfaz web, memoria SQLite, conversaciones aisladas por usuario, proveedor Groq OpenAI-compatible, investigacion web y voz del navegador opcional.

El producto se presenta como **MARTES — Personal AI Assistant**. La carpeta y algunos modulos internos conservan nombres historicos para evitar migraciones innecesarias.

## Estado implementado

- Login obligatorio en la app real.
- Propietario inicial: `kevynnquintero32@gmail.com`, creado solo mediante variables locales de bootstrap.
- Hash de contrasenas con Argon2id (`argon2-cffi`), sin contrasenas en codigo, README ni frontend.
- Sesiones con cookie `HttpOnly`, `SameSite=Lax`, expiracion, logout y token CSRF para escrituras.
- Creacion de usuarios desde un formulario seguro, solo para el propietario.
- Conversaciones SQLite con `user_id`; los usuarios normales solo ven sus propios chats.
- Memoria SQLite controlable por usuario, con busqueda, edicion, borrado, exportacion desde la UI y filtro contra credenciales.
- Identidad central de MARTES en `app/core/identity.py`, aplicada al proveedor.
- Registro real de capacidades en `app/tools/registry.py`, usado para explicar que puede hacer sin inventar accesos.
- Capa `IntentResolver` en `app/core/intent.py`: interpreta solicitudes naturales, errores comunes, referencias recientes, riesgos y herramientas candidatas antes de ejecutar.
- Diagnostico seguro de comprension por `/api/intent/diagnose` y `/api/intent/diagnostics`, sin exponer secretos ni razonamiento interno privado.
- `MartesCore` coordina solicitudes operativas iniciales, planes persistentes, proyectos, tareas y actividad verificable.
- Planes de ejecucion SQLite con estados `PENDING`, `PLANNING`, `WAITING_APPROVAL`, `RUNNING`, `VERIFYING`, `COMPLETED`, `FAILED` y `CANCELLED`.
- Gestor basico de proyectos y tareas: MARTES puede crear un proyecto desde lenguaje natural, guardar tareas iniciales y recuperarlas desde `/api/activity`.
- Gestión de tareas conectada al agente: MARTES consulta, busca, crea y actualiza tareas persistentes de SQLite mediante herramientas verificables, sin usar archivos TXT como sustituto.
- Centro de actividad en la interfaz para ver eventos, planes y proyectos guardados por el backend.
- Planificacion dinamica basada en herramientas registradas para solicitudes nuevas de documentos, busqueda de archivos y recordatorios.
- Centro documental local para TXT, Markdown, JSON y CSV dentro del workspace autorizado.
- Recordatorios persistentes SQLite con propietario, fecha, zona horaria, estado y consulta desde el Centro de actividad.
- Bienvenida local al iniciar sesion, con perfil de usuario, ajustes proactivos y ciudad meteorologica opcional.
- Clima actual mediante Open-Meteo con geocodificacion, cache local y atribucion.
- Agente de archivos seguro dentro de `MARTES_WORKSPACE_ROOT`, con creacion/listado/lectura/escritura controlada.
- Groq con `/models`, `/chat/completions` y streaming real cuando el proveedor entrega tokens.
- Estados visuales reales en la UI: `idle`, `greeting`, `thinking`, `planning`, `executing`, `researching`, `responding`, `speaking`, `offline` y `error`.
- Motor visual ligero en frontend que cambia la animacion segun eventos reales: crear carpeta, escribir archivo, analizar carpeta, planificar y crear proyectos.
- Tavily configurable como proveedor de investigacion; si no hay clave, se conserva la busqueda existente.
- Voz del navegador mediante `speechSynthesis`, activable desde la UI.

## Configuracion inicial segura

1. Copia el archivo de entorno:

```powershell
copy .env.example .env
```

2. Edita `.env` y define una contrasena nueva y unica:

```env
MARTES_BOOTSTRAP_EMAIL=kevynnquintero32@gmail.com
MARTES_BOOTSTRAP_PASSWORD=usa-una-contrasena-nueva-y-unica
MARTES_AUTH_REQUIRED=true
```

3. Inicia la app. Si el propietario ya existe, MARTES no sobrescribe su cuenta en reinicios.

4. Despues del primer acceso, conserva la clave fuera del repositorio. Si una contrasena fue compartida durante el diseno, no la reutilices.

## Groq

```env
PROJECT_X_PROVIDER_MODE=groq
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b
GROQ_BASE_URL=https://api.groq.com/openai/v1
PROJECT_X_REMOTE_MEMORY_ENABLED=false
MARTES_NATIVE_TOOL_CALLING_ENABLED=true
MARTES_NATIVE_TOOL_CALLING_MAX_ITERATIONS=4
```

La clave tambien puede guardarse desde Configuracion. No se expone en `/api/status`, historial ni interfaz. Si la memoria remota esta desactivada, Groq recibe solo el mensaje y el contexto reciente de la conversacion activa.

MARTES 4.1 usa llamadas nativas a herramientas cuando el proveedor activo las soporta.
Con Groq se envian definiciones `tools` compatibles con `/chat/completions`; el modelo puede devolver `tool_calls`, el backend valida el nombre y los argumentos, ejecuta la herramienta real en `ToolRegistry`, agrega el resultado como mensaje `tool` y repite el ciclo hasta obtener una respuesta final o alcanzar el limite configurado.

## Tavily

Tavily queda desactivado por defecto:

```env
TAVILY_API_KEY=
MARTES_TAVILY_ENABLED=false
```

Al activarlo, MARTES usa `https://api.tavily.com/search` con la clave local y muestra fuentes originales. No se describe como ilimitado ni permanentemente gratuito; revisa creditos y limites actuales en la consola de Tavily.

## Perfil, clima y workspace

```env
MARTES_WORKSPACE_ROOT=./workspace
MARTES_WEATHER_CACHE_MINUTES=45
```

Cada usuario tiene perfil, nombre visible, ciudad meteorologica, ajustes de bienvenida/proactividad/voz/tono y estado de bienvenida inicial. MARTES no infiere ubicacion silenciosamente; la ciudad se configura en la UI.

El clima usa Open-Meteo:

- Geocoding: `https://geocoding-api.open-meteo.com/v1/search`
- Forecast: `https://api.open-meteo.com/v1/forecast`
- Atribucion mostrada: Open-Meteo.com

El agente de archivos solo opera dentro de `MARTES_WORKSPACE_ROOT`. Bloquea rutas absolutas, `..` y escapes fuera de la carpeta autorizada. Las acciones destructivas no se ejecutan automaticamente en esta version.

## Planes, proyectos y actividad

MARTES guarda trabajo operativo en SQLite, separado del historial conversacional:

- `execution_plans`: objetivo, estado, riesgo, proxima accion y marcas de tiempo.
- `execution_plan_steps`: pasos ordenados, herramienta prevista, argumentos, dependencias, estado, resultado y error.
- `projects`: proyectos personales por usuario.
- `tasks`: tareas vinculadas a proyectos.
- `activity_events`: eventos verificables del backend para la interfaz.

`PlanExecutor` ejecuta planes persistidos con herramientas reales del registro central. En cada paso:

1. Verifica que el plan pertenezca al usuario autenticado.
2. Busca el siguiente paso pendiente cuyas dependencias esten completadas.
3. Valida que la herramienta exista y este disponible.
4. Ejecuta la herramienta mediante `ToolRegistry`.
5. Verifica el resultado estructurado.
6. Guarda resultado, error y eventos de actividad.

Si un paso falla, los pasos ya completados permanecen guardados y una reanudacion no los duplica. Las herramientas de riesgo `CONSEQUENCE` o superior requieren autorizacion concreta.

Ejemplos soportados:

```text
MARTES, prepara un proyecto para aprender Python durante dos meses y organiza mi primera semana.
MARTES, investiga los mejores recursos gratuitos para aprender Python y guarda un informe con las fuentes.
MARTES, crea una estructura inicial para una API en Python con pruebas y documentación.
planifica organizar mis documentos con permisos seguros.
muéstrame los trabajos recientes.
```

Flujos multipaso actuales:

- Proyecto de aprendizaje: crea proyecto y tareas en SQLite, crea carpeta dentro de `MARTES_WORKSPACE_ROOT`, escribe `README.md`, escribe `semana-1.txt`, lista la carpeta y registra eventos.
- Investigacion con informe: intenta `research.web`, crea carpeta `informes`, guarda un Markdown con fuentes recuperadas y verifica el archivo. Si la investigacion falla o no esta configurada, no inventa fuentes.
- Estructura de software: usa `files.scaffold_project`, crea una nota de pruebas y verifica la estructura. No afirma que las pruebas pasaron porque aun no hay ejecutor de comandos autorizado.

Correcciones 4.1.2:

- Las consultas de tareas por nombre de proyecto, por ejemplo `qué tareas tiene mi proyecto Aprendizaje Web`, consultan SQLite directamente y funcionan aunque se hagan desde un chat nuevo.
- Las investigaciones solo se verifican cuando `research.web` devuelve fuentes estructuradas. Un resumen sin URLs no se convierte en informe falso.
- Las peticiones compuestas de investigacion conservan el objetivo real antes de separar instrucciones como comparar, guardar informe o crear tareas.
- El streaming de `/api/chat/stream` emite siempre un evento terminal `done` o un error visible con `request_id`; el frontend corta solicitudes largas, limpia el estado `thinking` y muestra el fallo en el chat.

Endpoints utiles:

- `GET /api/activity`: actividad, planes, proyectos y tareas del usuario autenticado.
- `GET /api/plans`: planes persistentes del usuario.
- `POST /api/plans/{plan_id}/resume`: reanuda pasos pendientes sin repetir completados.
- `POST /api/plans/{plan_id}/cancel`: cancela un plan y sus pasos pendientes.
- `GET /api/projects`: proyectos persistentes del usuario.

Las acciones de mayor riesgo siguen pasando por el registro de herramientas y los niveles de permiso. Un plan guardado no autoriza por si solo una accion futura.

## Gestión de tareas 4.1.3

Las tareas y proyectos usan las tablas SQLite existentes `projects` y `tasks`. No hay una segunda lista basada en archivos. Las herramientas disponibles son:

- `projects.list`: lista proyectos del usuario autenticado.
- `tasks.list`: lista tareas de un proyecto identificado de forma inequívoca.
- `tasks.find`: busca por título normalizado dentro de un proyecto, sin seleccionar una coincidencia ambigua.
- `tasks.get`: vuelve a consultar una tarea por identificador.
- `tasks.create`: crea una tarea en un proyecto propio.
- `tasks.update_status` y `tasks.update`: actualizan campos persistentes y vuelven a leer SQLite para verificar el resultado.

Por ejemplo, esta solicitud actualiza la base de datos y registra los eventos `task_updated` y `task_verified` en el Centro de actividad:

```text
MARTES, marca como completada la tarea “Revisar los tres recursos gratuitos seleccionados” de mi proyecto Estudio Python - Semana 1. Comprueba que el cambio quedó guardado.
```

La búsqueda normaliza mayúsculas, acentos, espacios y puntuación, pero exige una coincidencia exacta tras normalizar. Si un nombre de proyecto o tarea resulta ambiguo, MARTES pide aclaración y no modifica ninguna tarea. Cuando un plan ejecuta una herramienta de tareas, los eventos quedan asociados al plan y conversación correspondientes.

## Comprension natural 4.0

MARTES separa comprension de ejecucion:

1. Conserva el mensaje original.
2. Recupera historial reciente y memoria pertinente limitada.
3. Produce una intencion validada con Pydantic: tipo, objetivo, entidades, herramientas candidatas, datos faltantes, riesgo y resultado esperado.
4. Decide si responder, ejecutar una herramienta, crear/reanudar un plan o pedir una aclaracion.
5. Ejecuta solo herramientas registradas en `ToolRegistry` y verifica resultados antes de confirmar.

El resolver local cubre operaciones frecuentes y de bajo coste sin llamar a Groq, por ejemplo:

```text
Hazme un directorio para el proyecto web.
Prepárame un espacio donde guardar el material de la web.
Échale un vistazo a esa carpeta.
Crea una carpeta en el escritorio llamada prueba final y dentro un archivo txt que diga Hola Joel.
```

El registro de herramientas incluye metadatos semanticos: proposito, ejemplos naturales, parametros requeridos/opcionales, errores posibles, riesgo y verificacion. El contexto enviado al modelo se genera desde ese registro real; si una capacidad no esta configurada, no se presenta como operativa.

Endpoints de diagnostico:

- `POST /api/intent/diagnose`: devuelve la interpretacion estructurada de un mensaje.
- `GET /api/intent/diagnostics`: ultimas interpretaciones registradas para el usuario.

Estos endpoints estan pensados para depurar fallos de comprension. No sustituyen las verificaciones del backend ni autorizan acciones por si solos.

## Tool calling híbrido 4.1

La ruta recomendada de decision es:

1. `IntentResolver` identifica intención, entidades, riesgo y herramientas candidatas.
2. Acciones simples y deterministas siguen usando rutas locales rápidas, por ejemplo crear carpetas o archivos.
3. `MartesCore` y `PlanExecutor` conservan la coordinación de proyectos y planes persistentes.
4. Si queda una solicitud operativa que necesita selección del modelo y el proveedor soporta herramientas, `ToolCallingEngine` ejecuta el ciclo nativo.
5. Toda llamada del modelo vuelve a pasar por `ToolRegistry.run`, permisos del backend y verificación básica del resultado.

Las herramientas nativas se generan desde el registro real con nombres seguros para la API, por ejemplo `weather.current` se expone al modelo como `weather_current` y se mapea de vuelta en el backend. El modelo nunca puede inventar una herramienta ni autorizar una acción sensible por sí mismo.

La integración se puede desactivar temporalmente con:

```env
MARTES_NATIVE_TOOL_CALLING_ENABLED=false
```

Límites actuales:

- El streaming de texto sigue usando la ruta textual cuando no hay tool calling; las herramientas nativas se ejecutan como ciclo no streaming y luego se emiten eventos visuales.
- Las pruebas normales usan proveedores simulados para no consumir cuota de Groq.
- No se ejecutan acciones destructivas; las herramientas `CONSEQUENCE` o superiores siguen pidiendo autorización concreta.

## Documentos locales

Herramientas registradas:

- `documents.read_text`: lee TXT y Markdown dentro del workspace.
- `documents.read_json`: valida y lee JSON.
- `documents.read_csv`: lee una muestra de CSV.
- `documents.summarize`: genera un resumen local basico sin enviar el archivo a Groq.
- `documents.create_markdown`: crea informes Markdown sin sobrescribir por defecto.

Formatos pendientes: PDF, DOCX y XLSX. Requieren dependencias especificas, limites de tamano y pruebas visuales/estructurales antes de activarse. Si un PDF requiere OCR, MARTES debe indicarlo en vez de fingir lectura.

## Recordatorios

Herramientas registradas:

- `reminders.create`
- `reminders.list`
- `reminders.upcoming`
- `reminders.cancel`
- `reminders.done`

Ejemplo:

```text
MARTES, recuérdame mañana a las ocho terminar el informe.
```

MARTES interpreta la fecha en `America/Bogota` y, si el runtime de Windows no trae base `tzdata`, usa UTC-05:00 manteniendo esa etiqueta. Los recordatorios se guardan en SQLite y aparecen en `/api/activity`. En esta fase las notificaciones son dentro de la aplicacion: MARTES no puede garantizar un aviso externo si la app no esta abierta.

## Voz

La salida de voz usa primero `speechSynthesis` del navegador. Puede activarse o detenerse desde la UI. La transcripcion remota con Groq Whisper queda documentada para una fase posterior; no se activa el microfono permanentemente ni se envia audio sin permiso explicito.

Groq STT soporta endpoints OpenAI-compatible para transcripcion y traduccion, incluidos modelos Whisper como `whisper-large-v3` y `whisper-large-v3-turbo`; el audio se enviaria a Groq si esa funcion se habilita.

## Ejecutar

```powershell
cd C:\Users\it03\Desktop\automatizacion\proyecto-x
uv run --python 3.12 uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Abre `http://127.0.0.1:8765`.

## Recuperacion de acceso

Si pierdes la contrasena del propietario:

1. Deten la app.
2. Haz copia de `data/proyecto_x.db`.
3. Usa una herramienta SQLite local para desactivar o recrear el usuario propietario con un procedimiento controlado, o crea una utilidad administrativa temporal fuera del repositorio.
4. No pongas contrasenas nuevas en el codigo ni en el historial del chat.

La app no sobrescribe automaticamente cuentas existentes para evitar borrar accesos de una instalacion real.

## Privacidad

- SQLite guarda usuarios, sesiones, conversaciones y memoria en `data/proyecto_x.db`.
- Groq recibe mensajes solo cuando esta configurado como proveedor activo.
- Tavily recibe consultas solo si se habilita y hay clave.
- La busqueda DuckDuckGo descarga paginas HTTPS publicas y mantiene proteccion SSRF.
- Las contrasenas, tokens, API keys y codigos se filtran antes de guardarse como memoria; si aparecen en el chat, se redactan antes de persistir o enviar.

## Pruebas

```powershell
uv run --python 3.12 --extra dev pytest
uv run --python 3.12 --extra dev ruff check .
node --check app\ui\app.js
```

Las pruebas usan simulaciones y no consumen cuotas reales de Groq ni Tavily.

La suite incluye regresiones de comprension natural para sinonimos, typos, solicitudes combinadas, acciones sensibles y ejecucion local de archivos sin depender del proveedor remoto.
Tambien incluye pruebas simuladas del ciclo nativo de herramientas y del payload OpenAI-compatible enviado a Groq.

## Arquitectura

- `app/auth.py`: usuarios, Argon2id, sesiones, CSRF y bootstrap.
- `app/main.py`: API FastAPI, autenticacion, conversaciones, memoria, streaming y configuracion.
- `app/core/identity.py`: identidad central de MARTES.
- `app/core/intent.py`: modelos Pydantic y resolver de intenciones.
- `app/core/tool_calling.py`: ciclo nativo de tool calling con ejecucion local segura.
- `app/core/martes_core.py`: coordinador operativo inicial de planes, proyectos y actividad.
- `app/core/orchestrator.py`: prompt, memoria, tono e investigacion.
- `app/ai`: proveedores Groq, Ollama, llama.cpp y remoto autorizado.
- `app/memory`: memoria y conversaciones SQLite con migraciones seguras.
- `app/planning`: modelos, persistencia y ejecutor de planes multipaso.
- `app/reminders`: persistencia de recordatorios.
- `app/research`: DuckDuckGo/Tavily y proteccion SSRF.
- `app/tools`: registro de capacidades y agente de archivos seguro.
- `app/users.py`: perfil y ajustes por usuario.
- `app/weather.py`: integracion Open-Meteo con cache.
- `app/ui`: HTML, CSS y JavaScript de la interfaz.

## Limitaciones actuales

- La reanudacion/cancelacion de planes ya existe por API; falta una UX mas rica para autorizaciones por paso y reintentos selectivos desde la interfaz.
- La lectura avanzada de PDF, DOCX y XLSX no se activa hasta registrar herramientas documentales con limites de tamano y pruebas.
- Las notificaciones de recordatorios son internas; no hay notificaciones del sistema operativo todavia.
- La voz de entrada no captura microfono todavia; solo hay salida por `speechSynthesis`.
- Las automatizaciones periodicas y recordatorios programados requieren una etapa adicional de scheduler persistente.
