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
    SequenceEntry,
    SourcePosition,
    SourceSpan,
    SpecialInvocation,
    Stack,
    SuiteMode,
)
from .errors import (
    DirectiveError,
    FlagError,
    MacroExpansionError,
    ParseError,
    TeXFluxError,
    ValidationError,
)
from .flags import FLAG_VALUES, Flags, collect_flags
from .macros import (
    MacroDefinition,
    MacroParameter,
    collect_macros,
    expand_macros,
)
from .normalize import (
    BUILTIN_DIRECTIVES,
    DirectiveRegistry,
    TransformContext,
    desugar,
    normalize,
)
from .parser import HeaderScanner, parse
from .remap import (
    RemapError,
    SourceMap,
    SourceMapSource,
    SourceMapping,
    load_source_map,
    remap_document,
    remap_synctex,
    remap_synctex_file,
)
from .render import (
    CompilationResult,
    GeneratedSpan,
    RenderWarning,
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
    flags: Flags | None = None,
) -> str:
    return compile_with_map(
        source,
        filename=filename,
        source_comments=source_comments,
        flags=flags,
    ).text


def compile_with_map(
    source: str,
    *,
    filename: str = "<string>",
    source_comments: bool = False,
    flags: Flags | None = None,
) -> CompilationResult:
    document = normalize(parse(source, filename=filename), flags=flags)
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
    "FLAG_VALUES",
    "FlagError",
    "Flags",
    "GenericInvocation",
    "GeneratedSpan",
    "GroupKind",
    "HeaderScanner",
    "InvocationKind",
    "Item",
    "MacroDefinition",
    "MacroExpansionError",
    "MacroParameter",
    "ParseError",
    "ParsedInvocation",
    "RawTex",
    "SequenceEntry",
    "RenderWarning",
    "RenderedDocument",
    "RenderedFragment",
    "RemapError",
    "SourceMap",
    "SourceMapSource",
    "SourceMapping",
    "SourcePosition",
    "SourceSpan",
    "SpecialInvocation",
    "Stack",
    "SuiteMode",
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
    "collect_flags",
    "collect_macros",
    "compile_text",
    "compile_with_map",
    "desugar",
    "expand_macros",
    "normalize",
    "parse",
    "load_source_map",
    "remap_document",
    "remap_synctex",
    "remap_synctex_file",
    "render",
    "render_with_provenance",
    "parse_synctex",
    "read_synctex_file",
    "serialize_source_map",
    "serialize_synctex",
    "write_synctex_file",
]
