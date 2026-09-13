"""Module system: sources, macro modules, content imports and bindings."""

import unittest

from texflux import (
    BUILTIN_DIRECTIVES,
    ParseError,
    compile_with_map,
    MacroExpansionError,
    ModuleError,
    ValidationError,
    collect_macros,
    compile_text,
    desugar,
    expand_macros,
    normalize,
    parse,
    render,
)

from .support import TempDirTestCase


def collect(source, filename, module):
    """Collect one module's own macros, the way the session does."""

    return collect_macros(
        desugar(parse(source, filename)),
        BUILTIN_DIRECTIVES,
        module=module,
    )


class InterpolationModuleTests(TempDirTestCase):
    def test_nested_macro_text_preserves_transitive_fragment_sources(self):
        self.write("inner.tfxm", "!defmacro{inner}{id}: |\n    \\label{!text{id}}\n")
        self.write("outer.tfxm", "!macroimport{inner.tfxm}\n"
                   "!defmacro{outer}{prefix}{id}: |\n    !inner{!text{prefix}-!text{id}}\n")
        source = "!macroimport{outer.tfxm}\n!outer:\n    - sec\n    - intro\n"
        root = self.write("main.tfx", source)
        result = compile_with_map(source, filename=str(root))
        self.assertEqual(result.text, "\\label{sec-intro}\n")
        fragments = {f.text: f for f in result.rendered.fragments if f.source is not None}
        self.assertEqual(fragments["sec"].source.start.line, 3)
        self.assertEqual(fragments["intro"].source.start.line, 4)
        self.assertEqual(fragments["-"].source.start.line, 2)
        self.assertEqual(fragments["-"].role, "scaffold")
        self.assertTrue(all(f.source.file == str(root) for f in fragments.values()))
        self.assertEqual(result.text, "".join(f.text for f in result.rendered.fragments))

    def test_ast_text_in_macro_module_gets_template_diagnostic(self):
        self.write("style.tfxm", "!defmacro{m}{x}: |\n    !text{x}\n")
        with self.assertRaisesRegex(MacroExpansionError, "!text is only valid inside a textual field"):
            compile_text("!macroimport{style.tfxm}\n!m{A}\n", filename=str(self.root / "main.tfx"))

class GuardTests(unittest.TestCase):
    """The import-free pipeline names the module constructs it cannot run."""

    def test_module_constructs_require_module_compilation(self):
        # normalize() has no source file to resolve an import against, so a
        # caller that bypasses the session is told so by name.
        for source in ("!import{a.tfx}\n", "!macroimport{a.tfxm}\n"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    ModuleError,
                    "requires module compilation",
                ):
                    normalize(parse(source, "x.tfx"))

    def test_module_construct_names_are_reserved_against_defmacro(self):
        for name in ("import", "macroimport"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    ValidationError,
                    "cannot be redefined",
                ):
                    compile_text(
                        "!defmacro{%s}: |\n    A\n" % name,
                        filename="x.tfx",
                    )


