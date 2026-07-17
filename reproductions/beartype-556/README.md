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
sys.meta_path (unchanged since install): [_Finder, ScikitBuildRedirectingFinder,
    BuiltinImporter, FrozenImporter, PathFinder]
-- resolution route divergences (1) --
'myproject': captured claim compared with an independent standard path probe
    captured route: ScikitBuildRedirectingFinder, loader _ScikitBuildLoaderWrapper, ...
    standard path probe: PathFinder, loader BeartypeSourceFileLoader, ...
    route differences (captured vs live probe): loader type
    interpretation: the probe does not predict which finder would win if the captured finder were absent
    structural evidence: ...
-- finder attribution (instrumented finders only) --
ScikitBuildRedirectingFinder: ... probes, 1 claimed
    myproject
nothing recorded: sys.meta_path mutations, ..., internal errors
```

This identifies the observed mechanics: scikit-build-core's meta-path finder
claimed the module before `PathFinder` was reached, while an independent
report-time standard-path probe selected beartype's loader. The difference is
route evidence, not a claim that `PathFinder` would necessarily win under a
different meta-path order.
