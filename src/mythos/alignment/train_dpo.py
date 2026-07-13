"""Native PyTorch DPO-style alignment loop for Mythos scratch checkpoints."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pydantic import Field

from src.mythos.alignment.common import PreferencePair, encode_text, load_checkpoint_model, load_jsonl_models, load_policy, load_tokenizer_required, prompt_response_text, select_device
from src.mythos.model_ops.pretrain_loop import save_training_checkpoint
from src.mythos.shared.schemas import StrictModel

try:
    import torch
    import torch.nn.functional as F
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]


class DPOTrainingReport(StrictModel):
    status: str
    mode: str
    checkpoint_manifest: str
    output_dir: str
    beta: float
    steps: int
    losses: list[float]
    pair_count: int
    duration_ms: float
    created_at_unix: float = Field(default_factory=time.time)


def sequence_logprob(model: object, input_ids: object) -> object:
    output = model(input_ids)
    logits = output.logits[:, :-1, :]
    labels = input_ids[:, 1:]
    log_probs = F.log_softmax(logits, dim=-1)
    token_log_probs = log_probs.gather(-1, labels.unsqueeze(-1)).squeeze(-1)
    return token_log_probs.sum(dim=-1)


def dpo_loss(
    *,
    policy_chosen_logp: object,
    policy_rejected_logp: object,
    reference_chosen_logp: object,
    reference_rejected_logp: object,
    beta: float,
) -> object:
    policy_margin = policy_chosen_logp - policy_rejected_logp
    reference_margin = reference_chosen_logp - reference_rejected_logp
    return -F.logsigmoid(beta * (policy_margin - reference_margin)).mean()


def train_dpo(
    *,
    policy_checkpoint: str | Path,
    reference_checkpoint: str | Path,
    pairs: str | Path,
    output_dir: str | Path,
    tokenizer_path: str | Path | None = None,
    policy_path: str | Path = "config/alignment_policy.json",
    steps: int = 10,
    device: str = "auto",
    learning_rate: float | None = None,
    beta: float | None = None,
) -> DPOTrainingReport:
    if torch is None or F is None:
        raise RuntimeError("torch is required for DPO training")
    selected = select_device(device)
    tokenizer = load_tokenizer_required(tokenizer_path)
    policy_model, policy_manifest, _payload = load_checkpoint_model(policy_checkpoint, device=selected)
    reference_model, _reference_manifest, _reference_payload = load_checkpoint_model(reference_checkpoint, device=selected)
    reference_model.eval()
    for parameter in reference_model.parameters():
        parameter.requires_grad_(False)
    active_policy = load_policy(policy_path)
    active_beta = float(beta if beta is not None else active_policy.get("dpo_beta", 0.1))
    lr = float(learning_rate or active_policy.get("learning_rate", 5e-5))
    pair_rows = [item for item in load_jsonl_models(pairs, PreferencePair)]  # type: ignore[list-item]
    if not pair_rows:
        raise ValueError("preference pair dataset has no valid pairs")
    optimizer = torch.optim.AdamW(policy_model.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.01)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    policy_model.train()
    losses: list[float] = []
    started = time.perf_counter()
    for step in range(steps):
        pair = pair_rows[step % len(pair_rows)]
        chosen_ids = encode_text(tokenizer, prompt_response_text(pair.prompt, pair.chosen), max_length=policy_model.config.max_sequence_length)
        rejected_ids = encode_text(tokenizer, prompt_response_text(pair.prompt, pair.rejected), max_length=policy_model.config.max_sequence_length)
        if len(chosen_ids) < 2 or len(rejected_ids) < 2:
            continue
        chosen = torch.tensor([chosen_ids], dtype=torch.long, device=selected)
        rejected = torch.tensor([rejected_ids], dtype=torch.long, device=selected)
        policy_chosen = sequence_logprob(policy_model, chosen)
        policy_rejected = sequence_logprob(policy_model, rejected)
        with torch.no_grad():
            ref_chosen = sequence_logprob(reference_model, chosen)
            ref_rejected = sequence_logprob(reference_model, rejected)
        loss = dpo_loss(
            policy_chosen_logp=policy_chosen,
            policy_rejected_logp=policy_rejected,
            reference_chosen_logp=ref_chosen,
            reference_rejected_logp=ref_rejected,
            beta=active_beta,
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(policy_model.parameters(), 1.0)
        optimizer.step()
        losses.append(float(loss.detach().cpu()))
    if not losses:
        raise RuntimeError("DPO loop produced no trainable batches")
    manifest_path = save_training_checkpoint(
        output_dir=root,
        model=policy_model,
        optimizer=optimizer,
        config=policy_model.config,
        step=policy_manifest.step + len(losses),
        trained_tokens=policy_manifest.trained_tokens,
        metrics={"dpo_loss": losses[-1], "dpo_loss_best": min(losses), "dpo_beta": active_beta},
        manifest_filename="checkpoint_manifest.json",
    )
    report = DPOTrainingReport(
        status="passed",
        mode="dpo",
        checkpoint_manifest=str(manifest_path),
        output_dir=str(root),
        beta=active_beta,
        steps=len(losses),
        losses=losses,
        pair_count=len(pair_rows),
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    (root / "alignment_training_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Mythos scratch checkpoint with native DPO.")
    parser.add_argument("--policy-checkpoint", required=True)
    parser.add_argument("--reference-checkpoint", required=True)
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tokenizer-path")
    parser.add_argument("--policy", default="config/alignment_policy.json")
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--beta", type=float)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = train_dpo(
        policy_checkpoint=args.policy_checkpoint,
        reference_checkpoint=args.reference_checkpoint,
        pairs=args.pairs,
        output_dir=args.output_dir,
        tokenizer_path=args.tokenizer_path,
        policy_path=args.policy,
        steps=args.steps,
        device=args.device,
        learning_rate=args.learning_rate,
        beta=args.beta,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
