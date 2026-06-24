#!/usr/bin/env python
"""AWQ INT4 quantization and HumanEval benchmark helper."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
from pathlib import Path


def looks_like_local_model_path(value: str) -> bool:
    expanded = Path(value).expanduser()
    return expanded.exists() or value.startswith((".", "/", "~", "\\")) or (len(value) > 1 and value[1] == ":")


def validate_revision_policy(model_path: str, revision: str | None, allow_unpinned: bool) -> None:
    if allow_unpinned or revision or looks_like_local_model_path(model_path):
        return
    raise SystemExit(
        f"Remote model '{model_path}' requires an explicit --model-revision pin. "
        "Pass --allow-unpinned-model-revision only for trusted experiments."
    )


def load_calibration_samples(path: Path, limit: int = 128) -> list[str]:
    samples: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            text = payload.get("text") or payload.get("prompt") or payload.get("content")
            if text:
                samples.append(str(text))
            if len(samples) >= limit:
                break
    if len(samples) < limit:
        raise ValueError(f"need {limit} calibration samples, found {len(samples)}")
    return samples


def quantize_awq(
    model_path: str,
    output_path: Path,
    calibration_samples: list[str],
    group_size: int,
    revision: str | None,
    trust_remote_code: bool,
) -> None:
    from awq import AutoAWQForCausalLM
    import torch
    from transformers import AutoTokenizer

    quant_config = {"zero_point": True, "q_group_size": group_size, "w_bit": 4, "version": "GEMM"}
    model = AutoAWQForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True,
        revision=revision,
        trust_remote_code=trust_remote_code,
    )
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=trust_remote_code, revision=revision)
    model.quantize(
        tokenizer,
        quant_config=quant_config,
        calib_data=calibration_samples,
        n_parallel_calib_samples=32,
        max_calib_samples=128,
        max_calib_seq_len=4096,
    )
    output_path.mkdir(parents=True, exist_ok=True)
    model.save_quantized(str(output_path), safetensors=True, shard_size="4GB")
    tokenizer.save_pretrained(str(output_path))


def run_humaneval(model_path: str, output_dir: Path, limit: int | None) -> dict[str, str | int | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "python",
        "-m",
        "bigcode_eval.main",
        "--model",
        model_path,
        "--tasks",
        "humaneval",
        "--max_length_generation",
        "512",
        "--temperature",
        "0.2",
        "--n_samples",
        "1",
        "--allow_code_execution",
        "--save_generations",
        "--metric_output_path",
        str(output_dir / "humaneval_metrics.json"),
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)  # nosec B603
    return {
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "metrics_path": str(output_dir / "humaneval_metrics.json"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quantize bf16 checkpoint to AWQ INT4 and benchmark HumanEval.")
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--model-revision")
    parser.add_argument("--allow-unpinned-model-revision", action="store_true")
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--output-path", required=True, type=Path)
    parser.add_argument("--calibration-jsonl", required=True, type=Path)
    parser.add_argument("--group-size", type=int, default=128)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--benchmark-output-dir", type=Path, default=Path("artifacts/phase8_humaneval"))
    parser.add_argument("--humaneval-limit", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_revision_policy(args.model_path, args.model_revision, args.allow_unpinned_model_revision)
    samples = load_calibration_samples(args.calibration_jsonl, limit=128)
    quantize_awq(args.model_path, args.output_path, samples, args.group_size, args.model_revision, args.trust_remote_code)
    result = {"quantized_model": str(args.output_path), "calibration_samples": len(samples)}
    if args.benchmark:
        result["humaneval"] = run_humaneval(str(args.output_path), args.benchmark_output_dir, args.humaneval_limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
