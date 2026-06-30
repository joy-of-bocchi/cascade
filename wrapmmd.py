#!/usr/bin/env python3
"""Wrap a Mermaid `.mmd` source file in a full-page, pan/zoom HTML viewer.

Mermaid renders in the browser, so there is no SVG to post-process the way
`wrap.py` does for D2. This emits a self-contained page that loads the Mermaid
runtime and a pan/zoom library from a CDN, renders the diagram, and sizes the
SVG to fill the entire viewport.

Sizing strategy: keep Mermaid's `viewBox` (it is the content's bounding box) and
force the SVG to `width:100% height:100%` with `preserveAspectRatio` meet, so the
vector scales up to fill the page by default — no fit() measurement that can
collapse a small graph into a corner. Pan/zoom is layered on top.

    python3 wrapmmd.py diagram.mmd   ->   diagram.view.html
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>__NAME__</title>
<style>
  html, body { margin: 0; width: 100%; height: 100%; background: #0d1117; overflow: hidden; }
  #wrap { position: fixed; inset: 0; }
  /* Fill the viewport: override Mermaid's max-width cap and let the viewBox scale up. */
  #wrap svg {
    max-width: none !important;
    width: 100% !important;
    height: 100% !important;
    display: block;
  }
  #chip {
    position: fixed; top: 8px; left: 8px; z-index: 10;
    padding: 4px 10px; border-radius: 6px;
    background: rgba(22,27,34,0.85); color: #cfe1f5;
    font: 12px ui-monospace, monospace; border: 1px solid #30363d;
    pointer-events: none;
  }
  .mermaid { opacity: 0; width: 100%; height: 100%; }
</style>
</head>
<body>
<div id="chip">__NAME__ · scroll = zoom, drag = pan</div>
<div id="wrap"><pre class="mermaid">
__MMD__
</pre></div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  import panzoom from "https://cdn.jsdelivr.net/npm/panzoom@9.4.3/+esm";

  mermaid.initialize({ startOnLoad: false, theme: "dark", maxTextSize: 500000, maxEdges: 5000 });
  await mermaid.run();

  const host = document.querySelector(".mermaid");
  const svg = host.querySelector("svg");
  if (svg) {
    // Fill the page: drop intrinsic size, keep the viewBox, scale to fit.
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("preserveAspectRatio", "xMidYMin slice");
    host.style.opacity = 1;

    // Pan/zoom the inner content group so the viewBox-driven fill stays intact.
    const target = svg.querySelector("g") || svg;
    panzoom(target, { maxZoom: 50, minZoom: 0.05, smoothScroll: false, zoomDoubleClickSpeed: 1 });
  }
</script>
</body>
</html>
"""


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: wrapmmd.py <diagram.mmd>")
    mmd_path = Path(sys.argv[1])
    out_path = mmd_path.with_suffix(".view.html")
    mermaid_src = mmd_path.read_text()
    page = TEMPLATE.replace("__NAME__", html.escape(mmd_path.stem)).replace(
        "__MMD__", mermaid_src
    )
    out_path.write_text(page)
    print(out_path)


if __name__ == "__main__":
    main()
