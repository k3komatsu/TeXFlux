"""Command-line interface for TeXFlux."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from . import compile_with_map
from .errors import FlagError, TeXFluxError
from .flags import FLAG_VALUES
from .paths import same_path
from .remap import RemapError, remap_synctex_file
from .source_map import serialize_source_map
from .synctex import SyncTeXError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="texflux")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile")
    compile_parser.add_argument("input", metavar="INPUT")
    compile_parser.add_argument("-o", "--output", required=True, metavar="OUTPUT")
    compile_parser.add_argument("--source-comments", action="store_true")
    compile_parser.add_argument(
        "--flag",
        dest="flags",
        action="append",
        default=[],
        metavar="NAME[=on|off]",
    )
    synctex_parser = commands.add_parser("synctex")
    synctex_commands = synctex_parser.add_subparsers(
        dest="synctex_command",
        required=True,
    )
    remap_parser = synctex_commands.add_parser("remap")
    remap_parser.add_argument("input", metavar="SYNCTEX")
    remap_parser.add_argument(
        "--map",
        dest="maps",
        action="append",
        required=True,
        metavar="MAP",
    )
    remap_parser.add_argument("--output", metavar="OUTPUT")
    return parser


def _flags(arguments: Sequence[str]) -> dict[str, bool]:
    """Read ``--flag NAME``, ``--flag NAME=on`` and ``--flag NAME=off``.

    A bare name turns its flag on, because enabling one is what a command
    line is usually for. Names are checked against the document's own
    declarations later, once those have been collected.
    """

    flags: dict[str, bool] = {}
    for argument in arguments:
        name, separator, value = argument.partition("=")
        # A flag group in the document is stripped, so a shell-quoted or
        # make-substituted argument reads the same way here.
        name, value = name.strip(), value.strip()
        if separator and value not in FLAG_VALUES:
            raise FlagError(
                "build flag must be NAME, NAME=on or NAME=off; "
                f"got '{argument}'"
            )
        if name in flags:
            raise FlagError(f"build flag '{name}' is set more than once")
        flags[name] = FLAG_VALUES[value] if separator else True
    return flags


def _fail(message: str) -> int:
    print(f"texflux: {message}", file=sys.stderr)
    return 1


def _compile(args: argparse.Namespace) -> int:
    """Compile one .tfx file, writing the .tex and its .tfxmap side by side."""

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.suffix != ".tfx":
        return _fail("input must have a .tfx extension")
    if same_path(input_path, output_path):
        return _fail("input and output must be different paths")

    try:
        # with_name rejects a directory-like output path such as "/".
        map_path = output_path.with_name(output_path.name + ".tfxmap")
        source_bytes = input_path.read_bytes()
        result = compile_with_map(
            source_bytes.decode("utf-8"),
            filename=str(input_path),
            source_comments=args.source_comments,
            flags=_flags(args.flags),
        )
        output_bytes = result.text.encode("utf-8")
        map_text = serialize_source_map(
            result,
            source_path=input_path,
            generated_path=output_path,
            map_path=map_path,
            source_bytes=source_bytes,
            generated_bytes=output_bytes,
        )
        for warning in result.rendered.warnings:
            print(warning.diagnostic(), file=sys.stderr)
        output_path.write_bytes(output_bytes)
        map_path.write_text(map_text, encoding="utf-8", newline="\n")
    except TeXFluxError as error:
        print(error.diagnostic(), file=sys.stderr)
        return 1
    except (FlagError, ValueError, OSError) as error:
        return _fail(str(error))
    except RecursionError:
        # Deeply nested composition or macro expansion exhausts the
        # interpreter stack before any TeXFlux limit is reached.
        return _fail(f"{input_path}: input nests too deeply to compile")
    return 0


def _synctex_remap(args: argparse.Namespace) -> int:
    """Rewrite a SyncTeX file so it points at .tfx sources."""

    try:
        remap_synctex_file(
            args.input,
            map_paths=args.maps,
            output_path=args.output,
        )
    except (OSError, RemapError, SyncTeXError) as error:
        return _fail(str(error))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        if isinstance(error.code, int):
            return error.code
        return 0 if error.code is None else 1

    match args.command, getattr(args, "synctex_command", None):
        case ("compile", _):
            return _compile(args)
        case ("synctex", "remap"):
            return _synctex_remap(args)
        case (command, _):
            return _fail(f"unsupported command {command}")


__all__ = ["main"]
