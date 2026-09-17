/**
 * Text handling whose exact semantics the compiler's output depends on.
 *
 * TeXFlux reports one-based columns that count code points, strips ASCII
 * spaces where the parser measures structure but Unicode whitespace where a
 * name is read, quotes the decoder's own wording when a source file is not
 * UTF-8, and escapes a path's non-printable characters the way the reference
 * does. Phobos has near-equivalents for all of these, but "near" is the wrong
 * relation for a compiler whose diagnostics are compared byte for byte against
 * another implementation, so the rules that differ live here and are stated
 * once.
 */
module texflux.text;

import std.algorithm : all, canFind, countUntil, stripLeft, stripRight;
import std.array : appender;
import std.exception : basicExceptionCtors;
import std.format : format;
import std.range : assumeSorted;
import std.traits : isSomeString;
import std.uni : isWhite;
import std.utf : byCodeUnit;

import texflux.printable : nonPrintableRanges;

/// A source file that is not valid UTF-8.
class UnicodeDecodeError : Exception
{
    mixin basicExceptionCtors;
}

/**
 * Whether a code point is whitespace.
 *
 * This is `std.uni.isWhite` plus the four information separators, U+001C to
 * U+001F, which the reference implementation's test counts and Phobos's does
 * not. That is the whole difference between the two, and the suite checks it
 * over every code point.
 */
bool isWhitespace(dchar c) @safe pure nothrow @nogc
{
    return c.isWhite || (c >= 0x1C && c <= 0x1F);
}

///
@safe pure nothrow @nogc unittest
{
    assert(isWhitespace(' '));
    assert(isWhitespace('\u3000'));
    assert(!isWhitespace('x'));
}

/// The four separators are what is added, and nothing else is taken away.
@safe pure nothrow @nogc unittest
{
    foreach (dchar c; "\x1c\x1d\x1e\x1f"d)
        assert(isWhitespace(c) && !isWhite(c));
}

/// The text without leading and trailing whitespace.
S stripWhitespace(S)(S text) if (isSomeString!S)
{
    return text.stripLeft!isWhitespace.stripRight!isWhitespace;
}

/// The text without leading whitespace.
S lstripWhitespace(S)(S text) if (isSomeString!S)
{
    return text.stripLeft!isWhitespace;
}

///
@safe pure unittest
{
    assert(stripWhitespace("  padded  ") == "padded");
    assert(stripWhitespace("\u3000\u00a0name\u2028") == "name");
    assert(stripWhitespace("\u2003\u2003") == "");
    assert(lstripWhitespace("\tname\t") == "name\t");
    assert(stripWhitespace("in the middle") == "in the middle");
}

/**
 * The text without leading and trailing ASCII spaces.
 *
 * Indentation and every structural offset the parser measures are spaces
 * alone, so a tab or a form feed is content that a strip here must not remove.
 */
S stripSpaces(S)(S text) if (isSomeString!S)
{
    return text.stripLeft(' ').stripRight(' ');
}

/// ditto
S lstripSpaces(S)(S text) if (isSomeString!S)
{
    return text.stripLeft(' ');
}

/// ditto
S rstripSpaces(S)(S text) if (isSomeString!S)
{
    return text.stripRight(' ');
}

///
@safe pure unittest
{
    assert(stripSpaces("  padded  ") == "padded");
    assert(stripSpaces("\tkept\t") == "\tkept\t", "only spaces are indentation");
    assert(lstripSpaces("   four") == "four");
    assert(rstripSpaces("four   ") == "four");
    assert(stripSpaces("  padded  "d) == "padded"d, "the parser scans code points");
}

/// How many ASCII spaces the text opens with, which is one line's indentation.
size_t leadingSpaces(S)(S text) if (isSomeString!S)
{
    const found = text.byCodeUnit.countUntil!(c => c != ' ');
    return found < 0 ? text.length : cast(size_t) found;
}

///
@safe pure unittest
{
    assert(leadingSpaces("    deep") == 4);
    assert(leadingSpaces("no indent") == 0);
    assert(leadingSpaces("\t    tab first") == 0);
    assert(leadingSpaces("    ") == 4, "a line of spaces is indented to its end");
    assert(leadingSpaces("    deep"d) == 4);
}

/// Whether the text holds nothing but ASCII spaces.
bool isBlank(S)(S text) if (isSomeString!S)
{
    return text.byCodeUnit.all!(c => c == ' ');
}

///
@safe pure unittest
{
    assert(isBlank("    "));
    assert(isBlank(""));
    assert(!isBlank("\t"), "a tab is content, so a line holding one is not blank");
    assert(!isBlank("  x  "));
}

/**
 * How many code points the text holds.
 *
 * Columns count code points, so a character outside the basic plane advances a
 * column by one however many bytes it occupies.
 */
size_t countCodePoints(const(char)[] text) @safe pure
{
    import std.utf : count;

    return text.count;
}

///
@safe pure unittest
{
    assert(countCodePoints("abc") == 3);
    assert(countCodePoints("日本語") == 3);
    assert(countCodePoints("\U0001f600") == 1, "an astral character is one column");
    assert(countCodePoints("") == 0);
}

/**
 * The text a byte sequence spells, or an error naming the first bad byte.
 *
 * The wording follows the reference decoder, because it reaches the user
 * through a module diagnostic and through the command line's own failure line,
 * where it is compared literally. That is also why this does not delegate to
 * `std.utf.validate`, whose classification of a bad sequence and whose message
 * are its own.
 */
