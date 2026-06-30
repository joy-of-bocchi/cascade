#!/usr/bin/env python3
"""Structural linter for a .d2 file: cycle / dangling-edge / isolated-node checks.

D2 lays a graph out but never validates it — it will happily draw a cycle, an
edge to an undefined node, or a node nothing connects to. This extracts the
directed graph from the .d2 source and runs the checks D2 omits, so a diagram
meant to be a DAG can be proven to be one.

Run: uv run --with pydantic python d2lint.py path/to/diagram.d2
Exit code is non-zero when any blocking violation is found.
"""
from __future__ import annotations

import argparse
import re
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

# A node id is a dotted container path, e.g. S0.Q0 or S6.S7.SVIEW.
NODE_PATH = r"[A-Za-z0-9_][A-Za-z0-9_.]*"
EDGE_RE = re.compile(rf"^({NODE_PATH})\s*->\s*({NODE_PATH})")
# A node is defined by `<path>: |...` (code block) or `<path>: "..."` (label/diamond).
NODE_DEF_RE = re.compile(rf'^({NODE_PATH}):\s*(?:\||")')
BLOCK_OPEN_RE = re.compile(r"\|`[A-Za-z]+\s*$")
BLOCK_CLOSE_RE = re.compile(r"^\s*`\|\s*$")
CONTAINER_SUFFIX = ".label"


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


def extract_graph(text: str) -> Graph:
    """Parse the directed graph, skipping code-block interiors so content arrows
    and labels are never mistaken for edges or node definitions."""
    nodes: set[str] = set()
    containers: set[str] = set()
    edges: list[Edge] = []
    in_block = False
    for line in text.splitlines():
        if in_block:
            if BLOCK_CLOSE_RE.match(line):
                in_block = False
            continue
        edge_match = EDGE_RE.match(line)
        if edge_match:
            edges.append(Edge(src=edge_match.group(1), dst=edge_match.group(2)))
            continue
        def_match = NODE_DEF_RE.match(line)
        if def_match:
            path = def_match.group(1)
            if path.endswith(CONTAINER_SUFFIX):
                containers.add(path[: -len(CONTAINER_SUFFIX)])
            else:
                nodes.add(path)
        if BLOCK_OPEN_RE.search(line):
            in_block = True
    return Graph(nodes=nodes, containers=containers, edges=edges)


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


def lint(path: Path) -> LintReport:
    graph = extract_graph(path.read_text())
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

    return LintReport(
        path=str(path),
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        violations=violations,
        topo_order=topological_order(graph),
    )


def render(report: LintReport) -> str:
    lines: list[str] = [
        f"d2lint: {report.path}",
        f"  nodes: {report.node_count}   edges: {report.edge_count}",
    ]
    if not report.violations:
        lines.append("  PASS - acyclic, no dangling edges, no isolated nodes")
    else:
        for violation in report.violations:
            marker = "WARN" if violation.kind == ViolationKind.ISOLATED_NODE else "FAIL"
            preview = ", ".join(violation.nodes[:8]) + ("..." if len(violation.nodes) > 8 else "")
            lines.append(f"  [{marker}] {violation.kind}: {violation.detail}")
            lines.append(f"         {preview}")
    lines.append(f"  DAG: {'yes' if report.topo_order is not None else 'NO (cyclic)'}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural linter for .d2 graphs.")
    parser.add_argument("path", type=Path, help="path to a .d2 file")
    args = parser.parse_args()
    report = lint(args.path)
    print(render(report))
    return 1 if report.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
