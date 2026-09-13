"""Text holes expand inside fields, without turning their output into syntax."""
from dataclasses import fields, is_dataclass, replace
import unittest

from texflux import (
    MacroExpansionError, ModuleError, ParseError, ValidationError,
    compile_text, normalize, parse, render,
)


def macro(body, call="!m{A}{B}\n", parameters="{x}{y}"):
    return f"!defmacro{{m}}{parameters}: |\n" + "".join(
        "    " + line + "\n" for line in body.splitlines()
    ) + call


class InterpolationBasicsTests(unittest.TestCase):
    def test_text_fields(self):
        for body, expected in (
            ("pre-!text{x}-!text{y}-!text{x}", "pre-A-B-A\n"),
            ("!!text{x}!text{y}", "AB\n"),
            (r"\includegraphics[width=!text{x}]{fig/!text{y}.pdf}",
             "\\includegraphics[width=A]{fig/B.pdf}\n"),
            ("@hoge{!text{x}}: |\n    !param{y}", "\\begin{hoge}{A}\nB\n\\end{hoge}\n"),
            ("@foo >> @bar >> @hoge{!text{x}}:\n    - !param{y}",
             "\\begin{foo}\n\\begin{bar}\n\\begin{hoge}{A}\nB\n\\end{hoge}\n\\end{bar}\n\\end{foo}\n"),
            ("\\foo{!text{x}}[!text{y}]<!text{x}>: |\n    C",
             "\\foo{A}[B]<A>{\nC\n}\n"),
            ("@{pre-!text{x}}: |\n    !param{y}", "{\npre-A\nB\n}\n"),
            ("!vpad{!text{x}}{!text{y}}: |\n    C", "\\vspace{A}\nC\n\\vspace{B}\n"),
        ):
            with self.subTest(body=body):
                self.assertEqual(compile_text(macro(body)), expected)

    def test_empty_and_single_line_values(self):
        for call, expected in (("!m{}\n", "\n"), ("!m{hello}\n", "hello\n"),
                               ("!m: |\n    LINE\n", "LINE\n")):
            with self.subTest(call=call):
                self.assertEqual(compile_text(macro("!!text{x}", call, "{x}")), expected)

    def test_each_reads_one_text_value_at_a_time(self):
        body = "!each{items}{item}: |\n    \\label{item:!text{item}}"
        self.assertEqual(compile_text(macro(body, "!m{alpha}{beta}\n", "{...items}")),
                         "\\label{item:alpha}\n\\label{item:beta}\n")
        self.assertEqual(compile_text(macro(body, "!m\n", "{...items}")), "\n")

    def test_nested_forwarding_and_inserted_text_are_not_rescanned(self):
        inner = "!defmacro{inner}{id}: |\n    \\label{!text{id}}\n"
        for value in ("intro", "!!text{y}", "!!param{y}", "!foo", "@frame", ">>", "!when{draft}"):
            with self.subTest(value=value):
                source = inner + macro("!inner{!text{x}-!text{y}}", f"!m{{sec}}{{{value}}}\n")
                self.assertEqual(compile_text(source), "\\label{sec-" + value.replace("!!", "!") + "}\n")

    def test_ast_forwarding_does_not_rescan_escaped_text(self):
        source = "!defmacro{inner}{x}: |\n    !param{x}\n" + macro(
            "!inner: |\n    literal !!text{x}", "!m\n", "")
        self.assertEqual(compile_text(source), "literal !text{x}\n")


class InterpolationEscapeTests(unittest.TestCase):
    def test_escapes_and_nonmarkers_inside_and_outside_templates(self):
        for text, expected in (
            ("pre !!text{x} !!param{x}", "pre !text{x} !param{x}"),
            ("pre !!!text{x}", "pre !!text{x}"),
            (r"pre \!text{x}", r"pre \!text{x}"),
            ("pre !text {x} !textbf{x} !text", "pre !text {x} !textbf{x} !text"),
            ("!!!text{x}", "!text{x}"),
            ("!!!!text{x}", "!!text{x}"),
        ):
            for source in (text + "\n", macro(text, "!m\n", "")):
                with self.subTest(source=source):
                    self.assertEqual(compile_text(source), expected + "\n")


