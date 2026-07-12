#!/usr/bin/env python3
"""Generate a native-text .d2 diagram from a typed graph spec.

This module does generation only — spec in, `.d2` text out. It performs no
validation: linting is a separate concern, handled by `speclint` / `d2lint` /
`namelint`. Validate before generating if you want to, but the generator never
calls a linter and a linter never calls the generator; they only share `d2spec`.

The emission logic lives in the D2 render backend; this is the stable entry point.

    from d2gen import build_d2
    from d2spec import DiagramSpec, ModelNode, Edge, Group
"""

from __future__ import annotations

from ..spec.d2spec import DiagramSpec
from .backends.d2 import D2Backend


def build_d2(spec: DiagramSpec) -> str:
    return D2Backend().render_spec(spec)
