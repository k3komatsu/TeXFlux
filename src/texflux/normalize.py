"""Syntax-AST normalization: value consumption and the remaining specials.

Flow control is not here. ``!before``, ``!after``, ``!around``, ``!off`` and
``!drop`` are ordinary source macros, defined in the bundled ``prelude.tfxm``
and expanded away by the macro pass, so the registry below holds nothing but
the two module constructs -- which only name themselves for a caller that
bypassed the compilation session.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Final, TypeAlias

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
    Node,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SourceSpan,
    SpecialInvocation,
    Stack,
    SuiteMode,
)
from .errors import DirectiveError, ModuleError, ValidationError
from .flags import Flags, collect_flags, validate_flag_forms
from .macros import collect_macros, expand_macros, validate_macro_forms
from .syntax import map_children, sequence_entries


#: The in-process special registry: one handler per name. A reusable
#: operation expressible as a source macro does not get an entry here.
DirectiveRegistry: TypeAlias = dict[str, "SpecialHandler"]

SpecialHandler: TypeAlias = Callable[
    [SpecialInvocation, DirectiveRegistry],
    tuple[CanonicalNode, ...],
]


_CANONICAL_TYPES = (RawTex, GenericInvocation, BraceGroup)


def _desugar_stack(
    node: Stack,
) -> ParsedInvocation | SpecialInvocation:
    if len(node.segments) < 2:
        raise ValidationError(
            "stack requires at least two segments",
            node.span,
            code="V034",
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
        map_children(
            _desugar_stack(node) if isinstance(node, Stack) else node,
            _desugar_tree,
        )
        for node in block.nodes
    )
    return replace(block, nodes=nodes)


def _normalize_block(block: Block, registry: DirectiveRegistry) -> Block:
    nodes: list[CanonicalNode] = []
    for node in block.nodes:
        nodes.extend(_normalize_node(node, registry))
    return Block(tuple(nodes), block.span)


def _normalize_node(
    node: Node,
    registry: DirectiveRegistry,
) -> tuple[CanonicalNode, ...]:
    match node:
        case RawTex():
            return (node,)
        case ParsedInvocation():
            return _normalize_invocation(node, registry)
        case SpecialInvocation():
            return _normalize_special(node, registry)
        case SequenceEntry():
            raise ValidationError(
                "sequence entries are only valid inside a ':::' suite",
                node.span,
                code="V035",
            )
        case GenericInvocation() | BraceGroup():
            return (_normalize_canonical(node, registry),)
        case _:
            raise TypeError(f"unsupported AST node: {type(node).__name__}")


def _normalize_canonical(
    node: CanonicalNode,
    registry: DirectiveRegistry,
) -> CanonicalNode:
    match node:
        case RawTex():
            return node
        case GenericInvocation(arguments=arguments, body=body):
            return replace(
                node,
                arguments=_header_arguments(arguments, registry),
                body=None if body is None else _normalize_block(body, registry),
            )
        case BraceGroup(body=body):
            return replace(node, body=_normalize_block(body, registry))
        case _:
            raise TypeError(f"unsupported canonical node: {type(node).__name__}")


def _header_arguments(
    arguments: tuple[Argument, ...],
    registry: DirectiveRegistry,
) -> tuple[Argument, ...]:
    """Normalize each header group, leaving inline raw text untouched."""

    return tuple(
        replace(group, value=_normalize_block(group.value, registry))
        if isinstance(group.value, Block)
        else group
        for group in arguments
    )


def _suite_mode(node: ParsedInvocation | SpecialInvocation) -> SuiteMode:
    """Read a suite mode, applying the block default to hand-built AST nodes."""

    return node.suite_mode or SuiteMode.BLOCK


def _argument_from_entry(
    entry: SequenceEntry,
    registry: DirectiveRegistry,
) -> Argument:
    value = _normalize_block(entry.value, registry)
    if entry.argument_kind is not None:
        return Argument(
            entry.argument_kind,
            value,
            ArgumentLayout.EXPLICIT,
            entry.span,
        )
    # A '-' always generates one required group, laid out the one way a
    # generated group is laid out. The value's shape decides nothing.
    return Argument(GroupKind.REQUIRED, value, ArgumentLayout.BLOCK, entry.span)


def _sequence_body(suite: Block, registry: DirectiveRegistry) -> Block:
    """Concatenate every generated-value entry of a sequence suite."""

    nodes: list[CanonicalNode] = []
    for entry in sequence_entries(suite):
        if entry.argument_kind is not None:
            raise ValidationError(
                "anonymous containers do not accept '+' sequence entries",
                entry.span,
                code="V036",
            )
        nodes.extend(_normalize_block(entry.value, registry).nodes)
    return Block(tuple(nodes), suite.span)


def _container_body(
    node: ParsedInvocation,
    suite: Block,
    registry: DirectiveRegistry,
) -> Block:
    """Flatten a container suite; either mode contributes one block value."""

    if _suite_mode(node) is SuiteMode.BLOCK:
        return _normalize_block(suite, registry)
    return _sequence_body(suite, registry)


def _suite_arguments(
    node: ParsedInvocation,
    suite: Block,
    registry: DirectiveRegistry,
) -> tuple[Argument, ...]:
    """Convert a command suite into its required block arguments."""

    if _suite_mode(node) is SuiteMode.SEQUENCE:
        return tuple(
            _argument_from_entry(entry, registry)
            for entry in sequence_entries(suite)
        )
    return (
        Argument(
            GroupKind.REQUIRED,
            _normalize_block(suite, registry),
            ArgumentLayout.BLOCK,
            node.suite_span or node.span,
        ),
    )


def _normalize_environment(
    node: ParsedInvocation,
    arguments: tuple[Argument, ...],
    suite: Block,
    registry: DirectiveRegistry,
) -> GenericInvocation:
    """A block suite is the body; a sequence suite ends with it."""

    if _suite_mode(node) is SuiteMode.BLOCK:
        body = _normalize_block(suite, registry)
    else:
        entries = sequence_entries(suite)
        if not entries:
            raise ValidationError(
                "environment sequence suites require a body value",
                node.span,
                code="V037",
            )
        if entries[-1].argument_kind is not None:
            raise ValidationError(
                "environment sequence suites require the final '-' entry to be the body",
                entries[-1].span,
                code="V038",
            )
        arguments += tuple(
            _argument_from_entry(entry, registry) for entry in entries[:-1]
        )
        body = _normalize_block(entries[-1].value, registry)
    return GenericInvocation(node.name, arguments, body, node.span)


def _normalize_invocation(
    node: ParsedInvocation,
    registry: DirectiveRegistry,
) -> tuple[CanonicalNode, ...]:
    arguments = _header_arguments(node.groups, registry)
    if node.suite is None:
        if node.kind is InvocationKind.COMMAND:
            return (GenericInvocation(node.name, arguments, None, node.span),)
        raise ValidationError(
            "container values require a suite or a closed stack payload",
            node.span,
            code="V039",
        )

    suite = node.suite

    match node.kind:
        case InvocationKind.COMMAND:
            return (
                GenericInvocation(
                    node.name,
                    arguments + _suite_arguments(node, suite, registry),
                    None,
                    node.span,
                ),
            )

        case InvocationKind.ENVIRONMENT:
            return (_normalize_environment(node, arguments, suite, registry),)

        case InvocationKind.BRACE:
            match node.groups:
                case (Argument(kind=GroupKind.REQUIRED, value=str() as raw),):
                    return (
                        BraceGroup(
                            _container_body(node, suite, registry),
                            node.span,
                            raw,
                            node.groups[0].parts,
                        ),
                    )
                case _:
                    raise ValidationError(
                        "literal brace containers require one raw header group",
                        node.span,
                        code="V040",
                    )

        case InvocationKind.TRANSPARENT:
            if node.groups:
                raise ValidationError(
                    "transparent containers do not accept header groups",
                    node.span,
                    code="V041",
                )
            return _container_body(node, suite, registry).nodes

        case _:
            raise TypeError(f"unsupported invocation kind: {node.kind}")


def _normalize_special(
    node: SpecialInvocation,
    registry: DirectiveRegistry,
) -> tuple[CanonicalNode, ...]:
    handler = registry.get(node.name)
    if handler is None:
        raise DirectiveError(
            f"unknown special directive '!{node.name}'; a literal '!' line is "
            "written '!!', and a run of them goes in a raw-mode region",
            node.span,
            code="D001",
        )
    result = handler(node, registry)
    if not isinstance(result, tuple) or any(
        not isinstance(item, _CANONICAL_TYPES) for item in result
    ):
        raise TypeError(
            "special directive handlers must return canonical AST tuples"
        )
    return tuple(_normalize_canonical(item, registry) for item in result)


def _module_guard(name: str) -> SpecialHandler:
    """Reject a module construct that reached the import-free pipeline.

    ``normalize`` has no source file to resolve an import against, so the two
    module constructs are registered only to name themselves here. Registering
    them also reserves both names against ``!defmacro``.
    """

    def handler(
        node: SpecialInvocation,
        _registry: DirectiveRegistry,
    ) -> tuple[CanonicalNode, ...]:
        raise ModuleError(
            f"'!{name}' requires module compilation; use "
            "texflux.compile_with_map or the texflux CLI",
            node.span,
            code="M029",
        )

    return handler


#: Every remaining built-in: the two module constructs, which only name
#: themselves here. Flow control is written as ordinary macros, which the
#: compilation session seeds from its bundled standard module.
BUILTIN_DIRECTIVES: Final[DirectiveRegistry] = {
    "import": _module_guard("import"),
    "macroimport": _module_guard("macroimport"),
}


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
            case _:
                raise TypeError(
                    "normalization produced a non-canonical AST node: "
                    f"{type(node).__name__}"
                )


def normalize(
    document: Document,
    registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
    *,
    flags: Flags | None = None,
) -> Document:
    """Turn syntax AST into canonical AST, expanding macros and specials.

    ``flags`` overrides the defaults the document's ``!flag`` declarations
    give. An override raises ``FlagError`` unless it names a declared flag and
    carries a real ``bool``, so a typo or a stray ``"off"`` cannot quietly
    build the other version of the document.

    This is the low-level pass, so its macro environment is exactly what
    ``document`` defines: the standard flow macros belong to a compilation
    session, and a source calling ``!before`` here fails as an unknown
    special. Full-language compilation goes through ``compile_text``,
    ``compile_with_map``, ``compile_ast`` or the CLI.
    """

    validate_macro_forms(document)
    validate_flag_forms(document)
    document = desugar(document)
    document, resolved = collect_flags(document, flags)
    document, macros = collect_macros(document, registry)
    document = expand_macros(document, macros, resolved)

    return canonicalize(document, registry)


def canonicalize(
    document: Document,
    registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
) -> Document:
    """Turn one expanded syntax AST into validated canonical AST.

    This is the tail ``normalize`` shares with the module pipeline, which
    splices imported canonical AST in between expansion and this pass.
    """

    normalized = Document(_normalize_block(document.body, registry), document.span)
    _assert_canonical_block(normalized.body)
    return normalized


__all__ = [
    "BUILTIN_DIRECTIVES",
    "DirectiveRegistry",
    "SpecialHandler",
    "canonicalize",
    "desugar",
    "normalize",
]
