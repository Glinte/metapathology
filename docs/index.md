# Diagnose import-hook contention

`metapathology` answers a narrow debugging question:

> Which finder handled this import, and did that stop another import hook from
> running?

It is for Python developers investigating behavior that changes when editable
installs, test runners, tracing, runtime type checking, or other import hooks
are combined.

```console
pip install metapathology
python -m metapathology your_script.py
python -m metapathology -m pytest tests/
```

The target runs normally. The report goes to standard error at exit.
Metapathology observes and reports; it never supplies a module spec or changes
an import result.

## Choose the next page

- [Get started](usage.md) to run a reproduction and save a report.
- [Read a report](report.md) to interpret findings and current-state checks.
- [Choose capture](capture.md) when the default evidence is insufficient.
- [Start earlier](startup.md) when a finder arrived from a `.pth` file or
  frozen bootstrap.
- [Configure metapathology](configuration.md) for CLI, API, and environment
  settings.
- [Consume JSON](json.md) for integrations and the schema contract.
- [Check limitations](limitations.md) before drawing a conclusion from missing
  evidence.

The [library API](api.md) is intended for environments where the CLI wrapper is
impractical. Most users do not need it.
