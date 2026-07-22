# Deferred report-schema changes (next major bump)

This collects schema changes that improve the machine-readable report but change
its **output shape**, so they cannot ship under the frozen-output constraint of
ordinary refactors. They require a major schema bump (`major: 1` → `2`) because
they move or restructure existing fields. Do them together to spend the one
breaking bump well.

## Why these are deferred

The report IR/rendering refactor (2026-07) held text and JSON byte-identical.
Anything that alters the emitted JSON — even a strictly more precise structure —
changes `schema.major`/`minor` in every report and regenerates the bundled
`src/metapathology/report.schema.json`, which `test_bundled_json_schema_is_current`
pins. That is a deliberate, coordinated release, not an incidental cleanup.

## Change 1 — nest event-specific fields under `data`

**Today:** each timeline event flattens its envelope and its kind-specific
fields into one object:

```json
{ "id": "event:12", "seq": 12, "kind": "find_spec_call",
  "finder_id": "0x…", "found": true, "origin": "…", … }
```

This forces the kitchen-sink `EventJSON` TypedDict (`total=False`, every field
optional), which cannot type-check which fields belong to which kind.

**Target:** nest the kind-specific payload under `data`:

```json
{ "id": "event:12", "seq": 12, "kind": "find_spec_call",
  "data": { "finder_id": "0x…", "found": true, "origin": "…", … } }
```

Then:

- `EventJSON` becomes `EventEnvelopeJSON` (`id`, `seq`, `kind`, `data`) plus a
  discriminated union of per-kind payload TypedDicts for `data`.
- `kind` can become a real `Literal` discriminant (see `EVENT_KIND` in
  `_report_events.py`, already the single source of truth).
- Each builder in `_report_json.py` returns its own fully-typed payload
  TypedDict — no shared-base merge, no `# type: ignore`, no kitchen sink. The
  per-kind field groups already exist as the `_json_<kind>` builders and the
  `_report_events` registry; lifting them into `data` is mechanical.

## Implementation checklist

- **`src/metapathology/_report_schema.py`** — replace the flat `EventJSON` with
  `EventEnvelopeJSON` + per-kind payload TypedDicts (one per `EVENT_KIND` value)
  + a `data` union. Consider narrowing `EventBaseJSON.kind` to a `Literal`.
- **`src/metapathology/_report_json.py`** — each `_json_<kind>` builder returns
  its payload TypedDict; the wrapper builds `{id, seq, kind, data: payload}`.
  Payload builders drop the inlined base keys.
- **Version bumps** (keep these three in lockstep):
  - `_SCHEMA_MAJOR` / `_SCHEMA_MINOR` in `_report_json.py` (→ `2` / `0`).
  - `schema_properties["major"|"minor"]` consts in
    `scripts/generate_report_schema.py` (lines ~33–34).
  - the `$id` URL in that generator (`…/schema/report-1.0.json` → `report-2.0.json`).
- **Regenerate** the bundled schema: `python scripts/generate_report_schema.py`
  (writes `src/metapathology/report.schema.json`), then confirm
  `test_bundled_json_schema_is_current` passes.
- **Consumers/tests** — update every JSON test that reads `event["<field>"]`
  directly to `event["data"]["<field>"]` (e.g. `test_structured_report.py`,
  `test_timeline.py`), and the schema docs in `docs/report.md` / `docs/api.md`.

## Other candidates to bundle into the same major bump

Evaluate these while the breaking window is open (each is optional):

- Tighten now-`str` schema fields to `Literal`s where the vocabulary is closed
  (e.g. event `kind`, `outcome`, `boundary`, resolution `category`,
  finding `severity`/`kind`). Purely a schema-precision gain.
- Reconsider any other flattened envelopes that share the same "base + optional
  grab-bag" shape and would read more precisely nested.

## References

- Registry / single source of truth: `src/metapathology/_report_events.py`.
- Per-kind builders: `_json_<kind>` in `src/metapathology/_report_json.py`.
- Memory note: `future-nest-json-event-data`.
