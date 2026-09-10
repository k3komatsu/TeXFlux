import unittest

from beamercraft.ast import (
    ArgumentLayout,
    GenericInvocation,
    GroupKind,
    ParsedGeneric,
    RawTex,
    SpecialInvocation,
    Stack,
)
from beamercraft.errors import ParseError
from beamercraft.parser import parse


class ParserTests(unittest.TestCase):
    def test_raw_and_literal_at_lines(self):
        document = parse("  raw\n@@directive\ninside @not-a-directive\n", "x.bmc")
        self.assertEqual(
            document.body.nodes,
            (
                RawTex("  raw", document.body.nodes[0].loc),
                RawTex("@directive", document.body.nodes[1].loc),
                RawTex("inside @not-a-directive", document.body.nodes[2].loc),
            ),
        )

    def test_groups_are_scanned_without_parsing_tex(self):
        document = parse(
            r"@foo{a{b}c}[x{y]z}]<2->:"
            "\n    BODY\n",
            "x.bmc",
        )
        node = document.body.nodes[0]
        self.assertIsInstance(node, ParsedGeneric)
        self.assertEqual([group.kind for group in node.groups], [
            GroupKind.REQUIRED,
            GroupKind.OPTIONAL,
            GroupKind.OVERLAY,
        ])
        self.assertEqual(node.groups[1].value, "x{y]z}")
        self.assertIsNotNone(node.suite)

    def test_stack_and_special_segments(self):
        document = parse("@A{x} >> B[y] >> !items:\n    - A\n", "x.bmc")
        node = document.body.nodes[0]
        self.assertIsInstance(node, Stack)
        self.assertEqual(node.segments[0].name, "A")
        self.assertEqual(node.segments[1].name, "B")
        self.assertIsInstance(node.segments[2], SpecialInvocation)
        self.assertEqual(node.segments[2].name, "items")

    def test_structural_indentation_is_removed_but_extra_raw_indent_stays(self):
        document = parse("@foo:\n    @bar:\n        BODY\n            indented\n", "x.bmc")
        outer = document.body.nodes[0]
        inner = outer.suite.nodes[0]
        self.assertEqual(inner.suite.nodes[0].text, "BODY")
        self.assertEqual(inner.suite.nodes[1].text, "    indented")

    def test_invalid_directive_indentation_and_missing_colon_have_locations(self):
        with self.assertRaisesRegex(ParseError, r"x\.bmc:2:7: parse error"):
            parse("@foo:\n      @bar\n", "x.bmc")
        with self.assertRaisesRegex(ParseError, r"x\.bmc:2:5: parse error"):
            parse("@foo\n    BODY\n", "x.bmc")

    def test_partially_indented_raw_tex_after_leaf_directive_is_preserved(self):
        document = parse("@vspace{-1em}\n  \\textbf{x}\n", "x.bmc")
        self.assertEqual(document.body.nodes[1].text, "  \\textbf{x}")

    def test_tabs_are_rejected_at_first_tab(self):
        with self.assertRaisesRegex(ParseError, r"x\.bmc:2:3: parse error"):
            parse("ok\n  \tbad\n", "x.bmc")

    def test_crlf_is_normalized_and_columns_count_unicode_characters(self):
        document = parse("日本語\r\n@foo{A}\r\n", "x.bmc")
        raw, invocation = document.body.nodes
        self.assertEqual(raw.text, "日本語")
        self.assertEqual(invocation.loc.line, 2)
        self.assertEqual(invocation.groups[0].loc.column, 5)

    def test_unclosed_and_invalid_headers_fail(self):
        for source in ("@foo{A\n", "@foo[x}\n", "@foo**\n", "@foo {A}\n", "@foo１\n"):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.bmc")

    def test_inline_and_block_long_argument_nodes_are_parsed(self):
        document = parse(
            "@foo:\n"
            "    @!arg{INLINE}\n"
            "    @!arg:\n"
            "        BLOCK\n",
            "x.bmc",
        )
        invocation = document.body.nodes[0]
        inline, block = invocation.suite.nodes
        self.assertIsInstance(inline, SpecialInvocation)
        self.assertEqual(inline.groups[0].layout, ArgumentLayout.INLINE)
        self.assertEqual(block.suite.nodes[0].text, "BLOCK")


if __name__ == "__main__":
    unittest.main()
