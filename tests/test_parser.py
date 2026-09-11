import unittest

from texflux.ast import (
    ArgumentLayout,
    GroupKind,
    InvocationKind,
    ParsedInvocation,
    RawTex,
    SpecialInvocation,
    Stack,
)
from texflux.errors import ParseError
from texflux.parser import parse


class ParserTests(unittest.TestCase):
    def test_prefixes_classify_commands_environments_and_specials(self):
        document = parse(
            "\\foo{A}\n"
            "@foo{A}:\n"
            "    BODY\n"
            "!items:\n"
            "    - A\n",
            "x.tfx",
        )
        raw, environment, special = document.body.nodes
        self.assertIsInstance(raw, RawTex)
        self.assertEqual(raw.text, "\\foo{A}")
        self.assertIsInstance(environment, ParsedInvocation)
        self.assertEqual(environment.kind, InvocationKind.ENVIRONMENT)
        self.assertEqual(environment.name, "foo")
        self.assertIsInstance(special, SpecialInvocation)
        self.assertEqual(special.name, "items")

    def test_raw_and_literal_at_lines(self):
        document = parse(
            "  raw\n@@directive\ninside @not-a-directive\n",
            "x.tfx",
        )
        self.assertEqual(
            document.body.nodes,
            (
                RawTex("  raw", document.body.nodes[0].span),
                RawTex("@directive", document.body.nodes[1].span),
                RawTex(
                    "inside @not-a-directive",
                    document.body.nodes[2].span,
                ),
            ),
        )

    def test_ordinary_tex_commands_are_not_scanned(self):
        document = parse(
            "\\foo{A}\n"
            "\\verb|a >> b|\n"
            "\\includegraphics[width=.8\\textwidth]{fig.pdf}\n",
            "x.tfx",
        )
        self.assertEqual(
            [node.text for node in document.body.nodes],
            [
                "\\foo{A}",
                "\\verb|a >> b|",
                "\\includegraphics[width=.8\\textwidth]{fig.pdf}",
            ],
        )

    def test_non_space_separated_stack_marker_is_ordinary_raw_tex(self):
        for source in (
            "\\alpha>>\\beta\n",
            "\\alpha >>\\beta\n",
            "\\alpha>> \\beta\n",
        ):
            with self.subTest(source=source):
                document = parse(source, "x.tfx")
                self.assertEqual(
                    document.body.nodes,
                    (
                        RawTex(
                            source.rstrip("\n"),
                            document.body.nodes[0].span,
                        ),
                    ),
                )

    def test_groups_are_scanned_without_parsing_tex(self):
        document = parse(
            r"\foo{a{b}c}[x{y]z}]<2->:"
            "\n    BODY\n",
            "x.tfx",
        )
        node = document.body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.kind, InvocationKind.COMMAND)
        self.assertEqual(
            [group.kind for group in node.groups],
            [
                GroupKind.REQUIRED,
                GroupKind.OPTIONAL,
                GroupKind.OVERLAY,
            ],
        )
        self.assertEqual(node.groups[1].value, "x{y]z}")
        self.assertIsNotNone(node.suite)

    def test_group_contents_do_not_create_structural_tokens(self):
        first = parse(
            r"\foo{A >> B}:"
            "\n    \\bar\n",
            "x.tfx",
        ).body.nodes[0]
        second = parse(
            r"\foo{\texttt{A: B}}:"
            "\n    \\bar\n",
            "x.tfx",
        ).body.nodes[0]
        self.assertIsInstance(first, ParsedInvocation)
        self.assertEqual(first.groups[0].value, "A >> B")
        self.assertIsInstance(second, ParsedInvocation)
        self.assertEqual(second.groups[0].value, r"\texttt{A: B}")

    def test_stack_requires_prefixed_segments(self):
        document = parse(
            "\\foo{x} >> @bar[y] >> !items:\n"
            "    - A\n",
            "x.tfx",
        )
        node = document.body.nodes[0]
        self.assertIsInstance(node, Stack)
        self.assertEqual(node.segments[0].kind, InvocationKind.COMMAND)
        self.assertEqual(node.segments[1].kind, InvocationKind.ENVIRONMENT)
        self.assertIsInstance(node.segments[2], SpecialInvocation)
        self.assertEqual(node.segments[2].name, "items")

    def test_starred_environment_names_are_scanned(self):
        node = parse("@align*:\n    x &= y\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.name, "align*")
        self.assertEqual(node.kind, InvocationKind.ENVIRONMENT)

    def test_structural_indentation_is_removed_but_extra_raw_indent_stays(self):
        document = parse(
            "@foo:\n"
            "    @bar:\n"
            "        BODY\n"
            "            indented\n",
            "x.tfx",
        )
        outer = document.body.nodes[0]
        inner = outer.suite.nodes[0]
        self.assertEqual(inner.suite.nodes[0].text, "BODY")
        self.assertEqual(inner.suite.nodes[1].text, "    indented")

    def test_invalid_directive_indentation_and_missing_colon_have_locations(self):
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:7: parse error"):
            parse("@foo:\n      @bar\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:5: parse error"):
            parse("root\n    \\foo:\n        BODY\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, "environment directives"):
            parse("@foo{A}\n", "x.tfx")

    def test_top_level_trailing_colon_reserves_structural_syntax(self):
        node = parse(
            "\\textbf{注意}:\n"
            "    本文\n",
            "x.tfx",
        ).body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.kind, InvocationKind.COMMAND)
        with self.assertRaisesRegex(ParseError, "indented suite"):
            parse("\\textbf{注意}:\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, "trailing token"):
            parse("\\textbf{注意}: 本文\n", "x.tfx")
        spaced = parse("\\foo :\n    BODY\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(spaced, ParsedInvocation)
        self.assertIsNotNone(spaced.suite)
        with self.assertRaises(ParseError):
            parse("\\verb|x|:\n    BODY\n", "x.tfx")

    def test_failed_group_scan_without_structure_stays_raw(self):
        document = parse("\\centering{\n", "x.tfx")
        self.assertEqual(document.body.nodes[0].text, "\\centering{")

    def test_tabs_are_rejected_at_first_tab(self):
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:3: parse error"):
            parse("ok\n  \tbad\n", "x.tfx")

    def test_crlf_is_normalized_and_columns_count_unicode_characters(self):
        document = parse("日本語\r\n\\foo{A}:\r\n    BODY\r\n", "x.tfx")
        raw, invocation = document.body.nodes
        self.assertEqual(raw.text, "日本語")
        self.assertEqual(invocation.span.start.line, 2)
        self.assertEqual(invocation.groups[0].span.start.column, 5)
        cr_only = parse("first\rsecond\r", "x.tfx")
        self.assertEqual(
            [line.text for line in cr_only.body.nodes],
            ["first", "second"],
        )

    def test_unclosed_and_invalid_headers_fail(self):
        for source in (
            "@foo{A\n",
            "@foo[x}\n",
            "@foo {A}\n",
            "!items |\n",
            "\\foo: comment\n",
            "\\section*{Title}:\n    BODY\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.tfx")

    def test_inline_and_block_explicit_nodes_are_parsed(self):
        document = parse(
            "@foo:\n"
            "    !arg{INLINE}\n"
            "    !arg:\n"
            "        BLOCK\n",
            "x.tfx",
        )
        invocation = document.body.nodes[0]
        inline, block = invocation.suite.nodes
        self.assertIsInstance(inline, SpecialInvocation)
        self.assertEqual(inline.groups[0].layout, ArgumentLayout.INLINE)
        self.assertEqual(block.suite.nodes[0].text, "BLOCK")

    def test_command_suite_is_kept_as_a_suite(self):
        document = parse(
            "\\foo{HEADER}:\n"
            "    @bar:\n"
            "        BODY\n",
            "x.tfx",
        )
        invocation = document.body.nodes[0]
        self.assertIsInstance(invocation, ParsedInvocation)
        self.assertEqual(invocation.groups[0].value, "HEADER")
        nested = invocation.suite.nodes[0]
        self.assertIsInstance(nested, ParsedInvocation)
        self.assertEqual(nested.name, "bar")

    def test_suite_rejects_trailing_tokens_after_colon(self):
        invalid = (
            "@foo: |\n    BODY\n",
            "!block: |\n    BODY\n",
            "!items: |\n    - ITEM\n",
            "\\foo >> @bar: |\n    BODY\n",
            "\\foo:|-\n    BODY\n",
            "\\foo{A}: |\n    BODY\n",
        )
        for source in invalid:
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.tfx")
        with self.assertRaisesRegex(ParseError, "indented suite"):
            parse("\\foo:\n", "x.tfx")

    def test_stack_requires_a_suite_and_prefixes(self):
        with self.assertRaisesRegex(ParseError, "suite marker"):
            parse("\\foo >> @bar\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, "each stack segment"):
            parse("\\foo >> bar:\n    BODY\n", "x.tfx")


if __name__ == "__main__":
    unittest.main()
