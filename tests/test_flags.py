import unittest

from texflux import (
    FlagError,
    ValidationError,
    collect_flags,
    compile_text,
    compile_with_map,
    desugar,
    load_source_map,
    parse,
)

from .support import TempDirTestCase


DECLARED = "!flag{draft}{off}\n!flag{notes}{on}\n\n"


def collect(source, overrides=None, filename="f.tfx"):
    return collect_flags(desugar(parse(source, filename)), overrides)


class FlagDeclarationTests(unittest.TestCase):
    def test_declarations_emit_no_tex_and_are_collected(self):
        document, flags = collect(DECLARED + "\\body\n")

        self.assertEqual(flags, {"draft": False, "notes": True})
        self.assertEqual(compile_text(DECLARED + "\\body\n"), "\n\\body\n")

    def test_a_default_of_on_needs_no_override(self):
        source = "!flag{notes}{on}\n!when{notes} >> \\note\n"

        self.assertEqual(compile_text(source), "\\note\n")

    def test_value_group_is_required(self):
        with self.assertRaises(ValidationError) as caught:
            collect("!flag{draft}\n")

        self.assertIn("'on' or 'off'", str(caught.exception))

    def test_value_must_be_on_or_off(self):
        with self.assertRaises(ValidationError) as caught:
            collect("!flag{draft}{yes}\n")

        self.assertIn("yes", str(caught.exception))

    def test_flag_name_must_be_an_identifier(self):
        with self.assertRaises(ValidationError) as caught:
            collect("!flag{2draft}{off}\n")

        self.assertIn("2draft", str(caught.exception))

    def test_duplicate_declaration_names_the_first_one(self):
        source = "!flag{draft}{off}\n!flag{draft}{on}\n"

        with self.assertRaises(ValidationError) as caught:
            collect(source)

        self.assertIn("f.tfx:1:1", str(caught.exception))

    def test_declaration_below_the_top_level_is_rejected(self):
        source = "@frame:\n    !flag{draft}{off}\n"

        with self.assertRaises(ValidationError) as caught:
            collect(source)

        self.assertIn("top level", str(caught.exception))

    def test_declaration_cannot_be_a_stack_segment(self):
        with self.assertRaises(ValidationError) as caught:
            compile_text("!flag{draft}{off} >> \\foo\n")

        self.assertIn("'>>' segment", str(caught.exception))

    def test_declaration_accepts_no_suite(self):
        with self.assertRaises(ValidationError) as caught:
            collect("!flag{draft}{off}:\n    body\n")

        self.assertIn("suite", str(caught.exception))


