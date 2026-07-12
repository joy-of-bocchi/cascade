# cascade/spec/ — the neutral schema layer

`d2spec.py` (DiagramSpec + model introspection) and `specgen.py` (registry → spec
fragments). Tools here must NOT import the engine — they type against structural
Protocols of the pipeline shape, so any conforming engine works. Output must be
deterministic: regenerate twice, diff byte-identical. House rules: ../../AGENTS.md.
