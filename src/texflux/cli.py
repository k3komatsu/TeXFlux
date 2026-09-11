"""Command-line interface for TeXFlux."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from . import compile_with_map
from .errors import TeXFluxError
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        if isinstance(error.code, int):
            return error.code
        return 0 if error.code is None else 1

    if args.command == "synctex" and args.synctex_command == "remap":
        try:
            remap_synctex_file(
                args.input,
                map_paths=args.maps,
                output_path=args.output,
            )
        except (OSError, RemapError, SyncTeXError) as error:
            print(f"texflux: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command != "compile":
        print(f"texflux: unsupported command {args.command}", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.suffix != ".tfx":
        print("texflux: input must have a .tfx extension", file=sys.stderr)
        return 1
    if same_path(input_path, output_path):
        print("texflux: input and output must be different paths", file=sys.stderr)
        return 1

    try:
        source_bytes = input_path.read_bytes()
        source = source_bytes.decode("utf-8")
        result = compile_with_map(
            source,
            filename=str(input_path),
            source_comments=args.source_comments,
        )
        output_bytes = result.text.encode("utf-8")
        try:
            map_path = output_path.with_name(output_path.name + ".tfxmap")
            map_text = serialize_source_map(
                result,
                source_path=input_path,
                generated_path=output_path,
                map_path=map_path,
                source_bytes=source_bytes,
                generated_bytes=output_bytes,
            )
        except ValueError as error:
            print(f"texflux: {error}", file=sys.stderr)
            return 1
        output_path.write_bytes(output_bytes)
        map_path.write_text(map_text, encoding="utf-8", newline="\n")
    except TeXFluxError as error:
        print(error.diagnostic(), file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as error:
        print(f"texflux: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = ["main"]
