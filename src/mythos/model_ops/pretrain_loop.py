"""Checkpoint-resumable scratch pretraining loop for Mythos decoder models."""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404
import time
from functools import partial
from pathlib import Path
from typing import Any, Literal

from src.mythos.model_ops.data_loader import TokenShardStream, count_batches, load_manifest
from src.mythos.model_ops.foundation import CheckpointManifest, sha256_file
from src.mythos.model_ops.tokenizer_pipeline import ShardBuildConfig, ShardManifest, build_token_shards, load_tokenizer, read_uint32_tokens
from src.mythos.model_ops.torch_decoder import (
    DecoderBlock,
    MythosDecoderLM,
    ScratchDecoderConfig,
    load_trusted_checkpoint,
    model_profile,
    require_torch,
    save_trusted_checkpoint,
    tiny_smoke_config,
)
from src.mythos.shared.progress import NullProgressReporter, ProgressReporter

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


DistributedStrategy = Literal["none", "fsdp", "deepspeed_zero2", "deepspeed_zero3", "megatron"]


def is_deepspeed_strategy(strategy: DistributedStrategy) -> bool:
    return strategy in {"deepspeed_zero2", "deepspeed_zero3"}


def default_deepspeed_config_path(strategy: DistributedStrategy) -> Path:
    if strategy == "deepspeed_zero2":
        return Path("deploy/gpu/deepspeed_zero2.json")
    if strategy == "deepspeed_zero3":
        return Path("deploy/gpu/deepspeed_zero3.json")
    raise ValueError(f"strategy does not use DeepSpeed: {strategy}")


