# cascade/lint/ — enforcement, one file per check

The layered naming discipline lives here (decllint, carrylint, derivlint, vocab's
gate) plus the graph checks (speclint, structlint, d2lint). The two source-reading
linters (carrylint, derivlint) are specified language-neutrally in ../../DIALECT.md —
change behavior there and here together. Design law: untraceable surface warns,
never silently passes. House rules: ../../AGENTS.md.
