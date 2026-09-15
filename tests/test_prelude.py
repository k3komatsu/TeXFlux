"""The bundled standard flow macros and how every module environment sees them.

The five names are ordinary source macros living in one compiler-owned
``.tfxm``; nothing in the compiler knows what they expand to. These tests pin
that they behave like macros, that no module has to import them, and that the
module they come from stays invisible to authors and to external consumers.
"""

import json
import unittest

from texflux import compile_ast, compile_text, compile_with_map, serialize_ast
from texflux.errors import MacroExpansionError, ModuleError, ValidationError
from texflux import modules as texflux_modules
from texflux.modules import (
    PRELUDE_MODULE,
    CompilationSession,
    load_standard_macros,
)

from .support import TempDirTestCase


STANDARD_NAMES = ("before", "after", "around", "off", "drop")


class StandardFlowBehaviourTests(unittest.TestCase):
    def test_each_operation_places_its_values_in_order(self):
        for source, expected in (
            ("!before{A} >> \\B\n", "A\n\\B\n"),
            ("!after{B} >> \\A\n", "\\A\nB\n"),
            ("!around{A}{C} >> \\B\n", "A\n\\B\nC\n"),
            ("!off{A} >> \\B\n", "\\B\n"),
            ("!drop >> \\B\n", "\n"),
        ):
            with self.subTest(source=source):
                self.assertEqual(compile_text(source), expected)

    def test_composition_nests_left_to_right(self):
        self.assertEqual(
            compile_text(
                "!before{A} >>\n!after{D} >>\n!around{B}{C} >>\n\\X\n"
            ),
            "A\nB\n\\X\nC\nD\n",
        )

    def test_around_equals_before_composed_with_after(self):
        self.assertEqual(
            compile_text("!around{A}{C} >> \\B\n"),
            compile_text("!before{A} >>\n!after{C} >>\n\\B\n"),
        )

    def test_block_and_stack_payloads_agree(self):
        for block, stack in (
            ("!before{A}::\n    \\B\n", "!before{A} >> \\B\n"),
            ("!after{B}::\n    \\A\n", "!after{B} >> \\A\n"),
            ("!around{A}{C}::\n    \\B\n", "!around{A}{C} >> \\B\n"),
            ("!off{A}::\n    \\B\n", "!off{A} >> \\B\n"),
            ("!drop::\n    \\B\n", "!drop >> \\B\n"),
        ):
            with self.subTest(block=block):
                self.assertEqual(compile_text(block), compile_text(stack))

    def test_a_sequence_suite_is_one_value_per_entry(self):
        # No rule of their own: a ':' suite feeds these calls the way it feeds
        # any other, so one entry is the payload and two are an arity error.
        self.assertEqual(compile_text("!off{X}:::\n    - \\a\n"), "\\a\n")
        self.assertEqual(compile_text("!drop:::\n    - \\a\n"), "\n")
        self.assertEqual(compile_text("!before{A}:::\n    - \\a\n"), "A\n\\a\n")
        with self.assertRaisesRegex(
            MacroExpansionError,
            r"'!off' expects \{ignored\}\{body\}, exactly 2 value\(s\), got 3",
        ):
            compile_text("!off{X}:::\n    - \\a\n    - \\b\n")

    def test_a_payload_keeps_its_blank_lines_and_nesting(self):
        self.assertEqual(
            compile_text("!before{A}::\n    first\n\n    @center::\n        B\n"),
            "A\nfirst\n\n\\begin{center}\nB\n\\end{center}\n",
        )


class StandardFlowProvenanceTests(unittest.TestCase):
    def test_values_map_to_the_group_the_author_wrote(self):
        result = compile_with_map(
            "!around{\\smallskip}{\\medskip} >> \\foo\n",
            filename="m.tfx",
        )
        sources = {
            fragment.text: fragment.source
            for fragment in result.rendered.fragments
            if fragment.source is not None
        }
        self.assertEqual(result.text, "\\smallskip\n\\foo\n\\medskip\n")
        # Both values are written on line 1, each at its own '{...}' group,
        # and neither points at the installed prelude.
        self.assertEqual(sources["\\smallskip"].start.column, 8)
        self.assertEqual(sources["\\medskip"].start.column, 20)
        self.assertTrue(
            all(span.file == "m.tfx" for span in sources.values()),
            sources,
        )

    def test_the_bundled_module_is_not_a_document_source(self):
        result = compile_ast("!before{A} >> \\foo\n", filename="m.tfx")
        self.assertEqual([source.file for source in result.sources], ["m.tfx"])
        payload = json.loads(serialize_ast(result))
        self.assertEqual([entry["file"] for entry in payload["sources"]], ["m.tfx"])
        # No call survives expansion, and no node type was added for one.
        self.assertEqual(
            [node.get("text") or node.get("name")
             for node in payload["document"]["body"]["nodes"]],
            ["A", "foo"],
        )


