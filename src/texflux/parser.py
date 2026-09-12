"""Physical-line parser and top-level structural header scanner."""

from __future__ import annotations

from dataclasses import dataclass, replace
import string
from typing import Final

from .ast import (
    GROUP_OPENERS,
    Argument,
    ArgumentLayout,
    Block,
    Document,
    GroupKind,
    InvocationKind,
    Node,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SourcePosition,
    SourceSpan,
    SpecialInvocation,
    Stack,
    SuiteMode,
)
from .errors import ParseError
from .syntax import is_escaped


@dataclass(frozen=True, slots=True)
class HeaderScanResult:
    """One physical header; a trailing separator requests another line."""

    segments: tuple[ParsedInvocation | SpecialInvocation, ...]
    suite_mode: SuiteMode | None
    suite_span: SourceSpan | None
    continuation_span: SourceSpan | None = None


@dataclass(frozen=True, slots=True)
class _PhysicalLine:
    text: str
    number: int

    @property
    def indent(self) -> int:
        return len(self.text) - len(self.text.lstrip(" "))

    @property
    def blank(self) -> bool:
        return not self.text.strip(" ")


def _block_span(boundary: SourceSpan, nodes: list[Node]) -> SourceSpan:
    end = max((node.span.end for node in nodes), default=boundary.end)
    return SourceSpan(boundary.file, boundary.start, max(boundary.end, end))


#: TeXFlux names are ASCII-only, independent of the host locale.
_NAME_START: Final = frozenset(string.ascii_letters)
_NAME_CHARS: Final = frozenset(string.ascii_letters + string.digits + "_")


def scan_group(
    text: str,
    start: int,
    *,
    span: SourceSpan,
) -> tuple[int, str]:
    """Scan one inline group, returning the end offset and raw content."""

    match text[start]:
        case "<":
            # An overlay group does not nest.
            index = start + 1
            while index < len(text):
                if text[index] == ">" and not is_escaped(text, index):
                    return index + 1, text[start + 1 : index]
                index += 1
            raise ParseError("unclosed overlay group", span)

        case "{":
            depth = 1
            index = start + 1
            while index < len(text):
                char = text[index]
                if not is_escaped(text, index):
                    if char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            return index + 1, text[start + 1 : index]
                index += 1
            raise ParseError("unclosed required group", span)

        case "[":
            # Brackets nest, but only outside a balanced brace group.
            bracket_depth = 1
            brace_depth = 0
            index = start + 1
            while index < len(text):
                char = text[index]
                if not is_escaped(text, index):
                    if char == "{":
                        brace_depth += 1
                    elif char == "}":
                        if brace_depth == 0:
                            raise ParseError("mismatched group delimiter", span)
                        brace_depth -= 1
                    elif brace_depth == 0 and char == "[":
                        bracket_depth += 1
                    elif brace_depth == 0 and char == "]":
                        bracket_depth -= 1
                        if bracket_depth == 0:
                            return index + 1, text[start + 1 : index]
                index += 1
            raise ParseError("unclosed optional group", span)

        case _:
            raise ParseError("invalid group opener", span)


def _has_top_level_trailing_colon(text: str, *, span: SourceSpan) -> bool:
    """Check a failed command scan for a reserved trailing colon."""

    end = len(text.rstrip(" "))
    index = 0
    while index < end:
        if text[index] in GROUP_OPENERS:
            try:
                index, _ = scan_group(text, index, span=span)
            except ParseError:
                return False
            continue
        index += 1
    return end > 0 and text[end - 1] == ":"


