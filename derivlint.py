#!/usr/bin/env python3
"""One-derivation-per-value lint: the same computation is defined at exactly one site.

Single-source-of-truth has a sibling failure mode to re-declaration (decllint's
job): re-*derivation*. If `velocity = distance / time` is written in one factory
and the same quotient is written again in another factory — under a different
name, over the same typed inputs — the two definitions can silently drift apart.
The names are irrelevant; the *derivation* is the thing that must live once.

This linter fingerprints derivation expressions found in Python source. It reads
two kinds of site with stdlib `ast`:

  1. Each keyword argument value in a "model constructor call" — `C(velocity=EXPR)`
     where `C` is one of the caller-supplied `model_names`, appearing inside any
     function.
  2. The return expression of any method decorated with `computed_field`.

Before comparing, each expression is normalized (see `_normalize`): single-use
local aliases are inlined, and attribute-chain roots that are function parameters
are rewritten to their annotated *type* names (`o: Order` makes `o.distance`
canonicalize to `Order.distance`; `self` becomes the enclosing class). The same
formula over *different* typed inputs is therefore a different fingerprint, which
is the point — `Order.distance / Order.time` and `Leg.distance / Leg.time` are
two honest derivations, not a duplicate.

Known limits (deliberate):
  * Commutativity is NOT assumed. `a / b` and `b / a` fingerprint differently,
    and so do `a + b` and `b + a`. Recognizing algebraic equivalence is out of
    scope; this catches copy-paste-and-rename, not re-proved identities.
  * Only structurally interesting expressions participate. Bare carries
    (attribute chains / names), constants, and no-argument calls are a different
    linter's concern and are filtered out to avoid crying wolf.

    from derivlint import check_derivations, check_paths, check_numbered_fields
"""
from __future__ import annotations

import argparse
import ast
import copy
import re
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from decllint import declaring_class

