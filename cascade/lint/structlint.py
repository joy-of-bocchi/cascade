#!/usr/bin/env python3
"""Renderer-agnostic structural graph linting: cycle / dangling-edge /
isolated-node checks over an already-parsed directed graph.

This module knows nothing about D2, Mermaid, or any diagram syntax. It operates
only on a neutral `Graph` (a set of node ids, a set of container ids, and a list
of directed `Edge`s). Backends parse their own rendered source into a `Graph`
and call `lint_graph` to run the checks every renderer shares.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ViolationKind(StrEnum):
    CYCLE = "cycle"
    DANGLING_EDGE = "dangling_edge"
    ISOLATED_NODE = "isolated_node"


class Edge(BaseModel):
    src: str
    dst: str


class Graph(BaseModel):
    nodes: set[str]
    containers: set[str]
    edges: list[Edge]


class Violation(BaseModel):
    kind: ViolationKind
    detail: str
    nodes: list[str]


class LintReport(BaseModel):
    path: str
    node_count: int
    edge_count: int
    violations: list[Violation]
    topo_order: list[str] | None

    @property
    def blocking(self) -> list[Violation]:
        return [v for v in self.violations if v.kind != ViolationKind.ISOLATED_NODE]


def find_cycles(graph: Graph) -> list[list[str]]:
    """Tarjan's SCC: any component with more than one node, or a self-loop, is a
    cycle. Returns the member list of each cyclic component."""
    adjacency: dict[str, list[str]] = {node: [] for node in graph.nodes}
    self_loops: set[str] = set()
    for edge in graph.edges:
        adjacency.setdefault(edge.src, [])
        adjacency.setdefault(edge.dst, [])
        if edge.src == edge.dst:
            self_loops.add(edge.src)
        else:
            adjacency[edge.src].append(edge.dst)

    index_of: dict[str, int] = {}
    low_link: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    cycles: list[list[str]] = []

    def strong_connect(start: str) -> None:
        nonlocal counter
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, child_index = work[-1]
            if child_index == 0:
                index_of[node] = low_link[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recursed = False
            neighbours = adjacency[node]
            for next_index in range(child_index, len(neighbours)):
                neighbour = neighbours[next_index]
                if neighbour not in index_of:
                    work[-1] = (node, next_index + 1)
                    work.append((neighbour, 0))
                    recursed = True
                    break
                if neighbour in on_stack:
                    low_link[node] = min(low_link[node], index_of[neighbour])
            if recursed:
                continue
            if low_link[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or component[0] in self_loops:
                    cycles.append(sorted(component))
            work.pop()
            if work:
                parent = work[-1][0]
                low_link[parent] = min(low_link[parent], low_link[node])

    for node in adjacency:
        if node not in index_of:
            strong_connect(node)
    return cycles


def topological_order(graph: Graph) -> list[str] | None:
    """Kahn's algorithm. Returns None when the graph is cyclic."""
    indegree: dict[str, int] = {node: 0 for node in graph.nodes}
    adjacency: dict[str, list[str]] = {node: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.src not in indegree or edge.dst not in indegree or edge.src == edge.dst:
            continue
        adjacency[edge.src].append(edge.dst)
        indegree[edge.dst] += 1
    ready: list[str] = sorted(node for node, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for neighbour in adjacency[node]:
            indegree[neighbour] -= 1
            if indegree[neighbour] == 0:
                ready.append(neighbour)
                ready.sort()
    return order if len(order) == len(graph.nodes) else None


def lint_graph(graph: Graph) -> list[Violation]:
    """Run the cycle / dangling-edge / isolated-node checks on a parsed graph."""
    violations: list[Violation] = []

    for component in find_cycles(graph):
        violations.append(
            Violation(
                kind=ViolationKind.CYCLE,
                detail=f"{len(component)} nodes form a cycle (not a DAG)",
                nodes=component,
            )
        )

    endpoints = {edge.src for edge in graph.edges} | {edge.dst for edge in graph.edges}
    for missing in sorted(endpoints - graph.nodes - graph.containers):
        violations.append(
            Violation(
                kind=ViolationKind.DANGLING_EDGE,
                detail="edge references an undefined node",
                nodes=[missing],
            )
        )

    connected = endpoints
    for isolated in sorted(graph.nodes - connected):
        violations.append(
            Violation(
                kind=ViolationKind.ISOLATED_NODE,
                detail="node has no edges",
                nodes=[isolated],
            )
        )

    return violations
