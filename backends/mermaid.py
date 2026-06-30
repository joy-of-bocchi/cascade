#!/usr/bin/env python3
"""The Mermaid render backend: `flowchart` and `classDiagram` emission, Mermaid
source parsing, and the `mmdc` CLI invocation for SVG rasterization.

A `DiagramSpec` renders to a Mermaid `flowchart`: model boxes, decision rhombi,
and terminal stadiums can all live together, optionally wrapped in `subgraph`
blocks for groups. ER roots render to a `classDiagram`. Parsing (`extract_graph`)
reads either dialect back into the neutral `structlint.Graph`, so `lint_text`
works against Mermaid output the same way it does for any backend.

Mermaid connects entity-to-entity rather than column-to-column, so field-level
edges degrade to entity edges with the field name carried in the relation label.
Decision rationale has no faithful flowchart home, so it is omitted from the
visual.
"""

from __future__ import annotations

import pathlib
import re

import structlint
from backends.base import DEFAULT_ER_DIRECTION, RenderBackend
from backends.d2 import ROLE_STYLE, _closure, _field_refs
from d2spec import (
    DecisionNode,
    DiagramSpec,
    ModelNode,
    NodeRole,
    TerminalNode,
    entity_fields,
    type_str,
)

# spec.direction -> Mermaid flowchart orientation token.
FLOWCHART_DIRECTION: dict[str, str] = {
    "down": "TD",
    "up": "BT",
    "right": "LR",
    "left": "RL",
}
DEFAULT_FLOWCHART_DIRECTION = "TD"

# ER direction -> Mermaid classDiagram orientation token.
CLASSDIAGRAM_DIRECTION: dict[str, str] = {
    "down": "TB",
    "up": "BT",
    "right": "LR",
    "left": "RL",
}
DEFAULT_CLASSDIAGRAM_DIRECTION = "LR"

COMPUTED_MARKER = "_computed"
DEFAULT_ATTR_TYPE = "any"

_ID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")
_ATTR_TYPE_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_~]")
_UNDERSCORE_COLLAPSE_RE = re.compile(r"_+")

