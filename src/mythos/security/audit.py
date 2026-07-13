"""Production security audit for Mythos source and deployment assets."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.deployment.k8s_validate import validate_manifests
from src.mythos.shared.schemas import StrictModel


SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{12,}['\"]")
SSRF_PATTERN = re.compile(r"(?i)(requests\.(get|post|put|delete)|httpx\.(get|post|put|delete)|urlopen)\s*\(")
PATH_TRAVERSAL_PATTERN = re.compile(r"(?i)(open|Path)\s*\([^)]*(request|args|params|user_input|filename)")
DANGEROUS_SUBPROCESS_PATTERN = re.compile(r"(?i)(shell\s*=\s*True|os\.system|subprocess\.(call|run|Popen)\([^)]*\+)")


class SecurityFinding(StrictModel):
    severity: str
    check: str
    file: str
    line: int = 0
    message: str
    evidence: str = ""


class SecurityAuditReport(StrictModel):
    status: str
    root: str
    findings: list[SecurityFinding]
    dependency_warnings: list[str]
    external_scanners: dict[str, Any] = Field(default_factory=dict)
    bandit: dict[str, Any] | None = None
    k8s: dict[str, Any] | None = None
    created_at_unix: float = Field(default_factory=time.time)

    def write(self, output_dir: str | Path) -> Path:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "security_audit_report.json"
        target.write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        write_markdown(self, root / "security_audit_report.md")
        return target


def _iter_source_files(root: Path) -> list[Path]:
    suffixes = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".yaml", ".yml", ".toml", ".env", ".txt"}
    excluded = {".git", "__pycache__", "artifacts", ".venv", "node_modules", "tests"}
    excluded_paths = {
        Path("src/mythos/evaluation/benchmarks.py"),
        Path("tools/codeql/python/tools/imp.py"),
    }
    paths = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in excluded for part in path.parts):
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path
        if relative.as_posix() in {item.as_posix() for item in excluded_paths}:
            continue
        paths.append(path)
    return paths


def _scan_patterns(root: Path) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for path in _iter_source_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if "replace-with" in stripped or "nosec" in stripped or "pragma: allowlist secret" in stripped:
                continue
            checks = [
                ("secret_pattern", SECRET_PATTERN, "fail", "possible hardcoded secret"),
                ("ssrf_sink", SSRF_PATTERN, "warn", "network request sink requires allowlist validation"),
                ("path_traversal_sink", PATH_TRAVERSAL_PATTERN, "warn", "file path sink may need canonical path validation"),
                ("dangerous_process", DANGEROUS_SUBPROCESS_PATTERN, "warn", "process execution sink requires strict argument handling"),
            ]
            for check, pattern, severity, message in checks:
                if pattern.search(stripped):
                    findings.append(
                        SecurityFinding(
                            severity=severity,
                            check=check,
                            file=str(path),
                            line=line_number,
                            message=message,
                            evidence=stripped[:240],
                        )
                    )
    return findings


def _dependency_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    for filename in ["requirements.txt", "requirements-local-dev.txt", "requirements-linux-gpu.txt"]:
        path = root / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if all(operator not in stripped for operator in ["==", ">=", "~=", "<="]):
                warnings.append(f"{filename}: dependency is not version bounded: {stripped}")
    return warnings


def _run_bandit(root: Path) -> dict[str, Any] | None:
    if shutil.which("bandit") is None:
        return {"status": "skipped", "reason": "bandit executable is not installed"}
    command = ["python", "-m", "bandit", "-q", "-r", "src/mythos", "-f", "json"]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)  # nosec B603
    if completed.returncode not in {0, 1}:
        return {"status": "skipped", "reason": completed.stderr[-1000:] or completed.stdout[-1000:]}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"status": "failed", "reason": "bandit returned invalid JSON", "stderr": completed.stderr[-1000:]}
    issue_count = len(payload.get("results", []))
    return {"status": "passed" if issue_count == 0 else "failed", "issue_count": issue_count, "metrics": payload.get("metrics", {})}


def _run_semgrep(root: Path) -> dict[str, Any]:
    if shutil.which("semgrep") is None:
        return {"status": "skipped", "reason": "semgrep executable is not installed"}
    command = ["semgrep", "scan", "--config", "auto", "--json", "src/mythos"]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)  # nosec B603
    if completed.returncode not in {0, 1}:
        return {"status": "failed", "reason": completed.stderr[-2000:] or completed.stdout[-2000:]}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"status": "failed", "reason": "semgrep returned invalid JSON", "stderr": completed.stderr[-1000:]}
    findings = payload.get("results", [])
    return {"status": "passed" if not findings else "failed", "issue_count": len(findings)}


def _run_codeql(root: Path) -> dict[str, Any]:
    if shutil.which("codeql") is None:
        return {"status": "skipped", "reason": "codeql executable is not installed"}
    database = root / "artifacts" / "mythos" / "codeql-db"
    if not database.exists():
        return {"status": "skipped", "reason": "CodeQL database missing; create it before production audit", "database": str(database)}
    output = root / "artifacts" / "mythos" / "codeql-results.sarif"
    command = ["codeql", "database", "analyze", str(database), "--format=sarifv2.1.0", f"--output={output}", "--rerun"]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)  # nosec B603
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "output": str(output),
        "stderr": completed.stderr[-2000:],
    }


def _run_pip_audit(root: Path) -> dict[str, Any]:
    if shutil.which("pip-audit") is None:
        return {"status": "skipped", "reason": "pip-audit executable is not installed"}
    command = ["pip-audit", "--format", "json"]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)  # nosec B603
    if completed.returncode not in {0, 1}:
        return {"status": "failed", "reason": completed.stderr[-2000:] or completed.stdout[-2000:]}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {"status": "failed", "reason": "pip-audit returned invalid JSON", "stderr": completed.stderr[-1000:]}
    vulnerabilities = payload.get("vulnerabilities", [])
    return {"status": "passed" if not vulnerabilities else "failed", "vulnerability_count": len(vulnerabilities)}


def run_security_audit(
    *,
    root: str | Path = ".",
    output_dir: str | Path | None = None,
    run_bandit: bool = True,
    validate_k8s: bool = True,
    run_semgrep: bool = True,
    run_codeql: bool = True,
    run_pip_audit: bool = True,
    strict_external_tools: bool = False,
) -> SecurityAuditReport:
    project_root = Path(root).resolve()
    findings = _scan_patterns(project_root)
    dependency_warnings = _dependency_warnings(project_root)
    bandit_report = _run_bandit(project_root) if run_bandit else None
    external_scanners: dict[str, Any] = {}
    if run_semgrep:
        external_scanners["semgrep"] = _run_semgrep(project_root)
    if run_codeql:
        external_scanners["codeql"] = _run_codeql(project_root)
    if run_pip_audit:
        external_scanners["pip_audit"] = _run_pip_audit(project_root)
    k8s_report = None
    if validate_k8s:
        manifests = sorted((project_root / "deploy" / "k8s").glob("*.yaml"))
        if manifests:
            k8s_report = validate_manifests(manifests).model_dump()
    failed = any(item.severity == "fail" for item in findings)
    failed = failed or bool(dependency_warnings)
    failed = failed or bool(bandit_report and bandit_report.get("status") == "failed")
    failed = failed or any(report.get("status") == "failed" for report in external_scanners.values())
    if strict_external_tools:
        failed = failed or bool(bandit_report and bandit_report.get("status") == "skipped")
        failed = failed or any(report.get("status") == "skipped" for report in external_scanners.values())
    failed = failed or bool(k8s_report and k8s_report.get("status") == "failed")
    report = SecurityAuditReport(
        status="failed" if failed else "passed",
        root=str(project_root),
        findings=findings,
        dependency_warnings=dependency_warnings,
        external_scanners=external_scanners,
        bandit=bandit_report,
        k8s=k8s_report,
    )
    if output_dir:
        report.write(output_dir)
    return report


def write_markdown(report: SecurityAuditReport, path: str | Path) -> Path:
    target = Path(path)
    lines = [
        "# Mythos Security Audit Report",
        "",
        f"- status: {report.status}",
        f"- findings: {len(report.findings)}",
        f"- dependency_warnings: {len(report.dependency_warnings)}",
        "",
        "| severity | check | file | line | message |",
        "|---|---|---|---:|---|",
    ]
    for finding in report.findings:
        lines.append(f"| {finding.severity} | {finding.check} | {finding.file} | {finding.line} | {finding.message} |")
    if report.dependency_warnings:
        lines.extend(["", "## Dependency Warnings", ""])
        for warning in report.dependency_warnings:
            lines.append(f"- {warning}")
    if report.external_scanners:
        lines.extend(["", "## External Scanners", "", "| scanner | status | detail |", "|---|---|---|"])
        for name, payload in report.external_scanners.items():
            detail = payload.get("reason") or payload.get("issue_count") or payload.get("vulnerability_count") or ""
            lines.append(f"| {name} | {payload.get('status')} | {detail} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos production security audit.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="artifacts/mythos/security-audit")
    parser.add_argument("--no-bandit", action="store_true")
    parser.add_argument("--no-k8s", action="store_true")
    parser.add_argument("--no-semgrep", action="store_true")
    parser.add_argument("--no-codeql", action="store_true")
    parser.add_argument("--no-pip-audit", action="store_true")
    parser.add_argument("--strict-external-tools", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = run_security_audit(
        root=args.root,
        output_dir=args.output_dir,
        run_bandit=not args.no_bandit,
        validate_k8s=not args.no_k8s,
        run_semgrep=not args.no_semgrep,
        run_codeql=not args.no_codeql,
        run_pip_audit=not args.no_pip_audit,
        strict_external_tools=args.strict_external_tools,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    if report.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
