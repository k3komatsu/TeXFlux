/**
 * The two trees a document passes through.
 *
 * Parsing produces a syntax tree, which still holds everything the author
 * wrote as they wrote it: suite markers, `>>` compositions, sequence entries,
 * and invocations whose values have not yet been decided. Normalization
 * consumes those and produces a canonical tree, which holds only what the
 * renderer needs: raw TeX, invocations with their arguments, and literal brace
 * groups. Nothing syntax-only may survive into the second tree, and the
 * renderer refuses one that does.
 *
 * Both trees are made of the same `Node`, because raw TeX belongs to both and
 * because a pass that rewrites a tree should not have to convert the parts it
 * does not touch. What separates them is which members may appear, which
 * `texflux.canonical` checks once at the boundary.
 */
module texflux.ast;

import std.algorithm : canFind;
import std.sumtype : has, match, SumType;
import std.typecons : Nullable, nullable;

import texflux.errors : CompilerDefect;
import texflux.source : plainText, SourcePosition, SourceSpan, SourceText;

/// A TeX group, identified by the delimiter pair that encloses it.
enum GroupKind : string
{
    required = "required",
    optional = "optional",
    overlay = "overlay",
    binding = "binding",
}

/// Where an argument's braces sit relative to its content.
enum ArgumentLayout : string
{
    /// Written on the header line, inside delimiters the author typed.
    inline = "inline",
    /// A generated group around a value written as an indented block.
    block = "block",
    /// A group the author wrote whole, delimiters included.
    explicit = "explicit",
}

/// What a structural header's prefix and shape say it is.
enum InvocationKind : string
{
    command = "command",
    environment = "environment",
    brace = "brace",
    transparent = "transparent",
}

/// Which suite marker introduced a value.
enum SuiteMode : string
{
    /// `:::`, one value per marked entry.
    sequence = "sequence",
    /// `::`, one value holding the whole indented block.
    block = "block",
}

/// The character a group of this kind opens with.
dchar opener(GroupKind kind) @safe pure nothrow @nogc
{
    final switch (kind)
    {
    case GroupKind.required:
        return '{';
    case GroupKind.optional:
        return '[';
    case GroupKind.overlay:
        return '<';
    case GroupKind.binding:
        return '(';
    }
}

/// The character a group of this kind closes with.
dchar closer(GroupKind kind) @safe pure nothrow @nogc
{
    final switch (kind)
    {
    case GroupKind.required:
        return '}';
    case GroupKind.optional:
        return ']';
    case GroupKind.overlay:
        return '>';
    case GroupKind.binding:
        return ')';
    }
}

/**
 * Every character that can open an inline group, in the order a header scans.
 *
 * The binding list's `(` is deliberately absent: it is a trailing list on a
 * special's header rather than a general inline group, so the group loop must
 * not scan it and `(` opens nothing anywhere else.
 */
enum inlineOpeners = "{[<"d;

/// The opener of the trailing binding list a special's header may carry.
enum bindingOpener = '(';

/// The group kind a character opens, if it opens one at all.
Nullable!GroupKind kindOfOpener(dchar c) @safe pure nothrow
{
    switch (c)
    {
    case '{':
        return GroupKind.required.nullable;
    case '[':
        return GroupKind.optional.nullable;
    case '<':
        return GroupKind.overlay.nullable;
    case '(':
        return GroupKind.binding.nullable;
    default:
        return Nullable!GroupKind.init;
    }
}

///
@safe pure unittest
{
    assert(GroupKind.required.opener == '{' && GroupKind.required.closer == '}');
    assert(kindOfOpener('<').get == GroupKind.overlay);
    assert(kindOfOpener('x').isNull);
    assert(inlineOpeners.canFind('{') && !inlineOpeners.canFind('('),
            "a binding list is not an inline group");
}

/// A run of nodes written at one indentation.
struct Block
{
    Node[] nodes;
    SourceSpan span;
}

