"""SQLite storage (stdlib only): JSON documents plus a few indexed columns per table.

Tables: conversations, evaluations, appeals, jobs. data/db/ is gitignored. One connection per
operation and a process-wide lock, so the store is safe to share between the API and job threads.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "db" / "cqa.sqlite"

# table -> indexed columns (besides id, created_at, doc)
TABLES: dict[str, tuple[str, ...]] = {
    "conversations": ("group_id", "source"),
    "evaluations": ("conversation_id", "checklist_id", "status", "job_id"),
    "appeals": ("evaluation_id", "status"),
    "jobs": ("status",),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class NotFound(KeyError):
    pass


class Store:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as db:
            for table, cols in TABLES.items():
                extra = "".join(f", {c} TEXT" for c in cols)
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, created_at TEXT{extra}, doc TEXT NOT NULL)")
                for c in cols:
                    db.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_{c} ON {table}({c})")

    @contextmanager
    def _conn(self):
        """Connection that commits on success, rolls back on error and is always closed."""
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def put(self, table: str, doc: dict, doc_id: str | None = None) -> dict:
        """Insert or replace; indexed columns are taken from the document."""
        cols = TABLES[table]
        doc = dict(doc)
        doc["id"] = doc_id or doc.get("id") or new_id()
        doc.setdefault("created_at", now())
        values = [doc["id"], doc["created_at"]] + [None if doc.get(c) is None else str(doc.get(c)) for c in cols]
        placeholders = ", ".join("?" for _ in values)
        with self._lock, self._conn() as db:
            db.execute(f"INSERT OR REPLACE INTO {table} (id, created_at{''.join(', ' + c for c in cols)}, doc) "
                       f"VALUES ({placeholders}, ?)", values + [json.dumps(doc, ensure_ascii=False)])
        return doc

    def get(self, table: str, doc_id: str) -> dict:
        with self._lock, self._conn() as db:
            row = db.execute(f"SELECT doc FROM {table} WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            raise NotFound(f"{table}/{doc_id}")
        return json.loads(row["doc"])

    def update(self, table: str, doc_id: str, **fields: object) -> dict:
        with self._lock:
            doc = self.get(table, doc_id)
            doc.update(fields)
            doc["updated_at"] = now()
            return self.put(table, doc)

    def list(self, table: str, **where: object) -> list[dict]:
        cols = TABLES[table]
        unknown = set(where) - set(cols)
        if unknown:
            raise ValueError(f"cannot filter {table} by {sorted(unknown)}")
        clause = " AND ".join(f"{c} = ?" for c in where) or "1=1"
        with self._lock, self._conn() as db:
            rows = db.execute(f"SELECT doc FROM {table} WHERE {clause} ORDER BY created_at",
                              [str(v) for v in where.values()]).fetchall()
        return [json.loads(r["doc"]) for r in rows]
