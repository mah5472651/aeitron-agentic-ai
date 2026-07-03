"""Defensive security scanner wrappers for Mythos.

The wrappers are intentionally conservative: missing external CLIs produce a
structured skipped result instead of a false pass or crash. Production gates can
decide whether skipped tools are acceptable.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class SecurityScanResult(StrictModel):
    tool: str
    status: str
    finding_count: int = 0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float = 0.0
    reason: str = ""


class SecurityScanner:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def run_semgrep(self, *, timeout_ms: int = 120_000) -> SecurityScanResult:
        executable = shutil.which("semgrep")
        if executable is None:
            return SecurityScanResult(tool="semgrep", status="skipped", reason="semgrep CLI is not installed")
        started = time.perf_counter()
        command = [executable, "scan", "--config", "auto", "--json", "--quiet", "."]
        return self._run_json_scanner(
            tool="semgrep",
            command=command,
            timeout_ms=timeout_ms,
            finding_key="results",
            started=started,
        )

    def run_codeql(
        self,
        *,
        database: str | Path | None = None,
        suite: str = "security-and-quality",
        timeout_ms: int = 120_000,
    ) -> SecurityScanResult:
        executable = shutil.which("codeql")
        if executable is None:
            return SecurityScanResult(tool="codeql", status="skipped", reason="codeql CLI is not installed")
        if database is None:
            return SecurityScanResult(tool="codeql", status="skipped", reason="codeql database path was not provided")
        database_path = Path(database).resolve()
        if not database_path.exists():
            return SecurityScanResult(tool="codeql", status="skipped", reason=f"codeql database not found: {database_path}")
        started = time.perf_counter()
        output_path = self.workspace / ".mythos-codeql-results.sarif"
        command = [
            executable,
            "database",
            "analyze",
            str(database_path),
            suite,
            "--format=sarif-latest",
            f"--output={output_path}",
        ]
        try:
            completed = subprocess.run(  # nosec B603 - argv list, shell disabled.
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout_ms / 1000,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SecurityScanResult(
                tool="codeql",
                status="timeout",
                stdout=(exc.stdout if isinstance(exc.stdout, str) else "")[-20_000:],
                stderr=(exc.stderr if isinstance(exc.stderr, str) else "")[-20_000:],
                exit_code=None,
                duration_ms=(time.perf_counter() - started) * 1000,
                reason="codeql timed out",
            )
        findings = self._read_sarif_findings(output_path)
        return SecurityScanResult(
            tool="codeql",
            status="failed" if findings else ("passed" if completed.returncode == 0 else "failed"),
            finding_count=len(findings),
            findings=findings,
            stdout=completed.stdout[-20_000:],
            stderr=completed.stderr[-20_000:],
            exit_code=completed.returncode,
            duration_ms=(time.perf_counter() - started) * 1000,
            reason="findings detected" if findings else "no findings detected",
        )

    def _run_json_scanner(
        self,
        *,
        tool: str,
        command: list[str],
        timeout_ms: int,
        finding_key: str,
        started: float,
    ) -> SecurityScanResult:
        try:
            completed = subprocess.run(  # nosec B603 - argv list, shell disabled.
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout_ms / 1000,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SecurityScanResult(
                tool=tool,
                status="timeout",
                stdout=(exc.stdout if isinstance(exc.stdout, str) else "")[-20_000:],
                stderr=(exc.stderr if isinstance(exc.stderr, str) else "")[-20_000:],
                exit_code=None,
                duration_ms=(time.perf_counter() - started) * 1000,
                reason=f"{tool} timed out",
            )
        findings: list[dict[str, Any]] = []
        if completed.stdout.strip():
            try:
                payload = json.loads(completed.stdout)
                raw_findings = payload.get(finding_key, [])
                if isinstance(raw_findings, list):
                    findings = [self._compact_finding(item) for item in raw_findings]
            except json.JSONDecodeError:
                findings = []
        return SecurityScanResult(
            tool=tool,
            status="failed" if findings else ("passed" if completed.returncode == 0 else "failed"),
            finding_count=len(findings),
            findings=findings,
            stdout=completed.stdout[-20_000:],
            stderr=completed.stderr[-20_000:],
            exit_code=completed.returncode,
            duration_ms=(time.perf_counter() - started) * 1000,
            reason="findings detected" if findings else "no findings detected",
        )

    def _compact_finding(self, item: dict[str, Any]) -> dict[str, Any]:
        path = item.get("path") or item.get("extra", {}).get("path") or ""
        start = item.get("start") or {}
        extra = item.get("extra") or {}
        return {
            "rule_id": item.get("check_id") or item.get("rule_id") or "",
            "path": path,
            "line": start.get("line"),
            "message": extra.get("message") or item.get("message") or "",
            "severity": extra.get("severity") or "",
        }

    def _read_sarif_findings(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []
        findings: list[dict[str, Any]] = []
        for run in payload.get("runs", []):
            rules = {
                rule.get("id"): rule.get("shortDescription", {}).get("text", "")
                for rule in run.get("tool", {}).get("driver", {}).get("rules", [])
            }
            for result in run.get("results", []):
                location = (result.get("locations") or [{}])[0].get("physicalLocation", {})
                artifact = location.get("artifactLocation", {})
                region = location.get("region", {})
                rule_id = result.get("ruleId") or ""
                findings.append(
                    {
                        "rule_id": rule_id,
                        "path": artifact.get("uri", ""),
                        "line": region.get("startLine"),
                        "message": result.get("message", {}).get("text") or rules.get(rule_id, ""),
                        "severity": result.get("level", ""),
                    }
                )
        return findings
