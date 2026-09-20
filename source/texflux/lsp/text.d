module texflux.lsp.text;

import std.array : appender;
import std.string : indexOf, startsWith;
import std.typecons : Nullable, nullable;
import std.utf : codeLength, decode;

import texflux.source : SourcePosition, SourceSpan;

enum PositionEncoding : string
{
    utf8 = "utf-8",
    utf16 = "utf-16",
    utf32 = "utf-32",
}

struct LspPosition
{
    long line;
    long character;
}

struct LspRange
{
    LspPosition start;
    LspPosition end;
}

/// Byte-backed line boundaries over the original UTF-8 document.
struct LineIndex
{
    string text;
    size_t[] starts;
    size_t[] ends;

    this(string source)
    {
        text = source;
        size_t start;
        size_t index;
        starts ~= 0;
        while (index < source.length)
        {
            if (source[index] == '\n')
            {
                ends ~= index;
                ++index;
                starts ~= index;
                start = index;
            }
            else if (source[index] == '\r')
            {
                ends ~= index;
                ++index;
                if (index < source.length && source[index] == '\n')
                    ++index;
                starts ~= index;
                start = index;
            }
            else
                ++index;
        }
        ends ~= source.length;
        assert(starts.length == ends.length);
    }

    size_t lineCount() const
    {
        return starts.length;
    }

    string line(size_t number) const
    {
        if (number >= starts.length)
            return "";
        return text[starts[number] .. ends[number]];
    }

    LspPosition toLsp(SourcePosition position, PositionEncoding encoding) const
    {
        size_t lineNumber = position.line <= 1 ? 0 : cast(size_t) position.line - 1;
        if (lineNumber >= starts.length)
            lineNumber = starts.length - 1;
        size_t codePoints = position.column <= 1 ? 0 : cast(size_t) position.column - 1;
        return LspPosition(cast(long) lineNumber,
                cast(long) unitsForCodePoints(line(lineNumber), codePoints, encoding));
    }

    Nullable!SourcePosition fromLsp(LspPosition position, PositionEncoding encoding) const
    {
        if (position.line < 0 || position.character < 0
                || cast(ulong) position.line >= starts.length)
            return Nullable!SourcePosition.init;
        const sourceLine = line(cast(size_t) position.line);
        auto codePoints = codePointsForUnits(sourceLine, cast(size_t) position.character,
                encoding);
        if (codePoints.isNull)
            return Nullable!SourcePosition.init;
        return SourcePosition(cast(int) position.line + 1,
                cast(int) codePoints.get + 1).nullable;
    }

    LspRange toLsp(SourceSpan span, PositionEncoding encoding) const
    {
        return LspRange(toLsp(span.start, encoding), toLsp(span.end, encoding));
    }
}

private size_t unitsForCodePoints(string text, size_t wanted, PositionEncoding encoding)
{
    size_t index;
    size_t points;
    size_t units;
    while (index < text.length && points < wanted)
    {
        const before = index;
        const character = decode(text, index);
        units += unitsOf(character, index - before, encoding);
        ++points;
    }
    return units;
}

private Nullable!size_t codePointsForUnits(string text, size_t wanted,
        PositionEncoding encoding)
{
    size_t index;
    size_t points;
    size_t units;
    while (index < text.length)
    {
        const before = index;
        const character = decode(text, index);
        const next = unitsOf(character, index - before, encoding);
        if (wanted == units)
            return points.nullable;
        if (wanted < units + next)
            return Nullable!size_t.init;
        units += next;
        ++points;
    }
    return wanted == units ? points.nullable : Nullable!size_t.init;
}

private size_t unitsOf(dchar character, size_t utf8Bytes, PositionEncoding encoding)
{
    final switch (encoding)
    {
    case PositionEncoding.utf8:
        return utf8Bytes;
    case PositionEncoding.utf16:
        return character > 0xffff ? 2 : 1;
    case PositionEncoding.utf32:
        return 1;
    }
}

/// Minimal file URI conversion kept at the LSP boundary.
string pathToUri(string path)
{
    version (Windows)
    {
        import std.string : replace;
        import std.ascii : toLower;

        auto normalized = path.replace("\\", "/").dup;
        if (normalized.length >= 2 && normalized[1] == ':')
            normalized[0] = normalized[0].toLower;
        const asString = cast(string) normalized;
        return asString.startsWith("/")
            ? "file://" ~ percentEncode(asString)
            : "file:///" ~ percentEncode(asString);
    }
    else
        return "file://" ~ percentEncode(path);
}

Nullable!string uriToPath(string uri)
{
    if (!uri.startsWith("file://"))
        return Nullable!string.init;

    auto rest = uri[7 .. $];
    string authority;
    string pathPart;
    auto slash = rest.indexOf('/');
    if (slash < 0)
        authority = rest;
    else
    {
        authority = rest[0 .. slash];
        pathPart = rest[slash .. $];
    }
    if (authority.length != 0 && authority != "localhost")
    {
        version (Windows)
            pathPart = "//" ~ authority ~ pathPart;
        else
            return Nullable!string.init;
    }
    auto decoded = percentDecode(pathPart);
    version (Windows)
    {
        import std.string : replace;

        if (decoded.length >= 3 && decoded[0] == '/'
                && decoded[2] == ':')
            decoded = decoded[1 .. $];
        decoded = decoded.replace("/", "\\");
    }
    return decoded.nullable;
}

private string percentEncode(string text)
{
    auto result = appender!string();
    static immutable hex = "0123456789ABCDEF";
    foreach (ubyte octet; cast(const(ubyte)[]) text)
    {
        if ((octet >= 'a' && octet <= 'z') || (octet >= 'A' && octet <= 'Z')
                || (octet >= '0' && octet <= '9')
                || octet == '-' || octet == '_' || octet == '.' || octet == '~'
                || octet == '/')
            result.put(cast(char) octet);
        else
        {
            result.put('%');
            result.put(hex[octet >> 4]);
            result.put(hex[octet & 15]);
        }
    }
    return result.data;
}

private string percentDecode(string text)
{
    auto result = appender!string();
    for (size_t index; index < text.length; ++index)
    {
        if (text[index] == '%' && index + 2 < text.length)
        {
            const high = hexValue(text[index + 1]);
            const low = hexValue(text[index + 2]);
            if (high >= 0 && low >= 0)
            {
                result.put(cast(char) (high * 16 + low));
                index += 2;
                continue;
            }
        }
        result.put(text[index]);
    }
    return result.data;
}

private int hexValue(char character)
{
    if (character >= '0' && character <= '9')
        return character - '0';
    if (character >= 'a' && character <= 'f')
        return character - 'a' + 10;
    if (character >= 'A' && character <= 'F')
        return character - 'A' + 10;
    return -1;
}

unittest
{
    auto index = LineIndex("@frame{日本語😀}\r\nnext\n");
    assert(index.lineCount == 3);
    assert(index.toLsp(SourcePosition(1, 9), PositionEncoding.utf32)
            == LspPosition(0, 8));
    assert(index.fromLsp(LspPosition(0, 8), PositionEncoding.utf32).get
            == SourcePosition(1, 9));
    assert(index.fromLsp(LspPosition(0, 9), PositionEncoding.utf16).get
            == SourcePosition(1, 10));
    assert(index.fromLsp(LspPosition(0, 11), PositionEncoding.utf16).isNull);
    assert(uriToPath(pathToUri("/tmp/日本.tfx")).get == "/tmp/日本.tfx");
}
