# Configuration migration

This is a clean breaking change. There are no deprecated API, CLI,
environment, or JSON aliases.

## Library API

Replace flat monitoring keywords with:

- `CaptureConfig` for core capture;
- `DetailedCaptureConfig` only for individual detailed mechanisms; and
- `AnalysisConfig` for report-time checks.

For the common “capture everything detailed” case:

```python
metapathology.install(
    capture=metapathology.CaptureConfig(detailed=True),
)
```

The removed speculative replay feature is now the displaced-finder check. Its
results use the shared `finder_results` pipeline.

## CLI and environment

Use the options and environment names in
[Configuration reference](configuration.md). Old `MONITOR_*`,
`SPECULATIVE_REPLAY`, `--deep-*`, and probe names are rejected.

## JSON

Schema 3.0 uses:

- `import_searches`;
- `finder_results` and `finder_result_comparisons`;
- `checks`;
- `program_outcome`; and
- the event, finding, evidence, and severity vocabularies documented in
  [JSON reports](json.md).

Regenerate consumers from the bundled schema. Do not translate old keys inside
metapathology; handling migration in the consuming integration keeps schema
failures visible.
