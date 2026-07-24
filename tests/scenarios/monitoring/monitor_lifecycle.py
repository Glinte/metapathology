"""Isolated child scenarios extracted from tests/monitoring/test_monitor_lifecycle.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "install_restore":
    import sys
    import metapathology

    before = list(sys.meta_path)
    monitor = metapathology.install(report_at_exit=False)
    assert not hasattr(monitor, "install")
    assert not hasattr(monitor, "uninstall")
    assert not hasattr(monitor, "_report_format")
    assert not hasattr(monitor, "_report_destination")
    assert type(sys.meta_path) is not list
    assert isinstance(sys.meta_path, list)  # real list subclass: isinstance scans keep working
    assert list(sys.meta_path) == before

    again = metapathology.install(report_at_exit=False)
    assert again is monitor  # idempotent, no double wrapping
    inner = sys.meta_path

    metapathology.uninstall()
    assert type(sys.meta_path) is list
    assert list(sys.meta_path) == before
    metapathology.uninstall()  # idempotent

elif _scenario == "monitoring_region_restores_after_an_exception":
    import sys
    import metapathology

    before = list(sys.meta_path)
    try:
        with metapathology.monitoring(capture=metapathology.CaptureConfig(path_hooks=False)) as monitor:
            assert monitor.enabled
            assert metapathology.get_monitor() is monitor
            assert type(sys.meta_path) is not list
            import colorsys

            raise LookupError("target failure")
    except LookupError:
        pass
    else:
        raise AssertionError("region suppressed the target exception")
    assert not monitor.enabled
    assert type(sys.meta_path) is list and list(sys.meta_path) == before
    assert any(getattr(event, "fullname", None) == "colorsys" for event in monitor.events())

elif _scenario == "nested_monitoring_regions_share_lifecycle_with_fixed_configuration":
    import warnings
    import metapathology

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with metapathology.monitoring(capture=metapathology.CaptureConfig(path_hooks=False)) as outer:
            assert not outer.path_hooks_enabled
            with metapathology.monitoring(capture=metapathology.CaptureConfig(path_hooks=False)) as inner:
                assert inner is outer and not inner.path_hooks_enabled
            assert outer.enabled and not outer.path_hooks_enabled
    assert not caught, caught
    assert not outer.enabled

elif _scenario == "monitoring_region_preserves_preexisting_installation":
    import warnings
    import metapathology

    explicit = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(path_hooks=False))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with metapathology.monitoring(capture=metapathology.CaptureConfig(path_hooks=False)) as regional:
            assert regional is explicit and not regional.path_hooks_enabled
    assert len(caught) == 1, caught
    assert caught[0].category is RuntimeWarning
    assert "pre-existing installation will remain active" in str(caught[0].message)
    assert explicit.enabled and not explicit.path_hooks_enabled
    metapathology.uninstall()
    assert not explicit.enabled

elif _scenario == "overlapping_monitoring_regions_uninstall_after_last_exit":
    import threading
    import metapathology

    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    first_exited = threading.Event()
    release_second = threading.Event()
    failures = []

    def first():
        try:
            with metapathology.monitoring():
                first_entered.set()
                assert release_first.wait(10)
            first_exited.set()
        except BaseException as exc:
            failures.append(exc)

    def second():
        try:
            assert first_entered.wait(10)
            with metapathology.monitoring():
                second_entered.set()
                assert release_second.wait(10)
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    assert second_entered.wait(10)
    release_first.set()
    assert first_exited.wait(10)
    assert metapathology.get_monitor().enabled
    release_second.set()
    for thread in threads:
        thread.join(10)
    assert not failures, failures
    assert all(not thread.is_alive() for thread in threads)
    assert not metapathology.get_monitor().enabled

elif _scenario == "install_warns_once_when_starting_on_unsupported_implementation":
    import warnings
    import metapathology
    import metapathology._monitor as monitor_module

    monitor_module._IMPLEMENTATION_NAME = "pypy"
    monitor_module._UNSUPPORTED_IMPLEMENTATION_WARNING = (
        "metapathology supports CPython only; monitoring on 'pypy' may be incomplete or inaccurate"
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        monitor = metapathology.install(report_at_exit=False)
        assert metapathology.install(report_at_exit=False) is monitor
    metapathology.uninstall()
    assert len(caught) == 1, caught
    risk = caught[0]
    assert risk.category is RuntimeWarning, risk.category
    assert str(risk.message) == monitor_module._UNSUPPORTED_IMPLEMENTATION_WARNING
else:
    raise ValueError(f"unknown scenario: {_scenario}")
