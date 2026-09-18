/**
 * Source macros: abstraction over structure, not over text.
 *
 * A macro is not a textual substitution. `!defmacro` stores one template made
 * of syntax nodes; a call binds its values and instantiates that template by
 * cloning it. Raw TeX is never re-parsed, no value is implicitly spliced into
 * a TeX group, and nothing a macro produces can go back through the parser.
 * That is the difference between this and a preprocessor in the m4 or cpp
 * sense, and it is why a value holding `::` cannot become structure.
 *
 * Expansion runs after `>>` desugaring and before normalization, so no macro
 * construct ever reaches the renderer. Template nodes are retargeted onto the
 * call site, while nodes substituted for a parameter keep the spans they
 * already carry from that site: an inverse search from the PDF should land on
 * the text the author wrote, not on the definition that moved it.
 */
module texflux.macros;

import std.algorithm : all, any, canFind, map, startsWith;
import std.array : assocArray, join;
import std.ascii : isAlpha, isAlphaNum;
import std.conv : to;
import std.range : zip;
import std.sumtype : get, has, match;
import std.typecons : Nullable, nullable;
import std.utf : byCodeUnit;

import texflux.ast;
import texflux.errors : Descent, MacroExpansionError, RelatedLocation, ValidationError;
import texflux.flags : Conditional, conditionalNames, evaluateConditional, Flags;
import texflux.interpolate : interpolate, rejectMarkers;
import texflux.source : plainText, SourceSpan, SourceText;
import texflux.ordered : OrderedMap;
import texflux.syntax : asSpecial, demandText, rawModeNames, requiredText, sequenceEntries,
    specials, stacks;

/// The special names that belong to the macro system itself.
enum Reserved : string
{
    define = "defmacro",
    param = "param",
    text = "text",
    each = "each",
}

/// Reserved names may be neither redefined nor used as macro names.
private enum reservedNames = [
    cast(string) Reserved.define, cast(string) Reserved.param,
    cast(string) Reserved.text, cast(string) Reserved.each,
] ~ conditionalNames ~ rawModeNames;

/**
 * The module constructs, which a template may not contain.
 *
 * Spelled here rather than imported, because the module system builds on this
 * module rather than the other way round.
 */
private enum moduleNames = ["import", "macroimport"];

/// What marks a parameter as taking everything left over.
private enum restPrefix = "...";

/// A macro must be callable as a special, so it uses the special-name grammar.
bool isMacroName(string name) @safe pure nothrow @nogc
{
    return name.length != 0 && name[0].isAlpha
        && name[1 .. $].byCodeUnit.all!(c => c.isAlphaNum || c == '_');
}

/// A parameter may also open with an underscore and may hold a hyphen.
bool isParameterName(string name) @safe pure nothrow @nogc
{
    return name.length != 0 && (name[0].isAlpha || name[0] == '_')
        && name[1 .. $].byCodeUnit.all!(c => c.isAlphaNum || c == '_' || c == '-');
}

///
@safe pure unittest
{
    assert(isMacroName("wrap") && !isMacroName("with-hyphen") && !isMacroName("_lead"));
    assert(isParameterName("_lead") && isParameterName("with-hyphen"));
    assert(!isParameterName("2nd") && !isParameterName(""));
}

/// One bound value: the nodes it contributes wherever it is referenced.
alias Value = Node[];

/// One declared parameter.
struct MacroParameter
{
    string name;
    bool rest;
}

/// One collected definition, with the site it was written at for diagnostics.
struct MacroDefinition
{
    string name;
    MacroParameter[] parameters;
    Block* template_;
    SourceSpan span;
    /**
     * The module that defines this macro.
     *
     * Its template's names resolve in that module's environment and never in
     * a caller's, which is what makes a macro library's own dependencies
     * private to it.
     */
    string module_;

    /// The trailing rest parameter, when this macro takes one.
    Nullable!MacroParameter rest() const
    {
        if (parameters.length != 0 && parameters[$ - 1].rest)
            return MacroParameter(parameters[$ - 1].name, true).nullable;
        return Nullable!MacroParameter.init;
    }

    /// How many values the parameters before the rest one consume.
    size_t required() const
    {
        return parameters.length - (rest.isNull ? 0 : 1);
    }

