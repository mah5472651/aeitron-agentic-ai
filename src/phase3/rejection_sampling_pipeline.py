#!/usr/bin/env python
"""Distributed rejection sampling pipeline for leakage-free SFT curation.

This script reads vulnerable code-repair samples with AST/call-graph metadata,
prompts a base reasoning model to produce exactly:

<|thought_start|>...<|thought_end|><|patch_start|>...<|patch_end|>

Hidden ground-truth patches, fix explanations, CVE identifiers, and oracle data
are withheld from the prompt. The proposed patch is injected into a fresh
sandbox file tree and evaluated exactly once. Only first-pass runs with sandbox
exit code 0 are appended to the immutable token-ready JSONL dataset:

{"prompt": "...", "chosen": "<|thought_start|>...<|thought_end|><|patch_start|>...<|patch_end|>"}
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase2.docker_sandbox_engine import (  # noqa: E402
    DEFAULT_IMAGE,
    DockerSandboxEngine,
    SandboxRequest,
    SourceFile,
)


THOUGHT_START = "<|thought_start|>"
THOUGHT_END = "<|thought_end|>"
PATCH_START = "<|patch_start|>"
PATCH_END = "<|patch_end|>"


def validate_http_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PipelineError("model endpoint must be an absolute http:// or https:// URL")
    return endpoint.rstrip("/")

GENERATION_RE = re.compile(
    re.escape(THOUGHT_START)
    + r"(?P<thought>.*?)"
    + re.escape(THOUGHT_END)
    + r"\s*"
    + re.escape(PATCH_START)
    + r"(?P<patch>.*?)"
    + re.escape(PATCH_END),
    flags=re.DOTALL,
)

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", flags=re.IGNORECASE)

LEAKAGE_KEYS = {
    "ground_truth",
    "ground_truth_patch",
    "reference_patch",
    "gold_patch",
    "target_patch",
    "fix_explanation",
    "solution",
    "answer",
    "cve",
    "cve_id",
    "vulnerability_id",
    "advisory",
    "oracle",
    "expected_patch",
}

SYSTEM_PROMPT = f"""You are an elite secure-code reasoning model generating SFT data.
Return exactly one sequence in this syntax and nothing else:
{THOUGHT_START}[comprehensive step-by-step reasoning over file contexts, call graph dependencies, and vulnerability surfaces]{THOUGHT_END}{PATCH_START}[complete clean fix patch]{PATCH_END}

Rules:
- Do not mention CVE IDs, hidden tests, oracle data, or reference patches.
- Do not claim knowledge of ground truth.
- The patch block must be one of:
  1. JSON: {{"files":[{{"path":"relative/file","content":"complete replacement content"}}]}}
  2. A unified diff that only modifies files present in the prompt.
