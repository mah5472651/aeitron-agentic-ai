#!/usr/bin/env python
"""vLLM production server launcher for 7B-13B coding/security LLMs.

This script wraps `vllm serve` with a hardened, explicit configuration:
- PagedAttention KV cache via high GPU memory utilization
- continuous batching limits: max_num_seqs=256, max_num_batched_tokens=8192
- tensor parallelism: tp_size=2
- AWQ INT4 quantized weight loading
"""

from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class VllmServeConfig:
    model: str
    served_model_name: str
    model_revision: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    tensor_parallel_size: int = 2
    max_num_seqs: int = 256
    max_num_batched_tokens: int = 8192
    gpu_memory_utilization: float = 0.94
    quantization: str = "awq"
    dtype: str = "auto"
    max_model_len: int = 8192
    trust_remote_code: bool = False
    enable_prefix_caching: bool = True
    disable_log_requests: bool = True

    def to_command(self) -> list[str]:
        cmd = [
            "vllm",
            "serve",
            self.model,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--served-model-name",
            self.served_model_name,
            "--tensor-parallel-size",
            str(self.tensor_parallel_size),
            "--max-num-seqs",
            str(self.max_num_seqs),
            "--max-num-batched-tokens",
            str(self.max_num_batched_tokens),
            "--gpu-memory-utilization",
            str(self.gpu_memory_utilization),
            "--dtype",
            self.dtype,
            "--max-model-len",
            str(self.max_model_len),
        ]
        if self.model_revision:
            cmd.extend(["--revision", self.model_revision])
        if self.quantization and self.quantization.lower() not in {"none", "false", "no"}:
            cmd.extend(["--quantization", self.quantization])
        if self.trust_remote_code:
            cmd.append("--trust-remote-code")
        if self.enable_prefix_caching:
            cmd.append("--enable-prefix-caching")
        if self.disable_log_requests:
            cmd.append("--disable-log-requests")
        return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch vLLM with Phase 8 production defaults.")
    parser.add_argument("--model", "--model-path", dest="model", default=os.environ.get("MODEL_PATH", "/models/security-coder-awq"))
    parser.add_argument("--model-revision", default=os.environ.get("MODEL_REVISION"))
    parser.add_argument("--served-model-name", default=os.environ.get("SERVED_MODEL_NAME", "security-coder"))
    parser.add_argument("--host", default=os.environ.get("VLLM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VLLM_PORT", "8000")))
    parser.add_argument("--tensor-parallel-size", "--tp-size", dest="tensor_parallel_size", type=int, default=int(os.environ.get("TP_SIZE", "2")))
    parser.add_argument("--max-num-seqs", type=int, default=int(os.environ.get("MAX_NUM_SEQS", "256")))
    parser.add_argument(
        "--max-num-batched-tokens",
        type=int,
        default=int(os.environ.get("MAX_NUM_BATCHED_TOKENS", "8192")),
    )
    parser.add_argument(
        "--gpu-memory-utilization",
        type=float,
        default=float(os.environ.get("GPU_MEMORY_UTILIZATION", "0.94")),
    )
    parser.add_argument("--quantization", default=os.environ.get("QUANTIZATION", "awq"))
    parser.add_argument("--dtype", default=os.environ.get("VLLM_DTYPE", "auto"))
    parser.add_argument("--max-model-len", type=int, default=int(os.environ.get("MAX_MODEL_LEN", "8192")))
    parser.add_argument("--trust-remote-code", action="store_true", default=env_flag("TRUST_REMOTE_CODE", False))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    args = parse_args()
    config = VllmServeConfig(
        model=args.model,
        served_model_name=args.served_model_name,
        model_revision=args.model_revision,
        host=args.host,
        port=args.port,
        tensor_parallel_size=args.tensor_parallel_size,
        max_num_seqs=args.max_num_seqs,
        max_num_batched_tokens=args.max_num_batched_tokens,
        gpu_memory_utilization=args.gpu_memory_utilization,
        quantization=args.quantization,
        dtype=args.dtype,
        max_model_len=args.max_model_len,
        trust_remote_code=args.trust_remote_code,
    )
    cmd = config.to_command()
    print(" ".join(cmd), flush=True)
    if args.dry_run:
        return
    raise SystemExit(subprocess.call(cmd))  # nosec B603


if __name__ == "__main__":
    main()