    /// The expected value shape, for an arity diagnostic.
    string signature() const
    {
        auto shape = parameters.map!(p => p.rest ? "{..." ~ p.name ~ "}" : "{" ~ p.name ~ "}")
            .join;
        const bound = rest.isNull ? "exactly" : "at least";
        return (shape.length == 0 ? "(no parameters)" : shape) ~ ", " ~ bound ~ " "
            ~ required.to!string ~ " value(s)";
    }
}

/**
 * Every macro one module can see, by name.
 *
 * Ordered because merging two environments reports the first name that
 * collides, and because a module is checked against its own environment in
 * definition order.
 */
alias MacroEnvironment = OrderedMap!MacroDefinition;

// ---------------------------------------------------------------------------
// Collecting definitions
// ---------------------------------------------------------------------------

private MacroParameter[] readParameters(Argument[] groups)
{
    MacroParameter[] parameters;
    foreach (group; groups)
    {
        const text = group.demandText("macro parameter");
        const rest = text.startsWith(restPrefix);
        const name = rest ? text[restPrefix.length .. $] : text;
        if (!isParameterName(name))
            throw new ValidationError("V013",
                    "invalid macro parameter name '" ~ text ~ "'", group.span);
        if (parameters.any!(p => p.name == name))
            throw new ValidationError("V014",
                    "duplicate macro parameter '" ~ name ~ "'", group.span);
        if (parameters.length != 0 && parameters[$ - 1].rest)
            throw new ValidationError("V015",
                    "a rest parameter must be the last macro parameter", group.span);
        parameters ~= MacroParameter(name, rest);
    }
    return parameters;
}

private MacroDefinition readDefinition(SpecialInvocation node, const(string)[] builtins,
        MacroEnvironment defined, string module_, const(string)[] standard)
{
    if (node.suite is null || node.suiteMode.isNull || node.suiteMode.get != SuiteMode.block)
        throw new ValidationError("V016", "!defmacro requires a '::' template suite", node.span);
    if (node.groups.length == 0)
        throw new ValidationError("V017", "!defmacro requires a macro name group", node.span);

    auto nameGroup = node.groups[0];
    const name = nameGroup.demandText("!defmacro name");
    if (!isMacroName(name))
        throw new ValidationError("V018", "invalid macro name '" ~ name ~ "'", nameGroup.span);
    if (reservedNames.canFind(name))
        throw new ValidationError("V019", "'!" ~ name ~ "' is reserved by TeXFlux",
                nameGroup.span);
    if (builtins.canFind(name))
        throw new ValidationError("V020",
                "'!" ~ name ~ "' is a built-in special and cannot be redefined", nameGroup.span);
    if (standard.canFind(name))
        // Strict collision rather than shadowing: with a handful of standard
        // names, no precedence layer and no import-order effect is simpler
        // than a weak-prelude rule nobody would remember.
        throw new ValidationError("V021",
                "macro '!" ~ name ~ "' conflicts with a TeXFlux standard flow macro",
                nameGroup.span);
    if (auto previous = name in defined)
        throw new ValidationError("V022", "macro '!" ~ name ~ "' is already defined at "
                ~ previous.span.location, nameGroup.span,
                [RelatedLocation("first defined here", previous.span)]);

    foreach (special; node.suite.specials)
    {
        if (special.name == cast(string) Reserved.define)
            throw new ValidationError("V023",
                    "!defmacro is only valid at the top level", special.span);
        if (moduleNames.canFind(special.name))
            // Otherwise discovering what a document depends on would depend
            // on expanding macros, and an import would have become a dynamic
            // one.
            throw new ValidationError("V024", "!" ~ special.name
                    ~ " is not allowed inside a macro template", special.span);
    }

    return MacroDefinition(name, readParameters(node.groups[1 .. $]), node.suite,
            node.span, module_);
}

/**
 * Reject a template construct whose suite is not actually written.
 *
 * Desugaring hands every segment but the rightmost a synthetic block suite
 * that no later pass can tell from a written one, so this runs while the
 * composition is still visible. The rightmost segment keeps the line's own
 * suffix, so a composition ending in a real template stays legal.
 */
