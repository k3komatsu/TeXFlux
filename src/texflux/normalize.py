"""Syntax-AST normalization and built-in TeXFlux specials."""

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
    SequenceEntry,
    SourcePosition,
    SourceSpan,
    SpecialInvocation,
    Stack,
    SuiteMode,
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
    """The deliberately small in-process special registry."""

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


_CANONICAL_TYPES = (RawTex, GenericInvocation, BraceGroup, Item)


def _blank(node: Node) -> bool:
    return isinstance(node, RawTex) and node.text == ""


def _desugar_block(block: Block) -> Block:
    """Desugar stacks wherever a syntax block contains them."""

    nodes: list[Node] = []
    changed = False
    for node in block.nodes:
        if isinstance(node, Stack):
            nodes.append(_desugar_stack(node))
            changed = True
        else:
            nodes.append(node)
    if not changed:
        return block
    return Block(tuple(nodes), block.span)


def _desugar_stack(
    node: Stack,
) -> ParsedInvocation | SpecialInvocation:
    if len(node.segments) < 2:
        raise ValidationError(
            "stack requires at least two segments",
            node.span,
        )

    # A stack's suffix belongs to its rightmost segment. Every segment to the
    # left receives exactly one synthetic block value.
    tail: ParsedInvocation | SpecialInvocation = replace(
        node.segments[-1],
        suite=node.suite,
        suite_mode=node.suite_mode,
        suite_span=node.suite_span,
    )
    for segment in reversed(node.segments[:-1]):
        child_end = tail.span.end
        if tail.suite is not None and tail.suite.span.end > child_end:
            child_end = tail.suite.span.end
        suite_span = SourceSpan(
            segment.span.file,
            segment.span.start,
            child_end,
        )
        tail = replace(
            segment,
            suite=Block((tail,), suite_span),
            suite_mode=SuiteMode.BLOCK,
            suite_span=suite_span,
        )
    return tail


def _normalize_block(block: Block, context: TransformContext) -> Block:
    block = _desugar_block(block)
    nodes: list[CanonicalNode] = []
    for node in block.nodes:
        nodes.extend(_normalize_node(node, context))
    return Block(tuple(nodes), block.span)


