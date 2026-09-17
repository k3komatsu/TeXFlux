/**
 * Turning physical lines into a syntax tree.
 *
 * Indentation is the whole of the structure: four spaces open a suite, a line
 * at its suite's base is a child of it, and a line that dedents closes it.
 * Everything else a line can be -- raw TeX, a blank separator, an escaped
 * literal, a verbatim region -- is decided by reading that line alone, which
 * is what lets an author drop arbitrary TeX into a document and know it will
 * come out the other side unread.
 *
 * Lines are scanned as code points, so an offset into a line is also a column.
 */
module texflux.parser;

import std.algorithm : canFind, countUntil, map, startsWith;
import std.array : array, join, replace, split;
import std.conv : to;
import std.sumtype : get, has, match;
import std.typecons : Nullable, nullable, Tuple, tuple;

import texflux.ast;
import texflux.errors : Descent, ParseError;
import texflux.scanner;
import texflux.source : SourcePosition, SourceSpan, SourceText;
import texflux.syntax : rawBeginMarker, rawEndMarker, rawLineMarker;
import texflux.text : isBlank, leadingSpaces, lstripSpaces, rstripSpaces, stripSpaces;

/// One line of the source, numbered from one.
private struct PhysicalLine
{
    dstring text;
    int number;

    /// How far the line is indented, which only ASCII spaces contribute to.
    size_t indent() const @safe pure nothrow
    {
        return text.leadingSpaces;
    }

    /// Whether the line holds nothing but spaces.
    bool blank() const @safe pure nothrow
    {
        return text.isBlank;
    }
}

/// Parse source text into the syntax tree.
Document parse(string source, string filename = "<string>") @safe
{
    auto parser = Parser(source, filename);
    return parser.run();
}

private struct Parser
{
    private string filename;
    private PhysicalLine[] lines;
    private SourceSpan documentSpan;
    private size_t index;
    private size_t[size_t] rawRegions;
    private bool[size_t] tabExempt;
    private size_t depth;

    this(string source, string filename) @safe
    {
        // The three spellings of a line ending become one before anything
        // measures a line, so a column never depends on which was written.
        const normalized = source.replace("\r\n", "\n").replace("\r", "\n");
        auto physical = normalized.split("\n");
        if (physical.length != 0 && physical[$ - 1].length == 0)
            physical = physical[0 .. $ - 1];

        this.filename = filename;
        this.documentSpan = SourceSpan(filename, SourcePosition(1, 1),
                SourcePosition(1, 1).advance(normalized));
        foreach (number, text; physical)
            lines ~= PhysicalLine(text.to!dstring, cast(int)(number + 1));

        rawRegions = scanRawRegions();
        // A raw region and a one-line raw escape are the two constructs that
        // keep their body exactly as written, tabs included, so both are
        // exempt from the prohibition below.
        foreach (begin, end; rawRegions)
            foreach (inside; begin + 1 .. end)
                tabExempt[inside] = true;
        foreach (at, line; lines)
            if (line.text.lstripSpaces.startsWith(rawLineMarker))
                tabExempt[at] = true;
        foreach (at, line; lines)
            if (at !in tabExempt)
                rejectTab(line);
    }

    Document run() @safe
    {
        auto body_ = block(0, documentSpan);
        return Document(body_, documentSpan);
    }

    // ---- spans -----------------------------------------------------------

    private SourceSpan lineSpan(PhysicalLine line, size_t startColumn = 1) const @safe pure
    {
        return lineSpan(line, startColumn, line.text[startColumn - 1 .. $]);
    }

    private SourceSpan lineSpan(PhysicalLine line, size_t startColumn, dstring text) const @safe pure
    {
        return SourceSpan(filename,
                SourcePosition(line.number, cast(int) startColumn),
                SourcePosition(line.number, cast(int)(startColumn + text.length)));
    }

    private SourceSpan headerSpan(PhysicalLine line, size_t base) const @safe pure
    {
        return lineSpan(line, base + 1, line.text[base .. $].rstripSpaces);
    }

    private static SourceSpan blockSpan(SourceSpan boundary, Node[] nodes) @safe pure
    {
        auto end = boundary.end;
        foreach (node; nodes)
            if (node.spanOf.end > end)
                end = node.spanOf.end;
        return SourceSpan(boundary.file, boundary.start, end);
    }

