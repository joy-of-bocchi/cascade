# demo/ — demonstration material

Example models used by `python -m cascade.vocab --demo` and the backend demos;
`trips/` is the complete worked example (engine + specgen + render + vocab +
rundump + lints, with committed drift-gated artifacts — see its README). May
import from `cascade`; `cascade` must never hard-depend on this directory (vocab
degrades gracefully when it is absent). Not part of the vendored copy.
