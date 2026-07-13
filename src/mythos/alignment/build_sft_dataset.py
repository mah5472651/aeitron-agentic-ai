"""Build refusal-injected SFT datasets for Mythos scratch alignment."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.alignment.common import SFTExample, load_policy, save_jsonl
from src.mythos.shared.schemas import StrictModel


class SFTDatasetReport(StrictModel):
    output_path: str
    input_rows: int
    output_rows: int
    refusal_rows: int
    refusal_injection_ratio: float
    created_at_unix: float = Field(default_factory=time.time)


def _normalize_safety_label(value: object) -> str:
    label = str(value or "neutral").lower().strip()
    if label in {"harmful", "helpful_defensive", "neutral"}:
        return label
    if label in {"safe", "defensive", "allowed", "benign"}:
        return "helpful_defensive"
    if label in {"unsafe", "misuse", "blocked"}:
        return "harmful"
    return "neutral"


def _normalize_row(row: dict[str, Any]) -> SFTExample | None:
    if "messages" in row and isinstance(row["messages"], list):
        user_parts = [str(item.get("content", "")) for item in row["messages"] if item.get("role") == "user"]
        assistant_parts = [str(item.get("content", "")) for item in row["messages"] if item.get("role") == "assistant"]
        if user_parts and assistant_parts:
            return SFTExample(
                prompt=user_parts[-1],
                response=assistant_parts[-1],
                category=str(row.get("category") or "general"),
                safety_label=_normalize_safety_label(row.get("safety_label")),
                source=str(row.get("source") or "messages"),
                metadata=row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {},
            )
    prompt = str(row.get("prompt") or row.get("instruction") or row.get("question") or "")
    response = str(row.get("response") or row.get("chosen") or row.get("answer") or "")
    if not prompt or not response:
        return None
    return SFTExample(
        prompt=prompt,
        response=response,
        category=str(row.get("category") or row.get("task_type") or "general"),
        safety_label=_normalize_safety_label(row.get("safety_label")),
        source=str(row.get("source") or row.get("source_url") or "unknown"),
        metadata=row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {},
    )


def build_sft_dataset(
    *,
    input_tasks: list[str | Path],
    policy_path: str | Path,
    output_path: str | Path,
) -> SFTDatasetReport:
    policy = load_policy(policy_path)
    ratio = float(policy.get("refusal_injection_ratio", 0.15))
    if ratio < 0.0 or ratio >= 1.0:
        raise ValueError("refusal_injection_ratio must be >= 0.0 and < 1.0")
    max_prompt_chars = int(policy.get("max_prompt_chars", 12000))
    max_response_chars = int(policy.get("max_response_chars", 12000))
    examples: list[SFTExample] = []
    input_rows = 0
    for path in input_tasks:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                input_rows += 1
                row = json.loads(line)
                example = _normalize_row(row)
                if example is None:
                    continue
                examples.append(
                    example.model_copy(
                        update={
                            "prompt": example.prompt[:max_prompt_chars],
                            "response": example.response[:max_response_chars],
                        }
                    )
                )
    refusal_templates = policy.get("refusal_templates", [])
    target_refusals = math.ceil(len(examples) * ratio / max(1e-9, 1.0 - ratio)) if examples else len(refusal_templates)
    refusal_rows = 0
    for index in range(min(max(target_refusals, 0), max(target_refusals, len(refusal_templates)))):
        template = refusal_templates[index % len(refusal_templates)] if refusal_templates else None
        if template is None:
            break
        examples.append(
            SFTExample(
                prompt=str(template["prompt"])[:max_prompt_chars],
                response=str(template["response"])[:max_response_chars],
                category="safety_refusal",
                safety_label="harmful",
                source="alignment_policy",
                metadata={"injected_refusal": True},
            )
        )
        refusal_rows += 1
        if refusal_rows >= target_refusals:
            break
    save_jsonl(output_path, examples)
    report = SFTDatasetReport(
        output_path=str(output_path),
        input_rows=input_rows,
        output_rows=len(examples),
        refusal_rows=refusal_rows,
        refusal_injection_ratio=round(refusal_rows / max(1, len(examples)), 6),
    )
    Path(output_path).with_name("sft_dataset_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Mythos refusal-injected SFT JSONL.")
    parser.add_argument("--input-tasks", nargs="+", required=True)
    parser.add_argument("--policy", default="config/alignment_policy.json")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = build_sft_dataset(input_tasks=args.input_tasks, policy_path=args.policy, output_path=args.output)
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
