/** Bundle v1 manifest, deterministic archive builder, and frame index. */
module texflux.bundle;

import core.atomic : atomicOp;
import std.algorithm : canFind, sort;
import std.conv : to;
import std.file : exists, isFile, mkdirRecurse, read, remove, rename, rmdirRecurse, write;
import std.json : JSONType, JSONValue, parseJSON;
import std.path : baseName, buildPath, dirName,
    relativePath;
import std.process : environment, thisProcessID;
import std.string : endsWith, indexOf, replace, startsWith;
import std.sumtype : get, has, match;
import std.typecons : Nullable, nullable;

import texflux.archive : ArchiveEntry, bundleLimitError, readArchive, writeArchive;
import texflux.ast : ArgumentLayout, ArgumentValue, Block, BraceGroup, Document,
    GenericInvocation, GroupKind, Node, RawTex;
import texflux.assets : AssetReference, AssetResolver;
import texflux.canonical : builtinDirectives;
import texflux.errors : BundleError, FlagError;
import texflux.flags : Flags;
import texflux.interchange : digest;
import texflux.json;
import texflux.modules : CompilationSession, ModulePathResolver, SourceReader;
import texflux.paths : absoluteNormalized, normalizedPath;
import texflux.source : LoadedSource, SourcePosition, SourceSpan, SourceText;
import texflux.text : decodeUtf8, UnicodeDecodeError;
import texflux.trace : CompilationTrace, TraceAsset, TraceBundle, TraceDependency,
    TraceSource, BundleResolutionState;

public import texflux.archive : BundleLimits;

private shared ulong cacheTemporarySequence;

enum bundleFormat = "texflux-bundle";
enum bundleIndexFormat = "texflux-bundle-index";

struct BundlePosition
{
    int line;
    int column;
}

struct BundleSpan
{
    BundlePosition start;
    BundlePosition end;
}

struct BundleFile
{
    string id;
    string kind;
    string sourceKind;
    string archivePath;
    string logicalPath;
    ulong size;
    string sha256;
}

struct BundleDependency
{
    string from;
    string kind;
    string path;
    string target;
    string selector;
    BundleSpan span;
}

struct BundleFragment
{
    string selector;
    string kind;
    Nullable!string title;
    string source;
    BundleSpan span;
}

struct BundleManifest
{
    string format;
    int version_;
    string producerName;
    string producerVersion;
    string root;
    Flags flags;
    BundleFile[] files;
    BundleDependency[] dependencies;
    BundleFragment[] fragments;
}

struct BundleIndex
{
    string format;
    int version_;
    string producerName;
    string producerVersion;
    string bundleFormat;
    int bundleVersion;
    BundleFragment[] fragments;
}

struct BundleBuildOptions
{
    Flags flags;
    BundleLimits limits;
    string cacheRoot;
}

struct BundleBuildResult
{
    immutable(ubyte)[] archive;
    BundleManifest manifest;
}

private SourceSpan bundleLocation()
{
    return SourceSpan("<bundle>", SourcePosition(1, 1), SourcePosition(1, 1));
}

private BundleError manifestError(string message)
{
    return new BundleError("B009", message, bundleLocation());
}

private BundleError versionError(string message)
{
    return new BundleError("B010", message, bundleLocation());
}

private BundleError memberError(string message)
{
    return new BundleError("B011", message, bundleLocation());
}

private BundleError selectorError(SourceSpan span, string message)
{
    return new BundleError("B012", message, span);
}

private BundleError fragmentError(string message)
{
    return new BundleError("B013", message, bundleLocation());
}

private BundleError rootError(string message)
{
    return new BundleError("B019", message, bundleLocation());
}

private BundleError flagsError(string message)
{
    return new BundleError("B020", message, bundleLocation());
}

private BundleError recompileIndexError(string message)
{
    return new BundleError("B021", message, bundleLocation());
}

private BundleError openError(string message)
{
    return new BundleError("B006", message, bundleLocation());
}

private BundleError edgeError(SourceSpan span, string message)
{
    return new BundleError("B014", message, span);
}

private BundleError cycleError(SourceSpan span, string message, SourceSpan first)
{
    import texflux.errors : RelatedLocation;

    return new BundleError("B015", message, span,
            [RelatedLocation("first Bundle import here", first)]);
}

private BundleError outputError(string message)
{
    return new BundleError("B016", message, bundleLocation());
}

private BundleError inputError(string message)
{
    return new BundleError("B017", message, bundleLocation());
}

private BundleError cacheError(string message)
{
    return new BundleError("B018", message, bundleLocation());
}

/** Read one Bundle without exposing a raw filesystem exception to callers. */
immutable(ubyte)[] readBundleBytes(string path)
{
    try
        return cast(immutable(ubyte)[]) read(path);
    catch (Exception error)
        throw openError("cannot read Bundle '" ~ path ~ "': " ~ error.msg);
}

private JSONValue required(JSONValue object, string name)
{
    if (object.type != JSONType.object || name !in object.objectNoRef)
        throw manifestError("manifest is missing '" ~ name ~ "'");
    return object.objectNoRef[name];
}