class ConditionalTests(unittest.TestCase):
    def compile(self, body, **overrides):
        return compile_text(DECLARED + body, flags=overrides or None)

    def test_when_keeps_its_payload_only_while_the_flag_is_on(self):
        body = "!when{draft} >> \\todo{check}\n"

        self.assertEqual(self.compile(body), "\n")
        self.assertEqual(self.compile(body, draft=True), "\n\\todo{check}\n")

    def test_unless_is_the_exact_inverse(self):
        body = "!unless{draft} >> \\todo{check}\n"

        self.assertEqual(self.compile(body), "\n\\todo{check}\n")
        self.assertEqual(self.compile(body, draft=True), "\n")

    def test_a_block_suite_splices_every_node_it_holds(self):
        body = "!when{notes}:\n    \\one\n    \\two\n"

        self.assertEqual(self.compile(body), "\n\\one\n\\two\n")
        self.assertEqual(self.compile(body, notes=False), "\n")

    def test_a_dropped_block_leaves_no_brace_group_behind(self):
        body = "@frame:\n    a\n    !when{draft}:\n        b\n    c\n"

        self.assertEqual(
            self.compile(body),
            "\n\\begin{frame}\na\nc\n\\end{frame}\n",
        )

    def test_nesting_composes_as_a_conjunction(self):
        body = "!when{notes} >> !unless{draft} >> \\both\n"

        self.assertEqual(self.compile(body), "\n\\both\n")
        self.assertEqual(self.compile(body, draft=True), "\n")
        self.assertEqual(self.compile(body, notes=False), "\n")

    def test_conditionals_work_inside_a_macro_template(self):
        source = (
            DECLARED
            + "!defmacro{point}{body}:\n"
            + "    !param{body}\n"
            + "    !when{draft} >> \\marginpar{TODO}\n"
            + "\n!point:\n    text\n"
        )

        self.assertEqual(compile_text(source), "\n\ntext\n")
        self.assertEqual(
            compile_text(source, flags={"draft": True}),
            "\n\ntext\n\\marginpar{TODO}\n",
        )

    def test_an_unknown_flag_is_an_error_rather_than_silent_removal(self):
        with self.assertRaises(ValidationError) as caught:
            self.compile("!when{drfat} >> \\todo\n")

        self.assertIn("drfat", str(caught.exception))

    def test_a_conditional_needs_a_payload(self):
        with self.assertRaises(ValidationError) as caught:
            self.compile("!when{draft}\n")

        self.assertIn("!when", str(caught.exception))

    def test_a_sequence_suite_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            self.compile("!when{draft}::\n    - a\n    - b\n")

        self.assertIn("sequence", str(caught.exception))

    def test_a_conditional_needs_at_least_one_flag(self):
        with self.assertRaises(ValidationError) as caught:
            self.compile("!when >> \\foo\n")

        self.assertIn("at least one", str(caught.exception))


    def test_when_and_unless_cannot_be_redefined_as_macros(self):
        for name in ("flag", "when", "unless"):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as caught:
                    compile_text(f"!defmacro{{{name}}}:\n    x\n")

                self.assertIn("reserved", str(caught.exception))

    def test_a_dropped_branch_is_not_checked(self):
        # The author disabled this branch; its macro call is never bound.
        body = "!when{draft}:\n    !nosuchmacro{a}\n"

        self.assertEqual(self.compile(body), "\n")

    def test_a_dropped_branch_still_cannot_hide_a_misplaced_declaration(self):
        # Flags are collected before any conditional is resolved, so this one
        # structural rule outlives the branch that would have dropped it.
        body = "!when{draft}:\n    !flag{extra}{on}\n"

        with self.assertRaises(ValidationError) as caught:
            self.compile(body)

        self.assertIn("top level", str(caught.exception))

    def test_a_conditional_value_empties_its_argument_rather_than_removing_it(self):
        # !when drops statements, not arguments: the '-' entry still exists.
        body = "\\cmd::\n    - !when{draft} >> \\x\n    - tail\n"

        self.assertEqual(self.compile(body), "\n\\cmd{}{tail}\n")


