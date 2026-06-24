#!/usr/bin/env python
"""GPU readiness pack generator for 7B-32B coding/security models.

This phase intentionally does not require a GPU. It prepares pinned model
profiles, vLLM commands, QLoRA/SFT/GRPO launch commands, DeepSpeed configs, and
machine-readiness reports so a future Linux CUDA box can start quickly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy" / "gpu"
PROFILE_DIR = DEPLOY_DIR / "profiles"
ARTIFACT_DIR = ROOT / "artifacts" / "phase17"


@dataclass(frozen=True)
class ModelProfile:
    name: str
    family: str
    model_id: str
    revision: str
    size_class: str
    role: str
    recommended_tp_size: int
    max_model_len: int
    bf16_inference_vram_gb: int
    awq_inference_vram_gb: int
    qlora_training_vram_gb: int
    preferred_training: str
    notes: list[str] = field(default_factory=list)

    def env(self) -> dict[str, str]:
        return {
            "MODEL_PROFILE": self.name,
            "MODEL_PATH": self.model_id,
            "MODEL_REVISION": self.revision,
            "SERVED_MODEL_NAME": self.name,
            "TP_SIZE": str(self.recommended_tp_size),
            "MAX_MODEL_LEN": str(self.max_model_len),
            "VLLM_DTYPE": "bfloat16",
            "QUANTIZATION": "none",
            "GPU_MEMORY_UTILIZATION": "0.92",
        }


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GpuReadinessReport:
    run_id: str
    created_at_unix: float
    profiles: list[ModelProfile]
    checks: list[ReadinessCheck]
    generated_files: list[str]
    next_steps: list[str]

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks if check.name != "cuda_runtime")


def model_profiles() -> list[ModelProfile]:
    return [
        ModelProfile(
            name="qwen2.5-coder-7b",
            family="qwen",
            model_id="Qwen/Qwen2.5-Coder-7B-Instruct",
            revision="c03e6d358207e414f1eca0bb1891e29f1db0e242",
            size_class="7B",
            role="first serious coding/security baseline",
            recommended_tp_size=1,
            max_model_len=32768,
            bf16_inference_vram_gb=24,
            awq_inference_vram_gb=10,
            qlora_training_vram_gb=24,
            preferred_training="QLoRA SFT first, then GRPO on verified tasks",
            notes=["Best first GPU target.", "Single 24GB GPU can be enough for QLoRA experiments."],
        ),
        ModelProfile(
            name="qwen2.5-coder-14b",
            family="qwen",
            model_id="Qwen/Qwen2.5-Coder-14B-Instruct",
            revision="aedcc2d42b622764e023cf882b6652e646b95671",
            size_class="14B",
            role="higher quality coding/security baseline",
            recommended_tp_size=1,
            max_model_len=32768,
            bf16_inference_vram_gb=40,
            awq_inference_vram_gb=18,
            qlora_training_vram_gb=48,
            preferred_training="QLoRA SFT on 48GB GPU or multi-GPU ZeRO",
            notes=["Better quality target after 7B pipeline is green."],
        ),
        ModelProfile(
            name="qwen2.5-coder-32b",
            family="qwen",
            model_id="Qwen/Qwen2.5-Coder-32B-Instruct",
            revision="381fc969f78efac66bc87ff7ddeadb7e73c218a7",
            size_class="32B",
            role="strong local lab target before 50B-100B",
            recommended_tp_size=2,
            max_model_len=32768,
            bf16_inference_vram_gb=96,
            awq_inference_vram_gb=40,
            qlora_training_vram_gb=96,
            preferred_training="Multi-GPU QLoRA/SFT, then GRPO with strict verifier gating",
            notes=["Use tensor parallel serving.", "Treat as pre-50B quality target."],
        ),
        ModelProfile(
            name="deepseek-coder-6.7b",
            family="deepseek",
            model_id="deepseek-ai/deepseek-coder-6.7b-instruct",
            revision="e5d64addd26a6a1db0f9b863abf6ee3141936807",
            size_class="7B",
            role="alternative coding baseline",
            recommended_tp_size=1,
            max_model_len=16384,
            bf16_inference_vram_gb=24,
            awq_inference_vram_gb=10,
            qlora_training_vram_gb=24,
            preferred_training="QLoRA SFT and comparison scorecard",
            notes=["Good alternate baseline for head-to-head evaluation."],
        ),
        ModelProfile(
            name="deepseek-coder-v2-lite",
            family="deepseek",
            model_id="deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct",
            revision="e434a23f91ba5b4923cf6c9d9a238eb4a08e3a11",
            size_class="16B MoE-lite",
            role="MoE-style practical research baseline",
            recommended_tp_size=1,
            max_model_len=16384,
            bf16_inference_vram_gb=40,
            awq_inference_vram_gb=18,
            qlora_training_vram_gb=48,
            preferred_training="Experiment after dense 7B baseline is stable",
            notes=["Useful for future MoE thinking without full 500B infrastructure."],
        ),
    ]


def profile_by_name(name: str) -> ModelProfile:
    for profile in model_profiles():
        if profile.name == name:
            return profile
    raise ValueError(f"unknown profile: {name}")


def vllm_command(profile: ModelProfile, *, host: str = "127.0.0.1", port: int = 8000) -> list[str]:
    return [
        "python",
        "src/phase8/vllm_server.py",
        "--model",
        profile.model_id,
        "--model-revision",
        profile.revision,
        "--served-model-name",
        profile.name,
        "--host",
        host,
        "--port",
        str(port),
        "--tp-size",
        str(profile.recommended_tp_size),
        "--max-model-len",
        str(profile.max_model_len),
        "--dtype",
        "bfloat16",
        "--quantization",
        "none",
    ]


def qlora_sft_command(profile: ModelProfile) -> list[str]:
    return [
        "accelerate",
        "launch",
        "--config_file",
        "deploy/gpu/accelerate_zero2.yaml",
        "src/phase17/qlora_sft_training.py",
        "--model-name-or-path",
        profile.model_id,
        "--model-revision",
        profile.revision,
        "--dataset",
        "artifacts/phase16/scorecard_failures_sft.jsonl",
        "--output-dir",
        f"artifacts/training/{profile.name}-qlora-sft",
        "--load-in-4bit",
        "--bf16",
        "--gradient-checkpointing",
    ]


def grpo_command(profile: ModelProfile) -> list[str]:
    return [
        "accelerate",
        "launch",
        "--config_file",
        "deploy/gpu/accelerate_zero2.yaml",
        "src/phase7/grpo_training_loop.py",
        "--model-name-or-path",
        profile.model_id,
        "--model-revision",
        profile.revision,
        "--dataset",
        "artifacts/phase16/scorecard_failures_grpo.jsonl",
        "--output-dir",
        f"artifacts/training/{profile.name}-grpo",
        "--deepspeed",
        "--bf16",
    ]


def check_cuda_runtime() -> ReadinessCheck:
    nvidia_smi = shutil.which("nvidia-smi")
    torch_cuda = False
    torch_version = None
    if importlib.util.find_spec("torch") is not None:
        try:
            import torch

            torch_version = torch.__version__
            torch_cuda = bool(torch.cuda.is_available())
        except Exception as exc:
            return ReadinessCheck("cuda_runtime", False, f"torch CUDA probe failed: {type(exc).__name__}: {exc}")
    ok = bool(nvidia_smi and torch_cuda and platform.system().lower() == "linux")
    return ReadinessCheck(
        "cuda_runtime",
        ok,
        "Linux CUDA runtime is ready." if ok else "No Linux CUDA runtime detected here; configs are generated for future GPU host.",
        {"nvidia_smi": nvidia_smi, "torch_version": torch_version, "torch_cuda": torch_cuda, "platform": platform.platform()},
    )


def check_required_assets() -> list[ReadinessCheck]:
    required = [
        ROOT / "src" / "phase7" / "grpo_training_loop.py",
        ROOT / "src" / "phase8" / "vllm_server.py",
        ROOT / "src" / "phase16" / "sft_exporter.py",
        ROOT / "artifacts" / "phase16" / "scorecard_failures_sft.jsonl",
        ROOT / "artifacts" / "phase16" / "scorecard_failures_grpo.jsonl",
        ROOT / "requirements-linux-gpu.txt",
    ]
    missing = [path.relative_to(ROOT).as_posix() for path in required if not path.exists()]
    return [
        ReadinessCheck(
            "gpu_training_assets",
            not missing,
            "Training/serving assets are present." if not missing else f"Missing {len(missing)} asset(s).",
            {"missing": missing},
        )
    ]


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_deploy_assets(profiles: list[ModelProfile]) -> list[Path]:
    generated: list[Path] = []
    generated.append(write_json(DEPLOY_DIR / "model_profiles.json", [asdict(profile) for profile in profiles]))
    generated.append(
        write_json(
            DEPLOY_DIR / "deepspeed_zero2.json",
            {
                "bf16": {"enabled": True},
                "zero_optimization": {
                    "stage": 2,
                    "offload_optimizer": {"device": "none"},
                    "allgather_partitions": True,
                    "allgather_bucket_size": 2e8,
                    "overlap_comm": True,
                    "reduce_scatter": True,
                    "reduce_bucket_size": 2e8,
                    "contiguous_gradients": True,
                },
                "gradient_accumulation_steps": "auto",
                "train_micro_batch_size_per_gpu": "auto",
                "train_batch_size": "auto",
                "wall_clock_breakdown": False,
            },
        )
    )
    generated.append(
        write_json(
            DEPLOY_DIR / "deepspeed_zero3.json",
            {
                "bf16": {"enabled": True},
                "zero_optimization": {
                    "stage": 3,
                    "offload_optimizer": {"device": "cpu", "pin_memory": True},
                    "offload_param": {"device": "cpu", "pin_memory": True},
                    "overlap_comm": True,
                    "contiguous_gradients": True,
                    "sub_group_size": 1e9,
                    "stage3_prefetch_bucket_size": "auto",
                    "stage3_param_persistence_threshold": "auto",
                    "stage3_max_live_parameters": 1e9,
                    "stage3_max_reuse_distance": 1e9,
                },
                "gradient_accumulation_steps": "auto",
                "train_micro_batch_size_per_gpu": "auto",
                "train_batch_size": "auto",
            },
        )
    )
    generated.append(
        write_text(
            DEPLOY_DIR / "accelerate_zero2.yaml",
            "\n".join(
                [
                    "compute_environment: LOCAL_MACHINE",
                    "distributed_type: DEEPSPEED",
                    "mixed_precision: bf16",
                    "num_machines: 1",
                    "num_processes: 1",
                    "deepspeed_config:",
                    "  deepspeed_config_file: deploy/gpu/deepspeed_zero2.json",
                    "  zero3_init_flag: false",
                    "use_cpu: false",
                    "",
                ]
            ),
        )
    )
    generated.append(
        write_json(
            DEPLOY_DIR / "qlora_defaults.json",
            {
                "lora_r": 64,
                "lora_alpha": 128,
                "lora_dropout": 0.05,
                "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
                "load_in_4bit": True,
                "bnb_4bit_quant_type": "nf4",
                "bnb_4bit_compute_dtype": "bfloat16",
            },
        )
    )
    for profile in profiles:
        env_lines = [f'{key}="{value}"' for key, value in profile.env().items()]
        generated.append(write_text(PROFILE_DIR / f"{profile.name}.env", "\n".join(env_lines) + "\n"))
    generated.extend(write_shell_scripts())
    return generated


def write_shell_scripts() -> list[Path]:
    run_vllm = """#!/usr/bin/env bash
