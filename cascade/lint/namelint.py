#!/usr/bin/env python3
"""One-name discipline + value-provenance linter for a set of Pydantic models.

A canonical registry names every quantity exactly once and records its origin
(base, or derived from other quantities). Models are then checked against it:

  closed vocabulary   every model field name is a registered canonical name
  consistent typing   a name has one canonical type everywhere it appears
  inputs exist        every `derived_from` references a registered name
  acyclic             no value is derived (transitively) from itself

Single-definition - the core "no value is re-derived" guarantee - is structural:
the registry holds exactly one entry per name, so a quantity cannot have two
derivations. The judgment "is this new field a new quantity or an alias?" is not
encoded here; an unregistered field is reported, and a human or model resolves it
once at registration (add it, or rename the field to the existing canonical name).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ..spec.d2spec import type_str


class Origin(StrEnum):
    BASE = "base"
    DERIVED = "derived"


class CanonicalField(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    type_repr: str
    origin: Origin
    derived_from: tuple[str, ...] = ()
    description: str = ""


def base(name: str, type_repr: str, description: str = "") -> CanonicalField:
    return CanonicalField(
        name=name, type_repr=type_repr, origin=Origin.BASE, description=description
    )


def derived(
    name: str, type_repr: str, from_: list[str], description: str = ""
) -> CanonicalField:
    return CanonicalField(
        name=name,
        type_repr=type_repr,
        origin=Origin.DERIVED,
        derived_from=tuple(from_),
        description=description,
    )


def registry(*fields: CanonicalField) -> dict[str, CanonicalField]:
    return {field.name: field for field in fields}


class ViolationKind(StrEnum):
    UNREGISTERED = "unregistered_name"
    TYPE_MISMATCH = "type_mismatch"
    UNKNOWN_INPUT = "unknown_derivation_input"
    DERIVATION_CYCLE = "derivation_cycle"


class NameViolation(BaseModel):
    kind: ViolationKind
    where: str
    detail: str


def check_models(
    models: list[type[BaseModel]], canonical: dict[str, CanonicalField]
) -> list[NameViolation]:
    violations: list[NameViolation] = []
    for model in models:
        for field_name, info in model.model_fields.items():
            where = f"{model.__name__}.{field_name}"
            if field_name not in canonical:
                violations.append(
                    NameViolation(
                        kind=ViolationKind.UNREGISTERED,
                        where=where,
                        detail=(
                            f"'{field_name}' is not a canonical name "
                            f"(register it as new, or rename to the existing canonical)"
                        ),
                    )
                )
                continue
            actual = type_str(info.annotation)
            expected = canonical[field_name].type_repr
            if actual != expected:
                violations.append(
                    NameViolation(
                        kind=ViolationKind.TYPE_MISMATCH,
                        where=where,
                        detail=f"'{field_name}' is {actual} here but canonical type is {expected}",
                    )
                )
    return violations


def _derivation_cycles(canonical: dict[str, CanonicalField]) -> list[list[str]]:
    adjacency: dict[str, list[str]] = {
        name: list(cf.derived_from) for name, cf in canonical.items()
    }
    color: dict[str, int] = {
        name: 0 for name in adjacency
    }  # 0 unseen, 1 active, 2 done
    cycles: list[list[str]] = []

    def visit(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, child_index = stack[-1]
            if child_index == 0:
                color[node] = 1
                path.append(node)
            inputs = adjacency[node]
            if child_index < len(inputs):
                stack[-1] = (node, child_index + 1)
                source = inputs[child_index]
                if source not in adjacency:
                    continue
                if color[source] == 1:
                    cycles.append([*path[path.index(source) :], source])
                elif color[source] == 0:
                    stack.append((source, 0))
                continue
            color[node] = 2
            path.pop()
            stack.pop()

    for name in adjacency:
        if color[name] == 0:
            visit(name)
    return cycles


def check_registry(canonical: dict[str, CanonicalField]) -> list[NameViolation]:
    violations: list[NameViolation] = []
    for field in canonical.values():
        for source in field.derived_from:
            if source not in canonical:
                violations.append(
                    NameViolation(
                        kind=ViolationKind.UNKNOWN_INPUT,
                        where=f"registry:{field.name}",
                        detail=f"derived from '{source}', which is not registered",
                    )
                )
    for loop in _derivation_cycles(canonical):
        violations.append(
            NameViolation(
                kind=ViolationKind.DERIVATION_CYCLE,
                where="registry",
                detail=" -> ".join(loop),
            )
        )
    return violations


def report(title: str, violations: list[NameViolation]) -> None:
    print(f"\n=== {title}: {len(violations)} violation(s) ===")
    if not violations:
        print("  clean")
        return
    for violation in violations:
        print(f"  [{violation.kind}] {violation.where}: {violation.detail}")
