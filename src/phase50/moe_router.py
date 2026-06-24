#!/usr/bin/env python
"""Phase 50 mixture-of-experts router.

Routes tasks to coding, security, planning, reasoning, memory, multimodal, or
research experts before execution. This is a routing layer, not a model trainer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ExpertRoute(StrictModel):
    expert: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class ExpertProfile(StrictModel):
    expert: str
    keywords: list[str]
    execution_hint: str
    priority: int = Field(ge=0, le=100)


class MoERouterReport(StrictModel):
    run_id: str
    prompt: str
    routes: list[ExpertRoute]
    primary_expert: str
    execution_hint: str
    created_at_unix: float = Field(default_factory=time.time)


class MoERouter:
    EXPERTS: list[ExpertProfile] = [
        ExpertProfile(expert="security_expert", keywords=["security", "secure", "vulnerability", "cwe", "auth", "login", "xss", "sql", "crypto", "solidity"], execution_hint="Run Phase 28, Phase 38, then Phase 27 security profile.", priority=96),
        ExpertProfile(expert="planning_expert", keywords=["build", "system", "architecture", "plan", "requirements", "product"], execution_hint="Run Phase 44 then Phase 43.", priority=90),
        ExpertProfile(expert="coding_expert", keywords=["code", "implement", "api", "frontend", "backend", "test", "bug", "debug", "login"], execution_hint="Run Phase 40 or Phase 45 with coder/tester lanes.", priority=86),
        ExpertProfile(expert="reasoning_expert", keywords=["reason", "decide", "compare", "tradeoff", "why", "root", "cause"], execution_hint="Run Phase 47 thinker/critic/verifier.", priority=82),
        ExpertProfile(expert="memory_expert", keywords=["remember", "history", "past", "similar", "experience", "knowledge"], execution_hint="Run Phase 46 and Phase 37 retrieval.", priority=76),
        ExpertProfile(expert="multimodal_expert", keywords=["image", "screenshot", "pdf", "diagram", "photo", "vision"], execution_hint="Run Phase 49 before planning.", priority=74),
        ExpertProfile(expert="research_expert", keywords=["latest", "docs", "library", "paper", "current", "api"], execution_hint="Use a research lane with primary sources before coding.", priority=72),
    ]

    def route(self, prompt: str, *, run_id: str | None = None, top_k: int = 3) -> MoERouterReport:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must not be empty")
        terms = set(re.findall(r"[a-z0-9][a-z0-9_-]*", prompt.lower()))
        lower = prompt.lower()
        routes: list[ExpertRoute] = []
        hints: dict[str, str] = {}
        priority: dict[str, int] = {}
        for profile in self.EXPERTS:
            hits = [keyword for keyword in profile.keywords if keyword in terms or keyword in lower]
            score = min(1.0, 0.16 + len(hits) * 0.19 + profile.priority / 1000)
            if hits:
                routes.append(ExpertRoute(expert=profile.expert, score=round(score, 3), reason=f"matched: {', '.join(hits)}"))
            hints[profile.expert] = profile.execution_hint
            priority[profile.expert] = profile.priority
        if not routes:
            routes.append(ExpertRoute(expert="planning_expert", score=0.42, reason="default route for ambiguous prompt"))
        routes.sort(key=lambda route: (route.score, priority.get(route.expert, 0)), reverse=True)
        selected = routes[: max(1, min(top_k, len(routes)))]
        primary = selected[0].expert
        return MoERouterReport(
            run_id=run_id or f"phase50-{time.time_ns()}",
            prompt=prompt,
            routes=selected,
            primary_expert=primary,
            execution_hint=hints.get(primary, "Run Phase 43 planning first."),
        )


def write_report(report: MoERouterReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "moe-router-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 50 MoE router.")
    parser.add_argument("--prompt", default="build secure login system")
    parser.add_argument("--run-id", default=f"phase50-{int(time.time())}")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase50")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = MoERouter().route(args.prompt, run_id=args.run_id, top_k=args.top_k)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "primary_expert": report.primary_expert, "routes": [route.model_dump() for route in report.routes], "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
