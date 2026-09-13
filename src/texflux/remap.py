"""Source-map validation and safe SyncTeX source remapping."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Final, get_args

from .ast import SourcePosition
from .paths import PathLike, normalized_path, same_path
from .render import RenderRole
from .synctex import (
    SyncTeXDocument,
    SyncTeXInput,
    SyncTeXLine,
    parse_synctex,
    serialize_synctex,
)


class RemapError(ValueError):
    """Raised when a source map cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class SourceMapSource:
    id: int
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class SourceMapping:
    generated_start: SourcePosition
    generated_end: SourcePosition
    source_id: int
    source_start: SourcePosition
    source_end: SourcePosition
    role: str


@dataclass(frozen=True, slots=True)
class SourceMap:
    path: Path
    generated_path: Path
    generated_sha256: str
    sources: tuple[SourceMapSource, ...]
    mappings: tuple[SourceMapping, ...]

    @property
    def sources_by_id(self) -> dict[int, SourceMapSource]:
        return {source.id: source for source in self.sources}


@dataclass(frozen=True, slots=True)
class _Target:
    source_map: SourceMap
    generated_tags: tuple[int, ...]
    source_tags: dict[int, int]


_HASH_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
#: The role vocabulary is owned by the renderer that writes the map.
_ROLES: Final = frozenset(get_args(RenderRole))
#: Without a column, content outranks a delimiter, which outranks filler.
_ROLE_RANK: Final[dict[str, int]] = {
    "content": 0,
    "scaffold": 1,
    "open": 1,
    "close": 1,
    "synthetic": 2,
}


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RemapError(f"{label} must be an object")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RemapError(f"{label} must be an integer")
    return value


def _position(value: object, label: str) -> SourcePosition:
    data = _object(value, label)
    line = _integer(data.get("line"), f"{label}.line")
    column = _integer(data.get("column"), f"{label}.column")
    if line < 1 or column < 1:
        raise RemapError(f"{label} must be one-based")
    return SourcePosition(line, column)


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise RemapError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _resolved_path(base: Path, stored: object, label: str) -> Path:
    if not isinstance(stored, str) or not stored:
        raise RemapError(f"{label} must be a non-empty path")
    return Path(os.path.abspath(os.path.join(os.fspath(base), stored)))


def _file_hash(path: Path, label: str) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RemapError(f"cannot read {label} {path}: {error}") from error
    return hashlib.sha256(data).hexdigest()


def _validate_mapping(value: object, source_ids: set[int], index: int) -> SourceMapping:
    data = _object(value, f"mappings[{index}]")
    generated = _object(data.get("generated"), f"mappings[{index}].generated")
    source = _object(data.get("source"), f"mappings[{index}].source")
    generated_start = _position(
        generated.get("start"),
        f"mappings[{index}].generated.start",
    )
    generated_end = _position(
        generated.get("end"),
        f"mappings[{index}].generated.end",
    )
    if not generated_start < generated_end:
        raise RemapError(f"mappings[{index}] has an empty generated range")
    source_id = _integer(source.get("id"), f"mappings[{index}].source.id")
    if source_id not in source_ids:
        raise RemapError(f"mappings[{index}] references unknown source id {source_id}")
    source_start = _position(
        source.get("start"),
        f"mappings[{index}].source.start",
    )
    source_end = _position(
        source.get("end"),
        f"mappings[{index}].source.end",
    )
    if source_end < source_start:
        raise RemapError(f"mappings[{index}] has a reversed source range")
    role = data.get("role")
    if not isinstance(role, str) or role not in _ROLES:
        raise RemapError(f"mappings[{index}] has an invalid role")
    return SourceMapping(
        generated_start,
        generated_end,
        source_id,
        source_start,
        source_end,
        role,
    )


