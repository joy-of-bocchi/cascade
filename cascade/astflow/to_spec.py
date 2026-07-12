#!/usr/bin/env python3
"""Turn a deterministic dataflow `FactIR` into a renderable `DiagramSpec`.

The extractor produces the facts (constructions, reads, post-construction
mutations, dormancy, control-flow gates); this module maps each fact to the
diagram edges it justifies. Every edge traces to a fact, so the verifier can
check the diagram against the IR that produced it.

Entities render as `sql_table` model nodes when the caller supplies their live
Pydantic class; decisions render as diamonds; consumers and dormancy render as
terminal markers.
"""

from __future__ import annotations

import re
from typing import Any, get_args, get_origin

from ..render import render
from ..render.backends.d2 import D2Backend
from ..spec.d2spec import (
    DecisionNode,
    DiagramSpec,
    Edge,
    ModelNode,
    TerminalNode,
    entity_fields,
    is_entity,
)
from .fixtures import sample
from .ir import (
    ArgResolution,
    ConstructionFact,
    DecisionFact,
    EntityFacts,
    FactIR,
    MutationFact,
    ReadFact,
    Site,
)

DIRECTION_DOWN = "down"
CONSUMERS_ID = "consumers"
CONSUMERS_LABEL = "report / consumers"
VOID_SUFFIX = "_void"
DORMANT_LABEL = "dormant — never built"
GATE_LABEL = "builds"

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def node_id(entity_name: str) -> str:
    """Diagram node id for an entity: the lowercased entity name."""
    return entity_name.lower()


def entity_names_in_typestr(typestr: str | None, entity_names: set[str]) -> list[str]:
    """Entity names named anywhere inside a resolved-type string.

    Unwraps container and union wrappers (`list[Mid]`, `Mid | None`,
    `dict[str, Mid]`) by pulling every identifier token and keeping the ones
    that name a known entity."""
    if not typestr:
        return []
    return [tok for tok in _IDENT_RE.findall(typestr) if tok in entity_names]


def entity_types_in_annotation(annotation: Any) -> list[type]:
    """Introspectable entity types reachable inside a type annotation, unwrapping
    `list[X]`, `X | None`, and `dict[K, V]` down to the bare entity types."""
    found: list[type] = []

    def walk(node: Any) -> None:
        origin = get_origin(node)
        if origin is None:
            if isinstance(node, type) and is_entity(node):
                found.append(node)
            return
        for arg in get_args(node):
            walk(arg)

    walk(annotation)
    return found


def field_annotation(entity_cls: type, field: str) -> Any | None:
    """Resolved type annotation of one field on an entity, or None if the entity
    has no such field."""
    for view in entity_fields(entity_cls):
        if view.name == field:
            return view.annotation
    return None


def build_spec(ir: FactIR, entity_classes: dict[str, type]) -> DiagramSpec:
    """Build a `DiagramSpec` from a dataflow `FactIR`.

    `entity_classes` maps each entity name to its live Pydantic class, used to
    render model nodes as sql_tables and to resolve field names to their entity
    types. Every solid edge traces to a construction or read fact; dashed edges
    trace to a mutation, dormancy, or control-flow decision.
    """
    entity_names: set[str] = set(ir.entities)
    node_ids: dict[str, str] = {name: node_id(name) for name in ir.entities}

    nodes: list[ModelNode | DecisionNode | TerminalNode] = []
    for name in ir.entities:
        cls: type | None = entity_classes.get(name)
        if cls is not None:
            nodes.append(ModelNode(id=node_ids[name], model=cls))
        else:
            nodes.append(TerminalNode(id=node_ids[name], label=name))

    edges: list[Edge] = []
    extra_terminals: dict[str, TerminalNode] = {}

    # BUILD_IN: a constructor arg that names an entity flows the part into the whole.
    for name, facts in ir.entities.items():
        for construction in facts.constructed_at:
            for arg in construction.args:
                for part in entity_names_in_typestr(arg.resolves_to, entity_names):
                    edges.append(
                        Edge(
                            src=node_ids[part],
                            dst=node_ids[name],
                            label=arg.field,
                            dashed=False,
                        )
                    )

    # DERIVED_AFTER: a post-construction mutation fills a field that is itself an entity.
    for name, facts in ir.entities.items():
        cls = entity_classes.get(name)
        if cls is None:
            continue
        for mutation in facts.mutated_after:
            for target in mutation.targets:
                field: str = target.split(".")[0]
                annotation: Any | None = field_annotation(cls, field)
                if annotation is None:
                    continue
                for filled in entity_types_in_annotation(annotation):
                    if filled.__name__ not in ir.entities:
                        continue
                    edges.append(
                        Edge(
                            src=node_ids[name],
                            dst=node_ids[filled.__name__],
                            label=f"{mutation.via}() fills after build",
                            dashed=True,
                        )
                    )

    # CONSUMED: a read of a field that is an entity flows that entity out to consumers.
    for name, facts in ir.entities.items():
        cls = entity_classes.get(name)
        if cls is None:
            continue
        for read in facts.fields_read:
            annotation = field_annotation(cls, read.attr)
            if annotation is None:
                continue
            for consumed in entity_types_in_annotation(annotation):
                if consumed.__name__ not in ir.entities:
                    continue
                edges.append(
                    Edge(
                        src=node_ids[consumed.__name__],
                        dst=CONSUMERS_ID,
                        label=f"read: .{read.attr}",
                        dashed=False,
                    )
                )
                extra_terminals[CONSUMERS_ID] = TerminalNode(
                    id=CONSUMERS_ID, label=CONSUMERS_LABEL
                )

    # DORMANT: an entity nothing ever constructs points at a "never built" marker.
    for name, facts in ir.entities.items():
        if not facts.is_dormant:
            continue
        void_id: str = f"{node_ids[name]}{VOID_SUFFIX}"
        extra_terminals[void_id] = TerminalNode(id=void_id, label=DORMANT_LABEL)
        edges.append(Edge(src=node_ids[name], dst=void_id, label="", dashed=True))

    # GATED_BY: a control-flow branch guards construction of the entities it gates.
    for index, decision in enumerate(ir.decisions):
        decision_id: str = f"dec_{index}"
        nodes.append(DecisionNode(id=decision_id, question=decision.condition))
        for gated in decision.gates:
            if gated not in node_ids:
                continue
            edges.append(
                Edge(
                    src=decision_id,
                    dst=node_ids[gated],
                    label=GATE_LABEL,
                    dashed=True,
                )
            )

    nodes.extend(extra_terminals.values())

    seen: set[tuple[str, str, str, bool]] = set()
    unique_edges: list[Edge] = []
    for edge in edges:
        key: tuple[str, str, str, bool] = (edge.src, edge.dst, edge.label, edge.dashed)
        if key in seen:
            continue
        seen.add(key)
        unique_edges.append(edge)

    return DiagramSpec(nodes=nodes, edges=unique_edges, direction=DIRECTION_DOWN)


