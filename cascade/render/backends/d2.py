#!/usr/bin/env python3
"""The D2 render backend: native-text `.d2` emission, `.d2` parsing, and the
`d2` CLI invocation for SVG rasterization.

Emission is generation only — spec in, `.d2` text out, no validation. Parsing
(`extract_graph`) reads `.d2` source back into the neutral `structlint.Graph`,
skipping code-block interiors so content arrows and labels are never mistaken
for edges or node definitions.
"""

from __future__ import annotations

import pathlib
import re
from types import UnionType
from typing import Any, Union, get_args, get_origin

from ...lint import structlint
from ...spec.d2spec import (
    NONE_TYPE,
    DecisionNode,
    DiagramSpec,
    ModelNode,
    ModuleNode,
    NodeRole,
    TerminalNode,
    entity_fields,
    is_entity,
    is_frozen,
    type_str,
)
from .base import DEFAULT_ER_DIRECTION, RenderBackend

COL_GAP = 2
CODE_LANG = "txt"

ROLE_STYLE: dict[NodeRole, dict[str, str]] = {
    NodeRole.MODEL: {"fill": "#1f2733", "stroke": "#3c5a82", "font-color": "#cfe1f5"},
    NodeRole.MINTED: {"fill": "#1d4029", "stroke": "#3f8f57", "font-color": "#c9f5d4"},
    NodeRole.DECISION: {
        "fill": "#41360f",
        "stroke": "#a8842a",
        "font-color": "#f3e3ae",
    },
    NodeRole.TERMINAL: {
        "fill": "#3a2020",
        "stroke": "#8f4a3f",
        "font-color": "#f5cfc9",
    },
    NodeRole.MODULE: {
        "fill": "#2b2f37",
        "stroke": "#555c66",
        "font-color": "#aab3bf",
    },
}

COLLECTION_ORIGINS = (list, set, frozenset, tuple)

# A node id is a dotted container path, e.g. S0.Q0 or S6.S7.SVIEW.
NODE_PATH = r"[A-Za-z0-9_][A-Za-z0-9_.]*"
EDGE_RE = re.compile(rf"^({NODE_PATH})\s*->\s*({NODE_PATH})")
# A node is defined by `<path>: |...` (code block) or `<path>: "..."` (label/diamond).
NODE_DEF_RE = re.compile(rf'^({NODE_PATH}):\s*(?:\||")')
BLOCK_OPEN_RE = re.compile(r"\|`[A-Za-z]+\s*$")
BLOCK_CLOSE_RE = re.compile(r"^\s*`\|\s*$")
CONTAINER_SUFFIX = ".label"

D2_LAYOUT_ENGINE = "elk"


def _q(text: str) -> str:
    """Quote-escape dynamic text for a D2 double-quoted string. A literal
    newline would terminate the string, so it is emitted as the `\\n` escape,
    which D2 renders as a line break."""
    return (
        text.replace("\\", "")
        .replace('"', "'")
        .replace("$", "\\$")
        .replace("\n", "\\n")
    )


def model_table_rows(node: ModelNode) -> list[str]:
    """The aligned field/type/default/note rows for a payload card. Authored
    `notes` take the note column; a field's schema description is the fallback."""
    header = ["field", "type", "default", "note"]
    rows: list[list[str]] = [header]
    for view in entity_fields(node.model):
        rows.append(
            [
                view.name,
                type_str(view.annotation),
                view.default,
                node.notes.get(view.name, view.description),
            ]
        )
    widths = [max(len(row[col]) for row in rows) for col in range(len(header))]

    def fmt(row: list[str]) -> str:
        cells = [
            row[col]
            if col == len(header) - 1
            else row[col].ljust(widths[col] + COL_GAP)
            for col in range(len(header))
        ]
        return "".join(cells).rstrip()

    return [fmt(row) for row in rows]


def model_title(node: ModelNode) -> str:
    frozen = " (frozen)" if is_frozen(node.model) else ""
    return f"{node.model.__name__}{frozen}"


def model_table(node: ModelNode) -> list[str]:
    return [model_title(node), *model_table_rows(node)]


def _referenced(annotation: Any) -> list[tuple[type, str]]:
    """Every (entity, cardinality) a type annotation points at."""
    found: list[tuple[type, str]] = []

    def walk(node: Any, card: str) -> None:
        origin = get_origin(node)
        if origin is None:
            if is_entity(node):
                found.append((node, card))
            return
        args = get_args(node)
        if origin in COLLECTION_ORIGINS:
            for arg in args:
                walk(arg, "*")
        elif origin is dict:
            for arg in args[1:]:
                walk(arg, "*")
        elif origin is Union or origin is UnionType:
            optional = NONE_TYPE in args
            for arg in args:
                if arg is NONE_TYPE:
                    continue
                walk(arg, "0..1" if optional and card == "1" else card)
        else:
            for arg in args:
                walk(arg, card)

    walk(annotation, "1")
    return found