def load_source_map(path: PathLike) -> SourceMap:
    """Read, validate, and hash-check one ``.tfxmap`` artifact."""

    map_path = Path(path).absolute()
    try:
        payload = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RemapError(f"cannot read source map {map_path}: {error}") from error
    root = _object(payload, "source map")
    if root.get("format") != "texflux-source-map":
        raise RemapError("source map has an unsupported format")
    if root.get("version") != 1:
        raise RemapError("source map has an unsupported version")

    generated = _object(root.get("generated"), "generated")
    generated_path = _resolved_path(
        map_path.parent,
        generated.get("path"),
        "generated.path",
    )
    generated_sha256 = _hash(generated.get("sha256"), "generated.sha256")
    actual_generated_sha256 = _file_hash(generated_path, "generated file")
    if actual_generated_sha256 != generated_sha256:
        raise RemapError(
            f"generated file hash does not match source map: {generated_path}"
        )

    source_values = root.get("sources")
    if not isinstance(source_values, list):
        raise RemapError("sources must be an array")
    sources: list[SourceMapSource] = []
    source_ids: set[int] = set()
    for index, value in enumerate(source_values):
        source = _object(value, f"sources[{index}]")
        source_id = _integer(source.get("id"), f"sources[{index}].id")
        if source_id in source_ids:
            raise RemapError(f"duplicate source id {source_id}")
        source_ids.add(source_id)
        source_path = _resolved_path(
            map_path.parent,
            source.get("path"),
            f"sources[{index}].path",
        )
        source_sha256 = _hash(source.get("sha256"), f"sources[{index}].sha256")
        if _file_hash(source_path, "source file") != source_sha256:
            raise RemapError(
                f"source file hash does not match source map: {source_path}"
            )
        sources.append(SourceMapSource(source_id, source_path, source_sha256))

    mapping_values = root.get("mappings")
    if not isinstance(mapping_values, list):
        raise RemapError("mappings must be an array")
    mappings = tuple(
        _validate_mapping(value, source_ids, index)
        for index, value in enumerate(mapping_values)
    )
    previous_end: SourcePosition | None = None
    for mapping in mappings:
        if previous_end is not None and mapping.generated_start < previous_end:
            raise RemapError("generated mappings must be sorted and non-overlapping")
        previous_end = mapping.generated_end

    return SourceMap(
        map_path,
        generated_path,
        generated_sha256,
        tuple(sources),
        mappings,
    )


def _input_path(input_record: SyncTeXInput, base: Path) -> Path:
    path = Path(os.fsdecode(input_record.path))
    if not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(path))


def _matching_inputs(
    document: SyncTeXDocument,
    target: Path,
    base: Path,
) -> list[SyncTeXInput]:
    return [
        input_record
        for input_record in document.inputs
        if same_path(_input_path(input_record, base), target)
    ]


def _mapping_on_line(mapping: SourceMapping, line: int) -> bool:
    start = mapping.generated_start
    end = mapping.generated_end
    if line < start.line or line > end.line:
        return False
    if start.line == end.line == line:
        return start.column < end.column
    if line == end.line:
        return end.column > 1
    return True


def _mapping_targets(
    source_map: SourceMap,
    mappings: Sequence[SourceMapping],
) -> set[tuple[str, int]]:
    """Reduce mappings to the distinct source lines they resolve to."""

    sources_by_id = source_map.sources_by_id
    return {
        (
            normalized_path(sources_by_id[mapping.source_id].path),
            mapping.source_start.line,
        )
        for mapping in mappings
    }


