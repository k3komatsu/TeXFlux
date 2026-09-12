"""Syntax-AST normalization and built-in TeXFlux specials."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Self, TypeAlias

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
from .errors import DirectiveError, ParseError, ValidationError
from .macros import collect_macros, expand_macros, validate_macro_forms
from .parser import scan_group
from .syntax import required_text, sequence_entries


@dataclass(frozen=True, slots=True)
class TransformContext:
    registry: "DirectiveRegistry"


SpecialHandler: TypeAlias = Callable[
    [SpecialInvocation, TransformContext],
    tuple[CanonicalNode, ...],
]


class DirectiveRegistry:
    """The deliberately small in-process special registry."""

    def __init__(self, handlers: Mapping[str, SpecialHandler] | None = None):
        self._handlers: dict[str, SpecialHandler] = dict(handlers or {})

    def copy(self) -> Self:
        return type(self)(self._handlers)

    def register(self, name: str, handler: SpecialHandler) -> None:
        self._handlers[name] = handler

    def lookup(self, name: str) -> SpecialHandler | None:
        return self._handlers.get(name)


_CANONICAL_TYPES = (RawTex, GenericInvocation, BraceGroup, Item)


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


def desugar(document: Document) -> Document:
    """Resolve every ``>>`` stack in one document, depth first.

    Macro expansion runs on the result, so a closed stack payload has already
    become an ordinary single block value by the time a call is bound.
    """

    return replace(document, body=_desugar_tree(document.body))


def _desugar_tree(block: Block) -> Block:
    nodes = tuple(
        _desugar_child(_desugar_stack(node) if isinstance(node, Stack) else node)
        for node in block.nodes
    )
    return replace(block, nodes=nodes)


def _desugar_child(node: Node) -> Node:
    match node:
        case ParsedInvocation() | SpecialInvocation():
            return replace(
                node,
                groups=tuple(_desugar_argument(group) for group in node.groups),
                suite=None if node.suite is None else _desugar_tree(node.suite),
            )
        case SequenceEntry():
            return replace(node, value=_desugar_tree(node.value))
        case _:
            return node


def _desugar_argument(argument: Argument) -> Argument:
    if isinstance(argument.value, Block):
        return replace(argument, value=_desugar_tree(argument.value))
    return argument


def _normalize_block(block: Block, context: TransformContext) -> Block:
    nodes: list[CanonicalNode] = []
    for node in block.nodes:
        nodes.extend(_normalize_node(node, context))
    return Block(tuple(nodes), block.span)


def _normalize_node(
    node: Node,
    context: TransformContext,
) -> tuple[CanonicalNode, ...]:
    match node:
        case RawTex():
            return (node,)
        case ParsedInvocation():
            return _normalize_invocation(node, context)
        case SpecialInvocation():
            return _normalize_special(node, context)
        case SequenceEntry():
            raise ValidationError(
                "sequence entries are only valid inside a ':' suite",
                node.span,
            )
        case GenericInvocation() | BraceGroup() | Item():
            return (_normalize_canonical(node, context),)
        case _:
            raise TypeError(f"unsupported AST node: {type(node).__name__}")


def _normalize_canonical(
    node: CanonicalNode,
    context: TransformContext,
) -> CanonicalNode:
    match node:
        case RawTex():
            return node
        case GenericInvocation(arguments=arguments, body=body):
            return replace(
                node,
                arguments=_header_arguments(arguments, context),
                body=None if body is None else _normalize_block(body, context),
            )
        case BraceGroup(body=body):
            return replace(node, body=_normalize_block(body, context))
        case Item(continuation=continuation):
            return replace(
                node,
                continuation=_normalize_block(continuation, context),
            )
        case _:
            raise TypeError(f"unsupported canonical node: {type(node).__name__}")


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


def _argument_from_entry(
    entry: SequenceEntry,
    context: TransformContext,
) -> Argument:
    value = _normalize_block(entry.value, context)
    if _writes_own_braces(value):
        # The author placed both braces, so TeXFlux emits the text verbatim.
        return Argument(GroupKind.REQUIRED, value, ArgumentLayout.EXPLICIT, entry.span)
    layout = (
        ArgumentLayout.HUGGED
        if entry.spans_one_line
        else ArgumentLayout.BLOCK
    )
    return Argument(GroupKind.REQUIRED, value, layout, entry.span)


def _writes_own_braces(value: Block) -> bool:
    """Whether a value is raw TeX already wrapped in its own balanced braces.

    The author then controls exactly where each brace sits, so the text is
    emitted verbatim instead of being wrapped in a generated pair. Anything
    that does not scan as one balanced group falls back to a generated pair,
    which shows up as a visible extra brace rather than as broken TeX.
    """

    nodes = [
        node
        for node in value.nodes
        if not (isinstance(node, RawTex) and not node.text)
    ]
    if not nodes or not all(isinstance(node, RawTex) for node in nodes):
        return False
    text = "\n".join(node.text for node in nodes).strip()
    if not text.startswith("{"):
        return False
    try:
        end, _ = scan_group(text, 0, span=value.span)
    except ParseError:
        return False
    return end == len(text)


def _sequence_body(suite: Block, context: TransformContext) -> Block:
    """Concatenate every ``-`` value of a sequence suite into one block."""

    nodes: list[CanonicalNode] = []
    for entry in sequence_entries(suite):
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
            for entry in sequence_entries(suite)
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
        entries = sequence_entries(suite)
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

    suite = node.suite

    match node.kind:
        case InvocationKind.COMMAND:
            return (
                GenericInvocation(
                    node.name,
                    arguments + _suite_arguments(node, suite, context),
                    None,
                    node.span,
                ),
            )

        case InvocationKind.ENVIRONMENT:
            return (_normalize_environment(node, arguments, suite, context),)

        case InvocationKind.BRACE:
            match node.groups:
                case (Argument(kind=GroupKind.REQUIRED, value=str() as raw),):
                    return (
                        BraceGroup(
                            _container_body(node, suite, context),
                            node.span,
                            raw,
                        ),
                    )
                case _:
                    raise ValidationError(
                        "literal brace containers require one raw header group",
                        node.span,
                    )

        case InvocationKind.TRANSPARENT:
            if node.groups:
                raise ValidationError(
                    "transparent containers do not accept header groups",
                    node.span,
                )
            return _container_body(node, suite, context).nodes

        case _:
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
        (group for group in node.groups if required_text(group) is None),
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


def _span_from(span: SourceSpan, offset: int) -> SourceSpan:
    """The tail of ``span`` starting ``offset`` columns in.

    A macro template's nodes are retargeted onto their call site, whose span is
    shorter than the raw item text it covers, so an offset derived from that
    text can run past the end. The whole call site is then the best origin
    available, and a span must never come out reversed.
    """

    start = SourcePosition(span.start.line, span.start.column + offset)
    if start > span.end:
        return span
    return SourceSpan(span.file, start, span.end)


def _span_at(span: SourceSpan, offset: int) -> SourceSpan:
    """One column of ``span``, ``offset`` columns in, clamped to its end."""

    start = SourcePosition(span.start.line, span.start.column + offset)
    end = SourcePosition(start.line, start.column + 1)
    if end > span.end:
        return span
    return SourceSpan(span.file, start, end)


def _source_span_at(line: RawTex, text_index: int) -> SourceSpan:
    return _span_at(line.span, text_index)


def _leading_spaces(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _char_at(text: str, index: int) -> str:
    """The character at ``index``, or ``""`` past the end of ``text``."""

    return text[index : index + 1]


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
    """Split one item line into its overlay, label, and first content line."""

    text = line.text
    marker_span = _source_span_at(line, depth)
    if _char_at(text, depth) != "-":
        raise ValidationError("expected an item marker '-'", marker_span)

    cursor = depth + 1
    if _char_at(text, cursor) not in {"", " ", "<", "["}:
        raise ValidationError(
            "item marker must be followed by a space, group, or end",
            marker_span,
        )

    overlay: Argument | None = None
    label: Argument | None = None
    if _char_at(text, cursor) == "<":
        overlay, cursor = _item_group(line, cursor, GroupKind.OVERLAY)
        if _char_at(text, cursor) == "<":
            raise ValidationError("duplicate item overlay prefix", overlay.span)

    if _char_at(text, cursor) == "[":
        label, cursor = _item_group(line, cursor, GroupKind.OPTIONAL)
    elif _char_at(text, cursor) == "]":
        raise ValidationError("invalid item label prefix", marker_span)

    if overlay is None and label is not None and _char_at(text, cursor) == "<":
        raise ValidationError("item label must not precede its overlay", label.span)
    if _char_at(text, cursor) in {"<", "["}:
        raise ValidationError("duplicate item prefix", _source_span_at(line, cursor))

    match _char_at(text, cursor):
        case "":
            pass
        case " ":
            cursor += 1
        case _ if overlay is not None or label is not None:
            raise ValidationError(
                "item prefix must be followed by a space or end",
                marker_span,
            )
        case _:
            raise ValidationError(
                "item marker must be followed by a space, group, or end",
                marker_span,
            )
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
            content.append(RawTex(stripped, _span_from(current.span, depth + 4)))
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
                and _char_at(next_line.text, depth + 4) == "-"
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
        if indent == depth + 4 and _char_at(current.text, depth + 4) == "-":
            nested, index = _nested_itemize(lines, index, depth + 4)
            continuation.append(nested)
            continue
        if indent >= depth + 4 and _char_at(current.text, indent) == "-":
            raise ValidationError(
                "nested item indentation skips a list level",
                current.span,
            )
        if indent < depth + 2:
            raise ValidationError(
                "item continuation requires at least two spaces",
                current.span,
            )
        continuation.append(
            RawTex(
                current.text[depth + 2 :],
                _span_from(current.span, depth + 2),
            )
        )
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
        item_span = _span_from(line.span, depth)
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
        match node:
            case RawTex():
                pass
            case BraceGroup(body=body):
                _assert_canonical_block(body)
            case GenericInvocation(arguments=arguments, body=body):
                for argument in arguments:
                    if isinstance(argument.value, Block):
                        _assert_canonical_block(argument.value)
                if body is not None:
                    _assert_canonical_block(body)
            case Item(continuation=continuation):
                _assert_canonical_block(continuation)
            case _:
                raise TypeError(
                    "normalization produced a non-canonical AST node: "
                    f"{type(node).__name__}"
                )


def normalize(
    document: Document,
    registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
) -> Document:
    """Turn syntax AST into canonical AST, expanding macros and specials."""

    validate_macro_forms(document)
    document = desugar(document)
    document, macros = collect_macros(
        document,
        lambda name: registry.lookup(name) is not None,
    )
    document = expand_macros(document, macros)

    context = TransformContext(registry)
    normalized = Document(_normalize_block(document.body, context), document.span)
    _assert_canonical_block(normalized.body)
    return normalized


__all__ = [
    "BUILTIN_DIRECTIVES",
    "DirectiveRegistry",
    "SpecialHandler",
    "TransformContext",
    "desugar",
    "normalize",
]
