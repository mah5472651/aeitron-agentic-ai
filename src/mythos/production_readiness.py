"""Production readiness contract for Mythos.

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


def _python_tool_present(executable: str, module: str | None = None) -> bool:
    if shutil.which(executable) is not None:
        return True
    scripts_candidate = Path(sys.executable).resolve().parent / "Scripts" / f"{executable}.exe"
    if scripts_candidate.exists():
        return True
    return bool(module and importlib.util.find_spec(module) is not None)


def _codeql_present() -> bool:
    env_path = os.environ.get("MYTHOS_CODEQL_BIN", "")
    if env_path and Path(env_path).expanduser().exists():
        return True
    if shutil.which("codeql") is not None:
        return True
    return any(
        candidate.exists()
        for candidate in [
            Path.home() / ".mythos" / "tools" / "codeql" / "codeql" / "codeql.exe",
            Path.home() / ".mythos" / "tools" / "codeql" / "codeql" / "codeql",
        ]
    )


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
    if backend in {"aeitron_serving", "mythos_serving", "active"} and not active.get("endpoint"):
        missing.append("MYTHOS_MODEL_ENDPOINT")
    checkpoint_manifest = os.environ.get("MYTHOS_CHECKPOINT_MANIFEST", "")
    tokenizer_path = os.environ.get("MYTHOS_TOKENIZER_PATH", "")
    if backend in {"aeitron_serving", "mythos_serving", "active"}:
        if not checkpoint_manifest or not Path(checkpoint_manifest).exists():
            missing.append("MYTHOS_CHECKPOINT_MANIFEST existing file")
        if not tokenizer_path or not Path(tokenizer_path).exists():
            missing.append("MYTHOS_TOKENIZER_PATH existing file")
    return ReadinessCheck(
        subsystem="serving",
        status="production_ready" if not missing else "blocked_missing_dependency",
        summary="Native Aeitron serving backend is selected." if not missing else "Serving is still using mock/test-double configuration.",
        required_dependencies=["MYTHOS_MODEL_BACKEND", "MYTHOS_MODEL_ENDPOINT", "MYTHOS_CHECKPOINT_MANIFEST", "MYTHOS_TOKENIZER_PATH"],
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
    hf_export_dir = os.environ.get("MYTHOS_HF_EXPORT_DIR", "")
    hf_export_ready = bool(
        hf_export_dir
        and all((Path(hf_export_dir) / name).exists() for name in ["config.json", "tokenizer.json"])
        and ((Path(hf_export_dir) / "model.safetensors").exists() or (Path(hf_export_dir) / "pytorch_model.bin").exists())
    )
    trt_build = shutil.which("trtllm-build") is not None
    vllm_trt_missing = []
    if not hf_export_ready:
        vllm_trt_missing.append("MYTHOS_HF_EXPORT_DIR with config.json/model.safetensors/tokenizer.json")
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
            evidence={"runtime_path": "src.mythos.model_ops.pretrain_loop"},
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
    parser = argparse.ArgumentParser(description="Run Mythos production readiness gate.")
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
