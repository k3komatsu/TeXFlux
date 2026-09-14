"""Modules: one file is one module, composed in two different ways.

A ``.tfx`` file is one independent content module. ``!import`` compiles it as
its own instance -- its own build flags, its own macros -- and splices the
resulting canonical AST into the caller. Nothing else crosses: the callee's
flags and macro names never enter the caller's tables.

A ``.tfxm`` file is one macro-definition module. ``!macroimport`` loads the
macros that module defines itself and nothing it imported in turn, so a macro
module's own dependencies stay private. Definitions keep a lexical scope, so a
template reaches its own module's imports rather than its caller's namespace.
That is what lets two content modules depend on different versions of the same
macro library in one document.

Module resolution is a compiler-session concern. The renderer, the provenance
fragments and SyncTeX remapping all stay downstream of it and know nothing
about module boundaries.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
import importlib.resources
import os
import re
from typing import Final, TypeAlias

from .ast import (
    Argument,
    Block,
    Document,
    GroupKind,
    Node,
    ParsedInvocation,
    RawTex,
    SourcePosition,
    SourceSpan,
    SpecialInvocation,
    SequenceEntry,
)
from .errors import (
    InternalError,
    ModuleError,
    RelatedLocation,
    TeXFluxError,
)
from .flags import (
    FLAG_NAME_PATTERN,
    FLAG_VALUES,
    Conditional,
    Flags,
    collect_flags,
    declared_flags_hint,
    validate_flag_forms,
)
from .macros import (
    MacroDefinition,
    Reserved,
    collect_macros,
    expand_macros,
    validate_macro_forms,
)
from .normalize import (
    BUILTIN_DIRECTIVES,
    DirectiveRegistry,
    canonicalize,
    desugar,
)
from .parser import parse
from .paths import normalized_path
from .render import LoadedSource
from .syntax import demand_text, required_text, stacks, walk


class ModuleKind(StrEnum):
    """A module's kind, which its file extension alone decides."""

    CONTENT = ".tfx"
    MACRO = ".tfxm"


class Module(StrEnum):
    """Special names that belong to the module system itself."""

    IMPORT = "import"
    MACROIMPORT = "macroimport"


#: The macro names one module makes visible, as a template sees them.
MacroEnvironment: TypeAlias = Mapping[str, MacroDefinition]

#: The bundled standard flow macros, and the synthetic identity they are
#: loaded under. The identity is what their lexical scope and their spans are
#: keyed by, so the installed package path never becomes language semantics.
PRELUDE_MODULE: Final = "texflux:prelude"
_PRELUDE_RESOURCE: Final = "prelude.tfxm"

#: What a ``.tfxm`` may state at its top level, beside comments and blanks.
_MACRO_MODULE_STATEMENTS: Final = frozenset({Reserved.DEFINE, Module.MACROIMPORT})

#: What a ``.tfxm`` may not contain anywhere, template interiors included. A
#: macro module declares no flags, so a conditional inside one would read the
#: flags of whichever content module instantiated it.
_MACRO_MODULE_FORBIDDEN: Final = frozenset(
    {Conditional.DECLARE, Conditional.WHEN, Conditional.UNLESS, Module.IMPORT}
)

#: Template-only constructs, which need no definition to be valid.
_TEMPLATE_NAMES: Final = frozenset({Reserved.PARAM, Reserved.EACH, Reserved.TEXT})

# A binding is written on one header line, so it stays as flat as the flag
# names and the two literals it joins. Both names are flag names, so the
# shape comes from the flag system rather than being spelled again here.
_BINDING_RE: Final = re.compile(
    f"({FLAG_NAME_PATTERN})[ ]*=[ ]*(on|off|\\${FLAG_NAME_PATTERN})"
)


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


#: Reads one module's bytes, given the spelling ``load`` was asked for. An
#: editor supplies its own so an unsaved buffer is compiled in place of the
#: file on disk. A reader signals "no such module" with ``OSError``, which is
#: what ``load`` already turns into a spanned diagnostic.
SourceReader: TypeAlias = Callable[[str], bytes]


def read_source(display: str) -> bytes:
    """The default reader: whatever is on disk at ``display``."""

    with open(os.path.abspath(display), "rb") as stream:
        return stream.read()


