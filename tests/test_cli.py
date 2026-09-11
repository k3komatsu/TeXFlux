import contextlib
from io import StringIO
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
            input_path.write_text("@frame{日本語}:\n    本文\n", encoding="utf-8")

            self.assertEqual(
                main(["compile", str(input_path), "-o", str(output_path)]),
                0,
            )
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "\\begin{frame}{日本語}\n本文\n\\end{frame}\n",
            )

    def test_compile_failure_does_not_change_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "bad.tfx"
            output_path = root / "out.tex"
            input_path.write_text("@foo {bad}\n", encoding="utf-8")
            output_path.write_text("keep\n", encoding="utf-8")
            stderr = StringIO()
            with contextlib.redirect_stderr(stderr):
                result = main(
                    ["compile", str(input_path), "-o", str(output_path)]
                )
            self.assertEqual(result, 1)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "keep\n")
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

    def test_argparse_usage_is_exit_code_two(self):
        stderr = StringIO()
        with contextlib.redirect_stderr(stderr):
            result = main([])
        self.assertEqual(result, 2)
        self.assertIn("usage:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
