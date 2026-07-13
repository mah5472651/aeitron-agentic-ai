"""Production readiness contract for Mythos.

This module is the single source of truth for honest deployment status. It does
not fake external infrastructure: services that require Redis, Postgres, object
storage, Qdrant, GPU clusters, benchmark files, or scanner CLIs are marked with
explicit dependency states until those dependencies are present and tested.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from src.mythos.identity.auth import AuthConfig
from src.mythos.identity.quota import QuotaConfig
from src.mythos.model_ops.backends import active_model_health
from src.mythos.shared.config import load_active_profile
from src.mythos.shared.schemas import StrictModel


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


def _check_auth(mode: str) -> ReadinessCheck:
    config = AuthConfig.from_env()
    missing = []
    if not config.enabled:
        missing.append("MYTHOS_AUTH_ENABLED=1")
    if not config.jwt_secret or len(config.jwt_secret) < 32:
        missing.append("MYTHOS_JWT_SECRET length >= 32")
    if config.allow_token_issue:
        missing.append("MYTHOS_ALLOW_TOKEN_ISSUE=0")
    return ReadinessCheck(
        subsystem="auth",
        status="production_ready" if not missing else "blocked_missing_dependency",
        summary="JWT auth is enforced for protected routes." if not missing else "JWT auth is not production-enforced.",
        required_dependencies=["MYTHOS_AUTH_ENABLED", "MYTHOS_JWT_SECRET"],
        missing_dependencies=missing,
        evidence={"enabled": config.enabled, "token_issue_allowed": config.allow_token_issue, "secret_present": bool(config.jwt_secret)},
        production_blocker=mode == "production" and bool(missing),
    )


def _check_quota(mode: str) -> ReadinessCheck:
    config = QuotaConfig.from_env()
    missing = []
    if not config.enabled:
        missing.append("MYTHOS_QUOTA_ENABLED=1")
    if not config.redis_url:
        missing.append("MYTHOS_REDIS_URL")
    return ReadinessCheck(
        subsystem="quota",
        status="production_ready_requires_external_service" if not missing else "blocked_missing_dependency",
        summary="Redis-backed regenerative quota is configured." if not missing else "Quota is missing Redis production configuration.",
        required_dependencies=["MYTHOS_QUOTA_ENABLED", "MYTHOS_REDIS_URL"],
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
        missing.append("non-mock MYTHOS_MODEL_BACKEND")
    if backend in {"mythos_serving", "active"} and not active.get("endpoint"):
        missing.append("MYTHOS_MODEL_ENDPOINT")
    return ReadinessCheck(
        subsystem="serving",
        status="production_ready_requires_external_service" if not missing else "blocked_missing_dependency",
        summary="Native Mythos serving backend is selected." if not missing else "Serving is still using mock/test-double configuration.",
        required_dependencies=["MYTHOS_MODEL_BACKEND", "MYTHOS_MODEL_ENDPOINT", "Mythos scratch checkpoint"],
        missing_dependencies=missing,
        evidence={"backend": backend, "model_name": active.get("model_name"), "active_profile": profile.get("profile", {})},
        production_blocker=mode == "production" and bool(missing),
    )


def _check_external_services(mode: str) -> list[ReadinessCheck]:
    specs = [
        ("postgres", "MYTHOS_DATABASE_URL", "Postgres persistence/migrations"),
        ("object_storage", "MYTHOS_OBJECT_STORE_URI", "S3/MinIO dataset/checkpoint artifact storage"),
        ("qdrant", "MYTHOS_QDRANT_URL", "Distributed vector memory/index backend"),
        ("otel", "MYTHOS_OTEL_EXPORTER_OTLP_ENDPOINT", "OpenTelemetry exporter"),
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
        ("semgrep", "semgrep", "Semgrep static security scan"),
        ("codeql", "codeql", "CodeQL semantic security scan"),
        ("bandit", "bandit", "Bandit Python security scan"),
        ("pip_audit", "pip-audit", "Dependency vulnerability scan"),
        ("docker", "docker", "Docker sandbox runtime"),
        ("kubectl", "kubectl", "Kubernetes server-side deployment validation"),
    ]
    checks: list[ReadinessCheck] = []
    for subsystem, executable, summary in tools:
        present, dep = _external_dependency(subsystem, executable=executable)
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
            evidence={"runtime_path": "src.mythos.model_ops.pretrain_loop"},
            production_blocker=False,
        ),
        ReadinessCheck(
            subsystem="deepspeed_megatron",
            status="blocked_missing_dependency",
            summary="DeepSpeed/Megatron are launch contracts until dedicated runtime adapters pass cluster gates.",
            required_dependencies=["deepspeed", "Megatron-LM checkout", "cluster release gate"],
            missing_dependencies=["runtime adapter not proven"],
            evidence={},
            production_blocker=mode == "production",
        ),
        ReadinessCheck(
            subsystem="vllm_tensorrt",
            status="not_implemented",
            summary="vLLM/TensorRT native Mythos checkpoint adapters are not implemented yet.",
            required_dependencies=["Mythos-to-HF/vLLM converter", "TensorRT-LLM conversion plugin"],
            missing_dependencies=["native serving adapter"],
            evidence={},
            production_blocker=mode == "production",
        ),
    ]


def _check_benchmark_files(mode: str, benchmark_dir: str | Path) -> ReadinessCheck:
    root = Path(benchmark_dir)
    required = [
        "humaneval.jsonl",
        "mbpp.jsonl",
        "swe_bench_style.jsonl",
        "cyberseceval_style.jsonl",
        "mythos_security.jsonl",
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
        _check_benchmark_files(mode, benchmark_dir),
    ]
    failed = any(check.production_blocker for check in checks)
    return ProductionReadinessReport(mode=mode, status="failed" if failed else "passed", checks=checks)


def write_markdown(report: ProductionReadinessReport, path: str | Path) -> Path:
    target = Path(path)
    lines = [
        "# Mythos Production Readiness Report",
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
    parser = argparse.ArgumentParser(description="Run Mythos production readiness gate.")
    parser.add_argument("--mode", choices=["dev", "production"], default="dev")
    parser.add_argument("--benchmark-dir", default="data/eval")
    parser.add_argument("--output-dir", default="artifacts/mythos/production-readiness")
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
