"""Versioned external canonical AST, independent of TeX rendering."""

from __future__ import annotations

from dataclasses import dataclass

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    Document,
    GenericInvocation,
    GroupKind,
    Node,
    RawTex,
)
from .interchange import dump_json, header, span_encoder
from .render import LoadedSource


#: The argument kinds a canonical node may carry: a binding list configures
#: an import and is resolved away before the AST is canonical.
_EXPORTED_KINDS = (GroupKind.REQUIRED, GroupKind.OPTIONAL, GroupKind.OVERLAY)


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

    payload = header("texflux-ast", result.sources)
    span = span_encoder(result.sources)

    def block(value: Block) -> dict:
        return {
            "type": "block",
            "nodes": [node(child) for child in value.nodes],
            "span": span(value.span),
        }

    def argument(value: Argument) -> dict:
        if value.kind not in _EXPORTED_KINDS:
            raise ValueError(f"unsupported canonical argument kind: {value.kind}")
        if value.layout == ArgumentLayout.INLINE and isinstance(value.value, str):
            content = {"type": "text", "text": value.value}
        elif value.layout != ArgumentLayout.INLINE and isinstance(value.value, Block):
            content = block(value.value)
        else:
            raise ValueError("canonical argument layout does not match its value")
        return {
            "kind": value.kind,
            "layout": value.layout,
            "value": content,
            "span": span(value.span),
        }

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
                return {
                    "type": "group",
                    "header": value.header_raw,
                    "body": block(value.body),
                    "span": span(value.span),
                }
            case _:
                raise TypeError(
                    f"unsupported canonical AST node: {type(value).__name__}"
                )

    payload["document"] = {
        "type": "document",
        "body": block(result.document.body),
        "span": span(result.document.span),
    }
    return dump_json(payload, pretty=pretty)


__all__ = ["AstCompilationResult", "serialize_ast"]