private string text(JSONValue object, string name)
{
    auto value = required(object, name);
    if (value.type != JSONType.string)
        throw manifestError("manifest member '" ~ name ~ "' must be a string");
    return value.str;
}

private ulong number(JSONValue object, string name)
{
    auto value = required(object, name);
    if (value.type == JSONType.integer && value.integer >= 0)
        return cast(ulong) value.integer;
    if (value.type == JSONType.uinteger)
        return value.uinteger;
    throw manifestError("manifest member '" ~ name ~ "' must be a non-negative integer");
}

private BundlePosition position(JSONValue value)
{
    const line = number(value, "line");
    const column = number(value, "column");
    if (line == 0 || column == 0 || line > int.max || column > int.max)
        throw manifestError("manifest positions are one-based");
    return BundlePosition(cast(int) line, cast(int) column);
}

private BundleSpan span(JSONValue value)
{
    return BundleSpan(position(required(value, "start")), position(required(value, "end")));
}

private Nullable!string nullableText(JSONValue object, string name)
{
    auto value = required(object, name);
    if (value.type == JSONType.null_)
        return Nullable!string.init;
    if (value.type != JSONType.string)
        throw manifestError("manifest member '" ~ name ~ "' must be a string or null");
    return value.str.nullable;
}

private void requireArray(JSONValue object, string name)
{
    if (required(object, name).type != JSONType.array)
        throw manifestError("manifest member '" ~ name ~ "' must be an array");
}

private BundleFile parseFile(JSONValue value)
{
    auto result = BundleFile(text(value, "id"), text(value, "kind"), "",
            text(value, "archivePath"), text(value, "logicalPath"),
            number(value, "size"), text(value, "sha256"));
    if (result.kind != "source" && result.kind != "asset" && result.kind != "bundle")
        throw manifestError("unknown Bundle file kind '" ~ result.kind ~ "'");
    if (result.kind == "source")
        result.sourceKind = text(value, "sourceKind");
    if (result.sha256.length != 64)
        throw manifestError("Bundle file sha256 must be a 64-character hex digest");
    return result;
}

private BundleDependency parseDependency(JSONValue value)
{
    const kind = text(value, "kind");
    if (!canFind(["import", "macroimport", "asset", "bundleimport"], kind))
        throw manifestError("unknown Bundle dependency kind '" ~ kind ~ "'");
    auto result = BundleDependency(text(value, "from"), kind, text(value, "path"),
            text(value, "target"), "", span(required(value, "span")));
    if (kind == "bundleimport")
        result.selector = text(value, "selector");
    return result;
}

private BundleFragment parseFragment(JSONValue value)
{
    const selector = text(value, "selector");
    if (!selector.startsWith("frame:") || selector.length == 6
            || selector[6] == '0')
        throw manifestError("invalid Bundle frame selector '" ~ selector ~ "'");
    foreach (char c; selector[6 .. $])
        if (c < '0' || c > '9')
            throw manifestError("invalid Bundle frame selector '" ~ selector ~ "'");
    const kind = text(value, "kind");
    if (kind != "frame")
        throw manifestError("unknown Bundle fragment kind '" ~ kind ~ "'");
    return BundleFragment(selector, kind, nullableText(value, "title"),
            text(value, "source"), span(required(value, "span")));
}

private bool validEdgePath(string path)
{
    if (path.length == 0 || path.canFind('\0') || path.canFind('\\')
            || path[0] == '/' || path.startsWith("//")
            || (path.length >= 2 && path[1] == ':'))
        return false;
    size_t start;
    while (start <= path.length)
    {
        const end = path[start .. $].indexOf('/');
        const stop = end < 0 ? path.length : start + end;
        if (stop == start)
            return false;
        start = stop + 1;
        if (end < 0)
            break;
    }
    return true;
}

private bool validFrameSelector(string selector)
{
    if (!selector.startsWith("frame:") || selector.length == 6 || selector[6] == '0')
        return false;
    foreach (char c; selector[6 .. $])
        if (c < '0' || c > '9')
            return false;
    return true;
}

