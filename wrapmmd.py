#!/usr/bin/env python3
"""Wrap a Mermaid `.mmd` source file in a full-page, infinite pan/zoom HTML viewer.

Mermaid renders in the browser, so there is no SVG file to post-process the way
`wrap.py` does for D2. This emits a self-contained page that loads the Mermaid
runtime from a CDN, renders the diagram, and then drives pan/zoom the same way
`wrap.py` does: by mutating the SVG `viewBox`, never by CSS-transforming a
container. The `<svg>` stays viewport-sized, so the browser only rasterizes the
visible region each frame — flat cost at any zoom depth or canvas size.

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
  html, body { margin: 0; width: 100%; height: 100%; background: #0f1115; overflow: hidden; }
  #wrap { position: fixed; inset: 0; }
  #wrap svg {
    max-width: none !important;
    width: 100% !important;
    height: 100% !important;
    display: block;
    cursor: grab;
    touch-action: none;
  }
  #wrap svg.drag { cursor: grabbing; }
  #hud {
    position: fixed; top: 10px; left: 10px; z-index: 10;
    color: #9aa3b0; font: 12px ui-monospace, monospace;
    background: #0009; padding: 6px 10px; border-radius: 7px;
    user-select: none; pointer-events: none;
  }
  .mermaid { opacity: 0; width: 100%; height: 100%; }
</style>
</head>
<body>
<div id="hud">__NAME__ &middot; scroll = zoom to cursor &middot; drag = pan &middot; 0 = reset</div>
<div id="wrap"><pre class="mermaid">
__MMD__
</pre></div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";

  mermaid.initialize({ startOnLoad: false, theme: "dark", maxTextSize: 500000, maxEdges: 5000 });
  await mermaid.run();

  const host = document.querySelector(".mermaid");
  const svg = host.querySelector("svg");
  if (svg) {
    // Mermaid's own viewBox is the content bounding box — that is the home view.
    const home = (svg.getAttribute("viewBox") || "0 0 1000 1000").split(/\\s+/).map(Number);
    const [HX, HY, W, H] = home;
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.style.maxWidth = "none";
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    host.style.opacity = 1;

    let vb = { x: HX, y: HY, w: W, h: H };
    const apply = () => svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    function geom() {
      const r = svg.getBoundingClientRect();
      const s = Math.min(r.width / vb.w, r.height / vb.h);
      return { r, s, ox: (r.width - vb.w * s) / 2, oy: (r.height - vb.h * s) / 2 };
    }
    function toSvg(cx, cy) {
      const { r, s, ox, oy } = geom();
      return { x: (cx - r.left - ox) / s + vb.x, y: (cy - r.top - oy) / s + vb.y };
    }
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const p = toSvg(e.clientX, e.clientY);
      const f = Math.exp(e.deltaY * 0.0015);
      const nw = Math.min(W * 4, Math.max(W / 4000, vb.w * f));
      const ratio = nw / vb.w;
      vb.w = nw;
      vb.h *= ratio;
      const { r, s, ox, oy } = geom();
      vb.x = p.x - (e.clientX - r.left - ox) / s;
      vb.y = p.y - (e.clientY - r.top - oy) / s;
      apply();
    }, { passive: false });
    let down = false, px, py;
    svg.addEventListener("pointerdown", (e) => {
      down = true; px = e.clientX; py = e.clientY;
      svg.classList.add("drag"); svg.setPointerCapture(e.pointerId);
    });
    svg.addEventListener("pointermove", (e) => {
      if (!down) return;
      const { s } = geom();
      vb.x -= (e.clientX - px) / s; vb.y -= (e.clientY - py) / s;
      px = e.clientX; py = e.clientY; apply();
    });
    svg.addEventListener("pointerup", () => { down = false; svg.classList.remove("drag"); });
    addEventListener("keydown", (e) => {
      if (e.key === "0") { vb = { x: HX, y: HY, w: W, h: H }; apply(); }
    });
    apply();
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
