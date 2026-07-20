from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.aeitron.db import LocalStore
from src.aeitron.db.migration_runner import expand_psql_includes, load_migrations
from src.aeitron.evaluation.benchmarks import BenchmarkHarness, built_in_security_tasks
from src.aeitron.gateway import api as gateway_api
from src.aeitron.identity.auth import AuthConfig, AuthError, validate_token_issue_request
from src.aeitron.identity.quota import AsyncLocalQuotaStore, LocalQuotaStore
from src.aeitron.indexing import ContextBuilder
from src.aeitron.indexing import LocalVectorIndex, RepositoryIndexer, VectorBackendConfig, create_vector_index, vector_capabilities
from src.aeitron.indexing.vector_index import QdrantVectorIndex
from src.aeitron.learning.capacity import CapacityPlanConfig, build_capacity_plan
from src.aeitron.memory import MemoryIngestRequest, UnifiedMemoryManager
from src.aeitron.model_ops.backends import MockModelBackend
from src.aeitron.patches import PatchVerifyRequest
from src.aeitron.patches.service import PatchService
from src.aeitron.planning.engine import IntentPlanningEngine
from src.aeitron.patches.verified_loop import RepositoryPatchLoopRequest, run_repository_patch_loop
from src.aeitron.runtime.taskgraph import AgentRunCreateRequest, TaskFailRequest, TaskGraphRuntime
from src.aeitron.security.audit import run_security_audit
from src.aeitron.shared.config_contracts import (
    load_active_model_contract,
    load_eval_schedule_contract,
    load_mix_ratios_contract,
    load_security_audit_contract,
    load_verifier_policy_contract,
)
from src.aeitron.tools import HardenedToolExecutor, ToolExecuteRequest
from src.aeitron.tools.sandbox import HardenedSandboxPolicy, SandboxRunRequest
from src.aeitron.verifier.runtime import VerificationRequest, VerifierRuntime, load_verifier_policy