private void validateManifest(BundleManifest manifest, ArchiveEntry[] entries)
{
    if (manifest.format != bundleFormat)
        throw versionError("unsupported Bundle format '" ~ manifest.format ~ "'");
    if (manifest.version_ != 1)
        throw versionError("unsupported Bundle manifest version");

    string[string] ids;
    string[string] archivePaths;
    string[string] logicalPaths;
    BundleFile[string] filesById;
    foreach (file; manifest.files)
    {
        if (file.id in ids || file.archivePath in archivePaths || file.logicalPath in logicalPaths)
            throw manifestError("Bundle file IDs and paths must be unique");
        ids[file.id] = file.id;
        archivePaths[file.archivePath] = file.id;
        logicalPaths[file.logicalPath] = file.id;
        if (!file.archivePath.startsWith("payload/"))
            throw manifestError("Bundle payload paths must start with 'payload/'");
        if (file.kind == "source"
                && file.sourceKind != "content" && file.sourceKind != "macro")
            throw manifestError("Bundle sourceKind must be 'content' or 'macro'");
        filesById[file.id] = file;
    }
    bool validRoot;
    foreach (file; manifest.files)
        validRoot |= file.id == manifest.root && file.kind == "source"
            && file.sourceKind == "content";
    if (!validRoot)
        throw manifestError("Bundle root must name a content source");

    string[string] members;
    foreach (entry; entries)
    {
        if (entry.name != "manifest.json")
            members[entry.name] = entry.name;
    }
    foreach (file; manifest.files)
    {
        if (file.archivePath !in members)
            throw memberError("Bundle manifest names a missing payload member '"
                    ~ file.archivePath ~ "'");
    }
    foreach (name; members.keys)
    {
        bool declared;
        foreach (file; manifest.files)
            declared |= file.archivePath == name;
        if (!declared)
            throw memberError("Bundle contains an undeclared payload member '" ~ name ~ "'");
    }

    foreach (dependency; manifest.dependencies)
    {
        if (dependency.from !in ids || dependency.target !in ids)
            throw edgeError(bundleLocation(), "Bundle dependency refers to an unknown file");
        const from = filesById[dependency.from];
        if (from.kind != "source" || !validEdgePath(dependency.path))
            throw edgeError(bundleLocation(), "Bundle dependency has an invalid source or path");
        const target = filesById[dependency.target];
        if ((dependency.kind == "import" && from.sourceKind != "content")
                || (dependency.kind == "bundleimport" && from.sourceKind != "content"))
            throw edgeError(bundleLocation(), "Bundle dependency kind is invalid for its source");
        const requiredExtension = dependency.kind == "import" ? ".tfx"
            : dependency.kind == "macroimport" ? ".tfxm"
            : dependency.kind == "bundleimport" ? ".tfxb" : "";
        if (requiredExtension.length != 0 && !dependency.path.endsWith(requiredExtension))
            throw edgeError(bundleLocation(), "Bundle dependency path has the wrong extension");
        const validTarget = dependency.kind == "asset" ? target.kind == "asset"
            : dependency.kind == "bundleimport" ? target.kind == "bundle"
            : target.kind == "source"
                && ((dependency.kind == "import" && target.sourceKind == "content")
                    || (dependency.kind == "macroimport" && target.sourceKind == "macro"));
        if (!validTarget)
            throw edgeError(bundleLocation(), "Bundle dependency target kind does not match");
        if (dependency.kind == "bundleimport" && !validFrameSelector(dependency.selector))
            throw edgeError(bundleLocation(), "Bundle dependency has an invalid frame selector");
        if (dependency.kind != "bundleimport" && dependency.selector.length != 0)
            throw edgeError(bundleLocation(), "Bundle dependency has an unexpected selector");
        foreach (previous; manifest.dependencies)
            if (previous.from == dependency.from && previous.kind == dependency.kind
                    && previous.path == dependency.path
                    && previous.target != dependency.target)
                throw edgeError(bundleLocation(), "Bundle dependency edge is ambiguous");
    }
    foreach (fragment; manifest.fragments)
        if (fragment.source !in ids || (filesById[fragment.source].kind != "source"
                && filesById[fragment.source].kind != "bundle"))
            throw fragmentError("Bundle fragment refers to an unknown source or Bundle");

    // Every payload is reachable from the root through the recorded closure.
    string[string] reached;
    reached[manifest.root] = manifest.root;
    bool changed = true;
    while (changed)
    {
        changed = false;
        foreach (dependency; manifest.dependencies)
            if (dependency.from in reached && dependency.target !in reached)
            {
                reached[dependency.target] = dependency.target;
                changed = true;
            }
    }
    foreach (file; manifest.files)
        if (file.id !in reached)
            throw edgeError(bundleLocation(), "Bundle contains an unreachable payload");
}

private BundleManifest parseManifest(const(ubyte)[] bytes, ArchiveEntry[] entries,
    BundleLimits limits)
{
    if (bytes.length > limits.maxManifestBytes)
        throw bundleLimitError("Bundle manifest exceeds the manifest byte limit");
    string manifestText;
    try
        manifestText = decodeUtf8(bytes);
    catch (UnicodeDecodeError error)
        throw manifestError("Bundle manifest is not valid UTF-8: " ~ error.msg);
    JSONValue root;
    try
        root = parseJSON(manifestText);
    catch (Exception error)
        throw manifestError("malformed Bundle manifest: " ~ error.msg);
    const format = text(root, "format");
    const manifestVersion = number(root, "version");
    if (format != bundleFormat || manifestVersion != 1)
        throw versionError("unsupported Bundle format or version");
    auto producer = required(root, "producer");
    auto manifest = BundleManifest(format, cast(int) manifestVersion, text(producer, "name"),
            text(producer, "version"), text(root, "root"), Flags.init);

    auto flagObject = required(root, "flags");
    if (flagObject.type != JSONType.object)
        throw manifestError("Bundle flags must be an object");
    foreach (name, value; flagObject.objectNoRef)
    {
        if (value.type != JSONType.true_ && value.type != JSONType.false_)
            throw manifestError("Bundle flags must be boolean");
        manifest.flags[name] = value.type == JSONType.true_;
    }
    requireArray(root, "files");
    foreach (value; required(root, "files").array)
        manifest.files ~= parseFile(value);
    requireArray(root, "dependencies");
    foreach (value; required(root, "dependencies").array)
        manifest.dependencies ~= parseDependency(value);
    requireArray(root, "fragments");
    foreach (value; required(root, "fragments").array)
        manifest.fragments ~= parseFragment(value);
    validateManifest(manifest, entries);
    return manifest;
}

