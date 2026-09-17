"""Write the D test fixtures that record what the Python implementation does.

The D implementation has to agree with this one byte for byte, so the values it
is checked against are taken from here rather than written by hand. Run this
from the repository root after changing anything it captures:

    python3 tests/conformance/generate_fixtures.py

It rewrites tests/d/texflux_tests/fixtures.d and source/texflux/printable.d in
place.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "tests" / "d" / "texflux_tests" / "fixtures.d"
PRINTABLE = ROOT / "source" / "texflux" / "printable.d"


def d_string(text: str) -> str:
    """Spell a Python string as a D string literal with the same characters."""

    out = []
    for character in text:
        if character in '"\\':
            out.append("\\" + character)
        elif character == "\n":
            out.append("\\n")
        elif character == "\r":
            out.append("\\r")
        elif character == "\t":
            out.append("\\t")
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            out.append(f"\\x{ord(character):02x}")
        elif not character.isprintable():
            out.append(f"\\U{ord(character):08x}")
        else:
            out.append(character)
    return '"' + "".join(out) + '"'


def d_bytes(data: bytes) -> str:
    return "[" + ", ".join(f"0x{byte:02x}" for byte in data) + "]"


def ranges_where(holds) -> list[tuple[int, int]]:
    """The maximal runs of code points, inclusive, for which ``holds`` is true."""

    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for code in range(0x110000):
        if holds(chr(code)):
            if start is None:
                start = code
        elif start is not None:
            ranges.append((start, code - 1))
            start = None
    if start is not None:
        ranges.append((start, 0x10FFFF))
    return ranges


def write_ranges(write, name: str, ranges: list[tuple[int, int]]) -> None:
    write(f"immutable uint[2][] {name} = [")
    for low, high in ranges:
        write(f"    [0x{low:04X}, 0x{high:04X}],")
    write("];")


#: Byte sequences that are not UTF-8, chosen to reach each decoder complaint.
DECODE_CASES = [
    b"\xff",
    b"a\xff b",
    b"\xe6",
    b"\xe6\x97",
    b"\xe6\x97\xff",
    b"abc\xc3",
    b"\x80",
    b"\xc3\x28",
    b"\xf0\x9f\x98",
    b"\xed\xa0\x80",
    b"\xf5\x80\x80\x80",
    b"ab\xe3\x81",
    b"\xc0\x80",
    b"\xe0\x80\x80",
    b"\xf4\x90\x80\x80",
    b"\xf0\x8f\x80\x80",
    b"\xc2",
    b"\xc2\x41",
]

#: Paths whose quoting an operating-system error message would show.
QUOTED_CASES = [
    "plain.tfx",
    "with space.tfx",
    "quote'inside.tfx",
    'double"inside.tfx',
    "both'and\".tfx",
    "back\\slash.tfx",
    "tab\there.tfx",
    "newline\nhere.tfx",
    "日本語.tfx",
    "bell\x07.tfx",
    "del\x7f.tfx",
    # Non-printable code points are escaped by size, printable text is kept.
    "nbsp\xa0.tfx",
    "soft\xadhyphen.tfx",
    "wide\u3000space.tfx",
    "zero\u200bwidth.tfx",
    "line\u2028separator.tfx",
    "private\ue000use.tfx",
    "unassigned\U0010fffe.tfx",
    "emoji\U0001f600.tfx",
]

#: Documents covering every shape the published formats are built from.
JSON_CASES: list[tuple[str, object]] = [
    ("empty object", {}),
    ("empty array", []),
    ("flat", {"format": "texflux-ast", "version": 1, "root": 0}),
    ("nested", {"a": {"b": [1, 2, 3]}, "c": []}),
    ("text", {"text": 'quote " backslash \\ newline \n tab \t bell \x07 del \x7f'}),
    ("unicode", {"text": "日本😀", "name": "é"}),
    ("bools", {"yes": True, "no": False, "none": None}),
    ("deep", {"a": [{"b": [{"c": 1}]}]}),
    ("array of objects", [{"id": 0, "file": "a.tfx"}, {"id": 1, "file": "b.tfx"}]),
]


def render() -> str:
    lines: list[str] = []
    write = lines.append

    write("/**")
    write(" * Expected values taken from the reference implementation.")
    write(" *")
    write(" * These are generated rather than written, because their whole purpose is")
    write(" * to be what the other implementation actually produces. Regenerate them")
    write(" * from that implementation rather than editing one by hand.")
    write(" */")
    write("module texflux_tests.fixtures;")
    write("")

    write("/// Every code point the reference implementation calls whitespace.")
    write_ranges(write, "whitespaceRanges", ranges_where(str.isspace))
    write("")

    write("/// Every code point the reference implementation calls printable.")
    write_ranges(write, "printableRanges", ranges_where(str.isprintable))
    write("")

    write("/// A byte sequence that is not UTF-8, and the message it must produce.")
    write("struct DecodeCase { immutable(ubyte)[] data; string message; }")
    write("")
    write("immutable DecodeCase[] decodeFailures = [")
    for data in DECODE_CASES:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            write(f"    DecodeCase({d_bytes(data)}, {d_string(str(error))}),")
        else:
            raise SystemExit(f"decode case is valid UTF-8: {data!r}")
    write("];")
    write("")

    write("/// A path, and the way the reference implementation quotes it in an error.")
    write("struct QuotedCase { string text; string quoted; }")
    write("")
    write("immutable QuotedCase[] quotedCases = [")
    for text in QUOTED_CASES:
        write(f"    QuotedCase({d_string(text)}, {d_string(repr(text))}),")
    write("];")
    write("")

    write("/// A document, and the two spellings the reference implementation writes it in.")
    write("struct JsonCase { string name; string compact; string pretty; }")
    write("")
    write("immutable JsonCase[] jsonCases = [")
    for name, payload in JSON_CASES:
        compact = json.dumps(
            payload, ensure_ascii=False, indent=None, separators=(",", ":")
        ) + "\n"
        pretty = json.dumps(payload, ensure_ascii=False, indent=2, separators=None) + "\n"
        write(f"    JsonCase({d_string(name)}, {d_string(compact)}, {d_string(pretty)}),")
    write("];")

    return "\n".join(lines) + "\n"


def render_printable() -> str:
    """The table the D implementation quotes paths with: what is not printable."""

    version = ".".join(str(part) for part in sys.version_info[:3])
    lines: list[str] = []
    write = lines.append
    write("/**")
    write(" * The code points the reference implementation does not call printable.")
    write(" *")
    write(" * A path quoted inside an error message has these escaped, and which code")
    write(" * points they are is decided by the reference's own Unicode tables rather")
    write(" * than by Phobos's, which are another revision and disagree on thousands of")
    write(" * unassigned code points. Regenerate this with")
    write(" * tests/conformance/generate_fixtures.py rather than editing it; this copy")
    write(f" * came from CPython {version} (Unicode {unicodedata.unidata_version}).")
    write(" */")
    write("module texflux.printable;")
    write("")
    write("/// Inclusive ranges in ascending order, together covering every such code point.")
    write_ranges(write, "nonPrintableRanges", ranges_where(lambda c: not c.isprintable()))
    return "\n".join(lines) + "\n"


def main() -> int:
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    PRINTABLE.write_text(render_printable(), encoding="utf-8")
    print(f"wrote {PRINTABLE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
