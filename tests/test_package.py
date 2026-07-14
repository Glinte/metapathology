from importlib.metadata import version

import metapathology


def test_package_is_importable() -> None:
    assert metapathology.hello() == "Hello from metapathology!"


def test_distribution_metadata_is_available() -> None:
    assert version("metapathology") == "0.1.0.dev0"
