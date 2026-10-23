"""Small, database-agnostic adapter for course learning graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LearningGraphError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedGoal:
    graph_id: str
    target_node_id: str
    score: int


class GraphCatalog:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.graphs: dict[str, dict[str, Any]] = {}
        for path in sorted(self.root.glob("*.json")):
            graph = json.loads(path.read_text(encoding="utf-8"))
            self._validate(graph, path)
            self.graphs[graph["graph_id"]] = graph
        if not self.graphs:
            raise LearningGraphError(f"no graph files found in {self.root}")

    def _validate(self, graph: dict[str, Any], path: Path) -> None:
        graph_id = str(graph.get("graph_id") or "").strip()
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if not graph_id or not isinstance(nodes, list):
            raise LearningGraphError(f"invalid graph schema: {path.name}")
        node_ids = [str(node.get("id") or "") for node in nodes]
        if not all(node_ids) or len(node_ids) != len(set(node_ids)):
            raise LearningGraphError(f"duplicate or empty node id: {path.name}")
        known = set(node_ids)
        adjacency = {node_id: [] for node_id in node_ids}
        for edge in edges:
            source, target = edge.get("source"), edge.get("target")
            if source not in known or target not in known:
                raise LearningGraphError(f"edge references unknown node: {path.name}")
            if edge.get("type", "prerequisite") == "prerequisite":
                adjacency[source].append(target)
        state: dict[str, int] = {}

        def visit(node_id: str) -> None:
            if state.get(node_id) == 1:
                raise LearningGraphError(f"cycle detected in graph {graph_id}")
            if state.get(node_id) == 2:
                return
            state[node_id] = 1
            for child in adjacency[node_id]:
                visit(child)
            state[node_id] = 2

        for node_id in node_ids:
            visit(node_id)

    def graph(self, graph_id: str) -> dict[str, Any]:
        try:
            return self.graphs[graph_id]
        except KeyError as exc:
            raise LearningGraphError(f"unknown graph: {graph_id}") from exc

    def resolve_goal(self, goal: str) -> ResolvedGoal:
        normalized = str(goal or "").lower().replace(" ", "")
        best: ResolvedGoal | None = None
        for graph_id, graph in self.graphs.items():
            graph_terms = [graph.get("title", "")] + list(graph.get("aliases") or [])
            graph_bonus = sum(1 for term in graph_terms if term and str(term).lower().replace(" ", "") in normalized)
            for node in graph["nodes"]:
                score = graph_bonus
                title = str(node.get("title") or "").lower().replace(" ", "")
                if title and title in normalized:
                    score += 6
                for alias in node.get("aliases") or []:
                    if str(alias).lower().replace(" ", "") in normalized:
                        score += 3
                for tag in node.get("tags") or []:
                    if str(tag).lower().replace(" ", "") in normalized:
                        score += 2
                candidate = ResolvedGoal(graph_id, node["id"], score)
                if score > 0 and (best is None or candidate.score > best.score):
                    best = candidate
        if best is None:
            graph_id, graph = next(iter(self.graphs.items()))
            best = ResolvedGoal(graph_id, graph["nodes"][-1]["id"], 0)
        return best

    def build_learning_path(
        self, graph_id: str, target_node_id: str, budget_minutes: int | None = None,
    ) -> list[dict[str, Any]]:
        graph = self.graph(graph_id)
        by_id = {node["id"]: node for node in graph["nodes"]}
        if target_node_id not in by_id:
            raise LearningGraphError(f"unknown node: {target_node_id}")
        prerequisites: dict[str, list[str]] = {node_id: [] for node_id in by_id}
        for edge in graph.get("edges") or []:
            if edge.get("type", "prerequisite") == "prerequisite":
                prerequisites[edge["target"]].append(edge["source"])
        ordered: list[str] = []

        def add(node_id: str) -> None:
            for prerequisite in prerequisites[node_id]:
                add(prerequisite)
            if node_id not in ordered:
                ordered.append(node_id)

        add(target_node_id)
        path = [dict(by_id[node_id]) for node_id in ordered]
        if budget_minutes is None:
            return path
        bounded = []
        used = 0
        for node in path:
            duration = max(1, int(node.get("estimated_minutes") or 20))
            if bounded and used + duration > budget_minutes:
                break
            if not bounded and duration > budget_minutes:
                break
            bounded.append(node)
            used += duration
        return bounded
