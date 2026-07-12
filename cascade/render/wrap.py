#!/usr/bin/env python3
"""Wrap an SVG in a standalone infinite pan/zoom canvas.

Pan/zoom is driven by mutating the SVG viewBox, not a CSS transform on a
container. The <svg> stays viewport-sized, so the browser only rasterizes the
visible region each frame - cost stays flat no matter how large the diagram or
how deep the zoom, which a CSS-transform on a giant inline layer cannot do.
"""

import re
import sys
from pathlib import Path

svg_path = Path(sys.argv[1])
out_path = svg_path.with_suffix(".view.html")
svg = svg_path.read_text()

# Pull intrinsic size, ensure a viewBox exists, drop fixed width/height so the
# element is governed by CSS (fills the viewport).
vb_match = re.search(r'viewBox="([\d.\s-]+)"', svg)
if vb_match:
    _, _, vw, vh = (float(v) for v in vb_match.group(1).split())
else:
    w_match = re.search(r'\bwidth="([\d.]+)"', svg)
    h_match = re.search(r'\bheight="([\d.]+)"', svg)
    vw = float(w_match.group(1)) if w_match else 1000.0
    vh = float(h_match.group(1)) if h_match else 1000.0
    svg = svg.replace("<svg", f'<svg viewBox="0 0 {vw} {vh}"', 1)

# Remove the first <svg ...> width/height attributes so CSS sizing wins.
head_match = re.search(r"<svg[^>]*>", svg)
head = head_match.group(0)
new_head = re.sub(r'\s(width|height)="[^"]*"', "", head)
new_head = new_head.replace(
    "<svg", '<svg id="diagram" preserveAspectRatio="xMidYMid meet"', 1
)
svg = svg.replace(head, new_head, 1)

TMPL = """<!doctype html><html><head><meta charset="utf-8"><title>__NAME__</title>
<style>
 html,body{margin:0;height:100%;overflow:hidden;background:#0f1115}
 #diagram{position:fixed;inset:0;width:100%;height:100%;cursor:grab;touch-action:none}
 #diagram.drag{cursor:grabbing}
 #hud{position:fixed;top:10px;left:10px;color:#9aa3b0;font:12px ui-monospace,monospace;
      background:#0009;padding:6px 10px;border-radius:7px;user-select:none;pointer-events:none}
</style></head><body>
__SVG__
<div id="hud">scroll = zoom to cursor &middot; drag = pan &middot; 0 = reset</div>
<script>
 const W=__W__,H=__H__,svg=document.getElementById('diagram');
 let vb={x:0,y:0,w:W,h:H};
 const apply=()=>svg.setAttribute('viewBox',`${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
 function geom(){const r=svg.getBoundingClientRect();
   const s=Math.min(r.width/vb.w,r.height/vb.h);
   return {r,s,ox:(r.width-vb.w*s)/2,oy:(r.height-vb.h*s)/2};}
 function toSvg(cx,cy){const{r,s,ox,oy}=geom();
   return {x:(cx-r.left-ox)/s+vb.x,y:(cy-r.top-oy)/s+vb.y};}
 svg.addEventListener('wheel',e=>{e.preventDefault();
   const p=toSvg(e.clientX,e.clientY);
   const f=Math.exp(e.deltaY*0.0015);
   const nw=Math.min(W*4,Math.max(W/4000,vb.w*f));
   const ratio=nw/vb.w; vb.w=nw; vb.h*=ratio;
   const{r,s,ox,oy}=geom();
   vb.x=p.x-(e.clientX-r.left-ox)/s;
   vb.y=p.y-(e.clientY-r.top-oy)/s;
   apply();
 },{passive:false});
 let down=false,px,py;
 svg.addEventListener('pointerdown',e=>{down=true;px=e.clientX;py=e.clientY;
   svg.classList.add('drag');svg.setPointerCapture(e.pointerId);});
 svg.addEventListener('pointermove',e=>{if(!down)return;const{s}=geom();
   vb.x-=(e.clientX-px)/s;vb.y-=(e.clientY-py)/s;px=e.clientX;py=e.clientY;apply();});
 svg.addEventListener('pointerup',()=>{down=false;svg.classList.remove('drag');});
 addEventListener('keydown',e=>{if(e.key==='0'){vb={x:0,y:0,w:W,h:H};apply();}});
 apply();
</script></body></html>"""

html = (
    TMPL.replace("__NAME__", svg_path.stem)
    .replace("__W__", repr(vw))
    .replace("__H__", repr(vh))
    .replace("__SVG__", svg)
)
out_path.write_text(html)
print(out_path)
