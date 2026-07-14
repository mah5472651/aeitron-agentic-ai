"""SQLite-backed local store for the Aeitron MVP.

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

CREATE TABLE IF NOT EXISTS task_graphs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  graph_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  task_graph_id TEXT NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  depends_on_json TEXT NOT NULL DEFAULT '[]',
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT NOT NULL DEFAULT '{}',
  attempt INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  error TEXT,
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
CREATE INDEX IF NOT EXISTS idx_runs_project_status ON runs(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_graph_status ON tasks(task_graph_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_project_kind ON memory_entries(project_id, kind);
"""


def default_store_path() -> Path:
    return Path(os.environ.get("AEITRON_SQLITE_PATH", "artifacts/aeitron/aeitron.sqlite3"))


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
                self._ensure_runtime_columns(self._connection)
                self._connection.commit()
            return self._connection

    def _ensure_runtime_columns(self, connection: sqlite3.Connection) -> None:
        task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        if "attempt" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0")
        if "max_attempts" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 2")

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

    def create_session(self, *, project_id: str, title: str) -> dict[str, Any]:
        if self.get_project(project_id) is None:
            raise KeyError(f"unknown project: {project_id}")
        session_id = new_id()
        timestamp = now_unix()
        self.connection.execute(
            """
            INSERT INTO sessions(id, project_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, project_id, title, timestamp, timestamp),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        result = row_to_dict(row)
        if result is None:
            raise RuntimeError("session insert did not round-trip")
        return result

    def create_run(
        self,
        *,
        project_id: str,
        session_id: str | None,
        prompt: str,
        mode: str,
        model_profile: str,
        status: str = "queued",
    ) -> dict[str, Any]:
        if self.get_project(project_id) is None:
            raise KeyError(f"unknown project: {project_id}")
        run_id = new_id()
        timestamp = now_unix()
        self.connection.execute(
            """
            INSERT INTO runs(id, project_id, session_id, prompt, mode, status, model_profile, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, project_id, session_id, prompt, mode, status, model_profile, timestamp),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        result = row_to_dict(row)
        if result is None:
            raise RuntimeError("run insert did not round-trip")
        return result

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        summary: str | None = None,
        confidence: float | None = None,
        finished_at: float | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE runs
            SET status = ?,
                summary = COALESCE(?, summary),
                confidence = COALESCE(?, confidence),
                finished_at = COALESCE(?, finished_at)
            WHERE id = ?
            """,
            (status, summary, confidence, finished_at, run_id),
        )
        self.connection.commit()

    def create_task_graph(
        self,
        *,
        project_id: str,
        run_id: str,
        goal: str,
        status: str,
        graph: dict[str, Any],
    ) -> dict[str, Any]:
        graph_id = str(graph["task_graph_id"])
        timestamp = now_unix()
        self.connection.execute(
            """
            INSERT INTO task_graphs(id, project_id, run_id, goal, status, graph_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (graph_id, project_id, run_id, goal, status, json.dumps(graph, sort_keys=True), timestamp, timestamp),
        )
        for node in graph.get("nodes", []):
            self.connection.execute(
                """
                INSERT INTO tasks(
                  id, task_graph_id, run_id, kind, title, status, depends_on_json,
                  input_json, output_json, attempt, max_attempts, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node["node_id"],
                    graph_id,
                    run_id,
                    node["kind"],
                    node["title"],
                    node["status"],
                    json.dumps(node.get("depends_on", []), sort_keys=True),
                    json.dumps(node.get("inputs", {}), sort_keys=True),
                    json.dumps(node.get("outputs", {}), sort_keys=True),
                    int(node.get("attempt", 0)),
                    int(node.get("max_attempts", 2)),
                    timestamp,
                ),
            )
        self.connection.commit()
        result = self.get_task_graph(graph_id)
        if result is None:
            raise RuntimeError("task graph insert did not round-trip")
        return result

    def get_task_graph(self, task_graph_id: str) -> dict[str, Any] | None:
        graph_row = self.connection.execute("SELECT * FROM task_graphs WHERE id = ?", (task_graph_id,)).fetchone()
        graph = row_to_dict(graph_row)
        if graph is None:
            return None
        graph_payload = json.loads(graph["graph_json"])
        task_rows = self.connection.execute(
            "SELECT * FROM tasks WHERE task_graph_id = ? ORDER BY created_at, rowid",
            (task_graph_id,),
        ).fetchall()
        graph_payload["nodes"] = [
            {
                "node_id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "status": row["status"],
                "depends_on": json.loads(row["depends_on_json"]),
                "inputs": json.loads(row["input_json"]),
                "outputs": json.loads(row["output_json"]),
                "attempt": int(row["attempt"] or 0),
                "max_attempts": int(row["max_attempts"] or 2),
                "error": row["error"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
            }
            for row in task_rows
        ]
        return graph_payload

    def update_task_graph_status(self, task_graph_id: str, status: str) -> None:
        self.connection.execute(
            """
            UPDATE task_graphs
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now_unix(), task_graph_id),
        )
        self.connection.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        data = row_to_dict(row)
        if data is None:
            return None
        data["depends_on"] = json.loads(data.pop("depends_on_json") or "[]")
        data["inputs"] = json.loads(data.pop("input_json") or "{}")
        data["outputs"] = json.loads(data.pop("output_json") or "{}")
        data["attempt"] = int(data.get("attempt") or 0)
        data["max_attempts"] = int(data.get("max_attempts") or 2)
        return data

    def list_tasks(self, task_graph_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM tasks WHERE task_graph_id = ? ORDER BY created_at, rowid",
            (task_graph_id,),
        ).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            data = row_to_dict(row) or {}
            data["depends_on"] = json.loads(data.pop("depends_on_json") or "[]")
            data["inputs"] = json.loads(data.pop("input_json") or "{}")
            data["outputs"] = json.loads(data.pop("output_json") or "{}")
            data["attempt"] = int(data.get("attempt") or 0)
            data["max_attempts"] = int(data.get("max_attempts") or 2)
            tasks.append(data)
        return tasks

    def update_task_state(
        self,
        task_id: str,
        *,
        status: str,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        timestamp = now_unix()
        self.connection.execute(
            """
            UPDATE tasks
            SET status = ?,
                output_json = CASE WHEN ? IS NULL THEN output_json ELSE ? END,
                error = ?,
                started_at = CASE WHEN ? THEN COALESCE(started_at, ?) ELSE started_at END,
                finished_at = CASE WHEN ? THEN ? ELSE finished_at END
            WHERE id = ?
            """,
            (
                status,
                None if outputs is None else 1,
                json.dumps(outputs or {}, sort_keys=True),
                error,
                started,
                timestamp,
                finished,
                timestamp,
                task_id,
            ),
        )
        self.connection.commit()

    def update_task_attempt(self, task_id: str, *, attempt: int, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.connection.execute(
            """
            UPDATE tasks
            SET attempt = ?,
                output_json = CASE WHEN ? IS NULL THEN output_json ELSE ? END,
                error = ?
            WHERE id = ?
            """,
            (
                attempt,
                None if outputs is None else 1,
                json.dumps(outputs or {}, sort_keys=True),
                error,
                task_id,
            ),
        )
        self.connection.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row_to_dict(row)

    def create_patch_record(
        self,
        *,
        project_id: str,
        run_id: str | None,
        status: str,
        diff: str,
        files_changed: list[str],
        backup: dict[str, Any],
    ) -> dict[str, Any]:
        if self.get_project(project_id) is None:
            raise KeyError(f"unknown project: {project_id}")
        patch_id = new_id()
        timestamp = now_unix()
        self.connection.execute(
            """
            INSERT INTO patches(id, project_id, run_id, status, diff, files_changed_json, backup_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                patch_id,
                project_id,
                run_id,
                status,
                diff,
                json.dumps(files_changed, sort_keys=True),
                json.dumps(backup, sort_keys=True),
                timestamp,
            ),
        )
        self.connection.commit()
        patch = self.get_patch(patch_id)
        if patch is None:
            raise RuntimeError("patch insert did not round-trip")
        return patch

    def get_patch(self, patch_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM patches WHERE id = ?", (patch_id,)).fetchone()
        data = row_to_dict(row)
        if data is None:
            return None
        data["files_changed"] = json.loads(data.pop("files_changed_json") or "[]")
        data["backup"] = json.loads(data.pop("backup_json") or "{}")
        return data

    def update_patch_status(self, patch_id: str, status: str, *, applied: bool = False, rolled_back: bool = False) -> None:
        timestamp = now_unix()
        self.connection.execute(
            """
            UPDATE patches
            SET status = ?,
                applied_at = CASE WHEN ? THEN ? ELSE applied_at END,
                rolled_back_at = CASE WHEN ? THEN ? ELSE rolled_back_at END
            WHERE id = ?
            """,
            (status, applied, timestamp, rolled_back, timestamp, patch_id),
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

    def insert_memory_entry(
        self,
        *,
        project_id: str | None,
        kind: str,
        content: dict[str, Any],
        source_run_id: str | None = None,
        relevance: float = 0.5,
        success_rate: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry_id = new_id()
        timestamp = now_unix()
        self.connection.execute(
            """
            INSERT INTO memory_entries(
              id, project_id, kind, content, source_run_id, relevance,
              success_rate, usage_count, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                entry_id,
                project_id,
                kind,
                json.dumps(content, sort_keys=True),
                source_run_id,
                relevance,
                success_rate,
                json.dumps(metadata or {}, sort_keys=True),
                timestamp,
            ),
        )
        self.connection.commit()
        result = self.get_memory_entry(entry_id)
        if result is None:
            raise RuntimeError("memory entry insert did not round-trip")
        return result

    def get_memory_entry(self, entry_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM memory_entries WHERE id = ?", (entry_id,)).fetchone()
        data = row_to_dict(row)
        if data is None:
            return None
        data["content"] = json.loads(data["content"] or "{}")
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        return data

    def list_memory_entries(self, project_id: str | None = None, *, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("(project_id = ? OR project_id IS NULL)")
            params.append(project_id)
        query = "SELECT * FROM memory_entries WHERE (project_id = ? OR project_id IS NULL) ORDER BY created_at DESC" if clauses else "SELECT * FROM memory_entries ORDER BY created_at DESC"
        rows = self.connection.execute(query, params).fetchall()
        allowed_kinds = set(kinds or [])
        results: list[dict[str, Any]] = []
        for row in rows:
            data = row_to_dict(row) or {}
            if allowed_kinds and data.get("kind") not in allowed_kinds:
                continue
            data["content"] = json.loads(data["content"] or "{}")
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
            results.append(data)
        return results

    def mark_memory_used(self, entry_id: str) -> None:
        self.connection.execute(
            """
            UPDATE memory_entries
            SET usage_count = usage_count + 1, last_used_at = ?
            WHERE id = ?
            """,
            (now_unix(), entry_id),
        )
        self.connection.commit()

