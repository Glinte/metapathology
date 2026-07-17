# Reading the report

The report leads with a verdict: the first lines after the title state how
the monitored target finished (`target outcome:`) and a one-sentence reading
of the evidence (`verdict:`), naming the most severe finding when one exists.
The numbered findings narrative follows the header; neutral route
divergences, unresolved imports, finder attribution, the chronological
evidence timeline, and the detailed mechanism sections follow for the
supporting evidence. Detail sections with nothing to show are collapsed into
one final `Nothing was recorded for:` line. The
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
renderer. The stable schema is identified by:

```json
{"name": "metapathology.report", "major": 1, "minor": 0}
```

Its top-level sections include `tool`, `process`, `capture`, `snapshots`,
`loader_inventory`, `import_attempts`, `standard_resolutions`,
`resolution_routes`, `route_comparisons`, `timeline`, `findings`,
`explanations`, `summary`, `target_outcome`, and `diagnostics`. The
`summary` object carries severity counts, the unresolved-import count, and
references to the top finding and explanation — never prose; headline
sentences exist only in the text rendering. `target_outcome` records the
target's completion kind, exception type name, the missing module name for
`ImportError` subclasses, and the exit status; it is `null` unless the CLI
(or an embedder calling `Monitor.record_target_outcome`) recorded one. Timeline events retain their shared sequence
number and receive an `event:<seq>` identifier. Findings reference
document-scoped routes and comparisons rather than duplicating probe evidence
or requiring consumers to parse human wording.

The bundled `metapathology/report.schema.json` file is the language-neutral
contract. A major-version change may remove or rename a field, change its
type or meaning, or remove an enum value. A minor-version change is additive:
consumers must ignore unknown object fields and tolerate unknown enum values.
Existing fields and enum meanings do not change within one major version.

Every report has a `report_status`: `complete` means projection completed
without recorded instrumentation or report-copy errors, `partial` means a
valid report contains such errors, and `generation_failed` is the canonical
minimal document produced when ordinary report generation raises. All three
statuses have the same required top-level sections.

Optional-value conventions are semantic. A field is omitted only when the
concept does not apply to that record kind. `null` means the concept applies
but its value is unknown, unavailable, or not captured. An empty array means
the collection is known to contain no retained entries. IDs and `*_ref` /
`*_refs` fields are document-scoped; every reference resolves within the same
document and IDs are unique. Object and finder identity values are meaningful
only within their originating process and report.

Array ordering is contractual where it carries evidence: `timeline` is in
increasing capture sequence, import attempts are in start order, snapshots
retain their documented install/report order, and meta-path/path-hook entries
retain finder precedence. Findings and explanations are in deterministic
report priority order. Inventory group and module arrays are sorted by their
documented safe type/name keys. Arrays described as sets of signals,
limitations, references, or changed paths are semantically unordered even
when output happens to be deterministic.

`generated_at` is a second-precision UTC report-capture timestamp, not process
start time or a per-event clock. JSON intentionally retains absolute paths,
`argv`, `cwd`, executable paths, module origins, and stack filenames. Review a
report before sharing it outside its original trust boundary.

Capacity, completeness, overflow, and synchronous shutdown behavior are
reported per capture mechanism; the
event producers retain all records and therefore grow with observed import
activity. Importer-cache snapshot storage is separately bounded at two full
maps with a replace-latest policy.

## Chronological evidence timeline

The text timeline is a bounded projection of the same event list used by
JSON. It interleaves uncached import starts, meta-path and path-hook changes,
importer-cache diffs, finder probes, and internal errors. Long runs of
routine events — consecutive uncached import starts and declined probes with
no claims, mutations, or errors — collapse into one line stating the sequence
range and counts; any event referenced by a finding or explanation always
renders expanded with one line of context on each side. Set
`METAPATHOLOGY_TEXT_TIMELINE=full` to restore the exhaustive per-event
rendering; the JSON `timeline` is always exhaustive. Detailed mechanism
sections remain below it for stacks and fuller values.

Sequence numbers reflect acquisition of the monitor's shared recording lock.
They provide deterministic capture order but do not claim a process-wide
wall-clock order for concurrent threads.

Context shared by every line is hoisted into the timeline preamble instead of
being repeated per event: a single-threaded capture states
`All events on thread X.` once and omits per-line `[thread ...]` markers, and
snapshot identities that stay stable across every audited import are declared
stable once. When an identity does change, the audit line that observed it
carries indented continuation lines describing only the changed mechanism.
Bounded sections point at the exhaustive record with a consistent
`details in JSON` trailer.

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

An `uncached import started:` line proves only that uncached resolution
started. The record still captures the copied `sys.meta_path` identity and
finder type names plus constant-size identities/fingerprints for enabled
auxiliary mechanisms; text shows them only on deviation, as described above.
The event has no completion signal, does not identify the winning finder,
and does not fire for `sys.modules` cache hits. JSON exposes these records
as `import_audit_start` with `evidence: resolution_started`.
Lower-level importlib entry points may bypass the builtin audit boundary, so a
finder call can legitimately appear without a preceding audit-start event.

## Header

The header opens with the `target outcome:` and `verdict:` lines described
above, then one `monitoring:` line naming the enabled mechanisms (disabled
default mechanisms and unused opt-ins are noted in parentheses).
Finder classes installed by well-known environment tooling carry a
display-only annotation, for example `_Finder (virtualenv startup,
expected)`; the annotation never affects severity or findings. It then shows the `sys.meta_path` and
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
normal and does not indicate degraded installation. The report may later use a
fresh `PathFinder` call for an independent standard-path probe of a captured
custom claim.

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
The independent standard-path probe remains separately labeled report-time
evidence and never upgrades attribution or predicts an alternative winner.

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
are counted but never formatted. The text report lists full entries only for
paths relevant to a finding or captured route (at most 25 per diff) and
summarizes the rest as counts; JSON retains every captured change.

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

