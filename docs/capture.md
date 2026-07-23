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
