module texflux.lsp.workspace;

import std.algorithm : canFind;
import std.path : extension;
import std.string : startsWith;

import texflux.analysis : AnalysisRequest, AnalysisResult, analyze;
import texflux.diagnostics : Severity;
import texflux.flags : Flags;
import texflux.modules : ModuleKind;
import texflux.paths : absoluteNormalized, normalizedPath;
import texflux.source : LoadedSource;
import texflux.text : decodeUtf8;
import texflux.trace : TraceDependency;

import texflux.lsp.features : FeatureIndex, buildFeatureIndex;
import texflux.lsp.text : LineIndex, LspPosition, LspRange, PositionEncoding, pathToUri,
    uriToPath;

struct OpenDocument
{
    string uri;
    string displayPath;
    string canonicalPath;
    int documentVersion;
    string text;
    immutable(ubyte)[] bytes;
    LineIndex lines;
    ModuleKind kind;
    FeatureIndex features;
}

struct TextChange
{
    bool hasRange;
    LspRange range;
    string text;
}

struct RootState
{
    AnalysisResult result;
}

struct RelatedDiagnostic
{
    string message;
    string uri;
    LspRange range;
}

struct PublishedDiagnostic
{
    int severity;
    string kind;
    string code;
    string message;
    LspRange range;
    RelatedDiagnostic[] related;
}

struct PublishedFile
{
    string uri;
    bool hasVersion;
    int documentVersion;
    PublishedDiagnostic[] diagnostics;
    private string[] keys;
}

final class Workspace
{
    OpenDocument[string] documents;
    RootState[string] roots;
    string[string] internalErrors;
    string[][string] reverseDependencies;
    ulong revision;
    PositionEncoding encoding;

    this(PositionEncoding encoding)
    {
        this.encoding = encoding;
    }

    bool put(string uri, long documentVersion, string text, out string path, out string error)
    {
        auto decoded = uriToPath(uri);
        if (decoded.isNull)
        {
            error = "only file URIs are supported";
            return false;
        }
        if (documentVersion < int.min || documentVersion > int.max)
        {
            error = "document version is outside the supported range";
            return false;
        }
        auto bytes = cast(immutable(ubyte)[]) text.idup;
        try
            text = decodeUtf8(bytes);
        catch (Exception exception)
        {
            error = exception.msg;
            return false;
        }
        path = absoluteNormalized(decoded.get);
        const canonical = normalizedPath(path);
        if (auto existing = canonical in documents)
            if (documentVersion < (*existing).documentVersion)
            {
                error = "document version must not go backwards";
                return false;
            }
        const suffix = extension(path);
        if (suffix != ".tfx" && suffix != ".tfxm")
        {
            error = "only .tfx and .tfxm documents are supported";
            return false;
        }
        auto kind = suffix == ".tfxm" ? ModuleKind.macro_ : ModuleKind.content;
        documents[canonical] = OpenDocument(uri, path, canonical, cast(int) documentVersion,
                text, bytes, LineIndex(text), kind, buildFeatureIndex(text, path));
        ++revision;
        return true;
    }

    /// Apply one LSP change sequence transactionally against the current text.
    bool applyChanges(string uri, long documentVersion, const(TextChange)[] changes,
        out string path, out string error)
    {
        if (changes.length == 0)
        {
            error = "at least one content change is required";
            return false;
        }
        auto decoded = uriToPath(uri);
        if (decoded.isNull)
        {
            error = "only file URIs are supported";
            return false;
        }
        const canonical = normalizedPath(absoluteNormalized(decoded.get));
        auto existing = canonical in documents;
        string next;
        bool hasBase;
        if (existing !is null)
        {
            next = (*existing).text;
            hasBase = true;
        }
        foreach (change; changes)
        {
            auto replacementBytes = cast(immutable(ubyte)[]) change.text.idup;
            string replacement;
            try
                replacement = decodeUtf8(replacementBytes);
            catch (Exception exception)
            {
                error = exception.msg;
                return false;
            }
            if (!change.hasRange)
            {
                next = replacement;
                hasBase = true;
                continue;
            }
            if (!hasBase)
            {
                error = "document is not open";
                return false;
            }
            auto index = LineIndex(next);
            auto start = index.byteOffset(change.range.start, encoding);
            auto end = index.byteOffset(change.range.end, encoding);
            if (start.isNull || end.isNull || end.get < start.get)
            {
                error = "change range is invalid";
                return false;
            }
            next = next[0 .. start.get] ~ replacement ~ next[end.get .. $];
        }
        return put(uri, documentVersion, next, path, error);
    }

    bool contains(string path) const
    {
        return (normalizedPath(path) in documents) !is null;
    }

    OpenDocument* document(string path)
    {
        return normalizedPath(path) in documents;
    }

    string[] rootPaths() const
    {
        return documents.keys;
    }

    void close(string path)
    {
        const canonical = normalizedPath(path);
        documents.remove(canonical);
        roots.remove(canonical);
        internalErrors.remove(canonical);
        rebuildReverse();
        ++revision;
    }

    void touch(string path)
    {
        if (normalizedPath(path) in documents)
            ++revision;
    }

    /// Remove stale diagnostics while retaining the last dependency edges.
    void invalidate(string path, string message)
    {
        const canonical = normalizedPath(path);
        if (canonical in roots)
        {
            roots[canonical].result.complete = false;
            roots[canonical].result.report.diagnostics = null;
        }
        internalErrors[canonical] = message;
    }

    AnalysisResult analyzeRoot(string path)
    {
        auto document = documents[normalizedPath(path)];
        auto overlays = overlayTable();
        return analyze(AnalysisRequest(document.text, document.displayPath, document.kind,
                Flags.init, document.bytes, overlays));
    }

