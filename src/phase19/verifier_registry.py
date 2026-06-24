#!/usr/bin/env python
"""Unified verifier registry for defensive coding and security workflows."""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import re
import subprocess  # nosec B404
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.memory_engine import iter_source_files, safe_workspace
from src.phase11.security_engine import SecurityReasoningEngine
from src.phase16.tool_adapters import CodeQLTool, SemgrepTool, find_tool

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class VerificationFinding(StrictModel):
    source: str
    severity: str
    title: str
    file_path: str | None = None
    line: int | None = None
    cwe: str | None = None
    evidence: str = ""
    recommendation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationCheck(StrictModel):
    name: str
    status: str
    score: float = Field(ge=0.0, le=1.0)
    summary: str
    findings: list[VerificationFinding] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class VerificationReport(StrictModel):
    run_id: str
    workspace: str
    status: str
    score: float = Field(ge=0.0, le=100.0)
    checks: list[VerificationCheck]
    findings: list[VerificationFinding]
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)
    duration_ms: float = 0.0
    artifacts: dict[str, str] = Field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        counts = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return counts


@dataclass(frozen=True)
class VerifierPolicy:
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
    exclude_patterns: tuple[str, ...] = (
        "artifacts/*",
        "data/*",
        "docs/*",
        "README.md",
        "src/phase12/capability_gauntlet.py",
        "src/phase9/security_suite.py",
        "src/phase10/bootstrap_mvp.py",
        "src/phase11/smoke_test.py",
        "src/phase13/backend_quality_harness.py",
        "src/phase16/critic_verifier.py",
        "src/phase30/expanded_benchmark_suite.py",
        "src/phase41/regression_pack.py",
        "**/__pycache__/*",
        "*.pyc",
    )


def excluded(relative: str, policy: VerifierPolicy) -> bool:
    normalized = relative.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in policy.exclude_patterns)


def status_from_findings(findings: list[VerificationFinding], *, fail_on_medium: bool) -> str:
    severities = {finding.severity.lower() for finding in findings}
    if severities.intersection({"critical", "high", "error"}):
        return FAIL
    if fail_on_medium and "medium" in severities:
        return FAIL
    if severities:
        return WARN
    return OK


def score_from_status(status: str) -> float:
    if status == OK:
        return 1.0
    if status == WARN:
        return 0.65
    if status == SKIP:
        return 0.8
    return 0.0


def source_files_text(workspace: Path, *, max_files: int, policy: VerifierPolicy) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in iter_source_files(workspace, max_files=max_files):
        relative = path.relative_to(workspace).as_posix()
        if excluded(relative, policy):
            continue
        try:
            files[relative] = path.read_text(encoding="utf-8", errors="replace")[:250_000]
        except OSError:
            continue
    return files


