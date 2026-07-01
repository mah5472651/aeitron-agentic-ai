# Mythos Architecture V1 Productization

Mythos V1 consolidates the architecture around one serious runtime:

```text
Phase 40 Integrated Agent
  -> intent and expert routing
  -> durable task graph
  -> project-isolated ranked memory
  -> specialist execution
  -> critic and strict reflection
  -> verifier and defensive security
  -> immutable reports and failure promotion
```

The older phase modules remain implementation components. They are not separate
products or separate brains.

## Eight V1 Decisions

1. Git baseline and release tags protect known-good architecture states.
2. The capability registry assigns one owner and quality signal to each core capability.
3. The release gate executes exact golden and regression suites before release.
4. Backend comparison separates architecture-control quality from real-model quality.
5. Unified memory uses project isolation, vector ranking, deduplication, and cold archival.
6. Serious API requests enforce strict stability, verifier, and security policy.
7. The chat UI streams real runtime stages and exposes plan, memory, tools, and verification.
8. Training preflight validates reviewed SFT/GRPO contracts without pretending a GPU is present.

## Release Gate

Quick local gate:

```powershell
.\scripts\run_mythos_v1_release.ps1
```

Full gate with live Docker, databases, gateway, and sandbox:

```powershell
$env:MYTHOS_RELEASE_MODE = "full"
.\scripts\run_mythos_v1_release.ps1
```

Include the active real model comparison:

```powershell
$env:MYTHOS_COMPARE_REAL = "1"
.\scripts\run_mythos_v1_release.ps1
```

Current local Windows CPU backend:

- Default stable profile: `tiny-llama-cpu-smoke`
- Model: `hf-internal-testing/tiny-random-LlamaForCausalLM`
- Revision: `9fb191250dd56d0ba7ec9785a025ed29c03d5998`
- Purpose: prove OpenAI-compatible serving, routing, release gate, scorecard, and failure promotion work end to end.
- Limitation: this is a random tiny model, so Phase 18 correctly reports low model quality. Do not use its score as Titan/Mythos reasoning quality.

The `qwen-cpu-smoke` profile remains documented, but the local Windows CPU stack currently exits with a native Torch access violation while loading that checkpoint. Use Linux CUDA/vLLM or a compatible Torch/Python stack for 7B-32B quality runs.

The release gate blocks on:

- missing capability modules,
- Python compilation failures,
- Bandit findings,
- role-contract or memory-lifecycle failures,
- integrated agent failures,
- regression failures,
- golden scorecard failures,
- missing training architecture assets.

GPU absence and missing reviewed training rows are reported separately. They do
not make the local architecture dishonest or unusable.

## Memory Lifecycle

Phase 51 memory now provides:

- per-project directories,
- deterministic entry IDs and atomic upserts,
- hash embeddings locally and optional sentence-transformer embeddings,
- exact weighted retrieval scoring,
- retrieval history,
- low-quality cold archival,
- optional Qdrant/PostgreSQL synchronization,
- session working-memory deletion.

## Training Contracts

Tracked schemas:

- `data/training/sft_record.schema.json`
- `data/training/grpo_record.schema.json`

Manifest:

- `config/mythos_v1_training_manifest.json`

Preflight:

```powershell
.\scripts\run_mythos_v1_training_preflight.ps1
```

Training remains blocked until reviewed data and Linux CUDA hardware exist.
After training, Phase 39 checkpoint comparison is mandatory before promotion.