private ArchiveEntry payload(ArchiveEntry[] entries, string name)
{
    foreach (entry; entries)
        if (entry.name == name)
            return entry;
    throw memberError("Bundle payload member is missing '" ~ name ~ "'");
}

private struct ValidatedBundle
{
    BundleManifest manifest;
    ArchiveEntry[] entries;
}

private ValidatedBundle readValidatedBundle(const(ubyte)[] bytes, BundleLimits limits)
{
    auto entries = readArchive(bytes, limits);
    const manifestBytes = payload(entries, "manifest.json").data;
    auto manifest = parseManifest(manifestBytes, entries, limits);
    foreach (file; manifest.files)
    {
        const data = payload(entries, file.archivePath).data;
        if (data.length != file.size || digest(data) != file.sha256)
            throw memberError("Bundle payload hash or size mismatch for '" ~ file.id ~ "'");
    }
    return ValidatedBundle(manifest, entries);
}

/** Read, validate, and hash-check a Bundle manifest from archive bytes. */
BundleManifest readBundleManifest(const(ubyte)[] bytes,
        BundleLimits limits = BundleLimits.init)
{
    return readValidatedBundle(bytes, limits).manifest;
}

BundleManifest readBundleManifest(string path, BundleLimits limits = BundleLimits.init)
{
    return readBundleManifest(readBundleBytes(path), limits);
}

BundleIndex readBundleIndex(const(ubyte)[] bytes,
        BundleLimits limits = BundleLimits.init)
{
    auto manifest = readBundleManifest(bytes, limits);
    return BundleIndex(bundleIndexFormat, 1, manifest.producerName, manifest.producerVersion,
            manifest.format, manifest.version_, manifest.fragments);
}

BundleIndex readBundleIndex(string path, BundleLimits limits = BundleLimits.init)
{
    return readBundleIndex(readBundleBytes(path), limits);
}

private BundleSpan manifestSpan(SourceSpan source)
{
    return BundleSpan(BundlePosition(source.start.line, source.start.column),
            BundlePosition(source.end.line, source.end.column));
}

private Nullable!string frameTitle(GenericInvocation invocation)
{
    foreach (argument; invocation.arguments)
        if (argument.kind == GroupKind.required && argument.layout == ArgumentLayout.inline
                && argument.value.has!string)
            return argument.value.get!string.nullable;
    return Nullable!string.init;
}

/** Index only direct canonical root frames. */
BundleFragment[] indexFrames(Document document, string[string] sourceIds)
{
    BundleFragment[] result;
    foreach (index, node; document.body_.nodes)
    {
        if (!node.has!GenericInvocation)
            continue;
        auto invocation = node.get!GenericInvocation;
        if (invocation.name != "frame" || invocation.body_ is null)
            continue;
        result ~= frameFragment(invocation, result.length + 1, sourceIds);
    }
    return result;
}

private BundleFragment frameFragment(GenericInvocation invocation, size_t number,
        string[string] sourceIds)
{
    const source = frameSourceId(invocation.span.file, sourceIds);
    auto root = "<root>" in sourceIds;
    return BundleFragment("frame:" ~ number.to!string, "frame", frameTitle(invocation),
            source is null && root !is null ? *root : source,
            manifestSpan(invocation.span));
}

private string frameSourceId(string file, string[string] sourceIds)
{
    auto found = file in sourceIds;
    if (found is null)
        found = normalizedPath(file) in sourceIds;
    if (found !is null)
        return *found;
    if (file.startsWith("tfxb:"))
    {
        string best;
        size_t bestLength;
        foreach (prefix, id; sourceIds)
            if (prefix.startsWith("tfxb:") && file.startsWith(prefix)
                    && prefix.length > bestLength)
            {
                best = id;
                bestLength = prefix.length;
            }
        if (best.length != 0)
            return best;
    }
    return null;
}

private string pathForLogical(string root, string path, ref size_t externalOrdinal,
        ref string[string] logicalPaths)
{
    string relative;
    try
        relative = relativePath(path, dirName(root)).replace('\\', '/');
    catch (Exception)
        relative = null;
    string result;
    if (relative.length != 0 && !relative.startsWith("../") && relative != ".."
            && relative !in logicalPaths)
        result = relative;
    else
    {
        do
        {
            result = "_external/" ~ externalOrdinal.to!string ~ "/" ~ baseName(path);
            ++externalOrdinal;
        }
        while (result in logicalPaths);
    }
    logicalPaths[result] = path;
    return result;
}

private JsonValue bundlePosition(BundlePosition value)
{
    return jsonObject(member("line", cast(long) value.line),
            member("column", cast(long) value.column));
}

private JsonValue bundleSpan(BundleSpan value)
{
    return jsonObject(member("start", bundlePosition(value.start)),
            member("end", bundlePosition(value.end)));
}

