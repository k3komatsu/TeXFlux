"""Versioned external canonical AST, independent of TeX rendering."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os

from . import __version__
from .ast import (
    Argument, ArgumentLayout, Block, BraceGroup, Document, GenericInvocation,
    GroupKind, Node, RawTex, SourceSpan,
)
from .render import LoadedSource


@dataclass(frozen=True, slots=True)
class AstCompilationResult:
    """A canonical document and every source loaded, in session load order."""

    document: Document
    sources: tuple[LoadedSource, ...]


def serialize_ast(result: AstCompilationResult, *, pretty: bool = False) -> str:
    """Serialize schema v1 as deterministic JSON with exactly one final LF.

    Source hashes use the original bytes; spans use diagnostics' file spelling.
    Text fragments are flattened to their already-resolved opaque string values.
    No renderer, TeX interpretation, or syntax re-scanning is involved.
    """

    if not result.sources:
        raise ValueError("an external AST needs at least the root source")
    ids = {source.file: index for index, source in enumerate(result.sources)}
    if len(ids) != len(result.sources):
        raise ValueError("external AST sources must have unique file names")

    def span(value: SourceSpan) -> dict:
        if value.file not in ids:
            raise ValueError(f"source span names a file that was not loaded: {value.file}")
        return {
            "source": ids[value.file],
            "start": {"line": value.start.line, "column": value.start.column},
            "end": {"line": value.end.line, "column": value.end.column},
        }

    def block(value: Block) -> dict:
        return {"type": "block", "nodes": [node(child) for child in value.nodes],
                "span": span(value.span)}

    def argument(value: Argument) -> dict:
        if value.kind not in (GroupKind.REQUIRED, GroupKind.OPTIONAL, GroupKind.OVERLAY):
            raise ValueError(f"unsupported canonical argument kind: {value.kind}")
        if value.layout == ArgumentLayout.INLINE and isinstance(value.value, str):
            content = {"type": "text", "text": value.value}
        elif value.layout in (
            ArgumentLayout.BLOCK, ArgumentLayout.HUGGED, ArgumentLayout.EXPLICIT,
        ) and isinstance(value.value, Block):
            content = block(value.value)
        else:
            raise ValueError("canonical argument layout does not match its value")
        return {"kind": value.kind, "layout": value.layout, "value": content,
                "span": span(value.span)}

    def node(value: Node) -> dict:
        match value:
            case RawTex():
                return {"type": "raw", "text": value.text, "span": span(value.span)}
            case GenericInvocation():
                encoded = {
                    "type": "invocation",
                    "form": "command" if value.body is None else "container",
                    "name": value.name,
                    "arguments": [argument(arg) for arg in value.arguments],
                }
                if value.body is not None:
                    encoded["body"] = block(value.body)
                encoded["span"] = span(value.span)
                return encoded
            case BraceGroup():
                return {"type": "group", "header": value.header_raw,
                        "body": block(value.body), "span": span(value.span)}
            case _:
                raise TypeError(f"unsupported canonical AST node: {type(value).__name__}")

    payload = {
        "format": "texflux-ast",
        "version": 1,
        "producer": {"name": "texflux", "version": __version__},
        "root": 0,
        "sources": [
            {"id": index, "file": source.file.replace(os.sep, "/"),
             "sha256": hashlib.sha256(source.data).hexdigest()}
            for index, source in enumerate(result.sources)
        ],
        "document": {"type": "document", "body": block(result.document.body),
                     "span": span(result.document.span)},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":")) + "\n"


__all__ = ["AstCompilationResult", "serialize_ast"]
