from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

import httpx

from src.mythos.learning.data_engine import DataEngine, DataEngineConfig, FrontierStore
from src.mythos.learning.data_pipeline import DataPipelineConfig, run_data_pipeline
from src.mythos.learning.production_check import DataPlatformReadinessConfig, run_readiness_check
from src.mythos.learning.quality_inspector import inspect_clean_jsonl
from src.mythos.learning.review import review_tasks
from src.mythos.learning.run_plan import DataRunPlanConfig, build_data_run_plan
from src.mythos.learning.feedback import build_feedback_report
from src.mythos.learning.source_registry import SourceRegistry
from src.mythos.learning.web_ingest import SourceSpec
from src.mythos.learning.contamination import ContaminationDetector


def _page(title: str, link: str | None = None) -> str:
    body = (
        f"<html><body><h1>{title}</h1>"
        + ("<a href='/child.html'>child</a>" if link else "")
        + (" Defensive secure coding guidance for authentication, validation, "
           "CWE mitigation, safe parsing, regression testing, and patch verification. " * 18)
        + "</body></html>"
    )
    return body


class MythosDataEngineTest(unittest.IsolatedAsyncioTestCase):
    def test_source_registry_rejects_urls_outside_allowlist(self) -> None:
        registry = SourceRegistry(
            [
                SourceSpec(
                    name="bad",
                    urls=["https://evil.example.net/page"],
                    allowed_domains=["example.org"],
                    license="mit",
                    category="defensive_security",
                )
            ]
        )
        with self.assertRaises(ValueError):
            registry.validate()

    async def test_persistent_engine_crawls_discovers_deduplicates_and_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/robots.txt":
                    return httpx.Response(200, text="User-agent: *\nAllow: /\n")
                if request.url.path == "/seed.html":
                    return httpx.Response(200, text=_page("seed", "child"), headers={"content-type": "text/html"})
                if request.url.path == "/child.html":
                    return httpx.Response(200, text=_page("child"), headers={"content-type": "text/html"})
                return httpx.Response(404, text="missing")

            source = SourceSpec(
                name="offline-defensive-source",
                urls=["https://example.org/seed.html"],
                allowed_domains=["example.org"],
                license="mit",
                category="defensive_security",
            )
            config = DataEngineConfig(
                frontier_path=str(root / "frontier.sqlite3"),
                output_dir=str(root / "raw"),
                clean_output_dir=str(root / "clean"),
                max_docs=2,
                workers=2,
                max_depth=1,
                delay_seconds=0.0,
                shard_rows=1,
                request_timeout_seconds=5.0,
            )

            engine = DataEngine(config)
            try:
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.org") as client:
                    report = await engine.run([source], client=client)
            finally:
                engine.close()

            self.assertEqual(report.status, "complete")
            self.assertEqual(report.fetched, 2)
            self.assertEqual(report.accepted, 2)
            self.assertGreaterEqual(report.discovered, 1)
            self.assertEqual(report.failed, 0)
            self.assertTrue(list((root / "raw").glob("raw-*.jsonl")))
            self.assertEqual(len(list((root / "clean").glob("clean-*.jsonl"))), 2)

            store = FrontierStore(root / "frontier.sqlite3")
            try:
                stats = store.stats()
            finally:
                store.close()
            self.assertEqual(stats["urls_done"], 2)
            self.assertEqual(stats["documents_accepted"], 2)

    async def test_unified_data_pipeline_crawls_shards_and_runs_one_training_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = root / "sources.json"
            sources.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "offline-pipeline-source",
                                "urls": ["https://example.org/seed.html"],
                                "allowed_domains": ["example.org"],
                                "license": "mit",
                                "category": "agentic_coding",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/robots.txt":
                    return httpx.Response(200, text="User-agent: *\nAllow: /\n")
                if request.url.path == "/seed.html":
                    return httpx.Response(200, text=_page("pipeline", "child"), headers={"content-type": "text/html"})
                if request.url.path == "/child.html":
                    return httpx.Response(200, text=_page("pipeline child"), headers={"content-type": "text/html"})
                return httpx.Response(404, text="missing")

            config = DataPipelineConfig(
                sources_path=str(sources),
                work_dir=str(root / "pipeline"),
                max_docs=2,
                workers=2,
                max_depth=1,
                delay_seconds=0.0,
                shard_rows=1,
                vocab_size=1200,
                tokenizer_min_frequency=1,
                shard_token_count=128,
                sequence_length=16,
                validation_fraction=0.0,
                train_steps=1,
                train_device="cpu",
                train_batch_size=1,
                dtype="fp32",
                object_store_uri=f"local://{root / 'object-store'}",
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.org") as client:
                report = await run_data_pipeline(config, client=client)

            self.assertEqual(report.status, "complete")
            self.assertEqual(report.crawl["accepted"], 2)
            self.assertTrue(report.clean_files)
            self.assertTrue(Path(report.tokenizer_path).exists())
            self.assertTrue(report.shard_manifest["train_shards"])
            self.assertIsNotNone(report.task_report)
            self.assertGreater(report.task_report["extracted"], 0)
            self.assertIsNotNone(report.contamination_report)
            self.assertFalse(report.contamination_report["blocked"])
            self.assertIsNotNone(report.quality_report)
            self.assertGreater(report.quality_report["avg_quality_score"], 0.0)
            self.assertIsNotNone(report.source_quality_report)
            self.assertIsNotNone(report.review_report)
            self.assertGreaterEqual(report.review_report["approved"], 1)
            self.assertIsNotNone(report.feedback_report)
            self.assertTrue(Path(report.version_manifest_path).exists())
            self.assertTrue(Path(report.dashboard_path).exists())
            self.assertTrue(report.uploaded_objects)
            self.assertIsNotNone(report.training)
            self.assertEqual(report.training["status"], "passed")
            self.assertIsNotNone(report.checkpoint_eval)
            self.assertEqual(report.checkpoint_eval["status"], "passed")
            self.assertTrue(Path(root / "pipeline" / "reports" / "checkpoint_eval" / "checkpoint_eval_report.json").exists())

    def test_contamination_detector_blocks_known_benchmark_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clean = root / "clean.jsonl"
            clean.write_text(
                json.dumps({"text": "HumanEval check(candidate) leaked benchmark prompt", "license": "mit"}) + "\n",
                encoding="utf-8",
            )
            report = ContaminationDetector().scan_jsonl([clean])
            self.assertTrue(report.blocked)
            self.assertEqual(len(report.hits), 1)

    def test_production_readiness_blocks_local_only_configuration(self) -> None:
        report = run_readiness_check(
            DataPlatformReadinessConfig(
                sources_path="config/data_sources.production.sample.json",
                frontier_backend="sqlite",
                object_store_uri="local://artifacts/mythos/object-store",
                production_mode=True,
                worker_replicas=1,
                async_workers=8,
            )
        )
        self.assertEqual(report.status, "block")
        failed = {item.name for item in report.checks if item.status == "fail"}
        self.assertIn("distributed_frontier", failed)
        self.assertIn("object_storage", failed)

    def test_production_readiness_passes_distributed_configuration_contract(self) -> None:
        report = run_readiness_check(
            DataPlatformReadinessConfig(
                sources_path="config/data_sources.production.sample.json",
                frontier_backend="postgres",
                postgres_dsn="postgresql://user:pass@postgres:5432/mythos",
                object_store_uri="s3://mythos-datasets/pretraining",
                production_mode=True,
                worker_replicas=4,
                async_workers=32,
            )
        )
        self.assertEqual(report.status, "pass")

    def test_quality_inspector_and_run_plan_prepare_first_serious_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clean = root / "clean.jsonl"
            clean.write_text(
                json.dumps(
                    {
                        "source": "offline",
                        "license": "mit",
                        "text": "def secure_patch(): pass",
                        "quality": {
                            "quality_score": 0.8,
                            "labels": ["code", "defensive_security"],
                            "language_hint": "python",
                            "data_type": "code",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            quality = inspect_clean_jsonl([clean])
            self.assertEqual(quality.rows, 1)
            self.assertEqual(quality.by_language["python"], 1)

            plan = build_data_run_plan(
                DataRunPlanConfig(
                    source_paths=["config/data_sources.production.sample.json"],
                    output_dir=str(root / "plan"),
                    target_documents=1000,
                    target_days=1,
                    postgres_dsn="postgresql://user:pass@postgres:5432/mythos",
                    object_store_uri="s3://mythos-datasets/pretraining",
                    worker_replicas=4,
                    async_workers=32,
                )
            )
            self.assertEqual(plan.status, "ready")
            self.assertTrue(Path(plan.merged_registry_path).exists())
            self.assertTrue(Path(plan.output_dir, "run_plan.json").exists())

    def test_task_review_and_feedback_loop_reports_promotion_or_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tasks = root / "tasks.jsonl"
            tasks.write_text(
                json.dumps(
                    {
                        "task_id": "task-1",
                        "task_type": "security_patch_generation",
                        "prompt": "Using approved defensive source context, write a safe secure patch for authentication validation. " * 3,
                        "source_url": "https://example.org/secure",
                        "language": "python",
                        "metadata": {"source": "example"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            review = review_tasks(tasks, root / "decisions.jsonl", root / "approved.jsonl")
            self.assertEqual(review.approved, 1)
            quality = root / "quality.json"
            quality.write_text(json.dumps({"avg_quality_score": 0.8}), encoding="utf-8")
            review_json = root / "review.json"
            review_json.write_text(json.dumps(review.model_dump()), encoding="utf-8")
            feedback = build_feedback_report(quality_report_path=quality, review_report_path=review_json)
            self.assertEqual(feedback.recommendations[0].kind, "promotion")


if __name__ == "__main__":
    unittest.main()
