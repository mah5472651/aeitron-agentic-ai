from __future__ import annotations

import tempfile
import unittest
import json
import shutil
import subprocess
from pathlib import Path

import httpx

from src.mythos.learning.benchmark_contamination_filter import filter_benchmark_contamination_jsonl
from src.mythos.learning.data_engine import DataEngine, DataEngineConfig, FrontierStore, is_supported_text_response
from src.mythos.learning.data_engine import ShardedJsonlWriter
from src.mythos.learning.data_pipeline import DataPipelineConfig, PipelineRunLock, run_data_pipeline
from src.mythos.learning.license_filter import filter_jsonl_by_license
from src.mythos.learning.near_dedup import deduplicate_jsonl
from src.mythos.learning.production_check import DataPlatformReadinessConfig, run_readiness_check
from src.mythos.learning.quality_inspector import inspect_clean_jsonl
from src.mythos.learning.repo_patch_extraction import extract_security_patch_tasks
from src.mythos.learning.resource_catalog import build_resource_catalog_report
from src.mythos.learning.review import review_tasks
from src.mythos.learning.run_plan import DataRunPlanConfig, build_data_run_plan
from src.mythos.learning.feedback import build_feedback_report
from src.mythos.learning.governance import GovernanceStore, HumanReviewItem, SourceApprovalRequest
from src.mythos.learning.source_budget import build_source_budget_plan
from src.mythos.learning.source_registry import SourceRegistry
from src.mythos.learning.source_balancing import balance_clean_jsonl
from src.mythos.learning.source_reputation import build_source_reputation_report
from src.mythos.learning.vulnerability_adapters import (
    CisaKevAdapter,
    GoVulnAdapter,
    NvdCveAdapter,
    VulnerabilityFetchConfig,
)
from src.mythos.learning.web_ingest import SourceSpec
from src.mythos.learning.contamination import ContaminationDetector
from src.mythos.learning.quality import iter_jsonl
from src.mythos.learning.quality import DatasetQualityGate
from src.mythos.learning.task_extraction import extract_tasks
from src.mythos.learning.training_data_gate import TrainingDataGateConfig, apply_training_data_gate
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

    def test_license_benchmark_and_near_dedup_filters_prepare_clean_training_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "raw.jsonl"
            rows = [
                {
                    "source": "good",
                    "license": "mit",
                    "text": "Secure authentication validation guidance with CWE mitigation and regression tests. " * 5,
                },
                {
                    "source": "bad-license",
                    "license": "proprietary",
                    "text": "Do not train on this.",
                },
                {
                    "source": "leak",
                    "license": "mit",
                    "text": "HumanEval canonical_solution should never enter pretraining.",
                },
                {
                    "source": "near",
                    "license": "mit",
                    "text": "Secure authentication validation guidance with CWE mitigation and regression test coverage. " * 5,
                },
            ]
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            license_report = filter_jsonl_by_license([source], root / "license.jsonl")
            self.assertEqual(license_report.accepted, 3)
            self.assertEqual(license_report.rejected, 1)
            benchmark_report = filter_benchmark_contamination_jsonl([root / "license.jsonl"], root / "benchmark.jsonl")
            self.assertEqual(benchmark_report.rejected, 1)
            dedup_report = deduplicate_jsonl([root / "benchmark.jsonl"], root / "dedup.jsonl", hamming_threshold=64)
            self.assertEqual(dedup_report.accepted, 1)
            self.assertEqual(dedup_report.near_duplicates, 1)

    def test_source_reputation_and_budget_promote_high_quality_security_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sources = root / "sources.json"
            sources.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "name": "good-security",
                                "urls": ["https://example.org/security"],
                                "allowed_domains": ["example.org"],
                                "license": "mit",
                                "category": "defensive_security",
                            },
                            {
                                "name": "weak-docs",
                                "urls": ["https://docs.example.org/page"],
                                "allowed_domains": ["docs.example.org"],
                                "license": "mit",
                                "category": "documentation",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            source_quality = root / "source_quality.json"
            source_quality.write_text(
                json.dumps(
                    {
                        "input_paths": ["clean.jsonl"],
                        "sources": [
                            {
                                "source": "good-security",
                                "rows": 100,
                                "avg_quality_score": 0.86,
                                "defensive_security_rows": 80,
                                "code_rows": 50,
                                "score": 0.95,
                                "action": "promote",
                            },
                            {
                                "source": "weak-docs",
                                "rows": 100,
                                "avg_quality_score": 0.42,
                                "defensive_security_rows": 1,
                                "code_rows": 0,
                                "score": 0.42,
                                "action": "demote",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            reputation = build_source_reputation_report(source_quality_report_path=source_quality)
            self.assertGreater(reputation.sources[0].reputation_score, reputation.sources[1].reputation_score)
            reputation_path = root / "reputation.json"
            reputation_path.write_text(json.dumps(reputation.model_dump()), encoding="utf-8")
            plan = build_source_budget_plan(
                sources_path=sources,
                reputation_report_path=reputation_path,
                target_total_docs=1000,
                min_docs_per_source=1,
            )
            budgets = {item.source: item.target_docs for item in plan.budgets}
            self.assertGreater(budgets["good-security"], budgets["weak-docs"])

    async def test_vulnerability_adapters_normalize_official_api_payloads(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if "known_exploited_vulnerabilities" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "vulnerabilities": [
                            {
                                "cveID": "CVE-2024-0001",
                                "vendorProject": "Example",
                                "product": "Widget",
                                "vulnerabilityName": "Input validation flaw",
                                "shortDescription": "Improper validation allows unsafe behavior.",
                                "requiredAction": "Apply vendor update.",
                                "dateAdded": "2024-01-01",
                            }
                        ]
                    },
                )
            if request.url.path.endswith("/cves/2.0"):
                return httpx.Response(
                    200,
                    json={
                        "vulnerabilities": [
                            {
                                "cve": {
                                    "id": "CVE-2024-0002",
                                    "published": "2024-01-02T00:00:00.000",
                                    "lastModified": "2024-01-03T00:00:00.000",
                                    "descriptions": [{"lang": "en", "value": "Buffer overflow in example parser."}],
                                    "weaknesses": [{"description": [{"value": "CWE-120"}]}],
                                    "references": {"referenceData": [{"url": "https://example.org/advisory"}]},
                                }
                            }
                        ]
                    },
                )
            if str(request.url) == "https://vuln.go.dev/index/db.json":
                return httpx.Response(200, json=[{"id": "GO-2024-0001"}])
            if str(request.url) == "https://vuln.go.dev/GO-2024-0001.json":
                return httpx.Response(
                    200,
                    json={
                        "id": "GO-2024-0001",
                        "summary": "Unsafe parsing in Go example module",
                        "details": "Bounds check bypass in parser.",
                        "affected": [{"package": {"name": "example.com/mod"}}],
                        "references": [{"url": "https://pkg.go.dev/vuln/GO-2024-0001"}],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            cisa = await CisaKevAdapter(VulnerabilityFetchConfig(max_records=1)).fetch(client)
            nvd = await NvdCveAdapter(VulnerabilityFetchConfig(max_records=1)).fetch(client)
            go_vuln = await GoVulnAdapter(VulnerabilityFetchConfig(max_records=1)).fetch(client)
        self.assertEqual(cisa[0].vulnerability_id, "CVE-2024-0001")
        self.assertEqual(cisa[0].license, "public-domain")
        self.assertEqual(nvd[0].cwe_ids, ["CWE-120"])
        self.assertEqual(go_vuln[0].vulnerability_id, "GO-2024-0001")
        self.assertEqual(go_vuln[0].source, "go-vuln")

    def test_governance_store_tracks_source_approvals_and_human_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = GovernanceStore(temp_dir)
            approval = store.submit_source_approval(
                SourceApprovalRequest(
                    source_name="portswigger-web-security-academy",
                    category="authorized_security_testing_labs",
                    urls=["https://portswigger.net/web-security"],
                    proposed_license="review-required",
                    evidence_url="https://portswigger.net/web-security",
                    requested_by="tester",
                    justification="High-value authorized web security education source.",
                )
            )
            store.decide_source_approval(
                approval.request_id,
                status="rejected",
                decided_by="legal",
                reason="license terms not approved for training yet",
            )
            item = store.enqueue_review(
                HumanReviewItem(
                    kind="high_value_patch_task",
                    priority=9,
                    payload={"source": "repo", "content_hash": "abc"},
                )
            )
            store.decide_review(item.item_id, status="approved", reviewer="security-reviewer", reason="defensive task")
            report = store.report()
            self.assertEqual(report.approvals_rejected, 1)
            self.assertEqual(report.review_approved, 1)
            self.assertEqual(report.high_priority_review, 0)

    def test_repo_patch_extraction_builds_defensive_patch_tasks_from_local_git_repo(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.org"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Mythos Test"], check=True)
            (repo / "app.py").write_text("def login(name):\n    return name\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
            (repo / "app.py").write_text("def login(name):\n    return name.strip()\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", "security validation fix for auth input"],
                check=True,
                capture_output=True,
            )
            report = extract_security_patch_tasks(repo, Path(temp_dir) / "patches.jsonl", license_name="mit")
            self.assertEqual(report.extracted, 1)
            tasks = list(iter_jsonl(Path(temp_dir) / "patches.jsonl"))
            self.assertIn("security validation fix", tasks[0]["subject"])

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
            self.assertTrue(any(task["metadata"]["training_priority"] == "critical" for task in tasks))

    def test_training_data_gate_promotes_high_signal_rows_and_separates_review_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clean = root / "clean.jsonl"
            rows = [
                {
                    "source": "trusted-security",
                    "license": "mit",
                    "text": "diff --git a/app.py b/app.py\n+ def safe_query(cursor, value):\n+     return cursor.execute('select * from t where id=?', [value])\n",
                    "quality": {
                        "quality_score": 0.9,
                        "labels": ["defensive_security", "code", "patch", "tests"],
                        "data_type": "patch",
                        "content_hash": "patch-row",
                    },
                },
                {
                    "source": "weak-docs",
                    "license": "mit",
                    "text": "cookie policy privacy policy table of contents subscribe to newsletter " * 20,
                    "quality": {
                        "quality_score": 0.45,
                        "labels": [],
                        "data_type": "documentation",
                        "risk_flags": ["navigation_or_boilerplate_noise"],
                        "content_hash": "noise-row",
                    },
                },
                {
                    "source": "trusted-security",
                    "license": "mit",
                    "text": "CWE-89 defensive SQL injection analysis with parameterized query guidance.",
                    "quality": {
                        "quality_score": 0.66,
                        "labels": ["defensive_security"],
                        "data_type": "security_reference",
                        "risk_flags": ["navigation_or_boilerplate_noise"],
                        "content_hash": "review-row",
                    },
                },
            ]
            clean.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            reputation = root / "reputation.json"
            reputation.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"source": "trusted-security", "reputation_score": 0.9},
                            {"source": "weak-docs", "reputation_score": 0.3},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = apply_training_data_gate(
                input_paths=[clean],
                promoted_path=root / "promoted.jsonl",
                holdout_path=root / "holdout.jsonl",
                review_queue_path=root / "review.jsonl",
                decisions_path=root / "decisions.jsonl",
                reputation_report_path=reputation,
                config=TrainingDataGateConfig(eval_holdout_fraction=0.0, min_quality_score=0.7),
            )

            self.assertEqual(report.promoted, 1)
            self.assertEqual(report.review_queue, 1)
            self.assertEqual(report.rejected, 1)
            promoted = list(iter_jsonl(root / "promoted.jsonl"))
            self.assertEqual(promoted[0]["train_policy"], "train")
            self.assertIn("patch_or_debug_trace", promoted[0]["training_gate"]["priority_labels"])

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
                min_training_quality_score=0.45,
                min_source_reputation_score=0.30,
                object_store_uri=f"local://{root / 'object-store'}",
            )
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.org") as client:
                report = await run_data_pipeline(config, client=client)

            self.assertEqual(report.status, "complete")
            self.assertEqual(report.crawl["accepted"], 2)
            self.assertTrue(report.clean_files)
            self.assertTrue(Path(report.tokenizer_path).exists())
            self.assertTrue(report.shard_manifest["train_shards"])
            self.assertIsNotNone(report.license_filter_report)
            self.assertGreaterEqual(report.license_filter_report["accepted"], 1)
            self.assertIsNotNone(report.benchmark_contamination_filter_report)
            self.assertIsNotNone(report.near_dedup_report)
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
            self.assertIsNotNone(report.source_reputation_report)
            self.assertTrue(report.source_reputation_report["sources"])
            self.assertIsNotNone(report.source_budget_plan)
            self.assertTrue(report.source_budget_plan["budgets"])
            self.assertIsNotNone(report.training_data_gate_report)
            self.assertGreaterEqual(report.training_data_gate_report["promoted"], 1)
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
                sources_path="config/data_sources.ultimate.json",
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
                sources_path="config/data_sources.ultimate.json",
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
                    source_paths=["config/data_sources.ultimate.json"],
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
            self.assertIsNotNone(plan.resource_catalog)
            self.assertTrue(Path(plan.resource_catalog_path).exists())
            self.assertEqual(plan.resource_catalog["priority_groups"][0]["name"], "Primus cybersecurity series")

    def test_resource_catalog_keeps_top_sources_and_benchmarks_separated(self) -> None:
        report = build_resource_catalog_report("config/data_sources.ultimate.json")
        self.assertEqual(report.total_resources, 45)
        self.assertEqual(report.priority_groups[0].resource_ids, [1, 2, 3, 4, 5, 6, 7])
        train_first_names = [item.name for item in report.train_first_resources]
        self.assertIn("Primus-Seed", train_first_names)
        self.assertIn("The Stack v2", train_first_names)
        eval_names = [item.name for item in report.eval_holdout_resources]
        self.assertIn("SWE-bench Verified", eval_names)
        self.assertIn("HumanEval", eval_names)

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

