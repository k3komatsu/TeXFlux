/**
 * Deciding what each invocation's values are, and checking the result.
 *
 * This is the boundary between the two trees. A command's suite becomes its
 * arguments, an environment's becomes its body, an anonymous container
 * disappears into its contents, and a special hands off to its handler. What
 * comes out holds only raw TeX, invocations and literal brace groups, and the
 * check at the end says so out loud: the renderer is written against that
 * shape and is entitled to assume it.
 *
 * Flow control is not here. `!before`, `!after`, `!around`, `!off` and `!drop`
 * are ordinary source macros defined in the bundled prelude and expanded away
 * before this pass, so the registry below holds nothing but the two module
 * constructs, which are registered only so that they can name themselves for a
 * caller that bypassed the compilation session.
 */
module texflux.canonical;

import std.algorithm : all, map;
import std.array : array, join;
import std.sumtype : match;
import std.typecons : Nullable;

import texflux.ast;
import texflux.errors : CompilerDefect, Descent, DirectiveError, ModuleError,
    ValidationError;
import texflux.syntax : mapBlocks, requiredText, sequenceEntries;

/**
 * What one special expands to.
 *
 * A handler is a tree-to-tree transformation and is given nothing but the node
 * it was called on. Neither of the two built-ins needs more than that, and a
 * handler that did would be a reason to decide what it may see rather than to
 * hand it everything in advance.
 */
alias SpecialHandler = Node[] delegate(SpecialInvocation node);

/// The in-process special registry: one handler per name.
alias DirectiveRegistry = SpecialHandler[string];

/**
 * Every built-in special: the two module constructs and nothing else.
 *
 * A reusable operation that a source macro can express does not get an entry
 * here. Registering these two also reserves both names against `!defmacro`.
 */
DirectiveRegistry builtinDirectives()
{
    return ["import": moduleGuard("import"), "macroimport": moduleGuard("macroimport")];
}

/**
 * Reject a module construct that reached the import-free pipeline.
 *
 * The low-level normalization has no source file to resolve an import
 * against, so the two module constructs are registered only to name
 * themselves here.
 */
private SpecialHandler moduleGuard(string name)
{
    return delegate(SpecialInvocation node) {
        throw new ModuleError("M029", "'!" ~ name ~ "' requires module compilation;"
                ~ " use texflux.compile_with_map or the texflux CLI", node.span);
    };
}

/**
 * Turn one expanded syntax tree into a validated canonical tree.
 *
 * This is the tail that the import-free pipeline shares with the module one,
 * which splices imported canonical trees in between expansion and this pass.
 */
Document canonicalize(Document document, DirectiveRegistry registry)
{
    auto normalizer = Normalizer(registry);
    auto body_ = normalizer.block(document.body_);
    assertCanonical(body_);
    return Document(body_, document.span);
}

private struct Normalizer
{
    private DirectiveRegistry registry;
    private size_t depth;

    this(DirectiveRegistry registry)
    {
        this.registry = registry;
    }

    Block* block(Block* source)
    {
        auto descent = Descent(depth);
        return new Block(source.nodes.map!(node => normalize(node)).join, source.span);
    }

    private Node[] normalize(Node node)
    {
        return node.match!(
            (RawTex raw) => [Node(raw)],
            (ParsedInvocation invocation) => invocation_(invocation),
            (SpecialInvocation invocation) => special(invocation),
            (SequenceEntry entry) {
                throw new ValidationError("V035",
                        "sequence entries are only valid inside a ':::' suite", entry.span);
                return Node[].init;
            },
            (Stack composition) {
                throw new CompilerDefect("normalization requires a desugared syntax AST");
                return Node[].init;
            },
            (GenericInvocation invocation) => [canonical(Node(invocation))],
            (BraceGroup group) => [canonical(Node(group))],
        );
    }

    /// A node that is already canonical, with the trees inside it normalized.
    private Node canonical(Node node)
    {
        return node.match!(
            (RawTex raw) => Node(raw),
            (GenericInvocation invocation) {
                invocation.arguments = headerArguments(invocation.arguments);
                invocation.body_ = invocation.body_ is null ? null : block(invocation.body_);
                return Node(invocation);
            },
            (BraceGroup group) {
                group.body_ = block(group.body_);
                return Node(group);
            },
            (n) {
                throw new CompilerDefect("unsupported canonical node");
                return Node.init;
            },
        );
    }

    /// Normalize each header group, leaving inline raw text untouched.
    private Argument[] headerArguments(Argument[] arguments)
    {
        return mapBlocks(arguments, &block);
    }

    /// A suite mode, applying the block default to a hand-built node.
    private static SuiteMode suiteMode(Nullable!SuiteMode mode)
    {
        return mode.isNull ? SuiteMode.block : mode.get;
    }

    private Argument argumentFromEntry(SequenceEntry entry)
    {
        auto value = ArgumentValue(block(entry.value));
        if (!entry.argumentKind.isNull)
            return Argument(entry.argumentKind.get, value, ArgumentLayout.explicit, entry.span);
        // A '-' always generates one required group, laid out the one way a
        // generated group is laid out. The value's shape decides nothing.
        return Argument(GroupKind.required, value, ArgumentLayout.block, entry.span);
    }

