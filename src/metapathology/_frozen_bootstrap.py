"""Minimal runtime activation shared by frozen-application integrations."""

import os

import metapathology

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    _FrozenIntegration = Literal["cx-freeze", "embedded", "nuitka", "pyinstaller"]


def activate_frozen(integration: "_FrozenIntegration", bootstrap_path: str) -> None:
    """Install monitoring at a named frozen-application startup boundary.

    Args:
        integration: Supported freezer or embedding integration name.
        bootstrap_path: Generated activation file being executed.

    Raises:
        ValueError: If ``integration`` is not a supported generated target.
    """
    if integration not in ("cx-freeze", "embedded", "nuitka", "pyinstaller"):
        raise ValueError(f"unsupported frozen integration: {integration!r}")
    monitor = metapathology.install()
    monitor._set_frozen_bootstrap(integration, os.path.abspath(bootstrap_path), "after freezer initialization")
