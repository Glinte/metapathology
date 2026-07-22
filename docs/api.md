# Library API

The public package is typed and exposes the following names. Prefer the
[CLI workflow](usage.md) unless the target cannot be wrapped. The package's
[`__all__` declaration][public-exports] is the machine-readable source of
truth for the supported public API.

[public-exports]: https://github.com/Glinte/metapathology/blob/main/src/metapathology/__init__.py

## Lifecycle and reporting

### `activate_frozen(integration, bootstrap_path) -> None`

Installs the process-wide monitor and records activation inside a supported
frozen or embedded startup boundary. `integration` accepts `"pyinstaller"`,
`"nuitka"`, `"cx-freeze"`, or `"embedded"`. Prefer the generated and
fail-open startup files described in the [frozen application guide](frozen.md)
over calling this function directly. Direct calls propagate invalid integration
and installation errors to the application.

### `install(*, report_at_exit=True, report_destination=None, report_text=None, report_json=None, report_color=None, monitor_path_hooks=None, monitor_importer_cache=None, monitor_sys_path=None, deep=None, deep_path_hooks=None, deep_path_entry_finders=None, deep_loaders=None, deep_import_outcomes=None, deep_import_calls=None, speculative_replay=None) -> Monitor`

Installs the process-wide monitor and returns it. Repeated calls return and
enable the same monitor. Only activity after installation can be observed.
When `report_at_exit` is true, a report is registered using Python's
[`atexit` mechanism][atexit]. `report_destination`, `report_text`, and
`report_json` each accept one path or a list of paths. `report_destination`
infers JSON from `.json`, text from `.txt` or `.text`, and text for the `-`
standard-error sentinel; other extensions require the format-specific
arguments. When no API destination is supplied, `METAPATHOLOGY_REPORT` is
consulted as an `os.pathsep`-separated destination list before defaulting to
one text report on standard error. All targets share one captured report
artifact.
`report_color` accepts `"auto"`, `"always"`, or `"never"` for automatic text
reports. API values override `METAPATHOLOGY_COLOR`; the default `"auto"` colors
TTY destinations unless `NO_COLOR` is nonempty or `TERM=dumb`. JSON never
contains ANSI escapes.

`monitor_path_hooks` controls path-hook observation and defaults to true. A later
true value enables it if initially disabled; false does not disable an active
mechanism. Use `uninstall()` for cleanup.
`monitor_importer_cache` has the same enable-later semantics and controls
passive `sys.path_importer_cache` snapshots and diffs.
`monitor_sys_path` opts into reversible list-mutation attribution and
import-boundary reassignment detection for `sys.path`; it defaults to false
unless `deep=True` and can also be set with
`METAPATHOLOGY_MONITOR_SYS_PATH`.

`deep=True` enables all five delegated deep mechanisms and `sys.path`
monitoring. Each `deep_*` argument can
override the umbrella independently for delegated path hooks, mutable
path-entry finders, mutable loaders, exact import outcomes, or
`builtins.__import__` calls. `deep_import_calls` wraps `__import__` to observe
every import statement, including `sys.modules` cache hits that no other
mechanism sees; the swap is chain-safe against other tools that also wrap
`__import__`, and `importlib.import_module()` bypasses it. Path-hook
wrapping changes callable identity; deep mechanisms put monitor code inline
with imports and should be reserved for controlled diagnostic runs.

`speculative_replay` is independent of `deep`. When enabled (or via
`METAPATHOLOGY_SPECULATIVE_REPLAY`), report generation replays a finder that a
`sys.path_importer_cache` change displaced for a path entry against a module
that later failed to resolve on that path, reporting whether the retained finder
returns a spec now. It performs at most one foreign `find_spec()` per selected
candidate, caps the report at 16 probes, never mutates import state, declines
reload-target lookups, and never claims the original import would have
succeeded. Because it recomputes each report, repeated reports repeat those
foreign calls.

Capture booleans resolve consistently: an explicit API value wins, then its
`METAPATHOLOGY_*` environment value, then the documented default. Accepted
environment booleans are `1/0`, `true/false`, `yes/no`, and `on/off`
(case-insensitive). `report_at_exit` remains API-only because it controls
callback ownership rather than captured evidence. Early bootstrap activation
remains environment-only because it must run before this API is importable.