class RuleSecurityVerifier:
    def __init__(self) -> None:
        self.security = SecurityReasoningEngine()

    async def run(self, workspace: Path, policy: VerifierPolicy) -> VerificationCheck:
        started = time.perf_counter()
        files = source_files_text(workspace, max_files=policy.max_files, policy=policy)
        reviews = [self.security.analyze_text(text, target=relative) for relative, text in files.items()]
        raw_findings = [finding for review in reviews for finding in review.findings]
        findings = [
            VerificationFinding(
                source="rule_security",
                severity=item.severity,
                title=item.title,
                file_path=item.file_path,
                line=item.line,
                cwe=item.cwe,
                evidence=item.evidence,
                recommendation=item.recommendation,
                metadata={"finding_id": item.finding_id, "confidence": item.confidence},
            )
            for item in raw_findings
        ]
        status = status_from_findings(findings, fail_on_medium=policy.fail_on_medium)
        score = 1.0 if not findings else max(0.0, 1.0 - (len(findings) * 0.08))
        return VerificationCheck(
            name="rule_security",
            status=status,
            score=score if status != FAIL else min(score, 0.5),
            summary=f"rule security findings={len(findings)} across {len(files)} files",
            findings=findings,
            data={"file_count": len(files)},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class SecretScanVerifier:
    PATTERNS = [
        ("high", "Possible AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
        ("high", "Possible private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")),
        ("medium", "Possible generic API token", re.compile(r"(?i)\b(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]")),
        ("medium", "Possible hardcoded password", re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]")),
    ]

    async def run(self, workspace: Path, policy: VerifierPolicy) -> VerificationCheck:
        started = time.perf_counter()
        findings: list[VerificationFinding] = []
        for relative, text in source_files_text(workspace, max_files=policy.max_files, policy=policy).items():
            for line_no, line in enumerate(text.splitlines(), start=1):
                for severity, title, pattern in self.PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            VerificationFinding(
                                source="secret_scan",
                                severity=severity,
                                title=title,
                                file_path=relative,
                                line=line_no,
                                evidence=line[:300],
                                recommendation="Move secrets to environment variables or a managed secret store and rotate exposed values.",
                            )
                        )
        status = status_from_findings(findings, fail_on_medium=policy.fail_on_medium)
        return VerificationCheck(
            name="secret_scan",
            status=status,
            score=score_from_status(status),
            summary=f"secret findings={len(findings)}",
            findings=findings[:100],
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class SemgrepVerifier:
    async def run(self, workspace: Path, policy: VerifierPolicy) -> VerificationCheck:
        started = time.perf_counter()
        tool = SemgrepTool(workspace)
        result = await tool.scan(config=policy.semgrep_config)
        payload = result.data.get("semgrep") if isinstance(result.data.get("semgrep"), dict) else {}
        raw_results = payload.get("results") if isinstance(payload, dict) else []
        findings: list[VerificationFinding] = []
        for item in raw_results or []:
            extra = item.get("extra") or {}
            metadata = extra.get("metadata") or {}
            start = item.get("start") or {}
            severity = str(extra.get("severity") or metadata.get("severity") or "medium").lower()
            cwe = None
            raw_cwe = metadata.get("cwe")
            if isinstance(raw_cwe, list) and raw_cwe:
                cwe = str(raw_cwe[0])
            elif isinstance(raw_cwe, str):
                cwe = raw_cwe
            findings.append(
                VerificationFinding(
                    source="semgrep",
                    severity=severity,
                    title=str(extra.get("message") or item.get("check_id") or "Semgrep finding"),
                    file_path=str(item.get("path") or ""),
                    line=start.get("line") if isinstance(start.get("line"), int) else None,
                    cwe=cwe,
                    evidence=str(extra.get("lines") or "")[:500],
                    recommendation="Review Semgrep finding and apply a defensive patch if it is reachable.",
                    metadata={"check_id": item.get("check_id"), "raw_severity": severity},
                )
            )
        if not result.ok and not findings:
            return VerificationCheck(
                name="semgrep",
                status=WARN,
                score=0.5,
                summary=f"semgrep unavailable or failed: {result.summary}",
                data={"stderr": result.stderr[-2000:], "stdout": result.stdout[-2000:]},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        status = status_from_findings(findings, fail_on_medium=policy.fail_on_medium)
        return VerificationCheck(
            name="semgrep",
            status=status,
            score=score_from_status(status),
            summary=f"semgrep findings={len(findings)}",
            findings=findings[:200],
            data={"exit_code": result.data.get("exit_code")},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class CodeQLVerifier:
    async def run(self, workspace: Path, policy: VerifierPolicy) -> VerificationCheck:
        started = time.perf_counter()
        executable = find_tool("codeql", "tools", "codeql", "codeql.exe", env_var="PHASE16_CODEQL")
        if not executable:
            return VerificationCheck(
                name="codeql",
                status=SKIP,
                score=0.8,
                summary="CodeQL CLI not installed.",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        db_root = ROOT / "artifacts" / "phase19" / "codeql-db"
        db_root.mkdir(parents=True, exist_ok=True)
        db_path = db_root / f"{workspace.name}-{policy.codeql_language}"
        if not db_path.exists():
            create = await asyncio.to_thread(
                self._run_command,
                [
                    executable,
                    "database",
                    "create",
                    str(db_path),
                    f"--language={policy.codeql_language}",
                    "--source-root",
                    str(workspace),
                    "--overwrite",
                ],
                workspace,
                300.0,
            )
            if create["exit_code"] != 0:
                return VerificationCheck(
                    name="codeql",
                    status=WARN,
                    score=0.5,
                    summary="CodeQL database creation failed.",
                    data=create,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
        sarif_path = ROOT / "artifacts" / "phase19" / f"codeql-{workspace.name}-{int(time.time())}.sarif"
        analyze = await asyncio.to_thread(
            self._run_command,
            [
                executable,
                "database",
                "analyze",
                str(db_path),
                policy.codeql_suite,
                "--format=sarif-latest",
                "--output",
                str(sarif_path),
            ],
            workspace,
            420.0,
        )
        if analyze["exit_code"] != 0:
            return VerificationCheck(
                name="codeql",
                status=WARN,
                score=0.5,
                summary="CodeQL analyze failed.",
                data=analyze,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        findings = self._parse_sarif(sarif_path)
        status = status_from_findings(findings, fail_on_medium=policy.fail_on_medium)
        return VerificationCheck(
            name="codeql",
            status=status,
            score=score_from_status(status),
            summary=f"codeql findings={len(findings)}",
            findings=findings[:200],
            data={"sarif": str(sarif_path)},
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def _run_command(self, argv: list[str], cwd: Path, timeout_s: float) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            completed = subprocess.run(  # nosec B603
                argv,
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            return {
                "argv": argv,
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "duration_ms": (time.perf_counter() - started) * 1000,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "argv": argv,
                "exit_code": None,
                "stdout": exc.stdout if isinstance(exc.stdout, str) else "",
                "stderr": exc.stderr if isinstance(exc.stderr, str) else "",
                "timed_out": True,
                "duration_ms": (time.perf_counter() - started) * 1000,
            }

    def _parse_sarif(self, path: Path) -> list[VerificationFinding]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        findings: list[VerificationFinding] = []
        for run in payload.get("runs") or []:
            rules = {
                rule.get("id"): rule
                for tool in [run.get("tool") or {}]
                for driver in [tool.get("driver") or {}]
                for rule in driver.get("rules") or []
            }
            for result in run.get("results") or []:
                rule_id = result.get("ruleId")
                rule = rules.get(rule_id) or {}
                locations = result.get("locations") or []
                physical = (((locations[0] if locations else {}).get("physicalLocation") or {}))
                region = physical.get("region") or {}
                artifact = physical.get("artifactLocation") or {}
                severity = str((rule.get("properties") or {}).get("problem.severity") or "medium").lower()
                findings.append(
                    VerificationFinding(
                        source="codeql",
                        severity=severity,
                        title=str((result.get("message") or {}).get("text") or rule_id or "CodeQL finding"),
                        file_path=artifact.get("uri"),
                        line=region.get("startLine") if isinstance(region.get("startLine"), int) else None,
                        evidence=str((result.get("message") or {}).get("text") or "")[:500],
                        recommendation="Review CodeQL alert and patch the vulnerable data/control flow defensively.",
                        metadata={"rule_id": rule_id},
                    )
                )
        return findings


class SandboxVerifier:
    async def run(self, workspace: Path, policy: VerifierPolicy) -> VerificationCheck:
        started = time.perf_counter()
        try:
            from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile
        except ImportError as exc:
            return VerificationCheck(
                name="sandbox_tests",
                status=SKIP,
                score=0.8,
                summary=f"sandbox unavailable: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        files = [
            SandboxFile(path=relative, content=content)
            for relative, content in source_files_text(workspace, max_files=policy.max_files, policy=policy).items()
        ]
        if not files:
            return VerificationCheck(name="sandbox_tests", status=SKIP, score=0.8, summary="No source files to test.")
        request = ExecutionRequest(
            files=files,
            compile_command=None,
            run_command=policy.sandbox_command,
            image="python:3.12-slim",
            request_id="phase19-verifier-sandbox",
            pull_missing_image=False,
        )
        try:
            async with SandboxEngine(pool_size=1) as engine:
                result = await engine.run(request)
            status = OK if result.ok else FAIL
            return VerificationCheck(
                name="sandbox_tests",
                status=status,
                score=1.0 if result.ok else 0.0,
                summary=f"exit_code={result.exit_code} timeout={result.timeout} flag={result.flag}",
                data={
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                    "exit_code": result.exit_code,
                    "timeout": result.timeout,
                    "metrics": result.metrics.__dict__,
                },
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return VerificationCheck(
                name="sandbox_tests",
                status=FAIL,
                score=0.0,
                summary=f"sandbox execution failed: {type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000,
            )


class MultiLanguageSecurityVerifier:
    async def run(self, workspace: Path, policy: VerifierPolicy) -> VerificationCheck:
        started = time.perf_counter()
        try:
            from src.phase38.multilang_security import MultiLanguageSecurityEngine
        except ImportError as exc:
            return VerificationCheck(
                name="multilang_security",
                status=SKIP,
                score=0.8,
                summary=f"Phase 38 unavailable: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        report = await asyncio.to_thread(
            MultiLanguageSecurityEngine().analyze_workspace,
            workspace,
            max_files=policy.max_files,
            include_fixtures=False,
        )
        findings: list[VerificationFinding] = []
        for item in report.findings:
            findings.append(
                VerificationFinding(
                    source="multilang_security",
                    severity=str(item.get("severity") or "medium"),
                    title=str(item.get("title") or "Multi-language security finding"),
                    file_path=item.get("file_path") if isinstance(item.get("file_path"), str) else None,
                    line=item.get("line") if isinstance(item.get("line"), int) else None,
                    cwe=item.get("cwe") if isinstance(item.get("cwe"), str) else None,
                    evidence=str(item.get("evidence") or "")[:500],
                    recommendation=str(item.get("recommendation") or "Review and patch defensively."),
                    metadata={"finding_id": item.get("finding_id"), "confidence": item.get("confidence")},
                )
            )
        status = status_from_findings(findings, fail_on_medium=policy.fail_on_medium)
        return VerificationCheck(
            name="multilang_security",
            status=status,
            score=report.score if status != FAIL else min(report.score, 0.5),
            summary=f"phase38 status={report.status} findings={len(findings)} languages={report.languages}",
            findings=findings[:200],
            data={"languages": report.languages, "phase38_status": report.status},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class VerifierRegistry:
    def __init__(self, policy: VerifierPolicy | None = None) -> None:
        self.policy = policy or VerifierPolicy()

    async def run(self, workspace: str | Path, *, run_id: str | None = None) -> VerificationReport:
        started = time.time()
        root = safe_workspace(workspace)
        checks: list[VerificationCheck] = []
        if self.policy.run_rule_security:
            checks.append(await RuleSecurityVerifier().run(root, self.policy))
        if self.policy.run_secret_scan:
            checks.append(await SecretScanVerifier().run(root, self.policy))
        if self.policy.run_multilang_security:
            checks.append(await MultiLanguageSecurityVerifier().run(root, self.policy))
        if self.policy.run_semgrep:
            checks.append(await SemgrepVerifier().run(root, self.policy))
        if self.policy.run_codeql:
            checks.append(await CodeQLVerifier().run(root, self.policy))
        if self.policy.run_sandbox:
            checks.append(await SandboxVerifier().run(root, self.policy))
        findings = [finding for check in checks for finding in check.findings]
        hard_fail = any(check.status == FAIL for check in checks)
        status = FAIL if hard_fail else WARN if any(check.status == WARN for check in checks) else OK
        score = round((sum(check.score for check in checks) / max(1, len(checks))) * 100, 2)
        recommendation = self._recommend(checks, findings)
        return VerificationReport(
            run_id=run_id or f"phase19-{int(started)}",
            workspace=str(root),
            status=status,
            score=score,
            checks=checks,
            findings=findings,
            recommendation=recommendation,
            created_at_unix=started,
            duration_ms=(time.time() - started) * 1000,
        )

    def _recommend(self, checks: list[VerificationCheck], findings: list[VerificationFinding]) -> str:
        if any(finding.severity.lower() in {"critical", "high"} for finding in findings):
            return "Block merge/deployment until high-severity findings are patched and verifier is rerun."
        if any(check.status == FAIL for check in checks):
            return "Fix failed verifier checks, then rerun sandbox/static gates before accepting the patch."
        if any(check.status == WARN for check in checks):
            return "Review warning-level verifier output and promote confirmed cases into regression tests."
        return "Verifier registry passed. Keep artifacts as baseline telemetry."


def write_report(report: VerificationReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    latest_path = output_dir / "verifier-latest.json"
    payload = report.model_dump()
    payload["artifacts"] = {"json": str(json_path), "markdown": str(md_path)}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Phase 19 Verifier Report",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Status: `{report.status}`",
        f"- Score: `{report.score:.2f}`",
        f"- Summary: `{report.summary()}`",
        f"- Recommendation: {report.recommendation}",
        "",
        "| Check | Status | Score | Summary |",
        "| --- | --- | ---: | --- |",
    ]
    for check in report.checks:
        lines.append(f"| {check.name} | {check.status} | {check.score:.2f} | {check.summary.replace('|', '/')} |")
    lines.extend(["", "## Findings", ""])
    if report.findings:
        for finding in report.findings[:80]:
            loc = f"{finding.file_path}:{finding.line}" if finding.file_path else "workspace"
            lines.append(f"- `{finding.severity}` {finding.source} {loc}: {finding.title}")
    else:
        lines.append("- No findings.")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 19 unified verifier registry.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--run-id", default=f"phase19-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase19")
    parser.add_argument("--run-semgrep", action="store_true")
    parser.add_argument("--run-multilang-security", action="store_true")
    parser.add_argument("--run-codeql", action="store_true")
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--sandbox-command", default="python3 -m pytest -q")
    parser.add_argument("--semgrep-config", default="auto")
    parser.add_argument("--codeql-language", default="python")
    parser.add_argument("--codeql-suite", default="codeql/python-queries")
    parser.add_argument("--allow-medium", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    policy = VerifierPolicy(
        run_semgrep=args.run_semgrep,
        run_multilang_security=args.run_multilang_security,
        run_codeql=args.run_codeql,
        run_sandbox=args.run_sandbox,
        semgrep_config=args.semgrep_config,
        codeql_language=args.codeql_language,
        codeql_suite=args.codeql_suite,
        sandbox_command=args.sandbox_command,
        fail_on_medium=not args.allow_medium,
    )
    report = await VerifierRegistry(policy).run(args.workspace, run_id=args.run_id)
    json_path, md_path = write_report(report, args.output_dir)
    if args.json:
        print(json.dumps({"run_id": report.run_id, "status": report.status, "score": report.score, "summary": report.summary(), "json": str(json_path)}, indent=2))
    else:
        print(f"{report.run_id}: {report.status} score={report.score:.2f} -> {json_path}")
    return 1 if args.strict and report.status == FAIL else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
