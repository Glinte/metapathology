# Speculative replay investigation

Status: Phases 0, 1, and 2 implemented (2026-07-22). Phase 3 (synthetic
`PathFinder` hook exclusion) remains deliberately deferred: it would require a
version-sensitive partial reimplementation of `importlib`'s path-based finder,
and its Phase 3 gate below is not met by any pinned fixture. See
`_speculative_replay.py` (bounded displaced-finder replay), the
`SpeculativeReplay` record, `DeepDiagnosticCall.returned_finder` (captured
hook-to-finder provenance), and the `speculative_replay` /
`METAPATHOLOGY_SPECULATIVE_REPLAY` / `--speculative-replay` switch.

Original status: active design decision, 2026-07-17.

## Decision

Do not add a general import-resolution simulator or arbitrary finder
permutations.

The next useful work is narrower:

1. Fix three correctness problems around the standard-path probe and
   deep-mode evidence. **Implemented.**
2. Capture the identity of the finder returned by each deep path-hook call and
   correlate it with importer-cache changes.
3. If the captured provenance still leaves the beartype#599-style diagnosis
   incomplete, add an independently enabled, bounded replay of one displaced
   path-entry finder.
4. Reconsider synthetic single-hook exclusion only if pinned fixtures show
   that the displaced-finder probe cannot answer a concrete user question.

The intended new question is not "what would every possible import ordering
do?" It is:

> A captured cache change displaced finder X for path P, and the later import
> of M failed. Does the retained finder X produce a spec for M now?

That question is useful for path-hook/cache contention, has a specific
evidence chain, and needs one foreign `find_spec()` call. General hook
exclusion needs fresh calls to multiple hook factories and finders plus a
partial reimplementation of `PathFinder`.

## Existing coverage

The completed roadmap already answers most route questions without a new
speculative mechanism:

| Case | Existing strongest evidence | Remaining gap |
| --- | --- | --- |
| A custom meta-path finder claims before `PathFinder` | Captured claim route plus an independent standard-path probe and neutral route comparison | None that justifies calling arbitrary later meta-path finders |
| A redirecting finder truncates a namespace | Captured and standard namespace routes plus exact descendant-outcome correlation | None for the scikit-build-core#1482 shape |
| A standard finder makes a later custom finder unreachable | Captured or inferred standard resolution attribution and ordering | No safe basis for claiming the later finder would win |
| A path hook creates a finder that displaces an earlier cached finder | Hook/cache timeline and deep path-entry calls | The report cannot yet link the accepting hook to its returned finder or ask the displaced finder about the failed name |

