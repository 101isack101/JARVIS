"""
actions/chrome_reader.py - Lectura de la pagina activa de Chrome para Jarvis.

Objetivo: que Isaac pueda decir "Jarvis, leeme esta pagina" o "explicame esto"
y recibir una explicacion hablada sin tener que leer la pantalla.

Estrategia por capas:
  1. Chrome DevTools Protocol (si Chrome se lanzo con remote debugging).
  2. UI Automation de Windows via pywinauto para leer texto visible/accesible.
  3. Fetch HTTP de la URL publica y extraccion simple de HTML.

No navega, no hace clicks, no simula teclas y no lee automaticamente. Solo se
ejecuta cuando Jarvis/Gemini invoca la tool por una indicacion explicita de Isaac.
"""

from __future__ import annotations

import html
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


DEFAULT_MAX_CHARS = 9000
BLOCKED_URL_SCHEMES = {"chrome", "chrome-extension", "edge", "about", "file"}
TEXT_CONTROL_TYPES = {
    "Document",
    "Text",
    "Hyperlink",
    "Button",
    "Header",
    "ListItem",
    "DataItem",
    "Edit",
    "Pane",
}


@dataclass(frozen=True)
class ChromePageRead:
    ok: bool
    source: str
    title: str = ""
    url: str = ""
    text: str = ""
    error: str = ""
    warnings: list[str] | None = None

    def as_dict(self, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
        safe_text = _redact_sensitive_browser_text(self.text)
        safe_url = _redact_sensitive_url(self.url)
        clipped, truncated = _clip_text(safe_text, max_chars)
        return {
            "ok": self.ok,
            "source": self.source,
            "title": _redact_sensitive_browser_text(self.title),
            "url": safe_url,
            "text": clipped,
            "truncated": truncated,
            "chars": len(safe_text),
            "error": self.error,
            "warnings": self.warnings or [],
        }


class _ReadableHTMLParser(HTMLParser):
    """Extractor simple de texto visible usando solo stdlib."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
    BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.parts.append(text)
            self.parts.append(" ")

    @property
    def title(self) -> str:
        return _normalize_text(" ".join(self.title_parts))[:300]

    @property
    def text(self) -> str:
        return _normalize_text("".join(self.parts))


class ChromeReader:
    """Lee la pestaña activa de Chrome con fallbacks seguros."""

    def __init__(
        self,
        *,
        devtools_port: int | None = None,
        request_timeout_s: float = 3.0,
    ) -> None:
        self.devtools_port = int(
            devtools_port
            if devtools_port is not None
            else os.environ.get("JARVIS_CHROME_DEBUG_PORT", "9222")
        )
        self.request_timeout_s = request_timeout_s

    def read_active_page(
        self,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        prefer_visible: bool = True,
    ) -> ChromePageRead:
        warnings: list[str] = []
        max_chars = max(1200, min(int(max_chars or DEFAULT_MAX_CHARS), 20000))

        if _port_open("127.0.0.1", self.devtools_port, timeout_s=0.25):
            result = self._read_via_devtools(max_chars=max_chars)
            if result.ok and result.text:
                return result
            warnings.append(result.error or "DevTools no devolvio texto util.")

        if prefer_visible:
            result = self._read_via_uia(max_chars=max_chars)
            if result.ok and result.text:
                return _with_warnings(result, warnings)
            warnings.append(result.error or "UI Automation no devolvio texto util.")

        url, title, url_error = self._active_chrome_url_title()
        if url:
            result = self._read_via_http(url=url, title_hint=title, max_chars=max_chars)
            if result.ok and result.text:
                return _with_warnings(result, warnings)
            warnings.append(result.error or "Fetch HTTP no devolvio texto util.")
        elif url_error:
            warnings.append(url_error)

        return ChromePageRead(
            ok=False,
            source="none",
            title=title,
            url=url,
            error=(
                "No pude extraer texto de Chrome. Si es una pagina visual, PDF, "
                "video o contenido protegido, usa screen_look como fallback."
            ),
            warnings=warnings,
        )

    # ---- DevTools Protocol ----

    def _read_via_devtools(self, *, max_chars: int) -> ChromePageRead:
        try:
            tabs = _json_get(f"http://127.0.0.1:{self.devtools_port}/json/list", self.request_timeout_s)
            pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if not pages:
                return ChromePageRead(ok=False, source="devtools", error="No hay pestañas page en DevTools.")
            active_title = self._active_window_title()
            page = _best_devtools_page(pages, active_title)
            websocket_url = page.get("webSocketDebuggerUrl")
            if not websocket_url:
                return ChromePageRead(ok=False, source="devtools", error="Tab sin websocket DevTools.")
            text = self._devtools_eval_inner_text(websocket_url, max_chars=max_chars)
            return ChromePageRead(
                ok=bool(text),
                source="devtools",
                title=page.get("title", ""),
                url=page.get("url", ""),
                text=text,
                error="" if text else "DevTools devolvio texto vacio.",
            )
        except Exception as exc:
            return ChromePageRead(
                ok=False,
                source="devtools",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _devtools_eval_inner_text(self, websocket_url: str, *, max_chars: int) -> str:
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError("Falta websocket-client para usar Chrome DevTools.") from exc

        expr = (
            "(() => {"
            "const parts=[];"
            "const pick=(s)=>s&&String(s).trim();"
            "const title=pick(document.title);"
            "if(title) parts.push('# '+title);"
            "const main=document.querySelector('main, article, [role=\"main\"]') || document.body;"
            "parts.push(main ? main.innerText : document.body.innerText);"
            f"return parts.join('\\n\\n').slice(0,{max_chars});"
            "})()"
        )
        ws = websocket.create_connection(websocket_url, timeout=self.request_timeout_s)
        try:
            ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            }))
            while True:
                payload = json.loads(ws.recv())
                if payload.get("id") != 1:
                    continue
                result = (((payload.get("result") or {}).get("result") or {}).get("value") or "")
                return _normalize_text(str(result))
        finally:
            ws.close()

    # ---- Windows UI Automation ----

    def _read_via_uia(self, *, max_chars: int) -> ChromePageRead:
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            return ChromePageRead(ok=False, source="uia", error=f"pywinauto no disponible: {exc}")

        try:
            window = _target_chrome_window_uia(Desktop)
            title = window.window_text() or ""
            class_name = ""
            try:
                class_name = window.element_info.class_name or ""
            except Exception:
                pass
            process_name = _window_process_name(window)
            if not _looks_like_chrome(title, class_name, process_name):
                return ChromePageRead(
                    ok=False,
                    source="uia",
                    title=title,
                    error=f"La ventana activa no parece Chrome: {title} ({process_name})",
                )

            url = self._url_from_window(window)
            pieces: list[str] = []
            seen: set[str] = set()
            document_roots = []
            try:
                document_roots = window.descendants(control_type="Document")
            except Exception:
                document_roots = []
            roots = document_roots or [window]
            for root in roots:
                candidates = [root]
                try:
                    candidates.extend(root.descendants())
                except Exception:
                    pass
                for element in candidates:
                    try:
                        info = element.element_info
                        control_type = info.control_type or ""
                        if control_type not in TEXT_CONTROL_TYPES:
                            continue
                        text = _normalize_text(element.window_text() or info.name or "")
                        if not text or len(text) < 2 or text in seen:
                            continue
                        if _is_browser_chrome_noise(text, url):
                            continue
                        seen.add(text)
                        pieces.append(text)
                        if sum(len(p) for p in pieces) >= max_chars:
                            break
                    except Exception:
                        continue
                if sum(len(p) for p in pieces) >= max_chars:
                    break

            text = _normalize_text("\n".join(pieces))
            return ChromePageRead(
                ok=bool(text),
                source="uia",
                title=_clean_chrome_title(title),
                url=url,
                text=text,
                error="" if text else "Chrome no expuso texto accesible en la pestaña activa.",
            )
        except Exception as exc:
            return ChromePageRead(ok=False, source="uia", error=f"{type(exc).__name__}: {exc}")

    def _active_chrome_url_title(self) -> tuple[str, str, str]:
        try:
            from pywinauto import Desktop

            window = _target_chrome_window_uia(Desktop)
            title = window.window_text() or ""
            class_name = getattr(window.element_info, "class_name", "") or ""
            process_name = _window_process_name(window)
            if not _looks_like_chrome(title, class_name, process_name):
                return "", title, f"La ventana activa no parece Chrome: {title} ({process_name})"
            return self._url_from_window(window), _clean_chrome_title(title), ""
        except Exception as exc:
            return "", "", f"No pude leer URL activa: {type(exc).__name__}: {exc}"

    @staticmethod
    def _active_window_title() -> str:
        try:
            from pywinauto import Desktop

            return _target_chrome_window_uia(Desktop).window_text() or ""
        except Exception:
            return ""

    @staticmethod
    def _url_from_window(window) -> str:
        candidates = []
        try:
            candidates.extend(window.descendants(control_type="Edit"))
        except Exception:
            pass
        for control in candidates:
            try:
                value = ""
                try:
                    value = control.iface_value.CurrentValue
                except Exception:
                    value = control.window_text()
                value = _normalize_text(value)
                if _looks_like_url(value):
                    return _normalize_url(value)
            except Exception:
                continue
        return ""

    # ---- HTTP fallback ----

    def _read_via_http(self, *, url: str, title_hint: str, max_chars: int) -> ChromePageRead:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() in BLOCKED_URL_SCHEMES:
            return ChromePageRead(
                ok=False,
                source="http",
                title=title_hint,
                url=url,
                error=f"No puedo leer URLs internas/locales por HTTP: {parsed.scheme}:",
            )
        if parsed.scheme.lower() not in {"http", "https"}:
            return ChromePageRead(
                ok=False,
                source="http",
                title=title_hint,
                url=url,
                error=f"URL no soportada: {url}",
            )
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 JarvisChromeReader/1.0"
                    )
                },
            )
            with urllib.request.urlopen(req, timeout=self.request_timeout_s) as resp:
                raw = resp.read(1_500_000)
                charset = resp.headers.get_content_charset() or "utf-8"
            parser = _ReadableHTMLParser()
            parser.feed(raw.decode(charset, errors="replace"))
            text = parser.text
            title = parser.title or title_hint
            return ChromePageRead(
                ok=bool(text),
                source="http",
                title=title,
                url=url,
                text=text[:max_chars],
                error="" if text else "HTML sin texto legible.",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ChromePageRead(
                ok=False,
                source="http",
                title=title_hint,
                url=url,
                error=f"{type(exc).__name__}: {exc}",
            )


def _normalize_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _redact_sensitive_browser_text(text: str) -> str:
    redacted = text or ""
    redacted = re.sub(
        r"(?i)\b(code|access_token|refresh_token|id_token|auth|authorization|token|secret|password)=([^&\s]+)",
        lambda m: f"{m.group(1)}=[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{20,}", r"\1[REDACTED]", redacted)
    return redacted


def _redact_sensitive_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        safe_query = []
        for key, value in query:
            if key.lower() in {"code", "access_token", "refresh_token", "id_token", "token", "secret", "password"}:
                safe_query.append((key, "[REDACTED]"))
            else:
                safe_query.append((key, value))
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(safe_query),
            parsed.fragment,
        ))
    except Exception:
        return _redact_sensitive_browser_text(url)


def _foreground_window_uia(desktop_cls):
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("No hay ventana foreground.")
    return desktop_cls(backend="uia").window(handle=hwnd)


def _target_chrome_window_uia(desktop_cls):
    """Foreground si es Chrome; si no, primera ventana visible de chrome.exe."""
    desktop = desktop_cls(backend="uia")
    try:
        foreground = _foreground_window_uia(desktop_cls)
        title = foreground.window_text() or ""
        class_name = getattr(foreground.element_info, "class_name", "") or ""
        process_name = _window_process_name(foreground)
        if _looks_like_chrome(title, class_name, process_name):
            return foreground
    except Exception:
        pass

    for window in desktop.windows():
        try:
            if not window.is_visible():
                continue
            title = window.window_text() or ""
            class_name = getattr(window.element_info, "class_name", "") or ""
            process_name = _window_process_name(window)
            if _looks_like_chrome(title, class_name, process_name):
                return window
        except Exception:
            continue
    raise RuntimeError("No encontre una ventana abierta de Google Chrome.")


def _clip_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    head = text[: max_chars - 250].rstrip()
    return head + "\n\n[Texto truncado para voz; pide 'continua' si necesitas mas.]", True


def _port_open(host: str, port: int, *, timeout_s: float) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return True
    except OSError:
        return False


def _json_get(url: str, timeout_s: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _best_devtools_page(pages: list[dict], active_title: str) -> dict:
    clean_active = _clean_chrome_title(active_title).lower()
    if clean_active:
        for page in pages:
            title = str(page.get("title", "")).lower()
            if clean_active and (clean_active in title or title in clean_active):
                return page
    return pages[0]


def _looks_like_chrome(title: str, class_name: str, process_name: str = "") -> bool:
    process = process_name.lower()
    if process in {"chrome.exe", "brave.exe", "msedge.exe"}:
        return True
    haystack = f"{title} {class_name}".lower()
    return "google chrome" in haystack or "chrome.exe" in haystack


def _window_process_name(window) -> str:
    try:
        import win32api
        import win32con
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(window.handle)
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            pid,
        )
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)
        return os.path.basename(path)
    except Exception:
        return ""


def _clean_chrome_title(title: str) -> str:
    return re.sub(r"\s*-\s*Google Chrome\s*$", "", title or "").strip()


def _looks_like_url(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    if value.startswith(("http://", "https://", "chrome://", "chrome-extension://", "about:")):
        return True
    return bool(re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", value, flags=re.I))


def _normalize_url(value: str) -> str:
    value = value.strip()
    if "://" not in value and not value.startswith("about:"):
        value = "https://" + value
    return value


def _is_browser_chrome_noise(text: str, url: str) -> bool:
    lower = text.lower()
    if url and text == url:
        return True
    noisy_exact = {
        "back",
        "forward",
        "reload",
        "address and search bar",
        "minimize",
        "maximize",
        "close",
        "new tab",
        "extensions",
        "profile",
        "google chrome",
    }
    if lower in noisy_exact:
        return True
    if lower.startswith(("tab search", "customize and control", "bookmarks")):
        return True
    return False


def _with_warnings(result: ChromePageRead, warnings: list[str]) -> ChromePageRead:
    if not warnings:
        return result
    return ChromePageRead(
        ok=result.ok,
        source=result.source,
        title=result.title,
        url=result.url,
        text=result.text,
        error=result.error,
        warnings=[*(result.warnings or []), *warnings],
    )


if __name__ == "__main__":
    reader = ChromeReader()
    print(json.dumps(reader.read_active_page().as_dict(), ensure_ascii=False, indent=2))