class HeaderScanner:
    """Scan one structural header while keeping group contents opaque."""

    def __init__(self, text: str, *, span: SourceSpan):
        self.text = text
        self.span = span
        self.end = len(text.rstrip(" "))
        self.saw_structure = False

    def _span(self, start: int, end: int | None = None) -> SourceSpan:
        if end is None:
            end = start
        return SourceSpan(
            self.span.file,
            SourcePosition(self.span.start.line, self.span.start.column + start),
            SourcePosition(self.span.start.line, self.span.start.column + end),
        )

    def _error(self, message: str, offset: int = 0) -> ParseError:
        end = min(offset + 1, self.end)
        return ParseError(message, self._span(offset, end))

    def scan(self) -> HeaderScanResult:
        if self.end == 0:
            raise self._error("empty structural header")

        segments: list[ParsedInvocation | SpecialInvocation] = []
        position = 0
        suite_mode: SuiteMode | None = None
        suite_span: SourceSpan | None = None
        continuation_span: SourceSpan | None = None

        while True:
            segment, position = self._segment(position, not segments)
            segments.append(segment)

            separator_start = position
            position = self._skip_spaces(position)
            if position == self.end:
                break

            if self.text[position] == ":":
                suite_mode, suite_span = self._suite_marker(position)
                break

            if self.text.startswith(">>", position):
                operator_start = position
                position = self._stack_separator(
                    position,
                    spaced=position > separator_start,
                )
                if position == self.end:
                    continuation_span = self._span(operator_start, operator_start + 2)
                    break
                continue

            raise self._error("unexpected token in structural header", position)

        return HeaderScanResult(
            tuple(segments),
            suite_mode,
            suite_span,
            continuation_span,
        )

    def _skip_spaces(self, position: int) -> int:
        while position < self.end and self.text[position] == " ":
            position += 1
        return position

    def _suite_marker(self, position: int) -> tuple[SuiteMode, SourceSpan]:
        """Scan the trailing ``:`` or ``: |`` suite marker."""

        marker_start = position
        position = self._skip_spaces(position + 1)
        if position == self.end:
            self.saw_structure = True
            return SuiteMode.SEQUENCE, self._span(marker_start, position)

        if self.text[position] != "|":
            if self.text.startswith(">>", position):
                self.saw_structure = True
            raise self._error("unexpected token in structural header", position)

        self.saw_structure = True
        position = self._skip_spaces(position + 1)
        if position != self.end:
            raise self._error("trailing token after suite marker", position)
        return SuiteMode.BLOCK, self._span(marker_start, position)

    def _stack_separator(self, position: int, *, spaced: bool) -> int:
        """Return the next segment's offset, or the line end for continuation."""

        if not spaced or (
            position + 2 != self.end
            and self.text[position + 2 : position + 3] != " "
        ):
            raise self._error(
                "stack separator requires surrounding spaces",
                position,
            )
        position += 2
        self.saw_structure = True
        position = self._skip_spaces(position)
        return position

    def _inline_group(self, position: int) -> tuple[Argument, int]:
        """Scan one inline group into an argument, keeping its contents opaque."""

        start = position
        kind = GroupKind.from_opener(self.text[position])
        end, value = scan_group(
            self.text,
            start,
            span=self._span(start, start + 1),
        )
        return (
            Argument(kind, value, ArgumentLayout.INLINE, self._span(start, end)),
            end,
        )

    def _segment(
        self,
        position: int,
        first: bool,
    ) -> tuple[ParsedInvocation | SpecialInvocation, int]:
        segment_start = position
        if position >= self.end:
            raise self._error("missing structural segment", position)

        prefix = self.text[position]
        match prefix:
            case "!":
                kind = None
            case "\\":
                kind = InvocationKind.COMMAND
            case "@":
                kind = InvocationKind.ENVIRONMENT
            case _:
                raise self._error(
                    "structural header must start with '\\', '@', or '!'"
                    if first
                    else "each stack segment must start with '\\', '@', or '!'",
                    position,
                )
        position += 1

        if prefix == "@" and position < self.end and self.text[position] == "{":
            group, position = self._inline_group(position)
            return (
                ParsedInvocation(
                    InvocationKind.BRACE,
                    "",
                    (group,),
                    None,
                    self._span(segment_start, position),
                ),
                position,
            )

        if prefix == "@" and (
            position >= self.end or self.text[position] in ":> "
        ):
            return (
                ParsedInvocation(
                    InvocationKind.TRANSPARENT,
                    "",
                    (),
                    None,
                    self._span(segment_start, position),
                ),
                position,
            )

        name_start = position
        if position >= self.end or self.text[position] not in _NAME_START:
            raise self._error("invalid structural name", position)
        position += 1

        # TeX environment names are intentionally scanned more broadly than
        # ordinary identifiers.  A trailing star is the common case, while
        # punctuation such as '-' remains available to environment names.
        if kind is InvocationKind.ENVIRONMENT:
            while position < self.end:
                char = self.text[position]
                if char in "{[<:> ":
                    break
                position += 1
        else:
            while position < self.end and self.text[position] in _NAME_CHARS:
                position += 1

        name = self.text[name_start:position]
        if not name:
            raise self._error("invalid structural name", name_start)

        groups: list[Argument] = []
        while position < self.end and self.text[position] in GROUP_OPENERS:
            group, position = self._inline_group(position)
            groups.append(group)

        if position < self.end and self.text[position] not in " :>":
            raise self._error(
                "unexpected token after structural name or group",
                position,
            )

        segment_span = self._span(segment_start, position)
        if prefix == "!":
            return SpecialInvocation(name, tuple(groups), None, segment_span), position
        return ParsedInvocation(kind, name, tuple(groups), None, segment_span), position


