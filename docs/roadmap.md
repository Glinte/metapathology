# Import-observation gaps and recommended work

This document records known weaknesses exposed by unusual import environments
such as beartype#599, beartype#638, PyInstaller, coverage, pytest assertion
rewriting, namespace packages, editable installs, re-entrant finders, and
manual loader execution. It is an implementation backlog, not a promise that
every proposed mechanism will be added.

The default mode must continue to prioritize observation with minimal
perturbation. Any mechanism that replaces foreign path hooks, path-entry
finders, or loaders belongs in an explicit deep-diagnostics mode.

Completing this roadmap should make metapathology a helpful general diagnostic
tool for Python *import resolution and module-loading lifecycle*. It will not
explain arbitrary exceptions raised by module code, native-library failures,
incompatible wheels, or missing files beyond showing where resolution or
loading stopped. Findings must say whether they are based on exact captured
events, post-hoc state, current replay, or speculation.

Three evidence rules apply throughout the roadmap:

1. An import audit event proves only that resolution started. It does not prove
   success, failure, or which finder won.
2. Presence in `sys.modules` at report time is post-hoc state. It does not prove
   which import created the module or that the same object survived throughout
   the process.
3. The mandatory re-entrancy guard remains in force. Default instrumentation
   may record safe before/after side effects around a delegated foreign call,
   but it must not recursively instrument nested imports while already active.

## Dependency overview

```text
T1 path-hook snapshots ──┬──> T3 evidence timeline ──> T6 counterfactual replay ──> T11 spec comparison
T2 importer-cache diffs ─┘            │
                                      └──────────────────────────────────────────────┐
T4 loader inventory ────────────────────────────────────────────────────────────────┤
T12 finder contract audit ──────────────────────────────────────────────────────────┤
T3 + T4 + T5 ──> T10 opt-in deep diagnostics ──┬──> T13 import outcome correlation ┤
                                               ├──> T14 module identity transitions ┤
                                               └──> T16 standard resolution evidence ┤
T6 + T11 + T12 + T13 + T14 + T16 ────────────────────────> T7 contention findings ─┤
T3 + T7 + T11 + T13 + T14 + T16 ─────────────────────────> T15 causal synthesis <──┘
T5 ──> T8 frozen bootstrap
T1 + T2 + T5 + T7 + T8 + T15 ──> T9 beartype#599 fixture
T1 + T2 + T5 ──> T17 opt-in early site bootstrap
```

## T1: Observe `sys.path_hooks` mutations (implemented)

**Weakness:** Metapathology can show that `PathFinder` would have selected a
different loader, but it cannot show when or why the path-hook ordering changed.
This omits the central operation in beartype#599: beartype prepends a source
loader factory ahead of a frozen importer.

**Implementation:** The default-on, independently toggleable path-hook monitor
captures immediate snapshots and mutation stack traces for all list operations,
using the same reversibility, re-entrancy, and lock rules as the meta-path
list. Record only hook identity and type/name at mutation time; never call
foreign `repr()` or `str()`.

It does not wrap hook factories. Function and closure hooks
generally cannot be instrumented by safe instance-dict shadowing (most real
hooks are `FileFinder.path_hook(...)` closures with no instance dict), and
replacing them with delegates can break identity-sensitive code.

Unlike `sys.meta_path`, there is no dedicated recovery signal for direct
reassignment of `sys.path_hooks`: "the next safe observation point" means the
existing `import` audit hook additionally snapshots `id(sys.path_hooks)`.
That widens the audit hook's scope; it is a deliberate decision, not a side
effect.

**Dependencies:** None.

**Definition of done:**

- Every supported `sys.path_hooks` list mutation is recorded with a stack.
- Direct list replacement is detected at the next safe observation point.
- Hooks added after installation appear in later snapshots.
- `uninstall()` restores a plain list containing the original hook objects in
  the correct order.
- Subprocess tests cover mutation, reassignment, re-entrancy, concurrent
  mutation, and interrupted cleanup.

## T2: Diff `sys.path_importer_cache` (implemented)

**Weakness:** Clearing or repopulating `sys.path_importer_cache` can change the
finder serving a path without changing `sys.meta_path`. Beartype#599 depends on
this: beartype clears cached PyInstaller finders after changing path hooks.

