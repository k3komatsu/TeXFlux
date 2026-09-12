import unittest

from texflux import compile_text, compile_with_map
from texflux.ast import (
    Argument,
    Block,
    BraceGroup,
    GenericInvocation,
    Item,
    ParsedInvocation,
    RawTex,
    SequenceEntry,
    SpecialInvocation,
    Stack,
)
from texflux.errors import DirectiveError, ValidationError
from texflux.normalize import BUILTIN_DIRECTIVES, normalize
from texflux.parser import parse
from texflux.render import render


class CompileTests(unittest.TestCase):
    def test_raw_commands_and_named_environment(self):
        self.assertEqual(compile_text("\\foo{A}\n"), "\\foo{A}\n")
        self.assertEqual(
            compile_text("@unknownenv[O]: |\n    raw $x$\n"),
            "\\begin{unknownenv}[O]\nraw $x$\n\\end{unknownenv}\n",
        )

    def test_command_sequence_consumes_all_values(self):
        self.assertEqual(
            compile_text("\\foo{COMPACT}:\n    - A\n    - B\n"),
            "\\foo{COMPACT}{A}{B}\n",
        )

    def test_command_block_is_one_long_argument(self):
        self.assertEqual(
            compile_text("\\foo: |\n    A\n    B\n"),
            "\\foo{\nA\nB\n}\n",
        )

    def test_mixed_inline_and_multiline_arguments(self):
        self.assertEqual(
            compile_text(
                "\\foo:\n"
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
        self.assertEqual(compile_text("\\foo:\n    - a\n    - b\n"), "\\foo{a}{b}\n")
        self.assertEqual(
            compile_text("\\foo:\n    - a\n      b\n"),
            "\\foo{\na\nb\n}\n",
        )
        self.assertEqual(
            compile_text("\\foo:\n    - @center: |\n        x\n"),
            "\\foo{\n\\begin{center}\nx\n\\end{center}\n}\n",
        )

    def test_author_written_braces_are_copied_verbatim(self):
        # '{' first and a matching '}' last means the author placed both
        # braces, so TeXFlux reproduces their exact layout.
        self.assertEqual(
            compile_text(
                "\\foo:\n    - {a\n      b\n      }\n    - {\n      c\n      d}\n"
            ),
            "\\foo{a\nb\n}{\nc\nd}\n",
        )
        for text in ("{a\\} b}", "{50\\% off}", "{a{b}c}"):
            with self.subTest(text=text):
                rendered = compile_text(f"\\foo:\n    - {text}\n").rstrip("\n")
                self.assertEqual(rendered, f"\\foo{text}")

    def test_unbalanced_braces_fall_back_to_a_generated_pair(self):
        # Visible extra braces beat silently changing the argument count.
        self.assertEqual(compile_text("\\foo:\n    - {a} {b}\n"), "\\foo{{a} {b}}\n")

    def test_a_trailing_comment_keeps_the_closing_brace_safe(self):
        # A fully commented last line would swallow a hugged brace.
        result = compile_with_map("\\foo:\n    - % commented\n", filename="c.tfx")

        self.assertEqual(result.text, "\\foo{% commented\n}\n")
        self.assertEqual(len(result.rendered.warnings), 1)
        self.assertIn("comment line", result.rendered.warnings[0].message)

    def test_an_inline_comment_only_warns(self):
        result = compile_with_map("\\foo:\n    - a % trailing\n", filename="c.tfx")

        self.assertEqual(result.text, "\\foo{a % trailing}\n")
        self.assertEqual(len(result.rendered.warnings), 1)
        self.assertIn("commented out", result.rendered.warnings[0].message)

    def test_author_written_braces_warn_like_generated_ones(self):
        # Identical output has to produce an identical diagnostic.
        generated = compile_with_map("\\cmd:\n    - a % b\n    - t\n", filename="c.tfx")
        explicit = compile_with_map(
            "\\cmd:\n    - {a % b}\n    - t\n",
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
            "\\cmd:\n    - {a\n      % foo}\n    - tail\n",
            filename="c.tfx",
        )

        self.assertEqual(result.text, "\\cmd{a\n% foo}\n{tail}\n")
        self.assertEqual(len(result.rendered.warnings), 1)
        self.assertIn("comment line", result.rendered.warnings[0].message)

    def test_an_escaped_percent_is_not_a_comment(self):
        result = compile_with_map("\\foo:\n    - 50\\% off\n", filename="c.tfx")

        self.assertEqual(result.text, "\\foo{50\\% off}\n")
        self.assertEqual(result.rendered.warnings, ())

    def test_environment_sequence_uses_last_value_as_body(self):
        self.assertEqual(
            compile_text(
                "@myenv:\n"
                "    - ARG1\n"
                "    - ARG2\n"
                "    - @: |\n"
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
                "@myenv:\n"
                "    - ARG\n"
                "    - @center: |\n"
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
                "@frame{Title}: |\n"
                "    Hello\n"
                "    @center: |\n"
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
            compile_text("@{\\small\\color{red}}: |\n    Hello\n"),
            "{\n\\small\\color{red}\nHello\n}\n",
        )
        self.assertEqual(compile_text("@: |\n    A\n    B\n"), "A\nB\n")
        self.assertEqual(compile_text("@:\n    - A\n    - B\n"), "A\nB\n")
        self.assertEqual(compile_text("@:\n"), "\n")
        self.assertEqual(compile_text("@{}:\n"), "{\n}\n")
        with self.assertRaises(ValidationError):
            compile_text("@foo:\n")
        self.assertEqual(
            compile_text("\\foo:\n    - @{}: |\n        A\n"),
            "\\foo{\n{\nA\n}\n}\n",
        )

    def test_literal_header_groups_are_independent_values(self):
        self.assertEqual(
            compile_text(
                "@{\\Large}: |\n"
                "    Large text\n"
                "\\\\\n"
                "@{\\small}: |\n"
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
                "@frame{Title} >> @center >> @{\\small}: |\n"
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
                "\\outer >> \\inner:\n"
                "    - A\n"
                "    - B\n"
            ),
            "\\outer{\n\\inner{A}{B}\n}\n",
        )

    def test_blank_line_semantics(self):
        self.assertEqual(
            compile_text("\\foo:\n    - A\n\n    - B\n"),
            "\\foo{A}{B}\n",
        )
        self.assertEqual(
            compile_text("\\foo: |\n    A\n\n    B\n"),
            "\\foo{\nA\n\nB\n}\n",
        )

    def test_specials_are_restricted_to_their_new_contracts(self):
        self.assertEqual(
            compile_text("!items:\n    - A\n    - B\n"),
            "\\begin{itemize}\n\\item A\n\\item B\n\\end{itemize}\n",
        )
        self.assertEqual(
            compile_text("!vpad{-1em}{2em}: |\n    contents\n"),
            "\\vspace{-1em}\ncontents\n\\vspace{2em}\n",
        )
        with self.assertRaises(ValidationError):
            compile_text("!vpad{-1em}:\n    - contents\n")
        for old in (
            "!block:\n",
            "!arg:\n",
            "!body:\n",
        ):
            with self.subTest(old=old):
                with self.assertRaises(DirectiveError):
                    compile_text(old)

    def test_items_overlay_labels_nested_and_multiline(self):
        source = (
            "!items:\n"
            "    -<2->[A] first\n"
            "      continuation\n"
            "        - nested\n"
            "    -\n"
            "        long first\n"
            "        continuation\n"
        )
        self.assertEqual(
            compile_text(source),
            "\\begin{itemize}\n"
            "\\item<2->[A] first\n"
            "continuation\n"
            "\\begin{itemize}\n"
            "\\item nested\n"
            "\\end{itemize}\n"
            "\\item long first\n"
            "continuation\n"
            "\\end{itemize}\n",
        )

    def test_items_keep_raw_tex_and_reject_structural_item_nodes(self):
        self.assertEqual(
            compile_text("!items:\n    - text @foo{A}\n"),
            "\\begin{itemize}\n\\item text @foo{A}\n\\end{itemize}\n",
        )
        with self.assertRaises(ValidationError):
            compile_text("!items:\n    @foo: |\n        BODY\n")

    def test_special_and_container_errors_have_spans(self):
        with self.assertRaisesRegex(DirectiveError, r"unknown\.tfx:1:1"):
            compile_text("!unknown\n", filename="unknown.tfx")
        with self.assertRaisesRegex(ValidationError, r"mix\.tfx:2:7"):
            compile_text("@foo:\n    - @bar\n", filename="mix.tfx")

    def test_source_comments_and_final_lf(self):
        self.assertEqual(
            compile_text(
                "@foo: |\n    raw\n\n@@at\n",
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
            parse("@frame >> @center >> !items:\n    - A\n")
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
            if isinstance(value, Item):
                return walk(value.continuation)
            if isinstance(value, Block):
                return all(walk(child) for child in value.nodes)
            if isinstance(value, Argument):
                return isinstance(value.value, str) or walk(value.value)
            if isinstance(value, (RawTex, str)):
                return True
            return False

        self.assertTrue(walk(document.body))
        self.assertEqual(
            render(document),
            compile_text("@frame >> @center >> !items:\n    - A\n"),
        )

    def test_custom_handler_remains_ast_to_ast(self):
        def result(node, _registry):
            return (GenericInvocation("infobox", (), node.suite, node.span),)

        registry = BUILTIN_DIRECTIVES.copy()
        registry["result"] = result
        document = normalize(parse("!result: |\n    \\foo{A}\n"), registry)
        self.assertEqual(
            render(document),
            "\\begin{infobox}\n\\foo{A}\n\\end{infobox}\n",
        )

    def test_custom_handler_must_return_canonical_tuple(self):
        def invalid(_node, _registry):
            return "generated TeX"

        registry = BUILTIN_DIRECTIVES.copy()
        registry["invalid"] = invalid
        with self.assertRaises(TypeError):
            normalize(parse("!invalid\n"), registry)

    def test_compact_groups_remain_before_sequence_values(self):
        self.assertEqual(
            compile_text("\\doublecolumn[0.48]:\n    - A\n    - B\n"),
            "\\doublecolumn[0.48]{A}{B}\n",
        )


if __name__ == "__main__":
    unittest.main()