void validateMacroForms(Document document)
{
    foreach (composition; document.body_.stacks)
        foreach (index, segment; composition.segments)
        {
            auto found = segment.asSpecial;
            if (found.isNull)
                continue;
            const special = found.get;
            if (special.name == cast(string) Reserved.define)
                // Composing a definition would nest it under the segments to
                // its left, and a definition has to be a top-level statement.
                throw new ValidationError("V025", "!defmacro must be a top-level"
                        ~ " '::' definition and cannot be a '>>' segment", special.span);
            const writesOwnSuite = index == composition.segments.length - 1
                && !composition.suiteMode.isNull
                && composition.suiteMode.get == SuiteMode.block;
            if (special.name == cast(string) Reserved.each && !writesOwnSuite)
                throw new ValidationError("V026",
                        "!each requires a '::' template suite of its own", special.span);
        }
}

/// A document with its definitions removed, and the macros it can now see.
struct CollectedMacros
{
    Document document;
    MacroEnvironment macros;
}

/**
 * Strip the top-level definitions and validate their signatures.
 *
 * A definition emits no TeX. Collecting every one of them before expanding any
 * call is what makes a forward reference work.
 *
 * `imported` seeds the table with what `!macroimport` brought in, so a local
 * definition that would shadow one is reported against it and the result is
 * this module's whole environment. `standard` names the bundled flow macros,
 * which every module sees without importing them; redefining one is an error
 * wherever it is written, which is what keeps those names out of a macro
 * module's public table too.
 */
CollectedMacros collectMacros(Document document, const(string)[] builtins,
        MacroEnvironment imported = MacroEnvironment.init, string module_ = "",
        const(string)[] standard = null)
{
    auto macros = imported.dup;
    Node[] nodes;
    foreach (node; document.body_.nodes)
    {
        auto found = node.asSpecial;
        if (found.isNull || found.get.name != cast(string) Reserved.define)
        {
            nodes ~= node;
            continue;
        }
        auto definition = readDefinition(found.get, builtins, macros, module_, standard);
        macros[definition.name] = definition;
    }
    return CollectedMacros(Document(new Block(nodes, document.body_.span), document.span),
            macros);
}

// ---------------------------------------------------------------------------
// Expansion
// ---------------------------------------------------------------------------

/// One template instantiation, and the call site it maps onto.
struct Frame
{
    MacroDefinition macro_;
    SourceSpan callSpan;
    Value[string] values;
    Value[][string] sequences;
    MacroDefinition[] chain;

    /// This frame carrying one more single value, which is what `!each` needs.
    Frame* bound(string name, Value value)
    {
        auto extended = values.dup;
        extended[name] = value;
        return new Frame(macro_, callSpan, extended, sequences, chain);
    }

    bool knows(string name)
    {
        return (name in values) !is null || (name in sequences) !is null;
    }

    /// Locate this expansion: its chain, and the call site it came from.
    string where()
    {
        return "while expanding '" ~ chainText(chain) ~ "' called at " ~ callSpan.location;
    }
}

/**
 * Spell one expansion chain, disambiguating names only when it has to.
 *
 * A chain confined to one module reads as plain macro names. Once two modules
 * are involved a bare name no longer identifies a macro, so each element names
 * the file that defines it.
 */
string chainText(const MacroDefinition[] chain)
{
    if (chain.all!(definition => definition.span.file == chain[0].span.file))
        return chain.map!(definition => definition.name).join(" -> ");
    return chain.map!(definition => definition.name ~ "@" ~ definition.span.file)
        .join(" -> ");
}

/// A diagnostic that names the expansion chain when it is raised inside one.
MacroExpansionError expansionError(string code, string message, SourceSpan span, Frame* frame)
{
    if (frame is null)
        return new MacroExpansionError(code, message, span);
    return new MacroExpansionError(code, message ~ "; " ~ frame.where, span,
            [RelatedLocation("called here", frame.callSpan)]);
}

/// A text field and its provenance, once interpolation has had its turn.
private struct Interpolated
{
    string text;
    Nullable!SourceText parts;
}

/**
 * Interpolate one field, unless its fragments are already final.
 *
 * Existing fragments, escaped marker spellings included, are never scanned
 * again. That is the whole of the rule that inserted text is not rescanned.
 */