private JsonValue manifestJson(BundleManifest manifest)
{
    JsonMember[] flags;
    auto flagNames = manifest.flags.keys.dup;
    flagNames.sort;
    foreach (name; flagNames)
        flags ~= member(name, manifest.flags[name]);
    JsonValue[] files;
    foreach (file; manifest.files)
    {
        JsonMember[] fields = [member("id", file.id), member("kind", file.kind)];
        if (file.kind == "source")
            fields ~= member("sourceKind", file.sourceKind);
        fields ~= [member("archivePath", file.archivePath), member("logicalPath", file.logicalPath),
            member("size", cast(long) file.size), member("sha256", file.sha256)];
        files ~= jsonObject(fields);
    }
    JsonValue[] dependencies;
    foreach (dependency; manifest.dependencies)
    {
        JsonMember[] fields = [member("from", dependency.from), member("kind", dependency.kind),
            member("path", dependency.path), member("target", dependency.target)];
        if (dependency.kind == "bundleimport")
            fields ~= member("selector", dependency.selector);
        fields ~= member("span", bundleSpan(dependency.span));
        dependencies ~= jsonObject(fields);
    }
    JsonValue[] fragments;
    foreach (fragment; manifest.fragments)
        fragments ~= jsonObject(member("selector", fragment.selector),
                member("kind", fragment.kind),
                member("title", fragment.title.isNull ? jsonNull() : jsonOf(fragment.title.get)),
                member("source", fragment.source), member("span", bundleSpan(fragment.span)));
    return jsonObject(member("format", manifest.format), member("version", 1L),
            member("producer", jsonObject(member("name", manifest.producerName),
                member("version", manifest.producerVersion))), member("root", manifest.root),
            member("flags", jsonObject(flags)), member("files", jsonArray(files)),
            member("dependencies", jsonArray(dependencies)), member("fragments", jsonArray(fragments)));
}

private string sourceId(string path, TraceSource[] sources)
{
    foreach (index, source; sources)
        if (source.path == path)
            return "source:" ~ index.to!string;
    return null;
}

private string assetId(string path, TraceAsset[] assets)
{
    foreach (index, asset; assets)
        if (asset.path == path)
            return "asset:" ~ index.to!string;
    return null;
}

private string bundleId(string path, TraceBundle[] bundles)
{
    foreach (index, bundle; bundles)
        if (bundle.path == path)
            return "bundle:" ~ index.to!string;
    return null;
}

private string dependencyTargetId(TraceDependency dependency, TraceSource[] sources,
        TraceAsset[] assets, TraceBundle[] bundles)
{
    final switch (dependency.kind)
    {
    case "import":
    case "macroimport":
        return sourceId(dependency.target, sources);
    case "asset":
        return assetId(dependency.target, assets);
    case "bundleimport":
        return bundleId(dependency.target, bundles);
    }
}

/** Build a v1 Bundle from a filesystem root. */
BundleBuildResult buildBundle(string inputPath, BundleBuildOptions options = BundleBuildOptions.init)
{
    if (!inputPath.endsWith(".tfx"))
        throw inputError("Bundle input must have a .tfx extension");
    const rootPath = normalizedPath(inputPath);
    immutable(ubyte)[] sourceBytes;
    try
        sourceBytes = cast(immutable(ubyte)[]) read(inputPath);
    catch (Exception error)
        throw inputError("cannot read Bundle input '" ~ inputPath ~ "': " ~ error.msg);

    CompilationTrace trace;
    auto session = new CompilationSession(builtinDirectives(), null, null, &trace,
            options.limits, options.cacheRoot);
    session.compileRoot(decodeUtf8(sourceBytes), inputPath, sourceBytes, options.flags);

    BundleManifest manifest;
    manifest.format = bundleFormat;
    manifest.version_ = 1;
    manifest.producerName = "texflux";
    import texflux : texfluxVersion;
    manifest.producerVersion = texfluxVersion;
    manifest.flags = trace.flags;

    string[string] sourceIds;
    string[string] logicalPaths;
    ArchiveEntry[] entries = [ArchiveEntry("manifest.json", null)];
    size_t externalOrdinal;
    ulong expandedTotal;
    void checkPayload(ulong size)
    {
        if (size > options.limits.maxEntryBytes
                || expandedTotal + size > options.limits.maxExpandedBytes)
            throw bundleLimitError("Bundle exceeds its expanded payload limit");
        expandedTotal += size;
    }
    foreach (index, source; trace.sources)
    {
        const id = "source:" ~ index.to!string;
        sourceIds[source.path] = id;
        checkPayload(source.data.length);
        const archivePath = "payload/source/" ~ formatOrdinal(index)
            ~ (source.sourceKind == "macro" ? ".tfxm" : ".tfx");
        manifest.files ~= BundleFile(id, "source", source.sourceKind, archivePath,
                pathForLogical(rootPath, source.path, externalOrdinal, logicalPaths),
                source.data.length,
                digest(source.data));
        entries ~= ArchiveEntry(archivePath, source.data);
    }
    foreach (index, asset; trace.assets)
    {
        const id = "asset:" ~ index.to!string;
        checkPayload(asset.data.length);
        const archivePath = "payload/asset/" ~ formatOrdinal(index) ~ ".bin";
        manifest.files ~= BundleFile(id, "asset", "", archivePath,
                pathForLogical(rootPath, asset.path, externalOrdinal, logicalPaths),
                asset.data.length,
                digest(asset.data));
        entries ~= ArchiveEntry(archivePath, asset.data);
    }
    foreach (index, nested; trace.bundles)
    {
        const id = "bundle:" ~ index.to!string;
        checkPayload(nested.data.length);
        const archivePath = "payload/bundle/" ~ formatOrdinal(index) ~ ".tfxb";
        manifest.files ~= BundleFile(id, "bundle", "", archivePath,
                pathForLogical(rootPath, nested.path, externalOrdinal, logicalPaths),
                nested.data.length,
                digest(nested.data));
        entries ~= ArchiveEntry(archivePath, nested.data);
        sourceIds["tfxb:" ~ digest(nested.data) ~ "!/"] = id;
    }

    manifest.root = sourceId(trace.root, trace.sources);
    foreach (dependency; trace.dependencies)
    {
        const from = sourceId(dependency.from, trace.sources);
        const target = dependencyTargetId(dependency, trace.sources, trace.assets,
                trace.bundles);
        if (from is null || target is null)
            throw edgeError(dependency.span, "Bundle trace contains an unknown dependency edge");
        manifest.dependencies ~= BundleDependency(from, dependency.kind, dependency.path,
                target, dependency.selector, manifestSpan(dependency.span));
    }
    sourceIds["<root>"] = manifest.root;
    manifest.fragments = indexFrames(trace.document, sourceIds);
    auto json = cast(immutable(ubyte)[]) dumpJson(manifestJson(manifest), false);
    if (json.length > options.limits.maxManifestBytes)
        throw bundleLimitError("Bundle manifest exceeds the manifest byte limit");
    checkPayload(json.length);
    if (cast(ulong) entries.length > options.limits.maxEntries)
        throw bundleLimitError("Bundle exceeds its entry limit");
    entries[0] = ArchiveEntry("manifest.json", json);
    auto archive = writeArchive(entries);
    if (archive.length > options.limits.maxArchiveBytes)
        throw bundleLimitError("Bundle archive exceeds the archive byte limit");
    // Reuse the reader's complete validation so writer limits and the emitted
    // manifest/hash contract cannot drift apart.
    readBundleManifest(archive, options.limits);
    return BundleBuildResult(archive, manifest);
}

