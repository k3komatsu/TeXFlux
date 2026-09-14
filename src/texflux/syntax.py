"""Syntax-AST rules shared by macro expansion and normalization.

These sit below both passes so neither has to restate a rule that the other
already owns. Predicates report failure by returning ``None`` so each caller
can raise the diagnostic that fits its own stage.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Final

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


#: The two whole-line markers that delimit a raw region. The physical-line
#: layer matches them as complete lines before any header scanning, so they
#: are the only two names below the prefix-only classification rule.
RAW_BEGIN_MARKER: Final = "!BEGIN_RAW_MODE"
RAW_END_MARKER: Final = "!END_RAW_MODE"

#: The same two, spelled as special names, for the checks that see a scanned
#: name rather than a line.
RAW_MODE_NAMES: Final = frozenset({RAW_BEGIN_MARKER[1:], RAW_END_MARKER[1:]})

#: The one-line raw escape. '@@' and '!!' strip a single prefix character, so
#: they only ever reach a line that already starts with '@' or '!'; this one
#: strips the whole marker, which makes it the only escape that can reach a
#: '\' line the header scanner would otherwise claim.
RAW_LINE_MARKER: Final = "!|"


def is_escaped(text: str, index: int) -> bool:
    """Whether the character at ``index`` is escaped by an odd backslash run."""

    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def blank(node: Node) -> bool:
    """Whether a node is a blank source line rather than content."""

    return isinstance(node, RawTex) and not node.text


def _inline_text(argument: Argument, kind: GroupKind) -> str | None:
    if (
        argument.kind is not kind
        or argument.layout is not ArgumentLayout.INLINE
        or not isinstance(argument.value, str)
    ):
        return None
    return argument.value


def required_text(argument: Argument) -> str | None:
    """The raw text of one required ``{...}`` group, or ``None`` if it is not one."""

    return _inline_text(argument, GroupKind.REQUIRED)


def optional_text(argument: Argument) -> str | None:
    """The raw text of one optional ``[...]`` group, or ``None`` if it is not one."""

    return _inline_text(argument, GroupKind.OPTIONAL)


def demand_text(argument: Argument, label: str) -> str:
    """Read one required inline group, or reject it at its own span.

    Every ``!`` construct that names something -- a macro, a parameter, a
    build flag -- spells that name as one ``{...}`` group, so they share both
    the reading and the diagnostic.
    """

    text = required_text(argument)
    if text is None:
        raise ValidationError(
            f"{label} must be a required '{{...}}' group",
            argument.span,
            code="V042",
        )
    return text.strip()


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


def map_children(node: Node, rewrite: Callable[[Block], Block]) -> Node:
    """Rebuild ``node`` with ``rewrite`` applied to every block it holds.

    The writing counterpart of ``walk``: a pass that rewrites a tree keeps
    its own rule for the nodes it cares about and hands every other node
    here, so the shape of the syntax AST is spelled out once. A node without
    blocks, canonical nodes included, is returned as it is. Every rewriting
    pass runs after ``>>`` desugaring, so a ``Stack`` is refused rather than
    given a meaning here.
    """

    match node:
        case ParsedInvocation() | SpecialInvocation():
            return replace(
                node,
                groups=tuple(
                    replace(group, value=rewrite(group.value))
                    if isinstance(group.value, Block)
                    else group
                    for group in node.groups
                ),
                suite=None if node.suite is None else rewrite(node.suite),
            )
        case Stack():
            raise TypeError("map_children requires a desugared syntax AST")
        case SequenceEntry():
            return replace(node, value=rewrite(node.value))
        case _:
            return node


def stacks(block: Block) -> Iterator[Stack]:
    """Yield every ``>>`` stack under ``block``, before desugaring removes it."""

    return (node for node in walk(block) if isinstance(node, Stack))


def sequence_entries(suite: Block) -> tuple[SequenceEntry, ...]:
    """Read the marked value entries of a sequence suite, ignoring blanks."""

    entries = tuple(child for child in suite.nodes if not blank(child))
    for child in entries:
        if not isinstance(child, SequenceEntry):
            raise ValidationError(
                "sequence suites require '-' or '+' value entries",
                child.span,
                code="V043",
            )
    return entries


__all__ = [
    "blank",
    "demand_text",
    "is_escaped",
    "map_children",
    "optional_text",
    "RAW_BEGIN_MARKER",
    "RAW_END_MARKER",
    "RAW_LINE_MARKER",
    "RAW_MODE_NAMES",
    "required_text",
    "sequence_entries",
    "stacks",
    "walk",
]
