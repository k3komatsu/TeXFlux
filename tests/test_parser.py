import unittest

from texflux.ast import (
    Block,
    GroupKind,
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
    def test_multiline_stack_keeps_physical_spans(self):
        node = parse("@a >>  \r\n@b{B} >>\r\n@c:\r\n    X\r\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, Stack)
        self.assertEqual(
            [(s.span.start.line, s.span.start.column) for s in node.segments],
            [(1, 1), (2, 1), (3, 1)],
        )
        self.assertEqual(node.segments[1].groups[0].span.start.line, 2)
        self.assertEqual(node.suite_span.start.line, 3)
        self.assertEqual(node.suite_span.start.column, 3)
        self.assertEqual(node.span.end.line, 3)
        self.assertEqual(node.suite.nodes[0].span.start.line, 4)

    def test_multiline_stack_requires_immediate_aligned_segment(self):
        for source, location in (
            ("@a >>\n", "1:4"),
            ("\\a >>\n", "1:4"),
            ("@a >>\n\n@b:\n", "2:1"),
            ("@a >>\n    @b:\n", "2:5"),
            ("@a:\n    @b >>\n@c:\n", "3:1"),
            ("@a >>\nraw\n", "2:1"),
            ("@a >>\n\\verb|raw|\n", "2:6"),
            ("@a >>\n@b: extra\n", "2:5"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ParseError, rf"x\.tfx:{location}: parse error"):
                    parse(source, "x.tfx")

    def test_missing_suite_diagnostics_name_both_markers(self):
        with self.assertRaisesRegex(
            ParseError,
            "environment directives require a suite marker ':' or '::'",
        ):
            parse("@foo\n", "x.tfx")
        with self.assertRaisesRegex(
            ParseError,
            "indented lines require a suite marker ':' or '::'",
        ):
            parse("!foo\n    BODY\n", "x.tfx")

    def test_raw_tex_does_not_continue_on_trailing_arrows(self):
        source = "raw >>\n\\foo{A >>}\n\\verb|x| >>\n@@literal >>\n!!literal >>\n"
        self.assertTrue(all(isinstance(n, RawTex) for n in parse(source).body.nodes))
        entries = parse("\\foo::\n    - literal >>\n    - next\n").body.nodes[0]
        self.assertEqual(len(entries.suite.nodes), 2)

    def test_prefixes_and_suite_modes_are_lexical(self):
        document = parse(
            "\\foo{A}\n"
            "@foo{A}:\n"
            "    BODY\n"
            "!foo::\n"
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

    def test_colon_is_block_and_double_colon_is_sequence(self):
        block = parse("\\foo:\n    BODY\n", "x.tfx").body.nodes[0]
        sequence = parse("\\foo::\n    - ITEM\n", "x.tfx").body.nodes[0]
        spaced_block = parse("\\foo :   \n    BODY\n", "x.tfx").body.nodes[0]
        spaced_sequence = parse("\\foo::   \n    - ITEM\n", "x.tfx").body.nodes[0]

        self.assertEqual(block.suite_mode, SuiteMode.BLOCK)
        self.assertEqual(sequence.suite_mode, SuiteMode.SEQUENCE)
        self.assertEqual(spaced_block.suite_mode, SuiteMode.BLOCK)
        self.assertEqual(spaced_sequence.suite_mode, SuiteMode.SEQUENCE)
        self.assertEqual(sequence.suite_span.start.column, 5)
        self.assertEqual(sequence.suite_span.end.column, 7)
        for source in ("\\foo:|\n    BODY\n", "\\foo: |\n    BODY\n"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ParseError, r"x\.tfx:1:"):
                    parse(source, "x.tfx")

    def test_pipe_is_not_a_suite_marker_for_structural_prefixes(self):
        for source in (
            "\\foo:|\n    BODY\n",
            "@foo:|\n    BODY\n",
            "@:|\n    BODY\n",
            "!foo:|\n    BODY\n",
            "@a >> @b: |\n    BODY\n",
            "\\foo::\n    - \\bar: |\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.tfx")

    def test_sequence_entries_keep_marker_spans(self):
        node = parse(
            "\\foo::\n"
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
        node = parse("\\foo:\n    A\n    @bar:\n        B\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.suite_mode, SuiteMode.BLOCK)
        self.assertIsInstance(node.suite.nodes[0], RawTex)
        self.assertIsInstance(node.suite.nodes[1], ParsedInvocation)

    def test_anonymous_containers_have_distinct_kinds(self):
        brace, transparent = parse(
            "@{\\small}:\n    A\n@:\n    B\n",
            "x.tfx",
        ).body.nodes
        self.assertEqual(brace.kind, InvocationKind.BRACE)
        self.assertEqual(brace.groups[0].value, "\\small")
        self.assertEqual(transparent.kind, InvocationKind.TRANSPARENT)

    def test_closed_and_open_stacks_are_preserved(self):
        closed, opened = parse(
            "@center >> \\includegraphics{fig.pdf}\n"
            "@center >> \\foo:\n"
            "    BODY\n",
            "x.tfx",
        ).body.nodes
        self.assertIsInstance(closed, Stack)
        self.assertIsNone(closed.suite)
        self.assertIsNone(closed.suite_mode)
        self.assertIsInstance(opened, Stack)
        self.assertEqual(opened.suite_mode, SuiteMode.BLOCK)

    def test_group_contents_do_not_create_structural_tokens(self):
        first = parse("\\foo{A >> B}:\n    A\n", "x.tfx").body.nodes[0]
        second = parse("\\foo{\\texttt{A: B}}::\n    - C\n", "x.tfx").body.nodes[0]
        self.assertEqual(first.groups[0].value, "A >> B")
        self.assertEqual(second.groups[0].value, "\\texttt{A: B}")

    def test_ordinary_commands_remain_raw(self):
        for prefix in ("@", "!"):
            with self.subTest(prefix=prefix):
                block = parse(f"@foo:\n    {prefix * 2}literal\n", "x.tfx").body.nodes[0]
                sequence = parse(f"\\foo::\n    - {prefix * 2}literal\n", "x.tfx").body.nodes[0]
                self.assertEqual(block.suite.nodes[0].text, prefix + "literal")
                self.assertEqual(sequence.suite.nodes[0].value.nodes[0].text, prefix + "literal")

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

    def test_raw_line_escapes_preserve_extra_indentation_and_spans(self):
        for prefix in ("@", "!"):
            with self.subTest(prefix=prefix):
                node = parse(
                    f"@lstlisting:\n       {prefix * 2}literal {{ >>:\n",
                    "escape.tfx",
                ).body.nodes[0].suite.nodes[0]
                self.assertIsInstance(node, RawTex)
                self.assertEqual(node.text, f"   {prefix}literal {{ >>:")
                self.assertEqual(node.span.file, "escape.tfx")
                self.assertEqual((node.span.start.line, node.span.start.column), (2, 5))

    def test_raw_line_escapes_strip_exactly_one_prefix(self):
        for prefix in ("@", "!"):
            for tail in ("", prefix + "foo", "重要", "literal >>", "literal {:"):
                with self.subTest(prefix=prefix, tail=tail):
                    raw = prefix * 2 + tail
                    node = parse(raw + "\n").body.nodes[0]
                    entry = parse(f"\\foo::\n    - {raw}\n").body.nodes[0].suite.nodes[0]
                    self.assertIsInstance(node, RawTex)
                    self.assertIsInstance(entry.value.nodes[0], RawTex)
                    self.assertEqual(node.text, prefix + tail)
                    self.assertEqual(entry.value.nodes[0].text, prefix + tail)
                    self.assertEqual(entry.value.nodes[0].span.start.column, 7)

    def test_raw_mode_region_emits_verbatim_lines_without_scanning(self):
        source = (
            "@lstlisting:\n"
            "    !BEGIN_RAW_MODE\n"
            "    @foo{A\n"
            "    !foo: |\n"
            "    \\foo:\n"
            "    \\foo >> \\bar\n"
            "    % comment\n"
            "    !!foo\n"
            "    !END_RAW_MODE\n"
        )
        node = parse(source, "raw.tfx").body.nodes[0]
        lines = node.suite.nodes

        self.assertEqual([line.text for line in lines], [
            "@foo{A",
            "!foo: |",
            "\\foo:",
            "\\foo >> \\bar",
            "% comment",
            "!!foo",
        ])
        self.assertTrue(all(line.verbatim for line in lines))
        self.assertTrue(all(line.parts is None for line in lines))
        self.assertEqual(
            [(line.span.start.line, line.span.start.column) for line in lines],
            [(3, 5), (4, 5), (5, 5), (6, 5), (7, 5), (8, 5)],
        )

    def test_raw_mode_region_dedents_by_at_most_the_block_base(self):
        source = (
            "@outer:\n"
            "    !BEGIN_RAW_MODE\n"
            "  shallow\n"
            "      deep\n"
            "    !END_RAW_MODE\n"
        )
        lines = parse(source, "raw.tfx").body.nodes[0].suite.nodes

        self.assertEqual([line.text for line in lines], ["shallow", "  deep"])
        self.assertEqual(lines[0].span.start.column, 3)
        self.assertEqual(lines[1].span.start.column, 5)

    def test_raw_mode_preserves_blank_lines_and_markers_only_create_no_nodes(self):
        source = (
            "!BEGIN_RAW_MODE\n"
            "A\n"
            "\n"
            "!END_RAW_MODE\n"
        )
        nodes = parse(source, "raw.tfx").body.nodes
        self.assertEqual([node.text for node in nodes], ["A", ""])
        self.assertTrue(all(node.verbatim for node in nodes))
        self.assertEqual(
            parse("@center:\n    !BEGIN_RAW_MODE\n    !END_RAW_MODE\n", "raw.tfx")
            .body.nodes[0].suite.nodes,
            (),
        )

    def test_raw_mode_allows_tabs_only_inside_the_region(self):
        node = parse(
            "!BEGIN_RAW_MODE\n"
            "\tinside\n"
            "!END_RAW_MODE\n",
            "raw.tfx",
        ).body.nodes[0]
        self.assertEqual(node.text, "\tinside")

        with self.assertRaisesRegex(
            ParseError,
            r"raw\.tfx:4:8: parse error: tab characters are not allowed",
        ):
            parse(
                "!BEGIN_RAW_MODE\n"
                "\tinside\n"
                "!END_RAW_MODE\n"
                "outside\t\n",
                "raw.tfx",
            )

    def test_raw_mode_ignores_nested_begin_and_misaligned_end(self):
        nodes = parse(
            "!BEGIN_RAW_MODE\n"
            "  !END_RAW_MODE\n"
            "    !BEGIN_RAW_MODE\n"
            "!END_RAW_MODE\n",
            "raw.tfx",
        ).body.nodes
        self.assertEqual(
            [node.text for node in nodes], ["  !END_RAW_MODE", "    !BEGIN_RAW_MODE"]
        )

    def test_raw_mode_can_be_a_sequence_continuation_block(self):
        entry = parse(
            "\\foo::\n"
            "    -\n"
            "        !BEGIN_RAW_MODE\n"
            "        !item{A\n"
            "        !END_RAW_MODE\n",
            "raw.tfx",
        ).body.nodes[0].suite.nodes[0]
        self.assertEqual([node.text for node in entry.value.nodes], ["!item{A"])
        self.assertTrue(entry.value.nodes[0].verbatim)

    def test_raw_mode_diagnostics_cover_unpaired_and_non_standalone_markers(self):
        cases = (
            (
                "!BEGIN_RAW_MODE\n",
                r"x\.tfx:1:1: parse error: '!BEGIN_RAW_MODE' is not closed by '!END_RAW_MODE'",
            ),
            (
                "!END_RAW_MODE\n",
                r"x\.tfx:1:1: parse error: '!END_RAW_MODE' has no matching '!BEGIN_RAW_MODE'",
            ),
            (
                "\\foo::\n    - !BEGIN_RAW_MODE\n",
                r"x\.tfx:2:7: parse error: '!BEGIN_RAW_MODE' must stand alone on its own line",
            ),
            (
                "!BEGIN_RAW_MODE >> @center\n",
                r"x\.tfx:1:1: parse error: '!BEGIN_RAW_MODE' must stand alone on its own line",
            ),
            (
                "!END_RAW_MODE:\n",
                r"x\.tfx:1:1: parse error: '!END_RAW_MODE' must stand alone on its own line",
            ),
            (
                "!BEGIN_RAW_MODE{x}\n",
                r"x\.tfx:1:1: parse error: '!BEGIN_RAW_MODE' must stand alone on its own line",
            ),
            (
                "!BEGIN_RAW_MODE % comment\n",
                r"x\.tfx:1:1: parse error: '!BEGIN_RAW_MODE' must stand alone on its own line",
            ),
            (
                "!BEGIN_RAW_MODE%comment\n",
                r"x\.tfx:1:1: parse error: '!BEGIN_RAW_MODE' must stand alone on its own line",
            ),
            (
                "!BEGIN_RAW_MODE{\n",
                r"x\.tfx:1:1: parse error: '!BEGIN_RAW_MODE' must stand alone on its own line",
            ),
            (
                "!END_RAW_MODE%comment\n",
                r"x\.tfx:1:1: parse error: '!END_RAW_MODE' must stand alone on its own line",
            ),
        )
        for source, message in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(ParseError, message):
                    parse(source, "x.tfx")

    def test_raw_mode_requires_the_existing_structural_indentation(self):
        with self.assertRaisesRegex(
            ParseError,
            r"x\.tfx:1:5: parse error: invalid structural indentation",
        ):
            parse("    !BEGIN_RAW_MODE\n    !END_RAW_MODE\n", "x.tfx")

    def test_plain_sequence_rejects_unmarked_children(self):
        with self.assertRaisesRegex(ParseError, "sequence suites"):
            parse("\\foo::\n    A\n", "x.tfx")

    def test_sequence_suites_require_entries_and_alignment(self):
        cases = (
            ("\\foo::\n", "1:1"),
            ("\\foo::\n  - A\n", "2:3"),
            ("\\foo::\n      - A\n", "2:7"),
            ("\\foo::\n    - A\n      continuation\n     bad\n", "4:6"),
            ("\\foo::\n    - \\bar::\n", "2:7"),
            ("\\foo::\n    - \\bar::\n      - A\n", "3:7"),
        )
        for source, location in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError,
                    rf"x\.tfx:{location}: parse error: sequence",
                ):
                    parse(source, "x.tfx")

    def test_blank_lines_are_preserved_only_in_block_values(self):
        sequence = parse("\\foo::\n    - A\n\n    - B\n", "x.tfx").body.nodes[0]
        block = parse("\\foo:\n    A\n\n    B\n", "x.tfx").body.nodes[0]
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
        node = parse("日本語\n@align*:\n    x &= y\n", "x.tfx").body.nodes[1]
        self.assertEqual(node.name, "align*")
        self.assertEqual(node.span.start.line, 2)
        self.assertEqual(node.span.start.column, 1)

    def test_stack_segments_require_prefixes(self):
        with self.assertRaisesRegex(ParseError, "each stack segment"):
            parse("\\foo >> bar:\n    BODY\n", "x.tfx")

    def test_suite_markers_and_trailing_tokens(self):
        self.assertEqual(
            parse("\\foo:\n    BODY\n", "x.tfx").body.nodes[0].suite_mode,
            SuiteMode.BLOCK,
        )
        self.assertEqual(
            parse("\\foo::\n    - BODY\n", "x.tfx").body.nodes[0].suite_mode,
            SuiteMode.SEQUENCE,
        )
        raw = parse("\\textbf{Note}: see below\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(raw, RawTex)
        self.assertEqual(raw.text, "\\textbf{Note}: see below")
        for source in (
            "\\foo:|\n    BODY\n",
            "\\foo: |\n    BODY\n",
            "\\foo: | extra\n",
            "\\foo: |-\n",
            "\\foo:: |\n    BODY\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.tfx")

    def test_empty_block_suites_are_allowed(self):
        self.assertEqual(
            parse("@:\n", "x.tfx").body.nodes[0].suite.nodes,
            (),
        )
        self.assertEqual(
            parse("\\foo:\n", "x.tfx").body.nodes[0].suite.nodes,
            (),
        )

    def test_special_segments_accept_one_trailing_binding_list(self):
        node = parse("!import{a.tfx}(answers=on, memo=$memo)\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, SpecialInvocation)
        self.assertEqual(
            [group.kind for group in node.groups],
            [GroupKind.REQUIRED, GroupKind.BINDING],
        )
        self.assertEqual(node.groups[1].value, "answers=on, memo=$memo")
        self.assertEqual(node.groups[1].span.start.column, 15)
        self.assertEqual(node.groups[1].span.end.column, 39)

    def test_binding_list_composes_with_stacks_and_suites(self):
        stack = parse("@center >> !import{a.tfx}(x=on)\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(stack, Stack)
        self.assertEqual(stack.segments[1].groups[1].kind, GroupKind.BINDING)
        entry = parse("\\foo::\n    - !import{a.tfx}(x=off)\n", "x.tfx").body.nodes[0]
        inner = entry.suite.nodes[0].value.nodes[0]
        self.assertEqual(inner.groups[1].value, "x=off")

    def test_binding_list_contents_stay_opaque(self):
        node = parse("!import{a.tfx}(a=(b), c={d,e}, f=\\))\n", "x.tfx").body.nodes[0]
        self.assertEqual(node.groups[1].value, "a=(b), c={d,e}, f=\\)")

    def test_binding_list_is_rejected_outside_special_segments(self):
        command = parse("\\foo(x):\n    B\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(command, RawTex)
        with self.assertRaisesRegex(
            ParseError,
            "unexpected token after structural name or group",
        ):
            parse("\\foo(x)::\n", "x.tfx")

        environment = parse("@foo(x):\n    B\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(environment, ParsedInvocation)
        # An environment name still swallows the parentheses.
        self.assertEqual(environment.name, "foo(x)")

    def test_only_a_special_reports_a_binding_list_diagnostic(self):
        # A command or environment can never carry a binding list, so '(' in
        # its header stays the ordinary unexpected token it always was.
        for source in ("\\vspace{1em}(x)::\n", "@tabular{c}(x):\n    A\n"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError,
                    "unexpected token after structural name or group",
                ):
                    parse(source, "x.tfx")

    def test_binding_list_errors_are_specific(self):
        for source, message in (
            ("!a{b}(x=on\n", "unclosed binding list"),
            ("!a{b}(x=}\n", "mismatched group delimiter"),
            ("!a(x=on){b}\n", r"must follow its groups"),
            ("!a{b}(x=on)(y=on)\n", r"at most one '\(\.\.\.\)' list"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ParseError, message):
                    parse(source, "x.tfx")

    def test_indentation_and_tabs_keep_diagnostics(self):
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:7: parse error"):
            parse("@foo:\n      @bar:\n        BODY\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:3: parse error"):
            parse("ok\n  \tbad\n", "x.tfx")


if __name__ == "__main__":
    unittest.main()
