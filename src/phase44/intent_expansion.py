#!/usr/bin/env python
"""Phase 44 intent expansion engine.

Turns tiny prompts into structured internal requirements before planning.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class IntentExpansionReport(StrictModel):
    run_id: str
    prompt: str
    expanded_intent: str
    domain: str
    requirements: list[str]
    non_functional_requirements: list[str]
    security_requirements: list[str]
    data_entities: list[str]
    user_roles: list[str]
    acceptance_tests: list[str]
    assumptions: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    created_at_unix: float = Field(default_factory=time.time)


DOMAIN_BLUEPRINTS: dict[str, dict[str, list[str]]] = {
    "login": {
        "keywords": ["login", "auth", "jwt", "signin", "signup", "session", "password"],
        "requirements": ["registration", "login", "logout", "refresh tokens", "password reset", "RBAC"],
        "security": ["password hashing", "rate limiting", "MFA-ready design", "JWT expiry", "session revocation"],
        "entities": ["User", "Credential", "Session", "Role", "AuditLog"],
        "roles": ["anonymous user", "authenticated user", "admin"],
    },
    "streaming": {
        "keywords": ["netflix", "video", "stream", "movie", "ott"],
        "requirements": ["catalog", "subscriptions", "playback", "watch history", "recommendations", "admin uploads"],
        "security": ["signed media URLs", "DRM boundary", "payment webhook verification", "tenant isolation"],
        "entities": ["User", "Title", "VideoAsset", "Subscription", "WatchEvent", "Recommendation"],
        "roles": ["viewer", "admin", "content manager"],
    },
    "rideshare": {
        "keywords": ["uber", "ride", "driver", "maps", "booking"],
        "requirements": ["rider booking", "driver matching", "pricing", "trip lifecycle", "payments", "ratings"],
        "security": ["payment tokenization", "location privacy", "fraud limits", "driver/rider authorization"],
        "entities": ["Rider", "Driver", "Trip", "Vehicle", "Payment", "LocationUpdate"],
        "roles": ["rider", "driver", "operator"],
    },
    "commerce": {
        "keywords": ["shop", "store", "ecommerce", "cart", "checkout"],
        "requirements": ["catalog", "cart", "checkout", "orders", "inventory", "refunds"],
        "security": ["payment webhook signatures", "idempotency", "PII minimization", "admin authorization"],
        "entities": ["Product", "Cart", "Order", "Payment", "InventoryItem", "Customer"],
        "roles": ["customer", "admin", "support"],
    },
    "api": {
        "keywords": ["api", "crud", "backend", "service", "endpoint"],
        "requirements": ["REST endpoints", "validation", "persistence", "pagination", "tests", "observability"],
        "security": ["input validation", "auth boundary", "rate limiting", "structured audit logs"],
        "entities": ["Resource", "User", "Request", "Response", "AuditLog"],
        "roles": ["api user", "operator"],
    },
}


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def prompt_terms(prompt: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9_-]*", prompt.lower()))


def detect_domain(prompt: str) -> str:
    terms = prompt_terms(prompt)
    lower = prompt.lower()
    for domain, blueprint in DOMAIN_BLUEPRINTS.items():
        if any(keyword in terms or keyword in lower for keyword in blueprint["keywords"]):
            return domain
    return "api" if any(token in terms for token in ["build", "create", "implement", "app", "system"]) else "general"


def expand_intent(prompt: str, *, run_id: str | None = None) -> IntentExpansionReport:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    domain = detect_domain(prompt)
    blueprint = DOMAIN_BLUEPRINTS.get(domain, DOMAIN_BLUEPRINTS["api"])
    requirements = unique([*blueprint["requirements"], "error handling", "unit tests", "integration tests"])
    non_functional = ["clear module boundaries", "structured logging", "configuration via environment", "migration-ready storage", "maintainable tests"]
    security = unique([*blueprint["security"], "least privilege", "secret-free source code", "defensive input handling"])
    entities = blueprint["entities"]
    roles = blueprint["roles"]
    tests = [
        "happy path works end-to-end",
        "invalid input is rejected with stable errors",
        "authorization boundary is enforced",
        "security-sensitive flows have regression tests",
        "observability captures failures without leaking secrets",
    ]
    expanded = "\n".join(
        [
            f"Original prompt: {prompt}",
            f"Detected domain: {domain}",
            "Requirements:",
            *[f"- {item}" for item in requirements],
            "Security:",
            *[f"- {item}" for item in security],
            "Acceptance tests:",
            *[f"- {item}" for item in tests],
        ]
    )
    return IntentExpansionReport(
        run_id=run_id or f"phase44-{time.time_ns()}",
        prompt=prompt,
        expanded_intent=expanded,
        domain=domain,
        requirements=requirements,
        non_functional_requirements=non_functional,
        security_requirements=security,
        data_entities=entities,
        user_roles=roles,
        acceptance_tests=tests,
        assumptions=["Use existing repo conventions.", "Keep changes minimal unless the prompt asks for a full product.", "Prefer defensive security defaults."],
        confidence=0.86 if domain != "general" else 0.72,
    )


def write_report(report: IntentExpansionReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "intent-expansion-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 44 intent expansion engine.")
    parser.add_argument("--prompt", default="build login system")
    parser.add_argument("--run-id", default=f"phase44-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase44")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = expand_intent(args.prompt, run_id=args.run_id)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "domain": report.domain, "requirements": len(report.requirements), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
