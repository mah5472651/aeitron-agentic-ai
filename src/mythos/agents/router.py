"""Agent Router & Worker Pool facade.

This intentionally replaces the old "software MoE" naming. It routes work to
specialist roles; it is not a neural MoE.
"""

from __future__ import annotations

from typing import Any

from src.phase50.moe_router import MoERouter


class AgentRouter:
    def route(self, prompt: str, *, top_k: int = 4) -> dict[str, Any]:
        return MoERouter().route(prompt, top_k=top_k).model_dump()

