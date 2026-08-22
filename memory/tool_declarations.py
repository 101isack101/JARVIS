"""memory/tool_declarations.py - Contrato de tools expuesto a Gemini Live.

Solo datos: una FunctionDeclaration por tool + los agregadores
all_function_declarations()/make_tool_object(). La logica de ejecucion vive
en memory/tools.py; separar el contrato del cuerpo permite revisar el schema
sin scrollear entre las implementaciones.
"""

from __future__ import annotations

from google.genai import types

from .tool_sets import tools_for_mode


# =====================================================================
# DECLARACIONES PARA GEMINI (function_declarations)
# =====================================================================

JARVIS_RECALL_DECL = types.FunctionDeclaration(
    name="jarvis_recall",
    description=(
        "Busca semanticamente en TODAS las notas de Isaac (vault Obsidian) "
        "cuando necesitas contexto previo, recordar decisiones pasadas, configs, "
        "o cualquier hecho que podrias haber guardado en sesiones anteriores. "
        "USALA AGRESIVAMENTE cuando el usuario menciona nombres de proyectos, "
        "decisiones, modelos, o cualquier 'lo que hicimos antes con X'. "
        "Devuelve los top_k fragmentos mas relevantes con titulo y texto."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Pregunta o tema a buscar en lenguaje natural.",
            ),
            "top_k": types.Schema(
                type=types.Type.INTEGER,
                description="Cantidad de resultados a traer. Default 3, max 5.",
            ),
        },
        required=["query"],
    ),
)

JARVIS_SESSION_RECALL_DECL = types.FunctionDeclaration(
    name="jarvis_session_recall",
    description=(
        "Recupera memorias de sesiones recientes ya sintetizadas. USALA cuando "
        "Isaac diga 'ayer', 'anoche', 'la sesion pasada', 'la vez anterior', "
        "'la conversacion que tuvimos', 'que hablamos' o cualquier referencia "
        "temporal a conversaciones previas. Es mas directa que jarvis_recall "
        "para continuidad entre sesiones y no llama a Grok."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Tema o frase a buscar dentro de los resumenes de sesiones.",
            ),
            "when": types.Schema(
                type=types.Type.STRING,
                description="Referencia temporal: ayer, anoche, hoy, anteayer, YYYY-MM-DD o DD/MM/YYYY.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Cantidad maxima de sesiones a devolver. Default 5, max 10.",
            ),
        },
    ),
)

JARVIS_CURRENT_SESSION_RECALL_DECL = types.FunctionDeclaration(
    name="jarvis_current_session_recall",
    description=(
        "Recupera turnos de ESTA sesion viva todavia no sintetizada. USALA antes "
        "de responder cuando Isaac diga 'lo que veniamos hablando', 'lo que te "
        "dije', 'hace rato', 'ahorita', 'lo anterior', 'sigamos', o cuando "
        "parezca que perdiste el hilo tras una reconexion/compresion. Prioriza "
        "esta tool sobre jarvis_session_recall si la referencia es al contexto "
        "actual y no a ayer/otra sesion."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="Tema o frase a buscar dentro de la conversacion actual.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Cantidad maxima de turnos vivos a devolver. Default 10, max 20.",
            ),
        },
    ),
)

JARVIS_REMEMBER_DECL = types.FunctionDeclaration(
    name="jarvis_remember",
    description=(
        "Crea o actualiza una nota Markdown en el vault de Isaac (subcarpeta "
        "'Jarvis Memory/'). USALA cuando la conversacion produzca informacion "
        "durable: decisiones tomadas, hechos a recordar, preferencias del usuario, "
        "configuraciones, links importantes. NO la uses para chitchat o info "
        "trivial. El title debe ser descriptivo. Las tags ayudan a recuperar."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(
                type=types.Type.STRING,
                description="Titulo descriptivo de la nota. Sera el nombre del archivo.",
            ),
            "content": types.Schema(
                type=types.Type.STRING,
                description="Cuerpo de la nota en markdown. Puede incluir secciones, listas, [[wikilinks]].",
            ),
            "tags": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="Tags para categorizar (ej. ['decision', 'agentics']).",
            ),
        },
        required=["title", "content"],
    ),
)