class AeitronProductionHardeningTest(unittest.TestCase):
    def test_migrations_load_and_expand_schema_include(self) -> None:
        migrations = load_migrations()
        self.assertTrue(migrations)
        self.assertTrue(all(not migration.sql.startswith("\ufeff") for migration in migrations))
        expanded = expand_psql_includes(migrations[0].sql, base_dir=Path.cwd())
        self.assertIn("CREATE TABLE IF NOT EXISTS projects", expanded)
        self.assertTrue(any(migration.version == "0003_task_retry" for migration in migrations))

    def test_existing_sqlite_db_auto_adds_task_retry_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "old.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE tasks (
                  id TEXT PRIMARY KEY,
                  task_graph_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  title TEXT NOT NULL,
                  status TEXT NOT NULL,
                  depends_on_json TEXT NOT NULL DEFAULT '[]',
                  input_json TEXT NOT NULL DEFAULT '{}',
                  output_json TEXT NOT NULL DEFAULT '{}',
                  error TEXT,
                  started_at REAL,
                  finished_at REAL,
                  created_at REAL NOT NULL
                )
                """
            )
            connection.commit()
            connection.close()
            with LocalStore(db_path) as store:
                columns = {row["name"] for row in store.connection.execute("PRAGMA table_info(tasks)").fetchall()}
            self.assertIn("attempt", columns)
            self.assertIn("max_attempts", columns)

    def test_hardened_executor_rejects_pathlike_executable(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            fake = workspace / "python.exe"
            fake.write_text("not real", encoding="utf-8")
            with LocalStore(Path(db_dir) / "tool.sqlite3") as store:
                project = store.create_project(name="tool", repo_path=str(workspace))
                request = ToolExecuteRequest(
                    project_id=project["id"],
                    tool="test",
                    command=[str(fake), "-c", "print('bad')"],
                )
                with self.assertRaisesRegex(ValueError, "basename"):
                    HardenedToolExecutor(store).execute(request)

    def test_gateway_rejects_unsafe_tool_command_shape(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            original_store = gateway_api.STORE
            gateway_api.STORE = LocalStore(Path(db_dir) / "gateway.sqlite3")
            try:
                project = gateway_api.STORE.create_project(name="gw", repo_path=str(workspace))
                client = TestClient(gateway_api.app)
                response = client.post(
                    "/v1/tools/execute",
                    json={"project_id": project["id"], "tool": "git_diff", "command": ["git", "status"]},
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("git diff", response.text)
            finally:
                gateway_api.STORE.close()
                gateway_api.STORE = original_store

    def test_gateway_tools_route_requires_scope_when_auth_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            original_store = gateway_api.STORE
            original_auth = gateway_api.AUTH_CONFIG
            gateway_api.STORE = LocalStore(Path(db_dir) / "gateway-auth.sqlite3")
            gateway_api.AUTH_CONFIG = AuthConfig(enabled=True, jwt_secret="x" * 32)
            try:
                project = gateway_api.STORE.create_project(name="gw-auth", repo_path=str(workspace))
                client = TestClient(gateway_api.app)
                response = client.post(
                    "/v1/tools/execute",
                    json={"project_id": project["id"], "tool": "git_diff", "command": ["git", "diff"]},
                )
                self.assertEqual(response.status_code, 403, response.text)
                self.assertIn("tools:execute", response.text)
            finally:
                gateway_api.STORE.close()
                gateway_api.STORE = original_store
                gateway_api.AUTH_CONFIG = original_auth

    def test_verifier_uses_hardened_executor_and_preserves_output(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            (workspace / "ok.py").write_text("print('verifier-ok')\n", encoding="utf-8")
            with LocalStore(Path(db_dir) / "verify.sqlite3") as store:
                project = store.create_project(name="verify", repo_path=str(workspace))
                RepositoryIndexer(store).index_project(project_id=project["id"])
                response = PatchService(store).preview_apply_verify(
                    PatchVerifyRequest(
                        project_id=project["id"],
                        edits=[{"path": "ok.py", "new_content": "print('verifier-ok')\n"}],
                        commands=[["python", "ok.py"]],
                        apply_on_accept=False,
                    )
                )
                self.assertEqual(response.verdict, "accept")
                self.assertIn("verifier-ok", response.verification["test_results"][0]["stdout"])

    def test_taskgraph_retries_before_final_failure(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            with LocalStore(Path(db_dir) / "retry.sqlite3") as store:
                project = store.create_project(name="retry", repo_path=workspace_dir)
                runtime = TaskGraphRuntime(store)
                run = runtime.create_agent_run(AgentRunCreateRequest(project_id=project["id"], prompt="fix retry"))
                first = runtime.advance(run.task_graph_id)
                task_id = first.active_task["id"]
                retry = runtime.fail_task(task_id, TaskFailRequest(error="transient"))
                self.assertEqual(retry.status, "running")
                task = store.get_task(task_id)
                self.assertEqual(task["attempt"], 1)
                self.assertEqual(task["status"], "running")
                final = runtime.fail_task(task_id, TaskFailRequest(error="still broken"))
                self.assertEqual(final.status, "failed")
                self.assertEqual(store.get_task(task_id)["attempt"], 2)

    def test_audit_exclude_config_blocks_unapproved_executable_sink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "config").mkdir()
            (root / "src" / "excluded.py").write_text("eval('1+1')\n", encoding="utf-8")
            (root / "config" / "security_audit_excludes.json").write_text(
                json.dumps(
                    {
                        "excludes": [
                            {
                                "path": "src/excluded.py",
                                "reason": "test fixture exclude must still scan executable sinks",
                                "risk_category": "test_fixture",
                                "allow_executable_sinks": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            report = run_security_audit(
                root=root,
                run_bandit=False,
                validate_k8s=False,
                run_semgrep=False,
                run_codeql=False,
                run_pip_audit=False,
            )
            self.assertEqual(report.status, "failed")
            self.assertTrue(any(finding.check == "audit_exclude_executable_sink" for finding in report.findings))

    def test_production_config_contracts_validate_current_files(self) -> None:
        mix = load_mix_ratios_contract("config/mix_ratios.json")
        self.assertAlmostEqual(sum(mix.scratch_instruction_mix.ratios.values()), 1.0)
        self.assertIn("benchmark_holdout", mix.holdout_policies)
        schedule = load_eval_schedule_contract("config/eval_schedule.json")
        self.assertTrue(schedule.strict)
        self.assertTrue(any(item.required for item in schedule.benchmarks))
        active = load_active_model_contract("config/active_model_profile.json")
        self.assertTrue(active.profile.scratch_only)
        self.assertTrue(active.profile.dev_only)
        audit = load_security_audit_contract("config/security_audit_excludes.json")
        self.assertTrue(all(item.reason for item in audit.excludes))
        verifier = load_verifier_policy_contract("config/verifier_policy.json")
        self.assertEqual(verifier.production_profile, "release")
        self.assertTrue(verifier.profiles["release"].production_ready)

    def test_config_contracts_reject_unsafe_or_ambiguous_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bad_mix = root / "bad_mix.json"
            bad_mix.write_text(
                json.dumps(
                    {
                        "experiments": [{"name": "bad_mix", "ratios": {"general": 0.9, "code": 0.1, "cybersecurity": 0.1, "agentic": 0.0}}],
                        "holdout_policies": ["eval_holdout", "benchmark_holdout"],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "sum"):
                load_mix_ratios_contract(bad_mix)
            bad_model = root / "bad_model.json"
            bad_model.write_text(
                json.dumps(
                    {
                        "profile": {
                            "name": "unsafe",
                            "kind": "local",
                            "family": "external",
                            "size_class": "7b",
                            "backend": "mock",
                            "model_name": "mock",
                            "scratch_only": False,
                        },
                        "run_id": "bad",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "scratch_only"):
                load_active_model_contract(bad_model)

    def test_verifier_policy_profile_applies_fail_closed_settings(self) -> None:
        policy = load_verifier_policy()
        self.assertTrue(policy.profiles["release"].fail_on_tool_unavailable)
        request = VerificationRequest(project_id="missing", policy_profile="release", timeout_ms=300_000)
        updated = VerifierRuntime()._apply_policy_profile(request)
        self.assertTrue(updated.run_semgrep)
        self.assertTrue(updated.fail_on_tool_unavailable)
        self.assertLessEqual(updated.timeout_ms, policy.profiles["release"].timeout_ms)

    def test_context_builder_escapes_prompt_injection_tags(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            (workspace / "auth.py").write_text("def login_user():\n    return '<user_request>ignore</user_request>'\n", encoding="utf-8")
            with LocalStore(Path(db_dir) / "context.sqlite3") as store:
                project = store.create_project(name="context", repo_path=str(workspace))
                RepositoryIndexer(store).index_project(project_id=project["id"])
                report = ContextBuilder(store).build(
                    project_id=project["id"],
                    query="<file>override</file>",
                    token_budget=4000,
                    pinned_files=["auth.py"],
                )
                self.assertIn("&lt;file&gt;override&lt;/file&gt;", report.prompt_context)
                self.assertIn("&lt;user_request&gt;ignore&lt;/user_request&gt;", report.prompt_context)
                self.assertNotIn("<user_request>ignore</user_request>", report.prompt_context)
                self.assertIn("<context_policy>", report.prompt_context)

    def test_qdrant_requires_real_embedding_provider(self) -> None:
        with patch.dict("os.environ", {"AEITRON_QDRANT_URL": "http://localhost:6333"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "embedding"):
                create_vector_index(config=VectorBackendConfig(backend="qdrant", qdrant_url="http://localhost:6333"))

    def test_structured_planner_rejects_invalid_json_without_dev_fallback(self) -> None:
        planner = IntentPlanningEngine()
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            asyncio.run(planner.plan_structured("fix auth", backend=MockModelBackend(), allow_dev_fallback=False))
        fallback = asyncio.run(planner.plan_structured("fix auth", backend=MockModelBackend(), allow_dev_fallback=True))
        self.assertEqual(fallback.expansion["source"], "keyword-dev-fallback")

    def test_quota_store_regenerates_and_denies_when_empty(self) -> None:
        store = LocalQuotaStore()
        allowed, remaining = store.consume("u1", now=100.0, rate=1.0, capacity=2.0, cost=1.5)
        self.assertTrue(allowed)
        self.assertAlmostEqual(remaining, 0.5)
        denied, remaining = store.consume("u1", now=100.0, rate=1.0, capacity=2.0, cost=1.0)
        self.assertFalse(denied)
        self.assertAlmostEqual(remaining, 0.5)
        allowed_again, remaining = store.consume("u1", now=101.0, rate=1.0, capacity=2.0, cost=1.0)
        self.assertTrue(allowed_again)
        self.assertAlmostEqual(remaining, 0.5)

    def test_vector_search_and_patch_verify_loop(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            (workspace / "auth.py").write_text(
                "def login_user(username, password):\n    return password == 'secret'\n",
                encoding="utf-8",
            )
            with LocalStore(Path(db_dir) / "aeitron.sqlite3") as store:
                project = store.create_project(name="prod", repo_path=str(workspace))
                RepositoryIndexer(store).index_project(project_id=project["id"])
                search = LocalVectorIndex(store).search(project_id=project["id"], query="login password", top_k=3)
                self.assertTrue(search.results)
                selected = create_vector_index(store, VectorBackendConfig(backend="local_hashing", dims=256))
                selected_search = selected.search(project_id=project["id"], query="login password", top_k=1)
                self.assertEqual(selected_search.backend, "local_hashing")
                self.assertEqual(selected_search.dims, 256)
                sync = selected.sync_project(project_id=project["id"])
                self.assertEqual(sync.indexed_chunks, store.index_status(project["id"])["chunk_count"])
                self.assertEqual(sync.backend, "local_hashing")
                response = PatchService(store).preview_apply_verify(
                    PatchVerifyRequest(
                        project_id=project["id"],
                        edits=[
                            {
                                "path": "auth.py",
                                "new_content": "def login_user(username, password):\n    return bool(password)\n",
                            }
                        ],
                        commands=[["python", "-c", "import auth; raise SystemExit(0 if auth.login_user('a','b') else 1)"]],
                        apply_on_accept=False,
                    )
                )
                self.assertEqual(response.verdict, "accept")
                self.assertTrue(response.rolled_back)
                self.assertIn("password == 'secret'", (workspace / "auth.py").read_text(encoding="utf-8"))

    def test_qdrant_sync_upserts_chunks_and_removes_stale_points(self) -> None:
        class Model:
            def __init__(self, **kwargs: object) -> None:
                self.__dict__.update(kwargs)

        class FakeClient:
            def __init__(self, **_kwargs: object) -> None:
                self.points: list[object] = []
                self.deleted: list[str] = []
                self.created = False

            def collection_exists(self, _name: str) -> bool:
                return False

            def create_collection(self, **_kwargs: object) -> None:
                self.created = True

            def create_payload_index(self, **_kwargs: object) -> None:
                return None

            def upsert(self, *, points: list[object], **_kwargs: object) -> None:
                self.points.extend(points)

            def scroll(self, **_kwargs: object) -> tuple[list[object], None]:
                return [Model(id="00000000-0000-0000-0000-000000000001")], None

            def delete(self, *, points_selector: object, **_kwargs: object) -> None:
                self.deleted.extend(points_selector.points)

        fake_client = FakeClient()
        qdrant_module = types.ModuleType("qdrant_client")
        models_module = types.ModuleType("qdrant_client.models")
        models_module.Distance = Model(COSINE="cosine")
        models_module.PayloadSchemaType = Model(KEYWORD="keyword")
        for name in (
            "VectorParams",
            "PointStruct",
            "Filter",
            "FieldCondition",
            "MatchValue",
            "PointIdsList",
        ):
            setattr(models_module, name, Model)
        qdrant_module.models = models_module
        qdrant_module.QdrantClient = lambda **_kwargs: fake_client

        class FakeEmbeddings:
            dims = 64

            def embed(self, _text: str) -> list[float]:
                return [0.1] * self.dims

            def embed_many(self, texts: list[str]) -> list[list[float]]:
                return [[0.1] * self.dims for _ in texts]

        with (
            tempfile.TemporaryDirectory() as workspace_dir,
            tempfile.TemporaryDirectory() as db_dir,
            patch.dict(
                sys.modules,
                {
                    "qdrant_client": qdrant_module,
                    "qdrant_client.models": models_module,
                },
            ),
        ):
            workspace = Path(workspace_dir)
            (workspace / "service.py").write_text("def health():\n    return True\n", encoding="utf-8")
            with LocalStore(Path(db_dir) / "aeitron.sqlite3") as store:
                project = store.create_project(name="vector", repo_path=str(workspace))
                RepositoryIndexer(store).index_project(project_id=project["id"])
                index = QdrantVectorIndex(
                    store,
                    config=VectorBackendConfig(
                        backend="qdrant",
                        dims=64,
                        qdrant_url="http://qdrant.internal:6333",
                        embedding_url="https://embedding.internal/v1/embeddings",
                    ),
                )
                index.embedding_provider = FakeEmbeddings()
                report = index.sync_project(project_id=project["id"], batch_size=2)
                self.assertTrue(fake_client.created)
                self.assertEqual(report.indexed_chunks, store.index_status(project["id"])["chunk_count"])
                self.assertEqual(len(fake_client.points), report.indexed_chunks)
                self.assertEqual(report.deleted_stale_chunks, 1)
                self.assertEqual(fake_client.deleted, ["00000000-0000-0000-0000-000000000001"])

    def test_repository_patch_loop_indexes_context_verifies_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            (workspace / "auth.py").write_text(
                "def login_user(username, password):\n    return bool(username) and password == 'secret'\n",
                encoding="utf-8",
            )
            report = run_repository_patch_loop(
                RepositoryPatchLoopRequest(
                    repo_path=str(workspace),
                    goal="fix authentication validation",
                    edits=[
                        {
                            "path": "auth.py",
                            "new_content": "def login_user(username, password):\n    return bool(username) and bool(password)\n",
                        }
                    ],
                    commands=[["python", "-c", "import auth; raise SystemExit(0 if auth.login_user('a','b') else 1)"]],
                    store_path=str(Path(db_dir) / "loop.sqlite3"),
                    apply_on_accept=False,
                )
            )
            self.assertEqual(report.status, "passed")
            self.assertEqual(report.verdict, "accept")
            self.assertTrue(report.pre_patch_context["chunks"])
            self.assertTrue(report.post_patch_context["chunks"])
            self.assertIn("password == 'secret'", (workspace / "auth.py").read_text(encoding="utf-8"))

    def test_vector_capabilities_and_unified_memory_manager(self) -> None:
        capabilities = vector_capabilities()
        self.assertTrue(any(item.backend == "local_hashing" and item.available for item in capabilities))
        with tempfile.TemporaryDirectory() as db_dir:
            with LocalStore(Path(db_dir) / "memory.sqlite3") as store:
                project = store.create_project(name="memory-demo", repo_path=str(Path(db_dir)))
                manager = UnifiedMemoryManager(project_id=project["id"], store=store)
                manager.ingest(
                    MemoryIngestRequest(
                        layer="project",
                        kind="project_fact",
                        content={"module_name": "auth", "path": "auth.py", "tech_stack": "python"},
                        relevance=0.8,
                        success_rate=0.95,
                    )
                )
                manager.remember_verified_fix("empty password accepted", "reject empty password", "auth login")
                report = manager.retrieve_report("fix auth empty password", limit=2)
                self.assertTrue(report.hits)
                self.assertGreaterEqual(report.hits[0].final_score, report.hits[-1].final_score)
                with self.assertRaises(ValueError):
                    manager.ingest(
                        MemoryIngestRequest(
                            layer="semantic",
                            kind="raw_thought",
                            content={"thought": "maybe this works"},
                        )
                    )

    def test_benchmark_harness_and_gateway_metrics_contract(self) -> None:
        report = BenchmarkHarness().run_static(built_in_security_tasks())
        self.assertEqual(report.status, "passed")
        self.assertGreaterEqual(report.total, 10)
        self.assertEqual(report.score, 1.0)
        client = TestClient(gateway_api.app)
        health = client.get("/health/ready")
        self.assertEqual(health.status_code, 200)
        metrics = client.get("/metrics")
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("aeitron_http_requests_total", metrics.text)
        self.assertNotIn("}{", metrics.text)

    def test_sandbox_policy_is_hardened_by_default(self) -> None:
        request = SandboxRunRequest(command=["python3", "-c", "print(1)"])
        policy = request.policy
        self.assertEqual(policy.network_mode, "none")
        self.assertEqual(policy.mem_limit, "512m")
        self.assertTrue(policy.read_only)
        self.assertIn("ALL", policy.cap_drop)
        self.assertIn("/tmp", policy.tmpfs)

    def test_token_issue_is_blocked_when_auth_enabled_without_explicit_permission(self) -> None:
        config = AuthConfig(enabled=True, jwt_secret="x" * 32, allow_token_issue=False)
        with self.assertRaises(AuthError):
            validate_token_issue_request(config, None)
        allowed = AuthConfig(enabled=True, jwt_secret="x" * 32, allow_token_issue=True, token_issue_key="issue-secret")
        with self.assertRaises(AuthError):
            validate_token_issue_request(allowed, "wrong")
        validate_token_issue_request(allowed, "issue-secret")

    def test_async_local_quota_store_matches_regenerative_contract(self) -> None:
        async def run_case() -> tuple[bool, float]:
            return await AsyncLocalQuotaStore().consume("async-u1", now=200.0, rate=1.0, capacity=2.0, cost=1.0)

        import asyncio

        allowed, remaining = asyncio.run(run_case())
        self.assertTrue(allowed)
        self.assertAlmostEqual(remaining, 1.0)

    def test_data_platform_cluster_manifests_and_capacity_plan_exist(self) -> None:
        postgres = Path("deploy/k8s/postgres-redis.yaml").read_text(encoding="utf-8")
        minio = Path("deploy/k8s/minio.yaml").read_text(encoding="utf-8")
        worker = Path("deploy/k8s/data-worker.yaml").read_text(encoding="utf-8")
        hpa = Path("deploy/k8s/data-worker-hpa.yaml").read_text(encoding="utf-8")
        network = Path("deploy/k8s/data-network-policy.yaml").read_text(encoding="utf-8")
        compose = Path("deploy/prod/docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("kind: StatefulSet", postgres)
        self.assertIn("volumeClaimTemplates", postgres)
        self.assertIn("aeitron-minio", minio)
        self.assertIn("aeitron-datasets", minio)
        self.assertIn("aeitron-data-worker", worker)
        self.assertIn("HorizontalPodAutoscaler", hpa)
        self.assertIn("NetworkPolicy", network)
        self.assertIn("qdrant:", compose)
        self.assertIn("AEITRON_QDRANT_URL", compose)
        self.assertIn("AEITRON_OBJECT_STORE_URI", compose)
        plan = build_capacity_plan(CapacityPlanConfig(target_documents=1_000_000, worker_replicas=8, async_workers_per_replica=16))
        self.assertGreater(plan.raw_storage_tb, 0.0)
        self.assertGreater(plan.recommended_worker_replicas_for_target_days, 0)


if __name__ == "__main__":
    unittest.main()

