import re
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

from texflux.cli import main


@unittest.skipUnless(
    shutil.which("pdflatex") and shutil.which("synctex"),
    "TeX Live pdflatex and synctex are not installed",
)
class EndToEndTests(unittest.TestCase):
    def test_compile_engine_remap_and_native_queries_resolve_tfx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.tfx"
            generated_path = root / "generated.tex"
            source_text = (
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "@center:\n"
                "    First block\n"
                "@center:\n"
                "    End-to-end marker\n"
                "\\end{document}\n"
            )
            source_path.write_text(source_text, encoding="utf-8")
            source_lines = source_text.splitlines()
            self.assertIn("    End-to-end marker", source_lines)
            source_marker_line = source_lines.index("    End-to-end marker") + 1

            self.assertEqual(
                main(
                    [
                        "compile",
                        str(source_path),
                        "-o",
                        str(generated_path),
                    ]
                ),
                0,
            )
            latex = subprocess.run(
                [
                    "pdflatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-synctex=1",
                    "-output-directory",
                    str(root),
                    str(generated_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(latex.returncode, 0, latex.stdout + latex.stderr)

            sync_path = root / "generated.synctex.gz"
            if not sync_path.exists():
                sync_path = root / "generated.synctex"
            self.assertTrue(sync_path.exists(), f"missing SyncTeX output near {root}")
            generated_lines = generated_path.read_text(encoding="utf-8").splitlines()
            self.assertIn("End-to-end marker", generated_lines)
            generated_line = generated_lines.index("End-to-end marker") + 1
            original_view = subprocess.run(
                [
                    "synctex",
                    "view",
                    "-i",
                    f"{generated_line}:0:{generated_path}",
                    "-o",
                    str(root / "generated.pdf"),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                original_view.returncode,
                0,
                original_view.stdout + original_view.stderr,
            )
            original_coordinates = self._coordinates(original_view.stdout)

            self.assertEqual(
                main(
                    [
                        "synctex",
                        "remap",
                        str(sync_path),
                        "--map",
                        str(root / "generated.tex.tfxmap"),
                    ]
                ),
                0,
            )

            view = subprocess.run(
                [
                    "synctex",
                    "view",
                    "-i",
                    f"{source_marker_line}:0:{source_path}",
                    "-o",
                    str(root / "generated.pdf"),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(view.returncode, 0, view.stdout + view.stderr)
            self.assertIn("SyncTeX result begin", view.stdout)
            page, x, y = self._coordinates(view.stdout)
            self.assertEqual((page, x, y), original_coordinates)

            edit = subprocess.run(
                [
                    "synctex",
                    "edit",
                    "-o",
                    f"{page}:{x}:{y}:{root / 'generated.pdf'}",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(edit.returncode, 0, edit.stdout + edit.stderr)
            self.assertIn(f"Input:{source_path}", edit.stdout)
            self.assertRegex(edit.stdout, rf"(?m)^Line:{source_marker_line}$")

    @staticmethod
    def _coordinates(output: str) -> tuple[str, str, str]:
        page = re.search(r"(?m)^Page:(\S+)$", output)
        x = re.search(r"(?m)^x:(\S+)$", output)
        y = re.search(r"(?m)^y:(\S+)$", output)
        if page is None or x is None or y is None:
            raise AssertionError(f"missing native SyncTeX coordinates:\n{output}")
        return page.group(1), x.group(1), y.group(1)


if __name__ == "__main__":
    unittest.main()
