# Library API

Prefer `python -m metapathology` when possible. It installs before target code
and handles target outcomes and automatic reporting. Use the API for notebooks,
embedded interpreters, or code that cannot be wrapped.

## Monitor a reproduction

```python
import metapathology

monitor = metapathology.install()
try:
    reproduce_problem()
finally:
    metapathology.write_report("diagnosis.txt")
    metapathology.uninstall()
```

`install()` is process-wide. Repeating it with the same resolved capture and
analysis settings returns the existing monitor. Changing those settings while
the monitor is active raises before import state is changed.

`uninstall()` restores ordinary `list` objects and removes owned finder
instrumentation. Python cannot remove a
[`sys.addaudithook()` hook](https://docs.python.org/3/library/sys.html#sys.addaudithook),
so the hook remains installed but becomes inert.

For code with a clear reproduction boundary, a context manager handles cleanup:

```python
with metapathology.monitoring() as monitor:
    reproduce_problem()

metapathology.write_report("diagnosis.json", format="json")
```

Nested and overlapping regions share the process monitor. A context that did
not create an existing installation does not remove it.

## Choose what to capture

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

Configuration records are immutable and value-comparable. Their fields are
tri-state: `True` enables a mechanism, `False` disables it, and `None` means
“use the environment or normal default.” In `DetailedCaptureConfig`, `enabled`
supplies the value for detailed fields left as `None`.

`unsafe_explore_import_branches=True` calls skipped finders and hooks during the
import. Use it only in a disposable process or container. Returned specs are
discarded, but other side effects are not undone. See
[Unsafe import-branch exploration](capture.md#unsafe-import-branch-exploration).

## Choose report-time checks

Analysis controls checks run while a report is built. Checks may call existing
finder code, so they are separate from passive capture:

```python
analysis = metapathology.AnalysisConfig(
    standard_path_check=True,
    displaced_finder_check=False,
)
```

Pass it to `install()` for the default policy or override one report:

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

A report override does not mutate the installed default.

## Produce a report

`destination=None` writes to standard error. A path is replaced atomically;
streams are written directly.

Calling `render_report()` or `write_report()` before installation raises
`RuntimeError`. I/O errors from an explicit `write_report()` call are re-raised.
Automatic exit reporting suppresses them so a diagnostic cannot replace the
target's exit behavior.

Automatic output can be configured through `install()` with
`report_destination`, `report_text`, `report_json`, `report_color`, and
`report_at_exit`.

## Inspect captured evidence

`monitor.events()` returns immutable event records copied from the monitor.
Public record names describe the observation directly, including
`ImportSearchStarted`, `MetaPathFinderCall`, `ImportMechanismCall`,
`ImporterCacheChange`, and `MonitoringError`.

Treat these records as low-level evidence. Integrations usually want the JSON
report instead.

Monitor properties report which capture mechanisms are active and why detailed
evidence may be unavailable. In particular,
`unsafe_import_branch_exploration_status` is `complete`, `partial`, `disabled`,
or `uninstalled`. Partial coverage means a profiler was already installed or a
prerequisite was disabled, so some skipped calls may be missing.

## Reference

The signatures and parameter descriptions below are generated from the library
source. Configuration fields accept `True`, `False`, or `None` unless stated
otherwise; `None` uses the corresponding environment setting or normal default.

### Lifecycle

::: metapathology.install

::: metapathology.monitoring

::: metapathology.uninstall

::: metapathology.get_monitor

### Reports

::: metapathology.write_report

::: metapathology.render_report

### Configuration

::: metapathology.CaptureConfig

::: metapathology.DetailedCaptureConfig

::: metapathology.AnalysisConfig

::: metapathology.Monitor
    options:
      show_signature: false
      members:
        - enabled
        - import_audit_enabled
        - meta_path_enabled
        - finder_attribution_enabled
        - path_hooks_enabled
        - importer_cache_enabled
        - sys_path_enabled
        - detailed_capture
        - import_results_capture_status
        - import_calls_capture_status
        - path_finder_capture_status
        - unsafe_import_branch_exploration_status
        - events
