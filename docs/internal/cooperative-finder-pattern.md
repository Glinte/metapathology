# The cooperative delegating meta-path finder pattern

Status: design note, recorded 2026-07-19. Not metapathology work — this is
about how import-instrumenting tools (beartype among them) could avoid the
conflicts metapathology diagnoses. Relevant to our docs advice
(`docs/report.md`, path-hook-shadow "What to do") and to upstream
discussions such as beartype#556.

## The observation

beartype's claw registers a `sys.path_hooks` entry that wraps `FileFinder`.
That position has two silent, persistent ways to lose:

1. An earlier `sys.meta_path` finder answers before `PathFinder`, so no
   path hook runs at all (scikit-build-core's redirecting finder;
   beartype#556 — the incident this project was built to diagnose).
2. The contested path entry is already in `sys.path_importer_cache` under
   someone else's path entry finder, so the hook is never consulted for it.

A finder at the front of `sys.meta_path` runs on *every* non-cached import
and has exactly one way to lose — a finder inserted before it later — and
that loss is visible in any `sys.meta_path` snapshot.

## The pattern

A meta-path finder at position 0 whose `find_spec()`:

1. delegates to the *remaining* meta-path finders in order (a snapshot of
   `sys.meta_path`, skipping every occurrence of itself);
2. inspects the returned spec: source origin present? loader a
   `SourceLoader` exposing `get_source` / `source_to_code`?
3. wraps the spec's loader with a delegating wrapper that interposes only
   the transform (e.g. `source_to_code`) when it can, and returns the spec
   untouched when it cannot.

Uncooperative finders then run *inside* the delegation instead of shadowing
the tool: scikit-build-core's finder returns its spec, the wrapper sees a
source loader, and the transform still applies. Failure degrades to
"module not transformed" rather than "tool silently disabled."

Best property: two such delegating finders **compose** — each wraps the
spec the next produced — whereas two path hooks contending for the same
path entry can never both win.

## Prior art and the common shortcut

pytest's `AssertionRewritingHook` and typeguard's import hook are the same
shape (meta-path finder producing a rewritten-source loader). Both take a
shortcut, though: they delegate to `PathFinder.find_spec()` directly rather
than to the rest of the chain. That merely flips who gets bypassed — now
the custom finder between them and `PathFinder` (e.g. an editable-install
redirector) is skipped, which can import the wrong files entirely.

## Why the full pattern is hard (be honest about this upstream)

- **No public "call the rest of the chain" API.** The cooperative variant
  reimplements `importlib._bootstrap._find_spec` semantics: meta-path
  snapshotting, self-skipping (all occurrences), and a re-entrancy guard
  because delegation itself can trigger imports. Private-machinery
  mirroring like this breaks subtly across CPython versions.
- **Loader wrapping perturbs identity.** Replacing `spec.loader` changes
  `module.__loader__` and `isinstance` results that other tools check. A
  wrapper that delegates everything and interposes only `source_to_code`
  minimizes this, but double-wrapping between two tools is possible.
- **The ordering war moves, not ends.** Anyone inserting a meta-path
  finder before the delegator still wins. The improvement is visibility
  (snapshots show it) and composability (delegators stack), not immunity.

## How metapathology relates

- A cooperative delegator appears in our reports as the claiming finder
  with a wrapped loader type — a benign loader-type difference in the
  "modules found by a custom finder" comparison, not a bypass.
- A metapathology report from `reproductions/beartype-556` is the concrete
  exhibit for the upstream argument: it shows the claim-before-`PathFinder`
  mechanism this pattern eliminates.
- If this pattern gains adoption, the path-hook-shadow and comparison
  guidance in `docs/report.md` should name it as the stronger form of
  "make the hooks cooperate."
