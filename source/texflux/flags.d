/**
 * Build flags: one document, more than one build.
 *
 * A flag is declared with a default and can be overridden when the document is
 * compiled; `!when` and `!unless` keep or drop one payload according to it.
 * That is the whole of the conditional language. There is nothing to compare,
 * nothing to compute, and no value a flag can hold but on and off, because a
 * preprocessor that grows a second language is a preprocessor whose output
 * nobody can predict from its input.
 *
 * Several flags fold with a leading `[and]` or `[or]`, and that fold is flat:
 * no parentheses, no fold inside a fold, and no negating one operand. Anything
 * deeper is written by composing whole conditionals with `>>`, which is not
 * restricted.
 */
module texflux.flags;

import std.algorithm : all, any, canFind, sort;
import std.array : join;
import std.ascii : isAlpha, isAlphaNum;
import std.utf : byCodeUnit;
import std.typecons : Nullable, nullable, Tuple, tuple;

import texflux.ast;
import texflux.errors : FlagError, RelatedLocation, ValidationError;
import texflux.ordered : OrderedMap;
import texflux.source : SourceSpan;
import texflux.syntax : asSpecial, demandText, optionalText, specials, stacks;
import texflux.text : stripWhitespace;

/// The special names that belong to the flag system itself.
enum Conditional : string
{
    declare = "flag",
    when = "when",
    unless = "unless",
}

/// How a conditional folds the several flags it names into one answer.
enum Combinator : string
{
    all = "and",
    any = "or",
}

/// Reserved alongside the macro constructs, so no macro may be named after one.
enum conditionalNames = [
    cast(string) Conditional.declare, cast(string) Conditional.when,
    cast(string) Conditional.unless,
];

/**
 * The shape of a flag name.
 *
 * A flag is written in a group and on a command line, so it stays identifier
 * shaped with the hyphen a command line tends to want. Import bindings name
 * flags too, which is why this is published rather than spelled twice.
 */
bool isFlagName(string name) @safe pure nothrow @nogc
{
    return name.length != 0 && name[0].isAlpha
        && name[1 .. $].byCodeUnit.all!(c => c.isAlphaNum || c == '_' || c == '-');
}

///
@safe pure unittest
{
    assert(isFlagName("draft") && isFlagName("no-notes") && isFlagName("v2_final"));
    assert(!isFlagName("") && !isFlagName("2nd") && !isFlagName("-lead"));
}

/// The only two spellings a declaration or an override accepts.
Nullable!bool flagValue(string spelling) @safe pure nothrow
{
    if (spelling == "on")
        return true.nullable;
    if (spelling == "off")
        return false.nullable;
    return Nullable!bool.init;
}

/**
 * Resolved flags: every declared name, mapped to the value this build uses.
 *
 * Ordered, because a build's overrides are checked in the order they were
 * written and the first unknown one is the one reported.
 */
alias Flags = OrderedMap!bool;

/// Name the flags a document declares, for an unknown-flag diagnostic.
string declaredFlagsHint(Flags flags)
{
    if (flags.length == 0)
        return "this document declares no flags";
    return "declared flags are: " ~ flags.keys.dup.sort.join(", ");
}

///
unittest
{
    assert(declaredFlagsHint(Flags.init) == "this document declares no flags");
    assert(declaredFlagsHint(Flags(["zebra": true, "apple": false]))
            == "declared flags are: apple, zebra", "named in one order, always");
}

/**
 * Split a modifier off the front of a conditional's header.
 *
 * Only the leading group can be the modifier. A misplaced optional group is
 * left in the flag list, where reading it as a name reports it at its own span
 * rather than blaming the header as a whole.
 */
private Tuple!(Nullable!Combinator, Argument[]) combinator(SpecialInvocation node)
{
    if (node.groups.length == 0)
        return tuple(Nullable!Combinator.init, Argument[].init);
    const text = optionalText(node.groups[0]);
    if (text.isNull)
        return tuple(Nullable!Combinator.init, node.groups);

    const modifier = text.get.stripWhitespace;
    if (modifier == cast(string) Combinator.all)
        return tuple(Combinator.all.nullable, node.groups[1 .. $]);
    if (modifier == cast(string) Combinator.any)
        return tuple(Combinator.any.nullable, node.groups[1 .. $]);
    throw new ValidationError("V001", "!" ~ node.name ~ " modifier must be '[and]'"
            ~ " or '[or]', got '[" ~ modifier ~ "]'", node.groups[0].span);
}

/// The declared flag names one conditional header combines.
private string[] flagNames(SpecialInvocation node, Argument[] groups, Flags flags)
{
    string[] names;
    foreach (group; groups)
    {
        const name = group.demandText("!" ~ node.name ~ " flag");
        if (name !in flags)
            throw new ValidationError("V002", "unknown build flag '" ~ name ~ "'; "
                    ~ declaredFlagsHint(flags), group.span);
        if (names.canFind(name))
            throw new ValidationError("V003",
                    "build flag '" ~ name ~ "' is listed twice", group.span);
        names ~= name;
    }
    return names;
}

