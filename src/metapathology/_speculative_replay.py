"""Bounded, report-time replay of displaced importer-cache finders.

This mechanism answers exactly one evidence-selected question about path-hook
and importer-cache contention (the beartype#599 shape): when a captured
``sys.path_importer_cache`` change removed or replaced a finder ``F`` for a
path ``P``, and a later import that traversed ``P`` failed to find a module
``M``, does the *retained* finder ``F`` still return a spec for ``M`` now?

Selection is pure and works only from already-captured evidence. The single
foreign call per selected candidate — ``F.find_spec(M, None)`` — runs at report
time under the caller's observation-suspension guard, calls no hook or loader, and
never mutates ``sys.path_hooks``, ``sys.path_importer_cache``, or
``sys.modules``. It says what the displaced finder returns now; it never claims
the original import would have succeeded.
"""

from metapathology._records import (
    DeepDiagnosticCall,
    ImporterCacheDiff,
    SpeculativeReplay,
    type_name,
)
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._records import MonitorEvent, ObjectRef

# The whole report is capped at this many foreign find_spec calls. Replay is a
# targeted probe, not a search; overflow is counted, never queued or retried.
MAX_SPECULATIVE_REPLAYS = 16


class _Candidate:
    """One displaced finder plus a later failed attempt that traversed its path."""

    __slots__ = ("attempt_seq", "diff_seq", "finder", "fullname", "path")

    def __init__(
        self,
        finder: "ObjectRef",
        path: str,
        diff_seq: int,
        fullname: str,
        attempt_seq: int,
    ) -> None:
        self.finder = finder
        self.path = path
        self.diff_seq = diff_seq
        self.fullname = fullname
        self.attempt_seq = attempt_seq


def _displaced_finders(events: "list[MonitorEvent]") -> "list[tuple[ObjectRef, str, int]]":
    """Collect ``(finder, path, diff_seq)`` for every cache displacement."""
    displaced: list[tuple[ObjectRef, str, int]] = []
    for event in events:
        if not isinstance(event, ImporterCacheDiff):
            continue
        displaced.extend((entry.finder, entry.path, event.seq) for entry in event.removed if entry.finder is not None)
        displaced.extend(
            (replacement.before, replacement.path, event.seq)
            for replacement in event.replaced
            if replacement.before is not None
        )
    return displaced


def _select_candidates(events: "list[MonitorEvent]") -> list[_Candidate]:
    """Pair each displaced finder with later failed lookups on its path.

    A failed lookup is a deep ``path_entry_finder`` call that returned no spec
    (``not_found``) for the displaced ``path`` after the displacement. That is
    the captured "later attempt M traversed P and failed" link; without deep
    path-entry evidence there is no defensible candidate and nothing is
    selected.
    """
    displaced = _displaced_finders(events)
    if not displaced:
        return []
    candidates: list[_Candidate] = []
    seen: set[tuple[str, str, int]] = set()
    for finder_ref, path, diff_seq in displaced:
        for event in events:
            if (
                not isinstance(event, DeepDiagnosticCall)
                or event.boundary != "path_entry_finder"
                or event.outcome != "not_found"
                or event.path != path
                or event.fullname is None
                or event.seq <= diff_seq
            ):
                continue
            key = (event.fullname, path, finder_ref.object_id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(_Candidate(finder_ref, path, diff_seq, event.fullname, event.seq))
    return candidates


def _replay_one(retained_finders: dict[int, object], candidate: _Candidate, had_target: bool) -> SpeculativeReplay:
    """Run at most one foreign ``find_spec`` for a selected candidate."""
    if had_target:
        # The original lookup carried a reload target we cannot faithfully
        # reconstruct at report time; a target=None probe would be a different
        # question, so decline rather than answer the wrong one.
        return SpeculativeReplay(
            candidate.fullname,
            candidate.path,
            candidate.finder,
            candidate.diff_seq,
            candidate.attempt_seq,
            "declined_target_unavailable",
        )
    finder = retained_finders.get(candidate.finder.object_id)
    if finder is None:
        return SpeculativeReplay(
            candidate.fullname,
            candidate.path,
            candidate.finder,
            candidate.diff_seq,
            candidate.attempt_seq,
            "finder_unavailable",
        )
    find_spec = getattr(finder, "find_spec", None)
    if not callable(find_spec):
        return SpeculativeReplay(
            candidate.fullname,
            candidate.path,
            candidate.finder,
            candidate.diff_seq,
            candidate.attempt_seq,
            "unsupported_finder",
        )
    try:
        spec = find_spec(candidate.fullname, None)
    except Exception as exc:
        # Only a foreign finder's ordinary failure is reduced to a safe outcome.
        # Control-flow exceptions outside ``Exception`` (KeyboardInterrupt,
        # SystemExit) propagate, never masking a genuine interrupt of the host.
        return SpeculativeReplay(
            candidate.fullname,
            candidate.path,
            candidate.finder,
            candidate.diff_seq,
            candidate.attempt_seq,
            "raised",
            None,
            type_name(exc),
        )
    if spec is None:
        return SpeculativeReplay(
            candidate.fullname,
            candidate.path,
            candidate.finder,
            candidate.diff_seq,
            candidate.attempt_seq,
            "returned_none",
        )
    summary, _loader = summarize_spec(spec, iterate_foreign_locations=False)
    return SpeculativeReplay(
        candidate.fullname,
        candidate.path,
        candidate.finder,
        candidate.diff_seq,
        candidate.attempt_seq,
        "returned_spec",
        summary,
    )


def replay_displaced_finders(
    events: "list[MonitorEvent]",
    retained_finders: tuple[tuple[int, object], ...],
) -> tuple[tuple[SpeculativeReplay, ...], int]:
    """Select and replay displaced cache finders, capped at a fixed budget.

    Returns the produced replays and the count of candidates omitted once the
    per-report cap was reached. The caller suspends observation around this
    probe so it cannot record monitor events or re-enter instrumentation.
    """
    candidates = _select_candidates(events)
    if not candidates:
        return (), 0
    # Map failed path-entry attempts to whether they carried a reload target, so
    # replay reproduces the original question or declines it.
    had_target: dict[int, bool] = {
        event.seq: event.target_state is not None
        for event in events
        if isinstance(event, DeepDiagnosticCall) and event.boundary == "path_entry_finder"
    }
    replays: list[SpeculativeReplay] = []
    finders_by_id = dict(retained_finders)
    omitted = 0
    for candidate in candidates:
        if len(replays) >= MAX_SPECULATIVE_REPLAYS:
            omitted += 1
            continue
        replays.append(_replay_one(finders_by_id, candidate, had_target.get(candidate.attempt_seq, False)))
    return tuple(replays), omitted
