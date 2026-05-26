"""
telemetry/persistence.py - Volcado a SQLite para historial cross-session.

Permite queries tipo:
  - "cuanto gaste esta semana?"
  - "que porcentaje de mis tokens fue cache hit en los ultimos 30 dias?"
  - "cual es mi sesion mas cara historicamente?"

Schema simple, append-only. La sesion vive en memoria (TokenTracker), este
modulo escribe periodicamente al disco para no perder datos en crash.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .tracker import TokenTracker

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    timestamp       REAL    NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_w_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_r_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL    NOT NULL DEFAULT 0.0,
    events_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_log(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_log(model);
"""


class UsagePersistence:
    """Vuelca snapshots del tracker a SQLite cada N segundos."""

    def __init__(self, db_path: Path | str, session_id: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self._last_by_model: dict[str, tuple[int, int, int, int, float, int]] = {}
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=2.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def flush_snapshot(self, tracker: TokenTracker) -> int:
        """Escribe deltas desde el ultimo flush. Returns rows inserted.

        El TokenTracker mantiene acumulados de sesion. Persistir snapshots
        completos y luego sumarlos sobrecontaria el gasto; por eso este modulo
        calcula el delta por modelo y guarda filas append-only de uso incremental.
        """
        snap = tracker.snapshot()
        rows = []
        ts = snap.snapshot_ts
        for model, u in snap.by_model.items():
            prev = self._last_by_model.get(model, (0, 0, 0, 0, 0.0, 0))
            delta = (
                u.input_tokens - prev[0],
                u.output_tokens - prev[1],
                u.cache_write_tokens - prev[2],
                u.cache_read_tokens - prev[3],
                u.cost_usd - prev[4],
                u.events - prev[5],
            )
            self._last_by_model[model] = (
                u.input_tokens,
                u.output_tokens,
                u.cache_write_tokens,
                u.cache_read_tokens,
                u.cost_usd,
                u.events,
            )
            if delta == (0, 0, 0, 0, 0.0, 0):
                continue
            rows.append((
                self.session_id, model, ts,
                delta[0], delta[1],
                delta[2], delta[3],
                delta[4], delta[5],
            ))
        if not rows:
            return 0
        with self._conn() as c:
            c.executemany(
                "INSERT INTO usage_log "
                "(session_id, model, timestamp, input_tokens, output_tokens, "
                "cache_w_tokens, cache_r_tokens, cost_usd, events_count) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def total_cost_window(self, hours_back: float, exclude_session_id: str | None = None) -> float:
        """Suma costos de las ultimas N horas a traves de TODAS las sesiones."""
        cutoff = time.time() - hours_back * 3600
        with self._conn() as c:
            if exclude_session_id:
                row = c.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log "
                    "WHERE timestamp >= ? AND session_id != ?",
                    (cutoff, exclude_session_id),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE timestamp >= ?",
                    (cutoff,),
                ).fetchone()
        return float(row[0])

    def cost_by_provider_window(
        self,
        hours_back: float,
        exclude_session_id: str | None = None,
    ) -> dict[str, float]:
        """Suma costos por proveedor para una ventana temporal."""
        cutoff = time.time() - hours_back * 3600
        params: tuple = (cutoff,)
        where = "timestamp >= ?"
        if exclude_session_id:
            where += " AND session_id != ?"
            params = (cutoff, exclude_session_id)
        out = {"gemini": 0.0, "claude": 0.0, "other": 0.0}
        with self._conn() as c:
            rows = c.execute(
                f"SELECT model, COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE {where} GROUP BY model",
                params,
            ).fetchall()
        for model, cost in rows:
            if str(model).startswith("gemini"):
                out["gemini"] += float(cost)
            elif str(model).startswith("claude"):
                out["claude"] += float(cost)
            else:
                out["other"] += float(cost)
        return out


# Smoke test
if __name__ == "__main__":
    import sys
    import tempfile

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    tr = TokenTracker()
    tr.record("claude-sonnet-4-6", input_tokens=100, output_tokens=200)
    tr.record("gemini-3.1-flash-live-preview:audio-out", output_tokens=500)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "test_usage.db"
        p = UsagePersistence(db, session_id="test-session-001")
        n = p.flush_snapshot(tr)
        print(f"Inserted {n} rows")

        cost_24h = p.total_cost_window(hours_back=24)
        print(f"Cost last 24h: ${cost_24h:.6f}")

        # Query directa con close explicito (Windows SQLite handle lock)
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute(
                "SELECT model, cost_usd, events_count FROM usage_log"
            ).fetchall()
            for model, cost, ev in rows:
                print(f"  {model}: ${cost:.6f} ({ev} events)")
        finally:
            conn.close()

    print("\n[OK] UsagePersistence smoke test passed")
