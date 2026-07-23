# Library API

Prefer `python -m metapathology` when possible. It installs before target code
and handles target outcomes and automatic reporting. The API is for notebooks,
embedded interpreters, and code that cannot be wrapped.

## Install and remove

```python
import metapathology

monitor = metapathology.install()
try:
    reproduce_problem()
finally:
    metapathology.write_report("diagnosis.txt")
metapathology.uninstall()
```

The complete installation signature is:

```python
install(
    *,
    report_at_exit=True,
    report_destination=None,
    report_text=None,
    report_json=None,
    report_color=None,
    capture=None,
    analysis=None,
    unsafe_explore_import_branches=None,
) -> Monitor
```

`install()` is process-wide. With the same resolved configuration it is
idempotent. A different active capture or analysis configuration raises before
mutation.

`uninstall()` restores ordinary `list` objects and removes owned finder
instrumentation. Python does not provide a way to remove a
[`sys.addaudithook()` hook](https://docs.python.org/3/library/sys.html#sys.addaudithook),
so that hook remains installed but becomes inert.

`unsafe_explore_import_branches=True` calls skipped finders and hooks during the
import. Use it only in a disposable process or container. Returned specs are
discarded, but other side effects are not undone. See
[Unsafe import-branch exploration](capture.md#unsafe-import-branch-exploration).

## Scoped monitoring

```python
with metapathology.monitoring() as monitor:
    reproduce_problem()

metapathology.write_report("diagnosis.json", format="json")
```

Nested and overlapping regions share the process monitor. A context that did
not create an existing installation does not remove it.

## Capture configuration

Defaults need no object:

```python
metapathology.install()
```

Change core capture or enable all detailed capture with one object:

```python
metapathology.install(
    capture=metapathology.CaptureConfig(
        sys_path=True,
        detailed=True,
    )
)
```

Use a nested object only for fine-grained detailed capture:

```python
metapathology.CaptureConfig(
    detailed=metapathology.DetailedCaptureConfig(
        loaders=True,
        import_results=True,
    )
)
```

All configuration records are immutable and value-comparable. Fields are
tri-state: `True` enables a mechanism, `False` disables it, and `None` means
“use the environment or normal default.” In `DetailedCaptureConfig`,
`enabled` supplies the value for detailed fields that remain `None`.

## Analysis configuration

Analysis controls checks run while a report is built. Checks may call existing
finder code, so they are kept separate from passive capture:

```python
analysis = metapathology.AnalysisConfig(
    standard_path_check=True,
    displaced_finder_check=False,
)
```

Pass it to `install()` for the default policy or override one artifact:

```python
text = metapathology.render_report(
    analysis=metapathology.AnalysisConfig(checks=False)
)

metapathology.write_report(
    "diagnosis.json",
    format="json",
    analysis=metapathology.AnalysisConfig(displaced_finder_check=True),
)
```

An artifact override does not mutate the installed default.

## Reporting

- `render_report(format="text" | "json", color=False, analysis=None) -> str`
- `write_report(destination=None, format="text" | "json", color="auto", analysis=None) -> None`

`destination=None` writes to standard error. A path is replaced atomically.
Streams are written directly.

Calling either function before installation raises `RuntimeError`. I/O errors
from an explicit `write_report()` call are re-raised. Automatic exit reporting
suppresses them so a diagnostic cannot replace the target's exit behavior.

Automatic output can be configured through `install()` with
`report_destination`, `report_text`, `report_json`, `report_color`, and
`report_at_exit`.

## Monitor evidence

`monitor.events()` returns immutable event records copied from the monitor.
Public record names describe the observation directly, including
`ImportSearchStarted`, `MetaPathFinderCall`, `ImportMechanismCall`,
`ImporterCacheChange`, and `MonitoringError`.

Treat these records as low-level evidence. Integrations usually want the JSON
report instead.

Useful monitor properties include `enabled`, the six core `*_enabled` values,
`detailed_capture`, and the three detailed status properties. A status explains
whether exact import results, import-call capture, or aggregate `PathFinder`
capture was active or why it was unavailable.

`unsafe_import_branch_exploration_status` reports complete, partial, disabled,
or uninstalled coverage. Partial coverage means some skipped calls may be
missing because a profiler was already installed or a prerequisite was
disabled.