private string formatOrdinal(size_t value)
{
    import std.format : format;

    return format("%06d", value);
}

/** Write a completed archive without exposing a partially-written output. */
void writeBundleAtomic(string outputPath, const(ubyte)[] bytes)
{
    const temporary = outputPath ~ ".texflux-tmp";
    try
    {
        write(temporary, bytes);
        rename(temporary, outputPath);
    }
    catch (Exception error)
    {
        try
        {
            if (exists(temporary))
                remove(temporary);
        }
        catch (Exception) {}
        throw outputError("cannot atomically write Bundle '" ~ outputPath ~ "': " ~ error.msg);
    }
}

private JsonValue fragmentJson(BundleFragment fragment)
{
    return jsonObject(member("selector", fragment.selector), member("kind", fragment.kind),
            member("title", fragment.title.isNull ? jsonNull() : jsonOf(fragment.title.get)),
            member("source", fragment.source), member("span", bundleSpan(fragment.span)));
}

string serializeBundleIndex(BundleIndex index, bool pretty = false)
{
    JsonValue[] fragments;
    foreach (fragment; index.fragments)
        fragments ~= fragmentJson(fragment);
    return dumpJson(jsonObject(member("format", index.format), member("version", 1L),
            member("producer", jsonObject(member("name", index.producerName),
                member("version", index.producerVersion))),
            member("bundle", jsonObject(member("format", index.bundleFormat),
                member("version", cast(long) index.bundleVersion))),
            member("fragments", jsonArray(fragments))), pretty);
}

private string frameSelectorIndex(string selector, SourceSpan span)
{
    if (!selector.startsWith("frame:") || selector.length == 6 || selector[6] == '0')
        throw selectorError(span, "invalid Bundle frame selector '" ~ selector ~ "'");
    foreach (char c; selector[6 .. $])
        if (c < '0' || c > '9')
            throw selectorError(span, "invalid Bundle frame selector '" ~ selector ~ "'");
    return selector;
}

private string defaultCacheRoot()
{
    version (Windows)
        return buildPath(environment.get("LOCALAPPDATA", "."), "texflux", "bundles-v1");
    version (OSX)
        return buildPath(environment.get("HOME", "."), "Library", "Caches", "texflux",
                "bundles-v1");
    else
        return buildPath(environment.get("XDG_CACHE_HOME",
                buildPath(environment.get("HOME", "."), ".cache")), "texflux", "bundles-v1");
}

private SourceSpan logicalSpan(SourceSpan span, string[string] logical)
{
    if (span.file.startsWith("tfxb:"))
        return span;
    auto found = normalizedPath(span.file) in logical;
    if (found is null)
        return span;
    span.file = *found;
    return span;
}

private void logicalParts(ref Nullable!SourceText parts, string[string] logical)
{
    if (parts.isNull)
        return;
    auto values = parts.get.dup;
    foreach (ref fragment; values)
        fragment.span = logicalSpan(fragment.span, logical);
    parts = values.nullable;
}

