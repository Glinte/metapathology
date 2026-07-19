# Reading the report

The report leads with a verdict and puts detail below it. Read top to bottom
and stop when you have your answer:

1. **Header** — how your program finished, a one-line verdict, and the state
   of `sys.meta_path` and `sys.path_hooks`.
2. **Findings** — numbered problems, most severe first, each with its
   evidence.
3. **Comparisons and summaries** — modules found by a custom finder, imports
   that never produced a module, and per-finder call counts.
4. **Event timeline and detail sections** — everything that was recorded, in
   order, with stack traces for each change to `sys.meta_path` and
   `sys.path_hooks`.

Paths under the working directory are shown relative to it (the header names
the base directory), and object ids like `0x2a30...` appear only when two
displayed objects would otherwise look identical. The JSON report always
keeps absolute paths and full identity data; see [JSON report](#json-report).

## Header

The first lines state how your program finished (`target outcome:`) and what
the evidence says (`verdict:`), naming the most severe finding when there is
one. After that:

- `monitoring:` lists which mechanisms were active.
- `sys.meta_path` and `sys.path_hooks` snapshots are shown once when
  unchanged, or as `at install:` / `now:` pairs when something changed them.
- `sys.path_importer_cache: 17 -> 34 entries` counts cached path entries at
  install and at report time.
- `modules imported since install:` counts new `sys.modules` entries.

`BuiltinImporter`, `FrozenImporter`, and `PathFinder` appear as "standard
CPython finders left unwrapped (expected)". They handle built-in, frozen, and
path-based imports respectively. They are classes shared by the interpreter,
so metapathology deliberately does not modify them; this is normal and not a
sign of a degraded installation.

A finder installed by well-known environment tooling gets a note under the
snapshot, such as `_Finder is installed by virtualenv at startup; its
presence is expected`. The note is display-only and never affects findings.

## Findings

Findings are numbered problems, ordered by severity: `actionable` (evidence
points at a concrete problem), `warning` (a compatibility or correctness
risk), or `informational`. Each block names the module and finder involved,
lists its supporting events, ends with a sentence stating the evidence level
and its limits, and links to the matching section below. Findings are
diagnostic leads, not verdicts — the last line of each block tells you how
much to trust it.

A run with no findings states that in one sentence. Background for all of
these sections: [How it works](concepts.md) explains finders, module specs,
and the module cache in a few minutes' reading.

### namespace-truncation

A custom finder answered for a
[namespace package](https://docs.python.org/3/reference/import.html#namespace-packages)
but returned fewer search locations than the standard path search finds, and
an import of one of its submodules failed.

*Background.* A namespace package's contents can be spread over several
directories; its `__path__` lists all of them. A finder that rebuilds this
list — editable installs commonly do — can accidentally omit directories,
and any submodule that only exists in an omitted directory becomes
unimportable, even though it is right there on disk.

*What to do.* The finding names the omitted location and the finder. Check
how that finder (usually an editable-install hook from your build backend)
was configured; reinstalling the affected package, or installing it
non-editable, typically restores the full namespace. Report the omission to
the tool that installed the finder.

### no-spec

A module is in `sys.modules` with no
[`__spec__`](https://docs.python.org/3/reference/import.html#import-related-module-attributes)
attribute and no recorded finder call.

*Background.* Every module loaded through the normal import machinery gets a
module spec describing how it was found. A module without one was likely
created manually (`types.ModuleType(...)` inserted into `sys.modules`) or
executed directly with loader APIs. Import hooks never saw it, so tools that
rely on hooks (assertion rewriters, type-checking instrumenters) cannot have
processed it.

*What to do.* Usually informational — several stdlib modules (for example
`pyexpat.errors`) are created this way and are harmless. Investigate only if
the module is one that another import hook was supposed to process; then
find the code that creates it and load it through
`importlib.import_module()` instead.

### finder-side-effect

A finder changed the target module's `sys.modules` entry even though it
returned `None` or raised.

*Background.* `find_spec()` is supposed to answer "can you locate this
module?" without loading anything. A finder that inserts, removes, or
replaces `sys.modules` entries while answering changes what every later
import of that name sees.

*What to do.* The finding shows the before/after state at the finder call.
Read that finder's `find_spec` implementation; a common cause is importing
the target (or a sibling) as part of deciding whether to handle it. Report
it to the finder's maintainer with the event numbers from the report.

### module-replacement

A loader call began and ended with different module objects. Requires
`--deep-loaders`.

*Background.* Python puts the new module object in `sys.modules` *before*
executing it, so recursive imports see the same object. A loader (or the
module's own code) that swaps in a different object afterwards splits
identity: code holding a reference from before the swap has a different
object than later imports receive, so attribute patches and state on one are
invisible on the other.

*What to do.* The finding shows both object identities. If a library does
this intentionally (lazy-loading proxies do), be careful about holding early
references to it. If not intentional, the loader named in the finding is the
place to look.

### legacy-finder-contract

A finder on `sys.meta_path` has a `find_module` method but no `find_spec`.

*Background.* `find_module` is the pre-Python-3.4 finder protocol.
[CPython 3.12 removed the fallback](https://docs.python.org/3/whatsnew/3.12.html#importlib)
that called it, so on 3.12+ the import system silently skips this finder —
whatever it was supposed to provide simply never happens. Third-party code
that iterates `sys.meta_path` itself may fail with `AttributeError` on any
version (the trigger for [pytest#12179](https://github.com/pytest-dev/pytest/issues/12179)).

*What to do.* Find what installed the finder (the report links the
`sys.meta_path` change that added it, with a stack trace) and upgrade it —
this is typically a very old vendored `six` or similar compatibility shim.
If it cannot be upgraded, removing it from `sys.meta_path` is usually safe
on 3.12+ where it is never called anyway.

### path-hook-shadow

Two different [path hooks](https://docs.python.org/3/reference/import.html#path-entry-finders)
accepted the same path entry. Requires `--deep-path-hooks`.

*Background.* When `PathFinder` meets a new path entry, it tries the
factories in `sys.path_hooks` in order and caches the first finder that
accepts. The second hook never handles that path, even though it also
claimed to understand it — a common way for two instrumentation tools to
conflict.

*What to do.* As a workaround, check `sys.path_hooks` order in the header
and decide which behavior you need more: importing the tool that must win
first (or re-registering its hook at the front), then clearing
`sys.path_importer_cache`, hands it the contested paths — but the other
hook's behavior is now the one silently lost.

The real fix is to make the hooks cooperate instead of compete, and that is
a change inside one of the two tools: the winning hook's path entry finder
can wrap or subclass the standard
[`FileFinder`](https://docs.python.org/3/library/importlib.html#importlib.machinery.FileFinder)
(or delegate to the finder the other hook would have created) so both
behaviors run for the same path. This is how tools like pytest's assertion
rewriter compose with the standard machinery. Report the conflict upstream
to both projects and attach this report; the event numbers show exactly
which paths were contested and which hook won.

### failed-after-mutation

An import failed after a recorded change to `sys.meta_path`,
`sys.path_hooks`, or `sys.path_importer_cache`. Requires `--deep-import-outcomes`.

*Background.* Removing or reordering finders and hooks mid-run changes which
imports can succeed afterwards.

*What to do.* The finding links both the mutation (with its stack trace) and
the failed import. Order alone does not prove causation — confirm by
checking whether the removed/moved finder was the one that could have found
the failed module.

## Modules found by a custom finder

When a finder other than `PathFinder` found a module whose source lives on
the filesystem, this section compares two results:

- **during the run** — the spec the custom finder actually returned, as
  recorded when it happened;
- **standard search at report time** — what
  [`PathFinder.find_spec()`](https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder)
  finds for the same name and search path, called fresh while the report is
  being written.

A difference here is evidence, not automatically a problem. Editable
installs, assertion rewriters, and similar tools legitimately use a different
loader than the standard search would. The comparison matters when another
tool needed the standard search to happen — as in
[beartype#556](https://github.com/beartype/beartype/issues/556), where an
editable-install finder found the module first and beartype's path hook never
saw it.

Two caveats, stated once in the section intro:

- The fresh search runs at report time, against current path hooks, cache,
  and filesystem state — any of which may have changed since the import.
- It calls `PathFinder` directly, skipping other custom finders, so it does
  not show which finder would have won had the custom finder been absent.

The `since install:` line notes whether `sys.path_hooks` or the relevant
`sys.path_importer_cache` entries changed between install and report time —
useful when deciding whether the fresh search still reflects import-time
conditions.

## Imports that started but produced no module

Imports that began but left nothing in `sys.modules`. Failed optional
imports (`pwd` on Windows, `fcntl`, try/except import fallbacks) are normal
here. An entry matters when your program actually needed the module — when
the run failed with `ModuleNotFoundError`, the failed module is marked.

## Finder calls

Per-finder totals: how many times each wrapped finder's `find_spec()` was
called and which modules it found. Standard CPython finders are not wrapped,
so their calls do not appear here; imports they handled show up in the next
section instead.

## Imports attributed to standard finders

When no custom finder found a module, the report attributes it to the
standard finder that evidently handled it. Entries marked `[inferred]`
combine import order with module metadata read at report time — the actual
`find_spec()` call was not recorded, so treat them as a reconstruction.
Entries marked `[captured]` come from `--deep-import-outcomes`, which records
the real `PathFinder` result.

## Event timeline

Every recorded event in capture order: import starts, finder calls,
`sys.meta_path` and `sys.path_hooks` changes, importer cache diffs, and
internal errors, all sharing one `#n` numbering that findings reference.

Long runs of routine events (imports where every finder returned `None`)
collapse into a single line; any event referenced by a finding stays
expanded. Set `METAPATHOLOGY_TEXT_TIMELINE=full` to disable collapsing. The
JSON report always lists every event.

Two things this timeline cannot show:

- An `import started:` line means resolution began; it does not say whether
  the import succeeded or which finder won. Imports satisfied from
  `sys.modules` never appear at all.
- Events are numbered in the order the monitor recorded them. With multiple
  threads, that order is consistent but not an exact wall-clock order.

## Detail sections

Below the timeline, each mechanism has its own section with full detail:

- **`sys.meta_path` mutations** — every list operation (append, insert,
  remove, slice assignment, reorder, …) with the resulting list and up to
  five stack frames showing which code made the change. This is the section
  to use when asking "who reordered the finders?".
- **`sys.meta_path` reassignments** — code that replaced the whole list
  (`sys.meta_path = [...]`). Plain assignment cannot be intercepted, so it
  is detected on the next import; the stack shown belongs to that import,
  not to the assignment.
- **`sys.path_hooks` mutations and reassignments** — the same records for
  `sys.path_hooks`. Hooks are identified by name; they are never called or
  wrapped by default monitoring.
- **`sys.path_importer_cache` changes** — paths added, removed, or switched
  to a different path entry finder between snapshots (taken at install,
  around `sys.path_hooks` changes, and at report time). A value of `None`
  means Python recorded that no importer handles that path. Short-lived
  changes between snapshots can be missed.
- **Loaders of imported modules** — modules grouped by the loader recorded
  in their metadata at report time. Only custom loaders and metadata
  problems are shown in text; JSON keeps the full inventory. This is
  report-time state and may differ from how a module was originally loaded.
- **Internal errors** — failures inside metapathology itself, recorded
  instead of breaking your program's imports. Exception text may be omitted
  because formatting a foreign exception during an import can execute
  arbitrary code.

Sections with nothing to show are collapsed into one final
`Nothing was recorded for:` line.

## Deep diagnostics in the report

With `--deep` options active, the header carries a warning (deep mode places
monitor code inline with imports) and the timeline gains lines for path hook
calls, path entry finder calls, and loader `create_module()` /
`exec_module()` calls. `--deep-import-outcomes` adds
`import of 'x': loaded/failed (directly observed)` lines — a definitive
result, stronger evidence than any inference. The header's
`import outcome observation:` line states the coverage achieved, or why the
mechanism was unavailable (for example, another profiler was already
installed).

## JSON report

`render_report(format="json")`, `write_report(..., format="json")`, and
`--report file.json` produce a machine-readable document built from the same
data as the text report, but complete: nothing is collapsed, capped, or
relativized.

The schema is versioned and stable:

```json
{"name": "metapathology.report", "major": 1, "minor": 0}
```

The bundled `metapathology/report.schema.json` file is the contract. Minor
versions only add fields — consumers must ignore unknown fields and tolerate
unknown enum values. Field removals or meaning changes require a new major
version.

Conventions for consumers:

- Top-level sections include `capture`, `snapshots`, `import_attempts`,
  `resolution_routes`, `route_comparisons`, `timeline`, `findings`,
  `summary`, `target_outcome`, and `diagnostics`.
- A field is *omitted* when the concept does not apply, `null` when it
  applies but was not captured, and an empty array when the collection is
  known to be empty.
- `*_ref` fields resolve to IDs within the same document. Object identity
  values are only meaningful within their originating process.
- `timeline` is ordered by capture sequence; findings are in report priority
  order.
- `report_status` is `complete`, `partial` (valid but some instrumentation
  or copy errors occurred), or `generation_failed` (a minimal fallback
  document).

JSON retains absolute paths, `argv`, and stack file names. Review a report
before sharing it outside its original trust boundary.

For what the monitor cannot observe at all, see
[Limitations](limitations.md).
