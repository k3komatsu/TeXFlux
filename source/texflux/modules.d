/**
 * Modules: one file is one module, composed in two different ways.
 *
 * A `.tfx` file is one independent content module. `!import` compiles it as
 * its own instance -- its own build flags, its own macros -- and splices the
 * canonical tree that produces into the caller. Nothing else crosses: the
 * callee's flags and macro names never enter the caller's tables.
 *
 * A `.tfxm` file is one macro-definition module. `!macroimport` loads the
 * macros that module defines itself and nothing it imported in turn, so a
 * macro library's own dependencies stay private to it. Definitions keep a
 * lexical scope, so a template reaches its own module's imports rather than
 * its caller's namespace. That is what lets two content modules in one
 * document depend on different versions of the same macro library.
 *
 * All of this belongs to the compilation session. The renderer, the
 * provenance fragments and the SyncTeX remapping stay downstream of it and
 * know nothing about module boundaries.
 */
module texflux.modules;

import std.algorithm : canFind, endsWith, filter, map, splitter, startsWith;
import std.array : array, join;
import std.conv : to;
import std.path : buildNormalizedPath, dirName, isAbsolute;
import std.string : indexOf;
import std.sumtype : get, has, match;
import std.typecons : Nullable, Tuple;

import texflux.ast;
import texflux.canonical : builtinDirectives, canonicalize, DirectiveRegistry;
import texflux.desugar : desugar;
import texflux.errors : Descent, InternalError, ModuleError, RelatedLocation, TeXFluxError;
import texflux.flags : collectFlags, Conditional, declaredFlagsHint, Flags, flagValue,
    isFlagName, validateFlagForms;
import texflux.macros : collectMacros, expandMacros, MacroEnvironment, Reserved,
    validateMacroForms;
import texflux.parser : parse;
import texflux.paths : absoluteNormalized, normalizedPath, openFailure;
import texflux.ordered : OrderedMap;
import texflux.source : LoadedSource, SourcePosition, SourceSpan;
import texflux.syntax : asSpecial, demandText, mapChildren, requiredText, specials, stacks;
import texflux.text : decodeUtf8, lstripWhitespace, stripSpaces, stripWhitespace,
    UnicodeDecodeError;

/// A module's kind, which its file extension alone decides.
enum ModuleKind : string
{
    content = ".tfx",
    macro_ = ".tfxm",
}

/// The special names that belong to the module system itself.
enum Module : string
{
    import_ = "import",
    macroimport = "macroimport",
}

/// The special that imports a module of one kind.
private string construct(ModuleKind kind) @safe pure nothrow
{
    return kind == ModuleKind.content ? Module.import_ : Module.macroimport;
}

/**
 * The bundled standard flow macros, under a synthetic identity.
 *
 * That identity is what their lexical scope and their spans are keyed by, so
 * the path the package happens to be installed at never becomes part of the
 * language. The file itself is embedded at compile time from the same source
 * the other implementation reads as a package resource.
 */
enum preludeModule = "texflux:prelude";

/// ditto
private enum preludeSource = import("prelude.tfxm");

/// What a macro module may state at its top level, beside comments and blanks.
private enum macroModuleStatements = [cast(string) Reserved.define,
    cast(string) Module.macroimport];

/**
 * What a macro module may not contain anywhere, template interiors included.
 *
 * A macro module declares no flags, so a conditional inside one would read
 * the flags of whichever content module happened to instantiate it.
 */
private enum macroModuleForbidden = [cast(string) Conditional.declare,
    cast(string) Conditional.when, cast(string) Conditional.unless,
    cast(string) Module.import_];

/// Template-only constructs, which need no definition to be valid.
private enum templateNames = [cast(string) Reserved.param, cast(string) Reserved.each,
    cast(string) Reserved.text];

/**
 * Reads one module's bytes, given the spelling it was asked for.
 *
 * An editor supplies its own, so that an unsaved buffer is compiled in place
 * of the file on disk. A reader says "no such module" by throwing, which the
 * session turns into a diagnostic that points at the import.
 */
alias SourceReader = immutable(ubyte)[] delegate(string display);

/// The default reader: whatever is on disk at that spelling.
immutable(ubyte)[] readSource(string display)
{
    import std.file : read;

    return cast(immutable(ubyte)[]) read(absoluteNormalized(display));
}

