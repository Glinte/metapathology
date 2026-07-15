# Historical import-hook reproductions

Each directory contains a pinned `uv` environment and a PowerShell runner for
a beartype import-hook issue. Run the scripts from the repository root; each
environment installs the current checkout as the `metapathology` command.

| Issue | Interaction | Expected diagnosis |
| --- | --- | --- |
| [beartype#269](https://github.com/beartype/beartype/issues/269) | `runpy` changes a `-m` submodule's identity to `__main__` | Normal `PathFinder`; no finder contention |
| [beartype#556](https://github.com/beartype/beartype/issues/556) | scikit-build-core editable redirect finder precedes claw's path hook | `ScikitBuildRedirectingFinder` `[bypass]` |
| [beartype#638](https://github.com/beartype/beartype/issues/638) | Coverage dotted-module lookup, pytest assertion rewriting, and claw loader re-entrancy | Partial-state `ImportError` plus assertion-rewriter `[bypass]` findings |

Beartype#599 is another confirmed finder-order bug involving PyInstaller's
frozen importer. It is not included: the failure exists only inside a frozen
executable, which cannot be placed under the `metapathology` Python command
without replacing the import environment responsible for the bug.
