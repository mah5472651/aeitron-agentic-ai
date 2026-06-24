# Phase 9: Automated Evaluation Harness

Phase 9 measures coding and cybersecurity model quality after every training run.
It is designed to run against a vLLM/OpenAI-compatible endpoint and execute
generated code through the hardened Phase 2 Docker sandbox.

## Components

- `src/phase9/evaluate.py`
  - Main CLI for benchmark runs, regression tracking, reports, and alerts.
- `src/phase9/benchmarks.py`
  - HumanEval, MBPP, CyberSecEval 2, and custom security benchmark runners.
- `src/phase9/security_suite.py`
  - Built-in 200-case security suite:
    - 50 buffer overflow detection cases.
    - 50 SQL injection identification cases.
    - 50 cryptographic weakness detection cases.
    - 50 secure patch generation cases.
- `src/phase9/regression_tracker.py`
  - PostgreSQL table initialization, result insertion, previous-run comparison,
    Markdown report generation, and Slack/Discord webhook regression alerts.
- `src/phase9/head_to_head.py`
  - Runs two model checkpoints on identical prompts and asks an LLM judge to score
    correctness, security awareness, and explanation quality.

## PostgreSQL Schema

```sql
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp DOUBLE PRECISION NOT NULL,
    benchmark TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    delta DOUBLE PRECISION NOT NULL,
    metrics JSONB NOT NULL,
    sample_results JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluation_runs_benchmark_time
ON evaluation_runs (benchmark, timestamp DESC);
```

## Standard Evaluation

HumanEval and MBPP can be loaded either from local JSONL exports or from the
optional Hugging Face `datasets` package. CyberSecEval 2 is loaded from local
JSONL because enterprise/security benchmark copies are commonly mirrored
internally.

When using the Hugging Face fallback, set `HF_DATASET_REVISION` to an exact
dataset commit. Local JSONL files are preferred for locked production evals.

```powershell
python src\phase9\evaluate.py `
  --run-id run_2026_06_11_a `
  --endpoint http://localhost:8080/v1 `
  --model security-coder `
  --benchmarks humaneval mbpp cyberseceval2 custom_security `
  --humaneval-jsonl artifacts\benchmarks\humaneval.jsonl `
  --mbpp-jsonl artifacts\benchmarks\mbpp.jsonl `
  --cyberseceval2-jsonl artifacts\benchmarks\cyberseceval2.jsonl `
  --postgres-dsn "postgresql://user:pass@localhost:5432/ai_eval" `
  --alert-webhook "https://hooks.slack.com/services/..." `
  --regression-threshold 0.02 `
  --init-db
```

Outputs:

- JSONL regression history: `artifacts/phase9/evaluation_runs.jsonl`
- Markdown report: `artifacts/phase9/evaluation_report.md`
- PostgreSQL rows, one per benchmark result.

## Metrics

- HumanEval:
  - `pass@1`
  - `pass@10`
- MBPP:
  - `pass@1`
- CyberSecEval 2:
  - `secure_rate`
  - `insecure_code_rate`
- Custom security suite:
  - Overall score.
  - Per-category scores for buffer overflow, SQL injection, crypto weakness, and
    patch generation.

The tracker compares each benchmark against the previous run and includes the
previous five runs in the Markdown report. If a benchmark score drops by more
than `--regression-threshold`, default `0.02`, it sends a
Slack/Discord-compatible webhook payload.

## Head-To-Head Checkpoint Comparison

```powershell
python src\phase9\evaluate.py `
  --head-to-head `
  --model-a-endpoint http://localhost:8081/v1 `
  --model-a security-coder-old `
  --model-b-endpoint http://localhost:8082/v1 `
  --model-b security-coder-new `
  --judge-endpoint https://api.openai.com/v1 `
  --judge-model gpt-4.1 `
  --api-key $env:OPENAI_API_KEY `
  --head-to-head-prompts artifacts\phase9\judge_prompts.jsonl
```

The output JSONL contains one row per prompt with the winner, per-axis scores,
and the judge rationale.
