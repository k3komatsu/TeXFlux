/**
 * Reading and writing a SyncTeX file without disturbing what it already says.
 *
 * A SyncTeX file is written by the TeX engine and read by a viewer, and this
 * compiler is neither: it only needs to change which source line some of the
 * records point at. So a document here keeps its physical lines, their exact
 * newline bytes, and every record it did not have to understand, and writing
 * one back out reproduces what was read.
 *
 * Three things are deliberately not reproduced but recomputed, because each is
 * a statement about the file's own bytes that a rewrite invalidates: a point
 * whose vertical coordinate was written as `=` is expanded to the number it
 * meant, every `!` anchor is recounted as the distance from the anchor before
 * it, and the `Count:` line is recounted from the records above it.
 *
 * Filenames are kept as bytes and never decoded. A path that is not UTF-8 is
 * still a path the viewer has to match.
 */
module texflux.synctex;

import std.algorithm : canFind, count, countUntil, startsWith;
import std.array : appender;
import std.conv : to;
import std.exception : basicExceptionCtors, enforce;
import std.range : only;
import std.typecons : Nullable, nullable, Tuple, tuple;

/// A SyncTeX byte stream that cannot be modeled safely.
class SyncTeXError : Exception
{
    mixin basicExceptionCtors;
}

/// Whether the file was compressed, which is reproduced on the way out.
enum Container
{
    plain,
    gzip,
}

/// One `Input:` mapping, whose filename bytes are kept verbatim.
struct SyncTeXInput
{
    int tag;
    immutable(ubyte)[] path;
}

/// A preamble or post-script setting, with byte-valued fields.
struct SyncTeXSetting
{
    immutable(ubyte)[] name;
    immutable(ubyte)[] value;
    string section = "preamble";
}

/// A source link attached to a box or node record.
struct SyncTeXLink
{
    int tag;
    int line;
    Nullable!int column;
}

/// A resolved point, which records how it appeared on disk.
struct SyncTeXPoint
{
    int horizontal;
    int vertical;
    bool compressed;
}

/// A modeled record line, and the byte ranges a rewrite may replace.
struct SyncTeXRecord
{
    immutable(ubyte)[] raw;
    ubyte kind;
    bool counted;
    Nullable!SyncTeXLink link;
    Nullable!SyncTeXPoint point;
    Nullable!long formTag;
    Nullable!long tag;
    Nullable!long anchorOffset;
    Nullable!(size_t[2]) linkSpan;
    Nullable!(size_t[2]) pointSpan;

    /// Whether this record's point was written against the one before it.
    bool isCompressed() const
    {
        return !point.isNull && point.get.compressed;
    }

    /// This record's bytes with a compressed point written out in full.
    immutable(ubyte)[] expandedRaw(immutable(ubyte)[] body_ = null) const
    {
        auto source = body_ is null ? raw : body_;
        if (!isCompressed || pointSpan.isNull)
            return source;
        const range = pointSpan.get;
        enforce!SyncTeXError(range[0] <= range[1] && range[1] <= source.length,
                "record point span is outside its raw bytes");
        enforce!SyncTeXError(source[range[0] .. range[1]] == raw[range[0] .. range[1]],
                "record point span does not match its raw bytes");
        const replacement = cast(immutable(ubyte)[])(
                point.get.horizontal.to!string ~ "," ~ point.get.vertical.to!string);
        return source[0 .. range[0]] ~ replacement ~ source[range[1] .. $];
    }
}

/// One physical line, including the newline bytes it ended with.
struct SyncTeXLine
{
    immutable(ubyte)[] body_;
    immutable(ubyte)[] newline;
    string section;
    Nullable!SyncTeXRecord record;
    Nullable!SyncTeXInput input;

    immutable(ubyte)[] rawLine() const
    {
        return body_ ~ newline;
    }
}

/**
 * A parsed SyncTeX file.
 *
 * The lines are the authoritative representation; the other collections are
 * projections of them, for looking things up and for checking them.
 */
struct SyncTeXDocument
{
    long version_;
    Container container;
    immutable(ubyte)[] newline;
    SyncTeXLine[] lines;
    SyncTeXInput[] inputs;
    SyncTeXSetting[] settings;
    Nullable!long count;
    Nullable!size_t countLine;
}

