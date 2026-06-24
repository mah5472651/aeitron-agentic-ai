# Phase 37-39 Production Hardening

These phases close the next three production gaps after auth, observability, and
the data flywheel.

## Phase 37: Production Vector Memory

Purpose:

- Replace Phase 25 token matching as the only retrieval path.
- Store failure/fix/outcome experience records as vector-searchable memories.
- Keep a local deterministic vector index for CPU development.
- Mirror the same records into Qdrant/PostgreSQL when production services are configured.

Primary file:

```text
src/phase37/vector_memory.py
```

Run:

```powershell
.\scripts\run_phase37_vector_memory.ps1 -Rebuild -Query "security patch verifier failure"
```

API:

```text
POST /v1/memory/vector/retrieve
```

Why it exists:

At 10k+ experience records, keyword overlap alone becomes noisy. Phase 37 gives
the planner a stable vector memory contract now, while allowing a trained code
embedding model later.

## Phase 38: Multi-Language Security Engine

Purpose:

- Add explicit Rust, Go, JavaScript/TypeScript, and Solidity defensive rules.
- Keep security behavior defensive: detection, patch guidance, verification.
- Produce language-specific patch guidance and regression recommendations.

Primary file:

```text
src/phase38/multilang_security.py
```

Run:

```powershell
.\scripts\run_phase38_multilang_security.ps1 -Workspace .
```

API:

```text
POST /v1/security/multilang
```

Coverage examples:

- Rust unsafe boundary review, command execution, panic-on-input paths.
- Go command execution, SQL formatting, path traversal surfaces.
- JavaScript child_process, SQL string building, prototype pollution.
- Solidity tx.origin authorization, reentrancy-prone calls, selfdestruct.

## Phase 39: Training Checkpoint Rollback Gate

Purpose:

- Prevent SFT/GRPO training from silently degrading the active model.
- Compare candidate evaluation metrics against a baseline or active checkpoint.
- Promote only non-regressing checkpoints.
- Keep rollback manifests for restoring the last known-good active pointer.

Primary file:

```text
src/phase39/checkpoint_rollback.py
```

Run:

```powershell
.\scripts\run_phase39_checkpoint_gate.ps1 `
  -CandidateCheckpoint artifacts\models\candidate `
  -CandidateReport artifacts\eval\candidate.json `
  -BaselineReport artifacts\eval\baseline.json `
  -DryRun
```

Production flow:

1. Phase 7 or Phase 17 writes a candidate checkpoint.
2. Phase 9/18/23 evaluates the checkpoint.
3. Phase 39 compares required metrics.
4. If metrics pass, Phase 39 promotes the checkpoint.
5. If metrics regress, Phase 39 keeps the active pointer unchanged and writes a rollback manifest.

Default protected metrics:

- `overall_score`
- `pass_at_1`
- `security_score`

Default max allowed drop:

```text
0.02
```

## Current Boundary

These phases are production-control architecture, not a replacement for real
model quality. They make the system safer and more scalable, but stronger model
behavior still depends on better data, stronger base models, and verified
training loops.
