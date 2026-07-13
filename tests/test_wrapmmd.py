from __future__ import annotations

from cascade.render.wrapmmd import MERMAID_CDN_URL, MERMAID_VERSION, TEMPLATE


def test_template_uses_pinned_mermaid_cdn_url() -> None:
    assert MERMAID_VERSION == "11.16.0"
    assert MERMAID_CDN_URL in TEMPLATE
    assert f"mermaid@{MERMAID_VERSION}/dist/mermaid.esm.min.mjs" in TEMPLATE
    assert "__MERMAID_CDN_URL__" not in TEMPLATE
