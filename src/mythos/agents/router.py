"""Agent Router & Worker Pool facade.

This intentionally replaces the old "software MoE" naming. It routes work to
specialist roles; it is not a neural MoE.
"""

from __future__ import annotations

from typing import Any


class AgentRouter:
    def route(self, prompt: str, *, top_k: int = 4) -> dict[str, Any]:
        lowered = prompt.lower()
        candidates = []
        if any(term in lowered for term in ["security", "vulnerability", "cve", "secret"]):
            candidates.append({"role": "security", "score": 0.95})
        if any(term in lowered for term in ["test", "pytest", "fail", "bug"]):
            candidates.append({"role": "testing", "score": 0.9})
        if any(term in lowered for term in ["code", "build", "implement", "fix"]):
            candidates.append({"role": "coding", "score": 0.88})
        candidates.append({"role": "planner", "score": 0.75})
        return {"route": candidates[:top_k], "top_role": candidates[0]["role"], "router": "native"}
