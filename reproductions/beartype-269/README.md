# beartype#269 reproduction

This reproduces [beartype#269](https://github.com/beartype/beartype/issues/269)
with Python 3.11 and beartype 0.15.0. Running a package submodule with `-m`
gave transformed code the transient name `__main__`, while the package-scoped
claw stored its configuration under `beartypeproject.my_functions`. The
mismatched lookup raises `BeartypeClawImportConfException` before the module
can run.

```powershell
.\reproductions\beartype-269\reproduce.ps1
```

Metapathology should show ordinary `PathFinder` handling and no `[bypass]`:
this was a `runpy` package-identity bug inside beartype rather than finder
contention. Beartype fixed it in commits `fad062c` and `d4e9e454b1`.

## Metapathology result

The monitored command exits with status 1. The relevant traceback and report
lines are:

```text
BeartypeClawImportConfException: Beartype configuration associated with module
"__main__" hooked by "beartype.claw" not found. Existing beartype
configurations associated with hooked modules include:
    {'beartypeproject.my_functions': BeartypeConf(...)}

initial sys.meta_path: [_Finder, BuiltinImporter, FrozenImporter, PathFinder]
-- sys.meta_path mutations (0) --
-- finder attribution (instrumented finders only) --
_Finder (...): ... find_spec calls, 0 claimed
-- internal errors (0) --
```

There is no `[bypass]` finding. The report also lists `[no-spec]` entries for
`typing.io` and `typing.re`; those are unrelated compatibility aliases created
by the standard `typing` module. The failure is the mismatch between runpy's
execution name and beartype's stored package configuration, not finder order.