/// One loaded file, cached by canonical path identity.
struct ModuleSource
{
    /// Canonical identity, which two spellings of one file share.
    string path;
    /// The spelling its spans and diagnostics use.
    string display;
    /// The bytes themselves, for the source map's digest.
    immutable(ubyte)[] data;
    /// The parsed tree, before desugaring.
    Document document;
}

/// One resolved macro import.
struct MacroImport
{
    string path;
    string display;
    /// The path group, which is what an author would fix.
    SourceSpan span;
}

/**
 * Resolve one written import path against the file that writes it.
 *
 * Resolution never depends on which parent imported that file, which is what
 * makes a reusable component portable.
 */
string resolveModulePath(string importer, string written, ModuleKind kind, SourceSpan span)
{
    if (written.length == 0)
        throw new ModuleError("M001", "module path must not be empty", span);
    if (written.canFind('\0'))
        // The operating system refuses to even look at such a path, and says
        // so differently from how it says a file is missing, so it is
        // rejected here for both constructs at once.
        throw new ModuleError("M002", "module path must not contain a NUL character", span);
    if (written.canFind('\\'))
        throw new ModuleError("M003", "module paths use '/' separators", span);
    if (written.isAbsolute)
        throw new ModuleError("M004",
                "module paths must be relative to the importing file", span);
    if (!written.endsWith(cast(string) kind))
        throw new ModuleError("M005", "!" ~ kind.construct ~ " requires a '"
                ~ cast(string) kind ~ "' module; got '" ~ written ~ "'", span);
    return buildNormalizedPath(importer.dirName, written);
}

// ---------------------------------------------------------------------------
// Flag bindings
// ---------------------------------------------------------------------------

/// One `name=value` entry of an import's binding list.
struct FlagBinding
{
    string name;
    /// The literal the binding gives, or nothing while forwarding.
    Nullable!bool literal;
    /// The caller flag being forwarded, or nothing for a literal.
    string caller;
    SourceSpan span;
    SourceSpan nameSpan;
    SourceSpan valueSpan;
}

/**
 * The span of one slice of a binding list.
 *
 * A header is one physical line, so only the column moves. The offset skips
 * the parenthesis the group's own span starts at.
 */
private SourceSpan subSpan(Argument argument, size_t start, size_t end)
{
    const line = argument.span.start.line;
    const column = argument.span.start.column + 1;
    return SourceSpan(argument.span.file,
            SourcePosition(line, column + cast(int) start),
            SourcePosition(line, column + cast(int) end));
}

/**
 * Read one binding list.
 *
 * Neither a comma nor a parenthesis can occur inside a binding value, so
 * splitting on commas is exact and needs no scanner of its own.
 */
FlagBinding[] parseBindings(Argument argument)
{
    if (!argument.value.has!string)
        throw new ModuleError("M006", "!import binding list must be inline text",
                argument.span);
    // Scanned as code points, like every header, so that an offset is a column.
    const whole = argument.value.get!string.to!dstring;
    if (whole.stripSpaces.length == 0)
        throw new ModuleError("M007", "!import binding list is empty; omit '(...)' instead",
                argument.span);

    FlagBinding[] bindings;
    foreach (piece; whole.splitter(','))
    {
        // A chunk is located by where its slice sits inside the whole list.
        const chunk = piece.stripSpaces;
        const first = chunk.ptr - whole.ptr;
        bindings ~= readBinding(argument, chunk, first, first + chunk.length);
    }
    return bindings;
}

/// A rejected binding is located in columns, which count code points.
unittest
{
    import std.exception : collectException;

    const span = SourceSpan("x.tfx", SourcePosition(1, 10), SourcePosition(1, 17));
    auto error = collectException!ModuleError(
            parseBindings(inlineArgument(GroupKind.binding, "日本=on", span)));
    assert(error !is null && error.code == "M008");
    assert(error.span == SourceSpan("x.tfx", SourcePosition(1, 11), SourcePosition(1, 16)));
    assert(parseBindings(inlineArgument(GroupKind.binding, "a=on, b=$c", span))[1].caller == "c");
}

