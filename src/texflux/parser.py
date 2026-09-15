"""Physical-line parser and top-level structural header scanner."""

from __future__ import annotations

from dataclasses import dataclass, replace
import string
from typing import Final, TypeAlias

from .ast import (
    BINDING_OPENER,
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
from .syntax import (
    RAW_BEGIN_MARKER,
    RAW_END_MARKER,
    RAW_LINE_MARKER,
    RAW_MODE_NAMES,
    is_escaped,
)


#: What one structural header line parses to.
_Structural: TypeAlias = ParsedInvocation | SpecialInvocation | Stack


@dataclass(frozen=True, slots=True)
class HeaderScanResult:
    """One physical header; a trailing separator requests another line."""

    segments: tuple[ParsedInvocation | SpecialInvocation, ...]
    suite_mode: SuiteMode | None
    suite_span: SourceSpan | None
    continuation_span: SourceSpan | None = None

    @property
    def closed_single(self) -> bool:
        """One segment, no suite, no continuation: the line is complete."""

        return (
            self.suite_mode is None
            and self.continuation_span is None
            and len(self.segments) == 1
        )


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


def _marker_span(filename: str, line: _PhysicalLine) -> SourceSpan:
    text = line.text.strip(" ")
    start = SourcePosition(line.number, line.indent + 1)
    return SourceSpan(
        filename,
        start,
        SourcePosition(line.number, line.indent + 1 + len(text)),
    )


def _line_marker(line: _PhysicalLine) -> str | None:
    """The raw-mode marker a whole line consists of, or None.

    A marker carries no group, no suite suffix and no comment, so matching the
    entire line is the whole rule.
    """

    text = line.text.strip(" ")
    return text if text in (RAW_BEGIN_MARKER, RAW_END_MARKER) else None


def _raw_escape_line(line: _PhysicalLine) -> bool:
    """Whether a whole line is a '!|' raw escape.

    '_block' strips the block base and any extra spaces before classifying a
    line, so the marker always sits at the line's first non-space character.
    That makes this decidable here, before any block base is known, which is
    what the whole-file tab pre-scan needs.
    """

    return line.text.lstrip(" ").startswith(RAW_LINE_MARKER)


def _scan_raw_regions(
    lines: tuple[_PhysicalLine, ...],
    filename: str,
) -> dict[int, int]:
    """Pair the raw-region markers, as ``begin index -> end index``.

    Pairing is a line-level property, so it is resolved before parsing: the
    tab prohibition has to know which lines are verbatim, and an unpaired
    marker leaves the rest of the file's line structure meaningless.
    """

    regions: dict[int, int] = {}
    begin: int | None = None
    indent = 0
    for index, line in enumerate(lines):
        marker = _line_marker(line)
        if marker is None:
            continue
        if begin is None:
            if marker == RAW_END_MARKER:
                raise ParseError(
                    f"'{RAW_END_MARKER}' has no matching '{RAW_BEGIN_MARKER}'",
                    _marker_span(filename, line),
                    code="P001",
                )
            begin, indent = index, line.indent
        elif marker == RAW_END_MARKER and line.indent == indent:
            regions[begin] = index
            begin = None
    if begin is not None:
        raise ParseError(
            f"'{RAW_BEGIN_MARKER}' is not closed by '{RAW_END_MARKER}'",
            _marker_span(filename, lines[begin]),
            code="P002",
        )
    return regions


def _block_span(boundary: SourceSpan, nodes: list[Node]) -> SourceSpan:
    end = max((node.span.end for node in nodes), default=boundary.end)
    return SourceSpan(boundary.file, boundary.start, max(boundary.end, end))


def _node_end(node: Node) -> SourcePosition:
    """Where a node's source ends, its suite included."""

    end = node.span.end
    if (
        isinstance(node, (ParsedInvocation, SpecialInvocation, Stack))
        and node.suite is not None
    ):
        end = max(end, node.suite.span.end)
    return end


#: The suite markers, longest first so ':::' wins over the ':' it opens with.
#: A lone ':' is deliberately absent: TeX prose ends a line with one, which is
#: why it is the one punctuation TeXFlux leaves entirely to TeX.
_SUITE_MARKERS: Final = (
    (":::", SuiteMode.SEQUENCE),
    ("::", SuiteMode.BLOCK),
)

#: TeXFlux names are ASCII-only, independent of the host locale.
_NAME_START: Final = frozenset(string.ascii_letters)
_NAME_CHARS: Final = frozenset(string.ascii_letters + string.digits + "_")


class _StrayBrace(Exception):
    """A ``}`` that closes nothing inside a bracket or binding group."""


#: How each inline group scans: its closer, whether another opener of the
#: same kind deepens it, and whether a balanced '{...}' inside it is skipped
#: as opaque text, so a '{a,b}' value or a '{]}' stays out of the count.
_GROUP_SCAN: Final = {
    "<": (">", False, False),
    "{": ("}", True, False),
    "[": ("]", True, True),
    "(": (")", True, True),
}


def _group_end(text: str, start: int) -> int | None:
    """The offset of the closer balancing ``text[start]``, or ``None`` if unclosed."""

    opener = text[start]
    closer, nests, skips_braces = _GROUP_SCAN[opener]
    depth = 1
    index = start + 1
    while index < len(text):
        char = text[index]
        if is_escaped(text, index):
            pass
        elif skips_braces and char == "{":
            inner = _group_end(text, index)
            if inner is None:
                return None
            index = inner
        elif skips_braces and char == "}":
            raise _StrayBrace
        elif nests and char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def scan_group(
    text: str,
    start: int,
    *,
    span: SourceSpan,
) -> tuple[int, str]:
    """Scan one inline group, returning the end offset and raw content."""

    opener = text[start]
    if opener not in _GROUP_SCAN:
        raise ParseError("invalid group opener", span, code="P009")
    try:
        end = _group_end(text, start)
    except _StrayBrace:
        # A code names one construction site, so each opener keeps its own.
        if opener == "[":
            raise ParseError("mismatched group delimiter", span, code="P005") from None
        raise ParseError("mismatched group delimiter", span, code="P007") from None
    if end is not None:
        return end + 1, text[start + 1 : end]
    match opener:
        case "<":
            raise ParseError("unclosed overlay group", span, code="P003")
        case "{":
            raise ParseError("unclosed required group", span, code="P004")
        case "[":
            raise ParseError("unclosed optional group", span, code="P006")
        case _:
            raise ParseError("unclosed binding list", span, code="P008")


def _has_top_level_suite_marker(text: str, *, span: SourceSpan) -> bool:
    r"""Check a failed command scan for a reserved trailing suite marker.

    Both markers end in ``::`` and a marker is the last thing on its line, so
    the only question left is whether that spelling sits at depth zero. A
    group the scanner cannot balance has no depth-zero end, which keeps a
    marker spelling inside an unclosed group opaque: ``\newcommand{\x}{a::``
    is the raw TeX it looks like. A lone trailing colon is TeX prose rather
    than a marker, so it reserves nothing either way.
    """

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
    return end > 1 and text[end - 2 : end] == "::"


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

    def _error(
        self,
        message: str,
        offset: int = 0,
        *,
        code: str,
    ) -> ParseError:
        end = min(offset + 1, self.end)
        return ParseError(message, self._span(offset, end), code=code)

    def scan(self) -> HeaderScanResult:
        if self.end == 0:
            raise self._error("empty structural header", code="P010")

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

            raise self._error(
                "unexpected token in structural header; write '!| ' in front "
                "of a line that has to stay raw TeX",
                position,
                code="P011",
            )

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
        """Scan the trailing ``::`` block or ``:::`` sequence marker.

        A lone colon is TeX prose rather than a marker, so it reserves no
        line: a command header ending in one falls back to raw TeX, and an
        ``@`` or ``!`` header ending in one is a malformed header. A stack
        separator behind it still reserves the line, because ``>>`` is
        structural wherever it is written.
        """

        marker_start = position
        for marker, mode in _SUITE_MARKERS:
            if self.text.startswith(marker, position):
                break
        else:
            if self.text.startswith(">>", self._skip_spaces(position + 1)):
                self.saw_structure = True
            raise self._error(
                "unexpected ':' in a structural header; the suite markers are "
                "'::' and ':::'",
                position,
                code="P013",
            )

        self.saw_structure = True
        position = self._skip_spaces(position + len(marker))
        if position != self.end:
            raise self._error(
                "trailing token after suite marker",
                position,
                code="P012",
            )
        return mode, self._span(marker_start, position)

    def _stack_separator(self, position: int, *, spaced: bool) -> int:
        """Return the next segment's offset, or the line end for continuation."""

        if not spaced or (
            position + 2 != self.end
            and self.text[position + 2 : position + 3] != " "
        ):
            raise self._error(
                "stack separator requires surrounding spaces",
                position,
                code="P014",
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
            raise self._error("missing structural segment", position, code="P015")

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
                    code="P016",
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
            raise self._error("invalid structural name", position, code="P017")
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
        if prefix == "!" and name in RAW_MODE_NAMES:
            raise self._error(
                f"'!{name}' must stand alone on its own line",
                segment_start,
                code="P018",
            )

        groups: list[Argument] = []
        while position < self.end and self.text[position] in GROUP_OPENERS:
            group, position = self._inline_group(position)
            groups.append(group)

        # Only a special carries a binding list, and only after its groups, so
        # '(' stays ordinary text everywhere a command or environment reads it.
        binding = None
        if (
            prefix == "!"
            and position < self.end
            and self.text[position] == BINDING_OPENER
        ):
            binding, position = self._inline_group(position)
            groups.append(binding)

        if position < self.end and self.text[position] not in " :>":
            if binding is not None and self.text[position] in GROUP_OPENERS:
                raise self._error(
                    "a special's '(...)' list must follow its groups",
                    position,
                    code="P019",
                )
            if binding is not None and self.text[position] == BINDING_OPENER:
                raise self._error(
                    "a special accepts at most one '(...)' list",
                    position,
                    code="P020",
                )
            raise self._error(
                "unexpected token after structural name or group",
                position,
                code="P021",
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
    Those tokens are ``::``, ``:::`` and ``>>``, and TeX prose writes none of
    them. A lone trailing colon is not one of them, so ``\\textbf{Note}:`` and
    ``\\item Note:`` are raw TeX whatever follows them: a line is classified
    by reading that line and nothing else.
    """

    scanner = HeaderScanner(text, span=span)
    try:
        result = scanner.scan()
    except ParseError:
        if scanner.saw_structure:
            raise
        if not _has_top_level_suite_marker(text, span=span):
            return None
        raise
    return result


def _requires_value(result: HeaderScanResult) -> bool:
    """Whether this header's suite has to produce at least one value.

    A command consumes its suite as arguments, so an empty one would emit a
    silent ``{}``. A container or a special accepts an empty suite.
    """

    return (
        isinstance(result.segments[-1], ParsedInvocation)
        and result.segments[-1].kind is InvocationKind.COMMAND
    )


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
        self.raw_regions = _scan_raw_regions(self.lines, filename)
        # Raw regions and '!|' escapes are the two constructs that keep their
        # body exactly as written, tabs included, so both are exempt here.
        self.tab_exempt = frozenset(
            {
                index
                for begin, end in self.raw_regions.items()
                for index in range(begin + 1, end)
            }
            | {
                index
                for index, line in enumerate(self.lines)
                if _raw_escape_line(line)
            }
        )
        for index, line in enumerate(self.lines):
            if index not in self.tab_exempt:
                self._reject_tab(line)

    def _reject_tab(self, line: _PhysicalLine) -> None:
        tab = line.text.find("\t")
        if tab >= 0:
            raise ParseError(
                "tab characters are not allowed; keep one after '!| ' or "
                "inside a raw-mode region",
                SourceSpan(
                    self.filename,
                    SourcePosition(line.number, tab + 1),
                    SourcePosition(line.number, tab + 2),
                ),
                code="P022",
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
    ) -> Block:
        nodes = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.blank:
                run = self._blank_run(base)
                if run is None:
                    break
                nodes.extend(self._blank_nodes(run))
                continue

            if line.indent < base:
                break

            rest = line.text[base:]
            extra = len(rest) - len(rest.lstrip(" "))
            first = rest[extra : extra + 1]

            # Escapes precede the indentation rules, so an escaped line may
            # sit deeper than the base and keeps those extra spaces.
            escaped = self._escaped_raw(
                line,
                rest[extra:],
                line.indent + 1,
                self._line_span(line, base + 1, rest),
                indent=rest[:extra],
            )
            if escaped is not None:
                nodes.append(escaped)
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
                if rest.rstrip(" ") == RAW_BEGIN_MARKER:
                    nodes.extend(self._raw_region(base))
                    continue
                nodes.append(self._structural_line(line, base))
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

    def _blank_nodes(self, run: range) -> list[Node]:
        """One empty raw line for each physical line of a blank run."""

        return [RawTex("", self._line_span(self.lines[index])) for index in run]

    def _escaped_raw(
        self,
        line: _PhysicalLine,
        text: str,
        column: int,
        span: SourceSpan,
        *,
        indent: str = "",
    ) -> RawTex | None:
        """The raw line a leading escape in ``text`` produces, or ``None``.

        ``@@`` and ``!!`` strip one prefix character and stay a text field;
        ``!|`` strips the whole marker and one space and is verbatim. Both
        keep ``indent``, the spaces beyond the block base, in front of the
        body. ``column`` is where ``text`` starts, for the ``!|`` diagnostic.
        """

        head = text[:2]
        if head in ("@@", "!!"):
            return RawTex(indent + text[1:], span)
        if head == RAW_LINE_MARKER:
            tail = self._raw_line_tail(line, text[len(RAW_LINE_MARKER) :], column)
            return RawTex(indent + tail, span, verbatim=True)
        return None

    def _raw_line_tail(
        self,
        line: _PhysicalLine,
        tail: str,
        marker_column: int,
    ) -> str:
        """Read the body of one '!|' escape, or reject a missing separator.

        One space separates the marker from the body and is not part of it.
        Everything after that space is kept exactly as written up to the
        newline, the way a raw-mode region keeps its own lines.
        """

        if tail and not tail.startswith(" "):
            raise ParseError(
                f"'{RAW_LINE_MARKER}' must be followed by one space"
                " or end the line",
                self._line_span(line, marker_column, RAW_LINE_MARKER),
                code="P023",
            )
        return tail[1:]

    def _raw_region(self, base: int) -> list[Node]:
        """Consume one '!BEGIN_RAW_MODE' region and return its verbatim lines.

        The region's extent was fixed before parsing, so nothing here scans a
        header, honours an escape, or lets a dedent close the enclosing block.
        """

        try:
            end = self.raw_regions[self.index]
        except KeyError:
            # A whole-line marker at an unexpected nesting level can be
            # swallowed by the physical pre-scan as a nested BEGIN.  Never
            # expose that implementation detail as an uncaught KeyError.
            raise self._indent_error(self.lines[self.index]) from None
        nodes: list[Node] = []
        for index in range(self.index + 1, end):
            line = self.lines[index]
            cut = min(base, line.indent)
            text = line.text[cut:]
            nodes.append(
                RawTex(text, self._line_span(line, cut + 1, text), verbatim=True)
            )
        self.index = end + 1
        return nodes

    def _indent_error(self, line: _PhysicalLine) -> ParseError:
        return ParseError(
            "invalid structural indentation; a structural line sits at its "
            "suite base, and a literal '@' or '!' line is written '@@' or '!!'",
            self._line_span(line, line.indent + 1),
            code="P024",
        )

    def _scan_command_header(
        self,
        line: _PhysicalLine,
        base: int,
    ) -> HeaderScanResult | None:
        """Scan a ``\\`` line, or return ``None`` when it stays ordinary TeX."""

        result = _scan_structural_header(
            line.text[base:],
            self._header_span(line, base),
        )
        # A closed single-segment command line is ordinary TeX.
        return None if result is None or result.closed_single else result

    def _try_structural_command(
        self,
        line: _PhysicalLine,
        base: int,
    ) -> _Structural | None:
        result = self._scan_command_header(line, base)
        return None if result is None else self._statement(line, base, result)

    def _structural_line(self, line: _PhysicalLine, base: int) -> _Structural:
        """Parse an ``@`` or ``!`` line, which has to scan as a header."""

        header_span = self._header_span(line, base)
        result = HeaderScanner(line.text[base:], span=header_span).scan()
        return self._statement(line, base, result)

    def _statement(
        self,
        line: _PhysicalLine,
        base: int,
        result: HeaderScanResult,
    ) -> _Structural:
        """Consume the header line and attach whatever suite it asks for."""

        self.index += 1
        if result.closed_single:
            self._reject_missing_suite(base, result.segments[0])
        return self._structural_node(base, result, self._header_span(line, base))

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
                "environment directives require a suite marker '::' or "
                "':::'; a literal '@' line is written '@@'",
                segment.span,
                code="P025",
            )
        next_index = self._next_nonblank(self.index)
        if next_index is not None and self.lines[next_index].indent >= base + 4:
            line = self.lines[next_index]
            raise ParseError(
                "indented lines require a suite marker '::' or ':::'",
                self._line_span(line, line.indent + 1),
                code="P026",
            )

    def _structural_node(
        self,
        base: int,
        result: HeaderScanResult,
        header_span: SourceSpan,
    ) -> _Structural:
        """Attach the parsed suite, if any, to one scanned header."""

        if result.continuation_span is not None:
            segments = list(result.segments)
            while result.continuation_span is not None:
                if self.index == len(self.lines):
                    raise ParseError(
                        "stack separator needs a following segment",
                        result.continuation_span,
                        code="P027",
                    )
                line = self.lines[self.index]
                if line.blank or line.indent != base:
                    raise ParseError(
                        "stack continuation requires the next line at the same indentation",
                        self._line_span(line, line.indent + 1),
                        code="P028",
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
                    code="P029",
                )
            suite = self._sequence_suite(suite_base, header_span)
            if not suite.nodes and _requires_value(result):
                raise ParseError(
                    "sequence suites require at least one '-' or '+' value entry",
                    header_span,
                    code="P030",
                )
            return suite
        if (
            next_index is not None
            and self.lines[next_index].indent >= suite_base
        ):
            return self._block(suite_base, header_span)
        if _requires_value(result):
            # An empty '{}' is raw TeX the author can write directly, so a
            # command's marker always owns a body and '\texttt{std}::' cannot
            # quietly become '\texttt{std}{}'.
            raise ParseError(
                "command block suites require an indented body",
                header_span,
                code="P037",
            )
        return Block((), header_span)

    def _sequence_suite(self, base: int, boundary: SourceSpan) -> Block:
        """Parse a ``::`` suite whose values are marked with ``-`` or ``+``."""

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
                    code="P031",
                )
            marker = line.text[base]
            if marker not in "-+":
                raise ParseError(
                    "sequence suites require '-' or '+' value entries",
                    self._line_span(line, base + 1),
                    code="P032",
                )
            entries.append(self._sequence_entry(line, base, marker))
        return Block(tuple(entries), _block_span(boundary, entries))

    def _sequence_entry(
        self,
        line: _PhysicalLine,
        base: int,
        marker: str,
    ) -> SequenceEntry:
        marker_span = self._line_span(line, base + 1, marker)
        entry_span = self._line_span(line, base + 1, line.text[base:])
        payload_start = base + 1
        while payload_start < len(line.text) and line.text[payload_start] == " ":
            payload_start += 1
        payload_raw = line.text[payload_start:]
        payload = payload_raw if marker == "+" else payload_raw.rstrip(" ")

        self.index += 1

        if marker == "+":
            return self._explicit_sequence_entry(
                line,
                payload,
                payload_start,
                marker_span,
                entry_span,
            )

        nodes: list[Node] = []
        if payload:
            payload_span = self._line_span(line, payload_start + 1, payload)
            escaped = self._escaped_raw(
                line,
                payload,
                payload_start + 1,
                payload_span,
            )
            if escaped is not None:
                nodes.append(escaped)
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

        value_end = max([entry_span.end, *map(_node_end, nodes)])
        value_span = SourceSpan(entry_span.file, entry_span.start, value_end)
        value = Block(tuple(nodes), value_span)
        return SequenceEntry(value, marker_span, value_span)

    def _explicit_sequence_entry(
        self,
        line: _PhysicalLine,
        payload: str,
        payload_start: int,
        marker_span: SourceSpan,
        entry_span: SourceSpan,
    ) -> SequenceEntry:
        """Parse a ``+`` entry as exactly one opaque authored group.

        The group is scanned only after physical continuation lines have been
        dedented.  None of those lines go through header scanning or the
        ``@@``/``!!`` raw-line escapes: an explicit group is authored TeX.
        """

        payload_span = self._line_span(line, payload_start + 1, payload)
        if not payload or payload[0] not in GROUP_OPENERS:
            raise ParseError(
                "explicit sequence entries require one '{...}', '[...]', "
                "or '<...>' group",
                payload_span,
                code="P033",
            )

        nodes: list[Node] = [RawTex(payload, payload_span)]
        continuation = self._raw_sequence_continuation(
            base=line.indent,
            boundary=entry_span,
        )
        nodes.extend(continuation.nodes)
        raw_nodes: list[RawTex] = []
        for node in nodes:
            if not isinstance(node, RawTex):
                raise ParseError(
                    "explicit sequence entries require opaque raw text",
                    node.span,
                    code="P034",
                )
            raw_nodes.append(node)
        text = "\n".join(node.text for node in raw_nodes)

        kind = GroupKind.from_opener(payload[0])
        try:
            end, _ = scan_group(text, 0, span=payload_span)
        except ParseError as error:
            raise ParseError(
                "explicit sequence entries require one balanced group",
                error.span,
                code="P035",
            ) from None
        if any(char != " " for char in text[end:]):
            raise ParseError(
                "explicit sequence entries require exactly one group",
                payload_span,
                code="P036",
            )

        kept = self._truncate_raw_nodes(raw_nodes, end)
        value = Block(tuple(kept), _block_span(entry_span, kept))
        return SequenceEntry(value, marker_span, value.span, kind)

    def _raw_sequence_continuation(
        self,
        base: int,
        boundary: SourceSpan,
    ) -> Block:
        """Read a sequence continuation without interpreting its contents."""

        next_index = self._next_nonblank(self.index)
        if next_index is None or self.lines[next_index].indent <= base:
            return Block((), boundary)

        continuation_base = self.lines[next_index].indent
        nodes: list[Node] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.blank:
                run = self._blank_run(continuation_base)
                if run is None:
                    break
                nodes.extend(self._blank_nodes(run))
                continue
            if line.indent < continuation_base:
                break
            marker = _line_marker(line)
            if marker is not None:
                if line.indent != continuation_base:
                    # Explicit groups are opaque, but raw-mode markers retain
                    # their physical-line indentation rule everywhere a
                    # continuation block can occur.
                    raise self._indent_error(line)
                if marker == RAW_BEGIN_MARKER:
                    nodes.extend(self._raw_region(continuation_base))
                    continue
            if self.index in self.tab_exempt:
                # An explicit '+' group body is opaque authored TeX, so '!|'
                # is not an escape here and the line never earned its
                # exemption from the whole-file tab pre-scan.
                self._reject_tab(line)
            raw_text = line.text[continuation_base:]
            nodes.append(
                RawTex(raw_text, self._line_span(line, continuation_base + 1, raw_text))
            )
            self.index += 1
        return Block(tuple(nodes), _block_span(boundary, nodes))

    def _truncate_raw_nodes(
        self,
        nodes: list[RawTex],
        length: int,
    ) -> list[RawTex]:
        """Keep the first ``length`` joined characters of raw line nodes."""

        kept: list[RawTex] = []
        remaining = length
        for index, node in enumerate(nodes):
            if remaining <= len(node.text):
                if remaining:
                    text = node.text[:remaining]
                    kept.append(
                        replace(
                            node,
                            text=text,
                            span=SourceSpan(
                                node.span.file,
                                node.span.start,
                                node.span.start.advance(text),
                            ),
                        )
                    )
                break
            kept.append(node)
            remaining -= len(node.text)
            if index < len(nodes) - 1:
                remaining -= 1
        return kept

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
