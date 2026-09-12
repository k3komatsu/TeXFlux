"""Source macros: structural AST-to-AST abstraction over TeXFlux values.

A macro is not a textual substitution. ``!defmacro`` stores one template
block of syntax AST; a call binds its values to parameters and instantiates
that template by cloning it, so raw TeX is never re-parsed and no parameter
is interpolated into a TeX group.

Expansion runs after ``>>`` desugaring and before normalization, so the
renderer never sees a macro. Instantiated template nodes are retargeted onto
the call site, while nodes substituted for a parameter keep the spans they
already carry from the call site.
"""

from __future__ import annotations

from collections.abc import Container, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import re
from typing import Final, TypeAlias

from .ast import (
    Argument,
    Block,
    Document,
    Node,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SourceSpan,
    SpecialInvocation,
    Stack,
    SuiteMode,
    SyntaxNode,
)
from .errors import MacroExpansionError, ValidationError
from .flags import (
    CONDITIONAL_NAMES,
    Conditional,
    Flags,
    evaluate_conditional,
)
from .syntax import demand_text, required_text, sequence_entries, stacks, walk


class Reserved(StrEnum):
    """Special names that belong to the macro system itself."""

    DEFINE = "defmacro"
    PARAM = "param"
    EACH = "each"


#: Reserved names may be neither redefined nor used as macro names.
_RESERVED_NAMES: Final = frozenset(Reserved) | CONDITIONAL_NAMES

# A macro must be callable as '!name', so it uses the special-name grammar.
_MACRO_NAME_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PARAM_NAME_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_REST_PREFIX: Final = "..."

#: One bound macro value: the syntax nodes it contributes where referenced.
Value: TypeAlias = tuple[SyntaxNode, ...]

@dataclass(frozen=True, slots=True)
class MacroParameter:
    name: str
    rest: bool = False


@dataclass(frozen=True, slots=True)
class MacroDefinition:
    """One collected ``!defmacro``, with its definition site for diagnostics."""

    name: str
    parameters: tuple[MacroParameter, ...]
    template: Block
    span: SourceSpan

    @property
    def rest(self) -> MacroParameter | None:
        """The trailing rest parameter, when this macro is variadic."""

        return next((p for p in self.parameters[-1:] if p.rest), None)

    @property
    def required(self) -> int:
        """How many values the non-rest parameters consume."""

        return len(self.parameters) - (1 if self.rest is not None else 0)

    def signature(self) -> str:
        """The expected value shape, for arity diagnostics."""

        shape = "".join(
            f"{{...{parameter.name}}}" if parameter.rest else f"{{{parameter.name}}}"
            for parameter in self.parameters
        )
        bound = "at least" if self.rest is not None else "exactly"
        return f"{shape or '(no parameters)'}, {bound} {self.required} value(s)"


# --------------------------------------------------------------------------
# Definition collection
# --------------------------------------------------------------------------


def _parameters(groups: tuple[Argument, ...]) -> tuple[MacroParameter, ...]:
    parameters: list[MacroParameter] = []
    seen: set[str] = set()
    for group in groups:
        text = demand_text(group, "macro parameter")
        rest = text.startswith(_REST_PREFIX)
        name = text.removeprefix(_REST_PREFIX) if rest else text
        if _PARAM_NAME_RE.fullmatch(name) is None:
            raise ValidationError(
                f"invalid macro parameter name '{text}'",
                group.span,
            )
        if name in seen:
            raise ValidationError(f"duplicate macro parameter '{name}'", group.span)
        if parameters and parameters[-1].rest:
            raise ValidationError(
                "a rest parameter must be the last macro parameter",
                group.span,
            )
        seen.add(name)
        parameters.append(MacroParameter(name, rest))
    return tuple(parameters)