JARVIS_BROWSE_DECL = types.FunctionDeclaration(
    name="jarvis_browse",
    description=(
        "Lista notas existentes en una carpeta del vault. Util cuando el usuario "
        "pregunta 'que tengo sobre X' o necesitas inspeccionar que notas existen "
        "antes de decidir si crear una nueva o actualizar una existente."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "folder": types.Schema(
                type=types.Type.STRING,
                description="Subcarpeta del vault (ej. 'Jarvis Memory', 'Proyectos'). "
                            "Vacio = todo el vault.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximo de notas a listar. Default 20.",
            ),
        },
    ),
)

JARVIS_LINK_DECL = types.FunctionDeclaration(
    name="jarvis_link",
    description=(
        "Agrega un wikilink [[note_to]] al frontmatter 'related' de la nota note_from. "
        "Usalo cuando descubras que dos notas estan relacionadas conceptualmente "
        "(ej. una decision afecta a un proyecto)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "note_from": types.Schema(
                type=types.Type.STRING,
                description="Path relativo o titulo de la nota origen (en Jarvis Memory/).",
            ),
            "note_to": types.Schema(
                type=types.Type.STRING,
                description="Titulo de la nota destino (sin .md ni rutas).",
            ),
        },
        required=["note_from", "note_to"],
    ),
)

ASK_GROK_DEEP_DECL = types.FunctionDeclaration(
    name="ask_grok_deep",
    description=(
        "Delegar a Grok 4.6 cuando la tarea necesita razonamiento profundo general, "
        "analisis largo o planning no centrado en codigo. Para codigo, debugging "
        "de software, arquitectura tecnica o modo agentico usa primero "
        "ask_terra_code. NO usar para preguntas simples o charla; Jarvis debe "
        "responder directo ahi."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "prompt": types.Schema(
                type=types.Type.STRING,
                description="Pregunta/tarea concreta para Grok 4.6.",
            ),
            "context_extra": types.Schema(
                type=types.Type.STRING,
                description="Contexto adicional opcional que ayude a razonar.",
            ),
            "max_tokens": types.Schema(
                type=types.Type.INTEGER,
                description="Max tokens de respuesta. Default 450 (corto, para voz); subilo solo si Isaac pide algo extenso. Max 2048.",
            ),
        },
        required=["prompt"],
    ),
)

ASK_TERRA_CODE_DECL = types.FunctionDeclaration(
    name="ask_terra_code",
    description=(
        "Encolar a GPT-5.6 Terra con razonamiento high cuando la tarea sea generar codigo, "
        "entrar en modo agentico, disenar una implementacion, depurar un bug de "
        "software, crear scripts, refactorizar o planear cambios tecnicos. "
        "Jarvis da primero una respuesta inicial breve y envia una mejora cuando "
        "Terra termina. Usar antes que ask_grok_deep para trabajo de codigo/agentico."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "prompt": types.Schema(
                type=types.Type.STRING,
                description="Tarea concreta para GPT-5.6 Terra.",
            ),
            "context_extra": types.Schema(
                type=types.Type.STRING,
                description="Contexto adicional opcional: archivos, error, objetivo, constraints.",
            ),
            "max_output_tokens": types.Schema(
                type=types.Type.INTEGER,
                description="Max tokens de salida. Default 1600; subir solo si Isaac pide codigo completo. Max 8192.",
            ),
        },
        required=["prompt"],
    ),
)

SCREEN_LOOK_DECL = types.FunctionDeclaration(
    name="screen_look",
    description=(
        "Captura la pantalla de Isaac y te la entrega para describir, comentar o "
        "responder sobre lo que sea que este viendo: una imagen, un video, una "
        "web, un documento, un meme, un grafico, codigo, un error, una conversacion "
        "o un juego. Usala cuando Isaac diga 'mira mi "
        "pantalla', 'que ves', 'esto que es', 'que opinas de esto', 'mira esto', "
        "o cualquier referencia a algo visual frente a el. No la uses para buscar "
        "datos sensibles como numeros de cuenta, tarjetas, claves o IDs salvo que "
        "Isaac lo pida explicitamente en el mismo turno."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(
                type=types.Type.STRING,
                description="Motivo breve de la captura.",
            ),
        },
    ),
)

