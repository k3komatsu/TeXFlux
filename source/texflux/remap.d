/**
 * Pointing a SyncTeX file back at what the author wrote.
 *
 * The TeX engine records which generated line each box came from, which is one
 * step short of useful: the author wrote a `.tfx`, not the `.tex` the engine
 * saw. This reads the map written beside the generated file and rewrites each
 * of those records, so a click in the PDF lands on the source.
 *
 * The map is checked before it is trusted. Every file it names is hashed and
 * compared, because a map that describes a different build would send a reader
 * to the wrong line with no sign that anything was wrong.
 *
 * A record that names a line but no column is resolved by rank: what the author
 * wrote outranks a delimiter, which outranks generated filler. If the survivors
 * still disagree about which source line they came from, that is reported
 * rather than guessed.
 */
module texflux.remap;

import std.algorithm : all, any, canFind, filter, find, fold, map, max, minElement, sort, uniq;
import std.array : array, join;
import std.ascii : isDigit;
import std.conv : to;
import std.exception : basicExceptionCtors, enforce;
import std.file : exists, FileException, read, remove, rename, write;
import std.json : JSONType, JSONValue, parseJSON;
import std.path : absolutePath, baseName, buildNormalizedPath, dirName;
import std.range : drop, empty, front, only, walkLength, zip;
import std.traits : EnumMembers;
import std.typecons : Nullable, Tuple, tuple;

import texflux.interchange : digest;
import texflux.paths : normalizedPath, openFailure, samePath;
import texflux.render : RenderRole;
import texflux.source : SourcePosition;
import texflux.text : decodeUtf8;
import texflux.synctex;

/// A source map that cannot be applied safely.
class RemapError : Exception
{
    mixin basicExceptionCtors;
}

/// One file a map describes.
struct SourceMapSource
{
    int id;
    string path;
    string sha256;
}

/// One run of generated text, and where it came from.
struct SourceMapping
{
    SourcePosition generatedStart;
    SourcePosition generatedEnd;
    int sourceId;
    SourcePosition sourceStart;
    SourcePosition sourceEnd;
    string role;
}

/// One validated map.
struct SourceMap
{
    string path;
    string generatedPath;
    string generatedSha256;
    SourceMapSource[] sources;
    SourceMapping[] mappings;

    SourceMapSource sourceById(int id) const
    {
        auto found = sources.find!(source => source.id == id);
        enforce!RemapError(!found.empty, "source map has no source with id " ~ id.to!string);
        return found.front;
    }
}

/**
 * Without a column, what the author wrote outranks everything else.
 *
 * A generated line often mixes runs from several places -- an author's text
 * between braces this compiler generated -- and a reader who asked about the
 * line means the text.
 */
private int roleRank(string role)
{
    switch (role)
    {
    case "content":
        return 0;
    case "scaffold":
    case "open":
    case "close":
        return 1;
    case "synthetic":
        return 2;
    default:
        throw new RemapError("mapping has an invalid role");
    }
}

private bool isKnownRole(string role)
{
    return only(EnumMembers!RenderRole).canFind(role);
}

// ---------------------------------------------------------------------------
// Reading a map
// ---------------------------------------------------------------------------

private JSONValue demandObject(JSONValue value, string label)
{
    enforce!RemapError(value.type == JSONType.object, label ~ " must be an object");
    return value;
}

private JSONValue member(JSONValue object, string name)
{
    auto found = name in object.object;
    return found is null ? JSONValue(null) : *found;
}

/// An integer, which a boolean is not: JSON tells them apart and so does this.
private int demandInteger(JSONValue value, string label)
{
    enforce!RemapError(value.type == JSONType.integer || value.type == JSONType.uinteger,
            label ~ " must be an integer");
    return cast(int) value.integer;
}

private SourcePosition demandPosition(JSONValue value, string label)
{
    auto data = demandObject(value, label);
    const line = demandInteger(member(data, "line"), label ~ ".line");
    const column = demandInteger(member(data, "column"), label ~ ".column");
    enforce!RemapError(line >= 1 && column >= 1, label ~ " must be one-based");
    return SourcePosition(line, column);
}

private string demandHash(JSONValue value, string label)
{
    enforce!RemapError(value.type == JSONType.string && value.str.length == 64
            && value.str.all!(c => c.isDigit || (c >= 'a' && c <= 'f')),
            label ~ " must be a lowercase SHA-256 hex digest");
    return value.str;
}

