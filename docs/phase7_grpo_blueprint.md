# Phase 7 GRPO Training Blueprint

## Objective

Train a coding/cybersecurity policy model with Group Relative Policy Optimization.

For each prompt:

1. Generate `G = 8` candidate responses from the current policy.
2. Score each candidate with execution, security, format, and efficiency rewards.
3. Normalize advantages within the candidate group:

```text
A_i = (R_i - mean(R)) / std(R)
```

4. Optimize clipped GRPO/PPO-style policy loss plus frozen-reference KL:

```text
L_GRPO = -min(r_i A_i, clip(r_i, 1-eps, 1+eps) A_i) + beta * KL(pi_theta || pi_ref)
```

Defaults:

- `G = 8`
- `temperature = 0.8`
- `epsilon = 0.2`
- `beta = 0.01`

## Reward Components

```text
R_exec = +1.0 if sandbox exit_code == 0 else -1.0
R_sec  = +0.5 if static analyzer finds zero new CVEs/issues else -0.5
R_fmt  = +0.3 if thought tokens are valid else -0.3
R_eff  = +0.2 if execution passes under 2000ms else -0.2

R_total = w1*R_exec + w2*R_sec + w3*R_fmt + w4*R_eff
```

## Dataset JSONL Shape

```json
{
  "prompt": "Fix this vulnerable code...",
  "security_baseline_findings": 0,
  "sandbox": {
    "image": "python:3.12-slim",
    "files": [
      {
        "path": "test_candidate.py",
        "content": "import candidate\n..."
      }
    ],
    "generated_path": "candidate.py",
    "command": ["python3", "/workspace/test_candidate.py"]
  },
  "metadata": {
    "task_id": "example"
  }
}
```

## Run

```powershell
python src\phase7\grpo_training_loop.py `
  --model-name-or-path Qwen/Qwen2.5-Coder-1.5B-Instruct `
  --dataset artifacts\grpo_prompts.jsonl `
  --output-dir artifacts\grpo_policy `
  --bf16 `
  --gradient-checkpointing `
  --deepspeed `
  --wandb
```

## Source

```text
src/phase7/grpo_training_loop.py
```