string decodeUtf8(const(ubyte)[] data) @safe pure
{
    const failure = firstDecodeFailure(data);
    if (failure.length == 0)
        return cast(string) data.idup;

    const start = failure.start;
    if (failure.length == 1)
        throw new UnicodeDecodeError(format(
                "'utf-8' codec can't decode byte 0x%02x in position %d: %s",
                data[start], start, failure.reason));
    throw new UnicodeDecodeError(format(
            "'utf-8' codec can't decode bytes in position %d-%d: %s",
            start, start + failure.length - 1, failure.reason));
}

/// Whether a byte sequence is valid UTF-8.
bool isValidUtf8(const(ubyte)[] data) @safe pure nothrow @nogc
{
    return firstDecodeFailure(data).length == 0;
}

///
@safe pure unittest
{
    import std.exception : assertThrown, collectExceptionMsg;

    assert(decodeUtf8(cast(immutable ubyte[]) "日 ok") == "日 ok");
    assertThrown!UnicodeDecodeError(decodeUtf8([0xff]));
    assert(collectExceptionMsg(decodeUtf8([0xff]))
            == "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte");
    assert(collectExceptionMsg(decodeUtf8([0xe6, 0x97]))
            == "'utf-8' codec can't decode bytes in position 0-1: unexpected end of data");
}

private struct DecodeFailure
{
    size_t start;
    size_t length;
    string reason;
}

/**
 * The first byte run that cannot be decoded.
 *
 * A run is as long as the prefix the decoder had already accepted: the lead
 * byte alone when its own first continuation is wrong, and the lead byte plus
 * every good continuation when a later one is. That length decides which of
 * the two message shapes the caller prints.
 *
 * The continuation bounds narrow for four lead bytes, which is what rejects an
 * overlong encoding, a surrogate, and anything above the last code point.
 */
private DecodeFailure firstDecodeFailure(const(ubyte)[] data) @safe pure nothrow @nogc
{
    size_t index = 0;
    while (index < data.length)
    {
        const lead = data[index];
        if (lead < 0x80)
        {
            ++index;
            continue;
        }

        size_t width;
        ubyte lowest = 0x80;
        ubyte highest = 0xBF;
        if (lead >= 0xC2 && lead <= 0xDF)
            width = 2;
        else if (lead >= 0xE0 && lead <= 0xEF)
        {
            width = 3;
            if (lead == 0xE0)
                lowest = 0xA0;
            else if (lead == 0xED)
                highest = 0x9F;
        }
        else if (lead >= 0xF0 && lead <= 0xF4)
        {
            width = 4;
            if (lead == 0xF0)
                lowest = 0x90;
            else if (lead == 0xF4)
                highest = 0x8F;
        }
        else
            return DecodeFailure(index, 1, "invalid start byte");

        foreach (offset; 1 .. width)
        {
            if (index + offset >= data.length)
                return DecodeFailure(index, offset, "unexpected end of data");
            const continuation = data[index + offset];
            const low = offset == 1 ? lowest : 0x80;
            const high = offset == 1 ? highest : 0xBF;
            if (continuation < low || continuation > high)
                return DecodeFailure(index, offset, "invalid continuation byte");
        }
        index += width;
    }
    return DecodeFailure(0, 0, null);
}

/**
 * Whether a code point is printable, in the sense the reference's quoting uses.
 *
 * Phobos's Unicode categories are another revision than the reference's and
 * disagree with it on thousands of unassigned code points, so the answer comes
 * from a table generated from the reference instead.
 */
bool isPrintable(dchar c) @safe pure nothrow @nogc
{
    // The last range starting at or before c is the only one that can hold it.
    uint[2] probe = [c + 1, 0];
    auto starting = nonPrintableRanges.assumeSorted!((a, b) => a[0] < b[0]).lowerBound(probe);
    return starting.empty || c > starting.back[1];
}

///
@safe pure nothrow @nogc unittest
{
    assert(isPrintable('a') && isPrintable(' ') && isPrintable('日'));
    assert(!isPrintable('\u3000') && !isPrintable('\u00a0') && !isPrintable(cast(dchar) 0x10FFFF));
}

/**
 * The text as a quoted literal, the way an operating-system error prints a
 * path inside its message.
 *
 * The quote character is the apostrophe unless the text holds one and holds no
 * double quote, and a character that is not printable is escaped by its size,
 * which is the rule the reference implementation's own quoting follows.
 */
string quoted(string text) @safe pure
{
    const quote = text.canFind('\'') && !text.canFind('"') ? '"' : '\'';
    auto output = appender!string();
    output.put(quote);
    foreach (dchar c; text)
    {
        if (c == quote || c == '\\')
        {
            output.put('\\');
            output.put(c);
        }
        else if (c == '\n')
            output.put("\\n");
        else if (c == '\r')
            output.put("\\r");
        else if (c == '\t')
            output.put("\\t");
        else if (c < 0x20 || c == 0x7F || (c >= 0x80 && !c.isPrintable))
            output.put(format(c < 0x100 ? "\\x%02x" : c < 0x10000 ? "\\u%04x" : "\\U%08x",
                    cast(uint) c));
        else
            output.put(c);
    }
    output.put(quote);
    return output.data;
}

///
@safe pure unittest
{
    assert(quoted("plain.tfx") == "'plain.tfx'");
    assert(quoted("it's.tfx") == `"it's.tfx"`, "an apostrophe alone switches the quote");
    assert(quoted("both'and\".tfx") == `'both\'and".tfx'`);
    assert(quoted("tab\there") == "'tab\\there'");
    assert(quoted("wide\u3000space.tfx") == "'wide\\u3000space.tfx'");
    assert(quoted("nbsp\u00a0.tfx") == "'nbsp\\xa0.tfx'", "escaped by size, in lowercase hex");
    assert(quoted("日本語.tfx") == "'日本語.tfx'", "printable text is kept");
}
