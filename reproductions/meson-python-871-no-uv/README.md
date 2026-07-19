# meson-python#871 reproduction without uv

This reproduces [meson-python#871](https://github.com/mesonbuild/meson-python/issues/871).
The package initializer installs `beartype.claw`, then the driver imports a
separate annotated submodule and calls it with an invalid argument. It uses
only the standard-library `venv` module and `pip`; uv is not required.

From the repository root on Windows:

```powershell
.\reproductions\meson-python-871-no-uv\reproduce.ps1
```

On Linux or macOS:

```sh
sh ./reproductions/meson-python-871-no-uv/reproduce.sh
```

Set `PYTHON` to select the interpreter. For example:

```sh
PYTHON=python3.13 sh ./reproductions/meson-python-871-no-uv/reproduce.sh
```

The runners create `.venv`, install pinned known-working versions of the
required tools, and install this reproduction editably. The editable install
uses `--no-build-isolation` deliberately: meson-python records its rebuild
command, which must refer to the persistent Meson and Ninja installation in
`.venv`, not a temporary isolated build environment.

The direct run exits successfully because `MesonpyMetaFinder` constructs a
spec with `SourceFileLoader` without consulting the path hook installed by
beartype. The monitored run attributes both package imports to that finder and
records the preceding `sys.path_hooks` insertion.
