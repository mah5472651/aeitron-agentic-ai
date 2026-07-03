"""Native MVP patch preview/apply/rollback service."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from src.mythos.db import LocalStore
from src.mythos.indexing import RepositoryIndexer
from src.mythos.shared.schemas import StrictModel
from src.mythos.tools.runtime import project_root
from src.mythos.verifier import VerificationRequest, VerifierRuntime


class FileEdit(StrictModel):
    path: str
    new_content: str

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("../") or "/../" in f"/{normalized}/":
            raise ValueError(f"unsafe edit path: {value}")
        if normalized == ".git" or normalized.startswith(".git/"):
            raise ValueError("patches cannot write inside .git")
        return normalized


class PatchPreviewRequest(StrictModel):
    project_id: str
    run_id: str | None = None
    edits: list[FileEdit] = Field(min_length=1)


class PatchResponse(StrictModel):
    patch_id: str
    project_id: str
    run_id: str | None = None
    status: str
    diff: str
    files_changed: list[str]


class PatchVerifyRequest(PatchPreviewRequest):
    commands: list[list[str]] = Field(default_factory=list)
    run_secret_scan: bool = True
    run_semgrep: bool = False
    run_codeql: bool = False
    fail_on_tool_unavailable: bool = False
    apply_on_accept: bool = False


class PatchVerifyResponse(StrictModel):
    patch: PatchResponse
    verification: dict[str, Any]
    verdict: str
    final_status: str
    rolled_back: bool


def resolve_inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes project root: {relative}")
    return target


class PatchService:
    def __init__(self, store: LocalStore | None = None) -> None:
        self.store = store or LocalStore()

    def preview(self, request: PatchPreviewRequest) -> PatchResponse:
        root = project_root(self.store, request.project_id)
        diff_parts: list[str] = []
        backup: dict[str, Any] = {"files": {}}
        for edit in request.edits:
            target = resolve_inside(root, edit.path)
            before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            backup["files"][edit.path] = before
            diff_parts.append(self.make_diff(edit.path, before, edit.new_content))
        record = self.store.create_patch_record(
            project_id=request.project_id,
            run_id=request.run_id,
            status="preview",
            diff="\n".join(part for part in diff_parts if part),
            files_changed=[edit.path for edit in request.edits],
            backup={**backup, "edits": [edit.model_dump() for edit in request.edits]},
        )
        return self.response_from_record(record)

    def apply(self, patch_id: str) -> PatchResponse:
        record = self.store.get_patch(patch_id)
        if record is None:
            raise KeyError(f"unknown patch: {patch_id}")
        root = project_root(self.store, record["project_id"])
        for edit in record["backup"].get("edits", []):
            target = resolve_inside(root, edit["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(edit["new_content"]), encoding="utf-8")
        self.store.update_patch_status(patch_id, "applied", applied=True)
        return self.response_from_record(self.store.get_patch(patch_id) or record)

    def rollback(self, patch_id: str) -> PatchResponse:
        record = self.store.get_patch(patch_id)
        if record is None:
            raise KeyError(f"unknown patch: {patch_id}")
        root = project_root(self.store, record["project_id"])
        for relative, before in record["backup"].get("files", {}).items():
            target = resolve_inside(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(before), encoding="utf-8")
        self.store.update_patch_status(patch_id, "rolled_back", rolled_back=True)
        return self.response_from_record(self.store.get_patch(patch_id) or record)

    def preview_apply_verify(self, request: PatchVerifyRequest) -> PatchVerifyResponse:
        patch = self.preview(request)
        applied = self.apply(patch.patch_id)
        RepositoryIndexer(self.store).index_project(project_id=request.project_id)
        verification = VerifierRuntime(self.store).run(
            VerificationRequest(
                project_id=request.project_id,
                run_id=request.run_id,
                patch_id=patch.patch_id,
                commands=request.commands,
                run_secret_scan=request.run_secret_scan,
                run_semgrep=request.run_semgrep,
                run_codeql=request.run_codeql,
                fail_on_tool_unavailable=request.fail_on_tool_unavailable,
            )
        )
        accepted = verification.verdict == "accept"
        rolled_back = False
        final_patch = applied
        if not accepted or not request.apply_on_accept:
            final_patch = self.rollback(patch.patch_id)
            rolled_back = True
            RepositoryIndexer(self.store).index_project(project_id=request.project_id)
        return PatchVerifyResponse(
            patch=final_patch,
            verification=verification.model_dump(),
            verdict=verification.verdict,
            final_status=final_patch.status,
            rolled_back=rolled_back,
        )

    def response_from_record(self, record: dict[str, Any]) -> PatchResponse:
        return PatchResponse(
            patch_id=record["id"],
            project_id=record["project_id"],
            run_id=record.get("run_id"),
            status=record["status"],
            diff=record["diff"],
            files_changed=record["files_changed"],
        )

    def make_diff(self, path: str, before: str, after: str) -> str:
        return "\n".join(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"{path}:before",
                tofile=f"{path}:after",
                lineterm="",
            )
        )
