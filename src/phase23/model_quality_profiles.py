#!/usr/bin/env python
"""Profile-driven launcher for future 7B-32B quality scorecard runs."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class QualityProfilePlan(StrictModel):
    run_id: str
    profile_name: str
    model_id: str
    revision: str | None = None
    endpoint: str
    command: list[str]
    env: dict[str, str]
    dry_run: bool
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    created_at_unix: float = Field(default_factory=time.time)


def load_profiles() -> dict[str, Any]:
    path = ROOT / "deploy" / "gpu" / "model_profiles.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles") if isinstance(payload, dict) else payload
    if isinstance(profiles, list):
        return {str(item.get("name")): item for item in profiles if isinstance(item, dict)}
    if isinstance(profiles, dict):
        return profiles
    return {}


def build_plan(args: argparse.Namespace) -> QualityProfilePlan:
    profiles = load_profiles()
    profile = profiles.get(args.profile)
    if not profile:
        raise ValueError(f"unknown profile: {args.profile}; available={sorted(profiles)}")
    model_id = str(profile.get("model_id") or args.profile)
    revision = profile.get("revision")
    run_id = args.run_id or f"phase23-{args.profile}-{int(time.time())}"
    command = [
        sys.executable,
        "src/phase18/model_quality_loop.py",
        "--run-id",
        run_id,
        "--model-endpoint",
        args.endpoint,
        "--model-name",
        model_id,
        "--max-new-tokens",
        str(args.max_new_tokens),
        "--include-warnings",
    ]
    if args.full:
        command.append("--full")
    else:
        command.extend(["--max-tasks", str(args.max_tasks), "--per-category", str(args.per_category)])
    if args.run_sandbox:
        command.append("--run-sandbox")
    if args.dry_run:
        command.extend(["--dry-run", "--json"])
    return QualityProfilePlan(
        run_id=run_id,
        profile_name=args.profile,
        model_id=model_id,
        revision=revision,
        endpoint=args.endpoint,
        command=command,
        env={
            "PHASE23_PROFILE": args.profile,
            "PHASE23_MODEL_ID": model_id,
            "PHASE23_REVISION": str(revision or ""),
        },
        dry_run=args.dry_run,
        status="planned",
    )


def execute_plan(plan: QualityProfilePlan, *, timeout_s: float) -> QualityProfilePlan:
    started = time.perf_counter()
    completed = subprocess.run(  # nosec B603
        plan.command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    result = {
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-6000:],
        "stderr": completed.stderr[-6000:],
        "duration_ms": (time.perf_counter() - started) * 1000,
    }
    return plan.model_copy(update={"status": "complete" if completed.returncode == 0 else "failed", "result": result})


def write_plan(plan: QualityProfilePlan, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{plan.run_id}.json"
    latest_path = output_dir / "quality-profile-latest.json"
    md_path = output_dir / f"{plan.run_id}.md"
    json_path.write_text(json.dumps(plan.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(plan.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Phase 23 Quality Profile",
        "",
        f"- Run ID: `{plan.run_id}`",
        f"- Profile: `{plan.profile_name}`",
        f"- Model: `{plan.model_id}`",
        f"- Endpoint: `{plan.endpoint}`",
        f"- Status: `{plan.status}`",
        "",
        "```powershell",
        " ".join(plan.command),
        "```",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or run Phase 23 model quality profile.")
    parser.add_argument("--profile", default="qwen2.5-coder-7b")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase23")
    parser.add_argument("--max-tasks", type=int, default=5)
    parser.add_argument("--per-category", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=3600.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = build_plan(args)
    if args.execute:
        plan = execute_plan(plan, timeout_s=args.timeout_s)
    json_path, md_path = write_plan(plan, args.output_dir)
    print(json.dumps({"run_id": plan.run_id, "status": plan.status, "profile": plan.profile_name, "json": str(json_path), "markdown": str(md_path)}, indent=2))


if __name__ == "__main__":
    main()
