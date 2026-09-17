/**
 * The map from generated TeX back to what the author wrote.
 *
 * A `.tfxmap` is written beside the generated file and records, for every run
 * of output, which range of which source it came from. That is what makes the
 * SyncTeX remapping possible, and through it what lets a click in the PDF land
 * on the `.tfx` line rather than on a generated one.
 *
 * Only the sources that actually contributed a run are listed, because every
 * listed source becomes a SyncTeX input. A macro module contributes none; so
 * does a content module whose every line was dropped by a conditional. That is
 * why this cannot share the header the other two published formats use, whose
 * table lists every file the compilation read.
 */
module texflux.sourcemap;

import std.algorithm : all, any, canFind, countUntil;
import std.exception : enforce;
import std.path : dirName, dirSeparator, relativePath;
import std.range : drop, zip;
import std.string : replace;

import texflux : texfluxVersion;
import texflux.errors : ValueError;
import texflux.interchange : digest, encodePosition;
import texflux.json;
import texflux.paths : absoluteNormalized;
import texflux.render : CompilationResult, RenderedFragment;

/**
 * Where one file sits, as the map file's own directory sees it.
 *
 * A map that moves with the files it describes keeps working, which a map full
 * of absolute paths would not.
 */
private string storedPath(string path, string mapPath)
{
    return relativePath(absoluteNormalized(path), dirName(absoluteNormalized(mapPath)))
        .replace(dirSeparator, "/");
}

/**
 * Refuse a run sequence that could not have come from one rendering.
 *
 * The consumer reads these in order and assumes they do not overlap, so the
 * assumption is checked here rather than discovered later as a wrong answer.
 */
private void validateFragments(RenderedFragment[] fragments)
{
    enforce!ValueError(fragments.all!(fragment => fragment.generated.start < fragment.generated.end),
            "generated fragments must have non-empty ranges");
    enforce!ValueError(!zip(fragments, fragments.drop(1))
            .any!(pair => pair[1].generated.start < pair[0].generated.end),
            "generated fragments must be sorted and non-overlapping");
}

/// No fragments and one fragment both have nothing to overlap.
unittest
{
    import texflux.render : GeneratedSpan;
    import texflux.source : SourcePosition;

    validateFragments([]);
    validateFragments([RenderedFragment("x", GeneratedSpan(SourcePosition(1, 1),
            SourcePosition(1, 2)))]);
}

/**
 * Number the source files this result's runs actually name.
 *
 * The root is always zero, so a document with no imports writes the map it
 * always did. Every other file follows in order of first appearance, which the
 * document's own order decides.
 */
private string[] sourceOrder(CompilationResult result)
{
    enforce!ValueError(result.sources.length != 0, "a source map needs at least the root source");

    auto order = [result.sources[0].file];
    foreach (fragment; result.rendered.fragments)
    {
        if (fragment.source.isNull)
            continue;
        const name = fragment.source.get.file;
        enforce!ValueError(result.sources.canFind!(source => source.file == name),
                "span names a file that was not loaded: " ~ name);
        if (!order.canFind(name))
            order ~= name;
    }
    return order;
}

/**
 * Serialize one compiled result as the map that goes beside it.
 *
 * `generatedBytes`, when given, must be the UTF-8 bytes of the result's text:
 * the digest has to describe the file that was actually written.
 */
string serializeSourceMap(CompilationResult result, string generatedPath, string mapPath,
        const(ubyte)[] generatedBytes = null)
{
    const encoded = cast(immutable(ubyte)[]) result.text;
    if (generatedBytes is null)
        generatedBytes = encoded;
    enforce!ValueError(generatedBytes == encoded,
            "generatedBytes must be the UTF-8 bytes of the rendered text");
    validateFragments(result.rendered.fragments);

    auto order = sourceOrder(result);
    JsonValue[] sources;
    foreach (index, name; order)
    {
        const at = result.sources.countUntil!(source => source.file == name);
        sources ~= jsonObject(member("id", cast(long) index),
                member("path", storedPath(result.sources[at].path, mapPath)),
                member("sha256", digest(result.sources[at].data)));
    }

    JsonValue[] mappings;
    foreach (fragment; result.rendered.fragments)
    {
        if (fragment.source.isNull)
            continue;
        const span = fragment.source.get;
        mappings ~= jsonObject(
                member("generated", jsonObject(
                    member("start", encodePosition(fragment.generated.start)),
                    member("end", encodePosition(fragment.generated.end)))),
                member("source", jsonObject(
                    member("id", cast(long) order.countUntil(span.file)),
                    member("start", encodePosition(span.start)),
                    member("end", encodePosition(span.end)))),
                member("role", cast(string) fragment.role));
    }

    return dumpJson(jsonObject(
            member("format", "texflux-source-map"),
            member("version", 1L),
            member("producer", jsonObject(member("name", "texflux"),
                member("version", texfluxVersion))),
            member("generated", jsonObject(
                member("path", storedPath(generatedPath, mapPath)),
                member("sha256", digest(generatedBytes)))),
            member("sources", jsonArray(sources)),
            member("mappings", jsonArray(mappings))));
}
