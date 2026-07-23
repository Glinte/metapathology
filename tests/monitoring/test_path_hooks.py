"""Subprocess coverage for ``sys.path_hooks`` observation."""

from support import PythonRunner


def test_install_toggle_and_uninstall_preserve_live_path_hooks(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "original = sys.path_hooks\n"
        "monitor = metapathology.install(report_at_exit=False, monitor_path_hooks=False)\n"
        "assert sys.path_hooks is original\n"
        "assert not monitor.path_hooks_enabled\n"
        "metapathology.install(report_at_exit=False, monitor_path_hooks=True)\n"
        "assert monitor.path_hooks_enabled\n"
        "assert type(sys.path_hooks) is not list and isinstance(sys.path_hooks, list)\n"
        "hook = lambda path: None\n"
        "sys.path_hooks.insert(0, hook)\n"
        "metapathology.install(report_at_exit=False, monitor_path_hooks=False)\n"
        "assert monitor.path_hooks_enabled\n"
        "metapathology.uninstall()\n"
        "assert type(sys.path_hooks) is list\n"
        "assert sys.path_hooks[0] is hook\n"
        "print('OK')\n"
    )
    assert "OK" in proc.stdout


def test_path_hook_mutators_record_plain_identity_data_without_calling_hooks(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "from metapathology import PathHooksMutation\n"
        "calls = []\n"
        "def first(path):\n"
        "    calls.append(path)\n"
        "def second(path):\n"
        "    calls.append(path)\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "sys.path_hooks.append(first)\n"
        "sys.path_hooks.insert(0, second)\n"
        "sys.path_hooks.remove(first)\n"
        "sys.path_hooks.extend([first])\n"
        "assert sys.path_hooks.pop() is first\n"
        "sys.path_hooks[0:1] = [first, second]\n"
        "del sys.path_hooks[0]\n"
        "sys.path_hooks.reverse()\n"
        "sys.path_hooks.sort(key=id)\n"
        "sys.path_hooks += [first]\n"
        "sys.path_hooks *= 1\n"
        "saved = list(sys.path_hooks)\n"
        "sys.path_hooks.clear()\n"
        "sys.path_hooks.extend(saved)\n"
        "events = [event for event in monitor.events() if isinstance(event, PathHooksMutation)]\n"
        "assert [event.op for event in events] == [\n"
        "    'append', 'insert', 'remove', 'extend', 'pop', '__setitem__',\n"
        "    '__delitem__', 'reverse', 'sort', '__iadd__', '__imul__', 'clear', 'extend'\n"
        "]\n"
        "assert events[0].added[0].object_id == id(first)\n"
        "assert events[0].added[0].type_name == 'function'\n"
        "assert events[0].added[0].name == 'first'\n"
        "assert any(frame.filename == '<string>' for frame in events[0].stack)\n"
        "assert not calls\n"
        "print('OK')\n"
    )
    assert "OK" in proc.stdout


def test_path_hooks_reassignment_is_recovered_on_next_import(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "from metapathology import PathHooksReassignment\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "mine = list(sys.path_hooks)\n"
        "sys.path_hooks = mine\n"
        "import colorsys\n"
        "assert sys.path_hooks is not mine\n"
        "assert type(sys.path_hooks) is not list\n"
        "events = [event for event in monitor.events() if isinstance(event, PathHooksReassignment)]\n"
        "assert len(events) == 1 and events[0].during_import == 'colorsys'\n"
        "mine.append(lambda path: None)\n"
        "assert len(sys.path_hooks) + 1 == len(mine)\n"
        "print('OK')\n"
    )
    assert "OK" in proc.stdout


def test_simultaneous_import_list_reassignments_use_meta_then_path_hook_sequence(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "from metapathology import MetaPathReassignment, PathHooksReassignment\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "sys.meta_path = list(sys.meta_path)\n"
        "sys.path_hooks = list(sys.path_hooks)\n"
        "import colorsys\n"
        "events = [event for event in monitor.events() if isinstance(event, (MetaPathReassignment, PathHooksReassignment))]\n"
        "assert len(events) == 2, events\n"
        "assert isinstance(events[0], MetaPathReassignment)\n"
        "assert isinstance(events[1], PathHooksReassignment)\n"
        "assert events[0].seq < events[1].seq\n"
        "print('OK')\n"
    )
    assert "OK" in proc.stdout


