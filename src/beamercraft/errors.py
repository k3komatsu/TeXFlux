"""User-facing Beamercraft errors."""

from .ast import SourceLocation


class BeamercraftError(Exception):
    error_kind = "beamercraft error"

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


class ParseError(BeamercraftError):
    error_kind = "parse error"


class ValidationError(BeamercraftError):
    error_kind = "validation error"


class DirectiveError(BeamercraftError):
    error_kind = "directive error"


__all__ = [
    "BeamercraftError",
    "DirectiveError",
    "ParseError",
    "ValidationError",
]
