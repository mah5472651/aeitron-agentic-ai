#!/usr/bin/env python
"""Phase 47 reasoning engine.

Separates thinker, critic, and verifier roles so reasoning quality is measured
before an answer is accepted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase43.meta_planner import create_meta_plan


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReasoningStage(StrictModel):
    name: str
    output: str
    score: float = Field(ge=0.0, le=1.0)
    findings: list[str] = Field(default_factory=list)


class ReasoningReport(StrictModel):
    run_id: str
    prompt: str
    stages: list[ReasoningStage]
    final_answer: str
    accepted: bool
    confidence: float = Field(ge=0.0, le=1.0)
    created_at_unix: float = Field(default_factory=time.time)


class ReasoningEngine:
    def run(self, prompt: str, *, run_id: str | None = None) -> ReasoningReport:
        actual_run_id = run_id or f"phase47-{time.time_ns()}"
        thinker = self.think(prompt, actual_run_id)
        critic = self.critic(prompt, thinker.output)
        verifier = self.verify(prompt, thinker.output, critic)
        accepted = critic.score >= 0.62 and verifier.score >= 0.68
        confidence = min(thinker.score, critic.score, verifier.score)
        final = self.final_answer(prompt, thinker, critic, verifier, accepted)
        return ReasoningReport(
            run_id=actual_run_id,
            prompt=prompt,
            stages=[thinker, critic, verifier],
            final_answer=final,
            accepted=accepted,
            confidence=confidence,
        )

    def think(self, prompt: str, run_id: str) -> ReasoningStage:
        plan = create_meta_plan(prompt, run_id=f"{run_id}-think")
        output = "\n".join(
            [
                plan.goal,
                "Requirements:",
                *[f"- {requirement}" for requirement in plan.requirements[:10]],
                "Execution:",
                *[f"- {lane.lane}: {', '.join(lane.tasks)}" for lane in plan.execution_lanes],
            ]
        )
        return ReasoningStage(name="thinker", output=output, score=plan.confidence, findings=plan.risks[:6])

    def critic(self, prompt: str, thought: str) -> ReasoningStage:
        lower = thought.lower()
        findings: list[str] = []
        for marker in ["test", "security", "verification", "requirements"]:
            if marker not in lower:
                findings.append(f"missing {marker} coverage")
        score = max(0.0, 0.9 - len(findings) * 0.12)
        return ReasoningStage(name="critic", output="Critic checked planning completeness and safety coverage.", score=score, findings=findings)

    def verify(self, prompt: str, thought: str, critic: ReasoningStage) -> ReasoningStage:
        findings = list(critic.findings)
        if len(prompt.strip()) < 4:
            findings.append("prompt too small to verify safely")
        if "delete" in prompt.lower() and not any(marker in thought.lower() for marker in ["backup", "rollback", "preview"]):
            findings.append("destructive intent needs backup/rollback mention")
        score = max(0.0, 0.92 - len(findings) * 0.1)
        return ReasoningStage(name="verifier", output="Verifier checked acceptance, safety, and prompt adequacy.", score=score, findings=findings)

    def final_answer(self, prompt: str, thinker: ReasoningStage, critic: ReasoningStage, verifier: ReasoningStage, accepted: bool) -> str:
        status = "accepted" if accepted else "needs_more_review"
        return "\n".join(
            [
                f"Reasoning status: {status}",
                thinker.output,
                f"Critic findings: {critic.findings or ['none']}",
                f"Verifier findings: {verifier.findings or ['none']}",
            ]
        )


def write_report(report: ReasoningReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "reasoning-engine-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 47 thinker/critic/verifier reasoning.")
    parser.add_argument("--prompt", default="build secure login system")
    parser.add_argument("--run-id", default=f"phase47-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase47")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = ReasoningEngine().run(args.prompt, run_id=args.run_id)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "accepted": report.accepted, "confidence": report.confidence, "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