**Implementation:** The default-on, independently toggleable mechanism
passively snapshots cache entries at install time, before
and after observed path-hook mutations, and at report time. Store path strings
plus finder identity and type/name. Report additions, removals, replacements,
and negative (`None`) entries.

It does not take a full snapshot at every import audit event. The cache grows with
one entry per path entry per package `__path__` and can reach thousands of
entries; O(cache size) work inside every import contradicts the minimal-
perturbation goal. Inside the audit hook, use a cheap fingerprint (e.g.,
`len(sys.path_importer_cache)` plus dictionary identity) to decide whether a
later full observation is warranted.

The cache is never replaced with a dictionary subclass. Importlib may
hold references to the exact dictionary, and instrumentation would add work to
every path lookup. Passive diffs are less complete but safer.

Full snapshot storage is bounded to the install snapshot and a rolling latest
snapshot; the latter is replaced after each successful observation. Diff
events retain all captured changes. Concurrent full observation requests are
coalesced rather than queued, and reports expose the coalesced count.

**Dependencies:** None. T1 provides better attribution for cache changes.

**Definition of done:**

- Cache clears and finder replacements are visible without stringifying
  foreign finders.
- Snapshot storage has a documented capacity and overflow policy, and the
  per-import cost is bounded and independent of cache size.
- Reports tolerate concurrent cache changes and non-string keys.
- Tests reproduce clear, negative-cache, and finder-replacement sequences.

## T3: Build a unified evidence timeline

**Weakness:** Current events are grouped by mechanism. Users must manually
correlate a path-hook mutation, cache clear, later import, and changed loader.

**Recommendation:** Use the monotonic sequence numbers from the existing shared
recording state and render a combined timeline containing meta-path mutations,
path-hook mutations, importer-cache diffs, finder calls, and relevant import
audit snapshots. Retain the mechanism-specific report sections for focused
inspection. Structured reports already contain a cross-mechanism event list;
this task adds the missing audit-start evidence and a useful chronological text
projection rather than inventing a second timeline model.

The timeline must be based on recorded plain data. It must not perform foreign
object inspection while an import is active or while the state lock is held.

**Dependencies:** T1 and T2.

**Definition of done:**

- A report can show “hook inserted, cache cleared, module claimed” in capture
  order across mechanisms.
- Audit-start records are not rendered as successful or failed imports unless
  T13 supplies separate outcome evidence.
- Concurrent events have a deterministic capture order without claiming that
  it is a global wall-clock order.
- Existing structured event consumers retain backward-compatible access to
  mechanism-specific records.

## T4: Add a loader inventory

**Weakness:** Loader types appear only inside individual findings. There is no
process-wide view of which loaders actually produced loaded modules, and
successful imports through `PathFinder` are not attributed to their path-entry
finders.

**Recommendation:** At report time, inventory copied `sys.modules` entries by
`module.__spec__.loader`, `module.__loader__`, origin, and cached path. Group
modules by loader type and identity where safe. Flag disagreement between
`__spec__.loader` and `__loader__` without assuming it is a defect.

Never read these via ordinary dynamic attribute access: it executes code on
foreign modules. `importlib.util.LazyLoader` modules fully materialize on
attribute access, and module-level `__getattr__` can import or raise. For exact
module objects, obtain the namespace through a validated base
`types.ModuleType` access that bypasses an overriding module subclass, then
read the plain dictionary. Treat arbitrary module-like objects and module
subclasses that cannot be inspected without foreign dispatch as unavailable.

This is post-hoc evidence only. A module may replace its metadata, and failed
imports leave no stable module to inventory.

**Dependencies:** None.

**Definition of done:**

- Reports group successfully loaded modules under source, frozen, archive,
  assertion-rewriting, and other observed loader types.
- Malformed, lazy, and partially initialized modules cannot break reporting or
  trigger loads.
- The report labels the inventory as post-hoc rather than exact attribution.

## T5: Add structured, file-based reports (implemented)

**Weakness:** Frozen GUI applications and embedded interpreters may have no
usable stderr. Multiple worker processes also make human-readable stderr
reports difficult to collect reliably.

