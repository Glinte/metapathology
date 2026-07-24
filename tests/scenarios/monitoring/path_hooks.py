"""Isolated child scenarios extracted from tests/monitoring/test_path_hooks.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "install_toggle_and_uninstall_preserve_live_path_hooks":
    import sys
    import metapathology

    original = sys.path_hooks
    monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(path_hooks=True))
    assert monitor.path_hooks_enabled
    assert type(sys.path_hooks) is not list and isinstance(sys.path_hooks, list)
    hook = lambda path: None
    sys.path_hooks.insert(0, hook)
    try:
        metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(path_hooks=False))
    except RuntimeError:
        pass
    else:
        raise AssertionError("active capture configuration must be fixed")
    assert monitor.path_hooks_enabled
    metapathology.uninstall()
    assert type(sys.path_hooks) is list
    assert sys.path_hooks[0] is hook

elif _scenario == "path_hook_mutators_record_plain_identity_data_without_calling_hooks":
    import sys
    import metapathology
    from metapathology import PathHooksChange

    calls = []

    def first(path):
        calls.append(path)

    def second(path):
        calls.append(path)

    monitor = metapathology.install(report_at_exit=False)
    sys.path_hooks.append(first)
    sys.path_hooks.insert(0, second)
    sys.path_hooks.remove(first)
    sys.path_hooks.extend([first])
    assert sys.path_hooks.pop() is first
    sys.path_hooks[0:1] = [first, second]
    del sys.path_hooks[0]
    sys.path_hooks.reverse()
    sys.path_hooks.sort(key=id)
    sys.path_hooks += [first]
    sys.path_hooks *= 1
    saved = list(sys.path_hooks)
    sys.path_hooks.clear()
    sys.path_hooks.extend(saved)
    events = [event for event in monitor.events() if isinstance(event, PathHooksChange)]
    assert [event.op for event in events] == [
        "append",
        "insert",
        "remove",
        "extend",
        "pop",
        "__setitem__",
        "__delitem__",
        "reverse",
        "sort",
        "__iadd__",
        "__imul__",
        "clear",
        "extend",
    ]
    assert events[0].added[0].object_id == id(first)
    assert events[0].added[0].type_name == "function"
    assert events[0].added[0].name == "first"
    assert any(frame.filename == "<string>" for frame in events[0].stack)
    assert not calls

elif _scenario == "path_hooks_replacement_is_recovered_on_next_import":
    import sys
    import metapathology
    from metapathology import PathHooksReplacement

    monitor = metapathology.install(report_at_exit=False)
    mine = list(sys.path_hooks)
    sys.path_hooks = mine
    import colorsys

    assert sys.path_hooks is not mine
    assert type(sys.path_hooks) is not list
    events = [event for event in monitor.events() if isinstance(event, PathHooksReplacement)]
    assert len(events) == 1 and events[0].during_import == "colorsys"
    mine.append(lambda path: None)
    assert len(sys.path_hooks) + 1 == len(mine)

elif _scenario == "simultaneous_import_list_reassignments_use_meta_then_path_hook_sequence":
    import sys
    import metapathology
    from metapathology import MetaPathReplacement, PathHooksReplacement

    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path = list(sys.meta_path)
    sys.path_hooks = list(sys.path_hooks)
    import colorsys

    events = [event for event in monitor.events() if isinstance(event, (MetaPathReplacement, PathHooksReplacement))]
    assert len(events) == 2, events
    assert isinstance(events[0], MetaPathReplacement)
    assert isinstance(events[1], PathHooksReplacement)
    assert events[0].sequence < events[1].sequence

elif _scenario == "hostile_callable_metadata_is_not_inspected":
    import sys
    import metapathology
    from metapathology import PathHooksChange

    class HostileHook:
        def __getattribute__(self, name):
            if name in ("__name__", "__qualname__"):
                raise AssertionError(name)
            return object.__getattribute__(self, name)

        def __call__(self, path):
            raise AssertionError("hook was called")

    monitor = metapathology.install(report_at_exit=False)
    hook = HostileHook()
    sys.path_hooks.append(hook)
    event = [event for event in monitor.events() if isinstance(event, PathHooksChange)][-1]
    assert event.added[0].type_name == "HostileHook"
    assert event.added[0].name is None

elif _scenario == "path_hook_callback_is_inert_during_reentrant_finder_instrumentation":
    import sys
    import metapathology
    from metapathology import PathHooksChange

    hook = lambda path: None

    class ReentrantFinder:
        def __getattr__(self, name):
            if name == "find_spec":
                sys.path_hooks.append(hook)
                return lambda fullname, path=None, target=None: None
            raise AttributeError(name)

    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path.append(ReentrantFinder())
    assert hook in sys.path_hooks
    assert not any(
        isinstance(event, PathHooksChange) and any(reference.object_id == id(hook) for reference in event.added)
        for event in monitor.events()
    )

elif _scenario == "concurrent_path_hooks_change_reads_reports_and_cleanup_are_safe":
    import sys
    import threading
    import metapathology
    from metapathology import PathHooksChange

    monitor = metapathology.install(report_at_exit=False)
    instrumented = sys.path_hooks
    barrier = threading.Barrier(5)
    failures = []

    def mutate():
        try:
            barrier.wait()
            for _ in range(100):
                hook = lambda path: None
                instrumented.append(hook)
                instrumented.remove(hook)
        except BaseException as exc:
            failures.append(exc)

    def read():
        try:
            barrier.wait()
            for _ in range(100):
                events = monitor.events()
                assert all(a.sequence < b.sequence for a, b in zip(events, events[1:]))
        except BaseException as exc:
            failures.append(exc)

    def report():
        try:
            barrier.wait()
            for _ in range(10):
                metapathology.render_report()
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=mutate),
        threading.Thread(target=mutate),
        threading.Thread(target=read),
        threading.Thread(target=report),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert not failures, failures
    cleanup_barrier = threading.Barrier(2)

    def mutate_during_cleanup():
        cleanup_barrier.wait()
        for _ in range(200):
            hook = lambda path: None
            instrumented.append(hook)
            instrumented.remove(hook)

    cleanup_thread = threading.Thread(target=mutate_during_cleanup)
    cleanup_thread.start()
    cleanup_barrier.wait()
    metapathology.uninstall()
    event_count = len([event for event in monitor.events() if isinstance(event, PathHooksChange)])
    cleanup_thread.join()
    instrumented.append(lambda path: None)
    assert len([event for event in monitor.events() if isinstance(event, PathHooksChange)]) == event_count
    assert type(sys.path_hooks) is list and sys.path_hooks is not instrumented
else:
    raise ValueError(f"unknown scenario: {_scenario}")
