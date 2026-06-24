#!/usr/bin/env python
"""Phase 12 architecture capability gauntlet.

This is not a benchmark for a fully trained frontier model. It is a hard local
proof harness for the architecture: short-prompt expansion, agent workflow,
security reasoning, long-context retrieval, self-healing routing, and tool
safety. When a real model backend replaces the mock backend, the same harness
also becomes a model-quality regression suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
SKIP = "skip"


@dataclass(frozen=True)
class GoldenTask:
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
class CapabilityResult:
    task_id: str
    category: str
    status: str
    score: float
    message: str
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityReport:
    run_id: str
    suite: str
    backend: str
    started_at_unix: float
    duration_ms: float
    overall_score: float
    architecture_ready: bool
    category_scores: dict[str, float]
    summary: dict[str, int]
    results: list[CapabilityResult]
    recommendations: list[str]


def normalize_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def status_for_score(score: float, *, skipped: bool = False) -> str:
    if skipped:
        return SKIP
    if score >= 0.85:
        return OK
    if score >= 0.60:
        return WARN
    return FAIL


def base_workspace_files() -> dict[str, str]:
    return {
        "README.md": "# Capability Fixture\n\nSmall repo used by the Phase 12 architecture gauntlet.\n",
        "app/main.py": (
            "from app.auth import hash_password\n"
            "from app.db import find_user\n\n"
            "def login(username, password):\n"
            "    user = find_user(username)\n"
            "    return user and hash_password(password) == user['password_hash']\n"
        ),
        "app/auth.py": (
            "import hashlib\n\n"
            "def hash_password(password):\n"
            "    return hashlib.md5(password.encode()).hexdigest()\n"
        ),
        "app/db.py": (
            "def find_user(username):\n"
            "    query = \"SELECT * FROM users WHERE name = '\" + username + \"'\"\n"
            "    return {'query': query, 'password_hash': 'demo'}\n"
        ),
        "tests/test_login.py": (
            "from app.main import login\n\n"
            "def test_login_false():\n"
            "    assert login('alice', 'wrong') is False\n"
        ),
    }


def generate_golden_tasks() -> list[GoldenTask]:
    tasks: list[GoldenTask] = []

    short_prompts = [
        "fix login security",
        "make this repo safer",
        "debug the auth bug",
        "add tests for login",
        "review weak crypto",
        "find vuln",
        "patch db query",
        "improve password hashing",
        "why tests fail",
        "make api robust",
        "secure file access",
        "cleanup code",
        "add error handling",
        "make build pass",
        "inspect project",
        "fix crash",
        "make auth production safe",
        "verify sandbox result",
        "review agent runtime",
        "ship safe patch",
    ]
    for index, prompt in enumerate(short_prompts, start=1):
        tasks.append(
            GoldenTask(
                task_id=f"short-{index:02d}",
                category="short_prompt_understanding",
                prompt=prompt,
                files=base_workspace_files(),
                expected_paths=["app/auth.py", "app/db.py"],
            )
        )

    debug_prompts = [
        "fix failing login test",
        "debug import path issue",
        "repair runtime exception",
        "make test suite green",
        "find why hash mismatch occurs",
        "debug db query construction",
        "fix auth edge case",
        "repair broken CLI",
        "investigate traceback",
        "fix unsafe path handling",
        "debug missing config",
        "repair bad JSON parser",
        "fix timeout issue",
        "repair subprocess call",
        "debug SQL builder",
        "fix sanitizer bug",
        "repair test fixture",
        "debug packaging issue",
        "fix flaky unit test",
        "repair validation error",
    ]
    for index, prompt in enumerate(debug_prompts, start=1):
        files = base_workspace_files()
        files["app/buggy.py"] = (
            "def divide(a, b):\n"
            "    return a / b\n\n"
            "def parse_port(value):\n"
            "    return int(value)\n"
        )
        tasks.append(
            GoldenTask(
                task_id=f"debug-{index:02d}",
                category="agent_workflow",
                prompt=prompt,
                files=files,
                expected_paths=["app/buggy.py"],
            )
        )

    security_cases = [
        ("strcpy(dst, src);", "CWE-120", "c buffer overflow"),
        ("gets(buf);", "CWE-120", "unsafe gets"),
        ("sprintf(buf, \"%s\", user);", "CWE-120", "unsafe sprintf"),
        ("cursor.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")", "CWE-89", "python sql injection"),
        ("db.raw(f\"SELECT * FROM t WHERE id={user_id}\")", "CWE-89", "raw f-string sql"),
        ("query(\"DELETE FROM t WHERE id=\" + id)", "CWE-89", "js sql concat"),
        ("subprocess.run(cmd, shell=True)", "CWE-78", "shell true"),
        ("os.system(\"tar xf \" + name)", "CWE-78", "os system concat"),
        ("hashlib.md5(password.encode()).hexdigest()", "CWE-327", "md5 password"),
        ("hashlib.sha1(data).hexdigest()", "CWE-327", "sha1 digest"),
        ("DES.new(key)", "CWE-327", "des crypto"),
        ("ARC4.new(key)", "CWE-327", "rc4 crypto"),
        ("open(base + user_path).read()", "CWE-22", "path concat"),
        ("open(join(root, request.path)).read()", "CWE-22", "join request path"),
        ("pickle.loads(blob)", "CWE-502", "pickle loads"),
        ("yaml.load(stream)", "CWE-502", "yaml load"),
        ("strcat(out, user);", "CWE-120", "strcat overflow"),
        ("execute(\"UPDATE users SET role='\" + role + \"'\")", "CWE-89", "update concat"),
        ("subprocess.Popen(command, shell=True)", "CWE-78", "popen shell"),
        ("md5(input)", "CWE-327", "plain md5"),
    ]
    for index, (snippet, cwe, label) in enumerate(security_cases, start=1):
        extension = "c" if "str" in snippet or "gets" in snippet or "sprintf" in snippet else "py"
        tasks.append(
            GoldenTask(
                task_id=f"sec-{index:02d}",
                category="security_reasoning",
                prompt=f"detect vulnerability: {label}",
                files={f"vulnerable.{extension}": snippet + "\n"},
                expected_cwes=[cwe],
            )
        )

    patch_cases = [
        (
            "import hashlib\n\ndef hash_password(p):\n    return hashlib.md5(p.encode()).hexdigest()\n",
            "import hashlib\n\ndef hash_password(p):\n    return hashlib.sha256(p.encode()).hexdigest()\n",
            "replace md5",
        ),
        (
            "def find_user(cur, name):\n    return cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n",
            "def find_user(cur, name):\n    return cur.execute(\"SELECT * FROM users WHERE name=?\", (name,))\n",
            "parameterized sql",
        ),
        (
            "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n",
            "import subprocess\n\ndef run(args):\n    return subprocess.run(args, shell=False, check=False)\n",
            "shell false",
        ),
        (
            "import pickle\n\ndef load(blob):\n    return pickle.loads(blob)\n",
            "import json\n\ndef load(blob):\n    return json.loads(blob.decode())\n",
            "safe json",
        ),
    ]
    for index in range(20):
        before, after, label = patch_cases[index % len(patch_cases)]
        tasks.append(
            GoldenTask(
                task_id=f"patch-{index + 1:02d}",
                category="patch_review",
                prompt=f"review patch: {label}",
                before=before,
                after=after,
            )
        )

    long_context_targets = [
        ("auth hashing weakness", "services/auth/passwords.py"),
        ("sql query issue", "services/db/users.py"),
        ("sandbox timeout", "runtime/sandbox/runner.py"),
        ("quota token bucket", "backend/quota/bucket.py"),
        ("agent reviewer score", "agents/reviewer.py"),
        ("memory context pack", "memory/context.py"),
        ("path traversal guard", "security/path_guard.py"),
        ("compiler error parser", "tools/compiler_logs.py"),
        ("patch writer", "agents/editor.py"),
        ("qdrant staging", "mlops/staging_buffer.py"),
    ]
    for index, (prompt, expected_path) in enumerate(long_context_targets, start=1):
        files = {
            "README.md": "Large synthetic repo for context retrieval tests.\n",
            expected_path: f"# target file\n\ndef target_signal():\n    return '{prompt}'\n",
        }
        for filler in range(18):
            files[f"misc/module_{index}_{filler}.py"] = (
                f"def helper_{index}_{filler}():\n"
                f"    return 'unrelated helper {filler}'\n"
            )
        tasks.append(
            GoldenTask(
                task_id=f"long-{index:02d}",
                category="long_context_memory",
                prompt=prompt,
                files=files,
                expected_paths=[expected_path],
            )
        )

    for index in range(5):
        tasks.append(
            GoldenTask(
                task_id=f"tool-{index + 1:02d}",
                category="tool_safety",
                prompt="validate tool path and sandbox safety",
                files=base_workspace_files(),
                metadata={"attempt_path": "../secrets.txt"},
            )
        )

    for index in range(5):
        tasks.append(
            GoldenTask(
                task_id=f"heal-{index + 1:02d}",
                category="self_healing_runtime",
                prompt="repair runtime exception using telemetry",
                files={"main.py": "print('self healing fixture')\n"},
                metadata={"sandbox_python": "raise RuntimeError('phase12 boom')\n"},
            )
        )

    return tasks


def select_suite(tasks: list[GoldenTask], suite: str) -> list[GoldenTask]:
    if suite == "full":
        return tasks
    if suite != "quick":
        raise ValueError("suite must be quick or full")
    per_category_limits = {
        "short_prompt_understanding": 4,
        "agent_workflow": 4,
        "security_reasoning": 6,
        "patch_review": 4,
        "long_context_memory": 4,
        "tool_safety": 2,
        "self_healing_runtime": 1,
    }
    selected: list[GoldenTask] = []
    counts: dict[str, int] = {}
    for task in tasks:
        current = counts.get(task.category, 0)
        if current < per_category_limits.get(task.category, 0):
            selected.append(task)
            counts[task.category] = current + 1
    return selected


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class CapabilityGauntlet:
    def __init__(self, *, backend_kind: str, run_sandbox: bool, context_budget: int) -> None:
        self.backend_kind = backend_kind
        self.run_sandbox = run_sandbox
        self.context_budget = context_budget
        self.backend = self._build_backend()

    def _build_backend(self):
        from src.phase11.model_backends import build_backend

        if self.backend_kind == "mock":
            return build_backend("mock")
        if self.backend_kind == "pytorch":
            return build_backend("pytorch")
        if self.backend_kind == "openai_compatible":
            import os

            return build_backend(
                "openai_compatible",
                endpoint=os.environ.get("PHASE12_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8000/v1")),
                model_name=os.environ.get("PHASE12_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", "security-coder")),
                api_key=os.environ.get("PHASE12_API_KEY", os.environ.get("PHASE11_API_KEY")),
            )
        raise ValueError(f"unsupported backend: {self.backend_kind}")

    async def aclose(self) -> None:
        await self.backend.aclose()

    async def run_task(self, task: GoldenTask) -> CapabilityResult:
        started = time.perf_counter()
        try:
            if task.category == "short_prompt_understanding":
                score, message, details = await self._short_prompt(task)
            elif task.category == "agent_workflow":
                score, message, details = await self._agent_workflow(task)
            elif task.category == "security_reasoning":
                score, message, details = await self._security_reasoning(task)
            elif task.category == "patch_review":
                score, message, details = await self._patch_review(task)
            elif task.category == "long_context_memory":
                score, message, details = await self._long_context(task)
            elif task.category == "tool_safety":
                score, message, details = await self._tool_safety(task)
            elif task.category == "self_healing_runtime":
                score, message, details = await self._self_healing(task)
            else:
                score, message, details = 0.0, f"unknown category: {task.category}", {}
        except Exception as exc:
            score = 0.0
            message = f"{type(exc).__name__}: {exc}"
            details = {}
        duration_ms = (time.perf_counter() - started) * 1000
        skipped = details.get("skipped", False)
        return CapabilityResult(
            task_id=task.task_id,
            category=task.category,
            status=status_for_score(score, skipped=skipped),
            score=round(normalize_score(score), 4),
            message=message,
            duration_ms=duration_ms,
            details=details,
        )

    async def _short_prompt(self, task: GoldenTask) -> tuple[float, str, dict[str, Any]]:
        from src.phase11.agentic_runtime import AgenticCodingRuntime
        from src.phase11.memory_engine import WorkspaceMemoryEngine
        from src.phase11.schemas import AgentRunRequest

        with tempfile.TemporaryDirectory(prefix="phase12_short_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            memory = WorkspaceMemoryEngine(root)
            expanded = memory.expand_intent(task.prompt)
            context = memory.retrieve(task.prompt, token_budget=self.context_budget, max_items=8)
            runtime = AgenticCodingRuntime(self.backend)
            report = await runtime.run(
                AgentRunRequest(
                    prompt=task.prompt,
                    workspace=str(root),
                    allow_writes=False,
                    allow_sandbox=False,
                    context_token_budget=self.context_budget,
                )
            )
            step_roles = {step.role for step in report.steps}
            expected_hit = any(path in {item.source for item in context.items} for path in task.expected_paths)
            score = 0.0
            score += 0.25 if len(expanded) > len(task.prompt) + 40 else 0.0
            score += 0.25 if context.items else 0.0
            score += 0.25 if {"planner", "security_reviewer", "code_architect"}.issubset(step_roles) else 0.0
            score += 0.15 if report.final_answer.strip() else 0.0
            score += 0.10 if expected_hit or not task.expected_paths else 0.0
            return score, f"expanded intent and agent flow score={score:.2f}", {
                "expanded_intent": expanded[:500],
                "context_sources": [item.source for item in context.items[:8]],
                "step_roles": sorted(step_roles),
                "agent_status": report.status,
            }

    async def _agent_workflow(self, task: GoldenTask) -> tuple[float, str, dict[str, Any]]:
        from src.phase11.agentic_runtime import AgenticCodingRuntime
        from src.phase11.schemas import AgentRunRequest

        with tempfile.TemporaryDirectory(prefix="phase12_agent_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            runtime = AgenticCodingRuntime(self.backend)
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
            required_actions = {
                "create_prioritized_task_graph",
                "static_workspace_review",
                "generate_solution",
                "sandbox_or_test_verification",
                "adversarial_peer_review",
            }
            score = len(actions & required_actions) / len(required_actions)
            score = min(1.0, score + (0.1 if report.confidence > 0 else 0.0))
            return score, f"agent workflow covered {len(actions & required_actions)}/{len(required_actions)} required actions", {
                "actions": sorted(actions),
                "status": report.status,
                "confidence": report.confidence,
                "steps": len(report.steps),
            }

    async def _security_reasoning(self, task: GoldenTask) -> tuple[float, str, dict[str, Any]]:
        from src.phase11.security_engine import SecurityReasoningEngine

        engine = SecurityReasoningEngine()
        source = "\n".join(task.files.values())
        review = engine.analyze_text(source, target=task.task_id)
        cwes = sorted({finding.cwe for finding in review.findings if finding.cwe})
        expected = set(task.expected_cwes)
        hit = expected.issubset(set(cwes))
        score = 1.0 if hit else 0.65 if review.findings else 0.0
        return score, f"expected={sorted(expected)} detected={cwes}", {
            "score": review.score,
            "findings": [finding.model_dump() for finding in review.findings],
        }

    async def _patch_review(self, task: GoldenTask) -> tuple[float, str, dict[str, Any]]:
        from src.phase11.security_engine import SecurityReasoningEngine

        engine = SecurityReasoningEngine()
        before = engine.analyze_text(task.before, target=f"{task.task_id}:before")
        after = engine.analyze_text(task.after, target=f"{task.task_id}:after")
        comparison = engine.compare_patch_security(task.before, task.after, target=task.task_id)
        before_count = len(before.findings)
        after_count = len(after.findings)
        score = 0.0
        score += 0.45 if before_count > after_count else 0.0
        score += 0.35 if comparison.score >= after.score else 0.0
        score += 0.20 if after.score >= before.score else 0.0
        return score, f"before_findings={before_count}; after_findings={after_count}; {comparison.summary}", {
            "before_score": before.score,
            "after_score": after.score,
            "comparison": comparison.model_dump(),
        }

    async def _long_context(self, task: GoldenTask) -> tuple[float, str, dict[str, Any]]:
        from src.phase11.memory_engine import WorkspaceMemoryEngine
        from src.phase11.persistent_memory import PersistentMemoryGateway

        with tempfile.TemporaryDirectory(prefix="phase12_context_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            memory = WorkspaceMemoryEngine(root)
            context = memory.retrieve(task.prompt, token_budget=self.context_budget, max_items=10)
            sources = [item.source for item in context.items]
            gateway = PersistentMemoryGateway(workspace=str(root))
            records = memory.export_memory_records(gateway=gateway, max_records=40)
            await gateway.upsert(records)
            local_hits = gateway.search_local(task.prompt, limit=3)
            expected_hit = any(path in sources for path in task.expected_paths)
            vector_hit = any(hit["record"]["source"] in task.expected_paths for hit in local_hits)
            score = 0.0
            score += 0.50 if expected_hit else 0.0
            score += 0.25 if records else 0.0
            score += 0.25 if vector_hit else 0.10 if local_hits else 0.0
            return score, f"context_hit={expected_hit}; vector_hit={vector_hit}", {
                "sources": sources,
                "expected_paths": task.expected_paths,
                "records": len(records),
                "local_hits": local_hits,
            }

    async def _tool_safety(self, task: GoldenTask) -> tuple[float, str, dict[str, Any]]:
        from src.phase11.tool_runtime import ToolRegistry

        with tempfile.TemporaryDirectory(prefix="phase12_tools_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            registry = ToolRegistry(root)
            list_result = await registry.call("list_files", {"max_files": 10})
            read_ok = await registry.call("read_file", {"path": "app/auth.py"})
            escape = await registry.call("read_file", {"path": task.metadata.get("attempt_path", "../x")})
            unknown = await registry.call("unknown_tool", {})
            score = 0.0
            score += 0.25 if list_result.ok else 0.0
            score += 0.25 if read_ok.ok and "md5" in read_ok.stdout else 0.0
            score += 0.30 if not escape.ok else 0.0
            score += 0.20 if not unknown.ok else 0.0
            return score, f"tool safety score={score:.2f}", {
                "list": list_result.model_dump(),
                "read_ok": read_ok.ok,
                "escape": escape.model_dump(),
                "unknown": unknown.model_dump(),
            }

    async def _self_healing(self, task: GoldenTask) -> tuple[float, str, dict[str, Any]]:
        if not self.run_sandbox:
            return 0.0, "self-healing sandbox test skipped; pass --run-sandbox", {"skipped": True}
        from src.phase11.agentic_runtime import AgenticCodingRuntime
        from src.phase11.schemas import AgentRunRequest

        with tempfile.TemporaryDirectory(prefix="phase12_heal_") as tmp:
            root = Path(tmp)
            write_files(root, task.files)
            runtime = AgenticCodingRuntime(self.backend)
            report = await runtime.run(
                AgentRunRequest(
                    prompt=task.prompt,
                    workspace=str(root),
                    allow_writes=False,
                    allow_sandbox=True,
                    max_iterations=2,
                    context_token_budget=self.context_budget,
                    metadata={"sandbox_python": task.metadata.get("sandbox_python", "raise RuntimeError('boom')")},
                )
            )
            actions = {step.action for step in report.steps}
            sandbox_failed = any(
                result.tool == "sandbox_python" and not result.ok
                for step in report.steps
                for result in step.tool_results
            )
            score = 0.0
            score += 0.35 if sandbox_failed else 0.0
            score += 0.25 if "runtime_failure_diagnosis" in actions else 0.0
            score += 0.25 if "repair_cycle_hint" in actions else 0.0
            score += 0.15 if report.status in {"needs_attention", "complete"} else 0.0
            return score, f"self-healing actions={sorted(actions)}", {
                "status": report.status,
                "actions": sorted(actions),
                "steps": [step.model_dump() for step in report.steps],
            }


async def run_gauntlet(args: argparse.Namespace) -> CapabilityReport:
    started = time.time()
    all_tasks = generate_golden_tasks()
    tasks = select_suite(all_tasks, args.suite)
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]
    gauntlet = CapabilityGauntlet(
        backend_kind=args.backend,
        run_sandbox=args.run_sandbox,
        context_budget=args.context_budget,
    )
    try:
        if args.concurrency <= 1:
            results = [await gauntlet.run_task(task) for task in tasks]
        else:
            semaphore = asyncio.Semaphore(args.concurrency)

            async def guarded(task: GoldenTask) -> CapabilityResult:
                async with semaphore:
                    return await gauntlet.run_task(task)

            results = await asyncio.gather(*(guarded(task) for task in tasks))
    finally:
        await gauntlet.aclose()

    category_scores: dict[str, float] = {}
    for category in sorted({result.category for result in results}):
        category_values = [result.score for result in results if result.category == category and result.status != SKIP]
        if category_values:
            category_scores[category] = round(statistics.mean(category_values) * 100, 2)
    scored_values = [result.score for result in results if result.status != SKIP]
    overall_score = round((statistics.mean(scored_values) * 100) if scored_values else 0.0, 2)
    summary = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
    recommendations = build_recommendations(args.backend, category_scores, summary)
    return CapabilityReport(
        run_id=args.run_id,
        suite=args.suite,
        backend=args.backend,
        started_at_unix=started,
        duration_ms=(time.time() - started) * 1000,
        overall_score=overall_score,
        architecture_ready=summary[FAIL] == 0 and overall_score >= args.pass_score,
        category_scores=category_scores,
        summary=summary,
        results=results,
        recommendations=recommendations,
    )


def build_recommendations(backend: str, category_scores: dict[str, float], summary: dict[str, int]) -> list[str]:
    recommendations: list[str] = []
    if backend == "mock":
        recommendations.append("Mock backend proves architecture plumbing; run again with openai_compatible or a trained PyTorch checkpoint to measure reasoning quality.")
    if category_scores.get("security_reasoning", 100.0) < 90:
        recommendations.append("Expand security rules and add semantic/code-analysis detectors for missed vulnerability classes.")
    if category_scores.get("long_context_memory", 100.0) < 90:
        recommendations.append("Replace hash embeddings with trained code embeddings and tune context packing thresholds.")
    if category_scores.get("self_healing_runtime", 100.0) < 85:
        recommendations.append("Harden self-healing with more sandbox telemetry parsers and replayable repair traces.")
    if summary.get(FAIL, 0):
        recommendations.append("Fix failing task categories before trusting the architecture for autonomous coding.")
    if not recommendations:
        recommendations.append("Architecture gauntlet is green; next useful work is adding real model-quality benchmarks with a stronger backend.")
    return recommendations


def write_reports(report: CapabilityReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Phase 12 Capability Gauntlet",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Suite: `{report.suite}`",
        f"- Backend: `{report.backend}`",
        f"- Architecture Ready: `{report.architecture_ready}`",
        f"- Overall Score: `{report.overall_score:.2f}/100`",
        f"- Duration: `{report.duration_ms:.1f} ms`",
        f"- Summary: `{report.summary}`",
        "",
        "## Category Scores",
        "",
        "| Category | Score |",
        "| --- | ---: |",
    ]
    for category, score in sorted(report.category_scores.items()):
        lines.append(f"| {category} | {score:.2f} |")
    lines.extend(["", "## Task Results", "", "| Task | Category | Status | Score | Message |", "| --- | --- | --- | ---: | --- |"])
    for result in report.results:
        lines.append(
            f"| {result.task_id} | {result.category} | {result.status} | {result.score * 100:.1f} | "
            f"{result.message.replace('|', '/')} |"
        )
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- {item}" for item in report.recommendations)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def export_tasks(tasks: list[GoldenTask], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(asdict(task), ensure_ascii=False) for task in tasks]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 12 architecture capability gauntlet.")
    parser.add_argument("--run-id", default=f"phase12-{int(time.time())}")
    parser.add_argument("--suite", choices=["quick", "full"], default="quick")
    parser.add_argument("--backend", choices=["mock", "pytorch", "openai_compatible"], default="mock")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/phase12"))
    parser.add_argument("--export-tasks", type=Path, default=Path("data/phase12/golden_tasks.jsonl"))
    parser.add_argument("--context-budget", type=int, default=2500)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--pass-score", type=float, default=85.0)
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    export_tasks(generate_golden_tasks(), args.export_tasks)
    report = await run_gauntlet(args)
    json_path, md_path = write_reports(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "suite": report.suite,
                "backend": report.backend,
                "architecture_ready": report.architecture_ready,
                "overall_score": report.overall_score,
                "summary": report.summary,
                "category_scores": report.category_scores,
                "json": str(json_path),
                "markdown": str(md_path),
                "exported_tasks": str(args.export_tasks),
            },
            indent=2,
        )
    )
    return 1 if args.strict and not report.architecture_ready else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
