# beartype#638 reproduction

This reproduces [beartype#638](https://github.com/beartype/beartype/issues/638)
from its upstream minimal attachment. Coverage 7.13.5 resolves a dotted-module
source target during startup. Importing `bt_repro.foo` transitively activates
the beartype 0.22.9 claw through py-key-value-aio 0.4.4, then re-enters the
claw loader while its state is only partially initialized.

```powershell
.\reproductions\beartype-638\reproduce.ps1
```

The package-level coverage control succeeds. The dotted-module command fails
with an `ImportError` from partially initialized `beartype.claw` state and
metapathology still prints its report. The report also shows pytest's assertion
rewriter claiming pytest-cov modules for which `PathFinder` would select the
claw loader, exposing the competing import-hook paths around the failure.

The bug was fixed after beartype 0.22.9 and closed by upstream commit
`578c633465c58c2e8de7da5bbaf68da6b78dfeaa`.

## Metapathology result

The package-level control exits successfully with one passing test. The
monitored dotted-module command exits with pytest status 3 and preserves the
historical failure before writing its report:

```text
INTERNALERROR> ImportError: cannot import name 'claw_state' from partially
INTERNALERROR> initialized module 'beartype.claw._clawstate'

sys.meta_path at install: [_Finder, BuiltinImporter, FrozenImporter, PathFinder]
sys.meta_path now: [_Finder, AssertionRewritingHook, ...]
-- suspicious findings (6) --
[bypass] 'pytest_cov': claimed by AssertionRewritingHook,
    bypassing sys.path_hooks-based tools
    claimed: loader AssertionRewritingHook, origin '<site-packages>/pytest_cov/__init__.py'
    PathFinder replay: loader BeartypeSourceFileLoader, same origin
    structural evidence: ...
[bypass] 'pytest_cov.plugin': claimed by AssertionRewritingHook, ...
[bypass] 'pytest_cov.engine': claimed by AssertionRewritingHook, ...
-- finder attribution (instrumented finders only) --
AssertionRewritingHook: ... probes, 4 claimed
-- sys.meta_path mutations (3) --
```

The traceback establishes claw loader re-entrancy as the immediate crash. The
report adds the surrounding contention evidence: pytest's meta-path assertion
rewriter claims pytest-cov modules that claw's path-hook loader would otherwise
handle. The three recorded meta-path mutations are pytest installing and later
removing `AssertionRewritingHook`, plus `key_value` installing its
`_DeprecatedModuleFinder`.