    // ---- whole-file pre-scans --------------------------------------------

    /// The raw-mode marker a whole line consists of, if it consists of one.
    private static Nullable!dstring lineMarker(PhysicalLine line) @safe pure
    {
        const text = line.text.stripSpaces;
        if (text == rawBeginMarker || text == rawEndMarker)
            return text.nullable;
        return Nullable!dstring.init;
    }

    /**
     * Pair the raw-region markers, as begin index to end index.
     *
     * Pairing is a property of the lines rather than of the tree, so it is
     * resolved before parsing: the tab prohibition has to know which lines are
     * verbatim, and an unpaired marker leaves the rest of the file's structure
     * meaningless.
     */
    private size_t[size_t] scanRawRegions() @safe
    {
        size_t[size_t] regions;
        Nullable!size_t begin;
        size_t indent;
        foreach (at, line; lines)
        {
            const marker = lineMarker(line);
            if (marker.isNull)
                continue;
            if (begin.isNull)
            {
                if (marker.get == rawEndMarker)
                    throw new ParseError("P001",
                            "'" ~ rawEndMarker ~ "' has no matching '" ~ rawBeginMarker ~ "'",
                            headerSpan(line, line.indent));
                begin = at;
                indent = line.indent;
            }
            else if (marker.get == rawEndMarker && line.indent == indent)
            {
                regions[begin.get] = at;
                begin.nullify();
            }
        }
        if (!begin.isNull)
            throw new ParseError("P002",
                    "'" ~ rawBeginMarker ~ "' is not closed by '" ~ rawEndMarker ~ "'",
                    headerSpan(lines[begin.get], lines[begin.get].indent));
        return regions;
    }

    private void rejectTab(PhysicalLine line) const @safe pure
    {
        const at = line.text.countUntil('\t');
        if (at >= 0)
            throw new ParseError("P022",
                    "tab characters are not allowed; keep one after '!| '"
                    ~ " or inside a raw-mode region",
                    lineSpan(line, at + 1, line.text[at .. at + 1]));
    }

    // ---- cursor ----------------------------------------------------------

    private Nullable!size_t nextNonblank(size_t from) const @safe pure nothrow
    {
        while (from < lines.length && lines[from].blank)
            ++from;
        return from == lines.length ? Nullable!size_t.init : from.nullable;
    }

    /**
     * Consume a run of blank lines, or rewind when it ends the block.
     *
     * A run is block content while more content follows at the base. At the
     * document root a trailing run is content too, because no enclosing block
     * can reclaim it.
     */
    private Nullable!(Tuple!(size_t, size_t)) blankRun(size_t base) @safe pure nothrow
    {
        size_t start = index;
        while (index < lines.length && lines[index].blank)
            ++index;
        const endsBlock = index >= lines.length || lines[index].indent < base;
        if (base != 0 && endsBlock)
        {
            index = start;
            return typeof(return).init;
        }
        return tuple(start, index).nullable;
    }

    /// One empty raw line for each physical line of a blank run.
    private Node[] blankNodes(Tuple!(size_t, size_t) run) const @safe pure
    {
        Node[] nodes;
        foreach (at; run[0] .. run[1])
            nodes ~= Node(RawTex("", lineSpan(lines[at])));
        return nodes;
    }

    // ---- blocks ----------------------------------------------------------

    private Block* block(size_t base, SourceSpan boundary) @safe
    {
        auto descent = Descent(depth);
        Node[] nodes;
        while (index < lines.length)
        {
            const line = lines[index];
            if (line.blank)
            {
                auto run = blankRun(base);
                if (run.isNull)
                    break;
                nodes ~= blankNodes(run.get);
                continue;
            }

            if (line.indent < base)
                break;

            const rest = line.text[base .. $];
            const extra = rest.leadingSpaces;
            const first = extra < rest.length ? rest[extra] : dchar.init;

            // Escapes precede the indentation rules, so an escaped line may
            // sit deeper than the base and keeps those extra spaces.
            auto escaped = escapedRaw(line, rest[extra .. $], line.indent + 1,
                    lineSpan(line, base + 1, rest), rest[0 .. extra]);
            if (!escaped.isNull)
            {
                nodes ~= Node(escaped.get);
                ++index;
                continue;
            }

            const structuralPrefix = first == '@' || first == '!';
            if (structuralPrefix && line.indent != base)
                throw indentError(line);

            if (line.indent != base)
            {
                if (first == '\\' && !scanCommandHeader(line, line.indent).isNull)
                    throw indentError(line);
                nodes ~= emitRaw(line, base);
                continue;
            }

            if (structuralPrefix)
            {
                if (rest.rstripSpaces == rawBeginMarker)
                {
                    nodes ~= rawRegion(base);
                    continue;
                }
                nodes ~= structuralLine(line, base);
                continue;
            }

            if (first == '\\')
            {
                auto structural = tryStructuralCommand(line, base);
                if (!structural.isNull)
                {
                    nodes ~= structural.get;
                    continue;
                }
            }

            nodes ~= emitRaw(line, base);
        }
        return new Block(nodes, blockSpan(boundary, nodes));
    }