private enum versionPrefix = cast(immutable(ubyte)[]) "SyncTeX Version:";
private enum inputPrefix = cast(immutable(ubyte)[]) "Input:";
private enum countPrefix = cast(immutable(ubyte)[]) "Count:";

private string sectionName(const(ubyte)[] body_)
{
    switch (cast(const(char)[]) body_)
    {
    case "Content:":
        return "content";
    case "Postamble:":
        return "postamble";
    case "Post scriptum:":
    case "Post Scriptum:":
        return "postscript";
    default:
        return null;
    }
}

private bool isSettingName(const(ubyte)[] name)
{
    return only("Output", "Magnification", "Unit", "X Offset", "Y Offset")
        .canFind(cast(const(char)[]) name);
}

/// The record kinds that carry a source link.
private enum linkKinds = cast(immutable(ubyte)[]) "[]()vhkgr$x";

/**
 * The record kinds the `Count:` line counts.
 *
 * `x` carries a link but is not counted: it comes from synctexcurrent, whose
 * writer updates the byte length and deliberately leaves the count alone.
 */
private enum countedKinds = cast(immutable(ubyte)[]) "!{}<>[]()vhkgr$cf?";

/**
 * Split bytes into lines, keeping each line's own ending.
 *
 * Only carriage return and line feed end a line here. Every other control byte
 * is content, because a filename may hold one.
 */
private Tuple!(immutable(ubyte)[], immutable(ubyte)[])[] splitLines(immutable(ubyte)[] data)
{
    typeof(return) lines;
    size_t start = 0;
    while (start < data.length)
    {
        size_t end = start;
        while (end < data.length && data[end] != '\n' && data[end] != '\r')
            ++end;
        size_t after = end;
        if (after < data.length && data[after] == '\r')
            ++after;
        if (after < data.length && data[after] == '\n')
            ++after;
        lines ~= tuple(data[start .. end], data[end .. after]);
        start = after;
    }
    if (lines.length == 0)
        lines ~= tuple(cast(immutable(ubyte)[]) null, cast(immutable(ubyte)[]) null);
    return lines;
}

/// One decimal integer, or the reference's complaint quoting the bytes that are not one.
private T integer(T = int)(const(ubyte)[] value, string description)
{
    import std.conv : ConvException;

    try
        return (cast(const(char)[]) value).to!T;
    catch (ConvException _)
        throw new SyncTeXError("invalid " ~ description ~ ": " ~ bytesRepr(value));
}

/// The bytes as Python's `bytes.__repr__` spells them, which the messages quote.
private string bytesRepr(const(ubyte)[] value)
{
    import std.format : format;

    const quote = value.canFind('\'') && !value.canFind('"') ? '"' : '\'';
    auto output = appender!string();
    output.put('b');
    output.put(quote);
    foreach (b; value)
    {
        if (b == quote || b == '\\')
        {
            output.put('\\');
            output.put(cast(char) b);
        }
        else if (b == '\t')
            output.put("\\t");
        else if (b == '\n')
            output.put("\\n");
        else if (b == '\r')
            output.put("\\r");
        else if (b < 0x20 || b >= 0x7f)
            output.put(format("\\x%02x", b));
        else
            output.put(cast(char) b);
    }
    output.put(quote);
    return output.data;
}

///
unittest
{
    assert(bytesRepr(cast(immutable(ubyte)[]) "abc") == "b'abc'");
    assert(bytesRepr(cast(immutable(ubyte)[]) "it's") == `b"it's"`);
    assert(bytesRepr(cast(immutable(ubyte)[]) "a\tb\x01\xff") == `b'a\tb\x01\xff'`);
}

/// How many bytes at `start` are a signed decimal integer.
private size_t integerLength(const(ubyte)[] body_, size_t start)
{
    size_t at = start;
    if (at < body_.length && body_[at] == '-')
        ++at;
    const digits = at;
    while (at < body_.length && body_[at] >= '0' && body_[at] <= '9')
        ++at;
    return at == digits ? 0 : at - start;
}

private SyncTeXInput parseInput(immutable(ubyte)[] body_)
{
    auto rest = body_[inputPrefix.length .. $];
    const at = rest.countUntil(':');
    enforce!SyncTeXError(at > 0, "invalid Input record: " ~ bytesRepr(body_));
    return SyncTeXInput(integer(rest[0 .. at], "Input tag"), rest[at + 1 .. $]);
}