class InterpolationDiagnosticTests(unittest.TestCase):
    def test_diagnostics_point_at_definition_markers_and_keep_chain(self):
        cases = (
            ("pre !text{nope}", "unknown macro parameter 'nope'", 9),
            ("pre !text{}", "invalid !text parameter name ''", 9),
            ("pre !text{1x}", "invalid !text parameter name '1x'", 9),
            ("pre !text{a b}", "invalid !text parameter name 'a b'", 9),
            ("pre !text{", "unterminated !text{...}", 9),
            ("pre !param{x}", "!param cannot be used inside a text field; use !text for text interpolation", 9),
            ("pre !param{", "!param cannot be used inside a text field; use !text for text interpolation", 9),
            ("!text{x}", "!text is only valid inside a textual field; use !param for an AST position", 5),
            ("\\foo:\n    - !text{x}", "!text is only valid inside a textual field; use !param for an AST position", 11),
        )
        for body, message, column in cases:
            with self.subTest(body=body):
                with self.assertRaises(MacroExpansionError) as caught:
                    compile_text(macro(body), filename="m.tfx")
                error = caught.exception
                line = 3 if body.startswith("\\foo:") else 2
                call_line = 4 if line == 3 else 3
                self.assertEqual(error.diagnostic(),
                    f"m.tfx:{line}:{column}: macro error: {message}; "
                    f"while expanding 'm' called at m.tfx:{call_line}:1")

    def test_group_marker_offsets(self):
        with self.assertRaises(MacroExpansionError) as caught:
            compile_text(macro("@hoge{!text{nope}}: |"), filename="m.tfx")
        self.assertEqual(caught.exception.span.start.column, 11)
        self.assertEqual(caught.exception.span.start.line, 2)

    def test_outside_template_and_reserved_name(self):
        for source in ("@hoge{!text{x}}: |\n", "\\foo{!text{x}}\n", "!!text{x}\n",
                       "!defmacro{m}{x}: |\n    !param{x}\n!m{!text{x}}\n"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(MacroExpansionError, "!text is only valid inside a macro template"):
                    compile_text(source)
        with self.assertRaisesRegex(MacroExpansionError, "!param cannot be used inside a text field"):
            compile_text("\\foo{!param{x}}\n")
        with self.assertRaisesRegex(ValidationError, "'!text' is reserved by TeXFlux"):
            compile_text("!defmacro{text}{x}: |\n    A\n")

    def test_nontext_values_and_rest(self):
        for call in ("!m: |\n    A\n    B\n", "!m: |\n", "!m: |\n    @center: |\n        A\n"):
            with self.subTest(call=call):
                with self.assertRaisesRegex(MacroExpansionError, "macro parameter 'x' is not a text value; use !param for structural values"):
                    compile_text(macro("pre !text{x}", call, "{x}"))
        with self.assertRaisesRegex(MacroExpansionError, "'x' is a rest parameter; use !each to access its values"):
            compile_text(macro("pre !text{x}", "!m{A}\n", "{...x}"))
        with self.assertRaisesRegex(MacroExpansionError, "macro parameter 'i' is not a text value"):
            compile_text(macro("!each{x}{i}: |\n    pre !text{i}",
                               "!m: |\n    @center: |\n        A\n", "{...x}"))

    def test_metadata_is_static(self):
        for body, where in (
            ("!when{!text{x}}: |", "a !when flag group; flag names are static"),
            ("!unless{!text{x}}: |", "a !unless flag group; flag names are static"),
            ("!when[and]{!text{x}}{b}: |", "a !when flag group; flag names are static"),
            ("!when[!text{x}]{b}: |", "a !when flag group; flag names are static"),
            ("!param{!text{x}}", "a !param name group"),
            ("!each{!text{x}}{i}: |", "a !each name group"),
            ("!each{items}{!text{x}}: |", "a !each name group"),
            ("!off{!text{x}}: |", "!off's arguments"),
            ("!drop{!text{x}}: |", "!drop's arguments"),
            ("!unknown{!text{x}}", "!unknown's arguments"),
            ("!vpad{1em}(a=!text{x}): |", "a '(...)' binding list"),
        ):
            with self.subTest(body=body):
                with self.assertRaises(MacroExpansionError) as caught:
                    compile_text(macro(body), filename="m.tfx")
                self.assertEqual(caught.exception.message,
                    f"interpolation is not allowed in {where}; while expanding 'm' called at m.tfx:3:1")
        for source, message in (("!import{!text{x}}\n", "!import's arguments"),
                                ("!import{missing.tfx}(a=!text{x})\n", "a '(...)' binding list")):
            with self.subTest(source=source):
                with self.assertRaises(MacroExpansionError) as caught:
                    compile_text(source)
                self.assertEqual(caught.exception.message, "interpolation is not allowed in " + message)
        for source, error, message in (
            ("!flag{!text{x}}{on}\n", ValidationError, "invalid build flag name"),
            ("!defmacro{m}{!text{x}}: |\n", ValidationError, "invalid macro parameter name"),
            ("!defmacro{!text{x}}: |\n", ValidationError, "invalid macro name"),
            ("!macroimport{!text{x}}\n", ModuleError, ""),
            ("@!text{env}: |\n", ParseError, "invalid structural name"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(error, message):
                    compile_text(source)


class InterpolationConditionalTests(unittest.TestCase):
    def test_live_and_dropped_payloads(self):
        source = "!flag{draft}{off}\n" + macro("!when{draft}: |\n    pre !text{x}", "!m{A}\n", "{x}")
        self.assertEqual(compile_text(source), "\n")
        self.assertEqual(compile_text(source, flags={"draft": True}), "pre A\n")
        for body in ("pre !text{missing}", "pre !text{", "pre !param{x}", "!text{x}",
                     "!off{!text{x}}: |", "!when{!text{x}}: |", "!import{!text{x}}"):
            with self.subTest(body=body):
                self.assertEqual(compile_text("!flag{draft}{off}\n!when{draft}: |\n    " + body + "\n"), "\n")
        source = "!flag{draft}{off}\n" + macro("!when{draft}: |\n    pre !text{x}",
                              "!m: |\n    @center: |\n        A\n", "{x}")
        self.assertEqual(compile_text(source), "\n")
        with self.assertRaisesRegex(MacroExpansionError, "not a text value"):
            compile_text(source, flags={"draft": True})


class InterpolationInvariantTests(unittest.TestCase):
    def test_rendering_parts_matches_plain_canonical_text(self):
        from texflux.ast import ParsedInvocation, SpecialInvocation, Stack

        def plain(node):
            self.assertNotIsInstance(node, (ParsedInvocation, SpecialInvocation, Stack))
            if isinstance(node, tuple):
                return tuple(plain(child) for child in node)
            if is_dataclass(node):
                return replace(node, **{
                    field.name: None if field.name in {"parts", "header_parts"}
                    else plain(getattr(node, field.name)) for field in fields(node)
                })
            return node

        for body in ("pre !text{x}", "@hoge{!text{x}}: |", "@{!text{x}}: |",
                     "!vpad{!text{x}}: |", "\\foo:\n    - pre !text{x}"):
            with self.subTest(body=body):
                canonical = normalize(parse(macro(body)))
                self.assertEqual(render(canonical), render(plain(canonical)))

    def test_scanner_fast_path_and_clamped_marker_spans(self):
        from texflux.ast import SourcePosition, SourceSpan
        from texflux.interpolate import interpolate, marker_span

        origin = SourceSpan("m.tfx", SourcePosition(1, 1), SourcePosition(1, 3))
        for text in ("no marker", "!other", r"\!text{x}"):
            with self.subTest(text=text):
                self.assertIsNone(interpolate(text, origin=origin, target=origin, offset=0, lookup=None))
        self.assertEqual(marker_span(origin, 1, 8), origin)
        self.assertEqual(marker_span(origin, 0, 1).end, SourcePosition(1, 2))
        with self.assertRaises(MacroExpansionError) as caught:
            interpolate("prefix !text{x}", origin=origin, target=origin, offset=0, lookup=None)
        self.assertEqual(caught.exception.span, origin)
