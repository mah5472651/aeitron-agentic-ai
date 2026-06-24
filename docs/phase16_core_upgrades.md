# Phase 16 Core Architecture Upgrades

Phase 16 converts the target architecture gaps into executable local modules.

## Built

- Durable TaskGraph planner and JSON store.
- Role-specific async micro-agents: architect, coder, tester, debugger, security auditor, reviewer, researcher.
- CriticBackend and VerifierBackend interfaces.
- Composite verifier with defensive security review and Phase 2 sandbox verification.
- ExperienceMemory with failure/fix/outcome promotion from scorecard failures.
- Defensive tool adapters for Git, Semgrep, CodeQL, and browser-style documentation metadata fetching.
- Scorecard failure exporter for SFT and GRPO preference candidates.
- Real base-model connector probe for Qwen/DeepSeek/Llama-family OpenAI-compatible endpoints or PyTorch checkpoints.

## Run

```powershell
.\scripts\run_phase16_core.ps1
```

Outputs:

```text
artifacts/phase16/phase16-smoke.json
artifacts/phase16/phase16-smoke.md
artifacts/phase16/task_graphs/
artifacts/phase16/experience_memory.jsonl
artifacts/phase16/scorecard_failures_sft.jsonl
artifacts/phase16/scorecard_failures_grpo.jsonl
```

## Real Model Connection

Use a vLLM/OpenAI-compatible server:

```powershell
$env:PHASE16_BACKEND = "openai_compatible"
$env:PHASE16_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE16_MODEL_NAME = "qwen-or-deepseek-coder"
.\scripts\run_phase16_core.ps1
```

Or use a local PyTorch checkpoint:

```powershell
$env:PHASE16_BACKEND = "pytorch"
$env:PHASE16_CHECKPOINT = "C:\path\to\checkpoint.pt"
$env:PHASE16_TOKENIZER = "artifacts\mvp\code_bpe_tokenizer\tokenizer.json"
.\scripts\run_phase16_core.ps1
```

The local architecture checks pass without a real model. The base-model connector
turns green only after a real endpoint/checkpoint is configured.

## Install Defensive Security Tools

```powershell
.\scripts\install_security_tools.ps1
```

This provisions:

- Semgrep through the official Docker image.
- CodeQL CLI locally under `tools/codeql/codeql.exe`.

The FastAPI endpoint exposes availability:

```text
GET /v1/tools/advanced
```

## Start Local Qwen Backend

```powershell
.\scripts\start_phase16_real_backend.ps1
.\scripts\run_phase16_core_real.ps1
```

Default local model:

```text
Qwen/Qwen2.5-Coder-0.5B-Instruct
```

It serves an OpenAI-compatible API at:

```text
http://127.0.0.1:8016/v1
```

To connect the main chat API to it:

```powershell
$env:PHASE11_BACKEND = "openai_compatible"
$env:PHASE11_MODEL_ENDPOINT = "http://127.0.0.1:8016/v1"
$env:PHASE11_MODEL_NAME = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
.\scripts\start_phase11_chat_background.ps1
```

## Safety

Security tooling stays defensive:

- Static analysis
- Vulnerability detection
- Patch generation guidance
- Sandbox verification

No autonomous exploit execution is enabled.
