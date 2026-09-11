"""Syntax-AST rules shared by macro expansion and normalization.

These sit below both passes so neither has to restate a rule that the other
already owns. Predicates report failure by returning ``None`` so each caller
can raise the diagnostic that fits its own stage.
"""

from __future__ import annotations

from collections.abc import Iterator

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    GroupKind,
    Node,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SpecialInvocation,
    Stack,
)
from .errors import ValidationError


def _blank(node: Node) -> bool:
    """Whether a node is a blank source line rather than content."""

    return isinstance(node, RawTex) and not node.text


def required_text(argument: Argument) -> str | None:
    """The raw text of one required inline group, or ``None`` if it is not one."""

    if (
        argument.kind is not GroupKind.REQUIRED
        or argument.layout is not ArgumentLayout.INLINE
        or not isinstance(argument.value, str)
    ):
        return None
    return argument.value


def walk(block: Block) -> Iterator[Node]:
    """Yield every syntax node under ``block``, depth first.

    Used to inspect a tree without rewriting it, so it deliberately does not
    build a replacement the way the desugaring and macro passes do.
    """

    for node in block.nodes:
        yield node
        match node:
            case ParsedInvocation() | SpecialInvocation():
                for group in node.groups:
                    if isinstance(group.value, Block):
                        yield from walk(group.value)
                if node.suite is not None:
                    yield from walk(node.suite)
            case Stack():
                yield from node.segments
                if node.suite is not None:
                    yield from walk(node.suite)
            case SequenceEntry():
                yield from walk(node.value)
            case _:
                pass


def sequence_entries(suite: Block) -> tuple[SequenceEntry, ...]:
    """Read the ``-`` value entries of a sequence suite, ignoring blank lines."""

    entries = tuple(child for child in suite.nodes if not _blank(child))
    for child in entries:
        if not isinstance(child, SequenceEntry):
            raise ValidationError(
                "sequence suites require '-' value entries",
                child.span,
            )
    return entries


__all__ = ["required_text", "sequence_entries", "walk"]
