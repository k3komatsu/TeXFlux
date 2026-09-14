"""Syntax and canonical AST nodes for TeXFlux."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self, TypeAlias


@dataclass(frozen=True, slots=True, order=True)
class SourcePosition:
    """One-based position whose columns count Python Unicode characters."""

    line: int
    column: int

    def advance(self, text: str) -> SourcePosition:
        """Return the position after text whose line endings are normalized to LF."""

        lines = text.count("\n")
        column = (
            len(text.rpartition("\n")[2]) + 1
            if lines
            else self.column + len(text)
        )
        return SourcePosition(self.line + lines, column)


@dataclass(frozen=True, slots=True)
class SourceSpan:
    file: str
    start: SourcePosition
    end: SourcePosition

    @property
    def location(self) -> str:
        """This span's start as the ``file:line:column`` diagnostics prefix."""

        return f"{self.file}:{self.start.line}:{self.start.column}"


@dataclass(frozen=True, slots=True)
class TextFragment:
    """One provenance-tagged run of a text field."""

    text: str
    span: SourceSpan
    #: Template literals rank below caller content in columnless SyncTeX.
    scaffold: bool = False


SourceText: TypeAlias = tuple[TextFragment, ...]


def plain_text(parts: SourceText) -> str:
    return "".join(fragment.text for fragment in parts)


def _check_parts(parts: SourceText | None, text: object, owner: str) -> None:
    """Reject provenance fragments that do not spell ``text`` exactly."""

    if parts is not None and (
        not isinstance(text, str) or plain_text(parts) != text
    ):
        raise ValueError(f"{owner} parts must match its text")


class GroupKind(StrEnum):
    """A TeX group, identified by the delimiter pair that encloses it."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    OVERLAY = "overlay"
    BINDING = "binding"

    @property
    def delimiters(self) -> tuple[str, str]:
        return _GROUP_DELIMITERS[self]

    @classmethod
    def from_opener(cls, opener: str) -> Self:
        return _GROUP_OPENERS[opener]


_GROUP_DELIMITERS: Final = {
    GroupKind.REQUIRED: ("{", "}"),
    GroupKind.OPTIONAL: ("[", "]"),
    GroupKind.OVERLAY: ("<", ">"),
    GroupKind.BINDING: ("(", ")"),
}
_GROUP_OPENERS: Final = {
    opener: kind for kind, (opener, _) in _GROUP_DELIMITERS.items()
}

#: The group kinds a structural header scans after a name, in order.
_INLINE_KINDS: Final = (
    GroupKind.REQUIRED,
    GroupKind.OPTIONAL,
    GroupKind.OVERLAY,
)

#: Every character that can open an inline group, in declaration order.
#: BINDING is deliberately absent: '(' is a trailing list on a '!' segment
#: rather than a general inline group, so the header loop must not scan it.
GROUP_OPENERS: Final = "".join(
    _GROUP_DELIMITERS[kind][0] for kind in _INLINE_KINDS
)

#: The opener of the trailing '(...)' binding list a '!' segment may carry.
BINDING_OPENER: Final = _GROUP_DELIMITERS[GroupKind.BINDING][0]


class ArgumentLayout(StrEnum):
    """Where an argument's braces sit relative to its content."""

    INLINE = "inline"
    BLOCK = "block"
    HUGGED = "hugged"
    EXPLICIT = "explicit"


class InvocationKind(StrEnum):
    COMMAND = "command"
    ENVIRONMENT = "environment"
    BRACE = "brace"
    TRANSPARENT = "transparent"


class SuiteMode(StrEnum):
    SEQUENCE = "sequence"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class Block:
    nodes: tuple["Node", ...]
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Argument:
    kind: GroupKind
    value: str | Block
    layout: ArgumentLayout
    span: SourceSpan
    parts: SourceText | None = None

    def __post_init__(self) -> None:
        _check_parts(self.parts, self.value, "Argument")


@dataclass(frozen=True, slots=True)
class RawTex:
    text: str
    span: SourceSpan
    parts: SourceText | None = None
    #: A raw-region line, which macro expansion must not interpolate.
    verbatim: bool = False

    def __post_init__(self) -> None:
        _check_parts(self.parts, self.text, "RawTex")


@dataclass(frozen=True, slots=True)
class ParsedInvocation:
    kind: InvocationKind
    name: str
    groups: tuple[Argument, ...]
    suite: Block | None
    span: SourceSpan
    suite_mode: SuiteMode | None = None
    suite_span: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class SpecialInvocation:
    name: str
    groups: tuple[Argument, ...]
    suite: Block | None
    span: SourceSpan
    suite_mode: SuiteMode | None = None
    suite_span: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class SequenceEntry:
    """One marked argument value in a sequence suite.

    ``argument_kind`` is ``None`` for a ``-`` entry, whose value receives a
    generated required group during normalization.  A ``+`` entry records
    the delimiter kind of the one group the author wrote and is rendered
    explicitly.
    """

    value: Block
    marker_span: SourceSpan
    span: SourceSpan
    #: A value confined to the marker line renders with braces that hug it.
    spans_one_line: bool = False
    #: The authored group's kind for ``+`` entries; ``None`` means ``-``.
    argument_kind: GroupKind | None = None


@dataclass(frozen=True, slots=True)
class Stack:
    segments: tuple[ParsedInvocation | SpecialInvocation, ...]
    suite: Block | None
    span: SourceSpan
    suite_mode: SuiteMode | None = None
    suite_span: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class GenericInvocation:
    name: str
    arguments: tuple[Argument, ...]
    body: Block | None
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class BraceGroup:
    body: Block
    span: SourceSpan
    header_raw: str = ""
    header_parts: SourceText | None = None

    def __post_init__(self) -> None:
        _check_parts(self.header_parts, self.header_raw, "BraceGroup header")


@dataclass(frozen=True, slots=True)
class Document:
    body: Block
    span: SourceSpan


SyntaxNode = RawTex | ParsedInvocation | SpecialInvocation | Stack | SequenceEntry
CanonicalNode = RawTex | GenericInvocation | BraceGroup
Node = SyntaxNode | CanonicalNode


__all__ = [
    "Argument",
    "BINDING_OPENER",
    "ArgumentLayout",
    "Block",
    "BraceGroup",
    "CanonicalNode",
    "Document",
    "GROUP_OPENERS",
    "GenericInvocation",
    "GroupKind",
    "InvocationKind",
    "Node",
    "ParsedInvocation",
    "RawTex",
    "SequenceEntry",
    "SourcePosition",
    "SourceSpan",
    "SourceText",
    "SpecialInvocation",
    "Stack",
    "SuiteMode",
    "SyntaxNode",
    "TextFragment",
    "plain_text",
]
