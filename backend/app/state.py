"""SQLite-backed persistence for agent runs.

Every mutation is written through immediately so a run's full state (transcript,
status, pending action) survives a process restart and can be resumed by id.
This is deliberately not an in-memory store: the brief requires state that
survives across steps and is resumable, and an in-memory dict would lose
everything on restart.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import Run

DB_PATH = Path(__file__).resolve().parent.parent / "triage.db"


def _connect() -> sqlite3.Connection:
    # FastAPI runs sync endpoint functions in a threadpool, so the connection
    # must tolerate cross-thread use; a lock serializes access since sqlite3
    # connections aren't safe for concurrent use from multiple threads at once.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    return conn


class RunStore:
    def __init__(self) -> None:
        self._conn = _connect()
        self._lock = threading.Lock()

    def save(self, run: Run) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, data, created_at) VALUES (?, ?, ?)\n"
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (run.id, run.model_dump_json(), run.created_at),
            )
            self._conn.commit()

    def get(self, run_id: str) -> Optional[Run]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return Run.model_validate(json.loads(row[0]))

    def list(self) -> list[Run]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM runs ORDER BY created_at DESC"
            ).fetchall()
        return [Run.model_validate(json.loads(r[0])) for r in rows]


store = RunStore()
