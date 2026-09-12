"""Deterministic rendering of the canonical AST with source provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from .syntax import blank, is_escaped
from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    CanonicalNode,
    Document,
    GenericInvocation,
    GroupKind,
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
class RenderWarning:
    """A rendering hazard the author should look at, not a failure."""

    message: str
    span: SourceSpan

    def diagnostic(self) -> str:
        return f"{self.span.location}: warning: {self.message}"


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    text: str
    fragments: tuple[RenderedFragment, ...]
    warnings: tuple[RenderWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadedSource:
    """One source file a compilation read, for the source map to record."""

    #: The spelling every ``SourceSpan`` of this file carries.
    file: str
    #: Its absolute filesystem path, which the map stores and digests.
    path: str
    data: bytes


@dataclass(frozen=True, slots=True)
class CompilationResult:
    text: str
    rendered: RenderedDocument
    #: Every module the compilation read, the root first.
    sources: tuple[LoadedSource, ...] = ()


class MappedEmitter:
    """Write generated text while recording coalesced source fragments."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._fragments: list[RenderedFragment] = []
        self._warnings: list[RenderWarning] = []
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
        self._position = start.advance(text)
        generated = GeneratedSpan(start, self._position)
        previous = self._fragments[-1] if self._fragments else None
        if previous is not None and previous.source == source and previous.role == role:
            self._fragments[-1] = RenderedFragment(
                previous.text + text,
                GeneratedSpan(previous.generated.start, generated.end),
                source,
                role,
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

    def mark(self) -> int:
        """A cursor into the emitted parts, for reading back one span of text."""

        return len(self._parts)

    def text_since(self, mark: int) -> str:
        """Every character emitted since ``mark``."""

        return "".join(self._parts[mark:])

    def drop_trailing_newline(self) -> None:
        """Retract one emitted newline so a closing brace can hug its content."""

        if not self._parts or not self._parts[-1].endswith("\n"):
            return
        last = self._fragments[-1]
        self._parts[-1] = self._parts[-1][:-1]
        if not self._parts[-1]:
            self._parts.pop()
        trimmed = last.text[:-1]
        # A coalesced fragment keeps the text that preceded the newline, so
        # the cursor belongs after that text rather than at the run's start.
        self._position = last.generated.start.advance(trimmed)
        if trimmed:
            self._fragments[-1] = RenderedFragment(
                trimmed,
                GeneratedSpan(last.generated.start, self._position),
                last.source,
                last.role,
            )
        else:
            self._fragments.pop()

    def finish(self) -> RenderedDocument:
        if not self._parts:
            self.newline()
        return RenderedDocument(
            "".join(self._parts),
            tuple(self._fragments),
            tuple(self._warnings),
        )

    def warn(self, message: str, span: SourceSpan) -> None:
        self._warnings.append(RenderWarning(message, span))


def _emit_group(emitter: MappedEmitter, argument: Argument) -> None:
    if argument.kind is GroupKind.BINDING:
        # A binding list configures an import; it is never TeX to emit.
        raise TypeError("renderer received a binding list")
    if (
        argument.layout is not ArgumentLayout.INLINE
        or not isinstance(argument.value, str)
    ):
        raise TypeError("renderer received a non-inline argument in an inline position")
    opener, closer = argument.kind.delimiters
    emitter.emit(opener, source=argument.span, role="open")
    emitter.emit(argument.value, source=argument.span, role="content")
    emitter.emit(closer, source=argument.span, role="close")


def _comment_start(line: str) -> bool:
    """Whether a rendered line is entirely a TeX comment."""

    return line.lstrip().startswith("%")


def _comment_index(line: str) -> int:
    """The offset of the first unescaped ``%``, or ``-1`` when there is none."""

    for index, char in enumerate(line):
        if char == "%" and not is_escaped(line, index):
            return index
    return -1


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
        if source_comments and not blank(node):
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
        explicit = argument.layout is ArgumentLayout.EXPLICIT
        if not explicit and argument.layout not in (
            ArgumentLayout.BLOCK,
            ArgumentLayout.HUGGED,
        ):
            raise TypeError("renderer received an invalid argument layout")
        if not isinstance(argument.value, Block):
            raise TypeError("renderer received an invalid argument value")

        if not explicit:
            emitter.emit("{", source=argument.span, role="open")
        if argument.layout is ArgumentLayout.BLOCK:
            emitter.newline()
        mark = emitter.mark()
        _render_block(emitter, argument.value, source_comments)
        if argument.layout is not ArgumentLayout.BLOCK:
            _close_hugged(emitter, argument, mark)
        if not explicit:
            emitter.emit("}", source=argument.span, role="close")


def _close_hugged(
    emitter: MappedEmitter,
    argument: Argument,
    mark: int,
) -> None:
    """Pull what follows onto the value's last line, when that is safe.

    A ``%`` on that line would comment out whatever is pulled up, so a fully
    commented line keeps its newline. A trailing comment after real content
    cannot be rescued without rewriting the author's TeX, so it only earns a
    warning. Author-written braces take the same path: identical output has
    to produce an identical diagnostic.
    """

    # Only the value's own text can carry a comment, so read back exactly
    # what it emitted rather than the whole output line.
    text = emitter.text_since(mark)
    line = text.removesuffix("\n").rpartition("\n")[2]
    if _comment_start(line):
        emitter.warn(
            "value ends with a comment line, so what follows it stays on "
            "its own line",
            argument.span,
        )
        return
    if _comment_index(line) >= 0:
        emitter.warn(
            "value ends with a line containing '%', so the closing brace and "
            "whatever follows are commented out",
            argument.span,
        )
    emitter.drop_trailing_newline()


def _render_invocation(
    emitter: MappedEmitter,
    node: GenericInvocation,
    source_comments: bool,
) -> None:
    _render_arguments(
        emitter,
        f"\\{node.name}" if node.body is None else f"\\begin{{{node.name}}}",
        node.arguments,
        node.span,
        source_comments,
    )
    emitter.newline()
    if node.body is None:
        return
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
    "GeneratedSpan",
    "LoadedSource",
    "MappedEmitter",
    "RenderRole",
    "RenderWarning",
    "RenderedDocument",
    "RenderedFragment",
    "render",
    "render_with_provenance",
]
