# Historical import-hook reproductions

Each directory contains a pinned `uv` environment and a PowerShell runner for
a historical import-hook issue. Run the scripts from the repository root; each
environment installs the current checkout as the `metapathology` command.

| Issue | Interaction | Expected diagnosis |
| --- | --- | --- |
| [beartype#269](https://github.com/beartype/beartype/issues/269) | `runpy` changes a `-m` submodule's identity to `__main__` | Normal `PathFinder`; no finder contention |
| [beartype#556](https://github.com/beartype/beartype/issues/556) | scikit-build-core editable redirect finder precedes claw's path hook | `ScikitBuildRedirectingFinder` claims `myproject`; neutral resolution route divergence (no findings) |
| [beartype#638](https://github.com/beartype/beartype/issues/638) | Coverage dotted-module lookup, pytest assertion rewriting, and claw loader re-entrancy | Partial-state `ImportError` plus `AssertionRewritingHook` resolution route divergences for the pytest-cov modules |
| [scikit-build-core#1482](https://github.com/scikit-build/scikit-build-core/issues/1482) | Editable redirect finder truncates a shared namespace package's path | `[namespace-truncation]` 'mqt': `ScikitBuildRedirectingFinder` claims the namespace before `PathFinder` |
| [pytest#12179](https://github.com/pytest-dev/pytest/issues/12179) | Boto installs a legacy meta-path importer that pytest calls via `find_spec` | `[legacy-finder-contract]` `_SixMetaPathImporter`; mutation attribution before collection fails |
| wrapt 1.14.2 | Post-import hooks leave wrapped loader metadata in place | Resolution route divergence: loader type mismatch between the chained loader and the original `SourceFileLoader` |
| [importlib_metadata#353](https://github.com/python/importlib_metadata/issues/353) | Current-directory package metadata is skipped even with matching `.egg-info` present | `files("plover")` resolves to `None` instead of package metadata |
| [importlib_resources#311](https://github.com/python/importlib_resources/issues/311) | Editable-install namespace paths leak synthetic `__path_hook__` entries into `files()` | `MultiplexedPath` rejects the non-directory editable marker |
| [pwntools#2737](https://github.com/Gallopsled/pwntools/issues/2737) | `pwnlib.shellcraft.*` still relies on a legacy meta-path importer that only implements `find_module` | `LazyImporter` is a nonstandard legacy finder on `sys.meta_path` |
| [setuptools#3073](https://github.com/pypa/setuptools/issues/3073) | `DistutilsMetaFinder` recursively imports an older setuptools and leaves conflicting `sys.modules` state | Finder attribution around the re-entrant `distutils` import |
| [discord.py#10017](https://github.com/Rapptz/discord.py/issues/10017) | Extension loading executes a second valid-spec module object instead of reusing `sys.modules` | With deep loader diagnostics enabled, `[module-replacement]` 'ext' — an actionable finding for the duplicate `SourceFileLoader` execution |
| [pip#11812](https://github.com/pypa/pip/issues/11812) | An inherited editable finder claims a build backend before pip's `backend-path` | `_EditableFinder` claims the wrong backend; neutral resolution route divergence (no findings) |
| [distributed#7782](https://github.com/dask/distributed/issues/7782) | `PathFinder` finds a cwd namespace package before setuptools' appended editable finder | Initial finder order explains why `_EditableFinder` never receives the import |
| [meson-python#871](https://github.com/mesonbuild/meson-python/issues/871) | Meson editable meta finder constructs source specs without consulting claw's path hook | `MesonpyMetaFinder` claims the package and submodule after claw installs its path hook |
| [mosquito-cfd#22](https://github.com/talmolab/mosquito-cfd/issues/22) | Coverage source discovery imports an eager package and then removes newly loaded NumPy extensions from `sys.modules` | Deep events show a successful `ExtensionFileLoader` load followed by a second failed load of the same extension |
| [rules_python#2009](https://github.com/bazel-contrib/rules_python/issues/2009) | rules_python moves the directly executed coverage package directory to the end of `sys.path`, where `coverage/python.py` still beats the earlier runfiles namespace | Deep path-entry attribution shows `PathFinder` preferring the later regular module over the PEP 420 namespace candidate |
| [Bifrost#418](https://github.com/gobifrost/bifrost/issues/418) follow-up | `VirtualModuleFinder` is inserted before every standard finder instead of only before `PathFinder` | The indexed virtual module shadows CPython's frozen `__hello__`; the control preserves `FrozenImporter` precedence |

Beartype#599 is another confirmed finder-order bug involving PyInstaller's
frozen importer. It is not included: the failure exists only inside a frozen
executable, which cannot be placed under the `metapathology` Python command
without replacing the import environment responsible for the bug.

PyInstaller#9324 has the same limitation: its failure exists inside a frozen
executable, so running the target under the metapathology interpreter would
remove the frozen finder interaction being diagnosed.
