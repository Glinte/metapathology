# metapathology

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

## Usage

Primary: run your program under observation, no code changes needed —

```console
$ python -m metapathology myscript.py --my-args
$ python -m metapathology -m pytest tests/
```

A report is printed at exit. Prefer `python -m metapathology` over a bare
`metapathology` command: it guarantees the hooks land in the same interpreter
and venv as the code under investigation.

Library API, for when a wrapper isn't possible (notebooks, embedded
interpreters, "I can only touch `conftest.py`"):

```python
import metapathology

metapathology.install()  # as early as possible
```

There are no runtime dependencies or configuration options.

## How Python finds an imported module

Python first checks whether the module is already present in `sys.modules`. If
it is not, Python reads [`sys.meta_path`](https://docs.python.org/3/library/sys.html#sys.meta_path),
a list of objects called *finders*. It asks each finder in order whether it can
locate the module by calling the finder's `find_spec()` method.

A finder returns `None` when it cannot locate the module, and Python moves on
to the next finder. A finder that can locate the module returns a *module
spec*: a small object describing the module and how to load it. Python stops
asking other finders once it receives a spec. The Python documentation calls
this process [the meta path](https://docs.python.org/3/reference/import.html#the-meta-path).

One of Python's standard finders is `PathFinder`. It performs the familiar
search through `sys.path` and, as part of that search, consults
`sys.path_hooks`. A custom finder placed before `PathFinder` can return a spec
first. That may be intentional, but it also means that `PathFinder` and its
path hooks do not see that module. See
[the path-based finder](https://docs.python.org/3/reference/import.html#the-path-based-finder)
for the full protocol.

## How it works

`metapathology` observes that process in three ways:

1. It registers a [`sys.addaudithook()`](https://docs.python.org/3/library/sys.html#sys.addaudithook)
   callback for CPython's [`import` audit event](https://docs.python.org/3/library/audit_events.html#audit-events).
   On each event, it checks whether code replaced the entire `sys.meta_path`
   list, for example with `sys.meta_path = [...]`. This event says that an
   import is starting; it does not say which finder will succeed.
2. It temporarily replaces `sys.meta_path` with a compatible `list` subclass.
   This records calls such as `append()`, `insert()`, and `remove()`, including
   the stack trace showing where the change came from.
3. It wraps each finder's existing `find_spec()` method. The wrapper records
   whether the finder returned `None` or a module spec, then returns the same
   result. It does not supply a spec of its own or load a module.

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
