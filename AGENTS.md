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

## Resource and failure behavior

- Import monitoring can produce data for the lifetime of the target process.
  Do not add an unbounded queue, cache, or retry loop accidentally. For every
  new producer/consumer path, define and test its capacity, overflow policy,
  and shutdown behavior. If exhaustive capture intentionally grows with the
  number of observed events, document that cost rather than silently dropping
  records.
- Keep observation failures isolated from the target program. Reporting and
  cleanup should degrade gracefully on malformed, partially initialized, or
  concurrently changing import state; they must not change import outcomes.
- Make nondeterminism explicit. Tests using concurrency, randomized inputs, or
  generated cases must be reproducible from their reported seed or example.

## Layout & tooling

- `src/metapathology/` — package source (`py.typed`; fully type-annotated).
- Build backend: `uv_build`; use `uv` for everything (`uv run`, `uv sync`).
  Note: `venv/` in the repo root is a stray non-uv env; `uv` will use `.venv`.
- Tests will need subprocess isolation: most of this tool's behavior can only
  be observed in a fresh interpreter (audit hooks are per-process and
  irremovable, import state is global). Prefer `uv run python -c ...` /
  script-based tests over in-process pytest tests for anything touching
  `install()`.

## Development practices

- Prefer simple, readable, maintainable solutions over clever abstractions.
  Add shared helpers only when they remove meaningful duplication; do not
  over-engineer speculative reuse.
- Keep all production code and tests fully type-annotated. Use built-in
  generics (`list`, `tuple`, etc.) and `collections.abc` rather than legacy
  aliases from `typing`.
- Keep imports at module scope. The import-hook hot paths are especially
  strict: anything they need must already have been imported by `install()`.
- Put repeated hardcoded values in named module-level constants.
- Comments should explain why a non-obvious decision exists, not narrate the
  code. Do not rewrite unrelated comments while making a focused change.
- Use Google-style docstrings for non-obvious modules and public APIs. Include
  `Args` and `Raises` where applicable; include `Returns` when the return value
  is not already clear from its annotation.
- Keep boundaries injectable where doing so makes import-system behavior
  independently testable, but prefer small functions and explicit parameters
  over a dependency-injection framework.
- Treat the public API, CLI help, report vocabulary, and documented examples as
  discovery surfaces. New functionality should be findable from the nearest
  relevant surface without requiring a user to read the implementation.
- Keep conceptual documentation and behavior in sync in the same change.
  Update `README.md` when public behavior, CLI usage, limitations, or report
  interpretation changes; update this file when architectural invariants
  change. Add a documentation site only when the material outgrows the README.
- Runtime dependencies remain forbidden. Before adding a development
  dependency, record why the standard library and existing tools are
  insufficient, what maintenance/security cost it adds, and whether it
  replaces an existing tool.
- Temporary compatibility, migration, or rollout code must leave a `TODO` at
  every future cleanup point with a concrete removal trigger such as a Python
  version, dependency version, or date. Vague cleanup reminders are not enough.

## Testing

- Use pytest through `uv run pytest`; do not invoke an environment's bare
  `pytest` executable.
- New features and bug fixes require tests. For a bug fix, first add a test
  that reproduces the failure, then implement the fix and verify the test.
- Cover relevant edge cases and failure paths, not only the happy path.
- Express correctness rules as invariants where practical. Use Hypothesis for
  stateful or property-based coverage of mutation sequences, round trips, and
  malformed inputs; keep the underlying helpers callable without the CLI so
  they remain fuzzable.
- Assert invariants after each operation in generated sequences, not only at
  the end. Include repeated operations, reordering, concurrent mutation, and
  interrupted cleanup when those failure modes apply.
- Keep a small corpus of representative historical inputs or outputs when a
  serialization or report format becomes a compatibility contract. Prefer
  semantic assertions; use snapshot/golden tests only when the reviewed whole
  output is itself the contract and a diff is useful.
- Prefer real finders, modules, files, and subprocesses over mocks. Do not test
  third-party library behavior or private implementation details.
- Keep shared fixtures in `tests/conftest.py` or `tests/fixtures/`; leave a
  fixture in a test module only when it is specific to that module.
- Avoid tautological assertions that merely repeat test setup without
  exercising behavior.

## CI and commits

- For GitHub Actions changes, check the current action release and pin actions
  by full commit hash rather than a mutable version tag.
- CI must run formatting/linting, both type checkers, tests, and package-build
  checks across the supported Python range as appropriate. Public examples or
  generated reference material should be executable or checked for drift
  rather than trusted to stay current manually.
- Keep commits atomic when commits are requested. Use concise, imperative,
  scoped subjects. In the body, explain the user-visible problem or invariant,
  why the chosen approach is appropriate, and important tradeoffs or follow-up
  triggers; do not merely narrate the diff (mitchellh style).