class LexicalScopeTests(unittest.TestCase):
    """A macro's template resolves names in its own defining module."""

    CORE = "!defmacro{wrapper}{body}: |\n    @{\\small} >> !param{body}\n"
    OUTER = "!defmacro{foo}{body}: |\n    !wrapper: |\n        !param{body}\n"

    def environments(self):
        _, core = collect(self.CORE, "core.tfxm", "/core.tfxm")
        _, outer = collect(self.OUTER, "a.tfxm", "/a.tfxm")
        return {
            "/core.tfxm": core,
            # A.tfxm sees core's public macros; nobody else does.
            "/a.tfxm": {**core, **outer},
        }, outer

    def test_a_template_sees_its_own_private_imports(self):
        environments, outer = self.environments()
        document = desugar(parse("!foo: |\n    Hello\n", "main.tfx"))
        # main.tfx sees !foo only, never core.tfxm's !wrapper.
        environments["/main.tfx"] = dict(outer)
        expanded = expand_macros(
            document,
            environments["/main.tfx"],
            {},
            environments=environments,
            module="/main.tfx",
        )
        self.assertIn("\\small", render(normalize(expanded)))

    def test_a_caller_does_not_see_a_private_import(self):
        environments, outer = self.environments()
        environments["/main.tfx"] = dict(outer)
        document = desugar(parse("!wrapper: |\n    Hello\n", "main.tfx"))
        expanded = expand_macros(
            document,
            environments["/main.tfx"],
            {},
            environments=environments,
            module="/main.tfx",
        )
        with self.assertRaisesRegex(Exception, "unknown special directive"):
            normalize(expanded)

    def test_same_name_in_two_modules_is_not_recursion(self):
        _, inner = collect(
            "!defmacro{same}{b}: |\n    \\inner{!text{b}}\n",
            "b.tfxm",
            "/b.tfxm",
        )
        _, outer = collect(
            "!defmacro{same}{b}: |\n    !same: |\n        !param{b}\n",
            "a.tfxm",
            "/a.tfxm",
        )
        environments = {
            "/b.tfxm": inner,
            # A.tfxm's '!same' body calls B.tfxm's '!same', not itself.
            "/a.tfxm": dict(inner),
            "/main.tfx": dict(outer),
        }
        document = desugar(parse("!same: |\n    X\n", "main.tfx"))
        expanded = expand_macros(
            document,
            outer,
            {},
            environments=environments,
            module="/main.tfx",
        )
        self.assertEqual("\\inner{X}\n", render(normalize(expanded)))

    def test_recursion_across_modules_names_each_defining_file(self):
        _, inner = collect(
            "!defmacro{same}{b}: |\n    !same: |\n        !param{b}\n",
            "b.tfxm",
            "/b.tfxm",
        )
        _, outer = collect(
            "!defmacro{same}{b}: |\n    !same: |\n        !param{b}\n",
            "a.tfxm",
            "/a.tfxm",
        )
        environments = {
            "/b.tfxm": dict(outer),
            "/a.tfxm": dict(inner),
            "/main.tfx": dict(outer),
        }
        document = desugar(parse("!same: |\n    X\n", "main.tfx"))
        with self.assertRaisesRegex(MacroExpansionError, "same@a.tfxm -> same@b.tfxm"):
            expand_macros(
                document,
                outer,
                {},
                environments=environments,
                module="/main.tfx",
            )

    def test_an_unregistered_module_is_reported_before_it_misleads(self):
        # A template only names a special sometimes, so a missing environment
        # has to fail on the lookup rather than on the first call that needs it.
        document, macros = collect(
            "!defmacro{m}: |\n    !vpad{1em}: |\n        A\n!m\n",
            "main.tfx",
            "",
        )
        with self.assertRaisesRegex(TypeError, "no macro environment"):
            expand_macros(
                document,
                macros,
                {},
                environments={"/main.tfx": macros},
                module="/main.tfx",
            )

    def test_single_module_recursion_keeps_its_plain_chain(self):
        with self.assertRaisesRegex(
            MacroExpansionError,
            "recursive macro expansion detected: foo -> foo",
        ):
            compile_text(
                "!defmacro{foo}: |\n    !foo\n!foo\n",
                filename="x.tfx",
            )


class TemplateContentTests(unittest.TestCase):
    def test_imports_are_rejected_inside_a_macro_template(self):
        for name in ("import", "macroimport"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    ValidationError,
                    f"!{name} is not allowed inside a macro template",
                ):
                    collect(
                        "!defmacro{m}: |\n    !%s{a.tfx}\n" % name,
                        "x.tfx",
                        "/x.tfx",
                    )


class ModuleTestCase(TempDirTestCase):
    """Compile a .tfx that lives on disk beside the modules it imports."""

    def build(self, name="main.tfx", **kwargs):
        path = self.root / name
        return compile_text(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            **kwargs,
        )

    def failure(self, name="main.tfx", **kwargs):
        with self.assertRaises(ModuleError) as caught:
            self.build(name, **kwargs)
        return caught.exception


