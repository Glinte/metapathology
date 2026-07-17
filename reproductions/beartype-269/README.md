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

Metapathology should show ordinary `PathFinder` handling and no findings:
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

target outcome: raised BeartypeClawImportConfException (exit status 1)
verdict: no import-hook interference detected across 259 monitored imports
sys.meta_path (unchanged since install): [_Finder (virtualenv startup, expected), BuiltinImporter, FrozenImporter, PathFinder]
-- findings (0) --
No import-hook interference detected across 259 monitored imports.

-- finder attribution (instrumented finders only) --
_Finder: 258 probes, 0 claimed
```

There is no finding. The report also lists `typing.io` and `typing.re` under
"relevant post-hoc loader inventory" as metadata unavailable for 2
module-cache entries (`not_module`); those are unrelated compatibility
aliases created by the standard `typing` module. The failure is the mismatch
between runpy's execution name and beartype's stored package configuration,
not finder order.
