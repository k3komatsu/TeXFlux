import unittest

from texflux import (
    BUILTIN_DIRECTIVES,
    MacroExpansionError,
    ValidationError,
    collect_macros,
    compile_text,
    compile_with_map,
    desugar,
    load_source_map,
    parse,
)

from .support import TempDirTestCase


WRAPPER = """!defmacro{smallred}{body}: |
    @{\\small\\color{red}} >> !param{body}
"""

ITEMS = """!defmacro{items_simple}{...items}: |
    @itemize: |
        !each{items}{item}: |
            \\item
            !param{item}
"""


def collect(source, filename="m.tfx"):
    return collect_macros(desugar(parse(source, filename)), BUILTIN_DIRECTIVES)


class MacroDefinitionTests(unittest.TestCase):
    def test_definitions_emit_no_tex_and_are_collected(self):
        document, macros = collect(WRAPPER)

        self.assertEqual(list(macros), ["smallred"])
        self.assertEqual(
            [parameter.name for parameter in macros["smallred"].parameters],
            ["body"],
        )
        # The definition is removed from the document it was collected from.
        self.assertEqual(document.body.nodes, ())
        self.assertEqual(compile_text(WRAPPER), "\n")

    def test_definition_requires_a_block_template_suite(self):
        with self.assertRaisesRegex(ValidationError, r"': \|' template suite"):
            compile_text("!defmacro{foo}{x}:\n    - A\n", filename="m.tfx")

    def test_definitions_are_top_level_only(self):
        with self.assertRaisesRegex(ValidationError, "only valid at the top level"):
            compile_text(
                "@frame: |\n    !defmacro{foo}{x}: |\n        A\n",
                filename="m.tfx",
            )

    def test_signature_rules_are_validated_at_the_definition_site(self):
        cases = {
            "!defmacro{foo}{x}{x}: |\n    A\n": "duplicate macro parameter 'x'",
            "!defmacro{foo}{...r}{t}: |\n    A\n": "must be the last macro parameter",
            "!defmacro{foo}{...a}{...b}: |\n    A\n":
                "must be the last macro parameter",
            "!defmacro{foo}{1bad}: |\n    A\n": "invalid macro parameter name",
            "!defmacro: |\n    A\n": "requires a macro name group",
        }
        for source, message in cases.items():
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValidationError, message):
                    compile_text(source, filename="m.tfx")


class MacroBindingTests(unittest.TestCase):
    def test_one_parameter_block_value(self):
        self.assertEqual(
            compile_text(WRAPPER + "!smallred: |\n    Hello\n    World\n"),
            "{\n\\small\\color{red}\nHello\nWorld\n}\n",
        )

    def test_closed_stack_rhs_is_one_positional_value(self):
        self.assertEqual(
            compile_text(WRAPPER + "!smallred >> \\TextCA{Important}\n"),
            "{\n\\small\\color{red}\n\\TextCA{Important}\n}\n",
        )

    def test_compact_groups_bind_before_suite_values(self):
        source = """!defmacro{box}{title}{body}: |
    @infobox:
        - !param{title}
        - !param{body}
!box{Result}: |
    Long
    body
"""
        self.assertEqual(
            compile_text(source),
            "\\begin{infobox}{Result}\nLong\nbody\n\\end{infobox}\n",
        )

    def test_sequence_values_bind_one_block_per_marker(self):
        source = """!defmacro{twocol}{left}{right}: |
    \\doublecolumn:
        - @center >> !param{left}
        - @center >> !param{right}
!twocol:
    - Left line 1
      Left line 2

    - Right line 1
      Right line 2
"""
        self.assertEqual(
            compile_text(source),
            "\\doublecolumn{\\begin{center}\n"
            "Left line 1\nLeft line 2\n"
            "\\end{center}}{\\begin{center}\n"
            "Right line 1\nRight line 2\n"
            "\\end{center}}\n",
        )

    def test_bare_pipe_sequence_entry_stays_raw_tex(self):
        # '- |' is not a block-scalar marker; the payload is ordinary TeX.
        self.assertEqual(
            compile_text("\\foo:\n    - |\n"),
            "\\foo{|}\n",
        )

    def test_a_macro_call_is_one_value_inside_a_stack(self):
        self.assertEqual(
            compile_text(WRAPPER + "@center >> !smallred >> \\TextCA{Important}\n"),
            "\\begin{center}\n"
            "{\n\\small\\color{red}\n\\TextCA{Important}\n}\n"
            "\\end{center}\n",
        )

    def test_arity_errors_name_the_macro_shape_and_call_site(self):
        source = "!defmacro{foo}{a}{b}: |\n    X\n!foo{A}\n"
        with self.assertRaisesRegex(
            MacroExpansionError,
            r"m\.tfx:3:1: macro error: '!foo' expects \{a\}\{b\}, "
            r"exactly 2 value\(s\), got 1",
        ):
            compile_text(source, filename="m.tfx")

        with self.assertRaisesRegex(MacroExpansionError, "got 3"):
            compile_text(
                "!defmacro{foo}{a}{b}: |\n    X\n!foo{A}{B}{C}\n",
                filename="m.tfx",
            )

    def test_macro_calls_reject_non_required_groups(self):
        with self.assertRaisesRegex(MacroExpansionError, r"required '\{\.\.\.\}'"):
            compile_text(WRAPPER + "!smallred[opt]: |\n    A\n", filename="m.tfx")


