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
                "build-flags",
                "generic-groups",
                "macro-each",
                "macro-interpolation",
                "macro-variadic",
                "macro-wrapper",
                "module-import",
                "module-macros",
                "nested-environments",
                "off-drop",
                "real-slide",
                "representative-command",
                "source-comments",
                "standard-flow",
                "stack-sequence",
                "stack-three-level",
                "raw-mode",
                "single-long-argument",
                "structured-command",
                "structured-environment",
                "structured-mixed",
                "trailing-colon",
            },
        )
        for input_path in input_paths:
            with self.subTest(case=input_path.parent.name):
                expected = input_path.with_name("expected.tex").read_bytes()
                comments = input_path.parent.name == "source-comments"
                # A filename decides where imports resolve from, so it has to
                # be the real path. Only the source-comments case writes its
                # filename into the output, so only it needs a fixed spelling.
                actual = compile_text(
                    input_path.read_text(encoding="utf-8"),
                    filename=(
                        input_path.relative_to(root.parent.parent).as_posix()
                        if comments
                        else str(input_path)
                    ),
                    source_comments=comments,
                ).encode("utf-8")
                self.assertEqual(actual, expected)

    def test_build_flags_golden_also_pins_its_overridden_build(self):
        # The shared harness compiles every case with its declared defaults,
        # so the other build of this one needs its own exact output.
        root = Path(__file__).with_name("golden") / "build-flags"
        actual = compile_text(
            (root / "input.tfx").read_text(encoding="utf-8"),
            filename="tests/golden/build-flags/input.tfx",
            flags={"draft": True, "handout": False},
        )
        expected = (root / "expected-draft.tex").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_small_examples_match_documented_goldens(self):
        root = Path(__file__).parents[1] / "examples"
        for stem in ("basic", "structured", "stacked-items", "macros"):
            with self.subTest(example=stem):
                actual = compile_text(
                    (root / f"{stem}.tfx").read_text(encoding="utf-8"),
                    filename=f"examples/{stem}.tfx",
                )
                expected = (root / f"{stem}.tex").read_text(encoding="utf-8")
                self.assertEqual(actual, expected)

    def test_multi_source_example_matches_its_golden(self):
        # This one imports, so it needs its real path rather than the
        # repository relative label the other examples can use.
        path = Path(__file__).parents[1] / "examples" / "modules.tfx"
        actual = compile_text(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        expected = path.with_suffix(".tex").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_converted_content_example_compiles(self):
        root = Path(__file__).parents[1] / "examples"
        output = compile_text(
            (root / "content.tfx").read_text(encoding="utf-8"),
            filename="examples/content.tfx",
        )
        expected = (root / "content.tex").read_text(encoding="utf-8")
        self.assertEqual(output, expected)


if __name__ == "__main__":
    unittest.main()