    void commit(string path, AnalysisResult result)
    {
        const canonical = normalizedPath(path);
        if (!result.complete)
        {
            if (auto previous = canonical in roots)
                result.dependencies = unionDependencies(previous.result.dependencies,
                        result.dependencies);
        }
        roots[canonical] = RootState(result);
        internalErrors.remove(canonical);
        rebuildReverse();
    }

    string[] affected(string path) const
    {
        const canonical = normalizedPath(path);
        string[] result;
        if (canonical in documents)
            result ~= canonical;
        if (auto found = canonical in reverseDependencies)
            foreach (root; *found)
                if (!result.canFind(root) && root in documents)
                    result ~= root;
        return result;
    }

    PublishedFile[] diagnostics()
    {
        PublishedFile[string] files;
        foreach (root, state; roots)
        {
            string[string] sourceText;
            LineIndex[string] indexes;
            string[string] sourcePath;
            foreach (source; state.result.report.sources)
            {
                if (source.file.startsWith("tfxb:"))
                    continue;
                sourceText[source.file] = decodeUtf8(source.data);
                indexes[source.file] = LineIndex(sourceText[source.file]);
                sourcePath[source.file] = source.path;
                addFile(files, uriFor(source.path), source.path);
            }
            foreach (diagnostic; state.result.report.diagnostics)
            {
                auto source = diagnostic.span.file in indexes;
                string diagnosticFile = diagnostic.span.file;
                bool fallbackRange;
                if (source is null)
                {
                    if (state.result.report.sources.length == 0)
                        continue;
                    diagnosticFile = state.result.report.sources[0].file;
                    source = diagnosticFile in indexes;
                    if (source is null)
                        continue;
                    fallbackRange = true;
                }
                auto uri = uriFor(sourcePath[diagnosticFile], diagnosticFile);
                auto file = addFile(files, uri, sourcePath[diagnosticFile]);
                PublishedDiagnostic item;
                item.severity = diagnostic.severity == Severity.warning ? 2 : 1;
                item.kind = diagnostic.kind;
                item.code = diagnostic.code;
                item.message = diagnostic.message;
                item.range = fallbackRange
                    ? LspRange(LspPosition(0, 0), LspPosition(0, 0))
                    : (*source).toLsp(diagnostic.span, encoding);
                foreach (related; diagnostic.related)
                {
                    auto relatedIndex = related.span.file in indexes;
                    if (relatedIndex is null)
                        continue;
                    RelatedDiagnostic location;
                    location.message = related.message;
                    location.uri = uriFor(sourcePath[related.span.file], related.span.file);
                    location.range = indexes[related.span.file].toLsp(related.span, encoding);
                    item.related ~= location;
                }
                const key = diagnosticKey(uri, item);
                if (!file.keys.canFind(key))
                {
                    file.keys ~= key;
                    file.diagnostics ~= item;
                }
            }
        }
        foreach (root, message; internalErrors)
        {
            auto document = root in documents;
            if (document is null)
                continue;
            auto file = addFile(files, (*document).uri, (*document).displayPath);
            PublishedDiagnostic item;
            item.severity = 1;
            item.kind = "internal";
            item.code = "internal";
            item.message = message;
            item.range = LspRange(LspPosition(0, 0), LspPosition(0, 0));
            const key = diagnosticKey((*document).uri, item);
            if (!file.keys.canFind(key))
            {
                file.keys ~= key;
                file.diagnostics ~= item;
            }
        }
        PublishedFile[] result;
        foreach (ref file; files)
        {
            auto open = canonicalFromUri(file.uri) in documents;
            if (open !is null)
            {
                file.hasVersion = true;
                file.documentVersion = (*open).documentVersion;
            }
            file.keys = null;
            result ~= file;
        }
        return result;
    }

    string uriFor(string physical, string fallback = null) const
    {
        const canonical = normalizedPath(physical.length == 0 ? fallback : physical);
        if (auto open = canonical in documents)
            return (*open).uri;
        return pathToUri(physical.length == 0 ? fallback : physical);
    }

    private string canonicalFromUri(string uri) const
    {
        auto path = uriToPath(uri);
        return path.isNull ? null : normalizedPath(path.get);
    }

    private const(ubyte)[][string] overlayTable()
    {
        const(ubyte)[][string] result;
        foreach (path, document; documents)
            result[path] = document.bytes;
        return result;
    }

    private void rebuildReverse()
    {
        reverseDependencies = null;
        foreach (root, state; roots)
            foreach (dependency; state.result.dependencies)
            {
                if (dependency.target.length == 0)
                    continue;
                auto target = normalizedPath(dependency.target);
                if (auto found = target in reverseDependencies)
                {
                    if (!(*found).canFind(root))
                        *found ~= root;
                }
                else
                    reverseDependencies[target] = [root];
            }
    }
}

private PublishedFile* addFile(ref PublishedFile[string] files, string uri, string path)
{
    if (auto found = uri in files)
        return found;
    files[uri] = PublishedFile(uri);
    return uri in files;
}

private TraceDependency[] unionDependencies(const TraceDependency[] first,
        const TraceDependency[] second)
{
    TraceDependency[] result = first.dup;
    foreach (candidate; second)
    {
        bool present;
        foreach (existing; result)
            present |= existing.from == candidate.from && existing.kind == candidate.kind
                && existing.target == candidate.target && existing.path == candidate.path
                && existing.selector == candidate.selector
                && existing.span == candidate.span;
        if (!present)
            result ~= candidate;
    }
    return result;
}

private string diagnosticKey(string uri, const PublishedDiagnostic item)
{
    import std.format : format;

    return format("%s|%s|%s|%s|%d:%d-%d:%d", uri, item.code, item.message,
            item.kind, item.range.start.line, item.range.start.character,
            item.range.end.line, item.range.end.character);
}