/// One binding, which is a flag name, an equals sign and one of three values.
private FlagBinding readBinding(Argument argument, dstring chunk, size_t first, size_t last)
{
    auto rejected()
    {
        return new ModuleError("M008", "!import bindings are written 'flag=on',"
                ~ " 'flag=off' or 'flag=$callerFlag'; got '" ~ chunk.to!string ~ "'",
                subSpan(argument, first, last > first ? last : first + 1));
    }

    const equals = chunk.indexOf('=');
    if (equals < 0)
        throw rejected();
    // Spaces around the sign are allowed and belong to neither side.
    const name = chunk[0 .. equals].stripSpaces;
    const value = chunk[equals + 1 .. $].stripSpaces;
    if (!isFlagName(name.to!string) || value.length == 0)
        throw rejected();

    const forwarded = value[0] == '$';
    if (forwarded)
    {
        if (!isFlagName(value[1 .. $].to!string))
            throw rejected();
    }
    else if (flagValue(value.to!string).isNull)
        throw rejected();

    // The name and the value are located inside the chunk, which is itself
    // located inside the list, so a diagnostic points at the word it is about.
    const nameAt = first + chunk.indexOf(name);
    const valueAt = first + chunk.length - value.length;
    return FlagBinding(name.to!string, forwarded ? Nullable!bool.init : flagValue(value.to!string),
            forwarded ? value[1 .. $].to!string : null, subSpan(argument, first, last),
            subSpan(argument, nameAt, nameAt + name.length),
            subSpan(argument, valueAt, valueAt + value.length));
}

/**
 * Resolve an imported module's flags from its defaults and one binding list.
 *
 * An unbound flag keeps the callee's own default. There is no same-name
 * inheritance: a caller's flag reaches a callee only through `$name`, so
 * importing a module cannot change its meaning by accident.
 */
Flags bindImportFlags(Flags declared, FlagBinding[] bindings, Flags callerFlags, string module_)
{
    auto resolved = declared.dup;
    SourceSpan[string] bound;
    foreach (binding; bindings)
    {
        if (binding.name !in declared)
            throw new ModuleError("M009", "imported module '" ~ module_
                    ~ "' does not declare build flag '" ~ binding.name ~ "'; "
                    ~ declaredFlagsHint(declared), binding.nameSpan);
        if (auto first = binding.name in bound)
            throw new ModuleError("M010", "build flag '" ~ binding.name
                    ~ "' is bound twice; first bound at " ~ first.location, binding.nameSpan,
                    [RelatedLocation("first bound here", *first)]);
        if (binding.caller !is null && binding.caller !in callerFlags)
            throw new ModuleError("M011", "unknown build flag '" ~ binding.caller
                    ~ "' in this module; " ~ declaredFlagsHint(callerFlags), binding.valueSpan);
        bound[binding.name] = binding.nameSpan;
        resolved[binding.name] = binding.caller is null
            ? binding.literal.get : callerFlags[binding.caller];
    }
    return resolved;
}

// ---------------------------------------------------------------------------
// Form validation
// ---------------------------------------------------------------------------

/**
 * Reject a macro import written as a `>>` segment.
 *
 * Like a flag declaration, a macro import is resolved before conditionals
 * are, so it must not be composable into one: a macro namespace that depended
 * on a build flag would defeat lexical scope and caching alike.
 */
void validateMacroImportForms(Document document)
{
    foreach (composition; document.body_.stacks)
        foreach (segment; composition.segments)
        {
            auto special = segment.asSpecial;
            if (!special.isNull && special.get.name == cast(string) Module.macroimport)
                throw new ModuleError("M012", "!macroimport must be a top-level"
                        ~ " declaration and cannot be a '>>' segment", special.get.span);
        }
}

/**
 * Keep a macro module a pure declarative one.
 *
 * Purity is what makes macro imports order-insensitive, cacheable, safe in a
 * cyclic graph, and independent of build flags.
 */
void validateMacroModulePurity(Document document)
{
    foreach (special; document.body_.specials)
        if (macroModuleForbidden.canFind(special.name))
            throw new ModuleError("M013", "'!" ~ special.name
                    ~ "' is not allowed in a .tfxm macro module", special.span);

    foreach (node; document.body_.nodes)
    {
        const allowed = node.match!(
            (RawTex raw) => raw.text.stripWhitespace.length == 0
                || raw.text.lstripWhitespace.startsWith("%"),
            (SpecialInvocation special) => macroModuleStatements.canFind(special.name),
            _ => false,
        );
        if (!allowed)
            throw new ModuleError("M014", "a .tfxm macro module may contain only !defmacro,"
                    ~ " !macroimport, comment lines and blank lines", node.spanOf);
    }
}

