"""User-facing TeXFlux errors."""

from .ast import SourceLocation


class TeXFluxError(Exception):
    error_kind = "texflux error"

    def __init__(self, message: str, loc: SourceLocation):
        self.message = message
        self.loc = loc
        super().__init__(message)

    def diagnostic(self) -> str:
        return (
            f"{self.loc.file}:{self.loc.line}:{self.loc.column}: "
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


__all__ = [
    "DirectiveError",
    "ParseError",
    "TeXFluxError",
    "ValidationError",
]
