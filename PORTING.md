# Adopting and porting Cascade

Cascade is not a package and should not become one. A package fixes one way to use it;
this repo is meant to be copied into a host codebase, owned there, and reshaped freely.
This document is the adoption story: what the artifacts are, how to vendor them into
another Python codebase, and how to re-derive them in another language.

## What Cascade actually is: three artifacts

```
1. THE DISCIPLINE          markdown, language-free
   README.md               the invariants ("one value, one name, one derivation"),
   diagram-flow/*.md       the why behind every mechanism, the jurisdiction tree,
                           the run-shaped test, what was deliberately NOT built

2. THE REFERENCE IMPL      Python, pydantic + stdlib only
   cascade/engine/engine.py, cascade/spec/specgen.py, ONE worked example of the discipline — an answer,
   cascade/vocab.py, cascade/lint/,             not the definition
   the renderer stack

3. THE BEHAVIORAL SPEC     the test suite (test_*.py)
                           what any conforming implementation must do, stated
                           as executable facts; the conformance gate for ports
```

The split matters because only artifact 2 is Python. Artifacts 1 and 3 are the durable
ones: the markdown carries the *why*, the tests carry the *what*, and the Python is the
first *how*.

## Vendoring into another Python codebase

Copy the `cascade/` directory in (tests and demo stay behind — the host's suite is
the translated conformance tests, per below). The host owns the copy: edit it, delete half of it, rename things —
there is no upstream to appease. Three copy modes, by how much history you want:

- `cp -r` / `git archive` — one-shot, no strings attached.
- `git subtree add` — keeps the ability to pull upstream improvements later.
- `git clone` + `rm -rf .git` — a folder, not a repo.

Dependencies are pydantic and the standard library. There is no setup.py and nothing to
install.

### The seams are Protocols, not imports

The tools do not import the engine. `cascade/spec/specgen.py` and `cascade/vocab.py` each define a small
structural Protocol (`.stages`, `.output_type`, `.sub_pipeline`, `.root_types`) and
accept anything with that shape. A host that already has its own stage engine keeps it
and vendors only the tools — the engine just has to expose the same duck-typed surface.

Two consequences:

- You can take any subset: only the linters, only the vocabulary, only the renderer.
  Relative imports inside the package (`vocab` -> `spec.d2spec`, `lint.decllint`) are
  the only coupling; check the
  import lines at the top of each file you take.
- `cascade/engine/rundump.py` is the one exception: it imports `cascade.engine` concretely (it renders
  engine-specific run records — statuses, skip reasons, sub-runs). Vendoring rundump
  against a foreign engine means retyping those imports against your engine's
  equivalents.

## Porting to another language

The recipe: hand an agent artifact 1 (the discipline) and artifact 3 (the tests),
let it read artifact 2 as *an* implementation rather than *the* implementation, and have
it re-derive in the target language. Then translate the test suite; the port is done
when the translated suite is green. This is a proven move — `cascade/engine/engine.py` itself was
written clean-room from a behavioral spec plus the Protocols, without seeing the code it
mirrors.

Portability varies by module:

```
nearly mechanical          cascade/engine/engine.py    typed registry + topo sort + run loop; swap
                                        pydantic for the target's validation library
                                        (zod/valibot, serde, encoding/json + validator)
                           cascade/vocab.py            model introspection -> sorted TSV; needs only
                                        "list a type's fields with names/types/docs"
                           cascade/spec/specgen.py     graph walk over the pipeline shape + overlay
                                        merge; pure data transformation
                           cascade/engine/rundump.py   string table over run records
                           decllint     "which ancestor declares this field" — needs
                                        the target's inheritance introspection

dialect must be re-decided carrylint    both read the PARSE TREE of source code, and
                           derivlint    their rules are stated over syntax shapes; see
                                        DIALECT.md for the language-neutral spec and
                                        what a port must re-decide (e.g. spread
                                        operators in TypeScript, struct literals in Go)
```

The renderer stack (Mermaid/D2 backends) is already language-neutral at its output:
any port that emits the same Mermaid/D2 text gets the same diagrams and can reuse the
same structural lint by parsing its own output, which is how the Python side works too.

## Conformance

A port conforms when:

1. The translated test suite passes.
2. The invariants in README's intent section hold: values declared once, carried down
   unchanged, never re-derived; generation, viewing, and linting stay separate concerns;
   everything mechanical is deterministic and the semantic judgments (is `speed` an
   alias of `velocity`?) are surfaced for review, never encoded as heuristics.
3. For the AST linters: the port documents its own checkable dialect the way DIALECT.md
   does, including what it cannot trace — untraceable surface must warn, never silently
   pass.
