# rules_python#2009 reproduction

This isolates the import collision reported in
[rules_python#2009](https://github.com/bazel-contrib/rules_python/issues/2009).
When the affected rules_python target runs through `bazel coverage`,
rules_python executes `coverage/__main__.py` directly. CPython initially puts
that script's `coverage` package directory on `sys.path`. A rules_python patch
moves the directory to the end but does not remove it, leaving the package's
internal `python.py` visible as the top-level module `python`.

The reproduction puts the intended Bazel runfiles namespace first and the
coverage package directory last, matching the patched order. It still fails:
`PathFinder` retains the earlier PEP 420 namespace candidate while continuing
the search, then prefers the later regular `coverage/python.py` module.

Run from the repository root:

```powershell
.\reproductions\rules-python-2009\reproduce.ps1
```

The import fails with:

```text
ModuleNotFoundError: No module named 'python.runfiles'; 'python' is not a package
```

The monitored run records the standard path search and the `FileFinder`
associated with the reproduction's trailing `coverage` directory. This
reduction does not require Bazel; the upstream issue's full rules_python command
supplies the same path shape.

This is not a coverage.py bug. `coverage/python.py` is normally importable only
as `coverage.python`; rules_python makes it a top-level candidate by retaining
the package directory itself on `sys.path`.
