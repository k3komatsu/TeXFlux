"""Byte-preserving SyncTeX parsing and canonical serialization."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import gzip
import os
from pathlib import Path
import re
from typing import Final, Literal, TypeAlias
import zlib


PathLike: TypeAlias = str | os.PathLike[str]
Container: TypeAlias = Literal["plain", "gzip"]


class SyncTeXError(ValueError):
    """Raised when a SyncTeX byte stream cannot be modeled safely."""


@dataclass(frozen=True, slots=True)
class SyncTeXInput:
    """One ``Input:`` mapping, retaining its filename bytes verbatim."""

    tag: int
    path: bytes


@dataclass(frozen=True, slots=True)
class SyncTeXSetting:
    """A preamble or post-script setting, with byte-valued fields."""

    name: bytes
    value: bytes
    section: str = "preamble"


@dataclass(frozen=True, slots=True)
class SyncTeXLink:
    """A source link attached to a box or node record."""

    tag: int
    line: int
    column: int | None = None


@dataclass(frozen=True, slots=True)
class SyncTeXPoint:
    """A resolved point. ``compressed`` records how it appeared on disk."""

    horizontal: int
    vertical: int
    compressed: bool = False


@dataclass(frozen=True, slots=True)
class SyncTeXRecord:
    """A modeled record line and the byte offsets of rewriteable fields."""

    raw: bytes
    kind: bytes
    counted: bool = False
    link: SyncTeXLink | None = None
    point: SyncTeXPoint | None = None
    form_tag: int | None = None
    tag: int | None = None
    anchor_offset: int | None = None
    link_span: tuple[int, int] | None = None
    point_span: tuple[int, int] | None = None

    @property
    def source_link(self) -> SyncTeXLink | None:
        """Alias that makes the source-bearing role explicit to remappers."""

        return self.link

    @property
    def is_compressed(self) -> bool:
        return self.point is not None and self.point.compressed

    def expanded_raw(self, body: bytes | None = None) -> bytes:
        """Expand this record's ``<horizontal>,=`` point if it was compressed."""

        if not self.is_compressed or self.point_span is None or self.point is None:
            return self.raw if body is None else body
        source = self.raw if body is None else body
        start, end = self.point_span
        if not 0 <= start <= end <= len(source):
            raise SyncTeXError("record point span is outside its raw bytes")
        if source[start:end] != self.raw[start:end]:
            raise SyncTeXError("record point span does not match its raw bytes")
        replacement = f"{self.point.horizontal},{self.point.vertical}".encode("ascii")
        return source[:start] + replacement + source[end:]


@dataclass(frozen=True, slots=True)
class SyncTeXLine:
    """One physical line, including its original newline bytes."""

    body: bytes
    newline: bytes
    section: str
    record: SyncTeXRecord | None = None
    input: SyncTeXInput | None = None

    @property
    def raw(self) -> bytes:
        return self.body + self.newline


@dataclass(frozen=True, slots=True)
class SyncTeXDocument:
    """Parsed SyncTeX data and its original plain/gzip container choice.

    ``lines`` is the authoritative serialized representation. The other
    collections are read-only projections for lookup and validation.
    """

    version: int
    container: Container
    newline: bytes
    lines: tuple[SyncTeXLine, ...]
    inputs: tuple[SyncTeXInput, ...]
    settings: tuple[SyncTeXSetting, ...]
    count: int | None
    count_line: int | None
    postamble_line: int | None
    post_scriptum_line: int | None

    @property
    def records(self) -> tuple[SyncTeXRecord, ...]:
        return tuple(
            line.record for line in self.lines if line.record is not None
        )


_VERSION_PREFIX: Final = b"SyncTeX Version:"
_INPUT_PREFIX: Final = b"Input:"
_COUNT_PREFIX: Final = b"Count:"
_SECTION_NAMES: Final = {
    b"Content:": "content",
    b"Postamble:": "postamble",
    b"Post scriptum:": "postscript",
    b"Post Scriptum:": "postscript",
}
_SETTING_NAMES: Final = {
    b"Output",
    b"Magnification",
    b"Unit",
    b"X Offset",
    b"Y Offset",
}
_LINK_KINDS: Final = frozenset(bytes((value,)) for value in b"[]()vhkgr$x")
# ``x`` is emitted by synctexcurrent, whose TeX Live writer updates the byte
# length but deliberately does not increment Count.
_COUNTED_KINDS: Final = frozenset(bytes((value,)) for value in b"!{}<>[]()vhkgr$cf?")
_LINK_RE: Final = re.compile(rb"(-?\d+),(-?\d+)(?:,(-?\d+))?:")
_POINT_RE: Final = re.compile(rb"(-?\d+),(=|-?\d+)")
_FORM_RE: Final = re.compile(rb"(-?\d+):")
_TAG_RE: Final = re.compile(rb"-?\d+")


