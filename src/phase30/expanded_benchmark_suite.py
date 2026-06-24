#!/usr/bin/env python
"""Generate an expanded golden task suite for coding/security architecture evals."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class ExpandedTask:
    task_id: str
    category: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    expected_paths: list[str] = field(default_factory=list)
    expected_cwes: list[str] = field(default_factory=list)
    before: str = ""
    after: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def generate_tasks() -> list[ExpandedTask]:
    tasks: list[ExpandedTask] = []
    for index in range(100):
        tasks.append(
            ExpandedTask(
                task_id=f"short-v2-{index+1:03d}",
                category="short_prompt_coding",
                prompt=[
                    "fix auth",
                    "make build pass",
                    "secure upload",
                    "add tests",
                    "repair api bug",
                ][index % 5],
                files={"app/main.py": "def handler(x):\n    return x\n", "tests/test_main.py": "def test_smoke():\n    assert True\n"},
                expected_paths=["app/main.py", "tests/test_main.py"],
                metadata={"difficulty": "short_prompt", "requires_intent_expansion": True},
            )
        )
    for index in range(100):
        tasks.append(
            ExpandedTask(
                task_id=f"debug-v2-{index+1:03d}",
                category="debugging",
                prompt=f"debug failing traceback case {index+1}",
                files={"app/calc.py": "def divide(a,b):\n    return a/b\n", "tests/test_calc.py": "from app.calc import divide\n\ndef test_zero():\n    assert divide(1,0) is None\n"},
                expected_paths=["app/calc.py", "tests/test_calc.py"],
                metadata={"failure_type": ["exception", "assertion", "import", "timeout"][index % 4]},
            )
        )
    security_cases = [
        ("CWE-120", "strcpy(dst, src);"),
        ("CWE-89", "cursor.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")"),
        ("CWE-78", "subprocess.run(cmd, shell=True)"),
        ("CWE-327", "hashlib.md5(password.encode()).hexdigest()"),
        ("CWE-22", "open(base + '/' + name).read()"),
    ]
    for index in range(100):
        cwe, snippet = security_cases[index % len(security_cases)]
        tasks.append(
            ExpandedTask(
                task_id=f"sec-v2-{index+1:03d}",
                category="security_finding",
                prompt=f"find and explain defensive fix for {cwe}",
                files={"vulnerable.py": snippet + "\n"},
                expected_cwes=[cwe],
                metadata={"defensive_only": True},
            )
        )
    patches = [
        ("subprocess.run(cmd, shell=True)\n", "subprocess.run(args, shell=False, check=False)\n", "CWE-78"),
        ("pickle.loads(blob)\n", "json.loads(blob.decode('utf-8'))\n", "CWE-502"),
        ("hashlib.md5(p.encode()).hexdigest()\n", "hashlib.sha256(p.encode()).hexdigest()\n", "CWE-327"),
        ("cur.execute(\"SELECT * FROM t WHERE id=\" + id)\n", "cur.execute(\"SELECT * FROM t WHERE id=?\", (id,))\n", "CWE-89"),
    ]
    for index in range(100):
        before, after, cwe = patches[index % len(patches)]
        tasks.append(ExpandedTask(task_id=f"patch-v2-{index+1:03d}", category="patch_generation", prompt=f"patch {cwe} safely", before=before, after=after, expected_cwes=[cwe]))
    for index in range(50):
        expected = f"services/domain_{index}/target.py"
        files = {expected: f"def target():\n    return 'signal-{index}'\n"}
        for filler in range(30):
            files[f"noise/pkg_{index}/module_{filler}.py"] = f"def helper():\n    return 'noise-{filler}'\n"
        tasks.append(ExpandedTask(task_id=f"long-v2-{index+1:03d}", category="long_context_repo", prompt=f"find signal-{index} and patch target path", files=files, expected_paths=[expected]))
    return tasks


def write_suite(tasks: list[ExpandedTask], output_dir: Path, run_id: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{run_id}.jsonl"
    summary_path = output_dir / "expanded-benchmark-latest.json"
    jsonl_path.write_text("\n".join(json.dumps(asdict(task), ensure_ascii=False) for task in tasks) + "\n", encoding="utf-8")
    summary: dict[str, Any] = {"run_id": run_id, "task_count": len(tasks), "jsonl": str(jsonl_path), "categories": {}}
    for task in tasks:
        summary["categories"][task.category] = summary["categories"].get(task.category, 0) + 1
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return jsonl_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Phase 30 expanded benchmark suite.")
    parser.add_argument("--run-id", default=f"phase30-expanded-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase30")
    args = parser.parse_args()
    tasks = generate_tasks()
    jsonl_path, summary_path = write_suite(tasks, args.output_dir, args.run_id)
    print(json.dumps({"run_id": args.run_id, "tasks": len(tasks), "jsonl": str(jsonl_path), "summary": str(summary_path)}, indent=2))


if __name__ == "__main__":
    main()

