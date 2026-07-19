# Idea: observe cache-hit imports by wrapping `builtins.__import__`

Status: candidate deep mechanism, not scheduled. Recorded 2026-07-19 after
discussing (and rejecting) a `sys.modules` wrapper.

## Problem

Cache-hit imports are invisible to every current mechanism: the `import`
audit event does not fire, no finder is called, and `--deep-import-outcomes`
profiles `_find_and_load`, which cache hits bypass. The unanswerable
question today is "which code imported X, and when," for modules that were
already loaded.

## Rejected approach: wrapping `sys.modules`

Replacing `sys.modules` with an observing dict subclass does not work:

- The import internals hold their own reference to the modules dict
  (`tstate->interp->imports.modules`); rebinding `sys.modules` is
  documented as unreliable, and the live dict's class cannot be changed in
  place.
- Observation would be silently partial: `import.c` fast-paths the exact
  `dict` case, and any C code using `PyDict_GetItem` on the subclass skips
  the override. The report would confidently claim "no cache hit" when one
  happened.
- It puts Python-level recording on the hottest dict in the interpreter and
  breaks the project rule against perturbing structures that third-party
  code inspects (`PyDict_CheckExact`, free-threaded assumptions).

## Candidate approach: wrap `builtins.__import__`

An opt-in deep switch (working name `--deep-import-calls`) that swaps
`builtins.__import__` for a delegating recorder.

What it buys:

- Every `import` statement and explicit `__import__()` call is observed,
  **including cache hits** — name, fromlist, level, and (via the caller's
  globals or one stack frame) the importing module.
- Plain function swap: fully reversible in `uninstall()`, no proxying of
  interpreter structures, no effect on `sys.modules` or finder identity.

Known boundaries (same class as existing deep-mechanism caveats; document,
don't fight):

- `importlib.import_module()` and lower-level importlib entry points bypass
  `__import__` entirely.
- Other tools also wrap `__import__`; install order matters, and uninstall
  must restore whatever was current at install (chain-safe restore or
  refuse, like the profiler-slot rule for deep import outcomes).
- The wrapper body is the hottest path in this codebase: full re-entrancy
  guard, no imports, no foreign stringification, constant-size records.
  Caller attribution from stack frames is expensive — consider making it a
  sub-option or capturing only module `__name__` from the caller's globals.
- CPython's ceval may cache/fast-path `__import__` lookups when it is the
  builtin; verify on each supported version that the swap is honored (it is
  the documented customization point, but performance shortcuts exist —
  test 3.10–3.14 and free-threaded builds).

## When to build it

Only when a real diagnosis needs cache-hit or per-callsite evidence —
e.g. "which module imported X before the hook installed" where
`baseline_modules` is not enough. Do not build speculatively; the default
report's story does not depend on it.