/// One value of an invocation, however the author wrote it.
struct Argument
{
    GroupKind kind;
    ArgumentValue value;
    ArgumentLayout layout;
    SourceSpan span;
    /// Where each run of an interpolated inline value came from.
    Nullable!SourceText parts;
}

/// An argument holds either the text of an inline group or an indented block.
alias ArgumentValue = SumType!(string, Block*);

/// A line of TeX that TeXFlux passes through without reading.
struct RawTex
{
    string text;
    SourceSpan span;
    /// Where each run came from, once interpolation has assembled the text.
    Nullable!SourceText parts;
    /// A raw-region line, which macro expansion must not interpolate.
    bool verbatim;
}

/// A structural header written with a backslash or an at sign.
struct ParsedInvocation
{
    InvocationKind kind;
    string name;
    Argument[] groups;
    Block* suite;
    SourceSpan span;
    Nullable!SuiteMode suiteMode;
    Nullable!SourceSpan suiteSpan;
}

/// A structural header written with an exclamation mark.
struct SpecialInvocation
{
    string name;
    Argument[] groups;
    Block* suite;
    SourceSpan span;
    Nullable!SuiteMode suiteMode;
    Nullable!SourceSpan suiteSpan;
}

/**
 * One marked value in a sequence suite.
 *
 * An entry keeps no record of the shape it was written in. Where the author
 * broke the line decides nothing about the output, so there is nothing here
 * for a renderer to read it from.
 */
struct SequenceEntry
{
    Block* value;
    SourceSpan markerSpan;
    SourceSpan span;
    /// The authored group's kind for a `+` entry; absent means a `-` entry.
    Nullable!GroupKind argumentKind;
}

/// A `>>` composition, which desugaring turns into nesting.
struct Stack
{
    Node[] segments;
    Block* suite;
    SourceSpan span;
    Nullable!SuiteMode suiteMode;
    Nullable!SourceSpan suiteSpan;
}

/**
 * A command or a named environment, with its values decided.
 *
 * A body distinguishes the two: absent is a command, present is an
 * environment whose body the renderer wraps in begin and end.
 */
struct GenericInvocation
{
    string name;
    Argument[] arguments;
    Block* body_;
    SourceSpan span;
}

/// A literal TeX brace group, which always renders its own braces.
struct BraceGroup
{
    Block* body_;
    SourceSpan span;
    string headerRaw;
    Nullable!SourceText headerParts;
}

/**
 * Any node of either tree.
 *
 * The first five are syntax-only and the last three are canonical, except
 * `RawTex`, which belongs to both and is the only node normalization passes
 * through untouched.
 */
alias Node = SumType!(
    RawTex,
    ParsedInvocation,
    SpecialInvocation,
    SequenceEntry,
    Stack,
    GenericInvocation,
    BraceGroup,
);

/// A whole document, which is one block and the span of the file it came from.
struct Document
{
    Block* body_;
    SourceSpan span;
}

/// Where a node was written.
SourceSpan spanOf(Node node) @safe pure
{
    return node.match!(n => n.span);
}

/// Whether a node is one of the three the renderer accepts.
bool isCanonical(Node node) @safe pure
{
    return node.has!RawTex || node.has!GenericInvocation || node.has!BraceGroup;
}

///
@safe pure unittest
{
    const span = SourceSpan("a.tfx", SourcePosition(3, 1), SourcePosition(3, 13));
    auto raw = Node(RawTex("\\vspace{1em}", span));
    assert(raw.spanOf == span);
    assert(raw.isCanonical);
    assert(!Node(Stack(null, null, span)).isCanonical);
}

/**
 * Reject provenance fragments that do not spell the text they belong to.
 *
 * The renderer emits the fragments rather than the text whenever a field has
 * them, so a field whose two spellings disagree would silently render as
 * something the author did not write. Interpolation is the only thing that
 * builds them, and this is what holds it to its own result.
 */
void checkParts(const Nullable!SourceText parts, string text, string owner) @safe pure
{
    if (!parts.isNull && plainText(parts.get) != text)
        throw new CompilerDefect(owner ~ " parts must match its text");
}

