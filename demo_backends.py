#!/usr/bin/env python3
"""Render the same neutral inputs through both backends, side by side.

One `DiagramSpec` (model + decision + terminal nodes, a group, a few edges) and
one set of Pydantic roots are rendered as Mermaid and as D2 through the neutral
`render` / `render_er` surface, and each rendering is structurally linted. The
point is to show that a single typed source produces either syntax and that the
shared `structlint` checks run against whichever backend emitted the text.

Run:
    PYTHONPATH=<repo> uv run --with pydantic python3 demo_backends.py
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from backends import D2Backend, MermaidBackend, RenderBackend
from d2spec import DecisionNode, DiagramSpec, Edge, Group, ModelNode, TerminalNode
from render import get_backend, lint, render, render_er
from structlint import LintReport, ViolationKind

RULE_WIDTH = 70


class Address(BaseModel):
    model_config = ConfigDict(frozen=True)
    street: str
    city: str
    postal_code: str


class Customer(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    address: Address


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    customer: Customer
    total: float
    lines: list[str]


def build_spec() -> DiagramSpec:
    """A small decision DAG: ingest an order, branch on its total, end in one
    of two terminals. The model node carries a real Pydantic class, so its table
    is introspected rather than hand-written."""
    return DiagramSpec(
        nodes=[
            ModelNode(id="ingest", model=Order, group="flow"),
            DecisionNode(
                id="gate",
                question="Total over the review limit?",
                rationale="Large orders route to a human before fulfilment.",
                group="flow",
            ),
            TerminalNode(id="approve", label="Auto-approve"),
            TerminalNode(id="review", label="Hold for review"),
        ],
        edges=[
            Edge(src="ingest", dst="gate"),
            Edge(src="gate", dst="approve", label="no"),
            Edge(src="gate", dst="review", label="yes", dashed=True),
        ],
        groups=[Group(id="flow", label="Order intake")],
    )


def format_report(report: LintReport) -> str:
    """One-line-per-fact summary of a structural lint report."""
    lines: list[str] = [
        f"nodes={report.node_count}  edges={report.edge_count}  "
        f"dag={'yes' if report.topo_order is not None else 'NO (cyclic)'}"
    ]
    if not report.violations:
        lines.append("no violations (acyclic, no dangling edges, no isolated nodes)")
    else:
        for violation in report.violations:
            marker = "WARN" if violation.kind == ViolationKind.ISOLATED_NODE else "FAIL"
            lines.append(f"[{marker}] {violation.kind}: {', '.join(violation.nodes)}")
    return "\n".join(lines)


def banner(text: str) -> None:
    print("\n" + "=" * RULE_WIDTH)
    print(text)
    print("=" * RULE_WIDTH)


def show_backend(
    backend: RenderBackend, spec: DiagramSpec, roots: list[type[BaseModel]]
) -> None:
    banner(f"{backend.name.upper()} backend ({backend.file_ext})")

    print("\n-- spec render --\n")
    spec_text = render(spec, backend)
    print(spec_text)
    print("-- spec lint --")
    print(format_report(lint(spec_text, backend)))

    print("\n-- ER render --\n")
    er_text = render_er(roots, backend)
    print(er_text)
    print("-- ER lint --")
    print(format_report(lint(er_text, backend)))


def main() -> int:
    spec = build_spec()
    roots: list[type[BaseModel]] = [Order]
    for backend in (MermaidBackend(), get_backend("d2")):
        show_backend(backend, spec, roots)
    # Confirm the explicit-D2 constructor and the registry lookup agree.
    if render(spec, D2Backend()) != render(spec, get_backend("d2")):
        raise RuntimeError("D2Backend() and get_backend('d2') disagree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