class StandardFlowModuleTests(TempDirTestCase):
    def build(self, source: str) -> str:
        return compile_text(source, filename=str(self.root / "main.tfx"))

    def test_an_imported_content_module_needs_no_import_of_its_own(self):
        self.write("part.tfx", "!before{A} >> \\foo\n")
        self.assertEqual(self.build("!import{part.tfx}\n"), "A\n\\foo\n")

    def test_a_macro_module_template_may_use_them(self):
        self.write(
            "style.tfxm",
            "!defmacro{compact}{body}::\n"
            "    !before{\\smallskip} >>\n"
            "    !after{\\smallskip} >>\n"
            "    !param{body}\n",
        )
        self.assertEqual(
            self.build("!macroimport{style.tfxm}\n!compact::\n    \\x\n"),
            "\\smallskip\n\\x\n\\smallskip\n",
        )

    def test_a_macro_module_may_not_define_a_standard_name(self):
        self.write("style.tfxm", "!defmacro{before}{a}{b}::\n    A\n")
        with self.assertRaisesRegex(
            ValidationError,
            "macro '!before' conflicts with a TeXFlux standard flow macro",
        ):
            self.build("!macroimport{style.tfxm}\n")

    def test_standard_names_do_not_leak_between_macro_modules(self):
        # Seeding every environment must not turn a private import into a
        # transitive one: only the standard names are shared.
        self.write("inner.tfxm", "!defmacro{inner}{body}::\n    !param{body}\n")
        self.write(
            "outer.tfxm",
            "!macroimport{inner.tfxm}\n"
            "!defmacro{outer}{body}::\n    !inner::\n        !param{body}\n",
        )
        self.assertEqual(
            self.build("!macroimport{outer.tfxm}\n!outer::\n    \\x\n"),
            "\\x\n",
        )
        with self.assertRaisesRegex(Exception, "unknown special directive"):
            self.build("!macroimport{outer.tfxm}\n!inner::\n    \\x\n")

    def test_the_cli_and_the_python_api_agree(self):
        from texflux.cli import main

        source = "!around{\\smallskip}{\\medskip} >> \\foo\n"
        self.write("main.tfx", source)
        self.assertEqual(
            main([
                "compile",
                str(self.root / "main.tfx"),
                "-o",
                str(self.root / "out.tex"),
            ]),
            0,
        )
        self.assertEqual(self.read("out.tex"), self.build(source))


class StandardFlowDropTests(TempDirTestCase):
    """``!drop`` discards before anything downstream of expansion runs."""

    def build(self, source: str) -> str:
        return compile_text(source, filename=str(self.root / "main.tfx"))

    def test_a_dropped_import_never_opens_its_file(self):
        self.assertEqual(self.build("!drop::\n    !import{missing.tfx}\n"), "\n")
        self.assertEqual(self.build("!drop >> !import{missing.tfx}\n"), "\n")

    def test_a_dropped_payload_is_never_normalized(self):
        for payload in (
            "!nosuchspecial::\n        A\n",
            "@unknown::\n        A\n",
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.build("!drop::\n    " + payload), "\n")

    def test_a_dropped_payload_may_hold_macro_calls_and_conditionals(self):
        self.assertEqual(
            self.build(
                "!flag{d}{off}\n"
                "!defmacro{m}{x}::\n    !param{x}\n"
                "!drop::\n"
                "    !m::\n        A\n"
                "    !when{d}::\n        B\n"
                "    !unless{d}::\n        C\n"
            ),
            "\n",
        )

    def test_a_dropped_payload_must_still_parse(self):
        from texflux.errors import ParseError

        with self.assertRaises(ParseError):
            self.build("!drop:\n    \\foo::\n    bad\n")

    def test_a_dropped_payload_is_still_expanded_before_it_is_discarded(self):
        # This is the boundary: !drop binds its payload as a macro value, so
        # expansion still visits it. Only normalization and content-import
        # resolution are skipped. A dropped !when payload is not expanded at
        # all, which is why that, not !drop, disables content that no longer
        # compiles. Losing either half would silently change which documents
        # build, so both are pinned here.
        for payload, error in (
            ("!m{a}{b}", MacroExpansionError),
            ("!defmacro{q}{y}::\n        A", ValidationError),
            ("!param{a}", MacroExpansionError),
        ):
            with self.subTest(payload=payload):
                source = "!defmacro{m}{x}::\n    A\n!drop::\n    " + payload + "\n"
                with self.assertRaises(error):
                    self.build(source)
                # The same payload under a dropped conditional is never
                # expanded, so it compiles.
                self.assertEqual(
                    self.build(
                        "!flag{d}{off}\n!defmacro{m}{x}::\n    A\n"
                        "!when{d}::\n    " + payload + "\n"
                    ),
                    "\n",
                )

    def test_a_dropped_stack_payload_discards_every_segment(self):
        self.assertEqual(self.build("!drop >> \\hoge >> \\fuga\n"), "\n")