# Mermaid edge-label segments sit between the arrow and the target as `|...|`.
_EDGE_LABEL_RE = re.compile(r"\|[^|]*\|")
# The three flowchart arrow forms this backend emits (plain, dashed, thick).
_ARROW_RE = re.compile(r"-\.->|-->|==>")
# A flowchart node declaration: an id followed by a shape opener.
_FLOW_NODE_DECL_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*[\[{(]")
_FLOW_BARE_NODE_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*$")
_SUBGRAPH_RE = re.compile(r"^\s*subgraph\s+([A-Za-z0-9_]+)")
_LEADING_ID_RE = re.compile(r"\s*([A-Za-z0-9_]+)")

# A classDiagram class header and relation line.
_CLASS_DECL_RE = re.compile(r"^\s*class\s+([A-Za-z0-9_]+)")
_CLASS_REL_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s*"  # source class
    r"[<|o*]*(?:--|\.\.)[|>o*]*\s*"  # relation connector with optional arrowheads
    r'(?:"[^"]*"\s*)?'  # optional cardinality label
    r"([A-Za-z0-9_]+)"  # target class
)

_FLOW_SKIP_PREFIXES: tuple[str, ...] = (
    "classDef",
    "class ",
    "click",
    "flowchart",
    "graph",
    "style ",
    "linkStyle",
    "direction",
    "%%",
)


def _nid(raw: str) -> str:
    """Map a spec node id to a Mermaid-safe identifier."""
    return _ID_SANITIZE_RE.sub("_", raw)


def _esc(text: str) -> str:
    """Escape dynamic text for a quoted Mermaid label (HTML-label renderer).
    Newlines become `<br/>` so multi-line labels render as line breaks rather
    than corrupting the source."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br/>")
    )


def _attr_type(rendered: str) -> str:
    """Flatten a type string into a single classDiagram-safe attribute token.

    Generic brackets become `~`, unions become `_or_`, and any remaining
    classDiagram-hostile character collapses to `_`."""
    token = rendered.replace("[", "~").replace("]", "~")
    token = token.replace(" | ", "_or_")
    token = token.replace(", ", "_")
    token = _ATTR_TYPE_SANITIZE_RE.sub("_", token)
    token = _UNDERSCORE_COLLAPSE_RE.sub("_", token).strip("_")
    return token or DEFAULT_ATTR_TYPE


def _model_label(node: ModelNode) -> str:
    """The flowchart box label: bold model name, then one field per line."""
    parts: list[str] = [f"<b>{_esc(node.model.__name__)}</b>"]
    for view in entity_fields(node.model):
        parts.append(f"{_esc(view.name)}: {_esc(type_str(view.annotation))}")
    return "<br/>".join(parts)


class MermaidBackend(RenderBackend):
    name = "mermaid"
    file_ext = ".mmd"
    supports_field_level_edges = False
    supports_mixed_model_and_decision = True

    def _declare(
        self, node: ModelNode | DecisionNode | TerminalNode
    ) -> tuple[str, str, NodeRole]:
        """Return the (sanitized id, declaration line, role) for one node."""
        nid = _nid(node.id)
        if isinstance(node, ModelNode):
            return nid, f'{nid}["{_model_label(node)}"]', node.role
        if isinstance(node, DecisionNode):
            return nid, f'{nid}{{"{_esc(node.question)}"}}', NodeRole.DECISION
        return nid, f'{nid}(["{_esc(node.label)}"])', NodeRole.TERMINAL

    def render_spec(self, spec: DiagramSpec) -> str:
        direction = FLOWCHART_DIRECTION.get(spec.direction, DEFAULT_FLOWCHART_DIRECTION)
        out: list[str] = [f"flowchart {direction}"]

        declared_groups = {group.id for group in spec.groups}
        members_by_group: dict[str | None, list[str]] = {}
        class_lines: list[str] = []
        used_roles: set[NodeRole] = set()

        for node in spec.nodes:
            nid, decl, role = self._declare(node)
            key = node.group if node.group in declared_groups else None
            members_by_group.setdefault(key, []).append(decl)
            class_lines.append(f"class {nid} {role};")
            used_roles.add(role)

        for group in spec.groups:
            gid = _nid(group.id)
            out.append(f'  subgraph {gid}["{_esc(group.label)}"]')
            for decl in members_by_group.get(group.id, []):
                out.append(f"    {decl}")
            out.append("  end")

        for decl in members_by_group.get(None, []):
            out.append(f"  {decl}")

        for edge in spec.edges:
            src = _nid(edge.src)
            dst = _nid(edge.dst)
            label = edge.label or (edge.payload.__name__ if edge.payload else "")
            arrow = "-.->" if edge.dashed else "-->"
            if label:
                out.append(f'  {src} {arrow}|"{_esc(label)}"| {dst}')
            else:
                out.append(f"  {src} {arrow} {dst}")

        for role in NodeRole:
            if role not in used_roles:
                continue
            style = ROLE_STYLE[role]
            out.append(
                f"  classDef {role} fill:{style['fill']},"
                f"stroke:{style['stroke']},color:{style['font-color']};"
            )
        out.extend(f"  {line}" for line in class_lines)

        return "\n".join(out) + "\n"

    def render_er(
        self, roots: list[type], direction: str = DEFAULT_ER_DIRECTION
    ) -> str:
        models = _closure(roots)
        in_scope = set(models)
        token = CLASSDIAGRAM_DIRECTION.get(direction, DEFAULT_CLASSDIAGRAM_DIRECTION)
        out: list[str] = ["classDiagram", f"  direction {token}", ""]

        for model in models:
            out.append(f"  class {model.__name__} {{")
            for view in entity_fields(model):
                attr = _attr_type(type_str(view.annotation))
                if view.computed:
                    attr += COMPUTED_MARKER
                out.append(f"    +{attr} {view.name}")
            out.append("  }")
        out.append("")

        for model in models:
            for name, ref, card in _field_refs(model):
                if ref in in_scope:
                    out.append(
                        f'  {model.__name__} --> "{card}" {ref.__name__} : {name}'
                    )

        return "\n".join(out) + "\n"

    def extract_graph(self, text: str) -> structlint.Graph:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("classDiagram"):
                return self._extract_classdiagram(text)
            if stripped.startswith(("flowchart", "graph")):
                return self._extract_flowchart(text)
            break
        return self._extract_flowchart(text)

    def _extract_flowchart(self, text: str) -> structlint.Graph:
        nodes: set[str] = set()
        containers: set[str] = set()
        edges: list[structlint.Edge] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped == "end":
                continue
            if stripped.startswith(_FLOW_SKIP_PREFIXES):
                continue
            subgraph_match = _SUBGRAPH_RE.match(line)
            if subgraph_match:
                containers.add(subgraph_match.group(1))
                continue
            if _ARROW_RE.search(stripped):
                without_labels = _EDGE_LABEL_RE.sub("", stripped)
                endpoints = [
                    match.group(1)
                    for token in _ARROW_RE.split(without_labels)
                    if (match := _LEADING_ID_RE.match(token))
                ]
                for src, dst in zip(endpoints, endpoints[1:]):
                    edges.append(structlint.Edge(src=src, dst=dst))
                continue
            decl_match = _FLOW_NODE_DECL_RE.match(line)
            if decl_match:
                nodes.add(decl_match.group(1))
                continue
            bare_match = _FLOW_BARE_NODE_RE.match(line)
            if bare_match:
                nodes.add(bare_match.group(1))
        return structlint.Graph(nodes=nodes, containers=containers, edges=edges)

    def _extract_classdiagram(self, text: str) -> structlint.Graph:
        nodes: set[str] = set()
        edges: list[structlint.Edge] = []
        brace_depth = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if brace_depth > 0:
                if "}" in stripped:
                    brace_depth -= 1
                continue
            class_match = _CLASS_DECL_RE.match(line)
            if class_match:
                nodes.add(class_match.group(1))
                if stripped.endswith("{"):
                    brace_depth += 1
                continue
            rel_match = _CLASS_REL_RE.match(line)
            if rel_match:
                edges.append(
                    structlint.Edge(src=rel_match.group(1), dst=rel_match.group(2))
                )
        return structlint.Graph(nodes=nodes, containers=set(), edges=edges)

    def svg_command(self, src: pathlib.Path, out: pathlib.Path) -> list[str]:
        return ["mmdc", "-i", str(src), "-o", str(out)]
