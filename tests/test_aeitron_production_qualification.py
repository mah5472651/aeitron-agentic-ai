from __future__ import annotations

import json
import os
import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace

from fastapi import HTTPException
from hypothesis import given, settings, strategies as st

from src.aeitron.deployment.production_qualification import (
    CanaryEvidence,
    ImmutableQualificationStore,
    LoadStagePolicy,
    OperatorNotificationEvidence,
    ProductionQualificationReport,
    QualificationCheck,
    QualificationPolicy,
    SecurityReviewEvidence,
    bind_evidence,
    check_observability,
    load_json_evidence,
    validate_canary,
    validate_security_review,
    validate_training_proofs,
    utc_now,
)
from src.aeitron.model_ops.foundation import sha256_file
from src.aeitron.model_ops.native_serving import (
    ChatCompletionRequest,
    NativeServingState,
)


def policy_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_max_age_seconds": 3600,
        "minimum_security_reviewers": 2,
        "require_report_signature_in_production": True,
        "require_operator_notification_proof": True,
        "load_stages": [
            {
                "name": "small",
                "concurrency": 2,
                "requests": 4,
                "streaming_requests": 1,
                "maximum_error_rate": 0.01,
                "maximum_p95_latency_ms": 30000,
                "minimum_throughput_rps": 0.01,
            }
        ],
        "required_training_proofs": [
            "postgres_redis_minio_lifecycle",
            "ordered_event_stress_1000000",
            "docker_disaster_recovery_drill",
            "worker_loss_retry_recovery",
        ],
        "required_soak_seconds": [86400, 604800],
        "required_security_domains": [
            "authentication_authorization",
            "ssrf",
            "path_traversal",
            "sandbox_escape",
            "secrets_and_iam",
            "dependency_supply_chain",
            "container_kubernetes",
        ],
        "required_canary_percentages": [1, 10, 50, 100],
        "minimum_internal_canary_users": 1,
        "maximum_internal_canary_users": 5,
        "canary_maximum_error_rate": 0.01,
        "canary_maximum_p95_latency_ms": 30000,
        "canary_rollback_error_rate": 0.02,
        "canary_maximum_rollback_seconds": 120,
    }


def build_policy() -> QualificationPolicy:
    return QualificationPolicy.model_validate(policy_payload())


def build_report(report_id: str) -> ProductionQualificationReport:
    return ProductionQualificationReport(
        report_id=report_id,
        status="blocked",
        mode="validation",
        created_at="2026-07-20T00:00:00+00:00",
        git_commit="a" * 40,
        policy_sha256="b" * 64,
        environment={"python": "test"},
        checks=[
            QualificationCheck(
                subsystem="production_proof_baseline",
                status="passed",
                summary="test",
            )
        ],
    )


