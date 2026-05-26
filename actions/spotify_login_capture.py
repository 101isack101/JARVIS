"""
actions/spotify_login_capture.py - Mini servidor para capturar OAuth callback.

Levanta HTTP server en 127.0.0.1:8888 que espera el redirect de Spotify con
?code=... Cuando llega, intercambia el code por access_token+refresh_token
usando SpotifyOAuth, escribe el cache local y termina.

Pensado para correr en background mientras el usuario clickea el link de
authorize en su navegador. Timeout 5 minutos.

Uso:
    python -m actions.spotify_login_capture
"""

from __future__ import annotations

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 300  # 5 minutos

# Resultado compartido entre handler y main thread
_captured: dict = {"code": None, "state": None, "error": None}
_done_event = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    """Captura GET /callback?code=...&state=... y guarda el code."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return
        qs = parse_qs(parsed.query)
        code = (qs.get("code") or [None])[0]
        state = (qs.get("state") or [None])[0]
        error = (qs.get("error") or [None])[0]

        if error:
            _captured["error"] = error
            html = (
                "<!doctype html><html><body style='font-family:sans-serif;"
                "max-width:600px;margin:80px auto;text-align:center;'>"
                "<h1 style='color:#dc2626;'>Spotify rechazo la autorizacion</h1>"
                f"<p>Error: <code>{error}</code></p>"
                "<p>Podes cerrar esta pestana y reintentar desde Jarvis.</p>"
                "</body></html>"
            )
        elif code:
            _captured["code"] = code
            _captured["state"] = state
            html = (
                "<!doctype html><html><body style='font-family:sans-serif;"
                "max-width:600px;margin:80px auto;text-align:center;background:#0a0f17;"
                "color:#c8fafc;'>"
                "<h1 style='color:#7ff4f8;'>Spotify autorizado</h1>"
                "<p>Jarvis ya tiene acceso a tu biblioteca.</p>"
                "<p style='color:#7aa3ab;'>Podes cerrar esta pestana.</p>"
                "</body></html>"
            )
        else:
            _captured["error"] = "no_code_in_callback"
            html = "<h1>Callback sin code ni error. Revisa el log.</h1>"

        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _done_event.set()

    def log_message(self, format, *args) -> None:
        # silenciar el log default del BaseHTTPRequestHandler
        pass


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from spotipy.oauth2 import SpotifyOAuth

    scope = (
        "user-read-playback-state user-modify-playback-state "
        "user-read-currently-playing user-library-read playlist-read-private"
    )
    cache_path = os.environ.get("JARVIS_SPOTIFY_CACHE_PATH", "data/spotify/.cache")
    cache_abs = Path(cache_path)
    if not cache_abs.is_absolute():
        cache_abs = ROOT / cache_abs
    cache_abs.parent.mkdir(parents=True, exist_ok=True)

    auth = SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        scope=scope,
        cache_path=str(cache_abs),
        open_browser=False,
    )

    # Imprimir el authorize URL para que Isaac lo abra
    auth_url = auth.get_authorize_url()
    print("=" * 70)
    print("Abri este URL en tu navegador para autorizar Jarvis en Spotify:")
    print()
    print(auth_url)
    print()
    print("Cuando clickees Authorize, este servidor capturara el redirect")
    print("automaticamente. Timeout: 5 minutos.")
    print("=" * 70)

    server = HTTPServer(("127.0.0.1", 8888), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        if not _done_event.wait(timeout=TIMEOUT_S):
            print(f"\n[FAIL] Timeout {TIMEOUT_S}s sin recibir callback. Reintenta.")
            return 1
    finally:
        server.shutdown()

    if _captured["error"]:
        print(f"\n[FAIL] Spotify rechazo la autorizacion: {_captured['error']}")
        return 2

    code = _captured["code"]
    if not code:
        print("\n[FAIL] No se obtuvo code del callback.")
        return 3

    print("\n[OK] Code recibido, intercambiando por access_token...")
    try:
        token = auth.get_access_token(code=code, as_dict=True, check_cache=False)
    except Exception as exc:
        print(f"\n[FAIL] Intercambio code->token fallo: {type(exc).__name__}: {exc}")
        return 4

    if not token:
        print("\n[FAIL] Spotify no devolvio token.")
        return 5

    scopes = (token.get("scope") or "").split()
    print(f"\n[OK] Token guardado en {cache_abs}")
    print(f"[OK] Scopes autorizados ({len(scopes)}):")
    for s in scopes:
        marker = " <-- requerido para 'Tus me gusta'" if s == "user-library-read" else ""
        print(f"        - {s}{marker}")

    if "user-library-read" not in scopes:
        print("\n[AVISO] El scope user-library-read NO quedo. La biblioteca no funcionara.")
        return 6

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
