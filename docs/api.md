# Library API

The public package is typed and exposes the following names. Prefer the
[CLI workflow](usage.md) unless the target cannot be wrapped. The package's
[`__all__` declaration][public-exports] is the machine-readable source of
truth for the supported public API.

[public-exports]: https://github.com/Glinte/metapathology/blob/main/src/metapathology/__init__.py

## Lifecycle and reporting

### `install(*, report_at_exit=True) -> Monitor`

Installs the process-wide monitor and returns it. Repeated calls return and
enable the same monitor. Only activity after installation can be observed.
When `report_at_exit` is true, a report is registered for standard error at
interpreter exit using Python's [`atexit` mechanism][atexit].

[atexit]: https://docs.python.org/3/library/atexit.html

### `uninstall() -> None`

Disables monitoring, restores a plain `sys.meta_path`, removes shadows from
instrumented finders, and unregisters the exit callback. Repeated calls are
safe. The CPython audit callback cannot be removed and remains as an inactive
no-op.

### `get_monitor() -> Monitor | None`

Returns the process-wide monitor, or `None` if `install()` has never been
called.

### `report(file=None) -> None`

Writes the current report to the supplied text stream, or standard error when
omitted. Raises `RuntimeError` before the first installation.

### `render_report() -> str`

Returns the current report, including its trailing newline. Raises
`RuntimeError` before the first installation. Report generation is designed to
degrade to an error message rather than propagate an internal reporting
failure.

## `Monitor`

Applications receive a `Monitor` from `install()`; directly constructing
competing monitors is not supported because import state is process-global.

- `enabled: bool` — whether observation is currently active.
- `initial_meta_path: tuple[str, ...]` — finder display names at installation.
- `baseline_modules: frozenset[str]` — `sys.modules` names at installation.
- `events() -> list[MonitorEvent]` — capture-order snapshot of all records.
- `skipped_finders() -> list[tuple[str, str]]` — finder display name and the
  reason it could not be wrapped. The normal CPython class finders are
  included with an explicit "expected" reason; see [Reading the
  report](report.md#header) for why they are deliberately left unchanged.

`Monitor.install()` and `Monitor.uninstall()` implement the same idempotent
lifecycle for the process-wide instance, but the module-level functions are
the intended entry points.

## Event records

All records are immutable, slotted classes with a standard field-based repr.
They deliberately use identity equality and do not define positional pattern
matching. Their shared `seq` field provides a single chronological order
across record types. `MonitorEvent` is the union of the four record classes.

### `FindSpecCall`

Records the module name, finder type and identity, whether the finder claimed
the module, loader type, origin, captured search path, exception type if the
finder raised, and thread name.

### `MetaPathMutation`

Records the list operation, added and removed finder display names, resulting
contents, thread name, and captured stack.

### `MetaPathReassignment`

Records the import during which replacement was detected, old and new finder
display names, triggering thread, and detection stack. The fields describe
detection time rather than the unknowable assignment moment.

### `InternalError`

Records the instrumentation location, exception type, and optional sanitized
message. Monitor-generated records leave the message unset when foreign error
stringification would be unsafe.

Record fields contain primitive snapshots rather than live foreign import
objects. See [How it works](concepts.md) for why this is necessary inside the
import machinery.
