# metapathology

A standalone, stdlib-only debug tool that diagnoses `sys.meta_path` / import
hook contention: attributes each import to the finder that claimed it, logs
mutations of `sys.meta_path` with stack traces, and flags modules loaded in
ways that bypass `sys.path_hooks`. Origin story: beartype#556 (scikit-build-core's
`ScikitBuildRedirectingFinder` short-circuited `sys.path_hooks`, silently
disabling `beartype.claw`).

Guiding constraint: observe the import machinery while perturbing it as little
as possible, and never break third-party code that inspects `sys.meta_path`.

## Interfaces

The CLI is the primary interface; `install()` is the library API it wraps.

- `python -m metapathology <script> [args...]` and
  `python -m metapathology -m <module> [args...]` — parse our args, call
  `install()`, fix up `sys.argv` and `sys.path[0]` to mimic a direct
  invocation, then `runpy.run_path(..., run_name="__main__")` /
  `runpy.run_module(..., run_name="__main__", alter_sys=True)`. Same
  `__main__` caveats as coverage/cProfile (different `__spec__`;
  multiprocessing re-import quirks on Windows) — accepted tradeoff.
- Docs lead with `python -m metapathology`, not a console script: `-m`
  guarantees the same interpreter/venv as the target. A `metapathology`
  console script may exist as sugar only.
- `metapathology.install()` stays public for cases where a wrapper is
  impossible (notebooks, embedded interpreters, conftest.py-only access).
- Known limitation, document it: finders installed by `.pth` files (this is
  exactly how scikit-build-core's `ScikitBuildRedirectingFinder` arrives) are
  added during site initialization, before *any* of our code can run — CLI
  included. The mutation log can never witness those insertions; they appear
  in the initial `sys.meta_path` snapshot and get instrumented from there.
  Coverage.py-style `.pth` injection for subprocesses is out of scope.

## Hard constraints

- **Zero runtime dependencies.** Stdlib only, forever. The tool must work in a
  broken environment where nothing else imports cleanly.
- **CPython only.** Relies on the `import` audit event and import-system
  internals. Python >= 3.10 (see `pyproject.toml`).
- **Never handle an import.** Nothing here may return a spec, load a module,
  or change import outcomes. Observe and report only.
- **Everything reversible.** `uninstall()` must restore pristine state:
  `del finder.__dict__['find_spec']`, swap back a plain list, etc. (Audit
  hooks can't be removed — theirs must become inert no-ops on uninstall.)

## Architecture: three layers

Each layer covers the blind spots of the one below. Keep them independently
toggleable.

1. **Audit hook** (`sys.addaudithook`): on each `import` event, snapshot
   `(id(sys.meta_path), [type(f) for f in sys.meta_path])` and diff against
   the previous snapshot. Only mechanism that catches wholesale reassignment
   (`sys.meta_path = [...]`). Irremovable by third parties.
2. **Instrumented list**: replace `sys.meta_path` with a `list` subclass
   overriding `append/insert/extend/remove/pop/__setitem__/__delitem__/__iadd__`.
   Each mutation logs `traceback.extract_stack()` → attribution at mutation
   time. When layer 1 detects the subclass was blown away by reassignment,
   log it and re-install a fresh instrumented list around the new contents.
   New finders added through the list get auto-instrumented (layer 3).
3. **Finder attribution**: shadow each finder's `find_spec` in its *instance
   dict* with a logging wrapper that delegates, recording
   `(fullname, finder, spec_or_None)`. Instance-dict shadowing — never proxy
   objects — because third parties (pytest's `AssertionRewritingHook` among
   them) scan `sys.meta_path` with `isinstance`. Fallbacks: finders with
   `__slots__` and class-entries (`PathFinder` itself is all classmethods) get
   post-hoc replay attribution instead; don't mutate stdlib classes.

**Bypass detection** (the beartype#556 check): at report time, for each loaded
module whose `spec.origin` ends in `.py`/`.pyc` and that was claimed by a
finder other than `PathFinder`, replay
`importlib.machinery.PathFinder.find_spec(name, parent_path)` and compare
loader types. Mismatch ⇒ that finder short-circuited `sys.path_hooks`. Modules
in `sys.modules` with no recorded `find_spec` call are their own bucket
(manual `exec_module`-style loads).

## Correctness rules (violating any of these is a bug)

These came out of the design discussion; they are not optional style.

- **Copy snapshots inside the hook.** The `import` audit event passes
  `sys.meta_path` *by reference*. Any deferred inspection sees only final
  state. Extract ids/type-names immediately.
- **No imports in hot paths.** Hook and wrapper code runs *inside* an import.
  Pre-import every dependency (`traceback`, `threading`, …) at `install()`
  time; never import lazily in hook/wrapper bodies.
- **Re-entrancy guard.** A `threading.local()` active-flag around every hook
  and wrapper body (`if self._local.active: return` / set–clear). Even
  innocent-looking formatting can trigger an import and re-enter.
- **Lock discipline.** One `threading.Lock` for shared state. Never do
  anything potentially-importing (including string formatting of arbitrary
  objects — `__repr__` can import) while holding it: acquire → append plain
  data → release. Format at report time. Rationale: per-module import locks
  since 3.3 mean hooks run concurrently on arbitrary threads; holding our
  lock across an import is a classic ABBA deadlock with the module locks.
  Don't rely on "list.append is atomic under the GIL" — free-threaded builds.
- **Never call `repr()`/`str()` on foreign objects at record time.** Store
  `type(f).__name__` and `id(f)`; stringify only in the exit report.
- **The exit report must tolerate concurrent imports** from daemon threads:
  iterate over `list(sys.modules.items())` copies, expect slight
  inconsistency, never raise.
- **The `import` audit event fires before resolution starts**, not when a
  finder wins — winner attribution comes from layer 3 (or replay), never from
  the event itself. It also doesn't fire on `sys.modules` cache hits or
  manual `spec_from_file_location` + `exec_module` loads.

## Layout & tooling

- `src/metapathology/` — package source (`py.typed`; fully type-annotated).
- Build backend: `uv_build`; use `uv` for everything (`uv run`, `uv sync`).
  Note: `venv/` in the repo root is a stray non-uv env; `uv` will use `.venv`.
- Tests will need subprocess isolation: most of this tool's behavior can only
  be observed in a fresh interpreter (audit hooks are per-process and
  irremovable, import state is global). Prefer `uv run python -c ...` /
  script-based tests over in-process pytest tests for anything touching
  `install()`.
