"""User-facing TeXFlux errors.

Every diagnostic carries a stable code, so an editor can recognize one across
releases without matching its message, and zero or more related locations. A
related location is the second place a diagnostic is about -- the first
declaration a duplicate collides with, the import that pulled a broken module
in -- which the message already names in prose and which a language server
needs as structured data.
"""

from dataclasses import dataclass
from typing import ClassVar, Self

from .ast import SourceSpan


@dataclass(frozen=True, slots=True)
class RelatedLocation:
    """A second place a diagnostic points at, beside its own span."""

    message: str
    span: SourceSpan


def diagnostic_line(
    span: SourceSpan,
    label: str,
    message: str,
    code: str,
) -> str:
    """The one line every diagnostic prints, errors and warnings alike."""

    return f"{span.location}: {label}: {message} [{code}]"


class TeXFluxError(Exception):
    #: The category this diagnostic reports, which each subclass names.
    kind: ClassVar[str] = "texflux"

    def __init__(
        self,
        message: str,
        span: SourceSpan,
        *,
        code: str,
        related: tuple[RelatedLocation, ...] = (),
    ):
        self.message = message
        self.span = span
        self.code = code
        self.related = related
        super().__init__(message)

    @property
    def error_kind(self) -> str:
        return f"{self.kind} error"

    def diagnostic(self) -> str:
        return diagnostic_line(self.span, self.error_kind, self.message, self.code)

    def __str__(self) -> str:
        return self.diagnostic()

    def chained(self, message: str, *related: RelatedLocation) -> Self:
        """This error with a longer message and more related locations.

        The code and the span stay the callee's, so an import chain can be
        appended without losing either what went wrong or where.
        """

        return type(self)(
            message,
            self.span,
            code=self.code,
            related=self.related + related,
        )


class ParseError(TeXFluxError):
    kind = "parse"


class ValidationError(TeXFluxError):
    kind = "validation"


class DirectiveError(TeXFluxError):
    kind = "directive"


class MacroExpansionError(TeXFluxError):
    kind = "macro"


class ModuleError(TeXFluxError):
    """A module boundary this compilation cannot cross.

    Covers import forms, path resolution, content import cycles, the purity
    of a ``.tfxm`` macro module, macro name conflicts between modules, and
    ``!import`` flag bindings.
    """

    kind = "module"


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
    "RelatedLocation",
    "TeXFluxError",
    "ValidationError",
    "diagnostic_line",
]
