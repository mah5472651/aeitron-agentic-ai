"""Runtime configuration helpers for consolidated Mythos modules."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def active_profile_path() -> Path:
    return ROOT / "config" / "active_model_profile.json"


def load_active_profile() -> dict[str, Any]:
    path = active_profile_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def env_value(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default

