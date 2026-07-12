# cascade/render/ — spec to picture, cosmetic only

Backends (Mermaid, D2), generators, and .view.html wrapping. Renderers never
validate and never invoke a linter — generation, viewing, and linting are separate
concerns by design. A backend proves its output by parsing it back into
`lint/structlint.py`'s Graph. House rules: ../../AGENTS.md.