CAMERA_LOOK_DECL = types.FunctionDeclaration(
    name="camera_look",
    description=(
        "Captura UNA foto de la camara frontal de Isaac y te la entrega para que "
        "describas o analices lo que te esta mostrando frente a la camara: un objeto, "
        "una placa o componente FPV/electronica, una nota en papel, la pantalla de un "
        "multimetro o cargador, etc. Usala cuando Isaac diga 'mira esto', 'que es esto', "
        "'mira lo que tengo', 'fijate en este objeto' o senale algo fisico. Para ver en "
        "continuo mientras trabaja, usa camera_watch. No leas datos sensibles salvo que "
        "Isaac lo pida explicitamente en el mismo turno."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(
                type=types.Type.STRING,
                description="Motivo breve de la captura.",
            ),
        },
    ),
)

CAMERA_WATCH_DECL = types.FunctionDeclaration(
    name="camera_watch",
    description=(
        "Activa o desactiva el MODO VISION: JARVIS ve en continuo por la camara "
        "frontal mientras Isaac trabaja o le muestra algo en movimiento. Usa "
        "action='start' cuando Isaac diga 'modo vision', 'mira lo que hago', "
        "'guiame con esto', 'observa mientras...'. Usa action='stop' cuando diga "
        "'ya', 'salir de modo vision', 'deja de mirar', 'listo'. El modo se apaga "
        "solo tras unos segundos por seguridad. Para una sola foto usa camera_look."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="start para activar, stop para desactivar.",
            ),
            "duration_s": types.Schema(
                type=types.Type.NUMBER,
                description="Segundos de observacion al iniciar (default 90, max 180).",
            ),
        },
        required=["action"],
    ),
)

CAMERA_FOCUS_DECL = types.FunctionDeclaration(
    name="camera_focus",
    description=(
        "Marca con un crosshair el objeto principal que Isaac te esta mostrando por "
        "la camara y dibuja un recuadro sobre el en el preview. Usala cuando Isaac "
        "diga 'enfoca esto', 'que es esto exactamente', 'senala lo que ves', "
        "'marca el objeto'. Requiere que haya una captura reciente (camera_look o "
        "modo vision activo)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "label": types.Schema(
                type=types.Type.STRING,
                description="Pista opcional de que objeto enfocar.",
            ),
        },
    ),
)

CHROME_READ_PAGE_DECL = types.FunctionDeclaration(
    name="chrome_read_page",
    description=(
        "Lee texto de la pestaña activa de Google Chrome para convertir una pagina "
        "web en una explicacion hablada. Usala cuando Isaac diga 'leeme esta "
        "pagina', 'explicame este articulo', 'resumime esto', 'que dice esta web', "
        "'ayudame a entender lo que tengo abierto' o quiera escuchar el contenido "
        "de Chrome sin leerlo. Devuelve titulo, URL y texto extraido. Si no logra "
        "leer DOM/texto accesible porque es PDF, video, canvas o pagina protegida, "
        "usa screen_look como fallback visual."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "intent": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Que hacer con la pagina: 'explain' para explicar, 'summary' "
                    "para resumir, 'read' para leer en voz natural, 'extract' para "
                    "sacar puntos/datos concretos. Default 'explain'."
                ),
            ),
            "max_chars": types.Schema(
                type=types.Type.INTEGER,
                description="Maximo de caracteres de texto a traer. Default 9000, max 20000.",
            ),
            "prefer_visible": types.Schema(
                type=types.Type.BOOLEAN,
                description=(
                    "true = prioriza texto visible/accesible de la ventana activa; "
                    "false = permite intentar leer por URL publica primero."
                ),
            ),
        },
    ),
)

