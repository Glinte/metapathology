"""Exercise the selected module after coverage source discovery."""

from eager_source.normalization import normalize


def test_normalize() -> None:
    """Normalization should preserve a unit sum."""
    assert sum(normalize([1.0, 3.0])) == 1.0
