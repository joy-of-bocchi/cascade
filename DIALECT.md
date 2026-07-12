# The checkable dialect: carrylint and derivlint

Language-neutral specification of the two source-reading linters. Everything here is
stated over parse-tree shapes, not Python's `ast` module, so a port can implement the
same rules against its own compiler API. The Python implementations (`cascade/lint/carrylint.py`,
`cascade/lint/derivlint.py`) and their tests are the reference; where this document and the tests
disagree, the tests win.

Both linters share one design law: **untraceable surface warns, never silently
passes.** A checker that quietly skips what it cannot read reports "clean" on code it
never looked at. Every rule below therefore sorts constructs into exactly three bins:
IN the dialect (checked), OUT with a warning (visible, non-blocking), or IRRELEVANT
(provably not a carry/derivation site).

## Shared machinery

**Model constructor call.** A call whose callee's trailing simple name is in the
caller-supplied set of model names: `Order(...)` and `models.Order(...)` both match
`Order`; a computed callee (subscript, call result) matches nothing. The linters take
the name set as input — they do not resolve imports.

**Function scope.** Each function body is analysed alone, excluding the bodies of
functions/lambdas nested inside it (those are analysed separately with their own
parameters). derivlint also records the directly enclosing class of a method, for
typing `self`.

**Single-assignment alias.** A local name bound exactly once in the function body by a
plain `name = EXPR` (or annotated `name: T = EXPR`) statement. Names bound more than
once (any binding counts: reassignment, loop target, augmented assignment) are not
aliases. Self-referential bindings (`x = x + 1`) are excluded. Aliases let the linters
see through one common idiom — hoisting a subexpression to a local — without building
a dataflow engine.

## carrylint — a bare carry keeps its name

Scans every keyword argument `dst=EXPR` of every model constructor call.

**Carry (IN, blocking on rename).** EXPR is a bare attribute chain rooted at a
parameter (`o.velocity`, `o.a.b.velocity`, `self.velocity`) or at an alias, or is a
bare reference to an alias of such a chain. The carried name is the chain's final
attribute (for a bare alias reference: the final attribute of the aliased chain).
If carried name != dst, that is RENAMED_CARRY — the one blocking violation.

**Transformation (IRRELEVANT).** Anything computed: arithmetic, calls, constants,
comprehensions, conditionals. A transformation mints a new quantity; naming it is
decllint/vocab jurisdiction, not carrylint's.

**Untraceable (OUT, warn).**
- Positional construction `Order(a, b)` — field mapping invisible: POSITIONAL_CONSTRUCTION.
- `**mapping` where the mapping is opaque (a name, a call result, a dict with computed
  keys): UNTRACEABLE_CARRY. Exception: a dict literal whose keys are all constant
  strings stays IN the dialect — each key/value pair is classified as if written
  `key=value`.

Exit code: fail only when a blocking violation exists; warnings alone pass.

## derivlint — one derivation per value

**Sites collected.**
1. Every keyword argument value in a model constructor call, in any function.
2. The return expression(s) of any method carrying the `computed_field` decorator
   (bare, called, or attribute-qualified; stacking with `property` is fine).

**Normalization** (per site, before comparing):
1. Inline single-assignment aliases to a fixpoint, capped at 10 passes (the cap only
   guards pathological chains; aliasing cycles cannot occur since self-referential
   bindings are not aliases).
2. Rewrite each parameter-rooted name to the parameter's annotated type name:
   `o.distance` with `o: Order` becomes `Order.distance`; `self` becomes the enclosing
   class name; an unannotated parameter becomes a shared placeholder. Type names are
   taken syntactically (trailing name of the annotation; a string forward-ref by its
   text; `list[Order]` reads as `list`).

**Participation filter.** Only expressions with derivation structure participate:
binary/boolean operations, comparisons, conditionals, comprehensions, or calls with at
least one argument. Bare carries, constants, and zero-argument calls are filtered out
(they are carrylint's or nobody's concern — this keeps the linter from crying wolf).

**Fingerprint.** The structural identity of the normalized tree (Python: `ast.dump`).
Two sites with equal fingerprints define the same derivation. A site is
(file, function, target field); the same fingerprint at two or more distinct sites is
DUPLICATE_DERIVATION (blocking) — across files, across names.

**Deliberate non-goals.** Commutativity is not assumed: `a / b` and `b / a` differ, as
do `a + b` and `b + a`. The linter catches copy-paste re-derivation, not re-proved
algebraic identities. And type identity is the point of normalization: the same formula
over different typed inputs (`Order.distance / Order.time` vs `Leg.distance /
Leg.time`) is two honest derivations, never a duplicate.

**Numbered names (blocking).** A field name matching `letters/underscores then digits`
(`velocity1`) is banned at all three surfaces: constructor keywords, `computed_field`
method names, and declared model fields (checked by introspection, attributed to the
declaring ancestor so an inherited field reports once).

## What a port must re-decide

The three-bin sorting is the invariant; the bin contents are Python-shaped and each
target language redraws them around its own idioms. Examples:

- TypeScript: object spread (`{...base, speed: o.velocity}`) is the `**` question —
  literal-with-known-keys stays IN, spread-of-expression warns. Destructuring
  (`const {velocity} = o`) is an alias form Python doesn't have.
- Go: struct literals with field names are IN; positional struct literals are the
  POSITIONAL_CONSTRUCTION warn; short variable declarations (`v := o.Velocity`) are the
  alias form.
- Any language: the alias definition ("bound exactly once, not self-referential")
  and the participation filter ("has derivation structure") must be restated over the
  target grammar, then locked with translated tests.

A port's DIALECT.md must state its bins explicitly. The failure mode to avoid is
silent narrowing: a port that only checks what was easy to parse, without warning on
the rest, violates the design law above.