/// Raw TeX whose provenance, if it has any, spells its text.
RawTex rawTex(string text, SourceSpan span,
        Nullable!SourceText parts = Nullable!SourceText.init, bool verbatim = false) @safe pure
{
    checkParts(parts, text, "RawTex");
    return RawTex(text, span, parts, verbatim);
}

/// An inline argument whose provenance, if it has any, spells its text.
Argument inlineArgument(GroupKind kind, string text, SourceSpan span,
        Nullable!SourceText parts = Nullable!SourceText.init) @safe pure
{
    checkParts(parts, text, "Argument");
    return Argument(kind, ArgumentValue(text), ArgumentLayout.inline, span, parts);
}

/// A literal brace group whose header provenance, if any, spells its header.
BraceGroup braceGroup(Block* body_, SourceSpan span, string headerRaw = "",
        Nullable!SourceText headerParts = Nullable!SourceText.init) @safe pure
{
    checkParts(headerParts, headerRaw, "BraceGroup header");
    return BraceGroup(body_, span, headerRaw, headerParts);
}

///
@safe pure unittest
{
    import std.exception : assertThrown;
    import texflux.source : SourcePosition, TextFragment;

    const span = SourceSpan("a.tfx", SourcePosition(1, 1), SourcePosition(1, 4));
    auto good = nullable([TextFragment("ab", span), TextFragment("c", span)]);
    assert(rawTex("abc", span, good).text == "abc");
    assertThrown!CompilerDefect(rawTex("abcd", span, good));
}

/**
 * One segment, given the suite its own header asked for.
 *
 * Both the parser and `>>` desugaring attach a suite to a segment they have
 * already scanned, so the rule that only the two invocation nodes can carry
 * one is written here rather than in each of them.
 */
Node withSuite(Node segment, Block* suite, Nullable!SuiteMode mode,
        Nullable!SourceSpan suiteSpan) @safe
{
    return segment.match!(
        (ParsedInvocation invocation) {
            invocation.suite = suite;
            invocation.suiteMode = mode;
            invocation.suiteSpan = suiteSpan;
            return Node(invocation);
        },
        (SpecialInvocation invocation) {
            invocation.suite = suite;
            invocation.suiteMode = mode;
            invocation.suiteSpan = suiteSpan;
            return Node(invocation);
        },
        (other) => Node(other),
    );
}

/// The same node, written at a different place.
Node withSpan(Node node, SourceSpan span) @safe
{
    return node.match!((n) { n.span = span; return Node(n); });
}

/// The suite a node carries, or null when it carries none.
Block* suiteOf(Node node) @safe
{
    return node.match!(
        (ParsedInvocation invocation) => invocation.suite,
        (SpecialInvocation invocation) => invocation.suite,
        (Stack composition) => composition.suite,
        _ => null,
    );
}

/// Where a node's source ends, its suite included.
SourcePosition endOf(Node node) @safe
{
    const end = node.spanOf.end;
    auto suite = node.suiteOf;
    return suite !is null && suite.span.end > end ? suite.span.end : end;
}

///
unittest
{
    import texflux.source : SourcePosition;

    const span = SourceSpan("a.tfx", SourcePosition(1, 1), SourcePosition(1, 5));
    auto suite = new Block(null, SourceSpan(span.file, span.start, SourcePosition(3, 1)));
    auto segment = Node(ParsedInvocation(InvocationKind.command, "cmd", null, null, span));
    auto attached = segment.withSuite(suite, SuiteMode.block.nullable,
            Nullable!SourceSpan.init);
    assert(attached.suiteOf is suite);
    assert(segment.endOf == span.end && attached.endOf == SourcePosition(3, 1),
            "a suite extends where its node ends");
    assert(Node(RawTex("x", span)).withSuite(suite, Nullable!SuiteMode.init,
            Nullable!SourceSpan.init).suiteOf is null,
            "only an invocation can carry a suite");
}
