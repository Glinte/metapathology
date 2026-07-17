<h1 align="center">metapathology</h1>

<p align="center">
  Diagnose Python import hooks without changing import outcomes.
</p>

<p align="center">
  <a href="https://pypi.org/project/metapathology/"><img src="https://img.shields.io/pypi/v/metapathology.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/metapathology/"><img src="https://img.shields.io/pypi/pyversions/metapathology.svg" alt="Supported Python versions"></a>
  <a href="https://github.com/Glinte/metapathology/actions/workflows/test.yml"><img src="https://github.com/Glinte/metapathology/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://glinte.github.io/metapathology/"><img src="https://github.com/Glinte/metapathology/actions/workflows/docs.yml/badge.svg" alt="Documentation"></a>
  <a href="https://sonarcloud.io/summary/new_code?id=Glinte_metapathology"><img src="https://sonarcloud.io/api/project_badges/measure?project=Glinte_metapathology&amp;metric=alert_status" alt="Quality gate status"></a>
</p>

> [!IMPORTANT]
> This project is mostly AI generated (for now). I do understand what it does and how it works, but I only skimmed the code. I can attest the explanations in README is accurate though.

`metapathology` is a stdlib-only diagnostic tool for CPython imports. It
reports:

- which finder located each imported module;
- where code changed `sys.meta_path` or `sys.path_hooks`, with a stack trace;
- how `sys.path_importer_cache` entries were added, removed, or replaced; and
- which modules were found without going through the usual `sys.path` and
  `sys.path_hooks` search.

