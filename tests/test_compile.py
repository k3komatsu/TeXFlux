import unittest

from beamercraft import compile_text
from beamercraft.ast import (
    Argument,
    Block,
    BraceGroup,
    GenericInvocation,
    Item,
    ParsedInvocation,
    RawTex,
    SpecialInvocation,
    Stack,
)
from beamercraft.errors import DirectiveError, ParseError, ValidationError
from beamercraft.normalize import DirectiveRegistry, DirectiveSpec, normalize
from beamercraft.parser import parse
from beamercraft.render import render


class CompileTests(unittest.TestCase):
    def test_prefix_semantics_are_name_agnostic(self):
        self.assertEqual(compile_text("\\foo{A}\n"), "\\foo{A}\n")
        self.assertEqual(
            compile_text("@foo{A}:\n    BODY\n"),
            "\\begin{foo}{A}\nBODY\n\\end{foo}\n",
        )
        self.assertEqual(
            compile_text("!items:\n    - A\n"),
            "\\begin{itemize}\n\\item A\n\\end{itemize}\n",
        )
        with self.assertRaises(ParseError):
            compile_text("@foo{A}\n")

    def test_ordinary_environment(self):
        self.assertEqual(
            compile_text("@unknownenv[O]:\n    raw $x$\n"),
            "\\begin{unknownenv}[O]\nraw $x$\n\\end{unknownenv}\n",
        )

    def test_structured_command_uses_one_long_argument_for_whole_suite(self):
        source = (
            "\\foo{SHORT}:\n"
            "    \\bar\n"
            "    @baz:\n"
            "        BODY\n"
        )
        self.assertEqual(
            compile_text(source),
            "\\foo{SHORT}{\n"
            "\\bar\n"
            "\\begin{baz}\n"
            "BODY\n"
            "\\end{baz}\n"
            "}\n",
        )

        invocation = normalize(parse(source)).body.nodes[0]
        self.assertIsInstance(invocation, GenericInvocation)
        self.assertEqual(len(invocation.arguments), 2)
        suite_argument = invocation.arguments[1]
        self.assertIsInstance(suite_argument.value, Block)
        self.assertEqual(len(suite_argument.value.nodes), 2)

    def test_command_suite_matches_explicit_arg(self):
        compact = (
            "\\rightnotebox{Note}:\n"
            "    @align*:\n"
            "        &\\text{圧縮率 } \\alpha = N/K < 1 \\\\\n"
            "        &\\text{サブキャリア数} N \\\\\n"
            "        &\\text{FFTサイズ} K\n"
        )
        explicit = (
            "\\rightnotebox{Note}:\n"
            "    !arg:\n"
            "        @align*:\n"
            "            &\\text{圧縮率 } \\alpha = N/K < 1 \\\\\n"
            "            &\\text{サブキャリア数} N \\\\\n"
            "            &\\text{FFTサイズ} K\n"
        )
        self.assertEqual(compile_text(compact), compile_text(explicit))
        self.assertEqual(
            compile_text(compact),
            "\\rightnotebox{Note}{\n"
            "\\begin{align*}\n"
            "&\\text{圧縮率 } \\alpha = N/K < 1 \\\\\n"
            "&\\text{サブキャリア数} N \\\\\n"
            "&\\text{FFTサイズ} K\n"
            "\\end{align*}\n"
            "}\n",
        )

    def test_explicit_command_uses_one_argument_per_arg_directive(self):
        self.assertEqual(
            compile_text(
                "\\foo:\n"
                "    !arg:\n"
                "        ARG1\n"
                "    !arg:\n"
                "        ARG2\n"
            ),
            "\\foo{\nARG1\n}{\nARG2\n}\n",
        )

    def test_command_suite_can_contain_structural_nodes(self):
        source = (
            "\\foo{SHORT}:\n"
            "    説明文\n"
            "\n"
            "    @infobox{結果}:\n"
            "        Hello\n"
        )
        self.assertEqual(
            compile_text(source),
            "\\foo{SHORT}{\n"
            "説明文\n"
            "\n"
            "\\begin{infobox}{結果}\n"
            "Hello\n"
            "\\end{infobox}\n"
            "}\n",
        )

    def test_block_is_always_a_brace_group(self):
        self.assertEqual(
            compile_text("!block:\n    \\small\n    local text\n"),
            "{\n\\small\nlocal text\n}\n",
        )
        self.assertEqual(
            compile_text("\\foo:\n    !block:\n        A\n        B\n"),
            "\\foo{\n{\nA\nB\n}\n}\n",
        )

    def test_explicit_arg_wrapping_a_block_makes_double_braces(self):
        self.assertEqual(
            compile_text(
                "\\foo:\n"
                "    !arg:\n"
                "        !block:\n"
                "            A\n"
            ),
            "\\foo{\n{\nA\n}\n}\n",
        )

    def test_command_explicit_mode_and_mixing_error(self):
        self.assertEqual(
            compile_text(
                "\\foo:\n"
                "    !arg:\n"
                "        A\n"
                "    !arg{B}\n"
            ),
            "\\foo{\nA\n}{B}\n",
        )
        with self.assertRaises(ValidationError):
            compile_text(
                "\\foo:\n"
                "    !arg:\n"
                "        A\n"
                "    @bar:\n"
                "        B\n"
            )
        with self.assertRaises(ValidationError):
            compile_text(
                "\\foo:\n"
                "    !body:\n"
                "        A\n"
            )

    def test_environment_explicit_mode_and_mixing_error(self):
        self.assertEqual(
            compile_text(
                "@foo:\n"
                "    !arg:\n"
                "        A\n"
            ),
            "\\begin{foo}{\nA\n}\n\\end{foo}\n",
        )
        self.assertEqual(
            compile_text(
                "@foo:\n"
                "    !arg:\n"
                "        A\n"
                "    !body:\n"
                "        B\n"
            ),
            "\\begin{foo}{\nA\n}\nB\n\\end{foo}\n",
        )
        self.assertEqual(
            compile_text(
                "@foo{SHORT}:\n"
                "    !arg:\n"
                "        LONG\n"
                "    !body:\n"
                "        BODY\n"
            ),
            "\\begin{foo}{SHORT}{\nLONG\n}\nBODY\n\\end{foo}\n",
        )
        with self.assertRaises(ValidationError):
            compile_text(
                "@foo:\n"
                "    !arg:\n"
                "        A\n"
                "    ordinary body\n"
            )
        with self.assertRaises(ValidationError):
            compile_text(
                "@foo:\n"
                "    !body:\n"
                "        BODY\n"
                "    !arg:\n"
                "        A\n"
            )
        with self.assertRaises(ValidationError):
            compile_text("@foo:\n    !body{bad}:\n        BODY\n")

    def test_environment_accepts_inline_explicit_arg(self):
        self.assertEqual(
            compile_text(
                "@foo:\n"
                "    !arg{INLINE}\n"
            ),
            "\\begin{foo}{INLINE}\n"
            "\\end{foo}\n",
        )

    def test_vertical_bar_after_suite_marker_is_not_supported(self):
        with self.assertRaises(ParseError):
            compile_text("@foo: |\n    BODY\n")
        with self.assertRaises(ParseError):
            compile_text("!block: |\n    BODY\n")
        with self.assertRaises(ParseError):
            compile_text("!items: |\n    - ITEM\n")
        with self.assertRaises(ParseError):
            compile_text("\\foo >> @bar: |\n    BODY\n")
        with self.assertRaises(ParseError):
            compile_text("\\foo: |-\n    BODY\n")

    def test_stack_is_pure_desugaring(self):
        self.assertEqual(
            compile_text(
                "@A{x} >> @B[y] >> @C:\n"
                "    BODY\n"
            ),
            "\\begin{A}{x}\n"
            "\\begin{B}[y]\n"
            "\\begin{C}\n"
            "BODY\n"
            "\\end{C}\n"
            "\\end{B}\n"
            "\\end{A}\n",
        )
        self.assertEqual(
            compile_text(
                "\\foo >> @bar >> !block:\n"
                "    A\n"
            ),
            "\\foo{\n"
            "\\begin{bar}\n"
            "{\n"
            "A\n"
            "}\n"
            "\\end{bar}\n"
            "}\n",
        )
        self.assertEqual(
            compile_text(
                "@frame{Title} >> @center >> !items:\n"
                "    - A\n"
            ),
            "\\begin{frame}{Title}\n"
            "\\begin{center}\n"
            "\\begin{itemize}\n"
            "\\item A\n"
            "\\end{itemize}\n"
            "\\end{center}\n"
            "\\end{frame}\n",
        )

    def test_stack_normalization_preserves_segment_locations(self):
        document = normalize(
            parse(
                "\\foo >> @bar >> !block:\n"
                "    A\n",
                filename="stack.bmc",
            )
        )
        outer = document.body.nodes[0]
        self.assertEqual((outer.loc.line, outer.loc.column), (1, 1))
        outer_argument = outer.arguments[0].value
        middle = outer_argument.nodes[0]
        self.assertEqual((middle.loc.line, middle.loc.column), (1, 9))
        inner = middle.body.nodes[0]
        self.assertEqual((inner.loc.line, inner.loc.column), (1, 17))
        self.assertEqual((inner.body.nodes[0].loc.line, inner.body.nodes[0].loc.column), (2, 5))

    def test_items_forms_and_nested_lists(self):
        source = (
            "!items:\n"
            "    -<2->[A] first\n"
            "      second\n"
            "        - nested\n"
            "    -\n"
        )
        self.assertEqual(
            compile_text(source),
            "\\begin{itemize}\n"
            "\\item<2->[A] first\n"
            "second\n"
            "\\begin{itemize}\n"
            "\\item nested\n"
            "\\end{itemize}\n"
            "\\item\n"
            "\\end{itemize}\n",
        )

    def test_items_keep_raw_tex_and_reject_directives(self):
        self.assertEqual(
            compile_text("!items:\n    - text @foo{A}\n"),
            "\\begin{itemize}\n\\item text @foo{A}\n\\end{itemize}\n",
        )
        with self.assertRaises(ValidationError):
            compile_text(
                "!items:\n"
                "    @foo{A}:\n"
                "        BODY\n"
            )

    def test_items_keep_directive_looking_continuations_raw(self):
        self.assertEqual(
            compile_text(
                "!items:\n"
                "    - first\n"
                "      @raw{A}:\n"
                "      !raw\n"
                "      \\raw:\n"
            ),
            "\\begin{itemize}\n"
            "\\item first\n"
            "@raw{A}:\n"
            "!raw\n"
            "\\raw:\n"
            "\\end{itemize}\n",
        )

    def test_items_reject_bad_levels_and_keep_continuation_blanks(self):
        with self.assertRaises(ValidationError):
            compile_text("!items:\n    - A\n            - jump\n")
        self.assertEqual(
            compile_text(
                "!items:\n"
                "    - A\n"
                "      continuation\n"
                "\n"
                "      next\n"
            ),
            "\\begin{itemize}\n"
            "\\item A\n"
            "continuation\n"
            "\n"
            "next\n"
            "\\end{itemize}\n",
        )

    def test_item_continuation_can_escape_directive_looking_tex(self):
        source = "!items:\n    - first\n      @@literal-at\n"
        self.assertEqual(
            compile_text(source),
            "\\begin{itemize}\n\\item first\n@literal-at\n\\end{itemize}\n",
        )

    def test_special_namespace_and_structured_constraints(self):
        with self.assertRaises(DirectiveError):
            compile_text("!unknown\n")
        with self.assertRaises(DirectiveError):
            compile_text("!arg:\n    A\n")
        with self.assertRaises(DirectiveError):
            compile_text("!body:\n    A\n")
        with self.assertRaises(ValidationError):
            compile_text("\\foo:\n    !arg{A}\n    text\n")
        with self.assertRaises(ValidationError):
            compile_text(
                "@foo:\n"
                "    !body:\n"
                "        BODY\n"
                "    !arg{A}\n"
            )
        with self.assertRaises(ValidationError):
            compile_text("\\foo:\n    !arg[A]\n")
        with self.assertRaises(DirectiveError):
            compile_text("!arg{INLINE}\n")
        with self.assertRaises(DirectiveError):
            compile_text("!body{INLINE}\n")

    def test_source_comments_and_final_lf(self):
        source = "@foo:\n    raw\n\n@@at\n"
        self.assertEqual(
            compile_text(
                source,
                filename="slides.bmc",
                source_comments=True,
            ),
            "% beamercraft: slides.bmc:1\n"
            "\\begin{foo}\n"
            "% beamercraft: slides.bmc:2\n"
            "raw\n"
            "\\end{foo}\n"
            "\n"
            "% beamercraft: slides.bmc:4\n"
            "@at\n",
        )

    def test_source_comments_survive_long_arguments_and_block(self):
        source = (
            "\\foo:\n"
            "    !arg:\n"
            "        A\n"
            "    !arg:\n"
            "        !block:\n"
            "            B\n"
        )
        self.assertEqual(
            compile_text(source, filename="slides.bmc", source_comments=True),
            "% beamercraft: slides.bmc:1\n"
            "\\foo{\n"
            "% beamercraft: slides.bmc:3\n"
            "A\n"
            "}{\n"
            "% beamercraft: slides.bmc:5\n"
            "{\n"
            "% beamercraft: slides.bmc:6\n"
            "B\n"
            "}\n"
            "}\n",
        )

    def test_trailing_blank_lines_close_suites_before_root(self):
        self.assertEqual(
            compile_text("\\foo:\n    A\n\n"),
            "\\foo{\nA\n}\n\n",
        )
        self.assertEqual(
            compile_text("\\foo:\n    !arg:\n        A\n\n"),
            "\\foo{\nA\n}\n\n",
        )

    def test_normalized_ast_has_no_syntax_only_nodes(self):
        document = normalize(
            parse("@frame >> @center >> !items:\n    - A\n")
        )

        def walk(value):
            if isinstance(value, (Stack, SpecialInvocation, ParsedInvocation)):
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
                return (
                    isinstance(value.value, str)
                    or walk(value.value)
                )
            if isinstance(value, (RawTex, str)):
                return True
            return False

        self.assertTrue(walk(document.body))
        self.assertEqual(
            render(document),
            compile_text("@frame >> @center >> !items:\n    - A\n"),
        )

    def test_custom_handler_is_ast_to_ast_and_is_normalized(self):
        def result(node, _context):
            return (
                GenericInvocation("infobox", (), node.suite, node.loc),
            )

        registry = DirectiveRegistry()
        registry.register("result", DirectiveSpec(result))
        document = normalize(
            parse("!result:\n    \\foo{A}\n"),
            registry,
        )
        self.assertEqual(
            render(document),
            "\\begin{infobox}\n\\foo{A}\n\\end{infobox}\n",
        )

    def test_custom_handler_may_return_raw_tex_or_brace_group(self):
        def result(node, _context):
            return (RawTex("generated", node.loc),)

        registry = DirectiveRegistry()
        registry.register("result", DirectiveSpec(result))
        document = normalize(
            parse("!result:\n    ignored\n"),
            registry,
        )
        self.assertEqual(render(document), "generated\n")

        def grouped(node, _context):
            return (
                BraceGroup(
                    Block((RawTex("inside", node.loc),), node.loc),
                    node.loc,
                ),
            )

        registry.register("grouped", DirectiveSpec(grouped))
        self.assertEqual(
            render(normalize(parse("!grouped:\n    ignored\n"), registry)),
            "{\ninside\n}\n",
        )


if __name__ == "__main__":
    unittest.main()
