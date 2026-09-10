"""Physical-line parser and top-level structural header scanner."""

from __future__ import annotations

from dataclasses import dataclass

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    Document,
    GroupKind,
    InvocationKind,
    ParsedInvocation,
    RawTex,
    SourceLocation,
    SpecialInvocation,
    Stack,
)
from .errors import ParseError


@dataclass(frozen=True, slots=True)
class HeaderScanResult:
    segments: tuple[ParsedInvocation | SpecialInvocation, ...]
    suite: bool


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
    loc: SourceLocation,
) -> tuple[int, str]:
    """Scan one inline group, returning the end offset and raw content."""

    opener = text[start]
    if opener == "<":
        index = start + 1
        while index < len(text):
            if text[index] == ">" and not _is_escaped(text, index):
                return index + 1, text[start + 1 : index]
            index += 1
        raise ParseError("unclosed overlay group", loc)

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
        raise ParseError("unclosed required group", loc)

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
                        raise ParseError("mismatched group delimiter", loc)
                    brace_depth -= 1
                elif brace_depth == 0 and char == "[":
                    bracket_depth += 1
                elif brace_depth == 0 and char == "]":
                    bracket_depth -= 1
                    if bracket_depth == 0:
                        return index + 1, text[start + 1 : index]
            index += 1
        raise ParseError("unclosed optional group", loc)

    raise ParseError("invalid group opener", loc)


