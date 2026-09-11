"""User-facing TeXFlux errors."""

from .ast import SourceSpan


class TeXFluxError(Exception):
    error_kind = "texflux error"

    def __init__(self, message: str, span: SourceSpan):
        self.message = message
        self.span = span
        super().__init__(message)

    def diagnostic(self) -> str:
        return (
            f"{self.span.file}:{self.span.start.line}:{self.span.start.column}: "
            f"{self.error_kind}: {self.message}"
        )

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


__all__ = [
    "DirectiveError",
    "MacroExpansionError",
    "ParseError",
    "TeXFluxError",
    "ValidationError",
]