**Implementation:** Text and experimental schema-versioned JSON are projections
of one cutoff-based report document containing process metadata, snapshots, a
chronological timeline, structured findings, and diagnostics. Every automatic
report filename includes the process ID: `{pid}` is replaced when present;
otherwise it is inserted before the file extension (`report.json` becomes
`report.1234.json` for process 1234). Direct `write_report()` paths remain
unchanged. The CLI, public API, and
`METAPATHOLOGY_REPORT` configuration all reach the same atomic file writer.

The initial schema was 0.1 and T1 extended it to 0.2 rather than prematurely
stabilizing 1.0. Later evidence tasks may extend or reshape schema 0.x as their
actual models are implemented; perform a schema 1.0 review after T15 and T16
and before T9 pins semantic assertions.
Human and JSON renderers must continue to consume the same report document.

There is no cross-process aggregation. Each process performs one synchronous
write bounded by existing retained events and copied report-time state, with no
queue, collector, retry loop, or silent dropping policy.

**Dependencies:** None.

**Definition of done:**

- A process without stderr can persist both human and JSON reports.
- Concurrent processes do not overwrite each other's files.
- Write failures become isolated internal errors and never change the target
  exit status.
- Schema 0.x is explicitly experimental and covered by round-trip and semantic
  tests; schema 1.0 is the future compatibility boundary.

## T6: Generalize counterfactual replay

**Weakness:** Current replay asks only what the current `PathFinder` would do.
It cannot answer what would have happened before path-hook or importer-cache
mutations.

**Recommendation:** Compare an observed claim against recorded initial and
current path-hook/cache structure. Distinguish three evidence levels:

- structural comparison using recorded identities and type names;
- live replay using current import objects;
- speculative replay with a selected hook excluded.

Historical foreign finder objects should not be called after their owning
framework has removed or invalidated them unless an explicit deep mode permits
it. Reports must label speculative results and must not present them as proof.

Note that even the existing replay perturbs state: `PathFinder.find_spec`
populates `sys.path_importer_cache` as a side effect. Speculative replay with
a hook excluded cannot go through `PathFinder` at all without either mutating
real state or reimplementing the path-entry search against a synthetic cache.
That reimplementation is the expensive part; ship structural comparison first,
live replay second, and let speculative replay slip if it demands too much
importlib duplication.

**Dependencies:** T1, T2, and T3.

**Definition of done:**

- The report distinguishes current replay from historical structural evidence.
- Tests demonstrate a loader choice changing after hook reorder plus cache
  clear.
- Replay failures are isolated and reported without affecting cleanup.

## T7: Expand contention findings

**Weakness:** `[bypass]`, `[unfindable]`, and `[no-spec]` compress distinct
failure mechanisms into a small vocabulary. A meta-path short circuit, a
path-hook shadow, and importer-cache displacement need different remedies.

**Recommendation:** Introduce precise findings while retaining existing labels
for compatibility where appropriate:

- `[meta-bypass]`: a meta-path finder prevented `PathFinder` from running;
- `[path-hook-shadow]`: an earlier path hook accepted a path another hook could
  serve;
- `[path-cache-displacement]`: a cached finder was removed or replaced after a
  relevant mutation;
- `[loader-displacement]`: loader choice changed across recorded states;
- `[frozen-source-conflict]`: a source loader displaced a frozen/archive
  loader;
- `[finder-side-effect]`: a finder changed the target's `sys.modules` entry
  before returning `None` or raising;
- `[module-replacement]`: the module object for one name changed identity
  across a captured loading boundary;
- `[loader-reentry]`: deep-mode lifecycle evidence shows loader recursion
  through partially initialized state;
- `[failed-after-mutation]`: a failed import followed a relevant recorded
  mutation.

Findings should describe mechanics, not declare a third-party package broken.

These labels are enabled only when their required evidence exists. In
particular, T13 owns failed-import evidence, T14 owns finder side effects and
module replacement, and T16 owns standard-finder attribution. An audit start
followed by absence from `sys.modules` is not enough to emit
`[failed-after-mutation]`.

**Dependencies:** T3 and T4 provide the common report model. Strong path-level
findings depend on T6; namespace and spec-level findings depend on T11;
finder-contract findings depend on T12; outcome findings depend on T13;
identity findings depend on T14; re-entry findings require T13 exact lifecycle
evidence plus T14 identity evidence; exact standard-winner findings depend on
T16. T7 must degrade to narrower existing labels when optional deep evidence
is unavailable.