class BundledModuleTests(unittest.TestCase):
    def test_the_module_defines_exactly_the_standard_surface(self):
        macros = load_standard_macros()
        self.assertEqual(sorted(macros), sorted(STANDARD_NAMES))
        for name, definition in macros.items():
            with self.subTest(name=name):
                self.assertEqual(definition.module, PRELUDE_MODULE)
                self.assertEqual(definition.span.file, PRELUDE_MODULE)

    def test_drop_is_an_ordinary_macro_with_an_empty_template(self):
        # The one reason drop was kept as a compiler primitive: a template
        # that reads none of its values already denotes empty output.
        drop = load_standard_macros()["drop"]
        self.assertEqual([p.name for p in drop.parameters], ["body"])
        self.assertEqual(drop.template.nodes, ())

    def test_a_broken_install_is_an_internal_error_the_cli_reports(self):
        # The bundled module is compiler distribution, so a failure to read it
        # is neither a document error nor something a caller can fix: it has
        # no span, and the CLI must still print one line rather than a
        # traceback.
        import io
        import pathlib
        import tempfile
        import unittest.mock
        from contextlib import redirect_stderr

        from texflux import cli, errors

        with unittest.mock.patch.object(
            texflux_modules, "_PRELUDE_RESOURCE", "no-such-module.tfxm"
        ):
            with self.assertRaises(errors.InternalError) as caught:
                load_standard_macros()
            self.assertIn("bundled prelude is invalid", str(caught.exception))
            self.assertIsInstance(caught.exception, RuntimeError)

            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                (root / "in.tfx").write_text("\\x\n", encoding="utf-8")
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    status = cli.main([
                        "compile",
                        str(root / "in.tfx"),
                        "-o",
                        str(root / "out.tex"),
                    ])
            self.assertEqual(status, 1)
            # The whole line, not a substring: the CLI supplies the
            # 'texflux: ' prefix, so a message carrying its own would
            # double it and no substring assertion would notice.
            self.assertEqual(
                stderr.getvalue().splitlines()[0].split(":")[:3],
                ["texflux", " internal error", " bundled prelude is invalid"],
            )

    def test_a_session_reads_the_module_once_and_shares_it(self):
        session = CompilationSession()
        first = session._standard
        session.compile_root("\\x\n", filename="a.tfx", data=b"\\x\n")
        self.assertIs(session._standard, first)
        self.assertEqual(
            [source.file for source in session.loaded()],
            ["a.tfx"],
        )

    def test_a_template_resolves_names_in_the_bundled_module(self):
        # A standard macro's template sees the standard module's environment,
        # never the caller's, so a document's own macros cannot reach into one.
        session = CompilationSession()
        session.compile_root(
            "!defmacro{mine}{x}::\n    !param{x}\n!mine::\n    A\n",
            filename="a.tfx",
            data=b"",
        )
        self.assertEqual(
            sorted(session._environments[PRELUDE_MODULE]),
            sorted(STANDARD_NAMES),
        )
        document = session._environments[
            next(path for path in session._environments if path != PRELUDE_MODULE)
        ]
        self.assertIn("mine", document)

    def test_an_imported_name_cannot_displace_a_standard_one(self):
        session = CompilationSession()
        definition = session._standard["off"]
        with self.assertRaisesRegex(ModuleError, "already available here"):
            from texflux.modules import MacroImport, merge_imports

            merge_imports(
                dict(session._standard),
                (MacroImport("/x.tfxm", "x.tfxm", definition.span),),
                {"/x.tfxm": {"off": definition}},
            )


class LowLevelApiTests(unittest.TestCase):
    def test_normalize_stays_low_level(self):
        # normalize() owns no compilation session, so it seeds no environment
        # either: the standard macros arrive with module-aware compilation.
        from texflux.errors import DirectiveError
        from texflux.normalize import normalize
        from texflux.parser import parse

        with self.assertRaisesRegex(DirectiveError, "unknown special directive"):
            normalize(parse("!before{A} >> \\B\n", "x.tfx"))

    def test_every_documented_entry_point_sees_them(self):
        source = "!before{A} >> \\B\n"
        self.assertEqual(compile_text(source), "A\n\\B\n")
        self.assertEqual(compile_with_map(source).text, "A\n\\B\n")
        self.assertEqual(
            len(compile_ast(source).document.body.nodes),
            2,
        )


class StandardFlowArityTests(unittest.TestCase):
    def test_missing_values_report_the_normal_macro_diagnostic(self):
        for source, message in (
            ("!before\n", r"'!before' expects \{prefix\}\{body\}"),
            ("!around{A} >> \\B\n", r"'!around' expects \{prefix\}\{suffix\}\{body\}"),
            ("!drop{x} >> \\B\n", r"'!drop' expects \{body\}"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(MacroExpansionError, message):
                    compile_text(source, filename="m.tfx")


if __name__ == "__main__":
    unittest.main()
