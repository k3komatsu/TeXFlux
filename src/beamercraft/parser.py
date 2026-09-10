"""Physical-line parser and directive header scanner."""

from __future__ import annotations

from dataclasses import dataclass

from .ast import (
    Argument,
    ArgumentLayout,
    Block,
    Document,
    GroupKind,
    ParsedGeneric,
    RawTex,
    SourceLocation,
    SpecialInvocation,
    Stack,
)
from .errors import ParseError


@dataclass(frozen=True, slots=True)
class HeaderScanResult:
    segments: tuple[ParsedGeneric | SpecialInvocation, ...]
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
    """Scan one inline group, returning the end offset and its raw content."""

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
    """Scan only a single directive header; TeX content remains opaque."""

    def __init__(self, text: str, *, loc: SourceLocation):
        self.text = text
        self.loc = loc
        self.end = len(text.rstrip(" "))

    def _loc(self, offset: int) -> SourceLocation:
        return SourceLocation(self.loc.file, self.loc.line, self.loc.column + offset)

    def _error(self, message: str, offset: int = 0) -> ParseError:
        return ParseError(message, self._loc(offset))

    def scan(self) -> HeaderScanResult:
        if self.end == 0:
            raise self._error("empty directive")

        segments: list[ParsedGeneric | SpecialInvocation] = []
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
                suite = True
                position += 1
                if position != self.end:
                    raise self._error("trailing token after suite marker", position)
                break

            if self.text.startswith(">>", position):
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

            raise self._error("unexpected token in directive header", position)

        return HeaderScanResult(tuple(segments), suite)

    def _segment(
        self,
        position: int,
        first: bool,
    ) -> tuple[ParsedGeneric | SpecialInvocation, int]:
        segment_start = position
        special = False

        if first:
            if position >= self.end or self.text[position] != "@":
                raise self._error("directive must start with '@'", position)
            position += 1
            if position < self.end and self.text[position] == "!":
                special = True
                position += 1
        else:
            if position < self.end and self.text[position] == "@":
                raise self._error("'@' is allowed only on the first segment", position)
            if position < self.end and self.text[position] == "!":
                special = True
                position += 1

        name_start = position
        if position >= self.end or not _is_ascii_letter(self.text[position]):
            raise self._error("invalid directive name", position)
        position += 1
        while position < self.end and _is_ascii_name_char(self.text[position]):
            position += 1
        if position < self.end and self.text[position] == "*":
            position += 1

        name = self.text[name_start:position]
        groups: list[Argument] = []
        while position < self.end and self.text[position] in "{[<":
            opener = self.text[position]
            kind = {
                "{": GroupKind.REQUIRED,
                "[": GroupKind.OPTIONAL,
                "<": GroupKind.OVERLAY,
            }[opener]
            group_loc = self._loc(position)
            position, value = scan_group(self.text, position, loc=group_loc)
            groups.append(Argument(kind, value, ArgumentLayout.INLINE, group_loc))

        if position < self.end and self.text[position] not in " :>":
            raise self._error("unexpected token after directive name or group", position)

        loc = self._loc(segment_start)
        if special:
            return SpecialInvocation(name, tuple(groups), None, loc), position
        return ParsedGeneric(name, tuple(groups), None, loc), position


class _Parser:
    def __init__(self, source: str, filename: str):
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        physical = source.split("\n")
        if physical and physical[-1] == "":
            physical.pop()
        self.filename = filename
        self.lines = tuple(_PhysicalLine(text, number) for number, text in enumerate(physical, 1))
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

    def _line_loc(self, line: _PhysicalLine, *, first_nonspace: bool = True) -> SourceLocation:
        column = line.indent + 1 if first_nonspace else 1
        return SourceLocation(self.filename, line.number, column)

    def _next_nonblank(self, index: int) -> int | None:
        while index < len(self.lines) and self.lines[index].blank:
            index += 1
        return None if index == len(self.lines) else index

    def _block(self, base: int, loc: SourceLocation) -> Block:
        nodes = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.blank:
                run_start = self.index
                while self.index < len(self.lines) and self.lines[self.index].blank:
                    self.index += 1
                next_index = self.index
                if next_index < len(self.lines) and self.lines[next_index].indent < base:
                    self.index = run_start
                    break
                for blank_index in range(run_start, self.index):
                    blank_line = self.lines[blank_index]
                    nodes.append(
                        RawTex(
                            "",
                            SourceLocation(self.filename, blank_line.number, 1),
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

            if first == "@":
                if line.indent != base:
                    raise ParseError("invalid directive indentation", self._line_loc(line))
                nodes.append(self._directive(line, base))
                continue

            raw_text = line.text[base:]
            nodes.append(RawTex(raw_text, self._line_loc(line)))
            self.index += 1

        return Block(tuple(nodes), loc)

    def _directive(self, line: _PhysicalLine, base: int):
        directive_loc = SourceLocation(self.filename, line.number, base + 1)
        result = HeaderScanner(line.text[base:], loc=directive_loc).scan()
        self.index += 1

        if not result.suite:
            if len(result.segments) > 1:
                raise ParseError("stack requires a suite marker ':'", directive_loc)
            next_index = self._next_nonblank(self.index)
            if next_index is not None and self.lines[next_index].indent >= base + 4:
                raise ParseError(
                    "indented lines require a suite marker ':'",
                    self._line_loc(self.lines[next_index]),
                )
            return result.segments[0]

        suite_base = base + 4
        next_index = self._next_nonblank(self.index)
        if next_index is None or self.lines[next_index].indent < suite_base:
            raise ParseError("suite marker ':' requires an indented suite", directive_loc)
        suite_loc = directive_loc
        suite = self._block(suite_base, suite_loc)
        if len(result.segments) == 1:
            segment = result.segments[0]
            if isinstance(segment, ParsedGeneric):
                return ParsedGeneric(segment.name, segment.groups, suite, segment.loc)
            return SpecialInvocation(segment.name, segment.groups, suite, segment.loc)
        return Stack(result.segments, suite, directive_loc)


def parse(source: str, filename: str = "<string>") -> Document:
    """Parse source text into the syntax AST."""

    return _Parser(source, filename).parse()


__all__ = ["HeaderScanResult", "HeaderScanner", "parse", "scan_group"]
