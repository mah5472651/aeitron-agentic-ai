#!/usr/bin/env python
"""Phase 41 real task regression pack.

Generates a larger deterministic task suite for the architecture:

- 100 short prompt coding tasks
- 100 debugging tasks
- 100 security finding tasks
- 50 multi-file repository tasks
- 50 patch verification tasks
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class RegressionTask:
    task_id: str
    category: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    expected_signals: list[str] = field(default_factory=list)
    expected_paths: list[str] = field(default_factory=list)
    expected_cwes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RegressionSmokeResult(StrictModel):
    task_id: str
    category: str
    score: float
    status: str
    signal_hits: list[str]
    missing_signals: list[str]


class RegressionPackReport(StrictModel):
    run_id: str
    dataset_path: str
    task_count: int
    category_counts: dict[str, int]
    smoke_results: list[RegressionSmokeResult]
    smoke_score: float | None
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


SHORT_FEATURES = [
    ("todo api", ["FastAPI", "CRUD", "tests", "validation"]),
    ("jwt login", ["JWT", "password hashing", "rate limit", "tests"]),
    ("csv parser", ["streaming", "schema", "error handling", "tests"]),
    ("cache layer", ["Redis", "TTL", "fallback", "tests"]),
    ("file uploader", ["size limit", "content type", "path safety", "tests"]),
    ("markdown renderer", ["sanitize", "preview", "unit tests", "XSS"]),
    ("notification worker", ["queue", "retry", "idempotency", "logs"]),
    ("billing webhook", ["signature", "idempotency", "database", "tests"]),
    ("search endpoint", ["pagination", "ranking", "input validation", "tests"]),
    ("audit log", ["append-only", "metadata", "query", "tests"]),
]

DEBUG_PATTERNS = [
    ("off by one pagination", "page_count = total // per_page", "ceil division"),
    ("race condition cache", "if key not in cache: cache[key] = build()", "lock or atomic set"),
    ("timezone bug", "datetime.now()", "timezone-aware UTC"),
    ("leaky file handle", "open(path).read()", "context manager"),
    ("retry storm", "while True: retry()", "bounded exponential backoff"),
    ("json crash", "json.loads(body)", "catch decode error"),
    ("null user", "user.email.lower()", "None guard"),
    ("slow loop query", "for id in ids: db.get(id)", "batch query"),
    ("wrong hash compare", "token == expected", "constant time compare"),
    ("missing await", "client.fetch()", "await async call"),
]

SECURITY_CASES = [
    ("CWE-89", "cursor.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")", "parameterized query"),
    ("CWE-78", "subprocess.run(cmd, shell=True)", "shell=False argument list"),
    ("CWE-22", "open(base + '/' + user_path).read()", "path containment"),
    ("CWE-327", "hashlib.md5(password.encode()).hexdigest()", "password hashing KDF"),
    ("CWE-502", "pickle.loads(blob)", "safe parser"),
    ("CWE-120", "strcpy(dst, src);", "bounded copy"),
    ("CWE-79", "res.send('<div>' + req.query.name + '</div>')", "HTML escaping"),
    ("CWE-352", "POST /transfer without csrf token", "CSRF token"),
    ("CWE-798", "API_KEY='hardcoded-secret'", "secret manager"),
    ("CWE-200", "return user.password_hash", "sensitive data filtering"),
]


def make_short_tasks() -> list[RegressionTask]:
    tasks: list[RegressionTask] = []
    for i in range(100):
        name, signals = SHORT_FEATURES[i % len(SHORT_FEATURES)]
        tasks.append(
            RegressionTask(
                task_id=f"short-coding-{i + 1:03d}",
                category="short_prompt_coding",
                prompt=f"Build {name}. Keep it production ready.",
                expected_signals=signals + ["plan", "implementation"],
                metadata={"difficulty": "short_prompt", "variant": i // len(SHORT_FEATURES)},
            )
        )
    return tasks


def make_debug_tasks() -> list[RegressionTask]:
    tasks: list[RegressionTask] = []
    for i in range(100):
        title, bug, fix = DEBUG_PATTERNS[i % len(DEBUG_PATTERNS)]
        path = f"app/module_{i % 10}.py"
        tasks.append(
            RegressionTask(
                task_id=f"debug-{i + 1:03d}",
                category="debugging",
                prompt=f"Debug this issue: {title}. Explain root cause, patch, and tests.",
                files={path: f"def broken(value):\n    {bug}\n    return value\n"},
                expected_signals=["root cause", fix, "test", path],
                expected_paths=[path],
                metadata={"bug": title, "fix_signal": fix},
            )
        )
    return tasks


def make_security_tasks() -> list[RegressionTask]:
    tasks: list[RegressionTask] = []
    for i in range(100):
        cwe, snippet, fix = SECURITY_CASES[i % len(SECURITY_CASES)]
        path = f"service/security_case_{i % 10}.txt"
        tasks.append(
            RegressionTask(
                task_id=f"security-{i + 1:03d}",
                category="security_finding",
                prompt=f"Find and fix the vulnerability. Code:\n{snippet}",
                files={path: snippet + "\n"},
                expected_signals=[cwe, fix, "vulnerability", "regression test"],
                expected_paths=[path],
                expected_cwes=[cwe],
                metadata={"cwe": cwe},
            )
        )
    return tasks


def make_multifile_tasks() -> list[RegressionTask]:
    tasks: list[RegressionTask] = []
    for i in range(50):
        service = i % 5
        files = {
            "app/api.py": "from app.auth import require_user\nfrom app.store import save_item\n",
            "app/auth.py": "def require_user(token):\n    return {'id': token}\n",
            "app/store.py": "def save_item(user, payload):\n    return {'ok': True, 'user': user['id']}\n",
            f"tests/test_flow_{service}.py": "def test_flow():\n    assert True\n",
        }
        tasks.append(
            RegressionTask(
                task_id=f"multifile-{i + 1:03d}",
                category="multi_file_repo",
                prompt="Add tenant-aware validation across API, auth, store, and tests without breaking existing behavior.",
                files=files,
                expected_signals=["app/api.py", "app/auth.py", "app/store.py", "tests", "tenant"],
                expected_paths=list(files),
                metadata={"repo_shape": "small_service", "service_variant": service},
            )
        )
    return tasks


def make_patch_tasks() -> list[RegressionTask]:
    tasks: list[RegressionTask] = []
    for i in range(50):
        cwe, snippet, fix = SECURITY_CASES[i % len(SECURITY_CASES)]
        tasks.append(
            RegressionTask(
                task_id=f"patch-{i + 1:03d}",
                category="patch_verification",
                prompt="Generate a minimal safe patch and verification plan for the vulnerable code.",
                files={"before.txt": snippet + "\n", "expected_fix.txt": fix + "\n"},
                expected_signals=["before", "after", fix, "verify", "test"],
                expected_cwes=[cwe],
                metadata={"cwe": cwe, "patch_type": "security"},
            )
        )
    return tasks


def generate_tasks() -> list[RegressionTask]:
    return make_short_tasks() + make_debug_tasks() + make_security_tasks() + make_multifile_tasks() + make_patch_tasks()


def category_counts(tasks: list[RegressionTask]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.category] = counts.get(task.category, 0) + 1
    counts["total"] = len(tasks)
    return counts


def write_jsonl(path: Path, tasks: list[RegressionTask]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(asdict(task), ensure_ascii=False) for task in tasks) + "\n", encoding="utf-8")


def score_text(task: RegressionTask, text: str) -> RegressionSmokeResult:
    lower = text.lower()
    hits = [signal for signal in task.expected_signals if signal.lower() in lower]
    missing = [signal for signal in task.expected_signals if signal.lower() not in lower]
    score = len(hits) / max(1, len(task.expected_signals))
    status = "ok" if score >= 0.80 else "warn" if score >= 0.50 else "fail"
    return RegressionSmokeResult(task_id=task.task_id, category=task.category, score=score, status=status, signal_hits=hits, missing_signals=missing)


async def smoke_run(tasks: list[RegressionTask], *, limit: int) -> list[RegressionSmokeResult]:
    selected = tasks[:limit]
    results: list[RegressionSmokeResult] = []
    for task in selected:
        synthetic_response = " ".join(task.expected_signals) + " plan implementation test verify root cause after"
        results.append(score_text(task, synthetic_response))
    return results


def write_report(report: RegressionPackReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "regression-pack-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Phase 41 regression pack.")
    parser.add_argument("--run-id", default=f"phase41-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase41")
    parser.add_argument("--dataset-path", type=Path, default=ROOT / "artifacts" / "phase41" / "regression-pack.jsonl")
    parser.add_argument("--smoke-limit", type=int, default=25)
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    tasks = generate_tasks()
    write_jsonl(args.dataset_path, tasks)
    smoke = await smoke_run(tasks, limit=args.smoke_limit)
    smoke_score = sum(result.score for result in smoke) / len(smoke) if smoke else None
    report = RegressionPackReport(
        run_id=args.run_id,
        dataset_path=str(args.dataset_path),
        task_count=len(tasks),
        category_counts=category_counts(tasks),
        smoke_results=smoke,
        smoke_score=smoke_score,
        recommendation="Use this pack for quick regression gates before and after model/backend changes.",
    )
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "task_count": report.task_count, "smoke_score": report.smoke_score, "json": str(json_path)}, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
