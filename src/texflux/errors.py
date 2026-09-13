"""User-facing TeXFlux errors."""

from .ast import SourceSpan


class TeXFluxError(Exception):
    error_kind = "texflux error"

    def __init__(self, message: str, span: SourceSpan):
        self.message = message
        self.span = span
        super().__init__(message)

    def diagnostic(self) -> str:
        return f"{self.span.location}: {self.error_kind}: {self.message}"

    def __str__(self) -> str:
        return self.diagnostic()


class ParseError(TeXFluxError):
    error_kind = "parse error"


class ValidationError(TeXFluxError):
    error_kind = "validation error"


class DirectiveError(TeXFluxError):
    error_kind = "directive error"


class MacroExpansionError(TeXFluxError):
    error_kind = "macro error"


class ModuleError(TeXFluxError):
    """A module boundary this compilation cannot cross.

    Covers import forms, path resolution, content import cycles, the purity
    of a ``.tfxm`` macro module, macro name conflicts between modules, and
    ``!import`` flag bindings.
    """

    error_kind = "module error"


class InternalError(RuntimeError):
    """A defect in the TeXFlux installation or compiler, not in a document.

    Only the compiler-bundled standard macro module raises it today: that
    file ships with TeXFlux and is validated by its own tests, so a failure
    to read it says nothing about the source being compiled and has no span
    to point at. It stays a ``RuntimeError`` because it reports a broken
    build rather than anything a caller can correct.
    """


class FlagError(Exception):
    """A build-flag override that no declaration matches or that is not a bool.

    An override describes the build rather than the document, so it has no
    source span and cannot implement ``diagnostic()``. That is why this is
    deliberately not a ``TeXFluxError``: only a call that passes ``flags``
    can raise it, and such a call has to handle it explicitly.
    """


__all__ = [
    "DirectiveError",
    "FlagError",
    "InternalError",
    "MacroExpansionError",
    "ModuleError",
    "ParseError",
    "TeXFluxError",
    "ValidationError",
]
