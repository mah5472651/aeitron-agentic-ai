"""Structured progress reporting for long Mythos jobs."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from threading import RLock
from typing import Any, TextIO


class ProgressReporter:
    def __init__(
        self,
        *,
        path: str | Path | None = None,
        to_stdout: bool = False,
        prefix: str = "mythos-progress",
        stream: TextIO | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.to_stdout = to_stdout
        self.prefix = prefix
        self.stream = stream or sys.stdout
        self.lock = RLock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, stage: str, status: str = "running", **metrics: Any) -> dict[str, Any]:
        payload = {
            "ts_unix": time.time(),
            "stage": stage,
            "status": status,
            **{key: value for key, value in metrics.items() if value is not None},
        }
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.lock:
            if self.path:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            if self.to_stdout:
                self.stream.write(f"[{self.prefix}] {line}\n")
                self.stream.flush()
        return payload


class NullProgressReporter(ProgressReporter):
    def __init__(self) -> None:
        super().__init__(path=None, to_stdout=False)

    def emit(self, stage: str, status: str = "running", **metrics: Any) -> dict[str, Any]:
        return {
            "ts_unix": time.time(),
            "stage": stage,
            "status": status,
            **{key: value for key, value in metrics.items() if value is not None},
        }


def progress_from_options(*, path: str | Path | None, to_stdout: bool) -> ProgressReporter:
    if path or to_stdout:
        return ProgressReporter(path=path, to_stdout=to_stdout)
    return NullProgressReporter()
