#!/usr/bin/env python
"""Security reasoning primitives for vulnerability finding and patch review."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from src.phase11.schemas import SecurityFinding, SecurityReview, ToolResult


@dataclass(frozen=True)
class SecurityRule:
    rule_id: str
    title: str
    severity: str
    pattern: re.Pattern[str]
    cwe: str
    recommendation: str


RULES = [
    SecurityRule(
        "c-buffer-copy",
        "Potential unsafe C buffer copy",
        "high",
        re.compile(r"\b(strcpy|strcat|sprintf|gets)\s*\(", re.IGNORECASE),
        "CWE-120",
        "Use bounded APIs, validate destination sizes, and preserve null termination.",
    ),
    SecurityRule(
        "sql-string-format",
        "Potential SQL injection through string-built query",
        "high",
        re.compile(
            r"\b(execute|executemany|query|raw)\s*\([^\n]*(?:f['\"]|SELECT|INSERT|UPDATE|DELETE)[^\n]*(?:\+|%|\.format\(|\{[A-Za-z_])",
            re.IGNORECASE,
        ),
        "CWE-89",
        "Use parameterized queries or prepared statements.",
    ),
    SecurityRule(
        "command-shell-true",
        "Potential command injection through shell execution",
        "high",
        re.compile(
            r"(subprocess\.(run|Popen|call)\s*\([^\n)]*shell\s*=\s*True|os\.system\s*\([^\n]*(?:\+|f['\"]|\{[A-Za-z_]))",
            re.IGNORECASE,
        ),
        "CWE-78",
        "Use shell=False with an argument list and allowlist user-controlled values.",
    ),
    SecurityRule(
        "weak-crypto",
        "Weak cryptographic primitive",
        "medium",
        re.compile(r"\b(hashlib\.(md5|sha1)|(md5|sha1)\s*\(|DES\.new|ARC4\.new)\b", re.IGNORECASE),
        "CWE-327",
        "Use modern primitives such as SHA-256/HMAC, Argon2id, AES-GCM, or libsodium.",
    ),
    SecurityRule(
        "path-traversal",
        "Potential path traversal",
        "medium",
        re.compile(r"open\s*\([^\n)]*(\+|join\([^\n)]*(user|request|input))", re.IGNORECASE),
        "CWE-22",
        "Resolve paths against a trusted base directory and reject escaping paths.",
    ),
    SecurityRule(
        "unsafe-deserialization",
        "Unsafe deserialization",
        "high",
        re.compile(r"\b(pickle\.loads?|yaml\.load)\s*\(", re.IGNORECASE),
        "CWE-502",
        "Use safe parsers and never deserialize untrusted bytes into executable objects.",
    ),
]


def finding_id(rule_id: str, target: str, line: int, evidence: str) -> str:
    raw = f"{rule_id}:{target}:{line}:{evidence}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


class SecurityReasoningEngine:
    def analyze_text(self, text: str, *, target: str = "<memory>") -> SecurityReview:
        findings: list[SecurityFinding] = []
        lines = text.splitlines()
        for rule in RULES:
            for match in rule.pattern.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                evidence = lines[line_no - 1].strip()[:240] if 0 < line_no <= len(lines) else match.group(0)[:240]
                findings.append(
                    SecurityFinding(
                        finding_id=finding_id(rule.rule_id, target, line_no, evidence),
                        title=rule.title,
                        severity=rule.severity,
                        cwe=rule.cwe,
                        file_path=target,
                        line=line_no,
                        evidence=evidence,
                        recommendation=rule.recommendation,
                        confidence=0.85 if rule.severity == "high" else 0.75,
                    )
                )
        score = self._score(findings)
        return SecurityReview(target=target, findings=findings, score=score, summary=self._summary(findings, score))

    def analyze_workspace(self, workspace: str | Path, *, max_files: int = 1000, include_fixtures: bool = False) -> SecurityReview:
        root = Path(workspace).resolve()
        all_findings: list[SecurityFinding] = []
        count = 0
        for path in root.rglob("*"):
            if count >= max_files:
                break
            if path.is_dir() or any(part in {".git", ".venv", "__pycache__", "node_modules"} for part in path.parts):
                continue
            if path.suffix.lower() not in {".py", ".c", ".h", ".cpp", ".hpp", ".js", ".ts", ".sh"}:
                continue
            relative = path.relative_to(root).as_posix()
            if not include_fixtures and self._is_fixture_path(relative):
                continue
            count += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            review = self.analyze_text(text, target=relative)
            all_findings.extend(review.findings)
        score = self._score(all_findings)
        return SecurityReview(target=str(root), findings=all_findings, score=score, summary=self._summary(all_findings, score))

    def compare_patch_security(self, before: str, after: str, *, target: str = "<patch>") -> SecurityReview:
        before_review = self.analyze_text(before, target=f"{target}:before")
        after_review = self.analyze_text(after, target=f"{target}:after")
        new_count = max(0, len(after_review.findings) - len(before_review.findings))
        summary = (
            f"Before findings={len(before_review.findings)}; after findings={len(after_review.findings)}; "
            f"new findings={new_count}."
        )
        score = max(0.0, after_review.score - new_count * 0.2)
        return SecurityReview(target=target, findings=after_review.findings, score=score, summary=summary)

    def _score(self, findings: list[SecurityFinding]) -> float:
        penalty = 0.0
        for finding in findings:
            penalty += {"high": 0.25, "medium": 0.12, "low": 0.05}.get(finding.severity, 0.08)
        return max(0.0, min(1.0, 1.0 - penalty))

    def _summary(self, findings: list[SecurityFinding], score: float) -> str:
        if not findings:
            return "No obvious rule-based security findings detected."
        high = sum(1 for finding in findings if finding.severity == "high")
        medium = sum(1 for finding in findings if finding.severity == "medium")
        low = sum(1 for finding in findings if finding.severity == "low")
        return f"Security score={score:.2f}; findings high={high}, medium={medium}, low={low}."

    def _is_fixture_path(self, relative_path: str) -> bool:
        lower = relative_path.lower()
        fixture_markers = [
            "test",
            "smoke",
            "sample",
            "benchmark",
            "fixture",
            "bootstrap_mvp.py",
            "security_suite.py",
        ]
        return any(marker in lower for marker in fixture_markers)

    async def verify_python_patch_in_sandbox(
        self,
        files: dict[str, str],
        *,
        command: str = "python3 /workspace/main.py",
        image: str = "python:3.12-slim",
    ) -> ToolResult:
        """Run supplied files in the hardened Phase 2 sandbox and return telemetry."""

        try:
            from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile

            sandbox_files = [SandboxFile(path=path, content=content) for path, content in files.items()]
            request = ExecutionRequest(
                files=sandbox_files,
                compile_command=None,
                run_command=command,
                image=image,
                env={"PYTHONDONTWRITEBYTECODE": "1"},
                request_id="phase11-security-verification",
                pull_missing_image=False,
            )
            async with SandboxEngine(pool_size=1) as engine:
                result = await engine.run(request)
            return ToolResult(
                tool="security_sandbox_verify",
                ok=result.ok,
                summary=f"exit_code={result.exit_code} timeout={result.timeout} flag={result.flag}",
                stdout=result.stdout,
                stderr=result.stderr,
                data={
                    "exit_code": result.exit_code,
                    "timeout": result.timeout,
                    "flag": result.flag,
                    "metrics": result.metrics.__dict__,
                    "image": result.image,
                    "command": result.command,
                    "error": result.error,
                },
            )
        except Exception as exc:
            return ToolResult(
                tool="security_sandbox_verify",
                ok=False,
                summary=f"sandbox unavailable or failed: {type(exc).__name__}: {exc}",
                data={"image": image, "command": command},
            )
