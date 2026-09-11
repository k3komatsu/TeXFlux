import unittest

from texflux.ast import (
    Block,
    InvocationKind,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SpecialInvocation,
    Stack,
    SuiteMode,
)
from texflux.errors import ParseError
from texflux.parser import parse


class ParserTests(unittest.TestCase):
    def test_prefixes_and_suite_modes_are_lexical(self):
        document = parse(
            "\\foo{A}\n"
            "@foo{A}: |\n"
            "    BODY\n"
            "!items:\n"
            "    - A\n",
            "x.tfx",
        )
        raw, environment, special = document.body.nodes
        self.assertIsInstance(raw, RawTex)
        self.assertIsInstance(environment, ParsedInvocation)
        self.assertEqual(environment.kind, InvocationKind.ENVIRONMENT)
        self.assertEqual(environment.suite_mode, SuiteMode.BLOCK)
        self.assertIsInstance(special, SpecialInvocation)
        self.assertEqual(special.suite_mode, SuiteMode.SEQUENCE)

    def test_sequence_entries_keep_marker_spans(self):
        node = parse(
            "\\foo:\n"
            "    - A\n"
            "      continuation\n"
            "    - B\n",
            "x.tfx",
        ).body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.suite_mode, SuiteMode.SEQUENCE)
        first, second = node.suite.nodes
        self.assertIsInstance(first, SequenceEntry)
        self.assertIsInstance(second, SequenceEntry)
        self.assertIsInstance(first.value, Block)
        self.assertEqual(first.marker_span.start.column, 5)
        self.assertEqual(second.marker_span.start.line, 4)
        self.assertEqual(
            [child.text for child in first.value.nodes],
            ["A", "continuation"],
        )
        self.assertEqual(second.value.nodes[0].text, "B")

    def test_block_suite_is_an_ordinary_structural_block(self):
        node = parse("\\foo: |\n    A\n    @bar: |\n        B\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.suite_mode, SuiteMode.BLOCK)
        self.assertIsInstance(node.suite.nodes[0], RawTex)
        self.assertIsInstance(node.suite.nodes[1], ParsedInvocation)

    def test_anonymous_containers_have_distinct_kinds(self):
        brace, transparent = parse(
            "@{\\small}: |\n    A\n@: |\n    B\n",
            "x.tfx",
        ).body.nodes
        self.assertEqual(brace.kind, InvocationKind.BRACE)
        self.assertEqual(brace.groups[0].value, "\\small")
        self.assertEqual(transparent.kind, InvocationKind.TRANSPARENT)

    def test_closed_and_open_stacks_are_preserved(self):
        closed, opened = parse(
            "@center >> \\includegraphics{fig.pdf}\n"
            "@center >> \\foo: |\n"
            "    BODY\n",
            "x.tfx",
        ).body.nodes
        self.assertIsInstance(closed, Stack)
        self.assertIsNone(closed.suite)
        self.assertIsNone(closed.suite_mode)
        self.assertIsInstance(opened, Stack)
        self.assertEqual(opened.suite_mode, SuiteMode.BLOCK)

    def test_group_contents_do_not_create_structural_tokens(self):
        first = parse("\\foo{A >> B}: |\n    A\n", "x.tfx").body.nodes[0]
        second = parse("\\foo{\\texttt{A: B}}:\n    - C\n", "x.tfx").body.nodes[0]
        self.assertEqual(first.groups[0].value, "A >> B")
        self.assertEqual(second.groups[0].value, "\\texttt{A: B}")

    def test_ordinary_commands_remain_raw(self):
        block = parse("@foo: |\n    @@literal\n", "x.tfx").body.nodes[0]
        sequence = parse("\\foo:\n    - @@literal\n", "x.tfx").body.nodes[0]
        self.assertEqual(block.suite.nodes[0].text, "@literal")
        self.assertEqual(sequence.suite.nodes[0].value.nodes[0].text, "@literal")

        document = parse(
            "\\foo{A}\n"
            "\\verb|a >> b|\n"
            "\\includegraphics[width=.8\\textwidth]{fig.pdf}\n",
            "x.tfx",
        )
        self.assertEqual([node.text for node in document.body.nodes], [
            "\\foo{A}",
            "\\verb|a >> b|",
            "\\includegraphics[width=.8\\textwidth]{fig.pdf}",
        ])

    def test_plain_sequence_rejects_unmarked_children(self):
        with self.assertRaisesRegex(ParseError, "sequence suites"):
            parse("\\foo:\n    A\n", "x.tfx")

    def test_sequence_suites_require_entries_and_alignment(self):
        cases = (
            ("\\foo:\n", "1:1"),
            ("\\foo:\n  - A\n", "2:3"),
            ("\\foo:\n      - A\n", "2:7"),
            ("\\foo:\n    - A\n      continuation\n     bad\n", "4:6"),
            ("\\foo:\n    - \\bar:\n", "2:7"),
            ("\\foo:\n    - \\bar:\n      - A\n", "3:7"),
        )
        for source, location in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError,
                    rf"x\.tfx:{location}: parse error: sequence",
                ):
                    parse(source, "x.tfx")

    def test_blank_lines_are_preserved_only_in_block_values(self):
        sequence = parse("\\foo:\n    - A\n\n    - B\n", "x.tfx").body.nodes[0]
        block = parse("\\foo: |\n    A\n\n    B\n", "x.tfx").body.nodes[0]
        self.assertEqual(len(sequence.suite.nodes), 2)
        self.assertEqual(
            [child.text for child in sequence.suite.nodes[0].value.nodes],
            ["A"],
        )
        self.assertEqual(
            [child.text for child in sequence.suite.nodes[1].value.nodes],
            ["B"],
        )
        self.assertEqual([node.text for node in block.suite.nodes], ["A", "", "B"])

    def test_starred_environment_and_unicode_positions(self):
        node = parse("日本語\n@align*: |\n    x &= y\n", "x.tfx").body.nodes[1]
        self.assertEqual(node.name, "align*")
        self.assertEqual(node.span.start.line, 2)
        self.assertEqual(node.span.start.column, 1)

    def test_stack_segments_require_prefixes(self):
        with self.assertRaisesRegex(ParseError, "each stack segment"):
            parse("\\foo >> bar: |\n    BODY\n", "x.tfx")

    def test_top_level_pipe_marker_and_trailing_tokens(self):
        self.assertEqual(
            parse("\\foo:|\n    BODY\n", "x.tfx").body.nodes[0].suite_mode,
            SuiteMode.BLOCK,
        )
        raw = parse("\\textbf{Note}: see below\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(raw, RawTex)
        self.assertEqual(raw.text, "\\textbf{Note}: see below")
        for source in ("\\foo: | extra\n", "\\foo: |-\n"):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.tfx")

    def test_empty_block_suites_are_allowed(self):
        self.assertEqual(
            parse("@: |\n", "x.tfx").body.nodes[0].suite.nodes,
            (),
        )

    def test_indentation_and_tabs_keep_diagnostics(self):
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:7: parse error"):
            parse("@foo: |\n      @bar: |\n        BODY\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:3: parse error"):
            parse("ok\n  \tbad\n", "x.tfx")


if __name__ == "__main__":
    unittest.main()
