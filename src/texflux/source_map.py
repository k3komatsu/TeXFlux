"""Deterministic ``.tfxmap`` serialization for compiled TeX."""

from __future__ import annotations

import hashlib
import json
import os

from . import __version__
from .ast import SourcePosition
from .paths import PathLike, normalized_path
from .render import CompilationResult, RenderedFragment


def _position(position: SourcePosition) -> dict[str, int]:
    return {"line": position.line, "column": position.column}


def _stored_path(path: PathLike, map_path: PathLike) -> str:
    target = os.path.abspath(os.fspath(path))
    map_directory = os.path.dirname(os.path.abspath(os.fspath(map_path)))
    try:
        stored = os.path.relpath(target, map_directory)
    except ValueError:
        stored = os.path.normpath(target)
    return stored.replace(os.sep, "/")


def _validate_generated_fragments(fragments: tuple[RenderedFragment, ...]) -> None:
    previous_end: SourcePosition | None = None
    for fragment in fragments:
        start = fragment.generated.start
        end = fragment.generated.end
        if not start < end:
            raise ValueError("generated fragments must have non-empty ranges")
        if previous_end is not None and start < previous_end:
            raise ValueError("generated fragments must be sorted and non-overlapping")
        previous_end = end


def serialize_source_map(
    result: CompilationResult,
    *,
    source_path: PathLike,
    generated_path: PathLike,
    map_path: PathLike,
    source_bytes: bytes,
    generated_bytes: bytes | None = None,
) -> str:
    """Serialize one result as deterministic UTF-8 JSON.

    ``source_path`` must identify the file named by every source span in the
    result. When supplied, ``generated_bytes`` must be the UTF-8 bytes of
    ``result.text``.
    """

    if generated_bytes is None:
        generated_bytes = result.text.encode("utf-8")
    elif generated_bytes != result.text.encode("utf-8"):
        raise ValueError("generated_bytes must be the UTF-8 bytes of result.text")
    _validate_generated_fragments(result.rendered.fragments)

    source_files = {
        fragment.source.file
        for fragment in result.rendered.fragments
        if fragment.source is not None
    }
    if any(
        normalized_path(source_file) != normalized_path(source_path)
        for source_file in source_files
    ):
        raise ValueError("source span file does not match source_path")

    mappings = []
    for fragment in result.rendered.fragments:
        if fragment.source is None:
            continue
        mappings.append(
            {
                "generated": {
                    "start": _position(fragment.generated.start),
                    "end": _position(fragment.generated.end),
                },
                "source": {
                    "id": 0,
                    "start": _position(fragment.source.start),
                    "end": _position(fragment.source.end),
                },
                "role": fragment.role,
            }
        )

    payload = {
        "format": "texflux-source-map",
        "version": 1,
        "producer": {"name": "texflux", "version": __version__},
        "generated": {
            "path": _stored_path(generated_path, map_path),
            "sha256": hashlib.sha256(generated_bytes).hexdigest(),
        },
        "sources": [
            {
                "id": 0,
                "path": _stored_path(source_path, map_path),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            }
        ],
        "mappings": mappings,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


__all__ = ["serialize_source_map"]
