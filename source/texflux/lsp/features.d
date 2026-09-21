/**
 * Small editor index built from the strict TeXFlux syntax tree.
 *
 * This module deliberately does not parse TeX. It records only names and
 * ranges that the TeXFlux parser has already classified, leaving completion
 * as the only feature that can work while the document is syntactically
 * incomplete.
 */
module texflux.lsp.features;

import std.algorithm : canFind;
import std.string : indexOf;
import std.sumtype : match;

import texflux.ast;
import texflux.errors : ParseError;
import texflux.parser : parse;
import texflux.source : SourcePosition, SourceSpan;
import texflux.syntax : rawModeNames, requiredText;
import texflux.text : countCodePoints, stripWhitespace;

enum FeatureOccurrenceKind
{
    macro_,
    flag_,
    modulePath,
}

enum FeatureTokenType : long
{
    namespace_,
    function_,
    keyword,
    variable,
    string_,
    macro_,
    flag_,
}

struct FeatureSymbol
{
    string name;
    long kind;
    SourceSpan span;
    SourceSpan selection;
    string container;
}

struct FeatureFold
{
    SourceSpan span;
    string kind;
}

struct FeatureOccurrence
{
    string name;
    FeatureOccurrenceKind kind;
    SourceSpan span;
    bool declaration;
    string target;
    string moduleKind;
}

struct FeatureToken
{
    SourceSpan span;
    long tokenType;
    long modifiers;
}

struct FeatureIndex
{
    bool complete;
    FeatureSymbol[] symbols;
    FeatureFold[] folds;
    FeatureOccurrence[] occurrences;
    FeatureToken[] tokens;
}

/// Build an index only when the syntax tree is complete.
FeatureIndex buildFeatureIndex(string source, string filename)
{
    FeatureIndex result;
    try
    {
        auto document = parse(source, filename);
        visitBlock(result, document.body_, true, null);
        result.complete = true;
    }
    catch (ParseError _)
    {
        // A partial syntax tree is not a safe basis for definitions or edits.
        result.complete = false;
    }
    return result;
}

enum builtinSpecialNames = [
    "before", "after", "around", "off", "drop", "when", "unless", "flag",
    "defmacro", "param", "text", "each", "import", "macroimport", "bundleimport"
];

private void visitBlock(ref FeatureIndex result, Block* block, bool topLevel,
        string container)
{
    if (block is null)
        return;
    foreach (node; block.nodes)
        visitNode(result, node, topLevel, container);
}

private void visitNode(ref FeatureIndex result, Node node, bool topLevel, string container)
{
    node.match!(
        (ParsedInvocation invocation) {
            visitParsed(result, invocation, topLevel, container);
        },
        (SpecialInvocation invocation) {
            visitSpecial(result, invocation, topLevel, container);
        },
        (SequenceEntry entry) {
            visitBlock(result, entry.value, false, container);
        },
        (Stack composition) {
            addSuiteFold(result, composition.span, composition.suite, "region");
            foreach (segment; composition.segments)
                visitNode(result, segment, false, container);
            visitBlock(result, composition.suite, false, container);
        },
        (BraceGroup group) {
            visitBlock(result, group.body_, false, container);
        },
        (GenericInvocation _) {},
        (RawTex _) {},
    );
}

private void visitParsed(ref FeatureIndex result, ParsedInvocation invocation,
        bool topLevel, string container)
{
    string childContainer = container;
    if (invocation.kind == InvocationKind.environment)
    {
        const selection = nameSpan(invocation.span, "@", invocation.name);
        const symbolName = "@" ~ invocation.name;
        result.symbols ~= FeatureSymbol(symbolName, 3L, nodeSpan(
                invocation.span, invocation.suite), selection, container);
        result.tokens ~= FeatureToken(selection, cast(long) FeatureTokenType.namespace_, 0L);
        childContainer = symbolName;
    }
    else if (invocation.kind == InvocationKind.command)
    {
        const selection = nameSpan(invocation.span, "\\", invocation.name);
        result.tokens ~= FeatureToken(selection, cast(long) FeatureTokenType.function_, 0L);
    }

    addSuiteFold(result, invocation.span, invocation.suite, "region");
    visitGroups(result, invocation.groups, false, childContainer);
    visitBlock(result, invocation.suite, false, childContainer);
}

