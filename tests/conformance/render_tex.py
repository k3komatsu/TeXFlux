"""Compile one document through the import-free pipeline and print the TeX.

The D implementation prints the same thing from tools/render_tex.d. Comparing
the two checks everything between parsing and output -- desugaring, flags,
macros, interpolation, value consumption and layout -- without needing the
module system to exist on both sides yet.

    python3 tests/conformance/render_tex.py FILE [--source-comments]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from texflux.errors import FlagError, TeXFluxError  # noqa: E402
from texflux.normalize import normalize  # noqa: E402
from texflux.parser import parse  # noqa: E402
from texflux.render import render  # noqa: E402


def compile_text(path: str, source_comments: bool) -> str:
    source = Path(path).read_text(encoding="utf-8")
    try:
        document = normalize(parse(source, path))
        return render(document, source_comments=source_comments)
    except TeXFluxError as error:
        return f"error {error.code} {error.span.location} {error.message}\n"
    except FlagError as error:
        return f"flag error {error}\n"
    except RecursionError:
        return "nesting error\n"


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print("usage: render_tex.py FILE [--source-comments]", file=sys.stderr)
        return 2
    source_comments = len(argv) == 3 and argv[2] == "--source-comments"
    sys.stdout.buffer.write(compile_text(argv[1], source_comments).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