NUMBERED_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_]+\d+$")
COMPUTED_FIELD_DECORATOR: str = "computed_field"
_ALIAS_DEPTH_LIMIT: int = 10
_PARAM_PLACEHOLDER: str = "_param_"
_STRUCTURAL_NODES: tuple[type[ast.AST], ...] = (
    ast.BinOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


class ViolationKind(StrEnum):
    DUPLICATE_DERIVATION = "duplicate_derivation"
    NUMBERED_NAME = "numbered_name"


BLOCKING_KINDS: frozenset[ViolationKind] = frozenset(
    {ViolationKind.DUPLICATE_DERIVATION, ViolationKind.NUMBERED_NAME}
)


class Site(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    function: str
    field: str
    line: int


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ViolationKind
    detail: str
    sites: tuple[Site, ...]


class _Collected(BaseModel):
    """One participating derivation occurrence: its structural fingerprint key, a
    human-readable canonical rendering, and where it was found."""

    model_config = ConfigDict(frozen=True)

    key: str
    readable: str
    site: Site


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _call_name(func: ast.expr) -> str | None:
    """The trailing name of a call target: `Order` for `Order(...)`, `attr` for
    `models.Order(...)`. Anything else (a subscript, a call result) has no name."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _annotation_name(annotation: ast.expr | None) -> str | None:
    """A single type name pulled from an annotation node. `Order` -> "Order",
    `pkg.Order` -> "Order", a string forward-ref -> its text, `list[Order]` ->
    "list". Unrecognized shapes yield None (treated as unannotated)."""
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value
    if isinstance(annotation, ast.Subscript):
        return _annotation_name(annotation.value)
    return None


def _is_computed_field(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when a decorator on `func` is (or wraps) `computed_field`, matching a
    bare name, a call `computed_field(...)`, or an attribute `pydantic.computed_field`.
    Stacking with `property` is fine — we only require the decorator to be present."""
    for decorator in func.decorator_list:
        target: ast.expr = decorator.func if isinstance(decorator, ast.Call) else decorator
        name: str | None = _call_name(target)
        if name == COMPUTED_FIELD_DECORATOR:
            return True
    return False


def _iter_functions(
    module: ast.Module,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, ast.ClassDef | None]]:
    """Every function/method in the tree paired with a qualified name and the
    class that directly encloses it (or None). Nested functions lose class
    context — only a method's own class is used for `self` typing."""
    results: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, ast.ClassDef | None]] = []

    def walk(node: ast.AST, prefix: str, class_node: ast.ClassDef | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname: str = f"{prefix}{child.name}"
                results.append((child, qualname, class_node))
                walk(child, f"{qualname}.<locals>.", None)
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.", child)
            else:
                walk(child, prefix, class_node)

    walk(module, "", None)
    return results


def _own_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """Every node in the function body, but not descending into nested function
    or class definitions — those belong to their own scope."""
    collected: list[ast.AST] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            collected.append(child)
            walk(child)

    for statement in func.body:
        collected.append(statement)
        walk(statement)
    return collected


def _param_types(
    func: ast.FunctionDef | ast.AsyncFunctionDef, class_node: ast.ClassDef | None
) -> dict[str, str | None]:
    """Map each parameter name to its annotated type name (or None when
    unannotated). Inside a class, `self` maps to the class name."""
    arguments: ast.arguments = func.args
    every: list[ast.arg] = [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    ]
    if arguments.vararg is not None:
        every.append(arguments.vararg)
    if arguments.kwarg is not None:
        every.append(arguments.kwarg)
    types: dict[str, str | None] = {arg.arg: _annotation_name(arg.annotation) for arg in every}
    if class_node is not None:
        types["self"] = class_node.name
    return types


def _alias_map(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, ast.expr]:
    """Local single-assignment aliases: names bound exactly once in the function
    body via `name = EXPR` (or `name: T = EXPR`). Reassigned names, loop targets,
    augmented assignments, and self-referential bindings are excluded."""
    own: list[ast.AST] = _own_nodes(func)
    store_counts: dict[str, int] = {}
    for node in own:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            store_counts[node.id] = store_counts.get(node.id, 0) + 1

    aliases: dict[str, ast.expr] = {}
    for node in own:
        name: str | None = None
        rhs: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name = node.targets[0].id
            rhs = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            rhs = node.value
        if name is None or rhs is None or store_counts.get(name) != 1:
            continue
        referenced: set[str] = {n.id for n in ast.walk(rhs) if isinstance(n, ast.Name)}
        if name in referenced:
            continue
        aliases[name] = rhs
    return aliases


class _AliasInliner(ast.NodeTransformer):
    """Replace each load of an alias name with a fresh copy of its bound expression."""

    def __init__(self, aliases: dict[str, ast.expr]) -> None:
        self.aliases: dict[str, ast.expr] = aliases
        self.changed: bool = False

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.aliases:
            self.changed = True
            return copy.deepcopy(self.aliases[node.id])
        return node


class _RootRenamer(ast.NodeTransformer):
    """Rewrite parameter-rooted name loads to their type names, so an attribute
    chain like `o.distance` (with `o: Order`) becomes `Order.distance`."""

    def __init__(self, param_types: dict[str, str | None]) -> None:
        self.param_types: dict[str, str | None] = param_types

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.param_types:
            type_name: str | None = self.param_types[node.id]
            node.id = type_name if type_name is not None else _PARAM_PLACEHOLDER
        return node


def _normalize(
    expr: ast.expr, aliases: dict[str, ast.expr], param_types: dict[str, str | None]
) -> ast.expr:
    """Canonicalize a derivation expression: inline aliases to a fixpoint (capped
    at `_ALIAS_DEPTH_LIMIT` passes), then rename parameter roots to their types."""
    tree: ast.expr = copy.deepcopy(expr)
    for _ in range(_ALIAS_DEPTH_LIMIT):
        inliner: _AliasInliner = _AliasInliner(aliases)
        tree = inliner.visit(tree)
        if not inliner.changed:
            break
    tree = _RootRenamer(param_types).visit(tree)
    ast.fix_missing_locations(tree)
    return tree


def _has_structure(tree: ast.AST) -> bool:
    """Whether an expression carries real derivation structure — a BinOp, BoolOp,
    Compare, ternary, comprehension, or a call *with* arguments. Bare carries,
    constants, and no-argument calls have none and are skipped."""
    for node in ast.walk(tree):
        if isinstance(node, _STRUCTURAL_NODES):
            return True
        if isinstance(node, ast.Call) and (node.args or node.keywords):
            return True
    return False


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #
def _numbered_violation(site: Site, origin: str) -> Violation:
    return Violation(
        kind=ViolationKind.NUMBERED_NAME,
        detail=(
            f"{origin} field name '{site.field}' ends in digits — a numbered "
            f"variant name is a re-derivation smell"
        ),
        sites=(site,),
    )


def _collect(source: str, model_names: set[str], path: str) -> tuple[list[_Collected], list[Violation]]:
    """Parse `source`, returning every participating derivation occurrence and any
    numbered-name violations found at derivation sites."""
    module: ast.Module = ast.parse(source)
    occurrences: list[_Collected] = []
    numbered: list[Violation] = []

    for func, qualname, class_node in _iter_functions(module):
        param_types: dict[str, str | None] = _param_types(func, class_node)
        aliases: dict[str, ast.expr] = _alias_map(func)
        own: list[ast.AST] = _own_nodes(func)

        for node in own:
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node.func) not in model_names:
                continue
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                site: Site = Site(
                    path=path, function=qualname, field=keyword.arg, line=keyword.value.lineno
                )
                if NUMBERED_NAME_RE.match(keyword.arg):
                    numbered.append(_numbered_violation(site, "constructor keyword"))
                normalized: ast.expr = _normalize(keyword.value, aliases, param_types)
                if _has_structure(normalized):
                    occurrences.append(
                        _Collected(
                            key=ast.dump(normalized),
                            readable=ast.unparse(normalized),
                            site=site,
                        )
                    )

        if _is_computed_field(func):
            field_name: str = func.name
            if NUMBERED_NAME_RE.match(field_name):
                numbered.append(
                    _numbered_violation(
                        Site(path=path, function=qualname, field=field_name, line=func.lineno),
                        "computed_field",
                    )
                )
            for node in own:
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                normalized = _normalize(node.value, aliases, param_types)
                if _has_structure(normalized):
                    occurrences.append(
                        _Collected(
                            key=ast.dump(normalized),
                            readable=ast.unparse(normalized),
                            site=Site(
                                path=path,
                                function=qualname,
                                field=field_name,
                                line=node.value.lineno,
                            ),
                        )
                    )

    return occurrences, numbered


