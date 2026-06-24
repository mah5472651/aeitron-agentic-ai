#!/usr/bin/env python
"""Export scorecard failures into SFT and GRPO training candidates."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TrainingExample(StrictModel):
    prompt: str
    chosen: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreferenceExample(StrictModel):
    prompt: str
    chosen: str
    rejected: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScorecardFailureExporter:
    def __init__(self, scorecard_path: str | Path) -> None:
        self.scorecard_path = Path(scorecard_path)
        self.payload = json.loads(self.scorecard_path.read_text(encoding="utf-8"))

    def failures(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        run_id = str(self.payload.get("run_id") or self.scorecard_path.stem)
        for mode in ("mock", "real"):
            section = self.payload.get(mode)
            if not isinstance(section, dict):
                continue
            for failure in section.get("failure_report") or []:
                item = dict(failure)
                item["mode"] = mode
                item["run_id"] = run_id
                results.append(item)
        return results

    def build_sft_examples(self) -> list[TrainingExample]:
        examples: list[TrainingExample] = []
        for failure in self.failures():
            prompt = self._prompt_for_failure(failure)
            chosen = self._chosen_for_failure(failure)
            examples.append(
                TrainingExample(
                    prompt=prompt,
                    chosen=chosen,
                    metadata={
                        "source": "phase16_scorecard_failure_export",
                        "scorecard": str(self.scorecard_path),
                        "created_at_unix": time.time(),
                        "failure": failure,
                    },
                )
            )
        return examples

    def build_preference_examples(self) -> list[PreferenceExample]:
        examples: list[PreferenceExample] = []
        for failure in self.failures():
            details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
            rejected = str(details.get("text_preview") or failure.get("message") or "Incomplete or low-signal model output.")
            examples.append(
                PreferenceExample(
                    prompt=self._prompt_for_failure(failure),
                    chosen=self._chosen_for_failure(failure),
                    rejected=rejected[:4000],
                    metadata={
                        "source": "phase16_scorecard_failure_export",
                        "scorecard": str(self.scorecard_path),
                        "created_at_unix": time.time(),
                        "failure": failure,
                    },
                )
            )
        return examples

    def export(self, *, sft_path: str | Path, preference_path: str | Path | None = None) -> dict[str, Any]:
        sft_examples = self.build_sft_examples()
        pref_examples = self.build_preference_examples()
        sft_target = Path(sft_path)
        sft_target.parent.mkdir(parents=True, exist_ok=True)
        sft_target.write_text(
            "\n".join(example.model_dump_json() for example in sft_examples) + ("\n" if sft_examples else ""),
            encoding="utf-8",
        )
        pref_target = Path(preference_path) if preference_path else None
        if pref_target:
            pref_target.parent.mkdir(parents=True, exist_ok=True)
            pref_target.write_text(
                "\n".join(example.model_dump_json() for example in pref_examples) + ("\n" if pref_examples else ""),
                encoding="utf-8",
            )
        return {
            "scorecard": str(self.scorecard_path),
            "sft_path": str(sft_target),
            "preference_path": str(pref_target) if pref_target else None,
            "sft_count": len(sft_examples),
            "preference_count": len(pref_examples) if pref_target else 0,
        }

    def _prompt_for_failure(self, failure: dict[str, Any]) -> str:
        category = failure.get("category") or "unknown"
        task_id = failure.get("task_id") or "unknown"
        failed_phase = failure.get("failed_phase") or "unknown"
        issue_type = failure.get("issue_type") or "unknown"
        details = failure.get("details") if isinstance(failure.get("details"), dict) else {}
        expected = details.get("expected_signals") or details.get("expected_cwes") or details.get("expected_paths") or []
        return (
            "You are a coding and cybersecurity architecture model. Repair the failed reasoning pattern.\n\n"
            f"Task ID: {task_id}\n"
            f"Category: {category}\n"
            f"Failed phase: {failed_phase}\n"
            f"Issue type: {issue_type}\n"
            f"Expected signals: {expected}\n\n"
            "Return a concise, defensive, implementation-ready response with plan, patch direction, tests, and verification."
        )

    def _chosen_for_failure(self, failure: dict[str, Any]) -> str:
        recommendation = failure.get("recommendation") or "Add targeted regression coverage and improve the failing architecture component."
        failed_phase = failure.get("failed_phase") or "unknown"
        issue_type = failure.get("issue_type") or "unknown"
        category = failure.get("category") or "unknown"
        return (
            "<|thought_start|>"
            f"Analyze the {category} failure as {failed_phase}/{issue_type}. "
            "Identify the missing architecture signal, preserve defensive security boundaries, "
            "and require deterministic verification before accepting the result."
            "<|thought_end|>"
            "<|patch_start|>"
            f"Recommended fix: {recommendation} "
            "Add or update a golden task for this case, run the scorecard, and promote only passing first-run outputs."
            "<|patch_end|>"
        )

