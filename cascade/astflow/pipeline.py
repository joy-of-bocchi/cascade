#!/usr/bin/env python3
"""End-to-end pipeline: source files -> AST facts -> DiagramSpec -> D2.

Ties the extractor, the diagram builder, and the verifier together. The AST
extractor works purely on source; the builder needs the live Pydantic classes to
render sql_tables, so the caller imports the modules and hands their classes in.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from pydantic import BaseModel

from ..render.backends.d2 import D2Backend
from ..spec.d2spec import DiagramSpec
from .extract import extract
from .to_spec import build_spec
from .verify import verify

DEFAULT_FIXTURE_PATH: Path = Path(__file__).with_name("fixtures") / "sample.py"
DEFAULT_FIXTURE_MODULE: str = "cascade.astflow.fixtures.sample"


def entity_classes(module_names: list[str]) -> dict[str, type[BaseModel]]:
    """Collect the Pydantic models defined in the given modules, keyed by name."""
    out: dict[str, type[BaseModel]] = {}
    for module_name in module_names:
        mod = importlib.import_module(module_name)
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__ == module_name
            ):
                out[obj.__name__] = obj
    return out


def run(paths: list[str], module_names: list[str], out_prefix: str) -> DiagramSpec:
    ir = extract(paths)
    classes = entity_classes(module_names)
    spec = build_spec(ir, classes)
    violations = verify(spec, ir, classes)

    d2_text = D2Backend().render_spec(spec)
    out_path = Path(out_prefix).with_suffix(".d2")
    out_path.write_text(d2_text)

    n_nodes = len(spec.nodes)
    n_edges = len(spec.edges)
    dormant = [name for name, e in ir.entities.items() if e.is_dormant]
    print(
        f"entities={len(ir.entities)} decisions={len(ir.decisions)} unresolved={len(ir.unresolved)}"
    )
    print(f"spec: {n_nodes} nodes, {n_edges} edges  ->  {out_path}")
    print(f"dormant entities: {dormant}")
    print(f"verify violations: {violations}")
    return spec


if __name__ == "__main__":
    run(
        paths=[str(DEFAULT_FIXTURE_PATH)],
        module_names=[DEFAULT_FIXTURE_MODULE],
        out_prefix="astflow_fixture",
    )
