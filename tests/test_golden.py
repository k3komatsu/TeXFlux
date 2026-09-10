from pathlib import Path
import unittest

from beamercraft import compile_text


class GoldenTests(unittest.TestCase):
    def test_golden_outputs_are_exact(self):
        root = Path(__file__).with_name("golden")
        input_paths = sorted(root.glob("*/input.bmc"))
        self.assertEqual(
            {path.parent.name for path in input_paths},
            {
                "generic-groups",
                "items-nested",
                "nested-environments",
                "real-slide",
                "source-comments",
                "stack-items",
                "stack-three-level",
                "structured-command",
                "structured-environment",
                "structured-mixed",
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


if __name__ == "__main__":
    unittest.main()
