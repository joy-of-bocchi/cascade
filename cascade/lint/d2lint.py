#!/usr/bin/env python3
"""Structural linter CLI for a .d2 file: cycle / dangling-edge / isolated-node checks.

D2 lays a graph out but never validates it — it will happily draw a cycle, an
edge to an undefined node, or a node nothing connects to. This parses the .d2
source into the neutral graph (via the D2 backend) and runs the renderer-agnostic
checks (in `structlint`) D2 omits, so a diagram meant to be a DAG can be proven
to be one.

Run: uv run --with pydantic python -m cascade.lint.d2lint path/to/diagram.d2
Exit code is non-zero when any blocking violation is found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..render.backends.d2 import D2Backend
from .structlint import LintReport, ViolationKind

NODE_PREVIEW_LIMIT = 8


def lint(path: Path) -> LintReport:
    report = D2Backend().lint_text(path.read_text())
    report.path = str(path)
    return report


def render(report: LintReport) -> str:
    lines: list[str] = [
        f"d2lint: {report.path}",
        f"  nodes: {report.node_count}   edges: {report.edge_count}",
    ]
    if not report.violations:
        lines.append("  PASS - acyclic, no dangling edges, no isolated nodes")
    else:
        for violation in report.violations:
            marker = "WARN" if violation.kind == ViolationKind.ISOLATED_NODE else "FAIL"
            preview = ", ".join(violation.nodes[:NODE_PREVIEW_LIMIT]) + (
                "..." if len(violation.nodes) > NODE_PREVIEW_LIMIT else ""
            )
            lines.append(f"  [{marker}] {violation.kind}: {violation.detail}")
            lines.append(f"         {preview}")
    lines.append(f"  DAG: {'yes' if report.topo_order is not None else 'NO (cyclic)'}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cascade.lint.d2lint",
        description="Structural linter for .d2 graphs.",
    )
    parser.add_argument("path", type=Path, help="path to a .d2 file")
    args = parser.parse_args()
    report = lint(args.path)
    print(render(report))
    return 1 if report.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