set -euo pipefail
PROFILE="${MODEL_PROFILE:-qwen2.5-coder-7b}"
PORT="${VLLM_PORT:-8000}"
python src/phase17/gpu_readiness.py --print-vllm-command --profile "$PROFILE" --port "$PORT" | tee artifacts/phase17/last_vllm_command.txt
exec $(python src/phase17/gpu_readiness.py --print-vllm-command --profile "$PROFILE" --port "$PORT")
"""
    train_sft = """#!/usr/bin/env bash
set -euo pipefail
PROFILE="${MODEL_PROFILE:-qwen2.5-coder-7b}"
python src/phase17/gpu_readiness.py --print-sft-command --profile "$PROFILE" | tee artifacts/phase17/last_sft_command.txt
exec $(python src/phase17/gpu_readiness.py --print-sft-command --profile "$PROFILE")
"""
    train_grpo = """#!/usr/bin/env bash
set -euo pipefail
PROFILE="${MODEL_PROFILE:-qwen2.5-coder-7b}"
python src/phase17/gpu_readiness.py --print-grpo-command --profile "$PROFILE" | tee artifacts/phase17/last_grpo_command.txt
exec $(python src/phase17/gpu_readiness.py --print-grpo-command --profile "$PROFILE")
"""
    return [
        write_text(DEPLOY_DIR / "run_vllm_profile.sh", run_vllm),
        write_text(DEPLOY_DIR / "train_qlora_sft_profile.sh", train_sft),
        write_text(DEPLOY_DIR / "train_grpo_profile.sh", train_grpo),
    ]


def render_markdown(report: GpuReadinessReport) -> str:
    lines = [
        "# Phase 17 GPU Readiness Pack",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Passed without GPU: `{report.passed}`",
        "",
        "## Profiles",
        "",
        "| Profile | Model | Revision | TP | BF16 VRAM | QLoRA VRAM | Role |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for profile in report.profiles:
        lines.append(
            f"| {profile.name} | {profile.model_id} | `{profile.revision[:12]}` | {profile.recommended_tp_size} | "
            f"{profile.bf16_inference_vram_gb}GB | {profile.qlora_training_vram_gb}GB | {profile.role} |"
        )
    lines.extend(["", "## Checks", "", "| Check | OK | Message |", "| --- | --- | --- |"])
    for check in report.checks:
        lines.append(f"| {check.name} | `{check.ok}` | {check.message.replace('|', '/')} |")
    lines.extend(["", "## Generated Files", ""])
    lines.extend(f"- `{path}`" for path in report.generated_files)
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {step}" for step in report.next_steps)
    return "\n".join(lines) + "\n"


def build_report(run_id: str) -> GpuReadinessReport:
    profiles = model_profiles()
    generated = write_deploy_assets(profiles)
    checks = [*check_required_assets(), check_cuda_runtime()]
    report = GpuReadinessReport(
        run_id=run_id,
        created_at_unix=time.time(),
        profiles=profiles,
        checks=checks,
        generated_files=[path.relative_to(ROOT).as_posix() for path in generated],
        next_steps=[
            "Run the exact scorecard against the connected Qwen backend now.",
            "On Linux CUDA, start with MODEL_PROFILE=qwen2.5-coder-7b deploy/gpu/run_vllm_profile.sh.",
            "Run QLoRA SFT first, then GRPO only on verifier-gated tasks.",
            "Move from 7B to 14B/32B only after eval gates improve.",
        ],
    )
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = asdict(report)
    payload["passed_without_gpu"] = report.passed
    write_text(ARTIFACT_DIR / "gpu-readiness.md", render_markdown(report))
    write_json(ARTIFACT_DIR / "gpu-readiness.json", payload)
    return report


def print_command(kind: str, profile_name: str, port: int) -> None:
    profile = profile_by_name(profile_name)
    if kind == "vllm":
        command = vllm_command(profile, port=port)
    elif kind == "sft":
        command = qlora_sft_command(profile)
    elif kind == "grpo":
        command = grpo_command(profile)
    else:
        raise ValueError(f"unsupported command kind: {kind}")
    print(" ".join(command))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate 7B-32B GPU readiness pack.")
    parser.add_argument("--run-id", default="gpu-readiness")
    parser.add_argument("--profile", default="qwen2.5-coder-7b")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--print-vllm-command", action="store_true")
    parser.add_argument("--print-sft-command", action="store_true")
    parser.add_argument("--print-grpo-command", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.print_vllm_command:
        print_command("vllm", args.profile, args.port)
        return
    if args.print_sft_command:
        print_command("sft", args.profile, args.port)
        return
    if args.print_grpo_command:
        print_command("grpo", args.profile, args.port)
        return
    report = build_report(args.run_id)
    payload = {
        "run_id": report.run_id,
        "passed_without_gpu": report.passed,
        "profiles": [profile.name for profile in report.profiles],
        "checks": [asdict(check) for check in report.checks],
        "json": str(ARTIFACT_DIR / "gpu-readiness.json"),
        "markdown": str(ARTIFACT_DIR / "gpu-readiness.md"),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else payload)


if __name__ == "__main__":
    main()
