#!/usr/bin/env python
"""Phase 7 GRPO training loop for coding/cybersecurity LLMs.

Implements a custom Group Relative Policy Optimization loop:

  G = 8 candidates per prompt
  A_i = (R_i - mean(R)) / std(R)
  L = -min(r_i A_i, clip(r_i, 1-eps, 1+eps) A_i) + beta KL(pi || pi_ref)

Reward components:
  R_exec = +1.0 if sandbox exit_code == 0 else -1.0
  R_sec  = +0.5 if static analyzer finds zero new CVEs/issues else -0.5
  R_fmt  = +0.3 if thought tokens are valid else -0.3
  R_eff  = +0.2 if sandbox wall time < 2000ms else -0.2

Designed for Transformers + PyTorch + TRL ecosystems, with optional
Accelerate/DeepSpeed ZeRO-2, bf16, gradient checkpointing, and W&B logging.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import torch
import torch.nn.functional as F
from accelerate import Accelerator, DeepSpeedPlugin
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader, Dataset
from tqdm.auto import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

try:
    import wandb
except ImportError:  # pragma: no cover
    wandb = None

try:
    import trl
except ImportError:  # pragma: no cover
    trl = None


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from src.phase2.docker_sandbox_engine import DockerSandboxEngine, SandboxRequest, SourceFile
except Exception:  # Docker may be unavailable on trainer-only hosts.
    DockerSandboxEngine = None
    SandboxRequest = None
    SourceFile = None


THOUGHT_START = "<|thought_start|>"
THOUGHT_END = "<|thought_end|>"
PATCH_START = "<|patch_start|>"
PATCH_END = "<|patch_end|>"


@dataclass(frozen=True)
class RewardWeights:
    w_exec: float = 1.0
    w_sec: float = 1.0
    w_fmt: float = 1.0
    w_eff: float = 1.0


@dataclass(frozen=True)
class RewardBreakdown:
    exec_reward: float
    security_reward: float
    format_reward: float
    efficiency_reward: float
    total_reward: float
    sandbox_exit_code: int | None
    sandbox_wall_ms: float | None
    security_findings: int


@dataclass(frozen=True)
class TrainSample:
    prompt: str
    sandbox: dict[str, Any] | None = None
    security_baseline_findings: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateBatch:
    prompt_index: int
    prompt: str
    responses: list[str]
    full_sequence_ids: list[torch.Tensor]
    prompt_token_lengths: list[int]
    old_logprobs: list[torch.Tensor]
    ref_logprobs: list[torch.Tensor]
    rewards: list[RewardBreakdown]
    advantages: torch.Tensor


class JsonlPromptDataset(Dataset[TrainSample]):
    def __init__(self, path: Path) -> None:
        self.samples = [sample_from_payload(payload) for payload in load_jsonl(path)]
        if not self.samples:
            raise ValueError(f"dataset is empty: {path}")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> TrainSample:
        return self.samples[index]


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc


def sample_from_payload(payload: dict[str, Any]) -> TrainSample:
    prompt = payload.get("prompt") or payload.get("instruction") or payload.get("input")
    if isinstance(prompt, dict):
        prompt = json.dumps(prompt, sort_keys=True, ensure_ascii=False)
    if not prompt:
        raise ValueError("sample missing prompt/instruction/input")
    return TrainSample(
        prompt=str(prompt),
        sandbox=payload.get("sandbox"),
        security_baseline_findings=int(payload.get("security_baseline_findings", 0)),
        metadata=dict(payload.get("metadata", {})),
    )


def collate_samples(samples: list[TrainSample]) -> list[TrainSample]:
    return samples


def has_valid_thought_tokens(text: str) -> bool:
    start = text.find(THOUGHT_START)
    end = text.find(THOUGHT_END)
    if start < 0 or end <= start:
        return False
    if text.count(THOUGHT_START) != 1 or text.count(THOUGHT_END) != 1:
        return False
    if PATCH_START in text and PATCH_END in text:
        return text.find(PATCH_START) > end and text.find(PATCH_END) > text.find(PATCH_START)
    return True


def extract_patch_or_response(response: str) -> str:
    if PATCH_START in response and PATCH_END in response:
        start = response.find(PATCH_START) + len(PATCH_START)
        end = response.find(PATCH_END, start)
        return response[start:end].strip()
    return response


class StaticSecurityAnalyzer:
    """Static analyzer adapter.

    If `semgrep` is available and a ruleset is provided, it is used. Otherwise,
    a conservative built-in detector counts CVE-like strings and dangerous code
    primitives as findings.
    """

    DANGEROUS_PATTERNS = [
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\bos\.system\s*\(",
        r"\bsubprocess\.Popen\s*\(",
        r"\bshell\s*=\s*True\b",
        r"\bstrcpy\s*\(",
        r"\bsprintf\s*\(",
        r"\bgets\s*\(",
        r"\bmemcpy\s*\([^,]+,[^,]+,\s*strlen\s*\(",
        r"CVE-\d{4}-\d{4,}",
    ]

    def __init__(self, semgrep_config: str | None = None) -> None:
        self.semgrep_config = semgrep_config
        self.semgrep_path = shutil.which("semgrep")

    def count_findings(self, text: str) -> int:
        if self.semgrep_config and self.semgrep_path:
            return self._semgrep_count(text)
        return sum(1 for pattern in self.DANGEROUS_PATTERNS if re.search(pattern, text, re.IGNORECASE))

    def _semgrep_count(self, text: str) -> int:
        try:
            with tempfile.TemporaryDirectory(prefix="grpo_semgrep_") as tmp:
                target = Path(tmp) / "candidate.txt"
                target.write_text(text, encoding="utf-8")
                result = subprocess.run(  # nosec B603
                    [self.semgrep_path, "--json", "--config", self.semgrep_config, str(target)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    check=False,
                )
                payload = json.loads(result.stdout or "{}")
                return len(payload.get("results", []))
        except Exception:
            return self.count_findings_builtin(text)

    def count_findings_builtin(self, text: str) -> int:
        return sum(1 for pattern in self.DANGEROUS_PATTERNS if re.search(pattern, text, re.IGNORECASE))


class SandboxRewardRunner:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled and DockerSandboxEngine is not None
        self.engine = DockerSandboxEngine() if self.enabled else None

    def run(self, sample: TrainSample, response: str) -> tuple[int | None, float | None]:
        if not self.enabled or not sample.sandbox:
            return None, None
        try:
            request = sandbox_request_from_sample(sample, response)
            result = self.engine.run(request)
            wall_ms = None
            if getattr(result, "metrics", None) is not None:
                wall_us = getattr(result.metrics, "wall_time_us", None)
                wall_ms = float(wall_us) / 1000.0 if wall_us is not None else None
            return result.exit_code, wall_ms
        except Exception:
            return -1, None


def sandbox_request_from_sample(sample: TrainSample, response: str) -> Any:
    if SandboxRequest is None or SourceFile is None:
        raise RuntimeError("Phase 2 sandbox classes are unavailable")
    sandbox = sample.sandbox or {}
    base_files = [
        SourceFile(
            path=str(item["path"]),
            content=str(item["content"]),
            encoding=str(item.get("encoding", "utf-8")),
            executable=bool(item.get("executable", False)),
        )
        for item in sandbox.get("files", [])
    ]
    patch_text = extract_patch_or_response(response)
    generated_path = sandbox.get("generated_path", "candidate.txt")
    base_files.append(SourceFile(path=generated_path, content=patch_text))
    command = sandbox.get("command")
    if not command:
        command = ["python3", f"/workspace/{generated_path}"]
    return SandboxRequest(
        files=base_files,
        command=list(command),
        image=str(sandbox.get("image", "python:3.12-slim")),
        pull_missing_image=bool(sandbox.get("pull_missing_image", False)),
    )


class MultiComponentReward:
    def __init__(
        self,
        weights: RewardWeights,
        static_analyzer: StaticSecurityAnalyzer,
        sandbox_runner: SandboxRewardRunner,
    ) -> None:
        self.weights = weights
        self.static_analyzer = static_analyzer
        self.sandbox_runner = sandbox_runner

    def score(self, sample: TrainSample, response: str) -> RewardBreakdown:
        exit_code, wall_ms = self.sandbox_runner.run(sample, response)
        exec_reward = 1.0 if exit_code == 0 else -1.0
        findings = self.static_analyzer.count_findings(response)
        new_findings = max(0, findings - sample.security_baseline_findings)
        security_reward = 0.5 if new_findings == 0 else -0.5
        format_reward = 0.3 if has_valid_thought_tokens(response) else -0.3
        efficiency_reward = 0.2 if wall_ms is not None and wall_ms < 2000.0 else -0.2
        total = (
            self.weights.w_exec * exec_reward
            + self.weights.w_sec * security_reward
            + self.weights.w_fmt * format_reward
            + self.weights.w_eff * efficiency_reward
        )
        return RewardBreakdown(
            exec_reward=exec_reward,
            security_reward=security_reward,
            format_reward=format_reward,
            efficiency_reward=efficiency_reward,
            total_reward=total,
            sandbox_exit_code=exit_code,
            sandbox_wall_ms=wall_ms,
            security_findings=new_findings,
        )


def build_deepspeed_plugin(args: argparse.Namespace) -> DeepSpeedPlugin | None:
    if not args.deepspeed:
        return None
    return DeepSpeedPlugin(
        zero_stage=2,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        gradient_clipping=args.max_grad_norm,
        offload_optimizer_device=args.offload_optimizer_device,
    )


def looks_like_local_model_path(value: str) -> bool:
    expanded = Path(value).expanduser()
    return expanded.exists() or value.startswith((".", "/", "~", "\\")) or (len(value) > 1 and value[1] == ":")


def validate_revision_policy(args: argparse.Namespace) -> None:
    if args.allow_unpinned_model_revision:
        return
    targets = [(args.model_name_or_path, args.model_revision)]
    if args.ref_model_name_or_path:
        targets.append((args.ref_model_name_or_path, args.ref_model_revision))
    for model_name, revision in targets:
        if not revision and not looks_like_local_model_path(model_name):
            raise SystemExit(
                f"Remote model '{model_name}' requires an explicit revision pin. "
                "Pass --model-revision/--ref-model-revision or --allow-unpinned-model-revision."
            )


def tokenizer_setup(model_name: str, revision: str | None) -> Any:
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True, revision=revision)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    return tokenizer


def model_setup(args: argparse.Namespace) -> tuple[Any, Any, Any]:
    dtype = torch.bfloat16 if args.bf16 else torch.float32
    tokenizer = tokenizer_setup(args.model_name_or_path, args.model_revision)
    ref_revision = args.ref_model_revision if args.ref_model_name_or_path else args.model_revision
    model = AutoModelForCausalLM.from_pretrained(
        args.model_name_or_path,
        torch_dtype=dtype,
        trust_remote_code=args.trust_remote_code,
        revision=args.model_revision,
    )
    ref_model = AutoModelForCausalLM.from_pretrained(
        args.ref_model_name_or_path or args.model_name_or_path,
        torch_dtype=dtype,
        trust_remote_code=args.trust_remote_code,
        revision=ref_revision,
    )
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.config.use_cache = False
    ref_model.eval()
    for param in ref_model.parameters():
        param.requires_grad_(False)
    return model, ref_model, tokenizer


def generate_candidates(
    model: Any,
    tokenizer: Any,
    prompts: list[str],
    group_size: int,
    max_new_tokens: int,
    temperature: float,
    top_p: float,
) -> tuple[list[str], torch.Tensor, torch.Tensor]:
    encoded = tokenizer(prompts, return_tensors="pt", padding=True, truncation=True, max_length=tokenizer.model_max_length)
    encoded = {key: value.to(model.device) for key, value in encoded.items()}
    # With left padding, generated sequences preserve the full padded input width.
    # Completion tokens therefore begin at input_ids.shape[1], not attention_mask.sum().
    prompt_lengths = torch.full(
        (encoded["input_ids"].shape[0],),
        encoded["input_ids"].shape[1],
        dtype=torch.long,
        device=model.device,
    )
    repeated_input_ids = encoded["input_ids"].repeat_interleave(group_size, dim=0)
    repeated_attention = encoded["attention_mask"].repeat_interleave(group_size, dim=0)
    repeated_prompt_lengths = prompt_lengths.repeat_interleave(group_size)
    with torch.no_grad():
        output_ids = model.generate(
            input_ids=repeated_input_ids,
            attention_mask=repeated_attention,
            do_sample=True,
            temperature=temperature,
            top_p=top_p,
            max_new_tokens=max_new_tokens,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    responses: list[str] = []
    response_token_ids: list[torch.Tensor] = []
    for row, prompt_len in zip(output_ids, repeated_prompt_lengths):
        completion_ids = row[int(prompt_len) :]
        response_token_ids.append(completion_ids.detach().clone())
        responses.append(tokenizer.decode(completion_ids, skip_special_tokens=False))
    return responses, output_ids.detach(), repeated_prompt_lengths.detach()


def sequence_logprobs(
    model: Any,
    sequences: torch.Tensor,
    prompt_lengths: torch.Tensor,
    pad_token_id: int,
) -> list[torch.Tensor]:
    attention_mask = (sequences != pad_token_id).long()
    outputs = model(input_ids=sequences, attention_mask=attention_mask)
    logits = outputs.logits[:, :-1, :]
    target_ids = sequences[:, 1:]
    token_logprobs = F.log_softmax(logits, dim=-1).gather(-1, target_ids.unsqueeze(-1)).squeeze(-1)
    values: list[torch.Tensor] = []
    for index in range(sequences.size(0)):
        start = max(0, int(prompt_lengths[index].item()) - 1)
        mask = attention_mask[index, 1:].bool()
        completion_mask = torch.zeros_like(mask)
        completion_mask[start:] = mask[start:]
        values.append(token_logprobs[index][completion_mask].detach())
    return values


def train_sequence_logprob(
    model: Any,
    sequence: torch.Tensor,
    prompt_len: int,
    pad_token_id: int,
) -> torch.Tensor:
    input_ids = sequence.unsqueeze(0)
    attention_mask = (input_ids != pad_token_id).long()
    outputs = model(input_ids=input_ids, attention_mask=attention_mask)
    logits = outputs.logits[:, :-1, :]
    target_ids = input_ids[:, 1:]
    token_logprobs = F.log_softmax(logits, dim=-1).gather(-1, target_ids.unsqueeze(-1)).squeeze(-1)[0]
    mask = attention_mask[0, 1:].bool()
    start = max(0, prompt_len - 1)
    completion_mask = torch.zeros_like(mask)
    completion_mask[start:] = mask[start:]
    selected = token_logprobs[completion_mask]
    if selected.numel() == 0:
        return token_logprobs.sum() * 0.0
    return selected.mean()


def normalize_advantages(rewards: list[float], eps: float = 1e-8) -> torch.Tensor:
    values = torch.tensor(rewards, dtype=torch.float32)
    mean = values.mean()
    std = values.std(unbiased=False)
    if std < eps:
        return torch.zeros_like(values)
    return (values - mean) / (std + eps)


def pass_at_k(rewards: list[float], k: int) -> float:
    if not rewards:
        return 0.0
    top = sorted(rewards, reverse=True)[: min(k, len(rewards))]
    return 1.0 if any(value > 0 for value in top) else 0.0


def build_candidate_batches(
    model: Any,
    ref_model: Any,
    tokenizer: Any,
    samples: list[TrainSample],
    reward_fn: MultiComponentReward,
    args: argparse.Namespace,
) -> list[CandidateBatch]:
    prompts = [sample.prompt for sample in samples]
    responses, full_sequences, prompt_lengths = generate_candidates(
        model=model,
        tokenizer=tokenizer,
        prompts=prompts,
        group_size=args.group_size,
        max_new_tokens=args.max_new_tokens,
        temperature=0.8,
        top_p=args.top_p,
    )
    old_logprobs = sequence_logprobs(model, full_sequences, prompt_lengths, tokenizer.pad_token_id)
    ref_logprobs = sequence_logprobs(ref_model, full_sequences, prompt_lengths, tokenizer.pad_token_id)
    batches: list[CandidateBatch] = []
    cursor = 0
    for prompt_index, sample in enumerate(samples):
        group_responses = responses[cursor : cursor + args.group_size]
        group_rewards = [reward_fn.score(sample, response) for response in group_responses]
        reward_values = [reward.total_reward for reward in group_rewards]
        batches.append(
            CandidateBatch(
                prompt_index=prompt_index,
                prompt=sample.prompt,
                responses=group_responses,
                full_sequence_ids=[
                    full_sequences[cursor + offset].detach().clone()
                    for offset in range(args.group_size)
                ],
                prompt_token_lengths=[
                    int(prompt_lengths[cursor + offset].item())
                    for offset in range(args.group_size)
                ],
                old_logprobs=old_logprobs[cursor : cursor + args.group_size],
                ref_logprobs=ref_logprobs[cursor : cursor + args.group_size],
                rewards=group_rewards,
                advantages=normalize_advantages(reward_values),
            )
        )
        cursor += args.group_size
    return batches


def grpo_loss_for_batch(
    model: Any,
    tokenizer: Any,
    candidate_batch: CandidateBatch,
    beta: float,
    epsilon: float,
    device: torch.device,
) -> tuple[torch.Tensor, dict[str, float]]:
    losses: list[torch.Tensor] = []
    kl_values: list[torch.Tensor] = []
    clip_fracs: list[torch.Tensor] = []
    for i, sequence in enumerate(candidate_batch.full_sequence_ids):
        sequence = sequence.to(device)
        prompt_len = candidate_batch.prompt_token_lengths[i]
        current_logprob = train_sequence_logprob(model, sequence, prompt_len, tokenizer.pad_token_id)
        old_logprob = candidate_batch.old_logprobs[i].to(device).mean()
        ref_logprob = candidate_batch.ref_logprobs[i].to(device).mean()
        advantage = candidate_batch.advantages[i].to(device)
        ratio = torch.exp(current_logprob - old_logprob)
        unclipped = ratio * advantage
        clipped = torch.clamp(ratio, 1.0 - epsilon, 1.0 + epsilon) * advantage
        policy_loss = -torch.minimum(unclipped, clipped)
        kl = current_logprob - ref_logprob
        loss = policy_loss + beta * kl
        losses.append(loss)
        kl_values.append(kl.detach())
        clip_fracs.append(((ratio.detach() > 1 + epsilon) | (ratio.detach() < 1 - epsilon)).float())
    stacked = torch.stack(losses).mean()
    metrics = {
        "kl": torch.stack(kl_values).mean().item(),
        "clip_frac": torch.stack(clip_fracs).mean().item(),
    }
    return stacked, metrics


def init_wandb(args: argparse.Namespace) -> None:
    if not args.wandb or wandb is None:
        return
    wandb.init(
        project=args.wandb_project,
        name=args.run_name,
        config=vars(args) | {"trl_version": getattr(trl, "__version__", None)},
    )


def log_metrics(args: argparse.Namespace, step: int, metrics: dict[str, float]) -> None:
    if args.wandb and wandb is not None:
        wandb.log(metrics, step=step)


def save_checkpoint(accelerator: Accelerator, model: Any, tokenizer: Any, output_dir: Path, step: int) -> None:
    target = output_dir / f"checkpoint-{step}"
    target.mkdir(parents=True, exist_ok=True)
    unwrapped = accelerator.unwrap_model(model)
    unwrapped.save_pretrained(target, safe_serialization=True, is_main_process=accelerator.is_main_process)
    if accelerator.is_main_process:
        tokenizer.save_pretrained(target)


def train(args: argparse.Namespace) -> None:
    deepspeed_plugin = build_deepspeed_plugin(args)
    accelerator = Accelerator(
        mixed_precision="bf16" if args.bf16 else "no",
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        deepspeed_plugin=deepspeed_plugin,
    )
    init_wandb(args)
    model, ref_model, tokenizer = model_setup(args)
    dataset = JsonlPromptDataset(args.dataset)
    dataloader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate_samples)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    total_steps = args.max_steps or (len(dataloader) * args.epochs)
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=args.warmup_steps,
        num_training_steps=total_steps,
    )
    model, optimizer, dataloader, scheduler = accelerator.prepare(model, optimizer, dataloader, scheduler)
    ref_model = accelerator.prepare(ref_model)
    reward_fn = MultiComponentReward(
        weights=RewardWeights(args.w1, args.w2, args.w3, args.w4),
        static_analyzer=StaticSecurityAnalyzer(args.semgrep_config),
        sandbox_runner=SandboxRewardRunner(enabled=not args.disable_sandbox),
    )
    global_step = 0
    model.train()
    for epoch in range(args.epochs):
        progress = tqdm(dataloader, disable=not accelerator.is_main_process, desc=f"epoch {epoch + 1}")
        for samples in progress:
            with torch.no_grad():
                candidate_batches = build_candidate_batches(
                    model=accelerator.unwrap_model(model),
                    ref_model=accelerator.unwrap_model(ref_model),
                    tokenizer=tokenizer,
                    samples=samples,
                    reward_fn=reward_fn,
                    args=args,
                )
            total_loss = torch.tensor(0.0, device=accelerator.device)
            kl_values: list[float] = []
            clip_values: list[float] = []
            all_rewards: list[RewardBreakdown] = []
            for candidate_batch in candidate_batches:
                loss, loss_metrics = grpo_loss_for_batch(
                    model=model,
                    tokenizer=tokenizer,
                    candidate_batch=candidate_batch,
                    beta=args.beta,
                    epsilon=args.epsilon,
                    device=accelerator.device,
                )
                total_loss = total_loss + loss
                kl_values.append(loss_metrics["kl"])
                clip_values.append(loss_metrics["clip_frac"])
                all_rewards.extend(candidate_batch.rewards)
            total_loss = total_loss / max(1, len(candidate_batches))
            accelerator.backward(total_loss)
            if accelerator.sync_gradients:
                clip_grad_norm_(model.parameters(), args.max_grad_norm)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            global_step += 1
            reward_values = [reward.total_reward for reward in all_rewards]
            metrics = aggregate_metrics(total_loss.item(), reward_values, all_rewards, kl_values, clip_values)
            if accelerator.is_main_process:
                progress.set_postfix({"loss": metrics["loss"], "reward": metrics["reward/mean"]})
                log_metrics(args, global_step, metrics)
            if args.save_steps and global_step % args.save_steps == 0:
                save_checkpoint(accelerator, model, tokenizer, args.output_dir, global_step)
            if args.max_steps and global_step >= args.max_steps:
                break
        if args.max_steps and global_step >= args.max_steps:
            break
    save_checkpoint(accelerator, model, tokenizer, args.output_dir, global_step)
    if args.wandb and wandb is not None:
        wandb.finish()


def aggregate_metrics(
    loss: float,
    reward_values: list[float],
    rewards: list[RewardBreakdown],
    kl_values: list[float],
    clip_values: list[float],
) -> dict[str, float]:
    if not reward_values:
        reward_values = [0.0]
    return {
        "loss": loss,
        "reward/mean": float(sum(reward_values) / len(reward_values)),
        "reward/max": float(max(reward_values)),
        "reward/min": float(min(reward_values)),
        "reward/exec": mean([item.exec_reward for item in rewards]),
        "reward/security": mean([item.security_reward for item in rewards]),
        "reward/format": mean([item.format_reward for item in rewards]),
        "reward/efficiency": mean([item.efficiency_reward for item in rewards]),
        "kl/mean": mean(kl_values),
        "clip_frac": mean(clip_values),
        "pass_at_1": pass_at_k(reward_values, 1),
        "pass_at_5": pass_at_k(reward_values, 5),
        "pass_at_10": pass_at_k(reward_values, 10),
    }


def mean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 7 GRPO training loop.")
    parser.add_argument("--model-name-or-path", required=True)
    parser.add_argument("--ref-model-name-or-path")
    parser.add_argument("--model-revision")
    parser.add_argument("--ref-model-revision")
    parser.add_argument("--allow-unpinned-model-revision", action="store_true")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--group-size", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--learning-rate", type=float, default=1e-6)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--warmup-steps", type=int, default=10)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=1)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--beta", type=float, default=0.01)
    parser.add_argument("--epsilon", type=float, default=0.2)
    parser.add_argument("--w1", type=float, default=1.0)
    parser.add_argument("--w2", type=float, default=1.0)
    parser.add_argument("--w3", type=float, default=1.0)
    parser.add_argument("--w4", type=float, default=1.0)
    parser.add_argument("--bf16", action="store_true")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--deepspeed", action="store_true")
    parser.add_argument("--offload-optimizer-device", default="none", choices=["none", "cpu", "nvme"])
    parser.add_argument("--disable-sandbox", action="store_true")
    parser.add_argument("--semgrep-config")
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--save-steps", type=int, default=100)
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--wandb-project", default="mythos-grpo")
    parser.add_argument("--run-name", default=f"grpo-{int(time.time())}")
    args = parser.parse_args()
    if args.group_size != 8:
        raise SystemExit("Task specification requires --group-size exactly 8.")
    if abs(args.temperature - 0.8) > 1e-9:
        raise SystemExit("Task specification requires temperature exactly 0.8.")
    validate_revision_policy(args)
    return args


def main() -> None:
    train(parse_args())


if __name__ == "__main__":
    main()
