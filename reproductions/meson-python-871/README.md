# meson-python#871 reproduction

This reproduces [meson-python#871](https://github.com/mesonbuild/meson-python/issues/871).
The package initializer installs `beartype.claw`, then the driver imports a
separate annotated submodule and calls it with an invalid argument.

From the repository root on Windows:

```powershell
.\reproductions\meson-python-871\reproduce.ps1
```

On Linux or macOS:

```sh
sh ./reproductions/meson-python-871/reproduce.sh
```

The direct run exits successfully because `MesonpyMetaFinder` constructs a
spec with `SourceFileLoader` without consulting the path hook installed by
beartype. The monitored run attributes both package imports to that finder and
records the preceding `sys.path_hooks` insertion.
