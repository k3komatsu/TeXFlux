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
        node = parse("@a >>  \r\n@b{B} >>\r\n@c::\r\n    X\r\n", "x.tfx").body.nodes[0]
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
            ("@a >>\n\n@b::\n", "2:1"),
            ("@a >>\n    @b::\n", "2:5"),
            ("@a::\n    @b >>\n@c::\n", "3:1"),
            ("@a >>\nraw\n", "2:1"),
            ("@a >>\n\\verb|raw|\n", "2:6"),
            ("@a >>\n@b:: extra\n", "2:6"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ParseError, rf"x\.tfx:{location}: parse error"):
                    parse(source, "x.tfx")

    def test_missing_suite_diagnostics_name_both_markers(self):
        with self.assertRaisesRegex(
            ParseError,
            "environment directives require a suite marker '::' or ':::'",
        ):
            parse("@foo\n", "x.tfx")
        with self.assertRaisesRegex(
            ParseError,
            "indented lines require a suite marker '::' or ':::'",
        ):
            parse("!foo\n    BODY\n", "x.tfx")

    def test_raw_tex_does_not_continue_on_trailing_arrows(self):
        source = "raw >>\n\\foo{A >>}\n\\verb|x| >>\n@@literal >>\n!!literal >>\n"
        self.assertTrue(all(isinstance(n, RawTex) for n in parse(source).body.nodes))
        entries = parse("\\foo:::\n    - literal >>\n    - next\n").body.nodes[0]
        self.assertEqual(len(entries.suite.nodes), 2)

    def test_prefixes_and_suite_modes_are_lexical(self):
        document = parse(
            "\\foo{A}\n"
            "@foo{A}::\n"
            "    BODY\n"
            "!foo:::\n"
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

    def test_double_colon_is_block_and_triple_colon_is_sequence(self):
        block = parse("\\foo::\n    BODY\n", "x.tfx").body.nodes[0]
        sequence = parse("\\foo:::\n    - ITEM\n", "x.tfx").body.nodes[0]
        spaced_block = parse("\\foo ::   \n    BODY\n", "x.tfx").body.nodes[0]
        spaced_sequence = parse("\\foo:::   \n    - ITEM\n", "x.tfx").body.nodes[0]

        self.assertEqual(block.suite_mode, SuiteMode.BLOCK)
        self.assertEqual(sequence.suite_mode, SuiteMode.SEQUENCE)
        self.assertEqual(spaced_block.suite_mode, SuiteMode.BLOCK)
        self.assertEqual(spaced_sequence.suite_mode, SuiteMode.SEQUENCE)
        self.assertEqual(sequence.suite_span.start.column, 5)
        self.assertEqual(sequence.suite_span.end.column, 8)
        for source in ("\\foo::|\n    BODY\n", "\\foo:: |\n    BODY\n"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ParseError, r"x\.tfx:1:"):
                    parse(source, "x.tfx")

    def test_pipe_is_not_a_suite_marker_for_structural_prefixes(self):
        for source in (
            "\\foo::|\n    BODY\n",
            "@foo:|\n    BODY\n",
            "@:|\n    BODY\n",
            "!foo:|\n    BODY\n",
            "@a >> @b: |\n    BODY\n",
            "\\foo:::\n    - \\bar:: |\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.tfx")
        # On a command line the lone colon in front of the pipe is prose, so
        # the line is raw TeX rather than a broken marker.
        for source in ("\\foo:|\n", "\\foo: |-\n"):
            with self.subTest(source=source):
                node = parse(source, "x.tfx").body.nodes[0]
                self.assertIsInstance(node, RawTex)
                self.assertEqual(node.text, source.rstrip("\n"))

    def test_sequence_entries_keep_marker_spans(self):
        node = parse(
            "\\foo:::\n"
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
        self.assertIsNone(first.argument_kind)
        self.assertIsNone(second.argument_kind)

    def test_explicit_sequence_entries_are_one_opaque_group(self):
        node = parse(
            "\\foo:::\n"
            "    + {%\n"
            "      \\bar: >> @baz{A} @@literal !!special\n"
            "      }   \n"
            "    - tail\n",
            "x.tfx",
        ).body.nodes[0]
        first, second = node.suite.nodes
        self.assertIsInstance(first, SequenceEntry)
        self.assertEqual(first.argument_kind, GroupKind.REQUIRED)
        self.assertEqual(
            [child.text for child in first.value.nodes],
            ["{%", "\\bar: >> @baz{A} @@literal !!special", "}"],
        )
        self.assertIsNone(second.argument_kind)

        for opener, close in (("{", "}"), ("[", "]"), ("<", ">")):
            with self.subTest(opener=opener):
                entry = parse(f"\\foo:::\n    + {opener}value{close}\n").body.nodes[0].suite.nodes[0]
                self.assertEqual(entry.argument_kind, GroupKind.from_opener(opener))

    def test_explicit_sequence_entries_require_exactly_one_balanced_group(self):
        for payload in ("value", "{a}{b}", "{unclosed"):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ParseError, "explicit sequence entries"):
                    parse(f"\\foo:::\n    + {payload}\n", "x.tfx")

    def test_explicit_group_preserves_trailing_spaces_inside_an_unclosed_line(self):
        source = "\\foo:::\n    + {a  \n      b}\n"
        entry = parse(source, "x.tfx").body.nodes[0].suite.nodes[0]
        self.assertEqual([child.text for child in entry.value.nodes], ["{a  ", "b}"])

    def test_block_suite_is_an_ordinary_structural_block(self):
        node = parse("\\foo::\n    A\n    @bar::\n        B\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.suite_mode, SuiteMode.BLOCK)
        self.assertIsInstance(node.suite.nodes[0], RawTex)
        self.assertIsInstance(node.suite.nodes[1], ParsedInvocation)

    def test_anonymous_containers_have_distinct_kinds(self):
        brace, transparent = parse(
            "@{\\small}::\n    A\n@::\n    B\n",
            "x.tfx",
        ).body.nodes
        self.assertEqual(brace.kind, InvocationKind.BRACE)
        self.assertEqual(brace.groups[0].value, "\\small")
        self.assertEqual(transparent.kind, InvocationKind.TRANSPARENT)

    def test_closed_and_open_stacks_are_preserved(self):
        closed, opened = parse(
            "@center >> \\includegraphics{fig.pdf}\n"
            "@center >> \\foo::\n"
            "    BODY\n",
            "x.tfx",
        ).body.nodes
        self.assertIsInstance(closed, Stack)
        self.assertIsNone(closed.suite)
        self.assertIsNone(closed.suite_mode)
        self.assertIsInstance(opened, Stack)
        self.assertEqual(opened.suite_mode, SuiteMode.BLOCK)

    def test_group_contents_do_not_create_structural_tokens(self):
        first = parse("\\foo{A >> B}::\n    A\n", "x.tfx").body.nodes[0]
        second = parse("\\foo{\\texttt{A: B}}:::\n    - C\n", "x.tfx").body.nodes[0]
        self.assertEqual(first.groups[0].value, "A >> B")
        self.assertEqual(second.groups[0].value, "\\texttt{A: B}")

    def test_ordinary_commands_remain_raw(self):
        for prefix in ("@", "!"):
            with self.subTest(prefix=prefix):
                block = parse(f"@foo::\n    {prefix * 2}literal\n", "x.tfx").body.nodes[0]
                sequence = parse(f"\\foo:::\n    - {prefix * 2}literal\n", "x.tfx").body.nodes[0]
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
                    f"@lstlisting::\n       {prefix * 2}literal {{ >>:\n",
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
                    entry = parse(f"\\foo:::\n    - {raw}\n").body.nodes[0].suite.nodes[0]
                    self.assertIsInstance(node, RawTex)
                    self.assertIsInstance(entry.value.nodes[0], RawTex)
                    self.assertEqual(node.text, prefix + tail)
                    self.assertEqual(entry.value.nodes[0].text, prefix + tail)
                    self.assertEqual(entry.value.nodes[0].span.start.column, 7)

    def test_raw_line_marker_emits_the_rest_of_the_line_verbatim(self):
        # '@@' / '!!' cannot reach any of these because they do not start
        # with '@' or '!'. The marker works whether or not the header scanner
        # would claim the line: the '>>' one it always does, the colon ones
        # only once a block follows.
        for text in ("\\item Note:", "\\textbf{Note}:", "\\emph{x} >> \\emph{y}"):
            with self.subTest(text=text):
                node = parse(f"!| {text}\n", "x.tfx").body.nodes[0]
                self.assertIsInstance(node, RawTex)
                self.assertEqual(node.text, text)
                self.assertTrue(node.verbatim)
                self.assertIsNone(node.parts)
                self.assertEqual(node.span.file, "x.tfx")
                self.assertEqual(
                    (node.span.start.line, node.span.start.column), (1, 1)
                )

    def test_raw_line_marker_preserves_extra_indentation_and_spans(self):
        node = parse(
            "@lstlisting::\n       !| literal { >>:\n",
            "escape.tfx",
        ).body.nodes[0].suite.nodes[0]
        self.assertIsInstance(node, RawTex)
        self.assertEqual(node.text, "   literal { >>:")
        self.assertEqual(node.span.file, "escape.tfx")
        self.assertEqual((node.span.start.line, node.span.start.column), (2, 5))

    def test_raw_line_marker_strips_exactly_the_marker_and_one_space(self):
        cases = (
            ("!| foo", "foo"),
            ("!|   foo", "  foo"),
            ("!| ", ""),
            ("!|", ""),
            ("!| !!foo", "!!foo"),
            ("!| !BEGIN_RAW_MODE", "!BEGIN_RAW_MODE"),
            ("!| !text{x}", "!text{x}"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                node = parse(source + "\n", "x.tfx").body.nodes[0]
                self.assertIsInstance(node, RawTex)
                self.assertEqual(node.text, expected)
                self.assertTrue(node.verbatim)

    def test_raw_line_marker_is_itself_escaped_by_the_doubling_rule(self):
        node = parse("!!| foo\n", "x.tfx").body.nodes[0]
        self.assertEqual(node.text, "!| foo")
        self.assertFalse(node.verbatim)

    def test_raw_line_marker_requires_one_space_or_the_line_end(self):
        for source, location in (
            ("!|foo\n", "1:1"),
            ("!|\tfoo\n", "1:1"),
            ("@center::\n    !|foo\n", "2:5"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError,
                    rf"x\.tfx:{location}: parse error: "
                    r"'!\|' must be followed by one space or end the line",
                ):
                    parse(source, "x.tfx")

    def test_raw_line_marker_accepts_any_character_up_to_the_newline(self):
        node = parse("!| \tdef f():\n", "x.tfx").body.nodes[0]
        self.assertEqual(node.text, "\tdef f():")

    def test_raw_line_marker_tab_exemption_is_one_line_wide(self):
        with self.assertRaisesRegex(
            ParseError,
            r"x\.tfx:2:5: parse error: tab characters are not allowed",
        ):
            parse("!| \tok\n\\foo\tbar\n", "x.tfx")

    def test_raw_line_marker_is_literal_inside_a_raw_mode_region(self):
        nodes = parse(
            "!BEGIN_RAW_MODE\n!| foo\n!|bar\n!END_RAW_MODE\n",
            "x.tfx",
        ).body.nodes
        self.assertEqual([node.text for node in nodes], ["!| foo", "!|bar"])

    def test_raw_line_marker_works_as_a_sequence_payload(self):
        entry = parse("\\foo:::\n    - !| \\item Note:\n", "x.tfx").body.nodes[0].suite.nodes[0]
        node = entry.value.nodes[0]
        self.assertIsInstance(node, RawTex)
        self.assertEqual(node.text, "\\item Note:")
        self.assertTrue(node.verbatim)
        self.assertEqual(node.span.start.column, 7)

    def test_raw_line_marker_payload_rejects_a_missing_space(self):
        with self.assertRaisesRegex(
            ParseError,
            r"x\.tfx:2:7: parse error: "
            r"'!\|' must be followed by one space or end the line",
        ):
            parse("\\foo:::\n    - !|bad\n", "x.tfx")

    def test_raw_line_marker_payload_tabs_stay_prohibited(self):
        # The whole-file tab pre-scan classifies lines by their own text, and
        # a leading '-' is only a sequence marker inside a '::' suite, so the
        # exemption deliberately stops at the start of a line.
        with self.assertRaisesRegex(
            ParseError,
            r"x\.tfx:2:10: parse error: tab characters are not allowed",
        ):
            parse("\\foo::\n    - !| \tx\n", "x.tfx")

    def test_explicit_sequence_body_never_earns_the_tab_exemption(self):
        with self.assertRaisesRegex(
            ParseError,
            r"x\.tfx:3:12: parse error: tab characters are not allowed",
        ):
            parse("\\foo:::\n    + {\n        !| \tx\n    }\n", "x.tfx")

    def test_raw_line_marker_payload_keeps_inner_spaces_but_not_trailing_ones(self):
        # A '-' payload is right-stripped before any escape runs, which the
        # '@@' / '!!' escapes share, so only the leading spaces survive here.
        for payload, expected in (("!|   foo", "  foo"), ("!| foo  ", "foo")):
            with self.subTest(payload=payload):
                entry = parse(
                    f"\\foo:::\n    - {payload}\n", "x.tfx"
                ).body.nodes[0].suite.nodes[0]
                self.assertEqual(entry.value.nodes[0].text, expected)

    def test_raw_line_marker_is_literal_in_an_explicit_group_body(self):
        entry = parse(
            "\\foo:::\n    + {\n        !| x\n        }\n",
            "x.tfx",
        ).body.nodes[0].suite.nodes[0]
        self.assertEqual([node.text for node in entry.value.nodes], ["{", "!| x", "}"])
        self.assertFalse(any(node.verbatim for node in entry.value.nodes))

    def test_raw_line_marker_is_not_an_explicit_group_payload(self):
        with self.assertRaisesRegex(
            ParseError,
            r"x\.tfx:2:7: parse error: explicit sequence entries require one",
        ):
            parse("\\foo:::\n    + !| x\n", "x.tfx")

    def test_raw_line_marker_in_a_generated_continuation_block_allows_tabs(self):
        # The documented way to put a tab in a sequence value: the '-' payload
        # cannot, but its continuation block starts the line with the marker.
        entry = parse(
            "\\foo:::\n    - x\n        !| \\item\ty\n", "x.tfx"
        ).body.nodes[0].suite.nodes[0]
        self.assertEqual(
            [node.text for node in entry.value.nodes], ["x", "\\item\ty"]
        )
        self.assertTrue(entry.value.nodes[1].verbatim)

    def test_raw_line_marker_is_rejected_in_a_stack_segment(self):
        for source, location in (
            ("\\foo >> !| bar\n", "1:10"),
            ("\\foo >>\n!| bar\n", "2:2"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError,
                    rf"x\.tfx:{location}: parse error: invalid structural name",
                ):
                    parse(source, "x.tfx")

    def test_raw_mode_region_emits_verbatim_lines_without_scanning(self):
        source = (
            "@lstlisting::\n"
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
            "@outer::\n"
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
            parse("@center::\n    !BEGIN_RAW_MODE\n    !END_RAW_MODE\n", "raw.tfx")
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
            "\\foo:::\n"
            "    -\n"
            "        !BEGIN_RAW_MODE\n"
            "        !item{A\n"
            "        !END_RAW_MODE\n",
            "raw.tfx",
        ).body.nodes[0].suite.nodes[0]
        self.assertEqual([node.text for node in entry.value.nodes], ["!item{A"])
        self.assertTrue(entry.value.nodes[0].verbatim)

    def test_raw_mode_can_be_an_explicit_sequence_continuation(self):
        entry = parse(
            "\\foo:::\n"
            "    + {\n"
            "      !BEGIN_RAW_MODE\n"
            "      \\foo: |\n"
            "      !END_RAW_MODE\n"
            "      }\n",
            "raw.tfx",
        ).body.nodes[0].suite.nodes[0]
        self.assertEqual(
            [node.text for node in entry.value.nodes], ["{", "\\foo: |", "}"],
        )
        self.assertFalse(entry.value.nodes[0].verbatim)
        self.assertTrue(entry.value.nodes[1].verbatim)
        self.assertFalse(entry.value.nodes[2].verbatim)

    def test_raw_region_in_an_explicit_sequence_still_allows_tabs(self):
        # The '+' body re-checks the tab exemption line by line, so a region
        # nested inside one must still reach _raw_region untouched.
        entry = parse(
            "\\foo:::\n"
            "    + {\n"
            "      !BEGIN_RAW_MODE\n"
            "      \tdeep\n"
            "      !END_RAW_MODE\n"
            "      }\n",
            "raw.tfx",
        ).body.nodes[0].suite.nodes[0]
        self.assertEqual(
            [node.text for node in entry.value.nodes], ["{", "\tdeep", "}"],
        )

    def test_explicit_sequence_rejects_misaligned_raw_mode_markers(self):
        cases = (
            "\\foo::\n"
            "    + {\n"
            "      content\n"
            "        !BEGIN_RAW_MODE\n"
            "        \\item\n"
            "        !END_RAW_MODE\n"
            "      }\n",
            "\\foo::\n"
            "    + {\n"
            "      a\n"
            "        !BEGIN_RAW_MODE\n"
            "      !BEGIN_RAW_MODE\n"
            "        !END_RAW_MODE\n"
            "      }\n",
        )
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError,
                    r"raw\.tfx:4:9: parse error: invalid structural indentation",
                ):
                    parse(source, "raw.tfx")

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
                "\\foo:::\n    - !BEGIN_RAW_MODE\n",
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
            parse("\\foo:::\n    A\n", "x.tfx")

    def test_sequence_suites_require_entries_and_alignment(self):
        cases = (
            ("\\foo:::\n", "1:1"),
            ("\\foo:::\n  - A\n", "2:3"),
            ("\\foo:::\n      - A\n", "2:7"),
            ("\\foo:::\n    - A\n      continuation\n     bad\n", "4:6"),
            ("\\foo:::\n    - \\bar:::\n", "2:7"),
            ("\\foo:::\n    - \\bar:::\n      - A\n", "3:7"),
        )
        for source, location in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError,
                    rf"x\.tfx:{location}: parse error: sequence",
                ):
                    parse(source, "x.tfx")

    def test_blank_lines_are_preserved_only_in_block_values(self):
        sequence = parse("\\foo:::\n    - A\n\n    - B\n", "x.tfx").body.nodes[0]
        block = parse("\\foo::\n    A\n\n    B\n", "x.tfx").body.nodes[0]
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
        node = parse("日本語\n@align*::\n    x &= y\n", "x.tfx").body.nodes[1]
        self.assertEqual(node.name, "align*")
        self.assertEqual(node.span.start.line, 2)
        self.assertEqual(node.span.start.column, 1)

    def test_stack_segments_require_prefixes(self):
        with self.assertRaisesRegex(ParseError, "each stack segment"):
            parse("\\foo >> bar:\n    BODY\n", "x.tfx")

    def test_suite_markers_and_trailing_tokens(self):
        self.assertEqual(
            parse("\\foo::\n    BODY\n", "x.tfx").body.nodes[0].suite_mode,
            SuiteMode.BLOCK,
        )
        self.assertEqual(
            parse("\\foo:::\n    - BODY\n", "x.tfx").body.nodes[0].suite_mode,
            SuiteMode.SEQUENCE,
        )
        raw = parse("\\textbf{Note}: see below\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(raw, RawTex)
        self.assertEqual(raw.text, "\\textbf{Note}: see below")
        for source in (
            "\\foo::|\n    BODY\n",
            "\\foo:: |\n    BODY\n",
            "\\foo:: | extra\n",
            "\\foo:: |-\n",
            "\\foo::: |\n    BODY\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source, "x.tfx")

    def test_empty_suites_belong_to_containers_and_specials(self):
        # A command consumes its suite as arguments, so an empty one would
        # emit a silent '{}'; an empty argument is raw TeX the author writes
        # directly. A container and a special may both consume nothing.
        for source in ("@::\n", "@:::\n", "@foo::\n", "!foo::\n"):
            with self.subTest(source=source):
                self.assertEqual(
                    parse(source, "x.tfx").body.nodes[0].suite.nodes,
                    (),
                )
        for source in ("\\foo::\n", "@center >> \\foo::\n"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ParseError, "command block suites require an indented body"
                ):
                    parse(source, "x.tfx")

    def test_command_trailing_colon_is_a_suite_only_when_a_block_follows(self):
        # A single trailing colon is the one structural token TeX prose also
        # ends a line with, so a command line keeps it as text unless the
        # next non-blank line sits at the suite base.
        raw, tail = parse("\\textbf{Note}:\nfoo\n", "x.tfx").body.nodes
        self.assertIsInstance(raw, RawTex)
        self.assertEqual(raw.text, "\\textbf{Note}:")
        self.assertEqual((raw.span.start.column, raw.span.end.column), (1, 15))
        self.assertEqual(tail.text, "foo")

        node = parse("\\textbf{Note}::\n    body\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual(node.suite_mode, SuiteMode.BLOCK)
        self.assertEqual(node.suite.nodes[0].text, "body")

        # Blank lines never separate a header from its suite.
        node = parse("\\foo::\n\n\n    body\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual([child.text for child in node.suite.nodes], ["", "", "body"])

        # The suite base is a lower bound: a first suite line indented beyond
        # it is still the suite, and keeps its extra spaces.
        node = parse("\\foo::\n     body\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(node, ParsedInvocation)
        self.assertEqual([child.text for child in node.suite.nodes], [" body"])

        # The last line of a document, or of a suite, has no block to own.
        self.assertIsInstance(parse("\\foo:\n", "x.tfx").body.nodes[0], RawTex)
        self.assertIsInstance(parse("\\foo:  \n", "x.tfx").body.nodes[0], RawTex)
        outer = parse("@center::\n    \\foo:\n@center::\n    x\n", "x.tfx").body.nodes[0]
        self.assertEqual([type(child) for child in outer.suite.nodes], [RawTex])

        # A body indented by fewer than four spaces is not a suite either;
        # both lines stay raw, the second with its extra indentation.
        nodes = parse("\\foo:\n  body\n", "x.tfx").body.nodes
        self.assertEqual([node.text for node in nodes], ["\\foo:", "  body"])

    def test_a_marker_spelling_inside_an_unclosed_group_stays_raw(self):
        # A marker reserves a line only at depth zero, and an unclosed group
        # has no depth-zero end, so raw TeX that splits a group across lines
        # keeps reading as the TeX it looks like.
        for source in (
            "\\newcommand{\\x}{a::\n",
            "\\verb|a::|\n",
            "\\foo{a:::\n",
        ):
            with self.subTest(source=source):
                node = parse(source, "x.tfx").body.nodes[0]
                self.assertIsInstance(node, RawTex)
                self.assertEqual(node.text, source.rstrip("\n"))
        # Closed on the same line, the marker is structural as always.
        self.assertEqual(
            parse("\\foo{a}::\n    B\n", "x.tfx").body.nodes[0].suite_mode,
            SuiteMode.BLOCK,
        )

    def test_a_command_line_ending_in_a_lone_colon_is_always_raw(self):
        # '\\item Note:' does not scan as a header, and a lone colon reserves
        # nothing, so the line stays prose whatever sits underneath it.
        for source in ("\\item Note:\n", "\\item \\textbf{Note}:\n", "\\item 手順:\n"):
            with self.subTest(source=source):
                node = parse(source, "x.tfx").body.nodes[0]
                self.assertIsInstance(node, RawTex)
                self.assertEqual(node.text, source.rstrip("\n"))
        self.assertEqual(
            [node.text for node in parse("\\item Note:\n    body\n", "x.tfx").body.nodes],
            ["\\item Note:", "    body"],
        )

    def test_a_lone_colon_never_makes_a_structural_candidate(self):
        # A raw line may sit deeper than the block base; a structural
        # candidate may not. A lone colon makes no candidate, so whatever the
        # next line does cannot change the line above it.
        suite = parse(
            "@itemize::\n    \\item a\n        \\textbf{Note}:\n            body\n",
            "x.tfx",
        ).body.nodes[0].suite
        self.assertEqual(
            [node.text for node in suite.nodes],
            ["\\item a", "    \\textbf{Note}:", "        body"],
        )
        # A real marker at that depth is a misplaced candidate, as before.
        with self.assertRaisesRegex(ParseError, "invalid structural indentation"):
            parse(
                "@itemize::\n    \\item a\n        \\textbf{Note}::\n            body\n",
                "x.tfx",
            )

    def test_trailing_colon_lookahead_applies_to_sequence_payloads(self):
        # The suite base of a payload is four spaces beyond the marker, so a
        # continuation line indented less than that leaves the colon as text.
        first, second, third = parse(
            "\\foo:::\n"
            "    - \\textbf{Note}:\n"
            "    - \\bar::\n"
            "        body\n"
            "    - \\item Note:\n"
            "      cont\n",
            "x.tfx",
        ).body.nodes[0].suite.nodes
        self.assertIsInstance(first.value.nodes[0], RawTex)
        self.assertEqual(first.value.nodes[0].text, "\\textbf{Note}:")
        self.assertIsInstance(second.value.nodes[0], ParsedInvocation)
        self.assertEqual(second.value.nodes[0].suite.nodes[0].text, "body")
        self.assertEqual(
            [child.text for child in third.value.nodes],
            ["\\item Note:", "cont"],
        )

    def test_the_markers_and_the_stack_reserve_a_command_line(self):
        # None of '::', ':::' and '>>' is TeX prose, so each reserves its line
        # and then demands the value it promised, block or no block.
        with self.assertRaisesRegex(ParseError, "command block suites require"):
            parse("\\foo::\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, "sequence suites require at least one"):
            parse("\\foo:::\n", "x.tfx")
        with self.assertRaisesRegex(
            ParseError, "unexpected token after structural name or group"
        ):
            parse("\\vspace{1em}(x)::\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, "trailing token after suite marker"):
            parse("\\foo:: |\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, "stack separator needs a following"):
            parse("\\foo >>\n", "x.tfx")
        # A stack hands its suffix to the rightmost segment, so the command
        # there answers for its body just as it would on a line of its own.
        stack = parse("\\foo >> \\bar::\n    BODY\n", "x.tfx").body.nodes[0]
        self.assertIsInstance(stack, Stack)
        self.assertEqual(stack.suite_mode, SuiteMode.BLOCK)
        self.assertEqual([node.text for node in stack.suite.nodes], ["BODY"])

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
        entry = parse("\\foo:::\n    - !import{a.tfx}(x=off)\n", "x.tfx").body.nodes[0]
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

        environment = parse("@foo(x)::\n    B\n", "x.tfx").body.nodes[0]
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
            parse("@foo::\n      @bar::\n        BODY\n", "x.tfx")
        with self.assertRaisesRegex(ParseError, r"x\.tfx:2:3: parse error"):
            parse("ok\n  \tbad\n", "x.tfx")


if __name__ == "__main__":
    unittest.main()
