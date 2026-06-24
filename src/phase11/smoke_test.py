#!/usr/bin/env python
"""Phase 11 smoke test for PyTorch core, memory, security, and agent runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.agentic_runtime import AgenticCodingRuntime
from src.phase11.memory_engine import WorkspaceMemoryEngine
from src.phase11.model_backends import MockReasoningBackend, PyTorchCausalLMBackend
from src.phase11.persistent_memory import PersistentMemoryGateway
from src.phase11.pytorch_model import DecoderConfig, DecoderOnlyTransformer
from src.phase11.roadmap import ROADMAP
from src.phase11.schemas import AgentRunRequest
from src.phase11.security_engine import SecurityReasoningEngine
from src.phase11.tokenization import load_tokenizer
from src.phase11.tool_runtime import ToolRegistry


async def run_smoke() -> dict:
    with tempfile.TemporaryDirectory(prefix="phase11_smoke_") as tmp:
        root = Path(tmp)
        (root / "app.py").write_text(
            "import hashlib\n\n"
            "def hash_password(password):\n"
            "    return hashlib.md5(password.encode()).hexdigest()\n",
            encoding="utf-8",
        )
        memory = WorkspaceMemoryEngine(root)
        context = memory.retrieve("fix security issue", token_budget=2000)
        tokenizer = load_tokenizer(None, fallback_vocab_size=256)
        memory_gateway = PersistentMemoryGateway(workspace=str(root))
        memory_records = memory.export_memory_records(gateway=memory_gateway, max_records=4)
        await memory_gateway.upsert(memory_records[:1])
        memory_search = memory_gateway.search_local("hash password security", limit=1)
        tools = ToolRegistry(root)
        tool_specs = tools.specs()
        list_files = await tools.call("list_files", {"max_files": 10})
        read_file = await tools.call("read_file", {"path": "app.py"})
        security = SecurityReasoningEngine().analyze_workspace(root)

        model = DecoderOnlyTransformer(DecoderConfig(vocab_size=256, max_seq_len=64, d_model=64, n_layers=1, n_heads=4, d_ff=128))
        logits = model(torch.tensor([[1, 2, 3, 4]], dtype=torch.long))
        pytorch_backend = PyTorchCausalLMBackend(config=DecoderConfig(vocab_size=256, max_seq_len=64, d_model=64, n_layers=1, n_heads=4, d_ff=128))
        runtime = AgenticCodingRuntime(MockReasoningBackend())
        report = await runtime.run(
            AgentRunRequest(
                prompt="fix security issue",
                workspace=str(root),
                allow_writes=False,
                allow_sandbox=False,
                context_token_budget=2000,
            )
        )
        checks = {
            "memory_context": len(context.items) >= 1,
            "tokenizer_loader": tokenizer.vocab_size == 256 and len(tokenizer.encode("abc")) == 3,
            "persistent_memory": len(memory_records) >= 1 and len(memory_search) == 1,
            "tool_registry": any(spec.name == "sandbox_python" for spec in tool_specs) and list_files.ok and read_file.ok,
            "security_finding": len(security.findings) >= 1,
            "pytorch_forward": list(logits.shape) == [1, 4, 256],
            "pytorch_backend": pytorch_backend.model.config.vocab_size == 256,
            "agent_report": report.status == "complete" and report.confidence > 0,
            "roadmap_20_steps": sum(len(track["steps"]) for track in ROADMAP) >= 20,
            "static_frontend": (Path(__file__).with_name("static") / "index.html").exists(),
        }
        return {"passed": all(checks.values()), "checks": checks, "agent_run_id": report.run_id}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 11 smoke test.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run_smoke())
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
