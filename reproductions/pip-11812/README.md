# pip#11812 reduced reproduction

This is a reduced, network-free reproduction of
[pip#11812](https://github.com/pypa/pip/issues/11812). It retains the import
interaction from the issue: an editable-install meta-path finder inherited by
the build process precedes `PathFinder`, while a different copy of the same
backend is placed first on the requested `backend-path`.

From the repository root on Windows:

```powershell
.\reproductions\pip-11812\reproduce.ps1
```

Both runs load `installed-backend/my_backend.py` and print the historical
`BackendInvalid` condition. The monitored run records `_EditableFinder`
claiming `my_backend` and reports a bypass because a `PathFinder` replay chooses
`candidate-backend/my_backend.py`. The harness omits pip's VCS checkout and
build-isolation setup, which are not necessary to exercise the contention.
