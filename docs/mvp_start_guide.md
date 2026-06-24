q# MVP Start Guide

Use this when starting from an empty local machine state. It creates starter
data, extracts call graphs, trains a small tokenizer artifact from the starter
corpus, and runs the offline deployment doctor.

```powershell
python src\phase10\bootstrap_mvp.py --run-id mvp-local-001
```

Generated paths:

- `data/raw_code/`
- `data/security_samples/vulnerable_samples.jsonl`
- `data/benchmarks/cyberseceval2_mini.jsonl`
- `artifacts/mvp/ast_graph.jsonl`
- `artifacts/mvp/callgraph.json`
- `artifacts/mvp/code_bpe_tokenizer/tokenizer.json`
- `artifacts/mvp/<run_id>.json`
- `artifacts/mvp/<run_id>.md`

When Docker, Redis, PostgreSQL, Qdrant, vLLM, and the FastAPI gateway are live:

```powershell
python src\phase10\bootstrap_mvp.py `
  --run-id mvp-live-001 `
  --run-live-smoke `
  --run-sandbox-smoke
```

If the local machine does not have Docker/GPU/database services yet, the runner
will still complete the offline MVP and report the missing live infrastructure
as warnings or skipped checks.

