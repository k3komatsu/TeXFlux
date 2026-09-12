import contextlib
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from texflux.cli import main


class CliTests(unittest.TestCase):
    def test_success_writes_utf8_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "slides.tfx"
            output_path = root / "out.tex"
            input_path.write_text("@frame{日本語}: |\n    本文\n", encoding="utf-8")

            self.assertEqual(
                main(["compile", str(input_path), "-o", str(output_path)]),
                0,
            )
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "\\begin{frame}{日本語}\n本文\n\\end{frame}\n",
            )

    def test_success_writes_deterministic_source_map_beside_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "slides.tfx"
            output_path = root / "out.tex"
            source_bytes = "raw 日本語\n".encode("utf-8")
            input_path.write_bytes(source_bytes)

            self.assertEqual(
                main(["compile", str(input_path), "-o", str(output_path)]),
                0,
            )

            map_path = root / "out.tex.tfxmap"
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "bad.tfx"
            output_path = root / "out.tex"
            input_path.write_text("@foo {bad}\n", encoding="utf-8")
            output_path.write_text("keep\n", encoding="utf-8")
            map_path = root / "out.tex.tfxmap"
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

    def test_non_tfx_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "slides.txt"
            output_path = root / "out.tex"
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "slides.tfx"
            output_path = root / "out.tex"
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "slides.tfx"
            input_path.write_text("raw\n", encoding="utf-8")
            stderr = StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(
                    main(["compile", str(input_path), "-o", str(input_path)]),
                    1,
                )
            self.assertIn("different paths", stderr.getvalue())

            output_path = root / "missing" / "out.tex"
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


class CliFlagTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
