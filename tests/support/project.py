"""Temporary source-tree helpers for subprocess tests."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class TempProject:
    """Own files created beneath one pytest temporary directory."""

    root: Path

    def write(self, relative_path: str | PurePosixPath, source: str) -> Path:
        """Write one UTF-8 file beneath the project root."""
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("temporary project paths must stay beneath the project root")
        destination = self.root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source, encoding="utf-8")
        return destination

    def module(self, fullname: str, source: str, *, package: bool = False) -> Path:
        """Write a module or package from its dotted import name."""
        parts = fullname.split(".")
        if not parts or any(not part.isidentifier() for part in parts):
            raise ValueError(f"invalid module name: {fullname!r}")
        relative = PurePosixPath(*parts)
        if package:
            relative /= "__init__.py"
        else:
            relative = relative.with_suffix(".py")
        return self.write(relative, source)