/// A path the map stores, resolved against the map's own directory.
private string resolvedPath(string base, JSONValue stored, string label)
{
    enforce!RemapError(stored.type == JSONType.string && stored.str.length != 0,
            label ~ " must be a non-empty path");
    return buildNormalizedPath(absolutePath(stored.str, base));
}

private string fileHash(string path, string label)
{
    try
        return digest(cast(const(ubyte)[]) read(path));
    catch (Exception error)
        throw new RemapError("cannot read " ~ label ~ " " ~ path ~ ": "
                ~ openFailure(error, path));
}

private SourceMapping validateMapping(JSONValue value, const int[] sourceIds, size_t index)
{
    const label = "mappings[" ~ index.to!string ~ "]";
    auto data = demandObject(value, label);
    auto generated = demandObject(member(data, "generated"), label ~ ".generated");
    auto source = demandObject(member(data, "source"), label ~ ".source");

    const generatedStart = demandPosition(member(generated, "start"), label ~ ".generated.start");
    const generatedEnd = demandPosition(member(generated, "end"), label ~ ".generated.end");
    enforce!RemapError(generatedStart < generatedEnd, label ~ " has an empty generated range");

    const sourceId = demandInteger(member(source, "id"), label ~ ".source.id");
    enforce!RemapError(sourceIds.canFind(sourceId),
            label ~ " references unknown source id " ~ sourceId.to!string);

    const sourceStart = demandPosition(member(source, "start"), label ~ ".source.start");
    const sourceEnd = demandPosition(member(source, "end"), label ~ ".source.end");
    enforce!RemapError(sourceStart <= sourceEnd, label ~ " has a reversed source range");

    auto role = member(data, "role");
    enforce!RemapError(role.type == JSONType.string && isKnownRole(role.str),
            label ~ " has an invalid role");

    return SourceMapping(generatedStart, generatedEnd, sourceId, sourceStart,
            sourceEnd, role.str);
}

/**
 * Read one map and check that it still describes the files it names.
 *
 * Every hash is verified here rather than at the point a mapping is used,
 * because a stale map is wrong about everything at once and saying so early
 * is the only useful answer.
 */
SourceMap loadSourceMap(string path)
{
    const mapPath = absolutePath(path);
    const base = dirName(mapPath);

    JSONValue payload;
    try
        payload = parseJSON(decodeUtf8(cast(const(ubyte)[]) read(mapPath)));
    catch (FileException error)
        throw new RemapError("cannot read source map " ~ mapPath ~ ": "
                ~ openFailure(error, mapPath));
    catch (Exception error)
        // A file that is there but is not a map is described by the decoder
        // or the JSON parser, not as a missing file.
        throw new RemapError("cannot read source map " ~ mapPath ~ ": " ~ error.msg);

    auto root = demandObject(payload, "source map");
    enforce!RemapError(member(root, "format").type == JSONType.string
            && member(root, "format").str == "texflux-source-map",
            "source map has an unsupported format");
    enforce!RemapError(member(root, "version").type == JSONType.integer
            && member(root, "version").integer == 1, "source map has an unsupported version");

    auto generated = demandObject(member(root, "generated"), "generated");
    const generatedPath = resolvedPath(base, member(generated, "path"), "generated.path");
    const generatedSha256 = demandHash(member(generated, "sha256"), "generated.sha256");
    enforce!RemapError(fileHash(generatedPath, "generated file") == generatedSha256,
            "generated file hash does not match source map: " ~ generatedPath);

    auto sourceValues = member(root, "sources");
    enforce!RemapError(sourceValues.type == JSONType.array, "sources must be an array");
    SourceMapSource[] sources;
    int[] sourceIds;
    foreach (index, value; sourceValues.array)
    {
        const label = "sources[" ~ index.to!string ~ "]";
        auto entry = demandObject(value, label);
        const id = demandInteger(member(entry, "id"), label ~ ".id");
        enforce!RemapError(!sourceIds.canFind(id), "duplicate source id " ~ id.to!string);
        sourceIds ~= id;
        const sourcePath = resolvedPath(base, member(entry, "path"), label ~ ".path");
        const sha256 = demandHash(member(entry, "sha256"), label ~ ".sha256");
        enforce!RemapError(fileHash(sourcePath, "source file") == sha256,
                "source file hash does not match source map: " ~ sourcePath);
        sources ~= SourceMapSource(id, sourcePath, sha256);
    }

    auto mappingValues = member(root, "mappings");
    enforce!RemapError(mappingValues.type == JSONType.array, "mappings must be an array");
    SourceMapping[] mappings;
    foreach (index, value; mappingValues.array)
        mappings ~= validateMapping(value, sourceIds, index);

    enforce!RemapError(!zip(mappings, mappings.drop(1))
            .any!(pair => pair[1].generatedStart < pair[0].generatedEnd),
            "generated mappings must be sorted and non-overlapping");

    return SourceMap(mapPath, generatedPath, generatedSha256, sources, mappings);
}

