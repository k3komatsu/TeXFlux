"""Command-line interface for TeXFlux."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
import sys

from . import (
    compile_ast,
    compile_with_map,
    diagnose,
    serialize_ast,
    serialize_diagnostics,
)
from .errors import FlagError, InternalError, TeXFluxError
from .flags import FLAG_VALUES
from .paths import same_path
from .remap import RemapError, remap_synctex_file
from .source_map import serialize_source_map
from .synctex import SyncTeXError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="texflux")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile")
    compile_parser.add_argument("--source-comments", action="store_true")
    ast_parser = commands.add_parser("ast", help="export canonical AST as JSON")
    ast_parser.add_argument("--pretty", action="store_true")
    check_parser = commands.add_parser(
        "check", help="report diagnostics without writing anything",
    )
    check_parser.add_argument(
        "input", metavar="INPUT", help="a .tfx file, or '-' for standard input",
    )
    check_parser.add_argument("--format", choices=("text", "json"), default="text")
    check_parser.add_argument("--pretty", action="store_true")
    check_parser.add_argument("--stdin-filename", metavar="PATH")
    for frontend in (compile_parser, ast_parser):
        frontend.add_argument("input", metavar="INPUT")
        frontend.add_argument("-o", "--output", required=True, metavar="OUTPUT")
    for frontend in (compile_parser, ast_parser, check_parser):
        frontend.add_argument(
            "--flag", dest="flags", action="append", default=[],
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


def _fail(message: str, status: int = 1) -> int:
    print(f"texflux: {message}", file=sys.stderr)
    return status


def _write_stdout(text: str) -> None:
    """Write bytes to bypass platform encoding and newline translation."""

    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(text.encode("utf-8"))
    else:
        sys.stdout.write(text)


def _reject_paths(input_path: Path, output: str | Path | None) -> int | None:
    """An exit status refusing the paths, or ``None`` when they are usable."""

    if input_path.suffix != ".tfx":
        return _fail("input must have a .tfx extension")
    if output is not None and same_path(input_path, output):
        return _fail("input and output must be different paths")
    return None


def _run(command: Callable[[], int], *, filename: object, failure: int = 1) -> int:
    """Run one command body, turning what it raises into an exit status.

    A document error prints its diagnostic line and exits 1. A problem with
    the invocation itself -- a bad flag, an unreadable file, a broken
    install -- exits with ``failure``, which ``check`` sets to 2 so that
    "the document has an error" and "the document could not be looked at"
    stay distinct.
    """

    try:
        return command()
    except TeXFluxError as error:
        print(error.diagnostic(), file=sys.stderr)
        return 1
    except (FlagError, InternalError, ValueError, OSError) as error:
        return _fail(str(error), failure)
    except RecursionError:
        # Deeply nested composition or macro expansion exhausts the
        # interpreter stack before any TeXFlux limit is reached.
        return _fail(f"{filename}: input nests too deeply to compile", failure)


def _compile(args: argparse.Namespace) -> int:
    """Compile one .tfx file, writing the .tex and its .tfxmap side by side."""

    input_path, output_path = Path(args.input), Path(args.output)
    if (status := _reject_paths(input_path, output_path)) is not None:
        return status

    def command() -> int:
        # with_name rejects a directory-like output path such as "/".
        map_path = output_path.with_name(output_path.name + ".tfxmap")
        source_bytes = input_path.read_bytes()
        result = compile_with_map(
            source_bytes.decode("utf-8"),
            filename=str(input_path),
            source_comments=args.source_comments,
            # An override configures this build, so it reaches the root module
            # only; an imported module is configured by its own binding list.
            flags=_flags(args.flags),
            source_bytes=source_bytes,
        )
        output_bytes = result.text.encode("utf-8")
        map_text = serialize_source_map(
            result,
            generated_path=output_path,
            map_path=map_path,
            generated_bytes=output_bytes,
        )
        for warning in result.rendered.warnings:
            print(warning.diagnostic(), file=sys.stderr)
        output_path.write_bytes(output_bytes)
        map_path.write_text(map_text, encoding="utf-8", newline="\n")
        return 0

    return _run(command, filename=input_path)


def _ast(args: argparse.Namespace) -> int:
    """Export a session's canonical AST without rendering TeX or a source map."""

    input_path = Path(args.input)
    to_stdout = args.output == "-"
    status = _reject_paths(input_path, None if to_stdout else args.output)
    if status is not None:
        return status

    def command() -> int:
        source_bytes = input_path.read_bytes()
        result = compile_ast(
            source_bytes.decode("utf-8"),
            filename=str(input_path),
            flags=_flags(args.flags),
            source_bytes=source_bytes,
        )
        text = serialize_ast(result, pretty=args.pretty)
        if to_stdout:
            _write_stdout(text)
        else:
            Path(args.output).write_bytes(text.encode("utf-8"))
        return 0

    return _run(command, filename=input_path)


def _check_input(args: argparse.Namespace) -> str | int:
    """The document's display name, or an exit code rejecting the arguments."""

    if args.pretty and args.format != "json":
        return _fail("--pretty requires --format json", 2)
    if args.input != "-":
        if args.stdin_filename is not None:
            return _fail("--stdin-filename requires INPUT '-'", 2)
        if Path(args.input).suffix != ".tfx":
            return _fail("input must have a .tfx extension", 2)
        return args.input
    if args.stdin_filename is None:
        # Imports then resolve against the working directory, which is the
        # best a caller that named no path can be given.
        return "<stdin>"
    if Path(args.stdin_filename).suffix != ".tfx":
        return _fail("--stdin-filename must have a .tfx extension", 2)
    return args.stdin_filename


def _check(args: argparse.Namespace) -> int:
    """Report one document's diagnostics on stdout, writing nothing else.

    Exit code 1 means the document has an error, so it is not the code for
    being unable to look at the document at all; that is 2, which is also
    what argparse uses for a misspelled command line.
    """

    filename = _check_input(args)
    if isinstance(filename, int):
        return filename

    def command() -> int:
        source_bytes = (
            sys.stdin.buffer.read()
            if args.input == "-"
            else Path(filename).read_bytes()
        )
        report = diagnose(
            source_bytes.decode("utf-8"),
            filename=filename,
            flags=_flags(args.flags),
            source_bytes=source_bytes,
        )
        if args.format == "json":
            _write_stdout(serialize_diagnostics(report, pretty=args.pretty))
        else:
            lines: list[str] = []
            for diagnostic in report.diagnostics:
                lines.append(diagnostic.line())
                lines.extend(
                    f"  {related.span.location}: note: {related.message}"
                    for related in diagnostic.related
                )
            if lines:
                _write_stdout("\n".join(lines) + "\n")
        return 0 if report.ok else 1

    return _run(command, filename=filename, failure=2)


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
        case ("ast", _):
            return _ast(args)
        case ("check", _):
            return _check(args)
        case ("synctex", "remap"):
            return _synctex_remap(args)
        case (command, _):
            return _fail(f"unsupported command {command}")


__all__ = ["main"]
