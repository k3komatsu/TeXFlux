"""Check that the D implementation agrees with this one, case by case.

The two implementations share a language, a corpus and a set of published
formats, so the way to know they agree is to run both and compare the bytes.
This is the harness that does it. The Python implementation is the reference:
there are no stored expectations here, only what it produced.

    python3 tests/conformance/run.py
    python3 tests/conformance/run.py --check compile
    python3 tests/conformance/run.py --only 'examples/*'
    python3 tests/conformance/run.py --list

A case is a document, plus whatever files it imports. Cases come from three
places: the repository's own documents, the source snippets written inside the
Python test suite -- which is where the malformed ones live -- and the extra
files under tests/conformance/cases.

A check is one way of running a case. Two of them use comparison instruments
that print an internal shape, so a difference can be caught at the layer it
happens in; the rest run the real command line and compare everything a caller
can see: the status, both streams, and every file written.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import difflib
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests" / "conformance"


@dataclass(frozen=True)
class Check:
    """One way of running a case, and what has to match afterwards."""

    #: The reference command. A leading "-m" runs it as a module.
    reference: tuple[str, ...]
    #: The D command, which dub builds into bin/.
    ported: tuple[str, ...]
    #: The dub configuration that builds it, for the missing-file hint.
    configuration: str
    #: Files both runs are expected to write, compared after they finish.
    outputs: tuple[str, ...] = ()
    #: Whether the document is fed on standard input rather than opened.
    stdin: bool = False
    #: Commands to run first, in the same implementation as the check itself.
    setup: tuple[tuple[str, ...], ...] = ()
    #: Files to write after the setup commands, by path and content template.
    fixture: tuple[tuple[str, str], ...] = ()
    #: A pattern limiting which cases this check runs for.
    only: str | None = None


CHECKS = {
    "syntax": Check(
        reference=(str(HERE / "dump_syntax.py"), "{input}"),
        ported=(str(ROOT / "bin" / "dump-syntax"), "{input}"),
        configuration="dump-syntax",
    ),
    "tex": Check(
        reference=(str(HERE / "render_tex.py"), "{input}"),
        ported=(str(ROOT / "bin" / "render-tex"), "{input}"),
        configuration="render-tex",
    ),
    "tex-comments": Check(
        reference=(str(HERE / "render_tex.py"), "{input}", "--source-comments"),
        ported=(str(ROOT / "bin" / "render-tex"), "{input}", "--source-comments"),
        configuration="render-tex",
    ),
    "compile": Check(
        reference=("-m", "texflux", "compile", "{input}", "-o", "out.tex"),
        ported=(str(ROOT / "bin" / "texflux"), "compile", "{input}", "-o", "out.tex"),
        configuration="application",
        outputs=("out.tex", "out.tex.tfxmap"),
    ),
    "ast": Check(
        reference=("-m", "texflux", "ast", "{input}", "-o", "-"),
        ported=(str(ROOT / "bin" / "texflux"), "ast", "{input}", "-o", "-"),
        configuration="application",
    ),
    "ast-pretty": Check(
        reference=("-m", "texflux", "ast", "{input}", "-o", "-", "--pretty"),
        ported=(str(ROOT / "bin" / "texflux"), "ast", "{input}", "-o", "-", "--pretty"),
        configuration="application",
    ),
    "check": Check(
        reference=("-m", "texflux", "check", "{input}"),
        ported=(str(ROOT / "bin" / "texflux"), "check", "{input}"),
        configuration="application",
    ),
    "check-json": Check(
        reference=("-m", "texflux", "check", "{input}", "--format", "json", "--pretty"),
        ported=(
            str(ROOT / "bin" / "texflux"), "check", "{input}", "--format", "json",
            "--pretty",
        ),
        configuration="application",
    ),
    "check-stdin": Check(
        reference=(
            "-m", "texflux", "check", "-", "--stdin-filename", "{input}",
            "--format", "json",
        ),
        ported=(
            str(ROOT / "bin" / "texflux"), "check", "-", "--stdin-filename", "{input}",
            "--format", "json",
        ),
        configuration="application",
        stdin=True,
    ),
    "compile-comments": Check(
        reference=(
            "-m", "texflux", "compile", "{input}", "-o", "out.tex", "--source-comments",
        ),
        ported=(
            str(ROOT / "bin" / "texflux"), "compile", "{input}", "-o", "out.tex",
            "--source-comments",
        ),
        configuration="application",
        outputs=("out.tex", "out.tex.tfxmap"),
    ),
}

#: A SyncTeX file the engine could have written for the generated TeX: one
#: input, and records pointing into it. Remapping has to turn those into
#: records pointing at the source instead.
SYNCTEX_FIXTURE = "\n".join([
    "SyncTeX Version:1",
    "Input:1:{here}/generated.tex",
    "Output:pdf",
    "Magnification:1000",
    "Unit:1",
    "X Offset:0",
    "Y Offset:0",
    "Content:",
    "!999",
    "{1",
    "[1,4:100,200:10,20,30",
    "v1,4:120,=:1,2,3",
    "h1,6,3:130,210",
    "]",
    "}1",
    "!999",
    "Postamble:",
    "Count:6",
    "!999",
    "Post scriptum:",
    "",
])

CHECKS["remap"] = Check(
    reference=("-m", "texflux", "synctex", "remap", "work.synctex",
               "--map", "generated.tex.tfxmap"),
    ported=(str(ROOT / "bin" / "texflux"), "synctex", "remap", "work.synctex",
            "--map", "generated.tex.tfxmap"),
    configuration="application",
    outputs=("work.synctex",),
    setup=(("compile", "{input}", "-o", "generated.tex"),),
    fixture=(("work.synctex", SYNCTEX_FIXTURE),),
    # Only a document that compiles has anything to remap, and the corpus is
    # where those are; a malformed snippet would only compare two failures.
    only="*.tfx",
)

#: Invocations the corpus itself cannot vary, each run on the one document that
#: exercises it: overrides in the order they were written, unknown overrides,
#: a spelling of the input path that the reference tidies, and a SyncTeX file
#: the remap has to refuse.
CHECKS["compile-flags"] = Check(
    reference=("-m", "texflux", "compile", "{input}", "-o", "out.tex",
               "--flag", "draft", "--flag", "handout=off"),
    ported=(str(ROOT / "bin" / "texflux"), "compile", "{input}", "-o", "out.tex",
            "--flag", "draft", "--flag", "handout=off"),
    configuration="application",
    outputs=("out.tex", "out.tex.tfxmap"),
    only="tests/golden/build-flags/*",
)
CHECKS["check-unknown-flags"] = Check(
    reference=("-m", "texflux", "check", "{input}", "--flag", "zulu", "--flag", "nn"),
    ported=(str(ROOT / "bin" / "texflux"), "check", "{input}", "--flag", "zulu", "--flag", "nn"),
    configuration="application",
    only="examples/basic.tfx",
)
CHECKS["ast-dotted-path"] = Check(
    reference=("-m", "texflux", "ast", "./{input}", "-o", "-"),
    ported=(str(ROOT / "bin" / "texflux"), "ast", "./{input}", "-o", "-"),
    configuration="application",
    only="examples/basic.tfx",
)
CHECKS["remap-bad-version"] = Check(
    reference=("-m", "texflux", "synctex", "remap", "work.synctex",
               "--map", "generated.tex.tfxmap"),
    ported=(str(ROOT / "bin" / "texflux"), "synctex", "remap", "work.synctex",
            "--map", "generated.tex.tfxmap"),
    configuration="application",
    outputs=("work.synctex",),
    setup=(("compile", "{input}", "-o", "generated.tex"),),
    fixture=(("work.synctex", "SyncTeX Version:abc\nInput:1:{here}/generated.tex\nContent:\n"),),
    only="examples/basic.tfx",
)

#: Where the corpus of real documents lives.
CORPUS_GLOBS = (
    "tests/golden/*/*.tfx",
    "examples/*.tfx",
    "examples/modules/*.tfx",
    "src/texflux/*.tfxm",
    "tests/conformance/cases/**/*.tfx",
)

#: Test modules whose string literals are documents worth compiling. Between
#: them these hold every malformed document this project has ever pinned down,
#: which is a far better corpus than a fresh guess at what is worth testing.
SNIPPET_SOURCES = (
    "tests/test_parser.py",
    "tests/test_spans.py",
    "tests/test_compile.py",
    "tests/test_macros.py",
    "tests/test_flags.py",
    "tests/test_interpolation.py",
    "tests/test_prelude.py",
    "tests/test_modules.py",
    "tests/test_diagnostics.py",
    "tests/test_ast.py",
)

#: A literal is a document if it spells something only a document spells.
SOURCE_MARKERS = ("\n", "::", ">>", "!|", "@@", "!!")


@dataclass(frozen=True)
class Case:
    """One document, named for the report and for --only."""

    name: str
    #: The path the document is compiled under, which its spans quote.
    filename: str
    #: Every file the case needs, by the path it must be written at.
    files: dict[str, str] = field(default_factory=dict)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def corpus_cases() -> list[Case]:
    """Every document in the repository, with whatever it imports beside it.

    An import resolves against the file that writes it, so a case brings its
    whole directory rather than only itself.
    """

    cases = []
    for pattern in CORPUS_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            relative = path.relative_to(ROOT).as_posix()
            companions = {
                companion.relative_to(ROOT).as_posix(): read(companion)
                for suffix in ("*.tfx", "*.tfxm")
                for companion in sorted(path.parent.rglob(suffix))
            }
            cases.append(Case(relative, relative, companions))
    return cases


def snippet_cases() -> list[Case]:
    """Every source snippet written inside the Python test suite."""

    cases = []
    for module in SNIPPET_SOURCES:
        path = ROOT / module
        tree = ast.parse(read(path), str(path))
        seen: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            source = node.value
            if not any(marker in source for marker in SOURCE_MARKERS):
                continue
            if source in seen:
                continue
            seen.add(source)
            cases.append(
                Case(f"{Path(module).stem}:{node.lineno}", "x.tfx", {"x.tfx": source})
            )
    return cases


def all_cases() -> list[Case]:
    return corpus_cases() + snippet_cases()


@dataclass(frozen=True)
class Outcome:
    """Everything one run of one implementation can be compared on."""

    status: int
    out: bytes
    error: bytes
    files: dict[str, bytes | None]


def execute(command: tuple[str, ...], check: Check, case: Case, directory: Path) -> Outcome:
    """Run one implementation in its own copy of the case's files."""

    directory.mkdir(parents=True, exist_ok=True)
    for name, content in case.files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def spell(command: tuple[str, ...]) -> tuple[list[str], dict[str, str]]:
        words = [word.replace("{input}", case.filename) for word in command]
        environment = dict(os.environ)
        if words[0] == "-m":
            words = [sys.executable, *words]
            environment["PYTHONPATH"] = str(ROOT / "src")
        elif words[0].endswith(".py"):
            words = [sys.executable, *words]
        return words, environment

    # A setup command belongs to the implementation under test: the remap has
    # to be given the map that implementation wrote, not the other one's. How
    # to name that implementation is the front of its own command, which is one
    # word for a program and two for a module.
    program = command[:2] if command[0] == "-m" else command[:1]
    for step in check.setup:
        words, environment = spell(program + step)
        subprocess.run(words, capture_output=True, cwd=directory, env=environment)
    for name, template in check.fixture:
        (directory / name).write_text(
            template.replace("{here}", str(directory.resolve())), encoding="utf-8"
        )

    words, environment = spell(command)
    fed = case.files.get(case.filename, "").encode("utf-8") if check.stdin else None
    finished = subprocess.run(
        words, capture_output=True, cwd=directory, env=environment, input=fed
    )
    written = {
        name: (directory / name).read_bytes() if (directory / name).exists() else None
        for name in check.outputs
    }
    return Outcome(finished.returncode, finished.stdout, finished.stderr, written)


