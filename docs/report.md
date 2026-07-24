# Read the report

Read from the top and stop when you have an explanation:

1. **Outcome and verdict** tell you how the target finished and whether
   metapathology found a concrete problem.
2. **Findings or explanations** name the affected module, finder, or path and
   point to the evidence.
3. **Finder comparisons** show how an observed custom-finder result differs
   from Python's standard path search.
4. **Timeline and change sections** show what happened and where import state
   was modified.

Paths below the working directory are shortened in text reports. JSON keeps
absolute paths and full object identities.

## Outcome and verdict

`target outcome:` reports whether the target completed, raised, or called
`SystemExit`. A target exception is not automatically an import-hook problem.

The verdict counts:

- **Problems**, where capture connects unusual import behavior to an observed
  effect.
- **Risks**, where the behavior is suspicious but the report cannot prove it
  caused a failure.
- **Notes**, which provide context without alleging a fault.

When a block says `observed`, metapathology recorded that event while the
target ran. `Correlated` joins several recorded events. `Inferred` combines
recorded activity with state inspected later. The last case deserves more
verification.

## Findings

### `module-executed-again`

**What it means.** The same loader executed the same module name again, but
with a different module object. This is not an ordinary
[`importlib.reload()`](https://docs.python.org/3/library/importlib.html#importlib.reload),
which normally reuses the object already stored in `sys.modules`.

**Why it matters.** Code can retain references to both objects. State, patches,
and type identities attached to one object are then invisible through the
other. Native extensions may also reject being initialized twice.

**What to check.** Follow both loader events. Look for code that removed or
replaced the first [`sys.modules`](https://docs.python.org/3/library/sys.html#sys.modules)
entry, or called `exec_module()` manually with another object. This finding
requires loader-call capture.

### `module-failed-after-loading`

**What it means.** One import loaded the module, then a later import resolved
the same normalized origin with the same loader type and failed.

**Why it matters.** A normal second import is served from `sys.modules`; it
does not resolve and execute the module again. Something made the first cache
entry unavailable. Source modules can fail on a second execution, and some
native extensions explicitly reject a second load.

**What to check.** Follow the linked searches and inspect code that removes or
replaces the module-cache entry. The report establishes the earlier success,
later failure, loader type, and origin. It does not invent an exception message
that was not captured.

### `import-failed-after-state-change`

**What it means.** An import failed after `sys.meta_path`, `sys.path_hooks`,
`sys.path`, or the importer cache changed during that same import search.

**Why it matters.** Changing import state while resolution is in progress can
remove the finder or path needed by the import. Timing alone does not prove
that this happened.

**What to check.** Open the linked change and its stack, then ask whether the
added, removed, or reordered entry could affect the failed module. This
finding requires exact import-result capture.

### `finder-changed-module-cache`

**What it means.** A finder's `find_spec()` call changed the target module's
`sys.modules` entry even though the finder returned `None` or raised.

**Why it matters.** A finder is expected to decide whether it can locate a
module. Loading or replacing that module while making the decision changes
what later finders and imports see.

**What to check.** The report shows the cache state before and after the call.
Inspect that finder's `find_spec()` implementation for an import of the target
or a related module. The
[`MetaPathFinder.find_spec()` contract](https://docs.python.org/3/library/importlib.html#importlib.abc.MetaPathFinder.find_spec)
is useful when reporting the behavior upstream.

### `module-replacement`

**What it means.** During a loader's `create_module()` or `exec_module()` call,
the object stored for the module name changed, or the loader was given a
different object from the one in `sys.modules`.

**Why it matters.** Recursive imports and existing references may see a
different module object from later imports. Lazy-loading systems sometimes do
this deliberately, so the finding is about identity, not necessarily a bug.

**What to check.** Compare the object identities shown in the finding. If the
replacement is intentional, check whether code keeps early references. If it
is not intentional, start with the named loader. This finding requires
loader-call capture.

### `missing-namespace-locations`

**What it means.** A custom finder returned a
[namespace package](https://docs.python.org/3/reference/import.html#namespace-packages)
with fewer search locations than `PathFinder` found, and an import below one
of the omitted locations failed.

**Why it matters.** A namespace package can span several directories. Its
`__path__` must preserve every contributing location; omitting one makes
modules in that directory unreachable.

**What to check.** The explanation names the missing location and failed
descendant. Editable-install finders are a common source, so try reinstalling
the distribution, comparing editable and regular installs, and reporting the
omission to the build backend or finder maintainer.

**Further investigation.** If files or import state may have changed before
reporting, [unsafe branch exploration](capture.md#unsafe-import-branch-exploration)
can ask the skipped `PathFinder` during a disposable reproduction.

### `module-hides-namespace`

**What it means.** `PathFinder` saw a namespace-package portion in an earlier
`sys.path` entry, continued searching, and selected a regular module or package
from a later entry. An import below the selected name then failed.

**Why it matters.** Continuing after a namespace portion is normal Python
behavior. The problem is the collision: a later regular module wins and may
not support the descendants that existed under the namespace directory.

**What to check.** The explanation names the namespace location, selected
file, and failed descendant. Fix path order, rename the colliding module, or
make the selected object a package if descendants are intended.

### `competing-path-hooks`

**What it means.** Two different
[path hooks](https://docs.python.org/3/reference/import.html#path-entry-finders)
accepted the same path in observed calls.

**Why it matters.** `PathFinder` tries `sys.path_hooks` in order and caches the
first path-entry finder that accepts a path. The other hook does not process
imports from that path.

**What to check.** Confirm that the acceptances refer to comparable path state;
the report may have observed them at different times. Reordering hooks and
clearing `sys.path_importer_cache` can demonstrate the conflict, but it merely
chooses the other behavior. The durable fix is for the tools to cooperate,
often by delegating to or wrapping
[`FileFinder`](https://docs.python.org/3/library/importlib.html#importlib.machinery.FileFinder).
This finding requires path-hook-call capture.

**Further investigation.** To see whether the skipped hook's finder can locate
the affected module, use
[unsafe branch exploration](capture.md#unsafe-import-branch-exploration) in a
disposable reproduction.

### `legacy-finder-api`

**What it means.** A meta-path finder has callable `find_module()` but no
callable `find_spec()`.

**Why it matters.** `find_module()` is the pre-3.4 API. Python 3.12
[removed the fallback that called it](https://docs.python.org/3/whatsnew/3.12.html#importlib),
so newer interpreters skip that finder. Code that assumes every meta-path
entry has `find_spec()` can also fail; [pytest#12179](https://github.com/pytest-dev/pytest/issues/12179)
is a real example.

**What to check.** Use the linked `sys.meta_path` change to identify what
installed the finder, then upgrade or replace that package. Metapathology
inspects the methods without calling them.

### `module-without-spec`

**What it means.** A module is in `sys.modules` without
[`__spec__`](https://docs.python.org/3/reference/import.html#import-related-module-attributes),
and no recorded finder call explains it.

**Why it matters.** Normal imports create a
[`ModuleSpec`](https://docs.python.org/3/library/importlib.html#importlib.machinery.ModuleSpec).
A missing spec often means code created the module manually or executed it
through lower-level loader APIs, bypassing tools that rely on import hooks.

**What to check.** Many such modules are harmless—some standard-library
modules are created this way. Investigate only when the named module should
have passed through another import hook. Then look for `types.ModuleType`,
manual `sys.modules` insertion, or direct loader execution.

## Finder results and comparisons

For modules found by a custom finder, the report can show:

- **During the run:** the result that finder actually returned.
- **Standard search at report time:** what
  [`PathFinder.find_spec()`](https://docs.python.org/3/library/importlib.html#importlib.machinery.PathFinder.find_spec)
  returns for the same name and recorded search path when the report is built.

A different loader is often legitimate. Editable installs, assertion
rewriters, and instrumentation tools exist specifically to use custom loaders.
The comparison becomes useful when another tool expected the standard path
search to run. In [beartype#556](https://github.com/beartype/beartype/issues/556),
an editable-install finder handled the module first, so beartype's path hook
never saw it.

The standard search uses current files, hooks, and caches. It also calls
`PathFinder` directly, skipping other meta-path finders. Read it as “what the
standard path search returns now,” not “what would have won earlier.”

If that timing difference matters, use
[unsafe branch exploration](capture.md#unsafe-import-branch-exploration) in a
disposable reproduction to ask later finders during the import.

The optional displaced-finder check investigates a different pattern: an
importer-cache entry changed, a later lookup through that path failed, and the
old finder is still available to ask about the failed module. It checks at most
16 candidates per report and states when more were omitted.

An explored result says that a skipped candidate returned a spec, returned
nothing, or raised when called. Check its timeline events for call order and
module-state changes.

`predicts_alternative_winner` is always false. Before changing finder order,
inspect the spec difference and look for an independently observed effect.

## Imports that did not produce a module

This section lists import searches that left no module in `sys.modules`.
Failed optional imports are common and harmless. Pay attention when the target
failed for the same module; the report marks it.

An `import started` event comes from CPython's
[`import` audit event](https://docs.python.org/3/library/audit_events.html#audit-events).
It says resolution started, not that the import succeeded or which finder won.
Imports served from the module cache do not emit this event.
On CPython 3.15 and newer, these events also cover searches started through
`importlib.import_module()`; earlier CPython versions omit those searches.

## Finder calls

This section counts calls to instrumented finder instances and lists the
modules they found. Standard CPython class finders are deliberately not
modified; their work appears in the standard-resolution section instead.

## Event timeline and change sections

The timeline orders all recorded events under one `#n` sequence so findings
can point back to them. On multi-threaded runs, this is monitor-recording order,
not a precise wall-clock order.

Long runs of routine events may collapse in text. Set
`METAPATHOLOGY_TEXT_TIMELINE=full` to show every event. JSON is always
complete.

The sections after the timeline answer specific questions:

- `sys.meta_path changes`: who added, removed, or reordered finders?
- `sys.meta_path replacements`: when did metapathology discover that code
  assigned a new list? The shown stack belongs to the next import, because
  plain assignment cannot be intercepted.
- `sys.path_hooks changes` and replacements: who changed path-hook order?
- `sys.path changes` and replacements: what changed when `--sys-path` was
  enabled?
- `sys.path_importer_cache changes`: which paths gained, lost, or switched
  path-entry finders between snapshots?
- Loader inventory: which loader does each module report at report time?
- Monitoring errors: where did metapathology lose evidence while allowing the
  target import to continue?

## Report-time checks

`active` means the check ran. `disabled` means configuration turned it off.
`unavailable` means it was requested but a required capture mechanism was
explicitly disabled; the report lists what is missing.

## What “no problems found” means

It means the enabled capture mechanisms did not produce a problem finding. It
does not certify the entire import history. Check the `monitoring:` line and
[limitations](limitations.md), especially when the relevant finder was
installed before monitoring or the import was served from `sys.modules`.