@dataclass(frozen=True, slots=True)
class ModuleSource:
    """One loaded file, cached by canonical path identity."""

    path: str            # canonical identity
    display: str         # the spelling spans and diagnostics use
    kind: ModuleKind
    data: bytes          # file bytes, for the source map's digest
    document: Document   # parsed syntax AST, before desugaring


@dataclass(frozen=True, slots=True)
class MacroImport:
    """One resolved ``!macroimport``."""

    path: str
    display: str
    span: SourceSpan     # the path group, which is what an author would fix


def resolve_module_path(
    importer: str,
    written: str,
    kind: ModuleKind,
    span: SourceSpan,
) -> str:
    """Resolve one written import path against the file that writes it.

    Resolution never depends on which parent imported that file, which is
    what makes a reusable component portable.
    """

    if not written:
        raise ModuleError("module path must not be empty", span, code="M001")
    if "\0" in written:
        # The OS refuses to even stat such a path, raising ValueError rather
        # than OSError, so it is rejected here for both constructs at once
        # instead of leaking out of whichever one happens to look first.
        raise ModuleError(
            "module path must not contain a NUL character",
            span,
            code="M002",
        )
    if "\\" in written:
        raise ModuleError("module paths use '/' separators", span, code="M003")
    if os.path.isabs(written.replace("/", os.sep)):
        raise ModuleError(
            "module paths must be relative to the importing file",
            span,
            code="M004",
        )
    if not written.endswith(kind.value):
        construct = (
            Module.IMPORT if kind is ModuleKind.CONTENT else Module.MACROIMPORT
        )
        raise ModuleError(
            f"!{construct} requires a '{kind.value}' module; got '{written}'",
            span,
            code="M005",
        )
    return os.path.normpath(
        os.path.join(os.path.dirname(importer), *written.split("/"))
    )


# --------------------------------------------------------------------------
# Flag bindings
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlagBinding:
    """One ``name=value`` entry of an ``!import`` binding list."""

    name: str
    #: ``True``/``False`` for ``on``/``off``; ``None`` while forwarding.
    literal: bool | None
    #: The caller flag ``$name`` forwards; ``None`` for a literal.
    caller: str | None
    span: SourceSpan
    name_span: SourceSpan
    value_span: SourceSpan


def _sub_span(argument: Argument, start: int, end: int) -> SourceSpan:
    """The span of ``content[start:end]`` inside one ``(...)`` list.

    A header is one physical line, so only the column moves. The offset skips
    the ``(`` the group's own span starts at.
    """

    line = argument.span.start.line
    column = argument.span.start.column + 1
    return SourceSpan(
        argument.span.file,
        SourcePosition(line, column + start),
        SourcePosition(line, column + end),
    )


def parse_bindings(argument: Argument) -> tuple[FlagBinding, ...]:
    """Read one ``(...)`` list into its bindings.

    Neither a comma nor a parenthesis can occur inside a binding value, so
    splitting on commas is exact and needs no scanner of its own.
    """

    text = argument.value
    if not isinstance(text, str):
        raise ModuleError(
            "!import binding list must be inline text",
            argument.span,
            code="M006",
        )
    if not text.strip(" "):
        raise ModuleError(
            "!import binding list is empty; omit '(...)' instead",
            argument.span,
            code="M007",
        )

    bindings: list[FlagBinding] = []
    start = 0
    while True:
        comma = text.find(",", start)
        stop = len(text) if comma < 0 else comma

        first, last = start, stop
        while first < last and text[first] == " ":
            first += 1
        while last > first and text[last - 1] == " ":
            last -= 1

        chunk = text[first:last]
        match = _BINDING_RE.fullmatch(chunk)
        if match is None:
            raise ModuleError(
                "!import bindings are written 'flag=on', 'flag=off' or "
                f"'flag=$callerFlag'; got '{chunk}'",
                _sub_span(argument, first, max(last, first + 1)),
                code="M008",
            )
        name, value = match.group(1), match.group(2)
        forwarded = value.startswith("$")
        bindings.append(
            FlagBinding(
                name,
                None if forwarded else FLAG_VALUES[value],
                value[1:] if forwarded else None,
                _sub_span(argument, first, last),
                _sub_span(argument, first, first + len(name)),
                _sub_span(argument, last - len(value), last),
            )
        )
        if comma < 0:
            return tuple(bindings)
        start = comma + 1


