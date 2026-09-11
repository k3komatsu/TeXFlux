"""Syntax and canonical AST nodes for TeXFlux."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True, order=True)
class SourcePosition:
    """One-based position whose columns count Python Unicode characters."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class SourceSpan:
    file: str
    start: SourcePosition
    end: SourcePosition


class GroupKind(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    OVERLAY = "overlay"


class ArgumentLayout(StrEnum):
    INLINE = "inline"
    BLOCK = "block"


class InvocationKind(StrEnum):
    COMMAND = "command"
    ENVIRONMENT = "environment"


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


@dataclass(frozen=True, slots=True)
class RawTex:
    text: str
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class ParsedInvocation:
    kind: InvocationKind
    name: str
    groups: tuple[Argument, ...]
    suite: Block | None
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class SpecialInvocation:
    name: str
    groups: tuple[Argument, ...]
    suite: Block | None
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Stack:
    segments: tuple[ParsedInvocation | SpecialInvocation, ...]
    suite: Block
    span: SourceSpan


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


@dataclass(frozen=True, slots=True)
class Item:
    overlay: Argument | None
    label: Argument | None
    first_line: str
    continuation: Block
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class Document:
    body: Block
    span: SourceSpan


SyntaxNode = RawTex | ParsedInvocation | SpecialInvocation | Stack
CanonicalNode = RawTex | GenericInvocation | BraceGroup | Item
Node = SyntaxNode | CanonicalNode


__all__ = [
    "Argument",
    "ArgumentLayout",
    "BraceGroup",
    "Block",
    "CanonicalNode",
    "Document",
    "GenericInvocation",
    "GroupKind",
    "InvocationKind",
    "Item",
    "Node",
    "ParsedInvocation",
    "RawTex",
    "SourcePosition",
    "SourceSpan",
    "SpecialInvocation",
    "Stack",
    "SyntaxNode",
]
