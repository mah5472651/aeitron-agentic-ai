#!/usr/bin/env python
"""Policy-driven verifier execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase19.verifier_registry import VerifierPolicy, VerifierRegistry, VerificationReport, write_report as write_verifier_report


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PolicyProfile(StrictModel):
    name: str
    description: str
    run_rule_security: bool = True
    run_secret_scan: bool = True
    run_multilang_security: bool = False
    run_semgrep: bool = False
    run_codeql: bool = False
    run_sandbox: bool = False
    semgrep_config: str = "auto"
    codeql_language: str = "python"
    codeql_suite: str = "codeql/python-queries"
    sandbox_command: str = "python3 -m pytest -q"
    fail_on_medium: bool = True
    max_files: int = 600
    exclude_patterns: list[str] = Field(default_factory=list)

    def to_policy(self) -> VerifierPolicy:
        base = VerifierPolicy(
            run_rule_security=self.run_rule_security,
            run_secret_scan=self.run_secret_scan,
            run_multilang_security=self.run_multilang_security,
            run_semgrep=self.run_semgrep,
            run_codeql=self.run_codeql,
            run_sandbox=self.run_sandbox,
            semgrep_config=self.semgrep_config,
            codeql_language=self.codeql_language,
            codeql_suite=self.codeql_suite,
            sandbox_command=self.sandbox_command,
            fail_on_medium=self.fail_on_medium,
            max_files=self.max_files,
        )
        if self.exclude_patterns:
            return VerifierPolicy(**{**base.__dict__, "exclude_patterns": tuple(self.exclude_patterns)})
        return base


DEFAULT_PROFILES = {
    "fast": PolicyProfile(name="fast", description="Local fast verifier: rules + secrets, fixture-aware."),
    "security": PolicyProfile(
        name="security",
        description="Semgrep + Phase 38 multi-language security gate.",
        run_multilang_security=True,
        run_semgrep=True,
        fail_on_medium=False,
    ),
    "release": PolicyProfile(
        name="release",
        description="Strict release gate.",
        run_multilang_security=True,
        run_semgrep=True,
        run_sandbox=True,
        fail_on_medium=True,
    ),
    "codeql-python": PolicyProfile(name="codeql-python", description="CodeQL Python deep scan.", run_codeql=True, codeql_language="python"),
}


def write_default_policy(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"profiles": {name: profile.model_dump() for name, profile in DEFAULT_PROFILES.items()}}, indent=2), encoding="utf-8")
    return path


def load_profile(path: Path, name: str) -> PolicyProfile:
    if not path.exists():
        write_default_policy(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    profile = (payload.get("profiles") or {}).get(name)
    if not profile:
        raise ValueError(f"unknown verifier policy profile: {name}")
    return PolicyProfile.model_validate(profile)


async def run_policy(*, workspace: str, profile: PolicyProfile, run_id: str, output_dir: Path) -> VerificationReport:
    report = await VerifierRegistry(profile.to_policy()).run(workspace, run_id=run_id)
    write_verifier_report(report, output_dir)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 27 verifier policy.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--profile", default="fast")
    parser.add_argument("--policy-file", type=Path, default=ROOT / "config" / "verifier_policy.json")
    parser.add_argument("--run-id", default=f"phase27-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase27")
    parser.add_argument("--write-default", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    if args.write_default:
        path = write_default_policy(args.policy_file)
        print(json.dumps({"policy_file": str(path), "profiles": sorted(DEFAULT_PROFILES)}, indent=2))
        return 0
    profile = load_profile(args.policy_file, args.profile)
    report = await run_policy(workspace=args.workspace, profile=profile, run_id=args.run_id, output_dir=args.output_dir)
    print(json.dumps({"run_id": report.run_id, "profile": profile.name, "status": report.status, "score": report.score}, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