def _definition(
    node: SpecialInvocation,
    builtins: Container[str],
    defined: Mapping[str, MacroDefinition],
) -> MacroDefinition:
    if node.suite is None or node.suite_mode is not SuiteMode.BLOCK:
        raise ValidationError(
            "!defmacro requires a ': |' template suite",
            node.span,
        )
    if not node.groups:
        raise ValidationError("!defmacro requires a macro name group", node.span)

    name_group = node.groups[0]
    name = demand_text(name_group, "!defmacro name")
    if _MACRO_NAME_RE.fullmatch(name) is None:
        raise ValidationError(f"invalid macro name '{name}'", name_group.span)
    if name in _RESERVED_NAMES:
        raise ValidationError(f"'!{name}' is reserved by TeXFlux", name_group.span)
    if name in builtins:
        raise ValidationError(
            f"'!{name}' is a built-in special and cannot be redefined",
            name_group.span,
        )
    if name in defined:
        previous = defined[name].span.location
        raise ValidationError(
            f"macro '!{name}' is already defined at {previous}",
            name_group.span,
        )
    nested = next(
        (
            child
            for child in walk(node.suite)
            if isinstance(child, SpecialInvocation)
            and child.name == Reserved.DEFINE
        ),
        None,
    )
    if nested is not None:
        raise ValidationError(
            "!defmacro is only valid at the top level",
            nested.span,
        )
    return MacroDefinition(name, _parameters(node.groups[1:]), node.suite, node.span)


def validate_macro_forms(document: Document) -> None:
    """Reject a template construct whose ``: |`` suite is not actually written.

    Stack desugaring hands every segment but the rightmost a synthetic block
    suite that no later pass can tell from a written one, so this runs on the
    syntax AST before ``>>`` is resolved. The rightmost segment keeps the
    stack's own suffix, so ``@{\\bfseries} >> !each{items}{item}: |`` owns a
    real template and stays legal.
    """

    for stack in stacks(document.body):
        for index, segment in enumerate(stack.segments):
            if not isinstance(segment, SpecialInvocation):
                continue
            if segment.name == Reserved.DEFINE:
                # Composing a definition would nest it under the segments to
                # its left, and a definition must be a top-level statement.
                raise ValidationError(
                    "!defmacro must be a top-level ': |' definition and "
                    "cannot be a '>>' segment",
                    segment.span,
                )
            writes_own_suite = (
                index == len(stack.segments) - 1
                and stack.suite_mode is SuiteMode.BLOCK
            )
            if segment.name == Reserved.EACH and not writes_own_suite:
                raise ValidationError(
                    "!each requires a ': |' template suite of its own",
                    segment.span,
                )


def collect_macros(
    document: Document,
    builtins: Container[str],
) -> tuple[Document, dict[str, MacroDefinition]]:
    """Strip top-level ``!defmacro`` nodes and validate their signatures.

    Definitions emit no TeX. Collecting every definition before expanding any
    call is what makes forward references work.
    """

    macros: dict[str, MacroDefinition] = {}
    nodes: list[Node] = []
    for node in document.body.nodes:
        if isinstance(node, SpecialInvocation) and node.name == Reserved.DEFINE:
            definition = _definition(node, builtins, macros)
            macros[definition.name] = definition
            continue
        nodes.append(node)
    body = replace(document.body, nodes=tuple(nodes))
    return replace(document, body=body), macros


# --------------------------------------------------------------------------
# Expansion
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Frame:
    """One macro template instantiation and the call site it maps onto."""

    macro: MacroDefinition
    call_span: SourceSpan
    values: Mapping[str, Value]
    sequences: Mapping[str, tuple[Value, ...]]
    chain: tuple[str, ...]

    def bound(self, name: str, value: Value) -> "_Frame":
        """Derive a frame carrying one more single value, for ``!each``."""

        return replace(self, values={**self.values, name: value})

    def knows(self, name: str) -> bool:
        return name in self.values or name in self.sequences

    def where(self) -> str:
        """Locate this expansion: its chain and the call site it came from."""

        chain = " -> ".join(self.chain)
        return f"while expanding '{chain}' called at {self.call_span.location}"


def _error(
    message: str,
    span: SourceSpan,
    frame: _Frame | None,
) -> MacroExpansionError:
    """Build a diagnostic that names the expansion chain when inside one."""

    if frame is not None:
        message = f"{message}; {frame.where()}"
    return MacroExpansionError(message, span)


