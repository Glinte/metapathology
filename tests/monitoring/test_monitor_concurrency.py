"""Monitor concurrency, locking, snapshot, and shutdown behavior."""

from pathlib import Path

from support import PythonRunner


def test_concurrent_mutation_event_reads_and_reports_are_safe(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/monitor_concurrency.py", "concurrent_activity")


def test_concurrent_uninstall_is_idempotent(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/monitor_concurrency.py", "concurrent_uninstall")


def test_install_does_not_hold_lifecycle_lock_across_finder_access(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "blocked_dependency.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "audit_check.py").write_text("VALUE = 1\n", encoding="utf-8")
    python_runner.run_scenario_ok(
        "monitoring/monitor_concurrency.py", "cross_thread_lifecycle_lock_order", str(tmp_path)
    )


# install() shadows find_spec with setattr(), which lands in the slot, but
# uninstall() restores through finder.__dict__, which a __slots__ instance
# does not have; the suppressed AttributeError must not strand the wrapper.
def test_install_can_enable_report_at_exit_after_first_install(python_runner: PythonRunner) -> None:
    proc = python_runner.run_scenario_ok("monitoring/monitor_concurrency.py", "report_at_exit_upgrade")
    assert "== metapathology report ==" in proc.stderr


# list.extend() through an iterator that raises mid-iteration keeps the
# already-yielded prefix; the instrumented list materializes the iterable
# first and silently appends nothing, diverging from plain-list semantics.
def test_uninstall_concurrent_with_mutation_leaves_no_wrapper(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/monitor_concurrency.py", "uninstall_mutation_race")


def test_monitor_snapshot_is_one_named_immutable_cutoff(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/monitor_concurrency.py", "monitor_snapshot")


# Characterization, not a bug: recovery from a reassignment can only
# copy-and-swap, because a plain list cannot be instrumented in place. The
# reassigner's own list object therefore goes stale once detection replaces it.
