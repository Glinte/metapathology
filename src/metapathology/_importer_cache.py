"""Bounded importer-cache observation and deterministic diffing."""

import sys
import threading

from metapathology._monitor_model import _ImporterCacheReportState
from metapathology._records import (
    ImporterCacheDiff,
    ImporterCacheEntry,
    ImporterCacheReplacement,
    ObjectRef,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._monitor import Monitor


def _cache_values_match(left: ObjectRef | None, right: ObjectRef | None) -> bool:
    """Compare captured cache values without relying on record identity."""
    if left is None or right is None:
        return left is right
    return left.object_id == right.object_id and left.type_name == right.type_name and left.name == right.name


def _diff_importer_cache(
    before: dict[str, ObjectRef | None],
    after: dict[str, ObjectRef | None],
) -> tuple[
    tuple[ImporterCacheEntry, ...],
    tuple[ImporterCacheEntry, ...],
    tuple[ImporterCacheReplacement, ...],
]:
    """Return deterministic additions, removals, and replacements."""
    before_paths = set(before)
    after_paths = set(after)
    added = tuple(ImporterCacheEntry(path, after[path]) for path in sorted(after_paths - before_paths))
    removed = tuple(ImporterCacheEntry(path, before[path]) for path in sorted(before_paths - after_paths))
    replaced = tuple(
        ImporterCacheReplacement(path, before[path], after[path])
        for path in sorted(before_paths & after_paths)
        if not _cache_values_match(before[path], after[path])
    )
    return added, removed, replaced


def _cache_entries(snapshot: dict[str, ObjectRef | None]) -> tuple[ImporterCacheEntry, ...]:
    return tuple(ImporterCacheEntry(path, snapshot[path]) for path in sorted(snapshot))


def _snapshot_importer_cache() -> tuple[
    dict[str, ObjectRef | None],
    int,
    dict[int, object],
    tuple[int, int],
]:
    """Copy string-keyed cache state without converting foreign objects."""
    cache = sys.path_importer_cache
    raw_items = list(cache.items())
    entries: dict[str, ObjectRef | None] = {}
    finders: dict[int, object] = {}
    non_string_keys = 0
    for path, finder in raw_items:
        if type(path) is not str:
            non_string_keys += 1
            continue
        if finder is None:
            entries[path] = None
            continue
        reference = ObjectRef.of(finder)
        entries[path] = reference
        finders[reference.object_id] = finder
    return entries, non_string_keys, finders, (id(cache), len(cache))


class _ImporterCacheObserver:
    """Own rolling cache state, coalescing, capacity, and shutdown."""

    def __init__(self, monitor: "Monitor") -> None:
        self._monitor = monitor
        self._enabled = False
        self._initial_non_string_keys = 0
        self._latest: dict[str, ObjectRef | None] | None = None
        self._latest_non_string_keys: int | None = None
        self._fingerprint: tuple[int, int] | None = None
        self._dirty = False
        self._observation_active = False
        self._observations = 0
        self._coalesced = 0
        self._observed_finders: dict[int, object] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True
        self.observe("install", initial=True)

    def audit_fingerprint(self) -> tuple[int, int] | None:
        if not self._enabled:
            return None
        cache = sys.path_importer_cache
        return id(cache), len(cache)

    def note_fingerprint_locked(self, fingerprint: tuple[int, int] | None) -> None:
        if fingerprint is not None and fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self._dirty = True

    def check_fingerprint(self) -> None:
        if not self._enabled:
            return
        cache = sys.path_importer_cache
        current = (id(cache), len(cache))
        with self._monitor._record_lock:
            if current != self._fingerprint:
                self._fingerprint = current
                self._dirty = True

    def observe(self, observation: str, *, initial: bool = False) -> None:
        monitor = self._monitor
        with monitor._record_lock:
            if not monitor._enabled or not self._enabled:
                return
            if self._observation_active:
                self._coalesced += 1
                self._dirty = True
                return
            self._observation_active = True
            self._dirty = False
        try:
            previously_active = monitor._local.active
            monitor._local.active = True
            try:
                snapshot, non_string_keys, finders, fingerprint = _snapshot_importer_cache()
            finally:
                monitor._local.active = previously_active
        except Exception as exc:
            with monitor._record_lock:
                self._observation_active = False
                self._dirty = True
            monitor._record_internal_error("importer_cache_snapshot", exc)
            return

        thread_name = threading.current_thread().name
        with monitor._record_lock:
            self._observation_active = False
            if not monitor._enabled or not self._enabled:
                return
            before = self._latest
            before_non_string = self._latest_non_string_keys
            self._latest = snapshot
            self._latest_non_string_keys = non_string_keys
            self._fingerprint = fingerprint
            self._observations += 1
            self._observed_finders.update(finders)
            if initial or before is None:
                monitor.initial_importer_cache = _cache_entries(snapshot)
                self._initial_non_string_keys = non_string_keys
                return
            added, removed, replaced = _diff_importer_cache(before, snapshot)
            if not added and not removed and not replaced and before_non_string == non_string_keys:
                return
            monitor._seq += 1
            monitor._events.append(
                ImporterCacheDiff(
                    seq=monitor._seq,
                    observation=observation,
                    added=added,
                    removed=removed,
                    replaced=replaced,
                    non_string_keys_before=0 if before_non_string is None else before_non_string,
                    non_string_keys_after=non_string_keys,
                    thread_name=thread_name,
                )
            )

    def report_state(self, monitor_enabled: bool) -> _ImporterCacheReportState:
        latest = self._latest
        return _ImporterCacheReportState(
            enabled=monitor_enabled and self._enabled,
            initial_entries=self._monitor.initial_importer_cache,
            initial_non_string_keys=self._initial_non_string_keys,
            latest_entries=None if latest is None else _cache_entries(latest),
            latest_non_string_keys=self._latest_non_string_keys,
            observations=self._observations,
            coalesced=self._coalesced,
        )

    def retained_finder(self, finder_id: int) -> object | None:
        """Return a live finder observed in the cache, for report-time replay."""
        if not self._enabled:
            return None
        return self._observed_finders.get(finder_id)

    def uninstall(self) -> None:
        self._observed_finders.clear()
        self._enabled = False
        self._observation_active = False
