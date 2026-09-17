/**
 * Rules about the shape of a syntax tree, shared by the passes that read one.
 *
 * These sit below macro expansion and normalization so neither has to restate
 * a rule the other already owns. The predicates report failure by returning
 * nothing, so each caller can raise the diagnostic that fits its own stage;
 * the two that do raise are the ones whose message would otherwise be written
 * out at a dozen call sites.
 */
module texflux.syntax;

import std.algorithm : filter, map;
import std.array : array;
import std.range : isInputRange, retro;
import std.sumtype : get, has, match;
import std.traits : isSomeString;
import std.typecons : Nullable, nullable;

import texflux.ast;
import texflux.errors : CompilerDefect, ValidationError;
import texflux.source : SourceSpan;
import texflux.text : stripWhitespace;

/**
 * The two whole-line markers that delimit a raw region.
 *
 * The physical-line layer matches them as complete lines before any header
 * scanning, which makes them the only two names below the rule that a prefix
 * alone classifies a line.
 */
enum rawBeginMarker = "!BEGIN_RAW_MODE";

/// ditto
enum rawEndMarker = "!END_RAW_MODE";

/// The same two, spelled as special names, for checks that see a scanned name.
enum rawModeNames = [rawBeginMarker[1 .. $], rawEndMarker[1 .. $]];

/**
 * The one-line raw escape.
 *
 * `@@` and `!!` strip a single prefix character, so they only ever reach a
 * line that already starts with one of those. This one strips the whole
 * marker, which makes it the only escape that can reach a backslash line the
 * header scanner would otherwise claim.
 */
enum rawLineMarker = "!|";

/// Whether the character at an index is escaped by an odd run of backslashes.
bool isEscaped(S)(S text, size_t index) if (isSomeString!S)
{
    size_t backslashes = 0;
    while (index > backslashes && text[index - backslashes - 1] == '\\')
        ++backslashes;
    return backslashes % 2 == 1;
}

///
@safe pure unittest
{
    assert(!isEscaped("a%b", 1));
    assert(isEscaped("a\\%b", 2));
    assert(!isEscaped("a\\\\%b", 3), "an even run escapes itself, not what follows");
    assert(isEscaped("\\\\\\%", 3));
    assert(!isEscaped("%", 0));
}

/// Whether a node is a blank source line rather than content.
bool blank(Node node) @safe pure
{
    return node.match!((RawTex raw) => raw.text.length == 0, _ => false);
}

private Nullable!string inlineText(Argument argument, GroupKind kind) @safe pure
{
    if (argument.kind != kind || argument.layout != ArgumentLayout.inline)
        return Nullable!string.init;
    return argument.value.match!(
        (string text) => text.nullable,
        (Block* _) => Nullable!string.init,
    );
}

/// The raw text of one required group, or nothing if the argument is not one.
Nullable!string requiredText(Argument argument) @safe pure
{
    return inlineText(argument, GroupKind.required);
}

/// The raw text of one optional group, or nothing if the argument is not one.
Nullable!string optionalText(Argument argument) @safe pure
{
    return inlineText(argument, GroupKind.optional);
}

/**
 * Read one required inline group, or reject the argument at its own span.
 *
 * Every construct that names something -- a macro, a parameter, a build flag,
 * a module path -- spells that name as one required group, so they share both
 * the reading and the diagnostic. The name is stripped of whitespace, which is
 * the one place a strip here is not limited to spaces.
 */
string demandText(Argument argument, string label) @safe pure
{
    const text = requiredText(argument);
    if (text.isNull)
        throw new ValidationError("V042",
                label ~ " must be a required '{...}' group", argument.span);
    return text.get.stripWhitespace;
}

/**
 * Every syntax node under a block, depth first, evaluated as it is read.
 *
 * Laziness is not an optimization here. Several checks want the first node
 * that breaks a rule and nothing after it, and a diagnostic that reports the
 * first offender has to stop at the one a reader would call first.
 *
 * A stack's segments are yielded but not descended into, which is what lets a
 * check ask whether a construct was written as a `>>` segment without also
 * seeing everything that segment holds.
 */
