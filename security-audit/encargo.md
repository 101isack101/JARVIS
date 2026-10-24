Eres un auditor de seguridad de aplicaciones. Analiza este repositorio EN MODO SOLO LECTURA; no modifiques, no crees y no borres ningún archivo. Es un proyecto propio y la auditoría es defensiva.

Contexto ya verificado (no lo redescubras a ciegas; contrástalo con el código):
- Raíz: este directorio (JARVIS). Python 3.11 + Tauri/React + sidecars C++.
- Entradas: jarvis.py, bridge HTTP 127.0.0.1:8765, jarvis-desktop.exe.
- Agente: Gemini Live con tools; ask_terra_code, ask_grok_deep, Claude tool; agent_runtime/tools.py (L1 lectura, L2 writes); actions/; file organizer; mcp_obsidian/.
- Visión y pantalla, hotkeys, HITL, secret_filter, data/ con SQLite, logs y screenshots.
- .env está en .gitignore. No imprimas secretos; redáctalos (sk-1234****). No llames APIs ni arranques JARVIS.

Para cada hallazgo entrega: título, severidad (crítica/alta/media/baja/info), CWE, archivo y línea exactos, fragmento mínimo de evidencia (con secretos redactados), escenario de explotación conceptual, impacto y corrección concreta, además de tu nivel de confianza (alta/media/baja). No inventes hallazgos: si no hay evidencia en el código, no lo reportes.

Cubre, si aplican: inyección (SQL, comandos, plantillas), path traversal y manejo de archivos, autenticación y autorización, secretos en código/historial/config, validación de entradas y salidas, deserialización insegura, SSRF, CORS/CSRF/cabeceras, webhooks sin verificación de firma, criptografía débil, dependencias con CVE conocidas, Docker/CI/infra, logs con datos sensibles, condiciones de carrera, DoS (límites, timeouts, tamaños) y, si hay agentes o LLMs: prompt injection directa e indirecta, herramientas con permisos excesivos, exfiltración de datos vía salidas del modelo y falta de confirmación en acciones destructivas.

Prioriza: agent_runtime/tools.py, actions/, mcp_obsidian/, el bridge HTTP :8765, file organizer, secret_filter, y cualquier shell/subprocess alcanzable desde una tool o desde el puente web.

Responde el informe completo en este chat. No escribas archivos.