def test_hostile_callable_metadata_is_not_inspected(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "from metapathology import PathHooksMutation\n"
        "class HostileHook:\n"
        "    def __getattribute__(self, name):\n"
        "        if name in ('__name__', '__qualname__'):\n"
        "            raise AssertionError(name)\n"
        "        return object.__getattribute__(self, name)\n"
        "    def __call__(self, path):\n"
        "        raise AssertionError('hook was called')\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "hook = HostileHook()\n"
        "sys.path_hooks.append(hook)\n"
        "event = [event for event in monitor.events() if isinstance(event, PathHooksMutation)][-1]\n"
        "assert event.added[0].type_name == 'HostileHook'\n"
        "assert event.added[0].name is None\n"
        "print('OK')\n"
    )
    assert "OK" in proc.stdout


def test_path_hook_callback_is_inert_during_reentrant_finder_instrumentation(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "from metapathology import PathHooksMutation\n"
        "hook = lambda path: None\n"
        "class ReentrantFinder:\n"
        "    def __getattr__(self, name):\n"
        "        if name == 'find_spec':\n"
        "            sys.path_hooks.append(hook)\n"
        "            return lambda fullname, path=None, target=None: None\n"
        "        raise AttributeError(name)\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "sys.meta_path.append(ReentrantFinder())\n"
        "assert hook in sys.path_hooks\n"
        "assert not any(\n"
        "    isinstance(event, PathHooksMutation)\n"
        "    and any(reference.object_id == id(hook) for reference in event.added)\n"
        "    for event in monitor.events()\n"
        ")\n"
        "print('OK')\n"
    )
    assert "OK" in proc.stdout


def test_concurrent_path_hook_mutation_reads_reports_and_cleanup_are_safe(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import threading\n"
        "import metapathology\n"
        "from metapathology import PathHooksMutation\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "instrumented = sys.path_hooks\n"
        "barrier = threading.Barrier(5)\n"
        "failures = []\n"
        "def mutate():\n"
        "    try:\n"
        "        barrier.wait()\n"
        "        for _ in range(100):\n"
        "            hook = lambda path: None\n"
        "            instrumented.append(hook)\n"
        "            instrumented.remove(hook)\n"
        "    except BaseException as exc:\n"
        "        failures.append(exc)\n"
        "def read():\n"
        "    try:\n"
        "        barrier.wait()\n"
        "        for _ in range(100):\n"
        "            events = monitor.events()\n"
        "            assert all(a.seq < b.seq for a, b in zip(events, events[1:]))\n"
        "    except BaseException as exc:\n"
        "        failures.append(exc)\n"
        "def report():\n"
        "    try:\n"
        "        barrier.wait()\n"
        "        for _ in range(10):\n"
        "            metapathology.render_report()\n"
        "    except BaseException as exc:\n"
        "        failures.append(exc)\n"
        "threads = [threading.Thread(target=mutate), threading.Thread(target=mutate),\n"
        "           threading.Thread(target=read), threading.Thread(target=report)]\n"
        "for thread in threads: thread.start()\n"
        "barrier.wait()\n"
        "for thread in threads: thread.join()\n"
        "assert not failures, failures\n"
        "cleanup_barrier = threading.Barrier(2)\n"
        "def mutate_during_cleanup():\n"
        "    cleanup_barrier.wait()\n"
        "    for _ in range(200):\n"
        "        hook = lambda path: None\n"
        "        instrumented.append(hook)\n"
        "        instrumented.remove(hook)\n"
        "cleanup_thread = threading.Thread(target=mutate_during_cleanup)\n"
        "cleanup_thread.start()\n"
        "cleanup_barrier.wait()\n"
        "metapathology.uninstall()\n"
        "event_count = len([event for event in monitor.events() if isinstance(event, PathHooksMutation)])\n"
        "cleanup_thread.join()\n"
        "instrumented.append(lambda path: None)\n"
        "assert len([event for event in monitor.events() if isinstance(event, PathHooksMutation)]) == event_count\n"
        "assert type(sys.path_hooks) is list and sys.path_hooks is not instrumented\n"
        "print('OK')\n"
    )
    assert "OK" in proc.stdout
