#!/usr/bin/env python
"""Phase 11 chat and agent API with a small built-in local chat interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.agentic_runtime import AgenticCodingRuntime
from src.phase11.memory_engine import WorkspaceMemoryEngine
from src.phase11.model_backends import ModelBackend, build_backend
from src.phase11.persistent_memory import PersistentMemoryGateway
from src.phase11.schemas import (
    AgentRunRequest,
    ChatMessage,
    ChatRole,
    GenerationConfig,
    GenerationRequest,
)
from src.phase11.security_engine import SecurityReasoningEngine
from src.phase11.tool_runtime import ToolCallRequest, ToolRegistry
from src.phase34.auth_quota import AuthConfig, auth_status, create_jwt, install_auth_quota
from src.phase35.observability import install_observability, latest_log_tail, metrics_response


DEFAULT_WORKSPACE = os.environ.get("PHASE11_WORKSPACE", str(ROOT))
STATIC_DIR = Path(__file__).with_name("static")
BRAND_DIR = ROOT / "image.png"
LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class CreateSessionRequest(BaseModel):
    workspace: str = DEFAULT_WORKSPACE
    system_prompt: str | None = None


class ChatTurnRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    workspace: str = DEFAULT_WORKSPACE
    stream: bool = False
    max_new_tokens: int = Field(default=900, ge=1, le=4096)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class SecurityAnalyzeRequest(BaseModel):
    text: str | None = None
    workspace: str | None = None
    include_fixtures: bool = False


class MemoryIndexRequest(BaseModel):
    workspace: str = DEFAULT_WORKSPACE
    max_records: int = Field(default=300, ge=1, le=2000)


class MemoryRetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    workspace: str = DEFAULT_WORKSPACE
    token_budget: int = Field(default=12000, ge=512, le=200000)
    max_items: int = Field(default=24, ge=1, le=100)


class VectorMemoryRetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    workspace: str = "mythos"
    limit: int = Field(default=8, ge=1, le=50)
    rebuild: bool = False


class VerifierRunRequest(BaseModel):
    workspace: str = DEFAULT_WORKSPACE
    run_multilang_security: bool = False
    run_semgrep: bool = False
    run_codeql: bool = False
    run_sandbox: bool = False
    allow_medium: bool = True


class TaskGraphRuntimeApiRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace: str = DEFAULT_WORKSPACE
    run_verifier: bool = True
    run_semgrep: bool = False
    run_sandbox: bool = False
    use_model_critic: bool = False


class MainAgentV2ApiRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace: str = DEFAULT_WORKSPACE
    run_verifier: bool = True
    run_semgrep: bool = False
    run_sandbox: bool = False
    retrieve_experience: bool = True
    use_model_critic: bool = False


class IntegratedAgentApiRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace: str = DEFAULT_WORKSPACE
    meta_planning: bool = True
    hierarchical_memory: bool = True
    reasoning_review: bool = True
    strict_stability: bool = True
    moe_routing: bool = True
    vector_memory: bool = True
    rebuild_vector_memory: bool = False
    run_verifier: bool = True
    run_security: bool = True
    verifier_profile: str | None = None
    use_model_critic: bool = False
    agent_backend_mode: str = Field(default="auto", pattern="^(auto|active|mock)$")
    max_agent_nodes: int | None = Field(default=None, ge=1, le=12)
    max_security_files: int = Field(default=500, ge=1, le=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MultiLanguageSecurityRequest(BaseModel):
    workspace: str = DEFAULT_WORKSPACE
    max_files: int = Field(default=3000, ge=1, le=20000)
    include_fixtures: bool = False


class AuthTokenRequest(BaseModel):
    user_id: str = Field(min_length=1)
    ttl_seconds: int = Field(default=3600, ge=60, le=604800)
    scopes: list[str] = Field(default_factory=lambda: ["api"])


class RegressionPackApiRequest(BaseModel):
    smoke_limit: int = Field(default=25, ge=0, le=400)


class ProfileSwitchApiRequest(BaseModel):
    profile: str = "qwen-cpu-smoke"


class ChatSession:
    def __init__(self, session_id: str, workspace: str, system_prompt: str | None = None) -> None:
        self.session_id = session_id
        self.workspace = workspace
        self.messages: list[ChatMessage] = []
        self.created_at_ms = int(time.time() * 1000)
        self.messages.append(
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=system_prompt
                or (
                    "You are a PyTorch-native agentic coding assistant. Expand short prompts, inspect context, "
                    "reason about security, and produce implementation-ready answers."
                ),
            )
        )


class AppState:
    def __init__(self) -> None:
        self.backend: ModelBackend | None = None
        self.sessions: dict[str, ChatSession] = {}
        self.security = SecurityReasoningEngine()


state = AppState()


def create_backend_from_env() -> ModelBackend:
    kind = os.environ.get("PHASE11_BACKEND", "mock")
    return build_backend(
        kind,
        endpoint=os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8000/v1"),
        model_name=os.environ.get("PHASE11_MODEL_NAME", "security-coder"),
        api_key=os.environ.get("PHASE11_API_KEY"),
        checkpoint=os.environ.get("PHASE11_CHECKPOINT"),
        tokenizer_path=os.environ.get("PHASE11_TOKENIZER"),
        device=os.environ.get("PHASE11_DEVICE", "cpu"),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    state.backend = create_backend_from_env()
    try:
        yield
    finally:
        if state.backend:
            await state.backend.aclose()


app = FastAPI(title="Phase 11 PyTorch AI Core", version="1.0.0", lifespan=lifespan)
AUTH_CONFIG = install_auth_quota(app)
install_observability(app)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="phase11-static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/brand/logo", response_model=None)
async def brand_logo():
    logo = find_logo_file()
    if logo is None:
        return Response(status_code=404)
    return FileResponse(logo)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
async def ready() -> dict[str, Any]:
    backend = require_backend()
    return {"status": "ready", "backend": backend.kind.value, "model": backend.model_name}


@app.get("/metrics")
async def metrics():
    return metrics_response()


@app.get("/v1/runtime")
async def runtime_info() -> dict[str, Any]:
    backend = require_backend()
    return {
        "status": "ready",
        "backend": backend.kind.value,
        "model": backend.model_name,
        "default_workspace": DEFAULT_WORKSPACE,
        "logo_available": find_logo_file() is not None,
        "auth": auth_status(AUTH_CONFIG),
    }


@app.get("/v1/architecture/roadmap")
async def architecture_roadmap() -> dict[str, Any]:
    from src.phase11.roadmap import ROADMAP

    return {"tracks": ROADMAP}


@app.get("/v1/quality/latest")
async def latest_quality() -> dict[str, Any]:
    return {
        "phase12": latest_report_payload(ROOT / "artifacts" / "phase12"),
        "phase13": latest_report_payload(ROOT / "artifacts" / "phase13"),
        "scorecard": latest_report_payload(ROOT / "artifacts" / "scorecard"),
        "phase16": latest_phase16_report_payload(),
        "phase17": latest_named_report(ROOT / "artifacts" / "phase17", "gpu-readiness.json"),
        "phase18": latest_named_report(ROOT / "artifacts" / "phase18", "model-quality-latest.json"),
        "phase19": latest_named_report(ROOT / "artifacts" / "phase19", "verifier-latest.json"),
        "phase20": latest_named_report(ROOT / "artifacts" / "phase20", "taskgraph-runtime-latest.json"),
        "phase21": latest_named_report(ROOT / "artifacts" / "phase21", "experience-promotion-latest.json"),
        "phase22": latest_named_report(ROOT / "artifacts" / "phase22", "critic-latest.json"),
        "phase23": latest_named_report(ROOT / "artifacts" / "phase23", "quality-profile-latest.json"),
        "phase24": latest_named_report(ROOT / "artifacts" / "phase24", "main-agent-v2-latest.json"),
        "phase25": latest_named_report(ROOT / "artifacts" / "phase25", "experience-retrieval-latest.json"),
        "phase26": latest_named_report(ROOT / "artifacts" / "phase26", "patch-manager-latest.json"),
        "phase27": latest_named_report(ROOT / "artifacts" / "phase27", "verifier-latest.json"),
        "phase28": latest_named_report(ROOT / "artifacts" / "phase28", "security-workflow-latest.json"),
        "phase29": latest_named_report(ROOT / "artifacts" / "phase29", "dataset-review-latest.json"),
        "phase30": latest_named_report(ROOT / "artifacts" / "phase30", "expanded-benchmark-latest.json"),
        "phase31": latest_named_report(ROOT / "artifacts" / "phase31", "long-context-latest.json"),
        "phase32": latest_named_report(ROOT / "artifacts" / "phase32", "critic-contract-latest.json"),
        "phase33": latest_named_report(ROOT / "artifacts" / "phase33", "gpu-backend-contract-latest.json"),
        "phase34": {"available": True, "status": "enabled" if AUTH_CONFIG.enabled else "disabled", "quota_enabled": AUTH_CONFIG.quota_enabled},
        "phase35": {"available": True, "status": "enabled", "logs": latest_log_tail(5)},
        "phase36": latest_named_report(ROOT / "artifacts" / "phase36", "data-flywheel-latest.json"),
        "phase37": latest_named_report(ROOT / "artifacts" / "phase37", "vector-memory-latest.json"),
        "phase38": latest_named_report(ROOT / "artifacts" / "phase38", "multilang-security-latest.json"),
        "phase39": latest_named_report(ROOT / "artifacts" / "phase39", "checkpoint-gate-latest.json"),
        "phase40": latest_named_report(ROOT / "artifacts" / "phase40", "integrated-agent-latest.json"),
        "phase41": latest_named_report(ROOT / "artifacts" / "phase41", "regression-pack-latest.json"),
        "phase42": latest_named_report(ROOT / "artifacts" / "phase42", "profile-switch-latest.json"),
        "phase43": latest_named_report(ROOT / "artifacts" / "phase43", "meta-planner-latest.json"),
        "phase44": latest_named_report(ROOT / "artifacts" / "phase44", "intent-expansion-latest.json"),
        "phase45": latest_named_report(ROOT / "artifacts" / "phase45", "parallel-agent-latest.json"),
        "phase46": latest_named_report(ROOT / "artifacts" / "phase46", "hierarchical-memory-latest.json"),
        "phase47": latest_named_report(ROOT / "artifacts" / "phase47", "reasoning-engine-latest.json"),
        "phase48": latest_named_report(ROOT / "artifacts" / "phase48", "knowledge-graph-latest.json"),
        "phase49": latest_named_report(ROOT / "artifacts" / "phase49", "multimodal-expert-latest.json"),
        "phase50": latest_named_report(ROOT / "artifacts" / "phase50", "moe-router-latest.json"),
        "phase51": latest_named_report(ROOT / "artifacts" / "phase51", "high-stability-reasoning-memory-latest.json"),
        "readiness": latest_report_payload(ROOT / "artifacts" / "phase10"),
    }


@app.get("/v1/auth/status")
async def auth_status_endpoint() -> dict[str, Any]:
    return auth_status(AUTH_CONFIG)


@app.post("/v1/auth/token")
async def issue_auth_token(request: AuthTokenRequest) -> dict[str, Any]:
    if os.environ.get("PHASE34_DEV_TOKEN_ENABLED", "0") != "1":
        raise HTTPException(status_code=403, detail="dev token issuance disabled; use the Phase 34 CLI or enable PHASE34_DEV_TOKEN_ENABLED=1")
    if not AUTH_CONFIG.jwt_secret:
        raise HTTPException(status_code=503, detail="PHASE34_JWT_SECRET is required")
    token = create_jwt(
        subject=request.user_id,
        secret=AUTH_CONFIG.jwt_secret,
        issuer=AUTH_CONFIG.issuer,
        audience=AUTH_CONFIG.audience,
        scopes=request.scopes,
        ttl_seconds=request.ttl_seconds,
    )
    return {"token_type": "Bearer", "access_token": token, "expires_in": request.ttl_seconds}  # nosec B105


@app.get("/v1/quality/phase12/latest")
async def latest_phase12_quality() -> dict[str, Any]:
    return latest_report_payload(ROOT / "artifacts" / "phase12")


@app.get("/v1/quality/phase13/latest")
async def latest_phase13_quality() -> dict[str, Any]:
    return latest_report_payload(ROOT / "artifacts" / "phase13")


@app.get("/v1/scorecard/latest")
async def latest_scorecard() -> dict[str, Any]:
    return latest_report_payload(ROOT / "artifacts" / "scorecard")


@app.get("/v1/phase16/status")
async def phase16_status() -> dict[str, Any]:
    return latest_phase16_report_payload()


@app.get("/v1/gpu-readiness/latest")
async def gpu_readiness_latest() -> dict[str, Any]:
    return latest_named_report(ROOT / "artifacts" / "phase17", "gpu-readiness.json")


@app.get("/v1/model-quality/latest")
async def model_quality_latest() -> dict[str, Any]:
    return latest_named_report(ROOT / "artifacts" / "phase18", "model-quality-latest.json")


@app.get("/v1/verifier/latest")
async def verifier_latest() -> dict[str, Any]:
    return latest_named_report(ROOT / "artifacts" / "phase19", "verifier-latest.json")


@app.post("/v1/verifier/run")
async def run_verifier(request: VerifierRunRequest) -> dict[str, Any]:
    from src.phase19.verifier_registry import VerifierPolicy, VerifierRegistry, write_report

    policy = VerifierPolicy(
        run_multilang_security=request.run_multilang_security,
        run_semgrep=request.run_semgrep,
        run_codeql=request.run_codeql,
        run_sandbox=request.run_sandbox,
        fail_on_medium=not request.allow_medium,
    )
    report = await VerifierRegistry(policy).run(request.workspace)
    write_report(report, ROOT / "artifacts" / "phase19")
    return report.model_dump()


@app.get("/v1/taskgraph/latest")
async def taskgraph_latest() -> dict[str, Any]:
    return latest_named_report(ROOT / "artifacts" / "phase20", "taskgraph-runtime-latest.json")


@app.post("/v1/taskgraph/run")
async def run_taskgraph_runtime(request: TaskGraphRuntimeApiRequest) -> dict[str, Any]:
    from src.phase20.taskgraph_runtime import TaskGraphAgentRuntime, TaskGraphRuntimeRequest, write_report

    backend = require_backend()
    report = await TaskGraphAgentRuntime(backend).run(
        TaskGraphRuntimeRequest(
            prompt=request.prompt,
            workspace=request.workspace,
            run_verifier=request.run_verifier,
            run_semgrep=request.run_semgrep,
            run_sandbox=request.run_sandbox,
            use_model_critic=request.use_model_critic,
        )
    )
    write_report(report, ROOT / "artifacts" / "phase20")
    return report.model_dump()


@app.get("/v1/main-agent-v2/latest")
async def main_agent_v2_latest() -> dict[str, Any]:
    return latest_named_report(ROOT / "artifacts" / "phase24", "main-agent-v2-latest.json")


@app.get("/v1/integrated-agent/latest")
async def integrated_agent_latest() -> dict[str, Any]:
    return latest_named_report(ROOT / "artifacts" / "phase40", "integrated-agent-latest.json")


@app.post("/v1/main-agent-v2/run")
async def run_main_agent_v2(request: MainAgentV2ApiRequest) -> dict[str, Any]:
    from src.phase24.main_agent_v2 import MainAgentV2, MainAgentV2Request, write_report

    backend = require_backend()
    report = await MainAgentV2(backend).run(
        MainAgentV2Request(
            prompt=request.prompt,
            workspace=request.workspace,
            run_verifier=request.run_verifier,
            run_semgrep=request.run_semgrep,
            run_sandbox=request.run_sandbox,
            retrieve_experience=request.retrieve_experience,
            use_model_critic=request.use_model_critic,
        )
    )
    write_report(report, ROOT / "artifacts" / "phase24")
    return report.model_dump()


@app.post("/v1/regression-pack/generate")
async def generate_regression_pack(request: RegressionPackApiRequest) -> dict[str, Any]:
    from src.phase41.regression_pack import (
        RegressionPackReport,
        category_counts,
        generate_tasks,
        smoke_run,
        write_jsonl,
        write_report,
    )

    tasks = generate_tasks()
    dataset_path = ROOT / "artifacts" / "phase41" / "regression-pack.jsonl"
    write_jsonl(dataset_path, tasks)
    smoke = await smoke_run(tasks, limit=request.smoke_limit)
    smoke_score = sum(result.score for result in smoke) / len(smoke) if smoke else None
    report = RegressionPackReport(
        run_id=f"phase41-api-{time.time_ns()}",
        dataset_path=str(dataset_path),
        task_count=len(tasks),
        category_counts=category_counts(tasks),
        smoke_results=smoke,
        smoke_score=smoke_score,
        recommendation="Use this pack for quick regression gates before and after model/backend changes.",
    )
    write_report(report, ROOT / "artifacts" / "phase41")
    return report.model_dump()


@app.get("/v1/profile-switcher/profiles")
async def profile_switcher_profiles() -> dict[str, Any]:
    from src.phase42.profile_switcher import list_profiles

    return {"profiles": list_profiles()}


@app.post("/v1/profile-switcher/activate")
async def profile_switcher_activate(request: ProfileSwitchApiRequest) -> dict[str, Any]:
    from src.phase42.profile_switcher import activate_profile, all_profiles

    profiles = all_profiles()
    if request.profile not in profiles:
        raise HTTPException(status_code=404, detail=f"unknown profile: {request.profile}")
    report = activate_profile(profiles[request.profile], output_dir=ROOT / "artifacts" / "phase42", run_id=f"phase42-api-{time.time_ns()}")
    return report.model_dump()


@app.post("/v1/chat/sessions")
async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    session_id = f"sess-{time.time_ns()}"
    session = ChatSession(session_id=session_id, workspace=request.workspace, system_prompt=request.system_prompt)
    state.sessions[session_id] = session
    return {"session_id": session_id, "workspace": session.workspace, "created_at_ms": session.created_at_ms}


@app.post("/v1/chat")
async def chat(request: ChatTurnRequest) -> Any:
    backend = require_backend()
    session = get_or_create_session(request.session_id, request.workspace)
    session.messages.append(ChatMessage(role=ChatRole.USER, content=request.message))
    generation_request = GenerationRequest(
        session_id=session.session_id,
        workspace=session.workspace,
        messages=session.messages,
        config=GenerationConfig(
            max_new_tokens=request.max_new_tokens,
            temperature=request.temperature,
            stream=request.stream,
        ),
    )
    generation = await backend.generate(generation_request)
    session.messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=generation.text, metadata=generation.metadata))
    if request.stream:
        return StreamingResponse(stream_text(generation.text, session.session_id), media_type="text/event-stream")
    return JSONResponse(
        {
            "session_id": session.session_id,
            "message": generation.text,
            "backend": generation.backend,
            "model": generation.model,
            "latency_ms": generation.latency_ms,
            "usage": {
                "prompt_tokens_estimate": generation.prompt_tokens_estimate,
                "completion_tokens_estimate": generation.completion_tokens_estimate,
            },
        }
    )


@app.post("/v1/agent/run")
async def run_agent(request: IntegratedAgentApiRequest) -> dict[str, Any]:
    from src.phase40.integrated_agent import IntegratedAgentRequest, IntegratedAgentRuntime, write_report

    backend = require_backend()
    report = await IntegratedAgentRuntime(backend).run(
        IntegratedAgentRequest(
            prompt=request.prompt,
            workspace=request.workspace,
            meta_planning=request.meta_planning,
            hierarchical_memory=request.hierarchical_memory,
            reasoning_review=request.reasoning_review,
            strict_stability=request.strict_stability,
            moe_routing=request.moe_routing,
            vector_memory=request.vector_memory,
            rebuild_vector_memory=request.rebuild_vector_memory,
            run_verifier=request.run_verifier,
            run_security=request.run_security,
            verifier_profile=request.verifier_profile,
            use_model_critic=request.use_model_critic,
            agent_backend_mode=request.agent_backend_mode,
            max_agent_nodes=request.max_agent_nodes,
            max_security_files=request.max_security_files,
            metadata=request.metadata,
        )
    )
    write_report(report, ROOT / "artifacts" / "phase40")
    return report.model_dump()


@app.post("/v1/agent/run/stream")
async def run_agent_stream(request: IntegratedAgentApiRequest) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        yield sse_json("status", {"stage": "accepted", "prompt_chars": len(request.prompt)})
        from src.phase40.integrated_agent import IntegratedAgentRequest, IntegratedAgentRuntime, write_report

        backend = require_backend()
        yield sse_json("status", {"stage": "phase40_start"})
        report = await IntegratedAgentRuntime(backend).run(
            IntegratedAgentRequest(
                prompt=request.prompt,
                workspace=request.workspace,
                meta_planning=request.meta_planning,
                hierarchical_memory=request.hierarchical_memory,
                reasoning_review=request.reasoning_review,
                strict_stability=request.strict_stability,
                moe_routing=request.moe_routing,
                vector_memory=request.vector_memory,
                rebuild_vector_memory=request.rebuild_vector_memory,
                run_verifier=request.run_verifier,
                run_security=request.run_security,
                verifier_profile=request.verifier_profile,
                use_model_critic=request.use_model_critic,
                agent_backend_mode=request.agent_backend_mode,
                max_agent_nodes=request.max_agent_nodes,
                max_security_files=request.max_security_files,
                metadata=request.metadata,
            )
        )
        write_report(report, ROOT / "artifacts" / "phase40")
        yield sse_json("report", report.model_dump())
        yield sse_json("done", {"run_id": report.run_id, "status": report.status})

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/v1/agent/run-legacy")
async def run_legacy_agent(request: AgentRunRequest) -> dict[str, Any]:
    backend = require_backend()
    runtime = AgenticCodingRuntime(backend)
    report = await runtime.run(request)
    return report.model_dump()


@app.get("/v1/tools")
async def list_tools(workspace: str = DEFAULT_WORKSPACE) -> dict[str, Any]:
    registry = ToolRegistry(workspace, security=state.security)
    return {"tools": [spec.model_dump() for spec in registry.specs()]}


@app.get("/v1/tools/advanced")
async def list_advanced_tools(workspace: str = DEFAULT_WORKSPACE) -> dict[str, Any]:
    from src.phase16.tool_adapters import ToolAdapterRegistry

    return await ToolAdapterRegistry(workspace).status()


@app.post("/v1/tools/call")
async def call_tool(request: ToolCallRequest) -> dict[str, Any]:
    registry = ToolRegistry(request.workspace, security=state.security)
    result = await registry.call(request.name, request.args)
    return result.model_dump()


@app.post("/v1/memory/index")
async def index_memory(request: MemoryIndexRequest) -> dict[str, Any]:
    memory = WorkspaceMemoryEngine(request.workspace)
    gateway = PersistentMemoryGateway(workspace=request.workspace)
    try:
        return await memory.index_persistent(gateway=gateway, max_records=request.max_records)
    finally:
        await gateway.aclose()


@app.post("/v1/memory/retrieve")
async def retrieve_memory(request: MemoryRetrieveRequest) -> dict[str, Any]:
    memory = WorkspaceMemoryEngine(request.workspace)
    context = memory.retrieve(request.query, token_budget=request.token_budget, max_items=request.max_items)
    return context.model_dump()


@app.post("/v1/memory/vector/retrieve")
async def retrieve_vector_memory(request: VectorMemoryRetrieveRequest) -> dict[str, Any]:
    from src.phase37.vector_memory import VectorExperienceMemory, write_report

    memory = VectorExperienceMemory(workspace=request.workspace)
    report = await memory.run(
        request.query,
        run_id=f"phase37-api-{time.time_ns()}",
        limit=request.limit,
        rebuild=request.rebuild,
    )
    write_report(report, ROOT / "artifacts" / "phase37")
    return report.model_dump()


@app.post("/v1/security/analyze")
async def analyze_security(request: SecurityAnalyzeRequest) -> dict[str, Any]:
    if request.text is None and request.workspace is None:
        raise HTTPException(status_code=400, detail="provide text or workspace")
    if request.workspace:
        review = await asyncio.to_thread(
            state.security.analyze_workspace,
            request.workspace,
            include_fixtures=request.include_fixtures,
        )
    else:
        review = state.security.analyze_text(request.text or "")
    return review.model_dump()


@app.post("/v1/security/multilang")
async def analyze_multilang_security(request: MultiLanguageSecurityRequest) -> dict[str, Any]:
    from src.phase38.multilang_security import MultiLanguageSecurityEngine, write_report

    report = await asyncio.to_thread(
        MultiLanguageSecurityEngine().analyze_workspace,
        request.workspace,
        max_files=request.max_files,
        include_fixtures=request.include_fixtures,
    )
    write_report(report, ROOT / "artifacts" / "phase38")
    return report.model_dump()


def require_backend() -> ModelBackend:
    if state.backend is None:
        raise HTTPException(status_code=503, detail="backend is not initialized")
    return state.backend


def get_or_create_session(session_id: str | None, workspace: str) -> ChatSession:
    if session_id and session_id in state.sessions:
        return state.sessions[session_id]
    new_id = session_id or f"sess-{time.time_ns()}"
    session = ChatSession(session_id=new_id, workspace=workspace)
    state.sessions[new_id] = session
    return session


def find_logo_file() -> Path | None:
    if BRAND_DIR.is_file() and BRAND_DIR.suffix.lower() in LOGO_EXTENSIONS:
        return BRAND_DIR
    if not BRAND_DIR.exists() or not BRAND_DIR.is_dir():
        return None
    for path in sorted(BRAND_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in LOGO_EXTENSIONS:
            return path
    return None


def latest_report_payload(directory: Path) -> dict[str, Any]:
    if not directory.exists():
        return {"available": False, "reason": f"missing directory: {directory}"}
    candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        return {"available": False, "reason": f"no json reports in {directory}"}
    return load_report_summary(candidates[0])


def latest_named_report(directory: Path, filename: str) -> dict[str, Any]:
    path = directory / filename
    if not path.exists():
        return {"available": False, "reason": f"missing report: {path}"}
    return load_report_summary(path)


def latest_phase16_report_payload() -> dict[str, Any]:
    real_report = latest_named_report(ROOT / "artifacts" / "phase16", "phase16-smoke-real.json")
    checks = real_report.get("checks") if isinstance(real_report.get("checks"), dict) else {}
    base_model = checks.get("base_model_connector") if isinstance(checks.get("base_model_connector"), dict) else {}
    if real_report.get("available") and base_model.get("ok"):
        return real_report
    return latest_named_report(ROOT / "artifacts" / "phase16", "phase16-smoke.json")


def load_report_summary(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "path": str(path), "reason": f"{type(exc).__name__}: {exc}"}
    baseline = payload.get("baseline") if isinstance(payload.get("baseline"), dict) else {}
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    mock = payload.get("mock") if isinstance(payload.get("mock"), dict) else {}
    real = payload.get("real") if isinstance(payload.get("real"), dict) else {}
    mock_metrics = mock.get("metrics") if isinstance(mock.get("metrics"), dict) else {}
    real_metrics = real.get("metrics") if isinstance(real.get("metrics"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    raw_checks = payload.get("checks")
    checks = raw_checks if isinstance(raw_checks, dict) else {}
    check_list = raw_checks if isinstance(raw_checks, list) else []
    candidate_ready = payload.get("candidate_ready")
    if candidate_ready is None and "real_ready" in payload:
        candidate_ready = payload.get("real_ready")
    if candidate_ready is None:
        candidate_ready = real.get("ready")
    return {
        "available": True,
        "path": str(path),
        "run_id": payload.get("run_id"),
        "score": payload.get("overall_score")
        or payload.get("score")
        or payload.get("confidence")
        or metrics.get("overall_score")
        or review.get("confidence")
        or candidate.get("overall_score")
        or mock_metrics.get("overall_score"),
        "passed": payload.get("passed"),
        "architecture_ready": payload.get("architecture_ready") or mock.get("ready"),
        "candidate_ready": candidate_ready,
        "backend": payload.get("backend_kind"),
        "model": payload.get("model_name"),
        "suite": payload.get("suite"),
        "task_dataset": payload.get("task_dataset"),
        "summary": payload.get("summary") or candidate.get("summary") or mock.get("summary"),
        "status": payload.get("status"),
        "metrics": metrics,
        "failure_analysis": payload.get("failure_analysis"),
        "promotion": payload.get("promotion"),
        "review": review,
        "category_scores": payload.get("category_scores") or candidate.get("category_scores"),
        "baseline_score": baseline.get("overall_score"),
        "candidate_score": candidate.get("overall_score"),
        "score_delta": payload.get("score_delta"),
        "mock_metrics": mock_metrics,
        "real_metrics": real_metrics,
        "checks": {name: {"ok": value.get("ok")} for name, value in checks.items() if isinstance(value, dict)},
        "check_list": [{"name": item.get("name"), "ok": item.get("ok")} for item in check_list if isinstance(item, dict)],
        "profiles": payload.get("profiles", []),
        "passed_without_gpu": payload.get("passed_without_gpu"),
        "required_green": payload.get("required_green", []),
        "model_dependent": payload.get("model_dependent", []),
        "comparison": payload.get("comparison"),
        "recommendations": payload.get("recommendations", [])[:5],
        "recommendation": payload.get("recommendation"),
        "reviewed": payload.get("reviewed"),
        "train_ready": payload.get("train_ready"),
        "needs_review": payload.get("needs_review"),
        "task_count": payload.get("task_count"),
        "estimated_tokens": payload.get("estimated_tokens"),
        "records": payload.get("records"),
        "trigger_state": payload.get("trigger_state"),
        "queued_for_rejection_sampling": payload.get("queued_for_rejection_sampling"),
        "phase3_queue_path": payload.get("phase3_queue_path"),
        "phase7_trigger_path": payload.get("phase7_trigger_path"),
        "indexed_records": payload.get("indexed_records"),
        "hits": payload.get("hits"),
        "languages": payload.get("languages"),
        "findings": payload.get("findings"),
        "decision": payload.get("decision"),
        "reason": payload.get("reason"),
        "active_checkpoint_after": payload.get("active_checkpoint_after"),
        "route": payload.get("route"),
        "failure_event": payload.get("failure_event"),
        "category_counts": payload.get("category_counts"),
        "smoke_score": payload.get("smoke_score"),
        "active_profile": payload.get("active_profile"),
        "generated_files": payload.get("generated_files"),
        "primary_expert": payload.get("primary_expert"),
        "routes": payload.get("routes"),
        "role_count": payload.get("role_count"),
        "nodes": payload.get("nodes"),
        "edges": payload.get("edges"),
        "artifacts": payload.get("artifacts"),
        "accepted": payload.get("accepted"),
    }


async def stream_text(text: str, session_id: str) -> AsyncIterator[str]:
    words = text.split(" ")
    for word in words:
        yield f"data: {word} \n\n"
        await asyncio.sleep(0.01)
    yield f"event: done\ndata: {session_id}\n\n"


def sse_json(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 11 chat API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run("src.phase11.chat_api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
