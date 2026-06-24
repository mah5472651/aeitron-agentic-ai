# Phase 43-50 Cognitive Architecture

This document covers the final cognitive control layer added on top of the
existing agent, verifier, memory, and training-readiness stack. The goal is to
move the system closer to a Cursor + Claude Code + DeepSeek R1 style coding and
security AI: short prompts are expanded, complex work is planned as a graph,
specialists run in parallel, memory informs decisions, and outputs are reviewed
before acceptance.

## Phase 43: Meta Planner

File:

- `src/phase43/meta_planner.py`

Purpose:

- Convert a broad user request into a durable architecture plan.
- Produce requirements, risks, components, execution lanes, and a TaskGraph
  brief.
- Make the planner more than a one-shot prompt expansion layer.

When it runs:

- Before serious agentic coding work.
- Inside Phase 40 when `meta_planning=True`.
- During the Phase 43-50 E2E contract test.

Why it exists:

- Most coding agents fail by starting implementation before decomposing the
  actual system. Phase 43 forces requirements, verification, and security into
  the plan first.

## Phase 44: Intent Expansion Engine

File:

- `src/phase44/intent_expansion.py`

Purpose:

- Expand tiny prompts like `login system` into domain requirements,
  acceptance tests, security requirements, and edge cases.

When it runs:

- Before Phase 43 planning for short or underspecified prompts.
- As a standalone analyzer for prompt-understanding tests.

Why it exists:

- The target AI must handle short prompts well. This layer gives the rest of
  the stack a richer interpretation without needing the user to write a giant
  prompt.

## Phase 45: Parallel Agent Runtime

File:

- `src/phase45/parallel_agent_runtime.py`

Purpose:

- Execute specialist lanes from the meta-plan concurrently.
- Use role agents such as architect, coder, tester, security auditor,
  researcher, and reviewer.
- Aggregate artifacts and surface conflicts.

When it runs:

- For broad tasks that benefit from parallel expert work.
- In mock mode for plumbing tests, or against the active model backend later.

Why it exists:

- Linear agents are slow and often overfit to one perspective. Parallel lanes
  let the system compare specialized outputs before final synthesis.

## Phase 46: Hierarchical Memory

File:

- `src/phase46/hierarchical_memory.py`

Purpose:

- Provide five memory layers: working, session, project, experience, and
  knowledge.
- Render a compact context block for planner/model prompts.

When it runs:

- Inside Phase 40 when `hierarchical_memory=True`.
- Before planning or review when past failures and project rules matter.

Why it exists:

- Long context cannot be solved only by bigger attention windows. Hierarchical
  memory keeps recent context, durable project knowledge, and past failure/fix
  outcomes separate but retrievable.

## Phase 47: Reasoning Engine

File:

- `src/phase47/reasoning_engine.py`

Purpose:

- Split reasoning into thinker, critic, and verifier stages.
- Decide whether an answer is accepted or needs more review.

When it runs:

- Inside Phase 40 when `reasoning_review=True`.
- For standalone reasoning quality checks.

Why it exists:

- A single generated answer is not enough. The system needs a dedicated review
  track that can catch missing tests, missing security, weak verification, or
  unsafe destructive actions.

## Phase 48: Knowledge Graph

File:

- `src/phase48/knowledge_graph.py`

Purpose:

- Store architecture phases, dependencies, patterns, bugs, fixes, and tools as
  graph relationships.
- Return graph context for planner and memory layers.

When it runs:

- During architecture context building and Phase 43-50 E2E checks.
- Later it can be fed by repository parsing, experience memory, and evaluation
  history.

Why it exists:

- Vector search is good for similarity, but explicit relationships are better
  for dependency reasoning: what feeds what, what verifies what, and where
  failures should be promoted.

## Phase 49: Multimodal Expert

File:

- `src/phase49/multimodal_expert.py`

Purpose:

- Create a safe local contract for images, screenshots, diagrams, PDFs, and
  repository folders.
- Extract metadata and route visual/document inputs into the planner.

When it runs:

- When the request includes screenshots, diagrams, PDFs, images, or attached
  repositories.

Why it exists:

- The future AI should reason over more than text. This phase gives the system
  a schema-ready multimodal slot now, while keeping CPU-safe local behavior.

## Phase 50: MoE Routing Layer

File:

- `src/phase50/moe_router.py`

Purpose:

- Route prompts to expert families: security, planning, coding, reasoning,
  memory, multimodal, and research.
- Return primary expert plus execution hints.

When it runs:

- Inside Phase 40 when `moe_routing=True`.
- Before running specialist workflows.

Why it exists:

- Long-term 50B-100B or MoE models will still need a router. This software
  router is the controllable version of that idea today.

## Integrated Path

Default serious request path:

```text
User prompt
  -> Phase 50 MoE route
  -> Phase 44 intent expansion when needed
  -> Phase 43 meta-plan
  -> Phase 46 hierarchical memory
  -> Phase 37 vector experience memory
  -> Phase 24 / Phase 20 agent execution
  -> Phase 22 critic
  -> Phase 47 reasoning review
  -> Phase 27 verifier policy
  -> Phase 38 multi-language security
  -> Phase 21 failure/fix/outcome memory promotion
```

## Commands

```powershell
python src\phase43\meta_planner.py --prompt "build secure login system"
python src\phase44\intent_expansion.py --prompt "login system"
python src\phase45\parallel_agent_runtime.py --prompt "build secure login system" --backend-mode mock
python src\phase46\hierarchical_memory.py --query "secure planner verifier" --seed
python src\phase47\reasoning_engine.py --prompt "debug secure login architecture"
python src\phase48\knowledge_graph.py --query "meta planner memory reasoning" --seed
python src\phase49\multimodal_expert.py --prompt "analyze architecture assets" --path .
python src\phase50\moe_router.py --prompt "build secure login system"
python src\phase50\phase43_to_50_e2e.py --prompt "build secure login system"
```

## Production Notes

- Phase 45 can run with mock backend locally and real model backends later.
- Phase 46 should later connect to Redis/Postgres/Qdrant-backed storage.
- Phase 47 is a local reasoning contract today; a dedicated critic/reasoner
  checkpoint can replace the heuristic layer later.
- Phase 48 should be fed by Phase 1 call graphs and Phase 21 experience memory.
- Phase 49 is metadata-only locally; vision/OCR adapters can be attached later.
- Phase 50 is the control-plane router for future expert models or MoE serving.

