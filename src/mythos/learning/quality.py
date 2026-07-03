"""Dataset quality gates for scratch pretraining corpora."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


SECRET_RE = re.compile(
    r"(?i)(-----BEGIN .*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,})"
)
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
DEFENSIVE_SECURITY_TERMS = {
    "cve",
    "cwe",
    "owasp",
    "vulnerability",
    "patch",
    "mitigation",
    "secure",
    "sanitiz",
    "authentication",
    "authorization",
    "encryption",
}


class QualityGateConfig(StrictModel):
    min_chars: int = Field(default=200, ge=1)
    max_chars: int = Field(default=2_000_000, ge=1)
    require_license: bool = True
    allowed_licenses: set[str] = Field(default_factory=lambda: {"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0", "public-domain", "unknown-ok"})
    reject_emails: bool = True
    reject_secrets: bool = True


class QualityDecision(StrictModel):
    accepted: bool
    reasons: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    content_hash: str


class QualityGateReport(StrictModel):
    input_path: str
    output_path: str
    accepted: int
    rejected: int
    duplicate: int
    created_at_unix: float = Field(default_factory=time.time)


def stable_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def iter_jsonl(path: str | Path) -> Iterable[dict[str, Any]]:
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            yield json.loads(line)


class DatasetQualityGate:
    def __init__(self, config: QualityGateConfig | None = None) -> None:
        self.config = config or QualityGateConfig()

    def evaluate(self, row: dict[str, Any], *, seen: set[str] | None = None) -> QualityDecision:
        text = str(row.get("text") or row.get("content") or "")
        normalized = " ".join(text.split())
        digest = stable_hash(normalized)
        reasons: list[str] = []
        labels: list[str] = []
        if len(normalized) < self.config.min_chars:
            reasons.append("too_short")
        if len(normalized) > self.config.max_chars:
            reasons.append("too_large")
        license_name = str(row.get("license") or row.get("metadata", {}).get("license") or "").lower()
        if self.config.require_license and license_name not in self.config.allowed_licenses:
            reasons.append("license_not_allowed")
        if self.config.reject_secrets and SECRET_RE.search(text):
            reasons.append("secret_like_content")
        if self.config.reject_emails and EMAIL_RE.search(text):
            reasons.append("email_like_pii")
        if seen is not None and digest in seen:
            reasons.append("duplicate")
        lowered = normalized.lower()
        if any(term in lowered for term in DEFENSIVE_SECURITY_TERMS):
            labels.append("defensive_security")
        if any(token in lowered for token in ["def ", "class ", "function ", "fn ", "package "]):
            labels.append("code")
        if any(token in lowered for token in ["test_", "pytest", "unittest", "assert "]):
            labels.append("tests")
        return QualityDecision(accepted=not reasons, reasons=reasons, labels=labels, content_hash=digest)

    def filter_jsonl(self, input_path: str | Path, output_path: str | Path) -> QualityGateReport:
        seen: set[str] = set()
        accepted = rejected = duplicate = 0
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for row in iter_jsonl(input_path):
                decision = self.evaluate(row, seen=seen)
                if "duplicate" in decision.reasons:
                    duplicate += 1
                if not decision.accepted:
                    rejected += 1
                    continue
                seen.add(decision.content_hash)
                row["quality"] = decision.model_dump()
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                accepted += 1
        return QualityGateReport(input_path=str(input_path), output_path=str(target), accepted=accepted, rejected=rejected, duplicate=duplicate)
