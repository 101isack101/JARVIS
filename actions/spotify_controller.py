"""
actions/spotify_controller.py - Control robusto de Spotify para Jarvis.

Usa Spotipy + SpotifyOAuth con cache local para que Jarvis pueda ejecutarse en
background sin abrir navegador en cada comando. Si Spotify Web API reporta que
no hay dispositivo activo, despierta la app nativa con `spotify:` y reintenta
el comando en background sin bloquear la conversacion de voz.
"""

from __future__ import annotations

import argparse
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_SCOPES = (
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    # Acceso a "Tus me gusta" (Liked Songs) + playlists privadas del usuario.
    # Necesario para que Jarvis reconozca la biblioteca personal en lugar de
    # buscar siempre globalmente. Si Isaac corrio --login antes de agregar
    # estos scopes, hay que correr `--login` de nuevo para refrescar el cache.
    "user-library-read",
    "playlist-read-private",
)

ROOT = Path(__file__).resolve().parent.parent


class SpotifyConfigError(RuntimeError):
    """Falta configuracion local para inicializar SpotifyOAuth."""


class SpotifyAuthRequired(RuntimeError):
    """No existe cache OAuth; se requiere login inicial interactivo."""

    def __init__(self, auth_url: str, cache_path: Path) -> None:
        self.auth_url = auth_url
        self.cache_path = cache_path
        super().__init__(
            "Spotify requiere login inicial. Abre la URL de autorizacion y "
            f"genera el cache OAuth en {cache_path}."
        )


class SpotifyCommandError(RuntimeError):
    """Error de Spotify no recuperable automaticamente."""


@dataclass(frozen=True)
class SpotifyActionResult:
    ok: bool
    action: str
    message: str
    retry_scheduled: bool = False
    data: dict[str, Any] | None = None

    def as_text(self) -> str:
        suffix = " Reintento programado en background." if self.retry_scheduled else ""
        return f"{self.message}{suffix}"


