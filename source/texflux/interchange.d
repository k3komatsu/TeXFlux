/**
 * The shape the published JSON documents share.
 *
 * The external AST and the diagnostics report open with the same header --
 * format, version, producer, the root's id and a table of every source read --
 * and spell positions and spans the same way, so both are written here once.
 *
 * A source's digest is taken over the bytes the compilation actually read,
 * which for an editor's unsaved buffer is that buffer rather than the file on
 * disk. That is what lets a consumer tell whether a report still describes
 * what it is looking at.
 */
module texflux.interchange;

import std.digest : LetterCase, toHexString;
import std.digest.sha : sha256Of;
import std.exception : enforce;
import std.path : dirSeparator;
import std.string : replace;

import texflux : texfluxVersion;
import texflux.json;
import texflux.errors : ValueError;
import texflux.source : LoadedSource, SourcePosition, SourceSpan;

/// Encodes one span against a source table, refusing a file it lacks.
alias SpanEncoder = JsonValue delegate(SourceSpan span);

/// One position, as the two members every published format spells it with.
JsonValue encodePosition(SourcePosition position)
{
    return jsonObject(member("line", cast(long) position.line),
            member("column", cast(long) position.column));
}

/// Number the sources in load order and encode spans against those ids.
SpanEncoder spanEncoder(LoadedSource[] sources)
{
    long[string] ids;
    foreach (index, source; sources)
    {
        enforce!ValueError(source.file !in ids, "sources must have unique file names");
        ids[source.file] = index;
    }

    return delegate(SourceSpan span) {
        const id = enforce!ValueError(span.file in ids,
                "span names a file that was not loaded: " ~ span.file);
        return jsonObject(member("source", *id), member("start", encodePosition(span.start)),
                member("end", encodePosition(span.end)));
    };
}

/// The members every published document starts with; the root is always zero.
JsonMember[] header(string documentFormat, LoadedSource[] sources)
{
    enforce!ValueError(sources.length != 0,
            "a " ~ documentFormat ~ " document needs at least the root source");

    JsonValue[] table;
    foreach (index, source; sources)
        table ~= jsonObject(member("id", cast(long) index),
                member("file", source.file.replace(dirSeparator, "/")),
                member("sha256", digest(source.data)));

    return [
        member("format", documentFormat),
        member("version", 1L),
        member("producer", jsonObject(member("name", "texflux"),
                member("version", texfluxVersion))),
        member("root", 0L),
        member("sources", jsonArray(table)),
    ];
}

/// The digest a published document records for one file's bytes.
string digest(const(ubyte)[] data)
{
    return sha256Of(data).toHexString!(LetterCase.lower).idup;
}

///
unittest
{
    assert(digest(cast(immutable ubyte[]) "abc")
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}