class AeitronProductionQualificationTest(unittest.TestCase):
    def test_observability_requires_operator_delivery_evidence(self) -> None:
        result = asyncio.run(
            check_observability(
                metrics_url="http://127.0.0.1:1/metrics",
                prometheus_url="http://127.0.0.1:1/-/ready",
                grafana_url="http://127.0.0.1:1/api/health",
                otel_health_url="http://127.0.0.1:1/",
                alertmanager_url="http://127.0.0.1:1",
                operator_notification_report=None,
                policy=build_policy(),
                allowed_insecure_hosts=["127.0.0.1"],
            )
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("--operator-notification-report is required", result.blockers)

    def test_operator_delivery_ids_must_be_distinct(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            OperatorNotificationEvidence(
                proof_id="notification-proof",
                created_at=utc_now(),
                provider="test-provider",
                channel_type="webhook",
                recipient_reference_sha256="a" * 64,
                firing_delivery_id="same",
                recovery_delivery_id="same",
                firing_delivered_at=utc_now(),
                recovery_delivered_at=utc_now(),
                firing_delivered=True,
                recovery_delivered=True,
                status="passed",
            )

    def test_immutable_report_is_versioned_signed_and_hash_chained(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ImmutableQualificationStore(root, signing_key="s" * 32)
            first_path = store.write(build_report("run-1"))
            first = ProductionQualificationReport.model_validate_json(
                first_path.read_text(encoding="utf-8")
            )
            self.assertEqual(first.signature_algorithm, "HMAC-SHA256")
            self.assertEqual(len(first.report_sha256), 64)
            self.assertEqual(
                sha256_file(first_path),
                json.loads((root / "latest.json").read_text(encoding="utf-8"))[
                    "file_sha256"
                ],
            )

            second_path = store.write(build_report("run-2"))
            second = ProductionQualificationReport.model_validate_json(
                second_path.read_text(encoding="utf-8")
            )
            self.assertEqual(second.previous_report_sha256, first.report_sha256)
            self.assertNotEqual(first_path, second_path)
            with self.assertRaises(FileExistsError):
                store.write(build_report("run-2"))

    def test_tampered_latest_report_blocks_next_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = ImmutableQualificationStore(root, signing_key=None)
            report_path = store.write(build_report("run-1"))
            report_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                store.write(build_report("run-2"))

    def test_stale_and_changed_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "evidence.json"
            path.write_text('{"status":"passed"}', encoding="utf-8")
            old = time.time() - 7200
            os.utime(path, (old, old))
            with self.assertRaisesRegex(ValueError, "stale"):
                bind_evidence(
                    path,
                    evidence_id="stale",
                    maximum_age_seconds=60,
                )

            os.utime(path, None)
            binding = bind_evidence(
                path,
                evidence_id="current",
                maximum_age_seconds=60,
            )
            path.write_text('{"status":"failed"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                load_json_evidence(binding)

    def test_training_proof_requires_failure_and_both_soak_durations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "training.json"
            proofs = [
                {
                    "name": "postgres_redis_minio_lifecycle",
                    "status": "passed",
                    "evidence": {
                        "redis_event_count": 1,
                        "duplicate_event_rejected": True,
                        "database": {
                            "job_rows": 1,
                            "attempt_rows": 1,
                            "ingress_rows": 1,
                        },
                    },
                },
                {
                    "name": "ordered_event_stress_1000000",
                    "status": "passed",
                    "evidence": {
                        "event_count": 1_000_000,
                        "final_sequence": 1_000_000,
                        "tail_sequence": 1_000_000,
                    },
                },
                {
                    "name": "docker_disaster_recovery_drill",
                    "status": "passed",
                    "evidence": {
                        "postgres_dump_restore": True,
                        "postgres_restart": True,
                        "redis_aof_restart": True,
                        "minio_volume_restart": True,
                        "qdrant_volume_restart": True,
                    },
                },
                {
                    "name": "worker_loss_retry_recovery",
                    "status": "passed",
                    "evidence": {
                        "status_after_loss": "queued",
                        "retry_failure_class": "node_loss",
                        "attempt_count": 1,
                    },
                },
                {
                    "name": "infrastructure_soak_86400s",
                    "status": "passed",
                    "duration_seconds": 86400,
                    "evidence": {
                        "requested_seconds": 86400,
                        "successful_transactions": 2880,
                    },
                },
                {
                    "name": "infrastructure_soak_604800s",
                    "status": "passed",
                    "duration_seconds": 604800,
                    "evidence": {
                        "requested_seconds": 604800,
                        "successful_transactions": 20160,
                    },
                },
            ]
            for proof in proofs:
                proof.setdefault("started_at", utc_now())
                proof.setdefault("duration_seconds", 1.0)
                proof.setdefault("blockers", [])
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "generated_at": utc_now(),
                        "environment": {},
                        "proofs": proofs,
                        "passed": len(proofs),
                        "failed": 0,
                        "blocked": 0,
                        "status": "passed",
                    }
                ),
                encoding="utf-8",
            )
            failure, soak = validate_training_proofs(
                str(path),
                policy=build_policy(),
            )
            self.assertEqual(failure.status, "passed")
            self.assertEqual(soak.status, "passed")

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["proofs"][-1]["status"] = "failed"
            path.write_text(json.dumps(payload), encoding="utf-8")
            _, failed_soak = validate_training_proofs(
                str(path),
                policy=build_policy(),
            )
            self.assertEqual(failed_soak.status, "failed")

    def test_security_review_requires_independence_domains_and_no_open_highs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "security.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "review_id": "manual-1",
                        "reviewed_at": utc_now(),
                        "reviewers": ["reviewer-a", "reviewer-b"],
                        "domains": {
                            domain: "passed"
                            for domain in build_policy().required_security_domains
                        },
                        "critical_findings": 0,
                        "high_findings": 0,
                        "unresolved_findings": [],
                        "scanner_report_sha256": "c" * 64,
                        "decision": "approved",
                    }
                ),
                encoding="utf-8",
            )
            result = validate_security_review(str(path), policy=build_policy())
            self.assertEqual(result.status, "passed")

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["reviewers"] = ["reviewer-a", "REVIEWER-A"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            failed = validate_security_review(str(path), policy=build_policy())
            self.assertEqual(failed.status, "failed")
            self.assertIn("distinct", failed.blockers[0])

    def test_canary_requires_every_stage_and_rollback_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "canary.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "canary_id": "canary-1",
                        "created_at": utc_now(),
                        "internal_user_count": 3,
                        "status": "passed",
                        "stages": [
                            {
                                "percentage": percentage,
                                "requests": 100,
                                "error_rate": 0.0,
                                "p95_latency_ms": 50,
                                "rollback_trigger_tested": True,
                                "rollback_trigger_error_rate": 0.02,
                                "rollback_succeeded": True,
                                "rollback_completed_seconds": 5,
                            }
                            for percentage in [1, 10, 50, 100]
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_canary(str(path), policy=build_policy())
            self.assertEqual(result.status, "passed")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["stages"][2]["rollback_succeeded"] = False
            path.write_text(json.dumps(payload), encoding="utf-8")
            failed = validate_canary(str(path), policy=build_policy())
            self.assertEqual(failed.status, "failed")
            self.assertTrue(any("50%" in blocker for blocker in failed.blockers))

    @settings(max_examples=100, deadline=None)
    @given(
        concurrency=st.integers(min_value=1, max_value=500),
        multiplier=st.integers(min_value=1, max_value=10),
        streaming_fraction=st.floats(
            min_value=0.0,
            max_value=1.0,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    def test_load_stage_property_preserves_bounded_invariants(
        self,
        concurrency: int,
        multiplier: int,
        streaming_fraction: float,
    ) -> None:
        requests = concurrency * multiplier
        streaming = min(requests, int(requests * streaming_fraction))
        stage = LoadStagePolicy(
            name="fuzz-stage",
            concurrency=concurrency,
            requests=requests,
            streaming_requests=streaming,
            maximum_error_rate=0.01,
            maximum_p95_latency_ms=30000,
            minimum_throughput_rps=0.01,
        )
        self.assertLessEqual(stage.streaming_requests, stage.requests)
        self.assertGreaterEqual(stage.requests, stage.concurrency)

    @settings(max_examples=100, deadline=None)
    @given(st.binary(min_size=0, max_size=4096))
    def test_malformed_json_evidence_never_silently_passes(self, payload: bytes) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fuzz.json"
            path.write_bytes(payload)
            binding = bind_evidence(
                path,
                evidence_id="fuzz",
                maximum_age_seconds=60,
            )
            try:
                parsed = load_json_evidence(binding)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            self.assertIsInstance(parsed, (dict, list, str, int, float, bool, type(None)))

    def test_strict_models_reject_unknown_evidence_fields(self) -> None:
        payload = {
            "schema_version": 1,
            "canary_id": "x",
            "created_at": "2026-07-20T00:00:00+00:00",
            "internal_user_count": 1,
            "status": "passed",
            "stages": [],
            "unexpected": "not allowed",
        }
        with self.assertRaises(ValueError):
            CanaryEvidence.model_validate(payload)
        with self.assertRaises(ValueError):
            SecurityReviewEvidence.model_validate({"unexpected": True})

    def test_timed_out_generation_keeps_capacity_until_worker_exits(self) -> None:
        state = object.__new__(NativeServingState)
        state.config = SimpleNamespace(
            max_concurrent_generations=1,
            max_queue_depth=1,
            queue_timeout_seconds=0.01,
            generation_timeout_seconds=0.01,
        )
        state._generation_slots = asyncio.Semaphore(1)
        state._state_lock = asyncio.Lock()
        state._queued = 0
        state._active = 0
        state._completed = 0
        state._failed = 0
        state._timed_out = 0
        active_workers = 0
        maximum_workers = 0
        guard = threading.Lock()

        def slow_generate(
            self: NativeServingState,
            request: ChatCompletionRequest,
        ) -> tuple[str, int, float]:
            nonlocal active_workers, maximum_workers
            with guard:
                active_workers += 1
                maximum_workers = max(maximum_workers, active_workers)
            try:
                time.sleep(0.08)
                return "done", 1, 80.0
            finally:
                with guard:
                    active_workers -= 1

        state.generate = MethodType(slow_generate, state)
        request = ChatCompletionRequest(
            messages=[{"role": "user", "content": "safe"}],
            max_tokens=1,
        )

        async def scenario() -> None:
            with self.assertRaises(HTTPException) as first:
                await state.generate_async(request)
            self.assertEqual(first.exception.status_code, 504)
            with self.assertRaises(HTTPException) as second:
                await state.generate_async(request)
            self.assertEqual(second.exception.status_code, 503)
            await asyncio.sleep(0.12)
            self.assertEqual(state._active, 0)

        asyncio.run(scenario())
        self.assertEqual(maximum_workers, 1)


if __name__ == "__main__":
    unittest.main()
