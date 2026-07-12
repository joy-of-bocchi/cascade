#!/usr/bin/env python3
"""Carry-name lint: a value copied downstream must keep its name.

Cascade's whole point is single-source-of-truth dataflow: a quantity is declared
once and *carried* down unchanged. The one move that quietly breaks that promise
is a rename during a bare carry — reading `upstream.velocity` and handing it to a
new model's `speed` field. Nothing type-checks it (both are floats), nothing runs
differently, and the name has now drifted from its single declaration.

This linter reads source (never imports it) and inspects every model-constructor
call inside a function body. For each `dst=EXPR` keyword it decides whether EXPR is
a *carry* — a bare attribute chain rooted at a parameter (`o.field`, `o.a.b.field`,
`self.field`), possibly through a single-assignment local alias — or a
*transformation* (anything computed: BinOp, Call, constant, comprehension, ...).
A carry whose destination name differs from the carried field's name is the bug.

The dialect is deliberately small. Transformations are always allowed (they mint a
new quantity, which `namelint`/`decllint` govern). Anything the AST cannot trace —
opaque ``**kwargs``, positional construction — is not silently passed; it is
reported as a non-blocking warning so the un-checkable surface stays visible.

    from carrylint import check_carries, check_paths
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

STRING_SOURCE_PATH: str = "<string>"
KWARG_UNPACK: None = None  # ast.keyword.arg is None for a ** expansion


class ViolationKind(StrEnum):
    RENAMED_CARRY = "RENAMED_CARRY"
    UNTRACEABLE_CARRY = "UNTRACEABLE_CARRY"
    POSITIONAL_CONSTRUCTION = "POSITIONAL_CONSTRUCTION"


BLOCKING_KINDS: frozenset[ViolationKind] = frozenset({ViolationKind.RENAMED_CARRY})


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ViolationKind
    path: str
    line: int
    detail: str

    @property
    def blocking(self) -> bool:
        """Only a proven rename blocks; the two warn kinds mark un-checkable
        surface, not a known defect."""
        return self.kind in BLOCKING_KINDS


def check_carries(
    source: str, model_names: set[str], path: str = STRING_SOURCE_PATH
) -> list[Violation]:
    """Parse one source string and report every carry-name violation in it."""
    tree: ast.Module = ast.parse(source, filename=path)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.extend(_check_function(node, model_names, path))
    return violations


def check_paths(paths: list[str], model_names: set[str]) -> list[Violation]:
    """Read each file and aggregate its violations in argument order."""
    violations: list[Violation] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            source: str = handle.read()
        violations.extend(check_carries(source, model_names, path=path))
    return violations


def _check_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    model_names: set[str],
    path: str,
) -> list[Violation]:
    own_nodes: list[ast.AST] = _own_nodes(func)
    params: set[str] = _param_names(func)
    aliases: dict[str, str] = _resolvable_aliases(own_nodes, params)

    violations: list[Violation] = []
    for node in own_nodes:
        if not isinstance(node, ast.Call):
            continue
        ctor: str | None = _called_name(node.func)
        if ctor is None or ctor not in model_names:
            continue
        violations.extend(_check_call(node, ctor, params, aliases, path))
    return violations


def _check_call(
    call: ast.Call,
    ctor: str,
    params: set[str],
    aliases: dict[str, str],
    path: str,
) -> list[Violation]:
    violations: list[Violation] = []

    if call.args:
        violations.append(
            Violation(
                kind=ViolationKind.POSITIONAL_CONSTRUCTION,
                path=path,
                line=call.lineno,
                detail=(
                    f"{ctor}(...) built with {len(call.args)} positional "
                    "argument(s); field mapping is invisible"
                ),
            )
        )

    for keyword in call.keywords:
        if keyword.arg is KWARG_UNPACK:
            violations.extend(_check_unpack(keyword.value, ctor, params, aliases, path))
            continue
        violation: Violation | None = _classify_kwarg(
            keyword.arg, keyword.value, ctor, params, aliases, path
        )
        if violation is not None:
            violations.append(violation)

    return violations


def _check_unpack(
    expr: ast.expr,
    ctor: str,
    params: set[str],
    aliases: dict[str, str],
    path: str,
) -> list[Violation]:
    """A ``**`` expansion. A dict literal with constant string keys is still in
    the checkable dialect — expand each pair and classify. Anything else (a name,
    a call result, a dict with computed keys) left the dialect: warn."""
    if isinstance(expr, ast.Dict) and _has_constant_string_keys(expr):
        violations: list[Violation] = []
        for key, value in zip(expr.keys, expr.values):
            if not isinstance(key, ast.Constant):
                continue
            violation: Violation | None = _classify_kwarg(
                key.value, value, ctor, params, aliases, path
            )
            if violation is not None:
                violations.append(violation)
        return violations

    return [
        Violation(
            kind=ViolationKind.UNTRACEABLE_CARRY,
            path=path,
            line=expr.lineno,
            detail=(
                f"{ctor}(**...) expands an opaque mapping; the carry surface "
                "is not statically checkable"
            ),
        )
    ]


def _classify_kwarg(
    dst: str,
    expr: ast.expr,
    ctor: str,
    params: set[str],
    aliases: dict[str, str],
    path: str,
) -> Violation | None:
    """A single ``dst=EXPR`` pair. Returns a RENAMED_CARRY violation only when
    EXPR is a traceable bare carry whose source field name differs from `dst`.
    Transformations and untraceable roots produce no violation here."""
    source: str | None = _carry_source_name(expr, params, aliases)
    if source is None or source == dst:
        return None
    return Violation(
        kind=ViolationKind.RENAMED_CARRY,
        path=path,
        line=expr.lineno,
        detail=(
            f"{ctor}: field '{source}' carried into '{dst}' — a bare carry must "
            "not rename its source"
        ),
    )


def _carry_source_name(
    expr: ast.expr, params: set[str], aliases: dict[str, str]
) -> str | None:
    """The carried field name if EXPR is a traceable bare carry, else None.

    A bare attribute chain rooted at a parameter carries its final attribute. A
    chain rooted at a resolvable alias carries its own final attribute. A bare
    reference to a resolvable alias carries that alias's resolved field name."""
    if isinstance(expr, ast.Attribute):
        chain: tuple[str, str] | None = _attribute_chain(expr)
        if chain is None:
            return None
        root, final_attr = chain
        if root in params or root in aliases:
            return final_attr
        return None
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id)
    return None


