# cascade/ — the vendorable package

This directory is the unit a host codebase copies (see ../PORTING.md). Everything in
it: relative imports only, pydantic + stdlib only, deterministic output, zero
references to external codebases. House rules: ../AGENTS.md. Subpackages route
further: engine/ (execution), spec/ (schema layer), render/ (cosmetic), lint/
(enforcement), astflow/ (AST tracer evidence).
