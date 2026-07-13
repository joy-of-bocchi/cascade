# Cascade

> Adopting this in another codebase, or porting it to another language? Read `PORTING.md` (vendor, don't install; the three artifacts) and `DIALECT.md` (the language-neutral spec of the source-reading linters).

A discipline and toolkit for **single-source-of-truth, reactive typed dataflows**: model a pipeline as frozen Pydantic entities where every value is defined once and derived through a DAG, so changing one value recomputes everything downstream — then project that pipeline into a diagram (auto-generated ER + hand-authored decision trees). One typed source renders to two backends, Mermaid or D2. Mermaid is the default; D2 is the precision option. Generation and viewing stay separate from a set of linters that enforce the structural properties.

## Quickstart

Setup is a copy, not an install: put the `cascade/` directory in your repo (see
`PORTING.md` for the vendoring options). The only dependency is pydantic.

```python
from pydantic import BaseModel, ConfigDict

from cascade.engine import Pipeline, run
from cascade.render import render
from cascade.spec import AuthoredExtra, Overlay, fragment_from_pipeline, merge
from cascade.vocab import render_tsv, vocabulary_from_pipeline


class Trip(BaseModel):
    model_config = ConfigDict(frozen=True)
    distance: float
    hours: float


class Summary(BaseModel):
    model_config = ConfigDict(frozen=True)
    velocity: float


pipeline = Pipeline(root_types=(Trip,))


@pipeline.stage(output=Summary, section="Summarize")
def summarize(trip: Trip) -> Summary:
    return Summary(velocity=trip.distance / trip.hours)


built = pipeline.build()

result = run(built, {Trip: Trip(distance=120.0, hours=2.0)})
result.final_store[Summary]                      # Summary(velocity=60.0)

spec = merge(
    [fragment_from_pipeline(built, "trips")],
    overlay=Overlay(),
    extra=AuthoredExtra(),
)
mermaid_text = render(spec)                      # paste into any markdown

vocabulary = render_tsv(vocabulary_from_pipeline(built))
# commit as vocabulary.tsv; a new name in its diff is the review event
```

Stage inputs come from function annotations: each parameter must be a Pydantic model
type, `list[Model]`, or `tuple[Model, ...]`; the engine pulls them from the run store,
calls stages in dependency order, and refuses to overwrite an existing value. Use
`when=` for gated stages and `include(...)` to nest a pipeline as one stage.

Lint from the command line (each exits 1 on a blocking violation):

```
uv run --with pydantic python -m cascade.lint.carrylint src/*.py --models Trip,Summary
uv run --with pydantic python -m cascade.lint.derivlint src/*.py --models Trip,Summary
uv run --with pydantic python -m cascade.lint.d2lint diagram.d2
uv run --with pydantic python -m cascade.vocab --demo
```

## The seam: one spec, two backends

The source of truth is a `DiagramSpec` — a typed graph of model / decision / terminal nodes, edges, and groups (in `cascade/spec/d2spec.py`). A `RenderBackend` turns that spec into diagram text and reports how to rasterize it to SVG:

```
DiagramSpec ──► RenderBackend.render_spec ──► diagram text ──► SVG
   (typed)        (Mermaid or D2)               (.mmd / .d2)
```

`cascade/lint/structlint.py` validates the graph regardless of backend: each backend parses its own rendered text back into a shared `structlint.Graph`, and the cycle / dangling-edge / isolated-node checks run on that neutral graph. Switch backends and the structural guarantees still hold; the only thing that changes is the syntax and what the syntax can express.

Render through the neutral surface in `cascade/render/render.py`:

```python
from cascade.render import render, render_er, lint, get_backend
from cascade.render.backends import D2Backend

render(spec)                       # Mermaid (the default)
render(spec, D2Backend())          # D2, by explicit backend
render(spec, get_backend("d2"))    # D2, by name
render_er([RootModel])             # Mermaid classDiagram from Pydantic roots
lint(render(spec))                 # structural report on the rendered text
```

`build_d2(spec)` (in `cascade/render/d2gen.py`) and `build_er_d2(roots)` (in `cascade/render/d2er.py`) remain as explicit D2 entry points for callers that always want `.d2`.

## Registry-to-spec seeding

`cascade/spec/specgen.py` turns a typed pipeline registry into a `DiagramSpec` seed. Pass `fragment_from_pipeline(...)` any object with `root_types` and `stages`; each stage can expose `name`, `input_types`, `output_type`, `when`, `section`, `collapse`, `marker`, `question`, `reads_external`, and `sub_pipeline`. Missing optional attributes use neutral defaults, so small registries do not need adapter classes.

Use `merge([fragment], overlay=Overlay(...), extra=AuthoredExtra(...))` to combine the seeded graph with authored nodes, edges, prose, notes, and group cadence overrides. Merge checks stale overlay keys, duplicate ids, dangling references, and authored model-to-model edges that need `intra_stage=True`.

## The map

Rendering, viewing, and linting are separate by design — they share only the
neutral schema and never import each other. The why is in `docs/INTENT.md`.

shared substrate     cascade/spec/d2spec.py        typed spec (DiagramSpec/nodes/edges) + introspection helpers; neutral
rendering            cascade/render/render.py      neutral surface: render / render_er / lint / get_backend (defaults to Mermaid)
                     cascade/render/backends/      base.py (the RenderBackend seam) + mermaid.py + d2.py
                     cascade/render/d2gen.py       build_d2(spec) -> .d2  — explicit-D2 HAND-AUTHORED decision tree w/ inline model tables
                     cascade/render/d2er.py        build_er_d2(roots) -> .d2  — explicit-D2 AUTO ER from real models (transitive closure)
                     cascade/render/wrap.py        svg -> infinite pan/zoom .view.html (D2 viewing path)
linting              cascade/lint/structlint.py    renderer-neutral graph checks (cycle/dangling/isolated, Tarjan + Kahn)
                     cascade/lint/speclint.py      validate(spec): cycle / frozen / referential / type-flow
                     cascade/lint/d2lint.py        structural checks on a .d2 file via the D2 backend (cycle/dangling/isolated)
                     cascade/lint/decllint.py      single-declaration: each field declared on ONE model (inheritance-aware)
                     cascade/lint/carrylint.py     a bare carry keeps its name: C(speed=o.velocity) FAILs (AST)
                     cascade/lint/derivlint.py     one derivation per value: same fingerprint at two sites FAILs (AST)
                     cascade/lint/namelint.py      one-name + provenance via an explicit canonical registry
vocabulary           cascade/vocab.py              generated canonical name registry from model_fields (TSV artifact + diff)
run view             cascade/engine/rundump.py     dump_run/dump_store: every field, value, producer stage, status
```

`structlint` is the renderer-neutral core: each backend parses its own output into a `structlint.Graph` and the same checks run. `cascade/lint/d2lint.py` is the `.d2`-file CLI front end over it; `lint(text)` in `cascade/render/render.py` does the same for whichever backend rendered the text.

## Where everything else lives

| doc | question it answers |
|---|---|
| `docs/PITCH.md` | the case for cascade — what rots without it and why the bet works; start here to convince someone |
| `docs/INTENT.md` | why is it built this way? invariants, the naming discipline, what was deliberately not built |
| `docs/RENDERING.md` | Mermaid vs D2, the D2 performance rules, viewing (`viewBox`, `foreignObject`, ELK) |
| `PORTING.md` | adopting in another codebase (vendor, don't install); porting to another language |
| `DIALECT.md` | the exact, language-neutral rules of the source-reading linters |
| `AGENTS.md` | working rules for agents editing this repo (every directory has a router) |
| `diagram-flow/` | the diagram-production skill: tracer/skeptic/author/registry contracts |