    /// One raw TeX line, dedented to the suite base.
    private Node emitRaw(PhysicalLine line, size_t base) @safe
    {
        const text = line.text[base .. $];
        ++index;
        return Node(RawTex(text.to!string, lineSpan(line, base + 1, text)));
    }

    /**
     * The raw line a leading escape produces, or nothing when there is none.
     *
     * `@@` and `!!` strip one prefix character and stay a text field, so what
     * they produce can still be interpolated. `!|` strips the whole marker and
     * one space and is verbatim. Both keep the spaces beyond the block base in
     * front of the body.
     */
    private Nullable!RawTex escapedRaw(PhysicalLine line, dstring text, size_t column,
            SourceSpan span, dstring indent = "") @safe pure
    {
        const head = text.length >= 2 ? text[0 .. 2] : ""d;
        if (head == "@@"d || head == "!!"d)
            return RawTex((indent ~ text[1 .. $]).to!string, span).nullable;
        if (head == rawLineMarker)
        {
            const tail = rawLineTail(line, text[rawLineMarker.length .. $], column);
            return RawTex((indent ~ tail).to!string, span, Nullable!SourceText.init, true)
                .nullable;
        }
        return Nullable!RawTex.init;
    }

    /**
     * The body of one one-line raw escape, or a rejection of a missing space.
     *
     * One space separates the marker from the body and is not part of it.
     * Everything after that space is kept exactly as written up to the
     * newline, the way a raw-mode region keeps its own lines.
     */
    private dstring rawLineTail(PhysicalLine line, dstring tail, size_t markerColumn) const @safe pure
    {
        if (tail.length != 0 && tail[0] != ' ')
            throw new ParseError("P023",
                    "'" ~ rawLineMarker ~ "' must be followed by one space or end the line",
                    lineSpan(line, markerColumn, rawLineMarker));
        return tail.length == 0 ? tail : tail[1 .. $];
    }

    /**
     * One raw-mode region, as the verbatim lines between its markers.
     *
     * The region's extent was fixed before parsing, so nothing here scans a
     * header, honours an escape, or lets a dedent close the enclosing block.
     */
    private Node[] rawRegion(size_t base) @safe
    {
        auto end = index in rawRegions;
        if (end is null)
            // A whole-line marker at an unexpected nesting level is swallowed
            // by the pre-scan as a nested begin. That is an implementation
            // detail, so it surfaces as the indentation error it looks like.
            throw indentError(lines[index]);

        Node[] nodes;
        foreach (at; index + 1 .. *end)
        {
            const line = lines[at];
            const cut = base < line.indent ? base : line.indent;
            const text = line.text[cut .. $];
            nodes ~= Node(RawTex(text.to!string, lineSpan(line, cut + 1, text),
                    Nullable!SourceText.init, true));
        }
        index = *end + 1;
        return nodes;
    }

    private ParseError indentError(PhysicalLine line) const @safe pure
    {
        return new ParseError("P024",
                "invalid structural indentation; a structural line sits at its suite base,"
                ~ " and a literal '@' or '!' line is written '@@' or '!!'",
                lineSpan(line, line.indent + 1));
    }

    // ---- structural lines ------------------------------------------------

