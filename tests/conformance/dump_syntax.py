"""Print a document's syntax tree in the shared comparison format.

The D implementation prints the same format from tools/dump_syntax.d. Comparing
the two catches a parsing difference at the one place where it is cheapest to
understand: right after parsing, before any later pass can hide it.

    python3 tests/conformance/dump_syntax.py FILE
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from texflux.ast import (  # noqa: E402
    Argument,
    Block,
    BraceGroup,
    Document,
    GenericInvocation,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SourceSpan,
    SpecialInvocation,
    Stack,
)
from texflux.errors import TeXFluxError  # noqa: E402
from texflux.parser import parse  # noqa: E402


def quote(text: str) -> str:
    """One text value, escaped so a line of the dump is always one line."""

    out = ['"']
    for character in text:
        if character in '"\\':
            out.append("\\" + character)
        elif character == "\n":
            out.append("\\n")
        elif character == "\r":
            out.append("\\r")
        elif character == "\t":
            out.append("\\t")
        else:
            out.append(character)
    out.append('"')
    return "".join(out)


def span_text(span: SourceSpan) -> str:
    return (
        f"{span.file}:{span.start.line}:{span.start.column}"
        f"-{span.end.line}:{span.end.column}"
    )


def optional_span(span: SourceSpan | None) -> str:
    return "-" if span is None else span_text(span)


def enum_text(value) -> str:
    return "-" if value is None else str(value)


class Dumper:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, depth: int, text: str) -> None:
        self.lines.append("  " * depth + text)

    def document(self, document: Document) -> None:
        self.emit(0, f"document {span_text(document.span)}")
        self.block(document.body, 1)

    def block(self, block: Block, depth: int) -> None:
        self.emit(depth, f"block {span_text(block.span)}")
        for node in block.nodes:
            self.node(node, depth + 1)

    def node(self, node, depth: int) -> None:
        match node:
            case RawTex():
                self.emit(
                    depth,
                    f"raw {span_text(node.span)}"
                    f" verbatim={'1' if node.verbatim else '0'}"
                    f" text={quote(node.text)}",
                )
                self.parts(node.parts, depth + 1)
            case ParsedInvocation():
                self.emit(
                    depth,
                    f"parsed {span_text(node.span)} kind={node.kind}"
                    f" name={quote(node.name)}"
                    f" suiteMode={enum_text(node.suite_mode)}"
                    f" suiteSpan={optional_span(node.suite_span)}",
                )
                self.groups(node.groups, depth + 1)
                self.suite(node.suite, depth + 1)
            case SpecialInvocation():
                self.emit(
                    depth,
                    f"special {span_text(node.span)} name={quote(node.name)}"
                    f" suiteMode={enum_text(node.suite_mode)}"
                    f" suiteSpan={optional_span(node.suite_span)}",
                )
                self.groups(node.groups, depth + 1)
                self.suite(node.suite, depth + 1)
            case SequenceEntry():
                self.emit(
                    depth,
                    f"entry {span_text(node.span)}"
                    f" marker={span_text(node.marker_span)}"
                    f" argumentKind={enum_text(node.argument_kind)}",
                )
                self.block(node.value, depth + 1)
            case Stack():
                self.emit(
                    depth,
                    f"stack {span_text(node.span)}"
                    f" suiteMode={enum_text(node.suite_mode)}"
                    f" suiteSpan={optional_span(node.suite_span)}",
                )
                self.emit(depth + 1, "segments")
                for segment in node.segments:
                    self.node(segment, depth + 2)
                self.suite(node.suite, depth + 1)
            case GenericInvocation():
                self.emit(
                    depth,
                    f"invocation {span_text(node.span)} name={quote(node.name)}",
                )
                self.groups(node.arguments, depth + 1)
                self.suite(node.body, depth + 1, "body")
            case BraceGroup():
                self.emit(
                    depth,
                    f"brace {span_text(node.span)} header={quote(node.header_raw)}",
                )
                self.parts(node.header_parts, depth + 1)
                self.block(node.body, depth + 1)
            case _:
                raise AssertionError(f"unknown node {type(node).__name__}")

    def groups(self, groups: tuple[Argument, ...], depth: int) -> None:
        for group in groups:
            self.emit(
                depth,
                f"group kind={group.kind} layout={group.layout}"
                f" {span_text(group.span)}",
            )
            if isinstance(group.value, Block):
                self.block(group.value, depth + 1)
            else:
                self.emit(depth + 1, f"text={quote(group.value)}")
            self.parts(group.parts, depth + 1)

    def suite(self, suite: Block | None, depth: int, label: str = "suite") -> None:
        if suite is None:
            return
        self.emit(depth, label)
        self.block(suite, depth + 1)

    def parts(self, parts, depth: int) -> None:
        if parts is None:
            return
        self.emit(depth, "parts")
        for fragment in parts:
            self.emit(
                depth + 1,
                f"fragment {span_text(fragment.span)}"
                f" scaffold={'1' if fragment.scaffold else '0'}"
                f" text={quote(fragment.text)}",
            )


def dump(path: str) -> str:
    source = Path(path).read_text(encoding="utf-8")
    try:
        document = parse(source, path)
    except TeXFluxError as error:
        return (
            f"error {error.code} {error.span.location} {quote(error.message)}\n"
        )
    dumper = Dumper()
    dumper.document(document)
    return "".join(line + "\n" for line in dumper.lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: dump_syntax.py FILE", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(dump(argv[1]).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