class _Expander:
    """Rewrite macro calls, conditionals and template constructs away."""

    def __init__(self, macros: Mapping[str, MacroDefinition], flags: Flags):
        self._macros = macros
        self._flags = flags

    def document(self, document: Document) -> Document:
        return replace(document, body=self.block(document.body, None))

    def block(self, block: Block, frame: _Frame | None) -> Block:
        nodes: list[Node] = []
        for node in block.nodes:
            nodes.extend(self.node(node, frame))
        return replace(
            block,
            nodes=tuple(nodes),
            span=self._span(block.span, frame),
        )

    def node(self, node: Node, frame: _Frame | None) -> tuple[Node, ...]:
        match node:
            case SpecialInvocation(name=Reserved.DEFINE):
                raise ValidationError(
                    "!defmacro is only valid at the top level",
                    node.span,
                )
            case SpecialInvocation(name=Reserved.PARAM):
                return self._param(node, frame)
            case SpecialInvocation(name=Reserved.EACH):
                return self._each(node, frame)
            case SpecialInvocation(name=Conditional.WHEN | Conditional.UNLESS):
                return self._conditional(node, frame)
            case SpecialInvocation(name=name) if name in self._macros:
                return self._call(node, self._macros[name], frame)
            case ParsedInvocation() | SpecialInvocation():
                return (self._invocation(node, frame),)
            case SequenceEntry():
                return (self._entry(node, frame),)
            case RawTex():
                return (replace(node, span=self._span(node.span, frame)),)
            case Stack():
                raise TypeError("macro expansion requires a desugared syntax AST")
            case _:
                raise TypeError(f"unsupported AST node: {type(node).__name__}")

    # -- span retargeting --------------------------------------------------

    def _span(self, span: SourceSpan, frame: _Frame | None) -> SourceSpan:
        """Template scaffolding points at the call site, not the definition."""

        return span if frame is None else frame.call_span

    def _invocation(
        self,
        node: ParsedInvocation | SpecialInvocation,
        frame: _Frame | None,
    ) -> ParsedInvocation | SpecialInvocation:
        return replace(
            node,
            groups=tuple(self._argument(group, frame) for group in node.groups),
            suite=None if node.suite is None else self.block(node.suite, frame),
            span=self._span(node.span, frame),
            suite_span=(
                None
                if node.suite_span is None
                else self._span(node.suite_span, frame)
            ),
        )

    def _argument(self, argument: Argument, frame: _Frame | None) -> Argument:
        value = argument.value
        if isinstance(value, Block):
            value = self.block(value, frame)
        return replace(argument, value=value, span=self._span(argument.span, frame))

    def _entry(self, node: SequenceEntry, frame: _Frame | None) -> SequenceEntry:
        return replace(
            node,
            value=self.block(node.value, frame),
            marker_span=self._span(node.marker_span, frame),
            span=self._span(node.span, frame),
        )

    # -- template constructs ----------------------------------------------

    def _single_name(
        self,
        node: SpecialInvocation,
        label: str,
        count: int,
    ) -> tuple[str, ...]:
        if len(node.groups) != count:
            raise ValidationError(
                f"{label} requires exactly {count} required group(s)",
                node.span,
            )
        return tuple(
            demand_text(group, f"{label} name") for group in node.groups
        )

    def _template_frame(
        self,
        node: SpecialInvocation,
        frame: _Frame | None,
    ) -> _Frame:
        """The frame a template-only construct needs to read its bindings."""

        if frame is None:
            raise MacroExpansionError(
                f"!{node.name} is only valid inside a macro template",
                node.span,
            )
        return frame

    def _param(
        self,
        node: SpecialInvocation,
        frame: _Frame | None,
    ) -> tuple[Node, ...]:
        frame = self._template_frame(node, frame)
        if node.suite is not None:
            raise ValidationError("!param does not accept a suite", node.span)
        (name,) = self._single_name(node, "!param", 1)

        if name in frame.sequences:
            raise _error(
                f"'{name}' is a rest parameter; use !each to expand it",
                node.span,
                frame,
            )
        if name not in frame.values:
            raise _error(f"unknown macro parameter '{name}'", node.span, frame)
        # The bound value already carries call-site spans, so it is spliced
        # in unchanged rather than retargeted.
        return frame.values[name]

    def _each(
        self,
        node: SpecialInvocation,
        frame: _Frame | None,
    ) -> tuple[Node, ...]:
        frame = self._template_frame(node, frame)
        if node.suite is None or node.suite_mode is not SuiteMode.BLOCK:
            raise ValidationError("!each requires a ': |' template suite", node.span)
        sequence_name, item_name = self._single_name(node, "!each", 2)
        if _PARAM_NAME_RE.fullmatch(item_name) is None:
            raise ValidationError(
                f"invalid !each item name '{item_name}'",
                node.groups[1].span,
            )

        if sequence_name in frame.values:
            raise _error(
                f"'{sequence_name}' is not a rest parameter; !each needs one",
                node.span,
                frame,
            )
        if sequence_name not in frame.sequences:
            raise _error(
                f"unknown macro parameter '{sequence_name}'",
                node.span,
                frame,
            )
        if frame.knows(item_name):
            raise _error(
                f"!each item '{item_name}' shadows a bound macro parameter",
                node.span,
                frame,
            )

        nodes: list[Node] = []
        for value in frame.sequences[sequence_name]:
            iteration = self.block(node.suite, frame.bound(item_name, value))
            nodes.extend(iteration.nodes)
        return tuple(nodes)

    # -- conditionals ------------------------------------------------------

    def _conditional(
        self,
        node: SpecialInvocation,
        frame: _Frame | None,
    ) -> tuple[Node, ...]:
        """Keep or drop one ``!when``/``!unless`` payload, whole."""

        keep = evaluate_conditional(node, self._flags)
        if node.suite is None:
            raise ValidationError(
                f"!{node.name} requires a ': |' suite or a '>>' payload",
                node.span,
            )
        if node.suite_mode is not SuiteMode.BLOCK:
            raise ValidationError(
                f"!{node.name} does not accept a ':' sequence suite",
                node.span,
            )

        if not keep:
            # A dropped payload is never expanded, so an author can disable
            # content that no longer compiles and still build the document.
            return ()
        return self.block(node.suite, frame).nodes

    # -- calls -------------------------------------------------------------

    def _call(
        self,
        node: SpecialInvocation,
        macro: MacroDefinition,
        frame: _Frame | None,
    ) -> tuple[Node, ...]:
        outer = () if frame is None else frame.chain
        chain = outer + (macro.name,)
        if macro.name in outer:
            raise _error(
                "recursive macro expansion detected: " + " -> ".join(chain),
                node.span,
                frame,
            )

        values = self._values(node, frame)
        call_span = self._span(node.span, frame)
        inner = _Frame(
            macro,
            call_span,
            *self._bind(node, macro, values, frame),
            chain,
        )
        return self.block(macro.template, inner).nodes

    def _values(
        self,
        node: SpecialInvocation,
        frame: _Frame | None,
    ) -> tuple[Value, ...]:
        """Read a call's values: compact groups first, then its suite."""

        values: list[Value] = []
        for group in node.groups:
            text = required_text(group)
            if text is None:
                raise _error(
                    "macro calls accept required '{...}' values only",
                    group.span,
                    frame,
                )
            values.append((RawTex(text, self._span(group.span, frame)),))

        if node.suite is not None:
            suite = self.block(node.suite, frame)
            if node.suite_mode is SuiteMode.SEQUENCE:
                values.extend(
                    entry.value.nodes for entry in sequence_entries(suite)
                )
            else:
                values.append(suite.nodes)
        return tuple(values)

    def _bind(
        self,
        node: SpecialInvocation,
        macro: MacroDefinition,
        values: tuple[Value, ...],
        frame: _Frame | None,
    ) -> tuple[dict[str, Value], dict[str, tuple[Value, ...]]]:
        rest = macro.rest
        required = macro.required
        if len(values) < required or (rest is None and len(values) != required):
            raise _error(
                f"'!{macro.name}' expects {macro.signature()}, "
                f"got {len(values)}",
                node.span,
                frame,
            )
        bound = {
            parameter.name: value
            for parameter, value in zip(macro.parameters[:required], values)
        }
        sequences = {rest.name: values[required:]} if rest is not None else {}
        return bound, sequences


def expand_macros(
    document: Document,
    macros: Mapping[str, MacroDefinition],
    flags: Flags,
) -> Document:
    """Expand macro calls and resolve conditionals in one document.

    ``document`` must already be desugared, as ``normalize`` arranges: a
    surviving ``>>`` stack would hide the single block value a call binds and
    the single payload a conditional keeps or drops.
    """

    return _Expander(macros, flags).document(document)


__all__ = [
    "MacroDefinition",
    "MacroParameter",
    "Reserved",
    "Value",
    "collect_macros",
    "expand_macros",
]
