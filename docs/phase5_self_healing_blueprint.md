# Phase 5 Self-Healing Runtime and QLoRA Staging Blueprint

## Integrated Runtime

The runtime wraps the Swarm/agent reasoning layer and the hardened Phase 2
Sandbox Engine.

1. The agent system emits a code artifact and reasoning path.
2. The sandbox compiles or executes the artifact.
3. If the sandbox crashes or exits nonzero, Phase 5 captures telemetry:
   - GCC/Clang warnings and errors
   - Python tracebacks
   - sanitizer findings
   - GDB/core-dump style frames
   - stack/register addresses
   - timeout flags
   - CPU and RAM metrics
4. The crash dump becomes an explicit `runtime_exception_trace`.
5. The repair model re-analyzes the failed reasoning path and proposes one patch.
6. The loop repeats up to exactly `5` recursive correction iterations.
7. On success, the full lifecycle becomes a token-ready training sequence.

## Captured Lifecycle

```text
Initial Prompt
-> Failed Reasoning Path
-> Caught Telemetry Logs
-> Corrected Reasoning Trace
-> Success Verification Patch
```

Token format:

```text
<|self_heal_start|>
<|initial_prompt|>...
<|failed_reasoning_path|>...
<|caught_telemetry_logs|>...
<|corrected_reasoning_trace|>...
<|success_verification_patch|>...
<|self_heal_end|>
```

## PostgreSQL Schema

Tables initialized by `POSTGRES_SCHEMA_SQL`:

- `healing_lifecycle_traces`
  - one successful self-healing trace
  - immutable hash
  - token-ready training sequence
  - promoted flag for offline QLoRA batching
- `healing_repair_iterations`
  - every failed and successful correction attempt
  - recursion depth
  - telemetry and sandbox result
- `qlora_training_jobs`
  - queued offline fine-tuning jobs
  - trace IDs
  - dataset path
  - base model and adapter output target

## Qdrant Collection

Default collection:

```text
self_healing_qlora_staging
```

Indexed text:

- initial prompt
- failed reasoning path
- crash telemetry
- corrected reasoning trace
- successful patch

Payload:

```json
{
  "trace_id": "string",
  "created_at_unix_ms": 0,
  "immutable_hash": "sha256",
  "metadata": {}
}
```

## Cron Worker

The cron worker polls PostgreSQL for unpromoted successful traces.

When the buffer count reaches `threshold_block_size`, it:

1. writes a JSONL QLoRA batch dataset
2. creates a `qlora_training_jobs` row
3. marks traces as promoted

This queues offline training safely without modifying online serving weights.

## Source

```text
src/phase5/self_healing_runtime.py
```