def _field_refs(model: type) -> list[tuple[str, type, str]]:
    refs: list[tuple[str, type, str]] = []
    for view in entity_fields(model):
        for ref, card in _referenced(view.annotation):
            refs.append((view.name, ref, card))
    return refs


def _closure(roots: list[type]) -> list[type]:
    ordered: dict[type, None] = {}
    stack = list(roots)
    while stack:
        model = stack.pop()
        if model in ordered:
            continue
        ordered[model] = None
        for _, ref, _ in _field_refs(model):
            if ref not in ordered:
                stack.append(ref)
    return list(ordered)


class D2Backend(RenderBackend):
    name = "d2"
    file_ext = ".d2"
    supports_field_level_edges = True
    supports_mixed_model_and_decision = True

    def render_spec(self, spec: DiagramSpec) -> str:
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
        if any(isinstance(n, ModuleNode) for n in spec.nodes):
            used_roles.add(NodeRole.MODULE)
        for role in NodeRole:
            if role not in used_roles:
                continue
            style = ROLE_STYLE[role]
            decls = "; ".join(f'{key}: "{value}"' for key, value in style.items())
            out.append(f"  {role}: {{ style: {{ {decls} }} }}")
        out.append("}")
        out.append("")

        for group in spec.groups:
            label = (
                f"STAGE: {group.label} — {group.cadence}"
                if group.cadence
                else group.label
            )
            out.append(f'{group.id}.label: "{_q(label)}"')
        if spec.groups:
            out.append("")

        def qual(node: ModelNode | DecisionNode | TerminalNode | ModuleNode) -> str:
            return f"{node.group}.{node.id}" if node.group else node.id

        for node in spec.nodes:
            ref = qual(node)
            if isinstance(node, ModelNode):
                if node.prose:
                    # Payload card: container label carries the name; the role
                    # sentence and the field table are native-text children.
                    out.append(f'{ref}: "{_q(model_title(node))}" {{')
                    out.append(f"  class: {node.role}")
                    out.append(f'  role: "{_q(node.prose)}"')
                    out.append(f"  fields: |`{CODE_LANG}")
                    out.extend(f"  {row}" for row in model_table_rows(node))
                    out.append("  `|")
                    out.append("}")
                else:
                    out.append(f"{ref}: |`{CODE_LANG}")
                    out.extend(model_table(node))
                    out.append("`|")
                    out.append(f"{ref}.class: {node.role}")
            elif isinstance(node, DecisionNode):
                if node.rationale:
                    # Inline rationale: a rect holds multi-line text; a diamond
                    # would stretch it into an unreadable sliver.
                    text = f"DECIDES: {node.question}\nWHY: {node.rationale}"
                    out.append(f'{ref}: "{_q(text)}"')
                    out.append(f"{ref}.class: {NodeRole.DECISION}")
                else:
                    out.append(f'{ref}: "{_q(node.question)}" {{')
                    out.append("  shape: diamond")
                    out.append(f"  class: {NodeRole.DECISION}")
                    out.append("}")
            elif isinstance(node, ModuleNode):
                lines = [node.label]
                if node.prose:
                    lines.append(node.prose)
                if node.products:
                    lines.append("gives: " + " · ".join(node.products))
                out.append(f'{ref}: "{_q(chr(10).join(lines))}"')
                out.append(f"{ref}.class: {NodeRole.MODULE}")
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

    def render_er(
        self, roots: list[type], direction: str = DEFAULT_ER_DIRECTION
    ) -> str:
        models = _closure(roots)
        in_scope = set(models)
        out: list[str] = [
            "# ER diagram auto-generated from typed models by d2er.py",
            f"direction: {direction}",
            "",
        ]

        for model in models:
            out.append(f"{model.__name__}: {{")
            out.append("  shape: sql_table")
            for view in entity_fields(model):
                suffix = " (computed)" if view.computed else ""
                out.append(f'  "{view.name}": "{type_str(view.annotation)}{suffix}"')
            out.append("}")
        out.append("")

        for model in models:
            for name, ref, card in _field_refs(model):
                if ref in in_scope:
                    out.append(f'{model.__name__}."{name}" -> {ref.__name__}: "{card}"')

        return "\n".join(out) + "\n"

    def extract_graph(self, text: str) -> structlint.Graph:
        """Parse the directed graph, skipping code-block interiors so content
        arrows and labels are never mistaken for edges or node definitions."""
        nodes: set[str] = set()
        containers: set[str] = set()
        edges: list[structlint.Edge] = []
        in_block = False
        for line in text.splitlines():
            if in_block:
                if BLOCK_CLOSE_RE.match(line):
                    in_block = False
                continue
            edge_match = EDGE_RE.match(line)
            if edge_match:
                edges.append(
                    structlint.Edge(src=edge_match.group(1), dst=edge_match.group(2))
                )
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
        return structlint.Graph(nodes=nodes, containers=containers, edges=edges)

    def svg_command(self, src: pathlib.Path, out: pathlib.Path) -> list[str]:
        return ["d2", "--layout", D2_LAYOUT_ENGINE, str(src), str(out)]
