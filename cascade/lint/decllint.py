#!/usr/bin/env python3
"""Single-declaration lint: every field name is *declared* on exactly one model.

This is the enforcement behind "a value is defined once and carried down
unchanged." A value lives on its owning model; downstream entities reach it by
nesting (a model-typed field) or by inheriting the single declaration — never by
re-declaring the leaf field and copying its value. Re-declaration is what lets a
name drift out of sync and silently re-derive, breaking single-source-of-truth.

The check is inheritance-aware: a field is attributed to the class in the MRO
that actually *declares* it (its own annotations / class dict), so inheriting a
field counts as one declaration, while re-declaring it on a subclass or on an
unrelated model counts as two. The same logic also enforces one-name-per-quantity:
the same field name declared on two unrelated models means one name is being used
for two things (or one thing is declared twice) — both are violations.

    from decllint import check_single_declaration
"""

from __future__ import annotations

from pydantic import BaseModel


class DeclViolation(BaseModel):
    name: str
    declarers: list[str]


def declaring_class(model: type[BaseModel], name: str) -> type:
    """The class in the model's MRO that actually declares `name` — via its own
    annotations (regular fields) or its own dict (computed fields). Inherited
    fields resolve to the base that declared them, not the inheriting model."""
    for cls in model.__mro__:
        if cls is BaseModel or cls is object:
            continue
        if name in getattr(cls, "__annotations__", {}) or name in vars(cls):
            return cls
    return model


def check_single_declaration(models: list[type[BaseModel]]) -> list[DeclViolation]:
    declarers: dict[str, set[type]] = {}
    for model in models:
        names = set(model.model_fields) | set(
            getattr(model, "model_computed_fields", {}) or {}
        )
        for name in names:
            declarers.setdefault(name, set()).add(declaring_class(model, name))
    return [
        DeclViolation(name=name, declarers=sorted(cls.__name__ for cls in classes))
        for name, classes in sorted(declarers.items())
        if len(classes) > 1
    ]


def report(title: str, models: list[type[BaseModel]]) -> list[DeclViolation]:
    violations = check_single_declaration(models)
    print(f"\n=== {title}: {len(violations)} violation(s) ===")
    if not violations:
        print("  clean — every field declared once")
    for violation in violations:
        print(
            f"  [duplicate_declaration] '{violation.name}' declared on {', '.join(violation.declarers)}"
        )
    return violations
