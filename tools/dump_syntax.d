/**
 * Print a document's syntax tree in the shared comparison format.
 *
 * This exists so that the two implementations can be diffed against each other
 * at the one place where a difference is cheapest to understand: right after
 * parsing, before any pass has had a chance to hide it. The Python side prints
 * the same format from tests/conformance/dump_syntax.py, and
 * tests/conformance/run.py compares the two over the whole corpus.
 *
 * It is deliberately not a subcommand of the real command line. Nothing a user
 * runs should depend on the internal shape of a tree.
 */
module tools.dump_syntax;

import std.array : appender, Appender;
import std.conv : to;
import std.file : readText;
import std.format : format;
import std.range : repeat;
import std.stdio : stderr, stdout;
import std.sumtype : match;
import std.typecons : Nullable;

import texflux.ast;
import texflux.errors : TeXFluxError;
import texflux.source : SourceSpan, SourceText;

int main(string[] args)
{
    if (args.length != 2)
    {
        stderr.writeln("usage: dump-syntax FILE");
        return 2;
    }
    try
    {
        auto document = parseFile(args[1]);
        auto output = appender!string();
        output.writeDocument(document);
        stdout.rawWrite(cast(const(ubyte)[]) output.data);
        return 0;
    }
    catch (TeXFluxError error)
    {
        stdout.rawWrite(cast(const(ubyte)[])("error " ~ error.code ~ " " ~ error.span.location
                ~ " " ~ quote(error.message) ~ "\n"));
        return 0;
    }
}

private Document parseFile(string path)
{
    import texflux.parser : parse;

    return parse(readText(path), path);
}

private void writeDocument(ref Appender!string output, Document document)
{
    output.put(format("document %s\n", spanText(document.span)));
    output.writeBlock(document.body_, 1);
}

private void writeBlock(ref Appender!string output, Block* block, size_t depth)
{
    output.indent(depth);
    output.put(format("block %s\n", spanText(block.span)));
    foreach (node; block.nodes)
        output.writeNode(node, depth + 1);
}

private void writeNode(ref Appender!string output, Node node, size_t depth)
{
    node.match!(
        (RawTex raw) {
            output.indent(depth);
            output.put(format("raw %s verbatim=%s text=%s\n", spanText(raw.span),
                    raw.verbatim ? "1" : "0", quote(raw.text)));
            output.writeParts(raw.parts, depth + 1);
        },
        (ParsedInvocation invocation) {
            output.indent(depth);
            output.put(format("parsed %s kind=%s name=%s suiteMode=%s suiteSpan=%s\n",
                    spanText(invocation.span), cast(string) invocation.kind,
                    quote(invocation.name), modeText(invocation.suiteMode),
                    optionalSpanText(invocation.suiteSpan)));
            output.writeGroups(invocation.groups, depth + 1);
            output.writeSuite(invocation.suite, depth + 1);
        },
        (SpecialInvocation invocation) {
            output.indent(depth);
            output.put(format("special %s name=%s suiteMode=%s suiteSpan=%s\n",
                    spanText(invocation.span), quote(invocation.name),
                    modeText(invocation.suiteMode), optionalSpanText(invocation.suiteSpan)));
            output.writeGroups(invocation.groups, depth + 1);
            output.writeSuite(invocation.suite, depth + 1);
        },
        (SequenceEntry entry) {
            output.indent(depth);
            output.put(format("entry %s marker=%s argumentKind=%s\n", spanText(entry.span),
                    spanText(entry.markerSpan), kindText(entry.argumentKind)));
            output.writeBlock(entry.value, depth + 1);
        },
        (Stack composition) {
            output.indent(depth);
            output.put(format("stack %s suiteMode=%s suiteSpan=%s\n",
                    spanText(composition.span), modeText(composition.suiteMode),
                    optionalSpanText(composition.suiteSpan)));
            output.indent(depth + 1);
            output.put("segments\n");
            foreach (segment; composition.segments)
                output.writeNode(segment, depth + 2);
            output.writeSuite(composition.suite, depth + 1);
        },
        (GenericInvocation invocation) {
            output.indent(depth);
            output.put(format("invocation %s name=%s\n", spanText(invocation.span),
                    quote(invocation.name)));
            output.writeGroups(invocation.arguments, depth + 1);
            output.writeSuite(invocation.body_, depth + 1, "body");
        },
        (BraceGroup group) {
            output.indent(depth);
            output.put(format("brace %s header=%s\n", spanText(group.span),
                    quote(group.headerRaw)));
            output.writeParts(group.headerParts, depth + 1);
            output.writeBlock(group.body_, depth + 1);
        },
    );
}

private void writeGroups(ref Appender!string output, Argument[] groups, size_t depth)
{
    foreach (group; groups)
    {
        output.indent(depth);
        output.put(format("group kind=%s layout=%s %s\n", cast(string) group.kind,
                cast(string) group.layout, spanText(group.span)));
        group.value.match!(
            (string text) {
                output.indent(depth + 1);
                output.put(format("text=%s\n", quote(text)));
            },
            (Block* nested) { output.writeBlock(nested, depth + 1); },
        );
        output.writeParts(group.parts, depth + 1);
    }
}

private void writeSuite(ref Appender!string output, Block* suite, size_t depth, string label = "suite")
{
    if (suite is null)
        return;
    output.indent(depth);
    output.put(label ~ "\n");
    output.writeBlock(suite, depth + 1);
}

private void writeParts(ref Appender!string output, Nullable!SourceText parts, size_t depth)
{
    if (parts.isNull)
        return;
    output.indent(depth);
    output.put("parts\n");
    foreach (fragment; parts.get)
    {
        output.indent(depth + 1);
        output.put(format("fragment %s scaffold=%s text=%s\n", spanText(fragment.span),
                fragment.scaffold ? "1" : "0", quote(fragment.text)));
    }
}

private void indent(ref Appender!string output, size_t depth)
{
    output.put(' '.repeat(depth * 2));
}

private string spanText(SourceSpan span)
{
    return format("%s:%d:%d-%d:%d", span.file, span.start.line, span.start.column,
            span.end.line, span.end.column);
}

private string optionalSpanText(Nullable!SourceSpan span)
{
    return span.isNull ? "-" : spanText(span.get);
}

private string modeText(Nullable!SuiteMode mode)
{
    return mode.isNull ? "-" : cast(string) mode.get;
}

private string kindText(Nullable!GroupKind kind)
{
    return kind.isNull ? "-" : cast(string) kind.get;
}

/// One text value, escaped so that a line of the dump is always one line.
private string quote(string text)
{
    auto output = appender!string();
    output.put('"');
    foreach (char c; text)
    {
        switch (c)
        {
        case '"':
            output.put("\\\"");
            break;
        case '\\':
            output.put("\\\\");
            break;
        case '\n':
            output.put("\\n");
            break;
        case '\r':
            output.put("\\r");
            break;
        case '\t':
            output.put("\\t");
            break;
        default:
            output.put(c);
        }
    }
    output.put('"');
    return output.data;
}
