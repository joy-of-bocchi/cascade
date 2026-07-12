# Intent — why Cascade is the way it is

Moved intact from the front page; this is the *why* behind every mechanism. The
quickstart and map live in ../README.md; rendering guidance in RENDERING.md.

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

Neither backend checks anything. Each will draw a cycle, an FK to a missing PK, or a box that disagrees with the real model without complaint. If the diagram is the source of truth for a typed DAG, validate it — but keep validation a *separate concern* from generation and viewing. Determinism works wherever structure is explicit (graph checks, model introspection, declared-group collapse); grouping and fidelity-level are judgment — keep them declared in the source or decided by the model, not inferred by heuristics.

**Rendering, viewing, and linting are separate, by design.** The toolkit is split so the concerns never sit on one pipeline:

**One value, one name, one derivation — the layered naming discipline.** The invariant is that a quantity is declared once, never renamed on the way down, and never re-derived. No single check covers that, so it's held in layers, each catching what the previous can't see:

```
"one value, one name, one derivation"
      ├── declared once ──────── decllint      field name on ONE model
      ├── never renamed ───────── carrylint    C(speed=o.velocity) FAILs
      ├── never re-derived ────── derivlint    same fingerprint twice FAILs
      │                           + engine     write-once store, duplicate-producer build error
      ├── name is canonical ───── cascade/vocab.py generated registry; a NEW name in the
      │                                        diff is the review event ("is `speed`
      │                                        just `velocity`?") — judgment, not code
      └── visible/debuggable ──── cascade/engine/rundump.py flat dump: field, value, producer, status
```

- **`cascade/lint/carrylint.py`** — a *bare carry* (a constructor kwarg whose value is an attribute chain, `C(velocity=o.trip.velocity)`, incl. single-assignment aliases) must keep its field name; a renamed carry is a blocking FAIL. Transformations (`speed=o.velocity * dt`) are new quantities and may take new names. Opaque `**kwargs` and positional construction are warned as untraceable — the fence that keeps the checkable dialect honest.
- **`cascade/lint/derivlint.py`** — the same computation must not be *defined* twice. Every derivation expression (constructor kwargs, `computed_field` returns) is normalized — aliases inlined, parameter roots rewritten to their annotated type names — and fingerprinted; one fingerprint at two distinct sites is a blocking FAIL, even across files and under different names. `Order.distance / Order.time` never collides with `Leg.distance / Leg.time` (types are the identity), and commutativity is deliberately not assumed (catches copy-paste re-derivation, not algebra). Numbered field names (`velocity1`) are banned outright.
- **`cascade/vocab.py`** — the canonical name list is *generated*, never maintained: `build_vocabulary(models)` sweeps `model_fields` (owner attributed via decllint's MRO walk, types rendered with the same helper the diagrams use) into a committed `vocabulary.tsv`. `check_stale` keeps it fresh in CI; `diff_names` makes a new name a visible, reviewable PR event — the one moment where the semantic question "is this a new quantity or an alias?" gets asked, by an agent or a human, against a short list with types and descriptions.
- **`cascade/engine/rundump.py`** — the "why is this weird?" view: `dump_run(built, result)` renders every value in the final store one row per field (`Trip.distance  12450.0  load_trip  SUCCESS 0.2ms`), roots first, sub-pipeline producers as `parent/child` paths, and skipped stages with their reasons — a complete, legible account of the run.

**`cascade/lint/decllint.py` is the minimal, type-first name discipline — prefer it.** `check_single_declaration(models)` introspects the models and flags any field name *declared* on more than one class (attributing each field to the class in its MRO that actually declares it, so inheriting a field counts once, re-declaring it counts twice). That single check enforces both **defined-once-carried-down** (a value lives on one model; downstream nests or inherits it, never re-declares it) and **one-name-per-quantity** (the same name on two unrelated models = one name used for two things). No registry to maintain. `cascade/lint/namelint.py` is the heavier alternative when you want an *explicit* canonical registry with declared `derived_from` provenance; reach for it only if you need provenance the models don't already encode via `@computed_field` / factory signatures. It passes on nesting and inheritance, and fails on a re-declared field.

`d2gen` imports no linter and no linter imports `d2gen`; both sides depend only on `d2spec`. Lint a spec when you want to — generation never invokes a linter, and a linter never generates. (Run order is the caller's choice: typically `validate(spec)` then `build_d2(spec)`, but they're independent calls.)

**`cascade/lint/speclint.py`** — `validate(spec)` runs the spec-level checks: **cycle**, **frozen** (every model node `frozen=True`), **referential** (edge/group references resolve), **type-flow** (a declared edge payload must be producible by the source model). In-degree > 1 is fine — a join/fan-in is normal in a dataflow DAG — so there is no single-producer check; `frozen` + `acyclic` + `type-flow` are the pipeline invariants.

**`cascade/render/d2gen.py`** — the *hand-authored decision-tree* side. `build_d2(spec)` only; each model node's table is introspected from `model_fields`, so a box can't disagree with its class, but the graph (decisions, edges, grouping) is authored because the control logic isn't in the types.

**`cascade/render/d2er.py`** — the *auto-generated ER* side. `build_er_d2(roots)` introspects the real Pydantic models: each model → a `sql_table`, and every field whose type references another model (directly, optional, in a `list`/`dict`, or in a union) → a column-level relationship edge with cardinality read from the type (`1` / `0..1` / `*`). It transitively closes from the roots you pass, so a single root yields the whole reachable ER, always in sync with the code — no hand-authoring, can't drift. Gotcha: field names that are D2 reserved words (e.g. a field named `label`) are quoted in rows and edges so they don't break compilation.

**`cascade/lint/d2lint.py`** — extracts the directed graph from a `.d2` file (skipping code-block interiors so content arrows aren't mistaken for edges) and runs cycle detection (Tarjan SCC, FAIL), dangling-edge endpoints (FAIL), isolated nodes (WARN), plus a Kahn topo-order when acyclic. No model needed:

```
uv run --with pydantic python -m cascade.lint.d2lint diagram.d2   # exit 1 on a blocking (FAIL) violation
```

**One-name discipline + provenance (bundled): `cascade/lint/namelint.py`.** Enforces single-source-of-truth at the *field* level — SSA for your data, the discipline that no value is ever re-derived. A canonical **registry** names every quantity once (`base(name, type)` / `derived(name, type, from_=[...])`), and models are checked against it:

- **closed vocabulary** — every model field name is a registered canonical name (aliases like `cost` for `upgrade_cost_usd`, and typos, FAIL)
- **consistent typing** — a name has one canonical type everywhere it appears (same name, two types FAILs)
- **inputs-exist** + **acyclic** — the `derived_from` graph references only registered names and never derives a value from itself

Single-definition (no value re-derived) is *structural*: one registry entry per name, so a quantity can't have two derivations. The judgment "is this new field a new quantity or an alias?" is **not** coded — an unregistered field is reported and resolved once at registration (register it as new, or rename the field to the existing canonical). The registry is the ubiquitous-language dictionary.
