"""Deterministic ``.tfxmap`` serialization for compiled TeX."""

from __future__ import annotations

import hashlib
import os

from . import __version__
from .ast import SourcePosition
from .interchange import dump_json, encode_position
from .paths import PathLike
from .render import CompilationResult, RenderedFragment


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


def _source_ids(result: CompilationResult) -> dict[str, int]:
    """Number the source files this result's fragments actually name.

    The root is always id 0, so a document with no imports keeps writing the
    map it always did. Every other file follows in order of first appearance,
    which the document's own order decides. A module that contributed no
    fragment -- a ``.tfxm``, or a ``.tfx`` whose content was all dropped --
    is left out, because every listed source becomes a SyncTeX input. That
    is why the map cannot share ``interchange.header``, whose table lists
    every loaded source in load order.
    """

    if not result.sources:
        raise ValueError("a source map needs at least the root source")
    order = [result.sources[0].file]
    known = {source.file for source in result.sources}
    for fragment in result.rendered.fragments:
        if fragment.source is None:
            continue
        name = fragment.source.file
        if name not in known:
            raise ValueError(f"span names a file that was not loaded: {name}")
        if name not in order:
            order.append(name)
    return {name: index for index, name in enumerate(order)}


def serialize_source_map(
    result: CompilationResult,
    *,
    generated_path: PathLike,
    map_path: PathLike,
    generated_bytes: bytes | None = None,
) -> str:
    """Serialize one result as deterministic UTF-8 JSON.

    The sources come from ``result.sources``, so one map can describe a
    document assembled from several ``.tfx`` files. When supplied,
    ``generated_bytes`` must be the UTF-8 bytes of ``result.text``.
    """

    encoded = result.text.encode("utf-8")
    if generated_bytes is None:
        generated_bytes = encoded
    elif generated_bytes != encoded:
        raise ValueError("generated_bytes must be the UTF-8 bytes of result.text")
    _validate_generated_fragments(result.rendered.fragments)

    ids = _source_ids(result)
    by_file = {source.file: source for source in result.sources}

    mappings = []
    for fragment in result.rendered.fragments:
        if fragment.source is None:
            continue
        mappings.append(
            {
                "generated": {
                    "start": encode_position(fragment.generated.start),
                    "end": encode_position(fragment.generated.end),
                },
                "source": {
                    "id": ids[fragment.source.file],
                    "start": encode_position(fragment.source.start),
                    "end": encode_position(fragment.source.end),
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
                "id": identifier,
                "path": _stored_path(by_file[name].path, map_path),
                "sha256": hashlib.sha256(by_file[name].data).hexdigest(),
            }
            for name, identifier in ids.items()
        ],
        "mappings": mappings,
    }
    return dump_json(payload)


__all__ = ["serialize_source_map"]