def _scan_structural_header(
    text: str,
    span: SourceSpan,
) -> HeaderScanResult | None:
    """Scan one header, or return ``None`` when the line stays raw TeX.

    An ordinary ``\\command`` only becomes structural through a top-level
    structural token, so a scan that fails without seeing one is raw TeX.
    """

    scanner = HeaderScanner(text, span=span)
    try:
        return scanner.scan()
    except ParseError:
        if scanner.saw_structure or _has_top_level_trailing_colon(text, span=span):
            raise
        return None


class _Parser:
    def __init__(self, source: str, filename: str):
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        physical = source.split("\n")
        if physical and physical[-1] == "":
            physical.pop()
        self.filename = filename
        self.document_span = SourceSpan(
            filename,
            SourcePosition(1, 1),
            SourcePosition(1, 1).advance(source),
        )
        self.lines = tuple(
            _PhysicalLine(text, number)
            for number, text in enumerate(physical, 1)
        )
        self.index = 0
        for line in self.lines:
            tab = line.text.find("\t")
            if tab >= 0:
                raise ParseError(
                    "tab characters are not allowed",
                    SourceSpan(
                        filename,
                        SourcePosition(line.number, tab + 1),
                        SourcePosition(line.number, tab + 2),
                    ),
                )

    def parse(self) -> Document:
        body = self._block(0, self.document_span)
        return Document(body, self.document_span)

    def _line_span(
        self,
        line: _PhysicalLine,
        start_column: int = 1,
        text: str | None = None,
    ) -> SourceSpan:
        if text is None:
            text = line.text[start_column - 1 :]
        return SourceSpan(
            self.filename,
            SourcePosition(line.number, start_column),
            SourcePosition(line.number, start_column + len(text)),
        )

    def _header_span(self, line: _PhysicalLine, base: int) -> SourceSpan:
        text = line.text[base:].rstrip(" ")
        return self._line_span(line, base + 1, text)

    def _next_nonblank(self, index: int) -> int | None:
        while index < len(self.lines) and self.lines[index].blank:
            index += 1
        return None if index == len(self.lines) else index

    def _blank_run(self, base: int) -> range | None:
        """Consume a blank-line run, or rewind when it ends the block.

        A run is block content while more content follows at ``base``. At the
        document root a trailing run is content too, because no enclosing
        block can reclaim it.
        """

        run_start = self.index
        while self.index < len(self.lines) and self.lines[self.index].blank:
            self.index += 1
        ends_block = (
            self.index >= len(self.lines)
            or self.lines[self.index].indent < base
        )
        if base != 0 and ends_block:
            self.index = run_start
            return None
        return range(run_start, self.index)

    def _block(
        self,
        base: int,
        boundary: SourceSpan,
        *,
        raw_suite: bool = False,
    ) -> Block:
        nodes = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.blank:
                run = self._blank_run(base)
                if run is None:
                    break
                for blank_index in run:
                    nodes.append(
                        RawTex("", self._line_span(self.lines[blank_index]))
                    )
                continue

            if line.indent < base:
                break

            rest = line.text[base:]
            extra = len(rest) - len(rest.lstrip(" "))
            first = rest[extra : extra + 1]

            if raw_suite:
                self._emit_raw(nodes, line, base)
                continue

            if first == "@" and rest[extra : extra + 2] == "@@":
                raw_text = rest[:extra] + rest[extra + 1 :]
                nodes.append(
                    RawTex(raw_text, self._line_span(line, base + 1, rest))
                )
                self.index += 1
                continue

            if first in {"@", "!"} and line.indent != base:
                raise self._indent_error(line)

            if line.indent != base:
                if first == "\\" and self._scan_command_header(
                    line,
                    line.indent,
                ) is not None:
                    raise self._indent_error(line)
                self._emit_raw(nodes, line, base)
                continue

            if first in {"@", "!"}:
                nodes.append(self._directive(line, base))
                continue

            if first == "\\":
                structural = self._try_structural_command(line, base)
                if structural is not None:
                    nodes.append(structural)
                    continue

            self._emit_raw(nodes, line, base)

        return Block(tuple(nodes), _block_span(boundary, nodes))

    def _emit_raw(
        self,
        nodes: list[Node],
        line: _PhysicalLine,
        base: int,
    ) -> None:
        """Append one raw TeX line, dedented to the suite base, and advance."""

        raw_text = line.text[base:]
        nodes.append(RawTex(raw_text, self._line_span(line, base + 1, raw_text)))
        self.index += 1

    def _indent_error(self, line: _PhysicalLine) -> ParseError:
        return ParseError(
            "invalid structural indentation",
            self._line_span(line, line.indent + 1),
        )

    def _scan_command_header(
        self,
        line: _PhysicalLine,
        base: int,
    ):
        header_span = self._header_span(line, base)
        result = _scan_structural_header(line.text[base:], header_span)
        if result is None:
            return None
        if (
            result.suite_mode is None
            and result.continuation_span is None
            and len(result.segments) == 1
        ):
            # A closed single-segment command line is ordinary TeX.
            return None
        return result, header_span

    def _try_structural_command(
        self,
        line: _PhysicalLine,
        base: int,
    ):
        scanned = self._scan_command_header(line, base)
        if scanned is None:
            return None
        result, header_span = scanned
        return self._directive_result(base, result, header_span)

    def _directive(self, line: _PhysicalLine, base: int):
        if line.indent != base:
            raise self._indent_error(line)
        header_span = self._header_span(line, base)
        result = HeaderScanner(line.text[base:], span=header_span).scan()
        return self._directive_result(base, result, header_span)

    def _directive_result(
        self,
        base: int,
        result: HeaderScanResult,
        header_span: SourceSpan,
    ):
        self.index += 1
        if (
            result.suite_mode is None
            and result.continuation_span is None
            and len(result.segments) == 1
        ):
            self._reject_missing_suite(base, result.segments[0])
        return self._structural_node(base, result, header_span)

    def _reject_missing_suite(
        self,
        base: int,
        segment: ParsedInvocation | SpecialInvocation,
    ) -> None:
        """Reject a suiteless line that an indented block or ``@`` needs."""

        if (
            isinstance(segment, ParsedInvocation)
            and segment.kind is InvocationKind.ENVIRONMENT
        ):
            raise ParseError(
                "environment directives require a suite marker ':'",
                segment.span,
            )
        next_index = self._next_nonblank(self.index)
        if next_index is not None and self.lines[next_index].indent >= base + 4:
            line = self.lines[next_index]
            raise ParseError(
                "indented lines require a suite marker ':'",
                self._line_span(line, line.indent + 1),
            )

    def _structural_node(
        self,
        base: int,
        result: HeaderScanResult,
        header_span: SourceSpan,
    ) -> ParsedInvocation | SpecialInvocation | Stack:
        """Attach the parsed suite, if any, to one scanned header."""

        if result.continuation_span is not None:
            segments = list(result.segments)
            while result.continuation_span is not None:
                if self.index == len(self.lines):
                    raise ParseError(
                        "stack separator needs a following segment",
                        result.continuation_span,
                    )
                line = self.lines[self.index]
                if line.blank or line.indent != base:
                    raise ParseError(
                        "stack continuation requires the next line at the same indentation",
                        self._line_span(line, line.indent + 1),
                    )
                line_span = self._header_span(line, base)
                result = HeaderScanner(line.text[base:], span=line_span).scan()
                segments.extend(result.segments)
                header_span = SourceSpan(header_span.file, header_span.start, line_span.end)
                self.index += 1
            result = replace(result, segments=tuple(segments))

        if result.suite_mode is None:
            if len(result.segments) == 1:
                return result.segments[0]
            return Stack(result.segments, None, header_span)

        suite = self._parse_suite(base, result, header_span)
        if len(result.segments) == 1:
            return replace(
                result.segments[0],
                suite=suite,
                suite_mode=result.suite_mode,
                suite_span=result.suite_span,
                span=header_span,
            )
        return Stack(
            result.segments,
            suite,
            header_span,
            result.suite_mode,
            result.suite_span,
        )

    def _parse_suite(
        self,
        base: int,
        result: HeaderScanResult,
        header_span: SourceSpan,
    ) -> Block:
        suite_base = base + 4
        next_index = self._next_nonblank(self.index)
        raw_suite = (
            isinstance(result.segments[-1], SpecialInvocation)
            and result.segments[-1].name == "items"
            and result.suite_mode is SuiteMode.SEQUENCE
        )
        if raw_suite:
            return self._block(suite_base, header_span, raw_suite=True)
        if result.suite_mode is SuiteMode.SEQUENCE:
            if (
                next_index is not None
                and base < self.lines[next_index].indent < suite_base
            ):
                raise ParseError(
                    "sequence suite entries require four-space indentation",
                    self._line_span(
                        self.lines[next_index],
                        self.lines[next_index].indent + 1,
                    ),
                )
            suite = self._sequence_suite(suite_base, header_span)
            requires_entry = (
                isinstance(result.segments[-1], ParsedInvocation)
                and result.segments[-1].kind is InvocationKind.COMMAND
            )
            if not suite.nodes and requires_entry:
                raise ParseError(
                    "sequence suites require at least one '-' value entry",
                    header_span,
                )
            return suite
        if (
            next_index is not None
            and self.lines[next_index].indent >= suite_base
        ):
            return self._block(suite_base, header_span)
        return Block((), header_span)

    def _sequence_suite(self, base: int, boundary: SourceSpan) -> Block:
        """Parse a ``:`` suite whose values are explicitly marked with ``-``."""

        entries: list[SequenceEntry] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.blank:
                # Blank runs separate entries; only their boundary matters.
                if self._blank_run(base) is None:
                    break
                continue
            if line.indent < base:
                break
            if line.indent != base:
                raise ParseError(
                    "sequence entries must start at suite indentation",
                    self._line_span(line, line.indent + 1),
                )
            if not line.text.startswith("-", base):
                raise ParseError(
                    "sequence suites require '-' value entries",
                    self._line_span(line, base + 1),
                )
            entries.append(self._sequence_entry(line, base))
        return Block(tuple(entries), _block_span(boundary, entries))

    def _sequence_entry(
        self,
        line: _PhysicalLine,
        base: int,
    ) -> SequenceEntry:
        marker_span = self._line_span(line, base + 1, "-")
        entry_span = self._line_span(line, base + 1, line.text[base:])
        payload_start = base + 1
        while payload_start < len(line.text) and line.text[payload_start] == " ":
            payload_start += 1
        payload = line.text[payload_start:].rstrip(" ")

        self.index += 1

        nodes: list[Node] = []
        if payload:
            payload_span = self._line_span(line, payload_start + 1, payload)
            if payload.startswith("@@"):
                nodes.append(RawTex(payload[1:], payload_span))
            elif payload[0] in "\\@!":
                # Only a command payload may turn out to be ordinary TeX; an
                # '@' or '!' payload must scan as a structural header.
                result = (
                    _scan_structural_header(payload, payload_span)
                    if payload[0] == "\\"
                    else HeaderScanner(payload, span=payload_span).scan()
                )
                nodes.append(
                    RawTex(payload, payload_span)
                    if result is None
                    else self._structural_node(base, result, payload_span)
                )
            else:
                nodes.append(RawTex(payload, payload_span))

        continuation = self._sequence_continuation(base, entry_span)
        nodes.extend(continuation.nodes)

        value_end = entry_span.end
        for node in nodes:
            value_end = max(value_end, node.span.end)
            if (
                isinstance(node, (ParsedInvocation, SpecialInvocation, Stack))
                and node.suite is not None
            ):
                value_end = max(value_end, node.suite.span.end)
        value_span = SourceSpan(entry_span.file, entry_span.start, value_end)
        value = Block(tuple(nodes), value_span)
        # A value that never leaves its marker line is the compact case; a
        # suite or a continuation line makes it a multi-line value.
        spans_one_line = bool(payload) and value_end.line == entry_span.start.line
        return SequenceEntry(value, marker_span, value_span, spans_one_line)

    def _sequence_continuation(
        self,
        base: int,
        boundary: SourceSpan,
    ) -> Block:
        next_index = self._next_nonblank(self.index)
        if (
            next_index is None
            or self.lines[next_index].indent <= base
        ):
            return Block((), boundary)
        return self._block(
            self.lines[next_index].indent,
            boundary,
        )

def parse(source: str, filename: str = "<string>") -> Document:
    """Parse source text into the syntax AST."""

    return _Parser(source, filename).parse()


__all__ = ["HeaderScanResult", "HeaderScanner", "parse", "scan_group"]
