"""Small NumPy-only module selected as the dotted coverage source."""

import numpy as np


def normalize(values: list[float]) -> list[float]:
    """Return values divided by their sum."""
    array = np.asarray(values, dtype=float)
    return list(array / array.sum())
