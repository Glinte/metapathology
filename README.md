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
$ python -m metapathology --no-path-hook-monitoring myscript.py
$ python -m metapathology --no-importer-cache-monitoring myscript.py
```

A text report is printed to standard error by default. `--report PATH` writes
an atomic file instead; files default to JSON, or select text with
`--report-format text`. An installed `metapathology` command provides a shorter
equivalent:

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
`diagnostic-1234.json`. Frozen and embedded bootstraps can set
`METAPATHOLOGY_REPORT` and `METAPATHOLOGY_REPORT_FORMAT` before calling
`install()`. Reports can contain argv values, paths, origins, and stack
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

- `metapathology.render_report(format="text")` returns text or experimental
  schema-versioned JSON as a string;
- `metapathology.write_report(destination=None, format="text")` writes to
  standard error, a text stream, or an atomic file path;
- `metapathology.get_monitor()` returns the process-wide monitor, or `None`
  before the first call to `install()`; and
- `monitor.events()` returns a capture-order snapshot of the structured
  `ImportAuditStart`, `FindSpecCall`, meta-path, path-hooks, importer-cache,
  and `InternalError` records. Path-hook records use `ImportObjectRef` values
  containing captured identity and safe type/name metadata. Mutating the
  returned list does not alter the monitor.

Calling `write_report()` or `render_report()` before `install()` raises
`RuntimeError`. There are no runtime dependencies. See the complete
[usage guide](https://glinte.github.io/metapathology/usage/) for CLI behavior,
lifecycle details, and integration examples.

## Reading the report

Start with the chronological evidence timeline. It interleaves resolution
starts, import-list changes, importer-cache diffs, and finder calls using the
monitor's shared sequence numbers. Sequence is deterministic capture order,
not a global wall-clock order across threads. Detailed sections then show how
finder precedence changed and group recorded `find_spec()` calls by finder.
The suspicious-findings section uses these labels:

- `[bypass]` means a custom finder claimed a source module, but a fresh
  `PathFinder` lookup would choose a different loader or origin. Tools attached
  through `sys.path_hooks` did not see the import that actually happened.
- `[unfindable]` means a custom finder claimed a source module that a fresh
  `PathFinder` lookup cannot find at all. This is a stronger form of bypass.
- `[no-spec]` means a new `sys.modules` entry has neither a `__spec__` nor a
  recorded finder claim. It was probably created manually or loaded through
  an `exec_module()`-style path that is invisible to meta-path finders.

These are diagnostic leads, not necessarily defects. Custom finders may bypass
the standard path machinery intentionally, and the report replays the current
`PathFinder` state rather than the exact state at import time. The
[report guide](https://glinte.github.io/metapathology/report/) explains every
section and finding category.

## Resource use

The monitor retains every recorded resolution start, finder call, mutation,
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
   whether the finder returned `None` or a module spec, then returns the same
   result. It does not supply a spec of its own or load a module.
5. It passively snapshots `sys.path_importer_cache` at installation, around
   observed path-hook mutations, and at report time. Pass
   `monitor_importer_cache=False`, or use `--no-importer-cache-monitoring`, to
   disable this mechanism. The cache object is never replaced or wrapped.

The standard `BuiltinImporter`, `FrozenImporter`, and `PathFinder` entries are
classes shared by CPython, so metapathology deliberately leaves them unwrapped.

At exit, the report compares the recorded result with what
`PathFinder.find_spec()` finds. If `PathFinder` cannot find the module or would
use a different kind of loader, the report notes that the normal
`sys.path_hooks` route was skipped.

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
- A low-level loader can replace an existing `sys.modules` entry with a second
  module object that retains a valid spec without starting a normal import.
  That replacement is indistinguishable at report time from an ordinary
  `PathFinder` load and is not currently flagged; discord.py#10017 is a
  representative example in `reproductions/`.
- This tool changes `sys.meta_path` while it is running. Use it for debugging,
  not as part of an application's normal runtime.
- Importer-cache diffs are passive boundary observations, not a complete log
  of short-lived changes between boundaries.

See the complete [limitations guide](https://glinte.github.io/metapathology/limitations/)
for timing, visibility, replay, cleanup, and memory boundaries.
