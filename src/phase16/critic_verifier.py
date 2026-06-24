#!/usr/bin/env python
"""Critic and verifier backends for defensive coding workflows."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.phase11.model_backends import ModelBackend
from src.phase11.schemas import ChatMessage, ChatRole, GenerationConfig, GenerationRequest
from src.phase11.security_engine import SecurityReasoningEngine


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CriticIssue(StrictModel):
    severity: str
    title: str
    evidence: str
    recommendation: str


class CriticReport(StrictModel):
    ok: bool
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    issues: list[CriticIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationReport(StrictModel):
    ok: bool
    score: float = Field(ge=0.0, le=1.0)
    summary: str
    checks: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CriticBackend(ABC):
    @abstractmethod
    async def review(self, *, prompt: str, artifact: str, context: str = "") -> CriticReport:
        raise NotImplementedError


class HeuristicCriticBackend(CriticBackend):
    async def review(self, *, prompt: str, artifact: str, context: str = "") -> CriticReport:
        lower = artifact.lower()
        issues: list[CriticIssue] = []
        if len(artifact.strip()) < 120:
            issues.append(
                CriticIssue(
                    severity="medium",
                    title="Artifact is too shallow",
                    evidence=artifact[:120],
                    recommendation="Expand into concrete files, tests, risks, and verification steps.",
                )
            )
        if "test" not in lower and "verify" not in lower:
            issues.append(
                CriticIssue(
                    severity="medium",
                    title="Missing verification path",
                    evidence="No test/verify keyword detected.",
                    recommendation="Add deterministic test, sandbox, or static-analysis checks.",
                )
            )
        if any(pattern in artifact for pattern in ("shell=True", "os.system(", "pickle.loads(", "yaml.load(")):
            issues.append(
                CriticIssue(
                    severity="high",
                    title="Risky implementation primitive",
                    evidence="Dangerous API marker detected in generated artifact.",
                    recommendation="Use safe parsers and subprocess argument lists with shell disabled.",
                )
            )
        if re.search(r"\b(cannot|can't)\s+(help|do)\b", lower):
            issues.append(
                CriticIssue(
                    severity="low",
                    title="Unhelpful refusal style",
                    evidence="Refusal-like phrase detected.",
                    recommendation="For defensive tasks, provide safe alternatives and verification guidance.",
                )
            )
        penalty = sum({"high": 0.30, "medium": 0.15, "low": 0.07}.get(issue.severity, 0.10) for issue in issues)
        confidence = max(0.0, min(1.0, 0.92 - penalty))
        return CriticReport(
            ok=confidence >= 0.85,
            confidence=confidence,
            summary=f"Heuristic critic confidence={confidence:.2f}; issues={len(issues)}.",
            issues=issues,
            metadata={"prompt_preview": prompt[:300], "context_chars": len(context)},
        )


class ModelCriticBackend(CriticBackend):
    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    async def review(self, *, prompt: str, artifact: str, context: str = "") -> CriticReport:
        response = await self.backend.generate(
            GenerationRequest(
                messages=[
                    ChatMessage(
                        role=ChatRole.SYSTEM,
                        content=(
                            "You are a strict code critic. Return concise JSON-like feedback with score, "
                            "risks, and concrete fixes. Defensive security only."
                        ),
                    ),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=f"Prompt:\n{prompt}\n\nContext:\n{context[:3000]}\n\nArtifact:\n{artifact[:6000]}",
                    ),
                ],
                config=GenerationConfig(max_new_tokens=700, temperature=0.1),
            )
        )
        lower = response.text.lower()
        issue_count = lower.count("risk") + lower.count("missing") + lower.count("fail")
        confidence = max(0.0, min(1.0, 0.90 - issue_count * 0.05))
        return CriticReport(
            ok=confidence >= 0.85,
            confidence=confidence,
            summary=response.text[:1200],
            issues=[],
            metadata={"backend": response.backend, "model": response.model, "latency_ms": response.latency_ms},
        )


class VerifierBackend(ABC):
    @abstractmethod
    async def verify(self, *, artifact: str, files: dict[str, str] | None = None) -> VerificationReport:
        raise NotImplementedError


class SecurityVerifier(VerifierBackend):
    def __init__(self) -> None:
        self.security = SecurityReasoningEngine()

    async def verify(self, *, artifact: str, files: dict[str, str] | None = None) -> VerificationReport:
        combined = artifact + "\n" + "\n".join((files or {}).values())
        review = self.security.analyze_text(combined, target="phase16-artifact")
        ok = review.score >= 0.75
        return VerificationReport(
            ok=ok,
            score=review.score,
            summary=review.summary,
            checks=[
                {
                    "name": "rule_based_security",
                    "ok": ok,
                    "findings": [finding.model_dump() for finding in review.findings[:20]],
                }
            ],
        )


class CodeSandboxVerifier(VerifierBackend):
    async def verify(self, *, artifact: str, files: dict[str, str] | None = None) -> VerificationReport:
        if not files:
            return VerificationReport(ok=True, score=1.0, summary="No executable files supplied; sandbox skipped.")
        python_files = {path: content for path, content in files.items() if path.endswith(".py")}
        if not python_files:
            return VerificationReport(ok=True, score=1.0, summary="No Python files supplied; sandbox skipped.")
        target = sorted(python_files)[0]
        try:
            from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile

            request = ExecutionRequest(
                files=[SandboxFile(path=path, content=content) for path, content in python_files.items()],
                compile_command=None,
                run_command=f"python3 /workspace/{target}",
                image="python:3.12-slim",
                request_id="phase16-code-verifier",
                pull_missing_image=False,
            )
            started = time.perf_counter()
            async with SandboxEngine(pool_size=1) as engine:
                result = await engine.run(request)
            ok = result.ok
            return VerificationReport(
                ok=ok,
                score=1.0 if ok else 0.0,
                summary=f"sandbox exit_code={result.exit_code} timeout={result.timeout} flag={result.flag}",
                checks=[
                    {
                        "name": "phase2_python_sandbox",
                        "ok": ok,
                        "stdout": result.stdout[-2000:],
                        "stderr": result.stderr[-2000:],
                        "metrics": result.metrics.__dict__,
                    }
                ],
                metadata={"duration_ms": (time.perf_counter() - started) * 1000},
            )
        except Exception as exc:
            return VerificationReport(
                ok=False,
                score=0.0,
                summary=f"sandbox unavailable: {type(exc).__name__}: {exc}",
                checks=[{"name": "phase2_python_sandbox", "ok": False, "error": str(exc)}],
            )


class CompositeVerifier(VerifierBackend):
    def __init__(self, verifiers: list[VerifierBackend] | None = None) -> None:
        self.verifiers = verifiers or [SecurityVerifier(), CodeSandboxVerifier()]

    async def verify(self, *, artifact: str, files: dict[str, str] | None = None) -> VerificationReport:
        reports = [await verifier.verify(artifact=artifact, files=files) for verifier in self.verifiers]
        ok = all(report.ok for report in reports)
        score = min((report.score for report in reports), default=0.0)
        checks = []
        for report in reports:
            checks.extend(report.checks)
        return VerificationReport(
            ok=ok,
            score=score,
            summary="; ".join(report.summary for report in reports),
            checks=checks,
            metadata={"verifier_count": len(reports)},
        )

