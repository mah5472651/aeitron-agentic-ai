"""Compare two Mythos scratch checkpoints on a fixed prompt suite."""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.model_ops.foundation import CheckpointManifest
from src.mythos.model_ops.tokenizer_pipeline import load_tokenizer
from src.mythos.model_ops.torch_decoder import MythosDecoderLM, ScratchDecoderConfig, load_trusted_checkpoint, require_torch
from src.mythos.shared.schemas import StrictModel

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


DEFAULT_PROMPTS = [
    {
        "task_id": "security-sql-injection",
        "category": "defensive_security",
        "prompt": "Find the SQL injection risk and describe a safe patch:\n\ncursor.execute('SELECT * FROM users WHERE name=' + user_input)",
        "expected_terms": ["sql", "injection", "parameter", "query"],
        "forbidden_terms": ["exploit", "payload"],
    },
    {
        "task_id": "security-xss",
        "category": "defensive_security",
        "prompt": "Review this JavaScript for XSS risk and give a defensive fix:\n\ndocument.body.innerHTML = location.hash.substring(1)",
        "expected_terms": ["xss", "escape", "textcontent", "sanitize"],
        "forbidden_terms": ["steal", "cookie"],
    },
    {
        "task_id": "debug-python-traceback",
        "category": "debugging",
        "prompt": "Debug this Python traceback and propose the smallest safe fix:\n\nTraceback (most recent call last):\n  File \"app.py\", line 4, in <module>\n    print(user.name)\nAttributeError: 'NoneType' object has no attribute 'name'",
        "expected_terms": ["none", "attributeerror", "check", "fix"],
        "forbidden_terms": ["ignore"],
    },
    {
        "task_id": "agentic-fastapi-auth",
        "category": "agentic_coding",
        "prompt": "Plan a small FastAPI JWT auth middleware with tests. Return requirements, implementation steps, and verification.",
        "expected_terms": ["jwt", "middleware", "test", "verify"],
        "forbidden_terms": ["hardcode"],
    },
    {
        "task_id": "patch-regression",
        "category": "patch_generation",
        "prompt": "A login function accepts empty passwords. Write a defensive patch plan and regression tests.",
        "expected_terms": ["password", "empty", "test", "validation"],
        "forbidden_terms": ["bypass"],
    },
]


class PromptCase(StrictModel):
    task_id: str
    category: str
    prompt: str
    expected_terms: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)


class GenerationConfig(StrictModel):
    max_new_tokens: int = Field(default=96, ge=1, le=2048)
    temperature: float = Field(default=0.0, ge=0.0, le=5.0)
    top_k: int = Field(default=20, ge=0, le=500)
    seed: int = 1337


class CandidateResult(StrictModel):
    task_id: str
    category: str
    prompt: str
    output: str
    score: float = Field(ge=0.0, le=1.0)
    expected_hits: list[str] = Field(default_factory=list)
    missing_expected_terms: list[str] = Field(default_factory=list)
    forbidden_hits: list[str] = Field(default_factory=list)
    repetition_ratio: float
    token_count: int
    latency_ms: float


class CheckpointSideReport(StrictModel):
    label: str
    checkpoint_manifest: str
    checkpoint_step: int
    trained_tokens: int
    average_score: float
    pass_count: int
    total: int
    results: list[CandidateResult]


class CheckpointComparisonReport(StrictModel):
    status: str
    tokenizer_path: str
    device: str
    generation: dict[str, Any]
    baseline: CheckpointSideReport
    candidate: CheckpointSideReport
    score_delta: float
    pass_delta: int
    improved_tasks: list[str]
    regressed_tasks: list[str]
    unchanged_tasks: list[str]
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)

    def write(self, output_dir: str | Path) -> Path:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        json_path = root / "checkpoint_comparison_report.json"
        json_path.write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        write_markdown(self, root / "checkpoint_comparison_report.md")
        return json_path


def _select_device(requested: str) -> "torch.device":
    require_torch()
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    return torch.device(requested)


def _load_manifest(path: str | Path) -> CheckpointManifest:
    return CheckpointManifest.model_validate(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def _load_model(manifest_path: str | Path, *, device: "torch.device") -> tuple[MythosDecoderLM, CheckpointManifest]:
    manifest = _load_manifest(manifest_path)
    checkpoint_path = Path(manifest.checkpoint_dir) / "model.pt"
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"checkpoint model file not found: {checkpoint_path}")
    payload = load_trusted_checkpoint(checkpoint_path, map_location=device)
    config = ScratchDecoderConfig.model_validate(payload["config"])
    model = MythosDecoderLM(config).to(device)
    model.load_state_dict(payload["model"])
    model.eval()
    return model, manifest


