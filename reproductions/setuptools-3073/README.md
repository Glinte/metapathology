# setuptools#3073 reproduction

This reproduces [setuptools#3073](https://github.com/pypa/setuptools/issues/3073)
with setuptools 60.7.0 and Python 3.10. The environment's newer setuptools
installs `DistutilsMetaFinder` during site initialization, while an older
setuptools is placed first on `PYTHONPATH`.

From the repository root on Windows:

```powershell
.\reproductions\setuptools-3073\reproduce.ps1
```

Both runs are expected to fail because `distutils.util` exists in
`sys.modules` but is not attached to the final `distutils` module. During its
attempt to claim `distutils`, `DistutilsMetaFinder` imports
`setuptools._distutils`; the older setuptools recursively imports distutils,
then lacks `_distutils`, and the outer resolution falls through after already
changing module-cache state. The monitored run identifies the preinstalled
finder and records its outer decision without changing the failure.