private alias ParsedPoint = Tuple!(Nullable!SyncTeXPoint, "point", Nullable!(size_t[2]), "span");

/// Parse one point, resolving a `=` vertical against the one before it.
private ParsedPoint parsePoint(immutable(ubyte)[] body_, size_t start, Nullable!int lastVertical)
{
    const horizontalLength = integerLength(body_, start);
    if (horizontalLength == 0)
        return ParsedPoint.init;
    size_t at = start + horizontalLength;
    if (at >= body_.length || body_[at] != ',')
        return ParsedPoint.init;
    ++at;

    bool compressed = false;
    int vertical;
    size_t end;
    if (at < body_.length && body_[at] == '=')
    {
        compressed = true;
        enforce!SyncTeXError(!lastVertical.isNull,
                "compressed point has no previous vertical coordinate");
        vertical = lastVertical.get;
        end = at + 1;
    }
    else
    {
        const verticalLength = integerLength(body_, at);
        if (verticalLength == 0)
            return ParsedPoint.init;
        vertical = integer(body_[at .. at + verticalLength], "point vertical coordinate");
        end = at + verticalLength;
    }

    const horizontal = integer(body_[start .. start + horizontalLength],
            "point horizontal coordinate");
    size_t[2] span = [start, end];
    return ParsedPoint(SyncTeXPoint(horizontal, vertical, compressed).nullable, span.nullable);
}

/// The single integer after the kind byte, when the rest of the line is one.
private Nullable!long singleTag(immutable(ubyte)[] body_, string description)
{
    const length = integerLength(body_, 1);
    if (length == 0 || 1 + length != body_.length)
        return Nullable!long.init;
    return integer!long(body_[1 .. $], description).nullable;
}

/// One link, as its tag, its line and the column it may carry.
private alias ParsedLink = Tuple!(Nullable!SyncTeXLink, "link", size_t, "end");

private ParsedLink parseLink(immutable(ubyte)[] body_)
{
    static immutable descriptions = ["link tag", "link line", "link column"];
    int[3] numbers;
    size_t count = 0;
    size_t at = 1;
    while (count < 3)
    {
        const length = integerLength(body_, at);
        if (length == 0)
            return ParsedLink.init;
        numbers[count] = integer(body_[at .. at + length], descriptions[count]);
        ++count;
        at += length;
        if (at < body_.length && body_[at] == ',' && count < 3)
        {
            ++at;
            continue;
        }
        break;
    }
    if (count < 2 || at >= body_.length || body_[at] != ':')
        return ParsedLink.init;
    auto link = SyncTeXLink(numbers[0], numbers[1],
            count == 3 ? numbers[2].nullable : Nullable!int.init);
    return ParsedLink(link.nullable, at + 1);
}

/// Model one record line. A returned point carries the new vertical state.
private Nullable!SyncTeXRecord parseRecord(immutable(ubyte)[] body_, Nullable!int lastVertical)
{
    if (body_.length == 0 || body_.startsWith(inputPrefix))
        return Nullable!SyncTeXRecord.init;

    const kind = body_[0];
    auto plain = SyncTeXRecord(body_, kind);

    switch (kind)
    {
    case '!':
        auto anchor = singleTag(body_, "anchor offset");
        if (anchor.isNull)
            return plain.nullable;
        return SyncTeXRecord(body_, kind, true, anchorOffset: anchor).nullable;

    case '{':
    case '}':
        auto tag = singleTag(body_, "sheet tag");
        if (tag.isNull)
            return plain.nullable;
        return SyncTeXRecord(body_, kind, true, tag: tag).nullable;

    case '<':
        auto formTag = singleTag(body_, "form tag");
        if (formTag.isNull)
            return plain.nullable;
        return SyncTeXRecord(body_, kind, true, formTag: formTag).nullable;

    case '>':
    case ']':
    case ')':
        return SyncTeXRecord(body_, kind, true).nullable;

    case 'f':
        const length = integerLength(body_, 1);
        if (length == 0 || 1 + length >= body_.length || body_[1 + length] != ':')
            return plain.nullable;
        auto point = parsePoint(body_, 1 + length + 1, lastVertical);
        return SyncTeXRecord(body_, kind, true, point: point.point,
                formTag: integer!long(body_[1 .. 1 + length], "form tag").nullable,
                pointSpan: point.span).nullable;

    case 'c':
    case '?':
        auto point = parsePoint(body_, 1, lastVertical);
        return SyncTeXRecord(body_, kind, countedKinds.canFind(kind), point: point.point,
                pointSpan: point.span).nullable;

    default:
        if (!linkKinds.canFind(kind))
            return plain.nullable;
        auto parsed = parseLink(body_);
        if (parsed.link.isNull)
            return plain.nullable;
        auto point = parsePoint(body_, parsed.end, lastVertical);
        size_t[2] span = [1, parsed.end - 1];
        return SyncTeXRecord(body_, kind, countedKinds.canFind(kind), link: parsed.link,
                point: point.point, linkSpan: span.nullable, pointSpan: point.span).nullable;
    }
}

