"""Syntax-AST normalization and built-in special directives."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    CanonicalNode,
    Document,
    GenericInvocation,
    GroupKind,
    InvocationKind,
    Item,
    Node,
    ParsedInvocation,
    RawTex,
    SourceLocation,
    SpecialInvocation,
    Stack,
)
from .errors import DirectiveError, ValidationError
from .parser import scan_group


@dataclass(frozen=True, slots=True)
class TransformContext:
    registry: "DirectiveRegistry"


SpecialHandler = Callable[
    [SpecialInvocation, TransformContext],
    tuple[CanonicalNode, ...],
]


class DirectiveRegistry:
    """The small in-process registry used by normalization."""

    def __init__(self):
        self._handlers: dict[str, SpecialHandler] = {}

    def copy(self) -> "DirectiveRegistry":
        registry = DirectiveRegistry()
        registry._handlers.update(self._handlers)
        return registry

    def register(self, name: str, handler: SpecialHandler) -> None:
        self._handlers[name] = handler

    def lookup(self, name: str) -> SpecialHandler | None:
        return self._handlers.get(name)


def _blank(node: Node) -> bool:
    return isinstance(node, RawTex) and node.text == ""


def _desugar_block(block: Block) -> Block:
    """Replace direct stack nodes with their nested syntax shape."""

    nodes = tuple(
        _desugar_stack(node) if isinstance(node, Stack) else node
        for node in block.nodes
    )
    if nodes == block.nodes:
        return block
    return Block(nodes, block.loc)


def _desugar_stack(
    node: Stack,
) -> ParsedInvocation | SpecialInvocation:
    if len(node.segments) < 2:
        raise ValidationError(
            "stack requires at least two segments",
            node.loc,
        )

    # >> is only a structural abbreviation. Give each segment a one-child
    # suite from right to left, before invocation mode is inspected.
    tail: ParsedInvocation | SpecialInvocation = replace(
        node.segments[-1],
        suite=node.suite,
    )
    for segment in reversed(node.segments[:-1]):
        tail = replace(
            segment,
            suite=Block((tail,), segment.loc),
        )
    return tail


def _normalize_block(block: Block, context: TransformContext) -> Block:
    block = _desugar_block(block)
    nodes: list[CanonicalNode] = []
    for node in block.nodes:
        nodes.extend(_normalize_node(node, context))
    return Block(tuple(nodes), block.loc)


def _normalize_node(
    node: Node,
    context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    if isinstance(node, RawTex):
        return (node,)
    if isinstance(node, ParsedInvocation):
        return (_normalize_invocation(node, context),)
    if isinstance(node, SpecialInvocation):
        return _normalize_special(node, context)
    if isinstance(node, Stack):
        return _normalize_node(_desugar_stack(node), context)
    if isinstance(node, (GenericInvocation, BraceGroup, Item)):
        return (_normalize_canonical(node, context),)
    raise TypeError(f"unsupported AST node: {type(node).__name__}")


def _normalize_canonical(
    node: CanonicalNode,
    context: TransformContext,
) -> CanonicalNode:
    if isinstance(node, RawTex):
        return node
    if isinstance(node, GenericInvocation):
        body = None if node.body is None else _normalize_block(node.body, context)
        return replace(
            node,
            arguments=_header_arguments(node.arguments, context),
            body=body,
        )
    if isinstance(node, BraceGroup):
        return replace(node, body=_normalize_block(node.body, context))
    return replace(
        node,
        continuation=_normalize_block(node.continuation, context),
    )


def _normalize_argument(
    argument: Argument,
    context: TransformContext,
) -> Argument:
    value = argument.value
    if isinstance(value, Block):
        value = _normalize_block(value, context)
    return replace(argument, value=value)


def _header_arguments(
    arguments: tuple[Argument, ...],
    context: TransformContext,
) -> tuple[Argument, ...]:
    return tuple(_normalize_argument(argument, context) for argument in arguments)


def _normalize_invocation(
    node: ParsedInvocation,
    context: TransformContext,
) -> GenericInvocation:
    arguments = _header_arguments(node.groups, context)
    if node.suite is None:
        if node.kind is InvocationKind.ENVIRONMENT:
            raise ValidationError(
                "environment directives require a suite marker ':'",
                node.loc,
            )
        return GenericInvocation(node.name, arguments, None, node.loc)

    suite = _desugar_block(node.suite)
    direct = tuple(child for child in suite.nodes if not _blank(child))
    explicit = any(
        isinstance(child, SpecialInvocation)
        and child.name in {"arg", "body"}
        for child in direct
    )

    if node.kind is InvocationKind.COMMAND:
        return _normalize_command(
            node,
            arguments,
            suite,
            direct,
            explicit,
            context,
        )
    return _normalize_environment(
        node,
        arguments,
        direct,
        explicit,
        suite,
        context,
    )


def _normalize_command(
    node: ParsedInvocation,
    arguments: tuple[Argument, ...],
    suite: Block,
    direct: tuple[Node, ...],
    explicit: bool,
    context: TransformContext,
) -> GenericInvocation:
    if explicit:
        long_arguments: list[Argument] = []
        for child in direct:
            if not isinstance(child, SpecialInvocation) or child.name != "arg":
                raise ValidationError(
                    "explicit command mode accepts only !arg direct children",
                    child.loc,
                )
            long_arguments.append(_structured_argument(child, context))
        return GenericInvocation(
            node.name,
            arguments + tuple(long_arguments),
            None,
            node.loc,
        )

    return GenericInvocation(
        node.name,
        arguments + (_long_argument(suite, node.loc, context),),
        None,
        node.loc,
    )


def _normalize_environment(
    node: ParsedInvocation,
    arguments: tuple[Argument, ...],
    direct: tuple[Node, ...],
    explicit: bool,
    suite: Block,
    context: TransformContext,
) -> GenericInvocation:
    if not explicit:
        return GenericInvocation(
            node.name,
            arguments,
            _normalize_block(suite, context),
            node.loc,
        )

    long_arguments: list[Argument] = []
    body: Block | None = None
    for child in direct:
        if not isinstance(child, SpecialInvocation) or child.name not in {
            "arg",
            "body",
        }:
            raise ValidationError(
                "explicit environment mode accepts only !arg or !body direct children",
                child.loc,
            )
        if child.name == "arg":
            if body is not None:
                raise ValidationError(
                    "!arg must appear before !body",
                    child.loc,
                )
            long_arguments.append(_structured_argument(child, context))
            continue

        if body is not None:
            raise ValidationError(
                "!body may appear only once",
                child.loc,
            )
        if child.groups or child.suite is None:
            raise ValidationError(
                "!body requires a block suite and no groups",
                child.loc,
            )
        body = _normalize_block(child.suite, context)

    if body is None:
        # An explicit environment with arguments but no !body is still an
        # environment.  An empty canonical body preserves that distinction
        # without teaching the renderer about syntax-only nodes.
        body = Block((), node.loc)

    return GenericInvocation(
        node.name,
        arguments + tuple(long_arguments),
        body,
        node.loc,
    )


def _long_argument(
    suite: Block,
    loc: SourceLocation,
    context: TransformContext,
) -> Argument:
    return Argument(
        GroupKind.REQUIRED,
        _normalize_block(suite, context),
        ArgumentLayout.BLOCK,
        loc,
    )


def _structured_argument(
    node: SpecialInvocation,
    context: TransformContext,
) -> Argument:
    if node.groups:
        if node.suite is not None:
            raise ValidationError("inline !arg cannot have a suite", node.loc)
        if len(node.groups) != 1 or node.groups[0].kind is not GroupKind.REQUIRED:
            raise ValidationError(
                "inline !arg requires exactly one required group",
                node.loc,
            )
        group = node.groups[0]
        return Argument(
            GroupKind.REQUIRED,
            group.value,
            ArgumentLayout.INLINE,
            group.loc,
        )

    if node.suite is None:
        raise ValidationError(
            "!arg requires one inline group or a block suite",
            node.loc,
        )
    return Argument(
        GroupKind.REQUIRED,
        _normalize_block(node.suite, context),
        ArgumentLayout.BLOCK,
        node.loc,
    )


def _normalize_special(
    node: SpecialInvocation,
    context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    if node.name in {"arg", "body"}:
        raise DirectiveError(
            f"!{node.name} is valid only as a direct child of a structured invocation",
            node.loc,
        )
    handler = context.registry.lookup(node.name)
    if handler is None:
        raise DirectiveError(
            f"unknown special directive '!{node.name}'",
            node.loc,
        )
    result = handler(node, context)
    if not isinstance(result, tuple) or any(
        not isinstance(item, (RawTex, GenericInvocation, BraceGroup, Item))
        for item in result
    ):
        raise TypeError(
            "special directive handlers must return canonical AST tuples"
        )
    return tuple(
        _normalize_canonical(item, context)
        for item in result
    )


def _block_handler(
    node: SpecialInvocation,
    _context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    if node.groups or node.suite is None:
        raise ValidationError(
            "!block requires a block suite and no groups",
            node.loc,
        )
    # _normalize_special normalizes handler output at the canonical boundary.
    return (BraceGroup(node.suite, node.loc),)


def _items_handler(
    node: SpecialInvocation,
    _context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    if node.groups or node.suite is None:
        raise ValidationError(
            "!items requires a nonempty block suite and no groups",
            node.loc,
        )
    invalid = next(
        (child for child in node.suite.nodes if not isinstance(child, RawTex)),
        None,
    )
    if invalid is not None:
        raise ValidationError(
            "!items suites may contain raw lines only",
            invalid.loc,
        )

    raw_lines = node.suite.nodes
    if not any(line.text for line in raw_lines):
        raise ValidationError(
            "!items suite must contain at least one item",
            node.loc,
        )
    items, index = _parse_item_level(raw_lines, 0, 0)
    if index != len(raw_lines):
        line = raw_lines[index]
        raise ValidationError("invalid !items indentation", line.loc)
    body = Block(tuple(items), node.suite.loc)
    return (GenericInvocation("itemize", (), body, node.loc),)


def _next_item_line(lines: tuple[RawTex, ...], index: int) -> int | None:
    while index < len(lines) and lines[index].text == "":
        index += 1
    return None if index == len(lines) else index


def _source_loc_at(
    line: RawTex,
    text_index: int,
    indent: int,
) -> SourceLocation:
    line_start = line.loc.column - indent
    return SourceLocation(
        line.loc.file,
        line.loc.line,
        line_start + text_index,
    )


def _leading_spaces(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _item_prefix(
    line: RawTex,
    depth: int,
) -> tuple[Argument | None, Argument | None, str]:
    text = line.text
    marker_loc = _source_loc_at(line, depth, depth)
    if len(text) <= depth or text[depth] != "-":
        raise ValidationError("expected an item marker '-'", marker_loc)
    cursor = depth + 1
    if cursor < len(text) and text[cursor] not in " <[":
        raise ValidationError(
            "item marker must be followed by a space, group, or end",
            marker_loc,
        )

    overlay = None
    label = None
    if cursor < len(text) and text[cursor] == "<":
        group_loc = _source_loc_at(line, cursor, depth)
        end, value = scan_group(text, cursor, loc=group_loc)
        overlay = Argument(
            GroupKind.OVERLAY,
            value,
            ArgumentLayout.INLINE,
            group_loc,
        )
        cursor = end
        if cursor < len(text) and text[cursor] == "<":
            raise ValidationError(
                "duplicate item overlay prefix",
                group_loc,
            )

    if cursor < len(text) and text[cursor] == "[":
        group_loc = _source_loc_at(line, cursor, depth)
        end, value = scan_group(text, cursor, loc=group_loc)
        label = Argument(
            GroupKind.OPTIONAL,
            value,
            ArgumentLayout.INLINE,
            group_loc,
        )
        cursor = end
    elif cursor < len(text) and text[cursor] == "]":
        raise ValidationError("invalid item label prefix", marker_loc)

    if (
        overlay is None
        and label is not None
        and cursor < len(text)
        and text[cursor] == "<"
    ):
        raise ValidationError(
            "item label must not precede its overlay",
            label.loc,
        )
    if cursor < len(text) and text[cursor] in "<[":
        raise ValidationError(
            "duplicate item prefix",
            _source_loc_at(line, cursor, depth),
        )
    if cursor < len(text) and text[cursor] != " ":
        if overlay is not None or label is not None:
            raise ValidationError(
                "item prefix must be followed by a space or end",
                marker_loc,
            )
        raise ValidationError(
            "item marker must be followed by a space, group, or end",
            marker_loc,
        )
    if cursor < len(text) and text[cursor] == " ":
        cursor += 1
    return overlay, label, text[cursor:]


def _nested_itemize(
    lines: tuple[RawTex, ...],
    index: int,
    depth: int,
) -> tuple[GenericInvocation, int]:
    nested_items, end = _parse_item_level(lines, index, depth)
    loc = nested_items[0].loc if nested_items else lines[index].loc
    return GenericInvocation(
        "itemize",
        (),
        Block(tuple(nested_items), loc),
        loc,
    ), end


def _parse_item_level(
    lines: tuple[RawTex, ...],
    index: int,
    depth: int,
) -> tuple[list[Item], int]:
    items: list[Item] = []
    while True:
        while index < len(lines) and lines[index].text == "":
            next_index = _next_item_line(lines, index)
            if next_index is None:
                return items, len(lines)
            next_indent = _leading_spaces(lines[next_index].text)
            if next_indent == depth:
                index = next_index
                break
            if next_indent < depth:
                return items, index
            raise ValidationError(
                "item indentation skips the current list level",
                lines[next_index].loc,
            )

        if index >= len(lines):
            return items, index
        line = lines[index]
        indent = _leading_spaces(line.text)
        if indent < depth:
            return items, index
        if indent > depth:
            raise ValidationError(
                "item indentation skips the current list level",
                line.loc,
            )

        overlay, label, first_line = _item_prefix(line, depth)
        item_loc = _source_loc_at(line, depth, depth)
        index += 1
        continuation: list[CanonicalNode] = []

        while index < len(lines):
            current = lines[index]
            if current.text == "":
                next_index = _next_item_line(lines, index)
                if next_index is None:
                    continuation.extend(lines[index:])
                    index = len(lines)
                    break
                next_line = lines[next_index]
                next_indent = _leading_spaces(next_line.text)
                if next_indent == depth:
                    index = next_index
                    break
                if next_indent < depth:
                    break
                if (
                    next_indent == depth + 4
                    and next_line.text[depth + 4 : depth + 5] == "-"
                ):
                    continuation.extend(lines[index:next_index])
                    nested, index = _nested_itemize(
                        lines,
                        next_index,
                        depth + 4,
                    )
                    continuation.append(nested)
                    continue
                if next_indent < depth + 2:
                    raise ValidationError(
                        "item continuation requires at least two spaces",
                        next_line.loc,
                    )
                continuation.extend(lines[index:next_index])
                index = next_index
                continue

            indent = _leading_spaces(current.text)
            if indent <= depth:
                break
            if (
                indent == depth + 4
                and current.text[depth + 4 : depth + 5] == "-"
            ):
                nested, index = _nested_itemize(
                    lines,
                    index,
                    depth + 4,
                )
                continuation.append(nested)
                continue
            if (
                indent >= depth + 4
                and current.text[indent : indent + 1] == "-"
            ):
                raise ValidationError(
                    "nested item indentation skips a list level",
                    current.loc,
                )
            if indent < depth + 2:
                raise ValidationError(
                    "item continuation requires at least two spaces",
                    current.loc,
                )
            continuation.append(
                RawTex(current.text[depth + 2 :], current.loc)
            )
            index += 1

        items.append(
            Item(
                overlay,
                label,
                first_line,
                Block(tuple(continuation), item_loc),
                item_loc,
            )
        )


BUILTIN_DIRECTIVES = DirectiveRegistry()
BUILTIN_DIRECTIVES.register("block", _block_handler)
BUILTIN_DIRECTIVES.register("items", _items_handler)


def _assert_canonical_block(block: Block) -> None:
    for node in block.nodes:
        if isinstance(node, RawTex):
            continue
        if isinstance(node, BraceGroup):
            _assert_canonical_block(node.body)
            continue
        if isinstance(node, GenericInvocation):
            for argument in node.arguments:
                if isinstance(argument.value, Block):
                    _assert_canonical_block(argument.value)
            if node.body is not None:
                _assert_canonical_block(node.body)
            continue
        if isinstance(node, Item):
            _assert_canonical_block(node.continuation)
            continue
        raise TypeError(
            "normalization produced a non-canonical AST node: "
            f"{type(node).__name__}"
        )


def normalize(
    document: Document,
    registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
) -> Document:
    """Turn syntax AST into canonical AST and expand built-ins."""

    context = TransformContext(registry)
    normalized = Document(
        _normalize_block(document.body, context),
        document.loc,
    )
    _assert_canonical_block(normalized.body)
    return normalized


__all__ = [
    "BUILTIN_DIRECTIVES",
    "DirectiveRegistry",
    "SpecialHandler",
    "TransformContext",
    "normalize",
]
