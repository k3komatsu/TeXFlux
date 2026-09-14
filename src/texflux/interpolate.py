"""Interpolate opaque text fields without parsing or rescanning inserted text."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ast import RawTex, SourcePosition, SourceSpan, SourceText, TextFragment
from .errors import MacroExpansionError
from .syntax import is_escaped

if TYPE_CHECKING:
    from .macros import _Frame


def marker_span(origin: SourceSpan, offset: int, length: int) -> SourceSpan:
    """One range of origin, falling back to origin if it would exceed its end."""
    start = SourcePosition(origin.start.line, origin.start.column + offset)
    end = SourcePosition(start.line, start.column + length)
    if end > origin.end:
        return origin
    return SourceSpan(origin.file, start, end)


def text_value(name: str, frame: _Frame) -> SourceText:
    """Read one bound parameter as text; the caller supplies the hole's span."""
    if name in frame.sequences:
        raise MacroExpansionError(
            f"'{name}' is a rest parameter; use !each to access its values",
            frame.call_span,
            code="E001",
        )
    if name not in frame.values:
        raise MacroExpansionError(
            f"unknown macro parameter '{name}'",
            frame.call_span,
            code="E002",
        )
    value = frame.values[name]
    if len(value) != 1 or not isinstance(value[0], RawTex):
        raise MacroExpansionError(
            f"macro parameter '{name}' is not a text value; "
            "use !param for structural values",
            frame.call_span,
            code="E003",
        )
    node = value[0]
    if node.parts is not None:
        return node.parts
    return (TextFragment(node.text, node.span),)


def reject_markers(text: str, span: SourceSpan, where: str) -> None:
    """Compiler metadata is static, including escaped marker spellings."""
    if "!text{" in text or "!param{" in text:
        raise MacroExpansionError(
            f"interpolation is not allowed in {where}",
            span,
            code="E004",
        )


def interpolate(
    text: str,
    *,
    origin: SourceSpan,
    target: SourceSpan,
    offset: int,
    lookup: _Frame | None,
) -> SourceText | None:
    """Interpolate one field, or return None when it has no marker or escape."""
    if "!" not in text:
        return None
    # Delayed import keeps the expander and its text helper free of an import cycle.
    from .macros import _PARAM_NAME_RE, _error

    parts: list[TextFragment] = []
    i = last = 0
    found = False

    def literal(value: str) -> None:
        # Only a template's own literals are scaffolding; the same text written
        # at a call site is the caller's content and keeps the higher rank.
        if value:
            parts.append(TextFragment(value, target, scaffold=lookup is not None))

    while (j := text.find("!", i)) >= 0:
        if is_escaped(text, j):
            i = j + 1
            continue
        escape = next(
            (m for m in ("!text{", "!param{") if text.startswith("!" + m, j)),
            None,
        )
        if escape is not None:
            literal(text[last:j])
            literal(escape)
            i = last = j + 1 + len(escape)
            found = True
            continue
        if text.startswith("!text{", j):
            close = text.find("}", j + 6)
            if close < 0:
                raise _error(
                    "unterminated !text{...}",
                    marker_span(origin, offset + j, 6), lookup,
                    code="E005",
                )
            name = text[j + 6 : close]
            span = marker_span(origin, offset + j, close + 1 - j)
            if _PARAM_NAME_RE.fullmatch(name) is None:
                raise _error(
                    f"invalid !text parameter name '{name}'",
                    span,
                    lookup,
                    code="E006",
                )
            if lookup is None:
                raise _error(
                    "!text is only valid inside a macro template",
                    span,
                    lookup,
                    code="E007",
                )
            literal(text[last:j])
            try:
                parts.extend(text_value(name, lookup))
            except MacroExpansionError as error:
                raise _error(error.message, span, lookup, code=error.code) from None
            i = last = close + 1
            found = True
            continue
        if text.startswith("!param{", j):
            raise _error(
                "!param cannot be used inside a text field; "
                "use !text for text interpolation",
                marker_span(origin, offset + j, 7), lookup,
                code="E008",
            )
        i = j + 1
    if not found:
        return None
    literal(text[last:])
    return tuple(parts)
