import shutil
import subprocess
import unittest

from texflux import compile_text

from .support import TempDirTestCase


@unittest.skipUnless(shutil.which("pdflatex"), "pdflatex is not installed")
class LatexIntegrationTests(TempDirTestCase):
    def test_generated_content_can_be_input_by_beamer(self):
        (self.root / "content.tex").write_text(
            compile_text("@frame{Title}::\n    Generated body\n"),
            encoding="utf-8",
        )
        (self.root / "main.tex").write_text(
            "\\documentclass{beamer}\n"
            "\\begin{document}\n"
            "\\input{content.tex}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                str(self.root),
                str(self.root / "main.tex"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generated_brace_layout_is_valid_tex(self):
        # The brace a block value opens sits behind a '%', and a value whose
        # last line carries one keeps its closing brace on the next line.
        # Both are layout decisions TeX has to read the way they are meant.
        (self.root / "content.tex").write_text(
            compile_text(
                "\\wrap::\n"
                "    block value\n"
                "\\pair:::\n"
                "    - trailing % comment\n"
                "    - tail\n"
                "@{\\small}::\n"
                "    inside a literal brace container\n"
            ),
            encoding="utf-8",
        )
        (self.root / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\newcommand\\wrap[1]{#1}\n"
            "\\newcommand\\pair[2]{#1#2}\n"
            "\\begin{document}\n"
            "\\input{content.tex}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                str(self.root),
                str(self.root / "main.tex"),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
