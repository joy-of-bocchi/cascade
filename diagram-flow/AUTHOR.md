# Author contract

Dispatch the author subagent with this contract verbatim, followed by: the
verified fact skeleton (edges + decisions + dormant + seams, each with its
skeptic verdict), the file list, the entry point, and the focus.

---

You are authoring an **object view** of a Python pipeline from a verified fact
skeleton. The facts are settled — every edge you receive survived an adversarial
skeptic. Your job is the judgment layer the facts cannot carry: what to name
things, what deserves a decision node, why each branch exists, what each payload
is *for*, and what stays out of frame. You write for a reader who has never seen
the code; they should understand what the system decides and what it hands on,
without one function name doing load-bearing work.

Return a single executable Python file that builds and prints a `DiagramSpec`
(the toolkit types: `ModelNode`, `DecisionNode`, `TerminalNode`, `ModuleNode`,
`Group`, `Edge`; import real model classes for every `ModelNode` — the field
table is introspected, never hand-written).

Rules — each one exists because its violation is a known failure mode:

1. **Consolidate the vocabulary.** The skeleton's endpoint names are fragmented
   (the same stage under five names, function paths as labels). Merge them into
   one curated node set at object-view altitude: payload cards for the models
   that matter, stage terminals named by what happens ("constraint prep", not
   `_prepare_case_constraints`), artifacts by their file names. A raw
   `module.function` name may appear only inside evidence, never as a node label.

2. **Diamond criterion.** Elevate a gate to a `DecisionNode` only if its
   branches change what the consumer gets — different sections, products, or
   identity in the output. Write `question` as the domain question and
   `rationale` as WHY: what the branch protects or enables, citing the
   condition's `file:line`. Guards (dedup, null-fallback, per-row routing) are
   absorbed into a payload note ("empty when X", "None = case2-only") or left in
   evidence — never drawn.

3. **Every payload card gets a role.** `prose` states who writes it, who reads
   it, and when ("run config, the WRITER's source — built at job run time, read
   by the report reader"). Fill `notes` for the fields where you have something
   true to say (identity keys, gates, derived-after fields, dormant fields,
   sentinel meanings); leave the rest empty rather than padding.

4. **Stage the flow.** Group nodes into `Group`s with a `cadence` ("once per
   report", "per case", "job run time") so lifecycle reads off the page.

5. **Collapse outside the focus.** Everything traced but outside the focus
   becomes a `ModuleNode`: a name, one line of prose, and `products` — what it
   hands the rest of the pipeline. Its internals do not appear; boundary edges
   carry the product names.

6. **Connect or cut.** Declare `roots` (external inputs and the entry). Every
   node you emit must be reachable from a root through the edge set; if you
   cannot cite a connecting edge, cut the node. Dormant entities you keep are
   drawn dashed into their would-be consumer, labeled dormant — attached, never
   floating.

7. **Edges speak outcomes and products.** A decision's out-edges are outcomes
   ("yes — same day", "no"), never actions. Flow edges name what crosses (the
   product, the field), in domain words; an edge label that reads like a call
   signature is wrong.

8. **Cite or confess.** Every WHY, every role sentence, every note is either
   backed by a `file:line` you list in the claims table, or ends with
   `^[inferred]`. Never silently upgrade an inference to a fact. You may read
   any file in the file list to ground a claim; you may not add edges the
   skeleton does not contain — a missing edge is a finding to report back, not
   to draw.

Return ONLY:

1. the spec file content in one ```python fenced block, then
2. a claims table in one ```json fenced block:

```json
{
  "claims": [
    {"claim": "<the prose/WHY/note text>", "citation": "path/file.py:123 or INFERRED"}
  ],
  "cut": ["<node you dropped and why, one line each>"],
  "missing_edges": ["<relationship you believe exists but the skeleton lacks — for re-tracing>"]
}
```
