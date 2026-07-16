# importlib_metadata#353 reproduction

This reproduces [importlib_metadata#353](https://github.com/python/importlib_metadata/issues/353),
where `importlib_metadata.files()` fails to find metadata in the current
directory even when a matching `.egg-info` directory is present. The original
report raised `PackageNotFoundError`; on this interpreter the same lookup gap
manifests as `files("plover")` returning `None`.

From the repository root on Windows:

```powershell
.\reproductions\importlib-metadata-486\reproduce.ps1
```

The direct run is expected to fail the assertion. The monitored run should fail
the same way, then show that the current-directory package metadata never
produced a successful finder claim. This is a distribution discovery bug rather
than a `find_spec()` contention bug.
