# importlib_resources#311 reproduction

This reproduces [importlib_resources#311](https://github.com/python/importlib_resources/issues/311)
with `importlib_resources` 6.4.0. The bug appears when a namespace package's
search path includes the synthetic `__editable__.*.__path_hook__` marker that
setuptools editable installs add. `importlib_resources.files()` incorrectly
tries to treat that marker as a real directory.

From the repository root on Windows:

```powershell
.\reproductions\importlib-resources-311\reproduce.ps1
```

The direct run is expected to fail with `ValueError: Invalid path` on this
interpreter. The monitored run should fail the same way, then report the
injected editable-install path hook entry that made the namespace package path
look non-directory-like.
