#!/usr/bin/env python
"""Safe patch manager with preview diff, backups, and rollback."""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.memory_engine import safe_workspace
from src.phase11.tool_runtime import resolve_inside


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ManagedPatch(StrictModel):
    path: str
    content: str
    rationale: str = ""

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or "/../" in f"/{normalized}/" or normalized in {".", ".."}:
            raise ValueError(f"unsafe patch path: {value}")
        return normalized


class PatchOperation(StrictModel):
    path: str
    applied: bool
    backup_path: str | None = None
    diff: str
    summary: str


class PatchManagerReport(StrictModel):
    run_id: str
    workspace: str
    mode: str
    status: str
    operations: list[PatchOperation]
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


class PatchManager:
    def __init__(self, workspace: str | Path, *, backup_root: str | Path | None = None) -> None:
        self.workspace = safe_workspace(workspace)
        self.backup_root = Path(backup_root or ROOT / "artifacts" / "phase26" / "backups")
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def preview_or_apply(self, patches: list[ManagedPatch], *, apply: bool, run_id: str | None = None) -> PatchManagerReport:
        active_run_id = run_id or f"phase26-{int(time.time())}"
        operations = [self._patch_one(patch, apply=apply, run_id=active_run_id) for patch in patches]
        status = "complete" if all(operation.applied or not apply for operation in operations) else "needs_attention"
        return PatchManagerReport(
            run_id=active_run_id,
            workspace=str(self.workspace),
            mode="apply" if apply else "preview",
            status=status,
            operations=operations,
            recommendation="Run verifier after apply; use rollback manifest if tests fail." if apply else "Review diff before applying.",
        )

    def rollback(self, manifest: Path, *, run_id: str | None = None) -> PatchManagerReport:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        operations: list[PatchOperation] = []
        for item in payload.get("operations", []):
            backup = item.get("backup_path")
            target = item.get("path")
            if not backup or not target:
                continue
            target_path = resolve_inside(self.workspace, target)
            backup_path = Path(backup)
            if not backup_path.exists():
                operations.append(PatchOperation(path=target, applied=False, diff="", summary="backup missing"))
                continue
            before = target_path.read_text(encoding="utf-8", errors="replace") if target_path.exists() else ""
            restored = backup_path.read_text(encoding="utf-8", errors="replace")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(restored, encoding="utf-8")
            diff = "\n".join(difflib.unified_diff(before.splitlines(), restored.splitlines(), fromfile=f"{target}:before-rollback", tofile=f"{target}:restored", lineterm=""))
            operations.append(PatchOperation(path=target, applied=True, backup_path=str(backup_path), diff=diff, summary="restored from backup"))
        return PatchManagerReport(
            run_id=run_id or f"phase26-rollback-{int(time.time())}",
            workspace=str(self.workspace),
            mode="rollback",
            status="complete",
            operations=operations,
            recommendation="Rerun verifier after rollback.",
        )

    def _patch_one(self, patch: ManagedPatch, *, apply: bool, run_id: str) -> PatchOperation:
        target = resolve_inside(self.workspace, patch.path)
        before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        after = patch.content
        diff = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile=f"{patch.path}:before", tofile=f"{patch.path}:after", lineterm=""))
        backup_path: Path | None = None
        if apply:
            backup_path = self.backup_root / run_id / patch.path.replace("/", "__")
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(before, encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(after, encoding="utf-8")
        return PatchOperation(
            path=patch.path,
            applied=apply,
            backup_path=str(backup_path) if backup_path else None,
            diff=diff,
            summary=patch.rationale or ("applied patch" if apply else "preview diff"),
        )


def write_report(report: PatchManagerReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "patch-manager-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview/apply/rollback managed patches.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--patch-json", help="JSON file with {patches:[{path,content,rationale}]}")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback-manifest")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase26")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manager = PatchManager(args.workspace)
    if args.rollback_manifest:
        report = manager.rollback(Path(args.rollback_manifest))
    else:
        patches: list[ManagedPatch]
        if args.patch_json:
            payload = json.loads(Path(args.patch_json).read_text(encoding="utf-8"))
            patches = [ManagedPatch.model_validate(item) for item in payload.get("patches", [])]
        else:
            patches = [ManagedPatch(path="artifacts/phase26/example.txt", content="phase26 preview example\n", rationale="default smoke patch")]
        report = manager.preview_or_apply(patches, apply=args.apply)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "mode": report.mode, "operations": len(report.operations), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
