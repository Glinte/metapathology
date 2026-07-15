# beartype#556 reproduction

This is the minimal scikit-build-core editable-install scenario from
[beartype#556](https://github.com/beartype/beartype/issues/556). Its isolated
environment installs the current metapathology checkout as both
`python -m metapathology` and the `metapathology` console command.
The conflicting packages are pinned to scikit-build-core 1.0.1 and beartype
0.22.9 so a later release cannot silently change the reproduction.

From the repository root on Windows:

```powershell
.\reproductions\beartype-556\reproduce.ps1
```

Or run the relevant commands individually:

```powershell
cd reproductions\beartype-556
uv sync
uv run --no-sync myproject
uv run --no-sync metapathology invoke.py
```

The direct command prints `3` twice and exits successfully: the invalid string
argument is not rejected. The monitored command reproduces that behavior, then
reports that `ScikitBuildRedirectingFinder` claimed `myproject` ahead of
`PathFinder`. Its suspicious finding demonstrates that the editable finder
short-circuited the path hooks used by `beartype.claw`.

The finder is installed from a `.pth` file during interpreter startup. It must
therefore appear in metapathology's initial snapshot rather than its mutation
log; no ordinary command can start early enough to witness that insertion.

## Metapathology result

The monitored command prints `3` twice and exits successfully, demonstrating
that the invalid argument was not checked. The normalized report excerpt is:

```text
initial sys.meta_path: [_Finder, ScikitBuildRedirectingFinder,
    BuiltinImporter, FrozenImporter, PathFinder]
-- sys.meta_path mutations (0) --
ScikitBuildRedirectingFinder (...): ... find_spec calls, 1 claimed
    myproject
-- suspicious findings (1) --
[bypass] 'myproject' was claimed by ScikitBuildRedirectingFinder
    (loader _ScikitBuildLoaderWrapper, origin <reproduction>/src/myproject/__init__.py);
    the current live PathFinder replay would use loader BeartypeSourceFileLoader
    (origin <reproduction>/src/myproject/__init__.py).
    sys.path_hooks-based tools were bypassed.
    historical structural evidence: ...
-- internal errors (0) --
```

This directly identifies the cause: scikit-build-core's meta-path finder wins
before `PathFinder` can reach the path hook installed by `beartype.claw`.
