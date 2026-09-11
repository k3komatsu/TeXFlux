from pathlib import Path
import unittest

from texflux import compile_text


class GoldenTests(unittest.TestCase):
    def test_golden_outputs_are_exact(self):
        root = Path(__file__).with_name("golden")
        input_paths = sorted(root.glob("*/input.tfx"))
        self.assertEqual(
            {path.parent.name for path in input_paths},
            {
                "generic-groups",
                "items-nested",
                "nested-environments",
                "real-slide",
                "representative-command",
                "source-comments",
                "stack-items",
                "stack-three-level",
                "single-long-argument",
                "structured-command",
                "structured-environment",
                "structured-mixed",
                "vpad",
            },
        )
        for input_path in input_paths:
            with self.subTest(case=input_path.parent.name):
                expected = input_path.with_name("expected.tex").read_bytes()
                actual = compile_text(
                    input_path.read_text(encoding="utf-8"),
                    filename=input_path.relative_to(root.parent.parent).as_posix(),
                    source_comments=input_path.parent.name == "source-comments",
                ).encode("utf-8")
                self.assertEqual(actual, expected)

    def test_small_examples_match_documented_goldens(self):
        root = Path(__file__).parents[1] / "examples"
        for stem in ("basic", "structured", "stacked-items"):
            with self.subTest(example=stem):
                actual = compile_text(
                    (root / f"{stem}.tfx").read_text(encoding="utf-8"),
                    filename=f"examples/{stem}.tfx",
                )
                expected = (root / f"{stem}.tex").read_text(encoding="utf-8")
                self.assertEqual(actual, expected)

    def test_converted_content_example_compiles(self):
        root = Path(__file__).parents[1] / "examples"
        output = compile_text(
            (root / "content.tfx").read_text(encoding="utf-8"),
            filename="examples/content.tfx",
        )
        self.assertIn("\\rightnotebox{Note}{", output)
        self.assertIn("\\begin{itemize}", output)


if __name__ == "__main__":
    unittest.main()
