---
name: d2-diagram-generation
description: Author or edit diagrams from typed Pydantic models — decision trees, ER/schema diagrams, data pipelines, state machines, typed-model graphs — rendering to Mermaid (the default, inline-renderable) or D2 (the precision option, with a smooth infinitely-zoomable viewing artifact). Use when the user wants to diagram a pipeline / decision tree / schema / state machine, mentions Mermaid or .d2 or .mmd, wants to convert between Mermaid and D2, or needs a zoomable diagram they can pan around. Triggers: "make a diagram", "diagram this pipeline", "mermaid diagram", "make a d2 diagram", "convert this mermaid to d2", "zoomable diagram", "draw the decision tree".
---

# Diagrams from typed models: Mermaid by default, D2 when you need its precision

One typed `DiagramSpec` (model / decision / terminal nodes, edges, groups) renders to two backends. Mermaid is the default: it renders inline on GitHub and in most markdown tooling, runs client-side with no binary to install, and is the more ubiquitous format. D2 is the opt-in precision option for what Mermaid can't express — column-level FK edges, faithful `sql_table` rendering, boards / drill-down — and it gives a smooth, infinitely-zoomable SVG viewing artifact.

Render through the neutral surface; the backend is an argument, never a rewrite of the spec:

```python
from render import render, render_er, lint, get_backend
from backends import D2Backend

render(spec)                       # Mermaid (the default)
render(spec, D2Backend())          # D2, by explicit backend
render(spec, get_backend("d2"))    # D2, by name
render_er([RootModel])             # Mermaid classDiagram from Pydantic roots
lint(render(spec))                 # structural report on the rendered text
```

`structlint` validates the graph regardless of backend: each backend parses its own output back into a shared graph, and the cycle / dangling-edge / isolated-node checks run on that neutral graph. Switch backends and the structural guarantees still hold.

On the D2 path, two authoring/viewing choices decide whether the SVG is smooth and deep-zoomable or a stuttering mess. Get them right and a 1000-node diagram pans like a map; get them wrong and it janks even when small. The performance and authoring rules further down are the distilled rule set for that path.

## Intent — why any of this exists (read first)

These are not arbitrary rules; each serves a goal. When a rule and a goal conflict, the goal wins. Hold the *why*, not just the *how*.

**The origin.** Human workflows are decision trees; data pipelines are ER / transformation graphs. The job is to encode them as diagrams that carry the *typed data* passed through them — the frozen Pydantic models — so the picture and the types are one artifact, not two things to keep in sync.

**The hard constraints (all of these, always — they are requirements, not preferences):**
- **Human-legible** — readable, editable, and reasonably pleasing to a person.
- **Agent-legible and FAST to edit** — an agent can read and change it quickly; no slow edit→render→screenshot loop (the old PDF workflow that took ~3 minutes per change is the explicit anti-goal). Edits must be near-instant, plain-text, and diff-able.
- **Not laggy AND infinitely zoomable** — the two non-negotiable *viewing* constraints. Pan/zoom must stay smooth at any depth on a large diagram. This is *why* native-text-not-`foreignObject` and `viewBox`-not-CSS-transform are load-bearing, not nitpicks — they exist solely to satisfy these two.
- **Deep modules / collapsible fidelity** — you can group boxes and hide them under abstracted "deep" modules, producing diagrams at *varying levels of fidelity* to aid both human and agent understanding. (D2 boards/layers; collapse a subgraph to one box.)
- **Thin, unopinionated, lightweight** — "Obsidian for markdown": a plain-text source you own and an agent can edit directly, not a heavy opinionated app.

