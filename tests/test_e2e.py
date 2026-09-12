import re
import shutil
import subprocess
import unittest

from texflux.cli import main

from .support import TempDirTestCase


@unittest.skipUnless(
    shutil.which("pdflatex") and shutil.which("synctex"),
    "TeX Live pdflatex and synctex are not installed",
)
class EndToEndTests(TempDirTestCase):
    def test_compile_engine_remap_and_native_queries_resolve_tfx(self):
        source_path = self.root / "source.tfx"
        generated_path = self.root / "generated.tex"
        source_text = (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "@center: |\n"
            "    First block\n"
            "@center: |\n"
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
                str(self.root),
                str(generated_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(latex.returncode, 0, latex.stdout + latex.stderr)

        sync_path = self.root / "generated.synctex.gz"
        if not sync_path.exists():
            sync_path = self.root / "generated.synctex"
        self.assertTrue(sync_path.exists(), f"missing SyncTeX output near {self.root}")
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
                str(self.root / "generated.pdf"),
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
                    str(self.root / "generated.tex.tfxmap"),
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
                str(self.root / "generated.pdf"),
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
                f"{page}:{x}:{y}:{self.root / 'generated.pdf'}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(edit.returncode, 0, edit.stdout + edit.stderr)
        self.assertIn(f"Input:{source_path}", edit.stdout)
        self.assertRegex(edit.stdout, rf"(?m)^Line:{source_marker_line}$")

    def test_inverse_search_reaches_the_module_that_wrote_the_line(self):
        """Imported content resolves to the callee, not to the !import line."""

        source_path = self.root / "deck.tfx"
        generated_path = self.root / "generated.tex"
        part_path = self.write(
            "part.tfx",
            "@center: |\n    Imported marker\n",
        )
        source_path.write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "@center: |\n"
            "    Root marker\n"
            "!import{part.tfx}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )

        self.assertEqual(
            main(["compile", str(source_path), "-o", str(generated_path)]),
            0,
        )
        latex = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-synctex=1",
                "-output-directory",
                str(self.root),
                str(generated_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(latex.returncode, 0, latex.stdout + latex.stderr)

        sync_path = self.root / "generated.synctex.gz"
        if not sync_path.exists():
            sync_path = self.root / "generated.synctex"

        # Locate the imported line in the generated TeX before remapping,
        # because remapping is what replaces that input with the .tfx files.
        generated_lines = generated_path.read_text(encoding="utf-8").splitlines()
        marker_line = generated_lines.index("Imported marker") + 1
        before = subprocess.run(
            [
                "synctex",
                "view",
                "-i",
                f"{marker_line}:0:{generated_path}",
                "-o",
                str(self.root / "generated.pdf"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
        expected_coordinates = self._coordinates(before.stdout)

        self.assertEqual(
            main(
                [
                    "synctex",
                    "remap",
                    str(sync_path),
                    "--map",
                    str(self.root / "generated.tex.tfxmap"),
                ]
            ),
            0,
        )

        # Forward search now starts at the module that wrote the line, not at
        # the deck that imported it.
        view = subprocess.run(
            [
                "synctex",
                "view",
                "-i",
                f"2:0:{part_path}",
                "-o",
                str(self.root / "generated.pdf"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(view.returncode, 0, view.stdout + view.stderr)
        page, x, y = self._coordinates(view.stdout)
        self.assertEqual((page, x, y), expected_coordinates)

        edit = subprocess.run(
            [
                "synctex",
                "edit",
                "-o",
                f"{page}:{x}:{y}:{self.root / 'generated.pdf'}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(edit.returncode, 0, edit.stdout + edit.stderr)
        # The point is which file it lands in: imported content must not be
        # retargeted onto the deck's !import line. Which of the module's own
        # lines a coordinate picks is SyncTeX's box choice, and the forward
        # search above already pinned the mapping's precision.
        self.assertIn(f"Input:{part_path}", edit.stdout)
        self.assertNotIn(f"Input:{source_path}", edit.stdout)

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