STUDY_MODE_DECL = types.FunctionDeclaration(
    name="study_mode",
    description=(
        "Controla JARVIS Study Mode, un agente observador de aprendizaje que captura "
        "evidencia de Chrome/lecturas/cursos y la convierte en notas Markdown para "
        "Obsidian. Usala cuando Isaac diga 'activa study mode', 'documenta esta "
        "pagina', 'toma apuntes de esta lectura', 'guarda esto en mi second brain', "
        "'pausa study mode', 'termina study mode', 'haz flush de los apuntes' o "
        "'cual es el estado del modo estudio'. En start crea una sesion explicita; "
        "capture_page agrega la pagina actual; flush_now sintetiza y escribe en "
        "Obsidian; stop hace flush y termina. Si flush_now/stop devuelven "
        "background=true, di que el guardado empezo en segundo plano y usa "
        "status para confirmar antes de decir que ya termino."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Accion: start, pause, resume, stop, status, capture_page, "
                    "add_observation, flush_now."
                ),
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description="Titulo de la sesion/curso/lectura. Ej: AWS Lambda Study.",
            ),
            "note_path": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Path relativo dentro de Jarvis Memory donde guardar la nota. "
                    "Ej: Study Mode/AWS Lambda.md. Si se omite, se genera por titulo."
                ),
            ),
            "continuous": types.Schema(
                type=types.Type.BOOLEAN,
                description="Si true, captura Chrome periodicamente mientras el modo esta activo.",
            ),
            "capture_now": types.Schema(
                type=types.Type.BOOLEAN,
                description="En start, capturar inmediatamente la pagina actual. Default true.",
            ),
            "text": types.Schema(
                type=types.Type.STRING,
                description="Observacion o duda de Isaac para add_observation.",
            ),
            "intent": types.Schema(
                type=types.Type.STRING,
                description="Intento de sintesis: study_notes, reading, video, questions, summary.",
            ),
        },
        required=["action"],
    ),
)

OBS_MEMORY_DECL = types.FunctionDeclaration(
    name="obs_memory",
    description=(
        "Controla OBS Studio por WebSocket y convierte grabaciones en memoria "
        "episodica para Obsidian. Usala cuando Isaac diga 'empieza a grabar con OBS', "
        "'documenta esta sesion', 'termina la captura', 'procesa la ultima grabacion "
        "de OBS' o quiera guardar una sesion de programacion, investigacion, reunion, "
        "edicion de video, curso o troubleshooting. start inicia grabacion; stop "
        "detiene y puede procesar; process_latest procesa el ultimo video; "
        "process_file procesa un archivo concreto; status revisa configuracion/OBS. "
        "El backend puede borrar el video tras una nota exitosa segun retencion."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Accion: start, stop, status, process_latest, process_file. "
                    "Aliases: begin/record, finish/end, latest."
                ),
            ),
            "title": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Titulo humano de la sesion. Ej: Debug Upwork Agent, Reunion cliente, "
                    "Investigacion OBS Jarvis."
                ),
            ),
            "path": types.Schema(
                type=types.Type.STRING,
                description="Path absoluto del video para process_file.",
            ),
            "process": types.Schema(
                type=types.Type.BOOLEAN,
                description="En stop, si true procesa la grabacion al detener. Default true.",
            ),
        },
        required=["action"],
    ),
)

FILE_ORGANIZER_DECL = types.FunctionDeclaration(
    name="file_organizer",
    description=(
        "Organiza archivos locales de Isaac de forma segura, sin borrar ni "
        "sobrescribir. Usa status para ver roots permitidos, scan para inspeccionar, "
        "plan para crear un plan persistido de movimientos, preview para crear "
        "una carpeta visible de vista previa sin mover originales, y apply para "
        "aplicar un plan con aprobacion visual HITL. En modo dry-run apply no "
        "mueve nada. Puede incluir carpetas/iconos del escritorio si include_folders=true. "
        "No acepta shell libre."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="Accion: status, scan, plan, preview, apply.",
            ),
            "root": types.Schema(
                type=types.Type.STRING,
                description="Root permitido para scan. Si se omite usa el primer root permitido.",
            ),
            "source_root": types.Schema(
                type=types.Type.STRING,
                description="Carpeta permitida de origen para crear un plan.",
            ),
            "target_root": types.Schema(
                type=types.Type.STRING,
                description="Carpeta permitida destino. Si se omite usa _Jarvis_Organized dentro del origen.",
            ),
            "recursive": types.Schema(
                type=types.Type.BOOLEAN,
                description="Si true inspecciona subcarpetas; default false.",
            ),
            "include_folders": types.Schema(
                type=types.Type.BOOLEAN,
                description=(
                    "Si true incluye carpetas top-level como elementos movibles. "
                    "Usalo para organizar iconos/carpetas del escritorio."
                ),
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximo de archivos a inspeccionar/planificar. Default 100, max 500.",
            ),
            "scheme": types.Schema(
                type=types.Type.STRING,
                description="Esquema de organizacion: by_type, by_extension, by_year_month.",
            ),
            "plan_id": types.Schema(
                type=types.Type.STRING,
                description="ID de plan a aplicar cuando action=apply.",
            ),
        },
        required=["action"],
    ),
)

