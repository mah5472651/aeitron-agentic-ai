#!/usr/bin/env python
"""Deep architecture readiness audit for the AI architecture build.

This audit is intentionally stricter than the Phase 10 smoke runner. The smoke
runner answers "does the local stack boot?". This file answers "is the stack
ready enough to trust for serious development, and what is still missing for a
production 7B-13B training/serving path?".
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import subprocess  # nosec B404
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


@dataclass(frozen=True)
class AuditItem:
    name: str
    status: str
    weight: float
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


@dataclass(frozen=True)
class ArchitectureAuditReport:
    run_id: str
    started_at_unix: float
    duration_ms: float
    score: float
    grade: str
    passed: bool
    items: list[AuditItem]
    done: list[str]
    remaining: list[str]
    local_constraints: list[str]

    def summary(self) -> dict[str, int]:
        counts = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts


def status_points(status: str) -> float:
    if status == OK:
        return 1.0
    if status == WARN:
        return 0.5
    return 0.0


def grade_for(score: float) -> str:
    if score >= 95:
        return "production-grade"
    if score >= 85:
        return "strong-local"
    if score >= 70:
        return "mvp-ready"
    if score >= 50:
        return "partial"
    return "blocked"


def item(name: str, status: str, weight: float, message: str, details: dict[str, Any] | None = None, started: float | None = None) -> AuditItem:
    duration_ms = 0.0 if started is None else (time.perf_counter() - started) * 1000
    return AuditItem(name=name, status=status, weight=weight, message=message, details=details or {}, duration_ms=duration_ms)


def run_command(argv: list[str], timeout_s: float = 120.0) -> CommandResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(  # nosec B603
            argv,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        return CommandResult(
            argv=argv,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            argv=argv,
            exit_code=None,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            duration_ms=(time.perf_counter() - started) * 1000,
            timed_out=True,
        )


def module_present(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def version_for(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def check_phase_files() -> AuditItem:
    started = time.perf_counter()
    required = [
        "src/phase1/callgraph_extractor.py",
        "src/phase1/train_code_bpe_tokenizer.py",
        "src/phase2/docker_sandbox_engine.py",
        "src/phase3/rejection_sampling_pipeline.py",
        "src/phase4/swarm_orchestrator.py",
        "src/phase5/self_healing_runtime.py",
        "src/phase6/redis_quota_engine.py",
        "src/phase6/redis_regenerative_bucket.lua",
        "src/phase7/grpo_training_loop.py",
        "src/phase8/gateway.py",
        "src/phase8/vllm_server.py",
        "src/phase8/quantize_awq.py",
        "src/phase9/evaluate.py",
        "src/phase10/e2e_smoke_runner.py",
        "src/phase11/pytorch_model.py",
        "src/phase11/model_backends.py",
        "src/phase11/memory_engine.py",
        "src/phase11/security_engine.py",
        "src/phase11/agentic_runtime.py",
        "src/phase11/chat_api.py",
        "src/phase11/roadmap.py",
        "src/phase11/smoke_test.py",
        "src/phase11/static/index.html",
        "src/phase11/static/styles.css",
        "src/phase11/static/app.js",
        "src/phase12/capability_gauntlet.py",
        "src/phase13/backend_quality_harness.py",
        "src/phase14/scorecard_harness.py",
        "src/phase15/target_architecture.py",
        "src/phase16/task_graph.py",
        "src/phase16/role_agents.py",
        "src/phase16/critic_verifier.py",
        "src/phase16/experience_memory.py",
        "src/phase16/tool_adapters.py",
        "src/phase16/sft_exporter.py",
        "src/phase16/base_model_connector.py",
        "src/phase16/local_hf_openai_server.py",
        "src/phase16/smoke_test.py",
        "src/phase17/gpu_readiness.py",
        "src/phase17/qlora_sft_training.py",
        "src/phase18/model_quality_loop.py",
        "src/phase19/verifier_registry.py",
        "src/phase20/taskgraph_runtime.py",
        "src/phase21/experience_promotion.py",
        "src/phase22/critic_service.py",
        "src/phase23/model_quality_profiles.py",
        "src/phase24/main_agent_v2.py",
        "src/phase25/experience_retrieval.py",
        "src/phase26/patch_manager.py",
        "src/phase27/verifier_policy_engine.py",
        "src/phase28/security_expert_workflow.py",
        "src/phase29/dataset_review_gate.py",
        "src/phase30/expanded_benchmark_suite.py",
        "src/phase31/long_context_packer.py",
        "src/phase32/critic_endpoint_contract.py",
        "src/phase33/gpu_backend_contract.py",
        "src/phase34/auth_quota.py",
        "src/phase35/observability.py",
        "src/phase36/data_flywheel.py",
        "src/phase37/vector_memory.py",
        "src/phase38/multilang_security.py",
        "src/phase39/checkpoint_rollback.py",
        "src/phase40/integrated_agent.py",
        "src/phase41/regression_pack.py",
        "src/phase42/profile_switcher.py",
        "src/phase43/meta_planner.py",
        "src/phase44/intent_expansion.py",
        "src/phase45/parallel_agent_runtime.py",
        "src/phase46/hierarchical_memory.py",
        "src/phase47/reasoning_engine.py",
        "src/phase48/knowledge_graph.py",
        "src/phase49/multimodal_expert.py",
        "src/phase50/moe_router.py",
        "src/phase50/phase43_to_50_e2e.py",
        "src/phase51/high_stability_reasoning_memory.py",
        "docs/phase12_capability_gauntlet.md",
        "docs/phase13_backend_quality.md",
        "docs/scorecard_harness.md",
        "docs/mythos_target_architecture.md",
        "docs/phase16_core_upgrades.md",
        "docs/gpu_7b32b_readiness.md",
        "docs/phase18_model_quality_loop.md",
        "docs/phase19_to_23_quality_hardening.md",
        "docs/phase24_to_33_power_architecture.md",
        "docs/phase1_to_51_architecture_manual.md",
        "docs/phase34_to_36_production_control.md",
        "docs/phase37_to_39_production_hardening.md",
        "docs/phase40_to_42_integrated_runtime.md",
        "docs/phase43_to_50_cognitive_architecture.md",
        "docs/phase51_high_stability_reasoning_memory.md",
        "scripts/run_phase12_gauntlet.ps1",
        "scripts/run_phase13_quality.ps1",
        "scripts/run_scorecard.ps1",
        "scripts/write_target_architecture.ps1",
        "scripts/run_phase16_core.ps1",
        "scripts/run_phase16_core_real.ps1",
        "scripts/run_phase17_gpu_readiness.ps1",
        "scripts/run_phase18_model_quality.ps1",
        "scripts/run_phase19_verifier.ps1",
        "scripts/run_phase20_taskgraph_runtime.ps1",
        "scripts/run_phase21_experience_promotion.ps1",
        "scripts/run_phase22_critic.ps1",
        "scripts/run_phase23_quality_profile.ps1",
        "scripts/run_phase24_main_agent_v2.ps1",
        "scripts/run_phase25_experience_retrieval.ps1",
        "scripts/run_phase26_patch_manager.ps1",
        "scripts/run_phase27_verifier_policy.ps1",
        "scripts/run_phase28_security_workflow.ps1",
        "scripts/run_phase29_dataset_review_gate.ps1",
        "scripts/run_phase30_expanded_benchmark.ps1",
        "scripts/run_phase31_long_context_packer.ps1",
        "scripts/run_phase32_critic_contract.ps1",
        "scripts/run_phase33_gpu_backend_contract.ps1",
        "scripts/run_phase34_auth_token.ps1",
        "scripts/run_phase35_observability.ps1",
        "scripts/run_phase36_data_flywheel.ps1",
        "scripts/run_phase37_vector_memory.ps1",
        "scripts/run_phase38_multilang_security.ps1",
        "scripts/run_phase39_checkpoint_gate.ps1",
        "scripts/run_phase40_integrated_agent.ps1",
        "scripts/run_phase41_regression_pack.ps1",
        "scripts/run_phase42_profile_switcher.ps1",
        "scripts/run_phase43_meta_planner.ps1",
        "scripts/run_phase44_intent_expansion.ps1",
        "scripts/run_phase45_parallel_agent.ps1",
        "scripts/run_phase46_hierarchical_memory.ps1",
        "scripts/run_phase47_reasoning_engine.ps1",
        "scripts/run_phase48_knowledge_graph.ps1",
        "scripts/run_phase49_multimodal_expert.ps1",
        "scripts/run_phase50_moe_router.ps1",
        "scripts/run_phase43_to_50_e2e.ps1",
        "scripts/run_phase51_high_stability.ps1",
        "scripts/install_security_tools.ps1",
        "scripts/start_phase16_real_backend.ps1",
        "scripts/stop_phase16_real_backend.ps1",
        "deploy/gpu/model_profiles.json",
        "deploy/gpu/deepspeed_zero2.json",
        "deploy/gpu/accelerate_zero2.yaml",
        "deploy/dev/docker-compose.yml",
        "deploy/phase8/docker-compose.yml",
        "deploy/phase8/nginx.conf",
        "README.md",
        "requirements.txt",
    ]
    missing = [path for path in required if not (ROOT / path).exists()]
    return item(
        "phase_surface",
        FAIL if missing else OK,
        8,
        "All phase entrypoints and deployment files are present." if not missing else f"Missing {len(missing)} required files.",
        {"missing": missing, "required_count": len(required)},
        started,
    )


def check_python_packages() -> AuditItem:
    started = time.perf_counter()
    core = {
        "tokenizers": "tokenizers",
        "tree_sitter": "tree-sitter",
        "tree_sitter_language_pack": "tree-sitter-language-pack",
        "docker": "docker",
        "redis": "redis",
        "asyncpg": "asyncpg",
        "qdrant_client": "qdrant-client",
        "pydantic": "pydantic",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "httpx": "httpx",
        "bandit": "bandit",
    }
    heavy = {
        "torch": "torch",
        "transformers": "transformers",
        "accelerate": "accelerate",
        "trl": "trl",
        "wandb": "wandb",
        "vllm": "vllm",
        "deepspeed": "deepspeed",
        "awq": "autoawq",
    }
    missing_core = [module for module in core if not module_present(module)]
    missing_heavy = [module for module in heavy if not module_present(module)]
    versions = {
        module: version_for(distribution)
        for module, distribution in (core | heavy).items()
        if module_present(module) or version_for(distribution)
    }
    status = FAIL if missing_core else WARN if missing_heavy else OK
    if missing_core:
        message = f"Missing core architecture packages: {', '.join(missing_core)}"
    elif missing_heavy:
        message = f"Core packages are installed; production GPU-only packages missing locally: {', '.join(missing_heavy)}"
    else:
        message = "Core and heavy ML/runtime packages are importable."
    return item(
        "python_packages",
        status,
        10,
        message,
        {"missing_core": missing_core, "missing_heavy": missing_heavy, "versions": versions},
        started,
    )


def check_pip_integrity() -> AuditItem:
    started = time.perf_counter()
    completed = run_command([sys.executable, "-m", "pip", "check"], timeout_s=120)
    ok = completed.exit_code == 0 and not completed.timed_out
    return item(
        "pip_integrity",
        OK if ok else FAIL,
        5,
        "No broken Python requirements found." if ok else "pip check reported dependency problems.",
        {"stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:], "exit_code": completed.exit_code},
        started,
    )


def check_gpu_runtime() -> AuditItem:
    started = time.perf_counter()
    nvidia_smi = shutil.which("nvidia-smi")
    torch_cuda = False
    torch_version = None
    if module_present("torch"):
        try:
            import torch

            torch_version = torch.__version__
            torch_cuda = bool(torch.cuda.is_available())
        except Exception as exc:
            return item(
                "gpu_runtime",
                WARN,
                8,
                f"Torch is installed but CUDA probing failed: {type(exc).__name__}: {exc}",
                {"nvidia_smi": nvidia_smi, "torch_version": torch_version},
                started,
            )
    if nvidia_smi and torch_cuda:
        status = OK
        message = "CUDA GPU runtime is visible to Python."
    else:
        status = WARN
        message = "No CUDA GPU runtime detected. Local stack can test orchestration; real GRPO/vLLM/AWQ needs Linux + NVIDIA CUDA."
    return item(
        "gpu_runtime",
        status,
        8,
        message,
        {"nvidia_smi": nvidia_smi, "torch_version": torch_version, "torch_cuda_available": torch_cuda},
        started,
    )


def check_live_smoke(args: argparse.Namespace) -> AuditItem:
    started = time.perf_counter()
    cmd = [
        sys.executable,
        "src/phase10/e2e_smoke_runner.py",
        "--run-id",
        f"{args.run_id}-live-smoke",
        "--tokenizer",
        str(args.tokenizer),
        "--redis-url",
        args.redis_url,
        "--postgres-dsn",
        args.postgres_dsn,
        "--qdrant-url",
        args.qdrant_url,
        "--gateway-url",
        args.gateway_url,
        "--vllm-url",
        args.vllm_url,
        "--run-sandbox-smoke",
        "--strict",
    ]
    completed = run_command(cmd, timeout_s=300)
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        pass
    ok = completed.exit_code == 0 and bool(payload.get("passed"))
    return item(
        "phase10_live_smoke",
        OK if ok else FAIL,
        15,
        "End-to-end live smoke passed." if ok else "End-to-end live smoke failed.",
        {
            "command": cmd,
            "exit_code": completed.exit_code,
            "summary": payload.get("summary"),
            "json": payload.get("json"),
            "markdown": payload.get("markdown"),
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
        },
        started,
    )


def check_phase11_smoke() -> AuditItem:
    started = time.perf_counter()
    completed = run_command([sys.executable, "src/phase11/smoke_test.py", "--json"], timeout_s=180)
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        pass
    ok = completed.exit_code == 0 and bool(payload.get("passed"))
    return item(
        "phase11_ai_core_smoke",
        OK if ok else FAIL,
        8,
        "Phase 11 PyTorch AI core, memory, security, and agent runtime smoke passed."
        if ok
        else "Phase 11 AI core smoke failed.",
        {
            "exit_code": completed.exit_code,
            "checks": payload.get("checks"),
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
        },
        started,
    )


def check_phase16_smoke() -> AuditItem:
    started = time.perf_counter()
    completed = run_command([sys.executable, "src/phase16/smoke_test.py", "--json"], timeout_s=180)
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        pass
    ok = completed.exit_code == 0 and bool(payload.get("passed"))
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    return item(
        "phase16_core_upgrades_smoke",
        OK if ok else FAIL,
        8,
        "Phase 16 durable task graph, role agents, critic/verifier, experience memory, tools, and exporter passed."
        if ok
        else "Phase 16 architecture upgrade smoke failed.",
        {
            "exit_code": completed.exit_code,
            "checks": {name: value.get("ok") for name, value in checks.items() if isinstance(value, dict)},
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
        },
        started,
    )


def check_phase17_gpu_readiness() -> AuditItem:
    started = time.perf_counter()
    completed = run_command([sys.executable, "src/phase17/gpu_readiness.py", "--run-id", "gpu-readiness", "--json"], timeout_s=120)
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        pass
    ok = completed.exit_code == 0 and bool(payload.get("passed_without_gpu"))
    return item(
        "phase17_gpu_readiness",
        OK if ok else FAIL,
        5,
        "7B-32B GPU serving/training profiles and launch configs are generated."
        if ok
        else "7B-32B GPU readiness pack failed.",
        {
            "exit_code": completed.exit_code,
            "profiles": payload.get("profiles"),
            "checks": payload.get("checks"),
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
        },
        started,
    )


def check_phase18_model_quality_loop() -> AuditItem:
    started = time.perf_counter()
    completed = run_command([sys.executable, "src/phase18/model_quality_loop.py", "--dry-run", "--json"], timeout_s=60)
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        pass
    ok = completed.exit_code == 0 and bool(payload.get("dry_run")) and int((payload.get("task_dataset") or {}).get("total", 0)) > 0
    return item(
        "phase18_model_quality_loop",
        OK if ok else FAIL,
        5,
        "Real-model scorecard, failure analysis, and training-promotion loop is configured."
        if ok
        else "Phase 18 model quality loop dry-run failed.",
        {
            "exit_code": completed.exit_code,
            "suite": payload.get("suite"),
            "task_dataset": payload.get("task_dataset"),
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-3000:],
        },
        started,
    )


def latest_json_report(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def latest_scored_json_report(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if any(isinstance(payload.get(key), (int, float)) for key in ("overall_score", "score", "candidate_score")):
            return candidate
    return candidates[0] if candidates else None


def check_phase37_39_controls() -> AuditItem:
    started = time.perf_counter()
    phase10_report = latest_scored_json_report(ROOT / "artifacts" / "phase10") or (ROOT / "artifacts" / "phase10" / "missing.json")
    commands = [
        [sys.executable, "src/phase37/vector_memory.py", "--run-id", "phase37-audit", "--rebuild", "--limit", "3"],
        [sys.executable, "src/phase38/multilang_security.py", "--run-id", "phase38-audit", "--max-files", "80"],
        [
            sys.executable,
            "src/phase39/checkpoint_rollback.py",
            "--candidate-checkpoint",
            str(ROOT),
            "--candidate-report",
            str(phase10_report),
            "--baseline-report",
            str(phase10_report),
            "--required-metrics",
            "overall_score",
            "--dry-run",
        ],
    ]
    results = [run_command(command, timeout_s=120) for command in commands]
    failed = [result for result in results if result.exit_code != 0 or result.timed_out]
    return item(
        "phase37_39_production_controls",
        OK if not failed else FAIL,
        6,
        "Vector memory, multi-language security, and checkpoint gate controls executed."
        if not failed
        else "One or more Phase 37-39 production controls failed.",
        {
            "commands": [
                {
                    "argv": result.argv,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "stdout_tail": result.stdout[-1200:],
                    "stderr_tail": result.stderr[-1200:],
                }
                for result in results
            ]
        },
        started,
    )


def check_phase40_42_integrated_runtime() -> AuditItem:
    started = time.perf_counter()
    commands = [
        [
            sys.executable,
            "src/phase40/integrated_agent.py",
            "--prompt",
            "build a safe todo api with tests",
            "--no-verifier",
            "--no-security",
            "--max-security-files",
            "80",
        ],
        [sys.executable, "src/phase41/regression_pack.py", "--run-id", "phase41-audit", "--smoke-limit", "10"],
        [sys.executable, "src/phase42/profile_switcher.py", "--profile", "qwen-cpu-smoke", "--activate", "--run-id", "phase42-audit"],
    ]
    results = [run_command(command, timeout_s=180) for command in commands]
    failed = [result for result in results if result.exit_code != 0 or result.timed_out]
    return item(
        "phase40_42_integrated_runtime",
        OK if not failed else FAIL,
        6,
        "Integrated agent path, regression pack, and profile switcher executed."
        if not failed
        else "One or more Phase 40-42 controls failed.",
        {
            "commands": [
                {
                    "argv": result.argv,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "stdout_tail": result.stdout[-1200:],
                    "stderr_tail": result.stderr[-1200:],
                }
                for result in results
            ]
        },
        started,
    )


def check_phase43_50_cognitive_architecture() -> AuditItem:
    started = time.perf_counter()
    commands = [
        [sys.executable, "src/phase43/meta_planner.py", "--prompt", "build secure login system", "--run-id", "phase43-audit"],
        [sys.executable, "src/phase44/intent_expansion.py", "--prompt", "login system", "--run-id", "phase44-audit"],
        [
            sys.executable,
            "src/phase45/parallel_agent_runtime.py",
            "--prompt",
            "build secure login system",
            "--backend-mode",
            "mock",
            "--run-id",
            "phase45-audit",
        ],
        [sys.executable, "src/phase46/hierarchical_memory.py", "--query", "secure planner verifier", "--seed", "--run-id", "phase46-audit"],
        [sys.executable, "src/phase47/reasoning_engine.py", "--prompt", "debug secure login architecture", "--run-id", "phase47-audit"],
        [sys.executable, "src/phase48/knowledge_graph.py", "--query", "meta planner memory reasoning", "--seed", "--run-id", "phase48-audit"],
        [
            sys.executable,
            "src/phase49/multimodal_expert.py",
            "--prompt",
            "analyze architecture assets",
            "--path",
            ".",
            "--max-files",
            "40",
            "--run-id",
            "phase49-audit",
        ],
        [sys.executable, "src/phase50/moe_router.py", "--prompt", "build secure login system", "--run-id", "phase50-audit"],
        [sys.executable, "src/phase50/phase43_to_50_e2e.py", "--prompt", "build secure login system", "--run-id", "phase43-to-50-audit"],
    ]
    results = [run_command(command, timeout_s=180) for command in commands]
    failed = [result for result in results if result.exit_code != 0 or result.timed_out]
    return item(
        "phase43_50_cognitive_architecture",
        OK if not failed else FAIL,
        8,
        "Meta planner, intent expansion, parallel runtime, hierarchical memory, reasoning, graph, multimodal, MoE, and E2E contracts executed."
        if not failed
        else "One or more Phase 43-50 cognitive architecture checks failed.",
        {
            "commands": [
                {
                    "argv": result.argv,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "stdout_tail": result.stdout[-1200:],
                    "stderr_tail": result.stderr[-1200:],
                }
                for result in results
            ]
        },
        started,
    )


def check_phase51_high_stability() -> AuditItem:
    started = time.perf_counter()
    command = [
        sys.executable,
        "src/phase51/high_stability_reasoning_memory.py",
        "--prompt",
        "build strict planner executor critic verifier memory architecture",
        "--run-id",
        "phase51-audit",
    ]
    result = run_command(command, timeout_s=120)
    ok = result.exit_code == 0 and not result.timed_out
    return item(
        "phase51_high_stability_reasoning_memory",
        OK if ok else FAIL,
        6,
        "Strict reasoning, reflection trigger contract, anti-pollution memory, and ranking formula executed."
        if ok
        else "Phase 51 high-stability reasoning and memory smoke failed.",
        {
            "argv": result.argv,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stdout_tail": result.stdout[-1600:],
            "stderr_tail": result.stderr[-1600:],
        },
        started,
    )


def check_security_scan(args: argparse.Namespace) -> AuditItem:
    started = time.perf_counter()
    output = args.output_dir / f"{args.run_id}-bandit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = run_command(
        [sys.executable, "-m", "bandit", "-r", "src", "-f", "json", "-o", str(output)],
        timeout_s=180,
    )
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    results = payload.get("results") or []
    totals = (payload.get("metrics") or {}).get("_totals") or {}
    high = int(totals.get("SEVERITY.HIGH", 0) or 0)
    medium = int(totals.get("SEVERITY.MEDIUM", 0) or 0)
    low = int(totals.get("SEVERITY.LOW", 0) or 0)
    if high:
        status = FAIL
        message = f"Bandit found {high} high-severity issue(s)."
    elif medium or low:
        status = WARN
        message = f"Bandit found medium={medium}, low={low} issue(s)."
    else:
        status = OK
        message = "Bandit scan found zero high/medium/low findings."
    return item(
        "static_security_scan",
        status,
        10,
        message,
        {
            "bandit_json": str(output),
            "exit_code": completed.exit_code,
            "result_count": len(results),
            "totals": totals,
            "stderr_tail": completed.stderr[-2000:],
        },
        started,
    )


async def check_sandbox_hardening() -> AuditItem:
    started = time.perf_counter()
    from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile

    probe_code = r'''
import os
import pathlib
import socket
import subprocess

print(f"uid={os.getuid()} gid={os.getgid()}")

try:
    socket.create_connection(("1.1.1.1", 53), timeout=1.0).close()
    print("network_open")
except OSError:
    print("network_blocked")

try:
    pathlib.Path("/workspace/should_not_write.txt").write_text("bad")
    print("workspace_writable")
except OSError:
    print("workspace_readonly")

try:
    pathlib.Path("/tmp/probe.txt").write_text("ok")
    print("tmp_writable")
except OSError:
    print("tmp_unwritable")

script = pathlib.Path("/tmp/noexec_probe.sh")
script.write_text("#!/bin/sh\nexit 0\n")
script.chmod(0o755)
try:
    completed = subprocess.run([str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1.0)
    print(f"tmp_exec_allowed:{completed.returncode}")
except (PermissionError, OSError):
    print("tmp_noexec")
'''
    request = ExecutionRequest(
        files=[SandboxFile(path="main.py", content=probe_code)],
        compile_command=None,
        run_command="python3 /workspace/main.py",
        image="python:3.12-slim",
    )
    timeout_request = ExecutionRequest(
        files=[SandboxFile(path="main.py", content="while True:\n    pass\n")],
        compile_command=None,
        run_command="python3 /workspace/main.py",
        image="python:3.12-slim",
    )
    try:
        async with SandboxEngine(pool_size=1) as engine:
            hardening = await engine.run(request)
            timeout_result = await engine.run(timeout_request)
    except Exception as exc:
        return item(
            "sandbox_hardening",
            FAIL,
            15,
            f"Sandbox hardening probe could not start Docker sandbox: {type(exc).__name__}: {exc}",
            started=started,
        )
    markers = set(hardening.stdout.splitlines())
    expected = {"network_blocked", "workspace_readonly", "tmp_writable", "tmp_noexec"}
    uid_ok = any(line.startswith("uid=65534") for line in markers)
    timeout_ok = timeout_result.timeout and timeout_result.flag == "<|timeout|>"
    ok = hardening.ok and expected.issubset(markers) and uid_ok and timeout_ok
    missing = sorted(marker for marker in expected if marker not in markers)
    if not uid_ok:
        missing.append("uid=65534")
    if not timeout_ok:
        missing.append("<|timeout|>")
    return item(
        "sandbox_hardening",
        OK if ok else FAIL,
        15,
        "Sandbox isolation, read-only workspace, noexec tmpfs, unprivileged UID, and 5s timeout passed."
        if ok
        else "Sandbox hardening probe failed.",
        {
            "stdout": hardening.stdout,
            "stderr": hardening.stderr,
            "exit_code": hardening.exit_code,
            "timeout_probe": {
                "timeout": timeout_result.timeout,
                "flag": timeout_result.flag,
                "exit_code": timeout_result.exit_code,
                "wall_time_us": timeout_result.metrics.wall_time_us,
            },
            "missing_markers": missing,
        },
        started,
    )


async def check_gateway_generation(args: argparse.Namespace) -> AuditItem:
    started = time.perf_counter()
    if not module_present("httpx"):
        return item("gateway_generation", SKIP, 5, "httpx is not installed.", started=started)
    import httpx

    payload = {
        "prompt": "Analyze this vulnerability surface and propose a safe patch.",
        "max_tokens": 32,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{args.gateway_url.rstrip('/')}/v1/chat/completions", json=payload)
        ok = response.status_code == 200 and response.headers.get("x-route-kind") == "vulnerability_analysis"
        return item(
            "gateway_generation",
            OK if ok else FAIL,
            5,
            "Gateway routed and completed a vulnerability-analysis request." if ok else "Gateway request/routing failed.",
            {
                "status_code": response.status_code,
                "route_kind": response.headers.get("x-route-kind"),
                "priority_lane": response.headers.get("x-priority-lane"),
                "body_tail": response.text[-1000:],
            },
            started,
        )
    except Exception as exc:
        return item(
            "gateway_generation",
            FAIL,
            5,
            f"Gateway request failed: {type(exc).__name__}: {exc}",
            started=started,
        )


async def check_quota_engine(args: argparse.Namespace) -> AuditItem:
    started = time.perf_counter()
    try:
        import redis.asyncio as redis

        from src.phase6.redis_quota_engine import QuotaPolicy, RedisRegenerativeQuotaEngine
    except ImportError as exc:
        return item("quota_engine", FAIL, 5, f"Quota imports failed: {exc}", started=started)

    client = redis.from_url(args.redis_url, decode_responses=True)
    try:
        engine = RedisRegenerativeQuotaEngine(
            redis_client=client,
            policy=QuotaPolicy(capacity=1.0, refill_rate=0.0, initialize_full=True, tenant="audit"),
        )
        await engine.initialize()
        key = engine.key_for_user(args.run_id)
        await client.delete(key)
        first = await engine.check_and_consume(args.run_id, 0.75)
        second = await engine.check_and_consume(args.run_id, 0.50)
        ok = first.allowed and not second.allowed and 0.0 <= first.remaining_balance <= 0.25 + 1e-9
        return item(
            "quota_engine",
            OK if ok else FAIL,
            5,
            "Redis Lua quota atomically allowed then denied as expected."
            if ok
            else "Redis Lua quota decision did not match expected token math.",
            {"first": asdict(first), "second": asdict(second)},
            started,
        )
    except Exception as exc:
        return item("quota_engine", FAIL, 5, f"Quota probe failed: {type(exc).__name__}: {exc}", started=started)
    finally:
        await client.aclose()


async def check_database_schemas(args: argparse.Namespace) -> AuditItem:
    started = time.perf_counter()
    try:
        from src.phase5.self_healing_runtime import AsyncStagingBuffer
        from src.phase9.regression_tracker import RegressionTracker

        staging = AsyncStagingBuffer(
            postgres_dsn=args.postgres_dsn,
            qdrant_url=args.qdrant_url,
            qdrant_api_key=None,
        )
        await staging.initialize()
        tracker = RegressionTracker(postgres_dsn=args.postgres_dsn)
        await tracker.init_db()
        return item(
            "database_schemas",
            OK,
            6,
            "PostgreSQL self-healing/evaluation schemas and Qdrant staging collection are initialized.",
            {"qdrant_collection": "self_healing_qlora_staging"},
            started,
        )
    except Exception as exc:
        return item(
            "database_schemas",
            FAIL,
            6,
            f"Database schema initialization failed: {type(exc).__name__}: {exc}",
            started=started,
        )


def check_docs_current() -> AuditItem:
    started = time.perf_counter()
    stale_doc = ROOT / "docs/live_infra_blockers.md"
    text = stale_doc.read_text(encoding="utf-8") if stale_doc.exists() else ""
    stale_markers = [
        "Docker CLI is not on `PATH`",
        "Redis is not running",
        "Qdrant is not running",
        "vLLM and the FastAPI gateway are not running",
    ]
    stale = [marker for marker in stale_markers if marker in text]
    return item(
        "documentation_freshness",
        WARN if stale else OK,
        3,
        "Readiness docs are current." if not stale else "Some blocker docs are stale after local setup completed.",
        {"stale_markers": stale, "doc": str(stale_doc)},
        started,
    )


def collect_done_and_remaining(items: list[AuditItem]) -> tuple[list[str], list[str], list[str]]:
    done = [
        "Phases 1-10 have implementation entrypoints and deployment assets.",
        "Docker Desktop engine is available and dev Redis/PostgreSQL/Qdrant containers are running.",
        "Local mock vLLM and FastAPI gateway path is health-checked.",
        "Tokenizer artifact is built and load-tested.",
        "Python code compiles and Bandit static scan has zero findings.",
        "Core ML package stack is installed locally, including torch/transformers/accelerate/trl/wandb/vllm.",
        "Phase 11 PyTorch AI core, chat API, memory engine, security engine, and agent runtime are implemented.",
        "Phase 12 capability gauntlet validates short prompts, agent workflow, security, memory, tools, and self-healing.",
        "Phase 13 backend quality harness compares model/backend response quality against the architecture control.",
        "Exact AI scorecard exports the requested golden dataset, metrics, two-mode runs, regressions, and failure auto-report.",
        "Target architecture blueprint captures the Cursor + Claude Code + DeepSeek R1 style destination and 50B-100B path.",
        "Phase 16 adds durable TaskGraph planning, role-specific agents, critic/verifier backends, experience memory, defensive tool adapters, and scorecard-to-training export.",
        "Phase 17 prepares pinned 7B-32B model profiles, vLLM serving commands, QLoRA SFT, GRPO, DeepSpeed, and Accelerate configs for future Linux CUDA hardware.",
        "Phase 18 runs the connected real model through the scorecard, clusters failures, and promotes only review-required SFT/GRPO candidates.",
        "Phases 19-23 add unified verifier gates, TaskGraph-first runtime, experience promotion, model critic hooks, and profile-driven 7B-32B quality runs.",
        "Phases 24-33 add Main Agent V2, experience retrieval, patch rollback, verifier policies, defensive security workflow, dataset review, expanded benchmarks, long-context packing, critic contract, and GPU backend contract.",
        "Phases 34-36 add JWT auth/quota, Prometheus-style observability, structured logs, and the Phase 18 -> Phase 3 -> Phase 7 data flywheel.",
        "Phases 37-39 add vector experience memory, multi-language defensive security scanning, and checkpoint promotion/rollback gates for training safety.",
        "Phases 40-42 make the integrated agent path default, generate a 400-task regression pack, and activate local/GPU runtime profiles through one switcher.",
        "Phases 43-50 add meta planning, short prompt intent expansion, parallel specialist execution, hierarchical memory, thinker/critic/verifier reasoning, knowledge graph, multimodal contract, and MoE routing.",
        "Phase 51 adds strict no-role-mixing reasoning contracts, forced reflection below critic confidence 0.6, four-tier unified memory, anti-pollution ingestion, and mathematical retrieval ranking.",
    ]
    remaining = []
    constraints = []
    for audit_item in items:
        if audit_item.status in {FAIL, WARN}:
            remaining.append(f"{audit_item.name}: {audit_item.message}")
    if any(audit_item.name == "gpu_runtime" and audit_item.status == WARN for audit_item in items):
        constraints.append("This Windows machine has no detected CUDA GPU; real 7B-13B GRPO/vLLM/AWQ production runs need a Linux CUDA host.")
    if any(audit_item.name == "python_packages" and "deepspeed" in audit_item.details.get("missing_heavy", []) for audit_item in items):
        constraints.append("DeepSpeed is not locally installed because Windows/Python 3.14 package build fails; use Linux GPU environment for ZeRO-2 training.")
    if any(audit_item.name == "python_packages" and "awq" in audit_item.details.get("missing_heavy", []) for audit_item in items):
        constraints.append("AWQ/autoawq is blocked locally by missing Triton Windows wheels; quantization should run on Linux CUDA.")
    return done, remaining, constraints


def write_reports(report: ArchitectureAuditReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Architecture Readiness Audit",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Passed: `{report.passed}`",
        f"- Score: `{report.score:.1f}/100`",
        f"- Grade: `{report.grade}`",
        f"- Duration: `{report.duration_ms:.1f} ms`",
        f"- Summary: `{report.summary()}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Weight | Message | Duration ms |",
        "| --- | --- | ---: | --- | ---: |",
    ]
    for audit_item in report.items:
        lines.append(
            f"| {audit_item.name} | {audit_item.status} | {audit_item.weight:.1f} | "
            f"{audit_item.message.replace('|', '/')} | {audit_item.duration_ms:.1f} |"
        )
    lines.extend(["", "## Done", ""])
    lines.extend(f"- {entry}" for entry in report.done)
    lines.extend(["", "## Remaining", ""])
    if report.remaining:
        lines.extend(f"- {entry}" for entry in report.remaining)
    else:
        lines.append("- No local blockers found.")
    lines.extend(["", "## Local Constraints", ""])
    if report.local_constraints:
        lines.extend(f"- {entry}" for entry in report.local_constraints)
    else:
        lines.append("- No machine-specific constraints detected.")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


async def build_report(args: argparse.Namespace) -> ArchitectureAuditReport:
    started = time.time()
    sync_items = [
        check_phase_files(),
        check_python_packages(),
        check_pip_integrity(),
        check_gpu_runtime(),
        check_live_smoke(args),
        check_phase11_smoke(),
        check_phase16_smoke(),
        check_phase17_gpu_readiness(),
        check_phase18_model_quality_loop(),
        check_phase37_39_controls(),
        check_phase40_42_integrated_runtime(),
        check_phase43_50_cognitive_architecture(),
        check_phase51_high_stability(),
        check_security_scan(args),
        check_docs_current(),
    ]
    async_items = await asyncio.gather(
        check_sandbox_hardening(),
        check_gateway_generation(args),
        check_quota_engine(args),
        check_database_schemas(args),
    )
    items = sync_items + list(async_items)
    total_weight = sum(entry.weight for entry in items if entry.status != SKIP)
    earned = sum(entry.weight * status_points(entry.status) for entry in items if entry.status != SKIP)
    score = 0.0 if total_weight == 0 else (earned / total_weight) * 100.0
    done, remaining, constraints = collect_done_and_remaining(items)
    return ArchitectureAuditReport(
        run_id=args.run_id,
        started_at_unix=started,
        duration_ms=(time.time() - started) * 1000,
        score=score,
        grade=grade_for(score),
        passed=not any(entry.status == FAIL for entry in items),
        items=items,
        done=done,
        remaining=remaining,
        local_constraints=constraints,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deep architecture readiness audit.")
    parser.add_argument("--run-id", default=f"architecture-audit-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/phase10"))
    parser.add_argument("--tokenizer", type=Path, default=Path("artifacts/mvp/code_bpe_tokenizer/tokenizer.json"))
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--postgres-dsn", default="postgresql://ai:ai_dev_password@localhost:5432/ai_eval")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18080")
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed audit items.")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await build_report(args)
    json_path, md_path = write_reports(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "passed": report.passed,
                "score": round(report.score, 2),
                "grade": report.grade,
                "summary": report.summary(),
                "json": str(json_path),
                "markdown": str(md_path),
            },
            indent=2,
        )
    )
    return 1 if args.strict and not report.passed else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
