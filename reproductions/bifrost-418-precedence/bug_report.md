# Virtual import hook is placed ahead of builtin and frozen finders, not only ahead of filesystem resolution

## Summary

PR #419 correctly ensures indexed workspace packages take precedence over
same-named filesystem modules. It implements that precedence with:

```python
sys.meta_path.insert(0, _finder)
```

This also places `VirtualModuleFinder` ahead of `BuiltinImporter`,
`FrozenImporter`, and any deliberately earlier custom finder. An indexed
workspace name can therefore replace a frozen standard-library module.

## Reproduction

CPython provides the frozen demonstration module `__hello__`. The reproduction
uses Bifrost's relevant current behavior: an index-gated virtual finder claims
`__hello__`, and the finder is inserted at index zero.

From `reproductions/bifrost-418-precedence`:

```powershell
uv sync
uv run --no-sync python reproduce.py
```

Observed:

```text
expected standard origin: frozen
observed origin: workspace/__hello__.py
observed implementation: workspace
```

The companion control inserts the virtual finder immediately before
`PathFinder`:

```powershell
uv run --no-sync python control.py
```

It imports the frozen module and reports `observed origin: frozen`.

## Why the prefix list does not close the gap

`STDLIB_PREFIXES` is a manually maintained set rather than a complete property
of the running interpreter. `__hello__` is not present. Additionally, the entry
`"_"` is tested using exact set membership, so it does not implement its comment
that all underscore-prefixed extension modules are skipped.

The module index prevents accidental probes for names absent from the workspace,
but it does not protect standard modules whose names genuinely collide with an
indexed workspace file.

## Expected behavior

Workspace modules should precede ordinary filesystem resolution without
overriding CPython's builtin and frozen modules.

## Suggested change

Insert `VirtualModuleFinder` immediately before `PathFinder` instead of at index
zero. Add regression coverage for:

- an indexed name also handled by `FrozenImporter`;
- an indexed name also handled by `BuiltinImporter` where available;
- coexistence with a pre-existing custom finder;
- the existing filesystem-module versus virtual-package collision.

This retains the intended fix from PR #419 while narrowing the precedence
change to the mechanism that caused the original shadowing bug.
