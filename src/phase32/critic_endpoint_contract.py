#!/usr/bin/env python
"""Validate critic endpoint contract for model-backed review."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.model_backends import build_backend
from src.phase22.critic_service import review_artifact


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CriticContractReport(StrictModel):
    run_id: str
    endpoint: str
    model: str
    status: str
    review: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float
    created_at_unix: float = Field(default_factory=time.time)


async def run_contract(endpoint: str, model: str, *, run_id: str) -> CriticContractReport:
    started = time.time()
    backend = build_backend("openai_compatible", endpoint=endpoint, model_name=model, api_key=os.environ.get("PHASE32_API_KEY"))
    try:
        review = await review_artifact(
            prompt="Review a safe coding patch.",
            artifact="Plan: change input validation. Verification: run tests and static security checks. Security: avoid shell execution.",
            context="Contract smoke test.",
            mode="model",
            backend=backend,
        )
        return CriticContractReport(run_id=run_id, endpoint=endpoint, model=model, status="complete" if review.summary else "needs_attention", review=review.model_dump(), duration_ms=(time.time() - started) * 1000)
    except Exception as exc:
        return CriticContractReport(run_id=run_id, endpoint=endpoint, model=model, status="failed", error=f"{type(exc).__name__}: {exc}", duration_ms=(time.time() - started) * 1000)
    finally:
        await backend.aclose()


def write_report(report: CriticContractReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "critic-contract-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Validate critic endpoint contract.")
    parser.add_argument("--endpoint", default=os.environ.get("PHASE32_ENDPOINT", "http://127.0.0.1:8016/v1"))
    parser.add_argument("--model", default=os.environ.get("PHASE32_MODEL", "Qwen/Qwen2.5-Coder-0.5B-Instruct"))
    parser.add_argument("--run-id", default=f"phase32-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase32")
    args = parser.parse_args()
    report = await run_contract(args.endpoint, args.model, run_id=args.run_id)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "json": str(json_path), "error": report.error}, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()

