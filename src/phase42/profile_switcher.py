#!/usr/bin/env python
"""Phase 42 production profile switcher.

Activates local CPU, mock, or future GPU/vLLM model profiles through one
consistent contract.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase17.gpu_readiness import grpo_command, model_profiles, qlora_sft_command, vllm_command


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RuntimeProfile(StrictModel):
    name: str
    kind: str
    family: str
    size_class: str
    backend: str
    model_name: str
    endpoint: str
    model_id: str | None = None
    revision: str | None = None
    requires_cuda: bool = False
    recommended_tp_size: int = 1
    max_model_len: int = 32768
    notes: list[str] = Field(default_factory=list)
    commands: dict[str, list[str]] = Field(default_factory=dict)


class ProfileSwitchReport(StrictModel):
    run_id: str
    active_profile: str
    status: str
    profile: dict[str, Any]
    generated_files: list[str]
    checks: dict[str, Any]
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


def local_profiles() -> list[RuntimeProfile]:
    return [
        RuntimeProfile(
            name="mock-local",
            kind="local",
            family="mock",
            size_class="tiny",
            backend="mock",
            model_name="security-coder",
            endpoint="local",
            notes=["Fastest architecture plumbing profile.", "No reasoning quality measurement."],
        ),
        RuntimeProfile(
            name="qwen-cpu-smoke",
            kind="local",
            family="qwen",
            size_class="0.5B",
            backend="openai_compatible",
            model_name="Qwen/Qwen2.5-Coder-0.5B-Instruct",
            endpoint="http://127.0.0.1:8016/v1",
            model_id="Qwen/Qwen2.5-Coder-0.5B-Instruct",
            revision="local_or_cached",
            notes=[
                "Target local Qwen behavior-check profile.",
                "On some Windows CPU Torch/Transformers stacks this checkpoint can crash natively while loading.",
                "Use tiny-llama-cpu-smoke when the local Qwen checkpoint is unstable.",
                "Not the final target model.",
            ],
        ),
        RuntimeProfile(
            name="tiny-llama-cpu-smoke",
            kind="local",
            family="llama",
            size_class="tiny",
            backend="openai_compatible",
            model_name="hf-internal-testing/tiny-random-LlamaForCausalLM",
            endpoint="http://127.0.0.1:8016/v1",
            model_id="hf-internal-testing/tiny-random-LlamaForCausalLM",
            revision="9fb191250dd56d0ba7ec9785a025ed29c03d5998",
            notes=[
                "Stable real Hugging Face/OpenAI-compatible plumbing profile for Windows CPU smoke tests.",
                "Measures backend connectivity and architecture routing, not final reasoning quality.",
                "Use 7B-32B Qwen/DeepSeek/Llama profiles on Linux CUDA for real quality.",
            ],
        ),
    ]


def gpu_runtime_profiles() -> list[RuntimeProfile]:
    profiles: list[RuntimeProfile] = []
    for profile in model_profiles():
        profiles.append(
            RuntimeProfile(
                name=profile.name,
                kind="gpu_vllm",
                family=profile.family,
                size_class=profile.size_class,
                backend="openai_compatible",
                model_name=profile.name,
                endpoint="http://127.0.0.1:8000/v1",
                model_id=profile.model_id,
                revision=profile.revision,
                requires_cuda=True,
                recommended_tp_size=profile.recommended_tp_size,
                max_model_len=profile.max_model_len,
                notes=profile.notes,
                commands={
                    "serve_vllm": vllm_command(profile),
                    "qlora_sft": qlora_sft_command(profile),
                    "grpo": grpo_command(profile),
                },
            )
        )
    return profiles


def all_profiles() -> dict[str, RuntimeProfile]:
    return {profile.name: profile for profile in local_profiles() + gpu_runtime_profiles()}


def runtime_checks(profile: RuntimeProfile) -> dict[str, Any]:
    nvidia_smi = shutil.which("nvidia-smi")
    torch_cuda = False
    torch_version = None
    if importlib.util.find_spec("torch") is not None:
        try:
            import torch

            torch_version = torch.__version__
            torch_cuda = bool(torch.cuda.is_available())
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    cuda_ready = bool(nvidia_smi and torch_cuda and platform.system().lower() == "linux")
    ok = True if not profile.requires_cuda else cuda_ready
    return {
        "ok": ok,
        "requires_cuda": profile.requires_cuda,
        "cuda_ready": cuda_ready,
        "nvidia_smi": nvidia_smi,
        "torch_version": torch_version,
        "torch_cuda": torch_cuda,
        "platform": platform.platform(),
    }


def env_for_profile(profile: RuntimeProfile) -> dict[str, str]:
    env = {
        "MYTHOS_ACTIVE_PROFILE": profile.name,
        "PHASE11_BACKEND": profile.backend,
        "PHASE11_MODEL_ENDPOINT": profile.endpoint,
        "PHASE11_MODEL_NAME": profile.model_name,
        "PHASE24_BACKEND": profile.backend,
        "PHASE24_MODEL_ENDPOINT": profile.endpoint,
        "PHASE24_MODEL_NAME": profile.model_name,
        "PHASE40_BACKEND": profile.backend,
        "PHASE40_MODEL_ENDPOINT": profile.endpoint,
        "PHASE40_MODEL_NAME": profile.model_name,
        "SCORECARD_BACKEND": profile.backend,
        "SCORECARD_MODEL_ENDPOINT": profile.endpoint,
        "SCORECARD_MODEL_NAME": profile.model_name,
    }
    if profile.name in {"qwen-cpu-smoke", "tiny-llama-cpu-smoke"}:
        env["PHASE16_AGENT_MAX_NEW_TOKENS"] = "220"
        env["PHASE40_AGENT_BACKEND_MODE"] = "auto"
    elif profile.kind == "gpu_vllm":
        env["PHASE16_AGENT_MAX_NEW_TOKENS"] = "700"
        env["PHASE40_AGENT_BACKEND_MODE"] = "active"
    else:
        env["PHASE16_AGENT_MAX_NEW_TOKENS"] = "360"
        env["PHASE40_AGENT_BACKEND_MODE"] = "auto"
    return env


def write_powershell_env(path: Path, env: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Generated by Phase 42 profile switcher."]
    for key, value in env.items():
        escaped = value.replace("'", "''")
        lines.append(f"$env:{key} = '{escaped}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def activate_profile(profile: RuntimeProfile, *, output_dir: Path, run_id: str) -> ProfileSwitchReport:
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = runtime_checks(profile)
    active_path = ROOT / "config" / "active_model_profile.json"
    env_path = ROOT / "config" / "active_model_profile.ps1"
    latest_path = output_dir / "profile-switch-latest.json"
    generated = []
    active_payload = {
        "profile": profile.model_dump(),
        "env": env_for_profile(profile),
        "updated_at_unix": time.time(),
        "run_id": run_id,
    }
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(active_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    generated.append(str(active_path))
    generated.append(str(write_powershell_env(env_path, active_payload["env"])))
    status = "active" if checks.get("ok") else "configured_waiting_for_cuda"
    recommendation = (
        "Profile activated for local use."
        if status == "active"
        else "Profile contract is written; run it on Linux CUDA hardware before serving/training."
    )
    report = ProfileSwitchReport(
        run_id=run_id,
        active_profile=profile.name,
        status=status,
        profile=profile.model_dump(),
        generated_files=generated,
        checks=checks,
        recommendation=recommendation,
    )
    report_path = output_dir / f"{run_id}.json"
    report_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def list_profiles() -> dict[str, Any]:
    return {name: profile.model_dump() for name, profile in all_profiles().items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 42 production profile switcher.")
    parser.add_argument("--profile", default="qwen-cpu-smoke")
    parser.add_argument("--run-id", default=f"phase42-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase42")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--activate", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profiles = all_profiles()
    if args.list:
        print(json.dumps({"profiles": list_profiles()}, indent=2, ensure_ascii=False))
        return
    if args.profile not in profiles:
        raise SystemExit(f"unknown profile: {args.profile}. Available: {', '.join(sorted(profiles))}")
    report = activate_profile(profiles[args.profile], output_dir=args.output_dir, run_id=args.run_id)
    print(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