def bind_import_flags(
    declared: Mapping[str, bool],
    bindings: Sequence[FlagBinding],
    caller_flags: Flags,
    *,
    module: str,
) -> dict[str, bool]:
    """Resolve an imported module's flags from its defaults and one binding list.

    An unbound flag keeps the callee's own default. There is no same-name
    inheritance: a caller flag reaches a callee only through ``$name``.
    """

    resolved = dict(declared)
    bound: dict[str, SourceSpan] = {}
    for binding in bindings:
        if binding.name not in declared:
            raise ModuleError(
                f"imported module '{module}' does not declare build flag "
                f"'{binding.name}'; {declared_flags_hint(declared)}",
                binding.name_span,
                code="M009",
            )
        if binding.name in bound:
            raise ModuleError(
                f"build flag '{binding.name}' is bound twice; first bound at "
                f"{bound[binding.name].location}",
                binding.name_span,
                code="M010",
                related=(
                    RelatedLocation(
                        "first bound here",
                        bound[binding.name],
                    ),
                ),
            )
        if binding.caller is not None and binding.caller not in caller_flags:
            raise ModuleError(
                f"unknown build flag '{binding.caller}' in this module; "
                f"{declared_flags_hint(caller_flags)}",
                binding.value_span,
                code="M011",
            )
        bound[binding.name] = binding.name_span
        resolved[binding.name] = (
            binding.literal
            if binding.caller is None
            else caller_flags[binding.caller]
        )
    return resolved


# --------------------------------------------------------------------------
# Form validation
# --------------------------------------------------------------------------


def validate_macroimport_forms(document: Document) -> None:
    """Reject a ``!macroimport`` written as a ``>>`` segment.

    Like ``!flag``, a macro import is a module-level declaration resolved
    before conditionals are, so it must not be composable into one: a
    flag-dependent macro namespace would defeat lexical scope and caching.
    """

    for stack in stacks(document.body):
        for segment in stack.segments:
            if (
                isinstance(segment, SpecialInvocation)
                and segment.name == Module.MACROIMPORT
            ):
                raise ModuleError(
                    "!macroimport must be a top-level declaration and cannot "
                    "be a '>>' segment",
                    segment.span,
                    code="M012",
                )


def validate_macro_module_purity(document: Document) -> None:
    """Keep a ``.tfxm`` a pure declarative macro module.

    Purity is what makes macro imports order-insensitive, cacheable, safe in
    a cyclic graph, and independent of build flags.
    """

    for child in walk(document.body):
        if (
            isinstance(child, SpecialInvocation)
            and child.name in _MACRO_MODULE_FORBIDDEN
        ):
            raise ModuleError(
                f"'!{child.name}' is not allowed in a .tfxm macro module",
                child.span,
                code="M013",
            )

    for node in document.body.nodes:
        match node:
            case RawTex(text=text) if (
                not text.strip() or text.lstrip().startswith("%")
            ):
                continue
            case SpecialInvocation(name=name) if name in _MACRO_MODULE_STATEMENTS:
                continue
            case _:
                raise ModuleError(
                    "a .tfxm macro module may contain only !defmacro, "
                    "!macroimport, comment lines and blank lines",
                    node.span,
                    code="M014",
                )


# --------------------------------------------------------------------------
# Macro imports
# --------------------------------------------------------------------------


def _macro_import(
    node: SpecialInvocation,
    seen: dict[str, SourceSpan],
    importer: str,
) -> MacroImport:
    if node.suite is not None:
        raise ModuleError(
            "!macroimport does not accept a suite",
            node.span,
            code="M015",
        )
    binding = next(
        (group for group in node.groups if group.kind is GroupKind.BINDING),
        None,
    )
    if binding is not None:
        raise ModuleError(
            "!macroimport does not accept a '(...)' list",
            binding.span,
            code="M016",
        )
    if len(node.groups) != 1:
        raise ModuleError(
            "!macroimport requires one '{path}' group",
            node.span,
            code="M017",
        )
    group = node.groups[0]
    if required_text(group) is None:
        raise ModuleError(
            "!macroimport path must be a required '{...}' group",
            group.span,
            code="M018",
        )

    written = demand_text(group, "!macroimport path")
    display = resolve_module_path(importer, written, ModuleKind.MACRO, group.span)
    path = normalized_path(display)
    if path in seen:
        raise ModuleError(
            f"macro module '{display}' is already imported at "
            f"{seen[path].location}",
            group.span,
            code="M019",
            related=(RelatedLocation("first imported here", seen[path]),),
        )
    seen[path] = group.span
    return MacroImport(path, display, group.span)