class MacroVariadicTests(unittest.TestCase):
    SOURCE = """!defmacro{foo}{head}{...rest}: |
    @wrap: |
        !param{head}
        !each{rest}{value}: |
            !param{value}
"""

    def wrapped(self, count):
        values = "".join(f"    - V{index}\n" for index in range(count))
        return compile_text(self.SOURCE + "!foo:\n" + values)

    def test_rest_absorbs_every_trailing_value(self):
        # The head takes the first value; the rest sequence takes the others.
        self.assertEqual(self.wrapped(1), "\\begin{wrap}\nV0\n\\end{wrap}\n")
        self.assertEqual(
            self.wrapped(2),
            "\\begin{wrap}\nV0\nV1\n\\end{wrap}\n",
        )
        self.assertEqual(
            self.wrapped(4),
            "\\begin{wrap}\nV0\nV1\nV2\nV3\n\\end{wrap}\n",
        )

    def test_rest_requires_at_least_the_fixed_values(self):
        with self.assertRaisesRegex(MacroExpansionError, "at least 1 value"):
            compile_text(self.SOURCE + "!foo\n", filename="m.tfx")

    def test_rest_parameter_cannot_be_referenced_with_param(self):
        source = "!defmacro{foo}{...rest}: |\n    !param{rest}\n!foo:\n    - A\n"
        with self.assertRaisesRegex(
            MacroExpansionError,
            "is a rest parameter; use !each",
        ):
            compile_text(source, filename="m.tfx")


