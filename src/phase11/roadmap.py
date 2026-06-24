#!/usr/bin/env python
"""Phase 11 architecture roadmap split from the six core tracks."""

from __future__ import annotations


ROADMAP = [
    {
        "track": "Unified AI Core Interface",
        "steps": [
            {
                "id": "core-01",
                "title": "Backend contract",
                "status": "implemented",
                "detail": "One ModelBackend API for mock, PyTorch checkpoints, vLLM/OpenAI-compatible endpoints, and future trained models.",
            },
            {
                "id": "core-02",
                "title": "Generation schema",
                "status": "implemented",
                "detail": "Strict request/response schemas for messages, generation config, usage estimates, and metadata.",
            },
            {
                "id": "core-03",
                "title": "Backend swap layer",
                "status": "implemented",
                "detail": "Environment-configured backend factory can swap mock, PyTorch, or OpenAI-compatible serving without changing API code.",
            },
            {
                "id": "core-04",
                "title": "Tokenizer adapter",
                "status": "implemented",
                "detail": "Hugging Face tokenizers artifact loader with deterministic char fallback for local CPU smoke tests.",
            },
        ],
    },
    {
        "track": "Chat and Agent API Backend",
        "steps": [
            {
                "id": "api-05",
                "title": "Session memory",
                "status": "implemented",
                "detail": "In-process chat sessions retain system, user, and assistant messages for local development.",
            },
            {
                "id": "api-06",
                "title": "Static chat interface",
                "status": "implemented",
                "detail": "HTML, CSS, and JavaScript are separated under src/phase11/static with user-logo support.",
            },
            {
                "id": "api-07",
                "title": "Agent endpoint",
                "status": "implemented",
                "detail": "FastAPI exposes /v1/agent/run for full planner-memory-security-sandbox-review workflow reports.",
            },
            {
                "id": "api-08",
                "title": "Tool call bus",
                "status": "implemented",
                "detail": "FastAPI exposes validated tool listing and tool execution endpoints for file, security, and sandbox tools.",
            },
            {
                "id": "api-09",
                "title": "Memory endpoints",
                "status": "implemented",
                "detail": "FastAPI exposes context retrieval and persistent-memory indexing endpoints.",
            },
        ],
    },
    {
        "track": "Agentic Coding Runtime v2",
        "steps": [
            {
                "id": "agent-10",
                "title": "Intent expansion",
                "status": "implemented",
                "detail": "Short prompts are expanded into an engineering task with likely workflow and constraints.",
            },
            {
                "id": "agent-11",
                "title": "Planner",
                "status": "implemented",
                "detail": "Planner creates a prioritized task graph, priority files, and verification strategy.",
            },
            {
                "id": "agent-12",
                "title": "Code editor",
                "status": "implemented",
                "detail": "Structured patch blocks can be parsed and optionally written only after path and security checks.",
            },
            {
                "id": "agent-13",
                "title": "Test runner",
                "status": "implemented",
                "detail": "Runtime can route verification through the Phase 2 Docker sandbox when enabled.",
            },
            {
                "id": "agent-14",
                "title": "Debugger",
                "status": "implemented",
                "detail": "Sandbox/stdout/stderr telemetry is converted into a structured diagnosis block on failure.",
            },
            {
                "id": "agent-15",
                "title": "Self-healer",
                "status": "implemented",
                "detail": "Failure telemetry can be re-injected into the backend for a bounded repair cycle.",
            },
            {
                "id": "agent-16",
                "title": "Adversarial reviewer",
                "status": "implemented",
                "detail": "Reviewer combines security score, sandbox status, and patch evidence into a confidence gate.",
            },
            {
                "id": "agent-17",
                "title": "Final patch generator",
                "status": "implemented",
                "detail": "Final answer includes generated solution, reviewer result, and runtime diagnosis if any.",
            },
        ],
    },
    {
        "track": "Long Context Memory Engine",
        "steps": [
            {
                "id": "memory-18",
                "title": "Workspace scanner",
                "status": "implemented",
                "detail": "Source and doc files are indexed with hashes, symbols, token estimates, and safe ignores.",
            },
            {
                "id": "memory-19",
                "title": "Context retrieval",
                "status": "implemented",
                "detail": "Prompt terms, file paths, and symbols are scored into a compact ContextPack.",
            },
            {
                "id": "memory-20",
                "title": "Call graph enrichment",
                "status": "implemented",
                "detail": "If AST/callgraph JSONL is available, matching structural records are added into context.",
            },
            {
                "id": "memory-21",
                "title": "Embedding contract",
                "status": "implemented",
                "detail": "Deterministic hash embeddings give the architecture a stable vector contract before trained embeddings arrive.",
            },
            {
                "id": "memory-22",
                "title": "Persistent memory gateway",
                "status": "implemented",
                "detail": "Memory records can upsert to local, Redis, PostgreSQL, and Qdrant backends through one gateway.",
            },
            {
                "id": "memory-23",
                "title": "Context packer",
                "status": "implemented",
                "detail": "Retrieved source, symbols, and call graph metadata are packed under a token budget for the model backend.",
            },
        ],
    },
    {
        "track": "Security Reasoning Engine",
        "steps": [
            {
                "id": "sec-24",
                "title": "Rule-based triage",
                "status": "implemented",
                "detail": "Detects unsafe buffer copy, SQL injection, shell injection, weak crypto, traversal, and deserialization patterns.",
            },
            {
                "id": "sec-25",
                "title": "Workspace security review",
                "status": "implemented",
                "detail": "Scores production source files while excluding fixtures/samples unless explicitly requested.",
            },
            {
                "id": "sec-26",
                "title": "Patch regression review",
                "status": "implemented",
                "detail": "Compares before/after patch content and rejects patches that introduce new obvious security risk.",
            },
            {
                "id": "sec-27",
                "title": "Sandbox verification",
                "status": "implemented",
                "detail": "Security workflow can verify generated Python patch/test files through the hardened Phase 2 Docker sandbox.",
            },
        ],
    },
    {
        "track": "PyTorch Model Skeleton",
        "steps": [
            {
                "id": "torch-28",
                "title": "Decoder transformer",
                "status": "implemented",
                "detail": "Local decoder-only transformer with causal attention, top-p generation, and configurable dimensions.",
            },
            {
                "id": "torch-29",
                "title": "Checkpoint format",
                "status": "implemented",
                "detail": "Config and state dict save/load are ready, using safer weights-only checkpoint loading.",
            },
            {
                "id": "torch-30",
                "title": "Inference wrapper",
                "status": "implemented",
                "detail": "PyTorchCausalLMBackend wraps tokenizer, checkpoint loading, device selection, and generation.",
            },
            {
                "id": "torch-31",
                "title": "Training bridge",
                "status": "implemented",
                "detail": "Future GRPO/SFT/QLoRA outputs can plug into the same checkpoint/tokenizer/backend API.",
            },
        ],
    },
]