def resolve_macro_imports(
    document: Document,
    importer: str,
) -> tuple[Document, tuple[MacroImport, ...]]:
    """Strip top-level ``!macroimport`` declarations and resolve their paths."""

    seen: dict[str, SourceSpan] = {}
    imports: list[MacroImport] = []
    nodes: list[Node] = []
    for node in document.body.nodes:
        if (
            isinstance(node, SpecialInvocation)
            and node.name == Module.MACROIMPORT
        ):
            imports.append(_macro_import(node, seen, importer))
            continue
        nodes.append(node)

    body = replace(document.body, nodes=tuple(nodes))
    # Every top-level declaration is gone, so anything left is nested.
    misplaced = next(
        (
            child
            for child in walk(body)
            if isinstance(child, SpecialInvocation)
            and child.name == Module.MACROIMPORT
        ),
        None,
    )
    if misplaced is not None:
        raise ModuleError(
            "!macroimport is only valid at the top level",
            misplaced.span,
            code="M020",
        )
    return replace(document, body=body), tuple(imports)


def merge_imports(
    base: Mapping[str, MacroDefinition],
    imports: Sequence[MacroImport],
    public: Mapping[str, Mapping[str, MacroDefinition]],
) -> dict[str, MacroDefinition]:
    """Union one module's own macros with the public macros it imports.

    Shadowing is not a rule here: a duplicate visible name is an error, so
    there is no 'last import wins' for an author to reason about.
    """

    environment = dict(base)
    for macro_import in imports:
        for name, definition in public[macro_import.path].items():
            if name in environment:
                raise ModuleError(
                    f"macro '!{name}' is already available here, defined at "
                    f"{environment[name].span.location}",
                    macro_import.span,
                    code="M021",
                    related=(
                        RelatedLocation(
                            "defined here",
                            environment[name].span,
                        ),
                    ),
                )
            environment[name] = definition
    return environment


def load_standard_macros(
    registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
) -> dict[str, MacroDefinition]:
    """Collect the compiler-bundled standard flow macros.

    The module ships inside the TeXFlux package and is versioned with it, so
    it is read as a package resource and never resolved against the user's
    project. It is an ordinary pure ``.tfxm`` and is validated as one; a
    failure here is an installation or compiler defect rather than anything
    a document wrote, so it is not a ``TeXFluxError``.
    """

    try:
        text = (
            importlib.resources.files(__package__)
            .joinpath(_PRELUDE_RESOURCE)
            .read_text(encoding="utf-8")
        )
        document = parse(text, PRELUDE_MODULE)
        validate_macro_forms(document)
        validate_macroimport_forms(document)
        validate_macro_module_purity(document)
        document, imports = resolve_macro_imports(desugar(document), PRELUDE_MODULE)
        if imports:
            raise ModuleError(
                "the bundled standard macro module imports no other module",
                imports[0].span,
                code="M022",
            )
        _, macros = collect_macros(document, registry, module=PRELUDE_MODULE)
    except (TeXFluxError, OSError, UnicodeError) as error:
        raise InternalError(
            f"internal error: bundled prelude is invalid: {error}"
        ) from error
    return macros


# --------------------------------------------------------------------------
# Content imports
# --------------------------------------------------------------------------