private Block* logicalBlock(Block* source, string[string] logical)
{
    Node[] nodes;
    foreach (node; source.nodes)
        nodes ~= logicalNode(node, logical);
    return new Block(nodes, logicalSpan(source.span, logical));
}

private Node logicalNode(Node node, string[string] logical)
{
    return node.match!(
        (RawTex raw) {
            raw.span = logicalSpan(raw.span, logical);
            logicalParts(raw.parts, logical);
            return Node(raw);
        },
        (GenericInvocation invocation) {
            invocation.span = logicalSpan(invocation.span, logical);
            invocation.arguments = invocation.arguments.dup;
            foreach (ref argument; invocation.arguments)
            {
                argument.span = logicalSpan(argument.span, logical);
                logicalParts(argument.parts, logical);
                argument.value.match!(
                    (string _) {},
                    (Block* nested) { argument.value = ArgumentValue(logicalBlock(nested, logical)); },
                );
            }
            if (invocation.body_ !is null)
                invocation.body_ = logicalBlock(invocation.body_, logical);
            return Node(invocation);
        },
        (BraceGroup group) {
            group.span = logicalSpan(group.span, logical);
            logicalParts(group.headerParts, logical);
            group.body_ = logicalBlock(group.body_, logical);
            return Node(group);
        },
        (other) { return Node(other); },
    );
}

private bool cacheMatches(string cache, BundleManifest manifest)
{
    try
    {
        foreach (file; manifest.files)
        {
            const output = buildPath(cache, file.archivePath);
            if (!output.isFile)
                return false;
            const data = cast(immutable(ubyte)[]) read(output);
            if (data.length != file.size || digest(data) != file.sha256)
                return false;
        }
    }
    catch (Exception)
    {
        return false;
    }
    return true;
}

private void cleanupTemporary(string path)
{
    try
    {
        if (!exists(path))
            return;
        if (path.isFile)
            remove(path);
        else
            rmdirRecurse(path);
    }
    catch (Exception) {}
}

private string materializeBundle(string cacheRoot, string bundleDigest,
        BundleManifest manifest, ArchiveEntry[] entries)
{
    const cache = buildPath(absoluteNormalized(cacheRoot), bundleDigest);
    if (exists(cache))
    {
        if (cacheMatches(cache, manifest))
            return cache;
        throw cacheError("Bundle cache is incomplete or has been modified: " ~ cache);
    }

    const sequence = atomicOp!"+="(cacheTemporarySequence, 1UL);
    const temporary = cache ~ ".tmp-" ~ thisProcessID.to!string ~ "-"
        ~ sequence.to!string;
    try
    {
        if (exists(temporary))
            cleanupTemporary(temporary);
        mkdirRecurse(temporary);
        foreach (file; manifest.files)
        {
            const output = buildPath(temporary, file.archivePath);
            mkdirRecurse(dirName(output));
            write(output, payload(entries, file.archivePath).data);
        }
        rename(temporary, cache);
    }
    catch (Exception error)
    {
        if (exists(cache) && cacheMatches(cache, manifest))
        {
            cleanupTemporary(temporary);
            return cache;
        }
        cleanupTemporary(temporary);
        throw cacheError("cannot materialize Bundle cache '" ~ cache ~ "': " ~ error.msg);
    }
    return cache;
}