## Finder API contracts

The exhaustive JSON `finder_contracts` inventory records each observed
meta-path object's `find_spec` and `find_module` availability, the raw
dictionary evidence source, its first observed position, and an insertion
event reference when available. The bounded text section prioritizes
legacy-only, protocol-less, and indeterminate custom entries, followed by the
standard CPython class entries.

These labels are compatibility risks rather than defect verdicts. CPython
3.10 and 3.11 can fall back to a callable `find_module`, CPython 3.12 and later
cannot, and third-party code that directly iterates `sys.meta_path` may require
`find_spec` on every version. An indeterminate result means safe raw inspection
encountered a descriptor, unusual dictionary, or inspection error; the report
does not resolve it by executing foreign code.

## Resolution routes

Each captured custom claim produces a `captured_claim` route. Reporting may
also produce an independent `standard_path_probe` route by calling
[`PathFinder.find_spec()`][path-finder] with the captured search path and an
exact live reload target when one remains available.

Route comparisons preserve these symmetric mechanics:

- found, not-found, failed, or target-unavailable status;
- loader type, origin, cached path, and package/module status;
- search locations present only in either route; and
- search-location reordering.

The probe has `evidence_level=live_probe`, `state_phase=report`, and
`predicts_alternative_winner=false`. It uses report-time path-hook,
importer-cache, filesystem, and finder state. It also skips intervening custom
meta-path finders. Text therefore calls it an independent standard path probe,
never the finder that "would have won."

Structural evidence next to a comparison is identity-only: it says whether
`sys.path_hooks` or relevant importer-cache entries changed between retained
snapshots. It does not reconstruct the import-time cache or invoke historical
foreign objects.

## Findings narrative

Raw status, loader, origin, package, cached-path, and location differences are
not findings. They remain visible in the resolution-route section. A route
difference is promoted only when an observed effect corroborates a specific
mechanism.

The `-- findings --` section renders numbered problem blocks in severity
order. When a causal explanation links to a finding through its
`cause_finding_ref`, the explanation headlines the block and the finding
renders indented beneath it as evidence — one problem, one block. Each block
ends with a static `why it matters:` consequence line for its kind (when the
headline does not already state it) and a prose sentence naming the
severity, evidence level, and limitations. Informational findings compress
to one line each under an `informational:` subheading. Cross-references use
the visible block numbers (`see [1]`). A run with no findings states the
clean result in one sentence.

When the CLI recorded a target failure, imports that started but produced no
module by report time are listed in a bounded `-- imports that started but
produced no module --` section; the target's failed module is marked, and a
conservative note connects legacy-only finders that CPython 3.12+ never
calls. Failed optional imports also appear there and are normal.

These findings are leads, not verdicts:

- `[namespace-truncation]` — an exact opt-in deep import completion captured a
  failed descendant after a custom namespace claim, and the standard path
  route contains a candidate location absent from the captured route. The
  finding references both routes and their comparison.
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
- `[legacy-finder-contract]` — safe raw-dictionary inspection captured a
  callable `find_module` without a callable `find_spec`. Protocols are not
  invoked, and descriptor-backed availability can remain indeterminate.
- `[path-hook-shadow]` — distinct opt-in path-hook boundaries accepted the
  same path across recorded resolution states. This is structural evidence;
  it does not claim both hooks were reachable in one historical call.
- `[failed-after-mutation]` — an exact deep import boundary reported `failed`
  after a retained meta-path, path-hook, or importer-cache mutation. Temporal
  ordering alone does not prove that the mutation caused the failure.

`loader-reentry` is reserved for nested lifecycle and partially initialized
identity evidence. The current `unobserved_reentrant` deep marker explicitly
lacks that evidence and therefore never produces this finding.

[path-finder]: https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder
[module-spec]: https://docs.python.org/3/reference/import.html#import-related-module-attributes

Every JSON finding contains an `evidence` object. Its primary `level` records
the basis for the promoted effect; `event_refs` links retained supporting
records and `limitations` contains stable machine-readable caveats. Route and
comparison objects are document-scoped and referenced by stable IDs rather
than duplicated inside findings.

Finder-call timeline records include import-time spec summaries. Exact string
values and exact list/tuple package paths are copied before the spec is returned
to importlib. Non-string values are represented only by safe type and identity
metadata. A foreign package-path sequence is marked `deferred` rather than
iterated in the import hot path. Namespace paths returned by the standard path probe
are copied during reporting and marked `post_hoc`. Field comparisons expose
locations present only in the left or right route, plus reordering, without
presenting probe state as exact historical proof.

Finder and mutable-loader records also expose constant-size target-module
states: `missing`, explicit `none`, `object` with safe identity/type metadata,
or `unavailable`. Text timelines omit unchanged pairs but JSON retains them.
Object identities are process-local evidence and are not stable across runs.

Each route-comparison-backed finding separately includes a `structural evidence:` line.
This identity-only comparison says whether `sys.path_hooks` changed between
the install and report snapshots and whether relevant
`sys.path_importer_cache` entries changed. JSON links the comparison to those
snapshots and to passive cache-diff events. It does not call removed or
invalidated historical finder objects, reconstruct exact import-time state, or
prove that a structural change caused the route difference.

Extension modules, built-ins, synthetic origins, and modules that predate
installation are not subjected to the source-module bypass check.

## Internal errors

Instrumentation failures are recorded instead of being allowed to break the
target import. This section identifies the failing monitor code path and the
exception type. It intentionally may omit exception text because converting a
foreign exception to text during an import can execute arbitrary code.

For capture boundaries and memory behavior, see
[Limitations and resource behavior](limitations.md).