**The structural properties every workflow diagram must hold:**
- **Immutability** — values don't change after they're produced (frozen).
- **Durable intermediate states** — for now this just means **every entity model is frozen** (a produced value can't be mutated afterward), checked by `speclint.check_frozen` sweeping the whole model list.
- **A single source of ground truth** — each value defined once; never re-derived.
- **A DAG with no cycles** — derivation flows one way; nothing feeds its own origin.

These are not aesthetic — they are exactly the properties that make the *underlying data pipeline* correct, which is why the lints check them and why the typed-model discipline below exists. The diagram is trustworthy only if the thing it depicts has these properties.

**North star: a reactive pipeline, drawn legibly for human and agent.** Two ends, and everything below serves one of them:
1. A data pipeline that behaves like a spreadsheet — **change one value anywhere in the hierarchy and everything downstream recomputes automatically, easily, and fast**; adding or removing a value stays cheap.
2. The pipeline's shape captured in a diagram that is **legible and editable by both a human and an agent, fast to change, and smooth to explore at any zoom**.

**Why single-source-of-truth / no re-derivation.** Cheap automatic recompute is only possible if each value is *defined once*. A value computed in two places can't have a change propagate to both — they drift, and "recompute" silently produces inconsistency. So: one definition per value, carried down unchanged, never re-derived. This is *the* central concern — the reason the whole typed shell exists.

**Why push everything into the type system.** Untyped string machinery (a `json_schema_extra` registry, hand-maintained name lists) is the thing to avoid: it's curated by hand with no guarantee its references even exist. The goal is the inverse — make the **models the single source of truth** and let **mypy be the linter**, so the guarantees are compiled, not maintained. Build the *minimum* deterministic machinery, only for the residue types genuinely can't express. (Thin shell around model reasoning: code the orchestration; let the type system / model judgment do the deciding — never a regex over meaning.)

**Why generation, viewing, and linting are separate concerns.** Drawing a picture and validating a model graph are different jobs; coupling them puts a linter on the critical path of just rendering. They share only the neutral schema and never import each other.

**Why minimal — what we deliberately did NOT build.** "Build only what actually does the job." Two checks were tried and removed for not fitting the real goal: *single-producer* (in a dataflow DAG a node with many inputs is a normal join, not a double-write — a state-machine notion that doesn't transfer) and *inter-entity construction enforcement* (token guards / one-factory lints — over-built; the real need was only that names are defined once and carried down). When in doubt, prefer the smaller mechanism.

**The data doctrine the diagrams represent.** After raw ingestion, essentially everything is derived. Values arrive in *families* — orders, customers, line items — each its own frozen model. Two derivation scales, both type-checked, no string registry:
- *intra-entity* (a pure function of sibling fields) → `@computed_field`: auto-recomputes, defined once (method name), inputs are `self.x` so mypy catches a missing one.
- *inter-entity* (combine families A + B into a new entity) → one typed factory `C.derive(a: A, b: B) -> C`: the signature *is* the provenance; mypy rejects a wrong-family combine.

A value is **carried downstream by nesting the frozen entity** (`enriched.order.total`), by *promoting* it to its own small entity, or by *inheriting* a single declaration — **never** by re-declaring the field and copying it. Needing just one field from upstream is usually a granularity smell: promote that field to its own entity.

**Two kinds of diagram, two sources — maintain both.**
1. An **ER diagram is auto-generated from the real Pydantic models** — entity relationships (nesting, model-typed fields, references), the data model. It's a pure projection of the types, always in sync, can't drift.
2. A **decision-tree diagram is hand-authored** to show the code's control/decision logic — which is *not* recoverable from types — with the model tables rendered **inline** (as code-block tables) to aid understanding.

The ER is *derived* from the models; the decision tree is *authored* (the branching logic lives in the code, not the type graph). The lints guard both; only the ER is generated. Don't try to auto-derive the decision tree from types, and don't hand-maintain the ER — let each come from its right source.

## Default to Mermaid; reach for D2 when you need its precision

Mermaid is the default. `render(spec)`, `render_er(roots)`, and `lint(text)` all render Mermaid unless you pass a backend. Reach for D2 when you need a capability Mermaid can't express. Where Mermaid can't match a capability it degrades rather than failing, so the choice is about fidelity, not whether the diagram renders.

| capability | Mermaid (default) | D2 (`get_backend("d2")` / `D2Backend()`) |
|---|---|---|
| inline render in GitHub / markdown | yes, native | no — needs the `d2` binary or a viewer |
| field-level / column FK edges (`tableA.col -> tableB.col`) | no — degrades to an entity-to-entity edge with the field name in the relation label | yes, via `sql_table` |
| pure ER, SQL-table look | yes — `render_er` emits a Mermaid `erDiagram` (boxed entity, title bar, typed attribute rows, crow's-foot cardinality on relations) | yes, faithful `sql_table` |
| model tables as nodes *inside a flowchart* | approximated — `flowchart` packs the fields into a `<br/>`-joined list inside the node | yes, `sql_table` shapes are first-class graph nodes |
| SQL-table models **and** decision diamonds in **one** canvas | **no** — see limitation below | yes, in one graph |
| boards / layers / drill-down (click a box into a sub-canvas) | no | yes |
| decision-node rationale as a tooltip | no — dropped from the visual | yes, via `tooltip:` |
| in-place expand/collapse with reflow | no (see below) | no (see below) |
| hot-reload viewing | mermaid.js re-rendering in a page (or `mmdc` to a file) | `d2 --watch` |
| layout engine | dagre / elk | dagre / elk (this toolkit uses elk) |
| structural linting (cycle / dangling-edge / isolated-node) | yes | yes |

Switching is a backend argument:

```python
render(spec)                     # Mermaid
render(spec, D2Backend())        # D2 (explicit constructor)
render(spec, get_backend("d2"))  # D2 (by name)
```

The deciding question: does a typed table need to sit as a node inside a flowchart, do you need column-level FK edges, or do you need drill-down boards? Any yes → D2. Otherwise Mermaid, and you get inline rendering for free.

### Mermaid limitation: `erDiagram` and `flowchart` can't share a canvas (why combined views go to D2)

Mermaid's SQL-table look comes from its `erDiagram` diagram type, and decision diamonds / flow stages come from its `flowchart` type — and **a single Mermaid diagram is exactly one type**. You cannot put a decision diamond inside an `erDiagram`, nor render an entity as a real SQL-table box inside a `flowchart`. So the two halves of a "data-flow hierarchy + schema" picture are mutually exclusive in Mermaid:

- **Pure ER** (models + their typed relationships) → Mermaid `erDiagram`, SQL-table look, renders inline. Use this for the schema view.
- **Decision tree / flow with model tables** → Mermaid can only approximate it as a `flowchart` whose model nodes are `<br/>`-joined field lists (not SQL tables).
- **One canvas with SQL-table models *and* decision diamonds together** → not expressible in Mermaid. **This is the reason to use D2**, whose `sql_table` shapes, `diamond` shapes, and free flow edges all coexist in one graph. The combined "ER + decision tree, code-free" artifact is a D2 deliverable; Mermaid serves the two halves separately.

**Viewing.** Mermaid renders inline wherever markdown supports it; for a file, `mmdc -i diagram.mmd -o diagram.svg` (the `MermaidBackend.svg_command`). D2 renders via `d2 --layout elk file.d2 out.svg`, then `wrap.py` for the infinite pan/zoom canvas.

### What neither backend does: in-place expand/collapse

Both backends are compile-time static renderers: spec in, finished diagram text out. Neither expands or collapses a node *in place* with live reflow. Mermaid can fake it only by re-running its own layout client-side (mermaid.js re-rendering the whole diagram); the real tool for interactive expand/collapse with reflow is a runtime graph library (Cytoscape.js with `expand-collapse`, or similar). D2 boards / drill-down are the nearest static analogue: a click navigates to a coarser or finer board, it doesn't reflow one canvas.

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

## Authoring rule 3: every hand-authored edge must be caller-verified

Applies to the **authored** path only — flow / pipeline / decision diagrams where you write the edges by hand. The *derived* ER is exempt (its edges come mechanically from the type graph and can't carry this bug). When you hand-author edges, the failure mode is drawing a relationship that reads true but the code never executes — a false edge from proximity, not from a real call.

- **An edge exists only if you found the call site.** Before adding `A -> B`, locate where A's output actually reaches B and note it (`file:line`) to yourself. No call site → no edge.
- **Proximity is not evidence.** Same directory, an `import`, a co-located sibling, or a docstring saying "used for X" / "feeds Y" / "triggers Z" are exactly the traps that manufacture false edges. Verify by caller, never by where something lives or what prose claims.
- **Trace backward from the entry point**, not forward by folder. Ask "what does the entry point's call tree reach?" — not "what's in this package?". A forward, by-directory read groups things that are co-located but never co-invoked.
- **Unverified ⇒ dashed + labelled `inferred`, or cut.** Never render an edge you couldn't anchor to a caller as a solid stage. Dynamic dispatch, `getattr`, and data carried through `self._x` state or untyped dicts/DataFrames are legitimately hard to resolve statically — mark those `inferred`, don't pretend they're confirmed.
- **A symbol being defined/imported on the path is not the same as being called on the path.** The test is invocation, not presence.

### If you delegate the trace to a subagent

- Require a structured edge list — `{from, to, caller_file_line}` — and **reject any edge with an empty `caller_file_line`**. The subagent must cite the call site, not just where each symbol is defined.
- Tell it to mark inference as `UNVERIFIED` and to **list every in-scope model that is NOT reachable from the entry point**, so out-of-path models surface explicitly instead of getting silently bundled into the flow.
- Treat hedge-verbs ("used for", "feeds", "triggers", "handles") without a cited call as `UNVERIFIED`, not as findings. Re-derive each claimed edge from the code before it enters the diagram — the subagent's report is a lead, not ground truth.

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
python3 <skill_dir>/wrap.py diagram.svg     # writes diagram.view.html
open diagram.view.html                        # scroll = zoom to cursor, drag = pan, 0 = reset
```

For editing, run `d2 --watch file.d2` (hot reload on save); its own viewer may stutter on huge diagrams — the wrapper is the smooth one. Do **not** build raster deep-zoom tiles (OpenSeadragon/DZI) for big diagrams: d2's PNG export caps at ~8192px, so the tiles come out blurry.

## When even a viewBox canvas is heavy

If a `viewBox` canvas still stutters (thousands of elements), the fix is structural, not viewer-side: split into D2 **boards/layers** (one per stage/module) for click-to-drill navigation. This doubles as the "deep module" abstraction — collapse a subgraph to a single box at a coarser fidelity level.

## Why the typed shell exists: cheap automatic recompute

The single-source-of-truth / no-re-derivation / frozen-DAG discipline exists to make the pipeline **reactive**: change one value anywhere in the hierarchy and everything downstream recomputes — automatically, easily, fast — while adding or removing values stays cheap. The principles are the preconditions for that:

- **single definition per value** → a change has one site and one propagation path; nothing drifts out of sync.
- **typed derivation** → the dependency graph is explicit and mypy-checked, so recompute is mechanical, not manual bookkeeping.
- **frozen + acyclic** → recompute is a clean topo-ordered forward pass, and frozen inputs are memoizable (skip a factory whose inputs didn't change) → incremental and fast.

It's incremental computation — a reactive spreadsheet for the pipeline. Two primitives carry the derivation graph, **both type-checked, no string registry**:

- **Intra-entity** (a value that's a pure function of sibling fields on the *same* model): `@computed_field` over a `@property`. It recomputes automatically on access, is defined exactly once (method name), and reads `self.x`, so mypy flags a missing input. Families stay as *separate* models — this is **not** "everything in one model"; computed fields only reach `self`.
- **Inter-entity** (combine families A + B into a new derived entity C): one typed factory `C.derive(a: A, b: B) -> C`. The signature *is* the provenance; mypy rejects a wrong-family combine. One factory per entity = one construction site = no re-derivation. This also splits the fat nullable-stage-fields model into per-stage entities whose new fields are *required*.

Change propagation: change an upstream value → `model_copy(update=...)` → computed fields recompute automatically; re-run the downstream factories in topo order (cheap because pure + frozen). **That topo-order re-run *is* the recompute — there is no separate engine.** The discipline (one definition, pure factories, frozen DAG) is what makes the manual re-run correct and cheap; an incremental re-run engine (memoized on frozen inputs, Make/Salsa-style) is a possible future, not the current target. mypy catches a computed field reading a missing sibling and a swapped-family `derive(...)`.

**mypy gotcha:** `@computed_field` stacked on `@property` trips mypy's `[prop-decorator]` on every computed field. Add `disable_error_code = prop-decorator` to your mypy config (it's spurious for this pattern); the real errors then surface cleanly.

## Renderers are cosmetic — validate elsewhere

Neither backend checks anything. Each will draw a cycle, an FK to a missing PK, or a box that disagrees with the real model without complaint. If the diagram is the source of truth for a typed DAG, validate it — but keep validation a *separate concern* from rendering and viewing. Determinism works wherever structure is explicit (graph checks, model introspection, declared-group collapse); grouping and fidelity-level are judgment — keep them declared in the source or decided by the model, not inferred by heuristics.

**Rendering, viewing, and linting are separate, by design.** The toolkit is split so the concerns never sit on one pipeline:

```
shared substrate     d2spec.py     typed spec (DiagramSpec/nodes/edges) + introspection helpers; neutral
rendering            render.py     neutral surface: render / render_er / lint / get_backend (defaults to Mermaid)
                     backends/     base.py (the RenderBackend seam) + mermaid.py + d2.py
                     d2gen.py      build_d2(spec) -> .d2  — explicit-D2 HAND-AUTHORED decision tree w/ inline model tables
                     d2er.py       build_er_d2(roots) -> .d2  — explicit-D2 AUTO ER from real models (transitive closure)
                     wrap.py       svg -> infinite pan/zoom .view.html (D2 viewing path)
linting              structlint.py renderer-neutral graph checks (cycle/dangling/isolated, Tarjan + Kahn)
                     speclint.py   validate(spec): cycle / frozen / referential / type-flow
                     d2lint.py     structural checks on a .d2 file via the D2 backend (cycle/dangling/isolated)
                     decllint.py   single-declaration: each field declared on ONE model (inheritance-aware)
                     namelint.py   one-name + provenance via an explicit canonical registry
```

`structlint` is the renderer-neutral core: each backend parses its own output into a `structlint.Graph` and the same checks run, which is why `lint(text)` works against Mermaid or D2 alike. A runnable side-by-side demo is bundled: `demo_backends.py` renders one spec and one ER through both backends and lints each.

```
PYTHONPATH=<skill_dir> uv run --with pydantic python3 <skill_dir>/demo_backends.py
```

**`decllint.py` is the minimal, type-first name discipline — prefer it.** `check_single_declaration(models)` introspects the models and flags any field name *declared* on more than one class (attributing each field to the class in its MRO that actually declares it, so inheriting a field counts once, re-declaring it counts twice). That single check enforces both **defined-once-carried-down** (a value lives on one model; downstream nests or inherits it, never re-declares it) and **one-name-per-quantity** (the same name on two unrelated models = one name used for two things). No registry to maintain. `namelint.py` is the heavier alternative when you want an *explicit* canonical registry with declared `derived_from` provenance; reach for it only if you need provenance the models don't already encode via `@computed_field` / factory signatures. It passes on nesting and inheritance, and fails on a re-declared field.

`d2gen` imports no linter and no linter imports `d2gen`; both sides depend only on `d2spec`. Lint a spec when you want to — generation never invokes a linter, and a linter never generates. (Run order is the caller's choice: typically `validate(spec)` then `build_d2(spec)`, but they're independent calls.)

**`speclint.py`** — `validate(spec)` runs the spec-level checks: **cycle**, **frozen** (every model node `frozen=True`), **referential** (edge/group references resolve), **type-flow** (a declared edge payload must be producible by the source model). In-degree > 1 is fine — a join/fan-in is normal in a dataflow DAG — so there is no single-producer check; `frozen` + `acyclic` + `type-flow` are the pipeline invariants.

**`d2gen.py`** — the *hand-authored decision-tree* side. `build_d2(spec)` only; each model node's table is introspected from `model_fields`, so a box can't disagree with its class, but the graph (decisions, edges, grouping) is authored because the control logic isn't in the types.

**`d2er.py`** — the *auto-generated ER* side. `build_er_d2(roots)` introspects the real Pydantic models: each model → a `sql_table`, and every field whose type references another model (directly, optional, in a `list`/`dict`, or in a union) → a column-level relationship edge with cardinality read from the type (`1` / `0..1` / `*`). It transitively closes from the roots you pass, so a single root yields the whole reachable ER, always in sync with the code — no hand-authoring, can't drift. Gotcha: field names that are D2 reserved words (e.g. a field named `label`) are quoted in rows and edges so they don't break compilation.

**`d2lint.py`** — extracts the directed graph from a `.d2` file (skipping code-block interiors so content arrows aren't mistaken for edges) and runs cycle detection (Tarjan SCC, FAIL), dangling-edge endpoints (FAIL), isolated nodes (WARN), plus a Kahn topo-order when acyclic. No model needed:

```
uv run --with pydantic python <skill_dir>/d2lint.py diagram.d2   # exit 1 on a blocking (FAIL) violation
```

**One-name discipline + provenance (bundled): `namelint.py`.** Enforces single-source-of-truth at the *field* level — SSA for your data, the discipline that no value is ever re-derived. A canonical **registry** names every quantity once (`base(name, type)` / `derived(name, type, from_=[...])`), and models are checked against it:

- **closed vocabulary** — every model field name is a registered canonical name (aliases like `cost` for `upgrade_cost_usd`, and typos, FAIL)
- **consistent typing** — a name has one canonical type everywhere it appears (same name, two types FAILs)
- **inputs-exist** + **acyclic** — the `derived_from` graph references only registered names and never derives a value from itself

Single-definition (no value re-derived) is *structural*: one registry entry per name, so a quantity can't have two derivations. The judgment "is this new field a new quantity or an alias?" is **not** coded — an unregistered field is reported and resolved once at registration (register it as new, or rename the field to the existing canonical). The registry is the ubiquitous-language dictionary.

## Verify before claiming done

```
grep -c '<foreignObject' out.svg                              # ~0
grep -oE 'width="[0-9]+" height="[0-9]+"' out.svg | head -1   # canvas size sanity
qlmanage -t -s 1600 -o . out.svg                              # thumbnail; then a high-zoom crop to confirm legibility
```
