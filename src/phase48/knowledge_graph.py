#!/usr/bin/env python
"""Phase 48 knowledge graph.

Stores projects, phases, dependencies, bugs, fixes, technologies, and patterns
as explicit graph relationships alongside vector memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GraphNode(StrictModel):
    node_id: str
    kind: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(StrictModel):
    edge_id: str
    source: str
    relation: str
    target: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    properties: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraphReport(StrictModel):
    run_id: str
    nodes: int
    edges: int
    query: str
    matches: list[dict[str, Any]]
    context_block: str
    created_at_unix: float = Field(default_factory=time.time)


def stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


class KnowledgeGraph:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (ROOT / "artifacts" / "phase48" / "knowledge-graph.json")
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}
        self.load()

    def add_node(self, kind: str, label: str, **properties: Any) -> GraphNode:
        node_id = stable_id(kind, label)
        node = GraphNode(node_id=node_id, kind=kind, label=label, properties=properties)
        self.nodes[node_id] = node
        return node

    def add_edge(self, source: GraphNode, relation: str, target: GraphNode, *, weight: float = 1.0, **properties: Any) -> GraphEdge:
        edge_id = stable_id(source.node_id, relation, target.node_id)
        edge = GraphEdge(edge_id=edge_id, source=source.node_id, relation=relation, target=target.node_id, weight=weight, properties=properties)
        self.edges[edge_id] = edge
        return edge

    def seed_architecture(self) -> None:
        project = self.add_node("project", "AI Architecture Build")
        for phase, label in [
            ("43", "Meta Planner"),
            ("44", "Intent Expansion"),
            ("45", "Parallel Agent Runtime"),
            ("46", "Hierarchical Memory"),
            ("47", "Reasoning Engine"),
            ("48", "Knowledge Graph"),
            ("49", "Multimodal Expert"),
            ("50", "MoE Router"),
        ]:
            node = self.add_node("phase", f"Phase {phase}: {label}", phase=int(phase))
            self.add_edge(project, "contains", node)
        self.add_edge(self.add_node("phase", "Phase 44: Intent Expansion", phase=44), "feeds", self.add_node("phase", "Phase 43: Meta Planner", phase=43))
        self.add_edge(self.add_node("phase", "Phase 43: Meta Planner", phase=43), "feeds", self.add_node("phase", "Phase 45: Parallel Agent Runtime", phase=45))
        self.add_edge(self.add_node("phase", "Phase 46: Hierarchical Memory", phase=46), "informs", self.add_node("phase", "Phase 43: Meta Planner", phase=43))
        self.add_edge(self.add_node("phase", "Phase 47: Reasoning Engine", phase=47), "reviews", self.add_node("phase", "Phase 40: Integrated Agent", phase=40))
        self.add_edge(self.add_node("phase", "Phase 50: MoE Router", phase=50), "routes", self.add_node("phase", "Phase 47: Reasoning Engine", phase=47))

    def query(self, text: str, *, limit: int = 12) -> list[dict[str, Any]]:
        tokens = {token.lower().strip(".,:;") for token in text.split() if len(token) >= 2}
        matches: list[dict[str, Any]] = []
        for node in self.nodes.values():
            haystack = f"{node.kind} {node.label} {json.dumps(node.properties, ensure_ascii=False)}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                related = [edge.model_dump() for edge in self.edges.values() if edge.source == node.node_id or edge.target == node.node_id]
                matches.append({"score": score, "node": node.model_dump(), "edges": related[:8]})
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:limit]

    def run(self, query: str, *, run_id: str, seed: bool = False) -> KnowledgeGraphReport:
        if seed or not self.nodes:
            self.seed_architecture()
            self.save()
        matches = self.query(query)
        context = self.render_context(matches)
        return KnowledgeGraphReport(run_id=run_id, nodes=len(self.nodes), edges=len(self.edges), query=query, matches=matches, context_block=context)

    def render_context(self, matches: list[dict[str, Any]]) -> str:
        if not matches:
            return "No graph matches found."
        lines = []
        for match in matches:
            node = match["node"]
            relations = ", ".join(edge["relation"] for edge in match["edges"][:4])
            lines.append(f"- {node['kind']} {node['label']} relations=[{relations}]")
        return "\n".join(lines)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.nodes = {node["node_id"]: GraphNode.model_validate(node) for node in payload.get("nodes", [])}
            self.edges = {edge["edge_id"]: GraphEdge.model_validate(edge) for edge in payload.get("edges", [])}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.nodes = {}
            self.edges = {}

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"nodes": [node.model_dump() for node in self.nodes.values()], "edges": [edge.model_dump() for edge in self.edges.values()]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.path


def write_report(report: KnowledgeGraphReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "knowledge-graph-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 48 knowledge graph query.")
    parser.add_argument("--query", default="meta planner memory reasoning")
    parser.add_argument("--run-id", default=f"phase48-{int(time.time())}")
    parser.add_argument("--graph-path", type=Path, default=ROOT / "artifacts" / "phase48" / "knowledge-graph.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase48")
    parser.add_argument("--seed", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = KnowledgeGraph(args.graph_path).run(args.query, run_id=args.run_id, seed=args.seed)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "nodes": report.nodes, "edges": report.edges, "matches": len(report.matches), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
