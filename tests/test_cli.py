import contextlib
import hashlib
import io
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import tomllib
from types import SimpleNamespace
import unittest

from texflux.cli import main

from .support import TempDirTestCase


class CliTests(TempDirTestCase):
    def test_success_writes_utf8_output(self):
        input_path = self.root / "slides.tfx"
        output_path = self.root / "out.tex"
        input_path.write_text("@frame{日本語}::\n    本文\n", encoding="utf-8")

        self.assertEqual(
            main(["compile", str(input_path), "-o", str(output_path)]),
            0,
        )
        self.assertEqual(
            output_path.read_text(encoding="utf-8"),
            "\\begin{frame}{日本語}\n本文\n\\end{frame}\n",
        )

    def test_success_writes_deterministic_source_map_beside_output(self):
        input_path = self.root / "slides.tfx"
        output_path = self.root / "out.tex"
        source_bytes = "raw 日本語\n".encode("utf-8")
        input_path.write_bytes(source_bytes)

        self.assertEqual(
            main(["compile", str(input_path), "-o", str(output_path)]),
            0,
        )

        map_path = self.root / "out.tex.tfxmap"
        payload = json.loads(map_path.read_text(encoding="utf-8"))
        output_bytes = output_path.read_bytes()
        self.assertEqual(payload["generated"]["path"], "out.tex")
        self.assertEqual(
            payload["generated"]["sha256"],
            hashlib.sha256(output_bytes).hexdigest(),
        )
        self.assertEqual(payload["sources"][0]["path"], "slides.tfx")
        self.assertEqual(
            payload["sources"][0]["sha256"],
            hashlib.sha256(source_bytes).hexdigest(),
        )
        self.assertTrue(map_path.read_bytes().endswith(b"\n"))

    def test_compile_failure_does_not_change_existing_output(self):
        input_path = self.root / "bad.tfx"
        output_path = self.root / "out.tex"
        input_path.write_text("@foo {bad}\n", encoding="utf-8")
        output_path.write_text("keep\n", encoding="utf-8")
        map_path = self.root / "out.tex.tfxmap"
        map_path.write_text("keep-map\n", encoding="utf-8")
        stderr = StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(
                ["compile", str(input_path), "-o", str(output_path)]
            )
        self.assertEqual(result, 1)
        self.assertEqual(output_path.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(map_path.read_text(encoding="utf-8"), "keep-map\n")
        self.assertIn("bad.tfx:1:6: parse error:", stderr.getvalue())
        self.assertIn("[P011]", stderr.getvalue())

    def test_non_tfx_input_is_rejected(self):
        input_path = self.root / "slides.txt"
        output_path = self.root / "out.tex"
        input_path.write_text("raw\n", encoding="utf-8")
        stderr = StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main(
                ["compile", str(input_path), "-o", str(output_path)]
            )
        self.assertEqual(result, 1)
        self.assertIn(".tfx extension", stderr.getvalue())
        self.assertFalse(output_path.exists())

    def test_source_comments_are_wired_through_cli(self):
        input_path = self.root / "slides.tfx"
        output_path = self.root / "out.tex"
        input_path.write_text("raw\n", encoding="utf-8")

        self.assertEqual(
            main(
                [
                    "compile",
                    str(input_path),
                    "-o",
                    str(output_path),
                    "--source-comments",
                ]
            ),
            0,
        )
        self.assertEqual(
            output_path.read_text(encoding="utf-8"),
            f"% texflux: {input_path}:1\nraw\n",
        )

    def test_same_path_and_missing_parent_are_rejected(self):
        input_path = self.root / "slides.tfx"
        input_path.write_text("raw\n", encoding="utf-8")
        stderr = StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(
                main(["compile", str(input_path), "-o", str(input_path)]),
                1,
            )
        self.assertIn("different paths", stderr.getvalue())

        output_path = self.root / "missing" / "out.tex"
        with contextlib.redirect_stderr(StringIO()):
            self.assertEqual(
                main(["compile", str(input_path), "-o", str(output_path)]),
                1,
            )
        self.assertFalse(output_path.exists())

        with contextlib.redirect_stderr(stderr):
            result = main(["compile", str(input_path), "-o", "/"])
        self.assertEqual(result, 1)
        self.assertIn("texflux:", stderr.getvalue())

    def test_argparse_usage_is_exit_code_two(self):
        stderr = StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main([])
        self.assertEqual(result, 2)
        self.assertIn("usage:", stderr.getvalue())


@contextlib.contextmanager
def stdin_bytes(data: bytes):
    """Feed ``data`` to a command that reads sys.stdin.buffer."""

    original = sys.stdin
    sys.stdin = SimpleNamespace(buffer=io.BytesIO(data))
    try:
        yield
    finally:
        sys.stdin = original


class CliCheckTests(TempDirTestCase):
    BAD = "@frame{x}::\n    @foo{bad\n"

    def check(self, *arguments, stdin=None):
        """Run 'texflux check', returning its status, stdout and stderr."""

        out, err = StringIO(), StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if stdin is None:
                status = main(["check", *arguments])
            else:
                with stdin_bytes(stdin):
                    status = main(["check", *arguments])
        return status, out.getvalue(), err.getvalue()

    def test_a_clean_document_says_nothing_at_all(self):
        path = self.write("slides.tfx", "@frame{t}::\n    body\n")
        self.assertEqual(self.check(str(path)), (0, "", ""))

    def test_an_error_is_one_line_on_stdout_and_exit_one(self):
        path = self.write("slides.tfx", self.BAD)
        status, out, err = self.check(str(path))

        self.assertEqual(status, 1)
        self.assertEqual(
            out,
            f"{path}:2:9: parse error: unclosed required group [P004]\n",
        )
        self.assertEqual(err, "")

    def test_a_comment_in_a_value_is_laid_out_rather_than_reported(self):
        # The closing brace moves below the comment instead of being
        # commented out, so there is nothing left to report.
        path = self.write("slides.tfx", "\\foo:::\n    - a % trailing\n")
        status, out, _ = self.check(str(path))

        self.assertEqual(status, 0)
        self.assertEqual(out, "")

    def test_related_locations_print_as_indented_notes(self):
        self.write("broken.tfx", "!defmacro{m}{x}{x}::\n    A\n")
        self.write("mid.tfx", "!import{broken.tfx}\n")
        path = self.write("main.tfx", "!import{mid.tfx}\n")
        status, out, _ = self.check(str(path))

        notes = [line for line in out.splitlines() if ": note: " in line]
        self.assertEqual(status, 1)
        self.assertEqual(len(notes), 2)
        self.assertTrue(all(line.startswith("  ") for line in notes))
        self.assertIn("mid.tfx", notes[0])
        self.assertIn("main.tfx", notes[1])
        self.assertTrue(all(line.endswith("imported from here") for line in notes))

    def test_json_carries_the_whole_report(self):
        path = self.write("slides.tfx", self.BAD)
        status, out, _ = self.check(str(path), "--format", "json")
        payload = json.loads(out)

        self.assertEqual(status, 1)
        self.assertEqual(payload["format"], "texflux-diagnostics")
        self.assertEqual(payload["diagnostics"][0]["code"], "P004")
        self.assertEqual(payload["sources"][0]["file"], str(path))
        self.assertTrue(out.endswith("}\n"))

    def test_pretty_indents_the_same_json(self):
        path = self.write("slides.tfx", self.BAD)
        _, compact, _ = self.check(str(path), "--format", "json")
        _, pretty, _ = self.check(str(path), "--format", "json", "--pretty")

        self.assertIn("\n  ", pretty)
        self.assertEqual(json.loads(pretty), json.loads(compact))

    def test_pretty_without_json_is_a_usage_error(self):
        path = self.write("slides.tfx", "raw\n")
        status, out, err = self.check(str(path), "--pretty")

        self.assertEqual(status, 2)
        self.assertEqual(out, "")
        self.assertIn("--pretty requires --format json", err)

    def test_stdin_is_checked_under_the_name_it_is_given(self):
        status, out, _ = self.check(
            "-", "--stdin-filename", "/w/buffer.tfx", stdin=self.BAD.encode(),
        )
        self.assertEqual(status, 1)
        self.assertTrue(out.startswith("/w/buffer.tfx:2:9: parse error:"))

    def test_a_stdin_name_also_decides_where_imports_resolve(self):
        self.write("dep.tfx", "DEP\n")
        name = str(self.root / "buffer.tfx")
        status, out, err = self.check(
            "-", "--stdin-filename", name, stdin=b"!import{dep.tfx}\n",
        )
        self.assertEqual((status, out, err), (0, "", ""))

    def test_a_stdin_name_reaches_the_source_table(self):
        status, out, _ = self.check(
            "-", "--stdin-filename", "/w/buffer.tfx", "--format", "json",
            stdin=self.BAD.encode(),
        )
        self.assertEqual(status, 1)
        self.assertEqual(
            json.loads(out)["sources"][0]["file"], "/w/buffer.tfx",
        )

    def test_a_stdin_name_must_be_a_tfx_and_needs_stdin(self):
        path = self.write("slides.tfx", "raw\n")
        status, out, err = self.check(
            "-", "--stdin-filename", "buffer.txt", stdin=b"raw\n",
        )
        self.assertEqual((status, out), (2, ""))
        self.assertIn(".tfx extension", err)

        status, out, err = self.check(str(path), "--stdin-filename", "x.tfx")
        self.assertEqual((status, out), (2, ""))
        self.assertIn("requires INPUT '-'", err)

    def test_being_unable_to_look_at_the_document_exits_two(self):
        self.write("slides.txt", "raw\n")
        self.write("bad-bytes.tfx", b"\xff\xfe raw\n")
        cases = (
            (str(self.root / "slides.txt"), ".tfx extension"),
            (str(self.root / "missing.tfx"), "No such file"),
            (str(self.root / "bad-bytes.tfx"), "utf-8"),
        )
        for argument, expected in cases:
            with self.subTest(input=argument):
                status, out, err = self.check(argument)
                self.assertEqual((status, out), (2, ""))
                self.assertIn(expected, err)

    def test_an_unknown_flag_override_exits_two(self):
        path = self.write("slides.tfx", "raw\n")
        status, out, err = self.check(str(path), "--flag", "nosuch")

        self.assertEqual((status, out), (2, ""))
        self.assertIn("unknown build flag 'nosuch'", err)

    def test_a_flag_selects_which_document_is_checked(self):
        path = self.write(
            "slides.tfx",
            "!flag{draft}{off}\n!when{draft} >> !nosuchmacro\n",
        )
        self.assertEqual(self.check(str(path))[0], 0)
        self.assertEqual(self.check(str(path), "--flag", "draft")[0], 1)

    def test_output_is_written_as_utf8_bytes_when_stdout_has_a_buffer(self):
        # The byte branch exists so a non-UTF-8 console encoding and CRLF
        # translation cannot corrupt the JSON; a StringIO would skip it.
        # The message quotes the name, so non-ASCII reaches the output.
        path = self.write("slides.tfx", "!defmacro{\u65e5}::\n    A\n")
        captured = io.BytesIO()
        original = sys.stdout
        sys.stdout = SimpleNamespace(
            buffer=captured, write=lambda text: self.fail("used the text branch")
        )
        try:
            status = main(["check", str(path), "--format", "json"])
        finally:
            sys.stdout = original

        self.assertEqual(status, 1)
        self.assertTrue(captured.getvalue().endswith(b"}\n"))
        self.assertIn("\u65e5".encode("utf-8"), captured.getvalue())
        self.assertNotIn(b"\r\n", captured.getvalue())

    def test_check_writes_no_tex_and_no_source_map(self):
        path = self.write("slides.tfx", "raw\n")
        self.check(str(path))
        self.assertEqual(
            sorted(item.name for item in self.root.iterdir()), ["slides.tfx"],
        )


class CliFlagTests(TempDirTestCase):
    SOURCE = (
        "!flag{draft}{off}\n"
        "!flag{notes}{on}\n"
        "!when{draft} >> \\todo\n"
        "!when{notes} >> \\note\n"
    )

    def run_compile(self, *flags):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "slides.tfx"
            output_path = root / "out.tex"
            input_path.write_text(self.SOURCE, encoding="utf-8")
            arguments = ["compile", str(input_path), "-o", str(output_path)]
            for flag in flags:
                arguments += ["--flag", flag]
            stderr = StringIO()
            with contextlib.redirect_stderr(stderr):
                status = main(arguments)
            text = output_path.read_text(encoding="utf-8") if status == 0 else ""
            return status, text, stderr.getvalue()

    def test_declared_defaults_apply_without_any_flag(self):
        self.assertEqual(self.run_compile()[:2], (0, "\\note\n"))

    def test_a_bare_name_turns_a_flag_on(self):
        self.assertEqual(
            self.run_compile("draft")[:2],
            (0, "\\todo\n\\note\n"),
        )

    def test_an_explicit_value_can_turn_a_flag_off(self):
        # Every statement is gone, so only the renderer's final newline is left.
        self.assertEqual(self.run_compile("notes=off")[:2], (0, "\n"))

    def test_a_flag_no_declaration_matches_is_rejected(self):
        status, _, stderr = self.run_compile("drfat")
        self.assertEqual(status, 1)
        self.assertIn("drfat", stderr)
        self.assertIn("draft", stderr)

    def test_a_value_outside_on_and_off_is_rejected(self):
        status, _, stderr = self.run_compile("draft=yes")
        self.assertEqual(status, 1)
        self.assertIn("draft=yes", stderr)

    def test_surrounding_whitespace_is_ignored(self):
        # Shell quoting and make substitution both leak spaces in.
        self.assertEqual(
            self.run_compile(" draft ", "notes = off")[:2],
            (0, "\\todo\n"),
        )

    def test_setting_one_flag_twice_is_rejected(self):
        status, _, stderr = self.run_compile("draft", "draft=off")
        self.assertEqual(status, 1)
        self.assertIn("more than once", stderr)


class EntryPointTests(TempDirTestCase):
    def test_the_console_script_points_at_main(self):
        root = Path(__file__).parents[1]
        metadata = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["project"]["name"], "texflux")
        self.assertEqual(
            metadata["project"]["scripts"],
            {"texflux": "texflux.cli:main"},
        )

    def test_a_missing_subcommand_is_a_usage_error(self):
        input_path = self.write("input.tfx", "raw\n")
        stderr = StringIO()
        with contextlib.redirect_stderr(stderr):
            status = main([str(input_path), "-o", str(self.root / "out.tex")])
        self.assertEqual(status, 2)
        self.assertIn("invalid choice", stderr.getvalue())
        self.assertFalse((self.root / "out.tex").exists())


if __name__ == "__main__":
    unittest.main()
