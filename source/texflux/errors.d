/**
 * The errors a document can cause, and the ones a document cannot.
 *
 * Every diagnostic carries a stable code, so an editor can recognize one
 * across releases without matching its message, and zero or more related
 * locations. A related location is the second place a diagnostic is about --
 * the first declaration a duplicate collides with, the import that pulled a
 * broken module in -- which the message already names in prose and which a
 * language server needs as structured data.
 *
 * A code identifies the place a diagnostic is raised rather than its wording.
 * It is one letter for the kind and three digits, written as a literal first
 * argument at every construction site so that the shared test which checks
 * every code against the published table can find them all. Codes are never
 * renumbered and never reused.
 */
module texflux.errors;

import std.exception : basicExceptionCtors;
import std.format : format;

import texflux.source : SourcePosition, SourceSpan;

/// A second place a diagnostic points at, beside its own span.
struct RelatedLocation
{
    string message;
    SourceSpan span;
}

/// The one line every diagnostic prints.
string diagnosticLine(SourceSpan span, string label, string message, string code) @safe pure
{
    return format("%s: %s: %s [%s]", span.location, label, message, code);
}

///
@safe pure unittest
{
    const span = SourceSpan("slides.tfx", SourcePosition(2, 9), SourcePosition(2, 10));
    assert(diagnosticLine(span, "parse error", "unclosed required group", "P004")
            == "slides.tfx:2:9: parse error: unclosed required group [P004]");
}

/// A diagnostic about a place in a document.
abstract class TeXFluxError : Exception
{
    /// The category this diagnostic reports, which each subclass names.
    abstract string kind() const @safe pure nothrow;

    const string message;
    const SourceSpan span;
    const string code;
    const RelatedLocation[] related;

    this(string code, string message, SourceSpan span, RelatedLocation[] related = null) @safe pure
    {
        super(message);
        this.code = code;
        this.message = message;
        this.span = span;
        this.related = related;
    }

    /// The label this diagnostic prints between its location and its message.
    final string errorKind() const @safe pure
    {
        return kind ~ " error";
    }

    /// This diagnostic as the one line the command line prints.
    final string diagnostic() const @safe pure
    {
        return diagnosticLine(span, errorKind, message, code);
    }

    override string toString() const @safe pure
    {
        return diagnostic;
    }

    /**
     * This error with a longer message and more related locations.
     *
     * The code and the span stay the callee's, so an import chain can be
     * appended without losing either what went wrong or where.
     */
    final TeXFluxError chained(string message, RelatedLocation[] extra...) @safe pure
    {
        return rebuild(message, (related ~ extra).dup);
    }

    protected abstract TeXFluxError rebuild(string message, RelatedLocation[] related) @safe pure;
}

/**
 * The parts every diagnostic class repeats.
 *
 * `rebuild` exists so that `chained` can produce the same class it was called
 * on without every caller knowing which one that is.
 */
private mixin template DiagnosticClass(string kindName)
{
    this(string code, string message, SourceSpan span, RelatedLocation[] related = null) @safe pure
    {
        super(code, message, span, related);
    }

    override string kind() const @safe pure nothrow
    {
        return kindName;
    }

    protected override TeXFluxError rebuild(string message, RelatedLocation[] related) @safe pure
    {
        return new typeof(this)(code, message, span, related);
    }
}

/// Malformed physical structure, headers, groups, suffixes, or indentation.
final class ParseError : TeXFluxError
{
    mixin DiagnosticClass!"parse";
}

/// Invalid value consumption, or a broken container or macro contract.
final class ValidationError : TeXFluxError
{
    mixin DiagnosticClass!"validation";
}

/// An unknown special name.
final class DirectiveError : TeXFluxError
{
    mixin DiagnosticClass!"directive";
}

/// Macro arity, an unbound or misused parameter, shadowing, or recursion.
final class MacroExpansionError : TeXFluxError
{
    mixin DiagnosticClass!"macro";
}

