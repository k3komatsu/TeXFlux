import unittest

from texflux import compile_text, compile_with_map
from texflux.ast import (
    Argument,
    Block,
    BraceGroup,
    GenericInvocation,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SpecialInvocation,
    Stack,
)
from texflux.errors import (
    DirectiveError,
    MacroExpansionError,
    ParseError,
    ValidationError,
)
from texflux.normalize import BUILTIN_DIRECTIVES, normalize
from texflux.parser import parse
from texflux.render import render, render_with_provenance


class CompileTests(unittest.TestCase):
    def test_multiline_stacks_match_single_line_stacks(self):
        for header in (
            "@hoge >>\n@fuga >>\n@fuge:",
            "@hoge >> @fuga >>\n@fuge:",
        ):
            with self.subTest(header=header):
                self.assertEqual(
                    compile_text(header + "\n    foobarhoge\n"),
                    "\\begin{hoge}\n\\begin{fuga}\n\\begin{fuge}\n"
                    "foobarhoge\n\\end{fuge}\n\\end{fuga}\n\\end{hoge}\n",
                )
        for source in (
            "@hoge >>\n@fuga >> \\foobar\n",
            "\\outer >>\n\\inner::\n    - A\n    - B\n",
            "@outer:\n    @center >>\n    \\textbf{A}\n",
            "!off{unused} >>\n!before{\\vspace{1em}} >>\n\\x\n",
            "@center >>\n@itemize:\n    \\item A\n",
        ):
            with self.subTest(source=source):
                joined = source.replace(">>\n    ", ">> ").replace(">>\n", ">> ")
                self.assertEqual(compile_text(source), compile_text(joined))

    def test_multiline_stack_sequence_value_uses_multiline_braces(self):
        self.assertEqual(
            compile_text("\\cmd::\n    - @center >>\n    \\x\n    - tail\n"),
            "\\cmd{\n\\begin{center}\n\\x\n\\end{center}\n}{tail}\n",
        )

    def test_raw_commands_and_named_environment(self):
        self.assertEqual(compile_text("\\foo{A}\n"), "\\foo{A}\n")
        self.assertEqual(
            compile_text("@unknownenv[O]:\n    raw $x$\n"),
            "\\begin{unknownenv}[O]\nraw $x$\n\\end{unknownenv}\n",
        )

    def test_command_sequence_consumes_all_values(self):
        self.assertEqual(
            compile_text("\\foo{COMPACT}::\n    - A\n    - B\n"),
            "\\foo{COMPACT}{A}{B}\n",
        )

    def test_sequence_markers_distinguish_generated_and_explicit_arguments(self):
        self.assertEqual(
            compile_text(
                "\\command::\n"
                "    - simple\n"
                "    - {group as content}\n"
                "    + {explicit required argument}\n"
                "    + [explicit optional argument]\n"
                "    + <2->\n"
                "    + {%\n"
                "      whitespace-sensitive%\n"
                "      }\n"
            ),
            "\\command{simple}{{group as content}}"
            "{explicit required argument}[explicit optional argument]<2->"
            "{%\nwhitespace-sensitive%\n}\n",
        )

    def test_environment_sequence_accepts_explicit_arguments_before_body(self):
        self.assertEqual(
            compile_text(
                "@myenv::\n"
                "    + [opt]\n"
                "    - ARG\n"
                "    - @:\n"
                "        BODY\n"
            ),
            "\\begin{myenv}[opt]{ARG}\n"
            "BODY\n"
            "\\end{myenv}\n",
        )
        with self.assertRaisesRegex(
            ValidationError,
            "environment sequence suites require the final '-' entry to be the body",
        ):
            compile_text("@myenv::\n    + {body}\n")

    def test_command_block_is_one_long_argument(self):
        self.assertEqual(
            compile_text("\\foo:\n    A\n    B\n"),
            "\\foo{\nA\nB\n}\n",
        )

    def test_command_empty_block_is_one_empty_long_argument(self):
        self.assertEqual(compile_text("\\foo:\n"), "\\foo{\n}\n")

    def test_mixed_inline_and_multiline_arguments(self):
        self.assertEqual(
            compile_text(
                "\\foo::\n"
                "    - short\n"
                "      continuation\n"
                "    -\n"
                "        long line 1\n"
                "        long line 2\n"
            ),
            "\\foo{\nshort\ncontinuation\n}{\nlong line 1\nlong line 2\n}\n",
        )

    def test_line_count_places_the_argument_braces(self):
        # A value confined to one line keeps its braces tight; a multi-line
        # value gets them on their own lines.
        self.assertEqual(compile_text("\\foo::\n    - a\n    - b\n"), "\\foo{a}{b}\n")
        self.assertEqual(
            compile_text("\\foo::\n    - a\n      b\n"),
            "\\foo{\na\nb\n}\n",
        )
        self.assertEqual(
            compile_text("\\foo::\n    - @center:\n        x\n"),
            "\\foo{\n\\begin{center}\nx\n\\end{center}\n}\n",
        )

    def test_generated_and_explicit_braces_have_distinct_semantics(self):
        # '-' treats brace-looking text as content; '+' preserves the one
        # group the author wrote, including its layout.
        self.assertEqual(
            compile_text(
                "\\foo::\n    - {a\n      b\n      }\n    - {\n      c\n      d}\n"
            ),
            "\\foo{\n{a\nb\n}\n}{\n{\nc\nd}\n}\n",
        )
        for text in ("{a\\} b}", "{50\\% off}", "{a{b}c}"):
            with self.subTest(text=text):
                rendered = compile_text(f"\\foo::\n    + {text}\n").rstrip("\n")
                self.assertEqual(rendered, f"\\foo{text}")

    def test_unbalanced_braces_fall_back_to_a_generated_pair(self):
        # Visible extra braces beat silently changing the argument count.
        self.assertEqual(compile_text("\\foo::\n    - {a} {b}\n"), "\\foo{{a} {b}}\n")

    def test_a_trailing_comment_keeps_the_closing_brace_safe(self):
        # A fully commented last line would swallow a hugged brace.
        result = compile_with_map("\\foo::\n    - % commented\n", filename="c.tfx")

        self.assertEqual(result.text, "\\foo{% commented\n}\n")
        self.assertEqual(len(result.rendered.warnings), 1)
        self.assertIn("comment line", result.rendered.warnings[0].message)

    def test_an_inline_comment_only_warns(self):
        result = compile_with_map("\\foo::\n    - a % trailing\n", filename="c.tfx")

        self.assertEqual(result.text, "\\foo{a % trailing}\n")
        self.assertEqual(len(result.rendered.warnings), 1)
        self.assertIn("commented out", result.rendered.warnings[0].message)

    def test_author_written_braces_warn_like_generated_ones(self):
        # Identical output has to produce an identical diagnostic.
        generated = compile_with_map("\\cmd::\n    - a % b\n    - t\n", filename="c.tfx")
        explicit = compile_with_map(
            "\\cmd::\n    + {a % b}\n    - t\n",
            filename="c.tfx",
        )

        self.assertEqual(generated.text, explicit.text)
        self.assertEqual(
            [w.message for w in generated.rendered.warnings],
            [w.message for w in explicit.rendered.warnings],
        )

    def test_a_comment_holding_an_author_brace_still_protects_what_follows(self):
        # scan_group does not know about comments, so this is EXPLICIT even
        # though the author's '}' sits inside one. The group is broken either
        # way, but the next argument must not be commented out too.
        result = compile_with_map(
            "\\cmd::\n    + {a\n      % foo}\n    - tail\n",
            filename="c.tfx",
        )

        self.assertEqual(result.text, "\\cmd{a\n% foo}\n{tail}\n")
        self.assertEqual(len(result.rendered.warnings), 1)
        self.assertIn("comment line", result.rendered.warnings[0].message)

    def test_an_escaped_percent_is_not_a_comment(self):
        result = compile_with_map("\\foo::\n    - 50\\% off\n", filename="c.tfx")

        self.assertEqual(result.text, "\\foo{50\\% off}\n")
        self.assertEqual(result.rendered.warnings, ())

    def test_environment_sequence_uses_last_value_as_body(self):
        self.assertEqual(
            compile_text(
                "@myenv::\n"
                "    - ARG1\n"
                "    - ARG2\n"
                "    - @:\n"
                "        BODY1\n"
                "        BODY2\n"
            ),
            "\\begin{myenv}{ARG1}{ARG2}\n"
            "BODY1\n"
            "BODY2\n"
            "\\end{myenv}\n",
        )

    def test_environment_sequence_accepts_a_structural_body_value(self):
        self.assertEqual(
            compile_text(
                "@myenv::\n"
                "    - ARG\n"
                "    - @center:\n"
                "        BODY\n"
            ),
            "\\begin{myenv}{ARG}\n"
            "\\begin{center}\n"
            "BODY\n"
            "\\end{center}\n"
            "\\end{myenv}\n",
        )

    def test_environment_block_is_body_only(self):
        self.assertEqual(
            compile_text(
                "@frame{Title}:\n"
                "    Hello\n"
                "    @center:\n"
                "        World\n"
            ),
            "\\begin{frame}{Title}\n"
            "Hello\n"
            "\\begin{center}\n"
            "World\n"
            "\\end{center}\n"
            "\\end{frame}\n",
        )

    def test_literal_and_transparent_containers(self):
        self.assertEqual(
            compile_text("@{\\small\\color{red}}:\n    Hello\n"),
            "{\n\\small\\color{red}\nHello\n}\n",
        )
        self.assertEqual(compile_text("@:\n    A\n    B\n"), "A\nB\n")
        self.assertEqual(compile_text("@::\n    - A\n    - B\n"), "A\nB\n")
        self.assertEqual(compile_text("@::\n"), "\n")
        self.assertEqual(compile_text("@{}::\n"), "{\n}\n")
        with self.assertRaisesRegex(
            ValidationError,
            "anonymous containers do not accept '\\+' sequence entries",
        ):
            compile_text("@::\n    + {A}\n")
        with self.assertRaises(ValidationError):
            compile_text("@foo::\n")
        self.assertEqual(
            compile_text("\\foo::\n    - @{}:\n        A\n"),
            "\\foo{\n{\nA\n}\n}\n",
        )

    def test_literal_header_groups_are_independent_values(self):
        self.assertEqual(
            compile_text(
                "@{\\Large}:\n"
                "    Large text\n"
                "\\\\\n"
                "@{\\small}:\n"
                "    Small text\n"
            ),
            "{\n"
            "\\Large\n"
            "Large text\n"
            "}\n"
            "\\\\\n"
            "{\n"
            "\\small\n"
            "Small text\n"
            "}\n",
        )

    def test_closed_stack_and_nested_closed_stack(self):
        self.assertEqual(
            compile_text("@center >> \\includegraphics{fig.pdf}\n"),
            "\\begin{center}\n\\includegraphics{fig.pdf}\n\\end{center}\n",
        )
        self.assertEqual(
            compile_text(
                "@frame{Title} >> @center >> @{\\small} >> \\input{fig.tex}\n"
            ),
            "\\begin{frame}{Title}\n"
            "\\begin{center}\n"
            "{\n"
            "\\small\n"
            "\\input{fig.tex}\n"
            "}\n"
            "\\end{center}\n"
            "\\end{frame}\n",
        )

    def test_incomplete_closed_stack_is_rejected(self):
        with self.assertRaises(ValidationError):
            compile_text("@foo >> @center\n")

    def test_open_stack_uses_rightmost_suffix(self):
        self.assertEqual(
            compile_text(
                "@frame{Title} >> @center >> @{\\small}:\n"
                "    BODY\n"
            ),
            "\\begin{frame}{Title}\n"
            "\\begin{center}\n"
            "{\n"
            "\\small\n"
            "BODY\n"
            "}\n"
            "\\end{center}\n"
            "\\end{frame}\n",
        )
        self.assertEqual(
            compile_text(
                "\\outer >> \\inner::\n"
                "    - A\n"
                "    - B\n"
            ),
            "\\outer{\n\\inner{A}{B}\n}\n",
        )

    def test_blank_line_semantics(self):
        self.assertEqual(
            compile_text("\\foo::\n    - A\n\n    - B\n"),
            "\\foo{A}{B}\n",
        )
        self.assertEqual(
            compile_text("\\foo:\n    A\n\n    B\n"),
            "\\foo{\nA\n\nB\n}\n",
        )

    def test_removed_specials_still_fail_as_unknown_ones(self):
        # !vpad joined the four explicit-mode constructs: TeX spacing is
        # written as ordinary TeX behind !before or !around, so the compiler
        # holds no handler that knows \\vspace.
        for old in (
            "!block::\n",
            "!arg::\n",
            "!body::\n",
            "!items::\n    - A\n",
            "!vpad{-1em}:\n    contents\n",
        ):
            with self.subTest(old=old):
                with self.assertRaises(DirectiveError):
                    compile_text(old)

    def test_standard_flow_macros_replace_the_vpad_special(self):
        self.assertEqual(
            compile_text("!around{\\vspace{-1em}}{\\vspace{2em}}:\n    contents\n"),
            "\\vspace{-1em}\ncontents\n\\vspace{2em}\n",
        )
        self.assertEqual(
            compile_text("!before{\\vspace{-1em}}:\n    contents\n"),
            "\\vspace{-1em}\ncontents\n",
        )
        self.assertEqual(
            compile_text("!after{\\vspace{2em}}:\n    contents\n"),
            "contents\n\\vspace{2em}\n",
        )

    def test_off_ignores_its_group_and_keeps_the_stack_payload(self):
        self.assertEqual(
            compile_text("\\fuga >> !off{\\foo{a}{b}} >> \\hoge\n"),
            "\\fuga{\n\\hoge\n}\n",
        )

    def test_drop_discards_its_whole_stack_payload(self):
        self.assertEqual(
            compile_text("!drop >> \\hoge >> \\fuga\n"),
            "\n",
        )

    def test_off_and_drop_report_ordinary_macro_arity_errors(self):
        # Both are ordinary macros now, so a wrong shape is an arity error
        # from the normal macro path rather than a hand-written contract.
        cases = {
            "!off >> \\hoge\n": r"'!off' expects \{ignored\}\{body\}, exactly 2 value\(s\), got 1",
            "!off{\\foo}\n": r"'!off' expects \{ignored\}\{body\}, exactly 2 value\(s\), got 1",
            "!drop{unused} >> \\hoge\n": r"'!drop' expects \{body\}, exactly 1 value\(s\), got 2",
            "!drop\n": r"'!drop' expects \{body\}, exactly 1 value\(s\), got 0",
        }
        for source, message in cases.items():
            with self.subTest(source=source):
                with self.assertRaisesRegex(MacroExpansionError, message):
                    compile_text(source)

    def test_an_item_body_is_an_ordinary_raw_line(self):
        # The removed !items grammar read its suite as raw text, the one
        # construct that scanned nothing, so item bodies were exempt from
        # every line rule. An environment body is an ordinary block suite,
        # so each of these has to keep behaving like the raw line it is --
        # and each escape has to keep working.
        for body, message in (
            ("\\item Summary:", "unexpected token in structural header"),
            ("\\TextCA{Note}::", "sequence suites require at least one"),
            ("@foo{A}", "environment directives require a suite marker"),
        ):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ParseError, message):
                    compile_text(f"@itemize:\n    {body}\n")
        with self.assertRaisesRegex(DirectiveError, "unknown special"):
            compile_text("@itemize:\n    !foo\n")

        for body, expected in (
            ("\\item Summary:{}", "\\item Summary:{}"),
            ("\\TextCA{Note}:{}", "\\TextCA{Note}:{}"),
            ("@@foo{A}", "@foo{A}"),
            ("!!foo{A}", "!foo{A}"),
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    compile_text(f"@itemize:\n    {body}\n"),
                    f"\\begin{{itemize}}\n{expected}\n\\end{{itemize}}\n",
                )
        self.assertEqual(
            compile_text("@itemize:\n    \\item\n    Summary:\n"),
            "\\begin{itemize}\n\\item\nSummary:\n\\end{itemize}\n",
        )

        # The two shapes that change the output rather than failing, which
        # is what makes them the ones a migration has to look for. '@@' is
        # both of these at once: the escape above, and a spelling a raw
        # suite used to emit verbatim.
        self.assertEqual(
            compile_text("@itemize:\n    \\item >> \\foo\n"),
            "\\begin{itemize}\n\\item{\n\\foo\n}\n\\end{itemize}\n",
        )
        self.assertEqual(
            compile_text("@itemize:\n    @@@foo{A}\n"),
            "\\begin{itemize}\n@@foo{A}\n\\end{itemize}\n",
        )

        self.assertEqual(
            compile_text("@itemize:\n    !!!foo{A}\n"),
            "\\begin{itemize}\n!!foo{A}\n\\end{itemize}\n",
        )

        # A block suite keeps blank lines; the removed handler dropped the
        # blank separators between items.
        self.assertEqual(
            compile_text("@itemize:\n    \\item a\n\n    \\item b\n"),
            "\\begin{itemize}\n\\item a\n\n\\item b\n\\end{itemize}\n",
        )
        self.assertEqual(
            compile_text("@itemize:\n    \\item a >> b\n"),
            "\\begin{itemize}\n\\item a >> b\n\\end{itemize}\n",
        )

    def test_exclamation_raw_line_escape_use_cases(self):
        for source, expected in (
            ("@verbatim:\n    !!important\n",
             "\\begin{verbatim}\n!important\n\\end{verbatim}\n"),
            ("@lstlisting:\n       !!! # { >>:\n",
             "\\begin{lstlisting}\n   !! # { >>:\n\\end{lstlisting}\n"),
            ("!!重要\n", "!重要\n"),
            ("\\foo::\n    - !!bar\n", "\\foo{!bar}\n"),
        ):
            with self.subTest(source=source):
                self.assertEqual(compile_text(source), expected)

    def test_raw_mode_covers_long_verbatim_regions(self):
        for source, expected in (
            (
                "@verbatim:\n"
                "    !BEGIN_RAW_MODE\n"
                "    !important\n"
                "    !END_RAW_MODE\n",
                "\\begin{verbatim}\n!important\n\\end{verbatim}\n",
            ),
            (
                "@lstlisting:\n"
                "    !BEGIN_RAW_MODE\n"
                "    !! # a listing marker\n"
                "    !END_RAW_MODE\n",
                "\\begin{lstlisting}\n!! # a listing marker\n\\end{lstlisting}\n",
            ),
            (
                "!BEGIN_RAW_MODE\n"
                "!重要\n"
                "!END_RAW_MODE\n",
                "!重要\n",
            ),
        ):
            with self.subTest(source=source):
                self.assertEqual(compile_text(source), expected)

    def test_raw_line_marker_reaches_command_lines_no_escape_could(self):
        # Each of these is a raw TeX line that the header scanner claims: the
        # first two are parse errors today and the last two compile to the
        # wrong output.  '@@' / '!!' cannot reach any of them because they do
        # not start with '@' or '!'.
        for source, expected in (
            ("!| \\item Note:\n", "\\item Note:\n"),
            ("!| \\item 手順:\n", "\\item 手順:\n"),
            ("!| \\textbf{Note}:\n", "\\textbf{Note}:\n"),
            ("!| \\emph{x} >> \\emph{y}\n", "\\emph{x} >> \\emph{y}\n"),
        ):
            with self.subTest(source=source):
                self.assertEqual(compile_text(source), expected)

    def test_raw_line_marker_reaches_sequence_payloads_too(self):
        for source, expected in (
            (
                "@itemize::\n    - !| \\item Note:\n",
                "\\begin{itemize}\n\\item Note:\n\\end{itemize}\n",
            ),
            (
                "@itemize::\n    - !| \\textbf{Note}:\n",
                "\\begin{itemize}\n\\textbf{Note}:\n\\end{itemize}\n",
            ),
        ):
            with self.subTest(source=source):
                self.assertEqual(compile_text(source), expected)

    def test_raw_line_marker_alone_is_a_blank_raw_line_not_a_blank_line(self):
        # This is why a bare '!|' is legal: a real blank line at the end of a
        # suite is rewound to the enclosing block, a '!|' line is content.
        self.assertEqual(
            compile_text("@center:\n    x\n    !|\ny\n"),
            "\\begin{center}\nx\n\n\\end{center}\ny\n",
        )
        self.assertEqual(
            compile_text("@center:\n    x\n\ny\n"),
            "\\begin{center}\nx\n\\end{center}\n\ny\n",
        )

    def test_raw_line_marker_keeps_tabs_the_way_a_region_does(self):
        self.assertEqual(
            compile_text("@lstlisting:\n    !| \tdef f():\n"),
            "\\begin{lstlisting}\n\tdef f():\n\\end{lstlisting}\n",
        )

    def test_special_and_container_errors_have_spans(self):
        with self.assertRaisesRegex(DirectiveError, r"unknown\.tfx:1:1"):
            compile_text("!unknown\n", filename="unknown.tfx")
        with self.assertRaisesRegex(ValidationError, r"mix\.tfx:2:7"):
            compile_text("@foo::\n    - @bar\n", filename="mix.tfx")

    def test_source_comments_and_final_lf(self):
        self.assertEqual(
            compile_text(
                "@foo:\n    raw\n\n@@at\n",
                filename="slides.tfx",
                source_comments=True,
            ),
            "% texflux: slides.tfx:1\n"
            "\\begin{foo}\n"
            "% texflux: slides.tfx:2\n"
            "raw\n"
            "\\end{foo}\n"
            "\n"
            "% texflux: slides.tfx:4\n"
            "@at\n",
        )

    def test_normalized_ast_has_no_syntax_only_nodes(self):
        document = normalize(
            parse("@frame >>\n@center >> @itemize:\n    \\item A\n")
        )

        def walk(value):
            if isinstance(value, (Stack, SpecialInvocation, ParsedInvocation, SequenceEntry)):
                return False
            if isinstance(value, BraceGroup):
                return walk(value.body)
            if isinstance(value, GenericInvocation):
                return (
                    value.body is None or walk(value.body)
                ) and all(walk(argument) for argument in value.arguments)
            if isinstance(value, Block):
                return all(walk(child) for child in value.nodes)
            if isinstance(value, Argument):
                return isinstance(value.value, str) or walk(value.value)
            if isinstance(value, (RawTex, str)):
                return True
            return False

        self.assertTrue(walk(document.body))
        self.assertEqual(document.body.nodes[0].body.nodes[0].span.start.line, 2)
        self.assertEqual(
            render(document),
            compile_text("@frame >> @center >> @itemize:\n    \\item A\n"),
        )

    def test_custom_handler_remains_ast_to_ast(self):
        def result(node, _registry):
            return (GenericInvocation("infobox", (), node.suite, node.span),)

        registry = BUILTIN_DIRECTIVES.copy()
        registry["result"] = result
        source = "!result:\n    \\foo{A}\n"
        document = normalize(parse(source, "h.tfx"), registry)
        self.assertEqual(
            render(document),
            "\\begin{infobox}\n\\foo{A}\n\\end{infobox}\n",
        )

        # A node a handler synthesized carries the special's own span, so
        # expansion never loses the line an author has to be sent back to.
        rendered = render_with_provenance(document)
        spans = {
            fragment.text: fragment.source
            for fragment in rendered.fragments
            if fragment.source is not None
        }
        self.assertEqual(spans["\\begin{infobox}"].start.line, 1)
        self.assertEqual(spans["\\end{infobox}"].start.line, 1)
        self.assertEqual(spans["\\foo{A}"].start.line, 2)

    def test_custom_handler_must_return_canonical_tuple(self):
        def invalid(_node, _registry):
            return "generated TeX"

        registry = BUILTIN_DIRECTIVES.copy()
        registry["invalid"] = invalid
        with self.assertRaises(TypeError):
            normalize(parse("!invalid\n"), registry)

    def test_compact_groups_remain_before_sequence_values(self):
        self.assertEqual(
            compile_text("\\doublecolumn[0.48]::\n    - A\n    - B\n"),
            "\\doublecolumn[0.48]{A}{B}\n",
        )


if __name__ == "__main__":
    unittest.main()