**Definition of done:**

- Every finding documents its evidence and known false positives.
- Every finding declares an evidence level: captured, post-hoc, live replay,
  structural inference, or speculative replay.
- No finding claims success, failure, re-entry, or object replacement from an
  audit-start event alone.
- Existing beartype#556 and #638 fixtures have stable semantic assertions.
- A future beartype#599 fixture produces a frozen/source conflict rather than a
  generic bypass alone.

## T8: Support bootstrap inside frozen applications

**Weakness:** `python -m metapathology frozen-app.exe` observes the outer Python
process, not the interpreter and import machinery inside the executable. The
observer must be bundled and installed inside the frozen process.

**Recommendation:** Provide a generated, stdlib-only runtime bootstrap that
calls the public `install()` API and configures a file report. Start with a
PyInstaller runtime-hook template, but keep freezer-specific imports out of
metapathology runtime code. The same generation interface can later support
Nuitka, cx_Freeze, embedded CPython, or application-owned bootstraps.

The bootstrap runs after the freezer establishes its machinery but before
application imports. Those pre-existing finders belong in the initial snapshot;
their earlier installation cannot be observed.

**Dependencies:** T5. T1 and T2 are required for useful beartype#599 evidence.

**Definition of done:**

- Documentation shows how to bundle the current checkout and runtime hook.
- A frozen application writes a report from inside its own interpreter.
- The bootstrap does not require PyInstaller at metapathology runtime.
- Missing or unwritable report destinations do not break application imports.

## T9: Add a pinned beartype#599 integration fixture

**Weakness:** The repository documents why beartype#599 cannot be reproduced by
the ordinary wrapper, but it lacks an executable regression environment for
frozen finder/path-hook contention.

**Recommendation:** Build the historical PyInstaller example with pinned Python,
PyInstaller, and pre-fix beartype versions. Bundle the T8 runtime bootstrap,
run the frozen executable, and assert semantically on its JSON report. Keep the
build optional or separately marked because freezer tests are slow and
platform-specific.

The fixture must contain a passing control and a failing historical case. It
must not claim cross-platform coverage from a single operating system.

This is the most maintenance-heavy item in the backlog: pinned toolchains rot
as Python versions age out. Pin the Python version alongside PyInstaller and
beartype, and make the fixture *skip* (with a reason) when the pinned
toolchain cannot be installed, so it degrades to "skipped" rather than
"broken".

**Dependencies:** T1, T2, T5, T7, T8, and T15.

**Definition of done:**

- The control imports a bundled standard-library module after application
  startup.
- The historical case reproduces the post-claw `ModuleNotFoundError`.
- The report shows path-hook ordering and importer-cache displacement inside
  the frozen process.
- The fixture is reproducible from pinned inputs and leaves generated binaries
  outside version control.

## T10: Add opt-in deep diagnostics

**Weakness:** Passive snapshots cannot attribute every path-hook factory call,
path-entry finder decision, failed import, or loader invocation. Short-lived
cache changes may occur entirely between snapshots.

**Recommendation:** Only after passive mechanisms prove insufficient, add an
explicit mode that may delegate through replacement callables for path hooks,
path-entry finders, or loaders. Each wrapper must preserve delegation exactly,
use a re-entrancy guard, isolate observation failures, and restore the original
object on uninstall.

Deep mode does not disable or weaken the re-entrancy guard. If a wrapped loader
or finder triggers another import while instrumentation is active, the nested
call delegates silently. T13 and T14 may still compare safe target-name state
before and after the outer call, but an exact nested trace requires a separately
validated mechanism; it must not be inferred from those deltas.

Deep mode brushes against the "never handle an import" hard constraint:
wrapping a loader puts our code inline in `exec_module`. Exact delegation
keeps the letter of "never change import outcomes", but a bug in a wrapper can
now *break* an import rather than merely miss an observation — a categorically
different failure mode from the passive mechanisms, and the real reason deep
mode must never enable itself.

The CLI and report must warn that deep mode can perturb identity inspection and
third-party behavior: replacing a hook breaks not only `isinstance` scans but
also `hook in sys.path_hooks` membership checks. Never enable it automatically
after detecting a weird environment.

**Dependencies:** T1 through T5. File-based structured output is required so a
deep-mode crash can still leave machine-readable evidence.

