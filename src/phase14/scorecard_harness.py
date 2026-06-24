#!/usr/bin/env python
"""Exact AI architecture scorecard harness.

This runner builds the scorecard the project needs before training:

- architecture reliability score
- agent workflow completion score
- security detection/fix score
- short prompt understanding score
- sandbox/test pass rate
- regression count

It uses the exact golden task shape requested by the user:
20 short prompt coding tasks, 20 debugging tasks, 20 security finding tasks,
20 patch generation tasks, and 10 long-context repo tasks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True)
class ScorecardTask:
    task_id: str
    category: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    expected_paths: list[str] = field(default_factory=list)
    expected_cwes: list[str] = field(default_factory=list)
    before: str = ""
    after: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    category: str
    mode: str
    status: str
    score: float
    failed_phase: str | None
    issue_type: str | None
    recommendation: str
    message: str
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScorecardMetrics:
    architecture_reliability_score: float
    agent_workflow_completion_score: float
    security_detection_fix_score: float
    short_prompt_understanding_score: float
    sandbox_test_pass_rate: float
    regression_count: int
    overall_score: float


@dataclass(frozen=True)
class ScorecardRun:
    run_id: str
    mode: str
    backend_kind: str
    started_at_unix: float
    duration_ms: float
    ready: bool
    metrics: ScorecardMetrics
    summary: dict[str, int]
    outcomes: list[TaskOutcome]
    failure_report: list[TaskOutcome]
    recommendations: list[str]


@dataclass(frozen=True)
class CombinedScorecardReport:
    run_id: str
    started_at_unix: float
    duration_ms: float
    task_dataset: dict[str, int]
    mock: ScorecardRun | None
    real: ScorecardRun | None
    comparison: dict[str, Any]
    artifacts: dict[str, str]


def status_for_score(score: float) -> str:
    if score >= 0.85:
        return OK
    if score >= 0.60:
        return WARN
    return FAIL


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def load_exact_golden_tasks() -> list[ScorecardTask]:
    from src.phase12.capability_gauntlet import generate_golden_tasks

    mapping = {
        "short_prompt_understanding": "short_prompt_coding",
        "agent_workflow": "debugging",
        "security_reasoning": "security_finding",
        "patch_review": "patch_generation",
        "long_context_memory": "long_context_repo",
    }
    limits = {
        "short_prompt_coding": 20,
        "debugging": 20,
        "security_finding": 20,
        "patch_generation": 20,
        "long_context_repo": 10,
    }
    counts = {category: 0 for category in limits}
    tasks: list[ScorecardTask] = []
    for task in generate_golden_tasks():
        mapped = mapping.get(task.category)
        if mapped is None or counts[mapped] >= limits[mapped]:
            continue
        counts[mapped] += 1
        tasks.append(
            ScorecardTask(
                task_id=f"{mapped}-{counts[mapped]:02d}",
                category=mapped,
                prompt=task.prompt,
                files=task.files,
                expected_paths=task.expected_paths,
                expected_cwes=task.expected_cwes,
                before=task.before,
                after=task.after,
                metadata=task.metadata,
            )
        )
    missing = {category: limit - counts[category] for category, limit in limits.items() if counts[category] != limit}
    if missing:
        raise RuntimeError(f"golden task dataset is incomplete: {missing}")
    return tasks


def dataset_summary(tasks: list[ScorecardTask]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for task in tasks:
        summary[task.category] = summary.get(task.category, 0) + 1
    summary["total"] = len(tasks)
    return summary


def export_tasks(tasks: list[ScorecardTask], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(asdict(task), ensure_ascii=False) for task in tasks) + "\n", encoding="utf-8")


def recommendation_for(category: str, failed_phase: str | None, issue_type: str | None, mode: str) -> str:
    if failed_phase == "sandbox_engine":
        return "Check Docker daemon, Phase 2 sandbox image availability, and timeout/resource policy."
    if mode == "real" and issue_type == "model_output":
        return "Backend response lacks required task signals; add targeted SFT/GRPO data or use a stronger real model."
    if category == "short_prompt_coding":
        return "Improve short-prompt intent expansion, context retrieval, and model prompt instructions."
    if category == "debugging":
        return "Inspect planner step coverage and debugger/self-healing routing for missing actions."
    if category == "security_finding":
        return "Expand security rules or train the model on missed CWE examples."
    if category == "patch_generation":
        return "Strengthen patch security comparison and train on before/after secure patch examples."
    if category == "long_context_repo":
        return "Tune memory retrieval, call graph enrichment, and context packing for target-file recall."
    return "Review task details and add a targeted regression test."


def classify_failure(category: str, *, mode: str, score: float, details: dict[str, Any]) -> tuple[str | None, str | None]:
    if score >= 0.85:
        return None, None
    if mode == "real":
        return "model_backend", "model_output"
    if category == "short_prompt_coding":
        if not details.get("context_sources"):
            return "memory_engine", "memory_issue"
        return "intent_expander" if mode == "mock" else "model_backend"
    if category == "debugging":
        return "agentic_runtime", "planner_issue" if mode == "mock" else "model_output"
    if category == "security_finding":
        return "security_engine" if mode == "mock" else "model_backend", "security_detection_issue" if mode == "mock" else "model_output"
    if category == "patch_generation":
        return "patch_reviewer" if mode == "mock" else "model_backend", "patch_fix_issue" if mode == "mock" else "model_output"
    if category == "long_context_repo":
        return "memory_engine", "memory_issue"
    return "unknown", "unknown"


def outcome(
    *,
    task: ScorecardTask,
    mode: str,
    score: float,
    message: str,
    started: float,
    details: dict[str, Any],
) -> TaskOutcome:
    normalized = max(0.0, min(1.0, score))
    failed_phase, issue_type = classify_failure(task.category, mode=mode, score=normalized, details=details)
    return TaskOutcome(
        task_id=task.task_id,
        category=task.category,
        mode=mode,
        status=status_for_score(normalized),
        score=round(normalized, 4),
        failed_phase=failed_phase,
        issue_type=issue_type,
        recommendation=recommendation_for(task.category, failed_phase, issue_type, mode),
        message=message,
        duration_ms=(time.perf_counter() - started) * 1000,
        details=details,
    )


class ScorecardRunner:
    def __init__(self, *, mode: str, backend_kind: str, run_sandbox: bool, context_budget: int, concurrency: int) -> None:
        self.mode = mode
        self.backend_kind = backend_kind
        self.run_sandbox = run_sandbox
        self.context_budget = context_budget
        self.concurrency = max(1, concurrency)
        self.backend = self._build_backend() if mode == "real" else None

    def _build_backend(self):
        import os

        from src.phase11.model_backends import build_backend

        if self.backend_kind == "openai_compatible":
            return build_backend(
                "openai_compatible",
                endpoint=os.environ.get("SCORECARD_MODEL_ENDPOINT", os.environ.get("PHASE13_MODEL_ENDPOINT", "http://127.0.0.1:8000/v1")),
                model_name=os.environ.get("SCORECARD_MODEL_NAME", os.environ.get("PHASE13_MODEL_NAME", "security-coder")),
                api_key=os.environ.get("SCORECARD_API_KEY", os.environ.get("PHASE13_API_KEY")),
            )
        if self.backend_kind == "pytorch":
            return build_backend(
                "pytorch",
                checkpoint=os.environ.get("SCORECARD_CHECKPOINT"),
                tokenizer_path=os.environ.get("SCORECARD_TOKENIZER"),
                device=os.environ.get("SCORECARD_DEVICE", "cpu"),
            )
        if self.backend_kind == "mock":
            return build_backend("mock")
        raise ValueError(f"unsupported backend kind: {self.backend_kind}")

    async def aclose(self) -> None:
        if self.backend is not None:
            await self.backend.aclose()

    async def run(self, run_id: str, tasks: list[ScorecardTask], previous: ScorecardRun | None) -> ScorecardRun:
        started = time.time()
        semaphore = asyncio.Semaphore(self.concurrency)

        async def guarded(task: ScorecardTask) -> TaskOutcome:
            async with semaphore:
                return await self.run_task(task)

        try:
            outcomes = await asyncio.gather(*(guarded(task) for task in tasks))
        finally:
            await self.aclose()
        sandbox_rate = await self.sandbox_pass_rate()
        regression_count = count_regressions(outcomes, previous)
        metrics = compute_metrics(outcomes, sandbox_rate=sandbox_rate, regression_count=regression_count)
        summary = {OK: 0, WARN: 0, FAIL: 0}
        for item in outcomes:
            summary[item.status] = summary.get(item.status, 0) + 1
        failures = [item for item in outcomes if item.status == FAIL]
        recommendations = build_run_recommendations(metrics, failures, self.mode)
        return ScorecardRun(
            run_id=run_id,
            mode=self.mode,
            backend_kind=self.backend_kind,
            started_at_unix=started,
            duration_ms=(time.time() - started) * 1000,
            ready=not failures and metrics.overall_score >= 85.0,
            metrics=metrics,
            summary=summary,
            outcomes=outcomes,
            failure_report=failures,
            recommendations=recommendations,
        )

    async def run_task(self, task: ScorecardTask) -> TaskOutcome:
        if self.mode == "real":
            return await self.run_real_task(task)
        if task.category == "short_prompt_coding":
            return await self.run_short_prompt(task)
        if task.category == "debugging":
            return await self.run_agent_workflow(task)
        if task.category == "security_finding":
            return await self.run_security_finding(task)
        if task.category == "patch_generation":
            return await self.run_patch_generation(task)
        if task.category == "long_context_repo":
            return await self.run_long_context(task)
        started = time.perf_counter()
        return outcome(task=task, mode=self.mode, score=0.0, message="unknown category", started=started, details={})

    async def run_short_prompt(self, task: ScorecardTask) -> TaskOutcome:
        from src.phase11.agentic_runtime import AgenticCodingRuntime
        from src.phase11.memory_engine import WorkspaceMemoryEngine
        from src.phase11.model_backends import MockReasoningBackend
        from src.phase11.schemas import AgentRunRequest

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="scorecard_short_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            memory = WorkspaceMemoryEngine(root)
            expanded = memory.expand_intent(task.prompt)
            context = memory.retrieve(task.prompt, token_budget=self.context_budget, max_items=8)
            runtime = AgenticCodingRuntime(MockReasoningBackend())
            report = await runtime.run(
                AgentRunRequest(
                    prompt=task.prompt,
                    workspace=str(root),
                    allow_writes=False,
                    allow_sandbox=False,
                    context_token_budget=self.context_budget,
                )
            )
            roles = {step.role for step in report.steps}
            expected_hit = any(path in {item.source for item in context.items} for path in task.expected_paths)
            score = 0.0
            score += 0.25 if len(expanded) > len(task.prompt) + 40 else 0.0
            score += 0.25 if context.items else 0.0
            score += 0.25 if {"planner", "security_reviewer", "code_architect"}.issubset(roles) else 0.0
            score += 0.15 if report.final_answer.strip() else 0.0
            score += 0.10 if expected_hit or not task.expected_paths else 0.0
            details = {"context_sources": [item.source for item in context.items[:8]], "roles": sorted(roles), "expanded": expanded[:500]}
            return outcome(task=task, mode=self.mode, score=score, message=f"short prompt architecture score={score:.2f}", started=started, details=details)

    async def run_agent_workflow(self, task: ScorecardTask) -> TaskOutcome:
        from src.phase11.agentic_runtime import AgenticCodingRuntime
        from src.phase11.model_backends import MockReasoningBackend
        from src.phase11.schemas import AgentRunRequest

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="scorecard_debug_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            runtime = AgenticCodingRuntime(MockReasoningBackend())
            report = await runtime.run(
                AgentRunRequest(
                    prompt=task.prompt,
                    workspace=str(root),
                    allow_writes=False,
                    allow_sandbox=False,
                    context_token_budget=self.context_budget,
                )
            )
            actions = {step.action for step in report.steps}
            required = {
                "create_prioritized_task_graph",
                "static_workspace_review",
                "generate_solution",
                "sandbox_or_test_verification",
                "adversarial_peer_review",
            }
            score = len(actions & required) / len(required)
            score = min(1.0, score + (0.1 if report.confidence > 0 else 0.0))
            details = {"actions": sorted(actions), "confidence": report.confidence, "context_sources": [item.source for item in report.context.items[:8]]}
            return outcome(task=task, mode=self.mode, score=score, message=f"agent actions covered {len(actions & required)}/{len(required)}", started=started, details=details)

    async def run_security_finding(self, task: ScorecardTask) -> TaskOutcome:
        from src.phase11.security_engine import SecurityReasoningEngine

        started = time.perf_counter()
        review = SecurityReasoningEngine().analyze_text("\n".join(task.files.values()), target=task.task_id)
        cwes = sorted({finding.cwe for finding in review.findings if finding.cwe})
        expected = set(task.expected_cwes)
        hit = expected.issubset(set(cwes))
        score = 1.0 if hit else 0.65 if review.findings else 0.0
        details = {"detected_cwes": cwes, "expected_cwes": sorted(expected), "findings": [finding.model_dump() for finding in review.findings]}
        return outcome(task=task, mode=self.mode, score=score, message=f"expected={sorted(expected)} detected={cwes}", started=started, details=details)

    async def run_patch_generation(self, task: ScorecardTask) -> TaskOutcome:
        from src.phase11.security_engine import SecurityReasoningEngine

        started = time.perf_counter()
        engine = SecurityReasoningEngine()
        before = engine.analyze_text(task.before, target=f"{task.task_id}:before")
        after = engine.analyze_text(task.after, target=f"{task.task_id}:after")
        comparison = engine.compare_patch_security(task.before, task.after, target=task.task_id)
        score = 0.0
        score += 0.45 if len(before.findings) > len(after.findings) else 0.0
        score += 0.35 if comparison.score >= after.score else 0.0
        score += 0.20 if after.score >= before.score else 0.0
        details = {"before_findings": len(before.findings), "after_findings": len(after.findings), "comparison": comparison.model_dump()}
        return outcome(task=task, mode=self.mode, score=score, message=comparison.summary, started=started, details=details)

    async def run_long_context(self, task: ScorecardTask) -> TaskOutcome:
        from src.phase11.memory_engine import WorkspaceMemoryEngine
        from src.phase11.persistent_memory import PersistentMemoryGateway

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="scorecard_context_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            memory = WorkspaceMemoryEngine(root)
            context = memory.retrieve(task.prompt, token_budget=self.context_budget, max_items=10)
            sources = [item.source for item in context.items]
            gateway = PersistentMemoryGateway(workspace=str(root))
            records = memory.export_memory_records(gateway=gateway, max_records=40)
            await gateway.upsert(records)
            local_hits = gateway.search_local(task.prompt, limit=3)
            context_hit = any(path in sources for path in task.expected_paths)
            vector_hit = any(hit["record"]["source"] in task.expected_paths for hit in local_hits)
            score = 0.0
            score += 0.50 if context_hit else 0.0
            score += 0.25 if records else 0.0
            score += 0.25 if vector_hit else 0.10 if local_hits else 0.0
            details = {"context_sources": sources, "expected_paths": task.expected_paths, "vector_hits": local_hits}
            return outcome(task=task, mode=self.mode, score=score, message=f"context_hit={context_hit}; vector_hit={vector_hit}", started=started, details=details)

    async def run_real_task(self, task: ScorecardTask) -> TaskOutcome:
        from src.phase11.schemas import ChatMessage, ChatRole, GenerationConfig, GenerationRequest

        started = time.perf_counter()
        if self.backend is None:
            return outcome(task=task, mode=self.mode, score=0.0, message="backend not initialized", started=started, details={})
        context = self.render_task_context(task)
        try:
            max_new_tokens = max(64, min(700, int(os.environ.get("SCORECARD_MAX_NEW_TOKENS", "700"))))
            response = await self.backend.generate(
                GenerationRequest(
                    messages=[
                        ChatMessage(
                            role=ChatRole.SYSTEM,
                            content="You are a coding and cybersecurity LLM. Be concrete, security-aware, and include verification.",
                        ),
                        ChatMessage(role=ChatRole.USER, content=context),
                    ],
                    config=GenerationConfig(max_new_tokens=max_new_tokens, temperature=0.2),
                )
            )
        except Exception as exc:
            return outcome(task=task, mode=self.mode, score=0.0, message=f"{type(exc).__name__}: {exc}", started=started, details={})
        text = response.text
        score, details = score_model_output(task, text)
        details["model"] = response.model
        details["latency_ms"] = response.latency_ms
        return outcome(task=task, mode=self.mode, score=score, message=f"model output score={score:.2f}", started=started, details=details)

    def render_task_context(self, task: ScorecardTask) -> str:
        parts = [f"Task category: {task.category}", f"Prompt: {task.prompt}"]
        if task.files:
            snippets = []
            for path, content in list(task.files.items())[:8]:
                snippets.append(f"### {path}\n{content[:1200]}")
            parts.append("Repository context:\n" + "\n\n".join(snippets))
        if task.before or task.after:
            parts.append(f"Before patch:\n{task.before}\n\nExpected secure direction:\n{task.after}")
        if task.expected_paths:
            parts.append(f"Relevant target paths to consider: {', '.join(task.expected_paths)}")
        if task.expected_cwes:
            parts.append(f"Expected CWE class to reason about: {', '.join(task.expected_cwes)}")
        return "\n\n".join(parts)

    async def sandbox_pass_rate(self) -> float:
        if not self.run_sandbox:
            return 0.0
        try:
            from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile

            request = ExecutionRequest(
                files=[SandboxFile(path="main.py", content="print('scorecard-sandbox-ok')\n")],
                compile_command=None,
                run_command="python3 /workspace/main.py",
                image="python:3.12-slim",
            )
            async with SandboxEngine(pool_size=1) as engine:
                result = await engine.run(request)
            return 100.0 if result.ok and "scorecard-sandbox-ok" in result.stdout else 0.0
        except Exception:
            return 0.0


def score_model_output(task: ScorecardTask, text: str) -> tuple[float, dict[str, Any]]:
    lower = text.lower()
    keyword_map = {
        "short_prompt_coding": ["plan", "inspect", "patch", "test", "security"],
        "debugging": ["debug", "reproduce", "traceback", "fix", "test"],
        "security_finding": ["cwe", "vulnerability", "risk", "patch", "test"],
        "patch_generation": ["patch", "before", "after", "secure", "test"],
        "long_context_repo": ["context", "file", "symbol", "retrieve", "token"],
    }
    expected = list(keyword_map.get(task.category, []))
    expected.extend(cwe.lower() for cwe in task.expected_cwes)
    expected.extend(path.lower() for path in task.expected_paths[:2])
    if task.category == "patch_generation":
        for token in ["parameter", "shell=false", "sha256", "json"]:
            if token.lower() in task.after.lower():
                expected.append(token)
    hits = [item for item in expected if item and item in lower]
    forbidden = [item for item in ["mock-vllm", "cannot help", "as an ai language model"] if item in lower]
    keyword_score = len(set(hits)) / max(1, len(set(expected)))
    length_score = 1.0 if len(text.strip()) >= 160 else len(text.strip()) / 160.0
    forbidden_score = 0.0 if forbidden else 1.0
    structure_score = 1.0 if any(marker in lower for marker in ["plan", "patch", "verify", "test", "risk"]) else 0.0
    score = keyword_score * 0.50 + length_score * 0.20 + forbidden_score * 0.15 + structure_score * 0.15
    return max(0.0, min(1.0, score)), {
        "expected_signals": sorted(set(expected)),
        "signal_hits": sorted(set(hits)),
        "forbidden_hits": forbidden,
        "text_preview": text[:1000],
    }


def compute_metrics(outcomes: list[TaskOutcome], *, sandbox_rate: float, regression_count: int) -> ScorecardMetrics:
    def category_average(category: str) -> float:
        values = [item.score for item in outcomes if item.category == category]
        return round(statistics.mean(values) * 100, 2) if values else 0.0

    short_score = category_average("short_prompt_coding")
    agent_score = category_average("debugging")
    security_values = [
        item.score
        for item in outcomes
        if item.category in {"security_finding", "patch_generation"}
    ]
    security_score = round(statistics.mean(security_values) * 100, 2) if security_values else 0.0
    architecture_reliability_values = [short_score, agent_score, security_score, category_average("long_context_repo"), sandbox_rate]
    architecture_score = round(statistics.mean(architecture_reliability_values), 2)
    overall = round(statistics.mean([item.score for item in outcomes]) * 100, 2) if outcomes else 0.0
    return ScorecardMetrics(
        architecture_reliability_score=architecture_score,
        agent_workflow_completion_score=agent_score,
        security_detection_fix_score=security_score,
        short_prompt_understanding_score=short_score,
        sandbox_test_pass_rate=round(sandbox_rate, 2),
        regression_count=regression_count,
        overall_score=overall,
    )


def count_regressions(current: list[TaskOutcome], previous: ScorecardRun | None, threshold: float = 0.03) -> int:
    if previous is None:
        return 0
    previous_by_task = {item.task_id: item for item in previous.outcomes}
    count = 0
    for item in current:
        old = previous_by_task.get(item.task_id)
        if old is None:
            continue
        if item.score + threshold < old.score:
            count += 1
        elif old.status == OK and item.status in {WARN, FAIL}:
            count += 1
    return count


def build_run_recommendations(metrics: ScorecardMetrics, failures: list[TaskOutcome], mode: str) -> list[str]:
    recs: list[str] = []
    if metrics.sandbox_test_pass_rate < 100:
        recs.append("Sandbox/test pass rate is below 100; verify Docker and Phase 2 sandbox hardening.")
    if metrics.short_prompt_understanding_score < 90:
        recs.append("Short prompt score is weak; improve intent expansion and examples for vague coding prompts.")
    if metrics.agent_workflow_completion_score < 90:
        recs.append("Agent workflow score is weak; inspect planner/debugger/reviewer step coverage.")
    if metrics.security_detection_fix_score < 90:
        recs.append("Security detection/fix score is weak; add CWE examples and strengthen patch reviews.")
    if metrics.regression_count:
        recs.append(f"{metrics.regression_count} regression(s) detected versus the previous scorecard run.")
    if failures:
        by_phase = sorted({failure.failed_phase or "unknown" for failure in failures})
        recs.append(f"Failing phases: {', '.join(by_phase)}.")
    if mode == "real" and metrics.overall_score < 80:
        recs.append("Real backend quality is below target; use failed tasks as SFT/GRPO training data candidates.")
    if not recs:
        recs.append("Scorecard is green; expand task count and run against a stronger real backend next.")
    return recs


def latest_previous_run(output_dir: Path, mode: str, current_run_id: str) -> ScorecardRun | None:
    if not output_dir.exists():
        return None
    candidates = sorted(output_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.stem == current_run_id:
            continue
        payload: dict[str, Any] | None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
        if payload is None:
            continue
        section = payload.get(mode)
        if not isinstance(section, dict):
            continue
        previous_run: ScorecardRun | None
        try:
            previous_run = scorecard_run_from_dict(section)
        except (KeyError, TypeError, ValueError):
            previous_run = None
        if previous_run is not None:
            return previous_run
    return None


def scorecard_run_from_dict(payload: dict[str, Any]) -> ScorecardRun:
    metrics = ScorecardMetrics(**payload["metrics"])
    outcomes = [TaskOutcome(**item) for item in payload.get("outcomes", [])]
    failures = [TaskOutcome(**item) for item in payload.get("failure_report", [])]
    return ScorecardRun(
        run_id=payload["run_id"],
        mode=payload["mode"],
        backend_kind=payload["backend_kind"],
        started_at_unix=payload["started_at_unix"],
        duration_ms=payload["duration_ms"],
        ready=payload["ready"],
        metrics=metrics,
        summary=payload.get("summary", {}),
        outcomes=outcomes,
        failure_report=failures,
        recommendations=payload.get("recommendations", []),
    )


async def run_scorecard(args: argparse.Namespace) -> CombinedScorecardReport:
    started = time.time()
    tasks = load_exact_golden_tasks()
    export_tasks(tasks, args.export_tasks)
    mock_run: ScorecardRun | None = None
    real_run: ScorecardRun | None = None
    if args.mode in {"mock", "both"}:
        previous = latest_previous_run(args.output_dir, "mock", args.run_id)
        runner = ScorecardRunner(mode="mock", backend_kind="mock", run_sandbox=args.run_sandbox, context_budget=args.context_budget, concurrency=args.concurrency)
        mock_run = await runner.run(args.run_id, tasks, previous)
    if args.mode in {"real", "both"}:
        previous = latest_previous_run(args.output_dir, "real", args.run_id)
        runner = ScorecardRunner(mode="real", backend_kind=args.real_backend, run_sandbox=args.run_sandbox, context_budget=args.context_budget, concurrency=args.concurrency)
        real_run = await runner.run(args.run_id, tasks, previous)
    comparison: dict[str, Any] = {}
    if mock_run and real_run:
        comparison = {
            "overall_delta_real_minus_mock": round(real_run.metrics.overall_score - mock_run.metrics.overall_score, 2),
            "architecture_delta_real_minus_mock": round(real_run.metrics.architecture_reliability_score - mock_run.metrics.architecture_reliability_score, 2),
            "real_ready": real_run.ready,
            "mock_ready": mock_run.ready,
        }
    return CombinedScorecardReport(
        run_id=args.run_id,
        started_at_unix=started,
        duration_ms=(time.time() - started) * 1000,
        task_dataset=dataset_summary(tasks),
        mock=mock_run,
        real=real_run,
        comparison=comparison,
        artifacts={"tasks": str(args.export_tasks)},
    )


def write_reports(report: CombinedScorecardReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# AI Architecture Scorecard",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Duration: `{report.duration_ms:.1f} ms`",
        f"- Dataset: `{report.task_dataset}`",
        "",
    ]
    for label, run in [("Mock Architecture Mode", report.mock), ("Real Backend Mode", report.real)]:
        if run is None:
            continue
        lines.extend(
            [
                f"## {label}",
                "",
                f"- Ready: `{run.ready}`",
                f"- Backend: `{run.backend_kind}`",
                f"- Summary: `{run.summary}`",
                f"- Architecture reliability score: `{run.metrics.architecture_reliability_score:.2f}`",
                f"- Agent workflow completion score: `{run.metrics.agent_workflow_completion_score:.2f}`",
                f"- Security detection/fix score: `{run.metrics.security_detection_fix_score:.2f}`",
                f"- Short prompt understanding score: `{run.metrics.short_prompt_understanding_score:.2f}`",
                f"- Sandbox/test pass rate: `{run.metrics.sandbox_test_pass_rate:.2f}`",
                f"- Regression count: `{run.metrics.regression_count}`",
                f"- Overall score: `{run.metrics.overall_score:.2f}`",
                "",
                "### Fail Auto Report",
                "",
            ]
        )
        if run.failure_report:
            lines.extend(["| Task | Category | Failed Phase | Issue Type | Score | Recommendation |", "| --- | --- | --- | --- | ---: | --- |"])
            for failure in run.failure_report[:40]:
                lines.append(
                    f"| {failure.task_id} | {failure.category} | {failure.failed_phase or ''} | "
                    f"{failure.issue_type or ''} | {failure.score * 100:.1f} | {failure.recommendation.replace('|', '/')} |"
                )
        else:
            lines.append("- No failed tasks.")
        lines.extend(["", "### Recommendations", ""])
        lines.extend(f"- {rec}" for rec in run.recommendations)
        lines.append("")
    if report.comparison:
        lines.extend(["## Mock Vs Real", "", f"`{report.comparison}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run exact AI architecture scorecard.")
    parser.add_argument("--run-id", default=f"scorecard-{int(time.time())}")
    parser.add_argument("--mode", choices=["mock", "real", "both"], default="both")
    parser.add_argument("--real-backend", choices=["openai_compatible", "pytorch", "mock"], default="openai_compatible")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/scorecard"))
    parser.add_argument("--export-tasks", type=Path, default=Path("data/scorecard/golden_tasks.jsonl"))
    parser.add_argument("--context-budget", type=int, default=2500)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--require-real-ready", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await run_scorecard(args)
    json_path, md_path = write_reports(report, args.output_dir)
    mock_ready = report.mock.ready if report.mock else None
    real_ready = report.real.ready if report.real else None
    payload = {
        "run_id": report.run_id,
        "task_dataset": report.task_dataset,
        "mock_ready": mock_ready,
        "real_ready": real_ready,
        "comparison": report.comparison,
        "json": str(json_path),
        "markdown": str(md_path),
    }
    if report.mock:
        payload["mock_metrics"] = asdict(report.mock.metrics)
    if report.real:
        payload["real_metrics"] = asdict(report.real.metrics)
    print(json.dumps(payload, indent=2))
    failed = False
    if args.strict and report.mock and not report.mock.ready:
        failed = True
    if args.strict and args.require_real_ready and report.real and not report.real.ready:
        failed = True
    return 1 if failed else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
