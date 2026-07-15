# Library API

The public package is typed and exposes the following names. Prefer the
[CLI workflow](usage.md) unless the target cannot be wrapped. The package's
[`__all__` declaration][public-exports] is the machine-readable source of
truth for the supported public API.

[public-exports]: https://github.com/Glinte/metapathology/blob/main/src/metapathology/__init__.py

## Lifecycle and reporting

### `install(*, report_at_exit=True, report_destination=None, report_format=None, monitor_path_hooks=True) -> Monitor`

Installs the process-wide monitor and returns it. Repeated calls return and
enable the same monitor. Only activity after installation can be observed.
When `report_at_exit` is true, a report is registered using Python's
[`atexit` mechanism][atexit]. `report_destination` selects an automatic file;
otherwise `METAPATHOLOGY_REPORT` is consulted before defaulting to standard
error. `report_format` accepts `"text"` or `"json"`; API values override
`METAPATHOLOGY_REPORT_FORMAT`, and files default to JSON while standard error
defaults to text.

`monitor_path_hooks` controls path-hook observation and defaults to true. A later
true value enables it if initially disabled; false does not disable an active
mechanism. Use `uninstall()` for cleanup.

Automatic reports always include the current process ID in their filename. For
process 1234, `report.json` becomes `report.1234.json`. When the configured path
contains `{pid}`, that marker is replaced instead, so `report-{pid}.json`
becomes `report-1234.json`. Explicit `write_report()` paths are used unchanged.
Write failures are recorded and suppressed by automatic reporting so they do
not replace the target's exit status.

[atexit]: https://docs.python.org/3/library/atexit.html

### `uninstall() -> None`

Disables monitoring, restores plain `sys.meta_path` and `sys.path_hooks`
lists, removes shadows from
instrumented finders, and unregisters the exit callback. Repeated calls are
safe. The CPython audit callback cannot be removed and remains as an inactive
no-op.

### `get_monitor() -> Monitor | None`

Returns the process-wide monitor, or `None` if `install()` has never been
called.

### `write_report(destination=None, *, format="text") -> None`

Writes the current report to standard error, a supplied text stream, or a
string/path-like file destination. Paths are exact for explicit calls and are
written through a same-directory temporary file plus `os.replace()`. Explicit
I/O failures are recorded as `InternalError` and re-raised. Raises
`RuntimeError` before the first installation and `ValueError` for an unknown
format.

### `render_report(*, format="text") -> str`

Returns text or JSON, including its trailing newline. JSON currently uses the
experimental `metapathology.report` schema version 0.2. Its shape may change
throughout schema 0.x; schema 1.0 will be reviewed once the evidence model stabilizes.
Raises `RuntimeError` before the first installation and `ValueError` for an
unknown format. Ordinary generation failures degrade to a valid failure
report rather than propagating.

## `Monitor`

Applications receive a `Monitor` from `install()`; directly constructing
competing monitors is not supported because import state is process-global.

- `enabled: bool` — whether observation is currently active.
- `initial_meta_path: tuple[str, ...]` — finder display names at installation.
- `path_hooks_enabled: bool` — whether path-hook observation is currently active.
- `initial_path_hooks: tuple[ImportObjectRef, ...]` — identities and safe
  type/name metadata captured when path-hook monitoring was enabled.
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
across record types. `MonitorEvent` is the union of the six event classes.

### `ImportObjectRef`

Carries an import object's numeric identity, safe type name, and optional
callable name. It never retains or stringifies the foreign object.

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

### `PathHooksMutation`

Records the operation, added and removed `ImportObjectRef` values, resulting
hook order, thread, and mutation stack. Monitoring never calls the hooks.

### `PathHooksReassignment`

Records old and new hook references plus the import, thread, and stack at the
next import audit event that detected direct replacement.

### `InternalError`

Records the instrumentation location, exception type, and optional sanitized
message. Monitor-generated records leave the message unset when foreign error
stringification would be unsafe.

Record fields contain primitive snapshots rather than live foreign import
objects. See [How it works](concepts.md) for why this is necessary inside the
import machinery.
