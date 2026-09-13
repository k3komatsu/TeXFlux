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
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SourcePosition,
    SourceSpan,
    SpecialInvocation,
    Stack,
    SuiteMode,
    TextFragment,
)
from .errors import (
    DirectiveError,
    FlagError,
    InternalError,
    MacroExpansionError,
    ModuleError,
    ParseError,
    TeXFluxError,
    ValidationError,
)
from .flags import FLAG_VALUES, Flags, collect_flags, declared_flags_hint
from .external_ast import AstCompilationResult, serialize_ast
from .macros import (
    MacroDefinition,
    MacroParameter,
    collect_macros,
    expand_macros,
)
from .modules import (
    PRELUDE_MODULE,
    CompilationSession,
    FlagBinding,
    MacroEnvironment,
    MacroImport,
    ModuleKind,
    ModuleSource,
    load_standard_macros,
)
from .normalize import (
    BUILTIN_DIRECTIVES,
    DirectiveRegistry,
    canonicalize,
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
    LoadedSource,
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


def compile_ast(
    source: str,
    *,
    filename: str = "<string>",
    flags: Flags | None = None,
    source_bytes: bytes | None = None,
) -> AstCompilationResult:
    """Resolve one source to canonical AST through a compilation session.

    ``flags`` overrides only the root module's declarations. ``source_bytes``
    supplies its original bytes for hashing; when omitted, UTF-8 encoding of
    ``source`` is used. All loaded content and macro modules are recorded.
    """

    session = CompilationSession()
    document = session.compile_root(
        source,
        filename=filename,
        data=source.encode("utf-8") if source_bytes is None else source_bytes,
        flags=flags,
    )
    return AstCompilationResult(document, session.loaded())


def compile_with_map(
    source: str,
    *,
    filename: str = "<string>",
    source_comments: bool = False,
    flags: Flags | None = None,
    source_bytes: bytes | None = None,
) -> CompilationResult:
    """Compile one document, resolving its imports against ``filename``.

    ``flags`` overrides the root module's declared defaults; an imported
    module receives nothing but the bindings its own ``!import`` writes.
    ``source_bytes`` supplies the root's file bytes, so a source map digests
    what is on disk rather than a re-encoding of ``source``.
    """

    result = compile_ast(
        source,
        filename=filename,
        flags=flags,
        source_bytes=source_bytes,
    )
    rendered = render_with_provenance(
        result.document,
        source_comments=source_comments,
    )
    return CompilationResult(rendered.text, rendered, result.sources)


__all__ = [
    "Argument",
    "ArgumentLayout",
    "AstCompilationResult",
    "BUILTIN_DIRECTIVES",
    "Block",
    "BraceGroup",
    "CompilationResult",
    "CompilationSession",
    "DirectiveError",
    "DirectiveRegistry",
    "Document",
    "FLAG_VALUES",
    "FlagBinding",
    "FlagError",
    "Flags",
    "GeneratedSpan",
    "GenericInvocation",
    "GroupKind",
    "HeaderScanner",
    "InternalError",
    "InvocationKind",
    "LoadedSource",
    "MacroDefinition",
    "MacroEnvironment",
    "MacroExpansionError",
    "MacroImport",
    "MacroParameter",
    "ModuleError",
    "ModuleKind",
    "ModuleSource",
    "PRELUDE_MODULE",
    "ParseError",
    "ParsedInvocation",
    "RawTex",
    "RemapError",
    "RenderWarning",
    "RenderedDocument",
    "RenderedFragment",
    "SequenceEntry",
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
    "TextFragment",
    "ValidationError",
    "__version__",
    "canonicalize",
    "collect_flags",
    "collect_macros",
    "compile_ast",
    "compile_text",
    "compile_with_map",
    "declared_flags_hint",
    "desugar",
    "expand_macros",
    "load_source_map",
    "load_standard_macros",
    "normalize",
    "parse",
    "parse_synctex",
    "read_synctex_file",
    "remap_document",
    "remap_synctex",
    "remap_synctex_file",
    "render",
    "render_with_provenance",
    "serialize_ast",
    "serialize_source_map",
    "serialize_synctex",
    "write_synctex_file",
]