/**
 * Whether one conditional header keeps its payload.
 *
 * `!unless` is the negation of the whole `!when` it mirrors, so
 * `!unless[or]{a}{b}` keeps its payload only while neither flag is on.
 */
bool evaluateConditional(SpecialInvocation node, Flags flags)
{
    import std.conv : to;

    auto split = combinator(node);
    auto modifier = split[0];
    auto groups = split[1];
    if (groups.length == 0)
        throw new ValidationError("V004",
                "!" ~ node.name ~ " requires at least one '{flag}' group", node.span);

    const names = flagNames(node, groups, flags);
    if (modifier.isNull && names.length > 1)
        throw new ValidationError("V005", "!" ~ node.name ~ " requires an '[and]'"
                ~ " or '[or]' modifier to combine " ~ names.length.to!string ~ " flags",
                node.span);

    const matched = !modifier.isNull && modifier.get == Combinator.any
        ? names.any!(name => flags[name]) : names.all!(name => flags[name]);
    return node.name == cast(string) Conditional.when ? matched : !matched;
}

/**
 * Reject a declaration written as a `>>` segment.
 *
 * Desugaring would nest it under the segments to its left, where it is no
 * longer the top-level statement a declaration has to be, so this runs while
 * the composition is still visible.
 */
void validateFlagForms(Document document)
{
    foreach (composition; document.body_.stacks)
        foreach (segment; composition.segments)
        {
            auto special = segment.asSpecial;
            if (!special.isNull && special.get.name == cast(string) Conditional.declare)
                throw new ValidationError("V006", "!flag must be a top-level"
                        ~ " declaration and cannot be a '>>' segment", special.get.span);
        }
}

/// Read one declaration and the default it gives.
private Tuple!(string, bool) declaration(SpecialInvocation node, SourceSpan[string] declared)
{
    if (node.suite !is null)
        throw new ValidationError("V007", "!flag does not accept a suite", node.span);
    if (node.groups.length != 2)
        throw new ValidationError("V008",
                "!flag requires a name group and an 'on' or 'off' default group", node.span);

    const name = node.groups[0].demandText("!flag name");
    if (!isFlagName(name))
        throw new ValidationError("V009",
                "invalid build flag name '" ~ name ~ "'", node.groups[0].span);
    if (auto first = name in declared)
        throw new ValidationError("V010", "build flag '" ~ name ~ "' is already declared at "
                ~ first.location, node.groups[0].span,
                [RelatedLocation("first declared here", *first)]);

    const spelling = node.groups[1].demandText("!flag default");
    auto value = flagValue(spelling);
    if (value.isNull)
        throw new ValidationError("V011", "!flag default must be 'on' or 'off', got '"
                ~ spelling ~ "'", node.groups[1].span);
    return tuple(name, value.get);
}

/// A document with its declarations removed, and the values this build uses.
alias CollectedFlags = Tuple!(Document, "document", Flags, "flags");

/**
 * Strip the top-level declarations and resolve this build's values.
 *
 * Every declaration is collected before any override is applied and before any
 * conditional is read, so a conditional may precede the declaration it names
 * and an override may name a flag declared anywhere in the document.
 */
CollectedFlags collectFlags(Document document, Flags overrides = Flags.init)
{
    SourceSpan[string] declared;
    Flags flags;
    Node[] nodes;
    foreach (node; document.body_.nodes)
    {
        auto found = node.asSpecial;
        if (found.isNull || found.get.name != cast(string) Conditional.declare)
        {
            nodes ~= node;
            continue;
        }
        auto read = declaration(found.get, declared);
        declared[read[0]] = found.get.span;
        flags[read[0]] = read[1];
    }

    auto body_ = new Block(nodes, document.body_.span);
    // Every top-level declaration is gone, so anything left is nested.
    foreach (special; body_.specials)
        if (special.name == cast(string) Conditional.declare)
            throw new ValidationError("V012", "!flag is only valid at the top level",
                    special.span);

    foreach (name, value; overrides)
    {
        if (name !in flags)
            throw new FlagError("unknown build flag '" ~ name ~ "'; "
                    ~ declaredFlagsHint(flags));
        flags[name] = value;
    }
    return CollectedFlags(Document(body_, document.span), flags);
}

/// Overrides are checked in the order they were given, so the first unknown one is named.
unittest
{
    import std.algorithm : startsWith;
    import std.exception : collectExceptionMsg;
    import texflux.parser : parse;

    Flags overrides;
    overrides["zulu"] = true;
    overrides["alpha"] = false;
    assert(collectExceptionMsg!FlagError(collectFlags(parse("x\n"), overrides))
            .startsWith("unknown build flag 'zulu'"));
}