private Interpolated textField(string text, Nullable!SourceText parts, SourceSpan origin,
        SourceSpan target, size_t offset, Frame* frame)
{
    if (parts.isNull)
        parts = interpolate(text, origin, target, offset, frame);
    return Interpolated(parts.isNull ? text : plainText(parts.get), parts);
}

/**
 * Expand macro calls and resolve conditionals in one document.
 *
 * The document must already be desugared: a surviving composition would hide
 * the single block value a call binds and the single payload a conditional
 * keeps or drops.
 *
 * `environments` gives each definition's module its own lexical scope, so a
 * template reaches its module's private imports and nothing else. Without it
 * every definition resolves in `macros`, which is what a single-file document
 * needs.
 */
Document expandMacros(Document document, MacroEnvironment macros, Flags flags,
        MacroEnvironment[string] environments = null, string module_ = "",
        bool scoped = false)
{
    auto expander = Expander(macros, flags, environments, module_, scoped);
    return Document(expander.block(document.body_, null), document.span);
}

private struct Expander
{
    private MacroEnvironment macros;
    private Flags flags;
    private MacroEnvironment[string] environments;
    private string module_;
    private bool scoped;
    private size_t depth;

    /// The macro names one template, or the document itself, can see.
    private MacroEnvironment environment(Frame* frame)
    {
        if (!scoped)
            return macros;
        const key = frame is null ? module_ : frame.macro_.module_;
        auto found = key in environments;
        if (found is null)
            // A definition whose module was never registered would otherwise
            // fail only once its template happened to name a special.
            throw new Error("no macro environment for module '" ~ key
                    ~ "'; every definition's module must be registered before expansion");
        return *found;
    }

    Block* block(Block* source, Frame* frame)
    {
        auto descent = Descent(depth);
        return new Block(source.nodes.map!(node => expand(node, frame)).join,
                retarget(source.span, frame));
    }

    private Node[] expand(Node node, Frame* frame)
    {
        return node.match!(
            (SpecialInvocation special) => this.special(special, frame),
            (ParsedInvocation invocation) => [invocation_(invocation, frame)],
            (SequenceEntry entry) => [this.entry(entry, frame)],
            (RawTex raw) => [this.raw(raw, frame)],
            (Stack _) {
                throw new Error("macro expansion requires a desugared syntax AST");
                return Node[].init;
            },
            (other) => [Node(other)],
        );
    }

    private Node[] special(SpecialInvocation node, Frame* frame)
    {
        switch (node.name)
        {
        case cast(string) Reserved.define:
            throw new ValidationError("V027", "!defmacro is only valid at the top level",
                    node.span);
        case cast(string) Reserved.param:
            return param(node, frame);
        case cast(string) Reserved.text:
            throw expansionError("E009", "!text is only valid inside a textual field;"
                    ~ " use !param for an AST position", node.span, frame);
        case cast(string) Reserved.each:
            return each(node, frame);
        case cast(string) Conditional.when:
        case cast(string) Conditional.unless:
            return conditional(node, frame);
        default:
            if (auto macro_ = node.name in environment(frame))
                return call(node, *macro_, frame);
            return [invocation_(node, frame)];
        }
    }

    // -- retargeting -------------------------------------------------------

    /// Template scaffolding points at the call site, not at the definition.
    private static SourceSpan retarget(SourceSpan span, Frame* frame)
    {
        return frame is null ? span : frame.callSpan;
    }

    private Node raw(RawTex node, Frame* frame)
    {
        const target = retarget(node.span, frame);
        if (node.verbatim)
        {
            // A raw-region line is never a text field at all.
            node.span = target;
            return Node(node);
        }
        auto field = textField(node.text, node.parts, node.span, target, 0, frame);
        return Node(rawTex(field.text, target, field.parts, node.verbatim));
    }

