"""Syntax and canonical AST nodes for Beamercraft."""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class SourceLocation:
    file: str
    line: int
    column: int


class GroupKind(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    OVERLAY = "overlay"


class ArgumentLayout(StrEnum):
    INLINE = "inline"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class Block:
    nodes: tuple["Node", ...]
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class Argument:
    kind: GroupKind
    value: str | Block
    layout: ArgumentLayout
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class RawTex:
    text: str
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class ParsedGeneric:
    name: str
    groups: tuple[Argument, ...]
    suite: Block | None
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class SpecialInvocation:
    name: str
    groups: tuple[Argument, ...]
    suite: Block | None
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class Stack:
    segments: tuple[ParsedGeneric | SpecialInvocation, ...]
    suite: Block
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class GenericInvocation:
    name: str
    arguments: tuple[Argument, ...]
    body: Block | None
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class Item:
    overlay: Argument | None
    label: Argument | None
    first_line: str
    continuation: Block
    loc: SourceLocation


@dataclass(frozen=True, slots=True)
class Document:
    body: Block
    loc: SourceLocation


SyntaxNode = RawTex | ParsedGeneric | SpecialInvocation | Stack
CanonicalNode = RawTex | GenericInvocation | Item
Node = SyntaxNode | CanonicalNode


__all__ = [
    "Argument",
    "ArgumentLayout",
    "Block",
    "CanonicalNode",
    "Document",
    "GenericInvocation",
    "GroupKind",
    "Item",
    "Node",
    "ParsedGeneric",
    "RawTex",
    "SourceLocation",
    "SpecialInvocation",
    "Stack",
    "SyntaxNode",
]
