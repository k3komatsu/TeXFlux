/**
 * Where a piece of a document came from.
 *
 * Every major node and every diagnostic carries a half-open span, so an editor
 * can jump to what a message is about and the renderer can record what each
 * run of generated TeX was written from.
 */
module texflux.source;

import std.algorithm : count, map;
import std.array : join;
import std.format : format;
import std.string : lastIndexOf, representation;
import std.typecons : tuple;

import texflux.text : countCodePoints;

/// A one-based position whose columns count code points.
struct SourcePosition
{
    int line = 1;
    int column = 1;

    /**
     * The position after text whose line endings are already normalized.
     *
     * Only a line feed advances a line, because the parser normalizes the
     * other two spellings away and the renderer emits nothing else.
     */
    SourcePosition advance(string text) const @safe pure
    {
        const breaks = text.representation.count('\n');
        if (breaks == 0)
            return SourcePosition(line, column + cast(int) text.countCodePoints);
        const tail = text[text.lastIndexOf('\n') + 1 .. $];
        return SourcePosition(line + cast(int) breaks, 1 + cast(int) tail.countCodePoints);
    }

    int opCmp(const SourcePosition other) const @safe pure nothrow @nogc
    {
        return tuple(line, column).opCmp(tuple(other.line, other.column));
    }
}

///
@safe pure unittest
{
    const origin = SourcePosition(1, 1);
    assert(origin.advance("abc") == SourcePosition(1, 4));
    assert(origin.advance("日本語") == SourcePosition(1, 4),
            "a column counts one per code point");
    assert(origin.advance("ab\ncd") == SourcePosition(2, 3));
    assert(origin.advance("a\n\nb") == SourcePosition(3, 2));
    assert(SourcePosition(4, 7).advance("") == SourcePosition(4, 7));
}

/// Positions order by line and then by column.
@safe pure nothrow @nogc unittest
{
    assert(SourcePosition(1, 1) < SourcePosition(1, 2));
    assert(SourcePosition(1, 9) < SourcePosition(2, 1));
    assert(SourcePosition(3, 4) == SourcePosition(3, 4));
}

/// A half-open range of one source file.
struct SourceSpan
{
    string file;
    SourcePosition start;
    SourcePosition end;

    /// This span's start as the `file:line:column` prefix a diagnostic prints.
    string location() const @safe pure
    {
        return format("%s:%d:%d", file, start.line, start.column);
    }
}

///
@safe pure unittest
{
    const span = SourceSpan("slides.tfx", SourcePosition(12, 5), SourcePosition(12, 9));
    assert(span.location == "slides.tfx:12:5");
}

/**
 * One provenance-tagged run of a text field.
 *
 * A field that no interpolation touched keeps no fragments at all; one
 * assembled from a template and its caller's values keeps a fragment per run,
 * so the source map can send each run back to where it was written.
 */
struct TextFragment
{
    string text;
    SourceSpan span;
    /// Template literals rank below caller content in columnless SyncTeX.
    bool scaffold = false;
}

/// The provenance of a text field, or no fragments at all when it has none.
alias SourceText = TextFragment[];

/// The string a field's fragments spell.
string plainText(const SourceText parts) @safe pure
{
    return parts.map!(fragment => fragment.text).join;
}

///
@safe pure unittest
{
    const span = SourceSpan("a.tfx", SourcePosition(1, 1), SourcePosition(1, 2));
    assert(plainText([TextFragment("one ", span), TextFragment("two", span)]) == "one two");
    assert(plainText([]) == "");
}

/**
 * One file a compilation read.
 *
 * `file` is the spelling every span of that file uses, which is what an editor
 * has to match; `path` is where it was read from; `data` is the bytes
 * themselves, which the source map and the interchange formats hash.
 */
struct LoadedSource
{
    string file;
    string path;
    immutable(ubyte)[] data;
}