private void visitSpecial(ref FeatureIndex result, SpecialInvocation invocation,
        bool topLevel, string container)
{
    const header = nameSpan(invocation.span, "!", invocation.name);
    const isDefinition = invocation.name == "defmacro";
    const isFlag = invocation.name == "flag";
    const isConditional = invocation.name == "when" || invocation.name == "unless";
    const isModule = invocation.name == "import"
        || invocation.name == "macroimport" || invocation.name == "bundleimport";

    if (isDefinition && topLevel && invocation.groups.length != 0)
    {
        string name;
        SourceSpan selection;
        if (groupValue(invocation.groups[0], name, selection))
        {
            const symbolName = "!" ~ name;
            result.symbols ~= FeatureSymbol(symbolName, 12L, nodeSpan(
                    invocation.span, invocation.suite), selection, container);
            result.occurrences ~= FeatureOccurrence(name, FeatureOccurrenceKind.macro_,
                    selection, true, null, null);
            result.tokens ~= FeatureToken(selection, cast(long) FeatureTokenType.macro_, 0L);
        }
    }
    else if (isFlag && topLevel && invocation.groups.length != 0)
    {
        string name;
        SourceSpan selection;
        if (groupValue(invocation.groups[0], name, selection))
        {
            result.symbols ~= FeatureSymbol(name, 13L, nodeSpan(
                    invocation.span, invocation.suite), selection, container);
            result.occurrences ~= FeatureOccurrence(name, FeatureOccurrenceKind.flag_,
                    selection, true, null, null);
            result.tokens ~= FeatureToken(selection, cast(long) FeatureTokenType.flag_, 0L);
        }
    }
    else if (isConditional)
    {
        foreach (group; invocation.groups)
        {
            string name;
            SourceSpan selection;
            if (group.kind == GroupKind.required && groupValue(group, name, selection))
            {
                result.occurrences ~= FeatureOccurrence(name, FeatureOccurrenceKind.flag_,
                        selection, false, null, null);
                result.tokens ~= FeatureToken(selection, cast(long) FeatureTokenType.flag_, 0L);
            }
        }
    }
    else if (isModule)
    {
        if (invocation.groups.length != 0)
        {
            string path;
            SourceSpan selection;
            if (groupValue(invocation.groups[0], path, selection))
            {
                result.occurrences ~= FeatureOccurrence(path,
                        FeatureOccurrenceKind.modulePath, selection, false, path,
                        invocation.name);
                result.tokens ~= FeatureToken(selection, cast(long) FeatureTokenType.string_, 0L);
            }
        }
    }
    else if (!isBuiltinSpecial(invocation.name))
    {
        result.occurrences ~= FeatureOccurrence(invocation.name,
                FeatureOccurrenceKind.macro_, nameOnlySpan(invocation.span, invocation.name),
                false, null, null);
        result.tokens ~= FeatureToken(header, cast(long) FeatureTokenType.macro_, 0L);
    }

    if (isBuiltinSpecial(invocation.name))
        result.tokens ~= FeatureToken(header, cast(long) FeatureTokenType.keyword, 0L);

    addSuiteFold(result, invocation.span, invocation.suite, "region");
    visitGroups(result, invocation.groups, false, container);
    visitBlock(result, invocation.suite, false, container);
}

private void visitGroups(ref FeatureIndex result, Argument[] groups, bool topLevel,
        string container)
{
    foreach (group; groups)
        group.value.match!(
            (string _) {},
            (Block* block) { visitBlock(result, block, topLevel, container); },
        );
}

bool isBuiltinSpecial(string name) @safe pure nothrow
{
    return builtinSpecialNames.canFind(name);
}

bool isReservedMacroName(string name) @safe pure nothrow
{
    return isBuiltinSpecial(name) || rawModeNames.canFind(name);
}

private bool groupValue(Argument group, out string value, out SourceSpan span)
{
    auto text = requiredText(group);
    if (text.isNull)
        return false;
    value = stripWhitespace(text.get);
    if (value.length == 0)
        return false;
    const offset = text.get.indexOf(value);
    if (offset < 0)
        return false;
    const start = SourcePosition(group.span.start.line,
            group.span.start.column + 1 + cast(int) countCodePoints(text.get[0 .. offset]));
    span = SourceSpan(group.span.file, start,
            SourcePosition(start.line, start.column + cast(int) countCodePoints(value)));
    return true;
}

private SourceSpan nameSpan(SourceSpan whole, string prefix, string name)
{
    const start = SourcePosition(whole.start.line,
            whole.start.column + cast(int) countCodePoints(prefix));
    return SourceSpan(whole.file, start,
            SourcePosition(start.line, start.column + cast(int) countCodePoints(name)));
}

private SourceSpan nameOnlySpan(SourceSpan whole, string name)
{
    const start = SourcePosition(whole.start.line,
            whole.start.column + 1);
    return SourceSpan(whole.file, start,
            SourcePosition(start.line, start.column + cast(int) countCodePoints(name)));
}

private SourceSpan nodeSpan(SourceSpan header, Block* suite)
{
    if (suite is null || suite.span.end <= header.end)
        return header;
    return SourceSpan(header.file, header.start, suite.span.end);
}

private void addSuiteFold(ref FeatureIndex result, SourceSpan header, Block* suite,
        string kind)
{
    if (suite is null || suite.span.end.line <= header.start.line)
        return;
    result.folds ~= FeatureFold(SourceSpan(header.file, header.start, suite.span.end), kind);
}