class SpotifyController:
    """Wrapper de Spotipy pensado para comandos conversacionales de Jarvis.

    Variables esperadas:
      - SPOTIFY_CLIENT_ID
      - SPOTIFY_CLIENT_SECRET
      - SPOTIFY_REDIRECT_URI, por ejemplo http://127.0.0.1:8888/callback
      - JARVIS_SPOTIFY_CACHE_PATH, opcional; default data/spotify/.cache

    En runtime normal `open_browser=False` evita prompts interactivos. El login
    inicial se hace una vez con `python -m actions.spotify_controller --login`.
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        cache_path: str | Path | None = None,
        scopes: Sequence[str] = DEFAULT_SCOPES,
        open_browser: bool = False,
        require_cached_token: bool = True,
        wake_retry_delay_s: float = 2.0,
    ) -> None:
        self.client_id = client_id or os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        self.redirect_uri = redirect_uri or os.environ.get(
            "SPOTIFY_REDIRECT_URI",
            "http://127.0.0.1:8888/callback",
        )
        self.cache_path = Path(
            cache_path
            or os.environ.get("JARVIS_SPOTIFY_CACHE_PATH", "data/spotify/.cache")
        ).resolve()
        self.scopes = tuple(scopes)
        self.open_browser = open_browser
        self.require_cached_token = require_cached_token
        self.wake_retry_delay_s = wake_retry_delay_s
        self._retry_lock = threading.Lock()
        self._pending_retries = 0
        self._volume_lock = threading.Lock()
        self._volume_ramp_generation = 0
        self._last_volume_percent: int | None = None

        if not self.client_id or not self.client_secret:
            raise SpotifyConfigError(
                "Configura SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET en .env."
            )

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._sp = self._build_client()

        # Lazy library cache (Liked Songs). Se crea al primer uso para no
        # bloquear el arranque con un network call si Isaac no usa el feature.
        self._library_cache = None  # type: ignore[assignment]

    @property
    def library(self):
        """LibraryCache lazy. Carga JSON desde disco al primer acceso; el
        consumidor decide si quiere `ensure_loaded()` (con refresh si stale)
        o `refresh()` explicito."""
        if self._library_cache is None:
            from actions.spotify_library import LibraryCache
            self._library_cache = LibraryCache(controller=self)
            self._library_cache.load()  # carga JSON si existe; no toca API
        return self._library_cache

    # ---- Public playback API ----

    def search_and_play(self, query: str) -> SpotifyActionResult:
        """Busca la mejor coincidencia y la reproduce en el dispositivo activo."""
        clean_query = (query or "").strip()
        if not clean_query:
            return SpotifyActionResult(
                ok=False,
                action="search_and_play",
                message="Necesito un query para buscar en Spotify.",
            )

        def op() -> SpotifyActionResult:
            match = self._best_search_match(clean_query)
            if match is None:
                return SpotifyActionResult(
                    ok=False,
                    action="search_and_play",
                    message=f"No encontre resultados para '{clean_query}'.",
                )
            device_id = self._active_or_first_device_id()
            if match["kind"] == "track":
                self._sp.start_playback(device_id=device_id, uris=[match["uri"]])
            else:
                self._sp.start_playback(device_id=device_id, context_uri=match["uri"])
            return SpotifyActionResult(
                ok=True,
                action="search_and_play",
                message=f"Reproduciendo {match['label']} en Spotify.",
                data=match,
            )

        return self._with_device_fallback("search_and_play", op)

    def pause(self) -> SpotifyActionResult:
        return self._with_device_fallback(
            "pause",
            lambda: self._simple_action(
                "pause",
                "Spotify pausado.",
                lambda: self._sp.pause_playback(device_id=self._active_or_first_device_id()),
            ),
        )

    def play(self) -> SpotifyActionResult:
        return self._with_device_fallback(
            "play",
            lambda: self._simple_action(
                "play",
                "Spotify reproduciendo.",
                lambda: self._sp.start_playback(device_id=self._active_or_first_device_id()),
            ),
        )

    def next(self) -> SpotifyActionResult:
        return self._with_device_fallback(
            "next",
            lambda: self._simple_action(
                "next",
                "Saltando a la siguiente cancion.",
                lambda: self._sp.next_track(device_id=self._active_or_first_device_id()),
            ),
        )

    def previous(self) -> SpotifyActionResult:
        return self._with_device_fallback(
            "previous",
            lambda: self._simple_action(
                "previous",
                "Volviendo a la cancion anterior.",
                lambda: self._sp.previous_track(device_id=self._active_or_first_device_id()),
            ),
        )

    def set_volume(
        self,
        percent: int,
        *,
        ramp: bool = True,
        duration_s: float = 0.9,
    ) -> SpotifyActionResult:
        """Ajusta el volumen de Spotify.

        Por default usa una rampa exponencial en background para evitar saltos
        bruscos y no bloquear el thread de Gemini/voz.
        """
        volume = max(0, min(int(percent), 100))
        if ramp:
            return self._with_device_fallback(
                "set_volume",
                lambda: self._schedule_volume_ramp(
                    target_percent=volume,
                    duration_s=duration_s,
                    message=f"Ajustando volumen de Spotify a {volume}%.",
                ),
            )
        return self._with_device_fallback(
            "set_volume",
            lambda: self._simple_action(
                "set_volume",
                f"Volumen de Spotify en {volume}%.",
                lambda: self._sp.volume(volume, device_id=self._active_or_first_device_id()),
            ),
        )

    def duck_audio(self, percent: int = 15) -> SpotifyActionResult:
        """Baja el volumen para reducir eco/VAD durante habla humana."""
        current = self._current_volume_percent()
        if current is not None and current > int(percent):
            self._last_volume_percent = current
        return self.set_volume(percent, ramp=True, duration_s=0.55)

    def restore_audio(self, percent: int | None = None) -> SpotifyActionResult:
        """Restaura Spotify despues del turno de voz de Isaac."""
        target = percent if percent is not None else self._last_volume_percent
        if target is None:
            target = 100
        return self.set_volume(int(target), ramp=True, duration_s=0.9)

    # ---- Library (Liked Songs) API ----

    def play_uri(self, uri: str, label: str = "") -> SpotifyActionResult:
        """Reproduce un URI especifico (track/playlist/album). Util tras
        resolver un match de la biblioteca local sin pasar por search global."""
        def op() -> SpotifyActionResult:
            device_id = self._active_or_first_device_id()
            if uri.startswith("spotify:track:"):
                self._sp.start_playback(device_id=device_id, uris=[uri])
            else:
                self._sp.start_playback(device_id=device_id, context_uri=uri)
            return SpotifyActionResult(
                ok=True,
                action="play_uri",
                message=f"Reproduciendo {label or uri}.",
                data={"uri": uri, "label": label},
            )
        return self._with_device_fallback("play_uri", op)

    def play_from_library(
        self,
        query: str,
        *,
        min_score: float = 0.6,
    ) -> SpotifyActionResult:
        """Busca `query` en Liked Songs local; si hay match decente, lo toca.

        Si no hay match >= min_score, devuelve ok=False con sugerencias
        para que Jarvis pueda preguntar o caer a search global.
        """
        clean = (query or "").strip()
        if not clean:
            return SpotifyActionResult(
                ok=False,
                action="play_from_library",
                message="Necesito un nombre o artista para buscar en tu biblioteca.",
            )
        lib = self.library
        try:
            lib.ensure_loaded()
        except Exception as exc:
            return SpotifyActionResult(
                ok=False,
                action="play_from_library",
                message=(
                    f"No pude cargar tu biblioteca: {type(exc).__name__}. "
                    "Probablemente falta scope user-library-read; corre "
                    "`python -m actions.spotify_controller --login` para refrescar."
                ),
            )
        matches = lib.search(clean, limit=5, min_score=min_score)
        if not matches:
            return SpotifyActionResult(
                ok=False,
                action="play_from_library",
                message=(
                    f"No encontre '{clean}' en tus me gusta. Tu biblioteca "
                    f"tiene {lib.count} canciones."
                ),
                data={"matches": []},
            )
        best = matches[0]
        result = self.play_uri(best.track.uri, label=best.track.label)
        # Aprovechar para devolver tambien alternativas (Jarvis decide si
        # mencionarlas: "puse X; tambien tenias Y, Z").
        result.data = {
            "match": best.track.to_dict(),
            "score": round(best.score, 3),
            "matched_on": best.matched_on,
            "alternatives": [
                {**m.track.to_dict(), "score": round(m.score, 3)}
                for m in matches[1:]
            ],
        }
        return result

    def play_random_liked(self, n: int = 1) -> SpotifyActionResult:
        """Toca una cancion aleatoria de Liked Songs.

        n > 1: pone la primera de inmediato; el resto van a la cola con
        add_to_queue (best-effort, sin bloquear si falla).
        """
        lib = self.library
        try:
            lib.ensure_loaded()
        except Exception as exc:
            return SpotifyActionResult(
                ok=False,
                action="play_random_liked",
                message=f"Biblioteca no disponible: {type(exc).__name__}: {exc}",
            )
        picks = lib.random_tracks(n=n)
        if not picks:
            return SpotifyActionResult(
                ok=False,
                action="play_random_liked",
                message="Tu biblioteca de Liked Songs esta vacia.",
            )
        first = picks[0]
        result = self.play_uri(first.uri, label=first.label)
        # Encolar el resto (best-effort)
        for track in picks[1:]:
            try:
                device_id = self._active_or_first_device_id()
                self._sp.add_to_queue(track.uri, device_id=device_id)
            except Exception:
                break
        result.action = "play_random_liked"
        result.message = f"Sonando: {first.label}. Aleatorio de tus {lib.count} likes."
        result.data = {
            "picked": [t.to_dict() for t in picks],
            "library_count": lib.count,
        }
        return result

    def refresh_library(self) -> SpotifyActionResult:
        """Re-descarga la biblioteca completa desde Spotify Web API."""
        lib = self.library
        try:
            snap = lib.refresh()
        except Exception as exc:
            return SpotifyActionResult(
                ok=False,
                action="refresh_library",
                message=(
                    f"No pude refrescar la biblioteca: {type(exc).__name__}: {exc}. "
                    "Si dice 'Insufficient client scope' corre "
                    "`python -m actions.spotify_controller --login` para refrescar."
                ),
            )
        return SpotifyActionResult(
            ok=True,
            action="refresh_library",
            message=f"Biblioteca actualizada: {len(snap.tracks)} canciones en tus me gusta.",
            data={"count": len(snap.tracks), "updated_at": snap.updated_at},
        )

    def library_status(self) -> SpotifyActionResult:
        """Reporte del cache local (cuantas canciones, edad, frescura)."""
        st = self.library.status()
        if not st["loaded"]:
            msg = "Aun no he cargado tu biblioteca. Pide refresh para descargarla."
        else:
            age = st.get("age_hours")
            age_str = f"{age:.1f}h" if isinstance(age, (int, float)) else "desconocida"
            stale_str = " (stale, conviene refresh)" if st["stale"] else ""
            msg = f"{st['count']} likes en cache, actualizado hace {age_str}{stale_str}."
        return SpotifyActionResult(
            ok=True,
            action="library_status",
            message=msg,
            data=st,
        )

    def library_top_recent(self, n: int = 10) -> SpotifyActionResult:
        """Lista las N canciones mas recientemente agregadas a Liked Songs.

        Util para 'que canciones agregue ultimamente' sin tocar la API: el
        cache local ya tiene el orden por added_at descendente disponible.
        """
        lib = self.library
        try:
            lib.ensure_loaded()
        except Exception as exc:
            return SpotifyActionResult(
                ok=False,
                action="library_top_recent",
                message=f"Biblioteca no disponible: {type(exc).__name__}: {exc}",
            )
        tracks = lib.recent(n=max(1, min(int(n), 50)))
        if not tracks:
            return SpotifyActionResult(
                ok=False,
                action="library_top_recent",
                message="Tu biblioteca esta vacia o no tiene timestamps de added_at.",
            )
        # Mensaje compacto pero util: "5 recientes: Track1 - Artist1; Track2 - Artist2; ..."
        listing = "; ".join(t.label for t in tracks[:5])
        suffix = f" (mostrando 5 de {len(tracks)})" if len(tracks) > 5 else ""
        return SpotifyActionResult(
            ok=True,
            action="library_top_recent",
            message=f"Recientes: {listing}{suffix}.",
            data={
                "tracks": [t.to_dict() for t in tracks],
                "count": len(tracks),
            },
        )

    # ---- OAuth helpers ----

    def _build_client(self):
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
        except ImportError as exc:
            raise SpotifyConfigError(
                "Falta instalar spotipy. Ejecuta pip install -r requirements.txt."
            ) from exc

        auth_manager = SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            scope=" ".join(self.scopes),
            cache_path=str(self.cache_path),
            open_browser=self.open_browser,
        )

        cached = auth_manager.cache_handler.get_cached_token()
        if self.require_cached_token and not cached:
            raise SpotifyAuthRequired(auth_manager.get_authorize_url(), self.cache_path)

        return spotipy.Spotify(
            auth_manager=auth_manager,
            requests_timeout=10,
            retries=2,
            status_retries=2,
        )

    # ---- Search and command execution ----

    def _best_search_match(self, query: str) -> dict[str, Any] | None:
        results = self._sp.search(
            q=query,
            type="track,album,artist,playlist",
            limit=1,
            market=os.environ.get("JARVIS_SPOTIFY_MARKET", "US"),
        )
        priority = (
            ("track", "tracks", self._track_label),
            ("playlist", "playlists", self._named_label),
            ("album", "albums", self._named_label),
            ("artist", "artists", self._named_label),
        )
        for kind, bucket, label_fn in priority:
            items = ((results.get(bucket) or {}).get("items") or [])
            if not items:
                continue
            item = items[0]
            return {
                "kind": kind,
                "uri": item["uri"],
                "label": label_fn(item),
                "name": item.get("name", ""),
            }
        return None

    def _active_or_first_device_id(self) -> str | None:
        """Devuelve un device_id usable y activa el primero disponible si hace falta.

        Spotify Web API a veces ve la app de escritorio pero la marca inactiva.
        En ese caso `start_playback` sin device_id responde 404; transferimos al
        dispositivo visible y despues enviamos comandos dirigidos a ese device.
        """
        payload = self._sp.devices() or {}
        devices = payload.get("devices") or []
        usable = [d for d in devices if not d.get("is_restricted")]
        active = next((d for d in usable if d.get("is_active") and d.get("id")), None)
        if active:
            return active["id"]
        candidate = next((d for d in usable if d.get("id")), None)
        if candidate:
            device_id = candidate["id"]
            try:
                self._sp.transfer_playback(device_id=device_id, force_play=False)
            except Exception:
                # Algunos clientes no aceptan transfer cuando no hay contexto
                # activo; aun asi pasar device_id al comando siguiente suele bastar.
                pass
            return device_id
        return None

    def _current_volume_percent(self) -> int | None:
        """Lee el volumen actual del dispositivo activo o del primero usable."""
        payload = self._sp.devices() or {}
        devices = payload.get("devices") or []
        active = next((d for d in devices if d.get("is_active")), None)
        device = active or next((d for d in devices if not d.get("is_restricted")), None)
        volume = (device or {}).get("volume_percent")
        return int(volume) if volume is not None else None

    def _schedule_volume_ramp(
        self,
        *,
        target_percent: int,
        duration_s: float,
        message: str,
    ) -> SpotifyActionResult:
        """Programa una rampa exponencial y retorna de inmediato."""
        device_id = self._active_or_first_device_id()
        start = self._current_volume_percent()
        if start is None:
            start = target_percent
        target = max(0, min(int(target_percent), 100))
        duration = max(0.0, float(duration_s or 0.0))
        if duration <= 0.05 or start == target:
            self._sp.volume(target, device_id=device_id)
            return SpotifyActionResult(ok=True, action="set_volume", message=message)

        with self._volume_lock:
            self._volume_ramp_generation += 1
            generation = self._volume_ramp_generation

        steps = max(6, min(24, int(duration / 0.06)))

        def ramp() -> None:
            try:
                sleep_s = duration / steps
                for idx in range(1, steps + 1):
                    with self._volume_lock:
                        if generation != self._volume_ramp_generation:
                            return
                    t = idx / steps
                    eased = self._exp_ease_in_out(t)
                    value = round(start + (target - start) * eased)
                    self._sp.volume(max(0, min(value, 100)), device_id=device_id)
                    time.sleep(sleep_s)
                for _ in range(3):
                    self._sp.volume(target, device_id=device_id)
                    time.sleep(0.08)
            except Exception:
                # Worker en background: no hay canal conversacional aqui.
                pass

        thread = threading.Thread(target=ramp, name="SpotifyVolumeRamp", daemon=True)
        thread.start()
        return SpotifyActionResult(
            ok=True,
            action="set_volume",
            message=message,
        )

    @staticmethod
    def _exp_ease_in_out(t: float) -> float:
        """Curva exponencial suave 0..1 para rampas de volumen."""
        t = max(0.0, min(float(t), 1.0))
        if t == 0.0 or t == 1.0:
            return t
        if t < 0.5:
            return math.pow(2.0, 20.0 * t - 10.0) / 2.0
        return (2.0 - math.pow(2.0, -20.0 * t + 10.0)) / 2.0

    @staticmethod
    def _track_label(item: dict[str, Any]) -> str:
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a)
        name = item.get("name", "la cancion")
        return f"'{name}' de {artists}" if artists else f"'{name}'"

    @staticmethod
    def _named_label(item: dict[str, Any]) -> str:
        return f"'{item.get('name', 'resultado')}'"

    @staticmethod
    def _simple_action(
        action: str,
        message: str,
        fn: Callable[[], Any],
    ) -> SpotifyActionResult:
        fn()
        return SpotifyActionResult(ok=True, action=action, message=message)

    def _with_device_fallback(
        self,
        action: str,
        operation: Callable[[], SpotifyActionResult],
    ) -> SpotifyActionResult:
        try:
            return operation()
        except Exception as exc:
            if self._is_no_active_device(exc):
                return self._wake_spotify_and_retry(action, operation)
            raise SpotifyCommandError(f"{action} fallo: {type(exc).__name__}: {exc}") from exc

    def _wake_spotify_and_retry(
        self,
        action: str,
        operation: Callable[[], SpotifyActionResult],
    ) -> SpotifyActionResult:
        try:
            os.startfile("spotify:")  # type: ignore[attr-defined]
        except Exception:
            # En Windows normal existe; si falla, aun programamos el retry.
            pass

        def retry() -> None:
            try:
                self._active_or_first_device_id()
                operation()
            except Exception:
                # El thread de retry no puede responderle a Gemini. Si Spotify
                # sigue sin dispositivo activo, evitamos stack traces ruidosos.
                pass
            finally:
                with self._retry_lock:
                    self._pending_retries = max(0, self._pending_retries - 1)

        with self._retry_lock:
            self._pending_retries += 1
        timer = threading.Timer(self.wake_retry_delay_s, retry)
        timer.daemon = True
        timer.start()

        return SpotifyActionResult(
            ok=False,
            action=action,
            message=(
                "No habia dispositivo activo en Spotify. Abri la app nativa "
                f"y voy a reintentar '{action}' en {self.wake_retry_delay_s:.0f}s."
            ),
            retry_scheduled=True,
        )

    @staticmethod
    def _is_no_active_device(exc: BaseException) -> bool:
        status = getattr(exc, "http_status", None)
        reason = str(getattr(exc, "reason", "") or "").lower()
        message = str(exc).lower()
        return (
            status == 404
            and (
                "no active device" in reason
                or "no active device" in message
                or "device not found" in reason
                or "device not found" in message
            )
        )


def _login() -> int:
    """Login interactivo one-shot para crear/actualizar data/spotify/.cache."""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    # Logger opcional: si telemetry/logger.py esta disponible, usalo. Si no,
    # caemos a print() porque este script puede correrse standalone antes de
    # que el resto del sistema este listo.
    try:
        from telemetry.logger import get_logger
        log = get_logger("spotify.login")
        emit = log.info
        emit_err = log.error
    except Exception:
        emit = print
        emit_err = print

    controller = SpotifyController(open_browser=True, require_cached_token=False)
    # Spotipy >= 2.24 deprecated as_dict=True; usar get_cached_token tras la
    # autorizacion interactiva. get_access_token(as_dict=True) sigue forzando
    # el flow OAuth si no hay cache valido — eso es lo que queremos en --login.
    token = controller._sp.auth_manager.get_access_token(as_dict=True)
    if not token:
        emit_err("No se pudo obtener token de Spotify.")
        return 1
    # Verificar que el cache nuevo tenga los scopes esperados
    cached = controller._sp.auth_manager.cache_handler.get_cached_token() or {}
    scopes = (cached.get("scope") or "").split()
    emit(f"Cache Spotify listo: {controller.cache_path}")
    emit(f"Scopes autorizados ({len(scopes)}): {' '.join(scopes)}")
    if "user-library-read" not in scopes:
        emit_err(
            "AVISO: el scope user-library-read no quedo autorizado. La "
            "biblioteca 'Tus me gusta' no sera accesible. Reintenta el login."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Spotify OAuth helper for Jarvis")
    parser.add_argument("--login", action="store_true", help="Crear cache OAuth inicial")
    args = parser.parse_args()
    if args.login:
        return _login()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
