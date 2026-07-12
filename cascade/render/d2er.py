#!/usr/bin/env python3
"""Auto-generate an ER .d2 diagram from real Pydantic models.

Generation only (no validation). Each model becomes a `sql_table`; every field
whose type references another model — directly, optional, in a list/dict, or in
a union — becomes a relationship edge, with cardinality read from the type
(`1`, `0..1`, `*`). Starting from the roots you pass, it transitively closes
over every referenced model, so the ER is always a faithful projection of the
code: change a model, regenerate, the picture follows.

The emission logic lives in the D2 render backend; this is the stable entry point.

    from d2er import build_er_d2
    print(build_er_d2([TopModel]))
"""

from __future__ import annotations

from .backends.d2 import D2Backend


def build_er_d2(roots: list[type], direction: str = "right") -> str:
    return D2Backend().render_er(roots, direction)