def load_deepspeed_config(
    *,
    strategy: DistributedStrategy,
    config_path: str | Path | None,
    batch_size: int,
    gradient_accumulation_steps: int,
    dtype: str,
) -> dict[str, Any]:
    path = Path(config_path) if config_path else default_deepspeed_config_path(strategy)
    if not path.exists():
        raise FileNotFoundError(f"DeepSpeed config not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    world_size = max(1, distributed_world_size())
    payload["train_micro_batch_size_per_gpu"] = batch_size
    payload["gradient_accumulation_steps"] = gradient_accumulation_steps
    payload["train_batch_size"] = batch_size * gradient_accumulation_steps * world_size
    payload["bf16"] = {"enabled": dtype == "bf16"}
    payload["fp16"] = {"enabled": dtype == "fp16"}
    return payload


def build_cluster_training_plan(
    *,
    output_dir: str | Path,
    manifest: str | Path,
    model_profile_name: str,
    strategy: DistributedStrategy,
    num_nodes: int,
    gpus_per_node: int,
    sequence_length: int,
    batch_size: int,
    gradient_accumulation_steps: int,
    steps: int,
    dtype: str,
    attention_impl: str = "auto",
    gradient_checkpointing: bool = True,
    node_rank: int = 0,
    master_addr: str = "127.0.0.1",
    master_port: int = 29500,
    deepspeed_config: str | Path | None = None,
    megatron_root: str | Path | None = None,
) -> dict[str, Any]:
    if strategy == "none":
        raise ValueError("cluster training plan requires a distributed strategy")
    if num_nodes < 1:
        raise ValueError("num_nodes must be >= 1")
    if gpus_per_node < 1:
        raise ValueError("gpus_per_node must be >= 1")
    if steps < 1:
        raise ValueError("steps must be >= 1")
    if batch_size < 1 or gradient_accumulation_steps < 1:
        raise ValueError("batch_size and gradient_accumulation_steps must be >= 1")
    if dtype not in {"bf16", "fp16", "fp32"}:
        raise ValueError("dtype must be one of bf16, fp16, fp32")
    if not Path(manifest).exists():
        raise FileNotFoundError(f"shard manifest not found: {manifest}")
    profile = model_profile(model_profile_name) if model_profile_name != "tiny" else tiny_smoke_config()
    total_gpus = num_nodes * gpus_per_node
    global_sequences = total_gpus * batch_size * gradient_accumulation_steps
    tokens_per_optimizer_step = global_sequences * sequence_length
    base_train_args = [
        "--manifest",
        str(manifest),
        "--output-dir",
        str(output_dir),
        "--device",
        "cuda",
        "--steps",
        str(steps),
        "--batch-size",
        str(batch_size),
        "--sequence-length",
        str(sequence_length),
        "--gradient-accumulation-steps",
        str(gradient_accumulation_steps),
        "--dtype",
        dtype,
        "--model-profile",
        model_profile_name,
        "--attention-impl",
        attention_impl,
    ]
    if gradient_checkpointing:
        base_train_args.append("--gradient-checkpointing")

    warnings: list[str] = []
    required_env = {
        "MASTER_ADDR": master_addr,
        "MASTER_PORT": str(master_port),
        "NODE_RANK": str(node_rank),
        "WORLD_SIZE": str(total_gpus),
        "NCCL_ASYNC_ERROR_HANDLING": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUNBUFFERED": "1",
    }
    if model_profile_name in {"32b", "62b"} and total_gpus < 8:
        warnings.append("32B/62B profiles normally need >=8 high-memory GPUs; this plan may OOM on smaller clusters.")
    if sequence_length > 8192 and strategy not in {"deepspeed_zero3", "megatron"}:
        warnings.append("Long-context runs above 8k tokens should usually use ZeRO-3/Megatron-style partitioning.")
    if dtype == "fp32" and model_profile_name != "tiny":
        warnings.append("fp32 is not practical for large-profile training; prefer bf16 on Ampere/Hopper or fp16 where required.")

    if strategy == "fsdp":
        launcher = "torchrun"
        command = [
            "torchrun",
            "--nnodes",
            str(num_nodes),
            "--nproc_per_node",
            str(gpus_per_node),
            "--node_rank",
            str(node_rank),
            "--master_addr",
            master_addr,
            "--master_port",
            str(master_port),
            "-m",
            "src.mythos.model_ops.pretrain_loop",
            "--distributed-strategy",
            "fsdp",
            *base_train_args,
        ]
    elif strategy in {"deepspeed_zero2", "deepspeed_zero3"}:
        launcher = "deepspeed"
        if deepspeed_config is None:
            stage = "2" if strategy == "deepspeed_zero2" else "3"
            warnings.append(f"No DeepSpeed JSON path provided; generate/use a ZeRO-{stage} config before launching.")
        elif not Path(deepspeed_config).exists():
            raise FileNotFoundError(f"DeepSpeed config not found: {deepspeed_config}")
        command = [
            "deepspeed",
            "--num_nodes",
            str(num_nodes),
            "--num_gpus",
            str(gpus_per_node),
            "-m",
            "src.mythos.model_ops.pretrain_loop",
            "--distributed-strategy",
            strategy,
            *base_train_args,
        ]
        if deepspeed_config is not None:
            command.extend(["--deepspeed-config", str(deepspeed_config)])
    else:
        launcher = "megatron"
        if megatron_root is None:
            warnings.append("Megatron root path is not set; set --megatron-root to the checked-out Megatron-LM repository.")
        elif not Path(megatron_root).exists():
            raise FileNotFoundError(f"Megatron root not found: {megatron_root}")
        command = [
            "torchrun",
            "--nnodes",
            str(num_nodes),
            "--nproc_per_node",
            str(gpus_per_node),
            "--node_rank",
            str(node_rank),
            "--master_addr",
            master_addr,
            "--master_port",
            str(master_port),
            "-m",
            "src.mythos.model_ops.pretrain_loop",
            "--distributed-strategy",
            "megatron",
            *base_train_args,
        ]
    return {
        "status": "ready_with_warnings" if warnings else "ready",
        "scratch_only": True,
        "strategy": strategy,
        "launcher": launcher,
        "command": command,
        "env": required_env,
        "model_profile": profile.model_dump(),
        "estimated_parameter_count": profile.parameter_estimate(),
        "num_nodes": num_nodes,
        "gpus_per_node": gpus_per_node,
        "total_gpus": total_gpus,
        "batch_size_per_gpu": batch_size,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "global_sequences_per_optimizer_step": global_sequences,
        "tokens_per_optimizer_step": tokens_per_optimizer_step,
        "target_train_tokens": tokens_per_optimizer_step * steps,
        "warnings": warnings,
        "required_release_gates": [
            "tokenizer audit passes on real corpus",
            "dataset contamination gate passes",
            "10k-step single-node GPU validation passes",
            "distributed dry-run initializes every rank",
            "first cluster checkpoint reloads and evaluates",
        ],
    }


def write_cluster_training_plan(*, output_path: str | Path, plan: dict[str, Any]) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    return target


def distributed_is_initialized() -> bool:
    require_torch()
    return bool(torch.distributed.is_available() and torch.distributed.is_initialized())


def distributed_rank() -> int:
    if distributed_is_initialized():
        return int(torch.distributed.get_rank())
    return 0


def distributed_world_size() -> int:
    if distributed_is_initialized():
        return int(torch.distributed.get_world_size())
    return 1


def distributed_barrier() -> None:
    if distributed_is_initialized():
        torch.distributed.barrier()


def initialize_distributed_runtime(strategy: DistributedStrategy, requested_device: "torch.device") -> dict[str, Any]:
    require_torch()
    if strategy == "none":
        return {"enabled": False, "strategy": "none", "rank": 0, "world_size": 1, "local_rank": 0}
    if is_deepspeed_strategy(strategy):
        try:
            import deepspeed
        except ImportError as exc:
            raise RuntimeError("DeepSpeed strategy requested but deepspeed is not installed") from exc
        if not torch.distributed.is_initialized():
            deepspeed.init_distributed()
        local_rank = int(os.environ.get("LOCAL_RANK", "0"))
        if requested_device.type == "cuda":
            torch.cuda.set_device(local_rank)
        return {
            "enabled": True,
            "strategy": strategy,
            "rank": distributed_rank(),
            "world_size": distributed_world_size(),
            "local_rank": local_rank,
            "backend": torch.distributed.get_backend() if torch.distributed.is_initialized() else "",
        }
    if strategy != "fsdp":
        raise RuntimeError(
            f"{strategy} requires its dedicated engine adapter and cluster release gate. "
            "Use --cluster-plan-only to validate launch math; use --distributed-strategy fsdp for native torch FSDP runtime."
        )
    if not torch.distributed.is_available():
        raise RuntimeError("torch.distributed is not available in this PyTorch build")
    if not torch.distributed.is_initialized():
        backend = "nccl" if requested_device.type == "cuda" else "gloo"
        torch.distributed.init_process_group(backend=backend, init_method="env://")
    local_rank = int(__import__("os").environ.get("LOCAL_RANK", "0"))
    if requested_device.type == "cuda":
        torch.cuda.set_device(local_rank)
    return {
        "enabled": True,
        "strategy": strategy,
        "rank": distributed_rank(),
        "world_size": distributed_world_size(),
        "local_rank": local_rank,
        "backend": torch.distributed.get_backend(),
    }


def wrap_for_distributed(
    model: "MythosDecoderLM",
    *,
    strategy: DistributedStrategy,
    dtype: str,
    device: "torch.device",
) -> "torch.nn.Module":
    require_torch()
    if strategy == "none":
        return model
    if strategy != "fsdp":
        raise RuntimeError(f"unsupported distributed runtime strategy: {strategy}")
    from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
    from torch.distributed.fsdp import MixedPrecision
    from torch.distributed.fsdp.wrap import transformer_auto_wrap_policy

    mixed_precision = None
    if device.type == "cuda" and dtype in {"bf16", "fp16"}:
        active_dtype = autocast_dtype(dtype)
        mixed_precision = MixedPrecision(param_dtype=active_dtype, reduce_dtype=active_dtype, buffer_dtype=active_dtype)
    auto_wrap_policy = partial(transformer_auto_wrap_policy, transformer_layer_cls={DecoderBlock})
    return FSDP(model, auto_wrap_policy=auto_wrap_policy, mixed_precision=mixed_precision, use_orig_params=True)


def checkpoint_model_state_dict(model: "torch.nn.Module") -> dict[str, Any]:
    require_torch()
    if hasattr(model, "module") and model.__class__.__name__.lower().endswith("engine"):
        return model.module.state_dict()
    try:
        from torch.distributed.fsdp import FullStateDictConfig, FullyShardedDataParallel as FSDP, StateDictType
    except ImportError:
        return model.state_dict()
    if isinstance(model, FSDP):
        config = FullStateDictConfig(offload_to_cpu=True, rank0_only=True)
        with FSDP.state_dict_type(model, StateDictType.FULL_STATE_DICT, config):
            return model.state_dict()
    return model.state_dict()


def wrap_for_deepspeed(
    model: "torch.nn.Module",
    optimizer: "torch.optim.Optimizer",
    scheduler: "torch.optim.lr_scheduler.LRScheduler",
    *,
    strategy: DistributedStrategy,
    config_path: str | Path | None,
    batch_size: int,
    gradient_accumulation_steps: int,
    dtype: str,
) -> tuple["torch.nn.Module", "torch.optim.Optimizer", "torch.optim.lr_scheduler.LRScheduler"]:
    if not is_deepspeed_strategy(strategy):
        return model, optimizer, scheduler
    try:
        import deepspeed
    except ImportError as exc:
        raise RuntimeError("DeepSpeed strategy requested but deepspeed is not installed") from exc
    ds_config = load_deepspeed_config(
        strategy=strategy,
        config_path=config_path,
        batch_size=batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        dtype=dtype,
    )
    engine, active_optimizer, _loader, active_scheduler = deepspeed.initialize(
        model=model,
        model_parameters=model.parameters(),
        optimizer=optimizer,
        lr_scheduler=scheduler,
        config=ds_config,
    )
    return engine, active_optimizer, active_scheduler


def select_device(requested: str) -> "torch.device":
    require_torch()
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    return torch.device(requested)


def autocast_dtype(name: str) -> "torch.dtype":
    if name == "bf16":
        return torch.bfloat16
    if name == "fp16":
        return torch.float16
    return torch.float32


def latest_checkpoint(output_dir: str | Path) -> Path | None:
    root = Path(output_dir)
    candidates = sorted(root.glob("checkpoint-step-*/model.pt"))
    return candidates[-1] if candidates else None


def git_commit(root: str | Path = ".") -> str:
    completed = subprocess.run(  # nosec B603
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def training_environment_report(*, device: "torch.device", dtype: str, distributed_strategy: DistributedStrategy) -> dict[str, Any]:
    require_torch()
    cuda_available = bool(torch.cuda.is_available())
    return {
        "python": __import__("sys").version.split()[0],
        "torch": str(torch.__version__),
        "cuda_available": cuda_available,
        "cuda_version": str(getattr(torch.version, "cuda", "")),
        "device": str(device),
        "device_name": torch.cuda.get_device_name(device) if device.type == "cuda" and cuda_available else "",
        "dtype": dtype,
        "distributed_strategy": distributed_strategy,
        "distributed_rank": distributed_rank(),
        "distributed_world_size": distributed_world_size(),
        "env": {
            "LOCAL_RANK": os.environ.get("LOCAL_RANK", ""),
            "RANK": os.environ.get("RANK", ""),
            "WORLD_SIZE": os.environ.get("WORLD_SIZE", ""),
            "MASTER_ADDR": os.environ.get("MASTER_ADDR", ""),
            "MASTER_PORT": os.environ.get("MASTER_PORT", ""),
        },
    }


def save_training_checkpoint(
    *,
    output_dir: Path,
    model: "torch.nn.Module",
    optimizer: "torch.optim.Optimizer",
    scheduler: "torch.optim.lr_scheduler.LRScheduler | None" = None,
    config: ScratchDecoderConfig,
    step: int,
    trained_tokens: int,
    metrics: dict[str, float],
    training_args: dict[str, Any] | None = None,
    dataset_manifest_path: str | Path | None = None,
    tokenizer_path: str | Path | None = None,
    environment: dict[str, Any] | None = None,
    manifest_filename: str = "checkpoint_manifest.json",
) -> Path:
    checkpoint_dir = output_dir / f"checkpoint-step-{step:08d}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(model, "save_checkpoint"):
        model.save_checkpoint(str(checkpoint_dir / "deepspeed"), tag=f"step-{step:08d}")
    model_state = checkpoint_model_state_dict(model)
    manifest_path = output_dir / manifest_filename
    if distributed_rank() == 0:
        dataset_manifest_hash = sha256_file(Path(dataset_manifest_path)) if dataset_manifest_path and Path(dataset_manifest_path).exists() else ""
        tokenizer_hash = sha256_file(Path(tokenizer_path)) if tokenizer_path and Path(tokenizer_path).exists() else ""
        save_trusted_checkpoint(
            {
                "model": model_state,
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict() if scheduler is not None else {"type": "constant", "state": {}},
                "config": config.model_dump(),
                "step": step,
                "trained_tokens": trained_tokens,
                "metrics": metrics,
                "training_args": training_args or {},
                "dataset_manifest_path": str(dataset_manifest_path or ""),
                "dataset_manifest_sha256": dataset_manifest_hash,
                "tokenizer_path": str(tokenizer_path or ""),
                "tokenizer_sha256": tokenizer_hash,
                "git_commit": git_commit(),
                "environment": environment or {},
                "distributed_world_size": distributed_world_size(),
            },
            checkpoint_dir / "model.pt",
        )
        (checkpoint_dir / "config.json").write_text(json.dumps(config.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        manifest = CheckpointManifest.from_directory(
            architecture_name=config.name,
            run_id="scratch-pretrain-loop",
            step=step,
            trained_tokens=trained_tokens,
            checkpoint_dir=checkpoint_dir,
            metrics=metrics,
        )
        manifest.write_atomic(manifest_path)
    distributed_barrier()
    return manifest_path


def load_checkpoint(
    checkpoint_path: Path,
    *,
    model: "torch.nn.Module",
    optimizer: "torch.optim.Optimizer",
    scheduler: "torch.optim.lr_scheduler.LRScheduler | None" = None,
    device: "torch.device",
    expected_config: ScratchDecoderConfig | None = None,
) -> tuple[int, int]:
    payload = load_trusted_checkpoint(checkpoint_path, map_location=device)
    if expected_config is not None:
        saved_config = payload.get("config") or {}
        if int(saved_config.get("vocab_size", expected_config.vocab_size)) != expected_config.vocab_size:
            raise ValueError("checkpoint vocab_size does not match current training config")
        if int(saved_config.get("hidden_size", expected_config.hidden_size)) != expected_config.hidden_size:
            raise ValueError("checkpoint hidden_size does not match current training config")
    model.load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    if scheduler is not None and isinstance(payload.get("scheduler"), dict):
        try:
            scheduler.load_state_dict(payload["scheduler"])
        except Exception as exc:
            raise ValueError(f"checkpoint scheduler state is incompatible: {exc}") from exc
    return int(payload.get("step", 0)), int(payload.get("trained_tokens", 0))


def validate_production_training_args(
    *,
    production_mode: bool,
    dev_smoke: bool,
    model_profile_name: str,
    manifest: str | Path | None,
    tokenizer_path: str | Path | None,
    active_manifest: ShardManifest,
    validate_every: int,
    checkpoint_every: int,
    run_steps: int,
) -> None:
    if not production_mode:
        return
    failures = []
    if model_profile_name == "tiny" and not dev_smoke:
        failures.append("production mode cannot use model-profile=tiny without --dev-smoke")
    if not manifest:
        failures.append("production mode requires an explicit shard manifest")
    if not active_manifest.tokenizer_path or not Path(active_manifest.tokenizer_path).exists():
        failures.append("production mode requires a tokenizer path in the shard manifest")
    if tokenizer_path and not Path(tokenizer_path).exists():
        failures.append("production mode tokenizer_path does not exist")
    if validate_every <= 0 or validate_every > run_steps:
        failures.append("production mode requires validation to run inside the requested step count")
    if checkpoint_every <= 0:
        failures.append("production mode requires checkpointing")
    if failures:
        raise ValueError("production training validation failed: " + "; ".join(failures))


def tensor_batch(batch: list[list[int]], *, device: "torch.device") -> "torch.Tensor":
    return torch.tensor(batch, dtype=torch.long, device=device)


def validate_training_shards(*, train_shards: list[str], sequence_length: int, batch_size: int) -> int:
    if not train_shards:
        raise ValueError("manifest has no training shards; provide a corpus that produces at least one train shard")
    required_tokens = sequence_length * batch_size
    available_batches = count_batches(train_shards, sequence_length=sequence_length, batch_size=batch_size)
    if available_batches < 1:
        total_tokens = sum(len(Path(path).read_bytes()) // 4 for path in train_shards)
        raise ValueError(
            "not enough training tokens for one batch: "
            f"train_tokens={total_tokens}, required_tokens={required_tokens} "
            f"(batch_size={batch_size} * sequence_length={sequence_length}). "
            "Use a larger corpus, reduce --batch-size, or reduce --sequence-length."
        )
    return available_batches


def max_token_id(shard_paths: list[str]) -> int:
    maximum = -1
    for path in shard_paths:
        tokens = read_uint32_tokens(path)
        if tokens:
            maximum = max(maximum, max(tokens))
    return maximum


def tokenizer_vocab_size(tokenizer_path: str | Path) -> int:
    return int(load_tokenizer(tokenizer_path).get_vocab_size(with_added_tokens=True))


def build_training_config(
    active_manifest: ShardManifest,
    *,
    sequence_length: int,
    model_profile_name: str = "tiny",
    attention_impl: str = "auto",
    gradient_checkpointing: bool = False,
) -> ScratchDecoderConfig:
    base = model_profile(model_profile_name) if model_profile_name != "tiny" else tiny_smoke_config()
    vocab_size = base.vocab_size
    tokenizer_path = Path(active_manifest.tokenizer_path)
    if tokenizer_path.exists():
        vocab_size = max(vocab_size, tokenizer_vocab_size(tokenizer_path))
    highest_token_id = max_token_id(active_manifest.train_shards + active_manifest.val_shards)
    if highest_token_id >= vocab_size:
        vocab_size = highest_token_id + 1
    return base.model_copy(
        update={
            "vocab_size": vocab_size,
            "max_sequence_length": max(base.max_sequence_length, sequence_length),
            "attention_impl": attention_impl,
            "gradient_checkpointing": gradient_checkpointing or base.gradient_checkpointing,
        }
    )


@torch.no_grad() if torch is not None else (lambda fn: fn)
def validation_loss(
    *,
    model: "MythosDecoderLM",
    stream: TokenShardStream,
    device: "torch.device",
    max_batches: int,
    dtype: str,
) -> float:
    model.eval()
    losses: list[float] = []
    use_autocast = device.type == "cuda" and dtype in {"bf16", "fp16"}
    for index, batch in enumerate(stream.batches(epoch=0)):
        if index >= max_batches:
            break
        input_ids = tensor_batch(batch, device=device)
        with torch.autocast(device_type=device.type, dtype=autocast_dtype(dtype), enabled=use_autocast):
            output = model(input_ids, labels=input_ids)
        if output.loss is not None:
            losses.append(float(output.loss.detach().cpu()))
    model.train()
    return sum(losses) / max(1, len(losses))


def run_pretraining_loop(
    *,
    output_dir: str | Path,
    manifest: str | Path | None = None,
    token_file: str | Path | None = None,
    tokenizer_path: str | Path | None = None,
    device: str = "auto",
    steps: int = 100,
    batch_size: int = 2,
    sequence_length: int = 64,
    learning_rate: float = 1e-3,
    gradient_accumulation_steps: int = 1,
    dtype: str = "bf16",
    validate_every: int = 25,
    validation_batches: int = 4,
    checkpoint_every: int = 50,
    early_stopping_patience: int = 0,
    early_stopping_min_delta: float = 0.0,
    resume: bool = True,
    progress: ProgressReporter | None = None,
    progress_every_steps: int = 25,
    model_profile_name: str = "tiny",
    attention_impl: str = "auto",
    gradient_checkpointing: bool = False,
    distributed_strategy: DistributedStrategy = "none",
    deepspeed_config: str | Path | None = None,
    production_mode: bool = False,
    dev_smoke: bool = False,
    max_training_loss: float = 10_000.0,
) -> dict[str, Any]:
    require_torch()
    if steps < 1:
        raise ValueError("steps must be >= 1")
    if gradient_accumulation_steps < 1:
        raise ValueError("gradient_accumulation_steps must be >= 1")
    if early_stopping_patience < 0:
        raise ValueError("early_stopping_patience must be >= 0")
    selected = select_device(device)
    distributed_report = initialize_distributed_runtime(distributed_strategy, selected)
    if distributed_report["enabled"] and selected.type == "cuda":
        selected = torch.device(f"cuda:{distributed_report['local_rank']}")
    active_progress = progress or NullProgressReporter()
    if distributed_rank() != 0:
        active_progress = NullProgressReporter()
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    active_manifest_path: Path | None = Path(manifest).resolve() if manifest else None
    if active_manifest_path is None and token_file and tokenizer_path:
        active_manifest = build_token_shards(
            input_paths=[token_file],
            tokenizer_path=tokenizer_path,
            output_dir=root / "generated-shards",
            config=ShardBuildConfig(
                shard_token_count=max(128, batch_size * sequence_length * 8),
                sequence_length=sequence_length,
                validation_fraction=0.1,
            ),
        )
    elif active_manifest_path is not None:
        active_manifest = load_manifest(active_manifest_path)
    else:
        raise ValueError("provide --manifest, or both --token-file and --tokenizer-path")
    validate_production_training_args(
        production_mode=production_mode,
        dev_smoke=dev_smoke,
        model_profile_name=model_profile_name,
        manifest=manifest,
        tokenizer_path=tokenizer_path,
        active_manifest=active_manifest,
        validate_every=validate_every,
        checkpoint_every=checkpoint_every,
        run_steps=steps,
    )

    config = build_training_config(
        active_manifest,
        sequence_length=sequence_length,
        model_profile_name=model_profile_name,
        attention_impl=attention_impl,
        gradient_checkpointing=gradient_checkpointing,
    )
    available_batches = validate_training_shards(
        train_shards=active_manifest.train_shards,
        sequence_length=sequence_length,
        batch_size=batch_size,
    )
    active_progress.emit(
        "training",
        "started",
        device=str(selected),
        dtype=dtype,
        requested_steps=steps,
        batch_size=batch_size,
        sequence_length=sequence_length,
        gradient_accumulation_steps=gradient_accumulation_steps,
        train_shards=len(active_manifest.train_shards),
        val_shards=len(active_manifest.val_shards),
        available_batches=available_batches,
        vocab_size=config.vocab_size,
        parameter_count=config.parameter_estimate(),
    )
    train_stream = TokenShardStream(
        active_manifest.train_shards,
        sequence_length=sequence_length,
        batch_size=batch_size,
        seed=1337,
        shuffle=True,
    )
    val_stream = (
        TokenShardStream(active_manifest.val_shards, sequence_length=sequence_length, batch_size=batch_size, seed=7331, shuffle=False)
        if active_manifest.val_shards
        else None
    )

    model = MythosDecoderLM(config).to(selected)
    if config.gradient_checkpointing:
        model.enable_gradient_checkpointing()
    if not is_deepspeed_strategy(distributed_strategy):
        model = wrap_for_distributed(model, strategy=distributed_strategy, dtype=dtype, device=selected)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, betas=(0.9, 0.95), weight_decay=0.1)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lambda _step: 1.0)
    if is_deepspeed_strategy(distributed_strategy):
        model, optimizer, scheduler = wrap_for_deepspeed(
            model,
            optimizer,
            scheduler,
            strategy=distributed_strategy,
            config_path=deepspeed_config,
            batch_size=batch_size,
            gradient_accumulation_steps=gradient_accumulation_steps,
            dtype=dtype,
        )
    checkpoint_environment = training_environment_report(device=selected, dtype=dtype, distributed_strategy=distributed_strategy)
    checkpoint_args = {
        "steps": steps,
        "batch_size": batch_size,
        "sequence_length": sequence_length,
        "learning_rate": learning_rate,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "dtype": dtype,
        "validate_every": validate_every,
        "validation_batches": validation_batches,
        "checkpoint_every": checkpoint_every,
        "early_stopping_patience": early_stopping_patience,
        "early_stopping_min_delta": early_stopping_min_delta,
        "model_profile_name": model_profile_name,
        "attention_impl": attention_impl,
        "gradient_checkpointing": gradient_checkpointing,
        "distributed_strategy": distributed_strategy,
        "deepspeed_config": str(deepspeed_config or default_deepspeed_config_path(distributed_strategy)) if is_deepspeed_strategy(distributed_strategy) else "",
        "production_mode": production_mode,
        "dev_smoke": dev_smoke,
        "max_training_loss": max_training_loss,
    }
    start_step = 0
    trained_tokens = 0
    if resume:
        checkpoint = latest_checkpoint(root)
        if checkpoint is not None:
            start_step, trained_tokens = load_checkpoint(
                checkpoint,
                model=model,
                optimizer=optimizer,
                scheduler=scheduler,
                device=selected,
                expected_config=config,
            )

    model.train()
    started = time.perf_counter()
    train_losses: list[float] = []
    val_losses: list[dict[str, float]] = []
    use_autocast = selected.type == "cuda" and dtype in {"bf16", "fp16"}
    optimizer.zero_grad(set_to_none=True)
    current_step = start_step
    epoch = 0
    best_val_loss = float("inf")
    best_val_step = 0
    best_checkpoint_manifest: Path | None = None
    validations_without_improvement = 0
    early_stopped = False
    early_stop_reason = ""
    while current_step < steps:
        progressed = False
        for batch in train_stream.batches(epoch=epoch):
            input_ids = tensor_batch(batch, device=selected)
            with torch.autocast(device_type=selected.type, dtype=autocast_dtype(dtype), enabled=use_autocast):
                output = model(input_ids, labels=input_ids)
                if output.loss is None:
                    raise RuntimeError("loss missing")
                if not torch.isfinite(output.loss.detach()):
                    raise FloatingPointError(f"non-finite training loss at step {current_step + 1}: {float(output.loss.detach().cpu())}")
                if float(output.loss.detach().cpu()) > max_training_loss:
                    raise FloatingPointError(
                        f"catastrophic training loss at step {current_step + 1}: "
                        f"{float(output.loss.detach().cpu())} > {max_training_loss}"
                    )
                loss = output.loss if is_deepspeed_strategy(distributed_strategy) else output.loss / gradient_accumulation_steps
            if is_deepspeed_strategy(distributed_strategy):
                model.backward(loss)
            else:
                loss.backward()
            progressed = True
            if is_deepspeed_strategy(distributed_strategy):
                model.step()
                grad_norm = torch.tensor(0.0)
            elif (current_step + 1) % gradient_accumulation_steps == 0:
                grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
            else:
                grad_norm = torch.tensor(0.0)
            current_step += 1
            trained_tokens += batch_size * sequence_length
            train_losses.append(float(output.loss.detach().cpu()))
            if current_step == 1 or current_step % max(1, progress_every_steps) == 0 or current_step >= steps:
                active_progress.emit(
                    "training",
                    "running",
                    step=current_step,
                    requested_steps=steps,
                    loss=round(train_losses[-1], 6),
                    grad_norm=round(float(grad_norm.detach().cpu()), 6),
                    trained_tokens=trained_tokens,
                    epoch=epoch,
                )

            if val_stream is not None and validate_every > 0 and current_step % validate_every == 0:
                current_val_loss = validation_loss(
                    model=model,
                    stream=val_stream,
                    device=selected,
                    max_batches=validation_batches,
                    dtype=dtype,
                )
                val_losses.append({"step": float(current_step), "loss": current_val_loss})
                active_progress.emit(
                    "validation",
                    "complete",
                    step=current_step,
                    validation_loss=round(current_val_loss, 6),
                    best_validation_loss=round(best_val_loss, 6) if best_val_loss != float("inf") else None,
                )
                if current_val_loss < best_val_loss - early_stopping_min_delta:
                    best_val_loss = current_val_loss
                    best_val_step = current_step
                    validations_without_improvement = 0
                    best_checkpoint_manifest = save_training_checkpoint(
                        output_dir=root,
                        model=model,
                        optimizer=optimizer,
                        scheduler=scheduler,
                        config=config,
                        step=current_step,
                        trained_tokens=trained_tokens,
                        metrics={"train_loss": train_losses[-1], "val_loss": current_val_loss, "best_val_loss": current_val_loss},
                        training_args=checkpoint_args,
                        dataset_manifest_path=active_manifest_path,
                        tokenizer_path=active_manifest.tokenizer_path,
                        environment=checkpoint_environment,
                        manifest_filename="best_checkpoint_manifest.json",
                    )
                    active_progress.emit(
                        "checkpoint",
                        "best_saved",
                        step=current_step,
                        validation_loss=round(current_val_loss, 6),
                        checkpoint_manifest=str(best_checkpoint_manifest),
                    )
                else:
                    validations_without_improvement += 1
                    if early_stopping_patience > 0 and validations_without_improvement >= early_stopping_patience:
                        early_stopped = True
                        early_stop_reason = (
                            f"validation loss did not improve by {early_stopping_min_delta} "
                            f"for {validations_without_improvement} validation checks"
                        )
                        active_progress.emit("training", "early_stopping", step=current_step, reason=early_stop_reason)
                        break
            if checkpoint_every > 0 and current_step % checkpoint_every == 0:
                checkpoint_manifest = save_training_checkpoint(
                    output_dir=root,
                    model=model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                    config=config,
                    step=current_step,
                    trained_tokens=trained_tokens,
                    metrics={"train_loss": train_losses[-1], "val_loss": val_losses[-1]["loss"] if val_losses else -1.0},
                    training_args=checkpoint_args,
                    dataset_manifest_path=active_manifest_path,
                    tokenizer_path=active_manifest.tokenizer_path,
                    environment=checkpoint_environment,
                )
                active_progress.emit(
                    "checkpoint",
                    "saved",
                    step=current_step,
                    checkpoint_manifest=str(checkpoint_manifest),
                    train_loss=round(train_losses[-1], 6),
                    validation_loss=round(val_losses[-1]["loss"], 6) if val_losses else None,
                )
            if current_step >= steps:
                break
        if early_stopped:
            break
        if not progressed:
            raise RuntimeError(
                "no training batches were produced from shards after preflight validation; "
                f"available_batches={available_batches}"
            )
        epoch += 1

    final_val_loss = val_losses[-1]["loss"] if val_losses else -1.0
    manifest_path = save_training_checkpoint(
        output_dir=root,
        model=model,
        optimizer=optimizer,
        scheduler=scheduler,
        config=config,
        step=current_step,
        trained_tokens=trained_tokens,
        metrics={"train_loss": train_losses[-1], "val_loss": final_val_loss, "best_val_loss": best_val_loss if val_losses else -1.0},
        training_args=checkpoint_args,
        dataset_manifest_path=active_manifest_path,
        tokenizer_path=active_manifest.tokenizer_path,
        environment=checkpoint_environment,
        manifest_filename="checkpoint_manifest.json",
    )
    if best_checkpoint_manifest is None:
        best_checkpoint_manifest = save_training_checkpoint(
            output_dir=root,
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            config=config,
            step=current_step,
            trained_tokens=trained_tokens,
            metrics={"train_loss": train_losses[-1], "val_loss": final_val_loss, "best_val_loss": final_val_loss},
            training_args=checkpoint_args,
            dataset_manifest_path=active_manifest_path,
            tokenizer_path=active_manifest.tokenizer_path,
            environment=checkpoint_environment,
            manifest_filename="best_checkpoint_manifest.json",
        )
        best_val_loss = final_val_loss
        best_val_step = current_step
    report = {
        "status": "early_stopped" if early_stopped else "passed",
        "scratch_only": True,
        "steps": current_step,
        "requested_steps": steps,
        "start_step": start_step,
        "device": str(selected),
        "dtype": dtype,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "validate_every": validate_every,
        "validation_batches": validation_batches,
        "early_stopping_patience": early_stopping_patience,
        "early_stopping_min_delta": early_stopping_min_delta,
        "early_stopped": early_stopped,
        "early_stop_reason": early_stop_reason,
        "model_config": config.model_dump(),
        "model_profile_name": model_profile_name,
        "attention_impl": attention_impl,
        "distributed_strategy": distributed_strategy,
        "distributed": distributed_report,
        "deepspeed_config": str(deepspeed_config or default_deepspeed_config_path(distributed_strategy)) if is_deepspeed_strategy(distributed_strategy) else "",
        "production_mode": production_mode,
        "dev_smoke": dev_smoke,
        "max_training_loss": max_training_loss,
        "git_commit": git_commit(),
        "train_losses": train_losses,
        "validation_losses": val_losses,
        "best_validation_loss": best_val_loss,
        "best_validation_step": best_val_step,
        "best_checkpoint_manifest": str(best_checkpoint_manifest),
        "trained_tokens": trained_tokens,
        "checkpoint_manifest": str(manifest_path),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if distributed_rank() == 0:
        (root / "pretrain_report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    active_progress.emit(
        "training",
        "complete",
        training_status=report["status"],
        steps=current_step,
        trained_tokens=trained_tokens,
        final_loss=round(train_losses[-1], 6),
        best_validation_loss=round(best_val_loss, 6) if best_val_loss != float("inf") else None,
        checkpoint_manifest=str(manifest_path),
        duration_ms=report["duration_ms"],
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos scratch pretraining loop.")
    parser.add_argument("--output-dir", default="artifacts/mythos/pretrain-loop")
    parser.add_argument("--manifest")
    parser.add_argument("--token-file")
    parser.add_argument("--tokenizer-path")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--sequence-length", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=1)
    parser.add_argument("--dtype", choices=["bf16", "fp16", "fp32"], default="bf16")
    parser.add_argument("--validate-every", type=int, default=25)
    parser.add_argument("--validation-batches", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument("--early-stopping-patience", type=int, default=0)
    parser.add_argument("--early-stopping-min-delta", type=float, default=0.0)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--model-profile", default="tiny", choices=["tiny", "1b", "7b", "32b", "62b"])
    parser.add_argument("--attention-impl", default="auto", choices=["auto", "sdpa", "eager"])
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--production", action="store_true", help="Enable strict production training validation.")
    parser.add_argument("--dev-smoke", action="store_true", help="Explicitly allow tiny/dev smoke behavior under production validation.")
    parser.add_argument("--max-training-loss", type=float, default=10_000.0)
    parser.add_argument(
        "--distributed-strategy",
        default="none",
        choices=["none", "fsdp", "deepspeed_zero2", "deepspeed_zero3", "megatron"],
        help="Validated distributed strategy contract. Use --cluster-plan-only before cluster execution.",
    )
    parser.add_argument("--cluster-plan-only", action="store_true", help="Write/print a distributed training launch plan without training.")
    parser.add_argument("--cluster-plan-out", default="artifacts/mythos/cluster_training_plan.json")
    parser.add_argument("--num-nodes", type=int, default=1)
    parser.add_argument("--gpus-per-node", type=int, default=8)
    parser.add_argument("--node-rank", type=int, default=0)
    parser.add_argument("--master-addr", default="127.0.0.1")
    parser.add_argument("--master-port", type=int, default=29500)
    parser.add_argument("--deepspeed-config")
    parser.add_argument("--megatron-root")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.cluster_plan_only:
        if not args.manifest:
            raise SystemExit("--manifest is required for --cluster-plan-only")
        plan = build_cluster_training_plan(
            output_dir=args.output_dir,
            manifest=args.manifest,
            model_profile_name=args.model_profile,
            strategy=args.distributed_strategy,
            num_nodes=args.num_nodes,
            gpus_per_node=args.gpus_per_node,
            sequence_length=args.sequence_length,
            batch_size=args.batch_size,
            gradient_accumulation_steps=args.gradient_accumulation_steps,
            steps=args.steps,
            dtype=args.dtype,
            attention_impl=args.attention_impl,
            gradient_checkpointing=args.gradient_checkpointing,
            node_rank=args.node_rank,
            master_addr=args.master_addr,
            master_port=args.master_port,
            deepspeed_config=args.deepspeed_config,
            megatron_root=args.megatron_root,
        )
        write_cluster_training_plan(output_path=args.cluster_plan_out, plan=plan)
        print(json.dumps(plan, indent=2, sort_keys=True))
        return
    report = run_pretraining_loop(
        output_dir=args.output_dir,
        manifest=args.manifest,
        token_file=args.token_file,
        tokenizer_path=args.tokenizer_path,
        device=args.device,
        steps=args.steps,
        batch_size=args.batch_size,
        sequence_length=args.sequence_length,
        learning_rate=args.learning_rate,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        dtype=args.dtype,
        validate_every=args.validate_every,
        validation_batches=args.validation_batches,
        checkpoint_every=args.checkpoint_every,
        early_stopping_patience=args.early_stopping_patience,
        early_stopping_min_delta=args.early_stopping_min_delta,
        resume=not args.no_resume,
        model_profile_name=args.model_profile,
        attention_impl=args.attention_impl,
        gradient_checkpointing=args.gradient_checkpointing,
        distributed_strategy=args.distributed_strategy,
        deepspeed_config=args.deepspeed_config,
        production_mode=args.production,
        dev_smoke=args.dev_smoke,
        max_training_loss=args.max_training_loss,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
