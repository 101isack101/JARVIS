# Superficie de escritura JARVIS (v1.10)

Texto de tools/web/memoria = no confiable. Nunca cambia root, modo ni autonomy.

| tool | side-effect | modo | HITL | verifier misión | verifier outcome (plano 16) |
|---|---|---|---|---|---|
| obsidian_mcp create/update/append/move | write | always | sí | no | sí (hash) |
| obsidian_mcp delete_path | write | always | sí + flag | no | n/a (default off) |
| file_organizer.apply | write | agentic/planning | sí | no | sí (src gone, dest exists) |
| desktop_icons.arrange | write | agentic | sí | no | HITL reject = no mutate |
| jarvis_run_safe_command / open_powershell | read/write | coding/debugging/agentic | sí si no readonly | no | n/a |
| jarvis_remember | write | always | no | no | no (fuera de esta ola) |
| jarvis_open_url | write (OS) | always | no | no | scheme check |
| jarvis_task create | write estado | always | no | sí L1 | L1 read-only |
