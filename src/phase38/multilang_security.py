#!/usr/bin/env python
"""Phase 38 multi-language defensive security scanner.

Adds explicit Rust, Go, JavaScript/TypeScript, and Solidity coverage on top of
the earlier Python/C/C++ rules. This is intentionally static and defensive:
find, explain, patch direction, and verification routing only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.security_engine import SecurityReasoningEngine
from src.phase11.schemas import SecurityFinding, SecurityReview


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


@dataclass(frozen=True)
class LanguageRule:
    rule_id: str
    language: str
    title: str
    severity: str
    cwe: str
    pattern: re.Pattern[str]
    recommendation: str
    confidence: float = 0.82


LANGUAGE_EXTENSIONS = {
    ".rs": "rust",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".sol": "solidity",
}


MULTILANG_RULES = [
    LanguageRule(
        "rust-unsafe-block",
        "rust",
        "Rust unsafe block requires explicit boundary review",
        "medium",
        "CWE-242",
        re.compile(r"\bunsafe\s*\{", re.IGNORECASE),
        "Minimize unsafe scope, document invariants, add fuzz/regression tests, and prefer safe abstractions.",
        0.72,
    ),
    LanguageRule(
        "rust-command-spawn-user-input",
        "rust",
        "Rust command execution may include untrusted input",
        "high",
        "CWE-78",
        re.compile(r"\bCommand::new\s*\([^\n;]*(user|input|arg|request|cmd)", re.IGNORECASE),
        "Use allowlisted executable names and pass arguments separately without shell expansion.",
    ),
    LanguageRule(
        "rust-unwrap-input-path",
        "rust",
        "Rust panic path through unwrap/expect on external input",
        "medium",
        "CWE-248",
        re.compile(r"\b(read_to_string|File::open|env::var|parse::<[^>]+>)\s*\([^\n;]*\)\s*\.(unwrap|expect)\s*\(", re.IGNORECASE),
        "Return typed errors instead of panicking on user-controlled file, env, or parse operations.",
        0.76,
    ),
    LanguageRule(
        "go-command-exec",
        "go",
        "Go command execution may include untrusted input",
        "high",
        "CWE-78",
        re.compile(r"\bexec\.Command\s*\([^\n]*(user|input|request|cmd|arg)", re.IGNORECASE),
        "Allowlist command names and pass validated args; avoid shell wrappers.",
    ),
    LanguageRule(
        "go-sql-format",
        "go",
        "Go SQL query constructed with formatting or concatenation",
        "high",
        "CWE-89",
        re.compile(r"\b(db|tx)\.(Query|Exec|QueryRow)\s*\([^\n]*(fmt\.Sprintf|\+)", re.IGNORECASE),
        "Use parameterized placeholders and pass user values as arguments.",
    ),
    LanguageRule(
        "go-path-clean-missing",
        "go",
        "Go file path joins user input without containment check",
        "medium",
        "CWE-22",
        re.compile(r"\b(os\.Open|os\.ReadFile|http\.ServeFile)\s*\([^\n]*(user|input|request|r\.URL|path)", re.IGNORECASE),
        "Clean paths, resolve under a trusted root, and reject traversal outside the root.",
        0.76,
    ),
    LanguageRule(
        "js-child-process",
        "javascript",
        "JavaScript child_process execution risk",
        "high",
        "CWE-78",
        re.compile(r"\b(exec|execSync|spawn|spawnSync)\s*\([^\n]*(req\.|user|input|cmd|query|body)", re.IGNORECASE),
        "Use spawn with fixed executable and validated argument array; never concatenate shell commands.",
    ),
    LanguageRule(
        "js-sql-template",
        "javascript",
        "JavaScript SQL injection through template/string-built query",
        "high",
        "CWE-89",
        re.compile(r"\b(query|execute|raw)\s*\([^\n]*(`[^`]*(SELECT|INSERT|UPDATE|DELETE)|\+)[^\n]*(req\.|user|input|body|query)", re.IGNORECASE),
        "Use parameterized query APIs and separate SQL text from values.",
    ),
    LanguageRule(
        "js-prototype-pollution",
        "javascript",
        "Prototype pollution through dynamic key assignment",
        "high",
        "CWE-1321",
        re.compile(r"\[[^\]]*(req\.|body|query|input|key)[^\]]*\]\s*=", re.IGNORECASE),
        "Reject __proto__/prototype/constructor keys and assign into null-prototype objects.",
        0.78,
    ),
    LanguageRule(
        "solidity-tx-origin",
        "solidity",
        "Solidity authorization uses tx.origin",
        "high",
        "CWE-346",
        re.compile(r"\btx\.origin\b"),
        "Use msg.sender for authorization and add phishing-resistant access control tests.",
    ),
    LanguageRule(
        "solidity-reentrancy-call",
        "solidity",
        "Solidity external call before state-hardening may be reentrant",
        "high",
        "CWE-841",
        re.compile(r"\.call\s*\{\s*value\s*:\s*[^}]+\}\s*\(", re.IGNORECASE),
        "Apply checks-effects-interactions, use ReentrancyGuard, and update state before external calls.",
    ),
    LanguageRule(
        "solidity-selfdestruct",
        "solidity",
        "Solidity selfdestruct is dangerous and deprecated",
        "medium",
        "CWE-284",
        re.compile(r"\bselfdestruct\s*\(", re.IGNORECASE),
        "Remove selfdestruct or restrict lifecycle controls with explicit governance and tests.",
        0.78,
    ),
]


class MultiLanguageSecurityReport(StrictModel):
    run_id: str
    workspace: str
    languages: dict[str, int]
    findings: list[dict[str, Any]]
    score: float
    status: str
    patch_guidance: list[dict[str, Any]]
    safety_position: str
    created_at_unix: float = Field(default_factory=time.time)


def severity_penalty(severity: str) -> float:
    return {"high": 0.18, "medium": 0.09, "low": 0.04}.get(severity.lower(), 0.06)


def finding_from_rule(rule: LanguageRule, target: str, text: str, match: re.Match[str]) -> SecurityFinding:
    line = text[: match.start()].count("\n") + 1
    lines = text.splitlines()
    evidence = lines[line - 1].strip()[:240] if 0 < line <= len(lines) else match.group(0)[:240]
    return SecurityFinding(
        finding_id=f"{rule.rule_id}:{target}:{line}:{abs(hash(evidence)) & 0xFFFFFFFF:x}",
        title=rule.title,
        severity=rule.severity,
        cwe=rule.cwe,
        file_path=target,
        line=line,
        evidence=evidence,
        recommendation=rule.recommendation,
        confidence=rule.confidence,
    )


class MultiLanguageSecurityEngine:
    def __init__(self, *, include_base_rules: bool = True) -> None:
        self.include_base_rules = include_base_rules
        self.base_engine = SecurityReasoningEngine()

    def analyze_text(self, text: str, *, target: str, language: str | None = None) -> SecurityReview:
        detected = language or self.language_for_path(Path(target))
        findings: list[SecurityFinding] = []
        if self.include_base_rules:
            findings.extend(self.base_engine.analyze_text(text, target=target).findings)
        for rule in MULTILANG_RULES:
            if not detected or rule.language != detected:
                continue
            for match in rule.pattern.finditer(text):
                findings.append(finding_from_rule(rule, target, text, match))
        findings = self.deduplicate_and_suppress(findings)
        score = self.score(findings)
        summary = self.summary(findings, score)
        return SecurityReview(target=target, findings=findings, score=score, summary=summary)

    def analyze_workspace(
        self,
        workspace: str | Path,
        *,
        max_files: int = 3000,
        include_fixtures: bool = False,
    ) -> MultiLanguageSecurityReport:
        root = Path(workspace).resolve()
        findings: list[SecurityFinding] = []
        languages: dict[str, int] = {}
        scanned = 0
        for path in root.rglob("*"):
            if scanned >= max_files:
                break
            if path.is_dir() or any(part in {".git", ".venv", "__pycache__", "node_modules", "target", "dist"} for part in path.parts):
                continue
            language = self.language_for_path(path)
            if not language and path.suffix.lower() not in {".py", ".c", ".h", ".cpp", ".hpp", ".sh"}:
                continue
            relative = path.relative_to(root).as_posix()
            if not include_fixtures and self.is_fixture_path(relative):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            language_name = language or path.suffix.lower().lstrip(".") or "text"
            languages[language_name] = languages.get(language_name, 0) + 1
            findings.extend(self.analyze_text(text, target=relative, language=language).findings)
        score = self.score(findings)
        status = "needs_patch" if findings else "complete"
        report = MultiLanguageSecurityReport(
            run_id=f"phase38-{int(time.time())}",
            workspace=str(root),
            languages=languages,
            findings=[finding.model_dump() for finding in findings],
            score=score,
            status=status,
            patch_guidance=[self.guidance(finding) for finding in findings[:100]],
            safety_position="defensive_static_analysis_patch_guidance_only_no_autonomous_exploit_execution",
        )
        return report

    def score(self, findings: list[SecurityFinding]) -> float:
        penalty = sum(severity_penalty(finding.severity) * max(0.5, finding.confidence) for finding in findings)
        return max(0.0, min(1.0, 1.0 - penalty))

    def summary(self, findings: list[SecurityFinding], score: float) -> str:
        if not findings:
            return "No multi-language rule-based security findings detected."
        by_lang: dict[str, int] = {}
        high = sum(1 for finding in findings if finding.severity == "high")
        medium = sum(1 for finding in findings if finding.severity == "medium")
        for finding in findings:
            suffix = Path(finding.file_path or "").suffix.lower()
            lang = LANGUAGE_EXTENSIONS.get(suffix, suffix.lstrip(".") or "unknown")
            by_lang[lang] = by_lang.get(lang, 0) + 1
        return f"Multi-language security score={score:.2f}; high={high}, medium={medium}; languages={by_lang}."

    def guidance(self, finding: SecurityFinding) -> dict[str, Any]:
        return {
            "finding_id": finding.finding_id,
            "language": self.language_for_path(Path(finding.file_path or "")) or "base",
            "file_path": finding.file_path,
            "line": finding.line,
            "severity": finding.severity,
            "cwe": finding.cwe,
            "title": finding.title,
            "safe_patch_direction": finding.recommendation,
            "verification": [
                "rerun Phase 38 scanner",
                "rerun Phase 19 verifier with Semgrep/CodeQL where available",
                "add language-specific regression test",
            ],
        }

    def deduplicate_and_suppress(self, findings: list[SecurityFinding]) -> list[SecurityFinding]:
        selected: list[SecurityFinding] = []
        seen: set[tuple[str | None, int | None, str | None, str]] = set()
        for finding in findings:
            if self.is_detector_pattern_literal(finding):
                continue
            key = (finding.file_path, finding.line, finding.cwe, finding.evidence)
            if key in seen:
                continue
            seen.add(key)
            selected.append(finding)
        return selected

    def is_detector_pattern_literal(self, finding: SecurityFinding) -> bool:
        path = finding.file_path or ""
        evidence = finding.evidence
        detector_files = {
            "src/phase11/security_engine.py",
            "src/phase16/critic_verifier.py",
            "src/phase38/multilang_security.py",
            "src/phase7/grpo_training_loop.py",
        }
        if path not in detector_files:
            return False
        return "for pattern in (" in evidence or "DANGEROUS_PATTERNS" in evidence or "LanguageRule(" in evidence

    def language_for_path(self, path: Path) -> str | None:
        return LANGUAGE_EXTENSIONS.get(path.suffix.lower())

    def is_fixture_path(self, relative_path: str) -> bool:
        lower = relative_path.lower()
        fixture_markers = [
            "test",
            "fixture",
            "sample",
            "benchmark",
            "gauntlet",
            "quality_harness",
            "security_suite.py",
            "bootstrap_mvp.py",
            "expanded_benchmark_suite.py",
            "regression_pack.py",
            "node_modules",
        ]
        return any(marker in lower for marker in fixture_markers)


def write_report(report: MultiLanguageSecurityReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "multilang-security-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 38 multi-language defensive security scanner.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase38")
    parser.add_argument("--max-files", type=int, default=3000)
    parser.add_argument("--include-fixtures", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = MultiLanguageSecurityEngine().analyze_workspace(
        args.workspace,
        max_files=args.max_files,
        include_fixtures=args.include_fixtures,
    )
    if args.run_id:
        report = report.model_copy(update={"run_id": args.run_id})
    json_path, _ = write_report(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "status": report.status,
                "score": report.score,
                "languages": report.languages,
                "findings": len(report.findings),
                "json": str(json_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
