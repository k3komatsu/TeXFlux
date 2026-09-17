/**
 * Resolving `>>` compositions into ordinary nesting.
 *
 * `\a >> \b >> \c` is written left to right and means what `\a` wrapping `\b`
 * wrapping `\c` means, so this pass folds it from the right: the line's suite
 * belongs to the last segment, and every segment to its left receives exactly
 * one synthetic block value holding what follows it.
 *
 * It is pure sugar. Nothing downstream knows a composition existed, and macro
 * expansion runs afterwards, so by the time a macro call is bound its payload
 * is already an ordinary block value.
 */
module texflux.desugar;

import std.algorithm : map;
import std.array : array;
import std.range : retro;
import std.sumtype : match;
import std.typecons : nullable;

import texflux.ast;
import texflux.errors : Descent, ValidationError;
import texflux.source : SourceSpan;
import texflux.syntax : mapChildren;

/// Resolve every composition in one document, depth first.
Document desugar(Document document)
{
    Desugarer desugarer;
    return Document(desugarer.block(document.body_), document.span);
}

private struct Desugarer
{
    private size_t depth;

    Block* block(Block* source)
    {
        auto descent = Descent(depth);
        return new Block(source.nodes.map!(node => mapChildren(resolve(node), &block)).array,
                source.span);
    }
}

/// One composition, or the node unchanged when it is not one.
private Node resolve(Node node)
{
    return node.match!(
        (Stack composition) => fold(composition),
        (n) => Node(n),
    );
}

private Node fold(Stack composition)
{
    if (composition.segments.length < 2)
        throw new ValidationError("V034", "stack requires at least two segments",
                composition.span);

    // The line's own suffix belongs to its rightmost segment. Every segment to
    // the left of that one receives a synthetic block value and nothing else.
    auto tail = composition.segments[$ - 1].withSuite(composition.suite,
            composition.suiteMode, composition.suiteSpan);

    foreach (segment; composition.segments[0 .. $ - 1].retro)
    {
        auto suiteSpan = SourceSpan(segment.spanOf.file, segment.spanOf.start, tail.endOf);
        tail = segment.withSuite(new Block([tail], suiteSpan),
                SuiteMode.block.nullable, suiteSpan.nullable);
    }
    return tail;
}

/// A composition too deep to follow is refused rather than overflowing the stack.
unittest
{
    import std.array : join;
    import std.exception : assertThrown;
    import std.range : repeat;
    import texflux.errors : NestingError;
    import texflux.parser : parse;

    assertThrown!NestingError(desugar(parse("\\a".repeat(500).join(" >> ") ~ "\n")));
}
