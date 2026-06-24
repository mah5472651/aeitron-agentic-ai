# Phase 13 Backend Quality Harness

Phase 13 compares real backend response quality. Phase 12 tells us whether the
architecture can run the workflow. Phase 13 tells us whether a backend/model
answers coding and cybersecurity prompts well enough to trust.

## What It Measures

- Short prompt response quality
- Coding answer specificity
- Security analysis correctness signals
- Debugging and self-healing reasoning
- Required output format compliance
- Agentic workflow reasoning
- Long-context memory explanation quality

Each task is scored with keyword coverage, required reasoning markers, answer
length, structure, and forbidden generic/refusal terms. The harness reports a
baseline score, candidate score, category deltas, winner counts, and whether the
candidate meets the target score.

## Run Local Comparison

```powershell
.\scripts\run_phase13_quality.ps1
```

Default behavior:

- Starts the local mock vLLM/gateway stack if needed.
- Uses `mock` as the architecture baseline.
- Uses the local OpenAI-compatible mock vLLM endpoint as the candidate.
- Writes reports to `artifacts/phase13/`.
- Exports tasks to `data/phase13/backend_quality_tasks.jsonl`.

## Compare A Real Backend

```powershell
$env:PHASE13_CANDIDATE_BACKEND = "openai_compatible"
$env:PHASE13_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE13_MODEL_NAME = "security-coder"
$env:PHASE13_SUITE = "full"
.\scripts\run_phase13_quality.ps1
```

For an external compatible endpoint:

```powershell
$env:PHASE13_MODEL_ENDPOINT = "https://your-server.example/v1"
$env:PHASE13_MODEL_NAME = "your-model"
$env:PHASE13_API_KEY = "..."
.\scripts\run_phase13_quality.ps1
```

## Compare Future PyTorch Checkpoint

```powershell
$env:PHASE13_CANDIDATE_BACKEND = "pytorch"
$env:PHASE13_CANDIDATE_CHECKPOINT = "artifacts\models\mythos_decoder.pt"
$env:PHASE13_CANDIDATE_TOKENIZER = "artifacts\tokenizer\mythos-code-bpe.json"
.\scripts\run_phase13_quality.ps1
```

## How To Read It

- `baseline_score`: architecture control score.
- `candidate_score`: model/backend quality score.
- `score_delta`: candidate minus baseline.
- `candidate_ready`: true only if candidate score is above the configured target
  and has no failed task.
- `winner_counts`: task-by-task response comparison.

The local mock vLLM is expected to score low because it returns a fixed smoke
response. That is useful: it proves the harness catches weak backends. A real
model should beat the baseline in the categories we care about.
