/**
 * Writing the canonical tree out as TeX, and recording where each run came from.
 *
 * The layout is fixed and says nothing about how the author wrote the source. A
 * generated argument always opens with `{%` on its own line, so the newline
 * behind the brace reaches TeX as nothing rather than as a space, and always
 * closes on the line after its value. A value that fits on its marker line and
 * a value that spans a whole environment therefore render identically, and no
 * parser state travels here to say which one it was.
 *
 * The one place the author's own text decides anything is the group they wrote
 * out themselves: its delimiters are theirs, but where the next argument starts
 * is not, and a comment on its last line would swallow whatever was pulled up
 * behind it.
 *
 * Every run of output is recorded with the span it came from, which is what the
 * source map serializes and what lets an editor jump from the PDF back to the
 * line the author actually wrote.
 */
module texflux.render;

import std.array : Appender, join;
import std.conv : to;
import std.string : lastIndexOf;
import std.sumtype : get, has, match;
import std.typecons : Nullable, nullable;

import texflux.ast;
import texflux.errors : CompilerDefect, Descent;
import texflux.source : LoadedSource, SourcePosition, SourceSpan, SourceText;
import texflux.syntax : blank, isEscaped;

/**
 * What one run of generated text is.
 *
 * The distinction is what an inverse search ranks by when it has a line but no
 * column: content is what the author wrote, scaffold is what a macro template
 * contributed, and the rest is punctuation this compiler generated.
 */
enum RenderRole : string
{
    content = "content",
    open = "open",
    close = "close",
    synthetic = "synthetic",
    scaffold = "scaffold",
}

/// A range of generated text, in the same columns a source span uses.
struct GeneratedSpan
{
    SourcePosition start;
    SourcePosition end;
}

/// One run of generated text and where it came from.
struct RenderedFragment
{
    string text;
    GeneratedSpan generated;
    Nullable!SourceSpan source;
    RenderRole role;
}

/// A rendered document: its text, and every run that makes it up.
struct RenderedDocument
{
    string text;
    RenderedFragment[] fragments;
}

/// A compiled document, with everything a source map needs to be written.
struct CompilationResult
{
    string text;
    RenderedDocument rendered;
    /// Every module the compilation read, the root first.
    LoadedSource[] sources;
}

/// Write generated text while recording the runs it is made of.
final class MappedEmitter
{
    private Appender!(string[]) parts;
    private RenderedFragment[] fragments;
    private SourcePosition cursor = SourcePosition(1, 1);

    /**
     * Append text, extending the previous run when it came from the same place.
     *
     * Coalescing is what keeps a source map to one entry per run of output
     * rather than one per call, and it is safe precisely because two adjacent
     * runs with the same span and the same role are indistinguishable to
     * everything downstream.
     */
    void emit(string text, Nullable!SourceSpan source, RenderRole role)
    {
        if (text.length == 0)
            return;
        const start = cursor;
        cursor = start.advance(text);
        const generated = GeneratedSpan(start, cursor);
        if (fragments.length != 0 && fragments[$ - 1].source == source
                && fragments[$ - 1].role == role)
        {
            auto previous = fragments[$ - 1];
            fragments[$ - 1] = RenderedFragment(previous.text ~ text,
                    GeneratedSpan(previous.generated.start, generated.end), source, role);
        }
        else
            fragments ~= RenderedFragment(text, generated, source, role);
        parts.put(text);
    }

    /// ditto
    void emit(string text, SourceSpan source, RenderRole role)
    {
        emit(text, source.nullable, role);
    }

    /// A line break, which belongs to the layout rather than to any source.
    void newline()
    {
        emit("\n", Nullable!SourceSpan.init, RenderRole.synthetic);
    }

    /// ditto
    void line(string text, SourceSpan source, RenderRole role)
    {
        emit(text, source.nullable, role);
        newline();
    }

    /// A cursor into what has been written, for reading one span of it back.
    size_t mark() const
    {
        return parts.data.length;
    }

    /// Everything written since a mark.
    string textSince(size_t mark) const
    {
        return parts.data[mark .. $].join;
    }

    /**
     * Retract one written newline, so that a closing group can hug its content.
     *
     * A coalesced run keeps the text that preceded the newline, so the cursor
     * belongs after that text rather than at the run's start.
     */
    void dropTrailingNewline()
    {
        auto written = parts.data;
        if (written.length == 0 || written[$ - 1].length == 0
                || written[$ - 1][$ - 1] != '\n')
            return;

        const last = fragments[$ - 1];
        written[$ - 1] = written[$ - 1][0 .. $ - 1];
        if (written[$ - 1].length == 0)
            parts.shrinkTo(written.length - 1);

        const trimmed = last.text[0 .. $ - 1];
        cursor = last.generated.start.advance(trimmed);
        if (trimmed.length != 0)
            fragments[$ - 1] = RenderedFragment(trimmed,
                    GeneratedSpan(last.generated.start, cursor), last.source, last.role);
        else
            fragments = fragments[0 .. $ - 1];
    }

    /// The finished document. An empty one is still one line.
    RenderedDocument finish()
    {
        if (parts.data.length == 0)
            newline();
        return RenderedDocument(parts.data.join, fragments);
    }
}

/// Render a canonical document, terminated by a newline.
string render(Document document, bool sourceComments = false)
{
    return renderWithProvenance(document, sourceComments).text;
}

/// ditto, keeping the record of where each run came from.
RenderedDocument renderWithProvenance(Document document, bool sourceComments = false)
{
    auto renderer = Renderer(new MappedEmitter, sourceComments);
    renderer.block(document.body_);
    return renderer.emitter.finish();
}

