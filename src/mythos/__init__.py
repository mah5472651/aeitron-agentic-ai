"""Consolidated Mythos production-facing architecture.

The old ``src/phase*`` modules remain as legacy implementation sources. New
runtime code should import through ``src.mythos`` so the public architecture is
12 modules instead of 51 phases.
"""

__all__ = [
    "gateway",
    "planning",
    "runtime",
    "agents",
    "tools",
    "context",
    "memory",
    "guardrails",
    "patches",
    "evaluation",
    "learning",
    "model_ops",
    "shared",
]

