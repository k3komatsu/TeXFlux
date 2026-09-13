import unittest

from texflux import (
    CompilationResult,
    GeneratedSpan,
    SourcePosition,
    SourceSpan,
    compile_with_map,
    render,
)
from texflux.normalize import normalize
from texflux.parser import parse
from texflux.render import MappedEmitter


class SpanTests(unittest.TestCase):
    def test_parser_nodes_use_half_open_source_spans(self):
        document = parse("@center:\n    BODY\n", "input.tfx")
        invocation = document.body.nodes[0]

        self.assertEqual(
            invocation.span,
            SourceSpan(
                "input.tfx",
                SourcePosition(1, 1),
                SourcePosition(1, 9),
            ),
        )
        body = invocation.suite.nodes[0]
        self.assertEqual(
            body.span,
            SourceSpan(
                "input.tfx",
                SourcePosition(2, 5),
                SourcePosition(2, 9),
            ),
        )

    def test_compile_with_map_returns_mapped_rendering(self):
        result = compile_with_map("@center:\n    BODY\n", filename="input.tfx")

        self.assertIsInstance(result, CompilationResult)
        self.assertEqual(
            result.text,
            "\\begin{center}\nBODY\n\\end{center}\n",
        )
        body_fragment = next(
            fragment for fragment in result.rendered.fragments if fragment.text == "BODY"
        )
        self.assertEqual(
            body_fragment.generated,
            GeneratedSpan(SourcePosition(2, 1), SourcePosition(2, 5)),
        )
        self.assertEqual(
            body_fragment.source,
            SourceSpan(
                "input.tfx",
                SourcePosition(2, 5),
                SourcePosition(2, 9),
            ),
        )
        self.assertEqual(body_fragment.role, "content")

    def test_fragments_tile_generated_output_and_preserve_roles(self):
        rendered = compile_with_map(
            "@center:\n    BODY\n",
            filename="input.tfx",
        ).rendered

        self.assertEqual("".join(fragment.text for fragment in rendered.fragments), rendered.text)
        cursor = SourcePosition(1, 1)
        for fragment in rendered.fragments:
            self.assertEqual(fragment.generated.start, cursor)
            self.assertLess(fragment.generated.start, fragment.generated.end)
            cursor = fragment.generated.end
        self.assertEqual(set(fragment.role for fragment in rendered.fragments), {
            "content",
            "open",
            "close",
            "synthetic",
        })

    def test_emitter_coalesces_adjacent_equal_source_and_role(self):
        span = SourceSpan(
            "input.tfx",
            SourcePosition(1, 1),
            SourcePosition(1, 3),
        )
        emitter = MappedEmitter()
        emitter.emit("A", source=span, role="content")
        emitter.emit("B", source=span, role="content")

        rendered = emitter.finish()
        self.assertEqual(rendered.text, "AB")
        self.assertEqual(len(rendered.fragments), 1)
        self.assertEqual(rendered.fragments[0].text, "AB")
        self.assertEqual(
            rendered.fragments[0].generated,
            GeneratedSpan(SourcePosition(1, 1), SourcePosition(1, 3)),
        )

    def test_blank_physical_lines_keep_source_provenance(self):
        rendered = compile_with_map("A\n\nB\n", filename="input.tfx").rendered
        blank = next(
            fragment
            for fragment in rendered.fragments
            if fragment.text == "\n" and fragment.source is not None
        )

        self.assertEqual(
            blank.source,
            SourceSpan(
                "input.tfx",
                SourcePosition(2, 1),
                SourcePosition(2, 1),
            ),
        )
        self.assertEqual(blank.role, "content")

    def test_source_comments_are_synthetic_and_mapped_separately(self):
        rendered = compile_with_map(
            "raw\n",
            filename="input.tfx",
            source_comments=True,
        ).rendered

        self.assertEqual(rendered.text, "% texflux: input.tfx:1\nraw\n")
        synthetic = [
            fragment for fragment in rendered.fragments if fragment.role == "synthetic"
        ]
        self.assertEqual(len(synthetic), 2)
        self.assertEqual(synthetic[0].text, "% texflux: input.tfx:1\n")
        self.assertTrue(all(fragment.source is None for fragment in synthetic))

    def test_render_is_the_same_canonical_renderer_used_by_compile(self):
        document = parse("@center:\n    BODY\n", "input.tfx")
        canonical = normalize(document)
        self.assertEqual(render(canonical), compile_with_map("@center:\n    BODY\n").text)

    def test_retracting_a_newline_leaves_the_cursor_after_the_kept_text(self):
        # A coalesced fragment keeps the text before the newline, so the
        # cursor must not rewind to the start of the whole run.
        span = SourceSpan("input.tfx", SourcePosition(1, 1), SourcePosition(1, 2))
        emitter = MappedEmitter()
        emitter.emit("abc", source=span, role="content")
        emitter.emit("\n", source=span, role="content")
        emitter.drop_trailing_newline()

        self.assertEqual(emitter.position, SourcePosition(1, 4))

    def test_a_value_ending_in_blank_lines_keeps_a_sorted_map(self):
        source = (
            "!defmacro{nothing}:\n"
            "!defmacro{m}:\n"
            "    plain\n\n\n"
            "    !nothing\n"
            "\\cmd::\n"
            "    - !m\n"
        )
        result = compile_with_map(source, filename="input.tfx")

        self.assertEqual(result.text, "\\cmd{plain\n\n}\n")
        previous = None
        for fragment in result.rendered.fragments:
            self.assertLess(fragment.generated.start, fragment.generated.end)
            if previous is not None:
                self.assertGreaterEqual(fragment.generated.start, previous)
            previous = fragment.generated.end
        self.assertEqual(
            "".join(fragment.text for fragment in result.rendered.fragments),
            result.text,
        )

    def test_renderer_rejects_syntax_only_nodes(self):
        with self.assertRaises(TypeError):
            render(parse("\\foo:\n    BODY\n", "input.tfx"))

    def test_unicode_source_columns_are_half_open(self):
        document = parse("日本語\n", "input.tfx")
        raw = document.body.nodes[0]

        self.assertEqual(
            raw.span,
            SourceSpan(
                "input.tfx",
                SourcePosition(1, 1),
                SourcePosition(1, 4),
            ),
        )


if __name__ == "__main__":
    unittest.main()
