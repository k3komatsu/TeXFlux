/**
 * Reading one structural header, without reading the TeX inside it.
 *
 * A header is the part of a line TeXFlux is allowed to interpret: the prefix
 * that classifies it, a name, the groups written after that name, and the
 * suite marker or composition separator that ends it. Everything inside a
 * group stays opaque, which is what lets a document hold arbitrary TeX without
 * the preprocessor needing to understand any of it.
 *
 * The scanner works in code points rather than bytes, so an offset into the
 * header is also a column, and a span can be made from one by addition. That
 * matters because every diagnostic and every source-map entry is measured that
 * way, and because a heading in Japanese must not shift the columns of what
 * follows it.
 */
module texflux.scanner;

import std.algorithm : canFind, startsWith;
import std.ascii : isAlpha, isAlphaNum;
import std.conv : to;
import std.sumtype : match;
import std.typecons : Nullable, nullable, Tuple, tuple;

import texflux.ast;
import texflux.errors : ParseError;
import texflux.source : SourcePosition, SourceSpan;
import texflux.syntax : isEscaped, rawModeNames;
import texflux.text : rstripSpaces;

/// One scanned header. A trailing separator asks for another line.
struct HeaderScanResult
{
    Node[] segments;
    Nullable!SuiteMode suiteMode;
    Nullable!SourceSpan suiteSpan;
    Nullable!SourceSpan continuationSpan;

    /// One segment, no suite, no continuation: the line is complete as written.
    bool closedSingle() @safe pure nothrow
    {
        return suiteMode.isNull && continuationSpan.isNull && segments.length == 1;
    }
}

/**
 * The suite markers, longest first so that `:::` wins over the `::` it opens
 * with.
 *
 * A lone `:` is deliberately absent. TeX prose ends a line with one, which is
 * why it is the one punctuation TeXFlux leaves entirely to TeX.
 */
private enum suiteMarkers = [
    tuple(":::"d, SuiteMode.sequence),
    tuple("::"d, SuiteMode.block),
];

/**
 * How one inline group scans.
 *
 * `nests` says whether another opener of the same kind deepens the group.
 * `skipsBraces` says whether a balanced `{...}` inside it is stepped over as
 * opaque text, which is what keeps a `{a,b}` value or a `{]}` out of the
 * count.
 */
private struct GroupRule
{
    dchar closer;
    bool nests;
    bool skipsBraces;
}

private Nullable!GroupRule groupRule(dchar opener) @safe pure nothrow
{
    switch (opener)
    {
    case '<':
        return GroupRule('>', false, false).nullable;
    case '{':
        return GroupRule('}', true, false).nullable;
    case '[':
        return GroupRule(']', true, true).nullable;
    case '(':
        return GroupRule(')', true, true).nullable;
    default:
        return Nullable!GroupRule.init;
    }
}

/// How a group scan ended.
private enum GroupOutcome
{
    closed,
    unclosed,
    /// A `}` that closes nothing, inside a bracket or a binding list.
    strayBrace,
}

private struct GroupEnd
{
    GroupOutcome outcome;
    size_t offset;
}

/// The offset of the closer balancing the opener at `start`.
private GroupEnd groupEnd(dstring text, size_t start) @safe pure nothrow
{
    const rule = groupRule(text[start]).get;
    size_t depth = 1;
    size_t index = start + 1;
    while (index < text.length)
    {
        const c = text[index];
        if (text.isEscaped(index))
        {
        }
        else if (rule.skipsBraces && c == '{')
        {
            const inner = groupEnd(text, index);
            if (inner.outcome != GroupOutcome.closed)
                return inner;
            index = inner.offset;
        }
        else if (rule.skipsBraces && c == '}')
            return GroupEnd(GroupOutcome.strayBrace, index);
        else if (rule.nests && c == text[start])
            ++depth;
        else if (c == rule.closer)
        {
            --depth;
            if (depth == 0)
                return GroupEnd(GroupOutcome.closed, index);
        }
        ++index;
    }
    return GroupEnd(GroupOutcome.unclosed, index);
}

/// One scanned inline group: where it ends, and the raw text inside it.
alias ScannedGroup = Tuple!(size_t, "end", dstring, "value");

/**
 * Scan one inline group, keeping its contents opaque.
 *
 * Each opener keeps its own diagnostic code, because a code names one
 * construction site rather than one wording.
 */
ScannedGroup scanGroup(dstring text, size_t start, SourceSpan span) @safe pure
{
    const opener = text[start];
    if (groupRule(opener).isNull)
        throw new ParseError("P009", "invalid group opener", span);

    const end = groupEnd(text, start);
    final switch (end.outcome)
    {
    case GroupOutcome.closed:
        return ScannedGroup(end.offset + 1, text[start + 1 .. end.offset]);
    case GroupOutcome.strayBrace:
        if (opener == '[')
            throw new ParseError("P005", "mismatched group delimiter", span);
        throw new ParseError("P007", "mismatched group delimiter", span);
    case GroupOutcome.unclosed:
        switch (opener)
        {
        case '<':
            throw new ParseError("P003", "unclosed overlay group", span);
        case '{':
            throw new ParseError("P004", "unclosed required group", span);
        case '[':
            throw new ParseError("P006", "unclosed optional group", span);
        default:
            throw new ParseError("P008", "unclosed binding list", span);
        }
    }
}