    /// Scan a backslash line, or report that it stays ordinary TeX.
    private Nullable!HeaderScanResult scanCommandHeader(PhysicalLine line, size_t base) @safe pure
    {
        auto result = scanStructuralHeader(line.text[base .. $], headerSpan(line, base));
        // A closed single-segment command line is ordinary TeX.
        if (result.isNull || result.get.closedSingle)
            return Nullable!HeaderScanResult.init;
        return result;
    }

    private Nullable!Node tryStructuralCommand(PhysicalLine line, size_t base) @safe
    {
        auto result = scanCommandHeader(line, base);
        if (result.isNull)
            return Nullable!Node.init;
        return statement(line, base, result.get).nullable;
    }

    /// Parse an at-sign or exclamation line, which has to scan as a header.
    private Node structuralLine(PhysicalLine line, size_t base) @safe
    {
        const span = headerSpan(line, base);
        auto scanner = HeaderScanner(line.text[base .. $], span);
        return statement(line, base, scanner.scan());
    }

    /// Consume the header line and attach whatever suite it asks for.
    private Node statement(PhysicalLine line, size_t base, HeaderScanResult result) @safe
    {
        ++index;
        if (result.closedSingle)
            rejectMissingSuite(base, result.segments[0]);
        return structuralNode(base, result, headerSpan(line, base));
    }

    /// Reject a suiteless line that an indented block or an at sign needs.
    private void rejectMissingSuite(size_t base, Node segment) @safe pure
    {
        const isEnvironment = segment.match!(
            (ParsedInvocation invocation) => invocation.kind == InvocationKind.environment,
            _ => false,
        );
        if (isEnvironment)
            throw new ParseError("P025",
                    "environment directives require a suite marker '::' or ':::';"
                    ~ " a literal '@' line is written '@@'", segment.spanOf);

        auto next = nextNonblank(index);
        if (!next.isNull && lines[next.get].indent >= base + 4)
        {
            const line = lines[next.get];
            throw new ParseError("P026", "indented lines require a suite marker '::' or ':::'",
                    lineSpan(line, line.indent + 1));
        }
    }

    /// Attach the parsed suite, if any, to one scanned header.
    private Node structuralNode(size_t base, HeaderScanResult result, SourceSpan headerSpan_) @safe
    {
        if (!result.continuationSpan.isNull)
        {
            auto segments = result.segments;
            while (!result.continuationSpan.isNull)
            {
                if (index == lines.length)
                    throw new ParseError("P027", "stack separator needs a following segment",
                            result.continuationSpan.get);
                const line = lines[index];
                if (line.blank || line.indent != base)
                    throw new ParseError("P028",
                            "stack continuation requires the next line at the same indentation",
                            lineSpan(line, line.indent + 1));
                const span = headerSpan(line, base);
                auto scanner = HeaderScanner(line.text[base .. $], span);
                result = scanner.scan();
                segments ~= result.segments;
                headerSpan_ = SourceSpan(headerSpan_.file, headerSpan_.start, span.end);
                ++index;
            }
            result.segments = segments;
        }

        if (result.suiteMode.isNull)
        {
            if (result.segments.length == 1)
                return result.segments[0];
            return Node(Stack(result.segments, null, headerSpan_));
        }

        auto suite = parseSuite(base, result, headerSpan_);
        if (result.segments.length == 1)
            return result.segments[0]
                .withSuite(suite, result.suiteMode, result.suiteSpan)
                .withSpan(headerSpan_);
        return Node(Stack(result.segments, suite, headerSpan_, result.suiteMode,
                result.suiteSpan));
    }

    private Block* parseSuite(size_t base, HeaderScanResult result, SourceSpan headerSpan_) @safe
    {
        const suiteBase = base + 4;
        auto next = nextNonblank(index);
        if (result.suiteMode.get == SuiteMode.sequence)
        {
            if (!next.isNull && base < lines[next.get].indent
                    && lines[next.get].indent < suiteBase)
            {
                const line = lines[next.get];
                throw new ParseError("P029",
                        "sequence suite entries require four-space indentation",
                        lineSpan(line, line.indent + 1));
            }
            auto suite = sequenceSuite(suiteBase, headerSpan_);
            if (suite.nodes.length == 0 && result.requiresValue)
                throw new ParseError("P030",
                        "sequence suites require at least one '-' or '+' value entry",
                        headerSpan_);
            return suite;
        }

        if (!next.isNull && lines[next.get].indent >= suiteBase)
            return block(suiteBase, headerSpan_);
        if (result.requiresValue)
            // An empty '{}' is raw TeX the author can write directly, so a
            // command's marker always owns a body and '\texttt{std}::' cannot
            // quietly become '\texttt{std}{}'.
            throw new ParseError("P037", "command block suites require an indented body",
                    headerSpan_);
        return new Block(null, headerSpan_);
    }