def _duplicate_violations(occurrences: list[_Collected]) -> list[Violation]:
    """Group occurrences by fingerprint; a fingerprint seen at two or more
    *distinct* sites (a differing function or target field) is a duplicate."""
    by_key: dict[str, list[_Collected]] = {}
    for occurrence in occurrences:
        by_key.setdefault(occurrence.key, []).append(occurrence)

    violations: list[Violation] = []
    for occurrences_for_key in by_key.values():
        distinct: set[tuple[str, str, str]] = {
            (o.site.path, o.site.function, o.site.field) for o in occurrences_for_key
        }
        if len(distinct) < 2:
            continue
        readable: str = occurrences_for_key[0].readable
        sites: tuple[Site, ...] = tuple(
            sorted(
                {o.site for o in occurrences_for_key},
                key=lambda s: (s.path, s.line, s.function, s.field),
            )
        )
        rendered: str = "; ".join(
            f"{s.path}:{s.line} ({s.function} -> {s.field})" for s in sites
        )
        violations.append(
            Violation(
                kind=ViolationKind.DUPLICATE_DERIVATION,
                detail=f"derivation `{readable}` defined at {len(sites)} sites: {rendered}",
                sites=sites,
            )
        )
    return sorted(violations, key=lambda v: v.detail)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def check_derivations(source: str, model_names: set[str], path: str = "<string>") -> list[Violation]:
    """Lint a single source string for duplicate derivations and numbered names."""
    occurrences, numbered = _collect(source, model_names, path)
    return _duplicate_violations(occurrences) + numbered


def check_paths(paths: list[str], model_names: set[str]) -> list[Violation]:
    """Lint multiple files, comparing fingerprints *across* files — two factories
    in two files that derive the same value are a duplicate."""
    all_occurrences: list[_Collected] = []
    all_numbered: list[Violation] = []
    for path in paths:
        source: str = Path(path).read_text(encoding="utf-8")
        occurrences, numbered = _collect(source, model_names, path)
        all_occurrences.extend(occurrences)
        all_numbered.extend(numbered)
    return _duplicate_violations(all_occurrences) + all_numbered


def check_numbered_fields(models: list[type]) -> list[Violation]:
    """Sweep real Pydantic classes for numbered *declared* field names. Inheritance
    is attributed via the MRO, so an inherited numbered field is reported once
    against the base that declares it, not once per subclass."""
    seen: set[tuple[type, str]] = set()
    violations: list[Violation] = []
    for model in models:
        names: set[str] = set(getattr(model, "model_fields", {})) | set(
            getattr(model, "model_computed_fields", {}) or {}
        )
        for name in sorted(names):
            if not NUMBERED_NAME_RE.match(name):
                continue
            owner: type = declaring_class(model, name)
            key: tuple[type, str] = (owner, name)
            if key in seen:
                continue
            seen.add(key)
            violations.append(
                Violation(
                    kind=ViolationKind.NUMBERED_NAME,
                    detail=(
                        f"model field '{name}' declared on {owner.__name__} ends in "
                        f"digits — a numbered variant name is a re-derivation smell"
                    ),
                    sites=(
                        Site(
                            path=getattr(owner, "__module__", "<unknown>"),
                            function=owner.__name__,
                            field=name,
                            line=0,
                        ),
                    ),
                )
            )
    return violations


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _format(violation: Violation) -> str:
    return f"[{violation.kind.value}] {violation.detail}"


def main(argv: list[str]) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Enforce one derivation per value across Python source files."
    )
    parser.add_argument("files", nargs="+", help="Python source files to lint.")
    parser.add_argument(
        "--models",
        required=True,
        help="Comma-separated model constructor names (e.g. Order,Leg).",
    )
    namespace: argparse.Namespace = parser.parse_args(argv)
    model_names: set[str] = {name.strip() for name in namespace.models.split(",") if name.strip()}

    violations: list[Violation] = check_paths(namespace.files, model_names)
    if not violations:
        print("derivlint: clean — every derivation defined once")
        return 0
    print(f"derivlint: {len(violations)} violation(s)")
    for violation in violations:
        print(f"  {_format(violation)}")
    return 1 if any(v.kind in BLOCKING_KINDS for v in violations) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
