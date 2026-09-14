"""The shape shared by TeXFlux's JSON exports.

The external AST and the diagnostics report open with the same header --
format, version, producer, the root's id and a table of every source read --
and spell positions and spans the same way, so both are written here once.
Source hashes use the bytes the compilation read, which for an editor's
unsaved buffer is that buffer rather than the file.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import hashlib
import json
import os
from typing import TypeAlias

from . import __version__
from .ast import SourcePosition, SourceSpan
from .render import LoadedSource


#: Encodes one span against a source table, refusing a file it lacks.
SpanEncoder: TypeAlias = Callable[[SourceSpan], dict]


def encode_position(position: SourcePosition) -> dict[str, int]:
    return {"line": position.line, "column": position.column}


def span_encoder(sources: Sequence[LoadedSource]) -> SpanEncoder:
    """Number ``sources`` in load order and encode spans against those ids."""

    ids = {source.file: index for index, source in enumerate(sources)}
    if len(ids) != len(sources):
        raise ValueError("sources must have unique file names")

    def encode(span: SourceSpan) -> dict:
        if span.file not in ids:
            raise ValueError(f"span names a file that was not loaded: {span.file}")
        return {
            "source": ids[span.file],
            "start": encode_position(span.start),
            "end": encode_position(span.end),
        }

    return encode


def header(document_format: str, sources: Sequence[LoadedSource]) -> dict:
    """The fields every export starts with; the root is always source 0."""

    if not sources:
        raise ValueError(
            f"a {document_format} document needs at least the root source"
        )
    return {
        "format": document_format,
        "version": 1,
        "producer": {"name": "texflux", "version": __version__},
        "root": 0,
        "sources": [
            {
                "id": index,
                "file": source.file.replace(os.sep, "/"),
                "sha256": hashlib.sha256(source.data).hexdigest(),
            }
            for index, source in enumerate(sources)
        ],
    }


def dump_json(payload: dict, *, pretty: bool = False) -> str:
    """Deterministic UTF-8 JSON with exactly one final LF; ``pretty`` only indents."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ) + "\n"


__all__ = ["SpanEncoder", "dump_json", "encode_position", "header", "span_encoder"]
