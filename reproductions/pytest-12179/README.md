# pytest#12179 reproduction

This reproduces [pytest#12179](https://github.com/pytest-dev/pytest/issues/12179)
with the reported pytest 8.1.1 and boto 2.49.0 on Python 3.10. The harness
loads boto's vendored `six.py` directly because later Python 3.10 patch
releases reject an unrelated invalid escape in boto's package initializer.
That is the file which installs `_SixMetaPathImporter`; the importer implements
the legacy `find_module` API but not `find_spec`. Pytest's importlib import mode
directly called `find_spec` on every `sys.meta_path` entry and raised
`AttributeError`.

From the repository root on Windows:

```powershell
.\reproductions\pytest-12179\reproduce.ps1
```

Both runs are expected to fail during collection. The monitored harness also
asserts that `_SixMetaPathImporter` is classified as legacy-only, links it to
the insertion mutation, and verifies that mutation's stack names boto's
vendored `six.py`. It prints the text report after checking the JSON evidence.
This is not a
finder-winner or `sys.path_hooks` bypass bug; the diagnostic value is identifying
which third party installed the incompatible meta-path entry and when.