/// A document that rendered nothing maps nothing, and its map still loads.
unittest
{
    import std.file : remove, tempDir;
    import std.path : buildPath;
    import texflux.interchange : digest;

    const generated = buildPath(tempDir, "texflux-remap-empty.tex");
    const mapPath = generated ~ ".tfxmap";
    write(generated, "\n");
    write(mapPath, `{"format":"texflux-source-map","version":1,"generated":{"path":`
            ~ `"texflux-remap-empty.tex","sha256":"` ~ digest(cast(const(ubyte)[]) "\n")
            ~ `"},"sources":[],"mappings":[]}`);
    scope (exit)
    {
        remove(generated);
        remove(mapPath);
    }
    assert(loadSourceMap(mapPath).mappings.length == 0);
}

// ---------------------------------------------------------------------------
// Resolving a position
// ---------------------------------------------------------------------------

/// Whether a mapping covers any of one generated line.
private bool onLine(SourceMapping mapping, int line)
{
    const start = mapping.generatedStart;
    const end = mapping.generatedEnd;
    if (line < start.line || line > end.line)
        return false;
    if (start.line == end.line && end.line == line)
        return start.column < end.column;
    if (line == end.line)
        return end.column > 1;
    return true;
}

/// How many distinct source lines a set of mappings resolves to.
private size_t distinctTargets(SourceMap map, SourceMapping[] mappings)
{
    return mappings.map!(mapping => normalizedPath(map.sourceById(mapping.sourceId).path)
            ~ ":" ~ mapping.sourceStart.line.to!string).array.sort.uniq.walkLength;
}

/// Resolve one generated position to the source position it came from.
private SourceMapping selectMapping(SourceMap map, int line, Nullable!int column)
{
    enforce!RemapError(line > 0, "invalid generated line " ~ line.to!string);
    if (!column.isNull && column.get <= 0)
        column.nullify();

    if (!column.isNull)
    {
        const point = SourcePosition(line, column.get);
        auto covering = map.mappings.find!(mapping => mapping.generatedStart <= point
                && point < mapping.generatedEnd);
        enforce!RemapError(!covering.empty, "no source mapping for generated line "
                ~ line.to!string ~ ", column " ~ column.get.to!string);
        return covering.front;
    }

    auto candidates = map.mappings.filter!(mapping => onLine(mapping, line)).array;
    enforce!RemapError(candidates.length != 0,
            "no source mapping for generated line " ~ line.to!string);

    const best = candidates.map!(mapping => roleRank(mapping.role)).minElement;
    candidates = candidates.filter!(mapping => roleRank(mapping.role) == best).array;
    enforce!RemapError(distinctTargets(map, candidates) == 1,
            "ambiguous source mappings for generated line " ~ line.to!string);
    return candidates[0];
}

// ---------------------------------------------------------------------------
// Rewriting
// ---------------------------------------------------------------------------

/// One map, the inputs it replaces, and the tag each of its sources takes.
private struct Target
{
    SourceMap map;
    int[] generatedTags;
    int[int] sourceTags;
}

private string inputPath(SyncTeXInput record, string base)
{
    return buildNormalizedPath(absolutePath(cast(string) record.path, base));
}

private SyncTeXInput[] matchingInputs(SyncTeXDocument document, string target, string base)
{
    return document.inputs.filter!(record => samePath(inputPath(record, base), target)).array;
}

/**
 * Decide which SyncTeX input each map replaces, and which tag each source takes.
 *
 * A source already present as an input keeps that input's tag rather than
 * gaining a second one; anything new is given the next free tag.
 */