def _split_lines(data: bytes) -> list[tuple[bytes, bytes]]:
    lines: list[tuple[bytes, bytes]] = []
    start = 0
    index = 0
    while index < len(data):
        byte = data[index]
        if byte == 0x0D:
            end = index
            if index + 1 < len(data) and data[index + 1] == 0x0A:
                newline = b"\r\n"
                index += 1
            else:
                newline = b"\r"
            lines.append((data[start:end], newline))
            start = index + 1
        elif byte == 0x0A:
            lines.append((data[start:index], b"\n"))
            start = index + 1
        index += 1
    if start < len(data) or not lines:
        lines.append((data[start:], b""))
    return lines


def _integer(value: bytes, description: str) -> int:
    try:
        return int(value, 10)
    except (TypeError, ValueError) as error:
        raise SyncTeXError(f"invalid {description}: {value!r}") from error


def _parse_input(body: bytes) -> SyncTeXInput:
    rest = body[len(_INPUT_PREFIX) :]
    separator = rest.find(b":")
    if separator <= 0:
        raise SyncTeXError(f"invalid Input record: {body!r}")
    tag = _integer(rest[:separator], "Input tag")
    return SyncTeXInput(tag, rest[separator + 1 :])


def _parse_point(
    body: bytes,
    start: int,
    last_vertical: int | None,
) -> tuple[SyncTeXPoint | None, tuple[int, int] | None]:
    """Parse one point, resolving a ``=`` vertical against the previous one."""

    match = _POINT_RE.match(body, start)
    if match is None:
        return None, None
    horizontal = _integer(match.group(1), "point horizontal coordinate")
    vertical_token = match.group(2)
    compressed = vertical_token == b"="
    if compressed:
        if last_vertical is None:
            raise SyncTeXError("compressed point has no previous vertical coordinate")
        vertical = last_vertical
    else:
        vertical = _integer(vertical_token, "point vertical coordinate")
    return (
        SyncTeXPoint(horizontal, vertical, compressed),
        (match.start(), match.end()),
    )


def _opaque(body: bytes, kind: bytes) -> SyncTeXRecord:
    """Model a record whose payload TeXFlux deliberately does not interpret."""

    return SyncTeXRecord(body, kind)


def _single_tag(body: bytes, description: str) -> int | None:
    """Read the single integer tag after the kind byte, if it is well formed."""

    match = _TAG_RE.fullmatch(body[1:])
    if match is None:
        return None
    return _integer(match.group(), description)


def _parse_record(body: bytes, last_vertical: int | None) -> SyncTeXRecord | None:
    """Model one record line. A returned point carries the new vertical state."""

    if not body or body.startswith(_INPUT_PREFIX):
        return None

    kind = body[:1]
    match kind:
        case b"%":
            return _opaque(body, kind)

        case b"!":
            if (anchor := _single_tag(body, "anchor offset")) is None:
                return _opaque(body, kind)
            return SyncTeXRecord(body, kind, counted=True, anchor_offset=anchor)

        case b"{" | b"}":
            if (tag := _single_tag(body, "sheet tag")) is None:
                return _opaque(body, kind)
            return SyncTeXRecord(body, kind, counted=True, tag=tag)

        case b"<":
            if (form_tag := _single_tag(body, "form tag")) is None:
                return _opaque(body, kind)
            return SyncTeXRecord(body, kind, counted=True, form_tag=form_tag)

        case b">" | b"]" | b")":
            return SyncTeXRecord(body, kind, counted=True)

        case b"f":
            if (form := _FORM_RE.match(body, 1)) is None:
                return _opaque(body, kind)
            point, point_span = _parse_point(body, form.end(), last_vertical)
            return SyncTeXRecord(
                body,
                kind,
                counted=True,
                point=point,
                form_tag=_integer(form.group(1), "form tag"),
                point_span=point_span,
            )

        case _ if kind in _LINK_KINDS:
            if (link := _LINK_RE.match(body, 1)) is None:
                return _opaque(body, kind)
            point, point_span = _parse_point(body, link.end(), last_vertical)
            return SyncTeXRecord(
                body,
                kind,
                counted=kind in _COUNTED_KINDS,
                link=SyncTeXLink(
                    _integer(link.group(1), "link tag"),
                    _integer(link.group(2), "link line"),
                    (
                        None
                        if link.group(3) is None
                        else _integer(link.group(3), "link column")
                    ),
                ),
                point=point,
                link_span=(1, link.end() - 1),
                point_span=point_span,
            )

        case b"c" | b"?":
            point, point_span = _parse_point(body, 1, last_vertical)
            return SyncTeXRecord(
                body,
                kind,
                counted=kind in _COUNTED_KINDS,
                point=point,
                point_span=point_span,
            )

        case _:
            return _opaque(body, kind)