private struct Renderer
{
    MappedEmitter emitter;
    bool sourceComments;
    private size_t depth;

    void block(Block* source)
    {
        auto descent = Descent(depth);
        foreach (node; source.nodes)
        {
            if (sourceComments && !node.blank)
                sourceComment(node);
            node.match!(
                (RawTex raw) {
                    if (raw.text.length == 0)
                        // A blank source line is a newline the author wrote,
                        // so it is attributed to them rather than to layout.
                        emitter.emit("\n", raw.span, RenderRole.content);
                    else
                    {
                        text(raw.text, raw.parts, raw.span, RenderRole.content);
                        emitter.newline();
                    }
                },
                (GenericInvocation invocation) { this.invocation(invocation); },
                (BraceGroup group) {
                    emitter.line("{%", group.span, RenderRole.open);
                    if (group.headerRaw.length != 0)
                    {
                        text(group.headerRaw, group.headerParts, group.span, RenderRole.content);
                        emitter.newline();
                    }
                    block(group.body_);
                    emitter.line("}", group.span, RenderRole.close);
                },
                (n) {
                    throw new CompilerDefect("renderer accepts canonical AST only");
                },
            );
        }
    }

    private void sourceComment(Node node)
    {
        import std.format : format;

        const span = node.spanOf;
        emitter.emit(format("%% texflux: %s:%d", span.file, span.start.line),
                Nullable!SourceSpan.init, RenderRole.synthetic);
        emitter.newline();
    }

    /// One text field, as its runs when it has them and as itself when it does not.
    private void text(string value, Nullable!SourceText parts, SourceSpan span, RenderRole role)
    {
        if (parts.isNull)
        {
            emitter.emit(value, span, role);
            return;
        }
        foreach (fragment; parts.get)
            emitter.emit(fragment.text, fragment.span,
                    fragment.scaffold ? RenderRole.scaffold : role);
    }

    private void invocation(GenericInvocation node)
    {
        const prefix = node.body_ is null ? "\\" ~ node.name : "\\begin{" ~ node.name ~ "}";
        emitter.emit(prefix, node.span, RenderRole.open);
        foreach (argument; node.arguments)
            this.argument(argument);
        emitter.newline();
        if (node.body_ is null)
            return;
        block(node.body_);
        emitter.line("\\end{" ~ node.name ~ "}", node.span, RenderRole.close);
    }

    private void argument(Argument value)
    {
        if (value.layout == ArgumentLayout.inline)
        {
            inlineGroup(value);
            return;
        }
        if (!value.value.has!(Block*))
            throw new CompilerDefect("renderer received an invalid argument value");
        blockArgument(value, value.value.get!(Block*));
    }

    private void inlineGroup(Argument value)
    {
        if (value.kind == GroupKind.binding)
            // A binding list configures an import; it is never TeX to emit.
            throw new CompilerDefect("renderer received a binding list");
        if (!value.value.has!string)
            throw new CompilerDefect(
                    "renderer received a non-inline argument in an inline position");
        emitter.emit(value.kind.opener.to!string, value.span, RenderRole.open);
        text(value.value.get!string, value.parts, value.span, RenderRole.content);
        emitter.emit(value.kind.closer.to!string, value.span, RenderRole.close);
    }

    /**
     * A generated value in its braces, or a group the author wrote as it is.
     *
     * The generated form is the whole of the layout rule: `{%` on its own line
     * and `}` on the line after the value, whatever shape that value has. The
     * closing brace is written without a line break of its own, so the next
     * argument's brace lands beside it.
     */
    private void blockArgument(Argument value, Block* body_)
    {
        final switch (value.layout)
        {
        case ArgumentLayout.block:
            emitter.line("{%", value.span, RenderRole.open);
            block(body_);
            emitter.emit("}", value.span, RenderRole.close);
            return;
        case ArgumentLayout.explicit:
            const mark = emitter.mark();
            block(body_);
            closeHugged(mark);
            return;
        case ArgumentLayout.inline:
            throw new CompilerDefect("renderer received an invalid argument layout");
        }
    }

    /**
     * Pull what follows onto an authored group's last line, when that is safe.
     *
     * The group's own delimiters are the author's, but where the next argument
     * starts is not. A comment on that last line would swallow whatever was
     * pulled up, so such a group keeps its newline and the next argument begins
     * underneath. The rule reads the rendered text rather than the author's
     * intent, so an escaped percent sign is not a comment.
     */
    private void closeHugged(size_t mark)
    {
        // Only the value's own text can carry a comment, so read back exactly
        // what it wrote rather than the whole output line.
        const written = emitter.textSince(mark);
        const trimmed = written.length != 0 && written[$ - 1] == '\n'
            ? written[0 .. $ - 1] : written;
        const lastLine = trimmed[trimmed.lastIndexOf('\n') + 1 .. $];
        if (hasComment(lastLine))
            return;
        emitter.dropTrailingNewline();
    }
}

/// Whether a line carries a comment, which an escaped percent sign is not.
private bool hasComment(const(char)[] line) @safe pure nothrow
{
    foreach (index, char c; line)
        if (c == '%' && !line.isEscaped(index))
            return true;
    return false;
}

///
@safe pure unittest
{
    assert(hasComment("value %"));
    assert(!hasComment("100\\% done"));
    assert(hasComment("100\\\\% done"), "an even run of backslashes escapes itself");
    assert(!hasComment("no comment here"));
}
