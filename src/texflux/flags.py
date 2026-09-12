"""Build flags: one source, several versions of the same document.

A flag is a boolean that ``!flag`` declares with a default and the compile
command may override. ``!when`` and ``!unless`` keep or drop whole statements
according to one flag, or to several folded by an ``[and]``/``[or]`` modifier.

That flat fold is the whole conditional language. A fold admits no
parentheses, no fold inside a fold, and no negation of one of its operands.
Composing whole conditionals with ``>>`` is unrestricted, and together with
``!unless`` it is how anything deeper than a single fold is written.

Declarations are resolved before macro expansion, so the expander can drop a
disabled branch without ever looking inside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from enum import StrEnum
import re
from typing import Final, TypeAlias

from .ast import (
    Argument,
    Document,
    Node,
    SourceSpan,
    SpecialInvocation,
)
from .errors import FlagError, ValidationError
from .syntax import demand_text, optional_text, stacks, walk


class Conditional(StrEnum):
    """Special names that belong to the build-flag system itself."""

    DECLARE = "flag"
    WHEN = "when"
    UNLESS = "unless"


class Combinator(StrEnum):
    """How a conditional folds the several flags it names into one answer."""

    ALL = "and"
    ANY = "or"


#: Reserved alongside the macro constructs, so no macro may be named after
#: one. Flag names live in their own namespace and are not restricted.
CONDITIONAL_NAMES: Final = frozenset(Conditional)

#: The modifiers a conditional accepts, spelled as an optional group.
_COMBINATORS: Final = frozenset(Combinator)

# A flag is written in a group and on a command line, so it stays identifier
# shaped with the hyphen a command line tends to want. Import bindings name
# flags too, so the shape is published rather than spelled twice.
FLAG_NAME_PATTERN: Final = r"[A-Za-z][A-Za-z0-9_-]*"
_FLAG_NAME_RE: Final = re.compile(FLAG_NAME_PATTERN)

#: The only two spellings a declaration or an override accepts.
FLAG_VALUES: Final = {"on": True, "off": False}

#: Resolved flags: every declared name, mapped to the value this build uses.
Flags: TypeAlias = Mapping[str, bool]


def declared_flags_hint(flags: Flags) -> str:
    """Name the flags a document declares, for an 'unknown flag' diagnostic."""

    if not flags:
        return "this document declares no flags"
    return "declared flags are: " + ", ".join(sorted(flags))


def _combinator(
    node: SpecialInvocation,
) -> tuple[Combinator | None, tuple[Argument, ...]]:
    """Split an ``[and]``/``[or]`` modifier off the front of a header.

    Only the leading group can be the modifier. A misplaced optional group is
    left in the flag list, where reading it as a name reports it at its own
    span rather than blaming the header as a whole.
    """

    if not node.groups:
        return None, ()
    text = optional_text(node.groups[0])
    if text is None:
        return None, node.groups
    modifier = text.strip()
    if modifier not in _COMBINATORS:
        raise ValidationError(
            f"!{node.name} modifier must be '[and]' or '[or]', "
            f"got '[{modifier}]'",
            node.groups[0].span,
        )
    return Combinator(modifier), node.groups[1:]


def _flag_names(
    node: SpecialInvocation,
    groups: tuple[Argument, ...],
    flags: Flags,
) -> list[str]:
    """Read the declared flag names one conditional header combines."""

    names: list[str] = []
    for group in groups:
        name = demand_text(group, f"!{node.name} flag")
        if name not in flags:
            raise ValidationError(
                f"unknown build flag '{name}'; {declared_flags_hint(flags)}",
                group.span,
            )
        if name in names:
            raise ValidationError(
                f"build flag '{name}' is listed twice",
                group.span,
            )
        names.append(name)
    return names


def evaluate_conditional(node: SpecialInvocation, flags: Flags) -> bool:
    """Whether one ``!when``/``!unless`` header keeps its payload.

    ``!unless`` is the negation of the whole ``!when`` it mirrors, so
    ``!unless[or]{a}{b}`` keeps its payload only while neither flag is on.
    """

    modifier, groups = _combinator(node)
    if not groups:
        raise ValidationError(
            f"!{node.name} requires at least one '{{flag}}' group",
            node.span,
        )
    names = _flag_names(node, groups, flags)
    if modifier is None and len(names) > 1:
        raise ValidationError(
            f"!{node.name} requires an '[and]' or '[or]' modifier to combine "
            f"{len(names)} flags",
            node.span,
        )

    fold = any if modifier is Combinator.ANY else all
    matched = fold(flags[name] for name in names)
    return matched if node.name == Conditional.WHEN else not matched


def validate_flag_forms(document: Document) -> None:
    """Reject a ``!flag`` declaration written as a ``>>`` segment.

    Stack desugaring would nest the declaration under the segments to its
    left, where it is no longer the top-level statement a declaration has to
    be, so this runs on the syntax AST while the stack is still visible.
    """

    for stack in stacks(document.body):
        for segment in stack.segments:
            if (
                isinstance(segment, SpecialInvocation)
                and segment.name == Conditional.DECLARE
            ):
                raise ValidationError(
                    "!flag must be a top-level declaration and cannot be a "
                    "'>>' segment",
                    segment.span,
                )


def _declaration(
    node: SpecialInvocation,
    declared: Mapping[str, SourceSpan],
) -> tuple[str, bool]:
    """Read one ``!flag{name}{on|off}`` declaration."""

    if node.suite is not None:
        raise ValidationError("!flag does not accept a suite", node.span)
    if len(node.groups) != 2:
        raise ValidationError(
            "!flag requires a name group and an 'on' or 'off' default group",
            node.span,
        )

    name = demand_text(node.groups[0], "!flag name")
    if _FLAG_NAME_RE.fullmatch(name) is None:
        raise ValidationError(
            f"invalid build flag name '{name}'",
            node.groups[0].span,
        )
    if name in declared:
        raise ValidationError(
            f"build flag '{name}' is already declared at "
            f"{declared[name].location}",
            node.groups[0].span,
        )

    default = demand_text(node.groups[1], "!flag default")
    if default not in FLAG_VALUES:
        raise ValidationError(
            f"!flag default must be 'on' or 'off', got '{default}'",
            node.groups[1].span,
        )
    return name, FLAG_VALUES[default]


def collect_flags(
    document: Document,
    overrides: Flags | None = None,
) -> tuple[Document, dict[str, bool]]:
    """Strip top-level ``!flag`` declarations and resolve this build's values.

    Every declaration is collected before any override is applied and before
    any ``!when`` is read, so a conditional may precede the declaration it
    names and an override may name a flag declared anywhere in the document.

    Raises ``FlagError`` for an override that names no declaration or whose
    value is not a ``bool``; ``FLAG_VALUES`` converts the on/off spellings.
    """

    declared: dict[str, SourceSpan] = {}
    flags: dict[str, bool] = {}
    nodes: list[Node] = []
    for node in document.body.nodes:
        if (
            isinstance(node, SpecialInvocation)
            and node.name == Conditional.DECLARE
        ):
            name, default = _declaration(node, declared)
            declared[name] = node.span
            flags[name] = default
            continue
        nodes.append(node)

    body = replace(document.body, nodes=tuple(nodes))
    # Every top-level declaration is gone, so anything left is nested.
    misplaced = next(
        (
            child
            for child in walk(body)
            if isinstance(child, SpecialInvocation)
            and child.name == Conditional.DECLARE
        ),
        None,
    )
    if misplaced is not None:
        raise ValidationError(
            "!flag is only valid at the top level",
            misplaced.span,
        )

    for name, value in (overrides or {}).items():
        if name not in flags:
            raise FlagError(
                f"unknown build flag '{name}'; {declared_flags_hint(flags)}"
            )
        if not isinstance(value, bool):
            # 'off' and 0 are both truthy-adjacent enough to silently build
            # the opposite document, so only a real bool is accepted here.
            raise FlagError(
                f"build flag '{name}' must be True or False, got {value!r}; "
                "FLAG_VALUES converts the 'on'/'off' spellings"
            )
        flags[name] = value
    return replace(document, body=body), flags


__all__ = [
    "CONDITIONAL_NAMES",
    "FLAG_NAME_PATTERN",
    "Combinator",
    "Conditional",
    "FLAG_VALUES",
    "Flags",
    "collect_flags",
    "declared_flags_hint",
    "evaluate_conditional",
    "validate_flag_forms",
]
