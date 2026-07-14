from importlib.metadata import version

import metapathology


def test_distribution_metadata_is_available() -> None:
    assert version("metapathology") == metapathology.__version__


def test_public_api_surface() -> None:
    for name in metapathology.__all__:
        assert hasattr(metapathology, name), name
