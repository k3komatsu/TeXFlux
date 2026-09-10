"""TeX-first, indentation-based LaTeX preprocessor."""

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    Document,
    GenericInvocation,
    GroupKind,
    Item,
    ParsedGeneric,
    RawTex,
    SourceLocation,
    SpecialInvocation,
    Stack,
)
from .errors import BeamercraftError, DirectiveError, ParseError, ValidationError
from .normalize import (
    BUILTIN_DIRECTIVES,
    DirectiveRegistry,
    DirectiveSpec,
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
    from .normalize import normalize
    from .parser import parse
    from .render import render

    return render(normalize(parse(source, filename=filename)), source_comments=source_comments)


__all__ = [
    "Argument",
    "ArgumentLayout",
    "BeamercraftError",
    "BUILTIN_DIRECTIVES",
    "Block",
    "DirectiveError",
    "DirectiveRegistry",
    "DirectiveSpec",
    "Document",
    "GenericInvocation",
    "GroupKind",
    "HeaderScanner",
    "Item",
    "ParseError",
    "ParsedGeneric",
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
