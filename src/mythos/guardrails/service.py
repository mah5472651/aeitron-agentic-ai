"""Consolidated Critic / Verifier / Guardrails service."""

from __future__ import annotations

from typing import Any

from src.phase22.critic_service import review_artifact
from src.phase27.verifier_policy_engine import load_profile, run_policy
from src.phase38.multilang_security import MultiLanguageSecurityEngine
from src.phase51.high_stability_reasoning_memory import ReasoningEngine as StrictReasoningEngine


class GuardrailService:
    def strict_review(self, prompt: str) -> dict[str, Any]:
        return StrictReasoningEngine().run(prompt).model_dump()

    def critic_review(self, artifact: str, *, prompt: str = "") -> dict[str, Any]:
        return review_artifact(artifact=artifact, prompt=prompt).model_dump()

    def verifier_policy(self, workspace: str, *, profile: str = "fast") -> dict[str, Any]:
        loaded = load_profile(profile)
        return run_policy(workspace, loaded).model_dump()

    def security_scan(self, workspace: str, *, max_files: int = 500) -> dict[str, Any]:
        return MultiLanguageSecurityEngine(workspace, max_files=max_files).scan().model_dump()

