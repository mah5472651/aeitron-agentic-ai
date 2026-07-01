"""SQLite-backed local store for the Mythos MVP.

The production contract is Postgres. This local store mirrors the MVP tables so
the gateway, indexer, context builder, and tests can run immediately on a
developer machine without external services.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  repo_path TEXT NOT NULL,
  default_branch TEXT NOT NULL DEFAULT 'main',
  index_status TEXT NOT NULL DEFAULT 'not_indexed',
  last_indexed_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_files (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  language TEXT,
  content_hash TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  indexed_at REAL NOT NULL,
  UNIQUE(project_id, path)
);

CREATE TABLE IF NOT EXISTS code_chunks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  file_id TEXT NOT NULL REFERENCES workspace_files(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  language TEXT,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  symbol_name TEXT,
  kind TEXT NOT NULL,
  chunk_hash TEXT NOT NULL,
  token_count INTEGER NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  prompt TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  model_profile TEXT NOT NULL,
  confidence REAL,
  summary TEXT,
  started_at REAL,
  finished_at REAL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS patches (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
  status TEXT NOT NULL,
  diff TEXT NOT NULL,
  files_changed_json TEXT NOT NULL DEFAULT '[]',
  backup_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  applied_at REAL,
  rolled_back_at REAL
);

CREATE TABLE IF NOT EXISTS evaluations (
  id TEXT PRIMARY KEY,
  benchmark TEXT NOT NULL,
  model_profile TEXT NOT NULL,
  status TEXT NOT NULL,
  total INTEGER NOT NULL DEFAULT 0,
  resolved INTEGER NOT NULL DEFAULT 0,
  score REAL,
  report_path TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  started_at REAL,
  finished_at REAL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entries (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  source_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
  relevance REAL NOT NULL DEFAULT 0.5,
  success_rate REAL NOT NULL DEFAULT 0.5,
  usage_count INTEGER NOT NULL DEFAULT 0,
  last_used_at REAL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_candidates (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
  patch_id TEXT REFERENCES patches(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  prompt TEXT NOT NULL,
  chosen TEXT NOT NULL,
  verification_json TEXT NOT NULL DEFAULT '{}',
  score REAL,
  exported_at REAL,
  created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_files_project_path ON workspace_files(project_id, path);
CREATE INDEX IF NOT EXISTS idx_code_chunks_project_path ON code_chunks(project_id, path);
CREATE INDEX IF NOT EXISTS idx_code_chunks_project_symbol ON code_chunks(project_id, symbol_name);
"""


def default_store_path() -> Path:
    return Path(os.environ.get("MYTHOS_SQLITE_PATH", "artifacts/mythos/mythos.sqlite3"))


def now_unix() -> float:
    return time.time()


def new_id() -> str:
    return str(uuid.uuid4())


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class LocalStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is None:
                self._connection = sqlite3.connect(self.path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA foreign_keys = ON")
                self._connection.executescript(SQLITE_SCHEMA)
                self._connection.commit()
            return self._connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> "LocalStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def create_project(self, *, name: str, repo_path: str, default_branch: str = "main") -> dict[str, Any]:
        project_id = new_id()
        timestamp = now_unix()
        self.connection.execute(
            """
            INSERT INTO projects(id, name, repo_path, default_branch, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, name, str(Path(repo_path).resolve()), default_branch, timestamp, timestamp),
        )
        self.connection.commit()
        project = self.get_project(project_id)
        if project is None:
            raise RuntimeError("project insert did not round-trip")
        return project

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return row_to_dict(row)

    def update_project_index_status(self, project_id: str, status: str, *, indexed_at: float | None = None) -> None:
        self.connection.execute(
            """
            UPDATE projects
            SET index_status = ?, last_indexed_at = COALESCE(?, last_indexed_at), updated_at = ?
            WHERE id = ?
            """,
            (status, indexed_at, now_unix(), project_id),
        )
        self.connection.commit()

    def clear_index(self, project_id: str) -> None:
        self.connection.execute("DELETE FROM code_chunks WHERE project_id = ?", (project_id,))
        self.connection.execute("DELETE FROM workspace_files WHERE project_id = ?", (project_id,))
        self.connection.commit()

    def upsert_workspace_file(
        self,
        *,
        project_id: str,
        path: str,
        language: str | None,
        content_hash: str,
        size_bytes: int,
    ) -> str:
        file_id = new_id()
        timestamp = now_unix()
        self.connection.execute(
            """
            INSERT INTO workspace_files(id, project_id, path, language, content_hash, size_bytes, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, path) DO UPDATE SET
              language = excluded.language,
              content_hash = excluded.content_hash,
              size_bytes = excluded.size_bytes,
              indexed_at = excluded.indexed_at
            """,
            (file_id, project_id, path, language, content_hash, size_bytes, timestamp),
        )
        row = self.connection.execute(
            "SELECT id FROM workspace_files WHERE project_id = ? AND path = ?",
            (project_id, path),
        ).fetchone()
        self.connection.commit()
        if row is None:
            raise RuntimeError(f"workspace file upsert failed for {path}")
        return str(row["id"])

    def insert_code_chunks(self, chunks: Iterable[dict[str, Any]]) -> int:
        count = 0
        for chunk in chunks:
            self.connection.execute(
                """
                INSERT INTO code_chunks(
                  id, project_id, file_id, path, language, start_line, end_line,
                  symbol_name, kind, chunk_hash, token_count, content, metadata_json, indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk["id"],
                    chunk["project_id"],
                    chunk["file_id"],
                    chunk["path"],
                    chunk.get("language"),
                    chunk["start_line"],
                    chunk["end_line"],
                    chunk.get("symbol_name"),
                    chunk["kind"],
                    chunk["chunk_hash"],
                    chunk["token_count"],
                    chunk["content"],
                    json.dumps(chunk.get("metadata", {}), sort_keys=True),
                    now_unix(),
                ),
            )
            count += 1
        self.connection.commit()
        return count

    def list_chunks(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT c.*, f.content_hash AS file_hash
            FROM code_chunks c
            JOIN workspace_files f ON f.id = c.file_id
            WHERE c.project_id = ?
            ORDER BY c.path, c.start_line
            """,
            (project_id,),
        ).fetchall()
        return [self._chunk_row(row) for row in rows]

    def index_status(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")
        files = self.connection.execute(
            "SELECT COUNT(*) AS count FROM workspace_files WHERE project_id = ?",
            (project_id,),
        ).fetchone()["count"]
        chunks = self.connection.execute(
            "SELECT COUNT(*) AS count FROM code_chunks WHERE project_id = ?",
            (project_id,),
        ).fetchone()["count"]
        symbols = self.connection.execute(
            "SELECT COUNT(*) AS count FROM code_chunks WHERE project_id = ? AND symbol_name IS NOT NULL",
            (project_id,),
        ).fetchone()["count"]
        return {
            "project_id": project_id,
            "status": project["index_status"],
            "file_count": int(files),
            "chunk_count": int(chunks),
            "symbol_count": int(symbols),
            "last_indexed_at": project["last_indexed_at"],
        }

    def _chunk_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = row_to_dict(row) or {}
        metadata = data.pop("metadata_json", "{}")
        data["metadata"] = json.loads(metadata or "{}")
        return data