class MacroEachTests(unittest.TestCase):
    def itemize(self, values):
        entries = "".join(f"    - {value}\n" for value in values)
        call = "!items_simple:\n" + entries if values else "!items_simple\n"
        return compile_text(ITEMS + call)

    def test_zero_items_generates_nothing(self):
        self.assertEqual(self.itemize([]), "\\begin{itemize}\n\\end{itemize}\n")

    def test_one_item(self):
        self.assertEqual(
            self.itemize(["Only"]),
            "\\begin{itemize}\n\\item\nOnly\n\\end{itemize}\n",
        )

    def test_many_items_keep_source_order(self):
        self.assertEqual(
            self.itemize(["A", "B", "C"]),
            "\\begin{itemize}\n"
            "\\item\nA\n\\item\nB\n\\item\nC\n"
            "\\end{itemize}\n",
        )

    def test_multiline_item_stays_one_value(self):
        source = ITEMS + "!items_simple:\n    - line 1\n      line 2\n    - tail\n"
        self.assertEqual(
            compile_text(source),
            "\\begin{itemize}\n"
            "\\item\nline 1\nline 2\n\\item\ntail\n"
            "\\end{itemize}\n",
        )

    def test_nested_each_binds_each_level(self):
        source = """!defmacro{grid}{...rows}: |
    @table: |
        !each{rows}{row}: |
            @row >> !param{row}
!grid:
    - A
    - B
"""
        self.assertEqual(
            compile_text(source),
            "\\begin{table}\n"
            "\\begin{row}\nA\n\\end{row}\n"
            "\\begin{row}\nB\n\\end{row}\n"
            "\\end{table}\n",
        )

    def test_each_requires_a_rest_parameter_and_a_block_suite(self):
        cases = {
            "!defmacro{foo}{x}: |\n    !each{x}{i}: |\n        !param{i}\n"
            "!foo: |\n    A\n": "is not a rest parameter",
            "!defmacro{foo}{x}: |\n    !each{zz}{i}: |\n        !param{i}\n"
            "!foo: |\n    A\n": "unknown macro parameter 'zz'",
        }
        for source, message in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(MacroExpansionError, message):
                    compile_text(source, filename="m.tfx")

        with self.assertRaisesRegex(ValidationError, r"': \|' template suite"):
            compile_text(
                "!defmacro{foo}{...r}: |\n    !each{r}{i}:\n        - A\n"
                "!foo: |\n    A\n",
                filename="m.tfx",
            )

    def test_each_item_must_not_shadow_a_bound_parameter(self):
        source = """!defmacro{foo}{x}{...r}: |
    !each{r}{x}: |
        !param{x}
!foo:
    - A
    - B
"""
        with self.assertRaisesRegex(MacroExpansionError, "shadows a bound"):
            compile_text(source, filename="m.tfx")


class MacroCompositionTests(unittest.TestCase):
    def test_forward_reference_expands(self):
        source = (
            "!foo >> \\TextCA{A}\n"
            "!defmacro{foo}{body}: |\n    @{\\small} >> !param{body}\n"
        )
        self.assertEqual(
            compile_text(source),
            "{\n\\small\n\\TextCA{A}\n}\n",
        )

    def test_a_macro_template_may_call_another_macro(self):
        source = """!defmacro{emph}{body}: |
    @{\\bfseries} >> !param{body}
!defmacro{warning}{body}: |
    @{\\color{red}} >> !emph >> !param{body}
!warning: |
    Careful
"""
        self.assertEqual(
            compile_text(source),
            "{\n\\color{red}\n{\n\\bfseries\nCareful\n}\n}\n",
        )

    def test_direct_recursion_is_a_deterministic_error(self):
        source = (
            "!defmacro{foo}{x}: |\n    !foo: |\n        !param{x}\n"
            "!foo: |\n    A\n"
        )
        with self.assertRaisesRegex(
            MacroExpansionError,
            "recursive macro expansion detected: foo -> foo",
        ):
            compile_text(source, filename="m.tfx")

    def test_indirect_recursion_reports_the_whole_chain(self):
        source = (
            "!defmacro{foo}{x}: |\n    !bar: |\n        !param{x}\n"
            "!defmacro{bar}{x}: |\n    !baz: |\n        !param{x}\n"
            "!defmacro{baz}{x}: |\n    !foo: |\n        !param{x}\n"
            "!foo: |\n    A\n"
        )
        with self.assertRaisesRegex(
            MacroExpansionError,
            "recursive macro expansion detected: foo -> bar -> baz -> foo",
        ):
            compile_text(source, filename="m.tfx")


