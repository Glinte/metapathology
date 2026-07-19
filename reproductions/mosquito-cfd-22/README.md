# mosquito-cfd#22 reproduction

This reduces [mosquito-cfd#22](https://github.com/talmolab/mosquito-cfd/issues/22)
to an eager package initializer, a NumPy-only target module, and a pandas-bearing
sibling. Coverage treats the dotted `--cov` value as a package name, imports it
inside `coverage.misc.sys_modules_saved()`, and removes modules introduced by
that discovery when the context exits.

On Windows with CPython 3.11, the later test import tries to load NumPy's
`_multiarray_umath` extension a second time and fails:

```text
ImportError: cannot load module more than once per process
```

Run from the repository root:

```powershell
.\reproductions\mosquito-cfd-22\reproduce.ps1
```

The runner first demonstrates that package-level coverage works, then runs the
failing dotted-module command directly and under deep metapathology monitoring.
The automatic JSON report is PID-suffixed. Look for two separate successful and
failed attempts for `numpy._core._multiarray_umath`, both attributed to
`PathFinder` with `ExtensionFileLoader` and the same `.pyd` origin.

The failure is platform-sensitive because extension reload behavior differs.
The environment is restricted to CPython 3.11 to retain the confirmed case.
