"""TeX-first, indentation-based LaTeX preprocessor."""

__version__ = "0.1.0"

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
    SourcePosition,
    SourceSpan,
    SpecialInvocation,
    Stack,
)
from .errors import DirectiveError, ParseError, TeXFluxError, ValidationError
from .normalize import (
    BUILTIN_DIRECTIVES,
    DirectiveRegistry,
    TransformContext,
    normalize,
)
from .parser import HeaderScanner, parse
from .render import (
    CompilationResult,
    GeneratedSpan,
    RenderedDocument,
    RenderedFragment,
    render,
    render_with_provenance,
)
from .source_map import serialize_source_map
from .synctex import (
    SyncTeXDocument,
    SyncTeXError,
    SyncTeXInput,
    SyncTeXLine,
    SyncTeXLink,
    SyncTeXPoint,
    SyncTeXRecord,
    SyncTeXSetting,
    parse_synctex,
    read_synctex_file,
    serialize_synctex,
    write_synctex_file,
)


def compile_text(
    source: str,
    *,
    filename: str = "<string>",
    source_comments: bool = False,
) -> str:
    return compile_with_map(
        source,
        filename=filename,
        source_comments=source_comments,
    ).text


def compile_with_map(
    source: str,
    *,
    filename: str = "<string>",
    source_comments: bool = False,
) -> CompilationResult:
    document = normalize(parse(source, filename=filename))
    rendered = render_with_provenance(
        document,
        source_comments=source_comments,
    )
    return CompilationResult(rendered.text, rendered)


__all__ = [
    "Argument",
    "ArgumentLayout",
    "BUILTIN_DIRECTIVES",
    "Block",
    "BraceGroup",
    "CompilationResult",
    "DirectiveError",
    "DirectiveRegistry",
    "Document",
    "GenericInvocation",
    "GeneratedSpan",
    "GroupKind",
    "HeaderScanner",
    "InvocationKind",
    "Item",
    "ParseError",
    "ParsedInvocation",
    "RawTex",
    "RenderedDocument",
    "RenderedFragment",
    "SourcePosition",
    "SourceSpan",
    "SpecialInvocation",
    "Stack",
    "SyncTeXDocument",
    "SyncTeXError",
    "SyncTeXInput",
    "SyncTeXLine",
    "SyncTeXLink",
    "SyncTeXPoint",
    "SyncTeXRecord",
    "SyncTeXSetting",
    "TeXFluxError",
    "TransformContext",
    "ValidationError",
    "__version__",
    "compile_text",
    "compile_with_map",
    "normalize",
    "parse",
    "render",
    "render_with_provenance",
    "parse_synctex",
    "read_synctex_file",
    "serialize_source_map",
    "serialize_synctex",
    "write_synctex_file",
]
