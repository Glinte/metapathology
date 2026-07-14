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
- where code changed `sys.meta_path`, with a stack trace; and
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
```

A report is printed at exit. Prefer `python -m metapathology` over a bare
`metapathology` command: it guarantees the hooks land in the same interpreter
and venv as the code under investigation.

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
    metapathology.report(sys.stdout)
    metapathology.uninstall()
```

`uninstall()` is idempotent. It restores a plain `sys.meta_path`, removes the
wrappers from finders, and unregisters the exit report. The CPython audit hook
cannot be removed, so it remains installed as an inactive no-op. Recorded
events remain available after uninstalling.

For integration with another diagnostic or test harness:

- `metapathology.render_report()` returns the same report as a string;
- `metapathology.report(file=None)` writes it to `file`, or to standard error
  when omitted;
- `metapathology.get_monitor()` returns the process-wide monitor, or `None`
  before the first call to `install()`; and
- `monitor.events()` returns a capture-order snapshot of the structured
  `FindSpecCall`, `MetaPathMutation`, `MetaPathReassignment`, and
  `InternalError` records. Mutating the returned list does not alter the
  monitor.

Calling `report()` or `render_report()` before `install()` raises
`RuntimeError`. There are no runtime dependencies. See the complete
[usage guide](https://glinte.github.io/metapathology/usage/) for CLI behavior,
lifecycle details, and integration examples.

## Reading the report

The mutation and reassignment sections show how finder precedence changed.
Finder attribution groups recorded `find_spec()` calls by finder and lists the
modules each finder claimed. The suspicious-findings section uses these
labels:

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

The monitor retains every recorded finder call, mutation, reassignment, and
internal error so the final report is exhaustive. Its memory use therefore
grows with import activity for as long as monitoring remains enabled; there is
currently no event limit or silent dropping policy. For a long-running or
import-heavy process, call `report()` and `uninstall()` once the behavior of
interest has been captured. Stack traces are stored for `sys.meta_path`
changes, which makes mutation records more expensive than finder-call records.
See [limitations and resource behavior](https://glinte.github.io/metapathology/limitations/)
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

`metapathology` observes that process in three ways:

1. It registers a [`sys.addaudithook()`](https://docs.python.org/3/library/sys.html#sys.addaudithook)
   callback for CPython's [`import` audit event](https://docs.python.org/3/library/audit_events.html#audit-events).
   This event says that an uncached import is starting; it does not say which
   finder will succeed. It also lets the monitor recover if less-common code
   assigns an entirely new list to `sys.meta_path`.
2. It temporarily replaces `sys.meta_path` with a compatible `list` subclass.
   This records the usual changes as they happen: additions, removals,
   replacements, clearing, and reordering, with a stack trace showing where
   each change came from. Newly added finders are prepared for call recording.
3. It wraps each finder's existing `find_spec()` method. The wrapper records
   whether the finder returned `None` or a module spec, then returns the same
   result. It does not supply a spec of its own or load a module.

The standard `BuiltinImporter`, `FrozenImporter`, and `PathFinder` entries are
classes shared by CPython, so metapathology deliberately leaves them unwrapped.

At exit, the report compares the recorded result with what
`PathFinder.find_spec()` finds. If `PathFinder` cannot find the module or would
use a different kind of loader, the report notes that the normal
`sys.path_hooks` route was skipped.

## Caveats

- CPython only (relies on the `import` audit event and import-system
  internals).
- Monitoring begins when `metapathology` is installed. Finders added earlier
  by `.pth` files are shown in the initial `sys.meta_path` list, but there can
  be no stack trace for when they were added because that happened during
  Python startup.
- The temporary changes to finders and `sys.meta_path` are reversed by
  `uninstall()`. Python does not provide a way to remove an audit hook, so the
  installed callback remains as an inactive no-op after uninstalling.
- This tool changes `sys.meta_path` while it is running. Use it for debugging,
  not as part of an application's normal runtime.

See the complete [limitations guide](https://glinte.github.io/metapathology/limitations/)
for timing, visibility, replay, cleanup, and memory boundaries.