if __name__ == "__main__":
    src = Site(file="fixtures/sample.py", line=48, function="build")

    ir = FactIR(
        entities={
            "Leaf": EntityFacts(
                name="Leaf",
                constructed_at=[
                    ConstructionFact(
                        site=Site(file="fixtures/sample.py", line=52, function="build"),
                        args=[
                            ArgResolution(field="a", expr="row['a']"),
                            ArgResolution(field="b", expr="row['b']"),
                        ],
                    )
                ],
            ),
            "Mid": EntityFacts(
                name="Mid",
                constructed_at=[
                    ConstructionFact(
                        site=Site(file="fixtures/sample.py", line=53, function="build"),
                        args=[
                            ArgResolution(field="key", expr="leaf", resolves_to="Leaf"),
                            ArgResolution(field="weight", expr="row['w']"),
                        ],
                    )
                ],
            ),
            "Summary": EntityFacts(
                name="Summary",
                constructed_at=[
                    ConstructionFact(
                        site=Site(file="fixtures/sample.py", line=41, function="Whole"),
                        args=[],
                    )
                ],
            ),
            "Whole": EntityFacts(
                name="Whole",
                constructed_at=[
                    ConstructionFact(
                        site=Site(file="fixtures/sample.py", line=54, function="build"),
                        args=[
                            ArgResolution(
                                field="items", expr="items", resolves_to="list[Mid]"
                            )
                        ],
                    )
                ],
                mutated_after=[
                    MutationFact(
                        site=Site(
                            file="fixtures/sample.py", line=45, function="compute"
                        ),
                        via="compute",
                        targets=["summary.total"],
                    )
                ],
                fields_read=[
                    ReadFact(
                        site=Site(
                            file="fixtures/sample.py", line=60, function="consume"
                        ),
                        attr="summary",
                    )
                ],
            ),
            "Dormant": EntityFacts(name="Dormant"),
        },
        decisions=[
            DecisionFact(
                id="d0",
                condition="flag",
                site=Site(file="fixtures/sample.py", line=50, function="build"),
                gates=["Leaf", "Mid"],
            )
        ],
    )

    entity_classes: dict[str, type] = {
        "Leaf": sample.Leaf,
        "Mid": sample.Mid,
        "Summary": sample.Summary,
        "Whole": sample.Whole,
        "Dormant": sample.Dormant,
    }

    spec = build_spec(ir, entity_classes)

    print("=== Mermaid ===")
    print(render(spec))
    print("=== D2 ===")
    print(render(spec, backend=D2Backend()))

    edge_set: set[tuple[str, str]] = {(edge.src, edge.dst) for edge in spec.edges}
    required_edges: dict[tuple[str, str], str] = {
        ("leaf", "mid"): "missing build-in leaf -> mid",
        ("mid", "whole"): "missing build-in mid -> whole",
        ("whole", "summary"): "missing derived whole -> summary",
        ("summary", CONSUMERS_ID): "missing read summary -> consumers",
    }
    for edge, message in required_edges.items():
        if edge not in edge_set:
            raise RuntimeError(message)

    has_dormant_marker: bool = any(
        edge.dst == f"dormant{VOID_SUFFIX}" for edge in spec.edges
    )
    if not has_dormant_marker:
        raise RuntimeError("missing dormant marker")

    has_decision_gate: bool = any(
        edge.src == "dec_0" and edge.dst in ("leaf", "mid") for edge in spec.edges
    )
    if not has_decision_gate:
        raise RuntimeError("missing decision gate")
    print("self-test OK")
