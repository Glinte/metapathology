"""Per-document display state shared by text-report sections."""

import os

from metapathology._records import MetaPathFinderCall, ObjectIdentity, PathHooksChange, PathHooksReplacement
from metapathology._report_model import ReportDocument, finding_structural_comparison

_ANSI_RESET = "\x1b[0m"
_ANSI_BOLD_CYAN = "\x1b[1;36m"
_ANSI_BOLD_RED = "\x1b[1;31m"
_ANSI_GREEN = "\x1b[32m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_CYAN = "\x1b[36m"


class _RenderContext:
    """Path, identity-label, and styling state computed once per document."""

    __slots__ = (
        "_base",
        "_base_prefix",
        "color",
        "comparisons_by_id",
        "finder_ambiguous",
        "hook_ambiguous",
        "only_thread",
        "relevant_cache_paths",
        "results_by_id",
    )

    def __init__(self, document: ReportDocument, *, color: bool) -> None:
        self.color = color
        cwd = document.process.cwd
        if cwd:
            base = os.path.normcase(cwd).rstrip(os.sep)
            self._base: str | None = base
            self._base_prefix: str | None = base + os.sep
        else:
            self._base = None
            self._base_prefix = None

        threads = {
            thread
            for thread in (getattr(event, "thread_name", None) for event in document.analysis.events)
            if thread is not None
        }
        self.only_thread: str | None = next(iter(threads)) if len(threads) == 1 else None

        finder_ids: dict[str, set[int]] = {}
        hook_ids: dict[str, set[int]] = {}
        for event in document.analysis.events:
            if isinstance(event, MetaPathFinderCall):
                finder_ids.setdefault(event.finder_type_name, set()).add(event.finder_id)
            elif isinstance(event, PathHooksChange):
                for reference in (*event.added, *event.removed, *event.contents_after):
                    _note_label(hook_ids, reference)
            elif isinstance(event, PathHooksReplacement):
                for reference in (*event.old_contents, *event.new_contents):
                    _note_label(hook_ids, reference)
        for reference in (*document.path_hooks.initial, *(document.path_hooks.current or ())):
            _note_label(hook_ids, reference)
        self.finder_ambiguous = {label for label, ids in finder_ids.items() if len(ids) > 1}
        self.hook_ambiguous = {label for label, ids in hook_ids.items() if len(ids) > 1}
        self.results_by_id = {result.result_id: result for result in document.analysis.finder_results}
        self.comparisons_by_id = {
            comparison.comparison_id: comparison for comparison in document.analysis.finder_result_comparisons
        }

        relevant_cache_paths: set[str] = set()
        for finding in document.analysis.findings:
            structural = finding_structural_comparison(finding)
            if structural is not None:
                relevant_cache_paths.update(os.path.normcase(path) for path in structural.importer_cache_changed_paths)
        for result in document.analysis.finder_results:
            relevant_cache_paths.update(os.path.normcase(path) for path in result.search_path)
        self.relevant_cache_paths = relevant_cache_paths

    def display_path(self, path: str) -> str:
        """Shorten a path under the report's base directory."""
        if self._base is None or self._base_prefix is None:
            return path
        # normcase preserves length, so slicing the original keeps its casing.
        normalized = os.path.normcase(path)
        if normalized.rstrip(os.sep) == self._base:
            return "<project>"
        if normalized.startswith(self._base_prefix):
            return path[len(self._base_prefix) :]
        return path

    def quoted_path(self, path: str) -> str:
        """Quote a shortened path without repr's backslash escaping."""
        return f"'{self.display_path(path)}'"

    def finder_label(self, type_name: str, finder_id: int) -> str:
        """Name an instrumented finder, adding its id only when ambiguous."""
        if type_name in self.finder_ambiguous:
            return f"{type_name} id 0x{finder_id:x}"
        return type_name

    def hook_ref(self, reference: ObjectIdentity) -> str:
        """Name a path hook, adding its id only when ambiguous."""
        label = _ref_label(reference)
        if label in self.hook_ambiguous:
            return f"{label} id 0x{reference.object_id:x}"
        return label

    def hook_refs(self, references: tuple[ObjectIdentity, ...]) -> str:
        """Format a path-hook snapshot."""
        return "[" + ", ".join(self.hook_ref(reference) for reference in references) + "]"

    def thread_suffix(self, thread_name: str) -> str:
        """Return a thread marker unless the whole capture used one thread."""
        return "" if self.only_thread is not None else f" [thread {thread_name}]"

    def styled(self, text: str, ansi: str) -> str:
        """Wrap one semantic token in ANSI escapes when color is enabled."""
        return f"{ansi}{text}{_ANSI_RESET}" if self.color else text

    def heading(self, text: str) -> str:
        """Style a report or section heading."""
        return self.styled(text, _ANSI_BOLD_CYAN)

    def severity(self, text: str, severity: str) -> str:
        """Style a compact marker according to finding severity."""
        ansi = {
            "problem": _ANSI_BOLD_RED,
            "risk": _ANSI_YELLOW,
            "note": _ANSI_CYAN,
        }.get(severity, _ANSI_CYAN)
        return self.styled(text, ansi)

    def positive(self, text: str) -> str:
        """Style a positive status marker."""
        return self.styled(text, _ANSI_GREEN)

    def negative(self, text: str) -> str:
        """Style a failure or removal marker."""
        return self.styled(text, _ANSI_BOLD_RED)

    def risk(self, text: str) -> str:
        """Style a risk or replacement marker."""
        return self.styled(text, _ANSI_YELLOW)


def _note_label(ids_by_label: dict[str, set[int]], reference: ObjectIdentity) -> None:
    """Record one displayed label/id pair for ambiguity detection."""
    ids_by_label.setdefault(_ref_label(reference), set()).add(reference.object_id)


def _ref_label(reference: ObjectIdentity) -> str:
    """Return a callable's own name when available, else its type name."""
    return reference.type_name if reference.name is None else reference.name