**Definition of done:**

- Each deep mechanism is independently toggleable.
- Tests include third-party-style identity and `isinstance` scans.
- A target produces the same import result with monitoring disabled, default
  monitoring, and deep monitoring for the supported test corpus.
- Cleanup remains reversible after normal completion, exceptions, recursive
  imports, and partial installation.
- Re-entrant imports delegate normally and are labeled unobserved rather than
  reconstructed as exact nested events.

## T11: Compare spec semantics and namespace search locations

**Weakness:** Current replay compares whether a custom meta-path claim and
`PathFinder` select different loader types. That detects many path-hook
bypasses, but it does not explain differences inside the returned specs. In
scikit-build-core#1482, the decisive defect is not merely that
`ScikitBuildRedirectingFinder` claims the `mqt` namespace first: its
`submodule_search_locations` omit the separately installed `site-packages/mqt`
contribution, making `mqt.core` invisible. The current report identifies the
claiming finder but stops short of showing the omitted namespace path.

**Recommendation:** Extend claim records and counterfactual replay with safe,
plain summaries of the observed and replayed specs:

- origin and cached path when they are strings;
- loader identity and type/name;
- package-versus-module status;
- a copied tuple of string `submodule_search_locations`;
- the copied string parent path supplied to `find_spec`.

Render a field-level comparison for suspicious custom claims. Introduce
specific evidence such as `[namespace-truncation]` when a custom namespace spec
omits locations found by standard path resolution, `[package-displacement]`
when package status differs, and `[origin-displacement]` when both resolutions
find a module at different origins. State what differs rather than declaring
which package is defective.

Recording must remain conservative. Do not call `repr()` or `str()` on spec,
loader, path, or location objects. Only copy values already known to be strings
and identify all other values by type/name and identity. Treat malformed specs,
foreign sequences, attribute access failures, and concurrent mutation as
isolated diagnostics rather than import failures. If obtaining a safe semantic
summary inside the finder wrapper would require iterating foreign code, defer
that portion to report time and label it post-hoc.

Namespace replay also has a timing limitation: `PathFinder.find_spec` against
the current parent path may already reflect a namespace truncated by the
custom parent claim. Where possible, replay a top-level namespace against the
recorded import-time parent path and distinguish this from reconstruction based
on current state. Never present a reconstructed namespace path as exact
historical proof.

**Dependencies:** T6. T3 is recommended so the spec difference appears beside
the claim that caused it. T7 should consume the resulting evidence rather than
infer namespace truncation from loader differences.

**Definition of done:**

- The scikit-build-core#1482 fixture reports the omitted `site-packages/mqt`
  namespace contribution, not only the redirecting finder's claims.
- Namespace, regular-package, and module specs have stable structured
  summaries shared by the text and JSON reports.
- Tests cover extended, truncated, reordered, malformed, and concurrently
  changing search-location sequences.
- Reports distinguish import-time observations, current live replay, and
  reconstructed or speculative comparisons.
- Spec comparison never changes the target's import result and cannot make a
  malformed third-party spec fail earlier than it otherwise would.

## T12: Audit finder API contracts

**Weakness:** A finder can be present on `sys.meta_path` without implementing
the modern `find_spec` protocol. CPython may still support a legacy
`find_module` fallback, while third-party code that directly iterates
`sys.meta_path` may call `find_spec` unconditionally. In pytest#12179, boto's
vendored six appends `_SixMetaPathImporter`, pytest calls its nonexistent
`find_spec`, and collection fails. Mutation attribution identifies who added
the object, but the report does not currently state the compatibility hazard
before or alongside the traceback.

**Recommendation:** Inventory every observed meta-path entry as implementing a
callable `find_spec`, a callable legacy `find_module`, both, or neither. Record
only protocol availability, finder identity and type/name, insertion sequence,
and the existing mutation stack. Report legacy-only and protocol-less custom
entries as compatibility risks, not necessarily defects: CPython's own import
machinery and direct third-party consumers do not have identical fallback
behavior.

Do not probe protocols by calling them. Avoid `hasattr()` on foreign objects
because dynamic attribute access can execute code; inspect instance and class
dictionaries conservatively, tolerate descriptors and unusual metaclasses,
and label an indeterminate result rather than forcing attribute resolution.
The audit must retain the existing rule that metapathology never adds a
`find_spec` method to make an incompatible finder appear compatible.

