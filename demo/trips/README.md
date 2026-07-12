# demo/trips — the whole kit, assembled

One small trip-billing pipeline that exercises every piece of cascade together. Read
it in this order:

1. **`models.py`** — five frozen entities. Every field carries a
   `Field(description=...)` and `Leg` has a `@computed_field`; those sentences are not
   decoration — they flow into `out/vocabulary.tsv` and the diagram, which is the
   co-location discipline (prose lives on the object it describes).
2. **`pipeline.py`** — the engine surface in ~100 lines:
   - a *nested* pipeline: `summarize_trip` is a child pipeline lifted in via
     `include(...)`, so its stage shows up as `summarize_trip/summarize_trip_details`
     in the run report;
   - a *gated pair*: `prepare_manual_approval` / `prepare_auto_approval` both produce
     `ApprovalPacket` under opposite `when=` predicates and share one `question=` —
     the engine enforces their runtime exclusivity, and the seeded diagram draws them
     as one decision;
   - a plain terminal stage (`create_invoice`) consuming the winner.
3. **`generate.py`** — regenerates every committed artifact:

   ```
   uv run --with pydantic python -m demo.trips.generate
   ```

4. **`out/`** — the committed projections, each a drift gate (byte-compared by
   `tests/test_demo_trips.py`, regenerating must be a no-op):

   | artifact | what it shows |
   |---|---|
   | `pipeline.mmd` | the seeded spec merged with a small authored overlay, rendered to Mermaid |
   | `pipeline.view.html` | the same diagram wrapped for pan/zoom viewing (`open` it) |
   | `vocabulary.tsv` | every field name the pipeline can touch, discovered FROM the pipeline (`vocabulary_from_pipeline`) — descriptions included |
   | `rundump.txt` | one demo run flattened: every value, its producer path, the skipped gate with its reason |

The demo code is itself held to the discipline: `tests/test_demo_trips.py` runs
carrylint, derivlint, and decllint over these files and fails on any blocking
violation. If you edit the demo and a linter objects, the demo is wrong, not the
linter.
