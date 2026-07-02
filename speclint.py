#!/usr/bin/env python3
"""Lint a typed DiagramSpec — the checks D2 itself never performs.

This is a linter, separate from generation: it imports the shared `d2spec`
schema but never the generator, and the generator never imports it. Run it on a
spec when you want to; generation does not depend on it.

Checks: acyclic (DAG), frozen (every model node immutable), referential
integrity (edge endpoints and group references resolve), type-flow (a declared
edge payload must be a type the source model can produce), and reachability
(every node connects to a root — floating islands are either connected or cut,
never shipped).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from d2spec import DiagramSpec, ModelNode, is_frozen, mentioned_types


class ViolationKind(StrEnum):
    CYCLE = "cycle"
    UNFROZEN_MODEL = "unfrozen_model"
    DANGLING_REFERENCE = "dangling_reference"
    TYPE_FLOW = "type_flow"
    UNREACHABLE = "unreachable"


class Violation(BaseModel):
    kind: ViolationKind
    detail: str
    nodes: list[str]


def check_frozen(models: list[type[BaseModel]]) -> list[Violation]:
    """Durability check: every entity model must be frozen, so no produced value
    can be mutated after the fact. Sweeps a plain model list (not tied to a spec)."""
    return [
        Violation(
            kind=ViolationKind.UNFROZEN_MODEL,
            detail=f"{model.__name__} is not frozen",
            nodes=[model.__name__],
        )
        for model in models
        if not model.model_config.get("frozen", False)
    ]


def _adjacency(spec: DiagramSpec) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {node.id: [] for node in spec.nodes}
    for edge in spec.edges:
        adjacency.setdefault(edge.src, []).append(edge.dst)
        adjacency.setdefault(edge.dst, [])
    return adjacency


def find_cycles(spec: DiagramSpec) -> list[list[str]]:
    adjacency = _adjacency(spec)
    color: dict[str, int] = {
        node: 0 for node in adjacency
    }  # 0 unseen, 1 active, 2 done
    cycles: list[list[str]] = []

    def visit(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, child_index = stack[-1]
            if child_index == 0:
                color[node] = 1
                path.append(node)
            neighbours = adjacency[node]
            if child_index < len(neighbours):
                stack[-1] = (node, child_index + 1)
                neighbour = neighbours[child_index]
                if color[neighbour] == 1:
                    cycles.append(path[path.index(neighbour) :])
                elif color[neighbour] == 0:
                    stack.append((neighbour, 0))
                continue
            color[node] = 2
            path.pop()
            stack.pop()

    for node in adjacency:
        if color[node] == 0:
            visit(node)
    return cycles


def find_unreachable(spec: DiagramSpec) -> list[str]:
    """Node ids not connected to any root. Connectivity is over the undirected
    graph: a dormant marker's dashed edge into a live node counts as attached,
    and what fails is a genuinely floating island. Roots are `spec.roots`, or
    every zero-in-degree node when none are declared."""
    ids = {node.id for node in spec.nodes}
    neighbours: dict[str, set[str]] = {node_id: set() for node_id in ids}
    in_degree: dict[str, int] = {node_id: 0 for node_id in ids}
    for edge in spec.edges:
        if edge.src in ids and edge.dst in ids:
            neighbours[edge.src].add(edge.dst)
            neighbours[edge.dst].add(edge.src)
            in_degree[edge.dst] += 1

    roots: list[str] = [r for r in spec.roots if r in ids]
    if not roots:
        roots = [node_id for node_id, degree in in_degree.items() if degree == 0]
    seen: set[str] = set(roots)
    frontier: list[str] = list(roots)
    while frontier:
        current = frontier.pop()
        for neighbour in neighbours[current]:
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)
    return sorted(ids - seen)


def validate(spec: DiagramSpec) -> list[Violation]:
    violations: list[Violation] = []
    ids = {node.id for node in spec.nodes}
    group_ids = {group.id for group in spec.groups}

    for loop in find_cycles(spec):
        violations.append(
            Violation(
                kind=ViolationKind.CYCLE,
                detail="nodes form a cycle (not a DAG)",
                nodes=loop,
            )
        )

    for node in spec.nodes:
        if isinstance(node, ModelNode) and not is_frozen(node.model):
            violations.append(
                Violation(
                    kind=ViolationKind.UNFROZEN_MODEL,
                    detail=f"{node.model.__name__} is not frozen (mutable intermediate state)",
                    nodes=[node.id],
                )
            )
        if node.group is not None and node.group not in group_ids:
            violations.append(
                Violation(
                    kind=ViolationKind.DANGLING_REFERENCE,
                    detail=f"node assigned to undefined group '{node.group}'",
                    nodes=[node.id],
                )
            )

    model_of: dict[str, ModelNode] = {
        n.id: n for n in spec.nodes if isinstance(n, ModelNode)
    }
    for edge in spec.edges:
        for endpoint in (edge.src, edge.dst):
            if endpoint not in ids:
                violations.append(
                    Violation(
                        kind=ViolationKind.DANGLING_REFERENCE,
                        detail="edge references an undefined node",
                        nodes=[endpoint],
                    )
                )
        if edge.payload is not None and edge.src in model_of:
            producible = mentioned_types(model_of[edge.src].model)
            if edge.payload not in producible:
                violations.append(
                    Violation(
                        kind=ViolationKind.TYPE_FLOW,
                        detail=(
                            f"edge carries {edge.payload.__name__} but producer "
                            f"{model_of[edge.src].model.__name__} has no such type"
                        ),
                        nodes=[edge.src, edge.dst],
                    )
                )

    unreachable = find_unreachable(spec)
    if unreachable:
        violations.append(
            Violation(
                kind=ViolationKind.UNREACHABLE,
                detail="nodes not connected to any root — connect (with a cited edge) or cut",
                nodes=unreachable,
            )
        )
    return violations
