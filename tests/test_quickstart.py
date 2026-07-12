from __future__ import annotations

from pathlib import Path
from typing import Any

README_PATH: Path = Path("README.md")
PYTHON_FENCE: str = "```python"
FENCE: str = "```"


def _first_python_block(markdown: str) -> str:
    in_python: bool = False
    lines: list[str] = []
    line: str
    for line in markdown.splitlines():
        if not in_python and line.strip() == PYTHON_FENCE:
            in_python = True
            continue
        if in_python and line.strip() == FENCE:
            return "\n".join(lines)
        if in_python:
            lines.append(line)
    raise AssertionError("README.md has no fenced python block")


def test_readme_quickstart_executes_current_code() -> None:
    namespace: dict[str, Any] = {}
    code: str = _first_python_block(README_PATH.read_text(encoding="utf-8"))

    exec(code, namespace)

    result: Any = namespace["result"]
    summary: Any = namespace["Summary"]
    mermaid_text: Any = namespace["mermaid_text"]
    vocabulary: Any = namespace["vocabulary"]

    assert result.final_store[summary].velocity == 60.0
    assert mermaid_text.startswith("flowchart")
    assert "distance\tfloat\tTrip\t" in vocabulary
    assert "hours\tfloat\tTrip\t" in vocabulary
    assert "velocity\tfloat\tSummary\t" in vocabulary
