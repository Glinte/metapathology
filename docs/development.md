# Development

The repository uses [uv][uv] and keeps runtime code stdlib-only.

[uv]: https://docs.astral.sh/uv/

## Set up and check a change

```console
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pyrefly check
```

Run repository hooks before committing:

```console
uv run prek run --all-files
```

Import monitoring changes often require a fresh interpreter because audit
hooks cannot be removed and import state is global. Prefer subprocess tests
for behavior that calls `install()`.

## Core invariants

Changes must preserve these properties:

- Never claim or load a module; record and delegate only.
- Keep hooks and finder wrappers re-entrancy guarded.
- Import everything needed by a hot path before that path runs.
- Never stringify foreign objects while recording an import event.
- Hold the shared state lock only for plain-data reads and writes, never while
  running code that could import.
- Make all reversible instrumentation pristine again in `uninstall()`.
- Keep reporting safe under concurrent imports and malformed module state.

The complete project rules live in the repository's
[`AGENTS.md`][agents-source].

[agents-source]: https://github.com/Glinte/metapathology/blob/main/AGENTS.md

## Tests

Use fully annotated tests and real finders, modules, files, and subprocesses
where practical. A bug fix starts with a reproducer. Generated mutation
sequences should assert invariants after each operation and remain reproducible
from the failing [Hypothesis][hypothesis] example or seed.

[hypothesis]: https://hypothesis.readthedocs.io/en/latest/

Use snapshot or golden tests only when the complete reviewed text is itself a
compatibility contract. Otherwise prefer semantic assertions.

## Performance benchmarks

Run the startup, import-throughput, mutation, and memory benchmark from the
repository root:

```console
uv run --script scripts/benchmark.py
```

The script uses fresh target-interpreter processes for every sample and writes
raw JSON, a Markdown summary, plus import and `sys.meta_path` mutation graphs beneath
`.cache/metapathology-benchmarks/`. Use `--quick` for a smoke run, or customize
the workload and interpreter, for example:

```console
uv run --script scripts/benchmark.py --counts 50,250,1000 --repeats 7 --python python3.12
```

Fresh-process timings also cover bare interpreter startup, package import,
deferred monitor-API import, direct script execution, and the monitored CLI
wrapper. Timing and memory are collected in separate trials so `tracemalloc` does not
distort the speed measurements. Workers use `python -S` and disable bytecode
writes to exclude interpreter-specific `.pth` finders and cache-warming order
effects. The `native` scenario therefore measures the controlled standard-
finder path; `attributed` installs the same delegating instance finder in both
control and monitored processes so every synthetic import exercises a retained
finder-call record. Mutation samples perform repeated `pop`/`append` pairs and
therefore include the monitor's intentional stack-capture cost. Trials are
shuffled to balance system warm-up; `--seed` controls and records that order.

Finder wrappers capture the finder's id and display name once rather than
allocating them for every recorded probe. Search-path snapshots remain exact,
but identity-equal immutable tuples share storage through a fixed eight-entry
least-recently used cache. Identity comparison avoids invoking foreign equality
code in the import hot path; bounded eviction prevents the cache itself from
becoming another unbounded producer. Cache access uses the existing record
lock, and `uninstall()` clears it without affecting retained event snapshots.

## Documentation

Update the closest discovery surface in the same change as public behavior:

- CLI behavior belongs in help and [Using metapathology](usage.md).
- Report vocabulary belongs in [Reading the report](report.md).
- Public Python behavior belongs in [Library API](api.md).
- Architectural invariants belong in `AGENTS.md` and, when useful to users,
  [How it works](concepts.md).

Build the documentation locally with the repository's
[Zensical][zensical] configuration:

```console
uv run --group docs zensical build --clean
```

[zensical]: https://zensical.org/docs/

CI performs a strict build for every pull request and every push to `main`,
deploys successful `main` builds to GitHub Pages, and repeats the build every
six hours to catch toolchain or hosting regressions. An agent-authored
freshness job that edits documentation and opens pull requests is deliberately
not preconfigured: it needs an explicitly chosen model provider, repository
write policy, and secret before it can be secure and functional.

Development dependencies need a recorded justification. Runtime dependencies
are not permitted. Temporary compatibility code must include a `TODO` with a
specific removal trigger such as a supported Python version, dependency
version, or date.

When releasing, update both `project.version` in `pyproject.toml` and
`metapathology.__version__`. The package test requires them to match. Keeping
the small duplication avoids importing `importlib.metadata` during every
package import and CLI invocation.

The private `_InstrumentedMetaPath.__iadd__` and `__imul__` annotations
deliberately return the concrete class while Python 3.10 remains supported.
This avoids adding `typing_extensions` solely for two private annotations.
When the minimum supported Python becomes 3.11, restore the more precise
standard-library [`typing.Self`][typing-self] annotation.

[typing-self]: https://docs.python.org/3/library/typing.html#typing.Self

Runtime modules assign `TYPE_CHECKING = False` directly instead of importing
it from `typing`. Basedpyright and Pyrefly both recognize the conventional name
as true during analysis. Keep type-only annotations quoted and type-only
imports inside those blocks so runtime startup does not import `typing`.

The development commands above are documented by [pytest][pytest],
[Ruff][ruff], [basedpyright][basedpyright], [Pyrefly][pyrefly], and
[prek][prek].

[pytest]: https://docs.pytest.org/en/stable/
[ruff]: https://docs.astral.sh/ruff/
[basedpyright]: https://docs.basedpyright.com/latest/
[pyrefly]: https://pyrefly.org/
[prek]: https://prek.j178.dev/

Commit subjects should be concise, imperative, and scoped. Explain the
user-visible problem or invariant, why the approach fits, and meaningful
tradeoffs or follow-up triggers in the body rather than narrating the diff.