/**
 * A module boundary this compilation cannot cross.
 *
 * Covers import forms, path resolution, content import cycles, the purity of a
 * macro module, macro name conflicts between modules, and import flag bindings.
 */
final class ModuleError : TeXFluxError
{
    mixin DiagnosticClass!"module";
}

///
@safe pure unittest
{
    const span = SourceSpan("a.tfx", SourcePosition(1, 1), SourcePosition(1, 2));
    auto error = new ModuleError("M027", "cannot read module 'b.tfx'", span);
    assert(error.kind == "module");
    assert(error.diagnostic == "a.tfx:1:1: module error: cannot read module 'b.tfx' [M027]");

    auto chained = error.chained(error.message ~ "; imported from c.tfx:3:1",
            RelatedLocation("imported from here", span));
    assert(cast(ModuleError) chained, "a chained error keeps its own class");
    assert(chained.code == "M027", "and its own code");
    assert(chained.span == span, "and its own span");
    assert(chained.related.length == 1);
}

/**
 * A build-flag override that no declaration matches.
 *
 * An override describes the build rather than the document, so it has no
 * source span and no code: only a call that passes flags can raise it, and
 * such a call has to handle it explicitly.
 */
class FlagError : Exception
{
    mixin basicExceptionCtors;
}

/**
 * A defect in the installation or in the compiler, not in a document.
 *
 * Only the bundled standard macro module raises it today: that file ships with
 * TeXFlux and is validated by its own tests, so a failure to read it says
 * nothing about the source being compiled and has no span to point at.
 */
class InternalError : Exception
{
    mixin basicExceptionCtors;
}

/**
 * An argument the public interface cannot accept.
 *
 * This is what a caller gets for a filename holding a null byte, or for
 * generated bytes that are not the text they claim to encode: a mistake in the
 * call rather than in the document, so it carries no span.
 */
class ValueError : Exception
{
    mixin basicExceptionCtors;
}

/**
 * An invariant of the compiler's own data that a pass broke.
 *
 * A syntax-only node reaching the renderer is the canonical example. This is a
 * defect rather than a diagnostic, so it names no source location and the
 * command line reports it as a tool failure.
 */
class CompilerDefect : Exception
{
    mixin basicExceptionCtors;
}

/**
 * A document nested deeper than the compiler will follow.
 *
 * The passes that walk a tree count their own depth rather than trusting the
 * machine stack, because a stack that runs out cannot be reported.
 */
class NestingError : Exception
{
    mixin basicExceptionCtors;
}

/// How deep any one pass will follow a document before refusing it.
enum size_t maximumNestingDepth = 400;

/**
 * One level of a recursive walk over a document.
 *
 * Constructing it counts the level and refuses one past the limit; leaving
 * the scope that holds it uncounts. Every pass that recurses once per nesting
 * level holds one at the top of its recursive function, which is what turns a
 * document too deep for the machine stack into a report instead of a crash.
 *
 * It keeps the address of the counter it is given, which is why its two
 * members are `@trusted`: the caller owns that counter as a field of the
 * struct doing the walk, which stays put while the walk runs, and copying a
 * level is disabled so that each one is released exactly once.
 */
struct Descent
{
    private size_t* depth;

    @disable this(this);

    this(ref size_t depth) @trusted pure
    {
        if (++depth > maximumNestingDepth)
        {
            --depth;
            throw new NestingError("input nests too deeply to compile");
        }
        this.depth = &depth;
    }

    ~this() @trusted pure nothrow @nogc
    {
        if (depth !is null)
            --*depth;
    }
}

///
@safe pure unittest
{
    import std.exception : assertThrown;

    size_t depth;
    {
        auto outer = Descent(depth);
        assert(depth == 1);
        {
            auto inner = Descent(depth);
            assert(depth == 2);
        }
        assert(depth == 1);
    }
    assert(depth == 0);

    size_t full = maximumNestingDepth;
    assertThrown!NestingError(Descent(full));
    assert(full == maximumNestingDepth, "a refused level is not counted");
}