class HeaderScanner:
    """Scan one structural header while keeping group contents opaque."""

    _PREFIXES = {"\\": InvocationKind.COMMAND, "@": InvocationKind.ENVIRONMENT}

    def __init__(self, text: str, *, loc: SourceLocation):
        self.text = text
        self.loc = loc
        self.end = len(text.rstrip(" "))
        self.saw_structure = False

    def _loc(self, offset: int) -> SourceLocation:
        return SourceLocation(self.loc.file, self.loc.line, self.loc.column + offset)

    def _error(self, message: str, offset: int = 0) -> ParseError:
        return ParseError(message, self._loc(offset))

    def scan(self) -> HeaderScanResult:
        if self.end == 0:
            raise self._error("empty structural header")

        segments: list[ParsedInvocation | SpecialInvocation] = []
        position = 0
        first = True
        suite = False

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
                self.saw_structure = True
                suite = True
                position += 1
                while position < self.end and self.text[position] == " ":
                    position += 1
                if position != self.end:
                    raise self._error("trailing token after suite marker", position)
                break

            if self.text.startswith(">>", position):
                self.saw_structure = True
                if spaces == 0:
                    raise self._error("stack separator requires surrounding spaces", position)
                position += 2
                if position >= self.end or self.text[position] != " ":
                    raise self._error("stack separator requires surrounding spaces", position)
                while position < self.end and self.text[position] == " ":
                    position += 1
                if position == self.end:
                    raise self._error("stack separator needs a following segment", position)
                continue

            raise self._error("unexpected token in structural header", position)

        return HeaderScanResult(
            tuple(segments),
            suite,
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
            group_loc = self._loc(position)
            position, value = scan_group(self.text, position, loc=group_loc)
            groups.append(
                Argument(group_kind, value, ArgumentLayout.INLINE, group_loc)
            )

        if position < self.end and self.text[position] not in " :>":
            raise self._error(
                "unexpected token after structural name or group",
                position,
            )

        loc = self._loc(segment_start)
        if prefix == "!":
            return SpecialInvocation(name, tuple(groups), None, loc), position
        return ParsedInvocation(kind, name, tuple(groups), None, loc), position


class _Parser:
    def __init__(self, source: str, filename: str):
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        physical = source.split("\n")
        if physical and physical[-1] == "":
            physical.pop()
        self.filename = filename
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
                    SourceLocation(filename, line.number, tab + 1),
                )

    def parse(self) -> Document:
        loc = SourceLocation(self.filename, 1, 1)
        body = self._block(0, loc)
        if self.index != len(self.lines):
            line = self.lines[self.index]
            raise ParseError("unexpected indentation", self._line_loc(line))
        return Document(body, loc)

    def _line_loc(
        self,
        line: _PhysicalLine,
        *,
        first_nonspace: bool = True,
    ) -> SourceLocation:
        column = line.indent + 1 if first_nonspace else 1
        return SourceLocation(self.filename, line.number, column)

    def _next_nonblank(self, index: int) -> int | None:
        while index < len(self.lines) and self.lines[index].blank:
            index += 1
        return None if index == len(self.lines) else index

    def _block(
        self,
        base: int,
        loc: SourceLocation,
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
                if (
                    next_index >= len(self.lines)
                    or self.lines[next_index].indent < base
                ):
                    if base == 0:
                        for blank_index in range(run_start, self.index):
                            blank_line = self.lines[blank_index]
                            nodes.append(
                                RawTex(
                                    "",
                                    SourceLocation(
                                        self.filename,
                                        blank_line.number,
                                        1,
                                    ),
                                )
                            )
                        continue
                    self.index = run_start
                    break
                for blank_index in range(run_start, self.index):
                    blank_line = self.lines[blank_index]
                    nodes.append(
                        RawTex(
                            "",
                            SourceLocation(
                                self.filename,
                                blank_line.number,
                                1,
                            ),
                        )
                    )
                continue

            if line.indent < base:
                break

            rest = line.text[base:]
            extra = len(rest) - len(rest.lstrip(" "))
            first = rest[extra : extra + 1]

            if first == "@" and rest[extra : extra + 2] == "@@":
                raw_text = rest[:extra] + rest[extra + 1 :]
                nodes.append(RawTex(raw_text, self._line_loc(line)))
                self.index += 1
                continue

            if raw_suite:
                nodes.append(RawTex(line.text[base:], self._line_loc(line)))
                self.index += 1
                continue

            if first in {"@", "!"} and line.indent != base:
                raise ParseError(
                    "invalid structural indentation",
                    self._line_loc(line),
                )

            if line.indent != base:
                nodes.append(RawTex(line.text[base:], self._line_loc(line)))
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
            nodes.append(RawTex(raw_text, self._line_loc(line)))
            self.index += 1

        return Block(tuple(nodes), loc)

    def _try_structural_command(
        self,
        line: _PhysicalLine,
        base: int,
    ):
        directive_loc = SourceLocation(self.filename, line.number, base + 1)
        scanner = HeaderScanner(line.text[base:], loc=directive_loc)
        try:
            result = scanner.scan()
        except ParseError:
            if scanner.saw_structure:
                raise
            return None
        if not result.suite and len(result.segments) == 1:
            return None
        return self._directive_result(line, base, result, directive_loc)

    def _directive(self, line: _PhysicalLine, base: int):
        if line.indent != base:
            raise ParseError(
                "invalid structural indentation",
                self._line_loc(line),
            )
        directive_loc = SourceLocation(self.filename, line.number, base + 1)
        result = HeaderScanner(line.text[base:], loc=directive_loc).scan()
        return self._directive_result(line, base, result, directive_loc)

    def _directive_result(
        self,
        line: _PhysicalLine,
        base: int,
        result: HeaderScanResult,
        directive_loc: SourceLocation,
    ):
        self.index += 1

        if not result.suite:
            if len(result.segments) > 1:
                raise ParseError(
                    "stack requires a suite marker ':'",
                    directive_loc,
                )
            segment = result.segments[0]
            if (
                isinstance(segment, ParsedInvocation)
                and segment.kind is InvocationKind.ENVIRONMENT
            ):
                raise ParseError(
                    "environment directives require a suite marker ':'",
                    segment.loc,
                )
            next_index = self._next_nonblank(self.index)
            if (
                next_index is not None
                and self.lines[next_index].indent >= base + 4
            ):
                raise ParseError(
                    "indented lines require a suite marker ':'",
                    self._line_loc(self.lines[next_index]),
                )
            return segment

        suite_base = base + 4
        next_index = self._next_nonblank(self.index)
        if (
            next_index is None
            or self.lines[next_index].indent < suite_base
        ):
            raise ParseError(
                "suite marker ':' requires an indented suite",
                directive_loc,
            )
        raw_suite = (
            isinstance(result.segments[-1], SpecialInvocation)
            and result.segments[-1].name == "items"
        )
        suite = self._block(
            suite_base,
            directive_loc,
            raw_suite=raw_suite,
        )

        if len(result.segments) == 1:
            segment = result.segments[0]
            if isinstance(segment, ParsedInvocation):
                return ParsedInvocation(
                    segment.kind,
                    segment.name,
                    segment.groups,
                    suite,
                    segment.loc,
                )
            return SpecialInvocation(
                segment.name,
                segment.groups,
                suite,
                segment.loc,
            )
        return Stack(result.segments, suite, directive_loc)


def parse(source: str, filename: str = "<string>") -> Document:
    """Parse source text into the syntax AST."""

    return _Parser(source, filename).parse()


__all__ = ["HeaderScanResult", "HeaderScanner", "parse", "scan_group"]