class _ImportResolver:
    """Replace each ``!import`` with the canonical AST of its own instance."""

    def __init__(
        self,
        session: "CompilationSession",
        module: ModuleSource,
        flags: Flags,
        stack: tuple[str, ...],
    ):
        self._session = session
        self._module = module
        self._flags = flags
        self._stack = stack

    def block(self, block: Block) -> Block:
        nodes: list[Node] = []
        for node in block.nodes:
            nodes.extend(self.node(node))
        return replace(block, nodes=tuple(nodes))

    def node(self, node: Node) -> tuple[Node, ...]:
        match node:
            case SpecialInvocation(name=Module.IMPORT):
                return self._expand(node)
            case ParsedInvocation() | SpecialInvocation():
                return (
                    replace(
                        node,
                        groups=tuple(
                            self._argument(group) for group in node.groups
                        ),
                        suite=(
                            None if node.suite is None else self.block(node.suite)
                        ),
                    ),
                )
            case SequenceEntry():
                return (replace(node, value=self.block(node.value)),)
            case _:
                # Raw TeX, and the canonical nodes an inner import produced.
                return (node,)

    def _argument(self, argument: Argument) -> Argument:
        if isinstance(argument.value, Block):
            return replace(argument, value=self.block(argument.value))
        return argument

    def _cycle(self, target: ModuleSource) -> str:
        chain = [self._session.display(path) for path in self._stack]
        return " -> ".join(chain + [target.display])

    def _expand(self, node: SpecialInvocation) -> tuple[Node, ...]:
        if node.suite is not None:
            raise ModuleError(
                "!import produces content: it does not accept a suite and "
                "cannot wrap a '>>' payload",
                node.span,
                code="M023",
            )

        path_group: Argument | None = None
        binding_group: Argument | None = None
        for group in node.groups:
            if group.kind is GroupKind.BINDING:
                binding_group = group
            elif required_text(group) is not None:
                if path_group is not None:
                    raise ModuleError(
                        "!import requires exactly one '{path}' group",
                        node.span,
                        code="M024",
                    )
                path_group = group
            else:
                raise ModuleError(
                    "!import does not accept '[...]' or '<...>' groups",
                    group.span,
                    code="M025",
                )
        if path_group is None:
            raise ModuleError(
                "!import requires exactly one '{path}' group",
                node.span,
                code="M026",
            )

        display = resolve_module_path(
            self._module.display,
            demand_text(path_group, "!import path"),
            ModuleKind.CONTENT,
            path_group.span,
        )
        bindings = (
            () if binding_group is None else parse_bindings(binding_group)
        )
        try:
            # Loading the target parses it, so a parse error in the callee has
            # to be inside the chain as much as a later validation error is.
            target = self._session.load(
                display,
                ModuleKind.CONTENT,
                path_group.span,
            )
            # The active import stack, not 'ever seen': importing one module
            # twice is legal and simply produces two instances.
            if target.path in self._stack:
                raise ModuleError(
                    "content import cycle: " + self._cycle(target),
                    node.span,
                    code="M027",
                )
            imported = self._session.compile_content(
                target,
                bindings=bindings,
                caller_flags=self._flags,
                stack=self._stack + (target.path,),
            )
        except TeXFluxError as error:
            # The span stays on the line that is actually broken, so inverse
            # search still reaches it; the chain is appended to the message.
            # A binding is written here rather than there, and its span says
            # so already, so only an error from the callee names this site.
            if error.span.file == node.span.file:
                raise
            raise error.chained(
                f"{error.message}; imported from {node.span.location}",
                RelatedLocation("imported from here", node.span),
            ) from error
        return imported.body.nodes


def resolve_content_imports(
    document: Document,
    session: "CompilationSession",
    *,
    module: ModuleSource,
    flags: Flags,
    stack: tuple[str, ...],
) -> Document:
    """Compile every ``!import`` and splice the canonical AST it produces.

    This runs after conditionals are resolved, so a dropped payload's import
    is never compiled and its file is never opened.
    """

    resolver = _ImportResolver(session, module, flags, stack)
    return replace(document, body=resolver.block(document.body))


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


