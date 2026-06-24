# Phase 40-42 Integrated Runtime

These phases turn the architecture from many strong components into a default
end-to-end agent path.

## Phase 40: Integrated Default Agent

Purpose:

- Make `/v1/agent/run` use the full architecture path.
- Inject Phase 37 vector memory before planning.
- Run Phase 24 Main Agent V2 and TaskGraph execution.
- Review output with Phase 22 critic.
- Run Phase 27 verifier policy.
- Run Phase 38 multi-language security scan when the intent needs it.
- Promote failures into experience memory for future retrieval.

Primary file:

```text
src/phase40/integrated_agent.py
```

Run:

```powershell
.\scripts\run_phase40_integrated_agent.ps1 -Prompt "build a secure login api with tests"
```

Local CPU behavior:

- `qwen-cpu-smoke` keeps chat/model behavior connected to the local Qwen endpoint.
- Phase 40 uses `agent_backend_mode=auto`, so multi-agent orchestration uses mock plumbing on CPU unless forced with `-AgentBackendMode active`.
- GPU/vLLM profiles switch Phase 40 to active model orchestration.

API:

```text
POST /v1/agent/run
```

Legacy path:

```text
POST /v1/agent/run-legacy
```

## Phase 41: Real Task Regression Pack

Purpose:

- Create a larger local regression suite for architecture and model changes.
- Catch regressions in short prompt understanding, debugging, security, multi-file repo reasoning, and patch verification.

Primary file:

```text
src/phase41/regression_pack.py
```

Dataset shape:

- 100 short prompt coding tasks
- 100 debugging tasks
- 100 security finding tasks
- 50 multi-file repository tasks
- 50 patch verification tasks

Run:

```powershell
.\scripts\run_phase41_regression_pack.ps1 -SmokeLimit 25
```

Output:

```text
artifacts/phase41/regression-pack.jsonl
artifacts/phase41/regression-pack-latest.json
```

## Phase 42: Production Profile Switcher

Purpose:

- Switch between local mock, local CPU Qwen smoke, and future GPU/vLLM profiles through one contract.
- Generate active env config for Phase 11, Phase 24, Phase 40, and scorecard runners.
- Keep GPU profiles ready without pretending this CPU machine can run them.

Primary file:

```text
src/phase42/profile_switcher.py
```

List profiles:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -List
```

Activate current local profile:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -Profile qwen-cpu-smoke
```

Activate future GPU profile:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -Profile qwen2.5-coder-7b
```

Outputs:

```text
config/active_model_profile.json
config/active_model_profile.ps1
artifacts/phase42/profile-switch-latest.json
```

## Default Production Flow

1. Activate a runtime profile with Phase 42.
2. Run `/v1/agent/run` or Phase 40 CLI.
3. Phase 40 retrieves vector memory and runs the full agent path.
4. Phase 41 regression pack checks whether changes improved or degraded behavior.
5. Future training outputs still go through Phase 39 before promotion.