**Dependencies:** None. T3 improves mutation correlation, and T7 should use
this inventory for a precise `[legacy-finder-contract]` finding.

**Definition of done:**

- The pytest#12179 fixture reports `_SixMetaPathImporter` as legacy-only and
  attributes its insertion to boto's vendored `six.py`.
- Standard class entries and supported legacy behavior are explained without
  being reported as third-party defects.
- Dynamic attributes, descriptors, class entries, `__slots__`, and unusual
  metaclasses cannot trigger imports or break reporting.
- The JSON report exposes protocol availability and its evidence source.
- Installing, reporting, and uninstalling leave every finder object unchanged.

## T13: Correlate import attempts with outcomes

**Weakness:** The audit hook records that an uncached import started, while
finder wrappers record only instrumentable custom-finder calls. Neither is an
import completion callback. Failed resolution, loader exceptions, cache hits,
and modules removed after a successful import are therefore easy to conflate.

**Recommendation:** Introduce an import-attempt record keyed by a generated
attempt id and thread identity. Default mode records only the plain fields
supplied safely by the `import` audit event and correlates later instrumented
finder calls by name, thread, and capture order. That correlation is not a
nested-import stack because a start-only signal provides no event with which to
pop such a stack. Its outcome vocabulary is deliberately partial:

- `started`: the audit event fired, with no later captured outcome;
- `finder_claimed`: an instrumented finder returned a spec;
- `finder_raised`: an instrumented finder raised;
- `present_at_report` / `absent_at_report`: post-hoc state, never aliases for
  success or failure;
- `unknown`: evidence cannot distinguish the alternatives.

Exact `loaded` and `failed` outcomes require opt-in deep evidence. Prototype a
scoped observer around importlib's load boundary or T10 loader delegates. That
observer may maintain a thread-local attempt stack only because it observes
both entry and exit. It must chain and restore an existing tracing/profiling
callback, remain thread-aware, and prove that it sees resolution failures as
well as loader exceptions on every supported Python version. If no sufficiently
safe and stable CPython mechanism exists, keep exact failure diagnosis
unsupported and remove findings that require it rather than approximating from
module absence.

Capacity follows the existing exhaustive event policy: one retained record per
observed audit start plus linked outcome records, with documented lifetime
growth and no silent dropping. Import cache hits remain invisible unless deep
mode establishes a safe boundary that sees them.

**Dependencies:** T3 supplies ordering and T5 supplies the structured identity
model. Exact outcomes additionally depend on T10.

**Definition of done:**

- Default reports distinguish starts, claims, finder exceptions, post-hoc
  presence, and unknown outcomes without calling any of them successful or
  failed imports.
- Deep mode, if implemented, has subprocess tests for successful resolution,
  missing modules, loader exceptions, cache hits, recursive imports, deletion
  after load, and concurrent imports.
- Existing tracing or profiling callbacks are either chained and restored
  exactly or cause that deep mechanism to decline activation with a diagnostic.
- Unsupported exact outcomes remain visibly unknown; absence from
  `sys.modules` never becomes proof of failure.

## T14: Track target-module identity transitions

**Weakness:** Report-time module metadata cannot reveal that a finder populated
`sys.modules` and then passed, that a loader replaced an existing module, or
that low-level extension loading executed a second object with the same valid
spec. Setuptools#3073 and discord.py#10017 are representative failures.

**Recommendation:** Never replace or proxy `sys.modules`. Add two tiers of
identity-only observation:

1. In default finder wrappers, capture the target name's state immediately
   before and after delegating: missing, `None`, or object identity plus safe
   type name. Store the delta on the existing finder-call record. This can show
   that a finder changed its own target entry before returning `None` without
   recursively observing the nested imports that caused it.
2. In deep mode, capture the same target-name state at T10 loader entry, return,
   and exception boundaries. A loader wrapper attached to a retained module
   spec can then expose manual later `exec_module()` calls and object
   replacement, including valid-spec replacements.

First validate safe lookup against ordinary dictionaries and known
`sys.modules` subclasses such as ddtrace's `ModuleWatchdog`. Do not call
overridden mapping methods on a foreign container merely to improve evidence.
If identity cannot be read without foreign dispatch, record `unavailable`.
Do not take a full module-cache copy around every finder or loader call; that
would make hot-path work scale with the environment and retain unrelated
modules.

