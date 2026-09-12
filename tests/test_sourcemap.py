import hashlib
import json
import unittest

from texflux import (
    CompilationResult,
    GeneratedSpan,
    RenderedDocument,
    RenderedFragment,
    SourcePosition,
    SourceSpan,
    __version__,
    compile_with_map,
)
from texflux.source_map import serialize_source_map

from .support import TempDirTestCase


class SourceMapTests(TempDirTestCase):
    def test_serialization_is_deterministic_and_omits_unmapped_fragments(self):
        source = "raw 日本語\n@center: |\n    BODY\n"
        source_bytes = source.encode("utf-8")

        result = compile_with_map(source, filename=str(self.root / "input.tfx"))
        generated_bytes = result.text.encode("utf-8")
        serialized = serialize_source_map(
            result,
            source_path=self.root / "input.tfx",
            generated_path=self.root / "out.tex",
            map_path=self.root / "out.tex.tfxmap",
            source_bytes=source_bytes,
            generated_bytes=generated_bytes,
        )
        serialized_again = serialize_source_map(
            compile_with_map(source, filename=str(self.root / "input.tfx")),
            source_path=self.root / "input.tfx",
            generated_path=self.root / "out.tex",
            map_path=self.root / "out.tex.tfxmap",
            source_bytes=source_bytes,
            generated_bytes=generated_bytes,
        )

        self.assertEqual(serialized, serialized_again)
        payload = json.loads(serialized)
        self.assertEqual(payload["format"], "texflux-source-map")
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["producer"]["name"], "texflux")
        self.assertEqual(payload["producer"]["version"], __version__)
        self.assertEqual(payload["generated"]["path"], "out.tex")
        self.assertEqual(
            payload["generated"]["sha256"],
            hashlib.sha256(generated_bytes).hexdigest(),
        )
        self.assertEqual(
            payload["sources"],
            [
                {
                    "id": 0,
                    "path": "input.tfx",
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                }
            ],
        )
        self.assertEqual(
            len(payload["mappings"]),
            sum(fragment.source is not None for fragment in result.rendered.fragments),
        )
        self.assertTrue(all("source" in mapping for mapping in payload["mappings"]))
        self.assertNotIn('"role":"synthetic"', serialized)
        self.assertTrue(serialized.endswith("\n"))
        self.assertEqual(serialized.count("\n"), 1)
        self.assertEqual(
            serialized,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        )

        def offset(position):
            lines = result.text.split("\n")
            return sum(
                len(line) + 1 for line in lines[: position["line"] - 1]
            ) + position["column"] - 1

        fragments = {
            (
                fragment.generated.start.line,
                fragment.generated.start.column,
                fragment.generated.end.line,
                fragment.generated.end.column,
            ): fragment.text
            for fragment in result.rendered.fragments
            if fragment.source is not None
        }
        for mapping in payload["mappings"]:
            generated = mapping["generated"]
            key = (
                generated["start"]["line"],
                generated["start"]["column"],
                generated["end"]["line"],
                generated["end"]["column"],
            )
            self.assertIn(key, fragments)
            self.assertEqual(
                result.text[offset(generated["start"]):offset(generated["end"])],
                fragments[key],
            )

    def test_generated_mappings_are_sorted_and_non_overlapping(self):
        source = "A\n\nB\n"
        result = compile_with_map(source, filename=str(self.root / "input.tfx"))
        payload = json.loads(
            serialize_source_map(
                result,
                source_path=self.root / "input.tfx",
                generated_path=self.root / "out.tex",
                map_path=self.root / "out.tex.tfxmap",
                source_bytes=source.encode("utf-8"),
            )
        )

        previous_end = None
        for mapping in payload["mappings"]:
            generated = mapping["generated"]
            start = (generated["start"]["line"], generated["start"]["column"])
            end = (generated["end"]["line"], generated["end"]["column"])
            self.assertLess(start, end)
            if previous_end is not None:
                self.assertLessEqual(previous_end, start)
            previous_end = end

        blank_mapping = next(
            mapping
            for mapping in payload["mappings"]
            if mapping["source"]["start"] == mapping["source"]["end"]
        )
        self.assertEqual(blank_mapping["source"]["start"]["line"], 2)

    def test_relative_paths_are_computed_from_the_map_directory(self):
        map_dir = self.root / "maps"
        source_path = self.root / "sources" / "input.tfx"
        result = compile_with_map("raw\n", filename=str(source_path))
        payload = json.loads(
            serialize_source_map(
                result,
                source_path=source_path,
                generated_path=self.root / "tex" / "out.tex",
                map_path=map_dir / "out.tex.tfxmap",
                source_bytes=b"raw\n",
            )
        )

        self.assertEqual(payload["generated"]["path"], "../tex/out.tex")
        self.assertEqual(payload["sources"][0]["path"], "../sources/input.tfx")

    def test_serializer_rejects_mismatched_source_or_generated_bytes(self):
        source_path = self.root / "input.tfx"
        result = compile_with_map("raw\n", filename=str(source_path))
        kwargs = {
            "source_path": source_path,
            "generated_path": self.root / "out.tex",
            "map_path": self.root / "out.tex.tfxmap",
            "source_bytes": b"raw\n",
        }
        with self.assertRaisesRegex(ValueError, "generated_bytes"):
            serialize_source_map(
                result,
                **kwargs,
                generated_bytes=b"different\n",
            )
        with self.assertRaisesRegex(ValueError, "source span file"):
            serialize_source_map(
                result,
                **(kwargs | {"source_path": self.root / "other.tfx"}),
            )

    def test_serializer_rejects_invalid_generated_ranges(self):
        span = SourceSpan(
            "input.tfx",
            SourcePosition(1, 1),
            SourcePosition(1, 2),
        )
        invalid_results = (
            CompilationResult(
                "A",
                RenderedDocument(
                    "A",
                    (
                        RenderedFragment(
                            "A",
                            GeneratedSpan(SourcePosition(1, 1), SourcePosition(1, 1)),
                            span,
                            "content",
                        ),
                    ),
                ),
            ),
            CompilationResult(
                "AB",
                RenderedDocument(
                    "AB",
                    (
                        RenderedFragment(
                            "A",
                            GeneratedSpan(SourcePosition(1, 1), SourcePosition(1, 3)),
                            span,
                            "content",
                        ),
                        RenderedFragment(
                            "B",
                            GeneratedSpan(SourcePosition(1, 2), SourcePosition(1, 3)),
                            span,
                            "content",
                        ),
                    ),
                ),
            ),
        )
        for result in invalid_results:
            with self.subTest(result=result):
                with self.assertRaisesRegex(ValueError, "generated fragments"):
                    serialize_source_map(
                        result,
                        source_path="input.tfx",
                        generated_path="out.tex",
                        map_path="out.tex.tfxmap",
                        source_bytes=b"input",
                    )

    def test_representative_constructs_produce_source_mappings(self):
        sources = (
            "raw\n",
            "\\foo: |\n    @{}: |\n        body\n",
            "@frame >> @center: |\n    body\n",
            "!items:\n    - item\n",
            "!vpad{1em}{2em}: |\n    body\n",
        )
        for index, source in enumerate(sources):
            with self.subTest(index=index):
                input_path = self.root / f"input{index}.tfx"
                result = compile_with_map(source, filename=str(input_path))
                payload = json.loads(
                    serialize_source_map(
                        result,
                        source_path=input_path,
                        generated_path=self.root / f"out{index}.tex",
                        map_path=self.root / f"out{index}.tex.tfxmap",
                        source_bytes=source.encode("utf-8"),
                    )
                )
                self.assertGreater(len(payload["mappings"]), 0)

    def test_representative_constructs_preserve_exact_source_spans(self):
        cases = (
            (
                "\\foo: |\n    @{}: |\n        body\n",
                [
                    ("open", (1, 1, 1, 8)),
                    ("open", (1, 5, 1, 8)),
                    ("open", (2, 5, 2, 11)),
                    ("content", (3, 9, 3, 13)),
                    ("close", (2, 5, 2, 11)),
                    ("close", (1, 5, 1, 8)),
                ],
            ),
            (
                "\\foo:\n    - A\n      continuation\n    - B\n",
                [
                    ("open", (1, 1, 1, 6)),
                    ("open", (2, 5, 3, 19)),
                    ("content", (2, 7, 2, 8)),
                    ("content", (3, 7, 3, 19)),
                    ("close", (2, 5, 3, 19)),
                    ("open", (4, 5, 4, 8)),
                    ("content", (4, 7, 4, 8)),
                    ("close", (4, 5, 4, 8)),
                ],
            ),
            (
                "@frame >> @center: |\n    body\n",
                [
                    ("open", (1, 1, 1, 7)),
                    ("open", (1, 11, 1, 18)),
                    ("content", (2, 5, 2, 9)),
                    ("close", (1, 11, 1, 18)),
                    ("close", (1, 1, 1, 7)),
                ],
            ),
            (
                "!items:\n    - item\n",
                [
                    ("open", (1, 1, 1, 8)),
                    ("open", (2, 5, 2, 11)),
                    ("content", (2, 5, 2, 11)),
                    ("close", (1, 1, 1, 8)),
                ],
            ),
            (
                "!vpad{1em}{2em}: |\n    body\n",
                [
                    ("open", (1, 6, 1, 11)),
                    ("content", (1, 6, 1, 11)),
                    ("close", (1, 6, 1, 11)),
                    ("content", (2, 5, 2, 9)),
                    ("open", (1, 11, 1, 16)),
                    ("content", (1, 11, 1, 16)),
                    ("close", (1, 11, 1, 16)),
                ],
            ),
        )

        for index, (source, expected) in enumerate(cases):
            with self.subTest(index=index):
                input_path = self.root / f"input{index}.tfx"
                result = compile_with_map(source, filename=str(input_path))
                payload = json.loads(
                    serialize_source_map(
                        result,
                        source_path=input_path,
                        generated_path=self.root / f"out{index}.tex",
                        map_path=self.root / f"out{index}.tex.tfxmap",
                        source_bytes=source.encode("utf-8"),
                    )
                )
                actual = []
                for mapping in payload["mappings"]:
                    source_span = mapping["source"]
                    actual.append(
                        (
                            mapping["role"],
                            (
                                source_span["start"]["line"],
                                source_span["start"]["column"],
                                source_span["end"]["line"],
                                source_span["end"]["column"],
                            ),
                        )
                )
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
