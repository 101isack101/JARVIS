"""Compat: el reasoner profundo de JARVIS es Grok 4.6.

Los imports historicos `from claude.reasoner import ClaudeReasoner` siguen
funcionando; la implementacion vive en `grok.reasoner`.
"""

from grok.reasoner import (  # noqa: F401
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    GrokReasoner,
    GrokReasoner as ClaudeReasoner,
    ReasonerResponse,
)