DESKTOP_ICONS_DECL = types.FunctionDeclaration(
    name="desktop_icons",
    description=(
        "Mueve visualmente la posicion de los iconos del escritorio de Windows. "
        "Usala cuando Isaac pida mover/reacomodar fisicamente iconos en la pantalla "
        "del escritorio. No mueve archivos a carpetas ni instalaciones; para eso usa "
        "file_organizer. Requiere aprobacion HITL para arrange."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="Accion: status, arrange.",
            ),
            "layout": types.Schema(
                type=types.Type.STRING,
                description="Layout: left, right, top. Default left.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximo de iconos a reposicionar. Omitir para todos.",
            ),
            "start_x": types.Schema(
                type=types.Type.INTEGER,
                description="Coordenada X inicial en pixeles. Default 20.",
            ),
            "start_y": types.Schema(
                type=types.Type.INTEGER,
                description="Coordenada Y inicial en pixeles. Default 20.",
            ),
            "spacing_x": types.Schema(
                type=types.Type.INTEGER,
                description="Espaciado horizontal entre iconos. Default 120.",
            ),
            "spacing_y": types.Schema(
                type=types.Type.INTEGER,
                description="Espaciado vertical entre iconos. Default 95.",
            ),
            "columns": types.Schema(
                type=types.Type.INTEGER,
                description="Columnas para layout top. Opcional.",
            ),
        },
        required=["action"],
    ),
)

JARVIS_SKILL_DECL = types.FunctionDeclaration(
    name="jarvis_skill",
    description=(
        "Gestiona skills runtime de Jarvis: perfiles operativos especializados "
        "con instrucciones y tools recomendadas. Usala cuando Isaac pida activar "
        "una capacidad/modo especializado, listar skills, o trabajar con una "
        "tarea que encaja con una skill disponible."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="Accion: list, get, activate, deactivate, status, reload.",
            ),
            "name": types.Schema(
                type=types.Type.STRING,
                description="Nombre de la skill para get/activate.",
            ),
        },
        required=["action"],
    ),
)

JARVIS_RUN_SAFE_COMMAND_DECL = types.FunctionDeclaration(
    name="jarvis_run_safe_command",
    description=(
        "Ejecuta operaciones read-only estructuradas dentro del proyecto Jarvis. "
        "No acepta shell libre ni PowerShell arbitrario. Usala para listar archivos, "
        "leer archivos no sensibles, buscar texto o consultar estado git."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "operation": types.Schema(
                type=types.Type.STRING,
                description="Operacion: list_dir, read_file, search_text, git_status, git_diff_stat, git_log.",
            ),
            "path": types.Schema(
                type=types.Type.STRING,
                description="Path relativo dentro del proyecto. Requerido para read_file; opcional en list_dir/search_text.",
            ),
            "query": types.Schema(
                type=types.Type.STRING,
                description="Texto a buscar cuando operation=search_text.",
            ),
            "max_chars": types.Schema(
                type=types.Type.INTEGER,
                description="Maximo de caracteres para read_file. Default 4000, max 20000.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Limite de items/resultados. Default 100, max 500.",
            ),
        },
        required=["operation"],
    ),
)

JARVIS_OPEN_POWERSHELL_DECL = types.FunctionDeclaration(
    name="jarvis_open_powershell",
    description=(
        "Abre una ventana de PowerShell en un directorio validado dentro del "
        "proyecto Jarvis. Usala cuando Isaac pida abrir PowerShell/terminal/consola. "
        "Requiere aprobacion HITL; no uses SendKeys ni WScript.Shell para esto."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "cwd": types.Schema(
                type=types.Type.STRING,
                description="Directorio de trabajo opcional dentro del proyecto Jarvis.",
            ),
        },
    ),
)

