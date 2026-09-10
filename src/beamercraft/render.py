"""Deterministic renderer for the canonical AST."""

from __future__ import annotations

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    Document,
    GenericInvocation,
    GroupKind,
    Item,
    RawTex,
)


def _group(argument: Argument) -> str:
    if (
        argument.layout is not ArgumentLayout.INLINE
        or not isinstance(argument.value, str)
    ):
        raise TypeError("renderer received a non-inline argument in an inline position")
    delimiters = {
        GroupKind.REQUIRED: ("{", "}"),
        GroupKind.OPTIONAL: ("[", "]"),
        GroupKind.OVERLAY: ("<", ">"),
    }
    opener, closer = delimiters[argument.kind]
    return f"{opener}{argument.value}{closer}"


def _render_block(block: Block, source_comments: bool) -> list[str]:
    lines: list[str] = []
    for node in block.nodes:
        if isinstance(node, RawTex):
            if source_comments and node.text != "":
                lines.append(f"% beamercraft: {node.loc.file}:{node.loc.line}")
            lines.append(node.text)
            continue

        if source_comments:
            lines.append(f"% beamercraft: {node.loc.file}:{node.loc.line}")
        if isinstance(node, GenericInvocation):
            lines.extend(_render_invocation(node, source_comments))
        elif isinstance(node, Item):
            lines.extend(_render_item(node, source_comments))
        elif isinstance(node, BraceGroup):
            lines.append("{")
            lines.extend(_render_block(node.body, source_comments))
            lines.append("}")
        else:
            raise TypeError(
                "renderer accepts canonical AST only; "
                f"got {type(node).__name__}"
            )
    return lines


def _render_arguments(
    prefix: str,
    arguments: tuple[Argument, ...],
    source_comments: bool,
) -> list[str]:
    lines = [prefix]
    for argument in arguments:
        if argument.layout is ArgumentLayout.INLINE:
            lines[-1] += _group(argument)
            continue
        if argument.layout is not ArgumentLayout.BLOCK:
            raise TypeError("renderer received an invalid argument layout")
        if not isinstance(argument.value, Block):
            raise TypeError("renderer received an invalid argument value")
        lines[-1] += "{"
        lines.extend(_render_block(argument.value, source_comments))
        lines.append("}")
    return lines


def _render_invocation(node: GenericInvocation, source_comments: bool) -> list[str]:
    if node.body is None:
        return _render_arguments(f"\\{node.name}", node.arguments, source_comments)
    lines = _render_arguments(
        f"\\begin{{{node.name}}}",
        node.arguments,
        source_comments,
    )
    lines.extend(_render_block(node.body, source_comments))
    lines.append(f"\\end{{{node.name}}}")
    return lines


def _render_item(node: Item, source_comments: bool) -> list[str]:
    line = r"\item"
    if node.overlay is not None:
        line += _group(node.overlay)
    if node.label is not None:
        line += _group(node.label)
    if node.first_line:
        line += f" {node.first_line}"
    lines = [line]
    lines.extend(_render_block(node.continuation, source_comments))
    return lines


def render(document: Document, *, source_comments: bool = False) -> str:
    """Render a canonical document, terminated by a newline."""

    return "\n".join(_render_block(document.body, source_comments)) + "\n"


__all__ = ["render"]
