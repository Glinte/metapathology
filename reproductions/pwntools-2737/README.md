# pwntools#2737 reproduction

This reproduces [pwntools#2737](https://github.com/Gallopsled/pwntools/issues/2737)
on Python 3.12 with pwntools 4.15.0. The failure comes from pwntools'
`LazyImporter`, which still exposes only the legacy `find_module` /
`load_module` meta-path API for `pwnlib.shellcraft.*`. Python 3.12 no longer
falls back to `find_module`, so explicit imports such as
`import pwnlib.shellcraft.amd64` stop resolving.

From the repository root on Windows:

```powershell
.\reproductions\pwntools-2737\reproduce.ps1
```

The direct run is expected to fail with `ModuleNotFoundError`. The monitored
run should fail the same way, then report the nonstandard `LazyImporter` entry
as a legacy meta-path finder that could not be instrumented with `find_spec`.
That makes the diagnosis obvious: the package still depends on a removed import
machinery fallback.