def _load_prompt_suite(path: str | Path | None) -> list[PromptCase]:
    if path is None:
        return [PromptCase.model_validate(item) for item in DEFAULT_PROMPTS]
    source = Path(path)
    if source.suffix == ".jsonl":
        return [PromptCase.model_validate(json.loads(line)) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload["prompts"] if isinstance(payload, dict) and "prompts" in payload else payload
    return [PromptCase.model_validate(item) for item in rows]


def _repetition_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_+-]*", text.lower())
    if not words:
        return 1.0
    return 1.0 - (len(set(words)) / len(words))


def _score_output(output: str, case: PromptCase) -> tuple[float, list[str], list[str], list[str], float]:
    lowered = output.lower()
    expected = [term.lower() for term in case.expected_terms]
    forbidden = [term.lower() for term in case.forbidden_terms]
    expected_hits = [term for term in expected if term in lowered]
    missing = [term for term in expected if term not in lowered]
    forbidden_hits = [term for term in forbidden if term in lowered]
    repetition = _repetition_ratio(output)
    nonempty_score = 0.2 if len(output.strip()) >= 20 else 0.0
    expected_score = 0.55 * (len(expected_hits) / max(1, len(expected)))
    structure_score = 0.15 if any(marker in lowered for marker in ["fix", "test", "step", "validate", "patch", "risk"]) else 0.0
    repetition_penalty = 0.15 if repetition > 0.65 else 0.0
    forbidden_penalty = min(0.3, 0.15 * len(forbidden_hits))
    score = max(0.0, min(1.0, nonempty_score + expected_score + structure_score + 0.1 - repetition_penalty - forbidden_penalty))
    return round(score, 6), expected_hits, missing, forbidden_hits, round(repetition, 6)


@torch.no_grad() if torch is not None else (lambda fn: fn)
def generate_text(
    *,
    model: MythosDecoderLM,
    tokenizer: Any,
    prompt: str,
    device: "torch.device",
    config: GenerationConfig,
) -> tuple[str, int]:
    encoded = tokenizer.encode(prompt).ids
    if not encoded:
        encoded = [0]
    max_context = max(1, model.config.max_sequence_length - config.max_new_tokens)
    input_ids = encoded[-max_context:]
    generated: list[int] = []
    if config.seed:
        torch.manual_seed(config.seed)
        if device.type == "cuda":
            torch.cuda.manual_seed_all(config.seed)
    for _ in range(config.max_new_tokens):
        active = torch.tensor([input_ids + generated], dtype=torch.long, device=device)
        if active.size(1) > model.config.max_sequence_length:
            active = active[:, -model.config.max_sequence_length :]
        logits = model(active).logits[:, -1, :]
        if config.temperature <= 0:
            next_token = int(torch.argmax(logits, dim=-1).item())
        else:
            logits = logits / max(config.temperature, 1e-6)
            if config.top_k > 0:
                values, indices = torch.topk(logits, k=min(config.top_k, logits.size(-1)), dim=-1)
                probs = torch.softmax(values, dim=-1)
                next_token = int(indices[0, torch.multinomial(probs[0], 1).item()].item())
            else:
                probs = torch.softmax(logits, dim=-1)
                next_token = int(torch.multinomial(probs[0], 1).item())
        generated.append(next_token)
    return tokenizer.decode(generated), len(generated)


