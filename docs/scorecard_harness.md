# AI Architecture Scorecard

This is the exact scorecard gate for the architecture.

## Metrics

- `architecture_reliability_score`
- `agent_workflow_completion_score`
- `security_detection_fix_score`
- `short_prompt_understanding_score`
- `sandbox_test_pass_rate`
- `regression_count`

## Golden Dataset

The harness exports:

```text
data/scorecard/golden_tasks.jsonl
```

It contains exactly:

- 20 short prompt coding tasks
- 20 debugging tasks
- 20 security finding tasks
- 20 patch generation tasks
- 10 long-context repo tasks

## Run Both Modes

```powershell
.\scripts\run_scorecard.ps1
```

Default:

- Mock mode checks architecture plumbing.
- Real mode checks an OpenAI/vLLM-compatible backend.
- Local default endpoint is `http://127.0.0.1:8000/v1`.

## Run Against A Real Model

```powershell
$env:SCORECARD_REAL_BACKEND = "openai_compatible"
$env:SCORECARD_MODEL_ENDPOINT = "http://your-model-server:8000/v1"
$env:SCORECARD_MODEL_NAME = "your-model"
$env:SCORECARD_REQUIRE_REAL_READY = "1"
.\scripts\run_scorecard.ps1
```

## Failure Auto Report

Every failed task reports:

- which task failed
- which phase failed
- whether it looks like memory, planner, sandbox, security, patch, or model output
- exact fix recommendation

Reports are written to:

```text
artifacts/scorecard/
```

The latest report is also exposed through the chat API:

```text
GET /v1/scorecard/latest
```