/**
 * Whether a header that failed to scan still ends in a reserved suite marker.
 *
 * Both markers end in `::` and a marker is the last thing on its line, so the
 * only question left is whether that spelling sits at depth zero. A group the
 * scanner cannot balance has no depth-zero end, which keeps a marker spelling
 * inside an unclosed group opaque: `\newcommand{\x}{a::` is the raw TeX it
 * looks like. A lone trailing colon is TeX prose rather than a marker, so it
 * reserves nothing either way.
 */
private bool hasTopLevelSuiteMarker(dstring text, SourceSpan span) @safe pure
{
    const end = text.rstripSpaces.length;
    size_t index = 0;
    while (index < end)
    {
        if (inlineOpeners.canFind(text[index]))
        {
            try
                index = scanGroup(text, index, span).end;
            catch (ParseError _)
                return false;
            continue;
        }
        ++index;
    }
    return end > 1 && text[end - 2 .. end] == "::"d;
}

/// Scan one structural header while keeping group contents opaque.
struct HeaderScanner
{
    private dstring text;
    private SourceSpan span;
    private size_t end;

    /**
     * Whether a token only TeXFlux writes has been seen.
     *
     * Once one has, a failed scan is a malformed header rather than a line of
     * TeX that merely looked like one, so the error is raised instead of being
     * turned into a fallback.
     */
    bool sawStructure;

    this(dstring text, SourceSpan span) @safe pure nothrow
    {
        this.text = text;
        this.span = span;
        this.end = text.rstripSpaces.length;
    }

    private SourceSpan spanAt(size_t start, size_t stop) const @safe pure nothrow
    {
        return SourceSpan(span.file,
                SourcePosition(span.start.line, span.start.column + cast(int) start),
                SourcePosition(span.start.line, span.start.column + cast(int) stop));
    }

    private ParseError fail(string code, string message, size_t offset = 0) const @safe pure
    {
        const stop = offset + 1 < end ? offset + 1 : end;
        return new ParseError(code, message, spanAt(offset, stop));
    }

    private size_t skipSpaces(size_t position) const @safe pure nothrow
    {
        while (position < end && text[position] == ' ')
            ++position;
        return position;
    }

    /// Read the whole header, or raise the first thing wrong with it.
    HeaderScanResult scan() @safe pure
    {
        if (end == 0)
            throw fail("P010", "empty structural header");

        HeaderScanResult result;
        size_t position = 0;
        while (true)
        {
            auto segment = readSegment(position, result.segments.length == 0);
            result.segments ~= segment[0];
            position = segment[1];

            const separatorStart = position;
            position = skipSpaces(position);
            if (position == end)
                break;

            if (text[position] == ':')
            {
                auto marker = readSuiteMarker(position);
                result.suiteMode = marker[0];
                result.suiteSpan = marker[1];
                break;
            }

            if (text[position .. $].startsWith(">>"d))
            {
                const operatorStart = position;
                position = readStackSeparator(position, position > separatorStart);
                if (position == end)
                {
                    result.continuationSpan = spanAt(operatorStart, operatorStart + 2);
                    break;
                }
                continue;
            }

            throw fail("P011", "unexpected token in structural header; write '!| '"
                    ~ " in front of a line that has to stay raw TeX", position);
        }
        return result;
    }

    /**
     * Scan the trailing block or sequence suite marker.
     *
     * A lone colon is TeX prose rather than a marker, so it reserves no line: a
     * command header ending in one falls back to raw TeX, and an at-sign or
     * exclamation header ending in one is a malformed header. A composition
     * separator behind it still reserves the line, because `>>` is structural
     * wherever it is written.
     */
    private Tuple!(SuiteMode, SourceSpan) readSuiteMarker(size_t position) @safe pure
    {
        const markerStart = position;
        foreach (candidate; suiteMarkers)
        {
            if (!text[position .. $].startsWith(candidate[0]))
                continue;
            sawStructure = true;
            position = skipSpaces(position + candidate[0].length);
            if (position != end)
                throw fail("P012", "trailing token after suite marker", position);
            return tuple(candidate[1], spanAt(markerStart, position));
        }

        if (text[skipSpaces(position + 1) .. $].startsWith(">>"d))
            sawStructure = true;
        throw fail("P013", "unexpected ':' in a structural header;"
                ~ " the suite markers are '::' and ':::'", position);
    }

    /// The next segment's offset, or the line's end when it asks for one more.
    private size_t readStackSeparator(size_t position, bool spaced) @safe pure
    {
        const followed = position + 2 == end
            || (position + 2 < end && text[position + 2] == ' ');
        if (!spaced || !followed)
            throw fail("P014", "stack separator requires surrounding spaces", position);
        sawStructure = true;
        return skipSpaces(position + 2);
    }

