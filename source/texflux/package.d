/**
 * TeXFlux: a TeX-first, indentation-based preprocessor.
 *
 * TeXFlux removes the structural boilerplate of LaTeX and Beamer without
 * replacing TeX semantics. It does not parse TeX, look a command up, or infer
 * what an environment means: three prefixes classify a line, two suite markers
 * say how its value is written, and everything else is passed through as
 * written. What it adds is structure, provenance and one way to say a thing.
 *
 * This module is the public interface. Every entry point here goes through a
 * compilation session, so the bundled standard macros and module resolution
 * behave the same whichever public entry point is called.
 *
 * The language is defined by texflux_tex_first_dsl_v1_spec.md, which is
 * normative for this implementation; its tests and golden files are part of
 * the same repository and are consumed by the D test executable.
 */
module texflux;

public import texflux.ast : Document;
public import texflux.bundle : BundleBuildOptions, BundleBuildResult, BundleDependency,
    BundleFile, BundleFragment, BundleIndex, BundleManifest, BundlePosition, BundleSpan,
    buildBundle, readBundleIndex, readBundleManifest, serializeBundleIndex,
    writeBundleAtomic;
public import texflux.archive : BundleLimits;
public import texflux.assets : AssetReference;
public import texflux.errors : DirectiveError, FlagError, InternalError,
    BundleError, MacroExpansionError, ModuleError, ParseError, RelatedLocation,
    TeXFluxError, ValidationError, ValueError;
public import texflux.flags : Flags;
public import texflux.modules : CompilationSession, SourceReader;
public import texflux.render : CompilationResult, RenderedDocument, RenderedFragment;
public import texflux.source : LoadedSource, SourcePosition, SourceSpan, SourceText,
    TextFragment;
public import texflux.sourcemap : serializeSourceMap;
public import texflux.trace : CompilationTrace, TraceAsset, TraceBundle, TraceDependency,
    TraceSource;

import texflux.render : renderWithProvenance;

/// The released version, which every published document names as its producer.
enum texfluxVersion = "0.3.0";

/// A compiled document, and every file the compilation read.
struct AstCompilationResult
{
    Document document;
    LoadedSource[] sources;
}

/**
 * Compile source text and return the canonical tree, without rendering it.
 *
 * `sourceBytes` is what the digest in a published document is taken over. It
 * defaults to the UTF-8 encoding of the text, which differs from the file on
 * disk when that file used other line endings; pass the original bytes when
 * the digest has to describe the file.
 */
AstCompilationResult compileAst(string source, string filename = "<string>",
        Flags flags = Flags.init, immutable(ubyte)[] sourceBytes = null)
{
    auto session = new CompilationSession;
    const data = sourceBytes is null ? cast(immutable(ubyte)[]) source : sourceBytes;
    auto document = session.compileRoot(source, filename, data, flags);
    return AstCompilationResult(document, session.loaded);
}

/// Compile source text to TeX, keeping the record of where each run came from.
CompilationResult compileWithMap(string source, string filename = "<string>",
        bool sourceComments = false, Flags flags = Flags.init,
        immutable(ubyte)[] sourceBytes = null)
{
    auto result = compileAst(source, filename, flags, sourceBytes);
    auto rendered = renderWithProvenance(result.document, sourceComments);
    return CompilationResult(rendered.text, rendered, result.sources);
}

/// Compile source text to TeX.
string compileText(string source, string filename = "<string>",
        bool sourceComments = false, Flags flags = Flags.init)
{
    return compileWithMap(source, filename, sourceComments, flags).text;
}

///
unittest
{
    const tex = compileText("@frame{Title}::\n    \\item one\n", "deck.tfx");
    assert(tex == "\\begin{frame}{Title}\n\\item one\n\\end{frame}\n");
}

/// The standard flow macros are available without importing anything.
unittest
{
    const tex = compileText("!before{\\medskip} >> \\emph{after}\n", "deck.tfx");
    assert(tex == "\\medskip\n\\emph{after}\n");
}

/// A build flag chooses between two versions of one document.
unittest
{
    enum source = "!flag{draft}{off}\n!when{draft}::\n    \\marginpar{note}\n";
    assert(compileText(source, "deck.tfx") == "\n");
    assert(compileText(source, "deck.tfx", false, Flags(["draft": true]))
            == "\\marginpar{note}\n");
}
