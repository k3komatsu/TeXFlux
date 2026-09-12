"""Path identity helpers shared by the CLI, source maps, and remapping."""

from __future__ import annotations

import os
from typing import TypeAlias


PathLike: TypeAlias = str | os.PathLike[str]


def normalized_path(path: PathLike) -> str:
    """Return one comparable spelling of ``path``.

    Symlinks, relative segments, and case-insensitive file systems are all
    resolved, so two spellings of the same file compare equal.
    """

    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def same_path(first: PathLike, second: PathLike) -> bool:
    """Check whether two paths name the same file."""

    try:
        if os.path.samefile(first, second):
            return True
    except OSError:
        # At least one path does not exist yet, so compare their spellings.
        pass
    return normalized_path(first) == normalized_path(second)


__all__ = ["PathLike", "normalized_path", "same_path"]