/// Parse a plain or gzip-compressed SyncTeX file.
SyncTeXDocument parseSyncTeX(immutable(ubyte)[] data)
{
    auto decoded = decodePayload(data);
    auto payload = decoded[0];
    auto physical = splitLines(payload);

    SyncTeXDocument document;
    document.container = decoded[1];
    document.newline = cast(immutable(ubyte)[]) "\n";
    foreach (line; physical)
        if (line[1].length != 0)
        {
            document.newline = line[1];
            break;
        }

    bool sawVersion = false;
    string section = "preamble";
    Nullable!int lastVertical;

    foreach (line; physical)
    {
        const body_ = line[0];
        auto lineSection = section;
        Nullable!SyncTeXRecord record;
        Nullable!SyncTeXInput input;

        if (body_.startsWith(versionPrefix))
        {
            document.version_ = integer!long(body_[versionPrefix.length .. $], "SyncTeX version");
            sawVersion = true;
        }
        else if (body_.startsWith(inputPrefix))
        {
            input = parseInput(body_);
            document.inputs ~= input.get;
        }
        else if (sectionName(body_) !is null)
        {
            section = lineSection = sectionName(body_);
        }
        else if (body_.startsWith(countPrefix))
        {
            document.count = integer!long(body_[countPrefix.length .. $], "SyncTeX Count").nullable;
            document.countLine = document.lines.length.nullable;
        }
        else if (isSetting(body_, section))
        {
            const at = body_.countUntil(':');
            document.settings ~= SyncTeXSetting(body_[0 .. at], body_[at + 1 .. $], lineSection);
        }
        else if (body_.length != 0)
        {
            record = parseRecord(body_, lastVertical);
            if (!record.isNull && !record.get.point.isNull)
                lastVertical = record.get.point.get.vertical.nullable;
        }

        document.lines ~= SyncTeXLine(body_, line[1], lineSection, record, input);
    }

    enforce!SyncTeXError(sawVersion, "missing SyncTeX Version record");
    return document;
}

/// Whether a line is a setting rather than a record.
private bool isSetting(const(ubyte)[] body_, string section)
{
    const at = body_.countUntil(':');
    if (at < 0)
        return false;
    return section == "preamble" || section == "postscript" || isSettingName(body_[0 .. at]);
}

/// How many counted records stand above the `Count:` line.
private Nullable!long canonicalCount(SyncTeXDocument document)
{
    if (document.countLine.isNull)
        return Nullable!long.init;
    return document.lines[0 .. document.countLine.get]
        .count!(line => !line.record.isNull && line.record.get.counted).to!long.nullable;
}

/// Serialize a document, without any newline conversion.
immutable(ubyte)[] serializeSyncTeX(SyncTeXDocument document)
{
    auto output = appender!(immutable(ubyte)[])();
    size_t anchorOrigin = 0;
    size_t written = 0;
    const count = canonicalCount(document);

    foreach (index, line; document.lines)
    {
        auto body_ = line.body_;
        Nullable!size_t anchorStart;
        if (!line.record.isNull)
        {
            body_ = line.record.get.expandedRaw(body_);
            if (!line.record.get.anchorOffset.isNull)
            {
                anchorStart = written.nullable;
                body_ = cast(immutable(ubyte)[])("!" ~ (written - anchorOrigin).to!string);
            }
        }
        if (!document.countLine.isNull && index == document.countLine.get && !count.isNull)
            body_ = countPrefix ~ cast(immutable(ubyte)[]) count.get.to!string;

        output.put(body_);
        output.put(line.newline);
        written += body_.length + line.newline.length;
        if (!anchorStart.isNull)
            // The next anchor is measured from this anchor's own line start.
            anchorOrigin = anchorStart.get;
    }

    auto payload = output.data;
    return document.container == Container.gzip ? gzipPayload(payload) : payload;
}