def _select_mapping(
    source_map: SourceMap,
    line: int,
    column: int | None,
) -> SourceMapping:
    """Resolve one generated position to the source position it came from."""

    if line <= 0:
        raise RemapError(f"invalid generated line {line}")
    if column is not None and column <= 0:
        column = None

    if column is not None:
        point = SourcePosition(line, column)
        candidates = [
            mapping
            for mapping in source_map.mappings
            if mapping.generated_start <= point < mapping.generated_end
        ]
        if not candidates:
            raise RemapError(
                f"no source mapping for generated line {line}, column {column}"
            )
        return candidates[0]

    candidates = [
        mapping
        for mapping in source_map.mappings
        if _mapping_on_line(mapping, line)
    ]
    if not candidates:
        raise RemapError(f"no source mapping for generated line {line}")

    # Without a column, content outranks a delimiter, which outranks
    # synthetic text. Remaining candidates must agree on one source line.
    best_rank = min(_ROLE_RANK[mapping.role] for mapping in candidates)
    candidates = [
        mapping
        for mapping in candidates
        if _ROLE_RANK[mapping.role] == best_rank
    ]
    if len(_mapping_targets(source_map, candidates)) > 1:
        raise RemapError(f"ambiguous source mappings for generated line {line}")
    return candidates[0]


def _rewrite_link(line: SyncTeXLine, tag: int, source_line: int) -> bytes:
    if line.record is None or line.record.link is None or line.record.link_span is None:
        raise RemapError("cannot rewrite a record without a source link")
    start, end = line.record.link_span
    replacement = f"{tag},{source_line}".encode("ascii")
    return line.body[:start] + replacement + line.body[end:]


def _allocate_targets(
    document: SyncTeXDocument,
    source_maps: Sequence[SourceMap],
    synctex_base: Path,
) -> tuple[list[_Target], dict[int, bytes]]:
    input_tags = [input_record.tag for input_record in document.inputs]
    if len(input_tags) != len(set(input_tags)):
        raise RemapError("SyncTeX contains duplicate Input tags")
    targets: list[_Target] = []
    target_tags: set[int] = set()
    matched_tags: list[tuple[SourceMap, tuple[int, ...]]] = []
    for source_map in source_maps:
        matches = _matching_inputs(document, source_map.generated_path, synctex_base)
        if not matches:
            raise RemapError(
                "generated file is not present in SyncTeX inputs: "
                f"{source_map.generated_path}"
            )
        generated_tags = tuple(sorted(input_record.tag for input_record in matches))
        if target_tags.intersection(generated_tags):
            raise RemapError("multiple source maps target the same SyncTeX input")
        target_tags.update(generated_tags)
        matched_tags.append((source_map, generated_tags))

    next_tag = max((tag for tag in input_tags if tag > 0), default=0) + 1
    allocated_by_path: dict[str, int] = {}
    new_inputs: dict[int, bytes] = {}
    for source_map, generated_tags in matched_tags:
        source_tags: dict[int, int] = {}
        for source in source_map.sources:
            existing = _matching_inputs(document, source.path, synctex_base)
            if existing:
                tag = min(input_record.tag for input_record in existing)
            else:
                key = normalized_path(source.path)
                if key in allocated_by_path:
                    tag = allocated_by_path[key]
                else:
                    tag = next_tag
                    next_tag += 1
                    allocated_by_path[key] = tag
                    path_bytes = os.fsencode(os.fspath(source.path))
                    if b"\n" in path_bytes or b"\r" in path_bytes:
                        raise RemapError(
                            "source path cannot be an Input record: "
                            f"{source.path}"
                        )
                    new_inputs[tag] = path_bytes
            source_tags[source.id] = tag
        targets.append(_Target(source_map, generated_tags, source_tags))
    return targets, new_inputs


