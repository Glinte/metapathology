"""Minimal stand-in for the intended Bazel runfiles package."""


class runfiles:
    """Match the name imported by rules_python's dependency resolver."""

    @staticmethod
    def Create() -> object:
        """Return a recognizable placeholder runfiles object."""
        return object()
