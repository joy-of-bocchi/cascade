# Rendering: Mermaid by default, D2 for precision

Moved intact from the front page: backend choice, the D2 performance rules, and
viewing. The quickstart and map live in ../README.md; the why in INTENT.md.

D2 (`terrastruct/d2`, `brew install d2`) turns text into diagrams: vector SVG out, plain-text source in. On the D2 path, two authoring/viewing choices decide whether the result is smooth and deep-zoomable or a stuttering mess. Get them right and a 1000-node diagram pans like a map; get them wrong and it janks even when small. The performance and authoring rules below are the distilled rule set for that path.

## Default to Mermaid; reach for D2 when you need its precision

Mermaid is the default backend. It renders inline on GitHub and in most markdown tooling, runs client-side with no binary to install before you can look at it, and is the more ubiquitous format (so an agent authors it from far more training data). `render(spec)`, `render_er(roots)`, and `lint(text)` all default to Mermaid. Reach for D2 when you need a capability Mermaid can't express: column-level FK edges, faithful `sql_table` rendering, or boards / drill-down.

The two backends share the same `DiagramSpec`. Where Mermaid can't match a capability it degrades rather than failing, so the choice is about fidelity, not whether the diagram renders at all.

| capability | Mermaid (default) | D2 (`get_backend("d2")` / `D2Backend()`) |
|---|---|---|
| inline render in GitHub / markdown | yes, native | no — needs the `d2` binary or a viewer |
| field-level / column FK edges (`tableA.col -> tableB.col`) | no — degrades to an entity-to-entity edge with the field name in the relation label | yes, via `sql_table` |
| model tables with typed field rows | approximated — `flowchart` packs the fields into a `<br/>`-joined list inside the node; `classDiagram` shows `+type field` rows with type strings flattened to safe tokens | yes, faithful `sql_table` |
| model boxes + decision diamonds in one diagram | yes, in one `flowchart` | yes, in one graph |
| boards / layers / drill-down (click a box into a sub-canvas) | no | yes |
| decision-node rationale as a tooltip | no — dropped from the visual | yes, via `tooltip:` |
| in-place expand/collapse with reflow | no (see below) | no (see below) |
| hot-reload viewing | mermaid.js re-rendering in a page | `d2 --watch` |
| layout engine | dagre / elk | dagre / elk (this toolkit uses elk) |
| structural linting (cycle / dangling-edge / isolated-node) | yes | yes |

Switching is a backend argument, never a rewrite of the spec:

```python
render(spec)                     # Mermaid
render(spec, D2Backend())        # D2 (explicit constructor)
render(spec, get_backend("d2"))  # D2 (by name)
```

The deciding question: does a typed table need to sit as a node inside a flowchart, do you need column-level FK edges, or do you need drill-down boards? Any yes → D2. Otherwise Mermaid, and you get inline rendering for free.

### What neither backend does: in-place expand/collapse

Both backends are compile-time static renderers: spec in, finished diagram text out. Neither expands or collapses a node *in place* with live reflow. Mermaid can fake it only by re-running its own layout client-side (mermaid.js re-rendering the whole diagram); the real tool for interactive expand/collapse with reflow is a runtime graph library (Cytoscape.js with `expand-collapse`, or similar). If you need a box you can click to grow and shrink while the rest of the graph rearranges around it, that's runtime-lib territory, not something either of these backends gives you. D2 boards / drill-down are the nearest static analogue: a click navigates to a coarser or finer board, it doesn't reflow one canvas.

## D2 path: the two decisions that determine performance (read first)

Everything from here through "When even a viewBox canvas is heavy" is D2-path guidance — it applies when you render to `.d2` and view the SVG. The Mermaid path renders inline and doesn't go through these knobs.

1. **Author with native SVG text, not `foreignObject`.** (authoring)
2. **View via the SVG `viewBox`, not a CSS transform.** (viewer)

Both must be right. A diagram with zero `foreignObject` still stutters in a CSS-transform viewer; a `viewBox` viewer still stutters if the SVG is full of `foreignObject`.

## Authoring rule 1: stay out of `foreignObject`

D2 renders some label forms as native `<text>` (GPU-composited, vector-crisp, free to transform) and others as `<foreignObject>` — embedded HTML that the browser re-rasterizes on the CPU every zoom frame. A few dozen `foreignObject`s make a large diagram lag. Verify after every render:

```
grep -c '<foreignObject' out.svg     # target 0 (single digits at most)
```

Label-form cheat sheet:

| form | renders as | use for |
|---|---|---|
| plain label, `x: "a\nb"` (single or `\n` multi-line) | native text ✓ | prose, short labels |
| code block, **backtick-fenced**: `` x: |`txt `` … `` `| `` | native **monospace** text ✓ | aligned N-column tables (field/type/default/note) — columns line up because monospace |
| `shape: sql_table` | native ✓ | ER / typed models when 2 cols + one constraint tag suffice; enables column-level FK edges + crow's-foot |
| ``|`md `` … `` `| `` markdown block | **foreignObject ✗** | avoid in large diagrams (this is what makes ported Mermaid lag) |
| untagged block `\| … \|` | **foreignObject ✗** | avoid |

Code-block language tag: use `txt` (or `none` / `plain`) for clean monospace with **no** syntax coloring. Use `python` only if you *want* types/keywords highlighted (`None` and `|` go bold, types go blue). `json`/`yaml` over-highlight. The lang must immediately follow the fence.

