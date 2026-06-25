#!/usr/bin/env python
"""GPU-independent preflight for reviewed SFT/GRPO data and launch assets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PreflightCheck(StrictModel):
    name: str
    status: str = Field(pattern="^(pass|warn|fail)$")
    required_for_architecture: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TrainingPreflightReport(StrictModel):
    run_id: str
    architecture_ready: bool
    data_ready: bool
    gpu_ready: bool
    train_now: bool
    reviewed_sft_rows: int
    grpo_groups: int
    checks: list[PreflightCheck]
    recommendations: list[str]
    created_at_unix: float = Field(default_factory=time.time)


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    if not path.exists():
        return rows, invalid
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                invalid += 1
        except json.JSONDecodeError:
            invalid += 1
    return rows, invalid


def valid_sft_row(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    chosen = str(row.get("chosen") or "")
    return bool(
        str(row.get("prompt") or "").strip()
        and chosen.strip()
        and metadata.get("verifier_status") == "passed"
        and metadata.get("first_pass") is True
        and str(metadata.get("source_run_id") or "").strip()
    )


def valid_grpo_group(row: dict[str, Any]) -> bool:
    candidates = row.get("candidates") if isinstance(row.get("candidates"), list) else []
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return bool(
        str(row.get("prompt") or "").strip()
        and len(candidates) == 8
        and metadata.get("sandbox_verified") is True
        and all(isinstance(item, dict) and isinstance(item.get("reward"), (int, float)) for item in candidates)
    )


def tokenizer_check(path: Path, required_tokens: list[str]) -> PreflightCheck:
    if not path.exists():
        return PreflightCheck(
            name="tokenizer_contract",
            status="warn",
            required_for_architecture=False,
            message="Tokenizer artifact is not built in this checkout; bootstrap it before GPU training.",
            details={"path": str(path)},
        )
    try:
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(path))
        vocab = tokenizer.get_vocab()
        missing = [token for token in required_tokens if token not in vocab]
        size = tokenizer.get_vocab_size()
        status = "pass" if not missing and size == 64000 else "warn"
        return PreflightCheck(
            name="tokenizer_contract",
            status=status,
            required_for_architecture=False,
            message="Tokenizer loaded and inspected." if status == "pass" else "Tokenizer needs vocabulary/control-token review before training.",
            details={"path": str(path), "vocab_size": size, "expected_vocab_size": 64000, "missing_tokens": missing},
        )
    except Exception as exc:
        return PreflightCheck(
            name="tokenizer_contract",
            status="fail",
            required_for_architecture=True,
            message=f"Tokenizer could not be loaded: {type(exc).__name__}: {exc}",
            details={"path": str(path)},
        )


def build_report(manifest_path: Path, *, run_id: str) -> TrainingPreflightReport:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks: list[PreflightCheck] = []

    required_files = [
        *manifest["schemas"].values(),
        *manifest["training_entrypoints"].values(),
        *manifest["launch_configs"],
        manifest["checkpoint_gate"],
    ]
    missing_files = [path for path in required_files if not (ROOT / path).exists()]
    checks.append(
        PreflightCheck(
            name="training_assets",
            status="fail" if missing_files else "pass",
            required_for_architecture=True,
            message="Training schemas, entrypoints, configs, and checkpoint gate are present." if not missing_files else "Required training assets are missing.",
            details={"missing": missing_files, "checked": len(required_files)},
        )
    )

    reviewed_rows: list[dict[str, Any]] = []
    invalid_sft = 0
    for relative in manifest["reviewed_sft_sources"]:
        rows, malformed = load_jsonl(ROOT / relative)
        reviewed_rows.extend(row for row in rows if valid_sft_row(row))
        invalid_sft += malformed + sum(1 for row in rows if not valid_sft_row(row))
    checks.append(
        PreflightCheck(
            name="reviewed_sft_data",
            status="pass" if reviewed_rows and invalid_sft == 0 else "warn",
            required_for_architecture=False,
            message="Reviewed first-pass SFT rows are ready." if reviewed_rows else "No reviewed SFT rows yet; architecture is ready but training data is not.",
            details={"valid_rows": len(reviewed_rows), "invalid_rows": invalid_sft},
        )
    )

    grpo_groups: list[dict[str, Any]] = []
    invalid_grpo = 0
    for relative in manifest["grpo_sources"]:
        rows, malformed = load_jsonl(ROOT / relative)
        grpo_groups.extend(row for row in rows if valid_grpo_group(row))
        invalid_grpo += malformed + sum(1 for row in rows if not valid_grpo_group(row))
    checks.append(
        PreflightCheck(
            name="verified_grpo_data",
            status="pass" if grpo_groups and invalid_grpo == 0 else "warn",
            required_for_architecture=False,
            message="Verified eight-candidate GRPO groups are ready." if grpo_groups else "No verified GRPO groups yet; generate them only through sandbox-backed evaluation.",
            details={"valid_groups": len(grpo_groups), "invalid_groups": invalid_grpo},
        )
    )

    checks.append(tokenizer_check(ROOT / manifest["tokenizer"], list(manifest["required_control_tokens"])))

    torch_present = importlib.util.find_spec("torch") is not None
    cuda_available = False
    torch_version = None
    if torch_present:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        torch_version = torch.__version__
    checks.append(
        PreflightCheck(
            name="cuda_runtime",
            status="pass" if cuda_available else "warn",
            required_for_architecture=False,
            message="CUDA runtime is ready." if cuda_available else "CUDA is not present locally; run training later on Linux NVIDIA hardware.",
            details={"torch_present": torch_present, "torch_version": torch_version, "cuda_available": cuda_available},
        )
    )

    architecture_ready = not any(check.status == "fail" and check.required_for_architecture for check in checks)
    data_ready = bool(reviewed_rows) and invalid_sft == 0
    gpu_ready = cuda_available
    recommendations = []
    if not data_ready:
        recommendations.append("Run rejection sampling, Phase 29 review, and export only verifier-passed first-pass rows.")
    if not grpo_groups:
        recommendations.append("Generate GRPO groups with exactly eight sandbox-scored candidates per prompt.")
    if not gpu_ready:
        recommendations.append("Keep architecture validation local; launch QLoRA/GRPO only after Linux CUDA is available.")
    recommendations.append("After training, require Phase 39 checkpoint comparison before promotion.")
    return TrainingPreflightReport(
        run_id=run_id,
        architecture_ready=architecture_ready,
        data_ready=data_ready,
        gpu_ready=gpu_ready,
        train_now=architecture_ready and data_ready and gpu_ready,
        reviewed_sft_rows=len(reviewed_rows),
        grpo_groups=len(grpo_groups),
        checks=checks,
        recommendations=recommendations,
    )


def write_report(report: TrainingPreflightReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "training-preflight-latest.json"
    md_path = output_dir / "training-preflight-latest.md"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Mythos V1 Training Preflight",
        "",
        f"- Architecture ready: `{report.architecture_ready}`",
        f"- Data ready: `{report.data_ready}`",
        f"- GPU ready: `{report.gpu_ready}`",
        f"- Train now: `{report.train_now}`",
        "",
        "| Check | Status | Message |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {check.name} | {check.status} | {check.message.replace('|', '/')} |" for check in report.checks)
    lines.extend(["", "## Recommendations", *[f"- {item}" for item in report.recommendations]])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate reviewed data and GPU training launch readiness.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "config" / "mythos_v1_training_manifest.json")
    parser.add_argument("--run-id", default=f"mythos-v1-training-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "mythos_v1")
    parser.add_argument("--strict-architecture", action="store_true")
    parser.add_argument("--require-train-now", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args.manifest, run_id=args.run_id)
    json_path, md_path = write_report(report, args.output_dir)
    print(json.dumps({"architecture_ready": report.architecture_ready, "data_ready": report.data_ready, "gpu_ready": report.gpu_ready, "train_now": report.train_now, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    failed = args.strict_architecture and not report.architecture_ready
    failed = failed or (args.require_train_now and not report.train_now)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

