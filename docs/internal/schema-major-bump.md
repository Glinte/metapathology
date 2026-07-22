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
- **Snapshots nest their payload under `data`.** Each snapshot is now
  `{id, kind, phase, data}`; `data` is a `kind`-discriminated union
  (`MetaPathSnapshotDataJSON`, `PathHooksSnapshotDataJSON`,
  `ImporterCacheSnapshotDataJSON`) so the `entries` element type follows `kind`
  and `non_string_keys` lives only on the importer-cache payload.
- **Findings nest their evidence under `data`.** The flat `total=False` grab-bag
  (`claim`, `deep_call`, `structural_comparison`, `attempt_refs`, `route_refs`,
  `route_comparison_ref`, `finder_contract_ref`, `module_state_baseline`) became
  a `detail`-tagged `FindingEvidenceDataJSON` union mirroring the
  `FindingEvidence` sum type in `_report_model` (each `Finding.kind` maps to one
  evidence family). `_json_finding` dispatches on the variant.
- **Mechanism `name`/`completeness` and the remaining open `str` fields are now
  `Literal`s.** `MechanismName` (16 names); `MechanismCompleteness` (static
  labels plus the three `_monitor_model` status vocabularies); finder-contract
  `category`/`observation`; loader-inventory `inspection`/`loader_source`; and
  the deep/replay event `outcome` fields (mirroring `DeepOutcome` /
  `SpeculativeReplayOutcome`). Internal producers were tightened in lockstep.
  `test_real_report_conforms_to_bundled_schema` now validates real output
  against the bundled enums.

## Intentionally still open

- **`MechanismJSON` stays `MechanismBaseJSON` + `total=False` extras**
  (`coalesced`, `comparison_count`, `observations`). Only 2 of 16 mechanisms
  carry extras, so a full per-mechanism `data` union would be 14 empty payloads —
  not worth the churn. The vocabularies (`name`, `completeness`) are closed.
- **`FindingEvidenceJSON.outcome` stays `str`.** For `finder_side_effect` it
  embeds the raising exception type (`raised:<type>`), so it is not a closed
  vocabulary.

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