- The patch must be complete, minimal, and compatible with the existing public API.
"""


@dataclass(frozen=True)
class VulnerableSample:
    sample_id: str
    source_files: list[SourceFile]
    ast_graph: Any
    test_command: list[str]
    hidden_test_files: list[SourceFile]
    image: str = DEFAULT_IMAGE
    public_context: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Generation:
    raw: str
    chosen: str
    thought: str
    patch: str


@dataclass(frozen=True)
class SampleResult:
    sample_id: str
    accepted: bool
    reason: str | None
    prompt: str | None = None
    chosen: str | None = None
    sandbox_result: dict[str, Any] | None = None
    model_latency_ms: int | None = None


class PipelineError(RuntimeError):
    """Raised for malformed samples, malformed model output, or sandbox failures."""


class GenerationParseError(PipelineError):
    """Raised when the model does not follow the required token schema."""


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_file_from_payload(payload: dict[str, Any]) -> SourceFile:
    return SourceFile(
        path=str(payload["path"]),
        content=str(payload["content"]),
        encoding=str(payload.get("encoding", "utf-8")),
        executable=bool(payload.get("executable", False)),
    )


def source_file_to_prompt(source: SourceFile, max_chars: int) -> dict[str, Any]:
    content = source.content
    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars] + "\n/* <|truncated_for_prompt|> */\n"
        truncated = True
    return {
        "path": source.path,
        "content": content,
        "encoding": source.encoding,
        "executable": source.executable,
        "truncated": truncated,
    }


def scrub_leakage(value: Any) -> Any:
    """Remove known ground-truth and CVE leakage fields from prompt material."""

    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if str(key).lower() in LEAKAGE_KEYS:
                continue
            output[key] = scrub_leakage(item)
        return output
    if isinstance(value, list):
        return [scrub_leakage(item) for item in value]
    if isinstance(value, str):
        return CVE_RE.sub("<|redacted_cve|>", value)
    return value


def sample_from_payload(payload: dict[str, Any]) -> VulnerableSample:
    clean = scrub_leakage(payload)
    source_payload = clean.get("source_files")
    if source_payload is None and "vulnerable_code" in clean:
        source_payload = [
            {
                "path": clean.get("path", "main.py"),
                "content": clean["vulnerable_code"],
            }
        ]
    if not source_payload:
        raise PipelineError("sample must contain source_files or vulnerable_code")
    hidden_test_payload = clean.get("hidden_test_files", [])
    if not hidden_test_payload:
        raise PipelineError("sample must contain hidden_test_files for verification")
    test_command = clean.get("test_command")
    if not test_command:
        raise PipelineError("sample must contain test_command")
    return VulnerableSample(
        sample_id=str(clean.get("sample_id") or clean.get("id") or sha256_text(stable_json(clean))[:16]),
        source_files=[source_file_from_payload(item) for item in source_payload],
        ast_graph=clean.get("ast_context_graph") or clean.get("ast_graph") or clean.get("call_graph") or {},
        test_command=list(test_command),
        hidden_test_files=[source_file_from_payload(item) for item in hidden_test_payload],
        image=str(clean.get("image", DEFAULT_IMAGE)),
        public_context=clean.get("public_context"),
        metadata=dict(clean.get("metadata", {})),
    )


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise PipelineError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc


def build_prompt(sample: VulnerableSample, max_file_chars: int, max_ast_chars: int) -> str:
    ast_graph = stable_json(scrub_leakage(sample.ast_graph))
    if len(ast_graph) > max_ast_chars:
        ast_graph = ast_graph[:max_ast_chars] + "\n<|ast_graph_truncated|>"
    prompt_payload = {
        "task": "Repair the vulnerable repository. Output only the required token sequence.",
        "required_output_schema": f"{THOUGHT_START}...{THOUGHT_END}{PATCH_START}...{PATCH_END}",
        "sample_id": sample.sample_id,
        "source_files": [source_file_to_prompt(source, max_file_chars) for source in sample.source_files],
        "structural_ast_call_graph_metadata": ast_graph,
        "public_context": scrub_leakage(sample.public_context),
        "anti_leakage_contract": [
            "No raw ground-truth patch is provided.",
            "No fix explanation or oracle output is provided.",
            "No target CVE identifier is provided.",
            "Reason only from source files and structural metadata.",
        ],
    }
    return SYSTEM_PROMPT + "\n\nUSER_CONTEXT_JSON:\n" + stable_json(prompt_payload)


class ModelClient:
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class OpenAICompatibleClient(ModelClient):
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None,
        temperature: float,
        max_tokens: int,
        timeout_seconds: int = 180,
    ) -> None:
        self.endpoint = validate_http_endpoint(endpoint)
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stop": [PATCH_END],
        }
        request = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise PipelineError(f"model HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise PipelineError(f"model endpoint error: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PipelineError(f"unexpected model response: {body}") from exc
        if PATCH_END not in content:
            content += PATCH_END
        return content

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


class MockResponseClient(ModelClient):
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.lock = threading.Lock()
        self.index = 0

    def generate(self, prompt: str) -> str:
        del prompt
        with self.lock:
            if self.index >= len(self.responses):
                raise PipelineError("mock response file ran out of responses")
            response = self.responses[self.index]
            self.index += 1
            return response


def load_mock_responses(path: Path) -> list[str]:
    responses: list[str] = []
    for payload in load_jsonl(path):
        if "response" in payload:
            responses.append(str(payload["response"]))
        else:
            responses.append(json.dumps(payload, ensure_ascii=False))
    return responses


def parse_generation(raw: str) -> Generation:
    match = GENERATION_RE.search(raw)
    if not match:
        raise GenerationParseError("model output did not match required thought/patch token schema")
    chosen = match.group(0)
    thought = match.group("thought").strip()
    patch = match.group("patch").strip()
    if not thought:
        raise GenerationParseError("empty thought block")
    if not patch:
        raise GenerationParseError("empty patch block")
    return Generation(raw=raw, chosen=chosen, thought=thought, patch=patch)


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json|diff|patch|[A-Za-z0-9_+-]+)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def extract_patch_files(patch_block: str, source_files: list[SourceFile]) -> list[SourceFile]:
    cleaned = strip_code_fence(patch_block)
    if cleaned.startswith("{"):
        return extract_json_patch(cleaned, source_files)
    if cleaned.startswith("--- ") or "\n--- " in cleaned:
        return extract_unified_diff_patch(cleaned, source_files)
    raise PipelineError("patch block must be JSON file replacement or unified diff")


def extract_json_patch(patch_block: str, source_files: list[SourceFile]) -> list[SourceFile]:
    try:
        payload = json.loads(patch_block)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"patch JSON parse failed: {exc}") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list) or not files:
        raise PipelineError("patch JSON must contain non-empty files list")
    source_by_path = {source.path: source for source in source_files}
    patched: list[SourceFile] = []
    for item in files:
        source = source_file_from_payload(item)
        if source.path not in source_by_path:
            raise PipelineError(f"patch attempted to modify unknown file: {source.path}")
        patched.append(source)
    return patched


def extract_unified_diff_patch(patch_block: str, source_files: list[SourceFile]) -> list[SourceFile]:
    source_by_path = {source.path: source for source in source_files}
    patched_contents = apply_unified_diff(source_by_path, patch_block)
    return [
        SourceFile(
            path=path,
            content=content,
            encoding=source_by_path[path].encoding,
            executable=source_by_path[path].executable,
        )
        for path, content in patched_contents.items()
    ]


def normalize_diff_path(path: str) -> str:
    path = path.strip()
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def apply_unified_diff(source_by_path: dict[str, SourceFile], patch_text: str) -> dict[str, str]:
    lines = patch_text.splitlines()
    index = 0
    patched: dict[str, str] = {}
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        old_path = normalize_diff_path(lines[index][4:].split("\t", 1)[0])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise PipelineError("malformed unified diff: missing +++ path")
        new_path = normalize_diff_path(lines[index][4:].split("\t", 1)[0])
        path = new_path if new_path != "/dev/null" else old_path
        if path not in source_by_path:
            raise PipelineError(f"diff attempted to modify unknown file: {path}")
        original = source_by_path[path].content.splitlines()
        output: list[str] = []
        original_index = 0
        index += 1
        while index < len(lines) and not lines[index].startswith("--- "):
            if not lines[index].startswith("@@"):
                index += 1
                continue
            hunk_header = lines[index]
            match = re.match(r"@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@", hunk_header)
            if not match:
                raise PipelineError(f"malformed hunk header: {hunk_header}")
            old_start = int(match.group("old_start")) - 1
            output.extend(original[original_index:old_start])
            original_index = old_start
            index += 1
            while index < len(lines) and not lines[index].startswith("@@") and not lines[index].startswith("--- "):
                line = lines[index]
                if line.startswith(" "):
                    expected = line[1:]
                    if original_index >= len(original) or original[original_index] != expected:
                        raise PipelineError(f"diff context mismatch in {path}")
                    output.append(expected)
                    original_index += 1
                elif line.startswith("-"):
                    expected = line[1:]
                    if original_index >= len(original) or original[original_index] != expected:
                        raise PipelineError(f"diff removal mismatch in {path}")
                    original_index += 1
                elif line.startswith("+"):
                    output.append(line[1:])
                elif line.startswith("\\"):
                    pass
                else:
                    raise PipelineError(f"unexpected diff line: {line}")
                index += 1
        output.extend(original[original_index:])
        patched[path] = "\n".join(output) + ("\n" if source_by_path[path].content.endswith("\n") else "")
    if not patched:
        raise PipelineError("unified diff contained no file patches")
    return patched


def merge_source_files(source_files: list[SourceFile], patched_files: list[SourceFile]) -> list[SourceFile]:
    merged = {source.path: source for source in source_files}
    for patched in patched_files:
        if patched.path not in merged:
            raise PipelineError(f"unknown patched file: {patched.path}")
        merged[patched.path] = patched
    return list(merged.values())


def verify_first_pass(sample: VulnerableSample, generation: Generation) -> dict[str, Any]:
    patched_files = extract_patch_files(generation.patch, sample.source_files)
    sandbox_files = merge_source_files(sample.source_files, patched_files) + sample.hidden_test_files
    request = SandboxRequest(
        files=sandbox_files,
        command=sample.test_command,
        image=sample.image,
        pull_missing_image=False,
    )
    result = DockerSandboxEngine().run(request)
    payload = asdict(result)
    if result.exit_code != 0 or result.timeout or not result.ok:
        raise PipelineError("sandbox rejected candidate on first execution")
    return payload


def immutable_append_jsonl(path: Path, payload: dict[str, Any], lock: threading.Lock) -> None:
    line = json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)


def process_sample(
    payload: dict[str, Any],
    client: ModelClient,
    dataset_out: Path,
    rejected_out: Path | None,
    output_lock: threading.Lock,
    max_file_chars: int,
    max_ast_chars: int,
) -> SampleResult:
    sample_id = str(payload.get("sample_id") or payload.get("id") or sha256_text(stable_json(payload))[:16])
    try:
        sample = sample_from_payload(payload)
        prompt = build_prompt(sample, max_file_chars=max_file_chars, max_ast_chars=max_ast_chars)
        start = time.perf_counter()
        raw = client.generate(prompt)
        latency_ms = int((time.perf_counter() - start) * 1000)
        generation = parse_generation(raw)
        sandbox_result = verify_first_pass(sample, generation)
        record = {"prompt": prompt, "chosen": generation.chosen}
        immutable_append_jsonl(dataset_out, record, output_lock)
        return SampleResult(
            sample_id=sample.sample_id,
            accepted=True,
            reason=None,
            prompt=prompt,
            chosen=generation.chosen,
            sandbox_result=sandbox_result,
            model_latency_ms=latency_ms,
        )
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        if rejected_out is not None:
            immutable_append_jsonl(
                rejected_out,
                {
                    "schema": "phase3.rejected.v2",
                    "sample_id": sample_id,
                    "reason": reason,
                    "created_at_unix_ms": int(time.time() * 1000),
                },
                output_lock,
            )
        return SampleResult(sample_id=sample_id, accepted=False, reason=reason)


def run_pipeline(args: argparse.Namespace) -> None:
    if args.mock_response_file:
        client: ModelClient = MockResponseClient(load_mock_responses(args.mock_response_file))
    else:
        api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
        client = OpenAICompatibleClient(
            endpoint=args.endpoint,
            model=args.model,
            api_key=api_key,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout_seconds=args.model_timeout_seconds,
        )

    samples = list(load_jsonl(args.input_jsonl))
    if args.limit is not None:
        samples = samples[: args.limit]
    output_lock = threading.Lock()
    accepted = 0
    rejected = 0
    results: list[SampleResult] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                process_sample,
                payload,
                client,
                args.dataset_out,
                args.rejected_out,
                output_lock,
                args.max_file_chars,
                args.max_ast_chars,
            )
            for payload in samples
        ]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            if result.accepted:
                accepted += 1
            else:
                rejected += 1
                if args.fail_fast:
                    raise PipelineError(result.reason or "sample rejected")

    summary = {
        "seen": len(samples),
        "accepted": accepted,
        "rejected": rejected,
        "dataset_out": str(args.dataset_out),
        "rejected_out": str(args.rejected_out) if args.rejected_out else None,
    }
    print(json.dumps(summary, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-threaded rejection sampling SFT curation pipeline.")
    parser.add_argument("--input-jsonl", required=True, type=Path)
    parser.add_argument("--dataset-out", required=True, type=Path)
    parser.add_argument("--rejected-out", type=Path)
    parser.add_argument("--endpoint", default=os.environ.get("BASE_MODEL_ENDPOINT", "http://localhost:8000/v1"))
    parser.add_argument("--model", default=os.environ.get("BASE_MODEL_NAME", "base-reasoning-model"))
    parser.add_argument("--api-key-env", default="BASE_MODEL_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--model-timeout-seconds", type=int, default=180)
    parser.add_argument("--mock-response-file", type=Path)
    parser.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 4))))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-file-chars", type=int, default=80_000)
    parser.add_argument("--max-ast-chars", type=int, default=120_000)
    parser.add_argument("--fail-fast", action="store_true")
    return parser.parse_args()


def main() -> None:
    run_pipeline(parse_args())


if __name__ == "__main__":
    main()
