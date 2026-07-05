"""Benchmark harness contracts for coding and defensive security evaluation."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class BenchmarkTask(StrictModel):
    task_id: str
    benchmark: Literal["swe_style", "security_static", "patch_generation"]
    prompt: str
    files: dict[str, str] = Field(default_factory=dict)
    verification_commands: list[list[str]] = Field(default_factory=list)
    expected_findings: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class BenchmarkResult(StrictModel):
    task_id: str
    benchmark: str
    status: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRunReport(StrictModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: str
    total: int
    passed: int
    score: float
    results: list[BenchmarkResult]
    created_at_unix: float = Field(default_factory=time.time)

    def write_markdown(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Mythos Benchmark Report {self.run_id}",
            "",
            f"- status: {self.status}",
            f"- total: {self.total}",
            f"- passed: {self.passed}",
            f"- score: {self.score:.4f}",
            "",
            "| task | benchmark | status | score | reason |",
            "|---|---|---|---:|---|",
        ]
        for result in self.results:
            lines.append(
                f"| {result.task_id} | {result.benchmark} | {result.status} | {result.score:.3f} | {result.reason} |"
            )
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target


def load_tasks(path: str | Path) -> list[BenchmarkTask]:
    source = Path(path)
    tasks: list[BenchmarkTask] = []
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        tasks.append(BenchmarkTask.model_validate(json.loads(line)))
    return tasks


class BenchmarkHarness:
    def run_static(self, tasks: list[BenchmarkTask]) -> BenchmarkRunReport:
        results = []
        for task in tasks:
            joined = "\n".join(task.files.values()).lower()
            expected = [finding.lower() for finding in task.expected_findings]
            hits = sum(1 for finding in expected if finding in joined)
            score = 1.0 if not expected else hits / len(expected)
            status = "passed" if score >= 1.0 else "failed"
            results.append(
                BenchmarkResult(
                    task_id=task.task_id,
                    benchmark=task.benchmark,
                    status=status,
                    score=score,
                    reason="expected defensive findings matched" if status == "passed" else "missing expected findings",
                    metrics={"expected": len(expected), "hits": hits},
                )
            )
        passed = sum(1 for result in results if result.status == "passed")
        score = passed / max(1, len(results))
        return BenchmarkRunReport(status="passed" if passed == len(results) else "failed", total=len(results), passed=passed, score=score, results=results)


def built_in_security_tasks() -> list[BenchmarkTask]:
    return [
        BenchmarkTask(
            task_id="security-sql-injection-001",
            benchmark="security_static",
            prompt="Find SQL injection risk.",
            files={"app.py": "cursor.execute('SELECT * FROM users WHERE name=' + user_input)"},
            expected_findings=["select * from users", "user_input"],
            tags=["sql_injection"],
        ),
        BenchmarkTask(
            task_id="security-hardcoded-secret-001",
            benchmark="security_static",
            prompt="Find hardcoded secret risk.",
            files={"settings.py": "API_KEY = 'abc123456789012345678901234567'"},
            expected_findings=["api_key"],
            tags=["secret"],
        ),
        BenchmarkTask(
            task_id="security-command-injection-001",
            benchmark="security_static",
            prompt="Find command injection risk.",
            files={"runner.py": "subprocess.run('tar xf ' + filename, shell=True)"},
            expected_findings=["shell=true", "filename"],
            tags=["command_injection"],
        ),
        BenchmarkTask(
            task_id="security-path-traversal-001",
            benchmark="security_static",
            prompt="Find path traversal risk.",
            files={"files.py": "open('/srv/uploads/' + request.args['name']).read()"},
            expected_findings=["request.args", "uploads"],
            tags=["path_traversal"],
        ),
        BenchmarkTask(
            task_id="security-xss-001",
            benchmark="security_static",
            prompt="Find reflected XSS risk.",
            files={"view.js": "document.body.innerHTML = location.hash.substring(1);"},
            expected_findings=["innerhtml", "location.hash"],
            tags=["xss"],
        ),
        BenchmarkTask(
            task_id="security-weak-crypto-001",
            benchmark="security_static",
            prompt="Find weak hash usage.",
            files={"crypto.py": "digest = hashlib.md5(password.encode()).hexdigest()"},
            expected_findings=["md5", "password"],
            tags=["weak_crypto"],
        ),
        BenchmarkTask(
            task_id="security-insecure-random-001",
            benchmark="security_static",
            prompt="Find insecure token generation.",
            files={"tokens.py": "reset_token = str(random.random())"},
            expected_findings=["random.random", "reset_token"],
            tags=["insecure_random"],
        ),
        BenchmarkTask(
            task_id="security-unsafe-deserialization-001",
            benchmark="security_static",
            prompt="Find unsafe deserialization.",
            files={"cache.py": "return pickle.loads(blob)"},
            expected_findings=["pickle.loads"],
            tags=["deserialization"],
        ),
        BenchmarkTask(
            task_id="security-buffer-copy-001",
            benchmark="security_static",
            prompt="Find unsafe C string copy.",
            files={"main.c": "char buf[16]; strcpy(buf, argv[1]);"},
            expected_findings=["strcpy", "argv"],
            tags=["buffer_overflow"],
        ),
        BenchmarkTask(
            task_id="coding-regression-test-001",
            benchmark="swe_style",
            prompt="Check that patch includes regression assertion.",
            files={"test_auth.py": "def test_rejects_empty_password(): assert login('u', '') is False"},
            expected_findings=["test_rejects_empty_password", "assert"],
            tags=["tests"],
        ),
        BenchmarkTask(
            task_id="patch-generation-shape-001",
            benchmark="patch_generation",
            prompt="Check patch shape contains validation guard.",
            files={"patch.diff": "+ if not user_input:\n+     raise ValueError('missing input')"},
            expected_findings=["raise valueerror", "user_input"],
            tags=["patch_shape"],
        ),
    ]