auto walk(Block* block) @safe
{
    static struct Walk
    {
        private static struct Pending
        {
            Node[] nodes;
            bool descend;
        }

        private Pending[] stack;

        private void settle() @safe pure nothrow
        {
            while (stack.length != 0 && stack[$ - 1].nodes.length == 0)
                stack = stack[0 .. $ - 1];
        }

        this(Block* block) @safe pure nothrow
        {
            if (block !is null)
                stack = [Pending(block.nodes, true)];
            settle();
        }

        bool empty() const @safe pure nothrow
        {
            return stack.length == 0;
        }

        Node front() @safe pure nothrow
        {
            return stack[$ - 1].nodes[0];
        }

        void popFront() @safe
        {
            auto node = stack[$ - 1].nodes[0];
            const descend = stack[$ - 1].descend;
            stack[$ - 1].nodes = stack[$ - 1].nodes[1 .. $];
            if (descend)
                foreach (pending; childrenOf(node).retro)
                    stack ~= pending;
            settle();
        }

        private static Pending[] childrenOf(Node node) @safe
        {
            Pending[] children;

            void addBlocks(Argument[] groups, Block* suite) @safe
            {
                foreach (group; groups)
                    group.value.match!(
                        (Block* nested) { children ~= Pending(nested.nodes, true); },
                        (string _) {},
                    );
                if (suite !is null)
                    children ~= Pending(suite.nodes, true);
            }

            node.match!(
                (ParsedInvocation invocation) => addBlocks(invocation.groups, invocation.suite),
                (SpecialInvocation invocation) => addBlocks(invocation.groups, invocation.suite),
                (Stack composition) {
                    children ~= Pending(composition.segments, false);
                    if (composition.suite !is null)
                        children ~= Pending(composition.suite.nodes, true);
                },
                (SequenceEntry entry) { children ~= Pending(entry.value.nodes, true); },
                (_) {},
            );
            return children;
        }
    }

    static assert(isInputRange!Walk);
    return Walk(block);
}

/// Every `>>` composition under a block, before desugaring removes them.
auto stacks(Block* block) @safe
{
    return block.walk.filter!(node => node.has!Stack).map!(node => node.get!Stack);
}

/// Every special under a block, in the order `walk` reads them.
auto specials(Block* block) @safe
{
    return block.walk.filter!(node => node.has!SpecialInvocation)
        .map!(node => node.get!SpecialInvocation);
}

/// The special a node is, or nothing when it is another kind of node.
Nullable!SpecialInvocation asSpecial(Node node) @safe
{
    return node.has!SpecialInvocation ? node.get!SpecialInvocation.nullable
        : Nullable!SpecialInvocation.init;
}

/// Every group with the rewrite applied to its block value; inline text is kept.
Argument[] mapBlocks(Argument[] groups, scope Block* delegate(Block*) rewrite)
{
    // A rewritten group is built rather than assigned into, because nothing
    // here needs to reuse the old one.
    return groups.map!((Argument group) {
        auto value = group.value.match!(
            (Block* nested) => ArgumentValue(rewrite(nested)),
            (string text) => ArgumentValue(text),
        );
        return Argument(group.kind, value, group.layout, group.span, group.parts);
    }).array;
}

/**
 * Rebuild a node with a rewrite applied to every block it holds.
 *
 * The writing counterpart of `walk`: a pass that rewrites a tree keeps its own
 * rule for the nodes it cares about and hands every other node here, so the
 * shape of the syntax tree is spelled out once. A node holding no block comes
 * back as it was. Every rewriting pass runs after `>>` desugaring, so a stack
 * is refused rather than given a meaning here.
 *
 * This is not `@safe`, and cannot be while it builds arrays of nodes: the
 * standard library's sum type declares its own assignment `@system`, even for
 * a sum of two integers, so every struct holding one inherits that. The
 * modules that hold no node -- the text rules, the JSON writer, the spans --
 * are `@safe` throughout.
 */
