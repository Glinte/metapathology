# Reading the report

Suspicious findings lead the report, right after the header; finder
attribution, the chronological evidence timeline, and the detailed mechanism
sections follow for the supporting evidence. Detail sections with nothing to
show are collapsed into one final `nothing recorded:` line. The
[library API](api.md#event-records) documents the corresponding structured
event records.

Text output favors readability: paths under the reported working directory
are shown relative to it (the header names the base with
`paths shown relative to:`), and object ids appear only when two displayed
objects would otherwise be indistinguishable. JSON always keeps absolute
paths and full identity metadata.

## JSON report

`render_report(format="json")`, `write_report(..., format="json")`, and JSON
file output all use the same cutoff-based report document as the human
renderer. The current experimental schema is identified by:

```json
{"name": "metapathology.report", "major": 0, "minor": 9}
```

Its top-level sections are `tool`, `process`, `capture`, `snapshots`, `loader_inventory`,
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

Context shared by every line is hoisted into the timeline preamble instead of
being repeated per event: a single-threaded capture states
`All events on thread X.` once and omits per-line `[thread ...]` markers, and
snapshot identities that stay stable across every audited import are declared
stable once. When an identity does change, the audit line that observed it
carries indented continuation lines describing only the changed mechanism.

Opt-in deep calls appear as `deep_diagnostic_call` records in JSON and `deep`
lines in text. Their evidence level is `deep_delegation`. A returned, found,
not-found, or raised outcome describes the exact wrapped boundary;
`unobserved_reentrant` explicitly marks a nested call that delegated under the
guard without reconstructing an exact nested trace.
Mutable loaders expose separate `loader_create_module` and
`loader_exec_module` boundaries when those methods already exist. Names come
from each call's actual spec or module metadata, so one loader shared by
multiple modules remains distinguishable.

`--deep-import-outcomes` adds paired `deep_import_event` records around
CPython's `_find_and_load` invocation. A directly observed completion may say
`loaded` or `failed`; this is stronger than finder or post-hoc module-cache
evidence. The header and JSON `deep_import_outcomes` mechanism always report
the runtime coverage or refusal reason. On CPython 3.10--3.14 the observer
covers the installing thread and future `threading` threads. It cannot cover
already-running threads or guarantee low-level `_thread` coverage, and normal
cache hits remain invisible because they bypass `_find_and_load`. If either
the current-thread or future-thread profiler slot is occupied, activation is
refused without replacing or chaining that callback.

An import-audit line proves only that uncached resolution started. The record
still captures the copied `sys.meta_path` identity and finder type names plus
constant-size identities/fingerprints for enabled auxiliary mechanisms; text
shows them only on deviation, as described above. The preamble states that
the audit event has no outcome or winner signal: it has no completion signal,
does not identify the winning finder, and does not fire for `sys.modules`
cache hits. JSON exposes these records as `import_audit_start` with
`evidence: resolution_started`.
Lower-level importlib entry points may bypass the builtin audit boundary, so a
finder call can legitimately appear without a preceding audit-start event.

## Header

The header opens with one `monitoring:` line naming the enabled mechanisms
(disabled mechanisms, inactive deep diagnostics, and an inactive early-site
bootstrap are noted in parentheses). It then shows the `sys.meta_path` and
`sys.path_hooks` snapshots — collapsed to a single
`(unchanged since install)` line when the install and report snapshots are
identical, and split into `at install:` / `now:` lines otherwise — the
importer-cache entry counts as `initial -> current`, finders that could not
be wrapped, and the number of modules added to `sys.modules` since
installation.

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

## Standard resolution outcomes

Default reports may attribute built-in, frozen, source, bytecode, extension,
zip, and namespace results to the matching standard finder. These records are
explicitly `inferred`: they combine an import audit start, its meta-path order,
absence of a contradictory captured custom claim, and post-hoc loader
metadata. They can explain why a later finder was unreachable, but they are
not historical `find_spec()` calls.

When deep import outcomes are enabled and CPython exposes a supported Python
code boundary for `PathFinder.find_spec`, the existing reversible profiler
captures its aggregate returned spec. Such records are `captured`, use the
import-time phase, and link to the exact timeline event and import attempt.
Deep path-entry finder calls are linked as component evidence when available;
their path remains null when it was not known at instrumentation time.

The header and JSON capture mechanisms report whether aggregate capture is
active, unsupported on the running CPython, or unavailable because another
profiler was already installed. In either unavailable case, the report keeps
the conservative inference rather than replacing or proxying `PathFinder`.
Live `PathFinder` replay remains separately labeled report-time
counterfactual evidence and never upgrades attribution.

Any nonstandard entries that could not be wrapped appear separately under
"other finders observed but not instrumented." Their calls are not directly
recorded, so attribution may require elimination.

## Post-hoc loader inventory

The loader inventory covers every safely inspectable string-keyed
`sys.modules` entry. It prefers a non-`None` `module.__spec__.loader`, falls
back to `module.__loader__`, and keeps modules without either value in a
separate group. A disagreement between the two loader identities is labeled
as metadata evidence, not as a package defect.

This is a report-time snapshot, not exact historical attribution: modules may
have replaced their metadata or disappeared before reporting. The inventory
includes modules that predate installation, unlike the separate
`modules_since_install` list. Text output groups modules by loader type name:
custom loader groups list module names and origins (at most 25 per group),
while groups whose type name matches a standard CPython loader are summarized
as counts, always listing their metadata disagreements. Cached paths are
omitted from text. JSON retains every copied record, grouped by loader type
and object identity.

Module metadata is read from real module dictionaries through the base
`ModuleType` implementation. This bypasses module-subclass overrides and does
not materialize `LazyLoader` modules. Arbitrary module-like values, inaccessible
module subclasses, malformed metadata, and non-string keys are reported as
unavailable or omitted without dynamic attribute access.

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

These records parallel meta-path mutations but identify each hook by a
callable name when it can be read without foreign attribute dispatch, else by
its safe type name; the object ID is added only when that label is ambiguous. Metapathology never wraps or calls a hook factory. The
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
- `[namespace-truncation]` — both claims describe namespace packages, but the
  custom claim omits locations returned by standard path resolution.
- `[package-displacement]` — package-versus-module status differs.
- `[origin-displacement]` — both resolutions find concrete modules at
  different origins.
- `[spec-difference]` — another comparable spec field differs, such as cached
  path or package-path ordering/extension.
- `[no-spec]` — a new `sys.modules` entry has no
  [`__spec__`][module-spec] and no recorded finder claim. It was likely
  created manually or loaded through a route invisible to meta-path finders.
- `[finder-side-effect]` — a captured finder boundary changed the target's
  `sys.modules` state before the finder returned `None` or raised. The report
  does not infer which nested action caused the delta.
- `[module-replacement]` — an opt-in deep loader boundary began and ended with
  different non-`None` module object identities. Matching valid specs do not
  hide this identity change; intermediate objects and internal steps remain
  unknown.

[path-finder]: https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder
[module-spec]: https://docs.python.org/3/reference/import.html#import-related-module-attributes

The replay uses the search path captured with the original claim, but it runs
against the report-time filesystem, path hooks, and importer cache. It is
labeled `live_replay` in JSON; in text each finding pairs a `claimed:` line
with a `PathFinder replay:` line (collapsing to `same origin` when only the
loader differs) and summarizes the field-level comparison on a
`differences (import-time claim vs live replay):` line. A package can
therefore produce an intentional or time-sensitive difference.

Finder-call timeline records include import-time spec summaries. Exact string
values and exact list/tuple package paths are copied before the spec is returned
to importlib. Non-string values are represented only by safe type and identity
metadata. A foreign package-path sequence is marked `deferred` rather than
iterated in the import hot path. Namespace paths returned by the live replay
are copied during reporting and marked `post_hoc`. Field comparisons expose
omitted, additional, and reordered locations without presenting replay state
as exact historical proof.

Finder and mutable-loader records also expose constant-size target-module
states: `missing`, explicit `none`, `object` with safe identity/type metadata,
or `unavailable`. Text timelines omit unchanged pairs but JSON retains them.
Object identities are process-local evidence and are not stable across runs.

Each replay-based finding separately includes a `structural evidence:` line.
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
