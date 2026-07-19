# Dotted `source` package discovery unloads extension modules and causes a later reload failure

## Summary

Using a dotted module as coverage's source can import the module's entire parent
package during source discovery. Coverage performs that import inside
`sys_modules_saved()`, which deletes every newly introduced `sys.modules` entry
after discovery. If the eager package import loaded a native extension, normal
application startup can then attempt to load that extension for a second time.

With NumPy on Windows this fails with:

```text
ImportError: cannot load module more than once per process
```

This appears to be another instance of
[coverage.py #1925](https://github.com/coveragepy/coveragepy/issues/1925), which
reports the same reload behavior with Torch and TorchVision. This reproduction
is smaller and uses only NumPy and pandas. The underlying fix therefore belongs
in coverage.py; [mosquito-cfd #22](https://github.com/talmolab/mosquito-cfd/issues/22)
is an additional real-world occurrence and NumPy-based reproducer.

## Reproduction

The reproduction is in `reproductions/mosquito-cfd-22` of the metapathology
repository. On Windows:

```powershell
uv sync
uv run --no-sync pytest -q --cov=eager_source tests/test_normalization.py
uv run --no-sync pytest -q --cov=eager_source.normalization tests/test_normalization.py
```

The first command passes. The second fails during collection.

## How the import sequence was observed

I also ran the failing command under metapathology, a diagnostic wrapper I made that
records which Python import finder and loader handled each import:

```powershell
uv run --no-sync python -m metapathology --deep \
  -m pytest -q --cov=eager_source.normalization tests/test_normalization.py
```

The resulting event sequence records:

1. Coverage source discovery imports `eager_source.normalization`.
2. Executing `eager_source/__init__.py` eagerly imports pandas and NumPy.
3. `PathFinder` and `ExtensionFileLoader` successfully load
   `numpy._core._multiarray_umath` from its `.pyd` file.
4. `coverage.misc.sys_modules_saved()` removes the newly imported package graph
   from `sys.modules` when source discovery finishes.
5. pytest imports the test module and therefore imports `eager_source` again.
6. `PathFinder` selects the same extension file with `ExtensionFileLoader`.
7. The second extension execution raises `ImportError`.

No competing meta-path finder is involved; both resolutions use the standard
path machinery and the same origin.

## Downstream mitigations

Until coverage.py avoids making the extension eligible for a second load,
projects can avoid this path by:

- selecting the containing package, such as `--cov=eager_source` or
  `--cov=mosquito_cfd.force_surrogate`, instead of a deeply dotted module;
- passing a filesystem source path where that is suitable;
- making the parent package initializer lazy, or avoiding eager re-exports that
  import pandas, NumPy, or other native-extension dependency graphs; and
- tracking coverage.py #1925 for the underlying fix.

These are workarounds. A dotted source value is supported coverage.py input and
should not change whether an extension can be imported later by the program.

## Expected behavior

Resolving a dotted source value should not make successfully initialized native
extensions eligible for a second process-level load. Source discovery should
avoid executing the package where possible, or cleanup should preserve modules
that cannot safely be re-imported.

## Environment

- Windows
- CPython 3.11
- coverage 7.14.1
- pytest-cov 7.1.0
- NumPy 2.4.2
- pandas 3.0.0