Node mapChildren(Node node, scope Block* delegate(Block*) rewrite)
{
    Node rebuilt(T)(T invocation)
    {
        invocation.groups = mapBlocks(invocation.groups, rewrite);
        invocation.suite = invocation.suite is null ? null : rewrite(invocation.suite);
        return Node(invocation);
    }

    return node.match!(
        (ParsedInvocation invocation) => rebuilt(invocation),
        (SpecialInvocation invocation) => rebuilt(invocation),
        (Stack _) {
            throw new CompilerDefect("mapChildren requires a desugared syntax AST");
            return Node.init;
        },
        (SequenceEntry entry) {
            entry.value = rewrite(entry.value);
            return Node(entry);
        },
        (other) => Node(other),
    );
}

/**
 * The marked value entries of a sequence suite, ignoring blank separators.
 *
 * A blank line inside a sequence suite separates entries rather than making
 * one, which is why the blanks are dropped here rather than reported.
 */
SequenceEntry[] sequenceEntries(Block* suite) @safe
{
    SequenceEntry[] entries;
    foreach (child; suite.nodes)
    {
        if (child.blank)
            continue;
        if (!child.has!SequenceEntry)
            throw new ValidationError("V043",
                    "sequence suites require '-' or '+' value entries", child.spanOf);
        entries ~= child.get!SequenceEntry;
    }
    return entries;
}

/// Walking reaches a node before what it holds, and holds nothing back.
@safe unittest
{
    import std.algorithm : equal, map;
    import texflux.source : SourcePosition;

    const span = SourceSpan("t.tfx", SourcePosition(1, 1), SourcePosition(1, 2));
    Node raw(string text)
    {
        return Node(RawTex(text, span));
    }

    auto groupBody = new Block([raw("in group")], span);
    auto suite = new Block([raw("in suite")], span);
    auto invocation = ParsedInvocation(InvocationKind.command, "cmd",
            [Argument(GroupKind.required, ArgumentValue(groupBody), ArgumentLayout.block, span)],
            suite, span);
    auto document = new Block([Node(invocation), raw("after")], span);

    assert(document.walk.map!(node => node.match!(
            (RawTex text) => text.text,
            (ParsedInvocation call) => "\\" ~ call.name,
            _ => "?",
    )).equal(["\\cmd", "in group", "in suite", "after"]));
}

/// A stack's segments are yielded, but nothing they hold is.
@safe unittest
{
    import std.algorithm : equal, map;
    import texflux.source : SourcePosition;

    const span = SourceSpan("t.tfx", SourcePosition(1, 1), SourcePosition(1, 2));
    auto hidden = new Block([Node(RawTex("hidden", span))], span);
    auto segment = Node(ParsedInvocation(InvocationKind.command, "seg",
            [Argument(GroupKind.required, ArgumentValue(hidden), ArgumentLayout.block, span)],
            null, span));
    auto suite = new Block([Node(RawTex("visible", span))], span);
    auto document = new Block([Node(Stack([segment], suite, span))], span);

    assert(document.walk.map!(node => node.match!(
            (Stack _) => "stack",
            (ParsedInvocation call) => "\\" ~ call.name,
            (RawTex text) => text.text,
            _ => "?",
    )).equal(["stack", "\\seg", "visible"]),
            "a segment's own groups stay out of the walk, which is what lets a"
            ~ " check ask where a construct was written without seeing its body");
}

/// A sequence suite's blank separators are not entries.
@safe unittest
{
    import texflux.source : SourcePosition;

    const span = SourceSpan("t.tfx", SourcePosition(1, 1), SourcePosition(1, 2));
    auto value = new Block([Node(RawTex("one", span))], span);
    auto suite = new Block([
        Node(RawTex("", span)),
        Node(SequenceEntry(value, span, span)),
        Node(RawTex("", span)),
    ], span);
    assert(suite.sequenceEntries.length == 1);

    auto bad = new Block([Node(RawTex("stray", span))], span);
    try
    {
        bad.sequenceEntries;
        assert(false, "a non-entry in a sequence suite is rejected");
    }
    catch (ValidationError error)
        assert(error.code == "V043");
}
