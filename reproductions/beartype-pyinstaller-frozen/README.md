# beartype + PyInstaller frozen import failure

The same bug is tracked in two places:
[beartype#599](https://github.com/beartype/beartype/issues/599) and
[PyInstaller#9324](https://github.com/pyinstaller/pyinstaller/issues/9324). A
PyInstaller-frozen application that (transitively) imports
[`py-key-value-aio`](https://pypi.org/project/py-key-value-aio/) fails at
runtime with `ModuleNotFoundError` for modules that are genuinely bundled.

Unlike the other reproductions, this one runs a real frozen executable rather
than a script under the `metapathology` command. The failure only exists inside
the frozen import environment, so `metapathology.install()` is called from
inside the app (the documented API entry point for when a CLI wrapper is
impossible) and writes its report before the process exits.

## Root cause

`py-key-value-aio`'s package initializer calls
`beartype.claw.beartype_this_package()`, which runs `add_beartype_pathhook()`.
That helper:

1. prepends a beartype `FileFinder` to the front of `sys.path_hooks`, and
2. clears `sys.path_importer_cache`.

Run normally this is harmless: every module is backed by a real `.py` file that
the beartype path hook can load. Frozen, the modules live only in PyInstaller's
PYZ archive and are served by `PyiFrozenFinder`. Once beartype's hook sits ahead
of PyInstaller's on the `_MEIPASS` path entries and the importer cache is
cleared, the next archive-only import cannot be resolved and fails — after which
the cascade takes down even stdlib modules like `json` or `asyncio`.

## Run it

```powershell
.\reproductions\beartype-pyinstaller-frozen\reproduce.ps1
```

```sh
reproductions/beartype-pyinstaller-frozen/reproduce.sh
```

Each runner performs four steps:

1. **Unfrozen control** — `python app.py` imports successfully.
2. **Frozen without the fix** — the bundled `app_nofix` exits non-zero with a
   `ModuleNotFoundError` for a bundled module.
3. **Frozen under metapathology** — `app_metapathology` reproduces the failure
   and writes `dist/mp_report.txt`.
4. **Frozen with the runtime-hook fix** — `app_fixed` neutralizes beartype's
   path hook when frozen (`rth_beartype_frozen.py`) and imports successfully.

## Expected diagnosis

metapathology attributes the `sys.path_hooks` insertion and the
`sys.path_importer_cache` churn to `beartype_this_package()`, with a stack trace
reaching `add_beartype_pathhook`, and shows PyInstaller's `PyiFrozenFinder`
repopulating the cache afterward. A representative report is saved in
[`report.txt`](report.txt):

```text
-- sys.path_hooks mutations (1) --
#361 insert +[path_hook_for_FileFinder id 0x...]
    path_hooks after: [path_hook_for_FileFinder ..., zipimporter, method, path_hook_for_FileFinder ...]
    at beartype/claw/_importlib/clawimpmain.py:84 in add_beartype_pathhook
    at beartype/claw/_package/clawpkgmain.py:177 in hook_packages
    at beartype/claw/_clawmain.py:301 in beartype_this_package
    at key_value/aio/__init__.py:13 in <module>
```

Two caveats, documented rather than hidden:

- The headline verdict reads *no import-hook interference detected*. metapathology
  records the path-hook prepend factually but does not raise it to a **finding**:
  on an ordinary interpreter a prepended path hook is not itself a bug. The
  evidence lives in the mutation and importer-cache sections, correlated by
  timeline position with the failed imports.
- Deep diagnostics (`deep=True`) are left **off** because they are not needed to
  attribute this failure; the path-hook mutation and importer-cache clear are
  captured without them.
