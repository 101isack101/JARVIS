"""mcp_obsidian/client.py - Cliente stdio sync para Jarvis."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent


class ObsidianMCPClient:
    """Cliente MCP sencillo.

    Para mantener Jarvis simple y evitar un loop MCP persistente paralelo, cada
    llamada levanta el server stdio, llama una tool y cierra. Las operaciones de
    edicion de Obsidian no estan en el camino de latencia de voz normal.
    """

    def __init__(
        self,
        python_exe: str | None = None,
        cwd: Path | str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.python_exe = python_exe or sys.executable
        self.cwd = Path(cwd or ROOT)
        self.timeout_s = timeout_s

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict:
        return anyio.run(self._call_tool_async, name, arguments or {})

    async def _call_tool_async(self, name: str, arguments: dict[str, Any]) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        server = StdioServerParameters(
            command=self.python_exe,
            args=["-m", "mcp_obsidian.server"],
            cwd=self.cwd,
            env=env,
            encoding="utf-8",
            encoding_error_handler="replace",
        )
        with anyio.fail_after(self.timeout_s):
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        return self._parse_result(result)

    def _parse_result(self, result) -> dict:
        if getattr(result, "isError", False):
            return {"ok": False, "error": str(result)}
        content = getattr(result, "content", []) or []
        if not content:
            return {"ok": True, "content": []}
        first = content[0]
        text = getattr(first, "text", None)
        if text is None:
            return {"ok": True, "content": [str(c) for c in content]}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return {"ok": True, "value": parsed}
        except json.JSONDecodeError:
            return {"ok": True, "text": text}
