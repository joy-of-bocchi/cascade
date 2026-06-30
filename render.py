#!/usr/bin/env python3
"""Neutral rendering entry surface for the Cascade diagramming toolkit.

The default backend is Mermaid. D2 is opt-in: pass `backend=D2Backend()` or
`backend=get_backend("d2")` to any function here, or call the backend directly.
Each function takes the same neutral inputs (a `DiagramSpec`, or Pydantic model
roots) and dispatches to the chosen backend so callers never hardcode a syntax.
"""

from __future__ import annotations

from pydantic import BaseModel

import structlint
from backends.base import DEFAULT_ER_DIRECTION, RenderBackend
from backends.d2 import D2Backend
from backends.mermaid import MermaidBackend
from d2spec import DiagramSpec

_BACKENDS: dict[str, type[RenderBackend]] = {
    MermaidBackend.name: MermaidBackend,
    D2Backend.name: D2Backend,
}


def get_backend(name: str) -> RenderBackend:
    """Construct a backend by name. Raises ValueError on an unknown name."""
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(
            f"unknown backend {name!r}; available: {sorted(_BACKENDS)}"
        ) from None


def render(spec: DiagramSpec, backend: RenderBackend | None = None) -> str:
    """Render a diagram spec to backend source. Defaults to Mermaid."""
    backend = backend or MermaidBackend()
    return backend.render_spec(spec)


def render_er(
    roots: list[type[BaseModel]],
    backend: RenderBackend | None = None,
    direction: str = DEFAULT_ER_DIRECTION,
) -> str:
    """Render an ER diagram from Pydantic roots. Defaults to Mermaid."""
    backend = backend or MermaidBackend()
    return backend.render_er(roots, direction=direction)


def lint(text: str, backend: RenderBackend | None = None) -> structlint.LintReport:
    """Parse and structurally lint rendered source. Defaults to Mermaid."""
    backend = backend or MermaidBackend()
    return backend.lint_text(text)
