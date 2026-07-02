#!/usr/bin/env python3
"""AST dataflow extractor.

Reads Python source with the stdlib `ast` module and recovers, at the
Pydantic-model level, how data flows between models: where each entity is
constructed, what its constructor args resolve to, which fields are read, which
fields are written after construction, which control-flow branches gate
construction, and which entities are never built (dormant). The result is a
`FactIR`. Nothing from the target module is imported; this is pure syntax.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator

from astflow.ir import (
    ArgResolution,
    ConstructionFact,
    DecisionFact,
    EntityFacts,
    FactIR,
    MutationFact,
    ReadFact,
    Site,
)

BASE_MODEL_NAME: str = "BaseModel"
MUTATION_METHODS: frozenset[str] = frozenset(
    {"append", "extend", "add", "update", "insert", "setdefault"}
)
IDENTIFIER_TOKEN: re.Pattern[str] = re.compile(r"\w+")


def _iter_local_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield descendants of `node` without descending into nested function or
    class bodies, so facts stay attributed to the function that owns them."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from _iter_local_nodes(child)


def _annotation_ref(node: ast.AST | None, entities: set[str]) -> str | None:
    """Resolve a type annotation to an entity reference string.

    A bare entity annotation resolves to its name (`Whole`); a subscripted
    annotation whose element is an entity resolves to the wrapped form
    (`list[Mid]`). Annotations that touch no entity resolve to None.
    """
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id if node.id in entities else None
    if isinstance(node, ast.Subscript):
        inner_ref: str | None = _annotation_ref(node.slice, entities)
        if inner_ref is None:
            return None
        container: str = ast.unparse(node.value)
        return f"{container}[{inner_ref}]"
    return None


def _entity_names_in_ref(ref: str | None, entities: set[str]) -> set[str]:
    """Extract the entity names mentioned in a reference string like
    `list[Mid]` (-> {"Mid"}) or `Whole` (-> {"Whole"})."""
    if ref is None:
        return set()
    return {token for token in IDENTIFIER_TOKEN.findall(ref) if token in entities}


def _attribute_path(node: ast.AST) -> list[str] | None:
    """Return the dotted path of an attribute/name target, e.g. `self.summary.total`
    -> ["self", "summary", "total"]. Subscripts pass through to their base."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        base: list[str] | None = _attribute_path(node.value)
        if base is None:
            return None
        return base + [node.attr]
    if isinstance(node, ast.Subscript):
        return _attribute_path(node.value)
    return None


