"""Unified model backend facade.

This wraps the Phase 11 backend implementations so new code depends on a stable
``src.mythos.model_ops`` contract instead of phase-specific modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.phase11.model_backends import ModelBackend, build_backend
from src.phase42.profile_switcher import RuntimeProfile, activate_profile, all_profiles, runtime_checks

from src.mythos.shared.config import load_active_profile


def build_active_backend() -> ModelBackend:
    payload = load_active_profile()
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    backend = str(profile.get("backend") or env.get("PHASE11_BACKEND") or "mock")
    return build_backend(
        backend,
        endpoint=str(profile.get("endpoint") or env.get("PHASE11_MODEL_ENDPOINT") or "http://127.0.0.1:8016/v1"),
        model_name=str(profile.get("model_name") or env.get("PHASE11_MODEL_NAME") or "security-coder"),
    )


def list_model_profiles() -> dict[str, Any]:
    return {name: profile.model_dump() for name, profile in all_profiles().items()}


def activate_model_profile(name: str, *, run_id: str = "mythos-profile") -> dict[str, Any]:
    profiles = all_profiles()
    if name not in profiles:
        raise ValueError(f"unknown model profile: {name}")
    report = activate_profile(profiles[name], output_dir=Path("artifacts") / "phase42", run_id=run_id)
    return report.model_dump()


def active_model_health() -> dict[str, Any]:
    payload = load_active_profile()
    profile_payload = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    if not profile_payload:
        return {"ok": False, "reason": "active profile missing"}
    profile = RuntimeProfile.model_validate(profile_payload)
    return runtime_checks(profile)
