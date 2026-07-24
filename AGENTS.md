# metapathology

`metapathology` is a standalone diagnostic tool for `sys.meta_path` and import-hook contention. Its primary interface is `python -m metapathology`; `install()` supports environments where a wrapper is impractical.

The governing rule is to observe import machinery with minimal perturbation and never break third-party code inspecting it.

## Non-negotiable constraints

- Runtime code is stdlib-only and supports CPython 3.10+.
- Outside explicitly enabled unsafe import-branch exploration, never handle an
  import or change its outcome. Ordinary capture observes and reports only.
  Unsafe exploration invokes skipped foreign finders and hooks, discards their
  results, never executes alternative loaders, and must state that foreign side
  effects can still change the diagnostic run.
- `uninstall()` must restore ordinary lists and remove instance instrumentation. Audit hooks are irremovable, so they become inert.
- Finders installed before startup, including those from already-processed `.pth` files, can only appear in the initial snapshot. Keep this limitation documented.

## Architecture

Keep the three core mechanisms independently toggleable:

1. An import audit hook detects direct `sys.meta_path` replacement and records immediate copied snapshots.
2. A real `list` subclass observes all supported `sys.meta_path` mutations and their stacks. It must remain compatible with `isinstance(..., list)` checks.
3. Finder attribution shadows `find_spec` in writable instance dictionaries and delegates unchanged. Never proxy finder objects or mutate shared stdlib finder classes.

Optional detailed monitoring applies the shared list observer to `sys.path` and related import state. It is exhaustive, may grow with observed mutations, and must restore ordinary state on uninstall.

`Monitor` owns observation and its mutable evidence. Reporting receives one immutable snapshot and must not acquire monitor locks or call back into live monitor components. Runtime code owns installation, atexit, and output policy. Analysis produces a format-neutral artifact that can support multiple exports from one capture.

Captured custom-finder results may be compared with a report-time `PathFinder` check. Describe differences as current-state evidence, never as a prediction of what would have won. Promote differences to findings only when corroborated by an observed effect.

## Import-hook correctness

Violating these rules is a bug:

- Copy audit-event values inside the hook; mutable arguments cannot be inspected later.
- Import every hot-path dependency before monitoring begins. Hooks and wrappers must not import lazily.
- Put a `threading.local()` re-entrancy guard around every hook and wrapper body.
- Protect shared state with one lock, but only append plain data while holding it. Never format, import, invoke foreign code, or rely on GIL atomicity under the lock.
- Never call `repr()` or `str()` on foreign objects while recording. Store safe primitives such as type names and object IDs.
- Keep observation failures isolated from the target program. Reporting and cleanup must degrade without changing import outcomes.
- Snapshot mutable global collections before report-time iteration and tolerate concurrent imports.
- Remember that the import audit event precedes resolution and does not identify the winning finder or cover cache hits and manual module execution.

## Resource behavior

For every producer/consumer path, define capacity, overflow, and shutdown behavior. If exhaustive capture intentionally grows with event count, document and test that cost. Do not add accidental unbounded queues, caches, or retries.

Concurrency and randomized tests must be reproducible from a reported seed or example.

## Development

- Source lives in `src/metapathology/` and is fully type-annotated. Tests are typed where practical.
- Use built-in generics and `collections.abc`; keep imports at module scope.
- Prefer small functions, explicit parameters, and straightforward ownership over speculative abstractions.
- Comments explain non-obvious reasons. Public and non-obvious APIs use Google-style docstrings.
- Runtime dependencies remain forbidden. Justify any new development dependency.
- Update the nearest public discovery surface only when public behavior changes. Keep internal design detail in focused docs or this file.
- Follow `docs/internal/writing-guide.md`: write for the reader's next decision, disclose detail progressively, and keep one authoritative exhaustive reference.
- Temporary compatibility code needs a `TODO` with a concrete removal trigger.

## Tooling and tests

- Use `uv` for all project commands: `uv run pytest`, type checks, linting, and builds. The default test run uses four xdist workers; use `uv run pytest -n 0` when debugging serially. Ignore the stray root `venv/`; `uv` uses `.venv`.
- Test global import-state behavior in fresh subprocesses because audit hooks cannot be removed.
- Keep opt-in freezer tests in the shared `freezer` xdist group so executable builds remain sequential.
- Add regression coverage for behavior changes; for bug fixes, reproduce the failure first.
- Prefer real finders, modules, files, and subprocesses over mocks. Test project behavior, not third-party internals.
- Cover cleanup, concurrency, malformed state, and overflow where relevant.
- Keep serialization assertions semantic unless the complete output is intentionally the contract.

## CI and commits

- CI must cover formatting, linting, both type checkers, tests, and package builds across supported Python versions as appropriate.
- Pin GitHub Actions by full commit hash.
- Commit early and frequently in reviewable, Mitchell Hashimoto-style pieces:
  keep each commit small, coherent, and buildable; make the sequence tell the
  implementation story; and separate preparatory refactors from behavior
  changes.
- Keep requested commits atomic. Use concise imperative subjects and bodies that explain the problem or invariant, why the solution fits, and important tradeoffs or follow-up triggers.
