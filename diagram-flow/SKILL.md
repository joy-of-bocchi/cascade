---
name: diagram-flow
description: Generate a verified decision-and-payload object view of a Python pipeline — parallel tracer subagents derive cited edges, a skeptic re-derives them independently, astflow cross-checks, an author writes the DECIDES/WHY layer — then render to Mermaid and D2.
disable-model-invocation: true
---

Produce an **object view** of a Python pipeline: stage groups, decision nodes with
inline `DECIDES:/WHY:` rationale, payload cards (introspected field tables + an
authored role sentence and note column), and collapsed boundary modules — where
every edge is **cite-or-cut** (backed by a code citation, dashed as `inferred`,
or absent) and every prose claim is cited or marked `^[inferred]`. Tracers derive
facts; a skeptic independently verifies; an author writes the judgment layer; the
diagram ships with its evidence. The ER half of any picture is derived
mechanically (`render_er`) and never needs this skill.

Input: an **entry point** (function or method), its package, and a **focus** —
the subsystem or theme drawn at full fidelity (e.g. "tap identity", "off-cycle
cross-case"). Everything traced but outside the focus is collapsed into
`ModuleNode` boundaries showing only their products. If the target package needs
an app context to import (e.g. Django), run all model-loading commands inside it.

## Steps

### 1. Scope

Trace the entry point's import closure to a file list, and collect the entity
set (Pydantic models AND dataclasses in scope). Partition entities into clusters
by module. Record which clusters fall inside the focus.

Done when: every entity in the closure is assigned to exactly one cluster, the
file list is written down, and the focus boundary is explicit.

### 2. Trace

Dispatch one tracer subagent per cluster, in parallel, each with the contract in
[`TRACER.md`](TRACER.md) verbatim plus its cluster's entity list and the file
list. Trace every cluster — facts outside the focus feed the module boundaries
and the evidence file.

Done when: every tracer has returned an edge list, and every edge in every list
either carries a `file:line` citation or is marked `UNVERIFIED`. A tracer that
returns prose instead of the edge schema is re-dispatched, not paraphrased.

### 3. Assemble

Merge the edge lists, dedupe, then apply the topology pass to the raw skeleton:

- parts point **into** the whole they compose (build-dependency direction, never
  ER "has-a" direction)
- external inputs flow in; the product flows on to its consumers
- post-construction derivation (`mutated_after`) renders dashed, whole → derived
  field's entity
- **dormant** entities (no producer found) are marked so — never drawn as live flow
- decision facts become decision nodes whose out-edges are outcomes (yes/no),
  never actions

This skeleton is facts only — it is the author's input, not the deliverable.

Done when: every skeleton edge maps to a tracer edge.

### 4. Verify

Two independent channels, in parallel:

- Dispatch the skeptic subagent with the contract in [`SKEPTIC.md`](SKEPTIC.md)
  verbatim, the skeleton's edge list, and the file list — **never the tracers'
  output**. It re-derives every edge from code and returns a verdict per edge.
- Run the astflow extractor over the same files and diff its edge set against the
  skeleton's, reading the diff asymmetrically: an edge astflow found that the
  agents missed → investigate the agents; an edge the agents drew that astflow
  lacks → check its citation, it is usually an extractor blind spot (helper
  returns, dynamic attributes, untyped seams), not an agent error.

Repair: `REFUTED` → delete; `UNVERIFIABLE` → dashed + `inferred`; astflow-only →
one targeted tracer, add only if it comes back cited. At most two rounds.

Done when: zero `REFUTED` edges remain, every `UNVERIFIABLE` edge is dashed, and
every skeleton edge has a skeptic verdict and an astflow presence bit.

### 5. Author

Dispatch the author subagent with the contract in [`AUTHOR.md`](AUTHOR.md)
verbatim, the verified skeleton (edges + decisions + dormant + seams, with
verdicts), the file list, the entry point, and the focus. It returns a typed
`DiagramSpec` builder — the object view. Then take a main-thread pass over the
returned spec: sharpen prose, check the diamond criterion held, adjust altitude.

The author's job, enforced by its contract:

- consolidate the tracers' fragmented endpoint names into a curated node
  vocabulary at object-view altitude
- elevate to a `DecisionNode` (with inline `rationale`) only the gates whose
  branches change what the consumer gets; absorb guards into notes/evidence
- write each payload's `prose` (who writes it, who reads it, when) and per-field
  `notes`; the field table itself is introspected, never hand-written
- group nodes into `Group` stages with cadence labels
- collapse everything outside the focus into `ModuleNode`s with `products`
- declare `roots` (external inputs / the entry)

Validate the spec with `speclint.validate` — the **reachability check is a hard
gate**: any node not connected to a root goes back to the author to connect
(with a cited edge) or cut, at most two rounds; anything still loose is cut.

Done when: `speclint` passes (connected, acyclic, referential), no diamond
carries an action-labeled out-edge, and every prose claim in the spec is cited
in the evidence file or marked `^[inferred]`.

### 6. Ship

Write next to each other: the spec (`<name>_spec.py`, executable), both rendered
sources with distinct stems (`<name>_mermaid.mmd`, `<name>_d2.d2` — the wrappers
derive `<stem>.view.html`, so identical stems clobber each other), both viewers
(`cascade/render/wrapmmd.py` / `cascade/render/wrap.py`, each an infinite viewBox pan/zoom canvas), and the
evidence file — a table of `edge | kind | citation | verdict` for every edge plus
a `claim | citation` section for authored prose. Open a viewer and confirm by
screenshot that it renders.

Done when: all files exist, the evidence covers every edge and prose claim, and
the render is visually confirmed — not assumed.

## Acceptance floor

When a prior trusted diagram of the same subject exists (a north-star page), it
is a **minimal set**: the new object view must cover every element of it —
diamonds, payloads, stages, key edges — or explain each gap with a citation
showing the code no longer does that. More than the floor is fine; less is a
failed run.

## Refresh

To check an existing object view for staleness without paying for the swarm:
re-run astflow and diff against the evidence file's edge set. No change → the
diagram stands. Changed → re-run from step 2, scoped to the clusters whose edges
moved.

## Toolkit paths

Rendering, spec types, and extraction live in
`~/.claude/skills/d2-diagram-generation/`: `cascade/spec/d2spec.py` (DiagramSpec /
ModelNode+prose+notes / DecisionNode+rationale / ModuleNode / Group+cadence /
roots), `cascade/render/render.py` (backends), `cascade/lint/speclint.py` (`validate` — includes the
reachability gate), `cascade/render/wrap.py` / `cascade/render/wrapmmd.py` (viewers), `cascade/astflow/` (extractor).
Run with that directory on `PYTHONPATH`. The cite-or-cut
rationale is Authoring Rule 3 in that skill's `SKILL.md`.
