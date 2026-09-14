"""Path identity helpers shared by the CLI, source maps, and remapping."""

from __future__ import annotations

import os
from typing import TypeAlias


PathLike: TypeAlias = str | os.PathLike[str]


def normalized_path(path: PathLike) -> str:
    """Return one comparable spelling of ``path``.

    Symlinks and relative segments are resolved, so two spellings of the same
    file compare equal. Case is folded only where ``normcase`` folds it, which
    is Windows: on a case-insensitive macOS volume two spellings that differ
    in case still compare as two files.
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