A before/after delta proves an identity transition across that boundary. It
does not prove the internal sequence, which nested import caused it, or that a
temporary intermediate object did not exist.

**Dependencies:** T4 provides safe module metadata conventions, T13 provides
attempt and boundary identities, and exact loader transitions depend on T10.

**Definition of done:**

- The setuptools#3073 fixture reports that `DistutilsMetaFinder` changed the
  `distutils` cache entry despite returning `None`.
- In deep mode, the discord.py#10017 fixture reports two different `ext` module
  identities crossing loader boundaries even though both objects have valid
  specs for the same origin.
- Missing entries, explicit `None`, replacements, removals, loader exceptions,
  recursive imports, and hostile module-cache containers are covered.
- Records contain only target-name state and have constant work and storage per
  observed boundary.
- The report never expands an identity delta into an invented nested-import
  trace.

## T15: Synthesize evidence into causal explanations

**Weakness:** Even a chronological report can force users to reconstruct the
cause from finder calls, mutations, cache state, module metadata, and replay.
A thorough log is not yet a diagnosis.

**Recommendation:** Build a deterministic, rule-based synthesis layer over the
structured evidence. It should emit a short primary explanation, contributing
events, alternative explanations, and the next useful observation when the
evidence is incomplete. Do not use arbitrary scoring or opaque confidence
percentages. Use categorical confidence tied to provenance:

- `captured`: directly observed at the relevant boundary;
- `correlated`: multiple captured events joined by attempt/thread identity;
- `inferred`: consistent ordering and post-hoc state, but the decisive call was
  not captured;
- `counterfactual`: based on current or reconstructed replay;
- `unknown`: evidence does not select one explanation.

Examples of acceptable conclusions include:

- “`_EditableFinder` claimed `my_backend` from X before `PathFinder`; replay
  against the recorded search path selects Y.”
- “`PathFinder` produced a namespace package before the later editable finder;
  the editable finder was not called.” This wording requires captured T16
  evidence. With inferred T16 evidence, the conclusion must say “likely” and
  cite the ordering and loader-inventory evidence.
- “`DistutilsMetaFinder` returned `None` after adding or replacing
  `sys.modules['distutils']`; nested activity was not observed.”

Keep atomic T7 findings as the compatibility and machine-consumption layer.
T15 groups them into explanations; it does not introduce facts absent from the
underlying records.

**Dependencies:** T3, T7, T11, T13, T14, and T16. It may synthesize a narrower
explanation when optional deep evidence is unavailable.

**Definition of done:**

- Text reports lead with concise likely-cause explanations before exhaustive
  mechanism sections; JSON links every explanation to event and finding ids.
- The pip#11812 fixture names the claiming finder and both backend origins.
- The distributed#7782 fixture either names the captured standard winner or
  labels the finder-order explanation as inference.
- The setuptools#3073 fixture describes the target-module side effect without
  claiming an exact nested trace.
- The discord.py#10017 fixture is diagnosed in deep mode and explicitly
  reported as unsupported or unknown in default mode.
- Ambiguous and contradictory evidence produces alternatives or `unknown`, not
  a confident single cause.

## T16: Attribute standard path-resolution outcomes

**Weakness:** `BuiltinImporter`, `FrozenImporter`, and `PathFinder` are shared
class entries and deliberately remain unwrapped. Consequently, default reports
can show that a later editable finder was never called but cannot directly say
that `PathFinder` first returned a namespace-package spec. Loader inventory is
useful post-hoc evidence, not exact winner attribution.

**Recommendation:** Provide progressively stronger evidence without mutating
shared stdlib classes:

1. Default mode combines the T4 loader inventory, recorded meta-path order,
   import-time search path, and absence of an earlier custom claim. Label the
   result `inferred standard resolution`, never a captured finder call.
2. Current replay uses T11 semantic spec summaries and remains explicitly
   counterfactual.
3. Deep mode uses T10 path-entry finder and loader delegates to capture the
   path-based search components. Add a CPython-version-gated feasibility spike
   for observing `PathFinder`'s aggregate result without replacing the shared
   class entry. If exact aggregate attribution cannot preserve `isinstance`,
   identity, and import outcomes, retain component evidence and keep the final
   winner labeled inferred.