JARVIS_OPEN_URL_DECL = types.FunctionDeclaration(
    name="jarvis_open_url",
    description=(
        "Abre el navegador web por defecto. Si el usuario pide abrir el navegador, "
        "usa esta tool con about:blank. Si pide abrir una pagina, pasa la URL. "
        "Solo acepta http(s) o about:blank."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "url": types.Schema(
                type=types.Type.STRING,
                description="URL http(s) a abrir, o about:blank. Default about:blank.",
            ),
        },
    ),
)

JARVIS_OPEN_OBSIDIAN_DECL = types.FunctionDeclaration(
    name="jarvis_open_obsidian",
    description=(
        "Abre Obsidian en el vault configurado de Isaac, o abre una nota concreta "
        "dentro del vault. Usala cuando Isaac pida abrir Obsidian, mostrar una nota, "
        "ver un nodo, o despues de crear/actualizar documentacion si quiere verla."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "path": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Path relativo de la nota dentro del vault. Opcional; si se omite "
                    "solo abre/enfoca el vault. Puede incluir o no .md."
                ),
            ),
            "pane_type": types.Schema(
                type=types.Type.STRING,
                description="Opcional: tab, split o window para abrir la nota.",
            ),
        },
    ),
)

JARVIS_SET_MODE_DECL = types.FunctionDeclaration(
    name="jarvis_set_mode",
    description=(
        "Cambia el modo de trabajo de Jarvis: general, coding, agentic, "
        "debugging, meeting, planning, study o english. Usa agentic cuando "
        "Isaac pida modo agentico o trabajo multi-paso con herramientas."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "mode": types.Schema(type=types.Type.STRING, description="Modo nuevo."),
        },
        required=["mode"],
    ),
)

JARVIS_GET_MODE_DECL = types.FunctionDeclaration(
    name="jarvis_get_mode",
    description="Consulta el modo de trabajo actual de Jarvis.",
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)

ENGLISH_PRACTICE_DECL = types.FunctionDeclaration(
    name="english_practice",
    description=(
        "Activa, desactiva o consulta English Practice Mode. Usala cuando Isaac "
        "pida practicar ingles, hablar en ingles, corregir su ingles, preparar "
        "entrevistas/roleplays en ingles, shadowing o desactivar la practica. "
        "Cuando esta activo, Jarvis conversa principalmente en ingles y da "
        "correcciones breves despues de cada intervencion de Isaac."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="Accion: start, stop, status, roleplay, shadowing.",
            ),
            "level": types.Schema(
                type=types.Type.STRING,
                description="Nivel estimado: A1, A2, B1, B2, C1 o auto. Default auto.",
            ),
            "focus": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Enfoque de practica: conversation, pronunciation, interview, "
                    "dev, upwork, travel, grammar, roleplay o shadowing."
                ),
            ),
            "correction_style": types.Schema(
                type=types.Type.STRING,
                description="Estilo de correccion: light, standard o strict. Default standard.",
            ),
        },
        required=["action"],
    ),
)

JARVIS_SECURITY_STATUS_DECL = types.FunctionDeclaration(
    name="jarvis_security_status",
    description=(
        "Consulta el estado real de las politicas de seguridad implementadas en "
        "el backend de Jarvis: HITL, sandbox de rutas, filtros de secretos, "
        "kill-switch y doble candado de borrado. Usala cuando Isaac pregunte "
        "por seguridad, permisos, autopilot, borrado, secretos, sandbox o HITL."
    ),
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)