// ---------------------------------------------------------------------------
// Macro imports
// ---------------------------------------------------------------------------

private MacroImport readMacroImport(SpecialInvocation node, ref SourceSpan[string] seen,
        string importer)
{
    if (node.suite !is null)
        throw new ModuleError("M015", "!macroimport does not accept a suite", node.span);
    foreach (group; node.groups)
        if (group.kind == GroupKind.binding)
            throw new ModuleError("M016", "!macroimport does not accept a '(...)' list",
                    group.span);
    if (node.groups.length != 1)
        throw new ModuleError("M017", "!macroimport requires one '{path}' group", node.span);

    auto group = node.groups[0];
    if (requiredText(group).isNull)
        throw new ModuleError("M018", "!macroimport path must be a required '{...}' group",
                group.span);

    const written = group.demandText("!macroimport path");
    const display = resolveModulePath(importer, written, ModuleKind.macro_, group.span);
    const path = normalizedPath(display);
    if (auto first = path in seen)
        throw new ModuleError("M019", "macro module '" ~ display
                ~ "' is already imported at " ~ first.location, group.span,
                [RelatedLocation("first imported here", *first)]);
    seen[path] = group.span;
    return MacroImport(path, display, group.span);
}

/// A document with its macro imports removed, and the imports themselves.
alias ResolvedMacroImports = Tuple!(Document, "document", MacroImport[], "imports");

/// Strip the top-level macro imports and resolve their paths.
ResolvedMacroImports resolveMacroImports(Document document, string importer)
{
    SourceSpan[string] seen;
    MacroImport[] imports;
    Node[] nodes;
    foreach (node; document.body_.nodes)
    {
        auto found = node.asSpecial;
        if (found.isNull || found.get.name != cast(string) Module.macroimport)
        {
            nodes ~= node;
            continue;
        }
        imports ~= readMacroImport(found.get, seen, importer);
    }

    auto body_ = new Block(nodes, document.body_.span);
    // Every top-level declaration is gone, so anything left is nested.
    foreach (special; body_.specials)
        if (special.name == cast(string) Module.macroimport)
            throw new ModuleError("M020", "!macroimport is only valid at the top level",
                    special.span);
    return ResolvedMacroImports(Document(body_, document.span), imports);
}

/**
 * Union one module's own macros with the public macros it imports.
 *
 * Shadowing is not a rule here: a duplicate visible name is an error, so
 * there is no "last import wins" for an author to have to reason about.
 */
MacroEnvironment mergeImports(MacroEnvironment base, MacroImport[] imports,
        ref OrderedMap!MacroEnvironment public_)
{
    auto environment = base.dup;
    foreach (macroImport; imports)
        foreach (name, ref definition; public_[macroImport.path])
        {
            if (auto existing = name in environment)
                throw new ModuleError("M021", "macro '!" ~ name
                        ~ "' is already available here, defined at " ~ existing.span.location,
                        macroImport.span,
                        [RelatedLocation("defined here", existing.span)]);
            environment[name] = definition;
        }
    return environment;
}

/// One validated macro module: the macros it defines, and what it imports.
alias CollectedMacroModule = Tuple!(MacroEnvironment, "macros", MacroImport[], "imports");

/**
 * Validate one parsed macro module and read its own macros and imports.
 *
 * `display` is the spelling its import paths resolve against and `module_`
 * the identity its definitions are scoped to. They differ only for the
 * bundled standard module, which has no path at all.
 */
CollectedMacroModule collectMacroModule(Document document, string display, string module_,
        DirectiveRegistry registry, const(string)[] standard = null)
{
    validateMacroForms(document);
    validateMacroImportForms(document);
    validateMacroModulePurity(document);
    auto resolved = resolveMacroImports(desugar(document), display);
    auto collected = collectMacros(resolved.document, registry.keys, MacroEnvironment.init,
            module_, standard);
    return CollectedMacroModule(collected.macros, resolved.imports);
}

/**
 * Collect the compiler-bundled standard flow macros.
 *
 * The module ships with TeXFlux and is versioned with it, so it is embedded
 * rather than resolved against the user's project. It is an ordinary pure
 * macro module and is validated as one; a failure here is a defect in the
 * build rather than anything a document wrote, which is why it is not a
 * diagnostic and carries no span.
 */
