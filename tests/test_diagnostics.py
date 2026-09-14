"""The structured diagnostic API editors and language servers read."""

import hashlib
import json
import os
import unittest

from texflux import (
    Diagnostic,
    DiagnosticReport,
    FlagError,
    RelatedLocation,
    Severity,
    compile_text,
    diagnose,
    serialize_diagnostics,
)
from texflux.ast import SourcePosition, SourceSpan
from texflux.errors import (
    DirectiveError,
    MacroExpansionError,
    ModuleError,
    ParseError,
    TeXFluxError,
    ValidationError,
)
from texflux.render import RenderWarning

from .support import TempDirTestCase


def span(file="f.tfx", line=1, column=2, width=1):
    return SourceSpan(
        file,
        SourcePosition(line, column),
        SourcePosition(line, column + width),
    )


class ErrorModelTests(unittest.TestCase):
    def test_each_error_names_its_own_kind(self):
        for error_type, kind in (
            (ParseError, "parse"),
            (ValidationError, "validation"),
            (DirectiveError, "directive"),
            (MacroExpansionError, "macro"),
            (ModuleError, "module"),
            (TeXFluxError, "texflux"),
        ):
            with self.subTest(error=error_type.__name__):
                error = error_type("m", span(), code="X001")
                self.assertEqual(error.kind, kind)
                self.assertEqual(error.error_kind, f"{kind} error")

    def test_a_diagnostic_line_ends_with_its_code(self):
        error = ParseError("unclosed required group", span(), code="P004")
        line = "f.tfx:1:2: parse error: unclosed required group [P004]"
        self.assertEqual(error.diagnostic(), line)
        self.assertEqual(str(error), line)

    def test_a_warning_prints_the_same_shape_as_an_error(self):
        warning = RenderWarning("w", span(), "W001")
        self.assertEqual(warning.kind, "render")
        self.assertEqual(warning.diagnostic(), "f.tfx:1:2: warning: w [W001]")

    def test_a_code_is_required(self):
        # A construction that forgets one must fail loudly rather than
        # serialize an empty code.
        with self.assertRaises(TypeError):
            ParseError("m", span())

    def test_chaining_keeps_the_code_span_and_earlier_related(self):
        first = RelatedLocation("first defined here", span("a.tfx"))
        second = RelatedLocation("imported from here", span("b.tfx"))
        error = ModuleError("m", span(), code="M021", related=(first,))
        chained = error.chained("m; imported from b.tfx:1:2", second)

        self.assertIsInstance(chained, ModuleError)
        self.assertEqual(chained.code, "M021")
        self.assertEqual(chained.span, error.span)
        self.assertEqual(chained.related, (first, second))
        self.assertEqual(error.related, (first,))


