# Choose capture

Start with defaults. Enable more capture when the report says it could not
observe something your explanation depends on.

## Default capture

The default installation enables:

| Capability | What it records |
| --- | --- |
| Import audit | Starts of uncached import searches and recovery after direct list replacement |
| Meta-path observation | Changes to [`sys.meta_path`](https://docs.python.org/3/library/sys.html#sys.meta_path) |
| Finder attribution | Calls to writable finder instances and the result they returned |
| Path-hook observation | Changes to [`sys.path_hooks`](https://docs.python.org/3/library/sys.html#sys.path_hooks) |
| Importer cache | Installation/report snapshots and changes seen when metapathology checks the cache |

`sys.path` observation and detailed capture default off.

The three core capabilities are independent. For example, finder attribution
without meta-path observation covers finders present at installation, but not
later additions. The report states that coverage.

## Detailed capture

Detailed capture observes calls inside the
[path-based finder](https://docs.python.org/3/reference/import.html#the-path-based-finder):

| Detailed field | CLI option | Use it when |
| --- | --- | --- |
| `path_hooks` | `--capture-path-hook-calls` | You need to know which hooks accepted or rejected a path |
| `path_entry_finders` | `--capture-path-entry-finder-calls` | You need the calls made by the finder cached for a path |
| `loaders` | `--capture-loader-calls` | Module creation, execution, replacement, or repeated execution matters |
| `import_results` | `--capture-import-results` | You need an exact loaded/failed result for a CPython import search |
| `import_calls` | `--capture-import-calls` | Module-cache hits, relative levels, or `fromlist` arguments matter |

Enable all detailed mechanisms from the CLI:

```console
python -m metapathology --detailed-capture app.py
```

Or from the API without a second configuration object:

```python
metapathology.install(
    capture=metapathology.CaptureConfig(detailed=True),
)
```

Select individual mechanisms only for a focused investigation:

```python
metapathology.install(
    capture=metapathology.CaptureConfig(
        detailed=metapathology.DetailedCaptureConfig(
            loaders=True,
            import_results=True,
        )
    )
)
```

Detailed capture is intentionally expensive and retains every event. See
[Speed and memory](performance.md).

It also wraps selected foreign callables. A wrapped path hook is not identical
to the original callable while monitoring is active, and code that compares
callable identities may notice. Metapathology delegates unchanged and restores
the original objects on uninstall, but this is why detailed capture is not the
default.

## Report-time checks

The standard-path check defaults on. It calls
[`PathFinder.find_spec()`](https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder.find_spec)
for selected modules that a custom finder found. This answers whether the
standard path search finds the module *when the report is built* and whether
its loader or locations differ.

The displaced-finder check defaults off. It revisits up to 16 importer-cache
changes where a path-entry finder failed, then checks the displaced finder. It
requires importer-cache capture and detailed path-entry-finder calls. Enabling
the check supplies those prerequisites only when they were not explicitly
disabled.

If you explicitly disable a required capture mechanism, the check remains
requested but is reported as unavailable, with the missing requirement listed.

## Unsafe import-branch exploration

Use this only when the report shows a skipped finder or hook and you need to
know what it returns during the failing import.

```console
python -m metapathology --unsafe-explore-import-branches app.py
```

Run it in a disposable process or container. It executes third-party code that
the import skipped. That code can change state, perform I/O, hang, or exit.
Metapathology does not undo those effects.

### What it calls

After the real import chooses a result or raises, metapathology calls the
remaining direct candidates in order:

- later entries in `sys.meta_path`;
- later `sys.path_hooks` and each returned finder; and
- later entries in the active search path.

It stops there. It does not explore new branches created by those calls or
execute alternative loaders.

### How to use the result

An explored result answers only: “What did this candidate return when called
now?” It does not show which finder would have won. Check the timeline for
earlier explored calls and state changes before trusting a later answer.

Calls are synchronous and uncapped. Shorten the reproduction if it becomes too
slow or retains too much data. An existing profiler or an explicitly disabled
prerequisite produces partial coverage; missing records are then inconclusive.
Uninstall restores metapathology's instrumentation, not third-party side
effects.