private Tuple!(immutable(ubyte)[], Container) decodePayload(immutable(ubyte)[] data)
{
    import std.zlib : HeaderFormat, UnCompress, ZlibException;

    if (!(data.length >= 2 && data[0] == 0x1f && data[1] == 0x8b))
        return tuple(data, Container.plain);
    try
    {
        auto stream = new UnCompress(HeaderFormat.gzip);
        auto payload = cast(immutable(ubyte)[])(stream.uncompress(data) ~ stream.flush());
        return tuple(payload, Container.gzip);
    }
    catch (ZlibException _)
        throw new SyncTeXError("invalid gzip SyncTeX data");
}

/**
 * Compress a payload the way the reference implementation compresses one.
 *
 * The header is written here rather than left to zlib, because zlib names the
 * operating system it ran on and the reference writer does not. The compressed
 * bytes themselves may still differ between zlib builds, which is why what is
 * compared is what comes back out.
 */
private immutable(ubyte)[] gzipPayload(immutable(ubyte)[] payload)
{
    import std.bitmanip : nativeToLittleEndian;
    import std.digest.crc : crc32Of;
    import std.zlib : Compress, HeaderFormat;

    auto compressor = new Compress(9, HeaderFormat.deflate);
    auto wrapped = cast(immutable(ubyte)[])(compressor.compress(payload) ~ compressor.flush());
    // A zlib stream is a two-byte header, the deflate data, and a checksum.
    auto deflated = wrapped[2 .. $ - 4];

    immutable(ubyte)[] header = [0x1f, 0x8b, 0x08, 0x00, 0, 0, 0, 0, 0x02, 0xff];
    return header ~ deflated ~ crc32Of(payload).idup
        ~ nativeToLittleEndian(cast(uint) payload.length).idup;
}

/// Read a SyncTeX file, detecting its container from its bytes.
SyncTeXDocument readSyncTeXFile(string path)
{
    import std.file : read;

    return parseSyncTeX(cast(immutable(ubyte)[]) read(path));
}

/// Write a SyncTeX file back in the container it was read from.
void writeSyncTeXFile(string path, SyncTeXDocument document)
{
    import std.file : write;

    write(path, serializeSyncTeX(document));
}

/// A file that needs no correction comes back exactly as it was read.
unittest
{
    immutable(ubyte)[] original = cast(immutable(ubyte)[])(
            "SyncTeX Version:1\nInput:1:/tmp/a.tex\nOutput:pdf\nContent:\n"
            ~ "!0\n{1\n[1,4:100,200\n]\n}1\n!0\nPostamble:\nCount:5\n!0\nPost scriptum:\n");
    auto document = parseSyncTeX(original);
    assert(document.version_ == 1);
    assert(document.container == Container.plain);
    assert(document.inputs.length == 1 && document.inputs[0].tag == 1);
    assert(cast(string) document.inputs[0].path == "/tmp/a.tex");
}

/// A compressed point is written out in full, and the count is recomputed.
unittest
{
    import std.algorithm : canFind;

    immutable(ubyte)[] original = cast(immutable(ubyte)[])(
            "SyncTeX Version:1\nInput:1:/tmp/a.tex\nContent:\n"
            ~ "[1,4:100,200\nv1,4:120,=\n]\nPostamble:\nCount:99\n");
    const written = cast(string) serializeSyncTeX(parseSyncTeX(original));
    assert(!written.canFind(",=\n"), "a point written against the one before it is expanded");
    assert(written.canFind("v1,4:120,200\n"));
    assert(written.canFind("Count:3\n"), "the count is what the records above it say");
}

/// An unknown record is carried through untouched, bytes and all.
unittest
{
    import std.algorithm : canFind;

    immutable(ubyte)[] original = cast(immutable(ubyte)[])(
            "SyncTeX Version:1\nContent:\nzopaque payload\n%a comment\n");
    const written = cast(string) serializeSyncTeX(parseSyncTeX(original));
    assert(written.canFind("zopaque payload\n") && written.canFind("%a comment\n"));
}

/// A file without a version record is not a SyncTeX file.
unittest
{
    import std.exception : assertThrown;

    assertThrown!SyncTeXError(parseSyncTeX(cast(immutable(ubyte)[]) "Content:\n"));
}
