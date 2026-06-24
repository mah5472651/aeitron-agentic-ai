#!/usr/bin/env python
"""Defensive tool adapters for Git, Semgrep, CodeQL, and browser-style research fetches."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.phase11.memory_engine import safe_workspace

ROOT = Path(__file__).resolve().parents[2]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AdapterResult(StrictModel):
    tool: str
    ok: bool
    summary: str
    stdout: str = ""
    stderr: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


def run_command(argv: list[str], *, cwd: Path, timeout_s: float = 30.0) -> AdapterResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(  # nosec B603
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        ok = completed.returncode == 0
        return AdapterResult(
            tool=Path(argv[0]).name,
            ok=ok,
            summary=f"exit_code={completed.returncode}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            data={"argv": argv, "exit_code": completed.returncode},
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    except subprocess.TimeoutExpired as exc:
        return AdapterResult(
            tool=Path(argv[0]).name,
            ok=False,
            summary=f"timeout after {timeout_s:.1f}s",
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            data={"argv": argv, "timed_out": True},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


def local_tool_path(*parts: str) -> str | None:
    candidate = ROOT.joinpath(*parts)
    return str(candidate) if candidate.exists() else None


def find_tool(executable: str, *local_parts: str, env_var: str | None = None) -> str | None:
    if env_var:
        configured_raw = __import__("os").environ.get(env_var)
        if configured_raw:
            configured = Path(configured_raw)
            if configured.exists():
                return str(configured)
    local = local_tool_path(*local_parts)
    if local:
        return local
    return shutil.which(executable)


def codeql_pack_dir() -> Path:
    return Path.home() / ".codeql" / "packages"


class GitTool:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = safe_workspace(workspace)

    async def status(self) -> AdapterResult:
        if not (self.workspace / ".git").exists():
            return AdapterResult(tool="git", ok=False, summary="workspace is not a git repository")
        return await asyncio.to_thread(run_command, ["git", "status", "--short"], cwd=self.workspace, timeout_s=15)

    async def diff(self, *, max_chars: int = 20000) -> AdapterResult:
        if not (self.workspace / ".git").exists():
            return AdapterResult(tool="git", ok=False, summary="workspace is not a git repository")
        result = await asyncio.to_thread(run_command, ["git", "diff", "--", "."], cwd=self.workspace, timeout_s=20)
        result.stdout = result.stdout[:max_chars]
        result.data["truncated"] = len(result.stdout) >= max_chars
        return result

    async def show(self, ref: str = "HEAD", *, max_chars: int = 20000) -> AdapterResult:
        safe_ref = re.sub(r"[^A-Za-z0-9_./:@{-}^-]", "", ref)[:120] or "HEAD"
        if not (self.workspace / ".git").exists():
            return AdapterResult(tool="git", ok=False, summary="workspace is not a git repository")
        result = await asyncio.to_thread(run_command, ["git", "show", "--stat", "--patch", safe_ref], cwd=self.workspace, timeout_s=20)
        result.stdout = result.stdout[:max_chars]
        result.data["ref"] = safe_ref
        return result


class SemgrepTool:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = safe_workspace(workspace)

    async def available(self) -> AdapterResult:
        executable = find_tool("semgrep", "tools", "bin", "semgrep.cmd", env_var="PHASE16_SEMGREP")
        if executable:
            return await asyncio.to_thread(run_command, [executable, "--version"], cwd=self.workspace, timeout_s=30)
        docker = shutil.which("docker")
        if not docker:
            return AdapterResult(tool="semgrep", ok=False, summary="semgrep executable not installed and Docker is unavailable")
        return await asyncio.to_thread(
            run_command,
            [docker, "run", "--rm", "--entrypoint", "semgrep", "semgrep/semgrep", "--version"],
            cwd=self.workspace,
            timeout_s=60,
        )

    async def scan(self, *, config: str = "auto", target: str = "src") -> AdapterResult:
        safe_config = config if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", config) else "auto"
        safe_target = target if re.fullmatch(r"[A-Za-z0-9_./:@+\\-]+", target) else "src"
        excludes = ["artifacts", ".git", "__pycache__", ".venv", "node_modules", "data"]
        executable = find_tool("semgrep", "tools", "bin", "semgrep.cmd", env_var="PHASE16_SEMGREP")
        if executable:
            argv = [executable, "scan", "--config", safe_config, "--json", "--error", "--quiet"]
            for pattern in excludes:
                argv.extend(["--exclude", pattern])
            argv.append(safe_target)
        else:
            docker = shutil.which("docker")
            if not docker:
                return AdapterResult(tool="semgrep", ok=False, summary="semgrep executable not installed and Docker is unavailable")
            argv = [
                docker,
                "run",
                "--rm",
                "--entrypoint",
                "semgrep",
                "-v",
                f"{self.workspace}:/src:ro",
                "-w",
                "/src",
                "semgrep/semgrep",
                "scan",
                "--config",
                safe_config,
                "--json",
                "--error",
                "--quiet",
            ]
            for pattern in excludes:
                argv.extend(["--exclude", pattern])
            argv.append(safe_target)
        timeout_s = float(os.environ.get("PHASE16_SEMGREP_TIMEOUT", "240"))
        result = await asyncio.to_thread(
            run_command,
            argv,
            cwd=self.workspace,
            timeout_s=timeout_s,
        )
        try:
            result.data["semgrep"] = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            result.data["semgrep_parse_error"] = True
        return result


class CodeQLTool:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = safe_workspace(workspace)

    async def available(self) -> AdapterResult:
        executable = find_tool("codeql", "tools", "codeql", "codeql.exe", env_var="PHASE16_CODEQL")
        if not executable:
            return AdapterResult(tool="codeql", ok=False, summary="codeql executable not installed")
        result = await asyncio.to_thread(run_command, [executable, "version", "--format=json"], cwd=self.workspace, timeout_s=15)
        pack_dir = codeql_pack_dir()
        if pack_dir.exists():
            packs = await asyncio.to_thread(
                run_command,
                [executable, "resolve", "qlpacks", "--format=json", "--additional-packs", str(pack_dir)],
                cwd=self.workspace,
                timeout_s=30,
            )
            result.data["qlpacks_available"] = packs.ok
            result.data["qlpacks_preview"] = packs.stdout[:4000]
        return result

    async def analyze_database(self, database: str | Path, *, suite: str = "codeql/python-queries", max_chars: int = 20000) -> AdapterResult:
        executable = find_tool("codeql", "tools", "codeql", "codeql.exe", env_var="PHASE16_CODEQL")
        if not executable:
            return AdapterResult(tool="codeql", ok=False, summary="codeql executable not installed")
        database_path = Path(database).resolve()
        if not database_path.exists():
            return AdapterResult(tool="codeql", ok=False, summary=f"database not found: {database_path}")
        pack_args = ["--additional-packs", str(codeql_pack_dir())] if codeql_pack_dir().exists() else []
        result = await asyncio.to_thread(
            run_command,
            [
                executable,
                "database",
                "analyze",
                *pack_args,
                str(database_path),
                suite,
                "--format=sarif-latest",
                "--output",
                "codeql-results.sarif",
            ],
            cwd=self.workspace,
            timeout_s=300,
        )
        result.stdout = result.stdout[:max_chars]
        result.stderr = result.stderr[:max_chars]
        return result


class BrowserResearchTool:
    async def fetch_metadata(self, url: str) -> AdapterResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return AdapterResult(tool="browser_fetch", ok=False, summary="only absolute http(s) URLs are allowed")
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "phase16-defensive-research/1.0"})
            text = response.text[:100000]
            title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
            return AdapterResult(
                tool="browser_fetch",
                ok=200 <= response.status_code < 400,
                summary=f"status={response.status_code} title={title[:120]}",
                stdout=text[:5000],
                data={
                    "status_code": response.status_code,
                    "final_url": str(response.url),
                    "content_type": response.headers.get("content-type", ""),
                    "title": title,
                },
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return AdapterResult(
                tool="browser_fetch",
                ok=False,
                summary=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000,
            )


class ToolAdapterRegistry:
    def __init__(self, workspace: str | Path = ".") -> None:
        self.workspace = safe_workspace(workspace)
        self.git = GitTool(self.workspace)
        self.semgrep = SemgrepTool(self.workspace)
        self.codeql = CodeQLTool(self.workspace)
        self.browser = BrowserResearchTool()

    def specs(self) -> list[dict[str, Any]]:
        return [
            {"name": "git.status", "defensive": True, "description": "Inspect repository status."},
            {"name": "git.diff", "defensive": True, "description": "Inspect current diff without modifying files."},
            {"name": "semgrep.scan", "defensive": True, "description": "Run static security analysis if Semgrep is installed."},
            {"name": "codeql.available", "defensive": True, "description": "Probe CodeQL CLI availability."},
            {"name": "browser.fetch_metadata", "defensive": True, "description": "Fetch http(s) metadata for documentation research."},
        ]

    async def status(self) -> dict[str, Any]:
        git_status, semgrep_status, codeql_status = await asyncio.gather(
            self.git.status(),
            self.semgrep.available(),
            self.codeql.available(),
        )
        return {
            "workspace": str(self.workspace),
            "tools": self.specs(),
            "probes": {
                "git": git_status.model_dump(),
                "semgrep": semgrep_status.model_dump(),
                "codeql": codeql_status.model_dump(),
            },
            "safety": "defensive_static_analysis_only",
        }