class DiagnoseTests(TempDirTestCase):
    def report(self, name="main.tfx", **kwargs):
        path = self.root / name
        return diagnose(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            **kwargs,
        )

    def only(self, name="main.tfx", **kwargs):
        report = self.report(name, **kwargs)
        self.assertEqual(len(report.diagnostics), 1, report.diagnostics)
        return report.diagnostics[0]

    def relative(self, report):
        return [
            os.path.relpath(source.file, self.root) for source in report.sources
        ]

    # -- shape ------------------------------------------------------------

    def test_a_clean_document_reports_nothing_and_lists_its_root(self):
        self.write("main.tfx", "@frame{t}:\n    body\n")
        report = self.report()

        self.assertEqual(report.diagnostics, ())
        self.assertTrue(report.ok)
        self.assertEqual(self.relative(report), ["main.tfx"])
        self.assertEqual(report.by_file(), {str(self.root / "main.tfx"): ()})

    def test_an_error_is_reported_rather_than_raised(self):
        self.write("main.tfx", "@frame{x}:\n    @foo{bad\n")
        diagnostic = self.only()

        self.assertIs(diagnostic.severity, Severity.ERROR)
        self.assertEqual(diagnostic.kind, "parse")
        self.assertEqual(diagnostic.code, "P004")
        self.assertEqual(diagnostic.message, "unclosed required group")
        self.assertEqual((diagnostic.span.start.line, diagnostic.span.start.column), (2, 9))
        self.assertFalse(self.report().ok)

    def test_a_root_that_does_not_parse_is_still_listed_as_a_source(self):
        # An editor publishes diagnostics per file, so the file that failed
        # to parse is the one that most needs to be in the table.
        self.write("main.tfx", "@frame{x}:\n    @foo{bad\n")
        self.assertEqual(self.relative(self.report()), ["main.tfx"])

    def test_an_import_that_does_not_parse_is_still_listed_as_a_source(self):
        self.write("broken.tfx", "@frame{a\n")
        self.write("main.tfx", "!import{broken.tfx}\n")
        self.assertEqual(
            self.relative(self.report()),
            ["main.tfx", "broken.tfx"],
        )

    def test_a_module_that_could_not_be_read_is_not_a_source(self):
        # It has no bytes to hash and no lines to publish against, so the
        # diagnostic belongs to the import that named it.
        self.write("main.tfx", "!import{missing.tfx}\n")
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "M028")
        self.assertTrue(diagnostic.span.file.endswith("main.tfx"))
        self.assertEqual(self.relative(self.report()), ["main.tfx"])

    def test_a_module_that_is_not_utf8_is_not_a_source(self):
        self.write("bad.tfx", b"\xff\xfe raw\n")
        self.write("main.tfx", "!import{bad.tfx}\n")
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "M028")
        self.assertEqual(self.relative(self.report()), ["main.tfx"])

    def test_a_dropped_conditional_imports_nothing_and_lists_nothing(self):
        # Disabling content that no longer compiles has to keep working, so
        # the file behind a dropped payload is never even opened.
        self.write("gone.tfx", "@frame{a\n")
        self.write(
            "main.tfx",
            "!flag{extra}{off}\n!when{extra} >> !import{gone.tfx}\n",
        )
        report = self.report()

        self.assertEqual(report.diagnostics, ())
        self.assertEqual(self.relative(report), ["main.tfx"])
        self.assertFalse(self.report(flags={"extra": True}).ok)

    def test_a_warning_leaves_the_compilation_ok(self):
        self.write("main.tfx", "\\foo::\n    - a % trailing\n")
        diagnostic = self.only()

        self.assertIs(diagnostic.severity, Severity.WARNING)
        self.assertEqual(diagnostic.kind, "render")
        self.assertEqual(diagnostic.code, "W002")
        self.assertTrue(self.report().ok)

    def test_a_diagnostic_and_the_error_it_came_from_agree(self):
        self.write("main.tfx", "!nosuch\n")
        diagnostic = self.only()
        with self.assertRaises(TeXFluxError) as caught:
            compile_text(
                (self.root / "main.tfx").read_text(encoding="utf-8"),
                filename=str(self.root / "main.tfx"),
            )
        error = caught.exception

        self.assertEqual(diagnostic.message, error.message)
        self.assertEqual(diagnostic.span, error.span)
        self.assertEqual(diagnostic.code, error.code)
        self.assertEqual(diagnostic.kind, error.kind)

    def test_by_file_covers_every_source_and_clears_the_clean_ones(self):
        self.write("style.tfxm", "!defmacro{s}:\n    S\n")
        self.write("broken.tfx", "!defmacro{m}{x}{x}:\n    A\n")
        self.write(
            "main.tfx",
            "!macroimport{style.tfxm}\n\n!import{broken.tfx}\n",
        )
        report = self.report()
        grouped = report.by_file()

        self.assertEqual(
            sorted(grouped),
            sorted(source.file for source in report.sources),
        )
        self.assertEqual(grouped[str(self.root / "main.tfx")], ())
        self.assertEqual(grouped[str(self.root / "style.tfxm")], ())
        self.assertEqual(len(grouped[str(self.root / "broken.tfx")]), 1)

    # -- related locations -------------------------------------------------

    def assert_related(self, diagnostic, expected):
        self.assertEqual(
            [
                (related.message, os.path.relpath(related.span.file, self.root))
                for related in diagnostic.related
            ],
            expected,
        )

    def test_a_duplicate_flag_points_at_the_first_declaration(self):
        self.write("main.tfx", "!flag{d}{off}\n!flag{d}{on}\n")
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "V010")
        self.assert_related(diagnostic, [("first declared here", "main.tfx")])
        self.assertEqual(diagnostic.related[0].span.start.line, 1)

    def test_a_duplicate_macro_points_at_the_first_definition(self):
        self.write(
            "main.tfx",
            "!defmacro{m}:\n    A\n\n!defmacro{m}:\n    B\n",
        )
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "V022")
        self.assert_related(diagnostic, [("first defined here", "main.tfx")])
        self.assertEqual(diagnostic.related[0].span.start.line, 1)

    def test_a_top_level_expansion_error_adds_no_related_location(self):
        # Its own span is already the call, so there is nothing to add.
        self.write("main.tfx", "!defmacro{m}{x}:\n    !param{x}\n\n!m\n")
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "E019")
        self.assertEqual(diagnostic.related, ())
        self.assertEqual(diagnostic.span.start.line, 4)

    def test_an_expansion_error_inside_a_template_points_at_the_call_site(self):
        self.write(
            "main.tfx",
            "!defmacro{inner}{x}:\n"
            "    !param{x}\n"
            "\n"
            "!defmacro{outer}:\n"
            "    !inner\n"
            "\n"
            "!outer\n",
        )
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "E019")
        # The span stays on the template line that is wrong; the related
        # location says which call put that template here.
        self.assertEqual(diagnostic.span.start.line, 5)
        self.assert_related(diagnostic, [("called here", "main.tfx")])
        self.assertEqual(diagnostic.related[0].span.start.line, 7)

    def test_a_double_binding_points_at_the_first_one(self):
        self.write("child.tfx", "!flag{d}{off}\n\nC\n")
        self.write("main.tfx", "!import{child.tfx}(d=on,d=off)\n")
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "M010")
        self.assert_related(diagnostic, [("first bound here", "main.tfx")])

    def test_a_double_macro_import_points_at_the_first_one(self):
        self.write("a.tfxm", "!defmacro{m}:\n    M\n")
        self.write("main.tfx", "!macroimport{a.tfxm}\n!macroimport{a.tfxm}\n")
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "M019")
        self.assert_related(diagnostic, [("first imported here", "main.tfx")])

    def test_a_macro_name_collision_points_at_the_visible_definition(self):
        self.write("a.tfxm", "!defmacro{m}:\n    A\n")
        self.write("b.tfxm", "!defmacro{m}:\n    B\n")
        self.write("main.tfx", "!macroimport{a.tfxm}\n!macroimport{b.tfxm}\n")
        diagnostic = self.only()

        self.assertEqual(diagnostic.code, "M021")
        self.assert_related(diagnostic, [("defined here", "a.tfxm")])

    def test_an_import_chain_is_carried_innermost_first(self):
        self.write("broken.tfx", "!defmacro{m}{x}{x}:\n    A\n")
        self.write("mid.tfx", "!import{broken.tfx}\n")
        self.write("main.tfx", "!import{mid.tfx}\n")
        diagnostic = self.only()

        self.assertTrue(diagnostic.span.file.endswith("broken.tfx"))
        self.assert_related(
            diagnostic,
            [
                ("imported from here", "mid.tfx"),
                ("imported from here", "main.tfx"),
            ],
        )

    def test_a_macro_module_error_names_its_import_site(self):
        self.write("impure.tfxm", "!defmacro{m}:\n    !nosuchmacro\n")
        self.write("main.tfx", "!macroimport{impure.tfxm}\n")
        diagnostic = self.only()

        self.assertTrue(diagnostic.span.file.endswith("impure.tfxm"))
        self.assert_related(diagnostic, [("imported from here", "main.tfx")])

    # -- what is not a diagnostic -----------------------------------------

    def test_a_bad_flag_override_is_not_a_diagnostic(self):
        # An override describes the build rather than the document, so it
        # has no span and cannot become a diagnostic.
        self.write("main.tfx", "A\n")
        with self.assertRaises(FlagError):
            self.report(flags={"nosuch": True})

    def test_a_non_bool_flag_override_is_not_a_diagnostic(self):
        self.write("main.tfx", "!flag{d}{off}\n\nA\n")
        with self.assertRaises(FlagError):
            self.report(flags={"d": "on"})

    # -- overlays ----------------------------------------------------------

    def test_an_overlay_is_read_instead_of_the_file(self):
        self.write("a.tfxm", "!defmacro{m}:\n    STALE\n")
        self.write("main.tfx", "!macroimport{a.tfxm}\n\n!m\n")
        overlay = {str(self.root / "a.tfxm"): "!defmacro{m}:\n    !nosuchmacro\n"}

        self.assertTrue(self.report().ok)
        self.assertFalse(self.report(overlays=overlay).ok)

    def test_an_overlay_repairs_a_module_that_is_broken_on_disk(self):
        self.write("a.tfxm", "!defmacro{m}:\n    !nosuchmacro\n")
        self.write("main.tfx", "!macroimport{a.tfxm}\n\n!m\n")
        overlay = {str(self.root / "a.tfxm"): "!defmacro{m}:\n    FIXED\n"}

        self.assertFalse(self.report().ok)
        self.assertTrue(self.report(overlays=overlay).ok)

    def test_an_overlay_can_supply_a_module_that_is_not_on_disk_yet(self):
        self.write("main.tfx", "!import{new.tfx}\n")
        self.assertFalse(self.report().ok)
        self.assertTrue(
            self.report(overlays={str(self.root / "new.tfx"): "NEW\n"}).ok
        )

    def test_overlay_keys_are_matched_by_path_identity(self):
        (self.root / "sub").mkdir()
        self.write("sub/a.tfx", "STALE\n")
        self.write("main.tfx", "!import{sub/a.tfx}\n")
        for key in (
            str(self.root / "sub" / "a.tfx"),
            str(self.root / "sub" / ".." / "sub" / "a.tfx"),
        ):
            with self.subTest(key=key):
                report = self.report(overlays={key: "FRESH\n"})
                source = next(
                    item for item in report.sources if item.file.endswith("a.tfx")
                )
                self.assertEqual(source.data, b"FRESH\n")

    def test_a_relative_overlay_key_resolves_like_any_other_path(self):
        # An editor may hand over whatever spelling it holds the buffer under.
        self.write("a.tfx", "STALE\n")
        self.write("main.tfx", "!import{a.tfx}\n")
        here = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, here)

        report = self.report(overlays={"a.tfx": "FRESH\n"})
        source = next(
            item for item in report.sources if item.file.endswith("a.tfx")
        )
        self.assertEqual(source.data, b"FRESH\n")

    def test_an_overlay_of_the_root_is_ignored(self):
        # The root is what the caller already passed as ``source``.
        self.write("main.tfx", "FROM DISK\n")
        report = self.report(overlays={str(self.root / "main.tfx"): "OTHER\n"})
        self.assertEqual(report.root.data, b"FROM DISK\n")

    def test_a_text_overlay_is_hashed_as_utf8(self):
        self.write("a.tfx", "old\n")
        self.write("main.tfx", "!import{a.tfx}\n")
        report = self.report(overlays={str(self.root / "a.tfx"): "\u65e5\n"})
        source = next(
            item for item in report.sources if item.file.endswith("a.tfx")
        )
        self.assertEqual(source.data, "\u65e5\n".encode("utf-8"))

    def test_a_bytes_overlay_is_used_as_is(self):
        self.write("a.tfx", "old\n")
        self.write("main.tfx", "!import{a.tfx}\n")
        report = self.report(overlays={str(self.root / "a.tfx"): b"raw\n"})
        source = next(
            item for item in report.sources if item.file.endswith("a.tfx")
        )
        self.assertEqual(source.data, b"raw\n")

    def test_source_bytes_default_to_the_utf8_encoding_of_the_text(self):
        report = diagnose("\u65e5\n", filename="x.tfx")
        self.assertEqual(report.root.data, "\u65e5\n".encode("utf-8"))


