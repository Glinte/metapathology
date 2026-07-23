# Configuration reference

Configuration priority follows one rule:

> An individual CLI/API value wins over its environment variable. An
> individual environment variable wins over the setting for the whole group.
> If none is supplied, the documented default applies.

Every Boolean CLI option also has a `--no-...` form.

## Core capture

| CLI | Environment | API field | Default |
| --- | --- | --- | --- |
| `--import-audit` | `METAPATHOLOGY_IMPORT_AUDIT` | `import_audit` | on |
| `--meta-path` | `METAPATHOLOGY_META_PATH` | `meta_path` | on |
| `--finder-attribution` | `METAPATHOLOGY_FINDER_ATTRIBUTION` | `finder_attribution` | on |
| `--path-hooks` | `METAPATHOLOGY_PATH_HOOKS` | `path_hooks` | on |
| `--importer-cache` | `METAPATHOLOGY_IMPORTER_CACHE` | `importer_cache` | on |
| `--sys-path` | `METAPATHOLOGY_SYS_PATH` | `sys_path` | off |

## Detailed capture

Use `--detailed-capture`, `METAPATHOLOGY_DETAILED_CAPTURE`, or
`CaptureConfig(detailed=True)` to set all detailed mechanisms together.

| CLI | Environment | Detailed field |
| --- | --- | --- |
| `--capture-path-hook-calls` | `METAPATHOLOGY_CAPTURE_PATH_HOOK_CALLS` | `path_hooks` |
| `--capture-path-entry-finder-calls` | `METAPATHOLOGY_CAPTURE_PATH_ENTRY_FINDER_CALLS` | `path_entry_finders` |
| `--capture-loader-calls` | `METAPATHOLOGY_CAPTURE_LOADER_CALLS` | `loaders` |
| `--capture-import-results` | `METAPATHOLOGY_CAPTURE_IMPORT_RESULTS` | `import_results` |
| `--capture-import-calls` | `METAPATHOLOGY_CAPTURE_IMPORT_CALLS` | `import_calls` |

## Checks

Use `--checks`, `METAPATHOLOGY_CHECKS`, or `AnalysisConfig(checks=True)` to
set both checks together.

| CLI | Environment | API field | Default |
| --- | --- | --- | --- |
| `--standard-path-check` | `METAPATHOLOGY_STANDARD_PATH_CHECK` | `standard_path_check` | on |
| `--displaced-finder-check` | `METAPATHOLOGY_DISPLACED_FINDER_CHECK` | `displaced_finder_check` | off |

An individual check setting takes priority. For example,
`AnalysisConfig(checks=True, standard_path_check=False)` enables the
displaced-finder check and disables the standard-path check.

## Unsafe import execution

| CLI | Environment | API keyword | Default |
| --- | --- | --- | --- |
| `--unsafe-explore-import-branches` | `METAPATHOLOGY_UNSAFE_EXPLORE_IMPORT_BRANCHES` | `unsafe_explore_import_branches` | off |

This executes skipped finder and hook code. Read
[Unsafe import-branch exploration](capture.md#unsafe-import-branch-exploration)
before using it.

It enables its required capture mechanisms unless you explicitly disabled one.
It never replaces an existing profiler. Either case is reported as partial
coverage. Loader and `__import__` call capture remain off.

## Report destinations

With no destination option, the CLI writes one text report to standard error.

| CLI/API | Environment | Meaning |
| --- | --- | --- |
| `--report PATH` / `report_destination=` | `METAPATHOLOGY_REPORT` | Infer text from `.txt`/`.text` and JSON from `.json` |
| `--report-text PATH` / `report_text=` | — | Force text output |
| `--report-json PATH` / `report_json=` | — | Force JSON output |
| `--color MODE` / `report_color=` | `METAPATHOLOGY_COLOR` | `auto`, `always`, or `never` |

Destination options are repeatable. `METAPATHOLOGY_REPORT` accepts an
[`os.pathsep`](https://docs.python.org/3/library/os.html#os.pathsep)-separated
list (`;` on Windows, `:` on POSIX). Environment destinations are made
process-safe: `{pid}` is replaced with the process ID, or the ID is inserted
before the extension when the placeholder is absent. This prevents inherited
settings from making parent and child processes overwrite one another.

Use `-` for standard error with `--report-text` or `--report-json`. Parent
directories must already exist. File output is written atomically.

## Environment values

Boolean environment values are case-insensitive:

- true: `1`, `true`, `yes`, `on`
- false: `0`, `false`, `no`, `off`

Invalid values are reported as configuration issues without changing the target
program's import result.

## Active installations

Capture and default analysis settings are frozen for an installation.
Repeating `install()` with the same resolved settings is idempotent. Omitting
the settings reuses the active values. A different setting raises before global
state is changed; call `uninstall()` first.

Report destinations can be reconfigured after the active settings match.