def _resolvable_aliases(own_nodes: list[ast.AST], params: set[str]) -> dict[str, str]:
    """Local names that are single-assignment aliases of a bare chain rooted at a
    parameter, mapped to the field name they carry. A name assigned more than once
    (in any form) or assigned from a non-chain is not resolvable and is omitted."""
    store_counts: Counter[str] = Counter(
        node.id
        for node in own_nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    )
    simple_rhs: dict[str, ast.expr] = {}
    for node in own_nodes:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            simple_rhs[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            simple_rhs[node.target.id] = node.value

    aliases: dict[str, str] = {}
    for name, rhs in simple_rhs.items():
        if store_counts[name] != 1:
            continue
        chain: tuple[str, str] | None = _attribute_chain(rhs)
        if chain is None:
            continue
        root, final_attr = chain
        if root in params:
            aliases[name] = final_attr
    return aliases


def _attribute_chain(expr: ast.expr) -> tuple[str, str] | None:
    """(root name, final attribute) for a bare attribute chain rooted at a Name,
    e.g. `o.a.b.velocity` -> ("o", "velocity"). None if not such a chain."""
    if not isinstance(expr, ast.Attribute):
        return None
    final_attr: str = expr.attr
    cursor: ast.expr = expr
    while isinstance(cursor, ast.Attribute):
        cursor = cursor.value
    if not isinstance(cursor, ast.Name):
        return None
    return cursor.id, final_attr


def _has_constant_string_keys(node: ast.Dict) -> bool:
    return all(
        isinstance(key, ast.Constant) and isinstance(key.value, str)
        for key in node.keys
    )


def _called_name(func: ast.expr) -> str | None:
    """The simple name a call targets: `Model(...)` -> "Model",
    `mod.Model(...)` -> "Model". None for anything more complex."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args: ast.arguments = func.args
    names: set[str] = {
        arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
    }
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _own_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """Every node in this function's own scope — its body subtree, not descending
    into nested function or lambda scopes (those are analysed on their own with
    their own parameters)."""
    result: list[ast.AST] = []
    stack: list[ast.AST] = list(func.body)
    while stack:
        node: ast.AST = stack.pop()
        result.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)
    return result


def _format(violation: Violation) -> str:
    return (
        f"{violation.path}:{violation.line} {violation.kind.value} {violation.detail}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cascade.lint.carrylint",
        description="Enforce the carry-name invariant on model constructions.",
    )
    parser.add_argument("paths", nargs="+", help="source files to lint")
    parser.add_argument(
        "--models",
        required=True,
        help="comma-separated model class names to treat as constructors",
    )
    args = parser.parse_args(argv)
    model_names: set[str] = {
        name.strip() for name in args.models.split(",") if name.strip()
    }
    violations: list[Violation] = check_paths(args.paths, model_names)
    for violation in violations:
        print(_format(violation))
    return 1 if any(violation.blocking for violation in violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