    /// A command, an environment or a built-in special, its fields expanded.
    private Node invocation_(T)(T node, Frame* frame)
            if (is(T == ParsedInvocation) || is(T == SpecialInvocation))
    {
        // Every remaining built-in special names compiler metadata -- a module
        // path, a binding list -- so none of its groups is a text field. A
        // macro call's own values are read by `values` instead.
        enum isSpecial = is(T == SpecialInvocation);
        static if (isSpecial)
            const owner = "!" ~ node.name;
        else
            enum owner = "";

        Argument[] groups;
        foreach (group; node.groups)
            groups ~= argument(group, frame, !isSpecial, owner);
        node.groups = groups;
        node.suite = node.suite is null ? null : block(node.suite, frame);
        node.span = retarget(node.span, frame);
        if (!node.suiteSpan.isNull)
            node.suiteSpan = retarget(node.suiteSpan.get, frame).nullable;
        return Node(node);
    }

    private void reject(string text, SourceSpan span, string where, Frame* frame)
    {
        try
            rejectMarkers(text, span, where);
        catch (MacroExpansionError error)
            throw expansionError(error.code, error.message, error.span, frame);
    }

    private Argument argument(Argument value, Frame* frame, bool allowed, string owner)
    {
        const target = retarget(value.span, frame);
        if (value.value.has!(Block*))
            return Argument(value.kind, ArgumentValue(block(value.value.get!(Block*), frame)),
                    value.layout, target, value.parts);

        const text = value.value.get!string;
        if (value.kind == GroupKind.binding)
        {
            reject(text, value.span, "a '(...)' binding list", frame);
            return Argument(value.kind, ArgumentValue(text), value.layout, target, value.parts);
        }
        if (!allowed)
        {
            reject(text, value.span, owner ~ "'s arguments", frame);
            return Argument(value.kind, ArgumentValue(text), value.layout, target, value.parts);
        }
        // A string-valued argument is an inline group: that is the one layout
        // the header scanner gives it, and nothing rewrites it before here.
        assert(value.layout == ArgumentLayout.inline);
        auto field = textField(text, value.parts, value.span, target, 1, frame);
        return inlineArgument(value.kind, field.text, target, field.parts);
    }

    private Node entry(SequenceEntry node, Frame* frame)
    {
        return Node(SequenceEntry(block(node.value, frame), retarget(node.markerSpan, frame),
                retarget(node.span, frame), node.argumentKind));
    }

    // -- template constructs ----------------------------------------------

    private string[] singleNames(SpecialInvocation node, string label, size_t count, Frame* frame)
    {
        if (node.groups.length != count)
            throw new ValidationError("V028", label ~ " requires exactly "
                    ~ count.to!string ~ " required group(s)", node.span);
        string[] texts;
        foreach (group; node.groups)
        {
            const text = group.demandText(label ~ " name");
            reject(text, group.span, "a " ~ label ~ " name group", frame);
            texts ~= text;
        }
        return texts;
    }

    /// The frame a template-only construct needs in order to read its bindings.
    private Frame* templateFrame(SpecialInvocation node, Frame* frame)
    {
        if (frame is null)
            throw new MacroExpansionError("E010",
                    "!" ~ node.name ~ " is only valid inside a macro template", node.span);
        return frame;
    }

    private Node[] param(SpecialInvocation node, Frame* outer)
    {
        auto frame = templateFrame(node, outer);
        if (node.suite !is null)
            throw new ValidationError("V029", "!param does not accept a suite", node.span);
        const name = singleNames(node, "!param", 1, frame)[0];

        if (name in frame.sequences)
            throw expansionError("E011",
                    "'" ~ name ~ "' is a rest parameter; use !each to expand it",
                    node.span, frame);
        auto value = name in frame.values;
        if (value is null)
            throw expansionError("E012", "unknown macro parameter '" ~ name ~ "'",
                    node.span, frame);
        // The bound value already carries call-site spans, so it is spliced in
        // unchanged rather than retargeted.
        return *value;
    }

    private Node[] each(SpecialInvocation node, Frame* outer)
    {
        auto frame = templateFrame(node, outer);
        if (node.suite is null || node.suiteMode.isNull || node.suiteMode.get != SuiteMode.block)
            throw new ValidationError("V030", "!each requires a '::' template suite", node.span);
        const names = singleNames(node, "!each", 2, frame);
        const sequenceName = names[0];
        const itemName = names[1];
        if (!isParameterName(itemName))
            throw new ValidationError("V031", "invalid !each item name '" ~ itemName ~ "'",
                    node.groups[1].span);

        if (sequenceName in frame.values)
            throw expansionError("E013",
                    "'" ~ sequenceName ~ "' is not a rest parameter; !each needs one",
                    node.span, frame);
        auto sequence = sequenceName in frame.sequences;
        if (sequence is null)
            throw expansionError("E014", "unknown macro parameter '" ~ sequenceName ~ "'",
                    node.span, frame);
        if (frame.knows(itemName))
            throw expansionError("E015",
                    "!each item '" ~ itemName ~ "' shadows a bound macro parameter",
                    node.span, frame);

        return (*sequence).map!(value => block(node.suite, frame.bound(itemName, value)).nodes)
            .join;
    }

