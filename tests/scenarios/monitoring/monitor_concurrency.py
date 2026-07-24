"""Isolated child scenarios extracted from tests/monitoring/test_monitor_concurrency.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "concurrent_activity":
    import sys
    import threading

    import metapathology
    from metapathology import ImportSearchStarted

    class DummyFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    module_names = [f"concurrent_module_{index}" for index in range(40)]
    for index, module_name in enumerate(module_names):
        with open(f"{module_name}.py", "w", encoding="utf-8") as module_file:
            module_file.write(f"value = {index}\n")

    monitor = metapathology.install(report_at_exit=False)
    failures = []
    barrier = threading.Barrier(7)

    def mutate():
        try:
            barrier.wait()
            for _ in range(100):
                finder = DummyFinder()
                sys.meta_path.append(finder)
                sys.meta_path.remove(finder)
        except BaseException as exc:
            failures.append(exc)

    def read_events():
        try:
            barrier.wait()
            for _ in range(200):
                events = monitor.events()
                assert all(left.sequence < right.sequence for left, right in zip(events, events[1:]))
        except BaseException as exc:
            failures.append(exc)

    def render():
        try:
            barrier.wait()
            for _ in range(20):
                assert "metapathology" in metapathology.render_report()
        except BaseException as exc:
            failures.append(exc)

    def import_modules():
        try:
            barrier.wait()
            for index, module_name in enumerate(module_names):
                module = __import__(module_name)
                assert module.value == index
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=mutate) for _ in range(3)]
    threads.extend(
        [threading.Thread(target=read_events), threading.Thread(target=render), threading.Thread(target=import_modules)]
    )
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert not failures, failures
    assert all(module_name in sys.modules for module_name in module_names)
    starts = {event.fullname for event in monitor.events() if isinstance(event, ImportSearchStarted)}
    assert starts >= set(module_names), (starts, module_names)
    assert monitor.enabled
    metapathology.uninstall()
    assert type(sys.meta_path) is list

elif _scenario == "concurrent_uninstall":
    import sys
    import threading

    import metapathology

    monitor = metapathology.install(report_at_exit=False)
    barrier = threading.Barrier(9)
    failures = []

    def uninstall():
        try:
            barrier.wait()
            metapathology.uninstall()
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=uninstall) for _ in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert not failures, failures
    assert not monitor.enabled
    assert type(sys.meta_path) is list

elif _scenario == "cross_thread_lifecycle_lock_order":
    import importlib._bootstrap as bootstrap
    import sys
    import threading

    import metapathology

    sys.path.insert(0, sys.argv[1])
    metapathology.install(report_at_exit=False)
    metapathology.uninstall()

    finder_started = threading.Event()
    module_lock_held = threading.Event()
    failures = []

    class LazyFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

        def __getattribute__(self, name):
            if name == "find_spec" and threading.current_thread().name == "installer":
                finder_started.set()
                import blocked_dependency
            return object.__getattribute__(self, name)

    sys.meta_path.insert(0, LazyFinder())

    def import_while_holding_module_lock():
        lock = bootstrap._get_module_lock("blocked_dependency")
        lock.acquire()
        module_lock_held.set()
        try:
            assert finder_started.wait(5)
            import audit_check
        except BaseException as exc:
            failures.append(exc)
        finally:
            lock.release()

    holder = threading.Thread(target=import_while_holding_module_lock, name="module-lock-holder", daemon=True)
    holder.start()
    assert module_lock_held.wait(5)
    installer = threading.Thread(
        target=metapathology.install,
        kwargs={"report_at_exit": False},
        name="installer",
        daemon=True,
    )
    installer.start()
    installer.join(5)
    holder.join(5)
    assert not installer.is_alive(), "installer waited for a module lock while holding the lifecycle lock"
    assert not holder.is_alive(), "audit recovery waited for the lifecycle lock while holding a module lock"
    assert not failures, failures

elif _scenario == "report_at_exit_upgrade":
    import metapathology

    metapathology.install(report_at_exit=False)
    metapathology.install(report_at_exit=True)

elif _scenario == "uninstall_mutation_race":
    import sys
    import threading

    import metapathology

    def original_find_spec(fullname, path=None, target=None):
        return None

    in_lookup = threading.Event()
    uninstall_done = threading.Event()

    class SlowAttributeFinder:
        def __getattr__(self, name):
            if name == "find_spec":
                in_lookup.set()
                assert uninstall_done.wait(timeout=10)
                return original_find_spec
            raise AttributeError(name)

    metapathology.install(report_at_exit=False)
    finder = SlowAttributeFinder()

    appender = threading.Thread(target=sys.meta_path.append, args=(finder,))
    appender.start()
    assert in_lookup.wait(timeout=10)
    metapathology.uninstall()
    uninstall_done.set()
    appender.join(timeout=10)

    assert finder.find_spec is original_find_spec, finder.__dict__.get("find_spec")

elif _scenario == "monitor_snapshot":
    import metapathology
    from metapathology._monitor_model import MonitorSnapshot

    monitor = metapathology.install(report_at_exit=False)
    import colorsys

    snapshot = monitor._snapshot()
    assert isinstance(snapshot, MonitorSnapshot)
    assert snapshot.enabled
    assert snapshot.events
    assert max(event.sequence for event in snapshot.events) <= snapshot.cutoff_seq
    assert snapshot.baseline_modules == monitor.baseline_modules
    try:
        snapshot.cutoff_seq = 0
    except AttributeError as exc:
        assert "read-only" in str(exc)
    else:
        raise AssertionError("monitor snapshots must be immutable")
    metapathology.uninstall()
else:
    raise ValueError(f"unknown scenario: {_scenario}")
