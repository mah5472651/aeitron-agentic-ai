# Phases 24-33 Power Architecture Layer

This batch turns the architecture into a stronger coding/security agent system
before serious model training.

## What Was Added

1. **Phase 24 Main Agent V2**
   - TaskGraph-first runtime bridge.
   - Injects experience memory into planning.
   - Runs verifier/critic hooks.

2. **Phase 25 Experience Retrieval**
   - Retrieves past failure/fix/outcome records for planner context.

3. **Phase 26 Patch Manager**
   - Safe patch preview, diff, backup, and rollback manifest.
   - Defaults to preview, not mutation.

4. **Phase 27 Verifier Policy Engine**
   - Profiles: `fast`, `security`, `release`, `codeql-python`.
   - Writes default policy to `config/verifier_policy.json`.

5. **Phase 28 Security Expert Workflow**
   - Defensive-only vulnerability finding, safe patch direction, verifier rerun.

6. **Phase 29 Dataset Review Gate**
   - Candidate states: rejected, needs human review, train ready.
   - Prevents raw synthetic failures from entering training directly.

7. **Phase 30 Expanded Golden Benchmark**
   - Generates 450 tasks:
     - 100 short prompt coding
     - 100 debugging
     - 100 security finding
     - 100 patch generation
     - 50 long-context repo

8. **Phase 31 Long Context Packer**
   - Combines workspace retrieval and experience memory into one budgeted context block.

9. **Phase 32 Critic Endpoint Contract**
   - Validates an OpenAI-compatible critic endpoint.

10. **Phase 33 GPU Backend Contract**
    - Validates future 7B/14B/32B quality-run profile readiness.

## Run Commands

```powershell
.\scripts\run_phase24_main_agent_v2.ps1
.\scripts\run_phase25_experience_retrieval.ps1
.\scripts\run_phase26_patch_manager.ps1
.\scripts\run_phase27_verifier_policy.ps1
.\scripts\run_phase28_security_workflow.ps1
.\scripts\run_phase29_dataset_review_gate.ps1
.\scripts\run_phase30_expanded_benchmark.ps1
.\scripts\run_phase31_long_context_packer.ps1
.\scripts\run_phase32_critic_contract.ps1
.\scripts\run_phase33_gpu_backend_contract.ps1
```

## Safety

Security workflows remain defensive:

- static analysis
- vulnerability identification
- patch guidance
- verification
- no autonomous exploit execution

