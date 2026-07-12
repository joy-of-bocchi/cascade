# Working in this repo

Cascade is a discipline plus a reference toolkit for single-source-of-truth typed
dataflows. Read `README.md` for what everything is and why; `PORTING.md` for the
three-artifact framing (discipline / reference impl / behavioral spec); `DIALECT.md`
for the source-reading linters' exact rules. This file is only the working rules.

## Shape of the repo

- One package directory, `cascade/`, grouped by concern (engine / spec / render /
  lint), with relative imports inside it — that directory is the unit a host copies.
  Tests live in `tests/`, demo material in `demo/`; neither ships with the copy.
- No pip/PyPI packaging, ever: no setup.py/pyproject, no version, nothing installable.
  This repo is vendored (the `cascade/` directory is copied into a host codebase and
  owned there), never installed. Plain `__init__.py` files are fine — they are what
  make the copied directory importable from anywhere; publishing machinery is not.
- Dependencies: pydantic + stdlib. Do not add more.
- Seams are structural Protocols, not imports. Tools (`specgen.py`, `vocab.py`) must
  not import `engine.py`; they type against a Protocol of the pipeline shape. Keep new
  tools on the same footing. `rundump.py` is the sole engine-importing exception.
- Nothing here may reference any external codebase, company, or product. Examples use
  generic domains (orders, trips, customers).

## Code rules

- Comprehensive type hints: parameters, returns, locals, module-level values.
- Pydantic `BaseModel` (frozen, `extra="forbid"`) for structured data — never
  `@dataclass`. (Introspection helpers *reading* foreign dataclasses are fine.)
- All imports at module level. No imports inside functions, no exceptions.
- No `assert` outside test files; raise typed errors.
- Comments and docstrings describe present state only — no "was X, now Y", no
  references to past changes or removed designs.
- Deterministic output everywhere: sorted iteration, no timestamps, no randomness.
  "Deterministic" is proven by generating twice and diffing — byte-identical or it
  isn't.

## Design laws (violating these is a bug even if tests pass)

- One value, one name, one derivation. A quantity is declared on one model, carried
  down unchanged, never re-derived. The layered enforcement (decllint, carrylint,
  derivlint, vocab, engine store) is described in README.
- Semantic judgment is never encoded as heuristics. "Is `speed` an alias of
  `velocity`?" gets surfaced as a reviewable diff for a human or model; do not write
  regex/fuzzy-matching/scoring code to decide meaning.
- Untraceable surface warns, never silently passes. A checker that cannot read a
  construct reports it as un-checkable instead of skipping it.
- Generation, viewing, and linting stay separate. A generator never invokes a linter;
  a linter never generates; renderers are cosmetic and validation lives elsewhere.
- Prefer the smaller mechanism. Checks have been removed from this repo for
  over-modeling the problem; when in doubt, build less.

## Verify before claiming done

```
uv run --with pytest --with pydantic python -m pytest tests/ -q
ruff format . && ruff check .
uv run --with pydantic python -m cascade.vocab --demo            # CLI smoke: -m form works
```

The tests are the behavioral spec (see PORTING.md): a behavior change without a test
change is suspect, and a port or refactor conforms when the suite is green. New
behavior lands with tests in the matching `test_<module>.py`.

## CLAUDE.md

`CLAUDE.md` is a symlink to this file so Claude Code and codex read the same source.
Edit `AGENTS.md` only.
