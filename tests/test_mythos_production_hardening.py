from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.mythos.db import LocalStore
from src.mythos.db.migration_runner import expand_psql_includes, load_migrations
from src.mythos.evaluation.benchmarks import BenchmarkHarness, built_in_security_tasks
from src.mythos.gateway import api as gateway_api
from src.mythos.identity.auth import AuthConfig, AuthError, validate_token_issue_request
from src.mythos.identity.quota import AsyncLocalQuotaStore, LocalQuotaStore
from src.mythos.indexing import LocalVectorIndex, RepositoryIndexer, VectorBackendConfig, create_vector_index, vector_capabilities
from src.mythos.learning.capacity import CapacityPlanConfig, build_capacity_plan
from src.mythos.memory import MemoryIngestRequest, UnifiedMemoryManager
from src.mythos.patches import PatchVerifyRequest
from src.mythos.patches.service import PatchService
from src.mythos.tools.sandbox import HardenedSandboxPolicy, SandboxRunRequest


class MythosProductionHardeningTest(unittest.TestCase):
    def test_migrations_load_and_expand_schema_include(self) -> None:
        migrations = load_migrations()
        self.assertTrue(migrations)
        expanded = expand_psql_includes(migrations[0].sql, base_dir=Path.cwd())
        self.assertIn("CREATE TABLE IF NOT EXISTS projects", expanded)

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
            with LocalStore(Path(db_dir) / "mythos.sqlite3") as store:
                project = store.create_project(name="prod", repo_path=str(workspace))
                RepositoryIndexer(store).index_project(project_id=project["id"])
                search = LocalVectorIndex(store).search(project_id=project["id"], query="login password", top_k=3)
                self.assertTrue(search.results)
                selected = create_vector_index(store, VectorBackendConfig(backend="local_hashing", dims=256))
                selected_search = selected.search(project_id=project["id"], query="login password", top_k=1)
                self.assertEqual(selected_search.backend, "local_hashing")
                self.assertEqual(selected_search.dims, 256)
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
        self.assertIn("mythos_http_requests_total", metrics.text)
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
        self.assertIn("kind: StatefulSet", postgres)
        self.assertIn("volumeClaimTemplates", postgres)
        self.assertIn("mythos-minio", minio)
        self.assertIn("mythos-datasets", minio)
        self.assertIn("mythos-data-worker", worker)
        self.assertIn("HorizontalPodAutoscaler", hpa)
        self.assertIn("NetworkPolicy", network)
        plan = build_capacity_plan(CapacityPlanConfig(target_documents=1_000_000, worker_replicas=8, async_workers_per_replica=16))
        self.assertGreater(plan.raw_storage_tb, 0.0)
        self.assertGreater(plan.recommended_worker_replicas_for_target_days, 0)


if __name__ == "__main__":
    unittest.main()
