from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

import httpx

from src.mythos.learning.data_engine import DataEngine, DataEngineConfig, FrontierStore, is_supported_text_response
from src.mythos.learning.data_engine import ShardedJsonlWriter
from src.mythos.learning.data_pipeline import DataPipelineConfig, PipelineRunLock, run_data_pipeline
from src.mythos.learning.production_check import DataPlatformReadinessConfig, run_readiness_check
from src.mythos.learning.quality_inspector import inspect_clean_jsonl
from src.mythos.learning.review import review_tasks
from src.mythos.learning.run_plan import DataRunPlanConfig, build_data_run_plan
from src.mythos.learning.feedback import build_feedback_report
from src.mythos.learning.source_registry import SourceRegistry
from src.mythos.learning.source_balancing import balance_clean_jsonl
from src.mythos.learning.web_ingest import SourceSpec
from src.mythos.learning.contamination import ContaminationDetector
from src.mythos.learning.quality import iter_jsonl
from src.mythos.learning.quality import DatasetQualityGate
from src.mythos.learning.task_extraction import extract_tasks
from src.mythos.evaluation.benchmarks import built_in_security_tasks


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
    def test_sharded_jsonl_writer_overwrites_stale_corrupt_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stale = root / "clean-000000.jsonl"
            stale.write_text('{"text": "unterminated', encoding="utf-8")
            writer = ShardedJsonlWriter(root, prefix="clean", rows_per_shard=10)
            try:
                writer.write({"text": "valid", "license": "mit"})
            finally:
                writer.close()
            rows = list(iter_jsonl(stale))
            self.assertEqual(rows, [{"license": "mit", "text": "valid"}])

    def test_iter_jsonl_reports_path_and_line_for_corrupt_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bad.jsonl"
            path.write_text('{"ok": true}\n{"bad": "unterminated\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid JSONL.*bad.jsonl.*line 2"):
                list(iter_jsonl(path))

    def test_iter_jsonl_keeps_unicode_line_separator_inside_json_string(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "valid.jsonl"
            path.write_text(json.dumps({"text": "left\u2028right"}, ensure_ascii=False) + "\n", encoding="utf-8")
            rows = list(iter_jsonl(path))
            self.assertEqual(rows[0]["text"], "left\u2028right")

    def test_binary_content_types_are_not_supported_for_corpus_text(self) -> None:
        self.assertFalse(is_supported_text_response("image/png", "https://example.org/a.png"))
        self.assertFalse(is_supported_text_response("", "https://example.org/a.pdf"))
        self.assertTrue(is_supported_text_response("text/html; charset=utf-8", "https://example.org/a"))
        self.assertTrue(is_supported_text_response("application/json", "https://example.org/a.json"))

    def test_pipeline_run_lock_blocks_concurrent_same_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / ".pipeline.lock"
            with PipelineRunLock(lock_path):
                with self.assertRaisesRegex(RuntimeError, "already locked"):
                    with PipelineRunLock(lock_path):
                        pass

    def test_source_balancing_caps_dominant_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clean = root / "clean.jsonl"
            rows = []
            for index in range(20):
                rows.append({"source": "dominant", "url": f"d/{index}", "text": "secure code", "quality": {"quality_score": 0.9}})
            for index in range(5):
                rows.append({"source": "small", "url": f"s/{index}", "text": "secure code", "quality": {"quality_score": 0.8}})
            clean.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            report = balance_clean_jsonl(
                input_paths=[clean],
                output_path=root / "balanced.jsonl",
                max_source_fraction=0.5,
                min_source_rows=1,
            )
            by_source = {item.source: item.output_rows for item in report.sources}
            self.assertEqual(by_source["dominant"], 5)
            self.assertEqual(by_source["small"], 5)

    def test_quality_gate_scores_security_code_with_components(self) -> None:
        gate = DatasetQualityGate()
        decision = gate.evaluate(
            {
                "license": "mit",
                "url": "https://example.org/app.py",
                "text": (
                    "def login(user_input):\n"
                    "    cursor.execute('SELECT * FROM users WHERE name=' + user_input)\n"
                    "    return validate(user_input)\n"
                    "This defensive security reference explains CWE-89 SQL injection mitigation and regression tests. "
                    * 6
                ),
            }
        )
        self.assertTrue(decision.accepted)
        self.assertIn("defensive_security", decision.labels)
        self.assertIn("code", decision.labels)
        self.assertEqual(decision.language_hint, "python")
        self.assertGreater(decision.component_scores["security_signal"], 0.0)
        self.assertGreater(decision.quality_score, 0.4)

    def test_task_extraction_creates_typed_security_and_test_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clean = root / "clean.jsonl"
            text = (
                "CWE-89 SQL injection patch guidance with regression tests.\n"
                "```python\n"
                "def find_user(name):\n"
                "    cursor.execute('SELECT * FROM users WHERE name=' + name)\n"
                "    return cursor.fetchone()\n"
                "```\n"
                "Use parameterized queries and add pytest regression coverage. " * 8
            )
            row = {
                "source": "offline",
                "url": "https://example.org/sql",
                "license": "mit",
                "text": text,
                "quality": {
                    "quality_score": 0.9,
                    "labels": ["defensive_security", "code", "tests"],
                    "language_hint": "python",
                    "data_type": "security_reference",
                },
            }
            clean.write_text(json.dumps(row) + "\n", encoding="utf-8")
            report = extract_tasks([clean], root / "tasks.jsonl", max_tasks=20)
            self.assertGreaterEqual(report.extracted, 3)
            self.assertIn("security_vulnerability_identification", report.by_type)
            self.assertIn("security_patch_generation", report.by_type)
            self.assertIn("regression_test_generation", report.by_type)
            tasks = list(iter_jsonl(root / "tasks.jsonl"))
            self.assertTrue(all(task["success_criteria"] for task in tasks))
            self.assertTrue(all(task["negative_constraints"] for task in tasks))

    def test_builtin_benchmark_suite_is_broader_than_smoke_static_checks(self) -> None:
        tasks = built_in_security_tasks()
        tags = {tag for task in tasks for tag in task.tags}
        self.assertGreaterEqual(len(tasks), 25)
        self.assertIn("solidity", tags)
        self.assertIn("kubernetes", tags)
        self.assertIn("github_actions", tags)

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

    async def test_engine_rejects_binary_responses_before_jsonl_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/robots.txt":
                    return httpx.Response(200, text="User-agent: *\nAllow: /\n")
                if request.url.path == "/image.png":
                    return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n", headers={"content-type": "image/png"})
                return httpx.Response(404, text="missing")

            source = SourceSpec(
                name="offline-binary-source",
                urls=["https://example.org/image.png"],
                allowed_domains=["example.org"],
                license="mit",
                category="agentic_coding",
            )
            config = DataEngineConfig(
                frontier_path=str(root / "frontier.sqlite3"),
                output_dir=str(root / "raw"),
                clean_output_dir=str(root / "clean"),
                max_docs=1,
                workers=1,
                delay_seconds=0.0,
            )
            engine = DataEngine(config)
            try:
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.org") as client:
                    report = await engine.run([source], client=client)
            finally:
                engine.close()
            self.assertEqual(report.fetched, 1)
            self.assertEqual(report.accepted, 0)
            self.assertEqual(report.rejected, 1)
            self.assertFalse(list((root / "clean").glob("clean-*.jsonl")))

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
            self.assertIsNotNone(report.source_balance_report)
            self.assertTrue(report.training_files)
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