def compare(case: Case, check: Check, workspace: Path) -> str | None:
    """The report of a disagreement on one check, or None when they agree.

    Both implementations run at the same path, one after the other, because a
    diagnostic about a file the operating system refused to open quotes that
    file's absolute path. Two directories would have made every such message
    differ for a reason that is the harness's fault rather than the port's.
    """

    reference = execute(check.reference, check, case, workspace / "run")
    shutil.rmtree(workspace / "run")
    ported = execute(check.ported, check, case, workspace / "run")

    if reference.status != ported.status:
        return (
            f"exit status {reference.status} but got {ported.status}\n"
            f"reference stderr:\n{reference.error.decode(errors='replace')}"
            f"ported stderr:\n{ported.error.decode(errors='replace')}"
        )
    if reference.out != ported.out:
        return "stdout differs\n" + diff(reference.out, ported.out)
    if reference.error != ported.error:
        return "stderr differs\n" + diff(reference.error, ported.error)
    for name in check.outputs:
        if reference.files[name] != ported.files[name]:
            return f"{name} differs\n" + diff(reference.files[name], ported.files[name])
    return None


def diff(reference: bytes | None, ported: bytes | None) -> str:
    if reference is None:
        return "the reference wrote no file but the port did"
    if ported is None:
        return "the reference wrote a file but the port did not"
    lines = difflib.unified_diff(
        reference.decode("utf-8", errors="replace").splitlines(),
        ported.decode("utf-8", errors="replace").splitlines(),
        fromfile="reference",
        tofile="ported",
        lineterm="",
        n=1,
    )
    return "\n".join(list(lines)[:40])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", metavar="PATTERN", help="run only matching cases")
    parser.add_argument(
        "--check",
        metavar="NAME",
        choices=sorted(CHECKS),
        action="append",
        help="run only this check; may be repeated",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count(),
        metavar="N",
        help="how many cases to run at once (default: one per processor)",
    )
    parser.add_argument("--list", action="store_true", help="name the cases and stop")
    parser.add_argument(
        "--keep", action="store_true", help="keep the directory each case ran in"
    )
    args = parser.parse_args(argv)

    cases = all_cases()
    if args.only:
        cases = [case for case in cases if fnmatch.fnmatch(case.name, args.only)]
    if args.list:
        for case in cases:
            print(case.name)
        return 0
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    checks = args.check or sorted(CHECKS)
    for name in checks:
        program = Path(CHECKS[name].ported[0])
        if not program.exists():
            print(
                f"{program.relative_to(ROOT)} is missing; build it with:"
                f" dub build -c {CHECKS[name].configuration}",
                file=sys.stderr,
            )
            return 2

    def run_case(case: Case) -> list[tuple[str, str | None]]:
        workspace = Path(tempfile.mkdtemp())
        try:
            return [
                (name, compare(case, CHECKS[name], workspace / name))
                for name in checks
                if CHECKS[name].only is None
                or fnmatch.fnmatch(case.name, CHECKS[name].only)
            ]
        finally:
            if args.keep:
                print(f"{case.name} ran in {workspace}")
            else:
                shutil.rmtree(workspace, ignore_errors=True)

    failures = 0
    runs = 0
    # Each case is independent and spends almost all its time waiting on two
    # processes, so running several at once is most of the wall clock back.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for case, reports in zip(cases, pool.map(run_case, cases)):
            for name, report in reports:
                runs += 1
                if report is None:
                    continue
                failures += 1
                print(f"FAIL {case.name} [{name}]")
                print(report)

    print(
        f"\n{runs - failures} of {runs} comparisons agree"
        f" ({len(cases)} cases x {len(checks)} checks)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