def remap_document(
    document: SyncTeXDocument,
    source_maps: Sequence[SourceMap],
    *,
    synctex_path: PathLike | None = None,
) -> bytes:
    """Apply validated source maps and return canonical SyncTeX bytes."""

    if not source_maps:
        raise RemapError("at least one source map is required")
    synctex_base = (
        Path(synctex_path).absolute().parent
        if synctex_path is not None
        else Path.cwd()
    )
    targets, new_inputs = _allocate_targets(document, source_maps, synctex_base)
    target_by_tag = {
        generated_tag: target
        for target in targets
        for generated_tag in target.generated_tags
    }
    rewritten_bodies: dict[int, bytes] = {}

    for index, line in enumerate(document.lines):
        if line.record is None or line.record.link is None:
            continue
        target = target_by_tag.get(line.record.link.tag)
        if target is None:
            continue
        mapping = _select_mapping(
            target.source_map,
            line.record.link.line,
            line.record.link.column,
        )
        rewritten_bodies[index] = _rewrite_link(
            line,
            target.source_tags[mapping.source_id],
            mapping.source_start.line,
        )

    lines = [
        replace(line, body=rewritten_bodies.get(index, line.body))
        for index, line in enumerate(document.lines)
    ]
    if new_inputs:
        input_lines = [
            SyncTeXLine(
                f"Input:{tag}:".encode("ascii") + path_bytes,
                document.newline,
                "preamble",
            )
            for tag, path_bytes in new_inputs.items()
        ]
        preamble_inputs = [
            index
            for index, line in enumerate(lines)
            if line.section == "preamble" and line.input is not None
        ]
        if preamble_inputs:
            anchor_index = max(preamble_inputs)
        else:
            anchor_index = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if line.body.startswith(b"SyncTeX Version:")
                ),
                -1,
            )
        if anchor_index >= 0 and not lines[anchor_index].newline:
            lines[anchor_index] = replace(
                lines[anchor_index],
                newline=document.newline,
            )
        insertion_index = anchor_index + 1
        lines[insertion_index:insertion_index] = input_lines

    plain = b"".join(line.raw for line in lines)
    reparsed = parse_synctex(plain)
    reparsed = replace(reparsed, container=document.container)
    return serialize_synctex(reparsed)


def remap_synctex(
    data: bytes | bytearray | memoryview,
    *,
    map_paths: Sequence[PathLike],
    synctex_path: PathLike | None = None,
) -> bytes:
    """Validate maps, remap a SyncTeX byte stream, and preserve its container."""

    source_maps = tuple(load_source_map(path) for path in map_paths)
    document = parse_synctex(data)
    return remap_document(document, source_maps, synctex_path=synctex_path)


def _same_stat(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write(
    path: Path,
    data: bytes,
    mode: int,
    *,
    check_path: Path | None = None,
    expected_stat: os.stat_result | None = None,
) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if check_path is not None and expected_stat is not None:
            try:
                current_stat = check_path.stat()
            except OSError as error:
                raise RemapError(
                    f"cannot stat SyncTeX file {check_path}: {error}"
                ) from error
            if not _same_stat(expected_stat, current_stat):
                raise RemapError("SyncTeX file changed during remapping")
        os.chmod(temporary_path, stat.S_IMODE(mode))
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    except OSError as error:
        raise RemapError(f"cannot replace {path}: {error}") from error
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()


def remap_synctex_file(
    path: PathLike,
    *,
    map_paths: Sequence[PathLike],
    output_path: PathLike | None = None,
) -> None:
    """Remap a file in place, or atomically write an explicit output path."""

    source_path = Path(path).absolute()
    destination = source_path if output_path is None else Path(output_path).absolute()
    try:
        before = source_path.stat()
        original = source_path.read_bytes()
    except OSError as error:
        raise RemapError(f"cannot read SyncTeX file {source_path}: {error}") from error
    rewritten = remap_synctex(
        original,
        map_paths=map_paths,
        synctex_path=source_path,
    )
    if same_path(destination, source_path):
        _atomic_write(
            destination,
            rewritten,
            before.st_mode,
            check_path=source_path,
            expected_stat=before,
        )
    else:
        _atomic_write(destination, rewritten, before.st_mode)


__all__ = [
    "RemapError",
    "SourceMap",
    "SourceMapSource",
    "SourceMapping",
    "load_source_map",
    "remap_document",
    "remap_synctex",
    "remap_synctex_file",
]
