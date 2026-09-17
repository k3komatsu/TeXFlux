/**
 * Reporting what a compilation found, without stopping on it.
 *
 * The compiler itself raises: a document that does not compile has nothing to
 * render, so there is no reason to carry on. An editor needs the opposite,
 * which is what this is: the same passes, the same diagnostics, handed back as
 * data along with every file the attempt read.
 *
 * Compilation stops at its first error, so a report holds either nothing or
 * exactly one diagnostic. The format allows for more, so that collecting
 * several later would change nothing a consumer has to read.
 */
module texflux.diagnostics;

import std.algorithm : all;

import texflux.canonical : builtinDirectives;
import texflux.errors : diagnosticLine, RelatedLocation, TeXFluxError;
import texflux.flags : Flags;
import texflux.interchange : header, spanEncoder;
import texflux.json;
import texflux.modules : CompilationSession, readSource, SourceReader;
import texflux.paths : normalizedPath;
import texflux.source : LoadedSource, SourceSpan;

/**
 * Whether a diagnostic stopped the compilation or only warned about it.
 *
 * Every diagnostic this version produces is an error. The warning channel is
 * part of the published format and is reserved: a consumer that handles it now
 * will not have to change when something eventually uses it.
 */
enum Severity : string
{
    error = "error",
    warning = "warning",
}

/// One reported problem, at one place, in one file.
struct Diagnostic
{
    Severity severity;
    string kind;
    string code;
    string message;
    SourceSpan span;
    RelatedLocation[] related;

    /// How this diagnostic names itself on a printed line.
    string label() const
    {
        return severity == Severity.warning ? "warning" : kind ~ " error";
    }

    /// The one line a diagnostic prints.
    string line() const
    {
        return diagnosticLine(span, label, message, code);
    }

    /// The same diagnostic the compiler would have raised.
    static Diagnostic fromError(const TeXFluxError error)
    {
        return Diagnostic(Severity.error, error.kind, error.code, error.message,
                error.span, error.related.dup);
    }
}

/// Everything one compilation read, and everything it reported.
struct DiagnosticReport
{
    /// The root first, then each module in the order the session loaded it.
    LoadedSource[] sources;
    /// In the order the compilation produced them.
    Diagnostic[] diagnostics;

    /// The document the compilation was asked about.
    LoadedSource root() const
    {
        return sources[0];
    }

    /// Whether nothing stopped the compilation.
    bool ok() const
    {
        return diagnostics.all!(diagnostic => diagnostic.severity != Severity.error);
    }
}

/**
 * A reader that serves unsaved editor buffers before touching the disk.
 *
 * Keys are matched by path identity, so an overlay written as an absolute path
 * still answers an import written as a relative one.
 */
SourceReader overlayReader(const(ubyte)[][string] overlays)
{
    immutable(ubyte)[][string] table;
    foreach (path, content; overlays)
        table[normalizedPath(path)] = content.idup;

    return delegate(string display) {
        auto found = normalizedPath(display) in table;
        return found is null ? readSource(display) : *found;
    };
}

/**
 * Report what one compilation found, rather than raising it.
 *
 * `overlays` maps a module path to unsaved text, which any import reaching
 * that path reads instead of the file. The root is not among them: it is what
 * the source argument already is.
 *
 * A broken installation and a build-flag override that names nothing still
 * raise. Neither describes a place in a document, so neither is a diagnostic:
 * they report how the compiler was called or installed.
 */
DiagnosticReport diagnose(string source, string filename = "<string>", Flags flags = Flags.init,
        immutable(ubyte)[] sourceBytes = null, const(ubyte)[][string] overlays = null)
{
    auto session = new CompilationSession(builtinDirectives(),
            overlays is null ? null : overlayReader(overlays));
    const data = sourceBytes is null ? cast(immutable(ubyte)[]) source : sourceBytes;
    try
        session.compileRoot(source, filename, data, flags);
    catch (TeXFluxError error)
        return DiagnosticReport(session.loaded, [Diagnostic.fromError(error)]);
    return DiagnosticReport(session.loaded, []);
}

/**
 * Serialize one report as the published diagnostics document.
 *
 * A source's digest is taken over the bytes the compilation read, which for an
 * overlaid module is the editor's buffer rather than the file. Spans index into
 * the same source table the external AST uses.
 */
string serializeDiagnostics(DiagnosticReport report, bool pretty = false)
{
    auto span = spanEncoder(report.sources);
    auto payload = header("texflux-diagnostics", report.sources);

    JsonValue[] reported;
    foreach (diagnostic; report.diagnostics)
    {
        JsonValue[] related;
        foreach (location; diagnostic.related)
            related ~= jsonObject(member("message", location.message),
                    member("span", span(location.span)));
        reported ~= jsonObject(
                member("severity", cast(string) diagnostic.severity),
                member("kind", diagnostic.kind),
                member("code", diagnostic.code),
                member("message", diagnostic.message),
                member("span", span(diagnostic.span)),
                member("related", jsonArray(related)));
    }

    return dumpJson(jsonObject(payload ~ member("diagnostics", jsonArray(reported))), pretty);
}