This is useful when two packages customize imports and one prevents the other
from seeing a module. The original example was
[beartype#556](https://github.com/beartype/beartype/issues/556): an editable
install finder located a module before `beartype.claw`'s path hook could see
it.

Full guides are published at
[glinte.github.io/metapathology](https://glinte.github.io/metapathology/).
They cover usage, import-system concepts, report interpretation, the library
API, limitations, and development.

## Usage

Primary: run your program under observation, no code changes needed —

```console
$ python -m metapathology myscript.py --my-args
$ python -m metapathology -m pytest tests/
$ python -m metapathology --report diagnostic.json myscript.py
$ python -m metapathology --color always myscript.py
$ python -m metapathology --no-path-hook-monitoring myscript.py
$ python -m metapathology --no-importer-cache-monitoring myscript.py
```

A text report is printed to standard error by default. `--report PATH` writes
an atomic file instead; files default to JSON, or select text with
`--report-format text`. Text color defaults to `auto`: ANSI colors are used for
TTY output except when `NO_COLOR` is nonempty or `TERM=dumb`, and redirected
output and files remain plain. Use `--color always` or `--color never` to
override detection. `METAPATHOLOGY_COLOR=auto|always|never` configures automatic
reports when no CLI or API value is supplied. An installed `metapathology`
command provides a shorter equivalent:

```console
$ metapathology myscript.py --my-args
```

A nonexistent script path is rejected as a CLI error before monitoring starts,
so it produces neither a diagnostic report nor a report file.

Prefer `python -m metapathology`: it guarantees the hooks land in the same
interpreter and venv as the code under investigation.

Metapathology adds the current process ID to every automatic report filename,
so concurrent workers write different files. For process 1234,
`diagnostic.json` becomes `diagnostic.1234.json`. To control the position, put
`{pid}` in the path: `diagnostic-{pid}.json` becomes
`diagnostic-1234.json`.

### Frozen and embedded applications

An executable must activate metapathology inside its own interpreter; placing
the executable under `python -m metapathology` observes the wrong process.
Generate a startup file for PyInstaller, Nuitka, cx_Freeze, or an embedded
interpreter:

```console
$ python -m metapathology.frozen_bootstrap generate pyinstaller metapathology-rthook.py
```

Set `METAPATHOLOGY_REPORT=diagnostic.txt` and
`METAPATHOLOGY_REPORT_FORMAT=text` when running the built application.
The same `METAPATHOLOGY_*` variables used by the ordinary CLI configure all
generated integrations. See the [frozen application guide](https://glinte.github.io/metapathology/frozen/)
for each freezer's generator and build commands.

The generated activation catches diagnostic startup failures so a missing
package, invalid setting, or unwritable report destination does not prevent the
application from starting. Reports identify the integration and the honest
observation boundary: freezer machinery established before the startup hook is
part of the initial snapshot, not observed history.

Reports can contain argv values, paths, origins, and stack
filenames; treat them as potentially sensitive diagnostic artifacts.

### Observe later `.pth` startup hooks

The normal wrapper starts after Python processes site-packages. For an
explicit diagnostic environment on CPython 3.10--3.14, install an inert,
environment-gated bootstrap into that interpreter's site-packages:

```console
$ python -m metapathology.site_bootstrap install
$ METAPATHOLOGY_EARLY_BOOTSTRAP=1 METAPATHOLOGY_REPORT=diagnostic.json python myscript.py
$ python -m metapathology.site_bootstrap remove
```

This can record mutations made by `.pth` files sorted after
`00_metapathology_early.pth` in the same site-packages directory. It cannot
observe files processed earlier, a previously processed site directory, or
startup under `-S`. The activation and report variables are inherited by
child processes, which write separate PID-safe reports. Ordinary package
installation never creates this file, and startup remains inactive unless
`METAPATHOLOGY_EARLY_BOOTSTRAP=1` is present. See the
[usage guide](https://glinte.github.io/metapathology/usage/#observe-later-pth-files)
for status, custom-directory, ordering, and version details.

[Library API](https://glinte.github.io/metapathology/api/), for when a wrapper
isn't possible (notebooks, embedded interpreters, "I can only touch
`conftest.py`"):

```python
import metapathology

monitor = metapathology.install()  # as early as possible
```

Opt-in deep diagnostics can capture delegated path-hook, path-entry finder,
and modern loader creation/execution calls when passive evidence is insufficient:

```console
python -m metapathology --deep myscript.py
```

These mechanisms are disabled by default because they put monitor code inline
with imports and path-hook wrapping changes callable identity. Enable only the
needed switches in a controlled reproduction; the report warns when any are
active.
Each mechanism also has a `--deep-*` / `--no-deep-*` switch. Capture settings
use the same explicit-value, environment, then default precedence in the CLI
and library API; see the usage guide for the corresponding
`METAPATHOLOGY_*` variables.
Loader instrumentation shadows existing `create_module` and `exec_module`
methods only; it never adds missing methods or wraps legacy `load_module`.
Every meta-path entry is also audited without calling its protocols. Reports
identify modern, legacy-only, protocol-less, and indeterminate finder
contracts and link entries added during monitoring to their mutation stacks.
Legacy-only is a compatibility risk: CPython 3.12 removed the `find_module`
fallback, and direct meta-path consumers may require `find_spec` even earlier.
Exact import outcomes profile CPython's private `_find_and_load` boundary and
are supported on CPython 3.10--3.14. They cover the installing thread and
threads subsequently created through `threading`, but not already-running or
low-level `_thread` threads. Ordinary `sys.modules` cache hits bypass this
boundary and remain unobserved. Activation is refused when a profiler is
already installed; every report states the applied coverage or refusal reason.

`install()` is idempotent and returns the process-wide `Monitor`. By default,
it prints a report to standard error when Python exits. To control when or
where the report is written, disable that callback and report explicitly:

```python
import sys

import metapathology

monitor = metapathology.install(report_at_exit=False)
try:
    import package_under_investigation
finally:
    metapathology.write_report(sys.stdout)
    metapathology.uninstall()
```

`uninstall()` is idempotent. It restores plain `sys.meta_path` and
`sys.path_hooks` lists, removes the
wrappers from finders, and unregisters the exit report. The CPython audit hook
cannot be removed, so it remains installed as an inactive no-op. Recorded
events remain available after uninstalling.

For integration with another diagnostic or test harness:

- `metapathology.render_report(format="text", color=False)` returns plain text,
  explicitly colored text, or stable schema-versioned JSON as a string;
- `metapathology.write_report(destination=None, format="text", color="auto")` writes to
  standard error, a text stream, or an atomic file path;
- `metapathology.get_monitor()` returns the process-wide monitor, or `None`
  before the first call to `install()`; and
- `monitor.events()` returns a capture-order snapshot of the structured
  `ImportAuditStart`, `FindSpecCall`, `StandardFinderCall`, `DeepDiagnosticCall`,
  meta-path, path-hooks, importer-cache, and `InternalError` records. Path-hook records use `ImportObjectRef` values
  containing captured identity and safe type/name metadata. Mutating the
  returned list does not alter the monitor.

Calling `write_report()` or `render_report()` before `install()` raises
`RuntimeError`. There are no runtime dependencies. See the complete
[usage guide](https://glinte.github.io/metapathology/usage/) for CLI behavior,
lifecycle details, and integration examples.

## Reading the report

The report leads with a verdict. The first lines state how the target
finished and what the evidence says, and numbered finding blocks follow with
their supporting evidence indented beneath them. Here is a trimmed real
report for a script whose custom finder claimed a namespace package with a
truncated search path, making `synthesis_ns.child` unimportable:

```text
== metapathology report ==
target outcome: raised ModuleNotFoundError for 'synthesis_ns.child' (exit status 1); the failed module appears under unresolved imports below
verdict: 2 findings (1 actionable, 1 warning); most severe is [namespace-truncation] 'synthesis_ns' — see [1]
report guide: https://glinte.github.io/metapathology/report/
...
-- findings (2: 1 actionable, 1 warning) --
[1] [correlated] 'synthesis_ns.child' failed after TruncatingFinder truncated its parent namespace
    omitted location 'installed\synthesis_ns' contains 'installed\synthesis_ns\child'
    supporting events: #11, #15
    [namespace-truncation] 'synthesis_ns': descendant failure is correlated with a narrower namespace route from TruncatingFinder
        locations available only through the standard path probe: 'installed\synthesis_ns'
        structural evidence: sys.path_hooks unchanged since install; importer cache unchanged for the captured search path
    corroborating signals: meta path short circuit
    why it matters: submodules that exist only under the omitted locations cannot be imported while the narrower namespace stays cached
    this is an actionable finding based on correlated evidence; limitations: ...
```

Below the findings, neutral resolution-route divergences compare each
captured custom claim with an independent report-time standard-path probe
without declaring either route correct, unresolved imports are joined to the
target's failure, and the chronological evidence timeline plus per-mechanism
detail sections carry the full supporting evidence. Findings are tiered as
`actionable`, `warning`, or `informational`, and every one states its
evidence level and limitations — they are diagnostic leads, not necessarily
defects. The [report guide](https://glinte.github.io/metapathology/report/)
explains every section and finding category.

## Resource use

The monitor retains every recorded resolution start, finder call (including
copied spec, package-path summaries, and constant-size target-module states), mutation,
reassignment, cache diff, and internal error so the final report is
exhaustive. Its memory use therefore grows with import activity for as long as
monitoring remains enabled; there is currently no event limit or silent
dropping policy. For a long-running or import-heavy process, call
`write_report()` and `uninstall()` once the behavior of interest has been
captured. Stack traces are stored for `sys.meta_path` and `sys.path_hooks`
changes, which makes mutation records more expensive than audit-start or
finder-call records.
Importer-cache monitoring retains an install snapshot, a rolling latest
snapshot, and every diff. Full cache observations happen at path-hook mutation
boundaries and report time; the import audit hook performs only an O(1)
identity-and-length cache fingerprint check. Each observed builtin-import
resolution start also retains an `ImportAuditStart`; imports already present
in `sys.modules` remain cache hits and create no new records. The published reference matrix predates
audit-start retention and is labeled as a pre-T3 baseline in the performance
guide.

Report-time route analysis retains two routes and one comparison for every
reported custom winner and performs one synchronous standard-path probe for
each. It has no fixed cap, silent dropping, retries, queue, or background
worker; its cost grows with the number of custom-claimed modules present at
report time. JSON exposes this policy as the `resolution_route_analysis`
capture mechanism.

See [limitations and resource behavior](https://glinte.github.io/metapathology/limitations/)
and the reproducible [speed and memory benchmarks](https://glinte.github.io/metapathology/performance/)
before monitoring a long-running process.

## How Python finds an imported module

The [detailed import walkthrough](https://glinte.github.io/metapathology/concepts/)
connects these Python mechanisms to what metapathology can record.

Python first checks the requested module's fully qualified name in
`sys.modules`. If an entry is already there, Python returns that same module
object without calling any finder or executing the module again. During a
first import, Python adds the new module to this cache before executing its
code so recursive imports do not load a second copy. If the name is absent,
Python reads [`sys.meta_path`](https://docs.python.org/3/library/sys.html#sys.meta_path),
a list of objects called *finders*. It asks each finder in order whether it can
locate the module by calling the finder's `find_spec()` method.

A finder returns `None` when it cannot locate the module, and Python moves on
to the next finder. A finder that can locate the module returns a *module
spec*: a small object describing the module and how to load it. Python stops
asking other finders once it receives a spec. The Python documentation calls
this process [the meta path](https://docs.python.org/3/reference/import.html#the-meta-path).

One of Python's standard finders is `PathFinder`. It searches `sys.path` for a
top-level module or a package's `__path__` for a submodule. For each path item,
it uses a cached path-entry finder or calls the factories in `sys.path_hooks`
to create one. Path hooks therefore do not receive every import themselves.
A custom meta-path finder placed before `PathFinder` can return a spec first,
preventing `PathFinder` and the path-entry finders created by those hooks from
seeing the module. See
[the path-based finder](https://docs.python.org/3/reference/import.html#the-path-based-finder)
for the full protocol.

## How it works

`metapathology` observes that process with five mechanisms:

1. It registers a [`sys.addaudithook()`](https://docs.python.org/3/library/sys.html#sys.addaudithook)
   callback for CPython's [`import` audit event](https://docs.python.org/3/library/audit_events.html#audit-events).
   The monitor records that an uncached resolution started, including an
   immediate `sys.meta_path` snapshot, but the event does not say whether the
   import succeeds or which finder wins. It also lets the monitor recover if
   less-common code assigns an entirely new list to `sys.meta_path` or
   `sys.path_hooks`.
2. It temporarily replaces `sys.meta_path` with a compatible `list` subclass.
   This records the usual changes as they happen: additions, removals,
   replacements, clearing, and reordering, with a stack trace showing where
   each change came from. Newly added finders are prepared for call recording.
3. It temporarily replaces `sys.path_hooks` with a compatible `list`
   subclass and records the same list operations without wrapping or calling
   hook factories. Pass `monitor_path_hooks=False`, or use
   `--no-path-hook-monitoring`, to leave this list untouched.
4. It wraps each finder's existing `find_spec()` method. The wrapper records
   whether the finder returned `None` or a module spec and the target name's
   `sys.modules` identity immediately before and after delegation, then returns
   the same result. It does not supply a spec of its own or load a module.
5. It passively snapshots `sys.path_importer_cache` at installation, around
   observed path-hook mutations, and at report time. Pass
   `monitor_importer_cache=False`, or use `--no-importer-cache-monitoring`, to
   disable this mechanism. The cache object is never replaced or wrapped.

The standard `BuiltinImporter`, `FrozenImporter`, and `PathFinder` entries are
classes shared by CPython, so metapathology deliberately leaves them unwrapped.

At exit, the report represents the recorded result as a captured resolution
route and compares it with an independent `PathFinder.find_spec()` standard
path probe over the search path captured with the claim. The comparison
preserves not-found and semantic differences as neutral evidence. It also
compares install and report-time path-hook order and relevant importer-cache
identities as historical structural evidence; those historical objects are
never called by that comparison.

## Caveats

- CPython only (relies on the `import` audit event and import-system
  internals).
- Normal CLI/API monitoring begins when `metapathology` is installed. Finders
  and hooks added earlier by `.pth` files appear in the initial snapshots. The
  opt-in early-site bootstrap can move this boundary into site initialization,
  subject to directory ordering and version limits.
- The temporary changes to finders, `sys.meta_path`, and `sys.path_hooks` are reversed by
  `uninstall()`. Python does not provide a way to remove an audit hook, so the
  installed callback remains as an inactive no-op after uninstalling.
- Low-level module replacement is visible only when the responsible mutable
  loader is reached after `--deep-loaders` is active. Standard class loaders,
  legacy `load_module()` calls, activity before installation, and
  uninstrumentable loaders remain outside this boundary. A foreign
  non-dictionary `sys.modules` replacement is reported as unavailable rather
  than called.
- This tool changes `sys.meta_path` while it is running. Use it for debugging,
  not as part of an application's normal runtime.
- Importer-cache diffs are passive boundary observations, not a complete log
  of short-lived changes between boundaries.

See the complete [limitations guide](https://glinte.github.io/metapathology/limitations/)
for timing, visibility, replay, cleanup, and memory boundaries.