class MacroNamespaceTests(unittest.TestCase):
    def test_builtin_and_reserved_names_are_protected(self):
        cases = {
            "!defmacro{items}{x}: |\n    A\n": "built-in special",
            "!defmacro{vpad}{x}: |\n    A\n": "built-in special",
            "!defmacro{defmacro}{x}: |\n    A\n": "reserved by TeXFlux",
            "!defmacro{param}{x}: |\n    A\n": "reserved by TeXFlux",
            "!defmacro{each}{x}: |\n    A\n": "reserved by TeXFlux",
        }
        for source, message in cases.items():
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValidationError, message):
                    compile_text(source, filename="m.tfx")

    def test_duplicate_macro_definitions_are_rejected(self):
        source = "!defmacro{foo}{x}: |\n    A\n!defmacro{foo}{y}: |\n    B\n"
        with self.assertRaisesRegex(ValidationError, "already defined at m.tfx:1:1"):
            compile_text(source, filename="m.tfx")

    def test_builtin_specials_still_work_and_unknown_ones_still_fail(self):
        self.assertEqual(
            compile_text("!items:\n    - A\n"),
            "\\begin{itemize}\n\\item A\n\\end{itemize}\n",
        )
        with self.assertRaisesRegex(Exception, "unknown special directive"):
            compile_text("!nope: |\n    A\n")

    def test_template_constructs_are_invalid_outside_a_template(self):
        with self.assertRaisesRegex(MacroExpansionError, "!param is only valid"):
            compile_text("!param{x}\n", filename="m.tfx")
        with self.assertRaisesRegex(MacroExpansionError, "!each is only valid"):
            compile_text("!each{a}{b}: |\n    X\n", filename="m.tfx")

    def test_unknown_parameter_names_the_definition_and_the_call(self):
        source = "!defmacro{foo}{x}: |\n    !param{nope}\n!foo: |\n    A\n"
        with self.assertRaisesRegex(
            MacroExpansionError,
            r"m\.tfx:2:5: macro error: unknown macro parameter 'nope'; "
            r"while expanding 'foo' called at m\.tfx:3:1",
        ):
            compile_text(source, filename="m.tfx")


class MacroSourceMapTests(unittest.TestCase):
    SOURCE = (
        "!defmacro{smallred}{body}: |\n"
        "    @{\\small\\color{red}} >> !param{body}\n"
        "!smallred: |\n"
        "    Hello\n"
    )

    def fragments(self):
        result = compile_with_map(self.SOURCE, filename="m.tfx")
        return result, {
            fragment.text: fragment.source
            for fragment in result.rendered.fragments
            if fragment.source is not None
        }

    def test_param_output_maps_to_the_call_site_value(self):
        _, sources = self.fragments()

        # 'Hello' is written on line 4, inside the call's own suite.
        self.assertEqual(sources["Hello"].start.line, 4)
        self.assertEqual(sources["Hello"].start.column, 5)

    def test_template_scaffolding_maps_to_the_macro_invocation(self):
        _, sources = self.fragments()

        # The braces and the group header come from the definition on line 2,
        # but inverse search must land on the call on line 3.
        for text in ("{", "}", "\\small\\color{red}"):
            with self.subTest(text=text):
                self.assertEqual(sources[text].start.line, 3)
                self.assertEqual(sources[text].start.column, 1)

    def test_every_fragment_still_points_at_the_compiled_file(self):
        result, sources = self.fragments()

        self.assertTrue(all(span.file == "m.tfx" for span in sources.values()))
        self.assertEqual(result.text, "{\n\\small\\color{red}\nHello\n}\n")