class SerializeTests(TempDirTestCase):
    def payload(self, name="main.tfx", **kwargs):
        path = self.root / name
        report = diagnose(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            **kwargs,
        )
        return report, json.loads(serialize_diagnostics(report))

    def test_a_clean_document_serializes_an_empty_list(self):
        self.write("main.tfx", "@frame{t}:\n    body\n")
        report, value = self.payload()

        self.assertEqual(value["format"], "texflux-diagnostics")
        self.assertEqual(value["version"], 1)
        self.assertEqual(value["producer"]["name"], "texflux")
        self.assertEqual(value["root"], 0)
        self.assertEqual(value["diagnostics"], [])
        self.assertEqual(
            value["sources"],
            [
                {
                    "id": 0,
                    "file": report.root.file,
                    "sha256": hashlib.sha256(report.root.data).hexdigest(),
                }
            ],
        )

    def test_an_error_serializes_its_severity_kind_code_and_span(self):
        self.write("main.tfx", "@frame{x}:\n    @foo{bad\n")
        _, value = self.payload()
        diagnostic = value["diagnostics"][0]

        self.assertEqual(diagnostic["severity"], "error")
        self.assertEqual(diagnostic["kind"], "parse")
        self.assertEqual(diagnostic["code"], "P004")
        self.assertEqual(diagnostic["message"], "unclosed required group")
        self.assertEqual(
            diagnostic["span"],
            {
                "source": 0,
                "start": {"line": 2, "column": 9},
                "end": {"line": 2, "column": 10},
            },
        )
        self.assertEqual(diagnostic["related"], [])

    def test_a_warning_serializes_as_a_warning(self):
        self.write("main.tfx", "\\foo::\n    - a % trailing\n")
        _, value = self.payload()

        self.assertEqual(value["diagnostics"][0]["severity"], "warning")
        self.assertEqual(value["diagnostics"][0]["kind"], "render")

    def test_related_locations_index_the_source_table(self):
        self.write("broken.tfx", "!defmacro{m}{x}{x}:\n    A\n")
        self.write("mid.tfx", "!import{broken.tfx}\n")
        self.write("main.tfx", "!import{mid.tfx}\n")
        _, value = self.payload()

        files = [source["file"] for source in value["sources"]]
        diagnostic = value["diagnostics"][0]
        self.assertTrue(files[diagnostic["span"]["source"]].endswith("broken.tfx"))
        self.assertEqual(
            [files[item["span"]["source"]].rsplit("/", 1)[-1]
             for item in diagnostic["related"]],
            ["mid.tfx", "main.tfx"],
        )

    def test_serialization_is_deterministic_and_ends_with_one_newline(self):
        self.write("main.tfx", "@frame{x}:\n    @foo{bad\n")
        path = self.root / "main.tfx"
        source = path.read_text(encoding="utf-8")
        first = serialize_diagnostics(diagnose(source, filename=str(path)))
        second = serialize_diagnostics(diagnose(source, filename=str(path)))

        self.assertEqual(first, second)
        self.assertTrue(first.endswith("}\n"))
        self.assertFalse(first.endswith("\n\n"))
        self.assertNotIn(", ", first)
        self.assertNotIn('": ', first)

    def test_pretty_changes_only_the_indentation(self):
        self.write("main.tfx", "@frame{x}:\n    @foo{bad\n")
        path = self.root / "main.tfx"
        report = diagnose(path.read_text(encoding="utf-8"), filename=str(path))
        pretty = serialize_diagnostics(report, pretty=True)

        self.assertIn("\n  ", pretty)
        self.assertTrue(pretty.endswith("}\n"))
        self.assertEqual(
            json.loads(pretty),
            json.loads(serialize_diagnostics(report)),
        )

    def test_non_ascii_is_not_escaped(self):
        self.write("main.tfx", "@frame{\u65e5}:\n    @foo{bad\n")
        path = self.root / "main.tfx"
        text = serialize_diagnostics(
            diagnose(path.read_text(encoding="utf-8"), filename=str(path))
        )
        self.assertNotIn("\\u", text)

    def test_a_report_needs_at_least_its_root(self):
        with self.assertRaises(ValueError):
            DiagnosticReport((), ())

    def test_a_span_outside_the_source_table_is_a_defect(self):
        self.write("main.tfx", "A\n")
        path = self.root / "main.tfx"
        report = diagnose(path.read_text(encoding="utf-8"), filename=str(path))
        stray = Diagnostic(
            Severity.ERROR,
            "parse",
            "P001",
            "m",
            span("nowhere.tfx"),
        )
        with self.assertRaises(ValueError):
            serialize_diagnostics(
                DiagnosticReport(report.sources, (stray,))
            )


if __name__ == "__main__":
    unittest.main()