Do not proxy or replace the standard class entries merely to turn inference
into capture. The tool's compatibility invariant is more important than an
exact label.

**Dependencies:** T4 and T11. Deep component evidence depends on T10, and
attempt correlation depends on T13.

**Definition of done:**

- The distributed#7782 fixture explains the namespace-package result and why
  the appended editable finder was unreachable, with its evidence level shown.
- Built-in, frozen, source, bytecode, extension, zip, and namespace loaders are
  distinguished without instrumenting shared class entries.
- Deep path-entry evidence identifies the path and path-entry finder involved
  when available.
- Current replay and post-hoc inventory are never rendered as exact historical
  standard-finder calls.
- Failure to capture the aggregate `PathFinder` result degrades to a labeled
  inference rather than blocking T15 or weakening compatibility.

## T17: Add an opt-in early site bootstrap

**Weakness:** The normal CLI and library API begin after CPython finishes site
initialization. Finders and path hooks installed by executable `.pth` lines
therefore appear only in the initial snapshots; their mutations and cache
effects cannot be attributed. This is the exact blind spot involved when an
editable or freezer integration installs import machinery from a `.pth` file.

**Recommendation:** Prototype a generated, environment-gated startup file for
diagnostic environments. On CPython versions that execute `import` lines in
`.pth` files, place a uniquely owned file early in one explicitly selected
site-packages directory. Its one-line bootstrap should import metapathology
and call `install()` only when a dedicated environment variable is enabled.
Ordinary package installation must never create or activate this file.

The generator must make the observation boundary honest:

- `.pth` names are ordered only within one site-packages directory. A bootstrap
  cannot observe files processed earlier in that directory or files in a site
  directory CPython processed first.
- `-S`, disabled user-site processing, isolated embedded configurations, and
  some `._pth` configurations can prevent the bootstrap from running.
- The activation variable and report configuration are inherited by child
  processes using the same environment. This is useful subprocess coverage,
  but must be explicit and produce PID-safe files through T5.
- Executable `.pth` lines are deprecated in Python 3.15. The replacement
  `.start` mechanism runs after `.pth` processing, so it can bootstrap ordinary
  application monitoring but cannot recover `.pth` mutation attribution. Treat
  this feature as version-gated and experimental rather than a permanent
  architecture.

Installation and removal should be symmetric commands. Record a generated
ownership token and refuse to overwrite or remove a file whose contents do not
match it. Reports should include the bootstrap path, selected site directory,
activation source, and whether earlier `.pth` files remained outside the
observable window.

**Dependencies:** T1, T2, and T5. T3 improves correlation with the later import
timeline but is not required for the bootstrap experiment.

**Definition of done:**

- A fresh-venv subprocess fixture proves that a later `.pth` mutation produces
  path-hook and importer-cache evidence rather than appearing only in the
  initial snapshots.
- A deliberately earlier `.pth` file remains unattributed and is described as
  pre-bootstrap state rather than silently claimed as observed.
- With the activation variable absent, startup does not import metapathology,
  install an audit hook, or write a report.
- An activated child process writes a distinct PID-safe report without any
  additional child-specific injection step.
- Generation and removal are idempotent, never delete foreign files, and leave
  the environment pristine after interrupted setup or cleanup.
- Tests and documentation cover supported CPython versions, `-S`, directory
  ordering limits, the Python 3.15 deprecation, and the absence of a future
  `.start` equivalent for observing `.pth` execution.

## Explicit non-goals

- Install an ungated or persistent `.pth` bootstrap as part of ordinary package
  installation.
- Claim that an early bootstrap observes every `.pth` file or every site
  directory involved in startup.
- Claim exact historical state when only report-time replay is available.
- Import or depend on freezer frameworks at metapathology runtime.
- Replace foreign finders or loaders in default mode merely to improve
  attribution.
- Claim an exact nested-import trace from before/after module-cache deltas.
- Treat audit-start events or report-time module absence as proof that an
  import failed.
- Explain arbitrary exceptions raised by imported module code or by the
  operating system's native-library loader.
- Diagnose arbitrary executable files from an unrelated outer Python process.
- Silently suppress or reorder third-party import hooks to make a target work.
