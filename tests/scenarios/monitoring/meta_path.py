"""Isolated child scenarios extracted from tests/monitoring/test_meta_path.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "mutations":
    import sys
    import metapathology
    from metapathology import MetaPathChange

    class DummyFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    monitor = metapathology.install(report_at_exit=False)
    finder = DummyFinder()
    sys.meta_path.insert(0, finder)
    sys.meta_path.remove(finder)
    sys.meta_path.append(finder)
    popped = sys.meta_path.pop()
    assert popped is finder
    sys.meta_path[0:0] = [finder]
    del sys.meta_path[0]

    mutations = [e for e in monitor.events() if isinstance(e, MetaPathChange)]
    ops = [m.op for m in mutations]
    assert ops == ["insert", "remove", "append", "pop", "__setitem__", "__delitem__"], ops
    assert mutations[0].added == ("DummyFinder",)
    assert mutations[1].removed == ("DummyFinder",)
    assert any(frame.filename == "<string>" for frame in mutations[0].stack), list(mutations[0].stack)

elif _scenario == "reassignment":
    import sys
    import metapathology
    from metapathology import MetaPathReplacement

    monitor = metapathology.install(report_at_exit=False)
    instrumented_type = type(sys.meta_path)
    sys.meta_path = list(sys.meta_path)  # blow away the instrumentation
    assert type(sys.meta_path) is list

    import colorsys  # any not-yet-imported module: triggers the import audit event

    reassignments = [e for e in monitor.events() if isinstance(e, MetaPathReplacement)]
    assert len(reassignments) == 1, reassignments
    assert reassignments[0].during_import == "colorsys"
    assert type(sys.meta_path) is instrumented_type  # reinstalled

elif _scenario == "ordinary_list_operations":
    import copy
    import sys

    import metapathology
    from metapathology import MetaPathChange

    monitor = metapathology.install(report_at_exit=False)

    sys.meta_path *= 2
    sys.meta_path.sort(key=id)
    copied = copy.deepcopy(sys.meta_path)

    ops = [event.op for event in monitor.events() if isinstance(event, MetaPathChange)]
    assert {"__imul__", "sort"} <= set(ops), ops
    assert type(copied) is list
    assert len(copied) == len(sys.meta_path)
    assert copied is not sys.meta_path

elif _scenario == "uninstall_preserves_replacements":
    import sys

    import metapathology

    original_meta_path = sys.meta_path
    original_path_hooks = sys.path_hooks
    original_sys_path = sys.path
    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(path_hooks=True, sys_path=True),
    )
    replacement_meta_path = []
    replacement_path_hooks = []
    replacement_sys_path = []
    sys.meta_path = replacement_meta_path
    sys.path_hooks = replacement_path_hooks
    sys.path = replacement_sys_path

    metapathology.uninstall()

    assert sys.meta_path is replacement_meta_path
    assert sys.path_hooks is replacement_path_hooks
    assert sys.path is replacement_sys_path
    sys.meta_path = original_meta_path
    sys.path_hooks = original_path_hooks
    sys.path = original_sys_path

elif _scenario == "extend_partial_iteration":
    import sys

    import metapathology
    from metapathology import MetaPathChange

    class DummyFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    def failing_iterator(item):
        yield item
        raise RuntimeError("boom")

    monitor = metapathology.install(report_at_exit=False)

    first = DummyFinder()
    before = list(sys.meta_path)
    try:
        sys.meta_path.extend(failing_iterator(first))
    except RuntimeError:
        pass
    assert list(sys.meta_path) == before + [first], (list(sys.meta_path), before)

    second = DummyFinder()
    before = list(sys.meta_path)
    try:
        sys.meta_path += failing_iterator(second)
    except RuntimeError:
        pass
    assert list(sys.meta_path) == before + [second], (list(sys.meta_path), before)

    added = [event.added for event in monitor.events() if isinstance(event, MetaPathChange)]
    assert ("DummyFinder",) in added, added  # partial additions must still be recorded

elif _scenario == "reassigned_list_goes_stale":
    import sys

    import metapathology
    from metapathology import MetaPathReplacement

    class DummyFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    monitor = metapathology.install(report_at_exit=False)
    mine = list(sys.meta_path)
    sys.meta_path = mine

    import colorsys  # first uncached import: detection swaps in an instrumented copy

    assert sys.meta_path is not mine
    assert [e for e in monitor.events() if isinstance(e, MetaPathReplacement)]
    mine.append(DummyFinder())  # mutates the stale list, not the live sys.meta_path
    assert not any(isinstance(f, DummyFinder) for f in sys.meta_path)

elif _scenario == "stateful_index":
    import sys

    import metapathology

    class DummyFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    class StatefulIndex:
        def __init__(self):
            self.calls = 0

        def __index__(self):
            result = self.calls
            self.calls += 1
            return result

    metapathology.install(report_at_exit=False)

    replacement = DummyFinder()
    set_index = StatefulIndex()
    sys.meta_path[set_index] = replacement
    assert set_index.calls == 1, set_index.calls
    assert sys.meta_path[0] is replacement

    first = sys.meta_path[0]
    delete_index = StatefulIndex()
    del sys.meta_path[delete_index]
    assert delete_index.calls == 1, delete_index.calls
    assert first not in sys.meta_path

    slice_replacement = DummyFinder()
    set_slice_index = StatefulIndex()
    sys.meta_path[slice(set_slice_index, 1)] = [slice_replacement]
    assert set_slice_index.calls == 1, set_slice_index.calls
    assert sys.meta_path[0] is slice_replacement

    delete_slice_index = StatefulIndex()
    del sys.meta_path[slice(delete_slice_index, 1)]
    assert delete_slice_index.calls == 1, delete_slice_index.calls
    assert slice_replacement not in sys.meta_path

elif _scenario == "failed_sort_mutation":
    import sys

    import metapathology
    from metapathology import MetaPathChange

    class ComparableFinder:
        comparisons = 0

        def __init__(self, value):
            self.value = value

        def __lt__(self, other):
            type(self).comparisons += 1
            if type(self).comparisons == 4:
                raise RuntimeError("comparison failed")
            return self.value < other.value

        def find_spec(self, fullname, path=None, target=None):
            return None

    monitor = metapathology.install(report_at_exit=False)
    finders = [ComparableFinder(value) for value in [5, 1, 4, 2, 3]]
    sys.meta_path[:] = finders
    event_count = len(monitor.events())

    try:
        sys.meta_path.sort()
    except RuntimeError:
        pass
    else:
        raise AssertionError("sort unexpectedly completed")

    assert [finder.value for finder in sys.meta_path] != [5, 1, 4, 2, 3]
    new_events = monitor.events()[event_count:]
    assert any(isinstance(event, MetaPathChange) and event.op == "sort" for event in new_events), new_events
else:
    raise ValueError(f"unknown scenario: {_scenario}")