def _normalize_node(
    node: Node,
    context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    if isinstance(node, RawTex):
        return (node,)
    if isinstance(node, SequenceEntry):
        raise ValidationError(
            "sequence entries are only valid inside a ':' suite",
            node.span,
        )
    if isinstance(node, ParsedInvocation):
        return _normalize_invocation(node, context)
    if isinstance(node, SpecialInvocation):
        return _normalize_special(node, context)
    if isinstance(node, Stack):
        return _normalize_node(_desugar_stack(node), context)
    if isinstance(node, _CANONICAL_TYPES):
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


def _suite_mode(
    node: ParsedInvocation | SpecialInvocation,
    default: SuiteMode = SuiteMode.BLOCK,
) -> SuiteMode:
    """Read a suite mode, applying ``default`` to hand-built AST nodes."""

    return node.suite_mode or default


def _sequence_entries(suite: Block) -> tuple[SequenceEntry, ...]:
    entries = tuple(child for child in suite.nodes if not _blank(child))
    for child in entries:
        if not isinstance(child, SequenceEntry):
            raise ValidationError(
                "sequence suites require '-' value entries",
                child.span,
            )
    return entries


def _argument_from_entry(
    entry: SequenceEntry,
    context: TransformContext,
) -> Argument:
    value = _normalize_block(entry.value, context)
    return Argument(GroupKind.REQUIRED, value, ArgumentLayout.BLOCK, entry.span)


def _sequence_body(suite: Block, context: TransformContext) -> Block:
    """Concatenate every ``-`` value of a sequence suite into one block."""

    nodes: list[CanonicalNode] = []
    for entry in _sequence_entries(suite):
        nodes.extend(_normalize_block(entry.value, context).nodes)
    return Block(tuple(nodes), suite.span)


def _container_body(
    node: ParsedInvocation,
    suite: Block,
    context: TransformContext,
) -> Block:
    """Flatten a container suite; either mode contributes one block value."""

    if _suite_mode(node) is SuiteMode.BLOCK:
        return _normalize_block(suite, context)
    return _sequence_body(suite, context)


def _suite_arguments(
    node: ParsedInvocation,
    suite: Block,
    context: TransformContext,
) -> tuple[Argument, ...]:
    """Convert a command suite into its required block arguments."""

    if _suite_mode(node) is SuiteMode.SEQUENCE:
        return tuple(
            _argument_from_entry(entry, context)
            for entry in _sequence_entries(suite)
        )
    return (
        Argument(
            GroupKind.REQUIRED,
            _normalize_block(suite, context),
            ArgumentLayout.BLOCK,
            node.suite_span or node.span,
        ),
    )


def _normalize_environment(
    node: ParsedInvocation,
    arguments: tuple[Argument, ...],
    suite: Block,
    context: TransformContext,
) -> GenericInvocation:
    """A block suite is the body; a sequence suite ends with it."""

    if _suite_mode(node) is SuiteMode.BLOCK:
        body = _normalize_block(suite, context)
    else:
        entries = _sequence_entries(suite)
        if not entries:
            raise ValidationError(
                "environment sequence suites require a body value",
                node.span,
            )
        arguments += tuple(
            _argument_from_entry(entry, context) for entry in entries[:-1]
        )
        body = _normalize_block(entries[-1].value, context)
    return GenericInvocation(node.name, arguments, body, node.span)


def _normalize_invocation(
    node: ParsedInvocation,
    context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    arguments = _header_arguments(node.groups, context)
    if node.suite is None:
        if node.kind is InvocationKind.COMMAND:
            return (GenericInvocation(node.name, arguments, None, node.span),)
        raise ValidationError(
            "container values require a suite or a closed stack payload",
            node.span,
        )

    suite = _desugar_block(node.suite)

    if node.kind is InvocationKind.COMMAND:
        return (
            GenericInvocation(
                node.name,
                arguments + _suite_arguments(node, suite, context),
                None,
                node.span,
            ),
        )

    if node.kind is InvocationKind.ENVIRONMENT:
        return (_normalize_environment(node, arguments, suite, context),)

    if node.kind is InvocationKind.BRACE:
        if len(node.groups) != 1 or node.groups[0].kind is not GroupKind.REQUIRED:
            raise ValidationError(
                "literal brace containers require one raw header group",
                node.span,
            )
        return (
            BraceGroup(
                _container_body(node, suite, context),
                node.span,
                node.groups[0].value,
            ),
        )

    if node.kind is InvocationKind.TRANSPARENT:
        if node.groups:
            raise ValidationError(
                "transparent containers do not accept header groups",
                node.span,
            )
        return _container_body(node, suite, context).nodes

    raise TypeError(f"unsupported invocation kind: {node.kind}")


def _normalize_special(
    node: SpecialInvocation,
    context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    handler = context.registry.lookup(node.name)
    if handler is None:
        raise DirectiveError(
            f"unknown special directive '!{node.name}'",
            node.span,
        )
    result = handler(node, context)
    if not isinstance(result, tuple) or any(
        not isinstance(item, _CANONICAL_TYPES) for item in result
    ):
        raise TypeError(
            "special directive handlers must return canonical AST tuples"
        )
    return tuple(_normalize_canonical(item, context) for item in result)


def _vspace(group: Argument) -> GenericInvocation:
    return GenericInvocation("vspace", (group,), None, group.span)


def _vpad_handler(
    node: SpecialInvocation,
    context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    if node.suite is None or _suite_mode(node) is not SuiteMode.BLOCK:
        raise ValidationError("!vpad requires a ': |' block suite", node.span)
    if not 1 <= len(node.groups) <= 2:
        raise ValidationError(
            "!vpad requires one or two required inline groups",
            node.span,
        )
    invalid_group = next(
        (
            group
            for group in node.groups
            if group.kind is not GroupKind.REQUIRED
            or group.layout is not ArgumentLayout.INLINE
            or not isinstance(group.value, str)
        ),
        None,
    )
    if invalid_group is not None:
        raise ValidationError(
            "!vpad requires one or two required inline groups",
            invalid_group.span,
        )

    body = _normalize_block(node.suite, context)
    result: list[CanonicalNode] = [_vspace(node.groups[0])]
    result.extend(body.nodes)
    if len(node.groups) == 2:
        result.append(_vspace(node.groups[1]))
    return tuple(result)


def _items_handler(
    node: SpecialInvocation,
    _context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    if (
        node.groups
        or node.suite is None
        or _suite_mode(node, SuiteMode.SEQUENCE) is not SuiteMode.SEQUENCE
    ):
        raise ValidationError(
            "!items requires a ':' sequence suite and no groups",
            node.span,
        )
    invalid = next(
        (child for child in node.suite.nodes if not isinstance(child, RawTex)),
        None,
    )
    if invalid is not None:
        raise ValidationError(
            "!items suites may contain item lines only",
            invalid.span,
        )

    raw_lines = node.suite.nodes
    if not any(line.text for line in raw_lines):
        raise ValidationError(
            "!items suite must contain at least one item",
            node.span,
        )
    items, index = _parse_item_level(raw_lines, 0, 0)
    if index != len(raw_lines):
        line = raw_lines[index]
        raise ValidationError("invalid !items indentation", line.span)
    body = Block(tuple(items), node.suite.span)
    return (GenericInvocation("itemize", (), body, node.span),)


def _next_item_line(lines: tuple[RawTex, ...], index: int) -> int | None:
    while index < len(lines) and lines[index].text == "":
        index += 1
    return None if index == len(lines) else index


def _source_span_at(line: RawTex, text_index: int) -> SourceSpan:
    start = SourcePosition(
        line.span.start.line,
        line.span.start.column + text_index,
    )
    return SourceSpan(
        line.span.file,
        start,
        SourcePosition(start.line, start.column + 1),
    )


def _leading_spaces(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _item_group(
    line: RawTex,
    cursor: int,
    kind: GroupKind,
) -> tuple[Argument, int]:
    """Scan one item prefix group and return it with the following cursor."""

    span = _source_span_at(line, cursor)
    end, value = scan_group(line.text, cursor, span=span)
    span = SourceSpan(
        span.file,
        span.start,
        SourcePosition(span.start.line, span.start.column + end - cursor),
    )
    return Argument(kind, value, ArgumentLayout.INLINE, span), end


def _item_prefix(
    line: RawTex,
    depth: int,
) -> tuple[Argument | None, Argument | None, str]:
    text = line.text
    marker_span = _source_span_at(line, depth)
    if len(text) <= depth or text[depth] != "-":
        raise ValidationError("expected an item marker '-'", marker_span)
    cursor = depth + 1
    if cursor < len(text) and text[cursor] not in " <[":
        raise ValidationError(
            "item marker must be followed by a space, group, or end",
            marker_span,
        )

    overlay = None
    label = None
    if cursor < len(text) and text[cursor] == "<":
        overlay, cursor = _item_group(line, cursor, GroupKind.OVERLAY)
        if cursor < len(text) and text[cursor] == "<":
            raise ValidationError("duplicate item overlay prefix", overlay.span)

    if cursor < len(text) and text[cursor] == "[":
        label, cursor = _item_group(line, cursor, GroupKind.OPTIONAL)
    elif cursor < len(text) and text[cursor] == "]":
        raise ValidationError("invalid item label prefix", marker_span)

    if (
        overlay is None
        and label is not None
        and cursor < len(text)
        and text[cursor] == "<"
    ):
        raise ValidationError("item label must not precede its overlay", label.span)
    if cursor < len(text) and text[cursor] in "<[":
        raise ValidationError("duplicate item prefix", _source_span_at(line, cursor))
    if cursor < len(text) and text[cursor] != " ":
        if overlay is not None or label is not None:
            raise ValidationError(
                "item prefix must be followed by a space or end",
                marker_span,
            )
        raise ValidationError(
            "item marker must be followed by a space, group, or end",
            marker_span,
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
    span = nested_items[0].span if nested_items else lines[index].span
    return GenericInvocation(
        "itemize",
        (),
        Block(tuple(nested_items), span),
        span,
    ), end


def _block_item_lines(
    lines: tuple[RawTex, ...],
    index: int,
    depth: int,
) -> tuple[str, list[RawTex], int]:
    """Consume the contents of a bare multiline item marker."""

    content: list[RawTex] = []
    while index < len(lines):
        current = lines[index]
        if current.text != "":
            indent = _leading_spaces(current.text)
            if indent <= depth:
                break
            if indent < depth + 4:
                raise ValidationError(
                    "multiline item content requires four spaces",
                    current.span,
                )
            stripped = current.text[depth + 4 :]
            span = SourceSpan(
                current.span.file,
                SourcePosition(
                    current.span.start.line,
                    current.span.start.column + depth + 4,
                ),
                current.span.end,
            )
            content.append(RawTex(stripped, span))
        else:
            next_index = _next_item_line(lines, index)
            if (
                next_index is not None
                and _leading_spaces(lines[next_index].text) <= depth
            ):
                break
            content.append(current)
        index += 1
    first = content[0].text if content else ""
    return first, content[1:], index


def _item_continuation(
    lines: tuple[RawTex, ...],
    index: int,
    depth: int,
) -> tuple[list[CanonicalNode], int]:
    """Consume one item's continuation lines and nested lists."""

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
                nested, index = _nested_itemize(lines, next_index, depth + 4)
                continuation.append(nested)
                continue
            if next_indent < depth + 2:
                raise ValidationError(
                    "item continuation requires at least two spaces",
                    next_line.span,
                )
            continuation.extend(lines[index:next_index])
            index = next_index
            continue

        indent = _leading_spaces(current.text)
        if indent <= depth:
            break
        if indent == depth + 4 and current.text[depth + 4 : depth + 5] == "-":
            nested, index = _nested_itemize(lines, index, depth + 4)
            continuation.append(nested)
            continue
        if indent >= depth + 4 and current.text[indent : indent + 1] == "-":
            raise ValidationError(
                "nested item indentation skips a list level",
                current.span,
            )
        if indent < depth + 2:
            raise ValidationError(
                "item continuation requires at least two spaces",
                current.span,
            )
        continuation_span = SourceSpan(
            current.span.file,
            SourcePosition(
                current.span.start.line,
                current.span.start.column + depth + 2,
            ),
            current.span.end,
        )
        continuation.append(RawTex(current.text[depth + 2 :], continuation_span))
        index += 1
    return continuation, index


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
                lines[next_index].span,
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
                line.span,
            )

        overlay, label, first_line = _item_prefix(line, depth)
        item_marker_span = _source_span_at(line, depth)
        item_span = SourceSpan(
            item_marker_span.file,
            item_marker_span.start,
            line.span.end,
        )
        index += 1

        if first_line == "" and overlay is None and label is None:
            next_index = _next_item_line(lines, index)
            has_block_lines = (
                next_index is not None
                and _leading_spaces(lines[next_index].text) >= depth + 4
            )
            if has_block_lines:
                first_line, block_lines, index = _block_item_lines(
                    lines,
                    index,
                    depth,
                )
                items.append(
                    Item(
                        overlay,
                        label,
                        first_line,
                        Block(tuple(block_lines), item_span),
                        item_span,
                    )
                )
                continue

        continuation, index = _item_continuation(lines, index, depth)

        items.append(
            Item(
                overlay,
                label,
                first_line,
                Block(tuple(continuation), item_span),
                item_span,
            )
        )


BUILTIN_DIRECTIVES = DirectiveRegistry()
BUILTIN_DIRECTIVES.register("items", _items_handler)
BUILTIN_DIRECTIVES.register("vpad", _vpad_handler)


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
    """Turn syntax AST into canonical AST and expand built-in specials."""

    context = TransformContext(registry)
    normalized = Document(_normalize_block(document.body, context), document.span)
    _assert_canonical_block(normalized.body)
    return normalized


__all__ = [
    "BUILTIN_DIRECTIVES",
    "DirectiveRegistry",
    "SpecialHandler",
    "TransformContext",
    "normalize",
]
