# Cascade: pipelines whose pictures can't lie

## The problem

Every data pipeline accumulates three kinds of rot, and they're all the same rot.

A value gets computed in two places. Someone needed `velocity` in a new report,
didn't know a factory already derived it, and wrote `distance / time` again. The two
definitions agree today. Then one gets a fix and the other doesn't, and now the
pipeline gives two answers to one question and no error anywhere.

A diagram gets drawn by hand. It's accurate the week it's made. Six months later it
shows a stage that was deleted and misses two that were added, and everyone knows it,
so nobody trusts it, so nobody updates it. The picture becomes decoration.

A naming convention lives in a wiki page. The page says the canonical name is
`upgrade_cost_usd`; the code has `cost`, `total_cost`, and `upgrade_cost` in three
models, and each was somebody's reasonable local choice.

Same failure each time: two copies of one truth, and no machine holding them together.

## The bet

Cascade's bet is that the typed models can be the *only* copy, and everything else a
projection of them.

The pipeline is a registry of typed stages over frozen Pydantic models: every value
is declared on exactly one model, produced by exactly one stage, and carried
downstream by nesting or inheritance, never by re-declaring and copying. That
structure is machine-readable, so the things that normally rot are generated instead
of maintained:

- **The diagram is a projection.** The pipeline registry seeds the diagram spec:
  stages, gates, payload types, nesting. Regenerating is one command, and a test
  fails if the committed picture doesn't match the code. The diagram can't drift
  because nobody draws it.
- **The vocabulary is a projection.** Every field name, type, owner, and description
  across every model the pipeline touches, swept into one committed TSV. Not a wiki
  page. A new name shows up as a diff line in review.
- **The run report is a projection.** One flat table per run: every value, which
  stage produced it, which gate skipped. Debugging starts from a complete account
  instead of print statements.

Projections stay honest because regeneration is deterministic — run it twice, the
bytes match — and drift-gated: committed artifacts are compared byte-for-byte in the
test suite.

## The part code can't do, done honestly

Structure is checkable; meaning isn't. Whether `speed` is a new quantity or a
rename of `velocity` is a judgment call, and encoding judgment as regex and fuzzy
matching produces confident nonsense. Cascade splits the work accordingly.

The deterministic layer catches what's provable: a field declared on two models
(decllint), a value renamed while being copied downstream (carrylint, reading the
source), the same formula defined at two sites under different names (derivlint,
fingerprinting normalized expressions), a second stage trying to produce an
already-produced type (the engine refuses at build time). Where the linters can't
see — an opaque `**kwargs`, a positional constructor — they warn instead of passing,
so the unchecked surface stays visible.

The judgment layer is deliberately not code. When a genuinely new name enters the
vocabulary, the committed TSV's diff is the review event: a human or a model looks
at one line, next to the existing names and their descriptions, and answers the only
question that matters. The machinery's job is to make that question small, visible,
and impossible to skip — not to answer it.

## Why this matters more now

Agents write a growing share of pipeline code, and agents are exactly the
contributor who doesn't know a derivation already exists somewhere. The discipline
is what makes agent-written changes safe to accept: the linters catch the copy-paste
re-derivation mechanically, the vocabulary diff surfaces the new name for review,
and the regenerated diagram shows the structural change without anyone asking the
agent to describe its work. The pipeline explains itself.

It also makes the whole thing portable. Cascade is three artifacts: the discipline
(markdown, language-free), a reference implementation (Python, pydantic + stdlib,
about 3k lines), and a behavioral spec (the test suite). An agent given the first
and third can re-derive the second in another language and prove conformance by
translating the tests. The Python engine in this repo was itself written that way —
clean-room, from the spec.

## It exists and runs

Not a proposal. The repo has a typed stage engine (gated stages, nested pipelines,
snapshot hooks, a write-once store), the diagram seeder and Mermaid/D2 renderers,
the four naming-discipline linters, the vocabulary generator, and the run dumper —
102 tests, including one that extracts the README's quickstart block and executes
it, so the front page is guaranteed to work. `demo/trips/` is the whole kit
assembled in one folder with drift-gated committed artifacts. A subset is already
vendored in a large production codebase, where the same seeding discipline generates
the pipeline documentation.

Adoption is a copy, not an install: the `cascade/` directory drops into any repo
(PORTING.md covers the options), and the tools accept any engine with the right
shape — they type against structural Protocols, so you can keep your own engine and
take only the linters, or the vocabulary, or the diagrams.

Start with the README quickstart: define two models and one stage, and you get the
run, the diagram, and the vocabulary from the same forty lines.
