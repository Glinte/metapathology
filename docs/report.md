# Reading the report

Start with the chronological evidence timeline, then use the detailed sections
to inspect changes to finder order and custom-finder claims before assessing
suspicious findings. The
[library API](api.md#event-records) documents the corresponding structured
event records.

## JSON report

`render_report(format="json")`, `write_report(..., format="json")`, and JSON
file output all use the same cutoff-based report document as the human
renderer. The current experimental schema is identified by:

```json
{"name": "metapathology.report", "major": 0, "minor": 6}
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

## Chronological evidence timeline

The text timeline is the exhaustive, compact projection of the same event list
used by JSON. It interleaves import audit starts, meta-path and path-hook
changes, importer-cache diffs, finder calls, and internal errors. Detailed
mechanism sections remain below it for stacks and fuller values.

Sequence numbers reflect acquisition of the monitor's shared recording lock.
They provide deterministic capture order but do not claim a process-wide
wall-clock order for concurrent threads.

Opt-in deep calls appear as `deep_diagnostic_call` records in JSON and `deep`
lines in text. Their evidence level is `deep_delegation`. A returned, found,
not-found, or raised outcome describes the exact wrapped boundary;
`unobserved_reentrant` explicitly marks a nested call that delegated under the
guard without reconstructing an exact nested trace.

An import-audit line proves only that uncached resolution started. It includes
the copied `sys.meta_path` identity and finder type names plus constant-size
identities/fingerprints for enabled auxiliary mechanisms. It deliberately says
`outcome unknown`: the audit event has no completion signal, does not identify
the winning finder, and does not fire for `sys.modules` cache hits. JSON exposes
these records as `import_audit_start` with `evidence: resolution_started`.
Lower-level importlib entry points may bypass the builtin audit boundary, so a
finder call can legitimately appear without a preceding audit-start event.

## Header

The header shows whether the monitor, path-hook, and importer-cache mechanisms are enabled,
the initial and current `sys.meta_path` and `sys.path_hooks` snapshots, finders
that could not be wrapped, and the number of modules added to `sys.modules`
since installation.

When the [early-site bootstrap](usage.md#observe-later-pth-files) activated the
monitor, the header and JSON `capture.early_site_bootstrap` object identify its
path, selected site-packages directory, activation variable, and lexically
earlier `.pth` files in that directory. Those earlier names are explicitly
outside the event window; other site directories may also have run first.

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

- `[bypass]` — a custom finder claimed a source module, while a report-time
  live [`PathFinder`][path-finder] replay selects a different loader or origin.
  Path-hook tools did not observe the actual import.
- `[unfindable]` — a custom finder claimed a source module that the replay
  cannot find through the standard path machinery at all. This is the stronger
  bypass signal.
- `[no-spec]` — a new `sys.modules` entry has no
  [`__spec__`][module-spec] and no recorded finder claim. It was likely
  created manually or loaded through a route invisible to meta-path finders.

[path-finder]: https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder
[module-spec]: https://docs.python.org/3/reference/import.html#import-related-module-attributes

The replay uses the search path captured with the original claim, but it runs
against the report-time filesystem, path hooks, and importer cache. It is
labeled `live_replay` in JSON and as a "current live PathFinder replay" in the
text report. A package can therefore produce an intentional or time-sensitive
difference.

Each replay-based finding separately includes historical structural evidence.
This identity-only comparison says whether `sys.path_hooks` changed between
the install and report snapshots and whether relevant
`sys.path_importer_cache` entries changed. JSON links the comparison to those
snapshots and to passive cache-diff events. It does not call removed or
invalidated historical finder objects, reconstruct exact import-time state, or
prove that a structural change caused the replay difference.

Extension modules, built-ins, synthetic origins, and modules that predate
installation are not subjected to the source-module bypass check.

## Internal errors

Instrumentation failures are recorded instead of being allowed to break the
target import. This section identifies the failing monitor code path and the
exception type. It intentionally may omit exception text because converting a
foreign exception to text during an import can execute arbitrary code.

For capture boundaries and memory behavior, see
[Limitations and resource behavior](limitations.md).