class CompilationSession:
    """One compilation: its source cache, macro environments and file list."""

    def __init__(
        self,
        registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
        *,
        reader: SourceReader | None = None,
    ):
        self._registry = registry
        self._reader = read_source if reader is None else reader
        self._sources: dict[str, ModuleSource] = {}
        self._loaded: list[LoadedSource] = []
        self._public: dict[str, dict[str, MacroDefinition]] = {}
        self._imports: dict[str, tuple[MacroImport, ...]] = {}
        self._environments: dict[str, MacroEnvironment] = {}
        #: Where each macro module was first imported from, for diagnostics.
        self._imported_from: dict[str, SourceSpan] = {}
        #: The standard flow macros, read once and seeded into every module
        #: environment below. They are immutable, so one table serves them
        #: all; no module is rewritten to contain a synthetic import.
        self._standard = load_standard_macros(registry)
        self._environments[PRELUDE_MODULE] = self._standard

    # -- sources -----------------------------------------------------------

    def loaded(self) -> tuple[LoadedSource, ...]:
        """Every file this compilation read, the root first."""

        return tuple(self._loaded)

    def display(self, path: str) -> str:
        """The display spelling of one loaded module."""

        return self._sources[path].display

    def _register(
        self,
        display: str,
        kind: ModuleKind,
        data: bytes,
        text: str,
    ) -> ModuleSource:
        # Recorded before parsing: a diagnostic report lists every file the
        # compilation read, and the one whose parse failed most of all.
        self._loaded.append(
            LoadedSource(display, os.path.abspath(display), data)
        )
        source = ModuleSource(
            normalized_path(display),
            display,
            kind,
            data,
            parse(text, display),
        )
        self._sources[source.path] = source
        return source

    def load(
        self,
        display: str,
        kind: ModuleKind,
        span: SourceSpan,
    ) -> ModuleSource:
        """Read and parse one module, caching it by canonical path identity.

        A second spelling of the same file reuses the first load, so its spans
        keep naming the module by the path that reached it first.
        """

        try:
            cached = self._sources.get(normalized_path(display))
            if cached is not None:
                return cached
            data = self._reader(display)
            text = data.decode("utf-8")
        except (OSError, UnicodeError, ValueError) as error:
            # A NUL byte makes a path the OS refuses to even look at, which
            # arrives as ValueError; without it that escapes unspanned.
            raise ModuleError(
                f"cannot read module '{display}': {error}",
                span,
                code="M028",
            ) from error
        return self._register(display, kind, data, text)

    # -- macro modules -----------------------------------------------------

    def _imported(self, error: TeXFluxError, path: str) -> TeXFluxError:
        """Name every ``!macroimport`` between this error and the document.

        A macro module is shared between decks, so which imports pulled it in
        is what locates the problem. Each module records the one import that
        first named it, so walking those records is a walk up a tree and
        always terminates. A level written at the error's own line adds
        nothing, so it is stepped over rather than stopping the walk: the
        levels above it still say how that file entered the build.
        """

        message = error.message
        related: list[RelatedLocation] = []
        seen: set[str] = set()
        current = path
        while current not in seen:
            seen.add(current)
            site = self._imported_from.get(current)
            if site is None:
                break
            if error.span.file != site.file:
                message = f"{message}; imported from {site.location}"
                related.append(RelatedLocation("imported from here", site))
            current = normalized_path(site.file)
        # Returning the error itself when nothing was added keeps the caller's
        # bare re-raise, and with it the original exception as __cause__.
        if message == error.message:
            return error
        return error.chained(message, *related)

    def _load_macro_modules(self, roots: Sequence[MacroImport]) -> None:
        """Load the macro-import closure of ``roots``, without recursing.

        A cycle is safe because a module already collected is skipped, and
        because the environment built below reaches only one import deep.
        The queue is first-in-first-out, so a module named by several
        importers records the shallowest of them, which is not necessarily
        the earliest in reading order.
        """

        pending = deque(roots)
        while pending:
            macro_import = pending.popleft()
            if macro_import.path in self._public:
                continue
            self._imported_from.setdefault(macro_import.path, macro_import.span)
            try:
                source = self.load(
                    macro_import.display,
                    ModuleKind.MACRO,
                    macro_import.span,
                )
                document = source.document
                validate_macro_forms(document)
                validate_macroimport_forms(document)
                validate_macro_module_purity(document)
                document, imports = resolve_macro_imports(
                    desugar(document),
                    source.display,
                )
                _, own = collect_macros(
                    document,
                    self._registry,
                    module=source.path,
                    standard=self._standard,
                )
            except TeXFluxError as error:
                chained = self._imported(error, macro_import.path)
                if chained is error:
                    raise
                raise chained from error
            self._public[source.path] = own
            self._imports[source.path] = imports
            pending.extend(imports)

    def _build_environments(self, roots: Sequence[MacroImport]) -> None:
        """Resolve every macro environment the closure of ``roots`` needs.

        A module's environment is its own macros plus the public macros of its
        direct imports, so it depends on nothing deeper and needs no recursion.
        """

        self._load_macro_modules(roots)
        for path, own in self._public.items():
            if path in self._environments:
                continue
            try:
                self._environments[path] = merge_imports(
                    {**self._standard, **own},
                    self._imports[path],
                    self._public,
                )
            except TeXFluxError as error:
                chained = self._imported(error, path)
                if chained is error:
                    raise
                raise chained from error
        for path in self._public:
            try:
                self._check_self_contained(path)
            except TeXFluxError as error:
                chained = self._imported(error, path)
                if chained is error:
                    raise
                raise chained from error

    def _check_self_contained(self, path: str) -> None:
        """Reject a macro module that depends on a name it cannot see itself.

        A caller's unrelated namespace must never make an otherwise invalid
        macro module valid.
        """

        environment = self._environments[path]
        for definition in self._public[path].values():
            for child in walk(definition.template):
                if not isinstance(child, SpecialInvocation):
                    continue
                if (
                    child.name in _TEMPLATE_NAMES
                    or child.name in self._registry
                    or child.name in environment
                ):
                    continue
                raise ModuleError(
                    f"'!{child.name}' is not defined in {definition.span.file} "
                    "and is not available through its own !macroimport",
                    child.span,
                    code="M029",
                )

    # -- content modules ---------------------------------------------------

    def compile_root(
        self,
        text: str,
        *,
        filename: str,
        data: bytes,
        flags: Flags | None = None,
    ) -> Document:
        """Compile the document a caller supplied as text."""

        source = self._register(filename, ModuleKind.CONTENT, data, text)
        return self._compile(
            source,
            overrides=flags,
            bindings=(),
            caller_flags={},
            stack=(source.path,),
        )

    def compile_content(
        self,
        source: ModuleSource,
        *,
        bindings: Sequence[FlagBinding],
        caller_flags: Flags,
        stack: tuple[str, ...],
    ) -> Document:
        """Compile one imported module as its own instance."""

        return self._compile(
            source,
            overrides=None,
            bindings=bindings,
            caller_flags=caller_flags,
            stack=stack,
        )

    def _compile(
        self,
        source: ModuleSource,
        *,
        overrides: Flags | None,
        bindings: Sequence[FlagBinding],
        caller_flags: Flags,
        stack: tuple[str, ...],
    ) -> Document:
        document = source.document
        validate_macro_forms(document)
        validate_flag_forms(document)
        validate_macroimport_forms(document)
        document = desugar(document)

        document, resolved = collect_flags(document, overrides)
        if bindings:
            resolved = bind_import_flags(
                resolved,
                bindings,
                caller_flags,
                module=source.display,
            )

        document, imports = resolve_macro_imports(document, source.display)
        self._build_environments(imports)
        document, macros = collect_macros(
            document,
            self._registry,
            imported=merge_imports(dict(self._standard), imports, self._public),
            module=source.path,
            standard=self._standard,
        )
        self._environments[source.path] = macros

        document = expand_macros(
            document,
            macros,
            resolved,
            environments=self._environments,
            module=source.path,
        )
        document = resolve_content_imports(
            document,
            self,
            module=source,
            flags=resolved,
            stack=stack,
        )
        return canonicalize(document, self._registry)


__all__ = [
    "PRELUDE_MODULE",
    "CompilationSession",
    "FlagBinding",
    "MacroEnvironment",
    "MacroImport",
    "Module",
    "ModuleKind",
    "ModuleSource",
    "SourceReader",
    "bind_import_flags",
    "load_standard_macros",
    "merge_imports",
    "parse_bindings",
    "read_source",
    "resolve_content_imports",
    "resolve_macro_imports",
    "resolve_module_path",
    "validate_macro_module_purity",
    "validate_macroimport_forms",
]