private Tuple!(Target[], "targets", Tuple!(int, immutable(ubyte)[])[], "newInputs") allocateTargets(
        SyncTeXDocument document, SourceMap[] maps, string base)
{
    auto inputTags = document.inputs.map!(record => record.tag).array;
    enforce!RemapError(inputTags.dup.sort.uniq.walkLength == inputTags.length,
            "SyncTeX contains duplicate Input tags");

    Tuple!(SourceMap, int[])[] matched;
    int[] claimed;
    foreach (map; maps)
    {
        auto matches = matchingInputs(document, map.generatedPath, base);
        enforce!RemapError(matches.length != 0,
                "generated file is not present in SyncTeX inputs: " ~ map.generatedPath);
        auto tags = matches.map!(record => record.tag).array;
        tags.sort();
        enforce!RemapError(!tags.any!(tag => claimed.canFind(tag)),
                "multiple source maps target the same SyncTeX input");
        claimed ~= tags;
        matched ~= tuple(map, tags);
    }

    int nextTag = inputTags.filter!(tag => tag > 0).fold!max(0) + 1;

    int[string] allocated;
    Tuple!(int, immutable(ubyte)[])[] newInputs;
    Target[] targets;
    foreach (entry; matched)
    {
        int[int] sourceTags;
        foreach (source; entry[0].sources)
        {
            auto existing = matchingInputs(document, source.path, base);
            int tag;
            if (existing.length != 0)
                tag = existing.map!(record => record.tag).minElement;
            else
            {
                const key = normalizedPath(source.path);
                if (auto found = key in allocated)
                    tag = *found;
                else
                {
                    tag = nextTag++;
                    allocated[key] = tag;
                    auto pathBytes = cast(immutable(ubyte)[]) source.path;
                    enforce!RemapError(!pathBytes.canFind!(b => b == '\n' || b == '\r'),
                            "source path cannot be an Input record: " ~ source.path);
                    newInputs ~= tuple(tag, pathBytes);
                }
            }
            sourceTags[source.id] = tag;
        }
        targets ~= Target(entry[0], entry[1], sourceTags);
    }
    return typeof(return)(targets, newInputs);
}

private immutable(ubyte)[] rewriteLink(SyncTeXLine line, int tag, int sourceLine)
{
    enforce!RemapError(!line.record.isNull && !line.record.get.link.isNull
            && !line.record.get.linkSpan.isNull, "cannot rewrite a record without a source link");
    const span = line.record.get.linkSpan.get;
    const replacement = cast(immutable(ubyte)[])(tag.to!string ~ "," ~ sourceLine.to!string);
    return line.body_[0 .. span[0]] ~ replacement ~ line.body_[span[1] .. $];
}

/**
 * Apply validated maps and return the SyncTeX bytes that result.
 *
 * The rewritten lines are parsed again before being written out, because the
 * anchors and the count are statements about byte offsets that the rewrite
 * moved.
 */
immutable(ubyte)[] remapDocument(SyncTeXDocument document, SourceMap[] maps,
        string synctexPath = null)
{
    import std.file : getcwd;

    enforce!RemapError(maps.length != 0, "at least one source map is required");
    const base = synctexPath is null ? getcwd() : dirName(absolutePath(synctexPath));

    auto allocation = allocateTargets(document, maps, base);
    Target[int] byTag;
    foreach (target; allocation.targets)
        foreach (tag; target.generatedTags)
            byTag[tag] = target;

    auto lines = document.lines.dup;
    foreach (index, line; lines)
    {
        if (line.record.isNull || line.record.get.link.isNull)
            continue;
        auto target = line.record.get.link.get.tag in byTag;
        if (target is null)
            continue;
        auto mapping = selectMapping(target.map, line.record.get.link.get.line,
                line.record.get.link.get.column);
        lines[index].body_ = rewriteLink(line, target.sourceTags[mapping.sourceId],
                mapping.sourceStart.line);
    }

    if (allocation.newInputs.length != 0)
        lines = withNewInputs(lines, allocation.newInputs, document.newline);

    auto reparsed = parseSyncTeX(lines.map!(line => line.rawLine).join);
    reparsed.container = document.container;
    return serializeSyncTeX(reparsed);
}

/// Insert the new inputs after the last one the preamble already had.
private SyncTeXLine[] withNewInputs(SyncTeXLine[] lines,
        Tuple!(int, immutable(ubyte)[])[] newInputs, immutable(ubyte)[] newline)
{
    SyncTeXLine[] inserted;
    foreach (entry; newInputs)
        inserted ~= SyncTeXLine(cast(immutable(ubyte)[])("Input:" ~ entry[0].to!string ~ ":")
                ~ entry[1], newline, "preamble");

    ptrdiff_t anchor = -1;
    foreach (index, line; lines)
        if (line.section == "preamble" && !line.input.isNull)
            anchor = index;
    if (anchor < 0)
        foreach (index, line; lines)
            if (line.body_.length >= 16 && line.body_[0 .. 16] == cast(immutable(ubyte)[]) "SyncTeX Version:")
            {
                anchor = index;
                break;
            }
    if (anchor >= 0 && lines[anchor].newline.length == 0)
        lines[anchor].newline = newline;

    const at = anchor + 1;
    return lines[0 .. at] ~ inserted ~ lines[at .. $];
}