/** Compile one filesystem Bundle and return the selected canonical frame. */
Node[] resolveBundleFrame(string path, string selector, SourceSpan span,
        CompilationSession parent = null)
{
    auto bytes = readBundleBytes(path);
    const bundleDigest = digest(bytes);
    auto state = parent is null ? new BundleResolutionState : parent.bundleResolutionState();
    const limits = parent is null ? BundleLimits.init : parent.bundleLimits();
    if (state.depth() >= limits.maxNestedDepth)
        throw bundleLimitError("Bundle import exceeds the nested Bundle depth limit");
    SourceSpan first;
    if (state.find(bundleDigest, first))
        throw cycleError(span, "Bundle import cycle detected for '" ~ path ~ "'", first);
    state.enter(bundleDigest, span);
    scope (exit)
        state.leave(bundleDigest);

    auto validated = readValidatedBundle(bytes, limits);
    auto manifest = validated.manifest;
    auto entries = validated.entries;
    frameSelectorIndex(selector, span);
    Nullable!BundleFragment fragment;
    foreach (value; manifest.fragments)
        if (value.selector == selector)
        {
            fragment = value.nullable;
            break;
        }
    if (fragment.isNull)
        throw selectorError(span, "Bundle frame selector not found: " ~ selector);

    // v1's archive stores source snapshots.  This first implementation uses a
    // private materialization directory so the existing module resolver can
    // remain unchanged; all content is still selected from the manifest edge
    // table and never from the caller's project.
    const configuredRoot = parent is null ? null : parent.bundleCacheRoot();
    const cacheRoot = configuredRoot is null || configuredRoot.length == 0
        ? defaultCacheRoot() : configuredRoot;
    const cache = materializeBundle(cacheRoot, bundleDigest, manifest, entries);
    string[string] materialized;
    string[string] sourceByPath;
    immutable(ubyte)[][string] dataByPath;
    immutable(ubyte)[][string] dataById;
    foreach (file; manifest.files)
    {
        const output = buildPath(cache, file.archivePath);
        const data = payload(entries, file.archivePath).data;
        materialized[file.id] = output;
        dataByPath[normalizedPath(output)] = data;
        dataById[file.id] = data;
        if (file.kind == "source")
            sourceByPath[normalizedPath(output)] = file.id;
    }
    ModulePathResolver paths = delegate(string importer, string written, string kind,
            SourceSpan edgeSpan) {
        auto from = normalizedPath(importer) in sourceByPath;
        if (from is null)
            throw edgeError(edgeSpan, "Bundle dependency source is not recorded");
        const dependencyKind = kind == ".tfx" || kind == "content" ? "import"
            : kind == ".tfxm" || kind == "macro" ? "macroimport" : "bundleimport";
        foreach (dependency; manifest.dependencies)
            if (dependency.from == *from && dependency.kind == dependencyKind
                    && dependency.path == written)
            {
                auto target = dependency.target in materialized;
                if (target is null)
                    throw edgeError(edgeSpan, "Bundle dependency target is not recorded");
                return (*target).replace("\\", "/");
            }
        throw edgeError(edgeSpan, "Bundle dependency edge is not recorded for '"
                ~ written ~ "'");
    };
    SourceReader reader = delegate(string display) {
        auto found = normalizedPath(display) in dataByPath;
        if (found is null)
            throw new Exception("Bundle dependency is not recorded: " ~ display);
        return *found;
    };
    AssetResolver assets = delegate(AssetReference reference) {
        auto from = normalizedPath(reference.ownerFile) in sourceByPath;
        if (from is null)
            throw edgeError(reference.span, "Bundle asset source is not recorded");
        foreach (dependency; manifest.dependencies)
            if (dependency.from == *from && dependency.kind == "asset"
                    && dependency.path == reference.path)
            {
                auto target = dependency.target in materialized;
                if (target is null)
                    throw edgeError(reference.span, "Bundle asset target is not recorded");
                return (*target).replace("\\", "/");
            }
        throw edgeError(reference.span, "Bundle asset dependency is not recorded for '"
                ~ reference.path ~ "'");
    };
    auto rootPath = materialized[manifest.root];
    auto rootData = dataById[manifest.root];
    string source;
    try
        source = decodeUtf8(rootData);
    catch (UnicodeDecodeError error)
        throw rootError("Bundle root source is not valid UTF-8: " ~ error.msg);
    auto session = new CompilationSession(builtinDirectives(), reader, assets, null,
            limits, cacheRoot, state, paths);
    Document document;
    try
        document = session.compileRoot(source, rootPath, rootData, manifest.flags);
    catch (FlagError error)
        throw flagsError("Bundle flags do not match its root source: " ~ error.msg);
    string[string] logical;
    string[string] logicalSourceIds;
    foreach (file; manifest.files)
        if (file.kind == "source")
        {
            logical[file.id] = "tfxb:" ~ bundleDigest ~ "!/" ~ file.logicalPath;
            logicalSourceIds[logical[file.id]] = file.id;
        }
        else if (file.kind == "bundle")
            logicalSourceIds["tfxb:" ~ digest(dataById[file.id]) ~ "!/"] = file.id;
    logicalSourceIds["<root>"] = manifest.root;
    string[string] logicalByPhysical;
    foreach (loaded; session.loaded)
    {
        auto id = normalizedPath(loaded.file) in sourceByPath;
        if (id is null)
            continue;
        auto identity = *id in logical;
        if (identity !is null)
            logicalByPhysical[normalizedPath(loaded.file)] = *identity;
    }
    document.body_ = logicalBlock(document.body_, logicalByPhysical);
    LoadedSource[] external;
    foreach (loaded; session.loaded)
    {
        auto copy = loaded;
        auto identity = normalizedPath(loaded.file) in logicalByPhysical;
        if (identity !is null)
            copy.file = *identity;
        external ~= copy;
    }
    if (parent !is null)
        parent.recordExternalSources(external);
    Node[] result;
    size_t selected;
    BundleFragment[] actual;
    foreach (value; document.body_.nodes)
    {
        if (!value.has!GenericInvocation)
            continue;
        auto invocation = value.get!GenericInvocation;
        if (invocation.name == "frame" && invocation.body_ !is null)
        {
            ++selected;
            auto indexed = frameFragment(invocation, selected, logicalSourceIds);
            actual ~= indexed;
            if (indexed.selector == selector)
                result ~= value;
        }
    }
    if (actual.length != manifest.fragments.length)
        throw recompileIndexError("Bundle manifest frame index does not match recompilation");
    foreach (index, expected; manifest.fragments)
    {
        const found = actual[index];
        if (expected.selector != found.selector || expected.kind != found.kind
                || expected.title != found.title || expected.source != found.source
                || expected.span != found.span)
            throw recompileIndexError("Bundle manifest frame index does not match recompilation");
    }
    if (result.length == 0)
        throw recompileIndexError("Bundle manifest frame index does not match recompilation");
    return result;
}

unittest
{
    assert(formatOrdinal(0) == "000000");
    assert(formatOrdinal(12) == "000012");
}