    // -- conditionals ------------------------------------------------------

    /// Keep or drop one payload, whole.
    private Node[] conditional(SpecialInvocation node, Frame* frame)
    {
        foreach (group; node.groups)
            if (group.value.has!string)
                reject(group.value.get!string, group.span,
                        "a !" ~ node.name ~ " flag group; flag names are static", frame);

        const keep = evaluateConditional(node, flags);
        if (node.suite is null)
            throw new ValidationError("V032",
                    "!" ~ node.name ~ " requires a '::' suite or a '>>' payload", node.span);
        if (node.suiteMode.isNull || node.suiteMode.get != SuiteMode.block)
            throw new ValidationError("V033",
                    "!" ~ node.name ~ " does not accept a ':::' sequence suite", node.span);

        if (!keep)
            // A dropped payload is never expanded, so an author can disable
            // content that no longer compiles and still build the document.
            return null;
        return block(node.suite, frame).nodes;
    }

    // -- calls -------------------------------------------------------------

    private Node[] call(SpecialInvocation node, MacroDefinition macro_, Frame* frame)
    {
        auto outer = frame is null ? null : frame.chain;
        auto chain = outer ~ macro_;
        // Two modules may define the same name, so identity is the pair.
        if (outer.any!(d => d.module_ == macro_.module_ && d.name == macro_.name))
            throw expansionError("E016",
                    "recursive macro expansion detected: " ~ chainText(chain), node.span, frame);

        auto values = readValues(node, frame);
        const callSpan = retarget(node.span, frame);
        auto bound = bind(node, macro_, values, frame);
        auto inner = new Frame(macro_, callSpan, bound.values, bound.sequences, chain);
        return block(macro_.template_, inner).nodes;
    }

    /// Read a call's values: its compact groups first, then its suite.
    private Value[] readValues(SpecialInvocation node, Frame* frame)
    {
        Value[] values;
        foreach (group; node.groups)
        {
            const text = requiredText(group);
            if (text.isNull)
                throw expansionError("E017", "macro calls accept required '{...}' values only",
                        group.span, frame);
            const target = retarget(group.span, frame);
            auto field = textField(text.get, group.parts, group.span, target, 1, frame);
            values ~= [Node(rawTex(field.text, target, field.parts))];
        }

        if (node.suite !is null)
        {
            const sequence = !node.suiteMode.isNull && node.suiteMode.get == SuiteMode.sequence;
            if (sequence)
                foreach (entry; node.suite.sequenceEntries)
                    if (!entry.argumentKind.isNull)
                        throw expansionError("E018",
                                "macro calls do not accept '+' sequence entries",
                                entry.span, frame);
            auto suite = block(node.suite, frame);
            if (sequence)
                foreach (entry; suite.sequenceEntries)
                    values ~= entry.value.nodes;
            else
                values ~= suite.nodes;
        }
        return values;
    }

    private struct BoundValues
    {
        Value[string] values;
        Value[][string] sequences;
    }

    private BoundValues bind(SpecialInvocation node,
            MacroDefinition macro_, Value[] values, Frame* frame)
    {
        auto rest = macro_.rest;
        const required = macro_.required;
        if (values.length < required || (rest.isNull && values.length != required))
            throw expansionError("E019", "'!" ~ macro_.name ~ "' expects " ~ macro_.signature
                    ~ ", got " ~ values.length.to!string, node.span, frame);

        auto bound = zip(macro_.parameters[0 .. required].map!(parameter => parameter.name),
                values[0 .. required]).assocArray;
        Value[][string] sequences;
        if (!rest.isNull)
            sequences[rest.get.name] = values[required .. $];
        return BoundValues(bound, sequences);
    }
}
