# Limitations

Use these limits when interpreting missing evidence.

## Platform

Metapathology supports CPython 3.10+. It relies on CPython's
[audit events](https://docs.python.org/3/library/audit_events.html#audit-events)
and reports a warning on other implementations.

## Monitoring starts late

Anything completed before installation is historical context, not observed
activity. This includes finders added by already-processed `.pth` files. They
appear in the initial snapshot without an installation stack.

Remote attachment starts later still: the boundary is the target-observed
installation time, not the controller command time. Its normal-shutdown report
recovery is best effort and cannot cover forced or fatal termination. See
[Attach to a running process](attachment.md).

## The import audit event is only a start

CPython's `import` audit event precedes resolution. It:

- does not identify the winning finder;
- does not cover `sys.modules` cache hits;
- does not cover manual module execution; and
- cannot by itself tell whether the import later succeeded.

Enable the relevant detailed mechanism when those distinctions matter.

## Attribution is deliberately conservative

Finder attribution shadows `find_spec` only in writable instance dictionaries.
Metapathology never proxies finder objects or changes shared standard-library
finder classes. Unwritable and standard class finders may therefore appear only
in aggregate or snapshot evidence.

Without meta-path observation, attribution covers finders present at
installation only. Later additions are not instrumented.

## List replacement recovery needs audit activity

Meta-path observation itself owns only the reversible list observer. Direct
assignment such as `sys.meta_path = [...]` is discovered when the next import
audit event occurs. With import audit disabled, that recovery is unavailable.

The same constraint applies to direct replacement of observed `sys.path_hooks`
and `sys.path` lists.

## Checks inspect current state

Standard-path and displaced-finder checks run while constructing the report.
Files, hooks, caches, and finder state may have changed since import time.
Results are labeled as current-state evidence and never claim an alternative
historical winner.

Checks call finder methods supplied by Python or third-party packages.
Exceptions are isolated and reported, but those methods can still have side
effects.

## Unsafe exploration can change the run

Unsafe exploration executes code the import skipped. It does not execute
alternative loaders, but it cannot undo other side effects. Calls run in order,
so one explored finder can also affect the next.

An explored result is not proof that a finder would have won or that its loader
would work. See
[Unsafe import-branch exploration](capture.md#unsafe-import-branch-exploration)
for the exact boundary.

## Evidence is retained

Every event from an enabled capture mechanism is retained until reporting.
There is no background queue, retry loop, or automatic eviction. Long-running
or import-heavy programs can use substantial memory.

The displaced-finder check is the exception: it examines at most 16 candidates
per report and states how many were omitted.

## Reports may contain sensitive context

Text and JSON can contain:

- absolute or project-relative paths;
- command-line arguments;
- stack filenames and function names;
- module and finder type names; and
- object identities.

Review reports before sharing.
