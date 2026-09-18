# Spec L2 — escrituras como steps de misión

Estado: activo en v1.10+ via `JARVIS_AGENT_AUTONOMY_LEVEL=L2`. HITL por step write.

## Objetivo

`file_organizer.apply` y `obsidian_mcp` write/move dejan de ser tools directas
de Gemini. Pasan a catálogo de misión L2 con HITL por step write.

## Fuera

PowerShell libre, browser, roots nuevos, `delete_path` (sigue flag).

## Amenazas

- Plan LLM inventa operation `write_file`
- Tool output cambia `root_path`
- HITL timeout → fail-closed
- Apply parcial marcado `completed`

## Criterio para abrir L2

Todos los tests de constitución §10 en fakes, más V&V de outcome de v1.10
en verde, más `MissionPolicy` rechazando WRITE ops en L1 (ya).
