"""Production readiness contract for Aeitron.

This module is the single source of truth for honest deployment status. It does
not fake external infrastructure: services that require Redis, Postgres, object
storage, Qdrant, GPU clusters, benchmark files, or scanner CLIs are marked with
explicit dependency states until those dependencies are present and tested.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from src.aeitron.identity.auth import AuthConfig
from src.aeitron.identity.quota import QuotaConfig
from src.aeitron.model_ops.backends import active_model_health
from src.aeitron.shared.config import load_active_profile
from src.aeitron.shared.schemas import StrictModel
from src.aeitron.training_workspace import TrainingProfileRegistry


ReadinessStatus = Literal[
    "production_ready",
    "production_ready_requires_external_service",
    "built_not_cluster_proven",
    "blocked_missing_dependency",
    "not_implemented",
]


class ReadinessCheck(StrictModel):
    subsystem: str
    status: ReadinessStatus
    summary: str
    required_dependencies: list[str] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    production_blocker: bool = False


class ProductionReadinessReport(StrictModel):
    mode: Literal["dev", "production"]
    status: str
    checks: list[ReadinessCheck]
    created_at_unix: float = Field(default_factory=time.time)

    @property
    def blockers(self) -> list[ReadinessCheck]:
        return [check for check in self.checks if check.production_blocker]

    def write(self, output_dir: str | Path) -> Path:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "production_readiness_report.json"
        target.write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        write_markdown(self, root / "production_readiness_report.md")
        return target


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "0") == "1"


def _external_dependency(name: str, *, env: str | None = None, executable: str | None = None, path: str | None = None) -> tuple[bool, str]:
    if env:
        value = os.environ.get(env)
        return bool(value), f"env:{env}"
    if executable:
        return shutil.which(executable) is not None, f"executable:{executable}"
    if path:
        return Path(path).exists(), f"path:{path}"
    return False, name


def _python_tool_present(executable: str, module: str | None = None) -> bool:
    if shutil.which(executable) is not None:
        return True
    scripts_candidate = Path(sys.executable).resolve().parent / "Scripts" / f"{executable}.exe"
    if scripts_candidate.exists():
        return True
    return bool(module and importlib.util.find_spec(module) is not None)


def _codeql_present() -> bool:
    env_path = os.environ.get("AEITRON_CODEQL_BIN", "")
    if env_path and Path(env_path).expanduser().exists():
        return True
    if shutil.which("codeql") is not None:
        return True
    return any(
        candidate.exists()
        for candidate in [
            Path.home() / ".aeitron" / "tools" / "codeql" / "codeql" / "codeql.exe",
            Path.home() / ".aeitron" / "tools" / "codeql" / "codeql" / "codeql",
        ]
    )


def _check_auth(mode: str) -> ReadinessCheck:
    config = AuthConfig.from_env()
    missing = []
    if not config.enabled:
        missing.append("AEITRON_AUTH_ENABLED=1")
    if not config.jwt_secret or len(config.jwt_secret) < 32:
        missing.append("AEITRON_JWT_SECRET length >= 32")
    if config.allow_token_issue:
        missing.append("AEITRON_ALLOW_TOKEN_ISSUE=0")
    return ReadinessCheck(
        subsystem="auth",
        status="production_ready" if not missing else "blocked_missing_dependency",
        summary="JWT auth is enforced for protected routes." if not missing else "JWT auth is not production-enforced.",
        required_dependencies=["AEITRON_AUTH_ENABLED", "AEITRON_JWT_SECRET"],
        missing_dependencies=missing,
        evidence={"enabled": config.enabled, "token_issue_allowed": config.allow_token_issue, "secret_present": bool(config.jwt_secret)},
        production_blocker=mode == "production" and bool(missing),
    )


def _check_quota(mode: str) -> ReadinessCheck:
    config = QuotaConfig.from_env()
    missing = []
    if not config.enabled:
        missing.append("AEITRON_QUOTA_ENABLED=1")
    if not config.redis_url:
        missing.append("AEITRON_REDIS_URL")
    return ReadinessCheck(
        subsystem="quota",
        status="production_ready_requires_external_service" if not missing else "blocked_missing_dependency",
        summary="Redis-backed regenerative quota is configured." if not missing else "Quota is missing Redis production configuration.",
        required_dependencies=["AEITRON_QUOTA_ENABLED", "AEITRON_REDIS_URL"],
        missing_dependencies=missing,
        evidence={"enabled": config.enabled, "redis_url_present": bool(config.redis_url)},
        production_blocker=mode == "production" and bool(missing),
    )


def _check_model_backend(mode: str) -> ReadinessCheck:
    active = active_model_health()
    profile = load_active_profile()
    backend = str(active.get("backend") or "mock")
    missing = []
    if backend == "mock":
        missing.append("non-mock AEITRON_MODEL_BACKEND")
    if backend in {"aeitron_serving", "aeitron_serving", "active"} and not active.get("endpoint"):
        missing.append("AEITRON_MODEL_ENDPOINT")
    checkpoint_manifest = os.environ.get("AEITRON_CHECKPOINT_MANIFEST", "")
    tokenizer_path = os.environ.get("AEITRON_TOKENIZER_PATH", "")
    if backend in {"aeitron_serving", "aeitron_serving", "active"}:
        if not checkpoint_manifest or not Path(checkpoint_manifest).exists():
            missing.append("AEITRON_CHECKPOINT_MANIFEST existing file")
        if not tokenizer_path or not Path(tokenizer_path).exists():
            missing.append("AEITRON_TOKENIZER_PATH existing file")
    return ReadinessCheck(
        subsystem="serving",
        status="production_ready" if not missing else "blocked_missing_dependency",
        summary="Native Aeitron serving backend is selected." if not missing else "Serving is still using mock/test-double configuration.",
        required_dependencies=["AEITRON_MODEL_BACKEND", "AEITRON_MODEL_ENDPOINT", "AEITRON_CHECKPOINT_MANIFEST", "AEITRON_TOKENIZER_PATH"],
        missing_dependencies=missing,
        evidence={
            "backend": backend,
            "model_name": active.get("model_name"),
            "checkpoint_manifest": checkpoint_manifest,
            "tokenizer_path": tokenizer_path,
            "active_profile": profile.get("profile", {}),
        },
        production_blocker=mode == "production" and bool(missing),
    )


def _check_external_services(mode: str) -> list[ReadinessCheck]:
    specs = [
        ("postgres", "AEITRON_DATABASE_URL", "Postgres persistence/migrations"),
        ("object_storage", "AEITRON_OBJECT_STORE_URI", "S3/MinIO dataset/checkpoint artifact storage"),
        ("qdrant", "AEITRON_QDRANT_URL", "Distributed vector memory/index backend"),
        ("otel", "AEITRON_OTEL_EXPORTER_OTLP_ENDPOINT", "OpenTelemetry exporter"),
    ]
    checks: list[ReadinessCheck] = []
    for subsystem, env, summary in specs:
        present, dep = _external_dependency(subsystem, env=env)
        checks.append(
            ReadinessCheck(
                subsystem=subsystem,
                status="production_ready_requires_external_service" if present else "blocked_missing_dependency",
                summary=summary if present else f"{summary} is not configured.",
                required_dependencies=[dep],
                missing_dependencies=[] if present else [dep],
                evidence={"env_present": present},
                production_blocker=mode == "production" and not present,
            )
        )
    return checks


def _check_cli_tools(mode: str) -> list[ReadinessCheck]:
    tools = [
        ("semgrep", "semgrep", "semgrep", "Semgrep static security scan"),
        ("codeql", "codeql", None, "CodeQL semantic security scan"),
        ("bandit", "bandit", "bandit", "Bandit Python security scan"),
        ("pip_audit", "pip-audit", "pip_audit", "Dependency vulnerability scan"),
        ("docker", "docker", None, "Docker sandbox runtime"),
        ("kubectl", "kubectl", None, "Kubernetes server-side deployment validation"),
    ]
    checks: list[ReadinessCheck] = []
    for subsystem, executable, module, summary in tools:
        present = _codeql_present() if subsystem == "codeql" else (_python_tool_present(executable, module) if module else shutil.which(executable) is not None)
        dep = f"executable:{executable}" if module is None else f"executable:{executable} or python module:{module}"
        checks.append(
            ReadinessCheck(
                subsystem=subsystem,
                status="production_ready_requires_external_service" if present else "blocked_missing_dependency",
                summary=summary if present else f"{summary} executable is missing.",
                required_dependencies=[dep],
                missing_dependencies=[] if present else [dep],
                evidence={"executable_present": present},
                production_blocker=mode == "production" and not present,
            )
        )
    return checks


def _check_training_stack(mode: str) -> list[ReadinessCheck]:
    cuda_available = False
    torch_version = ""
    try:
        import torch

        torch_version = str(torch.__version__)
        cuda_available = bool(torch.cuda.is_available())
    except Exception:
        pass
    deepspeed_available = importlib.util.find_spec("deepspeed") is not None
    vllm_available = importlib.util.find_spec("vllm") is not None
    megatron_root = os.environ.get("MEGATRON_LM_ROOT", "")
    megatron_available = bool(megatron_root and Path(megatron_root).exists())
    hf_export_dir = os.environ.get("AEITRON_HF_EXPORT_DIR", "")
    hf_export_ready = bool(
        hf_export_dir
        and all((Path(hf_export_dir) / name).exists() for name in ["config.json", "tokenizer.json"])
        and ((Path(hf_export_dir) / "model.safetensors").exists() or (Path(hf_export_dir) / "pytorch_model.bin").exists())
    )
    trt_build = shutil.which("trtllm-build") is not None
    vllm_trt_missing = []
    if not hf_export_ready:
        vllm_trt_missing.append("AEITRON_HF_EXPORT_DIR with config.json/model.safetensors/tokenizer.json")
    if not vllm_available:
        vllm_trt_missing.append("python module: vllm")
    if not trt_build:
        vllm_trt_missing.append("executable:trtllm-build")
    deepspeed_missing = []
    if not deepspeed_available:
        deepspeed_missing.append("python module: deepspeed")
    if not megatron_available:
        deepspeed_missing.append("MEGATRON_LM_ROOT existing checkout")
    return [
        ReadinessCheck(
            subsystem="pretraining",
            status="production_ready" if cuda_available else "blocked_missing_dependency",
            summary="CUDA training runtime is available." if cuda_available else "CUDA training runtime is not available on this host.",
            required_dependencies=["CUDA-capable GPU", "PyTorch CUDA build"],
            missing_dependencies=[] if cuda_available else ["CUDA-capable GPU or compatible PyTorch CUDA build"],
            evidence={"torch_version": torch_version, "cuda_available": cuda_available},
            production_blocker=mode == "production" and not cuda_available,
        ),
        ReadinessCheck(
            subsystem="fsdp",
            status="built_not_cluster_proven",
            summary="Native PyTorch FSDP runtime is wired but must pass a real multi-GPU cluster gate.",
            required_dependencies=["torchrun", "multi-GPU cluster", "NCCL"],
            missing_dependencies=[],
            evidence={"runtime_path": "src.aeitron.model_ops.pretrain_loop"},
            production_blocker=False,
        ),
        ReadinessCheck(
            subsystem="deepspeed_megatron",
            status="built_not_cluster_proven" if deepspeed_available else "blocked_missing_dependency",
            summary=(
                "DeepSpeed ZeRO runtime adapter is wired; Megatron still requires an external checkout and cluster gate."
                if deepspeed_available
                else "DeepSpeed/Megatron dependencies are missing."
            ),
            required_dependencies=["deepspeed", "Megatron-LM checkout", "cluster release gate"],
            missing_dependencies=deepspeed_missing,
            evidence={"deepspeed_module": deepspeed_available, "megatron_root": megatron_root},
            production_blocker=mode == "production",
        ),
        ReadinessCheck(
            subsystem="vllm_tensorrt",
            status="production_ready_requires_external_service" if not vllm_trt_missing else "blocked_missing_dependency",
            summary=(
                "HF/vLLM export path is built; TensorRT-LLM requires runtime engine build validation."
                if hf_export_ready
                else "HF/vLLM/TensorRT export artifacts or runtime dependencies are missing."
            ),
            required_dependencies=["Aeitron-to-HF/vLLM converter", "TensorRT-LLM conversion plugin"],
            missing_dependencies=vllm_trt_missing,
            evidence={"hf_export_dir": hf_export_dir, "hf_export_ready": hf_export_ready, "vllm_module": vllm_available, "trtllm_build": trt_build},
            production_blocker=mode == "production",
        ),
    ]


def _check_training_workspace(mode: str) -> ReadinessCheck:
    database_url = os.environ.get("AEITRON_DATABASE_URL", "")
    redis_url = os.environ.get("AEITRON_REDIS_URL", "")
    object_uri = os.environ.get("AEITRON_OBJECT_STORE_URI", "")
    jwt_secret = os.environ.get("AEITRON_JWT_SECRET", "")
    missing = []
    if not database_url.startswith(("postgres://", "postgresql://")):
        missing.append("AEITRON_DATABASE_URL=postgresql://...")
    if not redis_url.startswith(("redis://", "rediss://")):
        missing.append("AEITRON_REDIS_URL=redis[s]://...")
    if not object_uri.startswith("s3://"):
        missing.append("AEITRON_OBJECT_STORE_URI=s3://...")
    if len(jwt_secret) < 32:
        missing.append("AEITRON_JWT_SECRET length >= 32")
    profile_count = 0
    try:
        profile_count = len(TrainingProfileRegistry.from_file().profiles)
    except (FileNotFoundError, ValueError):
        missing.append("valid config/training_profiles.json")
    kubernetes_client = importlib.util.find_spec("kubernetes") is not None or shutil.which("kubectl") is not None
    if not kubernetes_client:
        missing.append("kubernetes Python client or kubectl")
    return ReadinessCheck(
        subsystem="training_workspace",
        status="built_not_cluster_proven" if not missing else "blocked_missing_dependency",
        summary=(
            "Durable training control-plane dependencies are configured; scheduler paths still require live cluster proof."
            if not missing
            else "Training workspace production dependencies are incomplete."
        ),
        required_dependencies=["Postgres", "Redis Streams", "S3/MinIO", "JWT signing key", "Kubernetes scheduler client"],
        missing_dependencies=missing,
        evidence={
            "profile_count": profile_count,
            "postgres_configured": database_url.startswith(("postgres://", "postgresql://")),
            "redis_configured": redis_url.startswith(("redis://", "rediss://")),
            "object_storage_configured": object_uri.startswith("s3://"),
            "scheduler_client_present": kubernetes_client,
            "cluster_proof": False,
        },
        production_blocker=mode == "production" and bool(missing),
    )


def _check_agent_collaboration(mode: str) -> ReadinessCheck:
    migration = Path("src/aeitron/db/migrations/0005_agent_collaboration.sql")
    database_url = os.environ.get("AEITRON_DATABASE_URL", "")
    postgres_configured = database_url.startswith(("postgres://", "postgresql://"))
    proof_path = Path(
        os.environ.get(
            "AEITRON_AGENT_COLLABORATION_PROOF_REPORT",
            "artifacts/aeitron/agent-collaboration-proof/agent_collaboration_postgres_proof.json",
        )
    )
    live_proven = False
    if proof_path.is_file():
        try:
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            live_proven = (
                proof.get("status") == "passed"
                and proof.get("migration") == "0005_agent_collaboration"
                and proof.get("atomic_claim_winner_count") == 1
                and proof.get("blackboard_stale_update_rejected") is True
                and proof.get("durable_message_round_trip") is True
            )
        except (OSError, json.JSONDecodeError):
            live_proven = False
    missing = []
    if not migration.exists():
        missing.append(str(migration))
    if mode == "production" and not postgres_configured:
        missing.append("AEITRON_DATABASE_URL=postgresql://...")
    if mode == "production" and not live_proven:
        missing.append(f"valid live Postgres lifecycle proof: {proof_path}")
    status: ReadinessStatus
    if missing:
        status = "blocked_missing_dependency"
    elif live_proven:
        status = "production_ready_requires_external_service"
    else:
        status = "built_not_cluster_proven"
    return ReadinessCheck(
        subsystem="agent_collaboration",
        status=status,
        summary=(
            "Concurrent TaskGraph leases, typed messages, blackboard CAS, bounded reflection, and failure intelligence are wired."
            if not missing
            else "Agent collaboration persistence is missing a production dependency."
        ),
        required_dependencies=[
            "migration 0005_agent_collaboration",
            "Postgres in production",
            "live concurrent lifecycle proof",
        ],
        missing_dependencies=missing,
        evidence={
            "migration_present": migration.exists(),
            "postgres_configured": postgres_configured,
            "live_postgres_lifecycle_proven": live_proven,
            "proof_report": str(proof_path),
            "max_reflection_revisions": 3,
            "typed_message_kinds": ["proposal", "evidence", "challenge", "review", "decision"],
        },
        production_blocker=mode == "production" and bool(missing),
    )


def _check_agent_execution(mode: str) -> ReadinessCheck:
    engine = Path("src/aeitron/runtime/execution.py")
    scorecard_runner = Path("src/aeitron/evaluation/agent_scorecard.py")
    report_path = Path(
        os.environ.get(
            "AEITRON_AGENT_SCORECARD_REPORT",
            "artifacts/aeitron/agent-scorecard/agent_scorecard.json",
        )
    )
    scorecard_proven = False
    scorecard_evidence: dict[str, Any] = {}
    if report_path.is_file():
        try:
            scorecard_evidence = json.loads(report_path.read_text(encoding="utf-8"))
            scorecard_proven = (
                scorecard_evidence.get("status") == "passed"
                and scorecard_evidence.get("policy_mode") == "strict"
                and 50 <= int(scorecard_evidence.get("task_count") or 0) <= 100
                and float(scorecard_evidence.get("architecture_reliability_score") or 0.0) >= 0.95
                and float(scorecard_evidence.get("workflow_completion_score") or 0.0) >= 0.80
                and float(scorecard_evidence.get("sandbox_test_pass_rate") or 0.0) >= 0.80
                and int(scorecard_evidence.get("regression_count") or 0) == 0
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            scorecard_proven = False
    missing = [str(path) for path in [engine, scorecard_runner] if not path.is_file()]
    if mode == "production" and not scorecard_proven:
        missing.append(f"strict 50-100 repository task scorecard proof: {report_path}")
    if missing:
        status: ReadinessStatus = "blocked_missing_dependency"
    elif scorecard_proven:
        status = "production_ready"
    else:
        status = "built_not_cluster_proven"
    return ReadinessCheck(
        subsystem="verified_agent_execution",
        status=status,
        summary=(
            "Architect, coder, sandbox tester, defensive reviewers, critic, verifier, bounded revision, and patch transaction are wired."
            if not missing
            else "Verified agent execution is missing code or a required real-repository proof."
        ),
        required_dependencies=[
            "non-mock Aeitron scratch serving backend",
            "Docker sandbox",
            "Semgrep or CodeQL",
            "strict 50-100 repository task scorecard",
        ],
        missing_dependencies=missing,
        evidence={
            "execution_engine_present": engine.is_file(),
            "scorecard_runner_present": scorecard_runner.is_file(),
            "scorecard_report": str(report_path),
            "scorecard_proven": scorecard_proven,
            "scorecard_summary": {
                key: scorecard_evidence.get(key)
                for key in [
                    "status",
                    "policy_mode",
                    "task_count",
                    "architecture_reliability_score",
                    "workflow_completion_score",
                    "sandbox_test_pass_rate",
                    "regression_count",
                ]
            },
            "max_patch_revisions": 3,
            "original_mutation_before_accept": False,
        },
        production_blocker=mode == "production" and bool(missing),
    )


def _check_benchmark_files(mode: str, benchmark_dir: str | Path) -> ReadinessCheck:
    root = Path(benchmark_dir)
    required = [
        "humaneval.jsonl",
        "mbpp.jsonl",
        "swe_bench_style.jsonl",
        "cyberseceval_style.jsonl",
        "aeitron_security.jsonl",
        "safety_prompts.jsonl",
    ]
    missing = [str(root / name) for name in required if not (root / name).exists()]
    return ReadinessCheck(
        subsystem="benchmark_eval",
        status="production_ready" if not missing else "blocked_missing_dependency",
        summary="Required benchmark suites are present." if not missing else "Required benchmark files are missing.",
        required_dependencies=[str(root / name) for name in required],
        missing_dependencies=missing,
        evidence={"benchmark_dir": str(root), "required_count": len(required), "missing_count": len(missing)},
        production_blocker=mode == "production" and bool(missing),
    )


def run_production_readiness(
    *,
    mode: Literal["dev", "production"] = "dev",
    benchmark_dir: str | Path = "data/eval",
) -> ProductionReadinessReport:
    checks = [
        _check_auth(mode),
        _check_quota(mode),
        _check_model_backend(mode),
        *_check_external_services(mode),
        *_check_cli_tools(mode),
        *_check_training_stack(mode),
        _check_training_workspace(mode),
        _check_agent_collaboration(mode),
        _check_agent_execution(mode),
        _check_benchmark_files(mode, benchmark_dir),
    ]
    failed = any(check.production_blocker for check in checks)
    return ProductionReadinessReport(mode=mode, status="failed" if failed else "passed", checks=checks)


def write_markdown(report: ProductionReadinessReport, path: str | Path) -> Path:
    target = Path(path)
    lines = [
        "# Aeitron Production Readiness Report",
        "",
        f"- mode: {report.mode}",
        f"- status: {report.status}",
        f"- blockers: {len(report.blockers)}",
        "",
        "| subsystem | status | blocker | missing | summary |",
        "|---|---|---:|---|---|",
    ]
    for check in report.checks:
        missing = ", ".join(check.missing_dependencies)
        lines.append(f"| {check.subsystem} | {check.status} | {check.production_blocker} | {missing} | {check.summary} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Aeitron production readiness gate.")
    parser.add_argument("--mode", choices=["dev", "production"], default="dev")
    parser.add_argument("--benchmark-dir", default="data/eval")
    parser.add_argument("--output-dir", default="artifacts/aeitron/production-readiness")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = run_production_readiness(mode=args.mode, benchmark_dir=args.benchmark_dir)
    report.write(args.output_dir)
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    if report.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

