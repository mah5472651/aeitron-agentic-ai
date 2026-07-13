-- Mythos MVP production Postgres schema.
-- Local development uses src/mythos/db/local_store.py with equivalent SQLite tables.

CREATE TABLE IF NOT EXISTS projects (
  id uuid PRIMARY KEY,
  name text NOT NULL,
  repo_path text NOT NULL,
  default_branch text NOT NULL DEFAULT 'main',
  index_status text NOT NULL DEFAULT 'not_indexed',
  last_indexed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
  prompt text NOT NULL,
  mode text NOT NULL,
  status text NOT NULL,
  model_profile text NOT NULL,
  confidence numeric(5,4),
  summary text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task_graphs (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id uuid REFERENCES runs(id) ON DELETE CASCADE,
  goal text NOT NULL,
  status text NOT NULL,
  graph_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
  id uuid PRIMARY KEY,
  task_graph_id uuid NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  kind text NOT NULL,
  title text NOT NULL,
  status text NOT NULL,
  depends_on uuid[] NOT NULL DEFAULT '{}',
  input_json jsonb NOT NULL DEFAULT '{}',
  output_json jsonb NOT NULL DEFAULT '{}',
  error text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_files (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path text NOT NULL,
  language text,
  content_hash text NOT NULL,
  size_bytes integer NOT NULL,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project_id, path)
);

CREATE TABLE IF NOT EXISTS code_chunks (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  file_id uuid NOT NULL REFERENCES workspace_files(id) ON DELETE CASCADE,
  path text NOT NULL,
  language text,
  start_line integer NOT NULL,
  end_line integer NOT NULL,
  symbol_name text,
  kind text NOT NULL,
  chunk_hash text NOT NULL,
  token_count integer NOT NULL,
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  indexed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patches (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id uuid REFERENCES runs(id) ON DELETE SET NULL,
  status text NOT NULL,
  diff text NOT NULL,
  files_changed text[] NOT NULL DEFAULT '{}',
  backup_json jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  applied_at timestamptz,
  rolled_back_at timestamptz
);

CREATE TABLE IF NOT EXISTS evaluations (
  id uuid PRIMARY KEY,
  benchmark text NOT NULL,
  model_profile text NOT NULL,
  status text NOT NULL,
  total integer NOT NULL DEFAULT 0,
  resolved integer NOT NULL DEFAULT 0,
  score numeric(8,5),
  report_path text,
  result_json jsonb NOT NULL DEFAULT '{}',
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_entries (
  id uuid PRIMARY KEY,
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
  kind text NOT NULL,
  content text NOT NULL,
  source_run_id uuid REFERENCES runs(id) ON DELETE SET NULL,
  relevance numeric(5,4) NOT NULL DEFAULT 0.5,
  success_rate numeric(5,4) NOT NULL DEFAULT 0.5,
  usage_count integer NOT NULL DEFAULT 0,
  last_used_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learning_candidates (
  id uuid PRIMARY KEY,
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
  run_id uuid REFERENCES runs(id) ON DELETE SET NULL,
  patch_id uuid REFERENCES patches(id) ON DELETE SET NULL,
  kind text NOT NULL,
  status text NOT NULL,
  prompt text NOT NULL,
  chosen text NOT NULL,
  verification_json jsonb NOT NULL DEFAULT '{}',
  score numeric(8,5),
  exported_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runs_project_status ON runs(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_graph_status ON tasks(task_graph_id, status);
CREATE INDEX IF NOT EXISTS idx_workspace_files_project_path ON workspace_files(project_id, path);
CREATE INDEX IF NOT EXISTS idx_code_chunks_project_path ON code_chunks(project_id, path);
CREATE INDEX IF NOT EXISTS idx_code_chunks_project_symbol ON code_chunks(project_id, symbol_name);
CREATE INDEX IF NOT EXISTS idx_memory_project_kind ON memory_entries(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_learning_status ON learning_candidates(status);
