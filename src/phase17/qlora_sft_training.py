#!/usr/bin/env python
"""QLoRA SFT trainer for future 7B-32B coding/security models."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
    if not rows:
        raise ValueError(f"dataset is empty: {path}")
    return rows


def looks_like_local_model_path(value: str) -> bool:
    return value.startswith(".") or value.startswith("/") or ":\\" in value or Path(value).exists()


def validate_revision_policy(args: argparse.Namespace) -> None:
    if args.allow_unpinned_model_revision or looks_like_local_model_path(args.model_name_or_path):
        return
    if not args.model_revision:
        raise ValueError("remote models require --model-revision for reproducible training")


def format_sample(row: dict[str, Any]) -> str:
    prompt = row.get("prompt") or row.get("instruction") or row.get("input")
    chosen = row.get("chosen") or row.get("output") or row.get("response")
    if prompt is None or chosen is None:
        raise ValueError("each SFT row must include prompt and chosen/output/response")
    return f"<|user|>\n{prompt}\n<|assistant|>\n{chosen}"


def train(args: argparse.Namespace) -> None:
    validate_revision_policy(args)

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    rows = load_jsonl(args.dataset)
    texts = [format_sample(row) for row in rows]
    dataset = Dataset.from_dict({"text": texts})

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_name_or_path,
        revision=args.model_revision,
        use_fast=True,
        trust_remote_code=args.trust_remote_code,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    if args.load_in_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16 if args.bf16 else torch.float16,
        )

    model = AutoModelForCausalLM.from_pretrained(
        args.model_name_or_path,
        revision=args.model_revision,
        quantization_config=quantization_config,
        torch_dtype=torch.bfloat16 if args.bf16 else torch.float16,
        trust_remote_code=args.trust_remote_code,
        device_map=args.device_map,
    )
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.config.use_cache = False
    if args.load_in_4bit:
        model = prepare_model_for_kbit_training(model)

    target_modules = [item.strip() for item in args.target_modules.split(",") if item.strip()]
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=target_modules,
    )
    model = get_peft_model(model, lora_config)

    def tokenize(batch: dict[str, list[str]]) -> dict[str, Any]:
        return tokenizer(
            batch["text"],
            truncation=True,
            max_length=args.max_seq_len,
            padding=False,
        )

    tokenized = dataset.map(tokenize, batched=True, remove_columns=["text"])
    training_args = TrainingArguments(
        output_dir=str(args.output_dir),
        per_device_train_batch_size=args.per_device_train_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        warmup_ratio=args.warmup_ratio,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        bf16=args.bf16,
        fp16=not args.bf16,
        optim=args.optim,
        report_to=["wandb"] if args.wandb else [],
        deepspeed=args.deepspeed_config,
        gradient_checkpointing=args.gradient_checkpointing,
        lr_scheduler_type="cosine",
        weight_decay=args.weight_decay,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
        data_collator=DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False),
    )
    trainer.train()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QLoRA SFT trainer for 7B-32B coding/security models.")
    parser.add_argument("--model-name-or-path", required=True)
    parser.add_argument("--model-revision")
    parser.add_argument("--allow-unpinned-model-revision", action="store_true")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--deepspeed-config", default="deploy/gpu/deepspeed_zero2.json")
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument("--bf16", action="store_true")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--max-seq-len", type=int, default=4096)
    parser.add_argument("--per-device-train-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--save-steps", type=int, default=200)
    parser.add_argument("--optim", default="paged_adamw_8bit")
    parser.add_argument("--lora-r", type=int, default=64)
    parser.add_argument("--lora-alpha", type=int, default=128)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument(
        "--target-modules",
        default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
    )
    parser.add_argument("--wandb", action="store_true")
    return parser.parse_args()


def main() -> None:
    train(parse_args())


if __name__ == "__main__":
    main()

