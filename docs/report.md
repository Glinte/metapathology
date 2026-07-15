# Reading the report

Start with changes to finder order, then identify the winning custom finder,
then assess suspicious findings. Sequence numbers order records across event
types even though the text report groups them into sections. The
[library API](api.md#event-records) documents the corresponding structured
event records.

## JSON report

`render_report(format="json")`, `write_report(..., format="json")`, and JSON
file output all use the same cutoff-based report document as the human
renderer. The current experimental schema is identified by:

```json
{"name": "metapathology.report", "major": 0, "minor": 3}
```

Its top-level sections are `tool`, `process`, `capture`, `snapshots`,
`timeline`, `findings`, and `diagnostics`. Timeline events retain their shared
sequence number and receive an `event:<seq>` identifier. Findings contain
structured claim and replay evidence rather than requiring consumers to parse
the human wording.

Schema 0.x is intentionally allowed to change as the remaining snapshot,
timeline, inventory, comparison, and finding models are introduced. A
schema 1.0 review is required before machine consumers treat the shape as
stable. Capacity and completeness are reported per capture mechanism; the
event producers retain all records and therefore grow with observed import
activity. Importer-cache snapshot storage is separately bounded at two full
maps with a replace-latest policy.

## Header

The header shows whether the monitor, path-hook, and importer-cache mechanisms are enabled,
the initial and current `sys.meta_path` and `sys.path_hooks` snapshots, finders
that could not be wrapped, and the number of modules added to `sys.modules`
since installation.

`BuiltinImporter`, `FrozenImporter`, and `PathFinder` normally appear as
"standard CPython finders left unwrapped (expected)." They handle built-in,
frozen, and path-based imports respectively. They are class objects shared by
the interpreter, so metapathology deliberately does not modify them. This is
normal and does not indicate degraded installation. The report later uses a
fresh `PathFinder` call when checking suspicious custom-finder claims.

Any nonstandard entries that could not be wrapped appear separately under
"other finders observed but not instrumented." Their calls are not directly
recorded, so attribution may require elimination.

## `sys.meta_path` mutations

Each record includes:

- the list operation and sequence number;
- finder types added or removed, or an order-change marker;
- the resulting list contents;
- the thread name; and
- up to five relevant stack frames.

Use the stack to locate code that changed finder precedence. The monitor
captures more frames than it displays and filters frames from itself and the
import machinery. Recorded operations include additions, removals, item and
slice replacement, clearing, in-place addition or repetition, and order
changes. This section covers the usual way libraries alter `sys.meta_path`.

## `sys.meta_path` reassignments

Less commonly, code replaces the list itself with an assignment such as
`sys.meta_path = new_list`. Plain attribute assignment cannot be intercepted
at the moment it happens.
Reassignment is detected on the next import, so the displayed stack belongs to
that triggering import, not necessarily to the code that assigned the list.
The report shows the abandoned and replacement contents and notes that
instrumentation was reinstalled.

## `sys.path_hooks` mutations

These records parallel meta-path mutations but identify each hook by object
ID, safe type name, and a callable name when it can be read without foreign
attribute dispatch. Metapathology never wraps or calls a hook factory. The
resulting snapshot shows hook precedence after each operation.

## `sys.path_hooks` reassignments

Direct replacement is detected at the next uncached import by the existing
audit hook. The report therefore shows the triggering import stack rather
than the unknowable assignment stack. Recovery installs an instrumented copy;
the list object originally assigned becomes stale.

## `sys.path_importer_cache` changes

Cache diffs show string paths added, removed, or switched to a different
finder identity. A `None` finder is a negative cache entry. Non-string keys
are counted but never formatted. The text report lists at most 25 changes per
diff; JSON retains every captured change.

Snapshots occur at installation, before and after observed path-hook list
mutations, and at report time. The audit hook only marks a changed
identity/length fingerprint dirty, so short-lived or same-size cache changes
between full observations may be absent. Sequence numbers place retained
diffs relative to the other event mechanisms.

## Finder attribution

Instrumented finders are grouped by finder type and object identity. The
section reports how many `find_spec()` probes occurred and which modules each
finder claimed. A finder claims a module by returning a spec. The report lists
at most 25 claimed modules per finder and reports the omitted count.

Two objects of the same finder class are separate entries because their object
identities differ.

## Suspicious findings

These findings are leads, not verdicts:

- `[bypass]` — a custom finder claimed a source module, while a current
  [`PathFinder`][path-finder] replay selects a different loader or origin.
  Path-hook tools did not observe the actual import.
- `[unfindable]` — a custom finder claimed a source module that the replay
  cannot find through the standard path machinery at all. This is the stronger
  bypass signal.
- `[no-spec]` — a new `sys.modules` entry has no
  [`__spec__`][module-spec] and no recorded finder claim. It was likely
  created manually or loaded through a route invisible to meta-path finders.

[path-finder]: https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder
[module-spec]: https://docs.python.org/3/reference/import.html#import-related-module-attributes

The replay uses the search path captured at import time, but it runs against
the current filesystem and finder state. A package can therefore produce an
intentional or time-sensitive difference. Extension modules, built-ins,
synthetic origins, and modules that predate installation are not subjected to
the source-module bypass check.

## Internal errors

Instrumentation failures are recorded instead of being allowed to break the
target import. This section identifies the failing monitor code path and the
exception type. It intentionally may omit exception text because converting a
foreign exception to text during an import can execute arbitrary code.

For capture boundaries and memory behavior, see
[Limitations and resource behavior](limitations.md).