    // ---- sequence suites -------------------------------------------------

    private Block* sequenceSuite(size_t base, SourceSpan boundary) @safe
    {
        // An entry's own suite recurses here without passing through `block`,
        // so this is the second place the depth has to be counted.
        auto descent = Descent(depth);
        Node[] entries;
        while (index < lines.length)
        {
            const line = lines[index];
            if (line.blank)
            {
                // Blank runs separate entries; only their boundary matters.
                if (blankRun(base).isNull)
                    break;
                continue;
            }
            if (line.indent < base)
                break;
            if (line.indent != base)
                throw new ParseError("P031", "sequence entries must start at suite indentation",
                        lineSpan(line, line.indent + 1));
            const marker = line.text[base];
            if (marker != '-' && marker != '+')
                throw new ParseError("P032", "sequence suites require '-' or '+' value entries",
                        lineSpan(line, base + 1));
            entries ~= Node(sequenceEntry(line, base, marker));
        }
        return new Block(entries, blockSpan(boundary, entries));
    }

    private SequenceEntry sequenceEntry(PhysicalLine line, size_t base, dchar marker) @safe
    {
        const markerSpan_ = lineSpan(line, base + 1, line.text[base .. base + 1]);
        const entrySpan = lineSpan(line, base + 1, line.text[base .. $]);
        const payloadStart = base + 1 + line.text[base + 1 .. $].leadingSpaces;
        const payloadRaw = line.text[payloadStart .. $];
        const payload = marker == '+' ? payloadRaw : payloadRaw.rstripSpaces;

        ++index;

        if (marker == '+')
            return explicitSequenceEntry(line, payload, payloadStart, markerSpan_, entrySpan);

        Node[] nodes;
        if (payload.length != 0)
        {
            const payloadSpan = lineSpan(line, payloadStart + 1, payload);
            auto escaped = escapedRaw(line, payload, payloadStart + 1, payloadSpan);
            if (!escaped.isNull)
                nodes ~= Node(escaped.get);
            else if ("\\@!"d.canFind(payload[0]))
            {
                // Only a command payload may turn out to be ordinary TeX; an
                // at-sign or exclamation payload must scan as a header.
                Nullable!HeaderScanResult result;
                if (payload[0] == '\\')
                    result = scanStructuralHeader(payload, payloadSpan);
                else
                {
                    auto scanner = HeaderScanner(payload, payloadSpan);
                    result = scanner.scan().nullable;
                }
                nodes ~= result.isNull
                    ? Node(RawTex(payload.to!string, payloadSpan))
                    : structuralNode(base, result.get, payloadSpan);
            }
            else
                nodes ~= Node(RawTex(payload.to!string, payloadSpan));
        }

        nodes ~= sequenceContinuation(base, entrySpan).nodes;

        SourcePosition valueEnd = entrySpan.end;
        foreach (node; nodes)
            if (node.endOf > valueEnd)
                valueEnd = node.endOf;
        const valueSpan = SourceSpan(entrySpan.file, entrySpan.start, valueEnd);
        return SequenceEntry(new Block(nodes, valueSpan), markerSpan_, valueSpan);
    }