def _evaluate_side(
    *,
    label: str,
    checkpoint_manifest: str | Path,
    tokenizer: Any,
    prompts: list[PromptCase],
    device: "torch.device",
    generation_config: GenerationConfig,
) -> CheckpointSideReport:
    model, manifest = _load_model(checkpoint_manifest, device=device)
    results: list[CandidateResult] = []
    for case in prompts:
        started = time.perf_counter()
        output, token_count = generate_text(
            model=model,
            tokenizer=tokenizer,
            prompt=case.prompt,
            device=device,
            config=generation_config,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        score, expected_hits, missing, forbidden_hits, repetition = _score_output(output, case)
        results.append(
            CandidateResult(
                task_id=case.task_id,
                category=case.category,
                prompt=case.prompt,
                output=output,
                score=score,
                expected_hits=expected_hits,
                missing_expected_terms=missing,
                forbidden_hits=forbidden_hits,
                repetition_ratio=repetition,
                token_count=token_count,
                latency_ms=round(latency_ms, 3),
            )
        )
    average = sum(item.score for item in results) / max(1, len(results))
    return CheckpointSideReport(
        label=label,
        checkpoint_manifest=str(checkpoint_manifest),
        checkpoint_step=manifest.step,
        trained_tokens=manifest.trained_tokens,
        average_score=round(average, 6),
        pass_count=sum(1 for item in results if item.score >= 0.65),
        total=len(results),
        results=results,
    )


def compare_checkpoints(
    *,
    baseline_manifest: str | Path,
    candidate_manifest: str | Path,
    tokenizer_path: str | Path,
    prompt_suite: str | Path | None = None,
    output_dir: str | Path = "artifacts/mythos/checkpoint-compare",
    device: str = "auto",
    generation_config: GenerationConfig | None = None,
) -> CheckpointComparisonReport:
    require_torch()
    active_generation = generation_config or GenerationConfig()
    selected = _select_device(device)
    tokenizer = load_tokenizer(tokenizer_path)
    prompts = _load_prompt_suite(prompt_suite)
    baseline = _evaluate_side(
        label="baseline",
        checkpoint_manifest=baseline_manifest,
        tokenizer=tokenizer,
        prompts=prompts,
        device=selected,
        generation_config=active_generation,
    )
    candidate = _evaluate_side(
        label="candidate",
        checkpoint_manifest=candidate_manifest,
        tokenizer=tokenizer,
        prompts=prompts,
        device=selected,
        generation_config=active_generation,
    )
    baseline_by_task = {item.task_id: item for item in baseline.results}
    improved: list[str] = []
    regressed: list[str] = []
    unchanged: list[str] = []
    for item in candidate.results:
        delta = item.score - baseline_by_task[item.task_id].score
        if delta > 0.05:
            improved.append(item.task_id)
        elif delta < -0.05:
            regressed.append(item.task_id)
        else:
            unchanged.append(item.task_id)
    score_delta = round(candidate.average_score - baseline.average_score, 6)
    pass_delta = candidate.pass_count - baseline.pass_count
    if score_delta > 0.03 and not regressed:
        recommendation = "candidate_improved"
        status = "improved"
    elif score_delta < -0.03 or pass_delta < 0:
        recommendation = "candidate_regressed"
        status = "regressed"
    else:
        recommendation = "candidate_neutral"
        status = "neutral"
    report = CheckpointComparisonReport(
        status=status,
        tokenizer_path=str(tokenizer_path),
        device=str(selected),
        generation=active_generation.model_dump(),
        baseline=baseline,
        candidate=candidate,
        score_delta=score_delta,
        pass_delta=pass_delta,
        improved_tasks=improved,
        regressed_tasks=regressed,
        unchanged_tasks=unchanged,
        recommendation=recommendation,
    )
    report.write(output_dir)
    return report


def write_markdown(report: CheckpointComparisonReport, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    baseline_by_task = {item.task_id: item for item in report.baseline.results}
    lines = [
        "# Mythos Checkpoint Comparison",
        "",
        f"- status: {report.status}",
        f"- recommendation: {report.recommendation}",
        f"- device: {report.device}",
        f"- baseline average: {report.baseline.average_score:.4f}",
        f"- candidate average: {report.candidate.average_score:.4f}",
        f"- score delta: {report.score_delta:.4f}",
        f"- pass delta: {report.pass_delta}",
        "",
        "| task | category | baseline | candidate | delta |",
        "|---|---|---:|---:|---:|",
    ]
    for item in report.candidate.results:
        base = baseline_by_task[item.task_id]
        delta = item.score - base.score
        lines.append(f"| {item.task_id} | {item.category} | {base.score:.3f} | {item.score:.3f} | {delta:.3f} |")
    lines.extend(["", "## Candidate Outputs", ""])
    for item in report.candidate.results:
        lines.extend(
            [
                f"### {item.task_id}",
                "",
                f"- score: {item.score:.3f}",
                f"- expected hits: {', '.join(item.expected_hits) if item.expected_hits else 'none'}",
                f"- missing: {', '.join(item.missing_expected_terms) if item.missing_expected_terms else 'none'}",
                "",
                "```text",
                item.output[:2000],
                "```",
                "",
            ]
        )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two Mythos scratch checkpoints with deterministic local scoring.")
    parser.add_argument("--baseline-manifest", required=True)
    parser.add_argument("--candidate-manifest", required=True)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--prompt-suite")
    parser.add_argument("--output-dir", default="artifacts/mythos/checkpoint-compare")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--max-new-tokens", type=int, default=96)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1337)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = compare_checkpoints(
        baseline_manifest=args.baseline_manifest,
        candidate_manifest=args.candidate_manifest,
        tokenizer_path=args.tokenizer,
        prompt_suite=args.prompt_suite,
        output_dir=args.output_dir,
        device=args.device,
        generation_config=GenerationConfig(
            max_new_tokens=args.max_new_tokens,
            temperature=args.temperature,
            top_k=args.top_k,
            seed=args.seed,
        ),
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    raise SystemExit(0 if report.status != "regressed" else 1)


if __name__ == "__main__":
    main()