Automatic reports always include the current process ID in their filename. For
process 1234, `report.json` becomes `report.1234.json`. When the configured path
contains `{pid}`, that marker is replaced instead, so `report-{pid}.json`
becomes `report-1234.json`. Explicit `write_report()` paths are used unchanged.
Write failures are recorded and suppressed by automatic reporting so they do
not replace the target's exit status.

[atexit]: https://docs.python.org/3/library/atexit.html

### `monitoring(*, monitor_path_hooks=None, monitor_importer_cache=None, monitor_sys_path=None, deep=None, deep_path_hooks=None, deep_path_entry_finders=None, deep_loaders=None, deep_import_outcomes=None, deep_import_calls=None, speculative_replay=None) -> ContextManager[Monitor]`

Defines a bounded monitoring region and yields the process-wide monitor. The
monitor is installed on entry and, if the region began from an inactive state,
uninstalled after the last nested or overlapping region exits. Cleanup also
runs when the block raises. If a monitor was installed explicitly before the
first region, `monitoring()` emits a `RuntimeWarning` and leaves it active
afterward; this makes the likely lifecycle mismatch visible without tearing
down instrumentation the region does not own.

Nested regions share the monitor. A nested region may enable another mechanism,
but mechanisms are not selectively disabled when that inner region exits; they
remain enabled until the shared installation ends. This matches the monotonic
enable-later behavior of `install()`.

The context manager does not register an atexit report because its cleanup
boundary occurs before process exit. Recorded evidence remains available after
the block, so call `write_report()` or `render_report()` afterward. Avoid mixing
manual `uninstall()` calls into active regions because manual lifecycle control
cannot participate in region ownership.

### `uninstall() -> None`

Disables monitoring, restores plain `sys.meta_path`, `sys.path_hooks`, and any
instrumented `sys.path` lists, removes shadows from
instrumented finders, and unregisters the exit callback. Repeated calls are
safe. The CPython audit callback cannot be removed and remains as an inactive
no-op.

### `get_monitor() -> Monitor | None`

Returns the process-wide monitor, or `None` if `install()` has never been
called.

### `write_report(destination=None, *, format="text", color="auto") -> None`

Writes the current report to standard error, a supplied text stream, or a
string/path-like file destination. Paths are exact for explicit calls and are
written through a same-directory temporary file plus `os.replace()`. Explicit
I/O failures are recorded as `InternalError` and re-raised. Raises
`RuntimeError` before the first installation and `ValueError` for an unknown
format or color mode. In `"auto"`, file paths and non-TTY streams remain plain;
`"always"` deliberately permits ANSI escapes in files.

### `render_report(*, format="text", color=False) -> str`

Returns text or JSON, including its trailing newline. JSON uses the stable
`metapathology.report` schema version 2.0. The bundled
`metapathology/report.schema.json` file defines its language-neutral shape;
`metapathology.ReportJSON` and `metapathology.ReportStatus` expose the Python
typing contract without eagerly importing the reporting implementation.
Returned text is plain by default because it has no destination to inspect;
pass `color=True` for ANSI styling. The flag has no effect on JSON.
Raises `RuntimeError` before the first installation and `ValueError` for an
unknown format. Ordinary generation failures degrade to a valid failure
report rather than propagating.

## `Monitor`

Applications receive a `Monitor` from `install()`; directly constructing
competing monitors is not supported because import state is process-global.

- `enabled: bool` — whether observation is currently active.
- `initial_meta_path: tuple[str, ...]` — finder display names at installation.
- `path_hooks_enabled: bool` — whether path-hook observation is currently active.
- `initial_path_hooks: tuple[ObjectRef, ...]` — identities and safe
  type/name metadata captured when path-hook monitoring was enabled.
- `importer_cache_enabled: bool` — whether passive importer-cache observation
  is currently active.
- `sys_path_enabled: bool` — whether opt-in `sys.path` mutation observation is
  currently active.
- `deep_diagnostics: tuple[str, ...]` — explicitly enabled inline delegation
  mechanisms.