class Extractor:
    """Builds a `FactIR` from one or more parsed Python files."""

    def __init__(self) -> None:
        self.ir: FactIR = FactIR()
        self.entities: set[str] = set()
        self.func_returns: dict[str, str] = {}

    def collect_definitions(self, tree: ast.AST, file: str) -> None:
        """First pass over a file: register entity classes and function return
        annotations that resolve to entities."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and self._is_entity_class(node):
                self.entities.add(node.name)

        # Return annotations depend on the full entity set, so resolve them after
        # every entity in this file is known.
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in self.entities:
                self.ir.entities.setdefault(
                    node.name,
                    EntityFacts(
                        name=node.name,
                        defined_at=Site(file=file, line=node.lineno),
                    ),
                )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ret_ref: str | None = _annotation_ref(node.returns, self.entities)
                if ret_ref is not None:
                    self.func_returns[node.name] = ret_ref

    def _is_entity_class(self, node: ast.ClassDef) -> bool:
        return any(
            isinstance(base, ast.Name) and base.id == BASE_MODEL_NAME
            for base in node.bases
        )

    def process_file(self, tree: ast.AST, file: str) -> None:
        """Second pass over a file: emit construction, read, decision, and
        return facts per function, and mutation facts per entity method."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._process_function(node, file)
            if isinstance(node, ast.ClassDef) and node.name in self.entities:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._process_mutations(item, node.name, file)

    def _build_symtab(
        self, func: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> dict[str, str]:
        """Map local variable names to entity references from parameter
        annotations, annotated assignments, and construction assignments."""
        symtab: dict[str, str] = {}

        params: list[ast.arg] = [
            *func.args.posonlyargs,
            *func.args.args,
            *func.args.kwonlyargs,
        ]
        for arg in params:
            ref: str | None = _annotation_ref(arg.annotation, self.entities)
            if ref is not None:
                symtab[arg.arg] = ref

        # A second binding may depend on an earlier one (chained assignment), so
        # iterate to a fixpoint over the assignments in the function body.
        for _ in range(2):
            for node in _iter_local_nodes(func):
                if isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name
                ):
                    ann_ref: str | None = _annotation_ref(
                        node.annotation, self.entities
                    )
                    if ann_ref is not None:
                        symtab[node.target.id] = ann_ref
                elif isinstance(node, ast.Assign):
                    value_ref: str | None = self._resolve_value(node.value, symtab)
                    if value_ref is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                symtab[target.id] = value_ref

        return symtab

    def _resolve_value(self, node: ast.AST, symtab: dict[str, str]) -> str | None:
        """Resolve an expression to the entity reference it produces, or None."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in self.entities:
                return node.func.id
            if node.func.id in self.func_returns:
                return self.func_returns[node.func.id]
            return None
        if isinstance(node, ast.Name):
            return symtab.get(node.id)
        if isinstance(node, ast.List):
            return self._resolve_sequence(node.elts, symtab)
        if isinstance(node, ast.ListComp):
            return self._resolve_sequence([node.elt], symtab)
        return None

    def _resolve_sequence(
        self, elements: list[ast.expr], symtab: dict[str, str]
    ) -> str | None:
        """Resolve a list/comprehension to `list[E]` when its elements are
        constructions of, or references to, a single entity E."""
        for element in elements:
            element_ref: str | None = self._resolve_value(element, symtab)
            if element_ref is not None and element_ref in self.entities:
                return f"list[{element_ref}]"
        return None

    def _call_func_attribute_ids(
        self, func: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> set[int]:
        """Node ids of attributes used as a call target (e.g. `whole.compute`),
        so method invocations are not mistaken for field reads."""
        ids: set[int] = set()
        for node in _iter_local_nodes(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                ids.add(id(node.func))
        return ids

    def _process_function(
        self, func: ast.FunctionDef | ast.AsyncFunctionDef, file: str
    ) -> None:
        symtab: dict[str, str] = self._build_symtab(func)
        call_func_ids: set[int] = self._call_func_attribute_ids(func)

        self._emit_constructions(func, file, symtab)
        self._emit_reads(func, file, symtab, call_func_ids)
        self._emit_decisions(func, file)
        self._emit_returns(func, file, symtab)

    def _emit_constructions(
        self,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        file: str,
        symtab: dict[str, str],
    ) -> None:
        for node in _iter_local_nodes(func):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in self.entities
            ):
                continue
            entity: str = node.func.id
            site: Site = Site(file=file, line=node.lineno, function=func.name)
            args: list[ArgResolution] = []
            for keyword in node.keywords:
                if keyword.arg is None:
                    self.ir.unresolved.append(
                        f"{entity}(...) splat '**{ast.unparse(keyword.value)}' "
                        f"at {file}:{node.lineno} (untyped seam)"
                    )
                    continue
                expr: str = ast.unparse(keyword.value)
                resolved: str | None = self._resolve_value(keyword.value, symtab)
                if resolved is None:
                    self.ir.unresolved.append(
                        f"{entity}(...) arg '{keyword.arg}' = {expr} "
                        f"at {file}:{node.lineno} (untyped seam)"
                    )
                args.append(
                    ArgResolution(field=keyword.arg, expr=expr, resolves_to=resolved)
                )
            self.ir.entities[entity].constructed_at.append(
                ConstructionFact(site=site, args=args)
            )

    def _emit_reads(
        self,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        file: str,
        symtab: dict[str, str],
        call_func_ids: set[int],
    ) -> None:
        for node in _iter_local_nodes(func):
            if not (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
            ):
                continue
            if id(node) in call_func_ids:
                continue
            ref: str | None = symtab.get(node.value.id)
            if ref is not None and ref in self.entities:
                self.ir.entities[ref].fields_read.append(
                    ReadFact(
                        site=Site(file=file, line=node.lineno, function=func.name),
                        attr=node.attr,
                    )
                )

    def _emit_decisions(
        self, func: ast.FunctionDef | ast.AsyncFunctionDef, file: str
    ) -> None:
        for node in _iter_local_nodes(func):
            if not isinstance(node, ast.If):
                continue
            gates: list[str] = []
            for sub in _iter_local_nodes(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id in self.entities
                    and sub.func.id not in gates
                ):
                    gates.append(sub.func.id)
            if gates:
                self.ir.decisions.append(
                    DecisionFact(
                        id=f"{func.name}@{file}:{node.lineno}",
                        condition=ast.unparse(node.test),
                        site=Site(file=file, line=node.lineno, function=func.name),
                        gates=sorted(gates),
                    )
                )

    def _emit_returns(
        self,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        file: str,
        symtab: dict[str, str],
    ) -> None:
        returned: set[str] = _entity_names_in_ref(
            _annotation_ref(func.returns, self.entities), self.entities
        )
        for node in _iter_local_nodes(func):
            if isinstance(node, ast.Return) and node.value is not None:
                value_ref: str | None = self._resolve_value(node.value, symtab)
                returned |= _entity_names_in_ref(value_ref, self.entities)
        for entity in sorted(returned):
            self.ir.entities[entity].returned_by.append(
                f"{func.name}@{file}:{func.lineno}"
            )

    def _process_mutations(
        self,
        method: ast.FunctionDef | ast.AsyncFunctionDef,
        owner: str,
        file: str,
    ) -> None:
        """Record post-construction writes to `self.<field>...` inside a method
        of an entity class as a single mutation fact keyed by the method name."""
        targets: list[str] = []
        for node in _iter_local_nodes(method):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    self._record_self_write(target, targets)
            elif isinstance(node, ast.AugAssign):
                self._record_self_write(node.target, targets)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in MUTATION_METHODS
            ):
                self._record_self_write(node.func.value, targets)

        if targets:
            self.ir.entities[owner].mutated_after.append(
                MutationFact(
                    site=Site(file=file, line=method.lineno, function=method.name),
                    via=method.name,
                    targets=targets,
                )
            )

    def _record_self_write(self, target: ast.AST, sink: list[str]) -> None:
        path: list[str] | None = _attribute_path(target)
        if path is not None and len(path) > 1 and path[0] == "self":
            dotted: str = ".".join(path[1:])
            if dotted not in sink:
                sink.append(dotted)


def extract(paths: list[str]) -> FactIR:
    """Parse each path and return a `FactIR` describing model-level dataflow."""
    extractor: Extractor = Extractor()
    parsed: dict[str, ast.AST] = {}
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            source: str = handle.read()
        tree: ast.AST = ast.parse(source, filename=path)
        parsed[path] = tree
        extractor.collect_definitions(tree, path)
    for path, tree in parsed.items():
        extractor.process_file(tree, path)
    return extractor.ir


if __name__ == "__main__":
    fixture: str = "astflow/fixtures/sample.py"
    argv_paths: list[str] = sys.argv[1:] or [fixture]
    ir: FactIR = extract(argv_paths)
    print(ir.model_dump_json(indent=2))
