# Development

Runtime code is standard-library only, fully typed, and supports CPython 3.10+.

## Set up

```console
uv sync
uv run pytest
```

Use [uv](https://docs.astral.sh/uv/) for every project command. The root
`venv/` is unrelated; uv uses `.venv`.

## Required checks

Run the configured formatter, linter, both type checkers, tests, documentation
build, and package build before release. CI is the source of truth for exact
commands and supported Python versions.

Global import-state behavior belongs in a fresh subprocess because audit hooks
cannot be removed. Prefer real finders, modules, files, and import operations
over mocks.

## Runtime invariants

- Never handle an import or change its outcome.
- Copy mutable audit values immediately.
- Import hot-path dependencies before monitoring.
- Guard hooks and wrappers against re-entry.
- Do not call foreign code, import, format, or inspect representations while
  holding the monitor lock.
- Restore ordinary lists and owned instance shadows on uninstall.
- Reporting consumes one immutable monitor snapshot.

See the repository `AGENTS.md` for the complete contributor contract and
[writing guide](internal/writing-guide.md) for documentation changes.
