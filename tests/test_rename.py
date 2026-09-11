import contextlib
from io import StringIO
from pathlib import Path
import tomllib
import tempfile
import unittest

from texflux import compile_text
from texflux.cli import main


class RenameTests(unittest.TestCase):
    def test_texflux_package_compiles_tfx_source(self):
        self.assertEqual(
            compile_text("@center: |\n    BODY\n", filename="input.tfx"),
            "\\begin{center}\nBODY\n\\end{center}\n",
        )

    def test_texflux_compile_subcommand_and_no_old_import(self):
        source_root = Path(__file__).parents[1] / "src"
        self.assertTrue((source_root / "texflux").is_dir())
        self.assertFalse((source_root / "beamercraft").exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.tfx"
            output_path = root / "output.tex"
            input_path.write_text("raw\n", encoding="utf-8")
            self.assertEqual(
                main(["compile", str(input_path), "-o", str(output_path)]),
                0,
            )
            self.assertEqual(output_path.read_text(encoding="utf-8"), "raw\n")

            stderr = StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(
                    main([str(input_path), "-o", str(output_path)]),
                    2,
                )
            self.assertIn("invalid choice", stderr.getvalue())

    def test_metadata_publishes_only_texflux(self):
        root = Path(__file__).parents[1]
        metadata = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["project"]["name"], "texflux")
        self.assertEqual(
            metadata["project"]["scripts"],
            {"texflux": "texflux.cli:main"},
        )


if __name__ == "__main__":
    unittest.main()
