import unittest

from beamercraft import compile_text
from beamercraft.ast import (
    Argument,
    Block,
    GenericInvocation,
    Item,
    ParsedGeneric,
    RawTex,
    SpecialInvocation,
    Stack,
)
from beamercraft.errors import DirectiveError, ParseError, ValidationError
from beamercraft.normalize import DirectiveRegistry, DirectiveSpec, normalize
from beamercraft.parser import parse
from beamercraft.render import render


class CompileTests(unittest.TestCase):
    def test_generic_command_and_environment_are_name_agnostic(self):
        self.assertEqual(compile_text("@unknown{A}\n"), r"\unknown{A}" + "\n")
        with self.assertRaises(ParseError):
            compile_text("@unknown-environment:\n")

    def test_ordinary_environment(self):
        self.assertEqual(
            compile_text("@unknownenv[O]:\n    raw $x$\n"),
            "\\begin{unknownenv}[O]\nraw $x$\n\\end{unknownenv}\n",
        )

    def test_structured_compact_and_long_arguments(self):
        source = (
            "@foo{SHORT}:\n"
            "    @!arg{INLINE}\n"
            "    @!arg:\n"
            "        BLOCK\n"
        )
        self.assertEqual(
            compile_text(source),
            "\\foo{SHORT}{INLINE}{\nBLOCK\n}\n",
        )

    def test_structured_environment_and_nested_body(self):
        source = (
            "@foo:\n"
            "    @!arg:\n"
            "        A\n"
            "    @!body:\n"
            "        @bar{B}\n"
        )
        self.assertEqual(
            compile_text(source),
            "\\begin{foo}{\nA\n}\n\\bar{B}\n\\end{foo}\n",
        )

    def test_mixed_argument_order_is_preserved(self):
        source = "@foo:\n    @!arg:\n        A\n    @!arg{B}\n"
        self.assertEqual(compile_text(source), "\\foo{\nA\n}{B}\n")

    def test_stack_desugars_right_to_left(self):
        source = "@A{x} >> B[y] >> C:\n    BODY\n"
        self.assertEqual(
            compile_text(source),
            "\\begin{A}{x}\n"
            "\\begin{B}[y]\n"
            "\\begin{C}\n"
            "BODY\n"
            "\\end{C}\n"
            "\\end{B}\n"
            "\\end{A}\n",
        )

    def test_items_forms_and_nested_lists(self):
        source = (
            "@!items:\n"
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
            compile_text("@!items:\n    - text @foo{A}\n"),
            "\\begin{itemize}\n\\item text @foo{A}\n\\end{itemize}\n",
        )
        with self.assertRaises(ValidationError):
            compile_text("@!items:\n    @foo\n")

    def test_items_reject_bad_levels_and_keep_continuation_blanks(self):
        with self.assertRaises(ValidationError):
            compile_text("@!items:\n    - A\n            - jump\n")
        self.assertEqual(
            compile_text("@!items:\n    - A\n      continuation\n\n      next\n"),
            "\\begin{itemize}\n\\item A\ncontinuation\n\nnext\n\\end{itemize}\n",
        )

    def test_item_continuation_can_escape_directive_looking_tex(self):
        source = "@!items:\n    - first\n      @@literal-at\n"
        self.assertEqual(
            compile_text(source),
            "\\begin{itemize}\n\\item first\n@literal-at\n\\end{itemize}\n",
        )

    def test_special_namespace_and_structured_constraints(self):
        with self.assertRaises(DirectiveError):
            compile_text("@!unknown\n")
        with self.assertRaises(DirectiveError):
            compile_text("@!arg:\n    A\n")
        with self.assertRaises(ValidationError):
            compile_text("@foo:\n    @!arg{A}\n    text\n")
        with self.assertRaises(ValidationError):
            compile_text("@foo:\n    @!body:\n        BODY\n    @!arg{A}\n")
        with self.assertRaises(ValidationError):
            compile_text("@foo:\n    @!arg[A]\n")

    def test_stack_special_terminal_and_final_command_rejection(self):
        self.assertEqual(
            compile_text("@frame{Title} >> !items:\n    - A\n"),
            "\\begin{frame}{Title}\n"
            "\\begin{itemize}\n"
            "\\item A\n"
            "\\end{itemize}\n"
            "\\end{frame}\n",
        )
        with self.assertRaises(ValidationError):
            compile_text("@A >> B:\n    @!arg{A}\n")
        with self.assertRaises(DirectiveError):
            compile_text("@A >> !arg:\n    A\n")

    def test_source_comments_and_final_lf(self):
        source = "@foo:\n    raw\n\n@@at\n"
        self.assertEqual(
            compile_text(source, filename="slides.bmc", source_comments=True),
            "% beamercraft: slides.bmc:1\n"
            "\\begin{foo}\n"
            "% beamercraft: slides.bmc:2\n"
            "raw\n"
            "\\end{foo}\n"
            "\n"
            "% beamercraft: slides.bmc:4\n"
            "@at\n",
        )

    def test_normalized_ast_has_no_syntax_only_nodes(self):
        document = normalize(parse("@A >> !items:\n    - A\n"))

        def walk(value):
            if isinstance(value, (Stack, SpecialInvocation)):
                return False
            if isinstance(value, ParsedGeneric):
                return False
            if isinstance(value, GenericInvocation):
                return (value.body is None or walk(value.body)) and all(
                    walk(arg.value) for arg in value.arguments if isinstance(arg.value, Block)
                )
            if isinstance(value, Item):
                return walk(value.continuation)
            if isinstance(value, (Block,)):
                return all(walk(child) for child in value.nodes)
            if isinstance(value, (Argument, RawTex, str)):
                return True
            return False

        self.assertTrue(walk(document.body))
        self.assertEqual(render(document), compile_text("@A >> !items:\n    - A\n"))

    def test_custom_handler_is_ast_to_ast_and_is_normalized(self):
        def result(node, _context):
            return (GenericInvocation("infobox", (), node.suite, node.loc),)

        registry = DirectiveRegistry()
        registry.register("result", DirectiveSpec(result))
        document = normalize(parse("@!result:\n    @foo{A}\n"), registry)
        self.assertEqual(
            render(document),
            "\\begin{infobox}\n\\foo{A}\n\\end{infobox}\n",
        )

    def test_custom_handler_may_return_raw_tex(self):
        def result(node, _context):
            return (RawTex("generated", node.loc),)

        registry = DirectiveRegistry()
        registry.register("result", DirectiveSpec(result))
        document = normalize(parse("@!result:\n    ignored\n"), registry)
        self.assertEqual(render(document), "generated\n")


if __name__ == "__main__":
    unittest.main()
