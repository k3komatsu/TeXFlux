"""Syntax-AST normalization and built-in special directives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    CanonicalNode,
    Document,
    GenericInvocation,
    GroupKind,
    Item,
    Node,
    ParsedGeneric,
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


SpecialHandler = Callable[[SpecialInvocation, TransformContext], tuple[CanonicalNode, ...]]


@dataclass(frozen=True, slots=True)
class DirectiveSpec:
    handler: SpecialHandler
    stack_terminal: bool = False


class DirectiveRegistry:
    """The small in-process registry used by normalization."""

    def __init__(self):
        self._specs: dict[str, DirectiveSpec] = {}

    def register(self, name: str, spec: DirectiveSpec) -> None:
        self._specs[name] = spec

    def lookup(self, name: str) -> DirectiveSpec | None:
        return self._specs.get(name)


def _blank(node: Node) -> bool:
    return isinstance(node, RawTex) and node.text == ""


def _normalize_block(block: Block, context: TransformContext) -> Block:
    nodes: list[CanonicalNode] = []
    for node in block.nodes:
        if isinstance(node, RawTex):
            nodes.append(node)
        elif isinstance(node, ParsedGeneric):
            nodes.append(_normalize_generic(node, context))
        elif isinstance(node, SpecialInvocation):
            nodes.extend(_normalize_special(node, context))
        elif isinstance(node, Stack):
            nodes.extend(_normalize_stack(node, context))
        elif isinstance(node, (GenericInvocation, Item)):
            nodes.append(_normalize_canonical(node, context))
        else:  # pragma: no cover - protects the canonical boundary
            raise TypeError(f"unsupported AST node: {type(node).__name__}")
    return Block(tuple(nodes), block.loc)


def _normalize_canonical(node: CanonicalNode, context: TransformContext) -> CanonicalNode:
    if isinstance(node, RawTex):
        return node
    if isinstance(node, GenericInvocation):
        body = None if node.body is None else _normalize_block(node.body, context)
        return GenericInvocation(
            node.name,
            _header_arguments(node.arguments, context),
            body,
            node.loc,
        )
    return Item(
        node.overlay,
        node.label,
        node.first_line,
        _normalize_block(node.continuation, context),
        node.loc,
    )


def _header_arguments(
    arguments: tuple[Argument, ...],
    context: TransformContext,
) -> tuple[Argument, ...]:
    normalized = []
    for argument in arguments:
        if argument.layout is ArgumentLayout.BLOCK and isinstance(argument.value, Block):
            normalized.append(
                Argument(
                    argument.kind,
                    _normalize_block(argument.value, context),
                    argument.layout,
                    argument.loc,
                )
            )
        else:
            normalized.append(argument)
    return tuple(normalized)


_NO_SUITE = object()


def _normalize_generic(
    node: ParsedGeneric,
    context: TransformContext,
    *,
    suite: Block | None | object = _NO_SUITE,
) -> GenericInvocation:
    actual_suite = node.suite if suite is _NO_SUITE else suite
    arguments = _header_arguments(node.groups, context)
    if actual_suite is None:
        return GenericInvocation(node.name, arguments, None, node.loc)

    assert isinstance(actual_suite, Block)
    direct = tuple(child for child in actual_suite.nodes if not _blank(child))
    structured = any(
        isinstance(child, SpecialInvocation) and child.name in {"arg", "body"}
        for child in direct
    )
    if not structured:
        return GenericInvocation(
            node.name,
            arguments,
            _normalize_block(actual_suite, context),
            node.loc,
        )

    long_arguments: list[Argument] = []
    body: Block | None = None
    body_seen = False
    for child in direct:
        if not isinstance(child, SpecialInvocation) or child.name not in {"arg", "body"}:
            raise ValidationError(
                "structured invocation may contain only @!arg or @!body direct children",
                getattr(child, "loc", node.loc),
            )
        if child.name == "arg":
            if body_seen:
                raise ValidationError("@!arg must appear before @!body", child.loc)
            long_arguments.append(_structured_argument(child, context))
            continue

        if body_seen:
            raise ValidationError("@!body may appear only once", child.loc)
        if child.groups or child.suite is None:
            raise ValidationError("@!body requires a block suite and no groups", child.loc)
        body = _normalize_block(child.suite, context)
        body_seen = True

    return GenericInvocation(
        node.name,
        arguments + tuple(long_arguments),
        body,
        node.loc,
    )


def _structured_argument(node: SpecialInvocation, context: TransformContext) -> Argument:
    if node.groups:
        if node.suite is not None:
            raise ValidationError("inline @!arg cannot have a suite", node.loc)
        if len(node.groups) != 1 or node.groups[0].kind is not GroupKind.REQUIRED:
            raise ValidationError(
                "inline @!arg requires exactly one required group",
                node.loc,
            )
        group = node.groups[0]
        return Argument(GroupKind.REQUIRED, group.value, ArgumentLayout.INLINE, group.loc)

    if node.suite is None:
        raise ValidationError("@!arg requires one inline group or a block suite", node.loc)
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
            f"@!{node.name} is valid only as a direct child of a structured generic invocation",
            node.loc,
        )
    spec = context.registry.lookup(node.name)
    if spec is None:
        raise DirectiveError(f"unknown special directive '@!{node.name}'", node.loc)
    result = spec.handler(node, context)
    if not isinstance(result, tuple) or any(
        not isinstance(item, (RawTex, GenericInvocation, Item)) for item in result
    ):
        raise TypeError("special directive handlers must return canonical AST tuples")
    return tuple(_normalize_canonical(item, context) for item in result)


def _assert_canonical_block(block: Block) -> None:
    for node in block.nodes:
        if isinstance(node, RawTex):
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


def _normalize_stack(node: Stack, context: TransformContext) -> tuple[CanonicalNode, ...]:
    if len(node.segments) < 2:
        raise ValidationError("stack requires at least two segments", node.loc)

    final = node.segments[-1]
    if isinstance(final, SpecialInvocation):
        if final.name in {"arg", "body"}:
            raise DirectiveError(
                f"@!{final.name} is valid only as a direct child of a structured generic invocation",
                final.loc,
            )
        spec = context.registry.lookup(final.name)
        if spec is None:
            raise DirectiveError(f"unknown special directive '@!{final.name}'", final.loc)
        if not spec.stack_terminal:
            raise DirectiveError(
                f"@!{final.name} cannot be used as a stack segment",
                final.loc,
            )
        expanded = _normalize_special(
            SpecialInvocation(final.name, final.groups, node.suite, final.loc),
            context,
        )
        if not expanded:
            raise ValidationError("terminal stack directive expanded to no nodes", final.loc)
        tail: CanonicalNode | Block = expanded[0] if len(expanded) == 1 else Block(expanded, final.loc)
    else:
        tail = _normalize_generic(final, context, suite=node.suite)
        if tail.body is None:
            raise ValidationError(
                "the final stack segment must be an environment",
                final.loc,
            )

    for segment in reversed(node.segments[:-1]):
        if not isinstance(segment, ParsedGeneric):
            if segment.name in {"arg", "body"}:
                raise DirectiveError(
                    f"@!{segment.name} is valid only as a direct child of a structured generic invocation",
                    segment.loc,
                )
            spec = context.registry.lookup(segment.name)
            if spec is None:
                raise DirectiveError(f"unknown special directive '@!{segment.name}'", segment.loc)
            raise DirectiveError(
                f"@!{segment.name} is allowed only as the final stack segment",
                segment.loc,
            )
        body = tail if isinstance(tail, Block) else Block((tail,), segment.loc)
        tail = GenericInvocation(
            segment.name,
            _header_arguments(segment.groups, context),
            body,
            segment.loc,
        )

    return (tail,) if isinstance(tail, (RawTex, GenericInvocation, Item)) else tuple(tail.nodes)


def _items_handler(node: SpecialInvocation, context: TransformContext) -> tuple[CanonicalNode, ...]:
    if node.groups or node.suite is None:
        raise ValidationError("@!items requires a nonempty block suite and no groups", node.loc)
    if any(not isinstance(child, RawTex) for child in node.suite.nodes):
        invalid = next(child for child in node.suite.nodes if not isinstance(child, RawTex))
        raise ValidationError("@!items suites may contain raw lines only", invalid.loc)

    raw_lines = tuple(node.suite.nodes)
    if not any(line.text for line in raw_lines):
        raise ValidationError("@!items suite must contain at least one item", node.loc)
    items, index = _parse_item_level(raw_lines, 0, 0)
    if index != len(raw_lines):
        line = raw_lines[index]
        raise ValidationError("invalid @!items indentation", line.loc)
    if not items:
        raise ValidationError("@!items suite must contain at least one item", node.loc)
    body = Block(tuple(items), node.suite.loc)
    return (GenericInvocation("itemize", (), body, node.loc),)


def _next_item_line(lines: tuple[RawTex, ...], index: int) -> int | None:
    while index < len(lines) and lines[index].text == "":
        index += 1
    return None if index == len(lines) else index


def _source_loc_at(line: RawTex, text_index: int, indent: int) -> SourceLocation:
    line_start = line.loc.column - indent
    return SourceLocation(line.loc.file, line.loc.line, line_start + text_index)


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
        raise ValidationError("item marker must be followed by a space, group, or end", marker_loc)

    overlay = None
    label = None
    if cursor < len(text) and text[cursor] == "<":
        group_loc = _source_loc_at(line, cursor, depth)
        end, value = scan_group(text, cursor, loc=group_loc)
        overlay = Argument(GroupKind.OVERLAY, value, ArgumentLayout.INLINE, group_loc)
        cursor = end
        if cursor < len(text) and text[cursor] == "<":
            raise ValidationError("duplicate item overlay prefix", group_loc)

    if cursor < len(text) and text[cursor] == "[":
        group_loc = _source_loc_at(line, cursor, depth)
        end, value = scan_group(text, cursor, loc=group_loc)
        label = Argument(GroupKind.OPTIONAL, value, ArgumentLayout.INLINE, group_loc)
        cursor = end
    elif cursor < len(text) and text[cursor] == "]":
        raise ValidationError("invalid item label prefix", marker_loc)

    if overlay is None and label is not None and cursor < len(text) and text[cursor] == "<":
        raise ValidationError("item label must not precede its overlay", label.loc)
    if cursor < len(text) and text[cursor] in "<[":
        raise ValidationError("duplicate item prefix", _source_loc_at(line, cursor, depth))
    if cursor < len(text) and text[cursor] != " ":
        if overlay is not None or label is not None:
            raise ValidationError("item prefix must be followed by a space or end", marker_loc)
        raise ValidationError("item marker must be followed by a space, group, or end", marker_loc)
    if cursor < len(text) and text[cursor] == " ":
        cursor += 1
    return overlay, label, text[cursor:]


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
            next_indent = len(lines[next_index].text) - len(lines[next_index].text.lstrip(" "))
            if next_indent == depth:
                index = next_index
                break
            if next_indent < depth:
                return items, index
            raise ValidationError("unexpected blank before a nested item", lines[next_index].loc)

        if index >= len(lines):
            return items, index
        line = lines[index]
        indent = len(line.text) - len(line.text.lstrip(" "))
        if indent < depth:
            return items, index
        if indent > depth:
            raise ValidationError("item indentation skips the current list level", line.loc)

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
                next_indent = len(next_line.text) - len(next_line.text.lstrip(" "))
                if next_indent == depth:
                    index = next_index
                    break
                if next_indent < depth:
                    break
                if next_indent == depth + 4 and next_line.text[depth + 4 : depth + 5] == "-":
                    continuation.extend(lines[index:next_index])
                    nested_items, nested_end = _parse_item_level(lines, next_index, depth + 4)
                    nested_loc = nested_items[0].loc if nested_items else next_line.loc
                    continuation.append(
                        GenericInvocation(
                            "itemize",
                            (),
                            Block(tuple(nested_items), nested_loc),
                            nested_loc,
                        )
                    )
                    index = nested_end
                    continue
                if next_indent < depth + 2:
                    raise ValidationError("item continuation requires at least two spaces", next_line.loc)
                continuation.extend(lines[index:next_index])
                index = next_index
                continue

            indent = len(current.text) - len(current.text.lstrip(" "))
            if indent <= depth:
                break
            if indent == depth + 4 and current.text[depth + 4 : depth + 5] == "-":
                nested_items, nested_end = _parse_item_level(lines, index, depth + 4)
                nested_loc = nested_items[0].loc if nested_items else current.loc
                continuation.append(
                    GenericInvocation(
                        "itemize",
                        (),
                        Block(tuple(nested_items), nested_loc),
                        nested_loc,
                    )
                )
                index = nested_end
                continue
            if indent >= depth + 4 and current.text[indent : indent + 1] == "-":
                raise ValidationError("nested item indentation skips a list level", current.loc)
            if indent < depth + 2:
                raise ValidationError("item continuation requires at least two spaces", current.loc)
            continuation.append(
                RawTex(current.text[depth + 2 :], current.loc)
            )
            index += 1

        items.append(Item(overlay, label, first_line, Block(tuple(continuation), item_loc), item_loc))


BUILTIN_DIRECTIVES = DirectiveRegistry()
BUILTIN_DIRECTIVES.register("items", DirectiveSpec(_items_handler, stack_terminal=True))


def normalize(document: Document, registry: DirectiveRegistry = BUILTIN_DIRECTIVES) -> Document:
    """Turn syntax AST into canonical AST and expand built-ins."""

    context = TransformContext(registry)
    normalized = Document(_normalize_block(document.body, context), document.loc)
    _assert_canonical_block(normalized.body)
    return normalized


__all__ = [
    "BUILTIN_DIRECTIVES",
    "DirectiveRegistry",
    "DirectiveSpec",
    "SpecialHandler",
    "TransformContext",
    "normalize",
]
