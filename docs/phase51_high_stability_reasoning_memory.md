# Phase 51: High-Stability Reasoning And Unified Memory

Phase 51 adds a strict backend control layer for reasoning and memory. It is
designed to prevent role mixing, context pollution, and low-quality memory
growth over time.

## Code

- `src/phase51/high_stability_reasoning_memory.py`
- `scripts/run_phase51_high_stability.ps1`

## Module 1: Strict Reasoning Engine

The `ReasoningEngine` runs:

```text
Think -> Execute -> Reflect -> Revise
```

Roles are separated by contract:

- Planner: creates `PlannerOutput` task graph only. It cannot write executable
  code or commands.
- Executor: executes the planner task graph only. It cannot reorder or alter
  the plan.
- Critic: computes confidence and reports flaws only. It cannot provide a
  solution or patch.
- Verifier: checks schema, step order, fingerprints, and success criteria only.
  It does not reason or revise.

All outputs are validated through strict Pydantic JSON schemas:

- `PlannerOutput`
- `ExecutorOutput`
- `CriticOutput`
- `VerifierOutput`
- `ReflectionOutput`
- `ReasoningTrace`

Reflection trigger:

- If critic confidence is `< 0.6`, the engine forces a reflection pass.
- The reflection pass always uses these prompts:

```text
What assumptions are wrong?
What can fail?
What security risks exist?
What was not verified?
```

## Module 2: Unified Memory Manager

`UnifiedMemoryManager` has four layers:

| Layer | Purpose | TTL | Format |
| --- | --- | --- | --- |
| Working Memory | Current task context | 1 session | `{"project": "...", "current_feature": "..."}` |
| Project Memory | Repository and architecture knowledge | Project lifetime | `{"module_name": "...", "path": "...", "tech_stack": "..."}` |
| Experience Memory | Solved failure/fix records | Durable | `{"failure": "...", "fix": "...", "context": "..."}` |
| Knowledge Graph | Concept relationships | Durable | nodes + edges |

## Module 3: Memory Gatekeeper

Allowed memory kinds:

- `verified_fix`
- `passed_benchmark`
- `security_finding`
- `successful_plan`

Rejected memory kinds:

- `raw_thought`
- `failed_guess`
- `temporary_output`

Every accepted memory entry includes:

```json
{
  "relevance": 0.0,
  "success_rate": 0.0,
  "last_used": 0.0,
  "usage_count": 0
}
```

Retrieval ranking uses the requested formula exactly:

```text
Final Score =
  (0.4 * Vector Similarity)
  + (0.3 * Success Rate)
  + (0.2 * Recency Weight)
  + (0.1 * Usage Count Weight)
```

The current local vector similarity is deterministic lexical similarity. The
interface can later be swapped for embedding vectors without changing the
ranking formula.

Archival:

- `archive_low_quality()` moves entries with consistently low quality into
  `cold_storage.jsonl`.
- Working memory can be cleared per session.

## Run

```powershell
.\scripts\run_phase51_high_stability.ps1
```

Direct:

```powershell
python src\phase51\high_stability_reasoning_memory.py --prompt "build strict planner executor critic verifier memory architecture"
```

Output:

```text
artifacts/phase51/high-stability-reasoning-memory-latest.json
```

## Integration With Existing Architecture

Phase 51 is not meant to replace the earlier phases or create a disconnected
parallel brain. It now sits on top of the existing Phase 40 integrated agent
path as a stability layer:

```text
Phase 40 integrated agent
  -> Phase 50 route
  -> Phase 43 meta-plan
  -> Phase 46 hierarchical memory
  -> Phase 51 strict memory retrieval
  -> Phase 37 vector memory
  -> Phase 24 / Phase 20 agent execution
  -> Phase 22 critic
  -> Phase 47 reasoning review
  -> Phase 51 strict role-contract review
  -> Phase 27 / Phase 38 verification
```

This means Phase 51 is both:

- a standalone contract test module, and
- an active guardrail inside the main agent runtime.

## Why This Exists

Earlier phases already had planning, reasoning, and memory pieces. Phase 51 is
the stricter stability contract:

- no role mixing,
- no unvalidated free-form component outputs,
- no raw thought ingestion,
- no failed guesses in durable memory,
- explicit mathematical ranking,
- explicit reflection trigger below confidence `0.6`.
