---
name: diagram-flow
description: Generate a verified dataflow/decision diagram of a Python pipeline — parallel tracer subagents derive cited edges, a skeptic re-derives them independently, astflow cross-checks — then render.
disable-model-invocation: true
---

Produce a dataflow + decision diagram of a Python pipeline whose every edge is **cite-or-cut**: backed by a code citation, or dashed as `inferred`, or absent. Tracers derive; a skeptic independently verifies; the diagram ships with its evidence. The ER half of any picture is derived mechanically (`render_er`) and never needs this skill — this is for the flow half, where hand-authored edges lie unless verified.

Input: an entry point (function or method) and its package. If the target package needs an app context to import (e.g. Django), run all model-loading commands inside it.

## Steps

### 1. Scope

Trace the entry point's import closure to a file list, and collect the entity set (the Pydantic models in scope — reuse the ER closure machinery). Partition entities into clusters by module.

Done when: every entity in the closure is assigned to exactly one cluster, and the file list is written down.

### 2. Trace

Dispatch one tracer subagent per cluster, in parallel, each with the contract in [`TRACER.md`](TRACER.md) verbatim plus its cluster's entity list and the file list.

Done when: every tracer has returned an edge list, and every edge in every list either carries a `file:line` citation or is marked `UNVERIFIED`. A tracer that returns prose instead of the edge schema is re-dispatched, not paraphrased.

### 3. Assemble

Merge the edge lists, dedupe, then apply the topology pass:

- parts point **into** the whole they compose (build-dependency direction, never ER "has-a" direction)
- external inputs flow in; the product flows on to its consumers
- post-construction derivation (`mutated_after`) renders dashed, whole → derived field's entity
- **dormant** entities (no producer found) are marked so — never drawn as live flow
- decision facts become diamond nodes whose out-edges are outcomes (yes/no), never actions

Build the `DiagramSpec` (models as `ModelNode` so they render as tables; stages and artifacts as `TerminalNode`; decisions as `DecisionNode`) and render — D2 for a combined tables+diamonds canvas, Mermaid otherwise.

Done when: every spec edge maps to a tracer edge, and no diamond has an out-edge labeled with an action instead of an outcome.

### 4. Verify

Two independent channels, in parallel:

- Dispatch the skeptic subagent with the contract in [`SKEPTIC.md`](SKEPTIC.md) verbatim, the rendered diagram, and the file list — **never the tracers' output**. It re-derives every edge from code and returns a verdict per edge.
- Run the astflow extractor over the same files and diff its edge set against the spec's, reading the diff asymmetrically: an edge astflow found that the agents missed → investigate the agents; an edge the agents drew that astflow lacks → check its citation, it is usually an extractor blind spot (helper returns, dynamic attributes, untyped seams), not an agent error.

Done when: every spec edge has a skeptic verdict (`CONFIRMED` / `REFUTED` / `UNVERIFIABLE`) and an astflow presence bit.

### 5. Repair

- `REFUTED` → delete the edge.
- `UNVERIFIABLE` → dashed + label `inferred`. Uncertainty ships visible; it never solidifies.
- astflow-only edge → dispatch one targeted tracer at that site; add the edge only if it comes back cited.

Re-render and re-verify changed edges. At most two repair rounds; anything still contested ships flagged, with the disagreement stated in the evidence file.

Done when: zero `REFUTED` edges remain and every `UNVERIFIABLE` edge is visibly dashed.

### 6. Ship

Write three artifacts next to each other: the diagram source (`.d2`/`.mmd`), the viewer (`wrap.py` / `wrapmmd.py`), and the evidence file — a table of `edge | kind | citation | verdict` for every edge in the diagram. Open the viewer and confirm by screenshot that it renders.

Done when: all three files exist, the evidence table covers every edge, and the render is visually confirmed — not assumed.

## Refresh

To check an existing diagram for staleness without paying for the swarm: re-run astflow and diff against the evidence file's edge set. No change → the diagram stands. Changed → re-run from step 2, scoped to the clusters whose edges moved.

## Toolkit paths

Rendering and extraction live at the repo root: `render.py` (backends), `wrap.py`/`wrapmmd.py` (viewers), `astflow/` (extractor: `extract.py`, `pipeline.py`). Run with the repo root (and `astflow/`) on `PYTHONPATH`. The cite-or-cut rationale is Authoring Rule 3 in the root `SKILL.md`.
