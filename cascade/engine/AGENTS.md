# cascade/engine/ — pipeline execution

Typed stage registry, build validation, and the run loop. The error taxonomy IS the
API surface — new failure modes get a typed error class, never a silent fallback.
`rundump.py` is the one module in the repo allowed to import `engine` concretely;
everything else types against Protocols. House rules: ../../AGENTS.md.
