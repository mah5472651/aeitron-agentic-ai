# Phase 8 vLLM Serving Blueprint

## vLLM Server

Launcher:

```text
src/phase8/vllm_server.py
```

Defaults:

- `--tensor-parallel-size 2`
- `--max-num-seqs 256`
- `--max-num-batched-tokens 8192`
- `--gpu-memory-utilization 0.94`
- `--quantization awq`
- `--enable-prefix-caching`
- `--max-model-len 8192`

vLLM handles PagedAttention KV cache management internally. GPU memory usage is
controlled through `gpu_memory_utilization`; batching is constrained by max
sequence and max token limits.

## FastAPI Gateway

Gateway:

```text
src/phase8/gateway.py
```

Features:

- SSE streaming for `/v1/chat/completions`
- priority queue lanes:
  - code execution / vulnerability / agentic
  - chat
  - batch
- request timeouts:
  - normal: `30s`
  - agentic: `120s`
- Kubernetes probes:
  - `/health/live`
  - `/health/ready`

## Prompt Router

Routing:

- vulnerability analysis: `temperature=0.1`, `top_p=0.9`
- code generation: `temperature=0.2`, `top_p=0.95`
- agentic reasoning: `temperature=0.4`, tool-call stop tokens

## AWQ Quantization

Quantizer:

```text
src/phase8/quantize_awq.py
```

Input:

- bf16/full checkpoint
- 128 representative code samples in JSONL
- remote checkpoints require `--model-revision <pinned_commit_sha_or_tag>` by default
- `--trust-remote-code` is opt-in and should be used only for audited model repos

Output:

- AWQ INT4 model directory
- optional HumanEval benchmark report

## Deployment

```text
deploy/phase8/docker-compose.yml
deploy/phase8/nginx.conf
```
