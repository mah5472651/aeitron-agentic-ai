"""Consolidated Critic / Verifier / Guardrails service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.mythos.verifier import VerificationRequest, VerifierRuntime


class GuardrailService:
    def strict_review(self, prompt: str) -> dict[str, Any]:
        risks = [term for term in ["delete", "secret", "password", "token", "unsafe", "eval"] if term in prompt.lower()]
        confidence = 0.9 if not risks else 0.65
        return {"accepted": confidence >= 0.6, "confidence": confidence, "risks": risks, "engine": "native"}

    def critic_review(self, artifact: str, *, prompt: str = "") -> dict[str, Any]:
        issues = []
        if "TODO" in artifact:
            issues.append("artifact contains TODO")
        if len(artifact.strip()) < 20:
            issues.append("artifact is too small to validate")
        return {"confidence": 0.9 if not issues else 0.55, "issues": issues, "prompt": prompt}

    def verifier_policy(self, workspace: str, *, profile: str = "fast") -> dict[str, Any]:
        return {"workspace": str(Path(workspace).resolve()), "profile": profile, "engine": "native", "status": "configured"}

    def security_scan(self, workspace: str, *, max_files: int = 500) -> dict[str, Any]:
        return {"workspace": str(Path(workspace).resolve()), "max_files": max_files, "status": "use /v1/verifier/run after indexing"}
