# Phase 11 Architecture Plan

The original six tracks are now split into concrete build steps. This is the
architecture-first path before model training.

## 1. Unified AI Core Interface

1. Backend contract: one `ModelBackend` API for mock, PyTorch checkpoints,
   vLLM/OpenAI-compatible servers, and future trained models.
2. Generation schema: strict typed messages, generation config, token estimates,
   and response metadata.
3. Backend swap layer: environment-driven backend selection without changing the
   chat, agent, memory, or security code.
4. Tokenizer adapter: Hugging Face tokenizer artifact loader plus local fallback
   tokenizer for CPU smoke tests.

## 2. Chat and Agent API Backend

5. Session memory: local session history with system/user/assistant messages.
6. Static chat interface: separate HTML, CSS, and JavaScript under
   `src/phase11/static`.
7. Agent endpoint: `/v1/agent/run` returns full structured runtime reports.
8. Tool call bus: `/v1/tools` and `/v1/tools/call` expose validated runtime
   tools.
9. Memory endpoints: `/v1/memory/retrieve` and `/v1/memory/index` expose context
   packing and persistent memory indexing.

## 3. Agentic Coding Runtime v2

10. Intent expansion: short prompts are expanded into actionable engineering
    intent.
11. Planner: priority files, staged task graph, and verification strategy.
12. Code editor: structured patches can be parsed, path checked, security
    reviewed, and optionally applied.
13. Test runner: verification can route through the hardened Docker sandbox.
14. Debugger: failed stdout/stderr telemetry becomes a structured diagnosis.
15. Self-healer: failed telemetry can be re-injected into the model for a
    bounded correction cycle.
16. Adversarial reviewer: security score, sandbox status, and patch evidence are
    combined into a confidence gate.
17. Final patch generator: final output includes model answer, reviewer score,
    and failure diagnosis when relevant.

## 4. Long Context Memory Engine

18. Workspace scanner: source and documentation files are indexed with hashes,
    symbols, and token estimates.
19. Context retrieval: prompt terms, file paths, and symbols are scored into a
    compact context pack.
20. Call graph enrichment: existing AST/callgraph JSONL records can be added to
    retrieved context.
21. Embedding contract: deterministic hash embeddings give the vector path a
    stable interface before trained code embeddings arrive.
22. Persistent memory gateway: local, Redis, PostgreSQL, and Qdrant upsert paths
    share one API.
23. Context packer: retrieved source, symbols, call graph records, and metadata
    are packed under a token budget.

## 5. Security Reasoning Engine

24. Rule-based triage: detect unsafe copy, SQL injection, shell injection, weak
    crypto, traversal, and unsafe deserialization patterns.
25. Workspace security review: production source is scanned while fixtures are
    excluded by default.
26. Patch regression review: before/after patches are checked for newly
    introduced obvious security issues.
27. Sandbox verification: generated Python patch/test files can be verified in
    the Phase 2 Docker sandbox.

## 6. PyTorch Model Skeleton

28. Decoder transformer: local decoder-only architecture with causal attention
    and top-p generation.
29. Checkpoint format: config/state dict save and safer weights-only load.
30. Inference wrapper: tokenizer, checkpoint, device, and generation are wrapped
    behind `PyTorchCausalLMBackend`.
31. Training bridge: later SFT/GRPO/QLoRA checkpoints can plug into the same
    tokenizer/backend contract.

## Current Local Status

Implemented now:

- Steps 1-31 have concrete local modules or endpoints.
- GPU-heavy model training, AWQ quantization, and real large-scale embeddings are
  intentionally later phases.

Next hardening options:

- Persistent database-backed chat sessions.
- Real semantic embedding model for memory retrieval.
- Browser-based file explorer and diff preview.
- Fine-grained project permissions per workspace.
- Live tool timeline in the chat interface.
