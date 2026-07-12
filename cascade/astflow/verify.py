#!/usr/bin/env python3
"""Check a `DiagramSpec` against the `FactIR` that produced it.

The contract: every solid edge must trace to a supporting fact. A solid build-in
edge (part -> whole) must correspond to a constructor argument that resolves to
the part; a solid consumed edge (entity -> consumers) must correspond to a read
of a field whose type is that entity. Dashed edges (derived-after, dormancy,
control-flow gates) are exempt from the strict check. Any solid edge with no
backing fact is a violation.
"""

from __future__ import annotations

from ..spec.d2spec import DiagramSpec, Edge
from .ir import FactIR
from .to_spec import (
    CONSUMERS_ID,
    entity_names_in_typestr,
    entity_types_in_annotation,
    field_annotation,
)


def _build_in_supported(
    edge: Edge,
    ir: FactIR,
    id_to_name: dict[str, str],
) -> bool:
    """True when a solid part -> whole edge matches a constructor argument that
    resolves the part into the whole under the edge's field label."""
    whole: str | None = id_to_name.get(edge.dst)
    part: str | None = id_to_name.get(edge.src)
    if whole is None or part is None:
        return False
    entity_names: set[str] = set(ir.entities)
    for construction in ir.entities[whole].constructed_at:
        for arg in construction.args:
            if arg.field != edge.label:
                continue
            if part in entity_names_in_typestr(arg.resolves_to, entity_names):
                return True
    return False


def _consumed_supported(
    edge: Edge,
    ir: FactIR,
    entity_classes: dict[str, type],
    id_to_name: dict[str, str],
) -> bool:
    """True when a solid entity -> consumers edge matches a read of a field whose
    type resolves to that entity, under the edge's `read: .<attr>` label."""
    consumed: str | None = id_to_name.get(edge.src)
    if consumed is None:
        return False
    for name, facts in ir.entities.items():
        cls: type | None = entity_classes.get(name)
        if cls is None:
            continue
        for read in facts.fields_read:
            if edge.label != f"read: .{read.attr}":
                continue
            annotation = field_annotation(cls, read.attr)
            if annotation is None:
                continue
            if any(
                t.__name__ == consumed for t in entity_types_in_annotation(annotation)
            ):
                return True
    return False


def verify(spec: DiagramSpec, ir: FactIR, entity_classes: dict[str, type]) -> list[str]:
    """Return human-readable violation strings for every solid edge with no
    backing fact in the IR. Returns an empty list when the spec is clean."""
    id_to_name: dict[str, str] = {name.lower(): name for name in ir.entities}
    violations: list[str] = []
    for edge in spec.edges:
        if edge.dashed:
            continue
        if edge.dst == CONSUMERS_ID:
            if not _consumed_supported(edge, ir, entity_classes, id_to_name):
                violations.append(
                    f"solid consumed edge {edge.src} -> {edge.dst} "
                    f"(label {edge.label!r}) has no backing read fact"
                )
            continue
        if not _build_in_supported(edge, ir, id_to_name):
            violations.append(
                f"solid build-in edge {edge.src} -> {edge.dst} "
                f"(label {edge.label!r}) has no backing construction fact"
            )
    return violations