class CombinatorTests(unittest.TestCase):
    """``[and]`` and ``[or]`` fold several flags into one answer."""

    DECLARED = "!flag{a}{off}\n!flag{b}{off}\n!flag{c}{off}\n"

    def keeps(self, header, **overrides):
        source = f"{self.DECLARED}{header} >> \\x\n"
        return compile_text(source, flags=overrides or None) == "\\x\n"

    def test_and_keeps_its_payload_only_when_every_flag_is_on(self):
        for on, expected in (
            ({}, False),
            ({"a": True}, False),
            ({"b": True}, False),
            ({"a": True, "b": True}, True),
        ):
            with self.subTest(on=on):
                self.assertIs(self.keeps("!when[and]{a}{b}", **on), expected)

    def test_or_keeps_its_payload_when_any_flag_is_on(self):
        for on, expected in (
            ({}, False),
            ({"a": True}, True),
            ({"b": True}, True),
            ({"a": True, "b": True}, True),
        ):
            with self.subTest(on=on):
                self.assertIs(self.keeps("!when[or]{a}{b}", **on), expected)

    def test_unless_negates_the_whole_header_rather_than_each_flag(self):
        # !unless[X] keeps exactly when !when[X] would drop, so 'neither' is
        # spelled with [or] and 'not both' with [and].
        for on, neither, not_both in (
            ({}, True, True),
            ({"a": True}, False, True),
            ({"a": True, "b": True}, False, False),
        ):
            with self.subTest(on=on):
                self.assertIs(self.keeps("!unless[or]{a}{b}", **on), neither)
                self.assertIs(self.keeps("!unless[and]{a}{b}", **on), not_both)

    def test_a_fold_takes_any_number_of_flags(self):
        self.assertFalse(self.keeps("!when[and]{a}{b}{c}", a=True, b=True))
        self.assertTrue(self.keeps("!when[and]{a}{b}{c}", a=True, b=True, c=True))
        self.assertTrue(self.keeps("!when[or]{a}{b}{c}", c=True))

    def test_a_modifier_on_a_single_flag_is_the_flag_itself(self):
        self.assertTrue(self.keeps("!when[and]{a}", a=True))
        self.assertTrue(self.keeps("!when[or]{a}", a=True))
        self.assertFalse(self.keeps("!when[or]{a}"))

    def test_several_flags_need_an_explicit_modifier(self):
        with self.assertRaises(ValidationError) as caught:
            self.keeps("!when{a}{b}")

        self.assertIn("[and]", str(caught.exception))

    def test_the_modifier_must_be_and_or_or(self):
        with self.assertRaises(ValidationError) as caught:
            self.keeps("!when[xor]{a}{b}")

        self.assertIn("[xor]", str(caught.exception))

    def test_a_modifier_is_only_read_in_front(self):
        with self.assertRaises(ValidationError) as caught:
            self.keeps("!when{a}[or]{b}")

        self.assertIn("required '{...}' group", str(caught.exception))

    def test_listing_one_flag_twice_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            self.keeps("!when[or]{a}{a}")

        self.assertIn("listed twice", str(caught.exception))

    def test_every_flag_in_a_fold_must_be_declared(self):
        with self.assertRaises(ValidationError) as caught:
            self.keeps("!when[or]{a}{bb}")

        self.assertIn("bb", str(caught.exception))

    def test_a_fold_composes_with_the_other_conditional(self):
        source = (
            self.DECLARED
            + "!when[or]{a}{b} >> !unless{c} >> \\x\n"
        )
        self.assertEqual(compile_text(source, flags={"a": True}), "\\x\n")
        self.assertEqual(
            compile_text(source, flags={"a": True, "c": True}), "\n"
        )


class FlagOverrideTests(unittest.TestCase):
    def test_an_override_replaces_the_declared_default_either_way(self):
        _, flags = collect(DECLARED, {"draft": True, "notes": False})

        self.assertEqual(flags, {"draft": True, "notes": False})

    def test_an_override_naming_no_declaration_is_rejected(self):
        with self.assertRaises(FlagError) as caught:
            collect(DECLARED, {"drfat": True})

        message = str(caught.exception)
        self.assertIn("drfat", message)
        self.assertIn("draft", message)

    def test_an_override_value_must_be_a_real_bool(self):
        # 'off' and 0 would otherwise fold as truthy or falsy by accident and
        # silently build the opposite document.
        for value in ("off", "on", 0, 1, None):
            with self.subTest(value=value):
                with self.assertRaises(FlagError) as caught:
                    collect(DECLARED, {"draft": value})

                self.assertIn("True or False", str(caught.exception))

    def test_an_override_on_a_document_without_declarations_is_rejected(self):
        with self.assertRaises(FlagError) as caught:
            collect("\\body\n", {"draft": True})

        self.assertIn("no flags", str(caught.exception))


class FlagSourceMapTests(TempDirTestCase):
    """Dropping content must not disturb the spans of what survives."""

    SOURCE = (
        "!flag{draft}{off}\n"
        "\\keep{one}\n"
        "!when{draft}:\n"
        "    \\gone\n"
        "    \\also{gone}\n"
        "\\keep{two}\n"
    )

    def test_surviving_lines_still_map_to_their_own_source_lines(self):
        result = compile_with_map(self.SOURCE, filename="f.tfx")

        self.assertEqual(result.text, "\\keep{one}\n\\keep{two}\n")
        lines = {
            fragment.text: fragment.source.start.line
            for fragment in result.rendered.fragments
            if fragment.source is not None
        }
        self.assertEqual(lines["\\keep{one}"], 2)
        self.assertEqual(lines["\\keep{two}"], 6)

    def test_the_written_map_loads_back_through_the_remapper(self):
        map_path, result = self.compile_to_disk(self.SOURCE)

        self.assertTrue(load_source_map(map_path).mappings)
        for fragment in result.rendered.fragments:
            if fragment.source is not None:
                self.assertLessEqual(fragment.source.start, fragment.source.end)


if __name__ == "__main__":
    unittest.main()
