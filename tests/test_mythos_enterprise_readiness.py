from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.mythos.deployment.k8s_validate import validate_manifests
from src.mythos.evaluation.benchmark_pack import BenchmarkPackConfig, run_benchmark_pack
from src.mythos.evaluation.benchmark_suites import BenchmarkSuiteSpec, run_benchmark_suites
from src.mythos.learning.dataset_validation import DatasetValidationConfig, validate_dataset
from src.mythos.learning.source_balancing import balance_clean_jsonl
from src.mythos.learning.storage import LocalObjectStore, ObjectStoreConfig, upload_paths, verify_object_store_lifecycle
from src.mythos.production_readiness import run_production_readiness
from src.mythos.security.audit import run_security_audit


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


class MythosEnterpriseReadinessTest(unittest.TestCase):
    def test_alembic_migration_contract_exists(self) -> None:
        self.assertTrue(Path("alembic.ini").exists())
        version_dir = Path("src/mythos/db/alembic/versions")
        versions = sorted(path.name for path in version_dir.glob("*.py"))
        self.assertIn("0001_initial.py", versions)
        self.assertIn("0002_data_platform.py", versions)
        self.assertIn("0001_initial", (version_dir / "0002_data_platform.py").read_text(encoding="utf-8"))

    def test_object_store_lifecycle_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = verify_object_store_lifecycle(
                config=ObjectStoreConfig(uri=f"local://{root / 'objects'}"),
                work_dir=root / "work",
            )
            self.assertEqual(report.status, "passed")
            self.assertTrue(report.checksum_match)
            self.assertTrue(report.deleted)

    def test_object_store_upload_paths_keeps_duplicate_shard_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            train = root / "shards" / "train" / "shard-000000.bin"
            val = root / "shards" / "val" / "shard-000000.bin"
            train.parent.mkdir(parents=True)
            val.parent.mkdir(parents=True)
            train.write_bytes(b"train")
            val.write_bytes(b"validation")

            objects = upload_paths(
                LocalObjectStore(root / "objects"),
                [train, val],
                prefix="runs/example/shards",
            )

            self.assertEqual(len(objects), 2)
            self.assertEqual(len({item.uri for item in objects}), 2)
            self.assertTrue(all(Path(item.uri).exists() for item in objects))
            self.assertIn("train", Path(objects[0].uri).read_text(encoding="utf-8"))
            self.assertIn("validation", Path(objects[1].uri).read_text(encoding="utf-8"))

    def test_dataset_validation_passes_and_blocks_bad_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = write_jsonl(
                root / "good.jsonl",
                [
                    {
                        "text": "General software architecture documentation with enough detail for language modeling.",
                        "license": "mit",
                        "category": "general",
                        "quality": {"labels": ["documentation"]},
                    },
                    {
                        "text": "def secure_query(cursor, value): return cursor.execute('select * from t where id=?', [value])",
                        "license": "apache-2.0",
                        "category": "code",
                        "quality": {"labels": ["code"]},
                    },
                    {
                        "text": "Defensive security analysis: SQL injection should be fixed with parameterized queries and tests.",
                        "license": "cc-by-4.0",
                        "category": "cybersecurity",
                        "quality": {"labels": ["defensive_security"]},
                    },
                ],
            )
            report = validate_dataset(
                DatasetValidationConfig(
                    input_paths=[str(good)],
                    min_records=3,
                    min_avg_chars=20,
                    max_duplicate_fraction=0.50,
                )
            )
            self.assertEqual(report.status, "passed")

            bad = write_jsonl(
                root / "bad.jsonl",
                [
                    {"text": "dup", "category": "general"},
                    {"text": "dup", "category": "general"},
                ],
            )
            bad_report = validate_dataset(
                DatasetValidationConfig(
                    input_paths=[str(bad)],
                    min_records=3,
                    max_duplicate_fraction=0.10,
                )
            )
            self.assertEqual(bad_report.status, "failed")

    def test_source_balancing_report_never_claims_more_rows_than_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                {
                    "text": f"dominant source record {index}",
                    "source": "dominant",
                    "quality": {"quality_score": 0.6},
                }
                for index in range(10)
            ]
            rows.append({"text": "tiny source record", "source": "tiny", "quality": {"quality_score": 0.9}})
            corpus = write_jsonl(root / "clean.jsonl", rows)

            report = balance_clean_jsonl(
                input_paths=[corpus],
                output_path=root / "balanced.jsonl",
                max_source_fraction=0.30,
                min_source_rows=25,
            )

            self.assertEqual(report.input_rows, 11)
            self.assertLessEqual(report.output_rows, report.input_rows)
            for item in report.sources:
                self.assertLessEqual(item.output_rows, item.input_rows)

    def test_k8s_manifests_validate_without_blocking_failures(self) -> None:
        report = validate_manifests(sorted(Path("deploy/k8s").glob("*.yaml")))
        self.assertEqual(report.status, "passed")
        self.assertGreaterEqual(report.resources.get("Deployment", 0), 1)
        self.assertGreaterEqual(report.resources.get("NetworkPolicy", 0), 1)

    def test_benchmark_suite_adapters_execute_local_holdout_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            swe = write_jsonl(
                root / "swe.jsonl",
                [
                    {
                        "instance_id": "swe-1",
                        "problem_statement": "Fix auth validation",
                        "patch": "+ def test_auth():\n+     assert True",
                        "expected_terms": ["test_auth", "assert"],
                    }
                ],
            )
            cyber = write_jsonl(
                root / "cyber.jsonl",
                [
                    {
                        "id": "sec-1",
                        "code": "cursor.execute('select * from users where name=' + user_input)",
                        "expected_findings": ["user_input"],
                    }
                ],
            )
            report = run_benchmark_suites(
                [
                    BenchmarkSuiteSpec(name="swe", kind="swe_bench_style", path=str(swe)),
                    BenchmarkSuiteSpec(name="cyber", kind="cyberseceval_style", path=str(cyber)),
                ]
            )
            self.assertEqual(report.status, "passed")
            self.assertEqual(report.aggregate_score, 1.0)

    def test_benchmark_pack_runs_required_and_optional_local_suites(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            human = write_jsonl(
                root / "human.jsonl",
                [{"task_id": "h1", "prompt": "Write add", "canonical_solution": "def add(a,b): return a+b", "expected_terms": ["def"]}],
            )
            mbpp = write_jsonl(
                root / "mbpp.jsonl",
                [{"task_id": "m1", "prompt": "Reverse string", "code": "def reverse(s): return s[::-1]", "expected_terms": ["return"]}],
            )
            security = write_jsonl(
                root / "security.jsonl",
                [{"id": "s1", "code": "cursor.execute('select * from t where x=' + x)", "expected_findings": ["cursor.execute"]}],
            )
            report = run_benchmark_pack(
                BenchmarkPackConfig(
                    human_eval_path=str(human),
                    mbpp_path=str(mbpp),
                    cyberseceval_path=str(security),
                    strict=True,
                ),
                output_dir=root / "bench",
            )
            self.assertEqual(report.status, "passed")
            self.assertIn("humaneval", report.required_suites)
            self.assertTrue((root / "bench" / "benchmark_pack_report.json").exists())

    def test_security_audit_detects_secret_and_can_pass_clean_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clean = root / "src" / "mythos"
            clean.mkdir(parents=True)
            (clean / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
            report = run_security_audit(root=root, run_bandit=False, validate_k8s=False, run_semgrep=False, run_codeql=False, run_pip_audit=False)
            self.assertEqual(report.status, "passed")

            (clean / "bad.py").write_text("API_KEY = 'abcdefghijklmnopqrstuvwxyz123456'\n", encoding="utf-8")
            bad = run_security_audit(root=root, run_bandit=False, validate_k8s=False, run_semgrep=False, run_codeql=False, run_pip_audit=False)
            self.assertEqual(bad.status, "failed")
            self.assertTrue(any(item.check == "secret_pattern" for item in bad.findings))

    def test_production_readiness_is_honest_about_missing_external_dependencies(self) -> None:
        cleared = {
            "MYTHOS_AUTH_ENABLED": "",
            "MYTHOS_JWT_SECRET": "",
            "MYTHOS_QUOTA_ENABLED": "",
            "MYTHOS_REDIS_URL": "",
            "MYTHOS_MODEL_BACKEND": "",
            "MYTHOS_MODEL_ENDPOINT": "",
            "MYTHOS_DATABASE_URL": "",
            "MYTHOS_OBJECT_STORE_URI": "",
            "MYTHOS_QDRANT_URL": "",
        }
        with patch.dict("os.environ", cleared, clear=False):
            report = run_production_readiness(mode="production", benchmark_dir="definitely-missing-eval-dir")
            self.assertEqual(report.status, "failed")
            statuses = {check.subsystem: check.status for check in report.checks}
            self.assertEqual(statuses["serving"], "blocked_missing_dependency")
            self.assertEqual(statuses["benchmark_eval"], "blocked_missing_dependency")
            self.assertTrue(any(check.production_blocker for check in report.checks))

            dev_report = run_production_readiness(mode="dev", benchmark_dir="definitely-missing-eval-dir")
            self.assertEqual(dev_report.status, "passed")
            self.assertTrue(any(check.status == "blocked_missing_dependency" for check in dev_report.checks))


if __name__ == "__main__":
    unittest.main()
