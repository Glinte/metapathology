# Import-observation gaps and recommended work

This document records known weaknesses exposed by unusual import environments
such as beartype#599, beartype#638, PyInstaller, coverage, pytest assertion
rewriting, namespace packages, and manual loader execution. It is an
implementation backlog, not a promise that every proposed mechanism will be
added.

The default mode must continue to prioritize observation with minimal
perturbation. Any mechanism that replaces foreign path hooks, path-entry
finders, or loaders belongs in an explicit deep-diagnostics mode.

## Dependency overview

```text
T1 path-hook snapshots ──┬──> T3 path-resolution timeline ──> T6 counterfactual replay
T2 importer-cache diffs ─┘                 │
                                           └──> T7 contention findings
T4 loader inventory ──────────────────────────> T7 contention findings
T5 structured reports ──> T8 frozen bootstrap
T1 + T2 + T5 + T7 + T8 ──────────────────────> T9 beartype#599 fixture
T1 + T2 + T3 + T4 ───────────────────────────> T10 deep diagnostics
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

## T2: Diff `sys.path_importer_cache`

**Weakness:** Clearing or repopulating `sys.path_importer_cache` can change the
finder serving a path without changing `sys.meta_path`. Beartype#599 depends on
this: beartype clears cached PyInstaller finders after changing path hooks.

**Recommendation:** Passively snapshot cache entries at install time, before
and after observed path-hook mutations, and at report time. Store path strings
plus finder identity and type/name. Report additions, removals, replacements,
and negative (`None`) entries.

Do not take a full snapshot at every import audit event. The cache grows with
one entry per path entry per package `__path__` and can reach thousands of
entries; O(cache size) work inside every import contradicts the minimal-
perturbation goal. Inside the audit hook, use a cheap fingerprint (e.g.,
`len(sys.path_importer_cache)` plus dictionary identity) to decide whether a
full snapshot is warranted.

Do not replace the cache with a dictionary subclass initially. Importlib may
hold references to the exact dictionary, and instrumentation would add work to
every path lookup. Passive diffs are less complete but safer.

**Dependencies:** None. T1 provides better attribution for cache changes.

**Definition of done:**

- Cache clears and finder replacements are visible without stringifying
  foreign finders.
- Snapshot storage has a documented capacity and overflow policy, and the
  per-import cost is bounded and independent of cache size.
- Reports tolerate concurrent cache changes and non-string keys.
- Tests reproduce clear, negative-cache, and finder-replacement sequences.

## T3: Build a path-resolution timeline

**Weakness:** Current events are grouped by mechanism. Users must manually
correlate a path-hook mutation, cache clear, later import, and changed loader.

**Recommendation:** Assign monotonic sequence numbers from the existing shared
recording state and render a combined timeline containing meta-path mutations,
path-hook mutations, importer-cache diffs, finder claims, and relevant import
audit snapshots. Retain the mechanism-specific report sections for focused
inspection.

The timeline must be based on recorded plain data. It must not perform foreign
object inspection while an import is active or while the state lock is held.

**Dependencies:** T1 and T2.

**Definition of done:**

- A report can show “hook inserted, cache cleared, module claimed” in capture
  order across mechanisms.
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

Never read these via plain attribute access: it executes code on foreign
modules. `importlib.util.LazyLoader` modules fully materialize on *any*
attribute access, and module-level `__getattr__` can import or raise. Read
`module.__dict__.get("__spec__")` and `module.__dict__.get("__loader__")`
instead, so the inventory cannot perturb exactly the environments it is
diagnosing.

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
stabilizing 1.0. T2--T7 may extend or reshape schema 0.x as their actual evidence models are implemented;
perform a schema 1.0 review after T7 and before T9 pins semantic assertions.
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
- `[loader-reentry]`: available evidence shows loader recursion through
  partially initialized state;
- `[failed-after-mutation]`: a failed import followed a relevant recorded
  mutation.

Findings should describe mechanics, not declare a third-party package broken.

`[failed-after-mutation]` has a hidden prerequisite: nothing currently records
failed imports. The `import` audit event fires when resolution *starts*, and a
failure leaves no `find_spec` claim and no `sys.modules` entry. Correlating
audit-event starts with absent outcomes is a new recording mechanism and must
be scoped as such, not smuggled in as a report label.

**Dependencies:** T3 and T4. Strong path-level findings also depend on T6.
`[failed-after-mutation]` additionally depends on new failed-import recording.

**Definition of done:**

- Every finding documents its evidence and known false positives.
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

**Dependencies:** T1, T2, T5, T7, and T8.

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

**Dependencies:** T1 through T4. T5 is recommended so crashes still leave
machine-readable evidence.

**Definition of done:**

- Each deep mechanism is independently toggleable.
- Tests include third-party-style identity and `isinstance` scans.
- A target produces the same import result with monitoring disabled, default
  monitoring, and deep monitoring for the supported test corpus.
- Cleanup remains reversible after normal completion, exceptions, recursive
  imports, and partial installation.

## Explicit non-goals

- Automatically inject into every subprocess through `.pth` files.
- Claim exact historical state when only report-time replay is available.
- Import or depend on freezer frameworks at metapathology runtime.
- Replace foreign finders or loaders in default mode merely to improve
  attribution.
- Diagnose arbitrary executable files from an unrelated outer Python process.
- Silently suppress or reorder third-party import hooks to make a target work.
