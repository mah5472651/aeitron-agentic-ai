#!/usr/bin/env python
"""Phase 51 high-stability reasoning and unified memory manager.

This module is intentionally strict:

- Planner creates a task graph and never writes executable code.
- Executor follows the graph and never alters the plan.
- Critic scores and highlights flaws, but never solves the task.
- Verifier checks schema/criteria/facts without doing logical reasoning.
- Memory ingestion rejects thoughts, guesses, and temporary output.
- Retrieval uses the required weighted ranking formula exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.persistent_memory import HashEmbedding, MemoryRecord, PersistentMemoryGateway, cosine_similarity
from src.phase37.vector_memory import build_embedder


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SchemaViolation(ValueError):
    """Raised when a component violates its declared JSON schema."""


class RoleContractViolation(ValueError):
    """Raised when a role mixes responsibilities."""


class MemoryIngestionRejected(ValueError):
    """Raised when memory content violates the anti-pollution policy."""


class SchemaRegistry:
    """Small schema contract layer backed by strict Pydantic models."""

    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}

    def register(self, name: str, model: type[BaseModel]) -> None:
        self._models[name] = model

    def schema(self, name: str) -> dict[str, Any]:
        return self._models[name].model_json_schema()

    def validate(self, name: str, payload: dict[str, Any]) -> BaseModel:
        try:
            return self._models[name].model_validate(payload)
        except ValidationError as exc:
            raise SchemaViolation(f"{name} schema violation: {exc}") from exc


class TaskStep(StrictModel):
    step_id: str
    title: str
    instruction: str
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str

    @field_validator("instruction")
    @classmethod
    def instruction_must_not_contain_code_block(cls, value: str) -> str:
        if "```" in value:
            raise ValueError("planner step instructions must not contain executable code blocks")
        return value


class PlannerOutput(StrictModel):
    goal: str
    requirements: list[str]
    risks: list[str]
    steps: list[TaskStep]
    success_criteria: list[str]


class ExecutorStepResult(StrictModel):
    step_id: str
    status: str = Field(pattern="^(complete|blocked)$")
    output: str
    evidence: list[str] = Field(default_factory=list)


class ExecutorOutput(StrictModel):
    goal: str
    plan_fingerprint: str
    step_results: list[ExecutorStepResult]
    final_artifact: str


class CriticOutput(StrictModel):
    confidence: float = Field(ge=0.0, le=1.0)
    flaws: list[str]
    missing_evidence: list[str]
    risk_notes: list[str]
    requires_reflection: bool


class VerifierOutput(StrictModel):
    valid: bool
    passed_criteria: list[str]
    failed_criteria: list[str]
    format_errors: list[str]
    fact_check_notes: list[str]


class ReflectionOutput(StrictModel):
    prompts: list[str]
    answers: dict[str, str]
    revised_plan: PlannerOutput | None = None


class ReasoningTrace(StrictModel):
    run_id: str
    prompt: str
    initial_plan: PlannerOutput
    executions: list[ExecutorOutput]
    critiques: list[CriticOutput]
    verifications: list[VerifierOutput]
    reflections: list[ReflectionOutput]
    final_output: str
    accepted: bool
    confidence: float = Field(ge=0.0, le=1.0)
    created_at_unix: float = Field(default_factory=time.time)


REFLECTION_PROMPTS = [
    "What assumptions are wrong?",
    "What can fail?",
    "What security risks exist?",
    "What was not verified?",
]

EXECUTABLE_CODE_MARKERS = [
    "```",
    "def ",
    "class ",
    "function ",
    "import ",
    "#include",
    "npm install",
    "pip install",
    "curl ",
    "rm -",
    "powershell",
]


def stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9_+-]*", text.lower())


def lexical_similarity(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def recency_weight(last_used: float, *, now: float | None = None, half_life_seconds: float = 60 * 60 * 24 * 30) -> float:
    current = now or time.time()
    age = max(0.0, current - last_used)
    return math.exp(-age / max(1.0, half_life_seconds))


def usage_count_weight(usage_count: int, *, saturation: int = 20) -> float:
    return min(1.0, math.log1p(max(0, usage_count)) / math.log1p(saturation))


def plan_fingerprint(plan: PlannerOutput) -> str:
    return stable_id("plan", stable_json(plan.model_dump()))


class PlannerComponent(Protocol):
    def plan(self, prompt: str, *, reflection: ReflectionOutput | None = None) -> PlannerOutput:
        ...


class ExecutorComponent(Protocol):
    def execute(self, plan: PlannerOutput) -> ExecutorOutput:
        ...


class CriticComponent(Protocol):
    def critique(self, plan: PlannerOutput, execution: ExecutorOutput) -> CriticOutput:
        ...


class VerifierComponent(Protocol):
    def verify(self, plan: PlannerOutput, execution: ExecutorOutput) -> VerifierOutput:
        ...


class DeterministicPlanner:
    """Planner only emits graph structure, requirements, risks, and criteria."""

    def plan(self, prompt: str, *, reflection: ReflectionOutput | None = None) -> PlannerOutput:
        lower = prompt.lower()
        requirements = self._requirements(prompt)
        risks = [
            "ambiguous scope",
            "missing verification",
            "security regression",
            "context pollution",
        ]
        if any(marker in lower for marker in ["auth", "jwt", "login", "session", "security"]):
            risks.extend(["authentication bypass", "secret leakage", "insufficient rate limiting"])
        if reflection:
            risks.extend(sorted({answer for answer in reflection.answers.values() if answer})[:4])
        steps = [
            TaskStep(
                step_id="step-1-requirements",
                title="Clarify Requirements",
                instruction="Extract requirements and acceptance boundaries from the request.",
                dependencies=[],
                expected_output="requirements summary",
            ),
            TaskStep(
                step_id="step-2-design",
                title="Design Execution Strategy",
                instruction="Map required components, data boundaries, and verification points.",
                dependencies=["step-1-requirements"],
                expected_output="component strategy",
            ),
            TaskStep(
                step_id="step-3-execute",
                title="Execute Work",
                instruction="Produce the requested artifact while preserving stated constraints.",
                dependencies=["step-2-design"],
                expected_output="candidate artifact",
            ),
            TaskStep(
                step_id="step-4-validate",
                title="Validate Output",
                instruction="Compare the artifact with the success criteria and evidence requirements.",
                dependencies=["step-3-execute"],
                expected_output="validation evidence",
            ),
        ]
        if reflection:
            steps.append(
                TaskStep(
                    step_id="step-5-revise",
                    title="Apply Reflection Corrections",
                    instruction="Adjust the artifact according to reflection findings and missing verification.",
                    dependencies=["step-4-validate"],
                    expected_output="revised artifact",
                )
            )
        success = [
            "all requirements are addressed",
            "all ordered steps completed",
            "security risks are named",
            "verification evidence exists",
            "output follows the requested format",
        ]
        return PlannerOutput(
            goal=f"Complete the task: {prompt.strip()}",
            requirements=requirements,
            risks=list(dict.fromkeys(risks)),
            steps=steps,
            success_criteria=success,
        )

    def _requirements(self, prompt: str) -> list[str]:
        base = ["preserve role separation", "return schema-valid output", "include verification evidence"]
        lower = prompt.lower()
        if any(marker in lower for marker in ["memory", "context", "retrieval"]):
            base.extend(["use anti-pollution memory policy", "rank retrieved memory mathematically"])
        if any(marker in lower for marker in ["security", "auth", "jwt", "session"]):
            base.extend(["include security criteria", "avoid secrets and unsafe defaults"])
        if any(marker in lower for marker in ["api", "backend", "engine", "architecture"]):
            base.extend(["keep modules composable", "define typed interfaces"])
        return list(dict.fromkeys(base))


class StrictExecutor:
    """Executor follows the planner graph; it cannot change the plan."""

    def execute(self, plan: PlannerOutput) -> ExecutorOutput:
        fingerprint = plan_fingerprint(plan)
        completed: set[str] = set()
        results: list[ExecutorStepResult] = []
        for step in plan.steps:
            unresolved = [dependency for dependency in step.dependencies if dependency not in completed]
            if unresolved:
                results.append(
                    ExecutorStepResult(
                        step_id=step.step_id,
                        status="blocked",
                        output=f"blocked by unresolved dependencies: {', '.join(unresolved)}",
                        evidence=[],
                    )
                )
                continue
            output = f"{step.title}: completed according to plan. Expected output: {step.expected_output}."
            evidence = [f"plan_step={step.step_id}", f"plan_fingerprint={fingerprint}"]
            results.append(ExecutorStepResult(step_id=step.step_id, status="complete", output=output, evidence=evidence))
            completed.add(step.step_id)
        final = "\n".join(result.output for result in results)
        return ExecutorOutput(goal=plan.goal, plan_fingerprint=fingerprint, step_results=results, final_artifact=final)


class StrictCritic:
    """Critic flags flaws and computes confidence. It never provides a solution."""

    def critique(self, plan: PlannerOutput, execution: ExecutorOutput) -> CriticOutput:
        flaws: list[str] = []
        missing: list[str] = []
        notes: list[str] = []
        if execution.plan_fingerprint != plan_fingerprint(plan):
            flaws.append("executor output does not match planner fingerprint")
        blocked = [result.step_id for result in execution.step_results if result.status != "complete"]
        if blocked:
            flaws.append(f"blocked steps: {', '.join(blocked)}")
        if any(not result.evidence for result in execution.step_results):
            flaws.append("one or more executor steps lack verification evidence")
            missing.extend(f"missing step evidence: {result.step_id}" for result in execution.step_results if not result.evidence)
        for criterion in plan.success_criteria:
            token = criterion.split()[0].lower()
            if token not in execution.final_artifact.lower() and criterion not in execution.final_artifact:
                missing.append(f"missing evidence for criterion: {criterion}")
        if not any("security" in risk.lower() for risk in plan.risks):
            notes.append("security risks were not explicitly included")
        confidence = 1.0
        confidence -= 0.16 * len(flaws)
        confidence -= 0.08 * len(missing)
        confidence -= 0.06 * len(notes)
        confidence = max(0.0, min(1.0, confidence))
        return CriticOutput(
            confidence=confidence,
            flaws=flaws,
            missing_evidence=missing,
            risk_notes=notes,
            requires_reflection=confidence < 0.6,
        )


class StrictVerifier:
    """Verifier only checks structure and criteria satisfaction."""

    def verify(self, plan: PlannerOutput, execution: ExecutorOutput) -> VerifierOutput:
        format_errors: list[str] = []
        if execution.goal != plan.goal:
            format_errors.append("execution goal differs from planner goal")
        if execution.plan_fingerprint != plan_fingerprint(plan):
            format_errors.append("execution fingerprint differs from planner fingerprint")
        result_ids = [result.step_id for result in execution.step_results]
        expected_ids = [step.step_id for step in plan.steps]
        if result_ids != expected_ids:
            format_errors.append("executor step order differs from planner step order")
        failed_criteria: list[str] = []
        passed_criteria: list[str] = []
        completed = all(result.status == "complete" for result in execution.step_results)
        for criterion in plan.success_criteria:
            if criterion == "all ordered steps completed":
                (passed_criteria if completed else failed_criteria).append(criterion)
            elif criterion == "verification evidence exists":
                has_evidence = all(result.evidence for result in execution.step_results)
                (passed_criteria if has_evidence else failed_criteria).append(criterion)
            elif criterion == "security risks are named":
                has_security = any("security" in risk.lower() or "auth" in risk.lower() for risk in plan.risks)
                (passed_criteria if has_security else failed_criteria).append(criterion)
            else:
                passed_criteria.append(criterion)
        return VerifierOutput(
            valid=not format_errors and not failed_criteria,
            passed_criteria=passed_criteria,
            failed_criteria=failed_criteria,
            format_errors=format_errors,
            fact_check_notes=["schema and success criteria checked without logical revision"],
        )


class ReasoningEngine:
    """Strict Think -> Execute -> Reflect -> Revise pipeline."""

    def __init__(
        self,
        *,
        planner: PlannerComponent | None = None,
        executor: ExecutorComponent | None = None,
        critic: CriticComponent | None = None,
        verifier: VerifierComponent | None = None,
        reflection_threshold: float = 0.6,
        max_reflection_passes: int = 1,
    ) -> None:
        self.planner = planner or DeterministicPlanner()
        self.executor = executor or StrictExecutor()
        self.critic = critic or StrictCritic()
        self.verifier = verifier or StrictVerifier()
        self.reflection_threshold = reflection_threshold
        self.max_reflection_passes = max_reflection_passes
        self.schemas = SchemaRegistry()
        for name, model in {
            "planner_output": PlannerOutput,
            "executor_output": ExecutorOutput,
            "critic_output": CriticOutput,
            "verifier_output": VerifierOutput,
            "reflection_output": ReflectionOutput,
            "reasoning_trace": ReasoningTrace,
        }.items():
            self.schemas.register(name, model)

    def run(self, prompt: str, *, run_id: str | None = None) -> ReasoningTrace:
        actual_run_id = run_id or f"phase51-{time.time_ns()}"
        plan = self._validate_plan(self.planner.plan(prompt))
        self._enforce_planner_contract(plan)
        initial_plan = plan
        executions: list[ExecutorOutput] = []
        critiques: list[CriticOutput] = []
        verifications: list[VerifierOutput] = []
        reflections: list[ReflectionOutput] = []

        for pass_index in range(self.max_reflection_passes + 1):
            execution = self._validate_execution(self.executor.execute(plan))
            self._enforce_executor_contract(plan, execution)
            critique = self._validate_critic(self.critic.critique(plan, execution))
            self._enforce_critic_contract(critique)
            verification = self._validate_verifier(self.verifier.verify(plan, execution))
            executions.append(execution)
            critiques.append(critique)
            verifications.append(verification)
            if critique.confidence >= self.reflection_threshold or pass_index >= self.max_reflection_passes:
                break
            reflection = self._reflection_pass(plan, execution, critique)
            reflections.append(reflection)
            if reflection.revised_plan is not None:
                plan = self._validate_plan(reflection.revised_plan)
                self._enforce_planner_contract(plan)

        final_confidence = critiques[-1].confidence if critiques else 0.0
        accepted = bool(verifications[-1].valid and final_confidence >= self.reflection_threshold)
        trace = ReasoningTrace(
            run_id=actual_run_id,
            prompt=prompt,
            initial_plan=initial_plan,
            executions=executions,
            critiques=critiques,
            verifications=verifications,
            reflections=reflections,
            final_output=executions[-1].final_artifact if executions else "",
            accepted=accepted,
            confidence=final_confidence,
        )
        self.schemas.validate("reasoning_trace", trace.model_dump())
        return trace

    def _reflection_pass(self, plan: PlannerOutput, execution: ExecutorOutput, critique: CriticOutput) -> ReflectionOutput:
        answers = {
            "What assumptions are wrong?": "Assume criteria without evidence is unsafe.",
            "What can fail?": "; ".join(critique.flaws or ["executor may miss success criteria"]),
            "What security risks exist?": "; ".join(critique.risk_notes or ["security verification may be incomplete"]),
            "What was not verified?": "; ".join(critique.missing_evidence or ["success criteria evidence coverage"]),
        }
        revised_requirements = list(dict.fromkeys([*plan.requirements, "add explicit evidence for every success criterion"]))
        revised_success = list(dict.fromkeys([*plan.success_criteria, "reflection gaps are addressed"]))
        revised_steps = list(plan.steps)
        revised_steps.append(
            TaskStep(
                step_id=f"step-{len(revised_steps) + 1}-reflection-evidence",
                title="Add Reflection Evidence",
                instruction="Record evidence for every missing criterion flagged by the critic.",
                dependencies=[revised_steps[-1].step_id] if revised_steps else [],
                expected_output="reflection evidence map",
            )
        )
        revised_plan = plan.model_copy(
            update={
                "requirements": revised_requirements,
                "risks": list(dict.fromkeys([*plan.risks, *answers.values()])),
                "steps": revised_steps,
                "success_criteria": revised_success,
            }
        )
        reflection = ReflectionOutput(prompts=REFLECTION_PROMPTS, answers=answers, revised_plan=revised_plan)
        self.schemas.validate("reflection_output", reflection.model_dump())
        return reflection

    def _validate_plan(self, output: PlannerOutput | dict[str, Any]) -> PlannerOutput:
        return PlannerOutput.model_validate(self.schemas.validate("planner_output", output.model_dump() if isinstance(output, BaseModel) else output))

    def _validate_execution(self, output: ExecutorOutput | dict[str, Any]) -> ExecutorOutput:
        return ExecutorOutput.model_validate(self.schemas.validate("executor_output", output.model_dump() if isinstance(output, BaseModel) else output))

    def _validate_critic(self, output: CriticOutput | dict[str, Any]) -> CriticOutput:
        return CriticOutput.model_validate(self.schemas.validate("critic_output", output.model_dump() if isinstance(output, BaseModel) else output))

    def _validate_verifier(self, output: VerifierOutput | dict[str, Any]) -> VerifierOutput:
        return VerifierOutput.model_validate(self.schemas.validate("verifier_output", output.model_dump() if isinstance(output, BaseModel) else output))

    def _enforce_planner_contract(self, plan: PlannerOutput) -> None:
        serialized = stable_json(plan.model_dump()).lower()
        if any(marker in serialized for marker in EXECUTABLE_CODE_MARKERS):
            raise RoleContractViolation("planner output appears to contain executable code or commands")

    def _enforce_executor_contract(self, plan: PlannerOutput, execution: ExecutorOutput) -> None:
        if execution.plan_fingerprint != plan_fingerprint(plan):
            raise RoleContractViolation("executor altered or ignored the planner fingerprint")
        expected = [step.step_id for step in plan.steps]
        actual = [result.step_id for result in execution.step_results]
        if actual != expected:
            raise RoleContractViolation("executor changed planner step order")

    def _enforce_critic_contract(self, critique: CriticOutput) -> None:
        serialized = stable_json(critique.model_dump()).lower()
        if any(marker in serialized for marker in ["solution:", "fix code", "patch:", "```"]):
            raise RoleContractViolation("critic must not provide the solution")


class MemoryLayer(str, Enum):
    WORKING = "working"
    PROJECT = "project"
    EXPERIENCE = "experience"
    KNOWLEDGE_GRAPH = "knowledge_graph"


class MemoryKind(str, Enum):
    VERIFIED_FIX = "verified_fix"
    PASSED_BENCHMARK = "passed_benchmark"
    SECURITY_FINDING = "security_finding"
    SUCCESSFUL_PLAN = "successful_plan"
    RAW_THOUGHT = "raw_thought"
    FAILED_GUESS = "failed_guess"
    TEMPORARY_OUTPUT = "temporary_output"


ALLOWED_MEMORY_KINDS = {
    MemoryKind.VERIFIED_FIX,
    MemoryKind.PASSED_BENCHMARK,
    MemoryKind.SECURITY_FINDING,
    MemoryKind.SUCCESSFUL_PLAN,
}


class QualityMetadata(StrictModel):
    relevance: float = Field(ge=0.0, le=1.0)
    success_rate: float = Field(ge=0.0, le=1.0)
    last_used: float = Field(default_factory=time.time)
    usage_count: int = Field(default=0, ge=0)
    retrieval_count: int = Field(default=0, ge=0)
    retrieval_score_sum: float = Field(default=0.0, ge=0.0)

    @property
    def average_retrieval_score(self) -> float:
        return self.retrieval_score_sum / self.retrieval_count if self.retrieval_count else 0.0


class MemoryEntry(StrictModel):
    entry_id: str
    project_id: str = "default"
    layer: MemoryLayer
    kind: MemoryKind
    text: str
    payload: dict[str, Any]
    content_hash: str = ""
    embedding: list[float] = Field(default_factory=list)
    verification_status: str = Field(default="verified", pattern="^(verified|reviewed)$")
    quality: QualityMetadata
    created_at_unix: float = Field(default_factory=time.time)


class RankedMemoryHit(StrictModel):
    entry: MemoryEntry
    vector_similarity: float = Field(ge=0.0, le=1.0)
    success_rate: float = Field(ge=0.0, le=1.0)
    recency_weight: float = Field(ge=0.0, le=1.0)
    usage_count_weight: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)


class KnowledgeNode(StrictModel):
    node_id: str
    label: str
    kind: str = "concept"
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeEdge(StrictModel):
    edge_id: str
    source: str
    relation: str
    target: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class KnowledgeGraphStore(StrictModel):
    nodes: dict[str, KnowledgeNode] = Field(default_factory=dict)
    edges: dict[str, KnowledgeEdge] = Field(default_factory=dict)


class MemoryRetrievalReport(StrictModel):
    query: str
    hits: list[RankedMemoryHit]
    formula: str
    created_at_unix: float = Field(default_factory=time.time)


class UnifiedMemoryManager:
    """Four-tier memory manager with anti-pollution gates and ranking."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        session_id: str | None = None,
        project_id: str = "default",
        embedding_dimensions: int = 384,
        strict_embeddings: bool = False,
    ) -> None:
        self.root = root or (ROOT / "artifacts" / "phase51" / "memory")
        self.root.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or stable_id("session", time.time_ns())
        self.project_id = self._safe_project_id(project_id)
        self.project_root = self.root / "projects" / self.project_id
        self.project_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.embedder, self.embedding_backend, self.embedding_dimensions = build_embedder(
            embedding_dimensions,
            strict_external=strict_embeddings,
        )
        self.working_memory: dict[str, Any] = {}
        self.knowledge_graph = self._load_graph()

    def set_working_memory(self, *, project: str, current_feature: str) -> MemoryEntry:
        self.working_memory = {"project": project, "current_feature": current_feature}
        return self.save(
            layer=MemoryLayer.WORKING,
            kind=MemoryKind.SUCCESSFUL_PLAN,
            payload=self.working_memory,
            text=f"project={project} current_feature={current_feature}",
            relevance=0.8,
            success_rate=1.0,
        )

    def save_project_memory(self, *, module_name: str, path: str, tech_stack: str, relevance: float = 0.75) -> MemoryEntry:
        return self.save(
            layer=MemoryLayer.PROJECT,
            kind=MemoryKind.SUCCESSFUL_PLAN,
            payload={"module_name": module_name, "path": path, "tech_stack": tech_stack},
            text=f"{module_name} {path} {tech_stack}",
            relevance=relevance,
            success_rate=1.0,
        )

    def save_experience_memory(self, *, failure: str, fix: str, context: str, relevance: float = 0.8, success_rate: float = 1.0) -> MemoryEntry:
        return self.save(
            layer=MemoryLayer.EXPERIENCE,
            kind=MemoryKind.VERIFIED_FIX,
            payload={"failure": failure, "fix": fix, "context": context},
            text=f"failure: {failure}\nfix: {fix}\ncontext: {context}",
            relevance=relevance,
            success_rate=success_rate,
        )

    def add_knowledge_relation(
        self,
        source_label: str,
        relation: str,
        target_label: str,
        *,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[KnowledgeNode, KnowledgeEdge]:
        source = self._add_node(source_label, metadata=metadata or {})
        target = self._add_node(target_label, metadata=metadata or {})
        edge = KnowledgeEdge(
            edge_id=stable_id(source.node_id, relation, target.node_id),
            source=source.node_id,
            relation=relation,
            target=target.node_id,
            weight=weight,
        )
        self.knowledge_graph.edges[edge.edge_id] = edge
        self._save_graph()
        self.save(
            layer=MemoryLayer.KNOWLEDGE_GRAPH,
            kind=MemoryKind.SECURITY_FINDING if "security" in relation.lower() else MemoryKind.SUCCESSFUL_PLAN,
            payload={
                "finding": f"knowledge relation: {source_label} {relation} {target_label}",
                "source": source_label,
                "relation": relation,
                "target": target_label,
                "weight": weight,
            },
            text=f"{source_label} {relation} {target_label}",
            relevance=min(1.0, weight),
            success_rate=1.0,
        )
        return source, edge

    def save(
        self,
        *,
        layer: MemoryLayer | str,
        kind: MemoryKind | str,
        payload: dict[str, Any],
        text: str,
        relevance: float,
        success_rate: float,
    ) -> MemoryEntry:
        parsed_layer = MemoryLayer(layer)
        parsed_kind = MemoryKind(kind)
        self._validate_ingestion(parsed_kind, payload, text)
        content_hash = hashlib.sha256(stable_json({"text": text, "payload": payload}).encode("utf-8")).hexdigest()
        entry = MemoryEntry(
            entry_id=stable_id(self.project_id, parsed_layer.value, parsed_kind.value, content_hash),
            project_id=self.project_id,
            layer=parsed_layer,
            kind=parsed_kind,
            text=text,
            payload=payload,
            content_hash=content_hash,
            embedding=self.embedder.embed(text),
            quality=QualityMetadata(relevance=relevance, success_rate=success_rate),
        )
        if parsed_layer == MemoryLayer.WORKING:
            self._upsert_jsonl(self._session_path(), entry)
        else:
            self._upsert_jsonl(self._path(parsed_layer), entry)
        return entry

    def retrieve(self, query: str, *, limit: int = 8, layers: list[MemoryLayer | str] | None = None) -> MemoryRetrievalReport:
        entries = self._load_entries(layers=layers)
        hits: list[RankedMemoryHit] = []
        now = time.time()
        query_embedding = self.embedder.embed(query)
        for entry in entries:
            entry_embedding = entry.embedding or self.embedder.embed(entry.text)
            vector = max(0.0, min(1.0, cosine_similarity(query_embedding, entry_embedding)))
            recent = recency_weight(entry.quality.last_used, now=now)
            usage = usage_count_weight(entry.quality.usage_count)
            # Required exact formula:
            # Final Score = (0.4 * Vector Similarity) + (0.3 * Success Rate)
            #             + (0.2 * Recency Weight) + (0.1 * Usage Count Weight)
            final_score = (0.4 * vector) + (0.3 * entry.quality.success_rate) + (0.2 * recent) + (0.1 * usage)
            hits.append(
                RankedMemoryHit(
                    entry=entry,
                    vector_similarity=vector,
                    success_rate=entry.quality.success_rate,
                    recency_weight=recent,
                    usage_count_weight=usage,
                    final_score=max(0.0, min(1.0, final_score)),
                )
            )
        hits.sort(key=lambda hit: (hit.final_score, hit.entry.quality.relevance, hit.entry.created_at_unix), reverse=True)
        selected = hits[:limit]
        self._mark_used({hit.entry.entry_id: hit.final_score for hit in selected})
        return MemoryRetrievalReport(
            query=query,
            hits=selected,
            formula="Final Score = (0.4 * Vector Similarity) + (0.3 * Success Rate) + (0.2 * Recency Weight) + (0.1 * Usage Count Weight)",
        )

    def archive_low_quality(self, *, threshold: float = 0.35, min_observations: int = 3) -> dict[str, Any]:
        archived: list[str] = []
        retained_by_layer: dict[MemoryLayer, list[MemoryEntry]] = {layer: [] for layer in MemoryLayer if layer != MemoryLayer.WORKING}
        for layer in retained_by_layer:
            for entry in self._read_jsonl(self._path(layer)):
                base_quality = (
                    0.55 * entry.quality.success_rate
                    + 0.35 * entry.quality.relevance
                    + 0.10 * usage_count_weight(entry.quality.usage_count)
                )
                observed_quality = entry.quality.average_retrieval_score
                consistently_low = entry.quality.retrieval_count >= min_observations and observed_quality < threshold
                if consistently_low and base_quality < max(0.5, threshold):
                    archived.append(entry.entry_id)
                    self._write_jsonl(self.project_root / "cold_storage.jsonl", entry)
                else:
                    retained_by_layer[layer].append(entry)
        for layer, entries in retained_by_layer.items():
            self._rewrite_jsonl(self._path(layer), entries)
        return {
            "archived": archived,
            "archived_count": len(archived),
            "threshold": threshold,
            "min_observations": min_observations,
        }

    async def sync_external(self) -> dict[str, Any]:
        """Mirror verified project memory to configured Postgres/Qdrant sinks."""
        entries = self._load_entries(layers=[MemoryLayer.PROJECT, MemoryLayer.EXPERIENCE, MemoryLayer.KNOWLEDGE_GRAPH])
        records = [
            MemoryRecord(
                record_id=entry.entry_id,
                workspace=self.project_id,
                source=f"phase51:{entry.layer.value}:{entry.kind.value}",
                content=entry.text,
                embedding=entry.embedding or self.embedder.embed(entry.text),
                metadata=entry.model_dump(exclude={"embedding"}),
                created_at_ms=int(entry.created_at_unix * 1000),
            )
            for entry in entries
            if entry.verification_status in {"verified", "reviewed"}
        ]
        gateway = PersistentMemoryGateway(
            workspace=self.project_id,
            qdrant_url=__import__("os").environ.get("PHASE51_QDRANT_URL") or __import__("os").environ.get("PHASE11_QDRANT_URL"),
            postgres_dsn=__import__("os").environ.get("PHASE51_POSTGRES_DSN") or __import__("os").environ.get("PHASE11_POSTGRES_DSN"),
            qdrant_collection="phase51_unified_memory",
            embedding_dimensions=self.embedding_dimensions,
        )
        try:
            initialized = await gateway.initialize()
            result = await gateway.upsert(records)
            return {"project_id": self.project_id, "records": len(records), "initialize": initialized, "upsert": result}
        finally:
            await gateway.aclose()

    def clear_working_memory(self) -> None:
        self.working_memory = {}
        session = self._session_path()
        if session.exists():
            session.unlink()

    def _validate_ingestion(self, kind: MemoryKind, payload: dict[str, Any], text: str) -> None:
        if kind not in ALLOWED_MEMORY_KINDS:
            raise MemoryIngestionRejected(f"memory kind rejected by anti-pollution gate: {kind.value}")
        lower = f"{text}\n{stable_json(payload)}".lower()
        pollution_markers = ["raw thought", "guess", "maybe", "temporary", "scratchpad", "failed guess"]
        if any(marker in lower for marker in pollution_markers) and kind not in {MemoryKind.SECURITY_FINDING}:
            raise MemoryIngestionRejected("memory content appears to be raw thought, failed guess, or temporary output")
        required_by_kind = {
            MemoryKind.VERIFIED_FIX: {"failure", "fix", "context"},
            MemoryKind.PASSED_BENCHMARK: {"benchmark", "score"},
            MemoryKind.SECURITY_FINDING: {"finding"},
            MemoryKind.SUCCESSFUL_PLAN: set(),
        }
        required = required_by_kind[kind]
        if required and not required.issubset(payload):
            raise MemoryIngestionRejected(f"{kind.value} memory missing required fields: {sorted(required - set(payload))}")

    def _load_entries(self, *, layers: list[MemoryLayer | str] | None) -> list[MemoryEntry]:
        selected_layers = [MemoryLayer(layer) for layer in layers] if layers else list(MemoryLayer)
        entries: list[MemoryEntry] = []
        for layer in selected_layers:
            if layer == MemoryLayer.WORKING:
                entries.extend(self._read_jsonl(self._session_path()))
            else:
                entries.extend(self._read_jsonl(self._path(layer)))
        return entries

    def _mark_used(self, scores: dict[str, float]) -> None:
        if not scores:
            return
        wanted = set(scores)
        for layer in [MemoryLayer.PROJECT, MemoryLayer.EXPERIENCE, MemoryLayer.KNOWLEDGE_GRAPH]:
            path = self._path(layer)
            entries = []
            for entry in self._read_jsonl(path):
                if entry.entry_id in wanted:
                    entry = entry.model_copy(
                        update={
                            "quality": entry.quality.model_copy(
                                update={"last_used": time.time(), "usage_count": entry.quality.usage_count + 1}
                                | {
                                    "retrieval_count": entry.quality.retrieval_count + 1,
                                    "retrieval_score_sum": entry.quality.retrieval_score_sum + scores[entry.entry_id],
                                }
                            )
                        }
                    )
                entries.append(entry)
            self._rewrite_jsonl(path, entries)

    def _add_node(self, label: str, *, metadata: dict[str, Any]) -> KnowledgeNode:
        node_id = stable_id("node", label)
        node = self.knowledge_graph.nodes.get(node_id) or KnowledgeNode(node_id=node_id, label=label, metadata=metadata)
        self.knowledge_graph.nodes[node_id] = node
        return node

    def _load_graph(self) -> KnowledgeGraphStore:
        path = self.project_root / "knowledge_graph.json"
        if not path.exists():
            return KnowledgeGraphStore()
        try:
            return KnowledgeGraphStore.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return KnowledgeGraphStore()

    def _save_graph(self) -> None:
        self.project_root.mkdir(parents=True, exist_ok=True)
        (self.project_root / "knowledge_graph.json").write_text(
            json.dumps(self.knowledge_graph.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _path(self, layer: MemoryLayer) -> Path:
        return self.project_root / f"{layer.value}.jsonl"

    def _session_path(self) -> Path:
        return self.project_root / f"working_{self.session_id}.jsonl"

    def _safe_project_id(self, project_id: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", project_id.strip()).strip("-.")
        return normalized[:100] or "default"

    def _write_jsonl(self, path: Path, entry: MemoryEntry) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")

    def _upsert_jsonl(self, path: Path, entry: MemoryEntry) -> None:
        with self._lock:
            existing = {item.entry_id: item for item in self._read_jsonl(path)}
            previous = existing.get(entry.entry_id)
            if previous is not None:
                entry = entry.model_copy(
                    update={
                        "created_at_unix": previous.created_at_unix,
                        "quality": previous.quality.model_copy(
                            update={
                                "relevance": max(previous.quality.relevance, entry.quality.relevance),
                                "success_rate": max(previous.quality.success_rate, entry.quality.success_rate),
                            }
                        ),
                    }
                )
            existing[entry.entry_id] = entry
            self._rewrite_jsonl(path, list(existing.values()))

    def _rewrite_jsonl(self, path: Path, entries: list[MemoryEntry]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")
        temporary.replace(path)

    def _read_jsonl(self, path: Path) -> list[MemoryEntry]:
        if not path.exists():
            return []
        entries: list[MemoryEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(MemoryEntry.model_validate(json.loads(line)))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return entries


class Phase51SmokeReport(StrictModel):
    run_id: str
    reasoning: ReasoningTrace
    reflection_contract: ReasoningTrace
    retrieval: MemoryRetrievalReport
    memory_lifecycle: dict[str, Any]
    rejected_memory: bool
    archive: dict[str, Any]
    schemas: dict[str, dict[str, Any]]
    created_at_unix: float = Field(default_factory=time.time)


class LowEvidenceExecutor:
    """Test executor that follows the plan but omits evidence to trigger reflection."""

    def execute(self, plan: PlannerOutput) -> ExecutorOutput:
        return ExecutorOutput(
            goal=plan.goal,
            plan_fingerprint=plan_fingerprint(plan),
            step_results=[
                ExecutorStepResult(
                    step_id=step.step_id,
                    status="complete",
                    output=f"{step.title}: minimal output.",
                    evidence=[],
                )
                for step in plan.steps
            ],
            final_artifact="minimal output without verification evidence",
        )


def run_smoke(prompt: str, *, run_id: str, output_dir: Path) -> Phase51SmokeReport:
    engine = ReasoningEngine()
    reasoning = engine.run(prompt, run_id=f"{run_id}-reasoning")
    reflection_contract = ReasoningEngine(executor=LowEvidenceExecutor(), max_reflection_passes=1).run(
        "underspecified task with missing evidence",
        run_id=f"{run_id}-reflection-contract",
    )
    memory = UnifiedMemoryManager(output_dir / "memory", session_id=run_id, project_id="phase51-smoke-project")
    memory.set_working_memory(project="AI_Architecture_Build", current_feature="high-stability reasoning memory")
    project_entry = memory.save_project_memory(
        module_name="Phase 51",
        path="src/phase51/high_stability_reasoning_memory.py",
        tech_stack="Python + Pydantic",
    )
    duplicate_entry = memory.save_project_memory(
        module_name="Phase 51",
        path="src/phase51/high_stability_reasoning_memory.py",
        tech_stack="Python + Pydantic",
    )
    memory.save_experience_memory(
        failure="planner and critic role mixing polluted context",
        fix="strict schemas plus role contract checks",
        context="Phase 51 reasoning engine",
    )
    memory.add_knowledge_relation("JWT", "supports_security_boundary_for", "Session", weight=0.86)
    rejected = False
    try:
        memory.save(
            layer=MemoryLayer.EXPERIENCE,
            kind=MemoryKind.RAW_THOUGHT,
            payload={"thought": "maybe this is useful"},
            text="raw thought maybe temporary",
            relevance=0.1,
            success_rate=0.0,
        )
    except MemoryIngestionRejected:
        rejected = True
    retrieval = memory.retrieve("strict planner memory security session", limit=5)
    isolated_memory = UnifiedMemoryManager(output_dir / "memory", session_id=f"{run_id}-isolated", project_id="isolated-project")
    isolated_hits = isolated_memory.retrieve("Phase 51 strict planner", limit=5).hits
    project_records = memory._read_jsonl(memory._path(MemoryLayer.PROJECT))
    working_path = memory._session_path()
    memory.clear_working_memory()
    lifecycle = {
        "project_id": memory.project_id,
        "embedding_backend": memory.embedding_backend,
        "deduplicated": project_entry.entry_id == duplicate_entry.entry_id and len(project_records) == 1,
        "cross_project_isolated": not isolated_hits,
        "session_cleared": not working_path.exists(),
    }
    archive = memory.archive_low_quality()
    return Phase51SmokeReport(
        run_id=run_id,
        reasoning=reasoning,
        reflection_contract=reflection_contract,
        retrieval=retrieval,
        memory_lifecycle=lifecycle,
        rejected_memory=rejected,
        archive=archive,
        schemas={
            "planner_output": engine.schemas.schema("planner_output"),
            "executor_output": engine.schemas.schema("executor_output"),
            "critic_output": engine.schemas.schema("critic_output"),
            "verifier_output": engine.schemas.schema("verifier_output"),
        },
    )


def write_report(report: Phase51SmokeReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "high-stability-reasoning-memory-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 51 strict reasoning and memory smoke.")
    parser.add_argument("--prompt", default="build a secure auth memory architecture")
    parser.add_argument("--run-id", default=f"phase51-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase51")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_smoke(args.prompt, run_id=args.run_id, output_dir=args.output_dir)
    json_path, _ = write_report(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "accepted": report.reasoning.accepted,
                "confidence": report.reasoning.confidence,
                "reflection_passes": len(report.reasoning.reflections),
                "reflection_contract_triggered": len(report.reflection_contract.reflections) > 0,
                "reflection_contract_confidences": [critique.confidence for critique in report.reflection_contract.critiques],
                "memory_hits": len(report.retrieval.hits),
                "rejected_memory": report.rejected_memory,
                "json": str(json_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
