# distributed#7782 reduced reproduction

This is a reduced, dependency-free reproduction of
[distributed#7782](https://github.com/dask/distributed/issues/7782). It models
setuptools' default PEP 660 arrangement: `_EditableFinder` is appended after
the standard `PathFinder`, and the current directory contains an otherwise
empty `distributed/` directory.

From the repository root on Windows:

```powershell
.\reproductions\distributed-7782\reproduce.ps1
```

Both runs import an empty namespace package: its spec origin and file are
`None`, and the editable package's marker is absent. The monitored run records
the editable finder being appended, but no call for `distributed`, proving
that the preceding `PathFinder` ended resolution. Because shared CPython class
finders are deliberately not wrapped, the report explains `PathFinder`'s role
rather than attributing its individual call.
