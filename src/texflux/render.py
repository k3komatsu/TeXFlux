"""Deterministic rendering of the canonical AST with source provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    CanonicalNode,
    Document,
    GenericInvocation,
    Item,
    RawTex,
    SourcePosition,
    SourceSpan,
)


#: The provenance role of one rendered fragment.
RenderRole: TypeAlias = Literal["content", "open", "close", "synthetic"]


@dataclass(frozen=True, slots=True)
class GeneratedSpan:
    """Generated range using the same Unicode-character columns as SourceSpan."""

    start: SourcePosition
    end: SourcePosition


@dataclass(frozen=True, slots=True)
class RenderedFragment:
    text: str
    generated: GeneratedSpan
    source: SourceSpan | None
    role: RenderRole


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    text: str
    fragments: tuple[RenderedFragment, ...]


@dataclass(frozen=True, slots=True)
class CompilationResult:
    text: str
    rendered: RenderedDocument


class MappedEmitter:
    """Write generated text while recording coalesced source fragments."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._fragments: list[RenderedFragment] = []
        self._position = SourcePosition(1, 1)

    @property
    def position(self) -> SourcePosition:
        return self._position

    def emit(
        self,
        text: str,
        *,
        source: SourceSpan | None,
        role: RenderRole,
    ) -> None:
        if not text:
            return
        start = self._position
        self._position = self._advance(start, text)
        generated = GeneratedSpan(start, self._position)
        if self._fragments:
            previous = self._fragments[-1]
            if previous.source == source and previous.role == role:
                self._fragments[-1] = RenderedFragment(
                    previous.text + text,
                    GeneratedSpan(previous.generated.start, generated.end),
                    source,
                    role,
                )
            else:
                self._fragments.append(
                    RenderedFragment(text, generated, source, role)
                )
        else:
            self._fragments.append(RenderedFragment(text, generated, source, role))
        self._parts.append(text)

    def newline(self) -> None:
        self.emit("\n", source=None, role="synthetic")

    def line(
        self,
        text: str,
        *,
        source: SourceSpan | None,
        role: RenderRole,
    ) -> None:
        self.emit(text, source=source, role=role)
        self.newline()

    def finish(self) -> RenderedDocument:
        if not self._parts:
            self.newline()
        return RenderedDocument("".join(self._parts), tuple(self._fragments))

    @staticmethod
    def _advance(start: SourcePosition, text: str) -> SourcePosition:
        lines = text.split("\n")
        if len(lines) == 1:
            return SourcePosition(start.line, start.column + len(text))
        return SourcePosition(
            start.line + len(lines) - 1,
            len(lines[-1]) + 1,
        )


def _emit_group(emitter: MappedEmitter, argument: Argument) -> None:
    if (
        argument.layout is not ArgumentLayout.INLINE
        or not isinstance(argument.value, str)
    ):
        raise TypeError("renderer received a non-inline argument in an inline position")
    opener, closer = argument.kind.delimiters
    emitter.emit(opener, source=argument.span, role="open")
    emitter.emit(argument.value, source=argument.span, role="content")
    emitter.emit(closer, source=argument.span, role="close")


def _blank_line(node: CanonicalNode) -> bool:
    """A rendered blank line, which carries no provenance worth annotating."""

    return isinstance(node, RawTex) and not node.text


def _source_comment(emitter: MappedEmitter, node: CanonicalNode) -> None:
    span = node.span
    emitter.line(
        f"% texflux: {span.file}:{span.start.line}",
        source=None,
        role="synthetic",
    )


def _render_block(
    emitter: MappedEmitter,
    block: Block,
    source_comments: bool,
) -> None:
    for node in block.nodes:
        if source_comments and not _blank_line(node):
            _source_comment(emitter, node)

        match node:
            case RawTex(text=""):
                emitter.emit("\n", source=node.span, role="content")
            case RawTex(text=text):
                emitter.line(text, source=node.span, role="content")
            case GenericInvocation():
                _render_invocation(emitter, node, source_comments)
            case Item():
                _render_item(emitter, node, source_comments)
            case BraceGroup(body=body, header_raw=header_raw):
                emitter.line("{", source=node.span, role="open")
                if header_raw:
                    emitter.line(header_raw, source=node.span, role="content")
                _render_block(emitter, body, source_comments)
                emitter.line("}", source=node.span, role="close")
            case _:
                raise TypeError(
                    "renderer accepts canonical AST only; "
                    f"got {type(node).__name__}"
                )


def _render_arguments(
    emitter: MappedEmitter,
    prefix: str,
    arguments: tuple[Argument, ...],
    source: SourceSpan,
    source_comments: bool,
) -> None:
    emitter.emit(prefix, source=source, role="open")
    for argument in arguments:
        if argument.layout is ArgumentLayout.INLINE:
            _emit_group(emitter, argument)
            continue
        if argument.layout is not ArgumentLayout.BLOCK:
            raise TypeError("renderer received an invalid argument layout")
        if not isinstance(argument.value, Block):
            raise TypeError("renderer received an invalid argument value")
        emitter.emit("{", source=argument.span, role="open")
        emitter.newline()
        _render_block(emitter, argument.value, source_comments)
        emitter.emit("}", source=argument.span, role="close")


def _render_invocation(
    emitter: MappedEmitter,
    node: GenericInvocation,
    source_comments: bool,
) -> None:
    if node.body is None:
        _render_arguments(
            emitter,
            f"\\{node.name}",
            node.arguments,
            node.span,
            source_comments,
        )
        emitter.newline()
        return

    _render_arguments(
        emitter,
        f"\\begin{{{node.name}}}",
        node.arguments,
        node.span,
        source_comments,
    )
    emitter.newline()
    _render_block(emitter, node.body, source_comments)
    emitter.line(
        f"\\end{{{node.name}}}",
        source=node.span,
        role="close",
    )


def _render_item(
    emitter: MappedEmitter,
    node: Item,
    source_comments: bool,
) -> None:
    emitter.emit(r"\item", source=node.span, role="open")
    if node.overlay is not None:
        _emit_group(emitter, node.overlay)
    if node.label is not None:
        _emit_group(emitter, node.label)
    if node.first_line:
        emitter.emit(f" {node.first_line}", source=node.span, role="content")
    emitter.newline()
    _render_block(emitter, node.continuation, source_comments)


def render_with_provenance(
    document: Document,
    *,
    source_comments: bool = False,
) -> RenderedDocument:
    emitter = MappedEmitter()
    _render_block(emitter, document.body, source_comments)
    return emitter.finish()


def render(document: Document, *, source_comments: bool = False) -> str:
    """Render a canonical document, terminated by a newline."""

    return render_with_provenance(
        document,
        source_comments=source_comments,
    ).text


__all__ = [
    "CompilationResult",
    "RenderRole",
    "GeneratedSpan",
    "MappedEmitter",
    "RenderedDocument",
    "RenderedFragment",
    "render",
    "render_with_provenance",
]
