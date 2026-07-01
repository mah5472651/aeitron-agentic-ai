#!/usr/bin/env python
"""One-command Mythos Architecture V1 release gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GateCheck(StrictModel):
    name: str
    status: str = Field(pattern="^(pass|warn|fail)$")
    required: bool
    duration_ms: float
    summary: str
    failed_component: str | None = None
    recommendation: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ReleaseGateReport(StrictModel):
    run_id: str
    mode: str
    decision: str
    score: float = Field(ge=0.0, le=100.0)
    passed: bool
    checks: list[GateCheck]
    failures: list[dict[str, Any]]
    warnings: list[str]
    artifacts: dict[str, str]
    created_at_unix: float = Field(default_factory=time.time)
    duration_ms: float


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: list[str]
    required: bool
    timeout_s: float
    component: str
    recommendation: str
    validator: Callable[[], tuple[bool, str, dict[str, Any]]] | None = None
    environment: dict[str, str] | None = None


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def execute(spec: CommandSpec) -> GateCheck:
    started = time.perf_counter()
    environment = os.environ.copy()
    environment.update(spec.environment or {})
    try:
        completed = subprocess.run(  # nosec B603
            spec.argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=spec.timeout_s,
            check=False,
            env=environment,
        )
        command_ok = completed.returncode == 0
        semantic_ok = True
        semantic_summary = "command completed"
        details: dict[str, Any] = {
            "exit_code": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }
        if command_ok and spec.validator:
            semantic_ok, semantic_summary, semantic_details = spec.validator()
            details.update(semantic_details)
        ok = command_ok and semantic_ok
        status = "pass" if ok else "fail" if spec.required else "warn"
        return GateCheck(
            name=spec.name,
            status=status,
            required=spec.required,
            duration_ms=(time.perf_counter() - started) * 1000,
            summary=semantic_summary if command_ok else f"exit_code={completed.returncode}",
            failed_component=None if ok else spec.component,
            recommendation=None if ok else spec.recommendation,
            details=details,
        )
    except subprocess.TimeoutExpired as exc:
        return GateCheck(
            name=spec.name,
            status="fail" if spec.required else "warn",
            required=spec.required,
            duration_ms=(time.perf_counter() - started) * 1000,
            summary=f"timeout after {spec.timeout_s:.0f}s",
            failed_component=spec.component,
            recommendation=spec.recommendation,
            details={
                "stdout_tail": exc.stdout[-2000:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": exc.stderr[-2000:] if isinstance(exc.stderr, str) else "",
            },
        )


def validate_capabilities() -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts" / "mythos_v1" / "capability-registry-latest.json")
    ok = bool(payload.get("valid")) and payload.get("capability_count") == 8
    return ok, f"capabilities={payload.get('capability_count')} valid={payload.get('valid')}", payload


def validate_bandit() -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts" / "mythos_v1" / "bandit.json")
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    return not results, f"security findings={len(results)}", {"finding_count": len(results), "findings": results[:20]}


def validate_phase51() -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts" / "phase51" / "high-stability-reasoning-memory-latest.json")
    lifecycle = payload.get("memory_lifecycle") if isinstance(payload.get("memory_lifecycle"), dict) else {}
    reflection = payload.get("reflection_contract") if isinstance(payload.get("reflection_contract"), dict) else {}
    reflection_triggered = bool(reflection.get("reflections"))
    lifecycle_ok = all(lifecycle.get(key) is True for key in ["deduplicated", "cross_project_isolated", "session_cleared"])
    rejected = payload.get("rejected_memory") is True
    ok = lifecycle_ok and reflection_triggered and rejected
    return ok, f"lifecycle={lifecycle_ok} reflection={reflection_triggered} pollution_rejected={rejected}", {
        "memory_lifecycle": lifecycle,
        "reflection_triggered": reflection_triggered,
        "pollution_rejected": rejected,
    }


def validate_phase40() -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts" / "phase40" / "integrated-agent-latest.json")
    strict = payload.get("strict_stability") if isinstance(payload.get("strict_stability"), dict) else {}
    trace = strict.get("reasoning_trace") if isinstance(strict.get("reasoning_trace"), dict) else {}
    ok = payload.get("status") == "complete" and trace.get("accepted") is True
    return ok, f"status={payload.get('status')} strict_accepted={trace.get('accepted')}", {
        "status": payload.get("status"),
        "confidence": payload.get("confidence"),
        "strict_confidence": trace.get("confidence"),
    }


def validate_regression() -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts" / "phase41" / "regression-pack-latest.json")
    score = payload.get("smoke_score")
    ok = payload.get("task_count") == 400 and isinstance(score, (int, float)) and score >= 0.99
    return ok, f"tasks={payload.get('task_count')} smoke_score={score}", {"task_count": payload.get("task_count"), "smoke_score": score}


def scorecard_path(run_id: str) -> Path:
    return ROOT / "artifacts" / "scorecard" / f"{run_id}-scorecard.json"


def validate_scorecard(run_id: str) -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(scorecard_path(run_id))
    mock = payload.get("mock") if isinstance(payload.get("mock"), dict) else {}
    metrics = mock.get("metrics") if isinstance(mock.get("metrics"), dict) else {}
    ok = mock.get("ready") is True and payload.get("task_dataset", {}).get("total") == 90
    return ok, f"ready={mock.get('ready')} tasks={payload.get('task_dataset', {}).get('total')} score={metrics.get('overall_score')}", {
        "ready": mock.get("ready"),
        "task_dataset": payload.get("task_dataset"),
        "metrics": metrics,
        "regressions": metrics.get("regression_count"),
    }


def validate_training() -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts" / "mythos_v1" / "training-preflight-latest.json")
    ok = payload.get("architecture_ready") is True
    return ok, f"architecture_ready={payload.get('architecture_ready')} data_ready={payload.get('data_ready')} gpu_ready={payload.get('gpu_ready')}", payload


def validate_backend() -> tuple[bool, str, dict[str, Any]]:
    payload = load_json(ROOT / "artifacts" / "mythos_v1" / "backend-comparison-latest.json")
    available = payload.get("endpoint_available") is True
    return available, f"status={payload.get('status')} endpoint_available={available} candidate_score={payload.get('candidate_score')}", payload


def build_specs(args: argparse.Namespace) -> list[CommandSpec]:
    py = sys.executable
    scorecard_run_id = f"{args.run_id}-scorecard"
    specs = [
        CommandSpec(
            "capability_registry",
            [py, "src/mythos_v1/capability_registry.py", "--run-id", f"{args.run_id}-capabilities", "--strict"],
            True,
            60,
            "capability_registry",
            "Restore missing capability modules or fix duplicate ownership.",
            validate_capabilities,
        ),
        CommandSpec(
            "python_compile",
            [py, "-m", "compileall", "-q", "src"],
            True,
            180,
            "source_tree",
            "Fix Python syntax/import compilation failures.",
        ),
        CommandSpec(
            "bandit_security",
            [py, "-m", "bandit", "-r", "src", "-f", "json", "-o", "artifacts/mythos_v1/bandit.json", "--exit-zero"],
            True,
            240,
            "security",
            "Patch or explicitly review every static security finding.",
            validate_bandit,
        ),
        CommandSpec(
            "strict_reasoning_memory",
            [py, "src/phase51/high_stability_reasoning_memory.py", "--run-id", f"{args.run_id}-phase51"],
            True,
            120,
            "reasoning_or_memory",
            "Fix role contracts, reflection trigger, project isolation, deduplication, or pollution gate.",
            validate_phase51,
        ),
        CommandSpec(
            "integrated_agent_workflow",
            [
                py,
                "src/phase40/integrated_agent.py",
                "--prompt",
                "build a secure login API with tests and verification",
                "--workspace",
                ".",
                "--agent-backend-mode",
                "mock",
                "--policy-mode",
                "development",
                "--no-verifier",
                "--no-security",
                "--no-vector-memory",
                "--strict",
            ],
            True,
            240,
            "integrated_runtime",
            "Inspect Phase 40 plan, strict stability trace, critic, and memory context.",
            validate_phase40,
            {"PHASE40_BACKEND": "mock", "PHASE24_BACKEND": "mock"},
        ),
        CommandSpec(
            "regression_pack",
            [py, "src/phase41/regression_pack.py", "--run-id", f"{args.run_id}-regression", "--smoke-limit", "400"],
            True,
            180,
            "regression_suite",
            "Fix missing golden task signals before release.",
            validate_regression,
        ),
        CommandSpec(
            "golden_scorecard",
            [
                py,
                "src/phase14/scorecard_harness.py",
                "--run-id",
                scorecard_run_id,
                "--mode",
                "mock",
                "--concurrency",
                str(args.concurrency),
                "--strict",
            ],
            True,
            300,
            "golden_evaluation",
            "Use the failure auto-report to locate planner, memory, security, patch, or sandbox gaps.",
            lambda: validate_scorecard(args.run_id),
        ),
        CommandSpec(
            "training_preflight",
            [py, "src/mythos_v1/training_preflight.py", "--run-id", f"{args.run_id}-training", "--strict-architecture"],
            True,
            120,
            "training_pipeline",
            "Restore data schemas, launch configs, tokenizer contract, or checkpoint gate assets.",
            validate_training,
        ),
    ]
    if args.include_real_backend:
        specs.append(
            CommandSpec(
                "real_backend_comparison",
                [
                    py,
                    "src/mythos_v1/backend_comparison.py",
                    "--run-id",
                    f"{args.run_id}-backend",
                    "--suite",
                    args.backend_suite,
                    "--max-tasks",
                    str(args.backend_tasks),
                ],
                args.require_real_backend,
                600,
                "model_backend",
                "Start the active model endpoint or improve weak model categories; architecture and model quality are reported separately.",
                validate_backend,
            )
        )
    if args.mode == "full":
        specs.append(
            CommandSpec(
                "full_architecture_readiness",
                [py, "src/phase10/architecture_readiness_audit.py", "--run-id", f"{args.run_id}-readiness", "--strict"],
                True,
                600,
                "live_infrastructure",
                "Start Docker/Redis/Postgres/Qdrant/gateway or repair the failing readiness component.",
            )
        )
    return specs


def write_report(report: ReleaseGateReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "release-gate-latest.json"
    md_path = output_dir / "release-gate-latest.md"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Mythos Architecture V1 Release Gate",
        "",
        f"- Decision: `{report.decision}`",
        f"- Score: `{report.score:.2f}`",
        f"- Passed: `{report.passed}`",
        "",
        "| Check | Status | Required | Duration ms | Summary |",
        "| --- | --- | --- | ---: | --- |",
    ]
    lines.extend(
        f"| {check.name} | {check.status} | {check.required} | {check.duration_ms:.1f} | {check.summary.replace('|', '/')} |"
        for check in report.checks
    )
    lines.extend(["", "## Failure Analysis", ""])
    lines.extend(
        f"- `{item['check']}` -> `{item['component']}`: {item['recommendation']}"
        for item in report.failures
    )
    if not report.failures:
        lines.append("- No required gate failures.")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mythos Architecture V1 release gate.")
    parser.add_argument("--run-id", default=f"mythos-v1-release-{int(time.time())}")
    parser.add_argument("--mode", choices=["quick", "full"], default="quick")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--include-real-backend", action="store_true")
    parser.add_argument("--require-real-backend", action="store_true")
    parser.add_argument("--backend-suite", choices=["quick", "full"], default="quick")
    parser.add_argument("--backend-tasks", type=int, default=7)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "mythos_v1")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.time()
    checks = [execute(spec) for spec in build_specs(args)]
    required = [check for check in checks if check.required]
    passed = not any(check.status == "fail" for check in required)
    earned = sum(1.0 if check.status == "pass" else 0.5 if check.status == "warn" else 0.0 for check in checks)
    score = (earned / max(1, len(checks))) * 100.0
    failures = [
        {
            "check": check.name,
            "component": check.failed_component,
            "recommendation": check.recommendation,
            "summary": check.summary,
        }
        for check in checks
        if check.status == "fail"
    ]
    warnings = [f"{check.name}: {check.summary}" for check in checks if check.status == "warn"]
    report = ReleaseGateReport(
        run_id=args.run_id,
        mode=args.mode,
        decision="release" if passed else "block",
        score=score,
        passed=passed,
        checks=checks,
        failures=failures,
        warnings=warnings,
        artifacts={},
        duration_ms=(time.time() - started) * 1000,
    )
    json_path, md_path = write_report(report, args.output_dir)
    report = report.model_copy(update={"artifacts": {"json": str(json_path), "markdown": str(md_path)}})
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"run_id": report.run_id, "decision": report.decision, "score": report.score, "passed": report.passed, "summary": {status: sum(1 for check in checks if check.status == status) for status in ["pass", "warn", "fail"]}, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    raise SystemExit(1 if args.strict and not report.passed else 0)


if __name__ == "__main__":
    main()
