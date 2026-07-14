from importlib.metadata import version

import metapathology

_EXPECTED_PUBLIC_API = frozenset(
    {
        "FindSpecCall",
        "InternalError",
        "MetaPathMutation",
        "MetaPathReassignment",
        "Monitor",
        "MonitorEvent",
        "__version__",
        "get_monitor",
        "install",
        "render_report",
        "report",
        "uninstall",
    }
)


def test_distribution_metadata_is_available() -> None:
    assert version("metapathology") == metapathology.__version__


def test_public_api_surface() -> None:
    assert frozenset(metapathology.__all__) == _EXPECTED_PUBLIC_API
    assert len(metapathology.__all__) == len(_EXPECTED_PUBLIC_API)
    assert all(hasattr(metapathology, name) for name in _EXPECTED_PUBLIC_API)
