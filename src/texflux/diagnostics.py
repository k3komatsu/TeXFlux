"""Structured diagnostics for editors and language servers.

``diagnose`` runs the compilation ``compile_with_map`` runs, but reports what
it found instead of raising the first thing that went wrong. It returns every
file the compilation read together with every diagnostic it produced, which is
what a language server needs: one publish per open file, and an empty list for
a file that is now clean.

Nothing here interprets a document. A ``Diagnostic`` is one ``TeXFluxError`` or
one ``RenderWarning`` with the message, span, code and related locations that
error or warning already carried.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .ast import SourceSpan
from .errors import RelatedLocation, TeXFluxError, diagnostic_line
from .flags import Flags
from .interchange import dump_json, header, span_encoder
from .modules import CompilationSession, SourceReader, read_source
from .paths import normalized_path
from .render import LoadedSource, RenderWarning, render_with_provenance


class Severity(StrEnum):
    """Whether a diagnostic stopped the compilation or only warned about it."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One reported problem, at one place, in one file."""

    severity: Severity
    kind: str
    code: str
    message: str
    span: SourceSpan
    related: tuple[RelatedLocation, ...] = ()

    @property
    def label(self) -> str:
        """How this diagnostic names itself on a printed line."""

        # Compared by value: a Diagnostic rebuilt from parsed JSON carries
        # the plain string rather than the enum member.
        if self.severity == Severity.WARNING:
            return "warning"
        return f"{self.kind} error"

    def line(self) -> str:
        return diagnostic_line(self.span, self.label, self.message, self.code)

    def __str__(self) -> str:
        return self.line()

    @classmethod
    def from_error(cls, error: TeXFluxError) -> "Diagnostic":
        return cls(
            Severity.ERROR,
            error.kind,
            error.code,
            error.message,
            error.span,
            error.related,
        )

    @classmethod
    def from_warning(cls, warning: RenderWarning) -> "Diagnostic":
        return cls(
            Severity.WARNING,
            warning.kind,
            warning.code,
            warning.message,
            warning.span,
        )


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Everything one compilation read, and everything it reported."""

    #: The root first, then each module in the order the session loaded it.
    sources: tuple[LoadedSource, ...]
    #: In the order the compilation produced them. Compilation stops at its
    #: first error and rendering runs only after it succeeds, so a report
    #: holds either one error or any number of warnings, never both.
    diagnostics: tuple[Diagnostic, ...]

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("a diagnostic report needs at least the root source")

    @property
    def root(self) -> LoadedSource:
        return self.sources[0]

    @property
    def ok(self) -> bool:
        """Whether nothing stopped the compilation; warnings still allow it."""

        return all(
            diagnostic.severity != Severity.ERROR
            for diagnostic in self.diagnostics
        )

    def by_file(self) -> dict[str, tuple[Diagnostic, ...]]:
        """Every file read, mapped to its diagnostics.

        A file that is clean maps to an empty tuple rather than being left
        out, because an editor clears a file's old diagnostics by publishing
        an empty list for it.

        A span naming a file outside ``sources`` would be a compiler defect,
        which ``serialize_diagnostics`` refuses. Here it gets a key of its
        own instead: losing a diagnostic an editor should show would be the
        worse failure, and the stray file name says what went wrong.
        """

        grouped: dict[str, list[Diagnostic]] = {
            source.file: [] for source in self.sources
        }
        for diagnostic in self.diagnostics:
            grouped.setdefault(diagnostic.span.file, []).append(diagnostic)
        return {file: tuple(items) for file, items in grouped.items()}


def overlay_reader(overlays: Mapping[str, str | bytes]) -> SourceReader:
    """A reader that serves unsaved editor buffers before touching the disk.

    Keys are matched by path identity, so an overlay written as an absolute
    path still answers an ``!import`` written as a relative one.
    """

    table = {
        normalized_path(path): (
            text.encode("utf-8") if isinstance(text, str) else text
        )
        for path, text in overlays.items()
    }

    def read(display: str) -> bytes:
        data = table.get(normalized_path(display))
        return read_source(display) if data is None else data

    return read


def diagnose(
    source: str,
    *,
    filename: str = "<string>",
    flags: Flags | None = None,
    source_bytes: bytes | None = None,
    overlays: Mapping[str, str | bytes] | None = None,
) -> DiagnosticReport:
    """Report what one compilation of ``source`` found, without raising.

    ``overlays`` maps a module path to unsaved text, which any ``!import`` or
    ``!macroimport`` that reaches that path reads instead of the file. The
    root is not among them: it is what ``source`` already is.

    ``FlagError``, ``InternalError`` and ``RecursionError`` still propagate.
    None of them describes a place in a document, so none of them is a
    diagnostic: they report how the compiler was called or installed.
    """

    session = CompilationSession(
        reader=None if overlays is None else overlay_reader(overlays),
    )
    data = source.encode("utf-8") if source_bytes is None else source_bytes
    try:
        document = session.compile_root(
            source,
            filename=filename,
            data=data,
            flags=flags,
        )
    except TeXFluxError as error:
        return DiagnosticReport(
            session.loaded(),
            (Diagnostic.from_error(error),),
        )
    rendered = render_with_provenance(document)
    return DiagnosticReport(
        session.loaded(),
        tuple(Diagnostic.from_warning(warning) for warning in rendered.warnings),
    )


def serialize_diagnostics(
    report: DiagnosticReport,
    *,
    pretty: bool = False,
) -> str:
    """Serialize schema v1 as deterministic JSON with exactly one final LF.

    Source hashes use the bytes the compilation read, which for an overlaid
    module is the editor's buffer rather than the file. Spans use diagnostics'
    file spelling, indexed into the same source table the external AST uses.
    """

    payload = header("texflux-diagnostics", report.sources)
    span = span_encoder(report.sources)
    payload["diagnostics"] = [
        {
            "severity": diagnostic.severity,
            "kind": diagnostic.kind,
            "code": diagnostic.code,
            "message": diagnostic.message,
            "span": span(diagnostic.span),
            "related": [
                {"message": related.message, "span": span(related.span)}
                for related in diagnostic.related
            ],
        }
        for diagnostic in report.diagnostics
    ]
    return dump_json(payload, pretty=pretty)


__all__ = [
    "Diagnostic",
    "DiagnosticReport",
    "Severity",
    "diagnose",
    "overlay_reader",
    "serialize_diagnostics",
]
