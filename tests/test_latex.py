import shutil
import subprocess
import unittest

from texflux import compile_text

from .support import TempDirTestCase


@unittest.skipUnless(shutil.which("pdflatex"), "pdflatex is not installed")
class LatexIntegrationTests(TempDirTestCase):
    def test_generated_content_can_be_input_by_beamer(self):
        (self.root / "content.tex").write_text(
            compile_text("@frame{Title}:\n    Generated body\n"),
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


if __name__ == "__main__":
    unittest.main()