## Authoring rule 2: gotchas that will bite

- **Backtick-fence any block whose content contains `|`.** A single-pipe fence (`` |txt … | `` or `` |md … | ``) is closed by the *first* `|` in the content — and union types (`float | None`) and markdown tables are full of pipes. Open with `` |`txt `` (pipe-backtick-lang) and close with `` `| `` (backtick-pipe).
- **Escape `$` → `\$` in quoted labels.** A bare `$` triggers D2's `${}` substitution and the file fails to compile (`substitutions must begin on {`).
- **No `"` inside a quoted label** — swap to `'`.
- **Diamonds: short label + `tooltip:`.** Put only the question in the decision node; move any rationale to `tooltip: "..."`. A multi-line diamond label stretches into an unreadable flat sliver and bloats the canvas.
- **Default to `--layout elk`** for anything non-trivial (see Layout engine below).

## Layout engine: default to ELK

D2 decouples layout from syntax — same `.d2`, swap `--layout`, different arrangement (only node positions and edge routing change; nodes/edges/labels/styles are identical, so switching is a flag, never a rewrite). There's no universally-best graph layout, so pick by diagram shape:

| engine | reach for it when | cost |
|---|---|---|
| **ELK** (`--layout elk`) — **the working default for serious diagrams** | large graphs, nested containers, dense edge webs; gives tight packing + orthogonal routing + good crossing minimization | slower, but free and bundled |
| **dagre** (D2's own built-in default) | quick, small flowcharts & trees | sprawls and routes messily as soon as containers or density appear |
| **TALA** | "designed-looking" architecture posters — intentional grouping, less rigid hierarchy | proprietary, license-gated |

Rule of thumb: start with `--layout elk` and only drop to dagre for throwaway small diagrams. Container-heavy or typed-DAG diagrams (the main use case here) always want ELK — dagre makes them sprawl, which is exactly what bloated the canvas before switching. (Aside: the pluggability is also Terrastruct's business model — dagre/ELK stay free while TALA is the paid premium engine.)

## Tables: two native options

- **`sql_table`** — when 2 columns + one constraint tag is enough, and you want column-level FK edges or crow's-foot cardinality (`source-arrowhead.shape: cf-many` / `cf-one` / `cf-one-required`). Quote complex type strings: `taps: "dict[tuple[str,str], X]"`.
- **Backtick `txt` code block** — when you need 3–4 columns (field / type / default / note). Pre-align cells with spaces (monospace makes it land). Render section dividers as `# - section -` (reads as a comment). This keeps full tabular richness while staying native/fast.

## Color encodes type

Define a `classes:` block; let color mean role (model / decision / terminal / deep-module), so the palette is a legend. Note: **code-block nodes render on a light background**, so their class `fill` shows only as the border — type-code those via `stroke`. Decision/prose nodes honor `fill` + `font-color` (dark fill + light text reads well).

## Converting Mermaid → D2

Do **not** transliterate Mermaid's workarounds. Because Mermaid `flowchart` nodes can't be typed tables, authors encode models as HTML `<table>` inside node labels. Port those verbatim and every node becomes a `foreignObject` → lag + a pasted-Mermaid look. Re-express natively:

- model tables → backtick `txt` code block (keep all columns) or `sql_table` (2-col)
- verbose `DECIDES:/WHY:` diamond labels → short question + `tooltip:`
- `subgraph` → containers (nest via dotted path `S6.S7.node` or nested blocks)
- `classDef` → `classes:`
- `-.->` dotted edges → `{ style.stroke-dash: 4 }`

## Viewing: the artifact must be a `viewBox` canvas

A raw `.svg` opened in a browser tab has no pan/zoom. And CSS-transforming a large inline SVG promotes it to one giant raster layer that re-rasterizes every frame — and past ~16k px it exceeds GPU texture limits and falls back to CPU. That stutters *even with zero `foreignObject`*. The fix is to pan/zoom by mutating the SVG `viewBox`: the `<svg>` stays viewport-sized, so the browser only paints the visible region — flat cost at any zoom depth or canvas size (how web maps and `svg-pan-zoom` work).

Use the bundled wrapper (in this skill's directory):

```
python3 <repo>/cascade/render/wrap.py diagram.svg     # writes diagram.view.html
open diagram.view.html                        # scroll = zoom to cursor, drag = pan, 0 = reset
```

For editing, run `d2 --watch file.d2` (hot reload on save); its own viewer may stutter on huge diagrams — the wrapper is the smooth one. Do **not** build raster deep-zoom tiles (OpenSeadragon/DZI) for big diagrams: d2's PNG export caps at ~8192px, so the tiles come out blurry.

## When even a viewBox canvas is heavy

If a `viewBox` canvas still stutters (thousands of elements), the fix is structural, not viewer-side: split into D2 **boards/layers** (one per stage/module) for click-to-drill navigation. This doubles as the "deep module" abstraction — collapse a subgraph to a single box at a coarser fidelity level.

## Verify before claiming done

```
grep -c '<foreignObject' out.svg                              # ~0
grep -oE 'width="[0-9]+" height="[0-9]+"' out.svg | head -1   # canvas size sanity
qlmanage -t -s 1600 -o . out.svg                              # thumbnail; then a high-zoom crop to confirm legibility
```