MacroEnvironment loadStandardMacros(DirectiveRegistry registry = builtinDirectives(),
        string text = preludeSource)
{
    try
    {
        auto collected = collectMacroModule(parse(text, preludeModule), preludeModule,
                preludeModule, registry);
        if (collected.imports.length != 0)
            throw new InternalError("internal error: bundled prelude is invalid: "
                    ~ collected.imports[0].span.location
                    ~ ": the bundled standard macro module imports no other module");
        return collected.macros;
    }
    catch (TeXFluxError error)
        throw new InternalError("internal error: bundled prelude is invalid: "
                ~ error.diagnostic);
    catch (UnicodeDecodeError error)
        throw new InternalError("internal error: bundled prelude is invalid: " ~ error.msg);
}

///
unittest
{
    auto standard = loadStandardMacros();
    foreach (name; ["before", "after", "around", "off", "drop"])
        assert(name in standard, "the bundled prelude defines the standard flow macros");
    assert(standard.length == 5, "and nothing else");
}

// ---------------------------------------------------------------------------
// Content imports
// ---------------------------------------------------------------------------

/// Replace each import with the canonical tree of its own instance.
private struct ImportResolver
{
    private CompilationSession session;
    private ModuleSource module_;
    private Flags flags;
    private string[] stack;
    private size_t depth;

    Block* block(Block* source)
    {
        auto descent = Descent(depth);
        Node[] nodes;
        foreach (node; source.nodes)
        {
            auto found = node.asSpecial;
            if (found.isNull || found.get.name != cast(string) Module.import_)
                // Raw TeX and the canonical nodes an inner import produced
                // hold no blocks, so they come back unchanged.
                nodes ~= mapChildren(node, &block);
            else
                nodes ~= expand(found.get);
        }
        return new Block(nodes, source.span);
    }

    private string cycleText(ModuleSource target)
    {
        auto chain = stack.map!(path => session.display(path)).array;
        return (chain ~ target.display).join(" -> ");
    }

    private Node[] expand(SpecialInvocation node)
    {
        if (node.suite !is null)
            throw new ModuleError("M022", "!import produces content: it does not accept"
                    ~ " a suite and cannot wrap a '>>' payload", node.span);

        Nullable!Argument pathGroup;
        Nullable!Argument bindingGroup;
        foreach (group; node.groups)
        {
            if (group.kind == GroupKind.binding)
                bindingGroup = group;
            else if (!requiredText(group).isNull)
            {
                if (!pathGroup.isNull)
                    throw new ModuleError("M023",
                            "!import requires exactly one '{path}' group", node.span);
                pathGroup = group;
            }
            else
                throw new ModuleError("M024",
                        "!import does not accept '[...]' or '<...>' groups", group.span);
        }
        if (pathGroup.isNull)
            throw new ModuleError("M025", "!import requires exactly one '{path}' group",
                    node.span);

        const display = resolveModulePath(module_.display,
                pathGroup.get.demandText("!import path"), ModuleKind.content,
                pathGroup.get.span);
        auto bindings = bindingGroup.isNull ? null : parseBindings(bindingGroup.get);

        Document imported;
        try
        {
            // Loading the target parses it, so a parse error in the callee is
            // inside the chain as much as a later validation error is.
            auto target = session.load(display, pathGroup.get.span);
            // The active import stack, not "ever seen": importing one module
            // twice is legal and simply produces two instances.
            if (stack.canFind(target.path))
                throw new ModuleError("M026", "content import cycle: " ~ cycleText(target),
                        node.span);
            imported = session.compileContent(target, bindings, flags, stack ~ target.path);
        }
        catch (TeXFluxError error)
        {
            // The span stays on the line that is actually broken, so inverse
            // search still reaches it, and the chain is appended to the
            // message. A binding is written here rather than there, and its
            // span says so already, so only an error from the callee names
            // this site.
            if (error.span.file == node.span.file)
                throw error;
            throw error.chained(error.message ~ "; imported from " ~ node.span.location,
                    RelatedLocation("imported from here", node.span));
        }
        return imported.body_.nodes;
    }
}

