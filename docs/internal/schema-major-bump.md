# Report-schema major bumps

The report IR/rendering refactors held text and JSON byte-identical, so anything
that alters the emitted JSON shape — even a strictly more precise structure —
must ride a coordinated major bump. Each such bump changes `schema.major`,
regenerates the bundled `src/metapathology/report.schema.json` (pinned by
`test_bundled_json_schema_is_current`), and updates every consumer at once.

## Shipped in 2.0

The `1.3` → `2.0` bump restructured the report for precise typing:

- **Timeline events nest their payload under `data`.** Each event is now an
  envelope `{id, seq, kind, data}`; the kind-specific fields live in `data`.
  `EventJSON` is the envelope and `EventDataJSON` is the discriminated union of
  per-kind payload TypedDicts (`ImportAuditStartDataJSON`, …). Each
  `_json_<kind>` builder returns its own fully-typed payload; `_json_event`
  wraps it. `kind` is a real `Literal` (`EventKind`).
- **Events reference their attempt by `data.attempt_ref`** (`"attempt:N"`),
  replacing the raw int `attempt_id` — now uniform with every other `*_ref`.
- **`summary.counts`** nests the per-severity totals (`actionable`, `warning`,
  `informational`) so they cannot drift from the `FindingSeverity` vocabulary.
- **Literal tightening.** Closed-vocabulary fields that were `str` are now
  `Literal`s, mirrored at module scope in `_report_schema.py` (the generator
  resolves them at runtime): event `kind`; snapshot `kind`/`phase`; module
  `state`; spec `locations_state`; protocol `availability`; finding
  `severity`/`kind`/`subject.kind`/evidence `level`; explanation
  `kind`/`confidence`; resolution `category`/`state_phase`/`evidence_level`;
  route `kind`/`purpose`/`status`/`evidence_level`; `search_path_kind`/`_phase`;
  import `progress`/`presence`; deep `boundary`; mutation `op`; loader inventory
  `evidence`/`phase`; mechanism `overflow_policy`/`shutdown`.

## Still deferred (next major bump)

These share the "base + optional grab-bag" shape but were left flat to keep the
2.0 blast radius bounded. Bundle them when the next breaking window opens:

- **`MechanismJSON`** — `MechanismBaseJSON` plus `total=False` extras
  (`coalesced`, `comparison_count`, `observations`) that only apply to specific
  mechanisms. A per-mechanism `data` payload (or discriminated union) would
  type-check which extras belong where. Its `name` and `completeness` fields are
  open-ended today; a closed vocabulary would let them become `Literal`s.
- **`SnapshotJSON`** — `entries` is a `kind`-discriminated union and
  `non_string_keys` only applies to `importer_cache`. The same envelope+`data`
  nesting used for events would read more precisely.
- **`FindingJSON` / `FindingEvidenceJSON`** — `total=False` grab-bags whose
  members depend on `kind`; could be nested the same way.
- **Remaining open `str` fields** — finder-contract `category`/`observation`,
  loader-inventory `inspection`/`loader_source`, event `outcome`. Left as `str`
  because their vocabularies are not confidently closed; tighten only alongside
  a schema-validation test that checks real output against the bundled enums
  (no such test exists yet — worth adding before more aggressive tightening).

## Bump checklist (for the next one)

- **`_report_schema.py`** — restructure the TypedDicts; add any new module-level
  `Literal` vocabularies (they must resolve at runtime for the generator).
- **`_report_json.py`** — update the affected builders and **both** document
  shapes: `_build_json_document` and the `failed_json_document` fallback (a
  second literal copy of the schema shape — keep it in lockstep).
- **Version bumps in lockstep:**
  - `_SCHEMA_MAJOR` / `_SCHEMA_MINOR` in `_report_json.py`.
  - `schema_properties["major"|"minor"]` in `scripts/generate_report_schema.py`.
  - the `$id` URL in that generator (`…/schema/report-N.0.json`).
- **Regenerate** `python scripts/generate_report_schema.py`; confirm
  `test_bundled_json_schema_is_current`.
- **Consumers/tests** — grep `event\["` and `["summary"]` across `tests/`
  (2.0 touched ~11 files) plus `docs/report.md` and `docs/api.md`.

## References

- Registry / single source of truth: `src/metapathology/_report_events.py`.
- Per-kind builders: `_json_<kind>` in `src/metapathology/_report_json.py`.
