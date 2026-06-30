#!/usr/bin/env python3
"""Generate a native-text .d2 diagram from a typed graph spec.

This module does generation only — spec in, `.d2` text out. It performs no
validation: linting is a separate concern, handled by `speclint` / `d2lint` /
`namelint`. Validate before generating if you want to, but the generator never
calls a linter and a linter never calls the generator; they only share `d2spec`.

    from d2gen import build_d2
    from d2spec import DiagramSpec, ModelNode, Edge, Group
"""
from __future__ import annotations

from d2spec import (
    DecisionNode,
    DiagramSpec,
    ModelNode,
    NodeRole,
    TerminalNode,
    field_default,
    type_str,
)

COL_GAP = 2
CODE_LANG = "txt"

ROLE_STYLE: dict[NodeRole, dict[str, str]] = {
    NodeRole.MODEL: {"fill": "#1f2733", "stroke": "#3c5a82", "font-color": "#cfe1f5"},
    NodeRole.MINTED: {"fill": "#1d4029", "stroke": "#3f8f57", "font-color": "#c9f5d4"},
    NodeRole.DECISION: {"fill": "#41360f", "stroke": "#a8842a", "font-color": "#f3e3ae"},
    NodeRole.TERMINAL: {"fill": "#3a2020", "stroke": "#8f4a3f", "font-color": "#f5cfc9"},
}


def _q(text: str) -> str:
    return text.replace("\\", "").replace('"', "'").replace("$", "\\$")


def model_table(node: ModelNode) -> list[str]:
    model = node.model
    frozen = " (frozen)" if model.model_config.get("frozen", False) else ""
    header = ["field", "type", "default", "note"]
    rows: list[list[str]] = [header]
    for name, info in model.model_fields.items():
        rows.append([name, type_str(info.annotation), field_default(info), info.description or ""])
    widths = [max(len(row[col]) for row in rows) for col in range(len(header))]

    def fmt(row: list[str]) -> str:
        cells = [
            row[col] if col == len(header) - 1 else row[col].ljust(widths[col] + COL_GAP)
            for col in range(len(header))
        ]
        return "".join(cells).rstrip()

    return [f"{model.__name__}{frozen}", *(fmt(row) for row in rows)]


def build_d2(spec: DiagramSpec) -> str:
    out: list[str] = [
        "# generated from typed model spec by d2gen.py",
        f"direction: {spec.direction}",
        "",
        "classes: {",
    ]
    used_roles = {n.role for n in spec.nodes if isinstance(n, ModelNode)} | {
        NodeRole.DECISION,
        NodeRole.TERMINAL,
    }
    for role in NodeRole:
        if role not in used_roles:
            continue
        style = ROLE_STYLE[role]
        head = "shape: diamond; " if role is NodeRole.DECISION else ""
        decls = "; ".join(f'{key}: "{value}"' for key, value in style.items())
        out.append(f"  {role}: {{ {head}style: {{ {decls} }} }}")
    out.append("}")
    out.append("")

    for group in spec.groups:
        out.append(f'{group.id}.label: "{_q(group.label)}"')
    if spec.groups:
        out.append("")

    def qual(node: ModelNode | DecisionNode | TerminalNode) -> str:
        return f"{node.group}.{node.id}" if node.group else node.id

    for node in spec.nodes:
        ref = qual(node)
        if isinstance(node, ModelNode):
            out.append(f"{ref}: |`{CODE_LANG}")
            out.extend(model_table(node))
            out.append("`|")
            out.append(f"{ref}.class: {node.role}")
        elif isinstance(node, DecisionNode):
            out.append(f'{ref}: "{_q(node.question)}" {{')
            out.append("  shape: diamond")
            out.append(f"  class: {NodeRole.DECISION}")
            if node.rationale:
                out.append(f'  tooltip: "{_q(node.rationale)}"')
            out.append("}")
        else:
            out.append(f'{ref}: "{_q(node.label)}"')
            out.append(f"{ref}.class: {NodeRole.TERMINAL}")
    out.append("")

    by_id = {node.id: node for node in spec.nodes}
    for edge in spec.edges:
        src = qual(by_id[edge.src]) if edge.src in by_id else edge.src
        dst = qual(by_id[edge.dst]) if edge.dst in by_id else edge.dst
        label = edge.label or (edge.payload.__name__ if edge.payload else "")
        line = f"{src} -> {dst}"
        if label:
            line += f': "{_q(label)}"'
        if edge.dashed:
            line += (" " if label else ": ") + "{ style.stroke-dash: 4 }"
        out.append(line)

    return "\n".join(out) + "\n"
