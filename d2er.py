#!/usr/bin/env python3
"""Auto-generate an ER .d2 diagram from real Pydantic models.

Generation only (no validation). Each model becomes a `sql_table`; every field
whose type references another model — directly, optional, in a list/dict, or in
a union — becomes a relationship edge, with cardinality read from the type
(`1`, `0..1`, `*`). Starting from the roots you pass, it transitively closes
over every referenced model, so the ER is always a faithful projection of the
code: change a model, regenerate, the picture follows.

    from d2er import build_er_d2
    print(build_er_d2([TopModel]))
"""
from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from d2spec import NONE_TYPE, type_str

COLLECTION_ORIGINS = (list, set, frozenset, tuple)


def _referenced(annotation: Any) -> list[tuple[type[BaseModel], str]]:
    """Every (model, cardinality) a type annotation points at."""
    found: list[tuple[type[BaseModel], str]] = []

    def walk(node: Any, card: str) -> None:
        origin = get_origin(node)
        if origin is None:
            if isinstance(node, type) and issubclass(node, BaseModel):
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


def _field_refs(model: type[BaseModel]) -> list[tuple[str, type[BaseModel], str]]:
    refs: list[tuple[str, type[BaseModel], str]] = []
    for name, info in model.model_fields.items():
        for ref, card in _referenced(info.annotation):
            refs.append((name, ref, card))
    for name, info in (getattr(model, "model_computed_fields", {}) or {}).items():
        for ref, card in _referenced(info.return_type):
            refs.append((name, ref, card))
    return refs


def _closure(roots: list[type[BaseModel]]) -> list[type[BaseModel]]:
    ordered: dict[type[BaseModel], None] = {}
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


def build_er_d2(roots: list[type[BaseModel]], direction: str = "right") -> str:
    models = _closure(roots)
    in_scope = set(models)
    out: list[str] = ["# ER diagram auto-generated from Pydantic models by d2er.py", f"direction: {direction}", ""]

    for model in models:
        out.append(f"{model.__name__}: {{")
        out.append("  shape: sql_table")
        for name, info in model.model_fields.items():
            out.append(f'  "{name}": "{type_str(info.annotation)}"')
        for name, info in (getattr(model, "model_computed_fields", {}) or {}).items():
            out.append(f'  "{name}": "{type_str(info.return_type)} (computed)"')
        out.append("}")
    out.append("")

    for model in models:
        for name, ref, card in _field_refs(model):
            if ref in in_scope:
                out.append(f'{model.__name__}."{name}" -> {ref.__name__}: "{card}"')

    return "\n".join(out) + "\n"
