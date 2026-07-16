# wrapt 1.14.2 reproduction

This reproduces the wrapt 1.14.2 post-import-hook transparency bug documented
in its changelog. When a target module is imported through wrapt's chained
loader, the module's `__loader__` and `__spec__.loader` are left pointing at
the wrapt wrapper instead of the original loader.

From the repository root on Windows:

```powershell
.\reproductions\wrapt-1150\reproduce.ps1
```

The direct run is expected to fail an assertion that the loader metadata still
identifies the original `SourceFileLoader`. The monitored run should fail the
same way, then report wrapt's import hook finder and the loader substitution
that changed the module metadata.
