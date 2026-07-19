"""Eager package initializer matching mosquito-cfd's relevant import shape."""

from eager_source.normalization import normalize
from eager_source.table import make_table

__all__ = ["make_table", "normalize"]
