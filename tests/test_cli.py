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


if __name__ == "__main__":
    unittest.main()
