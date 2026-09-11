"""Physical-line parser and top-level structural header scanner."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    Document,
    GroupKind,
    InvocationKind,
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


@dataclass(frozen=True, slots=True)
class HeaderScanResult:
    segments: tuple[ParsedInvocation | SpecialInvocation, ...]
    suite_mode: SuiteMode | None
    suite_span: SourceSpan | None

    @property
    def suite(self) -> bool:
        """Compatibility view for callers that only need presence."""

        return self.suite_mode is not None


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


def _source_end(source: str) -> SourcePosition:
    if not source:
        return SourcePosition(1, 1)
    lines = source.split("\n")
    if lines[-1] == "":
        return SourcePosition(len(lines), 1)
    return SourcePosition(len(lines), len(lines[-1]) + 1)


def _block_span(boundary: SourceSpan, nodes: list) -> SourceSpan:
    end = boundary.end
    for node in nodes:
        if node.span.end > end:
            end = node.span.end
    return SourceSpan(boundary.file, boundary.start, end)


def _is_ascii_letter(char: str) -> bool:
    return "A" <= char <= "Z" or "a" <= char <= "z"


def _is_ascii_name_char(char: str) -> bool:
    return _is_ascii_letter(char) or "0" <= char <= "9" or char == "_"


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def scan_group(
    text: str,
    start: int,
    *,
    span: SourceSpan,
) -> tuple[int, str]:
    """Scan one inline group, returning the end offset and raw content."""

    opener = text[start]
    if opener == "<":
        index = start + 1
        while index < len(text):
            if text[index] == ">" and not _is_escaped(text, index):
                return index + 1, text[start + 1 : index]
            index += 1
        raise ParseError("unclosed overlay group", span)

    if opener == "{":
        depth = 1
        index = start + 1
        while index < len(text):
            char = text[index]
            if not _is_escaped(text, index):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return index + 1, text[start + 1 : index]
            index += 1
        raise ParseError("unclosed required group", span)

    if opener == "[":
        bracket_depth = 1
        brace_depth = 0
        index = start + 1
        while index < len(text):
            char = text[index]
            if not _is_escaped(text, index):
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

    raise ParseError("invalid group opener", span)


def _has_top_level_trailing_colon(text: str, *, span: SourceSpan) -> bool:
    """Check a failed command scan for a reserved trailing colon."""

    end = len(text.rstrip(" "))
    index = 0
    while index < end:
        if text[index] in "{[<":
            try:
                index, _ = scan_group(text, index, span=span)
            except ParseError:
                return False
            continue
        index += 1
    return end > 0 and text[end - 1] == ":"


class HeaderScanner:
    """Scan one structural header while keeping group contents opaque."""

    _PREFIXES = {"\\": InvocationKind.COMMAND, "@": InvocationKind.ENVIRONMENT}

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
        first = True
        suite_mode: SuiteMode | None = None
        suite_span: SourceSpan | None = None

        while True:
            segment, position = self._segment(position, first)
            segments.append(segment)
            first = False

            spaces = 0
            while position < self.end and self.text[position] == " ":
                position += 1
                spaces += 1

            if position == self.end:
                break

            if self.text[position] == ":":
                marker_start = position
                position += 1
                while position < self.end and self.text[position] == " ":
                    position += 1
                if position == self.end:
                    self.saw_structure = True
                    suite_mode = SuiteMode.SEQUENCE
                    suite_span = self._span(marker_start, position)
                    break
                if self.text[position] != "|":
                    if self.text.startswith(">>", position):
                        self.saw_structure = True
                    raise self._error("unexpected token in structural header", position)
                self.saw_structure = True
                suite_mode = SuiteMode.BLOCK
                position += 1
                while position < self.end and self.text[position] == " ":
                    position += 1
                if position != self.end:
                    raise self._error("trailing token after suite marker", position)
                suite_span = self._span(marker_start, position)
                break

            if self.text.startswith(">>", position):
                if spaces == 0:
                    raise self._error(
                        "stack separator requires surrounding spaces",
                        position,
                    )
                position += 2
                if position >= self.end or self.text[position] != " ":
                    raise self._error(
                        "stack separator requires surrounding spaces",
                        position,
                    )
                self.saw_structure = True
                while position < self.end and self.text[position] == " ":
                    position += 1
                if position == self.end:
                    raise self._error(
                        "stack separator needs a following segment",
                        position,
                    )
                continue

            raise self._error("unexpected token in structural header", position)

        return HeaderScanResult(
            tuple(segments),
            suite_mode,
            suite_span,
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
        if prefix == "!":
            position += 1
            kind = None
        elif prefix in self._PREFIXES:
            position += 1
            kind = self._PREFIXES[prefix]
        else:
            if first:
                raise self._error(
                    "structural header must start with '\\', '@', or '!'",
                    position,
                )
            raise self._error(
                "each stack segment must start with '\\', '@', or '!'",
                position,
            )

        if prefix == "@" and position < self.end and self.text[position] == "{":
            group_start = position
            group_span = self._span(group_start, group_start + 1)
            position, value = scan_group(
                self.text,
                position,
                span=group_span,
            )
            group_span = self._span(group_start, position)
            return (
                ParsedInvocation(
                    InvocationKind.BRACE,
                    "",
                    (Argument(
                        GroupKind.REQUIRED,
                        value,
                        ArgumentLayout.INLINE,
                        group_span,
                    ),),
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
        if position >= self.end or not _is_ascii_letter(self.text[position]):
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
            while position < self.end and _is_ascii_name_char(self.text[position]):
                position += 1

        name = self.text[name_start:position]
        if not name:
            raise self._error("invalid structural name", name_start)

        groups: list[Argument] = []
        while position < self.end and self.text[position] in "{[<":
            opener = self.text[position]
            group_kind = {
                "{": GroupKind.REQUIRED,
                "[": GroupKind.OPTIONAL,
                "<": GroupKind.OVERLAY,
            }[opener]
            group_start = position
            group_span = self._span(group_start, group_start + 1)
            position, value = scan_group(
                self.text,
                position,
                span=group_span,
            )
            group_span = self._span(group_start, position)
            groups.append(
                Argument(group_kind, value, ArgumentLayout.INLINE, group_span)
            )

        if position < self.end and self.text[position] not in " :>":
            raise self._error(
                "unexpected token after structural name or group",
                position,
            )

        segment_span = self._span(segment_start, position)
        if prefix == "!":
            return SpecialInvocation(name, tuple(groups), None, segment_span), position
        return ParsedInvocation(kind, name, tuple(groups), None, segment_span), position


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
            _source_end(source),
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
                run_start = self.index
                while (
                    self.index < len(self.lines)
                    and self.lines[self.index].blank
                ):
                    self.index += 1
                next_index = self.index
                if base != 0 and (
                    next_index >= len(self.lines)
                    or self.lines[next_index].indent < base
                ):
                    self.index = run_start
                    break
                for blank_index in range(run_start, self.index):
                    blank_line = self.lines[blank_index]
                    nodes.append(
                        RawTex(
                            "",
                            self._line_span(blank_line),
                        )
                    )
                continue

            if line.indent < base:
                break

            rest = line.text[base:]
            extra = len(rest) - len(rest.lstrip(" "))
            first = rest[extra : extra + 1]

            if raw_suite:
                raw_text = line.text[base:]
                nodes.append(
                    RawTex(raw_text, self._line_span(line, base + 1, raw_text))
                )
                self.index += 1
                continue

            if first == "@" and rest[extra : extra + 2] == "@@":
                raw_text = rest[:extra] + rest[extra + 1 :]
                nodes.append(
                    RawTex(raw_text, self._line_span(line, base + 1, rest))
                )
                self.index += 1
                continue

            if first in {"@", "!"} and line.indent != base:
                raise ParseError(
                    "invalid structural indentation",
                    self._line_span(line, line.indent + 1),
                )

            if line.indent != base:
                if first == "\\" and self._scan_command_header(
                    line,
                    line.indent,
                ) is not None:
                    raise ParseError(
                        "invalid structural indentation",
                        self._line_span(line, line.indent + 1),
                    )
                raw_text = line.text[base:]
                nodes.append(
                    RawTex(raw_text, self._line_span(line, base + 1, raw_text))
                )
                self.index += 1
                continue

            if first in {"@", "!"}:
                nodes.append(self._directive(line, base))
                continue

            if first == "\\":
                structural = self._try_structural_command(line, base)
                if structural is not None:
                    nodes.append(structural)
                    continue

            raw_text = line.text[base:]
            nodes.append(
                RawTex(raw_text, self._line_span(line, base + 1, raw_text))
            )
            self.index += 1

        return Block(tuple(nodes), _block_span(boundary, nodes))

    def _scan_command_header(
        self,
        line: _PhysicalLine,
        base: int,
    ):
        header_span = self._header_span(line, base)
        scanner = HeaderScanner(line.text[base:], span=header_span)
        try:
            result = scanner.scan()
        except ParseError:
            if scanner.saw_structure or _has_top_level_trailing_colon(
                scanner.text,
                span=header_span,
            ):
                raise
            return None
        if not result.suite and len(result.segments) == 1:
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
            raise ParseError(
                "invalid structural indentation",
                self._line_span(line, line.indent + 1),
            )
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

        if result.suite_mode is None:
            if len(result.segments) > 1:
                return Stack(result.segments, None, header_span)
            segment = result.segments[0]
            if (
                isinstance(segment, ParsedInvocation)
                and segment.kind is InvocationKind.ENVIRONMENT
            ):
                raise ParseError(
                    "environment directives require a suite marker ':'",
                    segment.span,
                )
            next_index = self._next_nonblank(self.index)
            if (
                next_index is not None
                and self.lines[next_index].indent >= base + 4
            ):
                raise ParseError(
                    "indented lines require a suite marker ':'",
                    self._line_span(
                        self.lines[next_index],
                        self.lines[next_index].indent + 1,
                    ),
                )
            return segment

        suite = self._parse_suite(
            base,
            result,
            header_span,
        )

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
                run_start = self.index
                while (
                    self.index < len(self.lines)
                    and self.lines[self.index].blank
                ):
                    self.index += 1
                next_index = self.index
                if (
                    next_index >= len(self.lines)
                    or self.lines[next_index].indent < base
                ):
                    self.index = run_start
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

        nodes: list[RawTex | Block | ParsedInvocation | SpecialInvocation | Stack] = []
        if payload:
            if payload.startswith("@@"):
                nodes.append(
                    RawTex(
                        payload[1:],
                        self._line_span(line, payload_start + 1, payload),
                    )
                )
            elif payload[0] in "\\@!":
                header_span = self._line_span(line, payload_start + 1, payload)
                scanner = HeaderScanner(payload, span=header_span)
                try:
                    result = scanner.scan()
                except ParseError:
                    if payload[0] != "\\":
                        raise
                    if scanner.saw_structure or _has_top_level_trailing_colon(
                        payload,
                        span=header_span,
                    ):
                        raise
                    result = None
                if result is not None:
                    nodes.append(
                        self._sequence_structural_value(
                            base,
                            result,
                            header_span,
                        )
                    )
                else:
                    nodes.append(
                        RawTex(
                            payload,
                            self._line_span(line, payload_start + 1, payload),
                        )
                    )
            else:
                nodes.append(
                    RawTex(
                        payload,
                        self._line_span(line, payload_start + 1, payload),
                    )
                )

        continuation = self._sequence_continuation(base, entry_span)
        nodes.extend(continuation.nodes)

        value_end = entry_span.end
        for node in nodes:
            value_end = max(value_end, node.span.end)
            suite = getattr(node, "suite", None)
            if suite is not None:
                value_end = max(value_end, suite.span.end)
        value_span = SourceSpan(entry_span.file, entry_span.start, value_end)
        value = Block(tuple(nodes), value_span)
        return SequenceEntry(value, marker_span, value_span)

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

    def _sequence_structural_value(
        self,
        base: int,
        result: HeaderScanResult,
        header_span: SourceSpan,
    ) -> ParsedInvocation | SpecialInvocation | Stack:
        if result.suite_mode is None:
            if len(result.segments) == 1:
                return result.segments[0]
            return Stack(result.segments, None, header_span)

        suite = self._parse_suite(
            base,
            result,
            header_span,
        )

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


def parse(source: str, filename: str = "<string>") -> Document:
    """Parse source text into the syntax AST."""

    return _Parser(source, filename).parse()


__all__ = ["HeaderScanResult", "HeaderScanner", "parse", "scan_group"]
