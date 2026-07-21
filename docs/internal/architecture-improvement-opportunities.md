# Architecture improvement opportunities

This assessment records architectural follow-up identified in July 2026. The
items are ordered by correctness risk first, then by maintainability leverage.
They are proposals rather than compatibility commitments; each implementation
still needs focused tests and documentation updates where public behavior
changes.

## 1. Keep foreign code outside lifecycle locks

`Monitor.install()` currently holds `_reinstall_lock` while finder
instrumentation performs foreign `find_spec` attribute access. On reinstall,
the permanent audit hook is already active. A second thread can therefore hold
a module lock while waiting for `_reinstall_lock`, while the installer holds
`_reinstall_lock` and waits for that module lock through a foreign attribute
lookup.

Refactor installation into a generation-based, two-phase transition:

1. Claim and publish an `installing` generation under the lifecycle lock.
2. Release the lock before any foreign attribute access or delegated call.
3. Reacquire it to commit only if the generation is still current.
4. Make audit recovery recognize an in-progress generation without waiting on
   foreign work beneath the lifecycle lock.

Add a deterministic subprocess regression that coordinates two threads and a
CPython module lock. The existing lazy-attribute regression covers same-thread
re-entry but not the cross-thread lock order.

## 2. Model process-global hooks as owned leases

Every installed global resource should record both the value it replaced and
the exact value metapathology installed. Cleanup should restore the old value
only while the live resource is still owned by that installation. If another
tool replaced it later, cleanup must preserve that tool's value and record the
ownership conflict where useful.

Apply this convention to:

- `sys.setprofile()` and `threading.setprofile()` callbacks;
- instrumented import-list identities;
- `atexit` registration;
- finder, path-entry finder, and loader instance shadows.

This makes reversibility a shared lifecycle invariant instead of a collection
of mechanism-specific checks.

Status: complete for resource ownership. Profiler callbacks and import-list
installations retain explicit owned values; finder, path-entry finder, and
loader shadows retain both their prior and installed values; deep path-hook
cleanup replaces only wrappers still owned by the monitor; and the exact
registered exit callback is retained. Cleanup preserves any later third-party
replacement. Lifecycle transition serialization remains item 1.

## 3. Introduce an explicit monitor snapshot contract

Reporting currently copies the event cutoff and then reads other monitor-owned
properties through several private methods and properties. Introduce an
immutable internal `MonitorSnapshot` returned by one operation under
`_record_lock`. It should contain all monitor-owned report inputs, including
events, mechanism states, initial snapshots, finder contracts, skipped
finders, cache state, baseline modules, target outcome, and bootstrap
provenance.

Live interpreter inspection should remain a separately labelled report-time
phase. This makes the distinction between cutoff evidence and later live
evidence explicit in both code and tests.

Status: complete. `Monitor._report_state()` now returns one immutable
`MonitorSnapshot` containing the event cutoff and every monitor-owned report
input. `_report_capture.py` consumes that snapshot and performs separately
identified live interpreter reads.

## 4. Split the report pipeline by stage

The former `_report_data.py` owned report-domain records, live capture and
probing, deterministic analysis, and stable JSON serialization. Separate
those responsibilities by pipeline stage:

- `_report_model.py`: report documents, attempts, findings, routes, and
  explanations;
- `_report_capture.py`: snapshot assembly and guarded live-state inspection;
- `_report_analysis.py`: deterministic attempt, route, finding, and causal
  synthesis;
- `_report_json.py`: projection onto the stable `ReportJSON` schema.

Start with JSON projection because it is a leaf stage: it consumes an existing
`ReportDocument` and does not inspect or mutate import state. Leave
`_report_text.py` intact until its own change pressure justifies subdivision.

Status: complete. The mixed module has been removed; model, capture, analysis,
JSON projection, and text projection now have explicit one-way dependencies.

## 5. Give immutable record infrastructure one owner

Capture events and report-domain records share private immutable-record
machinery across module boundaries. Put that machinery behind one stable
internal boundary, or migrate both record families atomically when it changes.
Report-domain classes should not import private implementation names owned by
the capture-event module.

Preserve the public event-record API and its immutable, slotted, identity-based
semantics throughout any migration.

Status: complete. `_record.py` is the neutral owner of the immutable record
metaclass and base. Configuration, monitor snapshots, capture events, and
report-domain models now depend on that boundary rather than importing record
machinery from the capture-event module.

## 6. Separate configuration resolution from lifecycle mutation

CLI, API, and environment configuration currently travels through long lists
of related booleans before being resolved inside `Monitor.install()`. Introduce
an immutable internal `InstallRequest` that resolves precedence and validates
the whole request before process-global state changes.

Keep the existing public signature. Internally, distinguish requested options
from the active mechanism set so repeated enable-later installation remains
explicit and testable.

Status: the first implementation step is complete. `_config.py` now owns the
immutable resolved `InstallRequest`, environment precedence, report-option
validation, and destination normalization. `Monitor.install()` applies that
plain request after resolution; further lifecycle restructuring belongs to
items 1 and 2 rather than configuration parsing.

## Suggested order

1. Consolidate installation configuration without changing the public API.
2. Establish the immutable record boundary while completing the current record
   migration.
3. Split model, capture, analysis, and JSON projection into explicit stages.
4. Add the immutable `MonitorSnapshot` cutoff contract.
5. Give shared immutable-record machinery a neutral owner.
6. Implement owned leases for profilers and other global resources.
7. Rework installation around a two-phase lifecycle transition.
