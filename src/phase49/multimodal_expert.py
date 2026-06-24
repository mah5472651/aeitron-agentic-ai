#!/usr/bin/env python
"""Phase 49 multimodal expert.

Creates a safe analysis contract for images, PDFs, diagrams, screenshots, and
repositories. It extracts local metadata now and leaves a clean slot for future
vision/PDF models.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MediaArtifact(StrictModel):
    path: str
    media_type: str
    size_bytes: int
    signals: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class MultimodalReport(StrictModel):
    run_id: str
    prompt: str
    artifacts: list[MediaArtifact]
    planner_context: str
    limitations: list[str]
    created_at_unix: float = Field(default_factory=time.time)


class MultimodalExpert:
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    PDF_EXTENSIONS = {".pdf"}
    DIAGRAM_EXTENSIONS = {".drawio", ".mmd", ".svg"}

    def analyze(self, prompt: str, paths: list[Path], *, run_id: str | None = None, max_files: int = 200) -> MultimodalReport:
        artifacts = [self.inspect_path(path, max_files=max_files) for path in paths]
        context = self.render_context(prompt, artifacts)
        return MultimodalReport(
            run_id=run_id or f"phase49-{time.time_ns()}",
            prompt=prompt,
            artifacts=artifacts,
            planner_context=context,
            limitations=[
                "Local metadata analysis only; no OCR or vision model is invoked in this CPU-safe phase.",
                "Future model adapters should attach extracted text, diagram nodes, and visual findings to this same schema.",
            ],
        )

    def inspect_path(self, path: Path, *, max_files: int = 200) -> MediaArtifact:
        resolved = path.resolve()
        if resolved.is_dir():
            files = []
            for child in resolved.rglob("*"):
                if len(files) >= max_files:
                    break
                if child.is_file() and not any(part in {".git", "__pycache__", ".venv", "node_modules"} for part in child.parts):
                    files.append(child)
            signals = ["repository_or_folder", f"sampled_files={len(files)}"]
            interesting = [child.relative_to(resolved).as_posix() for child in files[:50]]
            return MediaArtifact(path=str(resolved), media_type="directory", size_bytes=0, signals=signals, metadata={"sample_files": interesting})
        size = resolved.stat().st_size if resolved.exists() else 0
        suffix = resolved.suffix.lower()
        mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        signals: list[str] = []
        if suffix in self.IMAGE_EXTENSIONS:
            signals.extend(["image", "vision_candidate"])
        if suffix in self.PDF_EXTENSIONS:
            signals.extend(["pdf", "document_candidate"])
        if suffix in self.DIAGRAM_EXTENSIONS:
            signals.extend(["diagram", "architecture_candidate"])
        if not signals:
            signals.append("generic_file")
        metadata = {"exists": resolved.exists(), "suffix": suffix, "mime": mime}
        return MediaArtifact(path=str(resolved), media_type=mime, size_bytes=size, signals=signals, metadata=metadata)

    def render_context(self, prompt: str, artifacts: list[MediaArtifact]) -> str:
        lines = [f"Multimodal prompt: {prompt}", "Artifacts:"]
        for artifact in artifacts:
            lines.append(f"- {artifact.path} type={artifact.media_type} size={artifact.size_bytes} signals={','.join(artifact.signals)}")
        lines.append("Planner instruction: ask for OCR/vision extraction when the task depends on visual content; otherwise use file metadata and repository context.")
        return "\n".join(lines)


def write_report(report: MultimodalReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "multimodal-expert-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 49 multimodal expert metadata analysis.")
    parser.add_argument("--prompt", default="analyze attached assets")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--run-id", default=f"phase49-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase49")
    parser.add_argument("--max-files", type=int, default=200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = [Path(item) for item in args.path] or [ROOT]
    report = MultimodalExpert().analyze(args.prompt, paths, run_id=args.run_id, max_files=args.max_files)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "artifacts": len(report.artifacts), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
