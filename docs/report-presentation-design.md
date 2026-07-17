# Report presentation design

Status: design plan, 2026-07-17. The prerequisite resolution-route refactor
and replay-boundary correctness fixes are implemented. The five presentation
stages described here remain unimplemented unless noted otherwise.

## Decision

Redesign how the text report communicates, in five independently reviewable
stages: mechanical wording repairs, a merged explanation/finding narrative, a
leading verdict with target-outcome correlation, timeline and diff volume
control, and a final documentation/reproduction synchronization pass.

The evidence pipeline does not need new capture mechanisms for any of this.
`render_lines()` in `_report_text.py` is a pure projection of
`ReportDocument`, and the data for the largest substance gap already exists:
`_import_attempts()` in `_report_data.py` computes per-attempt `progress` and
`presence`, but attempts are rendered only in JSON. The report is a database
dump with excellent provenance; the missing work is assembly and wording.

## Problem evidence

A review of the current text output against the reproduction suite found
these communication failures, in decreasing order of impact:

1. **No verdict.** The wrapt 1.14.2 report contains every fact needed for
   "wrapt's import hook replaced the loader metadata for `target`" but never
   states it. A zero-finding run prints `-- findings (0) -- (none)`, which
   reads as tool failure rather than a clean result.
2. **The target's failure is never connected to the findings.** In
   pwntools#2737 the program dies with `ModuleNotFoundError:
   pwnlib.shellcraft.amd64`; the report separately shows the `LazyImporter`
   insertion, its legacy-only contract warning, and every finder declining
   the failed name, but never joins them. The reproduction README calls the
   diagnosis "obvious"; it is left as an exercise.
3. **Explanations and findings duplicate each other.** A
   `custom_claim_displacement` explanation is generated 1:1 from its
   displacement finding and both render in full with different labels
   (`[counterfactual]` vs `[loader-displacement]`), reading as two problems.
4. **Cross-references point at invisible anchors.** `cause: finding:1` is
   rendered, but finding ids are never displayed, and the severity sort
   reorders findings relative to their ids.
5. **The timeline is exhaustive to the point of hostility.** beartype#556
   prints 1,104 events of which roughly two matter.
6. **Wording bugs.** `probed 'X': passed` reads as success where it means
   declined; `1 probes`; `import audit:` names the mechanism rather than the
   meaning and needs a standing preamble disclaimer; snake_case enum values
   leak into prose (`evidence: live_replay`); the documented "same origin"
   collapse is applied in only one finding branch; `<project>` is never
   defined; hex object ids appear where nothing needs disambiguation;
   `_Finder` (virtualenv) heads every meta-path snapshot unexplained.
7. **Severity does not drive layout.** An informational editable-redirect
   block looks identical to an actionable one.

## Sequencing constraints

The two prerequisites for most of this work are complete:

- The [speculative replay design](speculative-replay-design.md) Phase 0
  correctness fixes (wrapper-identity normalization, report-cutoff guard,
  reload-target evidence).
- The resolution-route evidence refactor. Resolution evidence is
  modeled as independent *routes* (captured claim, standard path probe,
  future displaced-finder probe) with symmetric neutral comparisons; a route
  difference is not by itself a finding; severity follows corroborating
  evidence, not finder-name heuristics; directional vocabulary
  (`live_replay`, `omitted_locations`, "PathFinder replay") is replaced with
  neutral route vocabulary, and `PathFinder` output is presented as a probe
  of one route, never as the winner an import "should" have had.

Stage 1 below remains independently deliverable. Stages 2-4 now build on the
implemented route vocabulary and severity policy. Stage 5 runs last.

## Stage 1: mechanical wording repairs (independent)

Renderer-only changes in `_report_text.py` plus test updates. Finding-block
prose is deliberately excluded; Stage 2 rewrites it on the route model.

- Rename timeline outcomes: `probed 'X': passed` becomes
  `probed 'X': declined`.
- Rename audit lines: `import audit: resolution started for 'X'` becomes
  `uncached import started: 'X'`, and the four-line timeline preamble
  shrinks to one line plus the report-guide link.
- Rework the header: positive phrasing for the `monitoring:` line instead of
  three parenthesized absence clauses ("unobservable", "inactive"); collapse
  the three-line standard-finders note to one line; annotate known
  environment shims from a display-only allowlist, for example
  `_Finder (virtualenv startup, expected)`. The allowlist affects display
  annotation only, never severity or finding logic, so it does not conflict
  with the route refactor's rejection of name heuristics.
- Fix grammar and formats: pluralize `1 probes`; define `<project>` where
  the base directory is introduced; apply the documented same-origin
  collapse in every branch that prints paired origins, including the causal
  explanation block; drop hex ids wherever the ambiguity rule does not
  actually require them.
- Unify the empty-section policy: all empty sections collapse into the
  single trailing line, rewritten as prose, and an empty findings section
  becomes an explicit clean-result line naming the number of monitored
  imports.
- Minimally humanize the `severity: ...; evidence: ...; limitations: ...`
  metadata line (full rewrite in Stage 2).

Definition of done:

- No timeline line uses "passed" for a declined probe; no header line
  describes a disabled optional mechanism with alarm words.
- Text output contains no raw snake_case enum values outside JSON.
- A zero-finding run states a clean result in one sentence.
- Reproduction suite output remains diagnosable; affected README excerpts
  are regenerated (or deferred to Stage 5 in one batch).

## Stage 2: merged explanation/finding narrative (after route refactor)

- Explanations become the headline blocks. Findings linked through
  `cause_finding_id` render as indented evidence beneath their explanation
  rather than as sibling top-level blocks; orphan findings render
  standalone. One problem, one block.
- Number blocks visibly (`[1]`, `[2]`) in display order and re-key
  cross-references after the severity sort so `cause: finding:1` becomes
  "see [1]" and points at something on screen.
- Add one static "why it matters" consequence line per finding kind (for
  example, loader displacement: code checking `__loader__` or
  `__spec__.loader`, or another import hook expecting to process the module,
  sees the substituted loader). This is the route model's interpretation
  layer expressed in prose; it must not overstate beyond the kind's
  documented semantics.
- Keep the implemented route-neutral labels in text: captured route and
  standard path probe (report time). Rewrite remaining evidence metadata as
  English prose.
- Severity-driven layout: actionable and warning blocks render in full;
  informational findings compress to one line each under a subheading.

Definition of done:

- The wrapt 1.14.2 report presents one block that names the finder, the
  installing package and stack location, both routes, and the consequence.
- No fact is printed twice for a single subject.
- Every rendered cross-reference resolves to a visible label.
- Informational editable redirects cannot be visually mistaken for the
  headline.

## Stage 3: verdict and target-outcome correlation

- Compute a `ReportSummary` in `_report_data.py`, shared by text and JSON:
  severity counts plus a one-sentence headline derived from the top
  explanation. Print it immediately after `== metapathology report ==`.
  Clean runs get "no import-hook interference detected in N monitored
  imports."
- Capture the target outcome in the CLI: `_run()` in `__main__.py` already
  catches the target's exception; record its type name, the `name`
  attribute for `ModuleNotFoundError`/`ImportError`, and the exit status
  through a small monitor API before the report is written. The summary
  then opens with the failure, and synthesis joins the failed name against
  import attempts. Reading `type(exc).__name__` and the builtin exception's
  `name` attribute stays within the existing no-foreign-stringification
  policy; message text is not captured.
- Render unresolved imports in default mode: attempts with
  `presence == "absent_at_report"` and no claim become a bounded "imports
  that started but produced no module" section. When an uninstrumented or
  legacy-only finder was inserted before such an attempt, add the
  conservative connective ("`LazyImporter` was on `sys.meta_path` but is
  legacy-only; CPython 3.12+ never calls `find_module` — see [1]") at
  `structural_inference` level. This assembles the pwntools#2737 diagnosis
  from evidence the monitor already retains.
- JSON gains `summary` and `target_outcome` objects with a schema-minor
  bump while 0.x remains fluid.

Definition of done:

- The pwntools#2737 fixture's summary names the failed module and links the
  legacy-only finder in one visible chain.
- A trivial monitored script reports a clean verdict in its first two
  lines.
- The summary never asserts causation beyond the underlying explanation's
  confidence label.

## Stage 4: volume control

- Collapse timeline runs: consecutive audit-start and declined-probe events
  with no claims, mutations, reassignments, cache diffs, or errors collapse
  to one line stating the sequence range, count, and that full detail is in
  JSON. Any event referenced by a finding or explanation always renders
  expanded, with one line of context on each side.
- Filter cache diffs by relevance: full entries only for paths intersecting
  a finding's captured search path (the `_cache_event_seqs_by_path` index
  already exists); everything else summarizes as per-root counts. JSON
  stays exhaustive.
- Standardize one "details in JSON" trailer convention, replacing ad-hoc
  parentheticals.
- Add `METAPATHOLOGY_TEXT_TIMELINE=full` as an escape hatch restoring the
  exhaustive timeline. Collapsed is the proposed default; this is the one
  open decision to confirm before implementation.
- Enabling refactor: split `render_lines()` into per-section builders
  registered with one section framework that owns headers, trailers, empty
  labels, and the JSON-details convention, so the policies are enforced
  mechanically rather than by copy-paste.

Definition of done:

- The beartype#556 text report shrinks from roughly 1,100 timeline lines to
  under 100 without losing any line referenced by a finding, explanation,
  or mutation record.
- Repeated renders produce identical collapsed output for identical event
  logs.
- The escape hatch reproduces the pre-collapse timeline exactly.

## Stage 5: documentation and reproduction synchronization

- Rewrite `report.md` around the final structure: summary first, the merged
  narrative section documented (the current "causal explanations" section
  is undocumented), an evidence-level table, and the new section order.
  Fix any stale claims about the leading section or current schema minor.
- Replace the README's duplicated finding-label list with a short sample
  report excerpt and a link to `report.md`, eliminating that drift surface.
- Regenerate every reproduction README excerpt from real output. The
  reproduction table and several READMEs still promise a `[bypass]` label
  that no longer exists.
- Extend `validate_report.py`-style assertions beyond beartype#556 to at
  least pwntools-2737 and pytest-12179 so excerpt drift fails a check
  instead of waiting for a user to notice.

Definition of done:

- No public document names a finding label, section heading, or schema
  version that the current code does not produce.
- At least three reproductions assert on report content mechanically.

## Non-goals

- No changes to capture semantics, hot-path recording, or event retention.
  Text remains a bounded projection; JSON remains exhaustive.
- No golden-file contract on complete text output; assertions remain
  semantic (labels, joins, counts, presence of referenced lines).
- Human prose stays out of the JSON contract, as established by T5/T15.
- No natural-language generation beyond deterministic templates.

## Test plan

- Update `test_report_text.py`, `test_timeline.py`,
  `test_causal_synthesis.py`, and `test_structured_report.py` per stage;
  wording changes land with their test updates in the same commit.
- Stage 3 adds a synthetic fixture asserting the summary line, target
  outcome capture, and the unresolved-import join end to end in a
  subprocess.
- Stage 4 adds collapse-boundary tests: a claim in the middle of a
  collapsible run, a mutation adjacent to a run, an event referenced by an
  explanation inside a run, and the escape-hatch equivalence check.
- Each stage ends by running the reproduction suite and comparing reports
  against the stage's definition of done.

## Reassessment rule

Stage 1 is approved wording repair. Stages 2-4 assume the resolution-route
refactor's model and vocabulary land first; if that refactor changes shape,
revisit Stage 2's block structure before implementing it. Stage 5 is
mechanical and runs last regardless.