class MacroFormTests(unittest.TestCase):
    """A template construct needs a ': |' suite that is actually written."""

    def test_each_may_compose_when_it_writes_its_own_suite(self):
        # The rightmost '>>' segment keeps the stack's own suffix, so this
        # '!each' owns a real template and wraps every iteration at once.
        source = (
            "!defmacro{m}{...r}: |\n"
            "    @{\\bfseries} >> !each{r}{i}: |\n"
            "        \\item\n"
            "        !param{i}\n"
            "!m{A}{B}\n"
        )
        self.assertEqual(
            compile_text(source),
            "{\n\\bfseries\n\\item\nA\n\\item\nB\n}\n",
        )

    def test_each_without_a_written_block_suite_is_rejected(self):
        cases = (
            # not the rightmost segment, so its suite would be synthetic
            "!defmacro{m}{...r}: |\n    !each{r}{i} >> \\R >> !param{i}\n"
            "!m:\n    - A\n",
            # rightmost, but the stack carries no suffix at all
            "!defmacro{m}{...r}: |\n    @{\\bf} >> !each{r}{i}\n!m:\n    - A\n",
            # rightmost, but a sequence suffix is not a template
            "!defmacro{m}{...r}: |\n    @{\\bf} >> !each{r}{i}:\n        - X\n"
            "!m:\n    - A\n",
        )
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ValidationError,
                    r"!each requires a ': \|' template suite of its own",
                ):
                    compile_text(source, filename="m.tfx")

    def test_a_definition_can_never_be_composed(self):
        for source in (
            "!defmacro{a} >> \\foo{x}\n\n!a\n",
            "\\foo >> !defmacro{a}: |\n    x\n",
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ValidationError,
                    "!defmacro must be a top-level ': \\|' definition",
                ):
                    compile_text(source, filename="m.tfx")

    def test_param_may_still_be_a_stack_segment(self):
        self.assertEqual(
            compile_text(WRAPPER + "!smallred >> \\X\n"),
            "{\n\\small\\color{red}\n\\X\n}\n",
        )

    def test_a_nested_definition_is_rejected_even_when_never_called(self):
        source = "!defmacro{m}: |\n    !defmacro{n}: |\n        x\n\nplain\n"
        with self.assertRaisesRegex(
            ValidationError,
            r"m\.tfx:2:5: validation error: !defmacro is only valid at the top level",
        ):
            compile_text(source, filename="m.tfx")


class MacroSourceMapArtifactTests(TempDirTestCase):
    """A macro document's .tfxmap must survive the remapper's validation."""

    # A retargeted template span is shorter than the raw item text it covers,
    # which is what once produced reversed source ranges.
    SOURCES = (
        "!defmacro{d}: |\n    !items:\n        - a\n            - b\n\n!d\n",
        "!defmacro{d}: |\n    !items:\n        -\n            multi\n"
        "            line\n\n!d\n",
        "!defmacro{d}: |\n    !items:\n        - a\n          cont\n\n!d\n",
        ITEMS + "!items_simple:\n    - A\n    - B\n",
    )

    def test_macro_maps_have_no_reversed_source_ranges(self):
        for source in self.SOURCES:
            with self.subTest(source=source):
                _, result = self.compile_to_disk(source)
                for fragment in result.rendered.fragments:
                    if fragment.source is not None:
                        self.assertLessEqual(
                            fragment.source.start,
                            fragment.source.end,
                        )

    def test_macro_maps_load_back_through_the_remapper(self):
        for source in self.SOURCES:
            with self.subTest(source=source):
                map_path, _ = self.compile_to_disk(source)
                self.assertTrue(load_source_map(map_path).mappings)

    def test_items_inside_a_template_maps_onto_the_call_site(self):
        source = "!defmacro{d}: |\n    !items:\n        - a\n            - b\n\n!d\n"
        result = compile_with_map(source, filename="m.tfx")

        # Every itemize fragment comes from the template, so line 6 ('!d').
        lines = {
            fragment.source.start.line
            for fragment in result.rendered.fragments
            if fragment.source is not None and "item" in fragment.text
        }
        self.assertEqual(lines, {6})


class MacroAcceptanceTests(unittest.TestCase):
    def test_a_user_macro_can_build_a_simple_itemize(self):
        macro = compile_text(ITEMS + "!items_simple:\n    - A\n    - B\n")
        builtin = compile_text("!items:\n    - A\n    - B\n")

        self.assertEqual(
            macro,
            "\\begin{itemize}\n\\item\nA\n\\item\nB\n\\end{itemize}\n",
        )
        # The built-in keeps its own richer item layout and is not replaced.
        self.assertEqual(
            builtin,
            "\\begin{itemize}\n\\item A\n\\item B\n\\end{itemize}\n",
        )


if __name__ == "__main__":
    unittest.main()
