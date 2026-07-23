"""Bounded report-time checks of displaced importer-cache finders."""

from metapathology._records import ImporterCacheChange, ImportMechanismCall, ObjectIdentity, type_name
from metapathology._report_model import CheckRun, FinderResult
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable

    from metapathology._records import MonitorEvent


MAX_DISPLACED_FINDER_CHECKS = 16


class _Candidate:
    __slots__ = ("call_seq", "diff_seq", "finder", "fullname", "path", "targeted")

    def __init__(
        self,
        finder: ObjectIdentity,
        path: str,
        diff_seq: int,
        fullname: str,
        call_seq: int,
        targeted: bool,
    ) -> None:
        self.finder = finder
        self.path = path
        self.diff_seq = diff_seq
        self.fullname = fullname
        self.call_seq = call_seq
        self.targeted = targeted


def _select_candidates(events: "list[MonitorEvent]") -> list[_Candidate]:
    displaced: list[tuple[ObjectIdentity, str, int]] = []
    for event in events:
        if isinstance(event, ImporterCacheChange):
            displaced.extend(
                (entry.finder, entry.path, event.sequence) for entry in event.removed if entry.finder is not None
            )
            displaced.extend(
                (replacement.before, replacement.path, event.sequence)
                for replacement in event.replaced
                if replacement.before is not None
            )
    candidates: list[_Candidate] = []
    seen: set[tuple[str, str, int]] = set()
    for finder, path, diff_seq in displaced:
        for event in events:
            if (
                not isinstance(event, ImportMechanismCall)
                or event.boundary != "path_entry_finder"
                or event.outcome != "not_found"
                or event.path != path
                or event.fullname is None
                or event.sequence <= diff_seq
            ):
                continue
            key = (event.fullname, path, finder.object_id)
            if key not in seen:
                seen.add(key)
                candidates.append(
                    _Candidate(finder, path, diff_seq, event.fullname, event.sequence, event.target_state is not None)
                )
    return candidates


def check_displaced_finders(
    events: "list[MonitorEvent]",
    retained_finders: tuple[tuple[int, object], ...],
    next_result_id: "Callable[[], str]",
) -> tuple[tuple[FinderResult, ...], CheckRun]:
    """Check evidence-selected displaced finders with a fixed synchronous budget."""
    candidates = _select_candidates(events)
    retained = dict(retained_finders)
    results: list[FinderResult] = []
    foreign_calls = 0
    for candidate in candidates[:MAX_DISPLACED_FINDER_CHECKS]:
        status = "not_found"
        summary = None
        exception_type_name = None
        finder = retained.get(candidate.finder.object_id)
        if candidate.targeted:
            status = "target_unavailable"
        elif finder is None:
            status = "finder_unavailable"
        else:
            try:
                find_spec = getattr(finder, "find_spec", None)
                if not callable(find_spec):
                    status = "unsupported_finder"
                else:
                    foreign_calls += 1
                    spec = find_spec(candidate.fullname, None)
                    if spec is not None:
                        status = "found"
                        summary, _loader = summarize_spec(spec, iterate_foreign_locations=False)
            except Exception as exc:
                status = "failed"
                exception_type_name = type_name(exc)
        results.append(
            FinderResult(
                result_id=next_result_id(),
                module=candidate.fullname,
                kind="displaced_finder_check",
                purpose="check_displaced_importer_cache_finder",
                limitations=(
                    "uses_report_time_finder_state",
                    "does_not_prove_loader_success",
                    "does_not_predict_alternative_winner",
                ),
                evidence_level="current_state_check",
                state_phase="report",
                predicts_alternative_winner=False,
                finder_type_name=candidate.finder.type_name,
                finder_id=candidate.finder.object_id,
                status=status,
                spec_summary=summary,
                exception_type_name=exception_type_name,
                source_event_seqs=(candidate.diff_seq, candidate.call_seq),
                search_path=(candidate.path,),
                search_path_kind="path_entry",
                search_path_phase="import",
            )
        )
    omitted = max(0, len(candidates) - MAX_DISPLACED_FINDER_CHECKS)
    return tuple(results), CheckRun(
        kind="displaced_finder",
        status="active",
        unavailable_reasons=(),
        candidates=len(candidates),
        results=len(results),
        foreign_calls=foreign_calls,
        capacity=MAX_DISPLACED_FINDER_CHECKS,
        omitted=omitted,
    )
