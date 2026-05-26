"""
actions/spotify_library.py - Cache local de "Tus me gusta" + fuzzy match.

Diseno:
  - Cache JSON en data/spotify/library.json con la biblioteca completa de
    Liked Songs del usuario. Se refresca on-demand o si el cache supera
    `MAX_AGE_HOURS`.
  - Search local con scoring custom (ver `score_match`): Jarvis puede
    reconocer canciones por fragmentos de titulo, artista, o ambos sin
    pegarle a la API en cada query.
  - Sin dependencia de rapidfuzz (que no esta pinneado): usa difflib +
    contains-match. Mas que suficiente para 1000-5000 canciones.

Patron de uso:
    library = LibraryCache(controller=sp_ctrl)
    library.ensure_loaded()      # carga JSON o refresca si stale
    matches = library.search("daft punk get lucky", limit=5)
    if matches and matches[0].score >= 0.7:
        ctrl.play_uri(matches[0].track.uri)
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = ROOT / "data" / "spotify" / "library.json"
MAX_AGE_HOURS = 24            # refresca el cache si tiene mas de 24h
PAGE_LIMIT = 50               # Spotify maximo por request
MAX_PAGES = 200               # tope defensivo: 200 * 50 = 10_000 tracks


# =====================================================================
# DATA MODEL
# =====================================================================

@dataclass(frozen=True)
class LikedTrack:
    """Representacion compacta de una cancion de Liked Songs.

    Solo guardamos lo que necesitamos para mostrar y hacer match. No
    queremos un dump completo de la API (popularity, available_markets,
    etc.) porque hincharia el JSON innecesariamente.
    """
    uri: str
    name: str
    artists: tuple[str, ...]
    album: str
    added_at: str            # ISO timestamp del momento que lo "likeo"
    duration_ms: int = 0

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists) if self.artists else ""

    @property
    def label(self) -> str:
        """Etiqueta humana corta: 'Cancion - Artistas'."""
        if self.artist_str:
            return f"{self.name} - {self.artist_str}"
        return self.name

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["artists"] = list(self.artists)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LikedTrack":
        return cls(
            uri=d["uri"],
            name=d["name"],
            artists=tuple(d.get("artists", [])),
            album=d.get("album", ""),
            added_at=d.get("added_at", ""),
            duration_ms=int(d.get("duration_ms", 0) or 0),
        )

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "LikedTrack":
        """Construye desde una entry de current_user_saved_tracks()."""
        track = item.get("track") or {}
        artists = tuple(
            (a.get("name") or "").strip()
            for a in (track.get("artists") or [])
            if a.get("name")
        )
        album = ((track.get("album") or {}).get("name") or "").strip()
        return cls(
            uri=track.get("uri", ""),
            name=track.get("name", "").strip(),
            artists=artists,
            album=album,
            added_at=item.get("added_at", ""),
            duration_ms=int(track.get("duration_ms", 0) or 0),
        )


@dataclass
class Match:
    """Resultado de un search local con score [0, 1]."""
    track: LikedTrack
    score: float
    matched_on: str           # 'name', 'artist', 'name+artist', 'album'


# =====================================================================
# SCORING — DESIGN DECISION (Isaac escribe esto)
# =====================================================================

def score_match(query: str, track: LikedTrack) -> tuple[float, str]:
    """Calcula un score 0..1 de cuanto matchea `query` con `track`.

    Devuelve (score, matched_on) donde matched_on indica el campo ganador:
    'name_exact' | 'artist_exact' | 'name' | 'artist' | 'tokens' |
    'album' | 'fuzzy'.

    --------------------------------------------------------------------
    UX DECISIONS (Isaac 2026-05-17):
      - Pide canciones por TITULO o ARTISTA principalmente.
      - Prefiere top 1 sin preguntar (velocidad).
    Por eso ambos campos tienen peso alto y el matching de artist se
    hace PER-ARTIST (no diluido por featurings). Token matching cubre
    el caso mixto "daft punk lucky" en cualquier orden.
    --------------------------------------------------------------------
    """
    q = (query or "").strip().lower()
    if not q:
        return 0.0, ""

    name_low = track.name.lower()
    album_low = track.album.lower()

    best_score = 0.0
    best_field = ""

    # === TITULO ===
    # Exacto: top absoluto. Sin chequear nada mas.
    if q == name_low:
        return 1.0, "name_exact"
    # Substring: 0.75-1.0 segun coverage del titulo
    if q in name_low:
        coverage = len(q) / max(len(name_low), 1)
        score = 0.75 + 0.25 * coverage
        if score > best_score:
            best_score, best_field = score, "name"

    # === ARTISTA (per-artist, sin diluir por colabos) ===
    # Si "Bad Bunny" matchea EL artista "Bad Bunny", score alto aunque
    # la canción tenga 3 features. Esto era el bug en la version A.
    for artist in track.artists:
        a_low = artist.lower()
        if not a_low:
            continue
        if q == a_low:
            # Match exacto al nombre completo del artista
            if 0.95 > best_score:
                best_score, best_field = 0.95, "artist_exact"
        elif q in a_low:
            coverage = len(q) / max(len(a_low), 1)
            score = 0.80 + 0.15 * coverage
            if score > best_score:
                best_score, best_field = score, "artist"

    # === TOKENS (multi-palabra en cualquier orden) ===
    # "daft punk lucky" debe matchear 'Get Lucky' de Daft Punk aunque
    # el orden no calce. Requerimos que TODOS los tokens aparezcan en
    # name+artist combinado.
    tokens = [t for t in q.split() if len(t) >= 2]
    if len(tokens) >= 2 and best_score < 0.90:
        haystack = f"{name_low} {track.artist_str.lower()}"
        if all(t in haystack for t in tokens):
            # Score proporcional a coverage total de los tokens
            total_len = sum(len(t) for t in tokens)
            coverage = total_len / max(len(haystack), 1)
            score = 0.82 + 0.13 * min(coverage * 2, 1.0)
            if score > best_score:
                best_score, best_field = score, "tokens"

    # === ALBUM (peso bajo, solo si nada mejor) ===
    if best_score < 0.6 and q in album_low:
        best_score, best_field = 0.55, "album"

    # === FUZZY FALLBACK ===
    # Solo si nada substring funciono. Util para typos de STT
    # ("daf punk" -> "daft punk").
    if best_score < 0.55:
        ratio_name = SequenceMatcher(None, q, name_low).ratio()
        ratio_artist = max(
            (SequenceMatcher(None, q, a.lower()).ratio() for a in track.artists),
            default=0.0,
        )
        ratio = max(ratio_name, ratio_artist * 0.9)
        if ratio > best_score:
            best_score, best_field = ratio, "fuzzy"

    return best_score, best_field


# =====================================================================
# CACHE
# =====================================================================

@dataclass
class LibrarySnapshot:
    tracks: list[LikedTrack] = field(default_factory=list)
    updated_at: str = ""        # ISO 8601
    source: str = "liked_songs"
    user: str = ""              # Spotify user id (informativo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "source": self.source,
            "user": self.user,
            "updated_at": self.updated_at,
            "count": len(self.tracks),
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LibrarySnapshot":
        return cls(
            tracks=[LikedTrack.from_dict(t) for t in d.get("tracks", [])],
            updated_at=d.get("updated_at", ""),
            source=d.get("source", "liked_songs"),
            user=d.get("user", ""),
        )


class LibraryCache:
    """Carga, persiste y consulta el cache local de Liked Songs."""

    def __init__(
        self,
        controller: Any | None = None,
        cache_path: Path | str | None = None,
        max_age_hours: float = MAX_AGE_HOURS,
    ) -> None:
        self.controller = controller
        self.cache_path = Path(
            cache_path
            or os.environ.get("JARVIS_SPOTIFY_LIBRARY_PATH", str(DEFAULT_CACHE_PATH))
        ).resolve()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_age_hours = float(max_age_hours)
        self._snapshot: LibrarySnapshot | None = None

    # ---- Persistence ----

    def load(self) -> bool:
        if not self.cache_path.exists():
            return False
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._snapshot = LibrarySnapshot.from_dict(data)
            return True
        except Exception:
            return False

    def save(self) -> None:
        if self._snapshot is None:
            return
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.cache_path)

    # ---- Freshness ----

    @property
    def is_loaded(self) -> bool:
        return self._snapshot is not None

    @property
    def age_hours(self) -> float | None:
        if self._snapshot is None or not self._snapshot.updated_at:
            return None
        try:
            dt = datetime.fromisoformat(self._snapshot.updated_at)
            return (datetime.utcnow() - dt).total_seconds() / 3600.0
        except Exception:
            return None

    @property
    def is_stale(self) -> bool:
        age = self.age_hours
        return age is None or age > self.max_age_hours

    @property
    def count(self) -> int:
        return 0 if self._snapshot is None else len(self._snapshot.tracks)

    # ---- Refresh from Spotify API ----

    def ensure_loaded(self, refresh_if_stale: bool = True) -> None:
        """Garantiza que el cache este cargado en memoria. Si esta vacio
        o stale (segun MAX_AGE_HOURS), pide refresh."""
        if not self.is_loaded:
            self.load()
        if not self.is_loaded or (refresh_if_stale and self.is_stale):
            self.refresh()

    def refresh(self) -> LibrarySnapshot:
        """Descarga todas las Liked Songs paginando hasta agotarlas.

        Requiere que `self.controller` tenga un `_sp` (cliente Spotipy)
        con scope `user-library-read`. Si no, lanza RuntimeError.
        """
        if self.controller is None or not hasattr(self.controller, "_sp"):
            raise RuntimeError(
                "LibraryCache.refresh() requiere un SpotifyController con _sp inicializado."
            )
        sp = self.controller._sp
        tracks: list[LikedTrack] = []
        offset = 0
        user_id = ""
        try:
            me = sp.current_user() or {}
            user_id = me.get("id", "") or ""
        except Exception:
            pass

        for page_idx in range(MAX_PAGES):
            try:
                resp = sp.current_user_saved_tracks(limit=PAGE_LIMIT, offset=offset)
            except Exception as exc:
                raise RuntimeError(f"Spotify API rechazo saved_tracks: {exc}") from exc
            items = (resp or {}).get("items") or []
            if not items:
                break
            for item in items:
                if not (item.get("track") or {}).get("uri"):
                    continue
                tracks.append(LikedTrack.from_api(item))
            if len(items) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT

        self._snapshot = LibrarySnapshot(
            tracks=tracks,
            updated_at=datetime.utcnow().isoformat(timespec="seconds"),
            source="liked_songs",
            user=user_id,
        )
        self.save()
        return self._snapshot

    # ---- Query API ----

    def search(self, query: str, limit: int = 5, min_score: float = 0.4) -> list[Match]:
        """Devuelve los `limit` mejores matches con score >= `min_score`.

        Ranking: score descendente, desempate por added_at desc
        (preferir lo mas reciente entre matches igual de buenos).
        """
        if self._snapshot is None:
            return []
        candidates: list[Match] = []
        for track in self._snapshot.tracks:
            score, field_name = score_match(query, track)
            if score >= min_score:
                candidates.append(Match(track=track, score=score, matched_on=field_name))

        candidates.sort(key=lambda m: (m.score, m.track.added_at), reverse=True)
        return candidates[:limit]

    def random_tracks(self, n: int = 1, seed: int | None = None) -> list[LikedTrack]:
        """Muestra aleatoria sin reposicion de la biblioteca."""
        if self._snapshot is None or not self._snapshot.tracks:
            return []
        rng = random.Random(seed) if seed is not None else random
        n = max(1, min(n, len(self._snapshot.tracks)))
        return rng.sample(self._snapshot.tracks, n)

    def recent(self, n: int = 10) -> list[LikedTrack]:
        """Los `n` mas recientemente agregados a Liked Songs."""
        if self._snapshot is None:
            return []
        ordered = sorted(self._snapshot.tracks, key=lambda t: t.added_at, reverse=True)
        return ordered[:n]

    def status(self) -> dict[str, Any]:
        """Resumen del cache para reporte conversacional."""
        return {
            "loaded": self.is_loaded,
            "count": self.count,
            "updated_at": self._snapshot.updated_at if self._snapshot else None,
            "age_hours": self.age_hours,
            "stale": self.is_stale,
            "cache_path": str(self.cache_path),
        }


# =====================================================================
# Smoke test
# =====================================================================

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")

    # Probar solo el scoring sin tocar Spotify
    fake = LikedTrack(
        uri="spotify:track:fake",
        name="Get Lucky",
        artists=("Daft Punk", "Pharrell Williams"),
        album="Random Access Memories",
        added_at="2024-01-01T00:00:00",
    )
    queries = [
        "get lucky",
        "daft punk",
        "daft punk lucky",
        "lucky daft",
        "pharrell",
        "random access",
        "girl",  # no deberia matchear
    ]
    print(f"Testing score_match against: {fake.label}\n")
    for q in queries:
        score, field_name = score_match(q, fake)
        print(f"  '{q:30s}' -> score={score:.3f}  field={field_name}")