    /**
     * One `+` entry, as exactly one opaque authored group.
     *
     * The group is scanned only after the physical continuation lines have
     * been dedented. None of them go through header scanning or the raw-line
     * escapes: an explicit group is authored TeX and stays that way.
     */
    private SequenceEntry explicitSequenceEntry(PhysicalLine line, dstring payload,
            size_t payloadStart, SourceSpan markerSpan_, SourceSpan entrySpan) @safe
    {
        const payloadSpan = lineSpan(line, payloadStart + 1, payload);
        if (payload.length == 0 || !inlineOpeners.canFind(payload[0]))
            throw new ParseError("P033",
                    "explicit sequence entries require one '{...}', '[...]', or '<...>' group",
                    payloadSpan);

        Node[] nodes = [Node(RawTex(payload.to!string, payloadSpan))];
        nodes ~= rawSequenceContinuation(line.indent, entrySpan).nodes;

        RawTex[] rawNodes;
        foreach (node; nodes)
        {
            if (!node.has!RawTex)
                throw new ParseError("P034", "explicit sequence entries require opaque raw text",
                        node.spanOf);
            rawNodes ~= node.get!RawTex;
        }

        auto texts = rawNodes.map!(node => node.text.to!dstring).array;
        const joined = texts.join("\n"d);

        auto kind = kindOfOpener(payload[0]);
        size_t end;
        try
            end = scanGroup(joined, 0, payloadSpan).end;
        catch (ParseError error)
            throw new ParseError("P035", "explicit sequence entries require one balanced group",
                    error.span);
        if (!joined[end .. $].isBlank)
            throw new ParseError("P036", "explicit sequence entries require exactly one group",
                    payloadSpan);

        auto kept = truncate(rawNodes, texts, end);
        auto value = new Block(kept, blockSpan(entrySpan, kept));
        return SequenceEntry(value, markerSpan_, value.span, kind);
    }

    /**
     * Keep the first `length` characters of the joined raw lines.
     *
     * The joins are newlines that no line holds, so each boundary costs one
     * more character than the line before it spelled.
     */
    private static Node[] truncate(RawTex[] nodes, dstring[] texts, size_t length) @safe pure
    {
        Node[] kept;
        size_t remaining = length;
        foreach (at, node; nodes)
        {
            if (remaining <= texts[at].length)
            {
                if (remaining != 0)
                {
                    const text = texts[at][0 .. remaining].to!string;
                    kept ~= Node(rawTex(text, SourceSpan(node.span.file, node.span.start,
                            node.span.start.advance(text)), node.parts, node.verbatim));
                }
                break;
            }
            kept ~= Node(node);
            remaining -= texts[at].length;
            if (at + 1 < nodes.length)
                --remaining;
        }
        return kept;
    }

    /// Read a sequence continuation without interpreting its contents.
    private Block* rawSequenceContinuation(size_t base, SourceSpan boundary) @safe
    {
        auto next = nextNonblank(index);
        if (next.isNull || lines[next.get].indent <= base)
            return new Block(null, boundary);

        const continuationBase = lines[next.get].indent;
        Node[] nodes;
        while (index < lines.length)
        {
            const line = lines[index];
            if (line.blank)
            {
                auto run = blankRun(continuationBase);
                if (run.isNull)
                    break;
                nodes ~= blankNodes(run.get);
                continue;
            }
            if (line.indent < continuationBase)
                break;
            const marker = lineMarker(line);
            if (!marker.isNull)
            {
                // An explicit group is opaque, but a raw-mode marker keeps its
                // own indentation rule everywhere a continuation can occur.
                if (line.indent != continuationBase)
                    throw indentError(line);
                if (marker.get == rawBeginMarker)
                {
                    nodes ~= rawRegion(continuationBase);
                    continue;
                }
            }
            if (index in tabExempt)
                // An explicit group's body is opaque authored TeX, so the
                // one-line escape is not an escape here and the line never
                // earned its exemption from the whole-file scan.
                rejectTab(line);
            const text = line.text[continuationBase .. $];
            nodes ~= Node(RawTex(text.to!string, lineSpan(line, continuationBase + 1, text)));
            ++index;
        }
        return new Block(nodes, blockSpan(boundary, nodes));
    }

    private Block* sequenceContinuation(size_t base, SourceSpan boundary) @safe
    {
        auto next = nextNonblank(index);
        if (next.isNull || lines[next.get].indent <= base)
            return new Block(null, boundary);
        return block(lines[next.get].indent, boundary);
    }
}

/// A suite nested too deeply to follow is refused rather than overflowing the stack.
@safe unittest
{
    import std.exception : assertThrown;
    import std.range : repeat;
    import texflux.errors : NestingError;

    // Every entry opens a sequence suite of its own, which never passes
    // through a block.
    string source = "\\root:::\n";
    foreach (level; 1 .. 501)
        source ~= ' '.repeat(4 * level).array ~ "- \\a:::\n";
    source ~= ' '.repeat(4 * 501).array ~ "- x\n";
    assertThrown!NestingError(parse(source, "deep.tfx"));
}
