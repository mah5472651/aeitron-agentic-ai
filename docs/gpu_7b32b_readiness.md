# 7B-32B GPU Readiness

We are not waiting for GPU hardware to build the training/serving path.

Phase 17 prepares everything that can be safely built on the current machine:

- pinned 7B-32B model profiles
- vLLM serving commands
- QLoRA SFT trainer
- GRPO launch commands
- DeepSpeed ZeRO-2/ZeRO-3 configs
- Accelerate config
- profile `.env` files
- readiness report

## Generate

```powershell
.\scripts\run_phase17_gpu_readiness.ps1
```

Outputs:

```text
artifacts/phase17/gpu-readiness.json
artifacts/phase17/gpu-readiness.md
deploy/gpu/model_profiles.json
deploy/gpu/deepspeed_zero2.json
deploy/gpu/deepspeed_zero3.json
deploy/gpu/accelerate_zero2.yaml
deploy/gpu/profiles/*.env
```

## First GPU Target

Start with:

```text
qwen2.5-coder-7b
```

Then move to:

```text
qwen2.5-coder-14b
qwen2.5-coder-32b
```

## Linux CUDA Commands

Serve:

```bash
MODEL_PROFILE=qwen2.5-coder-7b deploy/gpu/run_vllm_profile.sh
```

QLoRA SFT:

```bash
MODEL_PROFILE=qwen2.5-coder-7b deploy/gpu/train_qlora_sft_profile.sh
```

GRPO:

```bash
MODEL_PROFILE=qwen2.5-coder-7b deploy/gpu/train_grpo_profile.sh
```

## Important

The current Windows CPU Qwen backend is only a live integration fallback. The
real quality path is already prepared for 7B-32B Linux CUDA runs.

