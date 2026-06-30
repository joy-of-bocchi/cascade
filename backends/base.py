#!/usr/bin/env python3
"""The renderer-backend seam.

A `RenderBackend` turns the neutral `DiagramSpec` (and a set of Pydantic model
roots) into rendered diagram source for one concrete syntax, parses that source
back into a neutral `structlint.Graph`, and reports how to rasterize it to SVG.
Capability booleans let callers ask what a backend supports before handing it a
spec the backend can't express.

The concrete `lint_text` helper is syntax-agnostic: it parses with the backend's
own `extract_graph`, runs the shared `structlint` checks, and assembles a
`LintReport`. Every backend lints its own output for free.
"""

from __future__ import annotations

import pathlib
from abc import ABC, abstractmethod

from pydantic import BaseModel

import structlint
from d2spec import DiagramSpec

DEFAULT_ER_DIRECTION = "right"


class RenderBackend(ABC):
    name: str
    file_ext: str
    supports_field_level_edges: bool
    supports_mixed_model_and_decision: bool

    @abstractmethod
    def render_spec(self, spec: DiagramSpec) -> str:
        """Render a typed diagram spec into this backend's source syntax."""

    @abstractmethod
    def render_er(
        self, roots: list[type[BaseModel]], direction: str = DEFAULT_ER_DIRECTION
    ) -> str:
        """Render an ER diagram from Pydantic model roots into this backend's source syntax."""

    @abstractmethod
    def extract_graph(self, text: str) -> structlint.Graph:
        """Parse rendered diagram source into the neutral structural graph."""

    @abstractmethod
    def svg_command(self, src: pathlib.Path, out: pathlib.Path) -> list[str]:
        """Argv that renders a source file to an SVG at `out`."""

    def lint_text(self, text: str) -> structlint.LintReport:
        """Parse rendered source with this backend, run the shared structural
        checks, and assemble a report."""
        graph = self.extract_graph(text)
        violations = structlint.lint_graph(graph)
        return structlint.LintReport(
            path="",
            node_count=len(graph.nodes),
            edge_count=len(graph.edges),
            violations=violations,
            topo_order=structlint.topological_order(graph),
        )
