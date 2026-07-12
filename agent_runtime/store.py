"""Transactional SQLite store for JARVIS missions."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .models import (
    ArtifactMeta,
    Mission,
    MissionEvent,
    MissionStatus,
    MissionStep,
    PlanContract,
    StepStatus,
    TERMINAL_MISSION_STATUSES,
    canonical_json,
    ensure_transition,
    make_mission_step,
    utc_now,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('version', '1');

CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    autonomy_level TEXT NOT NULL DEFAULT 'L1',
    root_path TEXT NOT NULL,
    max_steps INTEGER NOT NULL,
    deadline_seconds INTEGER NOT NULL,
    planner_model TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    final_summary TEXT,
    error_code TEXT,
    error_message TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS mission_steps (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    operation TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expected_evidence TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    artifact_id TEXT,
    verification_status TEXT,
    error_code TEXT,
    error_message TEXT,
    UNIQUE(mission_id, ordinal)
);

CREATE TABLE IF NOT EXISTS mission_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    step_id TEXT,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mission_artifacts (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    step_id TEXT,
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    redacted INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_missions_status_created ON missions(status, created_at);
CREATE INDEX IF NOT EXISTS idx_steps_mission_ordinal ON mission_steps(mission_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_events_mission_id ON mission_events(mission_id, id);
"""


class MissionNotFound(KeyError):
    pass


class MissionStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._write_lock, self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def create_mission(self, mission: Mission) -> MissionEvent:
        with self._write_lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO missions (id, goal, status, autonomy_level, root_path, max_steps, "
                "deadline_seconds, planner_model, cancel_requested, created_at, updated_at, "
                "started_at, finished_at, final_summary, error_code, error_message, schema_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mission.id, mission.goal, mission.status.value, mission.autonomy_level,
                    mission.root_path, mission.max_steps, mission.deadline_seconds,
                    mission.planner_model, int(mission.cancel_requested), mission.created_at,
                    mission.updated_at, mission.started_at, mission.finished_at,
                    mission.final_summary, mission.error_code, mission.error_message,
                    mission.schema_version,
                ),
            )
            return self._insert_event(
                conn,
                mission.id,
                "mission.created",
                payload={"status": mission.status.value, "autonomy_level": mission.autonomy_level},
            )

    def get_mission(self, mission_id: str) -> Mission:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        if row is None:
            raise MissionNotFound(mission_id)
        return _mission_from_row(row)

    def list_missions(
        self,
        *,
        limit: int = 20,
        statuses: Sequence[MissionStatus | str] | None = None,
    ) -> list[Mission]:
        limit = max(1, min(int(limit), 100))
        params: list[Any] = []
        where = ""
        if statuses:
            values = [MissionStatus(status).value for status in statuses]
            where = f"WHERE status IN ({','.join('?' for _ in values)})"
            params.extend(values)
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM missions {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_mission_from_row(row) for row in rows]

    def next_queued(self) -> Mission | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM missions WHERE status=? ORDER BY created_at ASC LIMIT 1",
                (MissionStatus.QUEUED.value,),
            ).fetchone()
        return _mission_from_row(row) if row is not None else None

    def nonterminal_missions(self) -> list[Mission]:
        terminal = [status.value for status in TERMINAL_MISSION_STATUSES]
        placeholders = ",".join("?" for _ in terminal)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM missions WHERE status NOT IN ({placeholders}) ORDER BY created_at ASC",
                terminal,
            ).fetchall()
        return [_mission_from_row(row) for row in rows]

    def transition(
        self,
        mission_id: str,
        target: MissionStatus | str,
        *,
        event_type: str,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        final_summary: str | None = None,
    ) -> MissionEvent:
        target = MissionStatus(target)
        now = utc_now()
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            if row is None:
                raise MissionNotFound(mission_id)
            current = MissionStatus(row["status"])
            ensure_transition(current, target)
            started_at = row["started_at"]
            if target in {MissionStatus.PLANNING, MissionStatus.RUNNING} and not started_at:
                started_at = now
            finished_at = now if target in TERMINAL_MISSION_STATUSES else row["finished_at"]
            conn.execute(
                "UPDATE missions SET status=?, updated_at=?, started_at=?, finished_at=?, "
                "error_code=?, error_message=?, final_summary=COALESCE(?, final_summary) WHERE id=?",
                (
                    target.value, now, started_at, finished_at, error_code, error_message,
                    final_summary, mission_id,
                ),
            )
            body = {"from": current.value, "to": target.value, **(payload or {})}
            if error_code:
                body["error_code"] = error_code
            return self._insert_event(conn, mission_id, event_type, level=level, payload=body)

    def append_event(
        self,
        mission_id: str,
        event_type: str,
        *,
        step_id: str | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> MissionEvent:
        with self._write_lock, self._conn() as conn:
            if conn.execute("SELECT 1 FROM missions WHERE id=?", (mission_id,)).fetchone() is None:
                raise MissionNotFound(mission_id)
            return self._insert_event(
                conn, mission_id, event_type, step_id=step_id, level=level, payload=payload,
            )

    def events(self, mission_id: str, *, limit: int = 50) -> list[MissionEvent]:
        limit = max(1, min(int(limit), 500))
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mission_events WHERE mission_id=? ORDER BY id DESC LIMIT ?",
                (mission_id, limit),
            ).fetchall()
        return [_event_from_row(row) for row in reversed(rows)]

    def save_plan(self, mission_id: str, plan: PlanContract) -> list[MissionStep]:
        steps = [make_mission_step(mission_id, ordinal, item) for ordinal, item in enumerate(plan.steps, 1)]
        with self._write_lock, self._conn() as conn:
            mission = conn.execute("SELECT max_steps FROM missions WHERE id=?", (mission_id,)).fetchone()
            if mission is None:
                raise MissionNotFound(mission_id)
            if len(steps) > int(mission["max_steps"]):
                raise ValueError("plan excede max_steps de la mision")
            existing = conn.execute("SELECT COUNT(*) FROM mission_steps WHERE mission_id=?", (mission_id,)).fetchone()[0]
            if existing:
                raise ValueError("la mision ya tiene un plan persistido")
            conn.executemany(
                "INSERT INTO mission_steps (id, mission_id, ordinal, operation, arguments_json, "
                "purpose, expected_evidence, status, idempotency_key, attempt_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        step.id, step.mission_id, step.ordinal, step.operation,
                        canonical_json(step.arguments), step.purpose, step.expected_evidence,
                        step.status.value, step.idempotency_key, step.attempt_count,
                    )
                    for step in steps
                ],
            )
            self._insert_event(
                conn,
                mission_id,
                "plan.validated",
                payload={"step_count": len(steps), "operations": [s.operation for s in steps]},
            )
        return steps

    def get_steps(self, mission_id: str) -> list[MissionStep]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mission_steps WHERE mission_id=? ORDER BY ordinal ASC",
                (mission_id,),
            ).fetchall()
        return [_step_from_row(row) for row in rows]

    def get_step(self, step_id: str) -> MissionStep:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM mission_steps WHERE id=?", (step_id,)).fetchone()
        if row is None:
            raise KeyError(step_id)
        return _step_from_row(row)

    def start_step(self, step_id: str) -> MissionEvent:
        now = utc_now()
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM mission_steps WHERE id=?", (step_id,)).fetchone()
            if row is None:
                raise KeyError(step_id)
            if StepStatus(row["status"]) != StepStatus.PENDING:
                raise ValueError(f"step no pendiente: {row['status']}")
            conn.execute(
                "UPDATE mission_steps SET status=?, attempt_count=attempt_count+1, started_at=?, "
                "finished_at=NULL, error_code=NULL, error_message=NULL WHERE id=?",
                (StepStatus.RUNNING.value, now, step_id),
            )
            return self._insert_event(
                conn, row["mission_id"], "step.started", step_id=step_id,
                payload={"ordinal": row["ordinal"], "operation": row["operation"]},
            )

    def finish_step(
        self,
        step_id: str,
        status: StepStatus | str,
        *,
        artifact_id: str | None = None,
        verification_status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> MissionEvent:
        status = StepStatus(status)
        if status not in {StepStatus.COMPLETED, StepStatus.CANCELLED, StepStatus.BLOCKED, StepStatus.FAILED}:
            raise ValueError("status final de step invalido")
        now = utc_now()
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM mission_steps WHERE id=?", (step_id,)).fetchone()
            if row is None:
                raise KeyError(step_id)
            if StepStatus(row["status"]) == StepStatus.COMPLETED and status == StepStatus.COMPLETED:
                return self._insert_event(
                    conn, row["mission_id"], "step.duplicate_ignored", step_id=step_id,
                    level="warn", payload={"idempotency_key": row["idempotency_key"]},
                )
            conn.execute(
                "UPDATE mission_steps SET status=?, finished_at=?, artifact_id=?, "
                "verification_status=?, error_code=?, error_message=? WHERE id=?",
                (
                    status.value, now, artifact_id, verification_status, error_code,
                    error_message, step_id,
                ),
            )
            event_type = "step.completed" if status == StepStatus.COMPLETED else f"step.{status.value}"
            body = {"status": status.value, **(payload or {})}
            if error_code:
                body["error_code"] = error_code
            return self._insert_event(
                conn, row["mission_id"], event_type, step_id=step_id,
                level="info" if status == StepStatus.COMPLETED else "error", payload=body,
            )

    def reset_step_for_recovery(self, step_id: str) -> None:
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM mission_steps WHERE id=?", (step_id,)).fetchone()
            if row is None:
                raise KeyError(step_id)
            conn.execute(
                "UPDATE mission_steps SET status=?, started_at=NULL, finished_at=NULL, "
                "error_code=NULL, error_message=NULL WHERE id=?",
                (StepStatus.PENDING.value, step_id),
            )

    def request_cancel(self, mission_id: str) -> Mission:
        with self._write_lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            if row is None:
                raise MissionNotFound(mission_id)
            current = MissionStatus(row["status"])
            if current in TERMINAL_MISSION_STATUSES:
                return _mission_from_row(row)
            if current == MissionStatus.CANCEL_REQUESTED:
                return _mission_from_row(row)
            ensure_transition(current, MissionStatus.CANCEL_REQUESTED)
            now = utc_now()
            conn.execute(
                "UPDATE missions SET status=?, cancel_requested=1, updated_at=? WHERE id=?",
                (MissionStatus.CANCEL_REQUESTED.value, now, mission_id),
            )
            self._insert_event(
                conn,
                mission_id,
                "mission.cancel_requested",
                level="warn",
                payload={"from": current.value, "to": MissionStatus.CANCEL_REQUESTED.value},
            )
        return self.get_mission(mission_id)

    def upsert_artifact(self, artifact: ArtifactMeta) -> None:
        with self._write_lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mission_artifacts "
                "(id, mission_id, step_id, kind, relative_path, sha256, size_bytes, redacted, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    artifact.id, artifact.mission_id, artifact.step_id, artifact.kind,
                    artifact.relative_path, artifact.sha256, artifact.size_bytes,
                    int(artifact.redacted), artifact.created_at,
                ),
            )

    def get_artifact(self, artifact_id: str) -> ArtifactMeta | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM mission_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            return None
        return ArtifactMeta(
            id=row["id"], mission_id=row["mission_id"], step_id=row["step_id"],
            kind=row["kind"], relative_path=row["relative_path"], sha256=row["sha256"],
            size_bytes=int(row["size_bytes"]), redacted=bool(row["redacted"]),
            created_at=row["created_at"],
        )

    def get_artifact_for_step(self, step_id: str) -> ArtifactMeta | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM mission_artifacts WHERE step_id=? ORDER BY created_at DESC LIMIT 1",
                (step_id,),
            ).fetchone()
        if row is None:
            return None
        return ArtifactMeta(
            id=row["id"], mission_id=row["mission_id"], step_id=row["step_id"],
            kind=row["kind"], relative_path=row["relative_path"], sha256=row["sha256"],
            size_bytes=int(row["size_bytes"]), redacted=bool(row["redacted"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        mission_id: str,
        event_type: str,
        *,
        step_id: str | None = None,
        level: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> MissionEvent:
        created_at = utc_now()
        clean_payload = payload or {}
        cursor = conn.execute(
            "INSERT INTO mission_events (mission_id, step_id, event_type, level, payload_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (mission_id, step_id, event_type, level, canonical_json(clean_payload), created_at),
        )
        return MissionEvent(
            id=int(cursor.lastrowid), mission_id=mission_id, step_id=step_id,
            event_type=event_type, level=level, payload=clean_payload, created_at=created_at,
        )


def _mission_from_row(row: sqlite3.Row) -> Mission:
    return Mission(
        id=row["id"], goal=row["goal"], status=MissionStatus(row["status"]),
        root_path=row["root_path"], max_steps=int(row["max_steps"]),
        deadline_seconds=int(row["deadline_seconds"]), autonomy_level=row["autonomy_level"],
        planner_model=row["planner_model"], cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        started_at=row["started_at"], finished_at=row["finished_at"],
        final_summary=row["final_summary"], error_code=row["error_code"],
        error_message=row["error_message"], schema_version=int(row["schema_version"]),
    )


def _step_from_row(row: sqlite3.Row) -> MissionStep:
    return MissionStep(
        id=row["id"], mission_id=row["mission_id"], ordinal=int(row["ordinal"]),
        operation=row["operation"], arguments=json.loads(row["arguments_json"]),
        purpose=row["purpose"], expected_evidence=row["expected_evidence"],
        status=StepStatus(row["status"]), idempotency_key=row["idempotency_key"],
        attempt_count=int(row["attempt_count"]), started_at=row["started_at"],
        finished_at=row["finished_at"], artifact_id=row["artifact_id"],
        verification_status=row["verification_status"], error_code=row["error_code"],
        error_message=row["error_message"],
    )


def _event_from_row(row: sqlite3.Row) -> MissionEvent:
    return MissionEvent(
        id=int(row["id"]), mission_id=row["mission_id"], step_id=row["step_id"],
        event_type=row["event_type"], level=row["level"],
        payload=json.loads(row["payload_json"]), created_at=row["created_at"],
    )
