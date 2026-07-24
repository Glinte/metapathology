# JSON reports

Use JSON for automation, archival comparison, or attaching evidence to an
issue:

```console
python -m metapathology --report diagnosis.json app.py
```

Library integrations can inspect the structured report directly:

```python
import metapathology

document = metapathology.get_report()
for finding in document["findings"]:
    inspect_finding(finding)
```

The bundled schema is available as package data:

```python
from importlib.resources import files

schema_text = (
    files("metapathology")
    .joinpath("report.schema.json")
    .read_text(encoding="utf-8")
)
```

## Contract

The file follows
[JSON Schema draft 2020-12](https://json-schema.org/draft/2020-12). A minor
schema release may add fields or vocabulary; consumers should ignore fields
they do not use and handle unknown enum values. Removing a field or changing
its meaning requires a new major schema version.

Important top-level collections are:

| Key | Meaning |
| --- | --- |
| `findings` | Problems, risks, and notes derived from evidence |
| `explanations` | Causal summaries linked to findings |
| `finder_results` | Observed results and report-time check results |
| `finder_result_comparisons` | Structured differences between results |
| `import_searches` | Correlated uncached import searches |
| `timeline` | Raw enabled-mechanism events in capture order |
| `checks` | Check status, prerequisites, work, and capacity |
| `capture` | Enabled mechanisms and completeness labels |
| `snapshots` | Install-time and report-time import state |
| `diagnostics` | Monitoring and report-generation errors |

IDs are document-scoped. Follow references instead of joining by list position.
Examples include `event:4`, `search:2`, `result:1`, and
`finder-api:0x...`. Object identities are meaningful only in the process that
produced the report.

## Evidence language

- `observed`: recorded while the target ran.
- `correlated`: joined from multiple observed records.
- `current_state`: inspected while the report was built.
- `current_state_check`: produced by a report-time check.
- `explored`: returned by a candidate that the real import skipped. It does not
  predict which finder would have won.
- `inferred` or `inferred_from_state`: derived conservatively without a direct
  event for the claim.

## Failure behavior

Ordinary report-generation failures produce a valid
`report_status: "generation_failed"` document with a diagnostic entry. A
partial report uses `report_status: "partial"`.

Human wording is not part of the JSON contract. Integrations should use schema
fields, stable kinds, and references.

JSON keeps absolute paths, command-line arguments, stack filenames, and object
identities. Review it before attaching it to a public issue.
