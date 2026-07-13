"""Generate validated chosen/rejected preference pairs."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pydantic import Field

from src.mythos.alignment.common import PreferencePair, save_jsonl
from src.mythos.shared.schemas import StrictModel


REFUSAL_TERMS = ("can't help", "cannot help", "can't assist", "cannot assist", "i can't help", "i cannot")
HARMFUL_TERMS = ("ransomware", "steal", "break into", "without permission", "malware", "live target")


class PreferencePairReport(StrictModel):
    output_path: str
    prompt_rows: int
    candidate_rows: int
    pairs: int
    rejected_rows: int
    created_at_unix: float = Field(default_factory=time.time)


def _load_jsonl(path: str | Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _safety_score(prompt: str, response: str) -> float:
    lowered_prompt = prompt.lower()
    lowered = response.lower()
    harmful_prompt = any(term in lowered_prompt for term in HARMFUL_TERMS)
    refuses = any(term in lowered for term in REFUSAL_TERMS)
    defensive = any(term in lowered for term in ("defensive", "mitigation", "patch", "test", "detect", "authorized"))
    harmful_output = any(term in lowered for term in ("payload", "steal cookies", "encrypt files", "disable antivirus"))
    score = 0.5
    if harmful_prompt and refuses:
        score += 0.3
    if not harmful_prompt and not refuses:
        score += 0.2
    if defensive:
        score += 0.2
    if harmful_output:
        score -= 0.5
    return max(0.0, min(1.0, score))


def generate_preference_pairs(
    *,
    prompts_path: str | Path,
    candidate_outputs_path: str | Path,
    output_path: str | Path,
) -> PreferencePairReport:
    prompts = _load_jsonl(prompts_path)
    candidates = _load_jsonl(candidate_outputs_path)
    by_prompt: dict[str, list[dict[str, object]]] = {}
    for row in candidates:
        prompt = str(row.get("prompt") or "")
        if prompt:
            by_prompt.setdefault(prompt, []).append(row)
    pairs: list[PreferencePair] = []
    rejected_rows = 0
    for prompt_row in prompts:
        prompt = str(prompt_row.get("prompt") or prompt_row.get("question") or "")
        if not prompt:
            rejected_rows += 1
            continue
        rows = by_prompt.get(prompt, [])
        scored = sorted(
            ((row, _safety_score(prompt, str(row.get("response") or row.get("output") or ""))) for row in rows),
            key=lambda item: item[1],
            reverse=True,
        )
        if len(scored) < 2:
            rejected_rows += 1
            continue
        chosen = str(scored[0][0].get("response") or scored[0][0].get("output") or "")
        rejected = str(scored[-1][0].get("response") or scored[-1][0].get("output") or "")
        if not chosen or not rejected or chosen == rejected:
            rejected_rows += 1
            continue
        harmful = any(term in prompt.lower() for term in HARMFUL_TERMS)
        pairs.append(
            PreferencePair(
                prompt=prompt,
                chosen=chosen,
                rejected=rejected,
                category=str(prompt_row.get("category") or "general"),
                safety_label="harmful" if harmful else "helpful_defensive",
                source=str(prompt_row.get("source") or "preference_generation"),
                metadata={"chosen_score": scored[0][1], "rejected_score": scored[-1][1]},
            )
        )
    save_jsonl(output_path, pairs)
    report = PreferencePairReport(
        output_path=str(output_path),
        prompt_rows=len(prompts),
        candidate_rows=len(candidates),
        pairs=len(pairs),
        rejected_rows=rejected_rows,
    )
    Path(output_path).with_name("preference_pair_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Mythos preference pairs.")
    parser.add_argument("--prompts", required=True)
    parser.add_argument("--candidate-outputs", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = generate_preference_pairs(prompts_path=args.prompts, candidate_outputs_path=args.candidate_outputs, output_path=args.output)
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
