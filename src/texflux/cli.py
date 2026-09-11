"""Command-line interface for TeXFlux."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Sequence

from . import compile_text
from .errors import TeXFluxError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="texflux")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile")
    compile_parser.add_argument("input", metavar="INPUT")
    compile_parser.add_argument("-o", "--output", required=True, metavar="OUTPUT")
    compile_parser.add_argument("--source-comments", action="store_true")
    return parser


def _same_path(first: Path, second: Path) -> bool:
    try:
        if first.exists() and second.exists() and os.path.samefile(first, second):
            return True
    except OSError:
        pass
    first_resolved = os.path.normcase(str(first.resolve()))
    second_resolved = os.path.normcase(str(second.resolve()))
    return first_resolved == second_resolved


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        if isinstance(error.code, int):
            return error.code
        return 0 if error.code is None else 1

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.suffix != ".tfx":
        print("texflux: input must have a .tfx extension", file=sys.stderr)
        return 1
    if _same_path(input_path, output_path):
        print("texflux: input and output must be different paths", file=sys.stderr)
        return 1

    try:
        source = input_path.read_text(encoding="utf-8")
        output = compile_text(
            source,
            filename=str(input_path),
            source_comments=args.source_comments,
        )
        output_path.write_text(output, encoding="utf-8", newline="\n")
    except TeXFluxError as error:
        print(error.diagnostic(), file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as error:
        print(f"texflux: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = ["main"]