    private Tuple!(Argument, size_t) readInlineGroup(size_t position) @safe pure
    {
        const start = position;
        const kind = kindOfOpener(text[position]).get;
        auto scanned = scanGroup(text, start, spanAt(start, start + 1));
        return tuple(inlineArgument(kind, scanned.value.to!string,
                spanAt(start, scanned.end)), scanned.end);
    }

    private Tuple!(Node, size_t) readSegment(size_t position, bool first) @safe pure
    {
        const segmentStart = position;
        if (position >= end)
            throw fail("P015", "missing structural segment", position);

        const prefix = text[position];
        Nullable!InvocationKind kind;
        switch (prefix)
        {
        case '!':
            break;
        case '\\':
            kind = InvocationKind.command;
            break;
        case '@':
            kind = InvocationKind.environment;
            break;
        default:
            throw fail("P016", first
                    ? "structural header must start with '\\', '@', or '!'"
                    : "each stack segment must start with '\\', '@', or '!'", position);
        }
        ++position;

        if (prefix == '@' && position < end && text[position] == '{')
        {
            auto group = readInlineGroup(position);
            position = group[1];
            return tuple(Node(ParsedInvocation(InvocationKind.brace, "", [group[0]],
                    null, spanAt(segmentStart, position))), position);
        }

        if (prefix == '@' && (position >= end || ":> "d.canFind(text[position])))
            return tuple(Node(ParsedInvocation(InvocationKind.transparent, "", [],
                    null, spanAt(segmentStart, position))), position);

        // Structural names are ASCII, so they do not change with the host locale.
        const nameStart = position;
        if (position >= end || !text[position].isAlpha)
            throw fail("P017", "invalid structural name", position);
        ++position;

        // A TeX environment name is scanned more broadly than an identifier on
        // purpose: a trailing star is the common case, and punctuation such as
        // a hyphen stays available to one.
        if (!kind.isNull && kind.get == InvocationKind.environment)
            while (position < end && !"{[<:> "d.canFind(text[position]))
                ++position;
        else
            while (position < end && (text[position].isAlphaNum || text[position] == '_'))
                ++position;

        const name = text[nameStart .. position].to!string;
        if (prefix == '!' && rawModeNames.canFind(name))
            throw fail("P018", "'!" ~ name ~ "' must stand alone on its own line",
                    segmentStart);

        Argument[] groups;
        while (position < end && inlineOpeners.canFind(text[position]))
        {
            auto group = readInlineGroup(position);
            groups ~= group[0];
            position = group[1];
        }

        // Only a special carries a binding list, and only after its groups, so
        // `(` stays ordinary text everywhere a command or environment reads it.
        bool binding = false;
        if (prefix == '!' && position < end && text[position] == bindingOpener)
        {
            auto group = readInlineGroup(position);
            groups ~= group[0];
            position = group[1];
            binding = true;
        }

        if (position < end && !" :>"d.canFind(text[position]))
        {
            if (binding && inlineOpeners.canFind(text[position]))
                throw fail("P019", "a special's '(...)' list must follow its groups",
                        position);
            if (binding && text[position] == bindingOpener)
                throw fail("P020", "a special accepts at most one '(...)' list", position);
            throw fail("P021", "unexpected token after structural name or group", position);
        }

        const segmentSpan = spanAt(segmentStart, position);
        if (prefix == '!')
            return tuple(Node(SpecialInvocation(name, groups, null, segmentSpan)), position);
        return tuple(Node(ParsedInvocation(kind.get, name, groups, null, segmentSpan)),
                position);
    }
}

/**
 * Scan one header, or report that the line stays raw TeX.
 *
 * An ordinary command only becomes structural through a token TeXFlux
 * reserves, so a scan that fails without seeing one is raw TeX. Those tokens
 * are `::`, `:::` and `>>`, and TeX prose writes none of them. A lone trailing
 * colon is not one of them, so `\textbf{Note}:` and `\item Note:` stay raw TeX
 * whatever follows them: a line is classified by reading that line and nothing
 * else.
 */
Nullable!HeaderScanResult scanStructuralHeader(dstring text, SourceSpan span) @safe pure
{
    auto scanner = HeaderScanner(text, span);
    try
        return scanner.scan().nullable;
    catch (ParseError error)
    {
        if (scanner.sawStructure || hasTopLevelSuiteMarker(text, span))
            throw error;
        return Nullable!HeaderScanResult.init;
    }
}

/**
 * Whether this header's suite has to produce at least one value.
 *
 * A command consumes its suite as arguments, so an empty one would emit a
 * silent `{}`. A container or a special accepts an empty suite.
 */
bool requiresValue(HeaderScanResult result) @safe pure
{
    return result.segments[$ - 1].match!(
        (ParsedInvocation invocation) => invocation.kind == InvocationKind.command,
        _ => false,
    );
}