The last row is the valuable target. It corresponds to the mechanics in
[beartype#599][beartype-599]: hook ordering changes, the importer cache is
cleared, and a finder suitable for ordinary source paths can displace a frozen
or archive finder for the same path entry.

The namespace problem in [scikit-build-core#1482][skbuild-1482] is a
meta-path short circuit. The standard-path probe supplies the relevant
namespace locations, but the difference remains neutral unless exact deep
evidence captures a correlated descendant failure. Path-hook exclusion would
add noise, not evidence.

## Investigation findings

### CPython behavior to reproduce

The path-based finder selects one path-entry finder per path entry. On a cache
miss it calls path hooks in order until one returns; only `ImportError` means
"try the next hook." It stores the returned finder, or `None`, in
`sys.path_importer_cache`. Later module lookups use that cached finder without
consulting the hooks. These are documented properties of the
[path-based finder][python-path-finder].

The relevant `_bootstrap_external.PathFinder` implementation is small but not
identical across the supported range:

| CPython | Path entry types | Legacy path-entry protocol | Empty-path failure |
| --- | --- | --- | --- |
| 3.10 | `str` and `bytes` | `find_loader()` / `find_module()` fallback | `FileNotFoundError` |
| 3.11 | `str` | Legacy fallback remains | `FileNotFoundError` |
| 3.12-3.13 | `str` | Removed | `FileNotFoundError` |
| 3.14 | `str` | Removed | `FileNotFoundError` or `PermissionError` |

The core search and namespace aggregation are otherwise stable in the
reviewed [3.10 implementation][cpython-310-path-finder] and
[3.14 implementation][cpython-314-path-finder]. Stability does not make a
copy free to maintain: a compatible synthetic search must still track these
branches, legacy warning behavior, namespace aggregation, current-directory
normalization, exception propagation, and future CPython changes.

### Cache semantics make global exclusion ineffective

A prototype on CPython 3.10 through 3.14 used two hooks that both accepted one
virtual path. The first produced `LoaderA` and the second produced `LoaderB`.
After the first lookup populated `sys.path_importer_cache`, excluding the
first hook without clearing the cache still produced `LoaderA`. A private
empty cache that skipped the first hook produced `LoaderB`.

The same prototype recorded the extra calls: private replay invoked the
second foreign hook and its returned finder again. A synthetic cache prevents
global cache mutation; it does not make replay free of foreign side effects.

Temporarily clearing or replacing the real cache is rejected. A daemon thread
could observe the temporary state, an exception could interrupt restoration,
and report generation would be changing the target's import outcomes.

### A displaced finder is the smaller useful boundary

A second prototype modeled the beartype#599 shape on every supported Python:

1. A frozen hook populated the cache with a finder that returned
   `FrozenLoader` for a module.
2. A source hook was moved first and the cache was cleared.
3. The new cached source finder returned no spec.
4. Calling the retained displaced finder directly still returned
   `FrozenLoader`.

This answered the useful question without invoking either hook, changing a
global cache, aggregating namespace portions, or copying `PathFinder`.
Metapathology already retains cache-finder objects while monitoring is active;
the missing pieces are provenance, candidate selection, and a deliberately
enabled replay policy.

### Phase 0 correctness prerequisites

The investigation found three issues that had to be fixed before adding
another probe producer:

1. Deep path-hook wrappers leak into report snapshots. With no foreign hook
   mutation, the install snapshot contained `zipimporter` and
   `path_hook_for_FileFinder`, while the report snapshot contained two
   metapathology functions named `wrapped` with different identities. This
   falsely reports that `sys.path_hooks` changed and prevents reliable hook
   selection. Snapshots and mutation records must normalize metapathology's
   wrappers back to the original hook identity.
2. Report analysis suppresses ordinary finder recording but not deep
   path-entry recording. In a prototype, successive reports grew the monitor
   event log from 7 to 8 to 9 events because each standard-path probe recorded another
   deep `path_entry_finder` call after the report cutoff. All monitor producers
   must be inert during report analysis.
3. `FindSpecCall` did not record whether the original call had a reload target.
   The old probe always passed `target=None`. A target-sensitive fixture loaded
   with `ReloadLoader`, while the probe selected `NormalLoader`. The call now
   retains safe target identity evidence and the probe either passes the
   still-identical target or records `target_unavailable`.

These correctness fixes are implemented and are not speculative-replay
features. Regression tests cover wrapper identity normalization, report-time
producer suppression, and exact reload-target preservation.

### Missing captured provenance is more important than more execution

A deep path-hook record currently identifies the hook, path, and whether it
returned or raised, but not the identity/type of the returned finder. The
subsequent path-entry call identifies a finder and path, but the relationship
between those records is inferred from ordering rather than captured.

Record the returned finder as safe identity/type data on successful path-hook
calls. This enables an exact chain:

```text
hook H returned finder F for path P
cache entry P contained F
mutation/cache change removed or replaced F
later attempt M traversed P and failed
```

That chain improves reports even if speculative replay is never enabled. It
also gives any later probe a defensible candidate instead of trying every hook
or finder.

## Candidate mechanisms

| Mechanism | Diagnostic value | Perturbation | Maintenance | Decision |
| --- | --- | --- | --- | --- |
| Captured hook-to-finder provenance | High for cache displacement | Adds plain identity fields to already enabled deep events | Low | Implement first |
| Direct replay of one displaced cached finder | High for beartype#599-style failures | Calls one foreign finder; no hook, loader, or global cache mutation | Moderate | Conditional implementation after fixtures |
| Synthetic `PathFinder` with one hook excluded | Can discover an alternative when no displaced finder exists | Calls multiple foreign hooks/finders against current state | High and version-sensitive | Defer behind a value gate |
| Temporarily mutate `sys.path_hooks` or the real importer cache | Similar result to synthetic search | Concurrent target imports can observe counterfactual state | Low code, unacceptable behavior | Reject |
| Replay every later meta-path finder | Potentially names another claimant | Arbitrary finders can import, mutate `sys.modules`, raise, or depend on ordering | Unbounded behavior and noisy output | Reject |
| Try hook reorderings or subsets | Combinatorial output with weak causal basis | Repeated foreign calls | Unbounded | Reject |
| Execute alternative loaders | Purports to test import success | Runs third-party code and can replace modules or perform external actions | Unacceptable | Reject |
| Replay in a subprocess | Isolates some side effects | Closures, live finder state, frozen runtime state, and object identities are not serializable | Does not reproduce the observed interpreter | Reject |

## Proposed delivery

### Phase 0: repair probe boundaries (implemented)

This phase is required regardless of later decisions.

- Normalize deep hook wrappers to original hook references in current
  snapshots, mutation `contents_after`, and reassignment evidence.
- Make the report-analysis guard cover ordinary wrappers, deep wrappers,
  audit/profiling producers, and any future replay path.
- Record whether custom and path-entry `find_spec()` calls received a target.
  Decline replay when an equivalent current target cannot be established.
- Add a report-analysis invariant: standard-path probes do not append deep
  diagnostic events after the captured cutoff.

Definition of done:

- Enabling deep path hooks without foreign mutation leaves install and report
  hook identities structurally equal.
- Two successive text/JSON renders do not grow the event log because of
  replay.
- A target-sensitive reload cannot create a comparison against a
  `target=None` replay.

### Phase 1: capture cache-finder provenance (implemented)

Successful deep `path_hook` records now carry `returned_finder` (safe identity
and type name of the finder the hook installed), rendered in text and JSON. This
supplies the `hook H returned finder F for path P` step of the provenance chain
and gives Phase 2 a defensible candidate.

- Extend successful deep path-hook records with the returned finder's safe
  identity and type/name.
- Correlate that result with the matching path and later importer-cache
  snapshots/diffs. Do not infer ownership when another thread or unobserved
  operation makes the relationship ambiguous.
- Add a captured `cache_finder_provenance` signal to existing cache
  displacement and failed-after-mutation explanations. Do not create a new
  headline merely because a hook returned a finder.
- Keep records constant-size. No map may retain one entry per
  `(module, path, hook)` beyond the existing exhaustive event log.

Definition of done:

- The synthetic beartype#599 sequence identifies the old and new hook/finder
  pairs for the affected cache path without replay.
- Concurrent hook/cache activity degrades to ambiguous provenance rather than
  joining unrelated records.
- Text remains concise; experimental JSON carries exact event references.

After Phase 1, run the pinned beartype#599 fixture and assess the report. Stop
here if the captured provenance already identifies the contention and the
remedy. Speculation is not valuable merely because it is possible.

### Phase 2: bounded displaced-finder replay (implemented)

Implemented in `_speculative_replay.py`. Candidate selection is pure: it pairs
each displaced importer-cache finder (from `ImporterCacheDiff` removals and
replacements) with a later deep `path_entry_finder` `not_found` call on the same
path. Each selected candidate triggers at most one foreign
`prior_finder.find_spec(fullname, None)` at report time under the
report-analysis guard, capped at 16 probes with an overflow count. Results are a
report-phase `SpeculativeReplay` model attached to the document (text section
and JSON `speculative_replay` block), never appended to the monitor event log,
so repeated reports recompute rather than grow. Reload-target lookups are
declined; finder `None`, namespace, malformed spec, and ordinary `Exception`
outcomes are reduced to safe results, while control-flow exceptions outside
`Exception` (`KeyboardInterrupt`, `SystemExit`) keep propagating rather than
being swallowed by the probe.

Activation must be independent and explicit, for example
`--speculative-replay` / `install(speculative_replay=True)`. `--deep` must not
silently enable it: deep capture delegates along actual import paths, while
this feature invokes a path that the target did not take.

Candidate requirements:

- An exact importer-cache removal or replacement identifies the prior finder
  object and path.
- Captured deep evidence links a later failed attempt to that path.
- The attempt was not a reload with an unavailable target.
- The retained finder exposes a callable modern `find_spec`; legacy-only and
  dynamically synthesized protocols are reported unsupported, not invoked.
- Causal synthesis has already selected the attempt as relevant. Replay never
  scans all retained cache finders or all failed imports.

Execution policy:

- Call only `prior_finder.find_spec(fullname, target)` and never a hook or
  loader.
- Run outside the state lock under a report-analysis/re-entrancy guard.
- Catch `BaseException`, reduce it to safe type metadata, and leave the target
  report usable.
- Use the existing conservative `SpecSummary`; do not retain the returned
  foreign spec in the report document.
- Deduplicate by `(attempt_id, path, finder_id)` and cap the entire report at a
  named constant, initially 16 probes. When the cap is reached, record the
  omitted count; do not queue or retry.
- Do not memoize across reports. Each report labels its own current-state
  boundary. Documentation must warn that repeated explicit reports repeat
  foreign finder calls.

Output policy:

- Attach the result to the existing cache-displacement/failed-import
  explanation; do not create standalone findings for unrelated `not_found`
  results.
- Label it `speculative_replay`, `state_phase=report`, with the displaced
  finder, cache path, attempt, and cache-diff event references.
- Say "the displaced finder currently returns ...", never "the import would
  have succeeded." A spec does not prove loader success, and current finder
  state is not historical state.

Definition of done:

- The beartype#599-style fixture reports that the displaced frozen/archive
  finder currently returns a spec for the failed module.
- Finder `None`, namespace, malformed-spec, ordinary exception,
  `KeyboardInterrupt`, and `SystemExit` outcomes cannot break reporting.
- The probe never changes `sys.path_hooks`, `sys.path_importer_cache`, or
  `sys.modules` itself. Foreign finder side effects remain possible and are
  documented.
- Default mode and ordinary deep mode perform zero speculative calls.
- The fixed capacity, overflow count, and repeated-report behavior have tests.

### Phase 3 gate: synthetic hook exclusion

Do not schedule this phase merely after Phase 2. Require a pinned real-world
fixture where all of the following are true:

1. Captured provenance identifies the accepting hook and finder.
2. No displaced retained finder can answer the failed-name question.
3. Knowing the next hook's current result changes the diagnosis or remedy.
4. The same answer cannot be stated as a structural candidate without calling
   foreign code.

If that gate is met, restrict the design to one selected hook exclusion and a
private per-report cache. The helper would need a CPython-version contract and
differential tests against `PathFinder` on 3.10 through 3.14 for ordinary
finders, negative cache entries, namespaces, duplicate paths, invalid path
entries, empty-path handling, exceptions, and legacy finders. Unsupported
protocol/version shapes must return an explicit refusal.

Do not construct a synthetic `_NamespacePath` or expose a synthetic spec as if
CPython returned it. Aggregate safe spec summaries directly for comparison.

## Noise controls

Speculative output is useful only when tied to an observed effect. Apply these
rules to every future proposal:

- No replay for successful equivalent imports merely to enumerate
  alternatives.
- No replay for cache hits without a relevant cache displacement.
- No permutations, recursive replay, loader calls, retries, or background
  workers.
- One primary explanation owns a replay result; other findings reference it
  rather than duplicating text.
- `not_found` without a captured contention chain is not a finding.
- Ambiguous path/finder provenance prevents replay instead of broadening the
  search.
- JSON may retain exhaustive probe metadata within the fixed probe cap; text
  prints only probes that change or materially strengthen an explanation.

## Test plan

All import-state tests run in subprocesses. The minimum matrix is CPython
3.10 through 3.14.

- Regression tests for the three Phase 0 issues found by this investigation.
- Hook-to-finder-to-cache provenance with cache add, remove, replace, negative
  entry, reorder, direct hook-list replacement, and concurrent activity.
- Historical frozen-finder displacement followed by a failed import.
- A finder whose result depends on `target`.
- A finder that mutates its own cache, imports another module, mutates
  `sys.modules`, returns a malformed spec, or raises each `BaseException`
  category.
- Repeated report generation, probe-cap overflow, interrupted uninstall, and
  a daemon thread importing while a report is generated.
- Semantic assertions on evidence level, state phase, event references, and
  limitations; no golden report unless the complete rendering becomes the
  reviewed contract.

If Phase 3 is ever approved, use small fake path-entry finders and Hypothesis
to compare the private search helper with the running CPython `PathFinder`.
Assert after every generated path/hook/cache operation, and report the seed or
minimal example for nondeterministic failures.

## Reassessment rule

Phases 1 and 2 are implemented. Phase 3 has no current justification and
remains deferred: it requires a versioned partial reproduction of `PathFinder`,
and no pinned fixture meets its four-part gate above.

This keeps replay proportional to demonstrated value: prefer exact captured
provenance, then one evidence-selected foreign call, and only then consider a
versioned partial reproduction of importlib.

[beartype-599]: https://github.com/beartype/beartype/issues/599
[cpython-310-path-finder]: https://github.com/python/cpython/blob/v3.10.20/Lib/importlib/_bootstrap_external.py
[cpython-314-path-finder]: https://github.com/python/cpython/blob/v3.14.6/Lib/importlib/_bootstrap_external.py
[python-path-finder]: https://docs.python.org/3/reference/import.html#the-path-based-finder
[skbuild-1482]: https://github.com/scikit-build/scikit-build-core/issues/1482
