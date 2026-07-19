"""Sibling whose eager import expands the package dependency graph."""

import pandas as pd


def make_table() -> pd.DataFrame:
    """Return a trivial table."""
    return pd.DataFrame({"value": [1]})
