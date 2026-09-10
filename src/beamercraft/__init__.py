"""TeX-first, indentation-based LaTeX preprocessor."""

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    Document,
    GenericInvocation,
    GroupKind,
    InvocationKind,
    Item,
    ParsedInvocation,
    RawTex,
    SourceLocation,
    SpecialInvocation,
    Stack,
)
from .errors import BeamercraftError, DirectiveError, ParseError, ValidationError
from .normalize import (
    BUILTIN_DIRECTIVES,
    DirectiveRegistry,
    TransformContext,
    normalize,
)
from .parser import HeaderScanner, parse
from .render import render


def compile_text(
    source: str,
    *,
    filename: str = "<string>",
    source_comments: bool = False,
) -> str:
    document = normalize(parse(source, filename=filename))
    return render(document, source_comments=source_comments)


__all__ = [
    "Argument",
    "ArgumentLayout",
    "BeamercraftError",
    "BUILTIN_DIRECTIVES",
    "Block",
    "BraceGroup",
    "DirectiveError",
    "DirectiveRegistry",
    "Document",
    "GenericInvocation",
    "GroupKind",
    "HeaderScanner",
    "InvocationKind",
    "Item",
    "ParseError",
    "ParsedInvocation",
    "RawTex",
    "SourceLocation",
    "SpecialInvocation",
    "Stack",
    "TransformContext",
    "ValidationError",
    "compile_text",
    "normalize",
    "parse",
    "render",
]