- `standard_finder_status: str` — whether exact `PathFinder` result capture
  (`deep_import_outcomes`) is active, unavailable, unsupported, or inactive
  after cleanup.
- `initial_importer_cache: tuple[ImporterCacheEntry, ...]` — string-keyed
  cache entries captured when importer-cache monitoring was enabled.
- `baseline_modules: frozenset[str]` — `sys.modules` names at installation.
- `events() -> list[MonitorEvent]` — capture-order snapshot of all records.
- `skipped_finders() -> list[tuple[str, str]]` — finder display name and the
  reason it could not be wrapped. The normal CPython class finders are
  included with an explicit "expected" reason; see [Reading the
  report](report.md#header) for why they are deliberately left unchanged.

Lifecycle belongs to the module-level `install()`, `uninstall()`, and
`monitoring()` APIs. `Monitor` exposes captured evidence but does not remember
automatic report destinations, formats, or atexit policy.

## Event records

All records are immutable, slotted classes with a standard field-based repr.
They deliberately use identity equality and do not define positional pattern
matching. Their shared `seq` field provides a single chronological order
across record types. `MonitorEvent` includes the event classes below.

### `DeepDiagnosticCall`

Records one opt-in deep-diagnostic call with the mechanism, safe object
identity/type, module or path, outcome, exception type, and thread. The
`unobserved_reentrant` outcome means a nested call delegated normally while
the per-thread guard suppressed exact nested instrumentation. Modern mutable
loaders use `loader_create_module` and `loader_exec_module`; absent methods are
not added, and legacy `load_module` is not wrapped. Loader records also carry
the target's `ModuleCacheState` at entry and return or exception exit.

### `StandardFinderCall`

Records a `PathFinder` result captured by the opt-in reversible profiler
(`deep_import_outcomes`). It links the `SpecSummary` to the exact import
attempt and thread without modifying the shared `PathFinder` class. No record
is created when the profiler could not be activated; consult
`Monitor.standard_finder_status` and the report's stated fallback.

### `ImportAuditStart`

Records that CPython began uncached resolution for a module, together with the
thread name, immediate `sys.meta_path` identity and finder type names, and
constant-size identities/fingerprints from enabled path-hook and importer-cache
monitoring. It does not record an import outcome; success and failure remain
unknown without separate evidence.

### `ObjectRef`

Carries an observed object's numeric identity, safe type name, and optional
callable name. It never retains or stringifies the foreign object.

### `ModuleCacheState`

Carries one target name's constant-size `sys.modules` state: `unavailable`,
`missing`, explicit `none`, or `object` with a numeric identity and safe type
name. Dictionary subclasses are read through the built-in `dict`
implementation so overridden mapping methods do not run. Non-dictionary cache
replacements are not probed.

### `FindSpecCall`

Records the module name, finder type and identity, whether the finder found
the module, loader type, origin, captured search path and whether it represented
`sys.path` or a parent package path, a `SpecSummary`, the exception type if the
finder raised, and the thread name.
The record also carries `module_state_before` and `module_state_after`,
captured around the wrapped call. A changed pair does not reconstruct nested import
activity or temporary intermediate objects.

### `SpecSummary`

Contains only plain captured spec semantics: safe spec/loader identity,
origin, cached path, package and namespace status, copied search locations,
and explicit completeness state. Exact list and tuple locations are captured
during the finder call. Foreign location sequences are not iterated inside an
import.

### `ImporterCacheEntry` and `ImporterCacheReplacement`

An entry stores a path and either an `ObjectRef` or `None`; `None`
explicitly represents a negative cache entry. A replacement stores the path
and its before/after cached values.

### `ImporterCacheDiff`

Records additions, removals, replacements, omitted non-string-key counts,
the passive observation boundary, and thread name. It participates in the
shared event sequence.

### `MetaPathMutation`

Records the list operation, added and removed finder display names, resulting
contents, thread name, and captured stack.

### `MetaPathReassignment`

Records the import during which replacement was detected, old and new finder
display names, triggering thread, and detection stack. The fields describe
detection time rather than the unknowable assignment moment.

### `PathHooksMutation`

Records the operation, added and removed `ObjectRef` values, resulting
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
