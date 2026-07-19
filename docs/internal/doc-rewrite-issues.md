# Documentation and text-report issues (recorded before reading the code)

Observations from reading README.md, docs/*.md, and running the
beartype-556 and pytest-12179 reproductions. Written down before opening
`src/` so the critique reflects a reader's view, not the implementation.

## Vocabulary problems (docs and report)

Invented terms used without definition, where the Python docs already have
standard words or plain English works:

- "probe" / "probed" — a finder was *asked* / its `find_spec()` was called.
  "365 probes" reads like a medical report. Also collides with the separate
  "standard path probe".
- "claim" / "claimed" / "captured claim" — the finder *returned a spec* /
  *found the module*. "claims" as a noun is especially opaque.
- "resolution route divergences" — nowhere near normal English. This is
  "the finder that found the module differs from what the normal `sys.path`
  search would find".
- "target" — sometimes the script being run, sometimes the module being
  imported. Pick "your program" / "the module" per context.
- "synthesized" — "No findings were synthesized" should be "No problems were
  found" or similar.
- "early site bootstrap" — needs a plain name and a one-line explanation
  wherever it appears ("an optional `.pth` file that starts monitoring
  during interpreter startup").
- "negative (None)" in cache diffs — Python docs do say `None` means the
  path is not handled; "negative" adds jargon.
- "post-hoc", "constant-size", "document-scoped", "exhaustive",
  "boundary", "structural evidence", "evidence level", "identity-only" —
  spec-review language, not user language. Fine internally; not in docs.
- "uncached import started" / "progress started, began at event #60" —
  awkward; "import of 'pwd' started (event #60) but no module resulted".
- Internal task IDs leak: api.md says "links the semantic `SpecSummary` to
  the exact T13 attempt". Nobody outside the project knows what T13 is.
  (The pytest-12179 invoke.py also prints "T12 evidence verified".)

Python-docs-aligned terms to prefer: meta path finder, path entry finder,
path based finder, module spec, `find_spec()`, the module cache
(`sys.modules`), path entry hook.

## Text report problems (from real runs)

- Repeated boilerplate on every occurrence instead of stated once:
  - "details in JSON timeline" on every collapsed timeline line.
  - "(virtualenv startup, expected)" attached to `_Finder` every time it is
    printed, including inside every timeline meta_path snapshot.
  - Every route-divergence block repeats three fixed sentences
    ("interpretation: …", "probe boundary: …") that never vary. Say it once
    at the top of the section, or link to the docs.
  - Header sentence "custom claims are compared with an independent standard
    path probe below" is a tangent inside a list line.
- "verdict: no findings; neutral resolution route divergences were
  recorded" — double jargon for "no problems found; one difference worth a
  look is listed below".
- "monitoring: … (opt-in deep diagnostics and early site bootstrap available
  but not used for this run)" — advertising unused features in every report.
- Advanced trivia in normal reports: "string-keyed entries" for
  `sys.path_importer_cache` counts; "attribution remains report-time";
  "relevant post-hoc loader inventory" heading; "concurrent events have no
  global wall-clock order" preamble on a single-threaded run.
- "finder attribution (instrumented finders only)" — heading jargon; the
  parenthetical belongs in one explanatory sentence, if anywhere.
- "standard resolution outcomes" + "[inferred standard resolution]" — could
  say the report inferred that PathFinder handled it, in plain words.
- The `-- findings (0) --` section prints a sentence re-stating the verdict
  line; duplication.
- Section order is good (verdict first); wording is the problem.

## README problems

- Audience mixing. Advanced/rare material appears before basic usage or is
  interleaved with it: the `py3-none-any` wheel / non-CPython paragraph
  (before Usage!), frozen/embedded apps, the `.pth` bootstrap, deep
  diagnostics option matrix, "Resource use" detail. Each should be one or
  two sentences with a link, placed late, or cut.
- Dense wall-of-paragraph enumerations of flags and env vars duplicate the
  usage guide (`--color` precedence rules, `{pid}` naming, every deep
  switch). README needs: what it is, why, install, two commands, one sample
  report snippet, links.
- The sample report shown is the most exotic case (namespace truncation)
  full of unexplained jargon; a simple bypass example (the beartype-556
  story) would teach more.
- "How Python finds an imported module" and "How it works" are decent but
  duplicate docs/concepts.md; README can compress both drastically.
- Sentences written to defend design decisions ("without a collector,
  background worker, or retry loop", "no fixed cap, silent dropping,
  retries, queue") — meaningless to a new reader.

## Per-doc problems

- docs/report.md: leads with JSON schema contract details (versioning,
  null-vs-omitted, array ordering) before explaining the text sections a
  user actually reads first. Move JSON to the end. Finding descriptions are
  written as promotion-rule spec, not "what does this mean, what do I do".
- docs/api.md: reference is fine in shape but shares the jargon; T13 leak;
  many "deliberately/intentionally" defenses.
- docs/limitations.md: important content buried in abstract phrasing
  ("Contention findings intentionally degrade with capture settings").
  Rewrite as concrete "you will not see X unless Y".
- docs/usage.md: mostly good; deep-diagnostics and color/env-var paragraphs
  need structure (lists/tables) and less precedence-rule prose.
- docs/concepts.md: closest to right register; light touch only.
- docs/frozen.md, performance.md, development.md: acceptable; trim
  defensive asides where cheap.

## Principles for the rewrite

- Use Python documentation terminology; define any term of ours on first
  use, once, and reuse it consistently.
- Say invariant explanations once per report/section, not per line.
- Normal reports serve normal users; advanced mechanics go to docs pages
  (and JSON), reached by the one `report guide:` link.
- Short sentences; no defensive "intentionally/deliberately" unless the
  reader would otherwise think it is a bug.