    /// Concatenate every generated-value entry of a sequence suite.
    private Block* sequenceBody(Block* suite)
    {
        Node[] nodes;
        foreach (entry; suite.sequenceEntries)
        {
            if (!entry.argumentKind.isNull)
                throw new ValidationError("V036",
                        "anonymous containers do not accept '+' sequence entries", entry.span);
            nodes ~= block(entry.value).nodes;
        }
        return new Block(nodes, suite.span);
    }

    /// Flatten a container suite; either mode contributes one block value.
    private Block* containerBody(ParsedInvocation node, Block* suite)
    {
        return suiteMode(node.suiteMode) == SuiteMode.block ? block(suite) : sequenceBody(suite);
    }

    /// Convert a command suite into its required block arguments.
    private Argument[] suiteArguments(ParsedInvocation node, Block* suite)
    {
        if (suiteMode(node.suiteMode) == SuiteMode.sequence)
            return suite.sequenceEntries.map!(entry => argumentFromEntry(entry)).array;
        return [
            Argument(GroupKind.required, ArgumentValue(block(suite)), ArgumentLayout.block,
                    node.suiteSpan.isNull ? node.span : node.suiteSpan.get)
        ];
    }

    /// A block suite is the environment's body; a sequence suite ends with it.
    private Node environment(ParsedInvocation node, Argument[] arguments, Block* suite)
    {
        Block* body_;
        if (suiteMode(node.suiteMode) == SuiteMode.block)
            body_ = block(suite);
        else
        {
            auto entries = suite.sequenceEntries;
            if (entries.length == 0)
                throw new ValidationError("V037",
                        "environment sequence suites require a body value", node.span);
            if (!entries[$ - 1].argumentKind.isNull)
                throw new ValidationError("V038",
                        "environment sequence suites require the final '-' entry to be the body",
                        entries[$ - 1].span);
            foreach (entry; entries[0 .. $ - 1])
                arguments ~= argumentFromEntry(entry);
            body_ = block(entries[$ - 1].value);
        }
        return Node(GenericInvocation(node.name, arguments, body_, node.span));
    }

    private Node[] invocation_(ParsedInvocation node)
    {
        auto arguments = headerArguments(node.groups);
        if (node.suite is null)
        {
            if (node.kind == InvocationKind.command)
                return [Node(GenericInvocation(node.name, arguments, null, node.span))];
            throw new ValidationError("V039",
                    "container values require a suite or a closed stack payload", node.span);
        }

        auto suite = node.suite;
        final switch (node.kind)
        {
        case InvocationKind.command:
            return [
                Node(GenericInvocation(node.name, arguments ~ suiteArguments(node, suite),
                        null, node.span))
            ];

        case InvocationKind.environment:
            return [environment(node, arguments, suite)];

        case InvocationKind.brace:
            auto header = braceHeader(node);
            return [
                Node(braceGroup(containerBody(node, suite), node.span, header,
                        node.groups[0].parts))
            ];

        case InvocationKind.transparent:
            if (node.groups.length != 0)
                throw new ValidationError("V041",
                        "transparent containers do not accept header groups", node.span);
            return containerBody(node, suite).nodes;
        }
    }

    /// The one raw header group a literal brace container is written with.
    private static string braceHeader(ParsedInvocation node)
    {
        if (node.groups.length == 1)
        {
            const text = requiredText(node.groups[0]);
            if (!text.isNull)
                return text.get;
        }
        throw new ValidationError("V040",
                "literal brace containers require one raw header group", node.span);
    }

    private Node[] special(SpecialInvocation node)
    {
        auto handler = node.name in registry;
        if (handler is null)
            throw new DirectiveError("D001", "unknown special directive '!" ~ node.name
                    ~ "'; a literal '!' line is written '!!', and a run of them goes"
                    ~ " in a raw-mode region", node.span);
        auto produced = (*handler)(node);
        if (!produced.all!isCanonical)
            throw new CompilerDefect("special directive handlers must return canonical nodes");
        return produced.map!(item => canonical(item)).array;
    }
}

/**
 * Refuse a tree that still holds something the renderer cannot read.
 *
 * The renderer is written against three node kinds and is entitled to assume
 * it will see no others, so the assumption is checked once, here, rather than
 * guessed at in each of the places that would otherwise have to.
 */
private void assertCanonical(Block* block)
{
    foreach (node; block.nodes)
        node.match!(
            (RawTex _) {},
            (BraceGroup group) { assertCanonical(group.body_); },
            (GenericInvocation invocation) {
                foreach (argument; invocation.arguments)
                    argument.value.match!(
                        (Block* nested) { assertCanonical(nested); },
                        (string _) {},
                    );
                if (invocation.body_ !is null)
                    assertCanonical(invocation.body_);
            },
            (n) {
                throw new CompilerDefect(
                        "normalization produced a node the renderer cannot read");
            },
        );
}