SPOTIFY_CONTROL_DECL = types.FunctionDeclaration(
    name="spotify_control",
    description=(
        "Controla Spotify para Isaac mediante comandos de voz. Maneja la "
        "biblioteca personal de Isaac (su playlist 'Tus me gusta') ademas de "
        "busqueda global. Usala cuando Isaac:\n"
        "  - 'ponme [X de mi lista|que me gusta|de mis likes]' -> "
        "action='play_from_library', query='X'. PREFIERE esta sobre "
        "search_and_play cuando Isaac referencia su biblioteca personal o "
        "menciona canciones/artistas que ya escucha.\n"
        "  - 'ponme algo aleatorio/random de mis likes' -> action='play_random_liked'\n"
        "  - 'cuantas canciones tengo|estado de mi biblioteca' -> action='library_status'\n"
        "  - 'que likeaste ultimamente|recientes|ultimas que agregue' -> "
        "action='library_top_recent' (opcional count, default 10)\n"
        "  - 'actualiza mi biblioteca|refresca mis likes' -> action='refresh_library'\n"
        "  - 'pon X' generico (sin referencia a su lista) -> action='search_and_play'\n"
        "  - pausar/reanudar/siguiente/anterior, volumen con rampa, "
        "duck/restore para VAD: como antes.\n"
        "Si play_from_library devuelve ok=false (no encontro match en la "
        "biblioteca), puedes caer a search_and_play global con el mismo query."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Accion exacta: play_from_library, play_random_liked, "
                    "library_status, refresh_library, search_and_play, pause, "
                    "play, next, previous, set_volume, volume_up, volume_down, "
                    "duck_audio o restore_audio."
                ),
            ),
            "query": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Texto de busqueda. Para play_from_library: cancion, "
                    "artista, album o fragmento que Isaac dijo. Para "
                    "search_and_play: busqueda global Spotify."
                ),
            ),
            "volume_percent": types.Schema(
                type=types.Type.INTEGER,
                description=(
                    "Porcentaje objetivo de volumen 0-100 para set_volume, "
                    "duck_audio o restore_audio. Ej: 50 para dejarlo a la mitad."
                ),
            ),
            "duration_s": types.Schema(
                type=types.Type.NUMBER,
                description="Duracion de la rampa exponencial de volumen en segundos. Default 0.9.",
            ),
            "count": types.Schema(
                type=types.Type.INTEGER,
                description=(
                    "Para play_random_liked: cuantas canciones aleatorias "
                    "encolar (primera suena, resto a la cola). Default 1."
                ),
            ),
        },
        required=["action"],
    ),
)

OBSIDIAN_MCP_DECL = types.FunctionDeclaration(
    name="obsidian_mcp",
    description=(
        "Opera el vault Obsidian via MCP. Usala para crear carpetas, crear/editar/"
        "append notas, mover o renombrar notas/carpetas, leer notas, listar carpetas "
        "y linkear nodos. Para borrar requiere configuracion explicita."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "operation": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Operacion: list_folder, read_note, create_folder, create_note, "
                    "update_note, append_note, move_path, delete_path, link_notes."
                ),
            ),
            "path": types.Schema(type=types.Type.STRING, description="Path relativo dentro del vault."),
            "destination": types.Schema(type=types.Type.STRING, description="Destino para move_path."),
            "content": types.Schema(type=types.Type.STRING, description="Contenido markdown para notas."),
            "section_title": types.Schema(type=types.Type.STRING, description="Titulo de seccion para append_note."),
            "tags": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
                description="Tags Obsidian.",
            ),
            "overwrite": types.Schema(type=types.Type.BOOLEAN, description="Permitir sobrescribir si existe."),
            "limit": types.Schema(type=types.Type.INTEGER, description="Limite para list_folder."),
            "note_from": types.Schema(type=types.Type.STRING, description="Nota origen para link_notes."),
            "note_to": types.Schema(type=types.Type.STRING, description="Nota destino para link_notes."),
        },
        required=["operation"],
    ),
)


JARVIS_LISTEN_MODE_DECL = types.FunctionDeclaration(
    name="jarvis_listen_mode",
    description=(
        "Cambia el MODO DE ESCUCHA de Jarvis. Usala cuando Isaac pida activar o "
        "desactivar la escucha libre / manos libres por voz, para no tener que usar "
        "la tecla Ctrl+Shift+M. Frases tipicas: 'activá escucha libre', 'modo manos "
        "libres', 'escuchame en libre', 'dejá el microfono abierto' -> mode='libre'. "
        "'salí de escucha libre', 'volvé a push to talk', 'dejá de escucharme', "
        "'apagá el microfono', 'modo manual' -> mode='ptt'. Es idempotente: si ya "
        "esta en ese modo, lo confirma sin error. NO la uses para los modos de "
        "trabajo (coding/study/etc): para eso esta jarvis_set_mode."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "mode": types.Schema(
                type=types.Type.STRING,
                description="'libre' (manos libres, VAD) o 'ptt' (push-to-talk con Ctrl).",
            ),
        },
        required=["mode"],
    ),
)