def _decode_payload(data: bytes) -> tuple[bytes, Container]:
    if data.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(data), "gzip"
        except (OSError, EOFError, zlib.error) as error:
            raise SyncTeXError("invalid gzip SyncTeX data") from error
    return data, "plain"


def parse_synctex(data: bytes | bytearray | memoryview) -> SyncTeXDocument:
    """Parse plain or gzip-compressed SyncTeX bytes."""

    payload, container = _decode_payload(bytes(data))
    physical_lines = _split_lines(payload)
    newline = next(
        (line_newline for _, line_newline in physical_lines if line_newline),
        b"\n",
    )

    version: int | None = None
    inputs: list[SyncTeXInput] = []
    settings: list[SyncTeXSetting] = []
    lines: list[SyncTeXLine] = []
    count: int | None = None
    count_line: int | None = None
    postamble_line: int | None = None
    post_scriptum_line: int | None = None
    section = "preamble"
    last_vertical: int | None = None

    for body, line_newline in physical_lines:
        line_section = section
        record: SyncTeXRecord | None = None
        input_record: SyncTeXInput | None = None

        if body.startswith(_VERSION_PREFIX):
            version = _integer(body[len(_VERSION_PREFIX) :], "SyncTeX version")
        elif body.startswith(_INPUT_PREFIX):
            input_record = _parse_input(body)
            inputs.append(input_record)
        elif body in _SECTION_NAMES:
            section = _SECTION_NAMES[body]
            line_section = section
            if section == "postamble":
                postamble_line = len(lines)
            elif section == "postscript":
                post_scriptum_line = len(lines)
        elif body.startswith(_COUNT_PREFIX):
            count = _integer(body[len(_COUNT_PREFIX) :], "SyncTeX Count")
            count_line = len(lines)
        elif b":" in body and (
            section in {"preamble", "postscript"}
            or body.split(b":", 1)[0] in _SETTING_NAMES
        ):
            name, value = body.split(b":", 1)
            settings.append(SyncTeXSetting(name, value, line_section))
        elif body:
            record = _parse_record(body, last_vertical)
            if record is not None and record.point is not None:
                last_vertical = record.point.vertical

        lines.append(
            SyncTeXLine(
                body,
                line_newline,
                line_section,
                record,
                input_record,
            )
        )

    if version is None:
        raise SyncTeXError("missing SyncTeX Version record")

    return SyncTeXDocument(
        version,
        container,
        newline,
        tuple(lines),
        tuple(inputs),
        tuple(settings),
        count,
        count_line,
        postamble_line,
        post_scriptum_line,
    )


def _canonical_count(document: SyncTeXDocument) -> int | None:
    if document.count_line is None:
        return None
    return sum(
        line.record is not None and line.record.counted
        for line in document.lines[: document.count_line]
    )


def _gzip_payload(payload: bytes) -> bytes:
    buffer = BytesIO()
    with gzip.GzipFile(
        fileobj=buffer,
        mode="wb",
        filename="",
        mtime=0,
    ) as stream:
        stream.write(payload)
    return buffer.getvalue()


def serialize_synctex(document: SyncTeXDocument) -> bytes:
    """Serialize a document canonically without text-mode newline conversion."""

    output = bytearray()
    anchor_origin = 0
    canonical_count = _canonical_count(document)

    for index, line in enumerate(document.lines):
        body = line.body
        anchor_start: int | None = None
        if line.record is not None:
            body = line.record.expanded_raw(body)
            if line.record.anchor_offset is not None:
                anchor_start = len(output)
                body = f"!{len(output) - anchor_origin}".encode("ascii")
        if index == document.count_line and canonical_count is not None:
            body = _COUNT_PREFIX + str(canonical_count).encode("ascii")
        output.extend(body)
        output.extend(line.newline)
        if anchor_start is not None:
            # The next anchor is relative to this anchor's line start.
            anchor_origin = anchor_start

    payload = bytes(output)
    return _gzip_payload(payload) if document.container == "gzip" else payload


def read_synctex_file(path: PathLike) -> SyncTeXDocument:
    """Read a SyncTeX file, detecting its container from its bytes."""

    return parse_synctex(Path(path).read_bytes())


def write_synctex_file(path: PathLike, document: SyncTeXDocument) -> None:
    """Write a canonical SyncTeX file with its original container type."""

    Path(path).write_bytes(serialize_synctex(document))


__all__ = [
    "SyncTeXDocument",
    "SyncTeXError",
    "SyncTeXInput",
    "SyncTeXLine",
    "SyncTeXLink",
    "SyncTeXPoint",
    "SyncTeXRecord",
    "SyncTeXSetting",
    "parse_synctex",
    "read_synctex_file",
    "serialize_synctex",
    "write_synctex_file",
]