class PathResolutionTests(ModuleTestCase):
    def test_paths_resolve_against_the_file_that_writes_them(self):
        (self.root / "archive").mkdir()
        self.write("archive/macros.tfxm", "!defmacro{deep}: |\n    DEEP\n")
        self.write(
            "archive/slide.tfx",
            "!macroimport{macros.tfxm}\n\n!deep\n",
        )
        # A sibling of main.tfx by the same name must not win.
        self.write("macros.tfxm", "!defmacro{deep}: |\n    SHALLOW\n")
        self.write("main.tfx", "!import{archive/slide.tfx}\n")
        self.assertIn("DEEP", self.build())

    def test_parent_relative_paths_resolve(self):
        (self.root / "part").mkdir()
        self.write("shared.tfxm", "!defmacro{up}: |\n    UP\n")
        self.write("part/a.tfx", "!macroimport{../shared.tfxm}\n\n!up\n")
        self.write("main.tfx", "!import{part/a.tfx}\n")
        self.assertIn("UP", self.build())

    def test_malformed_paths_are_rejected(self):
        for written, message in (
            ("", "must not be empty"),
            ("a\\b.tfx", "'/' separators"),
            ("/abs/a.tfx", "must be relative"),
            ("a.tfxm", "requires a '.tfx' module"),
            ("a.txt", "requires a '.tfx' module"),
            ("missing.tfx", "cannot read module"),
            # A NUL byte makes a path the OS refuses to look at at all, and
            # it raises ValueError rather than OSError when it does.
            ("a\x00b.tfx", "must not contain a NUL character"),
        ):
            with self.subTest(written=written):
                self.write("main.tfx", "!import{%s}\n" % written)
                self.assertIn(message, self.failure().message)

    def test_both_constructs_reject_the_same_malformed_paths(self):
        # Path shape is checked before either construct touches the file
        # system, so neither can leak an unspanned error the other catches.
        self.write("a.tfxm", "!defmacro{m}: |\n    M\n")
        for source, message in (
            ("!macroimport{a\x00b.tfxm}\n", "must not contain a NUL character"),
            ("!macroimport{}\n", "must not be empty"),
            ("!macroimport{/abs/a.tfxm}\n", "must be relative"),
            ("!macroimport{a\\\\b.tfxm}\n", "'/' separators"),
        ):
            with self.subTest(source=source):
                self.write("main.tfx", source)
                self.assertIn(message, self.failure().message)

    def test_macro_import_requires_a_tfxm_path(self):
        self.write("a.tfx", "A\n")
        self.write("main.tfx", "!macroimport{a.tfx}\n")
        self.assertIn("requires a '.tfxm' module", self.failure().message)

    def test_one_file_reached_twice_keeps_its_first_spelling(self):
        (self.root / "sub").mkdir()
        self.write("sub/leaf.tfx", "LEAF\n")
        self.write("sub/mid.tfx", "!import{leaf.tfx}\n")
        self.write("main.tfx", "!import{sub/mid.tfx}\n!import{sub/leaf.tfx}\n")
        path = self.root / "main.tfx"
        result = compile_with_map(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        leaves = [s.file for s in result.sources if s.file.endswith("leaf.tfx")]
        self.assertEqual(len(leaves), 1)


class ImportFormTests(ModuleTestCase):
    def setUp(self):
        super().setUp()
        self.write("a.tfx", "A\n")

    def test_import_never_owns_a_suite_or_wraps_a_payload(self):
        for source in (
            "!import{a.tfx}: |\n    X\n",
            "!import{a.tfx}:\n    - X\n",
            "!import{a.tfx} >> @center\n",
        ):
            with self.subTest(source=source):
                self.write("main.tfx", source)
                self.assertIn(
                    "does not accept a suite",
                    self.failure().message,
                )

    def test_import_takes_one_path_group(self):
        for source, message in (
            ("!import\n", "exactly one '{path}' group"),
            ("!import{a.tfx}{b.tfx}\n", "exactly one '{path}' group"),
            ("!import[x]{a.tfx}\n", "does not accept '[...]'"),
            ("!import<2>{a.tfx}\n", "does not accept '[...]'"),
        ):
            with self.subTest(source=source):
                self.write("main.tfx", source)
                self.assertIn(message, self.failure().message)

    def test_import_composes_as_a_closed_value(self):
        self.write("main.tfx", "@center >> !import{a.tfx}\n")
        self.assertEqual(
            self.build(),
            "\\begin{center}\nA\n\\end{center}\n",
        )
        self.write("main.tfx", "@{} >> !import{a.tfx}\n")
        self.assertEqual(self.build(), "{\nA\n}\n")

    def test_import_is_valid_in_a_value_position(self):
        self.write("main.tfx", "\\pair:\n    - !import{a.tfx}\n    - tail\n")
        # One imported line is a one-line value, so its braces hug it.
        self.assertEqual(self.build(), "\\pair{A}{tail}\n")


class BindingTests(ModuleTestCase):
    QUIZ = (
        "!flag{answers}{off}\n"
        "\n"
        "Q\n"
        "!when{answers}: |\n"
        "    A\n"
    )

    def setUp(self):
        super().setUp()
        self.write("quiz.tfx", self.QUIZ)

    def test_an_unbound_flag_keeps_the_callee_default(self):
        self.write("main.tfx", "!import{quiz.tfx}\n")
        self.assertEqual(self.build(), "\nQ\n")

    def test_a_literal_binding_overrides_the_callee_default(self):
        self.write("main.tfx", "!import{quiz.tfx}(answers=on)\n")
        self.assertEqual(self.build(), "\nQ\nA\n")

    def test_a_caller_flag_is_forwarded_only_when_written(self):
        self.write(
            "main.tfx",
            "!flag{answers}{on}\n\n!import{quiz.tfx}\n",
        )
        # No same-name inheritance: the callee keeps its own default.
        self.assertEqual(self.build(), "\n\nQ\n")
        self.write(
            "main.tfx",
            "!flag{answers}{on}\n\n!import{quiz.tfx}(answers=$answers)\n",
        )
        self.assertEqual(self.build(), "\n\nQ\nA\n")

    def test_one_module_imported_twice_is_two_instances(self):
        self.write(
            "main.tfx",
            "!import{quiz.tfx}(answers=off)\n!import{quiz.tfx}(answers=on)\n",
        )
        self.assertEqual(self.build(), "\nQ\n\nQ\nA\n")

    def test_overrides_reach_the_root_module_only(self):
        self.write(
            "main.tfx",
            "!flag{answers}{off}\n\n!import{quiz.tfx}\n!when{answers} >> \\Root\n",
        )
        self.assertNotIn("A\n", self.build(flags={"answers": True}))

    def test_binding_errors_name_the_binding(self):
        for bindings, message, column in (
            ("()", "binding list is empty", 18),
            ("(answers)", "bindings are written", 19),
            ("(answers=maybe)", "bindings are written", 19),
            ("(answers=on,)", "bindings are written", 30),
            ("(nosuch=on)", "does not declare build flag 'nosuch'", 19),
            ("(answers=on, answers=off)", "is bound twice", 31),
            ("(answers=$gone)", "unknown build flag 'gone'", 27),
        ):
            with self.subTest(bindings=bindings):
                self.write("main.tfx", "!import{quiz.tfx}%s\n" % bindings)
                error = self.failure()
                self.assertIn(message, error.message)
                self.assertEqual(error.span.start.column, column)


class CycleTests(ModuleTestCase):
    def test_a_direct_content_cycle_is_an_error(self):
        self.write("main.tfx", "!import{b.tfx}\n")
        self.write("b.tfx", "!import{main.tfx}\n")
        self.assertIn("content import cycle", self.failure().message)

    def test_an_indirect_content_cycle_names_its_path(self):
        self.write("main.tfx", "!import{b.tfx}\n")
        self.write("b.tfx", "!import{c.tfx}\n")
        self.write("c.tfx", "!import{b.tfx}\n")
        message = self.failure().message
        self.assertIn("content import cycle", message)
        cycle = message.partition("content import cycle: ")[2].partition(";")[0]
        self.assertEqual(cycle.count("b.tfx"), 2)

    def test_repeating_an_import_is_not_a_cycle(self):
        self.write("leaf.tfx", "LEAF\n")
        self.write("mid.tfx", "!import{leaf.tfx}\n")
        self.write("main.tfx", "!import{mid.tfx}\n!import{leaf.tfx}\n")
        self.assertEqual(self.build(), "LEAF\nLEAF\n")

    def test_macro_module_cycles_are_allowed(self):
        self.write(
            "a.tfxm",
            "!macroimport{b.tfxm}\n\n!defmacro{fa}: |\n    !fb\n",
        )
        self.write(
            "b.tfxm",
            "!macroimport{a.tfxm}\n\n!defmacro{fb}: |\n    B\n",
        )
        self.write("main.tfx", "!macroimport{a.tfxm}\n\n!fa\n")
        self.assertEqual(self.build(), "\nB\n")


class MacroImportTests(ModuleTestCase):
    def test_macro_imports_are_private_and_non_transitive(self):
        self.write("core.tfxm", "!defmacro{hidden}: |\n    H\n")
        self.write(
            "style.tfxm",
            "!macroimport{core.tfxm}\n\n!defmacro{shown}: |\n    !hidden\n",
        )
        self.write("main.tfx", "!macroimport{style.tfxm}\n\n!shown\n")
        self.assertEqual(self.build(), "\nH\n")
        self.write("main.tfx", "!macroimport{style.tfxm}\n\n!hidden\n")
        with self.assertRaisesRegex(Exception, "unknown special directive"):
            self.build()

    def test_two_versions_of_one_library_coexist(self):
        self.write("core-v1.tfxm", "!defmacro{tag}: |\n    V1\n")
        self.write("core-v2.tfxm", "!defmacro{tag}: |\n    V2\n")
        self.write(
            "old.tfx",
            "!macroimport{core-v1.tfxm}\n\n!tag\n",
        )
        self.write(
            "new.tfx",
            "!macroimport{core-v2.tfxm}\n\n!tag\n",
        )
        self.write("main.tfx", "!import{old.tfx}\n!import{new.tfx}\n")
        self.assertEqual(self.build(), "\nV1\n\nV2\n")

    def test_diamond_imports_stay_isolated(self):
        self.write("core.tfxm", "!defmacro{shared}: |\n    S\n")
        self.write(
            "a.tfxm",
            "!macroimport{core.tfxm}\n\n!defmacro{fa}: |\n    !shared\n",
        )
        self.write(
            "b.tfxm",
            "!macroimport{core.tfxm}\n\n!defmacro{fb}: |\n    !shared\n",
        )
        self.write(
            "main.tfx",
            "!macroimport{a.tfxm}\n!macroimport{b.tfxm}\n\n!fa\n!fb\n",
        )
        self.assertEqual(self.build(), "\nS\nS\n")

    def test_visible_name_conflicts_are_errors(self):
        self.write("a.tfxm", "!defmacro{same}: |\n    A\n")
        self.write("b.tfxm", "!defmacro{same}: |\n    B\n")
        self.write(
            "main.tfx",
            "!macroimport{a.tfxm}\n!macroimport{b.tfxm}\n",
        )
        self.assertIn("already available here", self.failure().message)

        self.write(
            "main.tfx",
            "!macroimport{a.tfxm}\n\n!defmacro{same}: |\n    LOCAL\n",
        )
        with self.assertRaisesRegex(ValidationError, "already defined at"):
            self.build()

    def test_forms_are_checked(self):
        self.write("a.tfxm", "!defmacro{m}: |\n    M\n")
        for source, message in (
            ("!macroimport{a.tfxm}: |\n    X\n", "does not accept a suite"),
            ("!macroimport\n", "one '{path}' group"),
            ("!macroimport{a.tfxm}{b}\n", "one '{path}' group"),
            ("!macroimport[x]\n", "path must be a required"),
            ("!macroimport{a.tfxm}(x=on)\n", "does not accept a '(...)' list"),
            (
                "!macroimport{a.tfxm}\n!macroimport{a.tfxm}\n",
                "already imported at",
            ),
            (
                "@center: |\n    !macroimport{a.tfxm}\n",
                "only valid at the top level",
            ),
            (
                "!flag{d}{off}\n!when{d} >> !macroimport{a.tfxm}\n",
                "cannot be a '>>' segment",
            ),
        ):
            with self.subTest(source=source):
                self.write("main.tfx", source)
                self.assertIn(message, self.failure().message)


class MacroModulePurityTests(ModuleTestCase):
    def check(self, macro_module, message):
        self.write("a.tfxm", macro_module)
        self.write("main.tfx", "!macroimport{a.tfxm}\n")
        self.assertIn(message, self.failure().message)

    def test_a_macro_module_states_only_definitions_and_imports(self):
        for macro_module in (
            "raw tex\n",
            "@center: |\n    X\n",
            "\\foo: |\n    X\n",
            "@center >> \\foo{x}\n",
        ):
            with self.subTest(macro_module=macro_module):
                self.check(macro_module, "may contain only !defmacro")

    def test_comments_and_blank_lines_are_allowed(self):
        self.write(
            "a.tfxm",
            "% a comment\n\n!defmacro{m}: |\n    M\n\n  % indented comment\n",
        )
        self.write("main.tfx", "!macroimport{a.tfxm}\n\n!m\n")
        self.assertEqual(self.build(), "\nM\n")

    def test_flags_and_conditionals_are_rejected_anywhere(self):
        for macro_module in (
            "!flag{d}{off}\n",
            "!defmacro{m}: |\n    !when{d} >> \\X\n",
            "!defmacro{m}: |\n    !unless{d} >> \\X\n",
            "!defmacro{m}: |\n    !import{a.tfx}\n",
        ):
            with self.subTest(macro_module=macro_module):
                self.write("a.tfxm", macro_module)
                self.write("main.tfx", "!macroimport{a.tfxm}\n")
                with self.assertRaises((ModuleError, ValidationError)) as caught:
                    self.build()
                self.assertRegex(
                    str(caught.exception),
                    "not allowed in a .tfxm|not allowed inside a macro template",
                )

    def test_a_macro_module_must_be_self_contained(self):
        self.check(
            "!defmacro{foo}{body}: |\n    !helper: |\n        !param{body}\n",
            "'!helper' is not defined in",
        )

    def test_a_callers_namespace_cannot_complete_a_macro_module(self):
        self.write("a.tfxm", "!defmacro{foo}: |\n    !helper\n")
        self.write(
            "main.tfx",
            "!macroimport{a.tfxm}\n\n!defmacro{helper}: |\n    H\n\n!foo\n",
        )
        self.assertIn("'!helper' is not defined in", self.failure().message)


class ConditionalImportTests(ModuleTestCase):
    def test_a_dropped_import_never_opens_its_file(self):
        self.write(
            "main.tfx",
            "!flag{appendix}{off}\n\n!when{appendix}: |\n    !import{missing.tfx}\n",
        )
        self.assertEqual(self.build(), "\n")
        with self.assertRaises(ModuleError):
            self.build(flags={"appendix": True})

    def test_a_kept_import_is_compiled(self):
        self.write("part.tfx", "PART\n")
        self.write(
            "main.tfx",
            "!flag{appendix}{on}\n\n!when{appendix}: |\n    !import{part.tfx}\n",
        )
        self.assertEqual(self.build(), "\nPART\n")


class ImportAsMacroValueTests(ModuleTestCase):
    def test_an_import_passed_to_a_macro_expands_once_per_reference(self):
        self.write("a.tfx", "A\n")
        self.write(
            "main.tfx",
            "!defmacro{twice}{body}: |\n"
            "    !param{body}\n"
            "    !param{body}\n"
            "\n"
            "!twice: |\n"
            "    !import{a.tfx}\n",
        )
        self.assertEqual(self.build(), "\nA\nA\n")


class DiagnosticChainTests(ModuleTestCase):
    def test_an_error_keeps_its_own_span_and_names_the_import_chain(self):
        self.write("broken.tfx", "!vpad{1em}:\n    - x\n")
        self.write("mid.tfx", "!import{broken.tfx}\n")
        self.write("main.tfx", "!import{mid.tfx}\n")
        with self.assertRaises(ValidationError) as caught:
            self.build()
        error = caught.exception
        self.assertTrue(error.span.file.endswith("broken.tfx"))
        self.assertEqual(error.message.count("imported from"), 2)

    def test_a_parse_error_in_a_callee_names_every_importer(self):
        # Loading a module parses it, so a parse error must accumulate the
        # same chain a later validation error does.
        self.write("broken.tfx", "@frame{a\n")
        self.write("mid.tfx", "!import{broken.tfx}\n")
        self.write("main.tfx", "!import{mid.tfx}\n")
        with self.assertRaises(ParseError) as caught:
            self.build()
        error = caught.exception
        self.assertTrue(error.span.file.endswith("broken.tfx"))
        self.assertEqual(error.message.count("imported from"), 2)

    def test_a_macro_module_error_names_the_file_that_imported_it(self):
        # A .tfxm is shared between decks, so which !macroimport reached it
        # is what tells the author where to look.
        self.write("impure.tfxm", "!defmacro{m}: |\n    !nosuchmacro\n")
        self.write("mid.tfx", "!macroimport{impure.tfxm}\n\nA\n")
        self.write("main.tfx", "!import{mid.tfx}\n")
        error = self.failure()
        self.assertTrue(error.span.file.endswith("impure.tfxm"))
        self.assertIn("imported from", error.message)
        self.assertIn("mid.tfx", error.message)
        self.assertEqual(error.message.count("imported from"), 2)

    def test_a_macro_module_parse_error_names_its_import_site(self):
        self.write("broken.tfxm", "!defmacro{m}: |\n    @frame{a\n")
        self.write("main.tfx", "!macroimport{broken.tfxm}\n")
        with self.assertRaises(ParseError) as caught:
            self.build()
        error = caught.exception
        self.assertTrue(error.span.file.endswith("broken.tfxm"))
        self.assertIn("imported from", error.message)
        self.assertIn("main.tfx", error.message)

    def test_a_shared_macro_module_names_its_shallowest_import(self):
        self.write("shared.tfxm", "!defmacro{s}: |\n    !nope\n")
        self.write("p.tfxm", "!macroimport{shared.tfxm}\n\n!defmacro{p}: |\n    P\n")
        self.write("q.tfxm", "!macroimport{shared.tfxm}\n\n!defmacro{q}: |\n    Q\n")
        self.write("main.tfx", "!macroimport{p.tfxm}\n!macroimport{q.tfxm}\n")
        message = self.failure().message
        self.assertIn("p.tfxm", message)
        self.assertNotIn("q.tfxm", message)

    def test_a_shallower_import_wins_over_an_earlier_deeper_one(self):
        # Breadth first, so main.tfx's second line beats a.tfxm's first.
        self.write("z.tfxm", "!defmacro{z}: |\n    !nope\n")
        self.write("a.tfxm", "!macroimport{z.tfxm}\n\n!defmacro{a}: |\n    A\n")
        self.write("main.tfx", "!macroimport{a.tfxm}\n!macroimport{z.tfxm}\n")
        message = self.failure().message
        self.assertIn("main.tfx", message)
        self.assertNotIn("a.tfxm", message)

    def test_a_deep_macro_import_names_every_level(self):
        self.write("deep.tfxm", "!defmacro{m}: |\n    !nope\n")
        self.write("b.tfxm", "!macroimport{deep.tfxm}\n\n!defmacro{b}: |\n    B\n")
        self.write("main.tfx", "!macroimport{b.tfxm}\n")
        message = self.failure().message
        self.assertEqual(message.count("imported from"), 2)
        self.assertIn("b.tfxm", message)
        self.assertIn("main.tfx", message)

    def test_a_nested_unreadable_module_keeps_the_levels_above_it(self):
        # The level written at the error's own line adds nothing, but it must
        # not stop the walk: how a.tfxm entered the build is still needed.
        self.write("a.tfxm", "!macroimport{missing.tfxm}\n\n!defmacro{a}: |\n    A\n")
        self.write("main.tfx", "!macroimport{a.tfxm}\n")
        error = self.failure()
        self.assertTrue(error.span.file.endswith("a.tfxm"))
        self.assertIn("cannot read module", error.message)
        self.assertEqual(error.message.count("imported from"), 1)
        self.assertIn("main.tfx", error.message)

    def test_two_errors_on_one_line_get_the_same_context(self):
        # A wrong extension and an unreadable file are both written at the
        # same !macroimport line, so neither may lose the levels above it.
        self.write("dep.tfx", "A\n")
        for source in ("!macroimport{dep.tfx}\n", "!macroimport{missing.tfxm}\n"):
            with self.subTest(source=source):
                self.write("a.tfxm", source + "\n!defmacro{a}: |\n    A\n")
                self.write("main.tfx", "!macroimport{a.tfxm}\n")
                message = self.failure().message
                self.assertEqual(message.count("imported from"), 1)
                self.assertIn("main.tfx", message)

    def test_a_chained_error_keeps_its_real_root_cause(self):
        # Re-raising the same object 'from' itself destroys the cause and
        # leaves a cycle for anything that walks it.
        self.write("main.tfx", "!macroimport{missing.tfxm}\n")
        with self.assertRaises(ModuleError) as caught:
            self.build()
        error = caught.exception
        self.assertIsNot(error.__cause__, error)
        self.assertIsInstance(error.__cause__, OSError)

    def test_an_unreadable_macro_module_is_not_given_a_chain(self):
        # That error is already reported at the !macroimport line itself.
        self.write("main.tfx", "!macroimport{missing.tfxm}\n")
        self.assertNotIn("imported from", self.failure().message)

    def test_a_caller_side_binding_error_is_not_given_a_chain(self):
        self.write("child.tfx", "!flag{d}{off}\n\nC\n")
        self.write("main.tfx", "!import{child.tfx}(nosuch=on)\n")
        self.assertNotIn("imported from", self.failure().message)


if __name__ == "__main__":
    unittest.main()