/// Validate maps, remap a byte stream, and keep its container.
immutable(ubyte)[] remapSyncTeX(immutable(ubyte)[] data, string[] mapPaths,
        string synctexPath = null)
{
    auto maps = mapPaths.map!(path => loadSourceMap(path)).array;
    return remapDocument(parseSyncTeX(data), maps, synctexPath);
}

/**
 * Remap a file in place, or write an explicit output path.
 *
 * The replacement is atomic and checks, just before it happens, that the file
 * it read has not changed underneath it. A viewer reading a half-written
 * SyncTeX file would report nonsense, and a build that rewrote one the engine
 * was still producing would silently lose the engine's work.
 */
void remapSyncTeXFile(string path, string[] mapPaths, string outputPath = null)
{
    import std.file : getAttributes;

    const source = absolutePath(path);
    const destination = outputPath is null ? source : absolutePath(outputPath);

    immutable(ubyte)[] original;
    uint attributes;
    Snapshot before;
    try
    {
        original = cast(immutable(ubyte)[]) read(source);
        attributes = getAttributes(source);
        before = snapshot(source);
    }
    catch (Exception error)
        throw new RemapError("cannot read SyncTeX file " ~ source ~ ": "
                ~ openFailure(error, source));

    const rewritten = remapSyncTeX(original, mapPaths, source);
    atomicWrite(destination, rewritten, attributes,
            samePath(destination, source) ? source : null, before);
}

/// What a file looked like, for noticing that it changed underneath us.
private struct Snapshot
{
    ulong device;
    ulong inode;
    ulong size;
    long modified;
    long changed;
}

/// The file's identity, size and times, or the operating system's refusal.
private Snapshot snapshot(string path)
{
    version (Posix)
    {
        import core.stdc.errno : errno;
        import core.sys.posix.sys.stat : stat, stat_t;
        import std.file : timeLastModified, timeStatusChanged;
        import std.string : toStringz;

        stat_t info;
        if (stat(path.toStringz, &info) != 0)
            throw new FileException(path, errno);
        return Snapshot(info.st_dev, info.st_ino, info.st_size,
                timeLastModified(info).stdTime, timeStatusChanged(info).stdTime);
    }
    else
    {
        import std.file : getSize, timeLastModified;

        return Snapshot(0, 0, getSize(path), timeLastModified(path).stdTime, 0);
    }
}

private void atomicWrite(string path, immutable(ubyte)[] data, uint attributes,
        string checkPath, Snapshot expected)
{
    import std.file : setAttributes;
    import std.random : uniform;
    import std.stdio : File;

    const temporary = dirName(path) ~ "/." ~ path.baseName ~ "."
        ~ uniform(0, int.max).to!string ~ ".tmp";
    scope (failure)
        if (exists(temporary))
            remove(temporary);
    try
    {
        auto stream = File(temporary, "wb");
        stream.rawWrite(data);
        // On the disk before the rename makes it the file.
        stream.sync();
        stream.close();
        if (checkPath !is null)
        {
            Snapshot current;
            try
                current = snapshot(checkPath);
            catch (FileException error)
                throw new RemapError("cannot stat SyncTeX file " ~ checkPath ~ ": "
                        ~ openFailure(error, checkPath));
            enforce!RemapError(current == expected, "SyncTeX file changed during remapping");
        }
        setAttributes(temporary, attributes);
        rename(temporary, path);
        syncDirectory(dirName(path));
    }
    catch (RemapError error)
        throw error;
    catch (Exception error)
        throw new RemapError("cannot replace " ~ path ~ ": " ~ error.msg);
}

/// Ask the disk to record the directory entry too; not being able to is no error.
private void syncDirectory(string path)
{
    version (Posix)
    {
        import core.sys.posix.fcntl : O_RDONLY, open;
        import core.sys.posix.unistd : close, fsync;
        import std.string : toStringz;

        const descriptor = open(path.toStringz, O_RDONLY);
        if (descriptor >= 0)
        {
            fsync(descriptor);
            close(descriptor);
        }
    }
}