JARVIS_PROACTIVE_CHECK_DECL = types.FunctionDeclaration(
    name="jarvis_proactive_check",
    description=(
        "Consulta si JARVIS tiene una sugerencia proactiva pertinente para ofrecer "
        "AHORA. Llamala SOLO en ventanas naturales: cuando un tema se cierra, cuando "
        "Isaac pregunta '¿algo mas?', o al cerrar la sesion. NUNCA a media explicacion. "
        "Devuelve una sola oportunidad estructurada (o ninguna). Si Isaac descarto la "
        "sugerencia anterior, vuelve a llamarla con dismissed_last=true para no repetirla."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "dismissed_last": types.Schema(
                type=types.Type.BOOLEAN,
                description="true si Isaac ignoro/rechazo la sugerencia ofrecida antes.",
            ),
        },
    ),
)


JARVIS_TASK_DECL = types.FunctionDeclaration(
    name="jarvis_task",
    description=(
        "Crea, consulta, lista o cancela MISIONES DURABLES read-only de JARVIS. "
        "Usa action='create' para trabajo de varios pasos que debe continuar en "
        "background sin bloquear la voz; devuelve mission_id inmediatamente. Usa "
        "status para consultar progreso/resultado, list para ver misiones recientes y "
        "cancel para detener una mision. v0 solo puede leer el repositorio JARVIS."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="create | status | list | cancel",
            ),
            "goal": types.Schema(
                type=types.Type.STRING,
                description="Meta concreta de la mision; requerida para create.",
            ),
            "mission_id": types.Schema(
                type=types.Type.STRING,
                description="ID msn_*; requerido para status/cancel.",
            ),
            "max_steps": types.Schema(
                type=types.Type.INTEGER,
                description="Maximo de pasos read-only (1..8). Opcional.",
            ),
            "deadline_seconds": types.Schema(
                type=types.Type.INTEGER,
                description="Deadline de la mision (30..600 segundos). Opcional.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Cantidad de misiones recientes para list (1..20).",
            ),
        },
        required=["action"],
    ),
)


def all_function_declarations(mode: str | None = None) -> list[types.FunctionDeclaration]:
    decls = [
        JARVIS_RECALL_DECL,
        JARVIS_SESSION_RECALL_DECL,
        JARVIS_CURRENT_SESSION_RECALL_DECL,
        JARVIS_REMEMBER_DECL,
        JARVIS_BROWSE_DECL,
        JARVIS_LINK_DECL,
        ASK_TERRA_CODE_DECL,
        ASK_GROK_DEEP_DECL,
        SCREEN_LOOK_DECL,
        CAMERA_LOOK_DECL,
        CAMERA_WATCH_DECL,
        CAMERA_FOCUS_DECL,
        CHROME_READ_PAGE_DECL,
        STUDY_MODE_DECL,
        OBS_MEMORY_DECL,
        FILE_ORGANIZER_DECL,
        DESKTOP_ICONS_DECL,
        JARVIS_SKILL_DECL,
        JARVIS_RUN_SAFE_COMMAND_DECL,
        JARVIS_OPEN_POWERSHELL_DECL,
        JARVIS_OPEN_URL_DECL,
        JARVIS_OPEN_OBSIDIAN_DECL,
        JARVIS_SET_MODE_DECL,
        JARVIS_GET_MODE_DECL,
        JARVIS_LISTEN_MODE_DECL,
        ENGLISH_PRACTICE_DECL,
        JARVIS_SECURITY_STATUS_DECL,
        SPOTIFY_CONTROL_DECL,
        OBSIDIAN_MCP_DECL,
        JARVIS_PROACTIVE_CHECK_DECL,
        JARVIS_TASK_DECL,
    ]
    if mode is None:
        return decls
    allowed = tools_for_mode(mode)
    return [decl for decl in decls if decl.name in allowed]


def make_tool_object(mode: str | None = None) -> types.Tool:
    """Wrap function declarations en un objeto types.Tool listo para LiveConnectConfig.

    Si `mode` es None se expone el catalogo completo (tests / dispatcher).
    En Live, pasar el modo activo para no ofrecer computer-use en general.
    """
    return types.Tool(function_declarations=all_function_declarations(mode))