/**
 * Compile every import and splice the canonical tree it produces.
 *
 * This runs after conditionals are resolved, so a dropped payload's import is
 * never compiled and its file is never opened.
 */
Document resolveContentImports(Document document, CompilationSession session,
        ModuleSource module_, Flags flags, string[] stack)
{
    auto resolver = ImportResolver(session, module_, flags, stack);
    return Document(resolver.block(document.body_), document.span);
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

/**
 * One compilation: its source cache, its macro environments and its file list.
 *
 * Every entry point that compiles a document goes through one of these, so
 * that the standard macros and module resolution behave the same everywhere.
 */
final class CompilationSession
{
    private DirectiveRegistry registry;
    private SourceReader reader;
    private ModuleSource[string] sources;
    private LoadedSource[] readFiles;
    /// Each macro module's own macros, without anything it imported in turn.
    private OrderedMap!MacroEnvironment publicMacros;
    private MacroImport[][string] moduleImports;
    private MacroEnvironment[string] environments;
    /// Where each macro module was first imported from, for diagnostics.
    private SourceSpan[string] importedFrom;
    /**
     * The standard flow macros, read once and seeded into every environment.
     *
     * They are immutable, so one table serves them all and no module is
     * rewritten to contain a synthetic import of them.
     */
    private MacroEnvironment standard;

    this(DirectiveRegistry registry = builtinDirectives(), SourceReader reader = null)
    {
        this.registry = registry;
        this.reader = reader is null ? (string display) => readSource(display) : reader;
        this.standard = loadStandardMacros(registry);
        this.environments[preludeModule] = this.standard;
    }

    // -- sources -----------------------------------------------------------

    /// Every file this compilation read, the root first.
    LoadedSource[] loaded()
    {
        return readFiles;
    }

    /// The display spelling of one loaded module.
    string display(string path)
    {
        return sources[path].display;
    }

    private ModuleSource register(string display, immutable(ubyte)[] data, string text)
    {
        // Recorded before parsing: a diagnostic report lists every file the
        // compilation read, and the one whose parse failed most of all.
        readFiles ~= LoadedSource(display, absoluteNormalized(display), data);
        auto source = ModuleSource(normalizedPath(display), display, data,
                parse(text, display));
        sources[source.path] = source;
        return source;
    }

    /**
     * Read and parse one module, caching it by canonical path identity.
     *
     * A second spelling of the same file reuses the first load, so its spans
     * keep naming the module by the path that reached it first.
     */
    ModuleSource load(string display, SourceSpan span)
    {
        if (auto cached = normalizedPath(display) in sources)
            return *cached;

        immutable(ubyte)[] data;
        string text;
        string reason;
        try
        {
            data = reader(display);
            text = decodeUtf8(data);
        }
        catch (UnicodeDecodeError error)
            // A file that is there but is not UTF-8 says so in the decoder's
            // own words; anything else is the operating system's answer.
            reason = error.msg;
        catch (Exception error)
            reason = openFailure(error, absoluteNormalized(display));

        if (reason !is null)
            throw new ModuleError("M027",
                    "cannot read module '" ~ display ~ "': " ~ reason, span);
        return register(display, data, text);
    }

    // -- macro modules -----------------------------------------------------

    /**
     * Name every macro import between an error and the document.
     *
     * A macro module is shared between documents, so which imports pulled it
     * in is what locates the problem. Each module records the one import that
     * first named it, so walking those records is a walk up a tree and always
     * terminates. A level written at the error's own line adds nothing, so it
     * is stepped over rather than stopping the walk: the levels above it still
     * say how that file entered the build.
     */
    private void importing(string path, scope void delegate() body_)
    {
        try
            body_();
        catch (TeXFluxError error)
        {
            string message = error.message;
            RelatedLocation[] related;
            bool[string] seen;
            auto current = path;
            while (current !in seen)
            {
                seen[current] = true;
                auto site = current in importedFrom;
                if (site is null)
                    break;
                if (error.span.file != site.file)
                {
                    message = message ~ "; imported from " ~ site.location;
                    related ~= RelatedLocation("imported from here", *site);
                }
                current = normalizedPath(site.file);
            }
            if (related.length == 0)
                throw error;
            throw error.chained(message, related);
        }
    }

    /**
     * Load the macro-import closure of some roots, without recursing.
     *
     * A cycle is safe because a module already collected is skipped, and
     * because the environment built below reaches only one import deep. The
     * queue is first in, first out, so a module named by several importers
     * records the shallowest of them rather than the earliest in reading
     * order.
     */
    private void loadMacroModules(MacroImport[] roots)
    {
        auto pending = roots.dup;
        size_t at = 0;
        while (at < pending.length)
        {
            auto macroImport = pending[at++];
            if ((macroImport.path in publicMacros) !is null)
                continue;
            if (macroImport.path !in importedFrom)
                importedFrom[macroImport.path] = macroImport.span;

            MacroEnvironment own;
            MacroImport[] imports;
            importing(macroImport.path, {
                auto source = load(macroImport.display, macroImport.span);
                auto collected = collectMacroModule(source.document, source.display,
                        source.path, registry, standard.keys);
                own = collected.macros;
                imports = collected.imports;
                publicMacros[source.path] = own;
                moduleImports[source.path] = imports;
            });
            pending ~= imports;
        }
    }

    /**
     * Resolve every macro environment the closure of some roots needs.
     *
     * A module's environment is its own macros plus the public macros of its
     * direct imports, so it depends on nothing deeper and needs no recursion.
     * Every new environment is merged before any module is checked against its
     * own, so a name collision is always reported ahead of a template that
     * names something it cannot see. An environment never changes once built,
     * so each module is checked exactly once.
     */
    private void buildEnvironments(MacroImport[] roots)
    {
        loadMacroModules(roots);

        auto fresh = publicMacros.keys.filter!(path => path !in environments).array;

        foreach (path; fresh)
            importing(path, {
                auto base = standard.dup;
                foreach (name, ref definition; publicMacros[path])
                    base[name] = definition;
                environments[path] = mergeImports(base, moduleImports[path], publicMacros);
            });
        foreach (path; fresh)
            importing(path, { checkSelfContained(path); });
    }

    /**
     * Reject a macro module that depends on a name it cannot see itself.
     *
     * A caller's unrelated namespace must never be what makes an otherwise
     * invalid macro module valid.
     */
    private void checkSelfContained(string path)
    {
        auto environment = environments[path];
        foreach (name, ref definition; publicMacros[path])
            foreach (special; definition.template_.specials)
                if (!templateNames.canFind(special.name) && (special.name in registry) is null
                        && (special.name in environment) is null)
                    throw new ModuleError("M028", "'!" ~ special.name
                            ~ "' is not defined in " ~ definition.span.file
                            ~ " and is not available through its own !macroimport",
                            special.span);
    }

    // -- content modules ---------------------------------------------------

    /// Compile the document a caller supplied as text.
    Document compileRoot(string text, string filename, immutable(ubyte)[] data,
            Flags flags = Flags.init)
    {
        auto source = register(filename, data, text);
        return compile(source, flags, true, null, Flags.init, [source.path]);
    }

    /// Compile one imported module as its own instance.
    Document compileContent(ModuleSource source, FlagBinding[] bindings, Flags callerFlags,
            string[] stack)
    {
        return compile(source, Flags.init, false, bindings, callerFlags, stack);
    }

    /**
     * One module, through every pass in order.
     *
     * An imported module never receives the command line's overrides: a
     * binding list is how a caller reaches its flags, and it names each one.
     */
    private Document compile(ModuleSource source, Flags overrides, bool isRoot,
            FlagBinding[] bindings, Flags callerFlags, string[] stack)
    {
        auto document = source.document;
        validateMacroForms(document);
        validateFlagForms(document);
        validateMacroImportForms(document);
        document = desugar(document);

        auto collected = collectFlags(document, isRoot ? overrides : Flags.init);
        document = collected.document;
        auto resolved = collected.flags;
        if (bindings.length != 0)
            resolved = bindImportFlags(resolved, bindings, callerFlags, source.display);

        auto stripped = resolveMacroImports(document, source.display);
        document = stripped.document;
        buildEnvironments(stripped.imports);

        auto imported = mergeImports(standard, stripped.imports, publicMacros);
        auto macros = collectMacros(document, registry.keys, imported, source.path,
                standard.keys);
        environments[source.path] = macros.macros;

        document = expandMacros(macros.document, macros.macros, resolved, environments,
                source.path, true);
        document = resolveContentImports(document, this, source, resolved, stack);
        return canonicalize(document, registry);
    }
}
